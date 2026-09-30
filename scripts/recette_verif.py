#!/usr/bin/env python3
"""Vérificateur fonctionnel de la recette locale SEAMTECH Search.

Source UNIQUE des contrôles fonctionnels : les orchestrateurs
``scripts/recette_locale.ps1`` (Windows) et ``scripts/recette_locale.sh``
(Linux/CI) n'ont aucun contrôle dupliqué, ils exécutent ce fichier DANS le
conteneur web (``docker compose exec -T web python -``) :

    Get-Content scripts/recette_verif.py -Raw | docker compose exec -T web python -
    docker compose exec -T web python - < scripts/recette_verif.py

Entrées (variables d'environnement) :
    SEAMTECH_AUTH_TOKEN   jeton de service (déjà présent dans le conteneur)
    RECETTE_SOURCES       dossier des archives/dossiers à déposer
                          (défaut : /app/data/recette-sources)
    RECETTE_TRAVAIL       répertoire de travail (extraction, RG13)
                          (défaut : /app/data/recette-lot)
    RECETTE_UTILISATEUR   compte nominatif de recette (défaut : recette)
    RECETTE_MOT_DE_PASSE  mot de passe du compte (obligatoire)
    RECETTE_TIMEOUT_LOT   secondes max pour un lot (défaut : 1200)
    RECETTE_BASE          base URL de l'API (défaut : http://127.0.0.1:8000)

Sortie : lignes machine sur stdout, puis code 0/1.
    CONTROLE|<id>|PASS|<détail avec sortie brute>
    CONTROLE|<id>|FAIL|<détail avec sortie brute>
    INFO|<cle>|<valeur>            (consommée par les orchestrateurs)

RG13 : les archives et dossiers SOURCES sont lus, jamais modifiés — toute
l'extraction et la normalisation se font dans un répertoire de travail.

RG14_EXCEPTION : ce module est le client HTTP de la recette locale (outillage
d'acceptation exécuté dans le conteneur web, jamais importé par le service).
Sa seule dépendance réseau est urllib.request, pour parler à l'API en
LOOPBACK (127.0.0.1 du conteneur) et télécharger depuis l'endpoint S3 LOCAL
de la compose via l'URL présignée — aucune sortie Internet. Exception
documentée dans tests/test_garde_fous_preparation.py (EXCEPTIONS_RG14).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

# Liste épinglée par tests/test_recette_locale.py — ne pas réordonner.
CONTROLES = (
    "compte-recette",
    "depot-archives",
    "suivi-lots",
    "fiches-a-valider",
    "validation-fiche",
    "recherche-texte",
    "recherche-dimension",
    "filtres-facettes",
    "suggestions",
    "pdf-presigne",
    "zone-surlignee",
    "rejeu-idempotent",
)

BASE = os.environ.get("RECETTE_BASE", "http://127.0.0.1:8000").rstrip("/")
JETON = os.environ.get("SEAMTECH_AUTH_TOKEN", "")
UTILISATEUR = os.environ.get("RECETTE_UTILISATEUR", "recette")
MOT_DE_PASSE = os.environ.get("RECETTE_MOT_DE_PASSE", "")
TIMEOUT_LOT = int(os.environ.get("RECETTE_TIMEOUT_LOT", "1200"))
SOURCES = Path(os.environ.get("RECETTE_SOURCES", "/app/data/recette-sources"))
TRAVAIL = Path(os.environ.get("RECETTE_TRAVAIL", "/app/data/recette-lot"))

ECHECS: list[str] = []


def _ligne(controle: str, ok: bool, detail: str) -> None:
    statut = "PASS" if ok else "FAIL"
    print(f"CONTROLE|{controle}|{statut}|{detail}", flush=True)
    if not ok:
        ECHECS.append(controle)


def _info(cle: str, valeur: str) -> None:
    print(f"INFO|{cle}|{valeur}", flush=True)


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    """Ne JAMAIS suivre les redirections : /open répond 302 → URL présignée.

    La recette doit voir la redirection elle-même (contrat du PDF présigné),
    exactement comme la visionneuse du navigateur.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _appel(
    methode: str,
    chemin: str,
    corps: dict[str, Any] | None = None,
    entetes: dict[str, str] | None = None,
    brut: bool = False,
) -> tuple[int, Any, dict[str, str]]:
    """Appel HTTP vers l'API (jamais d'exception : retourne (code, corps, entêtes))."""
    url = f"{BASE}{chemin}"
    donnees = None
    entetes_totales = {"X-SEAMTECH-TOKEN": JETON}
    if entetes:
        entetes_totales.update(entetes)
    if corps is not None:
        donnees = json.dumps(corps).encode("utf-8")
        entetes_totales["Content-Type"] = "application/json"
    requete = urllib.request.Request(url, data=donnees, method=methode, headers=entetes_totales)
    ouvreur = urllib.request.build_opener(_SansRedirection())
    try:
        with ouvreur.open(requete, timeout=120) as reponse:
            brut_corps = reponse.read()
            entetes_reponse = dict(reponse.headers)
            code = reponse.status
    except urllib.error.HTTPError as erreur:
        brut_corps = erreur.read()
        entetes_reponse = dict(erreur.headers)
        code = erreur.code
    except Exception as erreur:  # noqa: BLE001 — la recette doit tout rapporter
        return 0, f"<erreur réseau : {erreur}>", {}
    if brut:
        return code, brut_corps, entetes_reponse
    try:
        return code, json.loads(brut_corps.decode("utf-8")), entetes_reponse
    except Exception:  # noqa: BLE001
        return code, brut_corps.decode("utf-8", "replace")[:400], entetes_reponse


