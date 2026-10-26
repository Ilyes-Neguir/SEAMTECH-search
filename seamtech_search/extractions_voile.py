"""Lot 3a — extraction structurée d'une voile depuis un PDF.

Ce module ne réinvente pas le moteur historique : les gabarits déterministes
(``seamtech_search.fiches.extraction``) restent la seule voie de lecture des
PDF natifs. Le Lot 3a ajoute au-dessus une projection stable et requêtable :

* dimensions canoniques en mètres / m² / kg, avec conversion uniquement
  lorsqu'une unité explicite est lue (mm, cm, cm², g…) ;
* provenance de chaque champ : valeur brute, valeur normalisée/canonique,
  unités, méthode, confiance bornée à [0, 1], page et zone ;
* matériaux structurés ;
* contrôles géométriques RG16 indépendants de la confiance ;
* persistance idempotente dans ``sail_extractions`` (migration 028).

Frontière de lot : ``modele_bateau_brut`` est conservé tel que lu. Aucune
résolution vers le référentiel canonique des bateaux n'est faite ici ; ce
rattachement, ses statuts et les corrections humaines appartiennent au
Lot 3b.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator

from seamtech_search.fiches import normalisation as norm
from seamtech_search.fiches.anomalies import (
    PLAGES_AUTRES,
    PLAGES_METRES,
    evaluer_anomalies,
)
from seamtech_search.fiches.modeles import (
    ChampExtrait,
    Cotes,
    FicheExtraite,
    Zone,
)

LOGGER = logging.getLogger("seamtech_search.extractions_voile")

VERSION_EXTRACTEUR_VOILE = "lot3a-v1"

StatutExtraction = Literal["complete", "partielle", "a_reprendre"]
StatutPlausibilite = Literal["plausible", "a_verifier", "implausible", "indeterminee"]
# Lot 3b : liaison du libellé brut vers le référentiel canonique (migration 029).
StatutLiaison = Literal["non_resolu", "lie_automatique", "lie_humain", "ambigu", "sans_modele"]
MethodeLiaison = Literal["cle_exacte", "alias_exact", "fiche_bateau", "humain", "humain_nouveau_modele"]


class AnomalieExtractionVoile(BaseModel):
    """Anomalie de plausibilité ou de complétude, indépendante de la confiance."""

    code: str
    gravite: str = "moyenne"  # faible | moyenne | forte
    message: str = ""
    champ: str | None = None


class ChampExtractionVoile(BaseModel):
    """Une valeur de voile et sa provenance complète dans le PDF source."""

    champ: str
    valeur_brute: str | None = None
    valeur_normalisee: str | None = None
    valeur_numerique: float | None = None
    unite_brute: str | None = None
    unite_canonique: str | None = None
    valeur_canonique: float | None = None
    methode: str | None = None
    confiance: float | None = Field(default=None, ge=0.0, le=1.0)
    page: int | None = None
    zone: Zone | None = None
    rang: int | None = None
    table_cible: str | None = None
    colonne_cible: str | None = None
    etat: str = "lu"  # lu | lu_sans_trace | non_normalise | manquant
    avertissements: list[str] = Field(default_factory=list)


class MateriauExtractionVoile(BaseModel):
    """Matériau structuré d'une voile, sans résolution vers un référentiel."""

    role: str
    niveau: int | None = None
    designation: str | None = None
    grammage_g_m2: float | None = None
    mesure_mm: float | None = None
    confiance: float | None = Field(default=None, ge=0.0, le=1.0)
    page: int | None = None
    zone: Zone | None = None
    champs: list[str] = Field(default_factory=list)


class ExtractionVoile(BaseModel):
    """Projection Lot 3a d'une fiche de fabrication de voile."""

    id_extraction: int | None = None
    cle_idempotence: str = ""
    id_fiche: int | None = None
    id_document: int | None = None
    code_fiche: str | None = None
    nom_fichier_source: str | None = None
    empreinte_sha256: str | None = None
    gabarit_code: str | None = None
    gabarit_version: int | None = None
    version_extracteur: str = VERSION_EXTRACTEUR_VOILE
    methode_principale: str = "inconnue"
    type_voile: str | None = None
    gamme: str | None = None
    designation_brute: str | None = None
    modele_bateau_brut: str | None = None
    taille_bateau_brute: str | None = None
    slu_m: float | None = Field(default=None, gt=0.0)
    sle_m: float | None = Field(default=None, gt=0.0)
    sf_m: float | None = Field(default=None, gt=0.0)
    shw_m: float | None = Field(default=None, gt=0.0)
    spa_m2: float | None = Field(default=None, gt=0.0)
    tetiere_m: float | None = Field(default=None, gt=0.0)
    poids_kg: float | None = Field(default=None, gt=0.0)
    materiaux: list[MateriauExtractionVoile] = Field(default_factory=list)
    champs: list[ChampExtractionVoile] = Field(default_factory=list)
    anomalies: list[AnomalieExtractionVoile] = Field(default_factory=list)
    score_qualite: float | None = Field(default=None, ge=0.0, le=1.0)
    confiance_moyenne: float | None = Field(default=None, ge=0.0, le=1.0)
    statut_extraction: StatutExtraction = "a_reprendre"
    statut_plausibilite: StatutPlausibilite = "indeterminee"
    # Lot 3b — résolution du modèle de bateau (migration 029). Le constructeur
    # Lot 3a ne résout toujours rien : ces champs naissent « non_resolu » et
    # sont arbitrés ensuite par `liaison_voile` (automatisme déterministe ou
    # décision d'approbateur), jamais devinés à l'extraction.
    boat_model_id: int | None = None
    statut_liaison: StatutLiaison = "non_resolu"
    methode_liaison: MethodeLiaison | None = None
    lie_le: datetime | None = None
    lie_par: int | None = None
    extrait_le: datetime | None = None
    cree_le: datetime | None = None
    maj_le: datetime | None = None

    @field_validator("empreinte_sha256", mode="before")
    @classmethod
    def _empreinte_canonique(cls, valeur: object) -> object:
        if valeur is None:
            return None
        texte = str(valeur).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", texte):
            raise ValueError("empreinte_sha256 doit contenir exactement 64 caractères hexadécimaux")
        return texte

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dimensions(self) -> dict[str, float | None]:
        """Dimensions canoniques, dans l'ordre métier SLU/SLE/SF/SHW/SPA."""
        return {
            "slu_m": self.slu_m,
            "sle_m": self.sle_m,
            "sf_m": self.sf_m,
            "shw_m": self.shw_m,
            "spa_m2": self.spa_m2,
            "tetiere_m": self.tetiere_m,
            "poids_kg": self.poids_kg,
        }

    @property
    def champs_manquants(self) -> list[str]:
        return [champ.champ for champ in self.champs if champ.etat == "manquant"]

    def champ(self, nom: str) -> ChampExtractionVoile | None:
        """Première provenance portant exactement ce nom de champ."""
        return next((champ for champ in self.champs if champ.champ == nom), None)


