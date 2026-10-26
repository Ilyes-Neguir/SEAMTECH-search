"""Lot 7 — famille accessoires : catégorie, projection, liaison, corrections.

Les accessoires (Lazy Bag, housses, tauds, capotes…) ne sont pas des voiles
soustraites : ils ont leur propre schéma métier (`accessory_extractions`,
migration 031), leur gabarit (`FICHE_ACCESSOIRE_V1`, générique par libellés
en attendant les échantillons réels) et une liaison bateau SIMPLIFIÉE —
exactement un modèle canonique ou aucun. Les invariants des Lots 3a/3b
restent entiers : provenance par champ, aucune valeur inventée (des
dimensions sans unité restent du texte), décision humaine jamais écrasée,
corrections historisées, 100 % local.

La catégorie d'une fiche n'est jamais silencieuse : proposée au dépôt par le
nom du dossier (« accessoire ») puis par la famille du gabarit détecté, elle
est toujours relisible et corrigeable par l'approbateur
(`changer_categorie_fiche`, source ``humain``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field

from seamtech_search.extractions_voile import (
    _convertir,
    _trace_preferee,
    construire_extraction_voile,
    enregistrer_extraction_voile_curseur,
)

# Réexporté : les appelants (dépôt, API, tests) parlent au module accessoires.
from seamtech_search.fiches.gabarits import (
    CODE_GABARIT_ACCESSOIRE,
    GABARIT_ACCESSOIRE_V1,
    GabaritDef,
)
from seamtech_search.fiches.modeles import ChampExtrait, FicheExtraite, Zone
from seamtech_search.liaison_voile import ExtractionIntrouvable, decider_resolution

LOGGER = logging.getLogger("seamtech-search.accessoires")

# CODE_GABARIT_ACCESSOIRE est un réexport délibéré : les appelants (dépôt,
# API, tests) parlent au module accessoires, pas aux gabarits.
__all__ = [
    "CODE_GABARIT_ACCESSOIRE",
    "VERSION_EXTRACTEUR_ACCESSOIRE",
    "AccessoireIntrouvable",
    "AccessoireRefuse",
    "AnomalieAccessoire",
    "ChampAccessoire",
    "ExtractionAccessoire",
    "FicheIntrouvable",
    "changer_categorie_fiche",
    "construire_extraction_accessoire",
    "corriger_accessoire",
    "detail_extraction_accessoire",
    "enregistrer_extraction_accessoire",
    "enregistrer_extraction_accessoire_curseur",
    "gabarit_accessoire_v1",
    "lier_accessoire",
    "lier_extraction_accessoire_document_curseur",
    "lister_extractions_accessoire",
    "proposer_categorie",
    "resoudre_accessoire_curseur",
]

VERSION_EXTRACTEUR_ACCESSOIRE = "accessoire-v1"

CategorieFiche = Literal["voile", "accessoire"]
SourceCategorie = Literal["dossier", "document", "humain"]

StatutExtractionAccessoire = Literal["complete", "partielle", "a_reprendre"]
StatutPlausibilite = Literal["plausible", "a_verifier", "incoherente", "indeterminee"]


class AccessoireRefuse(ValueError):
    """Demande refusée côté métier (valeur invalide, reclassification bloquée…)."""


class AccessoireIntrouvable(ExtractionIntrouvable):
    """Extraction d'accessoire inconnue — 404 côté API (même contrat que 3b)."""


class FicheIntrouvable(ExtractionIntrouvable):
    """Fiche inconnue pour une reclassification — 404 côté API."""


# ---------------------------------------------------------------------------
# Catégorie : proposition déterministe (jamais silencieuse)
# ---------------------------------------------------------------------------


def _texte_simple(valeur: str) -> str:
    """Minuscules, accents retirés — pour la lecture du nom de dossier."""
    decompose = unicodedata.normalize("NFKD", valeur)
    return "".join(c for c in decompose if not unicodedata.combining(c)).lower()


def proposer_categorie(
    nom_dossier: str | None,
    gabarit_code: str | None,
) -> tuple[str, str]:
    """Propose la catégorie d'une fiche au dépôt — ordre figé et explicite.

    1. Le nom du dossier contient « accessoire » (accents/casse normalisés)
       → ``("accessoire", "dossier")`` ;
    2. sinon la famille du gabarit détecté tranche → source ``document`` ;
    3. défaut ``("voile", "document")`` — le comportement historique.

    L'approbateur peut toujours reclasser ensuite (source ``humain``).
    """
    if nom_dossier and "accessoire" in _texte_simple(nom_dossier):
        return ("accessoire", "dossier")
    if gabarit_code and gabarit_code.startswith("FICHE_ACCESSOIRE"):
        return ("accessoire", "document")
    return ("voile", "document")


def gabarit_accessoire_v1() -> GabaritDef:
    """Le gabarit générique accessoires (défini avec les gabarits embarqués)."""
    return GABARIT_ACCESSOIRE_V1


# ---------------------------------------------------------------------------
# Modèle de projection
# ---------------------------------------------------------------------------


EtatChampAccessoire = Literal["lu", "present_non_quantifie", "lu_sans_trace"]
GraviteAnomalie = Literal["faible", "moyenne", "forte"]


