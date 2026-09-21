"""Inventaire en lecture seule d'une arborescence d'archive SEAMTECH (Phase 0).

Parcourt un ou plusieurs chemins racine SANS AUCUNE ÉCRITURE dans l'archive et
produit un rapport chiffré :

- nombre de dossiers et de fichiers, volume total ;
- répartition par type (extension) et par année (date de modification) ;
- doublons probables (même taille + même empreinte SHA-256) ;
- part de PDF natifs vs PDF scannés probables (aucun texte extractible) ;
- localisation probable des fiches techniques (ancres déjà utilisées par
  l'application : voir seamtech_search/anchors.py) ;
- familles de gabarits : regroupement des fiches par empreinte de libellés
  et de positions (c'est l'inconnue majeure du projet — ce recensement
  alimente le registre de gabarits du lot B).

Sorties : console (lisible), dossier de rapport avec
``inventaire.json`` (agrégats), ``inventaire_fichiers.csv`` (une ligne par
fichier) et ``inventaire_doublons.csv``. Les CSV utilisent le séparateur ``;``
et l'encodage utf-8-sig pour s'ouvrir directement dans Excel côté bureau.

Sécurité :
- aucun dossier n'est créé, déplacé, renommé ou supprimé dans les racines
  analysées ; les fichiers ne sont ouverts qu'en lecture (``rb``) ;
- le dossier de rapport est refusé s'il se trouve à l'intérieur d'une racine
  analysée (ou si une racine se trouve à l'intérieur du dossier de rapport) ;
- aucune extraction OCR : les fiches récentes portent leur texte dans le PDF ;
- aucun appel réseau.

Usage :
    python scripts/inventaire_archive.py CHEMIN_RACINE [CHEMIN2 ...]
        [--sortie DOSSIER] [--limite-empreinte MO] [--sans-empreintes]

Codes de retour : 0 rapport produit, 2 erreur d'usage (racine absente,
dossier de sortie mal placé).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from seamtech_search.anchors import classify_pdf_text  # noqa: E402
from seamtech_search.config import AppConfig  # noqa: E402
from seamtech_search.detection_fiches import ResultatDetection, detecter_fiche  # noqa: E402
from seamtech_search.extractors import extract_file  # noqa: E402
from seamtech_search.lexique import CHEMIN_LEXIQUE_PAR_DEFAUT, LexiqueFiches, charger_lexique  # noqa: E402

LOGGER = logging.getLogger("seamtech_search.inventaire")

# Position quantifiée pour l'empreinte de gabarit : un pas de 6 points absorbe
# les micro-écarts de crénage entre deux exports « identiques » d'un même
# gabarit, tout en distinguant deux mises en page réellement différentes.
BUCKET_POSITION_PT = 6.0
# Deux familles dont les ensembles de libellés recouvrent au moins cette
# proportion (Jaccard) sont fusionnées : ce sont des variantes du même gabarit
# (une ligne ajoutée ne doit pas créer une fausse famille).
SEUIL_JACCARD_FAMILLE = 0.85
# Nombre d'erreurs conservées intégralement dans le JSON (la suite n'est que
# comptée) afin que le rapport reste lisible sur une archive de 100 000 fichiers.
MAX_ERREURS_JSON = 500
# Nombre d'exemples de fichiers cités par famille de gabarit.
MAX_EXEMPLES_FAMILLE = 3
TAILLE_CHUNK_EMPREINTE = 1024 * 1024


def sans_accents(texte: str) -> str:
    """Retire les accents (NFD puis suppression des signes diacritiques)."""
    decompose = unicodedata.normalize("NFD", texte)
    return "".join(caractere for caractere in decompose if not unicodedata.combining(caractere))


def normaliser_mot(mot: str) -> str:
    """Minuscules sans accents, espaces comprimés — base des empreintes."""
    return " ".join(sans_accents(mot).lower().split())


def format_octets(nombre: int) -> str:
    """Taille humaine lisible en unités binaires (ko, Mo, Go…)."""
    valeur = float(nombre)
    for unite in ("o", "ko", "Mo", "Go", "To"):
        if abs(valeur) < 1024.0 or unite == "To":
            if unite == "o":
                return f"{int(valeur)} {unite}"
            return f"{valeur:.1f} {unite}".replace(".", ",")
        valeur /= 1024.0
    return f"{nombre} o"


@dataclass
class LigneFichier:
    """Une ligne d'inventaire : tout ce qu'on sait d'un fichier sans le modifier."""

    chemin: str
    taille: int
    annee: int
    extension: str
    categorie_pdf: str = ""  # technique | plan | scan_probable | erreur_extraction | "" (vue classifieur)
    statut_extraction: str = ""  # statut brut d'ExtractionResult pour les PDF
    detail: str = ""
    temps_extraction_ms: int = 0
    detection: ResultatDetection | None = None  # vue structurelle (PDF natifs uniquement)
    empreinte: str = ""  # sha256 du contenu ("" si non calculée)
    groupe_doublon: int = 0  # 0 = pas un doublon ; sinon numéro de groupe (>= 1)