# ---------------------------------------------------------------------------
# Conversion d'unités — explicite ou rien
# ---------------------------------------------------------------------------

_RE_NOMBRE_UNITE = re.compile(
    r"(?P<nombre>[+-]?\d+(?:[.,]\d+)?)\s*(?P<unite>mm²|mm2|cm²|cm2|m²|m2|mm|cm|m|kg|g)?(?![A-Za-zÀ-ÿ0-9²])",
    re.IGNORECASE,
)

_FACTEURS: dict[str, dict[str, float]] = {
    "longueur": {"m": 1.0, "cm": 0.01, "mm": 0.001},
    "surface": {"m2": 1.0, "cm2": 0.0001, "mm2": 0.000001},
    "masse": {"kg": 1.0, "g": 0.001},
}

# (attribut Lot 3a, colonne historique Cotes, famille, unité d'une valeur nue,
#  unité canonique). La têtière historique est en cm ; le Lot 3a l'expose en m.
_SPECS_DIMENSIONS: tuple[tuple[str, str, str, str, str], ...] = (
    ("slu_m", "slu_m", "longueur", "m", "m"),
    ("sle_m", "sle_m", "longueur", "m", "m"),
    ("sf_m", "sf_m", "longueur", "m", "m"),
    ("shw_m", "shw_m", "longueur", "m", "m"),
    ("spa_m2", "spa_m2", "surface", "m2", "m2"),
    ("tetiere_m", "tetiere_cm", "longueur", "cm", "m"),
    ("poids_kg", "poids_kg", "masse", "kg", "kg"),
)


def _arrondir(valeur: float | None) -> float | None:
    if valeur is None:
        return None
    resultat = round(float(valeur), 3)
    return 0.0 if resultat == 0 else resultat


def _normaliser_unite(unite: str | None) -> str | None:
    if not unite:
        return None
    return unite.strip().lower().replace("²", "2").replace("^2", "2")


def _lire_nombre_unite(texte: str | None) -> tuple[float | None, str | None, bool]:
    """Premier nombre d'une valeur et son unité si elle est explicitement lue."""
    if not texte:
        return None, None, False
    trouve = _RE_NOMBRE_UNITE.search(str(texte).replace("\u202f", " ").replace("\xa0", " "))
    if trouve is None:
        return None, None, False
    try:
        nombre = float(trouve.group("nombre").replace(",", "."))
    except ValueError:
        return None, None, False
    unite = _normaliser_unite(trouve.group("unite"))
    return nombre, unite, unite is not None


def _convertir(valeur: float | None, unite: str | None, famille: str) -> float | None:
    """Convertit vers m / m² / kg ; ``None`` si l'unité est incompatible."""
    if valeur is None or unite is None:
        return None
    facteur = _FACTEURS.get(famille, {}).get(unite)
    if facteur is None:
        return None
    return _arrondir(float(valeur) * facteur)


def _ecart_relatif(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) > max(0.005, abs(float(b)) * 0.01)


def _texte(valeur: object) -> str | None:
    if valeur is None:
        return None
    texte = str(valeur).strip()
    return texte or None


def _nom_fichier(valeur: str | Path | None) -> str | None:
    if valeur is None:
        return None
    texte = str(valeur).strip().replace("\\", "/")
    if not texte:
        return None
    return Path(texte).name or None


def _empreinte_fichier(chemin: Path) -> str | None:
    try:
        empreinte = hashlib.sha256()
        with chemin.open("rb") as flux:
            for bloc in iter(lambda: flux.read(1024 * 1024), b""):
                empreinte.update(bloc)
        return empreinte.hexdigest()
    except OSError:
        return None


def _trace_preferee(traces: list[ChampExtrait], nom: str) -> ChampExtrait | None:
    candidates = [trace for trace in traces if trace.champ == nom]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda trace: (
            trace.valeur_normalisee is not None,
            trace.valeur_brute is not None,
            trace.zone is not None,
            trace.confiance,
        ),
    )


def _modele_bateau_depuis_fiche(fiche: FicheExtraite, trace: ChampExtrait | None) -> str | None:
    """Libellé bateau brut : la trace PDF prime, sans canonisation."""
    if trace is not None and _texte(trace.valeur_brute):
        return _texte(trace.valeur_brute)
    nom = _texte(fiche.bateau_nom)
    if not nom:
        return None
    taille = _texte(fiche.bateau_taille)
    if not taille:
        return nom
    # Sans trace, on ne peut que juxtaposer nom et taille déjà séparés par le
    # gabarit. Si la taille est déjà visible dans le nom (« BAVARIA-45 » /
    # « 45 pieds »), la répéter créerait un libellé qui n'existe pas.
    jetons_nom = set(re.findall(r"[a-z0-9]+", norm.sans_accents(nom).casefold()))
    jetons_taille = [jeton for jeton in re.findall(r"[a-z0-9']+", norm.sans_accents(taille).casefold()) if jeton]
    jetons_numeriques = [jeton for jeton in jetons_taille if jeton.isdigit()]
    if jetons_numeriques and all(jeton in jetons_nom for jeton in jetons_numeriques):
        return nom
    if norm.sans_accents(taille).casefold() in norm.sans_accents(nom).casefold():
        return nom
    return f"{nom} {taille}"


