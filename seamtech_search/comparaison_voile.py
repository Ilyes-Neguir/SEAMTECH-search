"""Comparaison de voiles bloc par bloc — Lot 4b (Fonction A).

Deux extractions de voiles (Lot 3a), résolues vers le référentiel bateau
(Lot 3b), sont comparées bloc par bloc :

* identification (modèle canonique, type, gamme, code, gabarit, fichier) ;
* dimensions canoniques avec écarts — valeur dans l'unité, cm pour les
  longueurs, % par rapport à la voile de référence A ;
* matériaux appariés par rôle et niveau, grammages comparés ;
* qualité et traçabilité (scores, plausibilité RG16, statut de liaison,
  nombre de corrections d'approbateur — la valeur corrigée est celle qui
  est comparée, puisque les colonnes canoniques la portent déjà).

L'honnêteté prime sur le confort : « comparable même modèle » exige le même
`boat_model_id` canonique ET le même type de voile normalisé. Des modèles
différents ou non liés restent affichables côte à côte, mais signalés. Une
dimension manquante ne fabrique aucun écart. Et il n'y a ici AUCUN verdict
de réutilisation : les écarts sont des faits ; l'indice de confiance est la
Fonction B (Lot 5), avec ses propres garde-fous statistiques.

Lecture seule : aucune migration, aucune écriture, aucun service externe.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

from seamtech_search.extractions_voile import (
    _COLONNES_EXTRACTION_VOILE,
    ExtractionVoile,
    _extraction_depuis_ligne,
)
from seamtech_search.liaison_voile import ExtractionIntrouvable

LOGGER = logging.getLogger("seamtech_search.comparaison_voile")

DIMENSIONS_COMPAREES = ("slu_m", "sle_m", "sf_m", "shw_m", "spa_m2", "tetiere_m", "poids_kg")

#: Libellé atelier et unité canonique de chaque dimension comparée.
_LIBELLES_DIMENSIONS: dict[str, tuple[str, str]] = {
    "slu_m": ("SLU (guindant)", "m"),
    "sle_m": ("SLE (chute)", "m"),
    "sf_m": ("SF (bordure)", "m"),
    "shw_m": ("SHW (largeur mi-hauteur)", "m"),
    "spa_m2": ("SPA (surface)", "m²"),
    "tetiere_m": ("Têtière", "m"),
    "poids_kg": ("Poids", "kg"),
}

#: Seuil sous lequel le chevauchement de deux voiles est signalé insuffisant.
_CHEVAUCHEMENT_MIN_DIMENSIONS = 2

_COLONNES_SQL = ", ".join(_COLONNES_EXTRACTION_VOILE)


class ComparaisonRefusee(ValueError):
    """Demande de comparaison incohérente (ex. une extraction contre elle-même)."""


def normaliser_type_voile(valeur: str | None) -> str:
    """Clé de comparaison d'un type de voile : minuscules, sans accents."""
    if not valeur:
        return ""
    deplie = unicodedata.normalize("NFKD", valeur)
    sans_accents = "".join(c for c in deplie if not unicodedata.combining(c))
    return " ".join(sans_accents.lower().replace("-", " ").replace("_", " ").split())


def _arrondi(valeur: float, decimales: int) -> float:
    return round(float(valeur), decimales)


def _resume(extraction: ExtractionVoile, nom_modele: str | None) -> dict[str, Any]:
    return {
        "id_extraction": extraction.id_extraction,
        "code_fiche": extraction.code_fiche,
        "nom_fichier_source": extraction.nom_fichier_source,
        "type_voile": extraction.type_voile,
        "gamme": extraction.gamme,
        "modele_bateau_brut": extraction.modele_bateau_brut,
        "taille_bateau_brute": extraction.taille_bateau_brute,
        "boat_model_id": extraction.boat_model_id,
        "modele_lie": nom_modele,
        "statut_liaison": extraction.statut_liaison,
        "methode_liaison": extraction.methode_liaison,
        "gabarit_code": extraction.gabarit_code,
        "statut_extraction": extraction.statut_extraction,
        "statut_plausibilite": extraction.statut_plausibilite,
        "score_qualite": extraction.score_qualite,
        "confiance_moyenne": extraction.confiance_moyenne,
        "extrait_le": extraction.extrait_le.isoformat() if extraction.extrait_le else None,
        "cree_le": extraction.cree_le.isoformat() if extraction.cree_le else None,
    }


