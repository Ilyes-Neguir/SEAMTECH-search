#!/usr/bin/env python3
"""Preflight de l'arrivée de l'archive réelle — à exécuter avant TOUT traitement.

Ce script est la porte d'entrée obligatoire avant toute manipulation de
l'archive de production (Lot G à venir). Il est 100 % local :

- RG13 : AUCUNE écriture dans la source (lecture seule stricte) ;
- RG14 : AUCUN appel réseau (ni ``requests``, ni ``httpx``, ni ``urllib``,
  ni ``socket`` — contrôlé par ``tests/test_garde_fous_preparation.py``) ;
- AUCUN repli silencieux sur ``sample_data`` : le chemin source doit être
  fourni explicitement (argument ``--source``), sinon refus (code 2).

Il vérifie (voir docs/ARRIVEE_ARCHIVE.md) :
  chemin source fourni explicitement ; source existante ; source lisible ;
  présence de fichiers ; espace disque ; répertoire de travail distinct de la
  source ; répertoire de sortie distinct de la source ; absence de droit
  d'écriture dans la source (avertissement si le système la rend écrivable) ;
  Python ; Tesseract ; langue française ; pdftoppm (ou équivalent) ;
  PostgreSQL si ``--staging`` ; version du dépôt ; version du schéma ;
  configuration OCR utilisée.

Il produit aussi un ÉCHANTILLON de travail (jamais de traitement massif par
défaut) : 20-30 PDF (défaut 25) OU 5-10 dossiers représentatifs (limite
configurable), avec empreintes SHA-256 pour le contrôle d'intégrité ultérieur
(sous-commande ``empreintes``).

Usage :
    python3 scripts/preflight_archive.py preflight \\
        --source /chemin/ARCHIVE --travail /chemin/travail --sortie /chemin/rapports \\
        [--staging] [--min-libre-o N] [--echantillon-pdfs N] [--echantillon-dossiers N] \\
        [--limite-empreintes N] [--sans-empreintes] [--masquer-chemins] [--json]

    python3 scripts/preflight_archive.py empreintes \\
        --manifeste /chemin/rapports/echantillon.json --source /chemin/ARCHIVE \\
        [--sortie /chemin/rapports] [--masquer-chemins] [--json]

Codes de sortie (contrat — utilisables en scriptage) :
    0  préflight vert (avertissements possibles)
    1  erreur interne (rapport impossible, exception)
    2  refus : chemin source absent ou vide
    3  refus : source inexistante, illisible, ou sans fichiers
    4  refus : inclusion de chemins (travail/sortie dans la source ou l'inverse)
    5  refus : configuration ambiguë
    6  refus : prérequis outils manquants (Tesseract, langue fra, pdftoppm)
    7  refus : espace disque insuffisant
    8  refus : PostgreSQL demandé (--staging) mais absent
    9  refus : empreintes non conformes (fichiers modifiés ou manquants)

Rapports : ``preflight_rapport.json`` + ``preflight_rapport.txt`` (et
``echantillon.json``) écrits dans le répertoire de sortie. ``--masquer-chemins``
produit une version partageable (chemins sensibles remplacés par
``<SOURCE>`` / ``<DOMICILE>``). Les rapports ne contiennent JAMAIS de secret :
aucune variable d'environnement n'y est recopiée (test dédié).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# --- Codes de sortie (contrat, docs/ARRIVEE_ARCHIVE.md) ---------------------
CODE_OK = 0
CODE_ERREUR = 1
CODE_SOURCE_ABSENTE = 2
CODE_SOURCE_INVALIDE = 3
CODE_INCLUSION_CHEMINS = 4
CODE_CONFIG_AMBIGUE = 5
CODE_PREREQUIS = 6
CODE_ESPACE = 7
CODE_POSTGRES = 8
CODE_EMPREINTES = 9

# Priorité d'affichage quand plusieurs refus : le premier code présent gagne.
ORDRE_REFUS = (
    CODE_SOURCE_ABSENTE,
    CODE_CONFIG_AMBIGUE,
    CODE_INCLUSION_CHEMINS,
    CODE_SOURCE_INVALIDE,
    CODE_ESPACE,
    CODE_PREREQUIS,
    CODE_POSTGRES,
    CODE_EMPREINTES,
)

MIN_LIBRE_O_DEFAUT = 1024 * 1024 * 1024  # 1 Go — aligné sur AppConfig.min_free_bytes
ECH_PDF_DEFAUT = 25  # fourchette de procédure : 20 à 30 PDF
ECH_DOSSIERS_DEFAUT = 0  # mode dossiers inactif par défaut
FOURCHETTE_PDF = (20, 30)
FOURCHETTE_DOSSIERS = (5, 10)
LIMITE_EMPREINTES_DEFAUT = 100  # fichiers empreintés max par échantillon
LIMITE_TAILLE_EMPREINTE_MO = 500  # au-delà : pas d'empreinte (signalé)

LIBELLES_REFUS = {
    CODE_SOURCE_ABSENTE: "refus : chemin source absent ou vide",
    CODE_SOURCE_INVALIDE: "refus : source inexistante, illisible ou sans fichiers",
    CODE_INCLUSION_CHEMINS: "refus : inclusion de chemins (travail/sortie dans la source ou l'inverse)",
    CODE_CONFIG_AMBIGUE: "refus : configuration ambiguë",
    CODE_PREREQUIS: "refus : prérequis outils manquants",
    CODE_ESPACE: "refus : espace disque insuffisant",
    CODE_POSTGRES: "refus : PostgreSQL demandé mais absent",
    CODE_EMPREINTES: "refus : empreintes non conformes",
}


@dataclass
class Controle:
    """Un contrôle du préflight, avec statut et détail lisibles."""

    id: str
    libelle: str
    statut: str  # ok | avertissement | echec | non_demande | non_verifiable
    detail: str = ""
    donnees: dict[str, Any] = field(default_factory=dict)

    def en_dict(self) -> dict[str, Any]:
        return {"id": self.id, "libelle": self.libelle, "statut": self.statut, "detail": self.detail, "donnees": self.donnees}


def empreinte_sha256(chemin: Path, taille_max_mo: int = LIMITE_TAILLE_EMPREINTE_MO) -> str | None:
    """Empreinte SHA-256 d'un fichier (lecture seule). None si trop gros."""
    try:
        taille = chemin.stat().st_size
    except OSError:
        return None
    if taille > taille_max_mo * 1024 * 1024:
        return None
    h = hashlib.sha256()
    try:
        with chemin.open("rb") as flux:
            while True:
                bloc = flux.read(1 << 20)
                if not bloc:
                    break
                h.update(bloc)
    except OSError:
        return None
    return h.hexdigest()