def _extrait(texte: Any, limite: int = 240) -> str:
    """Extrait de sortie brute pour le rapport (jamais de secret)."""
    brut = texte if isinstance(texte, str) else json.dumps(texte, ensure_ascii=False, default=str)
    brut = re.sub(r"seamtech-[0-9a-f]{32}", "«secret»", brut)
    return re.sub(r"\s+", " ", brut)[:limite]


# ---------------------------------------------------------------------------
# Normalisation des sources → un dossier par affaire (lecture seule, RG13).
# ---------------------------------------------------------------------------


def _dossier_avec_pdf(racine: Path) -> Path | None:
    """Dossier le plus haut de ``racine`` contenant directement un PDF."""
    attente = [racine]
    while attente:
        courant = attente.pop(0)
        if any(f.is_file() and f.suffix.lower() == ".pdf" for f in courant.iterdir()):
            return courant
        attente.extend(sorted(f for f in courant.iterdir() if f.is_dir()))
    return None


def _normaliser_sources() -> list[str]:
    """Copie/extraction des sources vers TRAVAIL/affaires/<nom> (1 sous-dossier = 1 affaire)."""
    if TRAVAIL.exists():
        shutil.rmtree(TRAVAIL)
    racine = TRAVAIL / "affaires"
    racine.mkdir(parents=True)
    if not SOURCES.is_dir():
        raise FileNotFoundError(f"RECETTE_SOURCES introuvable : {SOURCES}")
    compteur = 0
    for item in sorted(SOURCES.iterdir()):
        if item.is_file() and item.suffix.lower() == ".zip":
            # Lecture seule de l'archive (RG13), garde traversee + taille max.
            with zipfile.ZipFile(item) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    cible = (racine / info.filename).resolve()
                    if not str(cible).startswith(str(racine.resolve())):
                        raise ValueError(f"traversée d'archive refusée : {info.filename}")
                    if info.file_size > 200 * 1024 * 1024:
                        raise ValueError(f"fichier > 200 Mo refusé : {info.filename}")
                    cible.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as src, cible.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        elif item.is_dir():
            cibles = []
            if any(f.is_file() and f.suffix.lower() == ".pdf" for f in item.iterdir()):
                cibles = [item]
            else:
                for sous in sorted(item.iterdir()):
                    if sous.is_dir():
                        dossier = _dossier_avec_pdf(sous)
                        if dossier is not None:
                            cibles.append(dossier)
            for dossier in cibles:
                compteur += 1
                shutil.copytree(dossier, racine / f"{item.name}-{compteur:02d}")
    affaires = sorted(p.name for p in racine.iterdir() if p.is_dir())
    return affaires