@dataclass
class FamilleGabarit:
    """Une famille de fiches supposées partager le même gabarit."""

    cle: str
    libelles: list[str] = field(default_factory=list)
    fichiers: list[str] = field(default_factory=list)
    empreintes_fines: set[str] = field(default_factory=set)

    def vers_dict(self) -> dict[str, Any]:
        return {
            "cle": self.cle,
            "nb_pdf": len(self.fichiers),
            "nb_libelles": len(self.libelles),
            "libelles_communs": self.libelles[:80],
            "empreintes_fines_distinctes": len(self.empreintes_fines),
            "exemples": self.fichiers[:MAX_EXEMPLES_FAMILLE],
        }


def empreinte_sha256(chemin: Path, limite_octets: int) -> str:
    """Empreinte SHA-256 d'un fichier lu par morceaux, en lecture seule.

    Les fichiers plus lourds que *limite_octets* renvoient un marqueur
    explicite : l'empreinte complète est trop coûteuse sur l'archive entière,
    et la taille seule suffit déjà à suspecter un doublon.
    """
    taille = chemin.stat().st_size
    if taille > limite_octets:
        return "non_calculé_fichier_lourd"
    digest = hashlib.sha256()
    with chemin.open("rb") as fichier:
        for bloc in iter(lambda: fichier.read(TAILLE_CHUNK_EMPREINTE), b""):
            digest.update(bloc)
    return digest.hexdigest()


@dataclass
class PageDetectee:
    """Contenu positionnel de la page 1 d'un PDF natif (lecture seule)."""

    mots: list[dict[str, Any]] = field(default_factory=list)
    largeur: float = 0.0
    hauteur: float = 0.0
    grille_tracee: bool = False  # vraie grille lignes/rects vue par pdfplumber


def analyser_pdf(chemin: Path, config: AppConfig) -> tuple[str, str, str, int, PageDetectee]:
    """Classe un PDF (vue classifieur) et rend sa page 1 pour la vue structurelle.

    Retourne ``(categorie, statut, detail, temps_ms, page)``. ``page`` porte les
    mots positionnés, les dimensions et la présence d'une grille tracée pour
    TOUS les PDF natifs (technique comme plan) : la détection structurelle ne
    doit pas hériter de l'angle mort du classifieur.
    """
    debut = time.perf_counter()
    resultat = extract_file(
        chemin,
        max_chars=config.max_extract_chars,
        max_file_size_bytes=config.max_file_size_bytes,
        enable_ocr=False,  # Phase 0 : jamais d'OCR, le texte est dans les PDF récents
    )
    temps_ms = int((time.perf_counter() - debut) * 1000)
    if resultat.status == "unavailable" and "no embedded text" in (resultat.detail or "").lower():
        return "scan_probable", resultat.status, resultat.detail, temps_ms, PageDetectee()
    if resultat.status != "extracted":
        LOGGER.warning(
            "Extraction impossible (%s) : %s — conséquence : PDF compté en erreur, hors fiches et hors scans.",
            chemin,
            resultat.detail or resultat.status,
        )
        return "erreur_extraction", resultat.status, resultat.detail, temps_ms, PageDetectee()
    texte = resultat.text
    if not texte.strip():
        # Filet de sécurité : extrait mais vide, même conclusion qu'un scan.
        return "scan_probable", resultat.status, "", temps_ms, PageDetectee()
    categorie = "technique" if classify_pdf_text(texte) == "technical_pdf" else "plan"
    return categorie, resultat.status, "", temps_ms, analyser_page_pdf(chemin)


def analyser_page_pdf(chemin: Path, numero_page: int = 0) -> PageDetectee:
    """Mots, dimensions et grille tracée d'une page via pdfplumber (lecture seule).

    Un échec ici n'empêche pas l'inventaire : le PDF garde sa classification
    par le texte, mais sort de la vue structurelle, avec un avertissement.
    """
    try:
        import pdfplumber  # import paresseux : inutile si aucune fiche technique
    except ImportError as exc:  # pragma: no cover - dépend de l'installation
        LOGGER.error(
            "pdfplumber indisponible (%s) — conséquence : détection structurelle et empreintes de gabarit absentes.",
            exc,
        )
        return PageDetectee()
    try:
        with pdfplumber.open(chemin) as pdf:
            if numero_page >= len(pdf.pages):
                return PageDetectee()
            page = pdf.pages[numero_page]
            mots = page.extract_words() or []
            tables = page.extract_tables() or []
            grille = any(
                (cellule or "").strip() for table in tables for ligne in table for cellule in ligne
            )
            return PageDetectee(
                mots=mots,
                largeur=float(page.width or 0.0),
                hauteur=float(page.height or 0.0),
                grille_tracee=grille,
            )
    except Exception as exc:  # noqa: BLE001 - un PDF corrompu ne doit pas tuer l'inventaire
        LOGGER.warning(
            "Lecture des positions impossible (%s) : %s — conséquence : fiche hors vue structurelle et hors familles.",
            chemin,
            exc,
        )
        return PageDetectee()