def _plage_dimension(attribut: str, valeur: float) -> tuple[float, float] | None:
    if attribut in PLAGES_METRES:
        return PLAGES_METRES[attribut]
    if attribut == "tetiere_m":
        mini, maxi = PLAGES_AUTRES["tetiere_cm"]
        return mini / 100.0, maxi / 100.0
    return PLAGES_AUTRES.get(attribut)


def _cle_idempotence(extraction: ExtractionVoile) -> str:
    version = extraction.version_extracteur
    if extraction.id_document is not None:
        return f"document:{extraction.id_document}:{version}"
    if extraction.id_fiche is not None:
        return f"fiche:{extraction.id_fiche}:{version}"
    if extraction.empreinte_sha256:
        return f"sha256:{extraction.empreinte_sha256}:{version}"
    if extraction.code_fiche:
        return f"code:{extraction.code_fiche}:{extraction.nom_fichier_source or 'sans-fichier'}:{version}"
    empreinte_contenu = hashlib.sha256(
        extraction.model_dump_json(exclude={"cle_idempotence"}).encode("utf-8")
    ).hexdigest()
    return f"contenu:{empreinte_contenu}:{version}"


# ---------------------------------------------------------------------------
# Construction depuis une FicheExtraite
# ---------------------------------------------------------------------------