# ---------------------------------------------------------------------------
# Contrôles fonctionnels.
# ---------------------------------------------------------------------------


def _creer_compte() -> None:
    """Compte nominatif de recette : créé s'il n'existe pas, connexion prouvée."""
    if not MOT_DE_PASSE:
        _ligne("compte-recette", False, "RECETTE_MOT_DE_PASSE absente de l'environnement")
        return
    try:
        from seamtech_search.comptes.comptes import ROLES, creer_utilisateur
        from seamtech_search.config import AppConfig
        from seamtech_search.indexer import SearchIndex

        config = AppConfig.load("config/config.json")
        index = SearchIndex(
            config.database_path,
            config.database_url,
            pool_min=config.pool_min,
            pool_max=config.pool_max,
            pool_timeout=config.pool_timeout,
            statement_timeout_ms=config.statement_timeout_ms,
        )
        try:
            creer_utilisateur(
                index,
                UTILISATEUR,
                "Compte de recette locale",
                MOT_DE_PASSE,
                role=ROLES[0],
                doit_changer_mot_de_passe=True,
            )
            creation = "créé"
        except Exception as erreur:  # noqa: BLE001 — 409 = déjà présent (rejeu)
            creation = f"déjà présent ({_extrait(erreur, 80)})"
        index.close()
    except Exception as erreur:  # noqa: BLE001
        _ligne("compte-recette", False, f"bootstrap du compte impossible : {_extrait(erreur)}")
        return
    code, corps, _ = _appel(
        "POST",
        "/auth/connexion",
        {"identifiant": UTILISATEUR, "mot_de_passe": MOT_DE_PASSE},
    )
    ok = code == 200 and isinstance(corps, dict) and corps.get("id_session")
    _ligne(
        "compte-recette",
        ok,
        f"compte « {UTILISATEUR} » {creation} ; connexion HTTP {code} {_extrait(corps, 120)}",
    )


def _deposer(affaires: list[str]) -> int | None:
    code, corps, _ = _appel(
        "POST",
        "/imports/dossier/lot",
        {"racine": str(TRAVAIL / "affaires"), "notes": "recette locale"},
        entetes={"X-SEAMTECH-BACKGROUND": "true"},
    )
    id_lot = corps.get("id_lot") if isinstance(corps, dict) else None
    ok = code == 202 and isinstance(id_lot, int)
    _ligne(
        "depot-archives",
        ok,
        f"{len(affaires)} affaire(s) : {affaires[:8]} — lot HTTP {code} id={id_lot} {_extrait(corps, 120)}",
    )
    _info("nb_affaires", str(len(affaires)))
    return int(id_lot) if ok else None


def _suivre_lot(id_lot: int | None) -> dict[str, Any] | None:
    if id_lot is None:
        _ligne("suivi-lots", False, "aucun lot à suivre (dépôt en échec)")
        return None
    debut = time.time()
    echantillons: list[str] = []
    etat: dict[str, Any] = {}
    while time.time() - debut < TIMEOUT_LOT:
        code, corps, _ = _appel("GET", f"/lots/{id_lot}")
        if code == 200 and isinstance(corps, dict):
            etat = corps
            progression = corps.get("progression_pct")
            echantillons.append(f"{progression}%({corps.get('nb_traites')}/{corps.get('nb_dossiers')})")
            if corps.get("statut") in {"termine", "interrompu"}:
                break
        time.sleep(5)
    statut = etat.get("statut")
    nb_dossiers = int(etat.get("nb_dossiers") or 0)
    nb_traites = int(etat.get("nb_traites") or 0)
    nb_echecs = int(etat.get("nb_echecs") or 0)
    ok = statut == "termine" and nb_echecs == 0 and nb_traites == nb_dossiers and nb_dossiers > 0
    progression_visible = len(echantillons) > 1
    # État des lignes : « tout reste en_attente » = le traitement n'a rien
    # produit (thread mort ou bloqué) — ce détail distingue un lot SLOW d'un
    # lot MORT sans avoir à lire les journaux du conteneur.
    lignes = [
        f"{d.get('id_lot_dossier')}:{d.get('statut')}"
        + (f"({str(d.get('raison') or '')[:32]})" if d.get("raison") else "")
        for d in etat.get("dossiers", [])
        if isinstance(d, dict)
    ]
    _ligne(
        "suivi-lots",
        ok,
        f"lot #{id_lot} statut={statut} {nb_traites}/{nb_dossiers} échecs={nb_echecs} "
        f"lignes={lignes[:4]}{'…' if len(lignes) > 4 else ''} "
        f"progression observée : {echantillons[:6]}{'…' if progression_visible else ' (lot terminé avant échantillonnage)'}",
    )
    return etat