def empreinte_gabarit(
    mots: list[dict[str, Any]], largeur_page: float = 0.0, hauteur_page: float = 0.0
) -> dict[str, Any] | None:
    """Empreinte structurelle d'une fiche à partir des mots de sa page 1.

    Deux niveaux :
    - ``cle_famille`` : empreinte de l'ensemble des libellés (mots alphabétiques
      normalisés, sans les valeurs numériques) — deux fiches du même gabarit
      partagent presque toujours le même vocabulaire ;
    - ``empreinte_fine`` : empreinte des mots ET de leurs positions quantifiées
      — elle distingue deux mises en page différentes au sein d'une famille.

    Les mots purement alphabétiques servent aux deux niveaux (les valeurs
    numériques — codes, cotes, grammages — sont exclues, et deux fiches du
    même gabarit aux valeurs différentes tombent alors dans la même famille) ;
    les mots alphabétiques portant une valeur (ex. un tissu « Dacron ») peuvent
    rester dans l'empreinte : c'est voulu et documenté — une variante qui
    change un libellé doit créer une famille distincte, c'est exactement
    ce que la Phase 0 cherche à recenser. La fusion Jaccard absorbe les
    écarts mineurs de vocabulaire.
    """
    if not mots:
        return None
    libelles: set[str] = set()
    elements_fins: list[str] = []
    for mot in mots:
        texte = str(mot.get("text", "")).strip()
        if not texte:
            continue
        normalise = normaliser_mot(texte)
        alpha = sans_accents(texte)
        # Libellés = mots purement alphabétiques (≥ 3 lettres). Les valeurs
        # (codes, cotes, grammages) contiennent presque toujours un chiffre :
        # les exclure fait que deux fiches du même gabarit, aux valeurs
        # différentes, partagent la même famille — c'est le but.
        if len(normalise) >= 3 and alpha.isalpha():
            libelles.add(normalise)
            try:
                if largeur_page > 0 and hauteur_page > 0:
                    position_x = int(float(mot.get("x0", 0.0)) / largeur_page * 50)
                    position_y = int(float(mot.get("top", 0.0)) / hauteur_page * 50)
                else:
                    position_x = int(round(float(mot.get("x0", 0.0)) / BUCKET_POSITION_PT))
                    position_y = int(round(float(mot.get("top", 0.0)) / BUCKET_POSITION_PT))
            except (TypeError, ValueError):
                LOGGER.debug("Position illisible pour un mot de l'empreinte ; mot gardé sans position.")
                position_x, position_y = -1, -1
            elements_fins.append(f"{position_x}:{position_y}:{normalise}")
    if not libelles:
        return None
    libelles_tries = sorted(libelles)
    return {
        "cle_famille": hashlib.sha256("|".join(libelles_tries).encode("utf-8")).hexdigest(),
        "libelles": libelles_tries,
        "empreinte_fine": hashlib.sha256("|".join(sorted(elements_fins)).encode("utf-8")).hexdigest(),
    }


def similarite_jaccard(ensemble_a: set[str], ensemble_b: set[str]) -> float:
    """Similarité de Jaccard entre deux ensembles de libellés."""
    if not ensemble_a or not ensemble_b:
        return 0.0
    intersection = len(ensemble_a & ensemble_b)
    union = len(ensemble_a | ensemble_b)
    return intersection / union if union else 0.0


def regrouper_familles(empreintes: list[tuple[str, dict[str, Any]]]) -> list[FamilleGabarit]:
    """Regroupe les empreintes en familles (exact puis fusion Jaccard).

    *empreintes* : liste ``(chemin, empreinte_gabarit(...))``. Le regroupement
    est déterministe : clés triées, puis fusion gloutonne dans cet ordre.
    """
    par_cle: dict[str, FamilleGabarit] = {}
    for chemin, empreinte in sorted(empreintes, key=lambda item: item[0]):
        cle = empreinte["cle_famille"]
        famille = par_cle.get(cle)
        if famille is None:
            famille = FamilleGabarit(cle=cle, libelles=list(empreinte["libelles"]))
            par_cle[cle] = famille
        famille.fichiers.append(chemin)
        famille.empreintes_fines.add(empreinte["empreinte_fine"])

    familles = sorted(par_cle.values(), key=lambda f: (-len(f.fichiers), f.cle))
    fusionnees: list[FamilleGabarit] = []
    for famille in familles:
        ensemble = set(famille.libelles)
        cible: FamilleGabarit | None = None
        for candidate in fusionnees:
            if similarite_jaccard(ensemble, set(candidate.libelles)) >= SEUIL_JACCARD_FAMILLE:
                cible = candidate
                break
        if cible is None:
            fusionnees.append(famille)
            continue
        cible.fichiers.extend(famille.fichiers)
        cible.empreintes_fines |= famille.empreintes_fines
        nouveaux = set(famille.libelles) - set(cible.libelles)
        if nouveaux:
            cible.libelles.extend(sorted(nouveaux))
        LOGGER.debug(
            "Famille %s fusionnée avec %s (%d PDF) : libellés recouverts à %.0f %%.",
            famille.cle[:12],
            cible.cle[:12],
            len(famille.fichiers),
            SEUIL_JACCARD_FAMILLE * 100,
        )
    return fusionnees