def _identique_texte(a: Any, b: Any) -> bool | None:  # noqa: ANN401
    if a is None or b is None:
        return None
    return normaliser_type_voile(str(a)) == normaliser_type_voile(str(b))


def comparer_extractions(
    a: ExtractionVoile,
    b: ExtractionVoile,
    *,
    noms_modeles: tuple[str | None, str | None] = (None, None),
    corrections: tuple[int | None, int | None] = (None, None),
) -> dict[str, Any]:
    """Compare deux extractions bloc par bloc (fonction pure).

    Les écarts de dimensions sont calculés B − A, le % par rapport à A
    (référence). Rien n'est inventé quand une valeur manque d'un côté.
    """
    if a.id_extraction is not None and a.id_extraction == b.id_extraction:
        raise ComparaisonRefusee("Comparer une extraction à elle-même n'a pas de sens.")

    meme_modele = (
        a.boat_model_id is not None and b.boat_model_id is not None and a.boat_model_id == b.boat_model_id
    )
    type_a = normaliser_type_voile(a.type_voile)
    type_b = normaliser_type_voile(b.type_voile)
    meme_type = bool(type_a) and type_a == type_b
    comparable = meme_modele and meme_type

    avertissements: list[str] = []
    if a.boat_model_id is None or b.boat_model_id is None:
        avertissements.append(
            "Au moins une extraction n'est pas liée à un modèle de bateau canonique "
            "(écran Extractions) : la comparaison même modèle n'est pas établie."
        )
    elif not meme_modele:
        avertissements.append(
            "Modèles de bateau DIFFÉRENTS : comparaison indicative seulement, "
            "ce n'est pas une comparaison même modèle."
        )
    if not meme_type:
        if not type_a or not type_b:
            avertissements.append("Type de voile non lu d'un côté : l'égalité de type n'est pas établie.")
        else:
            avertissements.append(
                f"Types de voile différents (« {a.type_voile} » / « {b.type_voile} »)."
            )
    if a.statut_plausibilite == "implausible" or b.statut_plausibilite == "implausible":
        avertissements.append(
            "Au moins une extraction est implausible (RG16) : ses cotes sont à "
            "vérifier avant toute conclusion."
        )

    identification = [
        {
            "champ": "modele_bateau",
            "libelle": "Modèle de bateau",
            "a": noms_modeles[0] or a.modele_bateau_brut,
            "b": noms_modeles[1] or b.modele_bateau_brut,
            "identique": meme_modele if (a.boat_model_id and b.boat_model_id) else _identique_texte(a.modele_bateau_brut, b.modele_bateau_brut),
        },
        {
            "champ": "type_voile",
            "libelle": "Type de voile",
            "a": a.type_voile,
            "b": b.type_voile,
            "identique": meme_type if (type_a and type_b) else None,
        },
        {
            "champ": "gamme",
            "libelle": "Gamme",
            "a": a.gamme,
            "b": b.gamme,
            "identique": _identique_texte(a.gamme, b.gamme),
        },
        {
            "champ": "code_fiche",
            "libelle": "Code fiche",
            "a": a.code_fiche,
            "b": b.code_fiche,
            "identique": _identique_texte(a.code_fiche, b.code_fiche),
        },
        {
            "champ": "gabarit_code",
            "libelle": "Gabarit d'extraction",
            "a": a.gabarit_code,
            "b": b.gabarit_code,
            "identique": _identique_texte(a.gabarit_code, b.gabarit_code),
        },
        {
            "champ": "nom_fichier_source",
            "libelle": "Fichier source",
            "a": a.nom_fichier_source,
            "b": b.nom_fichier_source,
            "identique": _identique_texte(a.nom_fichier_source, b.nom_fichier_source),
        },
    ]

    dimensions: list[dict[str, Any]] = []
    communes = 0
    ecart_max_pct: float | None = None
    ecart_max_champ: str | None = None
    identiques = 0
    for champ in DIMENSIONS_COMPAREES:
        libelle, unite = _LIBELLES_DIMENSIONS[champ]
        valeur_a = getattr(a, champ)
        valeur_b = getattr(b, champ)
        ligne: dict[str, Any] = {
            "champ": champ,
            "libelle": libelle,
            "unite": unite,
            "a": valeur_a,
            "b": valeur_b,
            "delta": None,
            "delta_cm": None,
            "delta_pct": None,
        }
        if valeur_a is None and valeur_b is None:
            ligne["etat"] = "manquant_des_deux"
        elif valeur_a is None:
            ligne["etat"] = "manquant_a"
        elif valeur_b is None:
            ligne["etat"] = "manquant_b"
        else:
            ligne["etat"] = "comparable"
            delta = float(valeur_b) - float(valeur_a)
            ligne["delta"] = _arrondi(delta, 3)
            if unite == "m":
                ligne["delta_cm"] = _arrondi(delta * 100.0, 1)
            if float(valeur_a) > 0:
                delta_pct = _arrondi(delta / float(valeur_a) * 100.0, 2)
                ligne["delta_pct"] = delta_pct
                if ecart_max_pct is None or abs(delta_pct) > abs(ecart_max_pct):
                    ecart_max_pct = delta_pct
                    ecart_max_champ = champ
            communes += 1
            if delta == 0:
                identiques += 1
        dimensions.append(ligne)

    if communes < _CHEVAUCHEMENT_MIN_DIMENSIONS:
        avertissements.append(
            f"Chevauchement insuffisant : {communes} dimension(s) commune(s) seulement "
            f"(minimum {_CHEVAUCHEMENT_MIN_DIMENSIONS} pour comparer utilement)."
        )

    materiaux_a = {(m.role.lower(), m.niveau): m for m in a.materiaux}
    materiaux_b = {(m.role.lower(), m.niveau): m for m in b.materiaux}
    materiaux: list[dict[str, Any]] = []
    for cle in sorted(set(materiaux_a) | set(materiaux_b), key=lambda c: (c[0], c[1] or 0)):
        mat_a = materiaux_a.get(cle)
        mat_b = materiaux_b.get(cle)

        def _vue(materiau: Any) -> dict[str, Any] | None:  # noqa: ANN401
            if materiau is None:
                return None
            return {
                "designation": materiau.designation,
                "grammage_g_m2": materiau.grammage_g_m2,
                "confiance": materiau.confiance,
            }

        delta_grammage_pct: float | None = None
        if (
            mat_a is not None and mat_b is not None
            and mat_a.grammage_g_m2 and mat_b.grammage_g_m2
        ):
            delta_grammage_pct = _arrondi(
                (float(mat_b.grammage_g_m2) - float(mat_a.grammage_g_m2)) / float(mat_a.grammage_g_m2) * 100.0,
                2,
            )
        materiaux.append(
            {
                "role": cle[0],
                "niveau": cle[1],
                "a": _vue(mat_a),
                "b": _vue(mat_b),
                "etat": "commun" if (mat_a is not None and mat_b is not None) else ("a_seul" if mat_a is not None else "b_seul"),
                "delta_grammage_pct": delta_grammage_pct,
            }
        )

    qualite = [
        {"champ": "statut_extraction", "libelle": "Complétude d'extraction", "a": a.statut_extraction, "b": b.statut_extraction},
        {"champ": "statut_plausibilite", "libelle": "Plausibilité RG16", "a": a.statut_plausibilite, "b": b.statut_plausibilite},
        {"champ": "score_qualite", "libelle": "Score qualité (0-1)", "a": a.score_qualite, "b": b.score_qualite},
        {"champ": "confiance_moyenne", "libelle": "Confiance moyenne des champs", "a": a.confiance_moyenne, "b": b.confiance_moyenne},
        {"champ": "statut_liaison", "libelle": "Liaison au modèle", "a": a.statut_liaison, "b": b.statut_liaison},
        {"champ": "corrections", "libelle": "Corrections approbateur", "a": corrections[0], "b": corrections[1]},
    ]

    return {
        "a": _resume(a, noms_modeles[0]),
        "b": _resume(b, noms_modeles[1]),
        "meme_modele_bateau": meme_modele,
        "meme_type_voile": meme_type,
        "comparable": comparable,
        "avertissements": avertissements,
        "blocs": {
            "identification": identification,
            "dimensions": dimensions,
            "materiaux": materiaux,
            "qualite": qualite,
        },
        "synthese": {
            "dimensions_communes": communes,
            "dimensions_identiques": identiques,
            "ecart_max_pct": ecart_max_pct,
            "ecart_max_champ": ecart_max_champ,
        },
    }