class ChampAccessoire(BaseModel):
    """Provenance d'un champ projeté — la valeur canonique et sa source lue."""

    nom: str
    etat: EtatChampAccessoire = "lu"
    valeur_brute: str | None = None
    valeur_texte: str | None = None
    valeur_canonique: float | int | None = None
    unite_brute: str | None = None
    unite_canonique: str | None = None
    confiance: float | None = None
    page: int | None = None
    zone: Zone | None = None
    methode: str | None = None


class AnomalieAccessoire(BaseModel):
    """Anomalie de plausibilité/lecture — jamais silencieuse."""

    code: str
    gravite: GraviteAnomalie = "faible"
    message: str = ""
    champs: list[str] = Field(default_factory=list)


class ExtractionAccessoire(BaseModel):
    """Projection canonique d'une fiche d'accessoire (table accessory_extractions)."""

    id_extraction: int | None = None
    cle_idempotence: str = ""
    id_document: int | None = None
    id_fiche: int | None = None
    code_fiche: str | None = None
    gabarit_code: str | None = None
    gabarit_version: int | None = None
    version_extracteur: str = VERSION_EXTRACTEUR_ACCESSOIRE
    methode_principale: str = "gabarit"
    nom_fichier_source: str | None = None
    empreinte_sha256: str | None = None

    type_accessoire: str | None = None
    designation_brute: str | None = None
    matiere: str | None = None
    couleur: str | None = None
    dimensions_texte: str | None = None
    longueur_m: float | None = None
    largeur_m: float | None = None
    hauteur_m: float | None = None
    quantite: int | None = None
    modele_bateau_brut: str | None = None
    taille_bateau_brute: str | None = None

    champs: list[ChampAccessoire] = Field(default_factory=list)
    anomalies: list[AnomalieAccessoire] = Field(default_factory=list)
    score_qualite: float | None = None
    confiance_moyenne: float | None = None
    statut_extraction: StatutExtractionAccessoire = "a_reprendre"
    statut_plausibilite: StatutPlausibilite = "indeterminee"

    boat_model_id: int | None = None
    statut_liaison: str = "non_resolu"
    methode_liaison: str | None = None
    lie_le: datetime | None = None
    lie_par: int | None = None
    cree_le: datetime | None = None
    maj_le: datetime | None = None


# ---------------------------------------------------------------------------
# Construction depuis une FicheExtraite
# ---------------------------------------------------------------------------

_RE_DIMENSION = re.compile(r"(\d+(?:[.,]\d+)?)\s*(mm|cm|m)\b", re.IGNORECASE)


def _nom_fichier(chemin: str | None) -> str | None:
    if not chemin:
        return None
    nom = PurePosixPath(str(chemin).replace("\\", "/")).name.strip()
    return nom or None


def _texte_trace(trace: ChampExtrait | None) -> str:
    """Valeur lue d'une trace : normalisée d'abord, brute sinon (nettoyée)."""
    if trace is None:
        return ""
    return (trace.valeur_normalisee or trace.valeur_brute or "").strip()


def _champ_depuis_trace(
    nom: str,
    trace: ChampExtrait | None,
    *,
    valeur_canonique: Any = None,  # noqa: ANN401
    unite_canonique: str | None = None,
    unite_brute: str | None = None,
    valeur_texte: str | None = None,
) -> ChampAccessoire:
    """Provenance d'un champ projeté — jamais de valeur sans sa source."""
    if trace is None:
        return ChampAccessoire(
            nom=nom,
            etat="lu_sans_trace",
            valeur_texte=valeur_texte,
            valeur_canonique=valeur_canonique,
            unite_canonique=unite_canonique,
            methode="fiche_extraite",
        )
    brut = (trace.valeur_brute or "").strip() or None
    return ChampAccessoire(
        nom=nom,
        etat="lu" if brut else "present_non_quantifie",
        valeur_brute=brut,
        valeur_texte=valeur_texte if valeur_texte is not None else (_texte_trace(trace) or None),
        valeur_canonique=valeur_canonique,
        unite_brute=unite_brute,
        unite_canonique=unite_canonique,
        confiance=trace.confiance,
        page=trace.page or None,
        zone=trace.zone,
        methode=trace.methode,
    )


def _parser_dimensions(texte: str) -> list[tuple[float, str]]:
    """(valeur en mètres, unité brute) pour chaque mesure AVEC unité explicite.

    Sans unité, aucune conversion n'est tentée : un « 120 x 45 » nu reste du
    texte (cm ? mm ? m ?) — deviner serait fabriquer une cote.
    """
    mesures: list[tuple[float, str]] = []
    for correspondance in _RE_DIMENSION.finditer(texte):
        valeur = float(correspondance.group(1).replace(",", "."))
        unite = correspondance.group(2).lower()
        en_metres = _convertir(valeur, unite, "longueur")
        if en_metres is not None and en_metres > 0:
            mesures.append((en_metres, unite))
    return mesures