def construire_extraction_voile(
    fiche: FicheExtraite,
    *,
    chemin_source: str | Path | None = None,
    empreinte_sha256: str | None = None,
    id_fiche: int | None = None,
    id_document: int | None = None,
) -> ExtractionVoile:
    """Construit l'extraction Lot 3a d'une fiche déjà lue par gabarit.

    Les valeurs brutes explicitement unitaires priment pour la canonisation :
    si le PDF dit « 6600 mm », le Lot 3a expose 6,6 m même si une valeur
    typée historique était restée à 6600. À l'inverse, une valeur nue hors
    plage n'est jamais divisée par 100 ou 1000 au motif qu'elle « doit »
    être en cm/mm : elle est signalée ``unite_suspecte`` et implausible.
    """
    traces = fiche.tous_les_champs()
    champs: list[ChampExtractionVoile] = []
    champ_par_trace: dict[int, ChampExtractionVoile] = {}
    anomalies: list[AnomalieExtractionVoile] = [
        AnomalieExtractionVoile(code=a.code, gravite=a.gravite, message=a.message)
        for a in fiche.anomalies
    ]

    for trace in traces:
        nombre_brut, unite_brute, unite_explicite = _lire_nombre_unite(trace.valeur_brute)
        nombre_normalise = norm.extraire_decimal(trace.valeur_normalisee)
        entree = ChampExtractionVoile(
            champ=trace.champ,
            valeur_brute=trace.valeur_brute,
            valeur_normalisee=trace.valeur_normalisee,
            valeur_numerique=nombre_normalise if nombre_normalise is not None else nombre_brut,
            unite_brute=unite_brute if unite_explicite else None,
            methode=trace.methode or "inconnue",
            confiance=trace.confiance,
            page=trace.page,
            zone=trace.zone,
            rang=trace.rang,
            table_cible=trace.table_cible,
            colonne_cible=trace.colonne_cible,
            etat="lu" if trace.valeur_normalisee is not None else "non_normalise",
        )
        champs.append(entree)
        champ_par_trace[id(trace)] = entree

    dimensions: dict[str, float | None] = {}
    jeux_ordonnes: list[str] = []
    for jeu in ("finie", "dessin"):
        if any(cotes.jeu == jeu for cotes in fiche.cotes):
            jeux_ordonnes.append(jeu)
    for cotes in fiche.cotes:
        if cotes.jeu not in jeux_ordonnes:
            jeux_ordonnes.append(cotes.jeu)

    for attribut, colonne, famille, unite_nue, unite_canonique in _SPECS_DIMENSIONS:
        valeur_canonique: float | None = None
        for jeu in jeux_ordonnes:
            cotes_jeu: Cotes | None = next((c for c in fiche.cotes if c.jeu == jeu), None)
            typee = getattr(cotes_jeu, colonne, None) if cotes_jeu is not None else None
            nom_champ = f"cotes.{jeu}.{colonne}"
            trace = _trace_preferee(traces, nom_champ)
            nombre_brut, unite_brute, unite_explicite = _lire_nombre_unite(
                trace.valeur_brute if trace is not None else None
            )
            canonique_typee = _convertir(typee, unite_nue, famille)
            canonique_brute = (
                _convertir(nombre_brut, unite_brute, famille) if unite_explicite else None
            )

            avertissements: list[str] = []
            if unite_explicite and nombre_brut is not None and canonique_brute is None:
                # Une unité explicitement incompatible (« kg » pour une
                # longueur) n'autorise pas à relire le nombre comme des
                # mètres : la trace reste visible, la cote structurée non.
                anomalies.append(
                    AnomalieExtractionVoile(
                        code="unite_incompatible",
                        gravite="forte",
                        champ=nom_champ,
                        message=(
                            f"{nom_champ} : unité « {unite_brute} » incompatible avec "
                            f"une valeur en {unite_canonique}."
                        ),
                    )
                )
                if typee is None:
                    if trace is not None and id(trace) in champ_par_trace:
                        entree_incompatible = champ_par_trace[id(trace)]
                        entree_incompatible.etat = "non_normalise"
                        entree_incompatible.avertissements.append("unite_incompatible")
                    continue
            if canonique_brute is not None:
                valeur_canonique = canonique_brute
                if canonique_typee is not None and _ecart_relatif(canonique_brute, canonique_typee):
                    avertissements.append("conversion_unite_depuis_valeur_brute")
                    anomalies.append(
                        AnomalieExtractionVoile(
                            code="unite_corrigee",
                            gravite="faible",
                            champ=nom_champ,
                            message=(
                                f"{nom_champ} : la valeur brute « {trace.valeur_brute if trace else ''} » "
                                f"donne {canonique_brute:g} {unite_canonique}, distinct de la valeur "
                                f"typée historique {canonique_typee:g} {unite_canonique}."
                            ),
                        )
                    )
            elif canonique_typee is not None:
                valeur_canonique = canonique_typee
            elif nombre_brut is not None and not unite_explicite:
                valeur_canonique = _convertir(nombre_brut, unite_nue, famille)
            if valeur_canonique is None:
                continue

            if trace is not None and id(trace) in champ_par_trace:
                entree = champ_par_trace[id(trace)]
            else:
                entree = ChampExtractionVoile(
                    champ=nom_champ,
                    valeur_normalisee=None if typee is None else str(typee),
                    methode="fiche_extraite",
                    confiance=None,
                    page=None,
                    table_cible="fiche_cotes",
                    colonne_cible=colonne,
                    etat="lu_sans_trace",
                )
                champs.append(entree)
            entree.valeur_numerique = nombre_brut if nombre_brut is not None else typee
            entree.unite_brute = unite_brute if unite_explicite else None
            entree.unite_canonique = unite_canonique
            entree.valeur_canonique = valeur_canonique
            entree.avertissements.extend(avertissements)

            if valeur_canonique <= 0:
                # Une dimension physique non positive est une donnée à
                # signaler, jamais un motif de faire échouer toute
                # l'extraction ni une valeur à écrire dans la colonne
                # canonique positive.
                entree.etat = "implausible"
                anomalies.append(
                    AnomalieExtractionVoile(
                        code="cote_hors_plage",
                        gravite="forte",
                        champ=nom_champ,
                        message=f"{nom_champ} = {valeur_canonique:g} {unite_canonique} : dimension non positive.",
                    )
                )
                valeur_canonique = None
                break

            plage = _plage_dimension(attribut, valeur_canonique)
            if plage is not None and not unite_explicite:
                mini, maxi = plage
                if valeur_canonique < mini or valeur_canonique > maxi:
                    for facteur in (100.0, 1000.0):
                        candidate = valeur_canonique / facteur
                        if mini <= candidate <= maxi:
                            anomalies.append(
                                AnomalieExtractionVoile(
                                    code="unite_suspecte",
                                    gravite="forte",
                                    champ=nom_champ,
                                    message=(
                                        f"{nom_champ} = {valeur_canonique:g} {unite_canonique} est hors plage ; "
                                        "aucune unité explicite ne permet de le rescaler sans deviner."
                                    ),
                                )
                            )
                            break
            break
        dimensions[attribut] = valeur_canonique

    trace_bateau = _trace_preferee(traces, "fiche.bateau")
    modele_bateau_brut = _modele_bateau_depuis_fiche(fiche, trace_bateau)
    trace_designation = _trace_preferee(traces, "fiche.designation")
    designation_brute = _texte(trace_designation.valeur_brute) if trace_designation else None
    type_voile = _texte(fiche.type_voile_libelle)
    gamme = _texte(fiche.gamme)
    if trace_designation is not None and trace_designation.valeur_normalisee:
        morceaux = [morceau.strip() for morceau in trace_designation.valeur_normalisee.split("|", 1)]
        # Seule une décomposition explicite « type | gamme » permet de
        # déduire le type de la trace. Une désignation inconnue en un seul
        # morceau reste une gamme/texte à reprendre, pas un type inventé.
        if len(morceaux) == 2:
            if type_voile is None and morceaux[0]:
                type_voile = morceaux[0]
            if gamme is None and morceaux[1]:
                gamme = morceaux[1]
    if designation_brute is None and trace_designation is not None:
        designation_brute = _texte(trace_designation.valeur_normalisee)

    materiaux: list[MateriauExtractionVoile] = []
    for materiau in fiche.materiaux:
        trace_materiau = next(
            (trace for trace in materiau.champs if trace.valeur_normalisee is not None),
            materiau.champs[0] if materiau.champs else None,
        )
        materiaux.append(
            MateriauExtractionVoile(
                role=materiau.role,
                niveau=materiau.niveau,
                designation=materiau.designation,
                grammage_g_m2=materiau.grammage_g_m2,
                mesure_mm=materiau.mesure_mm,
                confiance=trace_materiau.confiance if trace_materiau else None,
                page=trace_materiau.page if trace_materiau else None,
                zone=trace_materiau.zone if trace_materiau else None,
                champs=[trace.champ for trace in materiau.champs],
            )
        )
    if not materiaux and _texte(fiche.tissu_texte):
        materiaux.append(
            MateriauExtractionVoile(
                role="tissu_principal",
                designation=_texte(fiche.tissu_texte),
                grammage_g_m2=norm.grammage_g_m2(fiche.tissu_texte),
            )
        )

    gabarit_inconnu = any(anomalie.code == "gabarit_inconnu" for anomalie in anomalies)

    def ajouter_manquant(nom: str, cible: str | None, colonne: str | None) -> None:
        if any(champ.champ == nom for champ in champs):
            return
        champs.append(
            ChampExtractionVoile(
                champ=nom,
                methode=None,
                confiance=None,
                page=None,
                table_cible=cible,
                colonne_cible=colonne,
                etat="manquant",
            )
        )

    if modele_bateau_brut is None:
        ajouter_manquant("fiche.bateau", "fiche", "bateau")
    if type_voile is None:
        ajouter_manquant("fiche.designation", "fiche", "designation")
    if dimensions.get("slu_m") is None:
        ajouter_manquant("cotes.finie.slu_m", "fiche_cotes", "slu_m")
    if dimensions.get("sf_m") is None:
        ajouter_manquant("cotes.finie.sf_m", "fiche_cotes", "sf_m")
    if not materiaux:
        ajouter_manquant("materiau.principal", "fiche_materiau", "designation")

    if not gabarit_inconnu:
        if modele_bateau_brut is None:
            anomalies.append(
                AnomalieExtractionVoile(
                    code="modele_bateau_manquant",
                    gravite="moyenne",
                    champ="fiche.bateau",
                    message="Aucun modèle de bateau brut n'a été lu.",
                )
            )
        if type_voile is None:
            anomalies.append(
                AnomalieExtractionVoile(
                    code="type_voile_manquant",
                    gravite="moyenne",
                    champ="fiche.designation",
                    message="Aucun type de voile n'a été lu.",
                )
            )
        for attribut in ("slu_m", "sf_m"):
            if dimensions.get(attribut) is None:
                anomalies.append(
                    AnomalieExtractionVoile(
                        code="cote_manquante",
                        gravite="moyenne",
                        champ=f"cotes.finie.{attribut}",
                        message=f"Cote structurante absente : {attribut}.",
                    )
                )
        if not materiaux:
            anomalies.append(
                AnomalieExtractionVoile(
                    code="materiau_manquant",
                    gravite="moyenne",
                    champ="materiau.principal",
                    message="Aucun matériau structuré n'a été lu.",
                )
            )

    if any(valeur is not None for valeur in dimensions.values()):
        fiche_controle = FicheExtraite(
            code=fiche.code,
            type_voile_libelle=type_voile,
            cotes=[
                Cotes(
                    jeu="finie",
                    slu_m=dimensions.get("slu_m"),
                    sle_m=dimensions.get("sle_m"),
                    sf_m=dimensions.get("sf_m"),
                    shw_m=dimensions.get("shw_m"),
                    spa_m2=dimensions.get("spa_m2"),
                    tetiere_cm=(
                        _arrondir(dimensions["tetiere_m"] * 100.0)
                        if dimensions.get("tetiere_m") is not None
                        else None
                    ),
                    poids_kg=dimensions.get("poids_kg"),
                )
            ],
        )
        anomalies.extend(
            AnomalieExtractionVoile(code=a.code, gravite=a.gravite, message=a.message)
            for a in evaluer_anomalies(fiche_controle)
        )

    anomalies_dedup: list[AnomalieExtractionVoile] = []
    vues: set[tuple[str, str | None, str]] = set()
    for anomalie in anomalies:
        cle = (anomalie.code, anomalie.champ, anomalie.message)
        if cle not in vues:
            vues.add(cle)
            anomalies_dedup.append(anomalie)

    coeur = {
        "type": type_voile is not None,
        "bateau": modele_bateau_brut is not None,
        "slu": dimensions.get("slu_m") is not None,
        "sf": dimensions.get("sf_m") is not None,
        "materiau": bool(materiaux),
    }
    if gabarit_inconnu or not any(coeur.values()):
        statut_extraction: StatutExtraction = "a_reprendre"
    elif all(coeur.values()):
        statut_extraction = "complete"
    else:
        statut_extraction = "partielle"

    if gabarit_inconnu or (dimensions.get("slu_m") is None and dimensions.get("sf_m") is None):
        statut_plausibilite: StatutPlausibilite = "indeterminee"
    elif any(
        anomalie.code in {"cote_hors_plage", "cotes_incoherentes", "surface_incoherente", "unite_suspecte"}
        or anomalie.gravite == "forte"
        for anomalie in anomalies_dedup
    ):
        statut_plausibilite = "implausible"
    elif any(anomalie.code != "unite_corrigee" for anomalie in anomalies_dedup):
        # Une conversion prouvée par l'unité brute est un avertissement de
        # provenance, pas une implausibilité de la géométrie canonique.
        statut_plausibilite = "a_verifier"
    else:
        statut_plausibilite = "plausible"

    methodes = Counter(trace.methode for trace in traces if trace.methode)
    if gabarit_inconnu:
        methode_principale = "gabarit_inconnu"
    elif methodes:
        methode_principale = methodes.most_common(1)[0][0]
        if methode_principale not in {"gabarit", "ocr", "fiche_extraite"}:
            methode_principale = "fiche_extraite"
    elif any(coeur.values()):
        methode_principale = "fiche_extraite"
    else:
        methode_principale = "inconnue"

    confiances = [
        champ.confiance
        for champ in champs
        if champ.confiance is not None and champ.etat != "manquant"
    ]
    confiance_moyenne = round(sum(confiances) / len(confiances), 4) if confiances else None

    source = Path(chemin_source) if chemin_source is not None else (
        Path(fiche.fichier_pdf) if fiche.fichier_pdf else None
    )
    empreinte = empreinte_sha256.strip().lower() if empreinte_sha256 else None
    if empreinte is None and source is not None and source.is_file():
        empreinte = _empreinte_fichier(source)

    extraction = ExtractionVoile(
        id_fiche=id_fiche,
        id_document=id_document,
        code_fiche=_texte(fiche.code),
        nom_fichier_source=_nom_fichier(source),
        empreinte_sha256=empreinte,
        gabarit_code=fiche.gabarit_code,
        gabarit_version=fiche.gabarit_version,
        methode_principale=methode_principale,
        type_voile=type_voile,
        gamme=gamme,
        designation_brute=designation_brute,
        modele_bateau_brut=modele_bateau_brut,
        taille_bateau_brute=_texte(fiche.bateau_taille),
        slu_m=dimensions.get("slu_m"),
        sle_m=dimensions.get("sle_m"),
        sf_m=dimensions.get("sf_m"),
        shw_m=dimensions.get("shw_m"),
        spa_m2=dimensions.get("spa_m2"),
        tetiere_m=dimensions.get("tetiere_m"),
        poids_kg=dimensions.get("poids_kg"),
        materiaux=materiaux,
        champs=champs,
        anomalies=anomalies_dedup,
        score_qualite=fiche.score_qualite(),
        confiance_moyenne=confiance_moyenne,
        statut_extraction=statut_extraction,
        statut_plausibilite=statut_plausibilite,
    )
    extraction.cle_idempotence = _cle_idempotence(extraction)
    return extraction