def verifier_sortie_hors_archive(sortie: Path, racines: list[Path]) -> None:
    """Refuse un dossier de rapport qui toucherait l'archive (lecture seule)."""
    sortie_resolue = sortie.resolve()
    for racine in racines:
        racine_resolue = racine.resolve()
        if sortie_resolue == racine_resolue or racine_resolue in sortie_resolue.parents:
            raise ValueError(
                f"Le dossier de rapport {sortie_resolue} est à l'intérieur de la racine analysée "
                f"{racine_resolue} : l'archive est en lecture seule, choisissez une sortie ailleurs."
            )
        if sortie_resolue in racine_resolue.parents:
            raise ValueError(
                f"La racine analysée {racine_resolue} est à l'intérieur du dossier de rapport "
                f"{sortie_resolue} : choisissez une sortie ailleurs."
            )


@dataclass
class RapportInventaire:
    """Agrégats produits par le parcours de l'archive."""

    racines: list[str]
    nb_dossiers: int = 0
    nb_fichiers: int = 0
    volume_total: int = 0
    par_type: dict[str, dict[str, int]] = field(default_factory=dict)
    par_annee: dict[str, dict[str, int]] = field(default_factory=dict)
    pdf_natifs: int = 0
    pdf_techniques: int = 0
    pdf_plans: int = 0
    pdf_scans_probables: int = 0
    pdf_erreurs: int = 0
    doublons_groupes: int = 0
    doublons_fichiers_redondants: int = 0
    doublons_octets_redondants: int = 0
    fichiers_non_empreintes: int = 0
    nb_candidats_structurels: int = 0
    localisations_fiches: list[dict[str, Any]] = field(default_factory=list)
    familles: list[FamilleGabarit] = field(default_factory=list)
    section_detection: dict[str, Any] = field(default_factory=dict)
    erreurs: list[str] = field(default_factory=list)
    duree_secondes: float = 0.0

    def vers_dict(self) -> dict[str, Any]:
        return {
            "meta": {
                "horodatage": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "duree_secondes": round(self.duree_secondes, 2),
            },
            "racines": self.racines,
            "nb_dossiers": self.nb_dossiers,
            "nb_fichiers": self.nb_fichiers,
            "volume_total_octets": self.volume_total,
            "par_type": dict(sorted(self.par_type.items())),
            "par_annee": dict(sorted(self.par_annee.items())),
            "pdf": {
                "natifs": self.pdf_natifs,
                "techniques": self.pdf_techniques,
                "plans": self.pdf_plans,
                "scans_probables": self.pdf_scans_probables,
                "erreurs_extraction": self.pdf_erreurs,
            },
            "doublons": {
                "groupes": self.doublons_groupes,
                "fichiers_redondants": self.doublons_fichiers_redondants,
                "octets_redondants": self.doublons_octets_redondants,
                "fichiers_non_empreintes": self.fichiers_non_empreintes,
            },
            "fiches_techniques": {
                "nb": self.pdf_techniques,
                "localisations_principales": self.localisations_fiches[:10],
            },
            "detection_structurelle": self.section_detection,
            "familles_gabarits": [famille.vers_dict() for famille in self.familles],
            "erreurs": self.erreurs[:MAX_ERREURS_JSON],
            "erreurs_supprimees": max(0, len(self.erreurs) - MAX_ERREURS_JSON),
        }