def construire_extraction_accessoire(
    fiche: FicheExtraite,
    *,
    chemin_source: str | None = None,
    empreinte_sha256: str | None = None,
    id_fiche: int | None = None,
    id_document: int | None = None,
) -> ExtractionAccessoire:
    """Projette une FicheExtraite d'accessoire en extraction canonique."""
    traces = list(fiche.tous_les_champs())
    champs: list[ChampAccessoire] = []
    anomalies: list[AnomalieAccessoire] = []

    def texte(nom_trace: str, nom_champ: str) -> tuple[str | None, ChampExtrait | None]:
        trace = _trace_preferee(traces, nom_trace)
        valeur = _texte_trace(trace) if trace is not None else ""
        if valeur:
            champs.append(_champ_depuis_trace(nom_champ, trace, valeur_texte=valeur))
            return valeur, trace
        return None, trace

    type_accessoire, _ = texte("accessoire.type", "type_accessoire")
    matiere, _ = texte("accessoire.matiere", "matiere")
    couleur, _ = texte("accessoire.couleur", "couleur")
    dimensions_texte, trace_dimensions = texte("accessoire.dimensions", "dimensions_texte")
    if matiere is None and (fiche.tissu_texte or "").strip():
        matiere = fiche.tissu_texte.strip()
        champs.append(
            _champ_depuis_trace("matiere", None, valeur_texte=matiere)
        )

    longueur_m = largeur_m = hauteur_m = None
    if dimensions_texte:
        mesures = _parser_dimensions(dimensions_texte)
        if mesures:
            noms = ("longueur_m", "largeur_m", "hauteur_m")
            valeurs = [mesures[i][0] if i < len(mesures) else None for i in range(3)]
            longueur_m, largeur_m, hauteur_m = valeurs
            for indice, (valeur, unite) in enumerate(mesures[:3]):
                champs.append(
                    _champ_depuis_trace(
                        noms[indice],
                        trace_dimensions,
                        valeur_canonique=valeur,
                        unite_canonique="m",
                        unite_brute=unite,
                    )
                )
            if len(mesures) > 3:
                anomalies.append(
                    AnomalieAccessoire(
                        code="dimensions_excedentaires",
                        gravite="moyenne",
                        message=(
                            "Plus de trois mesures dans les dimensions : seules les "
                            "trois premières (longueur, largeur, hauteur) sont "
                            "projetées ; le texte complet reste dans dimensions_texte."
                        ),
                        champs=["dimensions_texte"],
                    )
                )
        else:
            anomalies.append(
                AnomalieAccessoire(
                    code="dimensions_sans_unite",
                    gravite="moyenne",
                    message=(
                        "Dimensions lues sans unité explicite : conservées en texte, "
                        "aucune cote en mètres n'est inventée."
                    ),
                    champs=["dimensions_texte"],
                )
            )

    quantite: int | None = None
    trace_quantite = _trace_preferee(traces, "accessoire.quantite")
    if trace_quantite is not None:
        brut_q = _texte_trace(trace_quantite)
        try:
            quantite = int(float(brut_q.replace(",", ".")))
            if quantite < 1:
                raise ValueError
            champs.append(
                _champ_depuis_trace("quantite", trace_quantite, valeur_canonique=quantite)
            )
        except (ValueError, AttributeError):
            quantite = None
            anomalies.append(
                AnomalieAccessoire(
                    code="quantite_illisible",
                    gravite="moyenne",
                    message=f"Quantité lue mais non interprétable : « {brut_q} ».",
                    champs=["quantite"],
                )
            )

    modele_brut, _ = texte("accessoire.modele_bateau", "modele_bateau_brut")
    if not modele_brut:
        parties = [p.strip() for p in (fiche.bateau_nom, fiche.bateau_taille) if p and p.strip()]
        modele_brut = " ".join(parties) or None
        if modele_brut:
            champs.append(
                _champ_depuis_trace("modele_bateau_brut", None, valeur_texte=modele_brut)
            )

    a_quelque_chose = any(
        valeur is not None
        for valeur in (type_accessoire, matiere, dimensions_texte, modele_brut)
    )
    if not a_quelque_chose:
        statut_extraction: StatutExtractionAccessoire = "a_reprendre"
        statut_plausibilite: StatutPlausibilite = "indeterminee"
    elif type_accessoire and longueur_m is not None:
        statut_extraction = "complete"
        statut_plausibilite = "plausible" if not anomalies else "a_verifier"
    else:
        statut_extraction = "partielle"
        statut_plausibilite = "a_verifier"

    attendus = (
        type_accessoire is not None,
        matiere is not None,
        couleur is not None,
        longueur_m is not None,
        quantite is not None,
        modele_brut is not None,
    )
    score_qualite = round(sum(attendus) / len(attendus), 4)
    confiances = [c.confiance for c in champs if c.confiance is not None]
    confiance_moyenne = round(sum(confiances) / len(confiances), 4) if confiances else None

    extraction = ExtractionAccessoire(
        id_fiche=int(id_fiche) if id_fiche is not None else None,
        id_document=int(id_document) if id_document is not None else None,
        code_fiche=(fiche.code or "").strip() or None,
        gabarit_code=fiche.gabarit_code,
        gabarit_version=fiche.gabarit_version,
        nom_fichier_source=_nom_fichier(chemin_source or fiche.fichier_source),
        empreinte_sha256=(empreinte_sha256 or "").strip().lower() or None,
        type_accessoire=type_accessoire,
        designation_brute=(fiche.titre or "").strip() or None,
        matiere=matiere,
        couleur=couleur,
        dimensions_texte=dimensions_texte,
        longueur_m=longueur_m,
        largeur_m=largeur_m,
        hauteur_m=hauteur_m,
        quantite=quantite,
        modele_bateau_brut=modele_brut,
        taille_bateau_brute=(fiche.bateau_taille or "").strip() or None,
        champs=champs,
        anomalies=anomalies,
        score_qualite=score_qualite,
        confiance_moyenne=confiance_moyenne,
        statut_extraction=statut_extraction,
        statut_plausibilite=statut_plausibilite,
    )
    extraction.cle_idempotence = _cle_idempotence(extraction)
    return extraction