def _fiches_a_valider(nb_attendu: int) -> list[str]:
    """Cohorte déposée : fiches a_valider (badgées) + déjà validées (rejeu).

    Idempotent : une re-recette ne doit pas échouer parce qu'une fiche du
    premier passage a été validée — la cohorte compte les deux statuts.
    """
    code, corps, _ = _appel("GET", "/fiches?statut=a_valider&taille=100")
    fiches = corps.get("fiches", []) if isinstance(corps, dict) else []
    codes = [f.get("code", "") for f in fiches if isinstance(f, dict)]
    badgees = all(f.get("statut") == "a_valider" for f in fiches if isinstance(f, dict))
    total = int(corps.get("total") or 0) if isinstance(corps, dict) else 0
    code_v, corps_v, _ = _appel("GET", "/fiches?statut=valide&taille=100")
    valides = [f.get("code", "") for f in (corps_v.get("fiches", []) if isinstance(corps_v, dict) else [])]
    cohorte = sorted(set(codes) | set(valides))
    ok = code == 200 and badgees and len(cohorte) >= nb_attendu
    _ligne(
        "fiches-a-valider",
        ok,
        f"HTTP {code} a_valider={total} (toutes badgées={badgees}) + valides={len(valides)} "
        f"= cohorte {len(cohorte)} (attendu ≥ {nb_attendu}) codes={cohorte[:8]}",
    )
    return cohorte


def _valider_une_fiche(codes: list[str]) -> str | None:
    if not codes:
        _ligne("validation-fiche", False, "aucune fiche à valider")
        return None
    # Cible DÉTERMINISTE (première de la cohorte triée) : une re-recette
    # valide la MÊME fiche — 409 « déjà validée » = succès d'idempotence.
    cible = codes[0]
    code_http, corps, _ = _appel("POST", f"/fiches/{urllib.parse.quote(cible)}/valider", {})
    code2, corps2, _ = _appel("GET", "/fiches?statut=valide&taille=100")
    valides = (
        [f.get("code") for f in corps2.get("fiches", [])]
        if isinstance(corps2, dict)
        else []
    )
    ok = code_http in {200, 409} and cible in valides  # 409 = déjà validée (rejeu)
    _ligne(
        "validation-fiche",
        ok,
        f"fiche {cible} : validation HTTP {code_http} {_extrait(corps, 80)} ; "
        f"statut=valide HTTP {code2} contient {cible} = {cible in valides}",
    )
    _info("fiche_validee", cible)
    return cible


def _recherche_texte(codes: list[str]) -> None:
    if not codes:
        _ligne("recherche-texte", False, "aucune fiche pour tirer un terme réel")
        return
    code_fiche = codes[0]
    _, liste, _ = _appel("GET", "/fiches?taille=100")
    fiche = next(
        (f for f in (liste.get("fiches", []) if isinstance(liste, dict) else []) if f.get("code") == code_fiche),
        {},
    )
    candidats = [str(fiche.get(c) or "") for c in ("bateau", "client", "titre", "gabarit")]
    terme = ""
    for valeur in candidats:
        mots = [m for m in re.findall(r"[A-Za-zÀ-ÿ0-9]{4,}", valeur)]
        if mots:
            terme = mots[0]
            break
    if not terme:
        _ligne("recherche-texte", False, f"aucun terme exploitable sur {code_fiche} : {fiche}")
        return
    code, corps, _ = _appel("GET", f"/recherche?q={urllib.parse.quote(terme)}&limit=10&inclure_a_valider=true")
    resultats = corps.get("resultats", []) if isinstance(corps, dict) else []
    codes_trouves = [r.get("code") for r in resultats if isinstance(r, dict)]
    ok = code == 200 and code_fiche in codes_trouves
    _ligne(
        "recherche-texte",
        ok,
        f"terme réel « {terme} » (fiche {code_fiche}) : HTTP {code} nb={corps.get('nb_resultats') if isinstance(corps, dict) else '?'} "
        f"top10={codes_trouves[:10]}",
    )