def construire_section_detection(
    lignes: list[LigneFichier], lexique: LexiqueFiches, chemin_lexique: str
) -> dict[str, Any]:
    """Section « detection_structurelle » du rapport : candidats + désaccords.

    Le livrable anti-angle-mort : les documents que la détection structurelle
    voit comme fiches alors que le classifieur actuel les range ailleurs
    (``rates_par_le_classifieur``), et l'inverse (fiches du classifieur sans
    structure détectable). Chaque entrée est expliquée (score, termes).
    """
    candidats = [ligne for ligne in lignes if ligne.detection is not None and ligne.detection.est_candidat]
    rates = [ligne for ligne in candidats if ligne.categorie_pdf != "technique"]
    techniques_sans_structure = [
        ligne
        for ligne in lignes
        if ligne.categorie_pdf == "technique" and ligne.detection is not None and not ligne.detection.est_candidat
    ]

    def entree(ligne: LigneFichier) -> dict[str, Any]:
        assert ligne.detection is not None  # filtré par les appelants
        return {
            "chemin": ligne.chemin,
            "categorie_classifieur": ligne.categorie_pdf,
            **ligne.detection.composantes(),
        }

    return {
        "lexique": {
            "version": lexique.version,
            "nb_termes": lexique.nb_termes,
            "fichier": chemin_lexique,
        },
        "nb_candidats": len(candidats),
        "candidats": [entree(ligne) for ligne in sorted(candidats, key=lambda item: -item.detection.score)[:50]],
        "desaccords": {
            "nb_rates_par_le_classifieur": len(rates),
            "rates_par_le_classifieur": [
                entree(ligne) for ligne in sorted(rates, key=lambda item: -item.detection.score)[:50]
            ],
            "nb_techniques_sans_structure": len(techniques_sans_structure),
            "techniques_sans_structure": [entree(ligne) for ligne in techniques_sans_structure[:50]],
        },
    }


def scanner_archive(
    racines: list[Path],
    config: AppConfig,
    limite_empreinte: int,
    sans_empreintes: bool,
    lexique: LexiqueFiches,
    chemin_lexique: str = "",
) -> tuple[RapportInventaire, list[LigneFichier], list[tuple[str, dict[str, Any]]]]:
    """Parcourt les racines en lecture seule et agrège les statistiques.

    Retourne ``(rapport, lignes, empreintes_familles)`` où *empreintes_familles*
    associe chaque fiche (candidate structurelle ou technique au sens du
    classifieur) à son empreinte de gabarit.
    """
    rapport = RapportInventaire(racines=[str(racine) for racine in racines])
    lignes: list[LigneFichier] = []
    empreintes_familles: list[tuple[str, dict[str, Any]]] = []
    types_pdf: Counter[str] = Counter()

    for racine in racines:
        erreurs_parcours: list[str] = []

        def erreur_parcours(erreur: OSError) -> None:
            # Journalisée avec sa conséquence : le sous-arbre manquant est
            # signalé dans le rapport, le reste de l'archive est quand même
            # inventorié (un inventaire partiel vaut mieux que pas d'inventaire).
            message = f"parcours impossible : {erreur.filename or racine} : {erreur}"
            erreurs_parcours.append(message)
            LOGGER.warning("%s — conséquence : sous-arbre absent du rapport.", message)

        for courant, dossiers, fichiers in os.walk(racine, onerror=erreur_parcours, followlinks=False):
            rapport.nb_dossiers += 1
            courant_path = Path(courant)
            dossiers[:] = [nom for nom in dossiers if not nom.startswith("~$")]
            for nom in sorted(fichiers):
                if nom.startswith("~$"):
                    continue
                chemin = courant_path / nom
                try:
                    stat = chemin.stat()
                except OSError as erreur:
                    message = f"fichier inaccessible : {chemin} : {erreur}"
                    rapport.erreurs.append(message)
                    LOGGER.warning("%s — conséquence : fichier absent du rapport.", message)
                    continue
                extension = chemin.suffix.lower() or "<sans extension>"
                rapport.nb_fichiers += 1
                rapport.volume_total += stat.st_size
                type_stats = rapport.par_type.setdefault(extension, {"nb": 0, "octets": 0})
                type_stats["nb"] += 1
                type_stats["octets"] += stat.st_size
                annee = datetime.fromtimestamp(stat.st_mtime).year
                annee_stats = rapport.par_annee.setdefault(str(annee), {"nb": 0, "octets": 0})
                annee_stats["nb"] += 1
                annee_stats["octets"] += stat.st_size

                ligne = LigneFichier(
                    chemin=str(chemin),
                    taille=stat.st_size,
                    annee=annee,
                    extension=extension,
                )
                if extension == ".pdf":
                    categorie, statut, detail, temps_ms, page = analyser_pdf(chemin, config)
                    ligne.categorie_pdf = categorie
                    ligne.statut_extraction = statut
                    ligne.detail = detail
                    ligne.temps_extraction_ms = temps_ms
                    types_pdf[categorie] += 1
                    if page.mots:
                        # Vue structurelle, indépendante du classifieur : c'est
                        # elle qui protège le recensement de l'angle mort
                        # constaté en Phase 0 (variantes hors vocabulaire).
                        ligne.detection = detecter_fiche(
                            page.mots, page.largeur, page.hauteur, page.grille_tracee, lexique
                        )
                        if ligne.detection.est_candidat or categorie == "technique":
                            empreinte = empreinte_gabarit(page.mots, page.largeur, page.hauteur)
                            if empreinte is not None:
                                empreintes_familles.append((ligne.chemin, empreinte))
                if not sans_empreintes:
                    try:
                        ligne.empreinte = empreinte_sha256(chemin, limite_empreinte)
                        if ligne.empreinte == "non_calculé_fichier_lourd":
                            rapport.fichiers_non_empreintes += 1
                    except OSError as erreur:
                        message = f"empreinte impossible : {chemin} : {erreur}"
                        rapport.erreurs.append(message)
                        LOGGER.warning("%s — conséquence : doublons non détectables pour ce fichier.", message)
                lignes.append(ligne)

        rapport.erreurs.extend(erreurs_parcours)

    rapport.pdf_natifs = types_pdf["technique"] + types_pdf["plan"]
    rapport.pdf_techniques = types_pdf["technique"]
    rapport.pdf_plans = types_pdf["plan"]
    rapport.pdf_scans_probables = types_pdf["scan_probable"]
    rapport.pdf_erreurs = types_pdf["erreur_extraction"]
    rapport.nb_candidats_structurels = sum(
        1 for ligne in lignes if ligne.detection is not None and ligne.detection.est_candidat
    )
    rapport.section_detection = construire_section_detection(lignes, lexique, chemin_lexique)
    return rapport, lignes, empreintes_familles