def _cle_idempotence(extraction: ExtractionAccessoire) -> str:
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
# Persistance (miroir du Lot 3a, résolution bateau incluse)
# ---------------------------------------------------------------------------

_COLONNES = (
    "cle_idempotence", "id_document", "id_fiche", "code_fiche", "gabarit_code",
    "gabarit_version", "version_extracteur", "methode_principale", "nom_fichier_source", "empreinte_sha256",
    "type_accessoire", "designation_brute", "matiere", "couleur", "dimensions_texte",
    "longueur_m", "largeur_m", "hauteur_m", "quantite", "modele_bateau_brut",
    "taille_bateau_brute", "champs", "anomalies", "score_qualite", "confiance_moyenne",
    "statut_extraction", "statut_plausibilite",
)

_SQL_UPSERT = (
    f"INSERT INTO accessory_extractions ({', '.join(_COLONNES)}) VALUES ("
    + ", ".join("%s::jsonb" if c in ("champs", "anomalies") else "%s" for c in _COLONNES)
    + ") ON CONFLICT (cle_idempotence) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in _COLONNES if c != "cle_idempotence")
    + ", maj_le = now() RETURNING id_extraction"
)

_SQL_CLE_EXISTANTE = (
    "SELECT cle_idempotence FROM accessory_extractions WHERE version_extracteur = %s "
    "AND ((%s IS NOT NULL AND id_document = %s) OR (%s IS NOT NULL AND id_fiche = %s) "
    "OR (%s IS NOT NULL AND empreinte_sha256 = %s)) ORDER BY id_extraction LIMIT 1"
)


def _parametres(extraction: ExtractionAccessoire) -> tuple[Any, ...]:
    valeurs: dict[str, Any] = extraction.model_dump()
    valeurs["champs"] = json.dumps(
        [c.model_dump(mode="json") for c in extraction.champs], ensure_ascii=False
    )
    valeurs["anomalies"] = json.dumps(
        [a.model_dump(mode="json") for a in extraction.anomalies], ensure_ascii=False
    )
    return tuple(valeurs[c] for c in _COLONNES)


def enregistrer_extraction_accessoire_curseur(
    curseur: Any,  # noqa: ANN401 - curseur psycopg2 réel
    extraction: ExtractionAccessoire,
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
            _SQL_CLE_EXISTANTE,
            (
                effective.version_extracteur,
                effective.id_document,
                effective.id_document,
                effective.id_fiche,
                effective.id_fiche,
                effective.empreinte_sha256,
                effective.empreinte_sha256,
            ),
        )
        ligne = curseur.fetchone()
        if ligne is not None and str(ligne[0]) != effective.cle_idempotence:
            effective = effective.model_copy(update={"cle_idempotence": str(ligne[0])})
    curseur.execute(_SQL_UPSERT, _parametres(effective))
    ligne = curseur.fetchone()
    if ligne is None:
        raise RuntimeError("L'enregistrement de l'extraction d'accessoire n'a rendu aucun identifiant.")
    id_extraction = int(ligne[0])
    # Résolution déterministe DU modèle de bateau dans la même transaction —
    # même moteur de décision que les voiles (decider_resolution), projection
    # simplifiée : un modèle ou aucun. Une décision humaine prime toujours.
    resoudre_accessoire_curseur(curseur, id_extraction)
    return id_extraction


def enregistrer_extraction_accessoire(
    index: Any,  # noqa: ANN401 - SearchIndex réel
    extraction: ExtractionAccessoire,
    *,
    connexion: Any = None,  # noqa: ANN401
) -> int:
    """Enregistre une extraction d'accessoire dans PostgreSQL (idempotent)."""
    if not getattr(index, "is_postgres", False):
        raise ValueError("Les accessoires exigent PostgreSQL (migration 031).")
    if connexion is not None:
        with connexion.cursor() as curseur:
            return enregistrer_extraction_accessoire_curseur(curseur, extraction)
    with index.connect() as connexion_locale:
        with connexion_locale.cursor() as curseur:
            return enregistrer_extraction_accessoire_curseur(curseur, extraction)


_SQL_LIER_DOCUMENT = (
    "UPDATE accessory_extractions SET id_document = %s, "
    "empreinte_sha256 = COALESCE(%s, empreinte_sha256), "
    "nom_fichier_source = COALESCE(%s, nom_fichier_source), maj_le = now() "
    "WHERE id_fiche = %s AND version_extracteur = %s"
)


def lier_extraction_accessoire_document_curseur(
    curseur: Any,  # noqa: ANN401
    *,
    id_fiche: int,
    id_document: int,
    empreinte_sha256: str | None = None,
    nom_fichier_source: str | None = None,
    version_extracteur: str = VERSION_EXTRACTEUR_ACCESSOIRE,
) -> int:
    """Lie l'extraction d'une fiche à son document source dans la transaction."""
    curseur.execute(
        _SQL_LIER_DOCUMENT,
        (
            int(id_document),
            empreinte_sha256.strip().lower() if empreinte_sha256 else None,
            _nom_fichier(nom_fichier_source),
            int(id_fiche),
            version_extracteur,
        ),
    )
    return int(curseur.rowcount or 0)


# ---------------------------------------------------------------------------
# Liaison bateau simplifiée (un modèle ou aucun)
# ---------------------------------------------------------------------------