def masquer_chemins(valeur: Any, source: Path | None = None) -> Any:
    """Remplace les chemins sensibles (domicile, source) dans une structure de rapport.

    Pour les rapports destinés à être partagés : aucun chemin absolu révélateur
    (nom d'utilisateur, emplacement de l'archive) ne doit sortir du poste.
    """
    domicile = str(Path.home())
    marque_source = str(source) if source else None

    def _masq(texte: str) -> str:
        if marque_source:
            texte = texte.replace(marque_source, "<SOURCE>")
        texte = texte.replace(domicile, "<DOMICILE>")
        return texte

    if isinstance(valeur, str):
        return _masq(valeur)
    if isinstance(valeur, dict):
        return {k: masquer_chemins(v, source) for k, v in valeur.items()}
    if isinstance(valeur, list):
        return [masquer_chemins(v, source) for v in valeur]
    return valeur


def _selectionner(items: list[Any], n: int) -> list[Any]:
    """Sélection déterministe de n éléments répartis sur toute la liste triée."""
    if n <= 0 or not items:
        return []
    if len(items) <= n:
        return list(items)
    pas = len(items) / n
    return [items[int(i * pas)] for i in range(n)]


def _parcourir_source(source: Path) -> dict[str, Any]:
    """Parcourt la source EN LECTURE SEULE et collecte les statistiques d'échantillonnage.

    Uniquement appelé quand le chemin source a été fourni explicitement et a
    passé les contrôles de chemin (jamais de repli sur sample_data).
    """
    fichiers: list[Path] = []
    pdfs: list[Path] = []
    par_dossier: dict[str, dict[str, int]] = {}
    total_octets = 0
    for chemin in sorted(source.rglob("*")):
        if chemin.name.startswith("."):
            continue
        if not chemin.is_file():
            continue
        rel = chemin.relative_to(source)
        fichiers.append(rel)
        try:
            total_octets += chemin.stat().st_size
        except OSError:
            pass
        if chemin.suffix.lower() == ".pdf":
            pdfs.append(rel)
        dossier = rel.parts[0] if len(rel.parts) > 1 else "(racine)"
        compte = par_dossier.setdefault(dossier, {"nb_fichiers": 0, "nb_pdfs": 0, "taille": 0})
        compte["nb_fichiers"] += 1
        if chemin.suffix.lower() == ".pdf":
            compte["nb_pdfs"] += 1
    return {
        "nb_fichiers": len(fichiers),
        "nb_pdfs": len(pdfs),
        "nb_dossiers_haut_niveau": len([d for d in par_dossier if d != "(racine)"]),
        "taille_totale_octets": total_octets,
        "fichiers": fichiers,
        "pdfs": pdfs,
        "par_dossier": par_dossier,
    }


def _empreinter_element(chemin: Path, rel: str, limite_restante: list[int]) -> dict[str, Any]:
    """Décrémenté par effet de bord : [nb restant à empreinter]."""
    try:
        st = chemin.stat()
    except OSError:
        return {"chemin_relatif": rel, "statut": "manquant", "taille": None, "mtime": None, "sha256": None}
    element: dict[str, Any] = {"chemin_relatif": rel, "taille": st.st_size, "mtime": st.st_mtime, "sha256": None}
    if limite_restante[0] <= 0:
        element["statut"] = "empreinte_limite_atteinte"
        return element
    if st.st_size > LIMITE_TAILLE_EMPREINTE_MO * 1024 * 1024:
        element["statut"] = "empreinte_taille_max"
        return element
    limite_restante[0] -= 1
    element["sha256"] = empreinte_sha256(chemin)
    element["statut"] = "empreinte_ok" if element["sha256"] else "empreinte_illisible"
    return element