def detecter_doublons(lignes: list[LigneFichier]) -> tuple[int, int, int]:
    """Numérote les groupes de doublons probables et compte les octets redondants.

    Un doublon probable = même taille ET même empreinte SHA-256 (les fichiers
    trop lourds pour l'empreinte ne participent pas, ils ne sont pas accusés).
    Retourne ``(groupes, fichiers_redondants, octets_redondants)``.
    """
    groupes_candidats: dict[tuple[int, str], list[LigneFichier]] = {}
    for ligne in lignes:
        if not ligne.empreinte or ligne.empreinte == "non_calculé_fichier_lourd":
            continue
        groupes_candidats.setdefault((ligne.taille, ligne.empreinte), []).append(ligne)

    groupes = redondants = octets_redondants = 0
    for candidats in sorted(groupes_candidats.values(), key=lambda groupe: groupe[0].chemin):
        if len(candidats) < 2:
            continue
        groupes += 1
        redondants += len(candidats) - 1
        octets_redondants += sum(candidat.taille for candidat in candidats[1:])
        for ligne in candidats:
            ligne.groupe_doublon = groupes
    return groupes, redondants, octets_redondants


def localiser_fiches(lignes: list[LigneFichier], racines: list[Path]) -> list[dict[str, Any]]:
    """Classe les fiches techniques par dossier parent (top localisations)."""
    compteur: Counter[str] = Counter()
    for ligne in lignes:
        if ligne.categorie_pdf != "technique":
            continue
        dossier = str(Path(ligne.chemin).parent)
        for racine in racines:
            racine_str = str(racine)
            if dossier.startswith(racine_str):
                relatif = os.path.relpath(dossier, racine_str)
                dossier = f"{racine.name}{os.sep}{relatif}" if relatif != "." else f"{racine.name}{os.sep}"
                break
        compteur[dossier] += 1
    return [
        {"dossier": dossier, "nb_fiches": nb}
        for dossier, nb in sorted(compteur.items(), key=lambda item: (-item[1], item[0]))
    ]


def exporter_rapports(rapport: RapportInventaire, lignes: list[LigneFichier], sortie: Path) -> list[Path]:
    """Écrit inventaire.json, inventaire_fichiers.csv et inventaire_doublons.csv."""
    sortie.mkdir(parents=True, exist_ok=True)
    chemin_json = sortie / "inventaire.json"
    chemin_fichiers = sortie / "inventaire_fichiers.csv"
    chemin_doublons = sortie / "inventaire_doublons.csv"

    with chemin_json.open("w", encoding="utf-8") as fichier:
        json.dump(rapport.vers_dict(), fichier, ensure_ascii=False, indent=2, sort_keys=True)
        fichier.write("\n")

    with chemin_fichiers.open("w", encoding="utf-8-sig", newline="") as fichier:
        ecrivain = csv.writer(fichier, delimiter=";")
        ecrivain.writerow(
            [
                "chemin",
                "taille_octets",
                "annee",
                "extension",
                "categorie_pdf",
                "candidat_fiche",
                "score_fiche",
                "statut_extraction",
                "detail",
                "temps_extraction_ms",
                "groupe_doublon",
            ]
        )
        for ligne in sorted(lignes, key=lambda item: item.chemin):
            ecrivain.writerow(
                [
                    ligne.chemin,
                    ligne.taille,
                    ligne.annee,
                    ligne.extension,
                    ligne.categorie_pdf,
                    int(ligne.detection.est_candidat) if ligne.detection else "",
                    round(ligne.detection.score, 3) if ligne.detection else "",
                    ligne.statut_extraction,
                    ligne.detail,
                    ligne.temps_extraction_ms,
                    ligne.groupe_doublon,
                ]
            )

    with chemin_doublons.open("w", encoding="utf-8-sig", newline="") as fichier:
        ecrivain = csv.writer(fichier, delimiter=";")
        ecrivain.writerow(["groupe", "chemin", "taille_octets"])
        for ligne in sorted(lignes, key=lambda item: item.chemin):
            if ligne.groupe_doublon:
                ecrivain.writerow([ligne.groupe_doublon, ligne.chemin, ligne.taille])
    return [chemin_json, chemin_fichiers, chemin_doublons]