def resoudre_accessoire_curseur(curseur: Any, id_extraction: int) -> dict[str, Any]:  # noqa: ANN401
    """Applique la décision automatique — jamais sur une décision humaine."""
    curseur.execute(
        "SELECT modele_bateau_brut, id_fiche, statut_liaison, boat_model_id, methode_liaison "
        "FROM accessory_extractions WHERE id_extraction = %s FOR UPDATE",
        (int(id_extraction),),
    )
    ligne = curseur.fetchone()
    if ligne is None:
        raise AccessoireIntrouvable(f"Extraction d'accessoire {id_extraction} inconnue.")
    brut, id_fiche, statut_actuel, id_modele_actuel, methode_actuelle = ligne
    if statut_actuel == "lie_humain":
        return {
            "id_extraction": int(id_extraction),
            "statut_liaison": "lie_humain",
            "boat_model_id": int(id_modele_actuel) if id_modele_actuel is not None else None,
            "methode_liaison": methode_actuelle,
            "candidats": [],
            "inchange": True,
        }
    decision = decider_resolution(
        curseur,
        modele_bateau_brut=str(brut) if brut is not None else None,
        id_fiche=int(id_fiche) if id_fiche is not None else None,
    )
    lie = decision["boat_model_id"] is not None
    curseur.execute(
        "UPDATE accessory_extractions SET boat_model_id = %s, statut_liaison = %s, "
        "methode_liaison = %s, lie_le = CASE WHEN %s THEN now() ELSE NULL END, "
        "lie_par = NULL, maj_le = now() WHERE id_extraction = %s",
        (
            decision["boat_model_id"],
            decision["statut_liaison"],
            decision["methode_liaison"],
            lie,
            int(id_extraction),
        ),
    )
    return {"id_extraction": int(id_extraction), **decision, "inchange": False}


def _exiger_postgres(index: Any) -> None:  # noqa: ANN401
    if not getattr(index, "is_postgres", False):
        raise ValueError("Les accessoires exigent PostgreSQL (migration 031).")


def lier_accessoire(
    index: Any,  # noqa: ANN401
    id_extraction: int,
    *,
    id_utilisateur: int,
    id_modele: int | None = None,
    nouveau_modele: str | None = None,
) -> dict[str, Any]:
    """Décision HUMAINE de liaison : modèle existant ou nouveau modèle nommé.

    Un accessoire ne retient qu'UN modèle — pas de liste, pas de score : soit
    l'approbateur confirme exactement un modèle canonique, soit l'accessoire
    reste non lié.
    """
    _exiger_postgres(index)
    libelle = (nouveau_modele or "").strip()
    if (id_modele is None) == (not libelle):
        raise AccessoireRefuse(
            "Précisez soit un modèle existant, soit un nouveau modèle à créer — pas les deux."
        )
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute(
            "SELECT id_extraction FROM accessory_extractions WHERE id_extraction = %s FOR UPDATE",
            (int(id_extraction),),
        )
        if curseur.fetchone() is None:
            raise AccessoireIntrouvable(
                f"Extraction d'accessoire {id_extraction} inconnue."
            )
        if libelle:
            from seamtech_search.referentiel_bateaux import assurer_modele

            modele = assurer_modele(
                curseur, libelle, source="manuel", cree_par=int(id_utilisateur)
            )
            if modele is None:
                raise AccessoireRefuse(f"Modèle « {libelle} » illisible.")
            id_cible = modele.id_modele
            methode = "humain_nouveau_modele"
        else:
            curseur.execute(
                "SELECT id_modele FROM boat_models WHERE id_modele = %s", (int(id_modele),)
            )
            if curseur.fetchone() is None:
                raise AccessoireRefuse(f"Modèle de bateau {id_modele} inconnu du référentiel.")
            id_cible = int(id_modele)  # type: ignore[arg-type]
            methode = "humain"
        curseur.execute(
            "UPDATE accessory_extractions SET boat_model_id = %s, "
            "statut_liaison = 'lie_humain', methode_liaison = %s, lie_le = now(), "
            "lie_par = %s, maj_le = now() WHERE id_extraction = %s",
            (id_cible, methode, int(id_utilisateur), int(id_extraction)),
        )
    return detail_extraction_accessoire(index, int(id_extraction))


# ---------------------------------------------------------------------------
# Corrections historisées
# ---------------------------------------------------------------------------

_CHAMPS_CORRIGABLES: dict[str, str] = {
    "type_accessoire": "type_accessoire",
    "designation": "designation_brute",
    "matiere": "matiere",
    "couleur": "couleur",
    "dimensions": "dimensions_texte",
    "quantite": "quantite",
    "longueur_m": "longueur_m",
    "largeur_m": "largeur_m",
    "hauteur_m": "hauteur_m",
    "modele_bateau_brut": "modele_bateau_brut",
}


def _valider_correction(champ: str, valeur: Any) -> Any:  # noqa: ANN401
    if champ == "quantite":
        if isinstance(valeur, bool) or not isinstance(valeur, int) or valeur < 1:
            raise AccessoireRefuse("La quantité doit être un entier ≥ 1.")
        return valeur
    if champ in ("longueur_m", "largeur_m", "hauteur_m"):
        if isinstance(valeur, bool) or not isinstance(valeur, (int, float)) or valeur <= 0:
            raise AccessoireRefuse("Une cote corrigée doit être un nombre strictement positif (mètres).")
        return float(valeur)
    if valeur is None:
        return None
    if not isinstance(valeur, str) or not valeur.strip():
        raise AccessoireRefuse("La valeur corrigée doit être un texte non vide.")
    return valeur.strip()