def _recherche_dimension(codes: list[str], index: Any) -> None:
    sous_details: list[str] = []
    # (a) mécanisme « 6,60 » : valeur seule → dimension_active (±0,5 %).
    code, corps, _ = _appel("GET", f"/recherche?q={urllib.parse.quote('6,60')}&limit=5")
    dim = corps.get("dimension_active") if isinstance(corps, dict) else None
    mecanisme = (
        code == 200
        and isinstance(dim, dict)
        and abs(float(dim.get("valeur") or 0) - 6.6) < 1e-9
        and float(dim.get("tolerance_pct") or 0) == 0.5
    )
    sous_details.append(f"« 6,60 » → dimension_active={_extrait(dim, 100)} OK={mecanisme}")
    # (b) cote nommée « SLU 6,60 » — la cote canonique retournée est « slu_m ».
    code, corps, _ = _appel("GET", f"/recherche?q={urllib.parse.quote('SLU 6,60')}&limit=5")
    dim2 = corps.get("dimension_active") if isinstance(corps, dict) else None
    nommee = code == 200 and isinstance(dim2, dict) and dim2.get("cote") in {"slu", "slu_m"}
    sous_details.append(f"« SLU 6,60 » → cote={dim2.get('cote') if isinstance(dim2, dict) else None} OK={nommee}")
    # (c) valeur RÉELLE issue du corpus : la fiche propriétaire doit remonter.
    reel_ok = True
    try:
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT id_fiche, jeu, slu_m, sle_m, sf_m, shw_m, spa_m2, tetiere_cm, poids_kg "
                    "FROM fiche_cotes LIMIT 5"
                )
                lignes = cursor.fetchall()
    except Exception as erreur:  # noqa: BLE001
        lignes = []
        sous_details.append(f"cotes en base illisibles : {_extrait(erreur, 80)}")
    if lignes:
        noms_cotes = ("slu_m", "sle_m", "sf_m", "shw_m", "spa_m2", "tetiere_cm", "poids_kg")
        for ligne in lignes:
            for nom, valeur in zip(noms_cotes, ligne[2:]):
                if valeur is None:
                    continue
                requete = f"{float(valeur):.2f}".replace(".", ",")
                code, corps, _ = _appel(
                    "GET", f"/recherche?q={urllib.parse.quote(requete)}&limit=10&inclure_a_valider=true"
                )
                resultats = corps.get("resultats", []) if isinstance(corps, dict) else []
                codes_trouves = [r.get("code") for r in resultats if isinstance(r, dict)]
                nb = corps.get("nb_resultats") if isinstance(corps, dict) else "?"
                sous_details.append(f"valeur réelle « {requete} » ({nom}) → nb={nb} top10={codes_trouves[:6]}")
                if not codes_trouves:
                    reel_ok = False
                break
            else:
                continue
            break
    else:
        sous_details.append("NON MESURÉ : aucune cote en base sur ce corpus")
    ok = mecanisme and nommee and reel_ok
    _ligne("recherche-dimension", ok, " ; ".join(sous_details))