def construire_echantillon(
    source: Path,
    stats: dict[str, Any],
    nb_pdfs: int,
    nb_dossiers: int,
    sans_empreintes: bool,
    limite_empreintes: int,
) -> dict[str, Any]:
    """Construit l'échantillon de travail (léger, déterministe — jamais massif)."""
    limite_restante = [0 if sans_empreintes else max(0, limite_empreintes)]
    echantillon: dict[str, Any] = {
        "version": 1,
        "type": "echantillon_arrivee_archive",
        "cree_le": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "mode": None,
        "elements": [],
        "resume": {},
    }

    if nb_pdfs > 0 and nb_dossiers > 0:
        # Refusé en amont (config ambiguë) — filet de sécurité.
        raise ValueError("configuration ambiguë : --echantillon-pdfs et --echantillon-dossiers exclusifs")

    if nb_pdfs > 0:
        echantillon["mode"] = "pdf"
        choisis = _selectionner(sorted(stats["pdfs"]), nb_pdfs)
        for rel in choisis:
            echantillon["elements"].append(_empreinter_element(source / rel, str(rel), limite_restante))
    elif nb_dossiers > 0:
        echantillon["mode"] = "dossiers"
        par_dossier = stats["par_dossier"]
        avec_pdf = sorted(d for d, c in par_dossier.items() if d != "(racine)" and c["nb_pdfs"] > 0)
        sans_pdf = sorted(d for d in par_dossier if d != "(racine)" and par_dossier[d]["nb_pdfs"] == 0)
        candidats = avec_pdf + sans_pdf  # représentativité : dossiers à PDF d'abord
        choisis_dossiers = _selectionner(candidats, nb_dossiers)
        for dossier in choisis_dossiers:
            compte = par_dossier[dossier]
            fichiers_dossier = sorted(r for r in stats["fichiers"] if r.parts[0] == dossier)
            elements_fichiers = [_empreinter_element(source / r, str(r), limite_restante) for r in fichiers_dossier]
            echantillon["elements"].append(
                {
                    "dossier_relatif": dossier,
                    "nb_fichiers": compte["nb_fichiers"],
                    "nb_pdfs": compte["nb_pdfs"],
                    "taille_totale_octets": compte["taille"],
                    "fichiers": elements_fichiers,
                }
            )

    nb_fichiers_ech = sum(1 for e in echantillon["elements"] if "chemin_relatif" in e) + sum(
        len(e.get("fichiers", [])) for e in echantillon["elements"]
    )
    echantillon["resume"] = {
        "mode": echantillon["mode"],
        "nb_elements": len(echantillon["elements"]),
        "nb_fichiers_listes": nb_fichiers_ech,
        "fourchette_procedure_pdf": list(FOURCHETTE_PDF),
        "fourchette_procedure_dossiers": list(FOURCHETTE_DOSSIERS),
    }
    return echantillon


def _controle_disque(chemin: Path, min_libre_o: int) -> tuple[str, str, dict[str, Any]]:
    """Espace libre sur le volume de `chemin` (parent existant le plus proche)."""
    ancre = chemin
    while not ancre.exists() and ancre != ancre.parent:
        ancre = ancre.parent
    try:
        usage = shutil.disk_usage(ancre)
    except OSError as exc:
        return "non_verifiable", f"espace disque non mesurable pour {chemin} : {exc}", {}
    libre = usage.free
    statut = "ok" if libre >= min_libre_o else "echec"
    detail = f"{libre / 1e9:.1f} Go libres (seuil {min_libre_o / 1e9:.1f} Go) sur le volume de {chemin}"
    return statut, detail, {"octets_libres": libre, "seuil_octets": min_libre_o}