def corriger_accessoire(
    index: Any,  # noqa: ANN401
    id_extraction: int,
    *,
    id_utilisateur: int,
    champ: str,
    valeur: Any,  # noqa: ANN401
    motif: str | None = None,
) -> dict[str, Any]:
    """Correction historisée d'un champ — l'extraction brute reste relisible."""
    _exiger_postgres(index)
    colonne = _CHAMPS_CORRIGABLES.get(champ)
    if colonne is None:
        raise AccessoireRefuse(
            "Champ non corrigeable : " + ", ".join(sorted(_CHAMPS_CORRIGABLES)) + "."
        )
    valeur_validee = _valider_correction(champ, valeur)
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute(
            f"SELECT {colonne} FROM accessory_extractions WHERE id_extraction = %s FOR UPDATE",
            (int(id_extraction),),
        )
        ligne = curseur.fetchone()
        if ligne is None:
            raise AccessoireIntrouvable(
                f"Extraction d'accessoire {id_extraction} inconnue."
            )
        avant = ligne[0]
        avant_json = float(avant) if isinstance(avant, Decimal) else avant
        maj: dict[str, Any] = {colonne: valeur_validee}
        if champ == "dimensions" and isinstance(valeur_validee, str):
            # Corriger le texte re-dérive les cotes — seulement si des unités
            # explicites sont présentes (même règle qu'à l'extraction).
            mesures = _parser_dimensions(valeur_validee)
            if mesures:
                maj["longueur_m"] = mesures[0][0]
                maj["largeur_m"] = mesures[1][0] if len(mesures) > 1 else None
                maj["hauteur_m"] = mesures[2][0] if len(mesures) > 2 else None
        affectations = ", ".join(f"{c} = %s" for c in maj)
        curseur.execute(
            f"UPDATE accessory_extractions SET {affectations}, maj_le = now() "
            "WHERE id_extraction = %s",
            (*maj.values(), int(id_extraction)),
        )
        curseur.execute(
            "INSERT INTO accessory_corrections (id_extraction, champ, valeur_avant, "
            "valeur_apres, motif, corrige_par) VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s)",
            (
                int(id_extraction),
                champ,
                json.dumps(avant_json, ensure_ascii=False),
                json.dumps(valeur_validee, ensure_ascii=False),
                (motif or "").strip() or None,
                int(id_utilisateur),
            ),
        )
        if champ == "modele_bateau_brut":
            # Le libellé change : l'ancienne liaison ne vaut plus — on repart
            # de la résolution déterministe (jamais d'héritage silencieux).
            curseur.execute(
                "UPDATE accessory_extractions SET boat_model_id = NULL, "
                "statut_liaison = 'non_resolu', methode_liaison = NULL, lie_le = NULL, "
                "lie_par = NULL WHERE id_extraction = %s",
                (int(id_extraction),),
            )
            resoudre_accessoire_curseur(curseur, int(id_extraction))
    return detail_extraction_accessoire(index, int(id_extraction))


# ---------------------------------------------------------------------------
# Lecture (liste / détail)
# ---------------------------------------------------------------------------

_COLONNES_LECTURE = (
    "a.id_extraction, a.cle_idempotence, a.id_document, a.id_fiche, a.code_fiche, "
    "a.gabarit_code, a.gabarit_version, a.version_extracteur, a.nom_fichier_source, "
    "a.empreinte_sha256, a.type_accessoire, a.designation_brute, a.matiere, a.couleur, "
    "a.dimensions_texte, a.longueur_m, a.largeur_m, a.hauteur_m, a.quantite, "
    "a.modele_bateau_brut, a.taille_bateau_brute, a.champs, a.anomalies, "
    "a.score_qualite, a.confiance_moyenne, a.statut_extraction, a.statut_plausibilite, "
    "a.boat_model_id, a.statut_liaison, a.methode_liaison, a.lie_le, a.lie_par, "
    "a.cree_le, a.maj_le, bm.nom_affiche AS nom_modele_lie"
)

_NOMS_LECTURE = (
    "id_extraction", "cle_idempotence", "id_document", "id_fiche", "code_fiche",
    "gabarit_code", "gabarit_version", "version_extracteur", "nom_fichier_source",
    "empreinte_sha256", "type_accessoire", "designation_brute", "matiere", "couleur",
    "dimensions_texte", "longueur_m", "largeur_m", "hauteur_m", "quantite",
    "modele_bateau_brut", "taille_bateau_brute", "champs", "anomalies",
    "score_qualite", "confiance_moyenne", "statut_extraction", "statut_plausibilite",
    "boat_model_id", "statut_liaison", "methode_liaison", "lie_le", "lie_par",
    "cree_le", "maj_le", "nom_modele_lie",
)


def _extraction_depuis_ligne(ligne: tuple[Any, ...]) -> tuple[ExtractionAccessoire, str | None]:
    donnees = dict(zip(_NOMS_LECTURE, ligne, strict=True))
    nom_modele_lie = donnees.pop("nom_modele_lie")
    for cle in ("longueur_m", "largeur_m", "hauteur_m", "score_qualite", "confiance_moyenne"):
        if isinstance(donnees.get(cle), Decimal):
            donnees[cle] = float(donnees[cle])  # type: ignore[arg-type]
    extraction = ExtractionAccessoire.model_validate(donnees)
    return extraction, (str(nom_modele_lie) if nom_modele_lie else None)