def _filtres_facettes() -> None:
    code, corps, _ = _appel("GET", "/recherche?q=&limit=20&inclure_a_valider=true")
    facettes = corps.get("facettes") if isinstance(corps, dict) else None
    groupes_vides = {
        cle: len(valeurs)
        for cle, valeurs in (facettes or {}).items()
        if isinstance(valeurs, list) and valeurs
    }
    a_des_facettes = isinstance(facettes, dict) and bool(groupes_vides)
    # Badge : inclure_a_valider=false doit EXCLURE les fiches encore non
    # vérifiées (la fiche validée plus haut a le droit d'y figurer).
    _, liste, _ = _appel("GET", "/fiches?statut=a_valider&taille=100")
    encore_a_valider = [
        f.get("code") for f in (liste.get("fiches", []) if isinstance(liste, dict) else [])
    ]
    code2, corps2, _ = _appel("GET", "/recherche?q=&limit=20&inclure_a_valider=false")
    resultats_valides = (
        [r.get("code") for r in corps2.get("resultats", []) if isinstance(r, dict)]
        if isinstance(corps2, dict)
        else []
    )
    exclues = bool(encore_a_valider) and all(c not in resultats_valides for c in encore_a_valider)
    ok = code == 200 and a_des_facettes and code2 == 200 and exclues
    _ligne(
        "filtres-facettes",
        ok,
        f"facettes présentes={a_des_facettes} ({_extrait(facettes, 80)}) ; "
        f"{len(encore_a_valider)} fiche(s) a_valider exclues de inclure_a_valider=false={exclues}",
    )


def _suggestions() -> None:
    _, liste, _ = _appel("GET", "/fiches?taille=5")
    fiches = liste.get("fiches", []) if isinstance(liste, dict) else []
    prefixe = ""
    for f in fiches:
        for cle in ("bateau", "client", "code"):
            valeur = str(f.get(cle) or "")
            if len(valeur) >= 3:
                prefixe = valeur[:3]
                break
        if prefixe:
            break
    if not prefixe:
        _ligne("suggestions", False, "aucun préfixe exploitable (corpus vide ?)")
        return
    code, corps, _ = _appel("GET", f"/recherche/suggestions?prefix={urllib.parse.quote(prefixe)}&limit=10")
    suggestions = corps.get("suggestions", []) if isinstance(corps, dict) else []
    ok = code == 200 and len(suggestions) >= 1
    _ligne(
        "suggestions",
        ok,
        f"préfixe réel « {prefixe} » : HTTP {code} {len(suggestions)} suggestion(s) {_extrait(suggestions, 120)}",
    )


def _pdf_presigne(codes: list[str]) -> Path | None:
    if not codes:
        _ligne("pdf-presigne", False, "aucune fiche")
        return None
    cible = codes[0]
    code, corps, _ = _appel("GET", f"/fiches/{urllib.parse.quote(cible)}/pieces")
    pdf_source = corps.get("pdf_source") if isinstance(corps, dict) else None
    if not pdf_source:
        _ligne("pdf-presigne", False, f"pieces HTTP {code} sans pdf_source {_extrait(corps, 100)}")
        return None
    code, _, entetes = _appel("POST", f"/open?path={urllib.parse.quote(str(pdf_source))}", {})
    location = entetes.get("Location") or entetes.get("location")
    if code != 302 or not location:
        _ligne("pdf-presigne", False, f"open HTTP {code} (attendu 302) location={location}")
        return None
    try:
        with urllib.request.urlopen(location, timeout=60) as telechargement:
            contenu = telechargement.read()
        local = Path(str(pdf_source)).read_bytes()
        meme = hashlib.sha256(contenu).hexdigest() == hashlib.sha256(local).hexdigest()
        _ligne(
            "pdf-presigne",
            meme,
            f"302 → téléchargement {len(contenu)} octets ; SHA-256 identique à la source = {meme}",
        )
        return Path(str(pdf_source))
    except Exception as erreur:  # noqa: BLE001
        _ligne("pdf-presigne", False, f"téléchargement de l'URL présignée impossible : {_extrait(erreur)}")
        return None


def _zone_surlignee(codes: list[str]) -> None:
    if not codes:
        _ligne("zone-surlignee", False, "aucune fiche")
        return
    cible = codes[0]
    code, corps, _ = _appel("GET", f"/fiches/{urllib.parse.quote(cible)}/champs")
    champs = corps if isinstance(corps, list) else (corps.get("champs", []) if isinstance(corps, dict) else [])
    avec_zone = [
        c
        for c in champs
        if isinstance(c, dict) and c.get("page") is not None and c.get("zone") is not None
    ]
    ok = code == 200 and len(avec_zone) >= 1
    _ligne(
        "zone-surlignee",
        ok,
        f"fiche {cible} : HTTP {code} {len(champs)} champ(s), {len(avec_zone)} avec page+zone "
        f"(ex: {_extrait(avec_zone[0] if avec_zone else champs[:1], 120)})",
    )