# ---------------------------------------------------------------------------
# Lecture PostgreSQL : couple comparé et candidats même modèle + type
# ---------------------------------------------------------------------------


def _exiger_postgres(index: Any, action: str) -> None:  # noqa: ANN401
    if not getattr(index, "is_postgres", False):
        raise ComparaisonRefusee(
            f"{action} exige PostgreSQL (couche métier, migrations 028/029)."
        )


def _charger_extraction(curseur: Any, id_extraction: int) -> ExtractionVoile:  # noqa: ANN401
    curseur.execute(
        f"SELECT {_COLONNES_SQL} FROM sail_extractions WHERE id_extraction = %s",
        (int(id_extraction),),
    )
    ligne = curseur.fetchone()
    if ligne is None:
        raise ExtractionIntrouvable(f"Extraction {id_extraction} inconnue.")
    return _extraction_depuis_ligne(tuple(ligne))


def _nom_modele(curseur: Any, extraction: ExtractionVoile) -> str | None:  # noqa: ANN401
    if extraction.boat_model_id is None:
        return None
    curseur.execute(
        "SELECT nom_canonique FROM boat_models WHERE id_modele = %s",
        (extraction.boat_model_id,),
    )
    ligne = curseur.fetchone()
    return str(ligne[0]) if ligne is not None else None