def _vers_json(extraction: ExtractionAccessoire, nom_modele_lie: str | None) -> dict[str, Any]:
    sortie = extraction.model_dump(mode="json")
    sortie["nom_modele_lie"] = nom_modele_lie
    return sortie


def lister_extractions_accessoire(
    index: Any,  # noqa: ANN401
    *,
    statut_liaison: str | None = None,
    recherche: str | None = None,
    limite: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Liste paginée des accessoires, du plus récent au plus ancien."""
    _exiger_postgres(index)
    conditions: list[str] = []
    parametres: list[Any] = []
    if statut_liaison:
        conditions.append("a.statut_liaison = %s")
        parametres.append(statut_liaison)
    if recherche and recherche.strip():
        motif = f"%{recherche.strip()}%"
        conditions.append(
            "(a.code_fiche ILIKE %s OR a.type_accessoire ILIKE %s OR a.matiere ILIKE %s "
            "OR a.modele_bateau_brut ILIKE %s OR a.nom_fichier_source ILIKE %s)"
        )
        parametres.extend([motif] * 5)
    clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute(
            f"SELECT count(*) FROM accessory_extractions a{clause}", tuple(parametres)
        )
        total = int(curseur.fetchone()[0])
        curseur.execute(
            f"SELECT {_COLONNES_LECTURE} FROM accessory_extractions a "
            "LEFT JOIN boat_models bm ON bm.id_modele = a.boat_model_id"
            + clause
            + " ORDER BY a.id_extraction DESC LIMIT %s OFFSET %s",
            (*parametres, max(1, min(int(limite), 200)), max(0, int(offset))),
        )
        lignes = curseur.fetchall()
    extractions = [_vers_json(*_extraction_depuis_ligne(ligne)) for ligne in lignes]
    return {"extractions": extractions, "total": total, "limite": limite, "offset": offset}


def detail_extraction_accessoire(index: Any, id_extraction: int) -> dict[str, Any]:  # noqa: ANN401
    """Détail complet : projection, modèle lié, candidats, corrections."""
    _exiger_postgres(index)
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute(
            f"SELECT {_COLONNES_LECTURE} FROM accessory_extractions a "
            "LEFT JOIN boat_models bm ON bm.id_modele = a.boat_model_id "
            "WHERE a.id_extraction = %s",
            (int(id_extraction),),
        )
        ligne = curseur.fetchone()
        if ligne is None:
            raise AccessoireIntrouvable(
                f"Extraction d'accessoire {id_extraction} inconnue."
            )
        extraction, nom_modele_lie = _extraction_depuis_ligne(ligne)
        curseur.execute(
            "SELECT champ, valeur_avant, valeur_apres, motif, corrige_par, corrige_le "
            "FROM accessory_corrections WHERE id_extraction = %s ORDER BY corrige_le, id_correction",
            (int(id_extraction),),
        )
        corrections = [
            {
                "champ": r[0],
                "valeur_avant": r[1],
                "valeur_apres": r[2],
                "motif": r[3],
                "corrige_par": int(r[4]) if r[4] is not None else None,
                "corrige_le": r[5].isoformat() if r[5] is not None else None,
            }
            for r in curseur.fetchall()
        ]
        candidats: list[dict[str, Any]] = []
        if extraction.boat_model_id is None and extraction.modele_bateau_brut:
            candidats = decider_resolution(
                curseur,
                modele_bateau_brut=extraction.modele_bateau_brut,
                id_fiche=extraction.id_fiche,
            )["candidats"]
    sortie = _vers_json(extraction, nom_modele_lie)
    sortie["corrections"] = corrections
    sortie["candidats"] = candidats
    sortie["modele_lie"] = (
        {"id_modele": extraction.boat_model_id, "nom_affiche": nom_modele_lie}
        if extraction.boat_model_id is not None
        else None
    )
    return sortie


# ---------------------------------------------------------------------------
# Reclassification (correction humaine de catégorie)
# ---------------------------------------------------------------------------

_TABLES_FAMILLE = {
    "voile": ("sail_extractions", "extraction_corrections"),
    "accessoire": ("accessory_extractions", "accessory_corrections"),
}


def _reconstruire_fiche_curseur(curseur: Any, id_fiche: int, code_fiche: str | None) -> FicheExtraite:  # noqa: ANN401
    """Reconstruit la FicheExtraite depuis les traces persistées (fiche_champ_extrait)."""
    curseur.execute(
        "SELECT champ, valeur_brute, valeur_normalisee, confiance, page, zone, "
        "rang, table_cible, colonne_cible, methode FROM fiche_champ_extrait "
        "WHERE id_fiche = %s ORDER BY rang",
        (int(id_fiche),),
    )
    champs: list[ChampExtrait] = []
    for nom, brut, normalisee, confiance, page, zone, rang, table_c, colonne_c, methode in curseur.fetchall():
        zone_modele = Zone()
        if zone:
            try:
                zone_modele = Zone.model_validate(zone if isinstance(zone, dict) else json.loads(zone))
            except Exception:  # noqa: BLE001 - zone illisible → zone vide, jamais d'échec
                zone_modele = Zone()
        champs.append(
            ChampExtrait(
                champ=str(nom),
                valeur_brute=brut or "",
                valeur_normalisee=normalisee or "",
                methode=str(methode or "texte"),
                confiance=float(confiance) if confiance is not None else None,
                page=int(page or 0),
                zone=zone_modele,
                rang=int(rang or 0),
                table_cible=str(table_c or ""),
                colonne_cible=str(colonne_c or ""),
            )
        )
    return FicheExtraite(code=code_fiche or "", champs=champs)


def changer_categorie_fiche(
    index: Any,  # noqa: ANN401
    id_fiche: int,
    *,
    categorie: str,
    id_utilisateur: int,
) -> dict[str, Any]:
    """Reclasse une fiche (voile ↔ accessoire) — décision humaine tracée.

    La projection de l'ancienne famille est remplacée par celle de la
    nouvelle, reconstruite depuis les traces persistées. Si l'ancienne
    projection porte des corrections humaines, on REFUSE : un travail humain
    ne disparaît pas en silence (l'approbateur doit d'abord trancher).
    """
    _exiger_postgres(index)
    if categorie not in _TABLES_FAMILLE:
        raise AccessoireRefuse("Catégorie invalide : « voile » ou « accessoire ».")
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute(
            "SELECT id_fiche, code, categorie FROM fiche WHERE id_fiche = %s FOR UPDATE",
            (int(id_fiche),),
        )
        ligne_fiche = curseur.fetchone()
        if ligne_fiche is None:
            raise FicheIntrouvable(f"Fiche {id_fiche} inconnue.")
        _id, code_fiche, categorie_actuelle = ligne_fiche
        if categorie_actuelle == categorie:
            curseur.execute(
                "UPDATE fiche SET categorie_source = 'humain' WHERE id_fiche = %s",
                (int(id_fiche),),
            )
            return {
                "id_fiche": int(id_fiche),
                "categorie": categorie,
                "categorie_source": "humain",
                "inchange": True,
            }
        table_ancienne, table_corrections = _TABLES_FAMILLE[str(categorie_actuelle)]
        curseur.execute(
            f"SELECT count(*) FROM {table_corrections} c JOIN {table_ancienne} e "
            "ON e.id_extraction = c.id_extraction WHERE e.id_fiche = %s",
            (int(id_fiche),),
        )
        if int(curseur.fetchone()[0]) > 0:
            raise AccessoireRefuse(
                "Reclassification refusée : la projection actuelle porte des "
                "corrections humaines — tranchez-les d'abord."
            )
        curseur.execute(
            f"SELECT modele_bateau_brut, code_fiche, id_document, empreinte_sha256, "  # noqa: S608 - table en liste blanche
            f"nom_fichier_source, gabarit_code, gabarit_version FROM {table_ancienne} "
            "WHERE id_fiche = %s ORDER BY id_extraction LIMIT 1",
            (int(id_fiche),),
        )
        ancienne = curseur.fetchone()
        fiche_reconstruite = _reconstruire_fiche_curseur(curseur, int(id_fiche), code_fiche)
        if categorie == "accessoire":
            nouvelle = construire_extraction_accessoire(
                fiche_reconstruite, id_fiche=int(id_fiche)
            )
        else:
            nouvelle = construire_extraction_voile(  # type: ignore[assignment]
                fiche_reconstruite, id_fiche=int(id_fiche)
            )
        if ancienne is not None:
            brut_ancien, code_ancien, doc_ancien, sha_ancien, nom_ancien, gab_ancien, gabv_ancien = ancienne
            if not nouvelle.modele_bateau_brut and brut_ancien:
                nouvelle.modele_bateau_brut = str(brut_ancien)
            nouvelle.code_fiche = nouvelle.code_fiche or code_ancien
            nouvelle.id_document = nouvelle.id_document or (
                int(doc_ancien) if doc_ancien is not None else None
            )
            nouvelle.empreinte_sha256 = nouvelle.empreinte_sha256 or sha_ancien
            nouvelle.nom_fichier_source = nouvelle.nom_fichier_source or nom_ancien
            nouvelle.gabarit_code = gab_ancien or nouvelle.gabarit_code
            nouvelle.gabarit_version = (
                int(gabv_ancien) if gabv_ancien is not None else nouvelle.gabarit_version
            )
            nouvelle.cle_idempotence = (
                _cle_idempotence(nouvelle)  # type: ignore[arg-type]
                if categorie == "accessoire"
                else nouvelle.cle_idempotence
            )
        curseur.execute(
            f"DELETE FROM {table_ancienne} WHERE id_fiche = %s", (int(id_fiche),)  # noqa: S608
        )
        curseur.execute(
            "UPDATE fiche SET categorie = %s, categorie_source = 'humain' WHERE id_fiche = %s",
            (categorie, int(id_fiche)),
        )
        if categorie == "accessoire":
            id_extraction = enregistrer_extraction_accessoire_curseur(
                curseur, nouvelle  # type: ignore[arg-type]
            )
        else:
            id_extraction = enregistrer_extraction_voile_curseur(curseur, nouvelle)  # type: ignore[arg-type]
        LOGGER.info(
            "Fiche %s reclassée %s → %s par l'utilisateur %s.",
            id_fiche, categorie_actuelle, categorie, id_utilisateur,
        )
    return {
        "id_fiche": int(id_fiche),
        "categorie": categorie,
        "categorie_source": "humain",
        "id_extraction": id_extraction,
        "inchange": False,
    }