def _rejeu(nb_attendu: int) -> None:
    _, avant, _ = _appel("GET", "/fiches?taille=1")
    total_avant = int(avant.get("total") or 0) if isinstance(avant, dict) else -1
    code, corps, _ = _appel(
        "POST",
        "/imports/dossier/lot",
        {"racine": str(TRAVAIL / "affaires"), "notes": "rejeu idempotent"},
        entetes={"X-SEAMTECH-BACKGROUND": "true"},
    )
    id_lot = corps.get("id_lot") if isinstance(corps, dict) else None
    statuts: list[str] = []
    if isinstance(id_lot, int):
        fin = time.time() + 300
        while time.time() < fin:
            code2, etat, _ = _appel("GET", f"/lots/{id_lot}")
            if code2 == 200 and isinstance(etat, dict):
                statuts = [d.get("statut") for d in etat.get("dossiers", []) if isinstance(d, dict)]
                if etat.get("statut") in {"termine", "interrompu"}:
                    break
            time.sleep(3)
    _, apres, _ = _appel("GET", "/fiches?taille=1")
    total_apres = int(apres.get("total") or 0) if isinstance(apres, dict) else -1
    ok = total_avant == total_apres and total_apres >= nb_attendu and code == 202
    _ligne(
        "rejeu-idempotent",
        ok,
        f"rejeu du lot : HTTP {code} ; fiches avant={total_avant} après={total_apres} "
        f"(inchangé = {total_avant == total_apres}) ; lignes du 2e lot = {statuts[:8]}",
    )


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def main() -> int:
    if not JETON:
        print("CONTROLE|compte-recette|FAIL|SEAMTECH_AUTH_TOKEN absente", flush=True)
        return 1
    # Santé de l'API (déjà contrôlée par l'orchestrateur ; double vérification).
    code, corps, _ = _appel("GET", "/health")
    if code != 200:
        print(f"CONTROLE|compte-recette|FAIL|API /health indisponible (HTTP {code}) {_extrait(corps, 80)}", flush=True)
        return 1

    _creer_compte()
    try:
        affaires = _normaliser_sources()
    except Exception as erreur:  # noqa: BLE001
        _ligne("depot-archives", False, f"normalisation des sources impossible : {_extrait(erreur)}")
        affaires = []
    id_lot = _deposer(affaires) if affaires else None
    if not affaires:
        _ligne("depot-archives", False, f"aucune affaire exploitable dans {SOURCES}")
    _suivre_lot(id_lot)

    codes = _fiches_a_valider(len(affaires)) if affaires else []
    fiche_validee = _valider_une_fiche(codes)
    _recherche_texte(codes)
    try:
        from seamtech_search.config import AppConfig
        from seamtech_search.indexer import SearchIndex

        config = AppConfig.load("config/config.json")
        index = SearchIndex(
            config.database_path,
            config.database_url,
            pool_min=config.pool_min,
            pool_max=config.pool_max,
            pool_timeout=config.pool_timeout,
            statement_timeout_ms=config.statement_timeout_ms,
        )
        _recherche_dimension(codes, index)
        index.close()
    except Exception as erreur:  # noqa: BLE001
        _ligne("recherche-dimension", False, f"index inutilisable : {_extrait(erreur)}")
    _filtres_facettes()
    _suggestions()
    _pdf_presigne(codes)
    _zone_surlignee(codes)
    _rejeu(len(affaires))

    if fiche_validee:
        _info("fiche_pour_restauration", fiche_validee)
    print(f"INFO|controles_echoues|{','.join(ECHECS) if ECHECS else 'aucun'}", flush=True)
    return 1 if ECHECS else 0


if __name__ == "__main__":
    sys.exit(main())