def _version_depot() -> dict[str, Any]:
    """Version du dépôt : commit git + version du paquet (local, aucun réseau)."""
    infos: dict[str, Any] = {"commit": None, "sale": None, "version_paquet": None}
    try:
        import seamtech_search

        infos["version_paquet"] = getattr(seamtech_search, "__version__", None)
    except Exception:
        pass
    try:
        resultat = subprocess.run(  # noqa: S603 — commande locale figée, aucun réseau
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if resultat.returncode == 0:
            infos["commit"] = resultat.stdout.strip() or None
        etat = subprocess.run(  # noqa: S603
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if etat.returncode == 0:
            infos["sale"] = bool(etat.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        infos["commit"] = None
    return infos


def _version_schema() -> str | None:
    try:
        from seamtech_search.schema_metier import VERSION_SCHEMA_METIER

        return VERSION_SCHEMA_METIER
    except Exception:
        return None


def _tesseract_langues(tesseract_command: str = "tesseract") -> tuple[bool, list[str], str]:
    """Liste les langues tesseract installées (binaire local, aucun réseau)."""
    try:
        resultat = subprocess.run(  # noqa: S603 — binaire local
            [tesseract_command, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, [], f"tesseract --list-langs impossible : {exc}"
    lignes = (resultat.stdout or "").splitlines()
    langues = [mot.strip() for mot in lignes[1:] if mot.strip() and " " not in mot.strip()]
    return True, langues, resultat.stdout.strip()


def executer_preflight(
    source: str | None,
    travail: str | None,
    sortie: str | None,
    *,
    staging: bool = False,
    min_libre_o: int = MIN_LIBRE_O_DEFAUT,
    echantillon_pdfs: int = ECH_PDF_DEFAUT,
    echantillon_dossiers: int = ECH_DOSSIERS_DEFAUT,
    sans_empreintes: bool = False,
    limite_empreintes: int = LIMITE_EMPREINTES_DEFAUT,
    masquer: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Exécute le préflight et retourne le rapport (dict JSON-serialisable).

    Ne parcourt réellement la source que si un chemin source explicite a été
    fourni ET que les contrôles de chemin passent. Aucun repli sur
    ``sample_data`` (testé).
    """
    environ = dict(os.environ if env is None else env)
    controles: list[Controle] = []
    refus: set[int] = set()
    parcours: dict[str, Any] | None = None
    echantillon: dict[str, Any] | None = None

    # --- 1. Chemin source fourni explicitement --------------------------------
    source_explicite = source is not None and source.strip() != ""
    env_source = environ.get("SEAMTECH_ARCHIVE_SOURCE", "").strip()
    controles.append(
        Controle(
            "source_fournie",
            "Chemin source fourni explicitement",
            "ok" if source_explicite else "echec",
            "--source renseigné" if source_explicite else "aucun chemin --source explicite (jamais de repli silencieux)",
        )
    )
    if not source_explicite:
        refus.add(CODE_SOURCE_ABSENTE)

    # --- 2. Configuration ambiguë ---------------------------------------------
    ambigu = False
    details_ambigu: list[str] = []
    if echantillon_pdfs < 0 or echantillon_dossiers < 0:
        ambigu = True
        details_ambigu.append("taille d'échantillon négative")
    if echantillon_pdfs > 0 and echantillon_dossiers > 0:
        ambigu = True
        details_ambigu.append("--echantillon-pdfs et --echantillon-dossiers sont exclusifs (choisir un axe)")
    if source_explicite and env_source:
        try:
            meme = Path(source).expanduser().resolve() == Path(env_source).expanduser().resolve()
        except OSError:
            meme = str(source) == str(env_source)
        if not meme:
            ambigu = True
            details_ambigu.append("--source et $SEAMTECH_ARCHIVE_SOURCE divergents")
    controles.append(
        Controle(
            "configuration_non_ambigue",
            "Configuration non ambiguë",
            "echec" if ambigu else "ok",
            "; ".join(details_ambigu) if ambigu else "un seul mode d'échantillonnage, source déclarée une seule fois",
        )
    )
    if ambigu:
        refus.add(CODE_CONFIG_AMBIGUE)

    # --- 3. Inclusions de chemins (résolution de liens symboliques comprise) --
    src_path: Path | None = None
    if source_explicite:
        src_path = Path(source).expanduser().resolve()
    inclusion = False
    details_inclusion: list[str] = []
    for libelle, brut in (("travail", travail), ("sortie", sortie)):
        if brut is None or str(brut).strip() == "":
            continue
        if src_path is None:
            continue
        try:
            chemin = Path(brut).expanduser().resolve()
        except OSError:
            chemin = Path(brut).expanduser()
        if chemin == src_path:
            inclusion = True
            details_inclusion.append(f"{libelle} == source")
        elif src_path in chemin.parents:
            inclusion = True
            details_inclusion.append(f"{libelle} dans la source ({chemin})")
        elif chemin in src_path.parents:
            inclusion = True
            details_inclusion.append(f"source dans {libelle} ({chemin})")
    controles.append(
        Controle(
            "chemins_disjoints",
            "Travail et sortie distincts de la source",
            "echec" if inclusion else "ok",
            "; ".join(details_inclusion) if inclusion else "aucun répertoire inclus dans l'autre",
        )
    )
    if inclusion:
        refus.add(CODE_INCLUSION_CHEMINS)

    # --- 4. Source : existence, lisibilité, fichiers (UNIQUEMENT si fournie) ---
    source_valide = False
    if source_explicite and not inclusion:
        assert src_path is not None
        existe = src_path.exists() and src_path.is_dir()
        controles.append(
            Controle(
                "source_existante",
                "Source existante (répertoire)",
                "ok" if existe else "echec",
                str(src_path) if existe else f"inexistant ou pas un répertoire : {src_path}",
            )
        )
        lisible = existe and os.access(src_path, os.R_OK)
        controles.append(
            Controle(
                "source_lisible",
                "Source lisible",
                "ok" if lisible else "echec",
                "lecture seule accessible" if lisible else "accès lecture refusé",
            )
        )
        if existe and lisible:
            try:
                parcours = _parcourir_source(src_path)
                nb_fichiers = parcours["nb_fichiers"]
                controles.append(
                    Controle(
                        "source_contient_fichiers",
                        "Présence de fichiers dans la source",
                        "ok" if nb_fichiers > 0 else "echec",
                        f"{nb_fichiers} fichier(s), {parcours['nb_pdfs']} PDF",
                        {"nb_fichiers": nb_fichiers, "nb_pdfs": parcours["nb_pdfs"]},
                    )
                )
                source_valide = nb_fichiers > 0
                if not source_valide:
                    refus.add(CODE_SOURCE_INVALIDE)
            except OSError as exc:
                controles.append(Controle("source_contient_fichiers", "Présence de fichiers dans la source", "echec", f"parcours impossible : {exc}"))
                refus.add(CODE_SOURCE_INVALIDE)
        else:
            controles.append(Controle("source_contient_fichiers", "Présence de fichiers dans la source", "non_verifiable", "source absente ou illisible"))
            refus.add(CODE_SOURCE_INVALIDE)
    else:
        for id_c, libelle in (
            ("source_existante", "Source existante (répertoire)"),
            ("source_lisible", "Source lisible"),
            ("source_contient_fichiers", "Présence de fichiers dans la source"),
        ):
            controles.append(Controle(id_c, libelle, "non_verifiable", "aucun chemin source explicite valide — aucun parcours effectué"))

    # --- 5. Droits d'écriture sur la source (avertissement, vérifiable ici) ---
    if source_explicite and src_path is not None and src_path.exists():
        ecrivable = os.access(src_path, os.W_OK)
        controles.append(
            Controle(
                "source_non_ecrivable",
                "Absence de droit d'écriture dans la source",
                "avertissement" if ecrivable else "ok",
                (
                    "la source est ouvrable en écriture par l'utilisateur courant — le traitement reste en "
                    "lecture seule (RG13) mais verrouillez les droits si possible"
                    if ecrivable
                    else "aucun droit d'écriture détecté"
                ),
                {"ecrivable": ecrivable},
            )
        )
    else:
        controles.append(Controle("source_non_ecrivable", "Absence de droit d'écriture dans la source", "non_verifiable", "source non vérifiée"))

    # --- 6. Espace disque ------------------------------------------------------
    statuts_disque = []
    donnees_disque = {}
    details_disque = []
    for libelle, chemin in (("source", src_path), ("travail", Path(travail).expanduser() if travail else None), ("sortie", Path(sortie).expanduser() if sortie else None)):
        if chemin is None:
            continue
        st, detail, donnees = _controle_disque(chemin, min_libre_o)
        statuts_disque.append(st)
        details_disque.append(detail)
        donnees_disque[libelle] = donnees
        if st == "echec":
            refus.add(CODE_ESPACE)
    statut_disque = "echec" if "echec" in statuts_disque else ("non_verifiable" if not statuts_disque or all(s == "non_verifiable" for s in statuts_disque) else "ok")
    controles.append(Controle("espace_disque", "Espace disque disponible", statut_disque, " | ".join(details_disque), donnees_disque))

    # --- 7. Outils : Python, Tesseract, langue fra, pdftoppm -------------------
    python_ok = sys.executable is not None
    controles.append(
        Controle(
            "python_present",
            "Python présent",
            "ok" if python_ok else "echec",
            f"{sys.executable} — Python {sys.version.split()[0]}",
            {"executable": sys.executable, "version": sys.version.split()[0]},
        )
    )
    tesseract_path = shutil.which("tesseract")
    controles.append(
        Controle(
            "tesseract_present",
            "Tesseract présent",
            "ok" if tesseract_path else "echec",
            tesseract_path or "tesseract introuvable dans le PATH",
            {"chemin": tesseract_path},
        )
    )
    if tesseract_path:
        dispo, langues, _ = _tesseract_langues("tesseract")
        fra = dispo and "fra" in langues
        controles.append(
            Controle(
                "langue_francaise",
                "Langue française (tesseract fra)",
                "ok" if fra else "echec",
                f"langues : {', '.join(langues)}" if dispo else "liste des langues illisible",
                {"langues": langues},
            )
        )
        version_moteur = None
        try:
            version = subprocess.run(["tesseract", "--version"], capture_output=True, text=True, timeout=10)  # noqa: S603
            version_moteur = (version.stdout or version.stderr or "").splitlines()[0].strip() or None
        except (OSError, subprocess.SubprocessError):
            version_moteur = None
    else:
        controles.append(Controle("langue_francaise", "Langue française (tesseract fra)", "echec", "non vérifiable (tesseract absent)"))
        version_moteur = None
    if not tesseract_path:
        refus.add(CODE_PREREQUIS)
    else:
        # fra manquant ou illisible = prérequis manquant aussi
        dernier = controles[-1]
        if dernier.statut != "ok":
            refus.add(CODE_PREREQUIS)

    pdftoppm_path = shutil.which("pdftoppm") or shutil.which("pdftocairo")
    controles.append(
        Controle(
            "pdftoppm_present",
            "pdftoppm (ou équivalent) présent",
            "ok" if pdftoppm_path else "echec",
            pdftoppm_path or "ni pdftoppm ni pdftocairo dans le PATH",
            {"chemin": pdftoppm_path},
        )
    )
    if not pdftoppm_path:
        refus.add(CODE_PREREQUIS)

    # --- 8. PostgreSQL (seulement si indexation staging demandée) --------------
    if staging:
        psql = shutil.which("psql") or shutil.which("pg_isready")
        db_url_configuree = bool(environ.get("SEAMTECH_DATABASE_URL", "").strip())
        pg_ok = bool(psql) or db_url_configuree
        controles.append(
            Controle(
                "postgres_present",
                "PostgreSQL présent (indexation staging)",
                "ok" if pg_ok else "echec",
                f"client : {psql or 'absent'} ; SEAMTECH_DATABASE_URL configurée : {'oui' if db_url_configuree else 'non'}",
                {"client": psql, "database_url_configuree": db_url_configuree},
            )
        )
        if not pg_ok:
            refus.add(CODE_POSTGRES)
    else:
        controles.append(Controle("postgres_present", "PostgreSQL présent (indexation staging)", "non_demande", "staging non demandé (--staging absent)"))

    # --- 9. Versions et configuration OCR --------------------------------------
    depot = _version_depot()
    schema = _version_schema()
    controles.append(
        Controle(
            "version_depot",
            "Version du dépôt",
            "ok" if depot.get("commit") else "avertissement",
            f"commit {depot.get('commit')} (paquet {depot.get('version_paquet')})"
            + (" — état de travail sale" if depot.get("sale") else "")
            if depot.get("commit")
            else "git indisponible — version du dépôt inconnue",
            depot,
        )
    )
    controles.append(
        Controle(
            "version_schema",
            "Version du schéma",
            "ok" if schema else "avertissement",
            schema or "version du schéma non importable",
            {"version_schema": schema},
        )
    )
    config_ocr = {
        "moteur": "tesseract",
        "version_moteur": version_moteur,
        "langue": "fra",
        "seuil_texte_natif": 20,
        "resolution_dpi": 300,
        "pdftoppm": pdftoppm_path,
    }
    try:
        from seamtech_search.ocr.pipeline import SEUIL_DEFAUT

        config_ocr["seuil_texte_natif"] = SEUIL_DEFAUT
    except Exception:
        pass
    controles.append(Controle("config_ocr", "Configuration OCR utilisée", "ok", f"tesseract {version_moteur or 'absent'}, langue fra, {config_ocr['resolution_dpi']} dpi, seuil {config_ocr['seuil_texte_natif']}", config_ocr))

    # --- 10. Échantillon de travail (jamais massif par défaut) -----------------
    if source_valide and parcours is not None and not refus.intersection({CODE_CONFIG_AMBIGUE, CODE_INCLUSION_CHEMINS}):
        try:
            echantillon = construire_echantillon(
                src_path, parcours, echantillon_pdfs, echantillon_dossiers, sans_empreintes, limite_empreintes
            )
            hors_fourchette = (
                echantillon["mode"] == "pdf" and not (FOURCHETTE_PDF[0] <= len(echantillon["elements"]) <= FOURCHETTE_PDF[1]) and echantillon_pdfs != 0
            ) or (
                echantillon["mode"] == "dossiers"
                and echantillon["elements"]
                and not (FOURCHETTE_DOSSIERS[0] <= len(echantillon["elements"]) <= FOURCHETTE_DOSSIERS[1])
            )
            controles.append(
                Controle(
                    "echantillon_travail",
                    "Échantillon de travail (sans traitement massif)",
                    "avertissement" if hors_fourchette or not echantillon["elements"] else "ok",
                    (
                        f"mode {echantillon['mode']} : {len(echantillon['elements'])} élément(s), "
                        f"{echantillon['resume']['nb_fichiers_listes']} fichier(s) listé(s)"
                    ),
                    echantillon["resume"],
                )
            )
        except ValueError as exc:
            controles.append(Controle("echantillon_travail", "Échantillon de travail (sans traitement massif)", "echec", str(exc)))
            refus.add(CODE_CONFIG_AMBIGUE)
    else:
        controles.append(Controle("echantillon_travail", "Échantillon de travail (sans traitement massif)", "non_verifiable", "aucun parcours de source — aucun échantillon produit"))

    # --- Code de sortie et rapport --------------------------------------------
    code_sortie = CODE_OK
    for code in ORDRE_REFUS:
        if code in refus:
            code_sortie = code
            break

    rapport: dict[str, Any] = {
        "outil": "preflight_archive",
        "version_outil": "1.0",
        "mode": "preflight",
        "date": datetime.now(timezone.utc).isoformat(),
        "code_sortie": code_sortie,
        "signification_code": LIBELLES_REFUS.get(code_sortie, "préflight vert"),
        "resume": "vert" if code_sortie == CODE_OK and not any(c.statut == "avertissement" for c in controles) else ("avertissements" if code_sortie == CODE_OK else "refus"),
        "source": str(src_path) if src_path else (source or ""),
        "travail": str(Path(travail).expanduser()) if travail else "",
        "sortie": str(Path(sortie).expanduser()) if sortie else "",
        "staging_demande": staging,
        "controles": [c.en_dict() for c in controles],
        "echantillon": echantillon,
        "configuration": {"version_depot": depot, "version_schema": schema, "config_ocr": config_ocr},
    }
    if masquer:
        rapport = masquer_chemins(rapport, src_path)
    return rapport


def verifier_empreintes(
    manifeste: str | Path,
    source: str | None,
    *,
    sortie: str | Path | None = None,
    masquer: bool = False,
) -> dict[str, Any]:
    """Rejoue le contrôle des empreintes d'un échantillon (SHA-256, taille, mtime).

    Détecte : fichiers manquants, changement d'empreinte SHA-256, changement de
    taille, changement de mtime. Seul un changement de mtime (touch) est un
    avertissement ; les autres sont des échecs (code 9).
    """
    controles: list[Controle] = []
    source_explicite = source is not None and str(source).strip() != ""
    controles.append(
        Controle(
            "source_fournie",
            "Chemin source fourni explicitement",
            "ok" if source_explicite else "echec",
            "--source renseigné" if source_explicite else "aucun chemin --source explicite",
        )
    )
    if not source_explicite:
        rapport = _rapport_empreintes(controles, [], CODE_SOURCE_ABSENTE, source, masquer)
        return rapport

    src_path = Path(source).expanduser().resolve()
    if not src_path.exists() or not src_path.is_dir() or not os.access(src_path, os.R_OK):
        controles.append(Controle("source_valide", "Source existante et lisible", "echec", str(src_path)))
        return _rapport_empreintes(controles, [], CODE_SOURCE_INVALIDE, src_path, masquer)
    controles.append(Controle("source_valide", "Source existante et lisible", "ok", str(src_path)))

    manifeste_path = Path(manifeste).expanduser().resolve()
    try:
        contenu = json.loads(manifeste_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        controles.append(Controle("manifeste_lisible", "Manifeste lisible", "echec", f"{manifeste_path} : {exc}"))
        return _rapport_empreintes(controles, [], CODE_ERREUR, src_path, masquer)
    controles.append(Controle("manifeste_lisible", "Manifeste lisible", "ok", str(manifeste_path)))

    # Aplatit les éléments (mode pdf ou mode dossiers).
    elements: list[dict[str, Any]] = []
    for element in contenu.get("elements", []):
        if "chemin_relatif" in element:
            elements.append(element)
        for fichier in element.get("fichiers", []):
            elements.append(fichier)

    resultats: list[dict[str, Any]] = []
    nb_identique = nb_modifie = nb_manquant = 0
    for element in elements:
        rel = element.get("chemin_relatif", "")
        chemin = src_path / rel
        ligne: dict[str, Any] = {"chemin_relatif": rel}
        if not chemin.is_file():
            ligne["statut"] = "manquant"
            nb_manquant += 1
        else:
            st = chemin.stat()
            changements: list[str] = []
            if element.get("taille") is not None and st.st_size != element["taille"]:
                changements.append("taille")
            if element.get("mtime") is not None and abs(st.st_mtime - element["mtime"]) > 1e-6:
                changements.append("mtime")
            if element.get("sha256"):
                sha = empreinte_sha256(chemin)
                if sha != element["sha256"]:
                    changements.append("sha256")
            if not changements:
                ligne["statut"] = "identique"
                nb_identique += 1
            elif changements == ["mtime"]:
                ligne["statut"] = "modifie_mtime"
                ligne["changements"] = changements
                nb_modifie += 1
            else:
                ligne["statut"] = "modifie"
                ligne["changements"] = changements
                nb_modifie += 1
        resultats.append(ligne)

    code = CODE_EMPREINTES if (nb_manquant or any(r["statut"] == "modifie" for r in resultats)) else CODE_OK
    nb_mtime_seul = sum(1 for r in resultats if r["statut"] == "modifie_mtime")
    statut_global = "echec" if code == CODE_EMPREINTES else ("avertissement" if nb_mtime_seul else "ok")
    controles.append(
        Controle(
            "empreintes_conformes",
            "Empreintes de l'échantillon inchangées",
            statut_global,
            f"{nb_identique} identique(s), {nb_modifie} modifié(s) dont {nb_mtime_seul} mtime seul, {nb_manquant} manquant(s)",
            {"nb_identique": nb_identique, "nb_modifie": nb_modifie, "nb_mtime_seul": nb_mtime_seul, "nb_manquant": nb_manquant},
        )
    )
    return _rapport_empreintes(controles, resultats, code, src_path, masquer)


def _rapport_empreintes(controles: list[Controle], resultats: list[dict[str, Any]], code: int, source: Path | None, masquer: bool) -> dict[str, Any]:
    rapport: dict[str, Any] = {
        "outil": "preflight_archive",
        "version_outil": "1.0",
        "mode": "empreintes",
        "date": datetime.now(timezone.utc).isoformat(),
        "code_sortie": code,
        "signification_code": LIBELLES_REFUS.get(code, "empreintes conformes"),
        "resume": "vert" if code == CODE_OK else "refus",
        "source": str(source) if source else "",
        "controles": [c.en_dict() for c in controles],
        "resultats": resultats,
    }
    if masquer:
        rapport = masquer_chemins(rapport, source)
    return rapport


def rapport_texte(rapport: dict[str, Any]) -> str:
    """Rendu texte lisible du rapport (pour opérateur et archivage)."""
    lignes = [
        f"SEAMTECH — préflight {rapport.get('mode', '')} — {rapport.get('date', '')}",
        f"Source  : {rapport.get('source', '')}",
        f"Travail : {rapport.get('travail', '')}",
        f"Sortie  : {rapport.get('sortie', '')}",
        "",
        "Contrôles :",
    ]
    marques = {"ok": "OK  ", "avertissement": "AVERT", "echec": "ECHEC", "non_demande": "N/D  ", "non_verifiable": "N/V  "}
    for controle in rapport.get("controles", []):
        marque = marques.get(controle["statut"], "?????")
        detail = f" — {controle['detail']}" if controle.get("detail") else ""
        lignes.append(f"  [{marque}] {controle['libelle']}{detail}")
    echantillon = rapport.get("echantillon")
    if echantillon:
        lignes.append("")
        lignes.append(f"Échantillon (mode {echantillon.get('mode')}) : {echantillon.get('resume', {})}")
        for element in echantillon.get("elements", [])[:5]:
            cle = element.get("chemin_relatif") or element.get("dossier_relatif")
            lignes.append(f"  - {cle}")
        if len(echantillon.get("elements", [])) > 5:
            lignes.append(f"  … et {len(echantillon['elements']) - 5} autre(s)")
    if rapport.get("mode") == "empreintes":
        lignes.append("")
        lignes.append("Résultats d'empreintes :")
        for ligne in rapport.get("resultats", [])[:20]:
            lignes.append(f"  - {ligne.get('chemin_relatif')} : {ligne.get('statut')}")
    lignes.append("")
    lignes.append(f"Code de sortie : {rapport.get('code_sortie')} — {rapport.get('signification_code')}")
    return "\n".join(lignes) + "\n"


def _ecrire_rapports(rapport: dict[str, Any], sortie: Path | None, json_stdout: bool) -> None:
    texte = rapport_texte(rapport)
    if json_stdout:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    else:
        print(texte, end="")
    if sortie is not None:
        try:
            sortie.mkdir(parents=True, exist_ok=True)
            prefixe = "preflight_rapport" if rapport.get("mode") == "preflight" else "empreintes_rapport"
            (sortie / f"{prefixe}.json").write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
            (sortie / f"{prefixe}.txt").write_text(texte, encoding="utf-8")
            if rapport.get("mode") == "preflight" and rapport.get("echantillon"):
                (sortie / "echantillon.json").write_text(json.dumps(rapport["echantillon"], ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"Avertissement : rapports non écrits dans {sortie} : {exc}", file=sys.stderr)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="preflight_archive",
        description="Preflight de l'arrivée de l'archive réelle (lecture seule, aucun réseau).",
    )
    sous = parser.add_subparsers(dest="commande", required=True)

    p_pre = sous.add_parser("preflight", help="Contrôles avant tout traitement + échantillon de travail.")
    p_pre.add_argument("--source", default=None, help="Chemin de l'archive (OBLIGATOIRE, aucun repli silencieux).")
    p_pre.add_argument("--travail", default=None, help="Répertoire de travail (distinct de la source).")
    p_pre.add_argument("--sortie", default=None, help="Répertoire des rapports (distinct de la source).")
    p_pre.add_argument("--staging", action="store_true", help="Vérifier aussi PostgreSQL (indexation staging).")
    p_pre.add_argument("--min-libre-o", type=int, default=MIN_LIBRE_O_DEFAUT, help=f"Espace libre minimal en octets (défaut {MIN_LIBRE_O_DEFAUT}).")
    p_pre.add_argument("--echantillon-pdfs", type=int, default=ECH_PDF_DEFAUT, help=f"Nombre de PDF de l'échantillon (défaut {ECH_PDF_DEFAUT}, procédure 20-30).")
    p_pre.add_argument("--echantillon-dossiers", type=int, default=ECH_DOSSIERS_DEFAUT, help="Nombre de dossiers représentatifs (procédure 5-10 ; exclusif avec --echantillon-pdfs).")
    p_pre.add_argument("--limite-empreintes", type=int, default=LIMITE_EMPREINTES_DEFAUT, help=f"Fichiers empreintés max (défaut {LIMITE_EMPREINTES_DEFAUT} — jamais massif).")
    p_pre.add_argument("--sans-empreintes", action="store_true", help="Ne calcule aucune empreinte (inventaire seul).")
    p_pre.add_argument("--masquer-chemins", action="store_true", help="Rapport partageable : chemins sensibles masqués.")
    p_pre.add_argument("--json", action="store_true", help="Écrit aussi le rapport JSON sur stdout.")

    p_emp = sous.add_parser("empreintes", help="Re-vérifie les empreintes d'un échantillon (SHA-256, taille, mtime).")
    p_emp.add_argument("--manifeste", required=True, help="Manifeste echantillon.json produit par preflight.")
    p_emp.add_argument("--source", default=None, help="Chemin de l'archive (OBLIGATOIRE, à re-fournir explicitement).")
    p_emp.add_argument("--sortie", default=None, help="Répertoire des rapports (défaut : celui du manifeste).")
    p_emp.add_argument("--masquer-chemins", action="store_true", help="Rapport partageable : chemins sensibles masqués.")
    p_emp.add_argument("--json", action="store_true", help="Écrit aussi le rapport JSON sur stdout.")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.commande == "preflight":
            rapport = executer_preflight(
                args.source,
                args.travail,
                args.sortie,
                staging=args.staging,
                min_libre_o=args.min_libre_o,
                echantillon_pdfs=args.echantillon_pdfs,
                echantillon_dossiers=args.echantillon_dossiers,
                sans_empreintes=args.sans_empreintes,
                limite_empreintes=args.limite_empreintes,
                masquer=args.masquer_chemins,
            )
            sortie = Path(args.sortie).expanduser() if args.sortie and rapport["code_sortie"] != CODE_INCLUSION_CHEMINS else None
        else:
            manifeste = args.manifeste
            sortie_arg = args.sortie if args.sortie else str(Path(manifeste).expanduser().parent)
            rapport = verifier_empreintes(manifeste, args.source, sortie=sortie_arg, masquer=args.masquer_chemins)
            sortie = Path(sortie_arg).expanduser() if rapport["code_sortie"] != CODE_INCLUSION_CHEMINS else None
        _ecrire_rapports(rapport, sortie, args.json)
        return int(rapport["code_sortie"])
    except Exception as exc:  # filet : jamais de trace bavarde sur les secrets
        print(f"Erreur interne préflight : {type(exc).__name__}", file=sys.stderr)
        return CODE_ERREUR


if __name__ == "__main__":
    sys.exit(main())