def afficher_rapport(rapport: RapportInventaire, chemins_sortie: list[Path]) -> None:
    """Rapport console lisible par un opérateur (français)."""
    print("=" * 72)
    print("INVENTAIRE D'ARCHIVE — PHASE 0 (lecture seule, aucune modification)")
    print("=" * 72)
    for racine in rapport.racines:
        print(f"  Racine analysée : {racine}")
    print(f"  Dossiers : {rapport.nb_dossiers:,}".replace(",", " "))
    print(f"  Fichiers : {rapport.nb_fichiers:,}".replace(",", " "))
    print(f"  Volume total : {format_octets(rapport.volume_total)}")
    print()
    print("Types principaux (par volume) :")
    for extension, stats in sorted(rapport.par_type.items(), key=lambda item: -item[1]["octets"])[:10]:
        print(
            f"  {extension:<20} {stats['nb']:>8,} fichiers".replace(",", " ")
            + f"  {format_octets(stats['octets'])}"
        )
    print()
    print("Volumes par année :")
    for annee, stats in sorted(rapport.par_annee.items()):
        print(f"  {annee}  {stats['nb']:>8,} fichiers".replace(",", " ") + f"  {format_octets(stats['octets'])}")
    print()
    part_scans = (
        f" ({100.0 * rapport.pdf_scans_probables / max(1, rapport.pdf_natifs + rapport.pdf_scans_probables):.1f} % des PDF)"
        if rapport.pdf_natifs + rapport.pdf_scans_probables
        else ""
    )
    print(
        f"PDF : {rapport.pdf_natifs:,} natifs".replace(",", " ")
        + f" (dont {rapport.pdf_techniques:,} fiches techniques probables,".replace(",", " ")
        + f" {rapport.pdf_plans:,} plans)".replace(",", " ")
    )
    print(f"PDF scannés probables (aucun texte extractible) : {rapport.pdf_scans_probables:,}{part_scans}".replace(",", " "))
    if rapport.pdf_erreurs:
        print(f"PDF en erreur d'extraction : {rapport.pdf_erreurs:,} (voir rapport JSON)".replace(",", " "))
    print()
    print(
        f"Doublons probables : {rapport.doublons_groupes:,} groupes,".replace(",", " ")
        + f" {rapport.doublons_fichiers_redondants:,} fichiers redondants,".replace(",", " ")
        + f" {format_octets(rapport.doublons_octets_redondants)} récupérables"
    )
    if rapport.fichiers_non_empreintes:
        print(
            f"  (fichiers non empreintés au-delà de la limite : {rapport.fichiers_non_empreintes:,})".replace(",", " ")
        )
    print()
    desaccords = rapport.section_detection.get("desaccords", {})
    nb_rates = desaccords.get("nb_rates_par_le_classifieur", 0)
    print(
        f"Détection structurelle (vue indépendante du classifieur) : "
        f"{rapport.nb_candidats_structurels:,} candidat(s) fiche,".replace(",", " ")
        + f" lexique « {rapport.section_detection.get('lexique', {}).get('fichier', '?')} »"
    )
    if nb_rates:
        print(
            f"  ⚠ DÉSACCORDS : {nb_rates:,} document(s) vus comme fiches par la détection".replace(",", " ")
            + " mais classés autrement par le classifieur actuel :"
        )
        for entree in desaccords.get("rates_par_le_classifieur", [])[:5]:
            print(
                f"    - {entree['chemin']} (classé « {entree['categorie_classifieur']} »,"
                + f" score {entree['score']:.2f}, termes : {', '.join(entree['vocabulaire_trouve'][:6])})"
            )
        if nb_rates > 5:
            print(f"    … et {nb_rates - 5} autres (voir inventaire.json → detection_structurelle)")
    nb_sans_structure = desaccords.get("nb_techniques_sans_structure", 0)
    if nb_sans_structure:
        print(
            f"  ⚠ {nb_sans_structure:,} fiche(s) du classifieur sans structure de tableau détectable".replace(",", " ")
            + " (à examiner : gabarit atypique ?)"
        )
    print()
    if rapport.localisations_fiches:
        print("Localisation probable des fiches techniques :")
        for localisation in rapport.localisations_fiches[:5]:
            print(f"  {localisation['dossier']:<50} {localisation['nb_fiches']:>8,} fiches".replace(",", " "))
    print()
    if rapport.familles:
        print(f"Familles de gabarits détectées : {len(rapport.familles)}")
        for numero, famille in enumerate(rapport.familles[:10], start=1):
            exemples = " ; ".join(Path(exemple).name for exemple in famille.fichiers[:MAX_EXEMPLES_FAMILLE])
            print(
                f"  Famille {numero} : {len(famille.fichiers):,} PDF".replace(",", " ")
                + f", {len(famille.empreintes_fines)} mise(s) en page distincte(s) — ex. {exemples}"
            )
        if len(rapport.familles) > 10:
            print(f"  … et {len(rapport.familles) - 10} autres familles (voir inventaire.json)")
    else:
        print("Familles de gabarits : aucune fiche technique détectée, rien à regrouper.")
    if rapport.erreurs:
        print()
        print(f"Avertissements ({len(rapport.erreurs):,}) :".replace(",", " "))
        for erreur in rapport.erreurs[:5]:
            print(f"  ! {erreur}")
        if len(rapport.erreurs) > 5:
            print(f"  … et {len(rapport.erreurs) - 5} autres (voir inventaire.json)")
    print()
    print("Rapports écrits :")
    for chemin in chemins_sortie:
        print(f"  {chemin}")


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        description="Inventaire en lecture seule d'une arborescence SEAMTECH (Phase 0)."
    )
    parseur.add_argument("racines", nargs="+", help="chemin(s) racine de l'archive (jamais modifiés)")
    parseur.add_argument(
        "--sortie",
        default=None,
        help="dossier de rapport (défaut : ./rapport_inventaire_<horodatage> à côté du dépôt, jamais dans l'archive)",
    )
    parseur.add_argument(
        "--limite-empreinte",
        type=int,
        default=20,
        help="taille maximale (Mo) d'empreinte SHA-256 par fichier (défaut : 20)",
    )
    parseur.add_argument(
        "--sans-empreintes",
        action="store_true",
        help="ne calcule aucune empreinte (détection des doublons désactivée)",
    )
    parseur.add_argument(
        "--lexique",
        default=None,
        help="chemin du lexique de fiches JSON (défaut : config/lexique_fiches.json du dépôt)",
    )
    parseur.add_argument("--silencieux", action="store_true", help="ne journaliser que les avertissements")
    # Convention du dépôt (voir validate_extraction.py) : argv[0] est le nom du
    # script, comme lors d'un appel en ligne de commande.
    arguments = parseur.parse_args(sys.argv[1:] if argv is None else list(argv)[1:])

    logging.basicConfig(
        level=logging.WARNING if arguments.silencieux else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        lexique = charger_lexique(arguments.lexique)
    except (FileNotFoundError, ValueError) as erreur:
        print(f"ERREUR : {erreur}", file=sys.stderr)
        return 2
    chemin_lexique = (
        str(Path(arguments.lexique).expanduser().resolve()) if arguments.lexique else str(CHEMIN_LEXIQUE_PAR_DEFAUT)
    )

    racines: list[Path] = []
    for brut in arguments.racines:
        racine = Path(brut).expanduser()
        if not racine.is_dir():
            print(f"ERREUR : racine inexistante ou pas un dossier : {racine}", file=sys.stderr)
            return 2
        racines.append(racine)

    sortie = (
        Path(arguments.sortie).expanduser()
        if arguments.sortie
        else Path.cwd() / f"rapport_inventaire_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    try:
        verifier_sortie_hors_archive(sortie, racines)
    except ValueError as erreur:
        print(f"ERREUR : {erreur}", file=sys.stderr)
        return 2

    config = AppConfig(root_paths=racines)
    debut = time.perf_counter()
    rapport, lignes, empreintes = scanner_archive(
        racines,
        config,
        limite_empreinte=arguments.limite_empreinte * 1024 * 1024,
        sans_empreintes=arguments.sans_empreintes,
        lexique=lexique,
        chemin_lexique=chemin_lexique,
    )
    rapport.duree_secondes = time.perf_counter() - debut

    rapport.familles = regrouper_familles(empreintes)
    rapport.localisations_fiches = localiser_fiches(lignes, racines)
    (
        rapport.doublons_groupes,
        rapport.doublons_fichiers_redondants,
        rapport.doublons_octets_redondants,
    ) = detecter_doublons(lignes)

    chemins_sortie = exporter_rapports(rapport, lignes, sortie)
    afficher_rapport(rapport, chemins_sortie)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