def extraire_voile_avec_fiche(
    chemin: str | Path,
    gabarits: list[Any] | tuple[Any, ...] | None = None,
    gabarit_code: str | None = None,
) -> tuple[FicheExtraite, ExtractionVoile]:
    """Lit un PDF natif par gabarit et rend la fiche + sa projection Lot 3a.

    Sans gabarit reconnu, le filet historique conserve les mesures libres et
    l'extraction reste ``a_reprendre`` : aucune cote structurée n'est
    inventée à partir du texte libre.
    """
    source = Path(chemin)
    if not source.is_file():
        raise FileNotFoundError(f"PDF introuvable : {source.name}")

    from seamtech_search.fiches.extraction import extraire_avec_filet, extraire_fiche
    from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES

    liste_gabarits = list(gabarits) if gabarits is not None else list(GABARITS_EMBARQUES)
    if gabarit_code:
        fiche = extraire_fiche(source, gabarits=liste_gabarits, gabarit_code=gabarit_code)
    else:
        fiche = extraire_avec_filet(source, liste_gabarits)
    return fiche, construire_extraction_voile(fiche, chemin_source=source)


def extraire_voile_depuis_pdf(
    chemin: str | Path,
    gabarits: list[Any] | tuple[Any, ...] | None = None,
    gabarit_code: str | None = None,
) -> ExtractionVoile:
    """Extraction Lot 3a autonome d'un PDF, sans écriture en base."""
    return extraire_voile_avec_fiche(chemin, gabarits=gabarits, gabarit_code=gabarit_code)[1]


