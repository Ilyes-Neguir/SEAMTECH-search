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


def _resume(
    extraction: ExtractionVoile,
    nom_modele: str | None,
    code_fiche_metier: str | None = None,
) -> dict[str, Any]:
    return {
        "id_extraction": extraction.id_extraction,
        "code_fiche": extraction.code_fiche,
        # Code de la fiche métier rattachée (Lot 6) : l'écran ne rend un lien
        # vers /fiches/{code} que s'il est résolu — jamais de lien mort.
        "code_fiche_metier": code_fiche_metier,
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
    codes_fiches: tuple[str | None, str | None] = (None, None),
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
        "a": _resume(a, noms_modeles[0], codes_fiches[0]),
        "b": _resume(b, noms_modeles[1], codes_fiches[1]),
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
# Comparaison de N fiches — attributs à gauche, une colonne par fiche
# ---------------------------------------------------------------------------

#: Au-delà, la table n'entre plus dans un écran d'atelier : on refuse au lieu
#: d'étaler trente colonnes que personne ne lit.
NB_MAX_FICHES_COMPAREES = 12


def _etats(valeurs: list[Any], rang_reference: int) -> list[str]:  # noqa: ANN401
    etats: list[str] = []
    for rang, valeur in enumerate(valeurs):
        if valeur is None:
            etats.append("manquant")
        elif rang == rang_reference:
            etats.append("reference")
        else:
            etats.append("present")
    return etats


def _ligne_texte(
    champ: str,
    libelle: str,
    valeurs: list[Any],  # noqa: ANN401
    rang_reference: int,
) -> dict[str, Any]:
    """Une ligne d'attribut textuel : les valeurs, leur état, et c'est tout.

    Pas d'écart : « Ocean » contre « medium Ocean » ne se soustrait pas. La
    ligne dit seulement si c'est la même chose, et répond « je ne sais pas »
    quand une fiche n'a pas la valeur — l'identité n'est pas supposée.
    """
    connues = [valeur for valeur in valeurs if valeur is not None]
    return {
        "champ": champ,
        "libelle": libelle,
        "numerique": False,
        "unite": None,
        "valeurs": valeurs,
        "etats": _etats(valeurs, rang_reference),
        "nb_fiches_avec_valeur": len(connues),
        "identique": (len({normaliser_type_voile(str(valeur)) for valeur in connues}) == 1)
        if len(connues) == len(valeurs)
        else None,
    }


def _ligne_numerique(
    champ: str,
    libelle: str,
    unite: str | None,
    valeurs: list[Any],  # noqa: ANN401
    rang_reference: int,
) -> dict[str, Any]:
    """Une ligne d'attribut chiffré : écarts par rapport à la colonne de référence.

    L'écart est une soustraction, affichée dès deux fiches — c'est de
    l'arithmétique, pas une estimation, et aucun effectif minimum ne la
    conditionne. Il n'est en revanche calculé que là où les DEUX valeurs
    existent : une cote non lue ne devient pas un écart de zéro.
    """
    reference = valeurs[rang_reference]
    deltas: list[float | None] = []
    deltas_cm: list[float | None] = []
    deltas_pct: list[float | None] = []
    for rang, valeur in enumerate(valeurs):
        if valeur is None or reference is None or rang == rang_reference:
            deltas.append(None)
            deltas_cm.append(None)
            deltas_pct.append(None)
            continue
        delta = float(valeur) - float(reference)
        deltas.append(_arrondi(delta, 3))
        # L'atelier parle en centimètres : 0,05 m de guindant, c'est 5 cm.
        deltas_cm.append(_arrondi(delta * 100.0, 1) if unite == "m" else None)
        deltas_pct.append(_arrondi(delta / float(reference) * 100.0, 2) if float(reference) > 0 else None)
    connues = [float(valeur) for valeur in valeurs if valeur is not None]
    return {
        "champ": champ,
        "libelle": libelle,
        "numerique": True,
        "unite": unite,
        "valeurs": valeurs,
        "etats": _etats(valeurs, rang_reference),
        "nb_fiches_avec_valeur": len(connues),
        "deltas": deltas,
        "deltas_cm": deltas_cm,
        "deltas_pct": deltas_pct,
        "identique": (len(set(connues)) == 1) if len(connues) == len(valeurs) else None,
        "min": min(connues) if connues else None,
        "max": max(connues) if connues else None,
        "etendue": _arrondi(max(connues) - min(connues), 3) if len(connues) >= 2 else None,
        "ecart_max_pct": (
            max((pct for pct in deltas_pct if pct is not None), key=abs) if any(p is not None for p in deltas_pct) else None
        ),
    }


def comparer_plusieurs(
    extractions: list[ExtractionVoile],
    *,
    noms_modeles: list[str | None] | None = None,
    corrections: list[int | None] | None = None,
    codes_fiches: list[str | None] | None = None,
    id_reference: int | None = None,
) -> dict[str, Any]:
    """Compare N extractions : ATTRIBUTS À GAUCHE, une colonne par fiche.

    Mêmes blocs et mêmes états que la comparaison à deux, étendus à un groupe.
    Deux règles tiennent tout l'édifice :

    * **les écarts sont affichés dès deux fiches**, sans seuil — ce sont des
      soustractions, pas des estimations. Rien ne les conditionne, et surtout
      pas un effectif minimum ;
    * **un pourcentage sans cible ne veut rien dire** : « +2 % » par rapport à
      quoi ? La référence est donc NOMMÉE dans la sortie, et choisissable. Par
      défaut c'est la première colonne, jamais une moyenne implicite.

    Aucun verdict de réutilisation ici, aucune cohérence statistique : ce
    module mesure des écarts. Le verdict est la Fonction B (Lot 5), qui exige
    un effectif et l'écrit.
    """
    if len(extractions) < 2:
        raise ComparaisonRefusee("Comparer demande au moins deux fiches.")
    if len(extractions) > NB_MAX_FICHES_COMPAREES:
        raise ComparaisonRefusee(
            f"Comparer plus de {NB_MAX_FICHES_COMPAREES} fiches n'est pas lisible : "
            "la table est refusée au lieu d'étaler des colonnes que personne ne lit."
        )
    identifiants = [extraction.id_extraction for extraction in extractions if extraction.id_extraction is not None]
    if len(set(identifiants)) != len(identifiants):
        raise ComparaisonRefusee("Comparer une extraction à elle-même n'a pas de sens.")

    nombre = len(extractions)
    noms = list(noms_modeles or (None,) * nombre)
    corr = list(corrections or (None,) * nombre)
    codes = list(codes_fiches or (None,) * nombre)

    avertissements: list[str] = []
    rang_reference = 0
    if id_reference is not None:
        trouves = [
            rang
            for rang, extraction in enumerate(extractions)
            if extraction.id_extraction is not None and int(extraction.id_extraction) == int(id_reference)
        ]
        if trouves:
            rang_reference = trouves[0]
        else:
            avertissements.append(
                f"Référence {id_reference} absente du groupe : la première colonne sert de référence."
            )
    reference = extractions[rang_reference]

    # « Même modèle » n'est jamais une ressemblance de libellés : c'est
    # l'égalité de l'identifiant canonique, sur TOUTES les fiches du groupe.
    modeles = {extraction.boat_model_id for extraction in extractions}
    meme_modele = len(modeles) == 1 and None not in modeles
    types = [normaliser_type_voile(extraction.type_voile) for extraction in extractions]
    type_manquant = any(not valeur for valeur in types)
    meme_type = len(set(types)) == 1 and not type_manquant

    if any(extraction.boat_model_id is None for extraction in extractions):
        avertissements.append(
            "Au moins une fiche n'est pas liée à un modèle de bateau canonique "
            "(écran Extractions) : la comparaison même modèle n'est pas établie."
        )
    elif not meme_modele:
        avertissements.append(
            "Modèles de bateau DIFFÉRENTS : comparaison indicative seulement, "
            "ce n'est pas une comparaison même modèle."
        )
    if type_manquant:
        avertissements.append("Type de voile non lu sur au moins une fiche : l'égalité de type n'est pas établie.")
    elif not meme_type:
        avertissements.append("Types de voile différents au sein du groupe : comparaison indicative seulement.")
    if any(extraction.statut_plausibilite == "implausible" for extraction in extractions):
        avertissements.append(
            "Au moins une fiche est implausible (RG16) : ses cotes sont à vérifier avant toute conclusion."
        )

    identification: list[dict[str, Any]] = [
        _ligne_texte(
            "modele_bateau",
            "Modèle de bateau",
            [noms[rang] or extraction.modele_bateau_brut for rang, extraction in enumerate(extractions)],
            rang_reference,
        ),
        _ligne_texte("type_voile", "Type de voile", [e.type_voile for e in extractions], rang_reference),
        _ligne_texte("gamme", "Gamme", [e.gamme for e in extractions], rang_reference),
        _ligne_texte("code_fiche", "Code fiche", [e.code_fiche for e in extractions], rang_reference),
        _ligne_texte("gabarit_code", "Gabarit d'extraction", [e.gabarit_code for e in extractions], rang_reference),
        _ligne_texte(
            "nom_fichier_source", "Fichier source", [e.nom_fichier_source for e in extractions], rang_reference
        ),
    ]
    if meme_modele:
        identification[0]["identique"] = True
    elif all(e.boat_model_id is not None for e in extractions):
        identification[0]["identique"] = False
    if meme_type:
        identification[1]["identique"] = True
    elif not type_manquant:
        identification[1]["identique"] = False

    dimensions = [
        _ligne_numerique(
            champ,
            *_LIBELLES_DIMENSIONS[champ],
            [getattr(extraction, champ) for extraction in extractions],
            rang_reference,
        )
        for champ in DIMENSIONS_COMPAREES
    ]

    cle_materiau = lambda materiau: (materiau.role.lower(), materiau.niveau or 0)  # noqa: E731
    cles: list[tuple[str, int]] = sorted(
        {cle_materiau(materiau) for extraction in extractions for materiau in extraction.materiaux},
        key=lambda cle: (cle[0], cle[1]),
    )
    materiaux: list[dict[str, Any]] = []
    for cle in cles:
        par_fiche = [
            next((m for m in extraction.materiaux if cle_materiau(m) == cle), None) for extraction in extractions
        ]
        niveau = next((m.niveau for m in par_fiche if m is not None), None)
        suffixe = f" — niveau {niveau}" if niveau else ""
        materiaux.append(
            _ligne_texte(
                "materiau.designation",
                f"{cle[0].replace('_', ' ').capitalize()}{suffixe} : matière",
                [m.designation if m else None for m in par_fiche],
                rang_reference,
            )
        )
        materiaux.append(
            _ligne_numerique(
                "materiau.grammage",
                f"{cle[0].replace('_', ' ').capitalize()}{suffixe} : grammage",
                "g/m²",
                [m.grammage_g_m2 if m else None for m in par_fiche],
                rang_reference,
            )
        )
        materiaux[-2]["role"] = cle[0]
        materiaux[-2]["niveau"] = niveau
        materiaux[-1]["role"] = cle[0]
        materiaux[-1]["niveau"] = niveau

    qualite: list[dict[str, Any]] = [
        _ligne_texte(
            "statut_extraction",
            "Complétude d'extraction",
            [e.statut_extraction for e in extractions],
            rang_reference,
        ),
        _ligne_texte(
            "statut_plausibilite", "Plausibilité RG16", [e.statut_plausibilite for e in extractions], rang_reference
        ),
        _ligne_numerique(
            "score_qualite", "Score qualité", None, [e.score_qualite for e in extractions], rang_reference
        ),
        _ligne_numerique(
            "confiance_moyenne",
            "Confiance moyenne des champs",
            None,
            [e.confiance_moyenne for e in extractions],
            rang_reference,
        ),
        _ligne_texte("statut_liaison", "Liaison au modèle", [e.statut_liaison for e in extractions], rang_reference),
        _ligne_numerique(
            "corrections", "Corrections approbateur", None, list(corr), rang_reference
        ),
    ]

    # Le chevauchement et le plus grand écart ne comptent que les COTES : une
    # mesure physique. Les compter sur les scores de qualité donnerait un
    # « écart max » de 0 % sur deux fiches dont aucune cote n'a été lue — un
    # chiffre rassurant et faux.
    dimensions_comparables = [ligne for ligne in dimensions if ligne["nb_fiches_avec_valeur"] >= 2]
    if len(dimensions_comparables) < _CHEVAUCHEMENT_MIN_DIMENSIONS:
        avertissements.append(
            f"Chevauchement insuffisant : {len(dimensions_comparables)} cote(s) comparable(s) "
            f"seulement (minimum {_CHEVAUCHEMENT_MIN_DIMENSIONS} pour comparer utilement)."
        )

    ecart_max_pct: float | None = None
    ecart_max_champ: str | None = None
    ecart_max_libelle: str | None = None
    for ligne in dimensions_comparables:
        candidat = ligne.get("ecart_max_pct")
        if candidat is None:
            continue
        if ecart_max_pct is None or abs(candidat) > abs(ecart_max_pct):
            ecart_max_pct = candidat
            ecart_max_champ = ligne["champ"]
            ecart_max_libelle = ligne["libelle"]

    return {
        "nb_fiches": nombre,
        "reference": {
            "rang": rang_reference,
            "id_extraction": reference.id_extraction,
            "code_fiche": reference.code_fiche,
            "libelle": codes[rang_reference] or reference.code_fiche or f"Colonne {rang_reference + 1}",
        },
        "colonnes": [_resume(e, noms[rang], codes[rang]) for rang, e in enumerate(extractions)],
        "comparable": meme_modele and meme_type,
        "meme_modele_bateau": meme_modele,
        "meme_type_voile": meme_type,
        "avertissements": avertissements,
        "blocs": {
            "identification": identification,
            "dimensions": dimensions,
            "materiaux": materiaux,
            "qualite": qualite,
        },
        "synthese": {
            "dimensions_comparables": len(dimensions_comparables),
            "ecart_max_pct": ecart_max_pct,
            "ecart_max_champ": ecart_max_champ,
            "ecart_max_libelle": ecart_max_libelle,
            # Le plus grand effectif rencontré sur une cote : c'est cette
            # valeur que lit le seuil statistique (≥ 5) de la Fonction B.
            "effectif_max": max((ligne["nb_fiches_avec_valeur"] for ligne in dimensions), default=0),
        },
    }


def comparaison_entre_plusieurs(
    index: Any,  # noqa: ANN401
    ids: list[int],
    *,
    id_reference: int | None = None,
) -> dict[str, Any]:
    """Comparaison persistée de N extractions (lecture seule)."""
    _exiger_postgres(index, "La comparaison de voiles")
    if len(ids) < 2:
        raise ComparaisonRefusee("Comparer demande au moins deux fiches.")
    if len(set(int(identifiant) for identifiant in ids)) != len(ids):
        raise ComparaisonRefusee("Comparer une extraction à elle-même n'a pas de sens.")
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            fiches = [_charger_extraction(curseur, int(identifiant)) for identifiant in ids]
            return comparer_plusieurs(
                fiches,
                noms_modeles=[_nom_modele(curseur, fiche) for fiche in fiches],
                corrections=[_nombre_corrections(curseur, int(identifiant)) for identifiant in ids],
                codes_fiches=[_code_fiche_metier(curseur, fiche) for fiche in fiches],
                id_reference=id_reference,
            )


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


def _code_fiche_metier(curseur: Any, extraction: ExtractionVoile) -> str | None:  # noqa: ANN401
    """Code de la fiche métier rattachée à l'extraction (None si aucune)."""
    if extraction.id_fiche is None:
        return None
    curseur.execute(
        "SELECT code FROM fiche WHERE id_fiche = %s", (extraction.id_fiche,)
    )
    ligne = curseur.fetchone()
    return str(ligne[0]) if ligne is not None and ligne[0] else None


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
                codes_fiches=(
                    _code_fiche_metier(curseur, a),
                    _code_fiche_metier(curseur, b),
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