def _nombre_corrections(curseur: Any, id_extraction: int) -> int:  # noqa: ANN401
    curseur.execute(
        "SELECT COUNT(*) FROM extraction_corrections WHERE id_extraction = %s",
        (int(id_extraction),),
    )
    return int(curseur.fetchone()[0])


def comparaison_entre(index: Any, id_a: int, id_b: int) -> dict[str, Any]:  # noqa: ANN401
    """Comparaison complète de deux extractions persistées (lecture seule)."""
    _exiger_postgres(index, "La comparaison de voiles")
    if int(id_a) == int(id_b):
        raise ComparaisonRefusee("Comparer une extraction à elle-même n'a pas de sens.")
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            a = _charger_extraction(curseur, id_a)
            b = _charger_extraction(curseur, id_b)
            return comparer_extractions(
                a,
                b,
                noms_modeles=(_nom_modele(curseur, a), _nom_modele(curseur, b)),
                corrections=(
                    _nombre_corrections(curseur, int(id_a)),
                    _nombre_corrections(curseur, int(id_b)),
                ),
            )


def _dimensions_communes(a: ExtractionVoile, b: ExtractionVoile) -> int:
    return sum(
        1
        for champ in DIMENSIONS_COMPAREES
        if getattr(a, champ) is not None and getattr(b, champ) is not None
    )


def candidats_comparaison(
    index: Any,  # noqa: ANN401
    id_extraction: int,
    *,
    limite: int = 20,
) -> dict[str, Any]:
    """Extractions comparables « même modèle + même type » à une extraction.

    Une extraction non liée à un modèle canonique n'a aucun candidat garanti
    même modèle : la réponse le dit (`raison = "non_liee"`) au lieu de
    proposer des ressemblances de libellés.
    """
    _exiger_postgres(index, "La recherche de voiles comparables")
    limite_effective = max(1, min(int(limite), 100))
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            source = _charger_extraction(curseur, id_extraction)
            resume_source = _resume(source, _nom_modele(curseur, source))
            if source.boat_model_id is None:
                return {"source": resume_source, "candidats": [], "raison": "non_liee"}
            curseur.execute(
                f"SELECT {_COLONNES_SQL} FROM sail_extractions "
                "WHERE boat_model_id = %s AND id_extraction <> %s "
                "ORDER BY cree_le DESC, id_extraction DESC",
                (source.boat_model_id, int(id_extraction)),
            )
            lignes = curseur.fetchall()
            nom_source = resume_source["modele_lie"]
            candidats: list[dict[str, Any]] = []
            for ligne in lignes:
                candidate = _extraction_depuis_ligne(tuple(ligne))
                if normaliser_type_voile(candidate.type_voile) != normaliser_type_voile(source.type_voile):
                    continue
                if not normaliser_type_voile(source.type_voile):
                    continue
                candidats.append(
                    {
                        "extraction": _resume(candidate, nom_source),
                        "dimensions_communes": _dimensions_communes(source, candidate),
                    }
                )
            # Tri stable : plus de dimensions communes d'abord ; à égalité,
            # l'ordre de la requête (plus récentes d'abord) est conservé.
            candidats.sort(key=lambda c: -int(c["dimensions_communes"]))
            return {
                "source": resume_source,
                "candidats": candidats[:limite_effective],
                "raison": None,
            }