# ---------------------------------------------------------------------------
# Évaluation sur échantillons d'or
# ---------------------------------------------------------------------------

_CLES_HERITAGE_EXACTES = {
    "client",
    "bateau",
    "type_voile",
    "gamme",
    "date_dessin",
    "date_edition",
}
_PREFIXES_HERITAGE = (
    "cotes.",
    "fiche.",
    "materiau.",
    "galon.",
    "jonction.",
    "finition.",
    "option.",
    "renfort.",
)


def _texte_compare(valeur: object) -> str:
    return re.sub(r"\s+", " ", norm.sans_accents(str(valeur)).casefold()).strip()


def _valeurs_equivalentes(obtenu: object, attendu: object) -> bool:
    if isinstance(attendu, bool):
        return obtenu is attendu
    if isinstance(attendu, (int, float)) and not isinstance(attendu, bool):
        return (
            isinstance(obtenu, (int, float))
            and not isinstance(obtenu, bool)
            and not _ecart_relatif(float(obtenu), float(attendu))
        )
    if isinstance(attendu, list):
        return isinstance(obtenu, list) and [_texte_compare(v) for v in obtenu] == [
            _texte_compare(v) for v in attendu
        ]
    if obtenu is None or attendu is None:
        return obtenu is attendu
    return _texte_compare(obtenu) == _texte_compare(attendu)


def _valeur_lot3a(extraction: ExtractionVoile, cible: str) -> object:
    if cible.startswith("lot3a."):
        cible = cible.removeprefix("lot3a.")
    direct = {
        "code": extraction.code_fiche,
        "code_fiche": extraction.code_fiche,
        "type_voile": extraction.type_voile,
        "gamme": extraction.gamme,
        "designation_brute": extraction.designation_brute,
        "bateau": extraction.modele_bateau_brut,
        "modele_bateau": extraction.modele_bateau_brut,
        "modele_bateau_brut": extraction.modele_bateau_brut,
        "taille_bateau": extraction.taille_bateau_brute,
        "taille_bateau_brute": extraction.taille_bateau_brute,
    }
    if cible in direct:
        return direct[cible]
    if cible.startswith("dimensions."):
        return extraction.dimensions.get(cible.split(".", 1)[1])
    if cible in extraction.dimensions:
        return extraction.dimensions[cible]
    if cible == "materiaux.designations":
        return [m.designation for m in extraction.materiaux if m.designation]
    if cible == "materiaux.designations_contient":
        return [m.designation for m in extraction.materiaux if m.designation]
    if cible == "materiaux.principal":
        principal = next(
            (m for m in extraction.materiaux if m.role == "tissu_principal" and m.designation),
            None,
        )
        if principal is not None:
            return principal.designation
        return next((m.designation for m in extraction.materiaux if m.designation), None)
    if cible == "materiaux.nombre":
        return len(extraction.materiaux)
    if cible.startswith("materiaux."):
        morceaux = cible.split(".")
        if len(morceaux) == 3:
            identifiant, attribut = morceaux[1], morceaux[2]
            materiau = None
            if identifiant.isdigit():
                materiau = next((m for m in extraction.materiaux if m.niveau == int(identifiant)), None)
            if materiau is None:
                materiau = next((m for m in extraction.materiaux if m.role == identifiant), None)
            return getattr(materiau, attribut, None) if materiau is not None else None
    if cible == "anomalies.codes":
        return [anomalie.code for anomalie in extraction.anomalies]
    if cible.startswith("champs."):
        reste = cible.removeprefix("champs.")
        nom, _, attribut = reste.rpartition(".")
        champ = extraction.champ(nom)
        return getattr(champ, attribut, None) if champ is not None else None
    if cible in ExtractionVoile.model_fields:
        return getattr(extraction, cible)
    return None


def evaluer_extraction_or(
    extraction: ExtractionVoile,
    attendu: dict[str, object],
    *,
    fiche: FicheExtraite | None = None,
) -> dict[str, Any]:
    """Compare une extraction à un attendu d'or, champ par champ.

    Les clés Lot 3a lisent la projection normalisée. Si la fiche source est
    fournie, les clés historiques du banc (``cotes.*``, ``materiau.*``…)
    sont déléguées à l'évaluateur existant, afin que le rapport d'or mesure
    aussi les 74 cibles 7792 sans dupliquer leur sémantique.
    """
    details: list[dict[str, Any]] = []
    corrects = 0
    valeur_heritage = None
    if fiche is not None:
        from seamtech_search.fiches.persistance import _valeur_extraite

        valeur_heritage = _valeur_extraite

    for cible, voulu in attendu.items():
        est_heritage = cible in _CLES_HERITAGE_EXACTES or cible.startswith(_PREFIXES_HERITAGE)
        if fiche is not None and est_heritage and valeur_heritage is not None:
            obtenu = valeur_heritage(fiche, cible)
            correct = _valeurs_equivalentes(obtenu, voulu)
        elif cible == "materiaux.designations_contient":
            obtenu = _valeur_lot3a(extraction, cible)
            correct = isinstance(obtenu, list) and any(
                _texte_compare(valeur) == _texte_compare(voulu) for valeur in obtenu
            )
        else:
            obtenu = _valeur_lot3a(extraction, cible)
            correct = _valeurs_equivalentes(obtenu, voulu)
        if correct:
            corrects += 1
        details.append(
            {
                "champ": cible,
                "attendu": voulu,
                "obtenu": obtenu,
                "correct": bool(correct),
            }
        )
    total = len(attendu)
    return {
        "total": total,
        "corrects": corrects,
        "taux": (corrects / total) if total else None,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Persistance PostgreSQL idempotente
# ---------------------------------------------------------------------------

_COLONNES_EXTRACTION_VOILE = (
    "id_extraction",
    "cle_idempotence",
    "id_fiche",
    "id_document",
    "code_fiche",
    "nom_fichier_source",
    "empreinte_sha256",
    "gabarit_code",
    "gabarit_version",
    "version_extracteur",
    "methode_principale",
    "type_voile",
    "gamme",
    "designation_brute",
    "modele_bateau_brut",
    "taille_bateau_brute",
    "slu_m",
    "sle_m",
    "sf_m",
    "shw_m",
    "spa_m2",
    "tetiere_m",
    "poids_kg",
    "materiaux",
    "champs",
    "anomalies",
    "score_qualite",
    "confiance_moyenne",
    "statut_extraction",
    "statut_plausibilite",
    "extrait_le",
    "cree_le",
    "maj_le",
    # Lot 3b (migration 029) — toujours en fin de liste : les requêtes Lot 3a
    # relisent par position via cette même liste.
    "boat_model_id",
    "statut_liaison",
    "methode_liaison",
    "lie_le",
    "lie_par",
)

_SQL_UPSERT_EXTRACTION_VOILE = """
INSERT INTO sail_extractions (
    cle_idempotence, id_fiche, id_document, code_fiche, nom_fichier_source,
    empreinte_sha256, gabarit_code, gabarit_version, version_extracteur,
    methode_principale, type_voile, gamme, designation_brute,
    modele_bateau_brut, taille_bateau_brute, slu_m, sle_m, sf_m, shw_m,
    spa_m2, tetiere_m, poids_kg, materiaux, champs, anomalies,
    score_qualite, confiance_moyenne, statut_extraction,
    statut_plausibilite, extrait_le
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
    %s, %s, %s, %s, now()
)
ON CONFLICT (cle_idempotence) DO UPDATE SET
    id_fiche = COALESCE(EXCLUDED.id_fiche, sail_extractions.id_fiche),
    id_document = COALESCE(EXCLUDED.id_document, sail_extractions.id_document),
    code_fiche = COALESCE(EXCLUDED.code_fiche, sail_extractions.code_fiche),
    nom_fichier_source = COALESCE(
        EXCLUDED.nom_fichier_source, sail_extractions.nom_fichier_source
    ),
    empreinte_sha256 = COALESCE(
        EXCLUDED.empreinte_sha256, sail_extractions.empreinte_sha256
    ),
    gabarit_code = EXCLUDED.gabarit_code,
    gabarit_version = EXCLUDED.gabarit_version,
    methode_principale = EXCLUDED.methode_principale,
    type_voile = EXCLUDED.type_voile,
    gamme = EXCLUDED.gamme,
    designation_brute = EXCLUDED.designation_brute,
    modele_bateau_brut = EXCLUDED.modele_bateau_brut,
    taille_bateau_brute = EXCLUDED.taille_bateau_brute,
    slu_m = EXCLUDED.slu_m,
    sle_m = EXCLUDED.sle_m,
    sf_m = EXCLUDED.sf_m,
    shw_m = EXCLUDED.shw_m,
    spa_m2 = EXCLUDED.spa_m2,
    tetiere_m = EXCLUDED.tetiere_m,
    poids_kg = EXCLUDED.poids_kg,
    materiaux = EXCLUDED.materiaux,
    champs = EXCLUDED.champs,
    anomalies = EXCLUDED.anomalies,
    score_qualite = EXCLUDED.score_qualite,
    confiance_moyenne = EXCLUDED.confiance_moyenne,
    statut_extraction = EXCLUDED.statut_extraction,
    statut_plausibilite = EXCLUDED.statut_plausibilite,
    extrait_le = now(),
    maj_le = now()
RETURNING id_extraction
"""

_SQL_CLE_EXISTANTE_EXTRACTION_VOILE = """
SELECT cle_idempotence
FROM sail_extractions
WHERE version_extracteur = %s
  AND (
      (%s IS NOT NULL AND id_document = %s)
      OR (%s IS NOT NULL AND id_fiche = %s)
      OR (%s IS NOT NULL AND empreinte_sha256 = %s)
  )
ORDER BY
  CASE
    WHEN %s IS NOT NULL AND id_document = %s THEN 0
    WHEN %s IS NOT NULL AND id_fiche = %s THEN 1
    ELSE 2
  END,
  id_extraction
LIMIT 1
"""

_SQL_LIRE_EXTRACTION_PAR_FICHE = f"""
SELECT {", ".join(_COLONNES_EXTRACTION_VOILE)}
FROM sail_extractions
WHERE id_fiche = %s
ORDER BY extrait_le DESC, id_extraction DESC
LIMIT 1
"""

_SQL_LIER_EXTRACTION_DOCUMENT = """
UPDATE sail_extractions
SET id_document = %s,
    empreinte_sha256 = COALESCE(%s, empreinte_sha256),
    nom_fichier_source = COALESCE(%s, nom_fichier_source),
    maj_le = now()
WHERE id_fiche = %s AND version_extracteur = %s
"""


def _parametres_extraction(extraction: ExtractionVoile) -> tuple[Any, ...]:
    return (
        extraction.cle_idempotence,
        extraction.id_fiche,
        extraction.id_document,
        extraction.code_fiche,
        extraction.nom_fichier_source,
        extraction.empreinte_sha256,
        extraction.gabarit_code,
        extraction.gabarit_version,
        extraction.version_extracteur,
        extraction.methode_principale,
        extraction.type_voile,
        extraction.gamme,
        extraction.designation_brute,
        extraction.modele_bateau_brut,
        extraction.taille_bateau_brute,
        extraction.slu_m,
        extraction.sle_m,
        extraction.sf_m,
        extraction.shw_m,
        extraction.spa_m2,
        extraction.tetiere_m,
        extraction.poids_kg,
        json.dumps(
            [materiau.model_dump(mode="json") for materiau in extraction.materiaux],
            ensure_ascii=False,
        ),
        json.dumps(
            [champ.model_dump(mode="json") for champ in extraction.champs],
            ensure_ascii=False,
        ),
        json.dumps(
            [anomalie.model_dump(mode="json") for anomalie in extraction.anomalies],
            ensure_ascii=False,
        ),
        extraction.score_qualite,
        extraction.confiance_moyenne,
        extraction.statut_extraction,
        extraction.statut_plausibilite,
    )


def enregistrer_extraction_voile_curseur(
    curseur: Any,  # noqa: ANN401 - curseur psycopg2 réel
    extraction: ExtractionVoile,
) -> int:
    """Upsert idempotent dans la transaction de l'appelant ; ne committe pas."""
    effective = extraction
    if not effective.cle_idempotence:
        effective = extraction.model_copy(update={"cle_idempotence": _cle_idempotence(extraction)})

    if (
        effective.id_document is not None
        or effective.id_fiche is not None
        or effective.empreinte_sha256 is not None
    ):
        curseur.execute(
            _SQL_CLE_EXISTANTE_EXTRACTION_VOILE,
            (
                effective.version_extracteur,
                effective.id_document,
                effective.id_document,
                effective.id_fiche,
                effective.id_fiche,
                effective.empreinte_sha256,
                effective.empreinte_sha256,
                effective.id_document,
                effective.id_document,
                effective.id_fiche,
                effective.id_fiche,
            ),
        )
        ligne = curseur.fetchone()
        if ligne is not None and str(ligne[0]) != effective.cle_idempotence:
            effective = effective.model_copy(update={"cle_idempotence": str(ligne[0])})

    curseur.execute(_SQL_UPSERT_EXTRACTION_VOILE, _parametres_extraction(effective))
    ligne = curseur.fetchone()
    if ligne is None:
        raise RuntimeError("L'enregistrement de l'extraction de voile n'a rendu aucun identifiant.")
    id_extraction = int(ligne[0])
    # Lot 3b : résolution déterministe du modèle de bateau dans la MÊME
    # transaction que l'écriture. Import paresseux pour éviter tout cycle
    # (liaison_voile lit ce module), et décision humaine préservée : une
    # extraction déjà « lie_humain » n'est jamais revisitée (esprit RG11).
    from seamtech_search.liaison_voile import resoudre_extraction_curseur

    resoudre_extraction_curseur(curseur, id_extraction)
    return id_extraction


def enregistrer_extraction_voile(
    index: Any,  # noqa: ANN401 - SearchIndex réel
    extraction: ExtractionVoile,
    *,
    connexion: Any = None,  # noqa: ANN401
) -> int:
    """Enregistre une extraction Lot 3a dans PostgreSQL, de façon idempotente."""
    if not getattr(index, "is_postgres", False):
        raise ValueError("Les extractions de voiles exigent PostgreSQL (couche métier, migration 028).")
    if connexion is not None:
        with connexion.cursor() as curseur:
            return enregistrer_extraction_voile_curseur(curseur, extraction)
    with index.connect() as connexion_locale:
        with connexion_locale.cursor() as curseur:
            return enregistrer_extraction_voile_curseur(curseur, extraction)


def lier_extraction_voile_document_curseur(
    curseur: Any,  # noqa: ANN401 - curseur psycopg2 réel
    *,
    id_fiche: int,
    id_document: int,
    empreinte_sha256: str | None = None,
    nom_fichier_source: str | None = None,
    version_extracteur: str = VERSION_EXTRACTEUR_VOILE,
) -> int:
    """Lie l'extraction d'une fiche à son document source dans la transaction."""
    curseur.execute(
        _SQL_LIER_EXTRACTION_DOCUMENT,
        (
            int(id_document),
            empreinte_sha256.strip().lower() if empreinte_sha256 else None,
            _nom_fichier(nom_fichier_source),
            int(id_fiche),
            version_extracteur,
        ),
    )
    return int(curseur.rowcount or 0)


def _vers_python(valeur: Any) -> Any:  # noqa: ANN401
    if isinstance(valeur, Decimal):
        return float(valeur)
    if isinstance(valeur, str):
        texte = valeur.strip()
        if texte.startswith(("[", "{")):
            try:
                return json.loads(texte)
            except json.JSONDecodeError:
                return valeur
        return valeur
    if isinstance(valeur, list):
        return [_vers_python(element) for element in valeur]
    if isinstance(valeur, dict):
        return {cle: _vers_python(element) for cle, element in valeur.items()}
    return valeur


def _extraction_depuis_ligne(ligne: tuple[Any, ...]) -> ExtractionVoile:
    donnees = {
        colonne: _vers_python(valeur)
        for colonne, valeur in zip(_COLONNES_EXTRACTION_VOILE, ligne, strict=True)
    }
    return ExtractionVoile.model_validate(donnees)


def lire_extraction_voile_par_fiche(
    index: Any,  # noqa: ANN401 - SearchIndex réel
    id_fiche: int,
) -> ExtractionVoile | None:
    """Dernière extraction Lot 3a d'une fiche, ou ``None`` si elle n'existe pas."""
    if not getattr(index, "is_postgres", False):
        raise ValueError("Les extractions de voiles exigent PostgreSQL (couche métier, migration 028).")
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(_SQL_LIRE_EXTRACTION_PAR_FICHE, (int(id_fiche),))
            ligne = curseur.fetchone()
    return _extraction_depuis_ligne(ligne) if ligne is not None else None
