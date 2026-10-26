"""Lot 5 — Fonction B : confiance de réutilisation d'une voile.

Question métier : « cette voile vérifiée ressemble-t-elle aux voiles déjà
connues pour ce modèle de bateau et ce type de voile ? » La réponse est un
**indice de cohérence robuste** (médiane / MAD / IQR), JAMAIS une probabilité
inventée. Règles d'honnêteté figées par le plan directeur v2 :

- **N < 5 échantillons → aucun pourcentage.** Le verdict est
  ``donnees_insuffisantes`` et s'affiche « Données insuffisantes (N/5) ».
- **Population hétérogène → aucun score moyen.** Si au moins la moitié des
  dimensions communes dispersent trop (IQR/médiane > 7,5 %), la population
  mélange vraisemblablement des voiles différentes : verdict
  ``heterogene``, score ``None``, mais les statistiques complètes restent
  affichées pour que l'approbateur juge.
- **Score robuste, jamais une moyenne de moyennes.** Chaque dimension est
  notée par un z-robuste (|x − médiane| / σ̂, σ̂ issu de l'IQR ou du MAD) ; le
  score global est la **médiane** des scores par dimension.
- **Seuil d'alerte 75.** En dessous : ``a_examiner`` — le score s'affiche
  avec sa justification complète, sans verdict positif.
- **Justification obligatoire** : N, médiane/Q1/Q3/IQR/MAD/min/max et écart
  de la candidate par dimension, matériaux comparés, fiches sources. Un
  score ne voyage jamais sans elle (la base le refuse aussi : migration
  030, contrainte de cohérence score/verdict).

La population = extractions liées au même modèle canonique (Lot 3b) et au
même type de voile normalisé (Lot 4b), plausibilité non ``implausible``. Les
colonnes portent déjà les valeurs corrigées par l'approbateur (Lot 3b les
applique à l'extraction) : ce sont toujours les valeurs vérifiées qui sont
comparées. La candidate est exclue de sa propre population. 100 % local.
"""

from __future__ import annotations

import json
import math
from typing import Any

from seamtech_search.comparaison_voile import DIMENSIONS_COMPAREES, normaliser_type_voile
from seamtech_search.extractions_voile import (
    _COLONNES_EXTRACTION_VOILE,
    ExtractionVoile,
    _extraction_depuis_ligne,
)
from seamtech_search.liaison_voile import ExtractionIntrouvable

CONFIANCE_MIN_ECHANTILLONS = 5
CONFIANCE_SEUIL_ALERTE = 75
#: Dispersion relative (IQR/médiane) au-delà de laquelle une dimension est
#: déclarée hétérogène : une même voile de série ne disperse pas ainsi.
SEUIL_HETEROGENEITE_REL = 0.075
VERDICTS = ("donnees_insuffisantes", "heterogene", "a_examiner", "compatible")

_MIN_N_SCORE_DIMENSION = 3
_COLONNES_SQL = ", ".join(_COLONNES_EXTRACTION_VOILE)


class ConfianceRefusee(ValueError):
    """Évaluation impossible : candidate non résolue (aucune population)."""


def _exiger_postgres(index: Any) -> None:  # noqa: ANN401
    if not getattr(index, "is_postgres", False):
        raise ConfianceRefusee(
            "La confiance de réutilisation exige PostgreSQL "
            "(couche métier, migrations 028 → 030)."
        )


# ---------------------------------------------------------------------------
# Statistiques robustes (pures)
# ---------------------------------------------------------------------------


def _quantile(valeurs_triees: list[float], p: float) -> float:
    """Quantile par interpolation linéaire (méthode « type 7 », numpy défaut)."""
    n = len(valeurs_triees)
    if n == 1:
        return valeurs_triees[0]
    position = p * (n - 1)
    bas = math.floor(position)
    haut = math.ceil(position)
    if bas == haut:
        return valeurs_triees[int(position)]
    poids = position - bas
    return valeurs_triees[bas] * (1.0 - poids) + valeurs_triees[haut] * poids


def statistiques(valeurs: list[float]) -> dict[str, Any]:
    """Résumé robuste d'un échantillon : médiane, quartiles, IQR, MAD, extrêmes."""
    triees = sorted(float(v) for v in valeurs)
    n = len(triees)
    if n == 0:
        return {
            "n": 0,
            "mediane": None,
            "q1": None,
            "q3": None,
            "iqr": None,
            "mad": None,
            "min": None,
            "max": None,
        }
    mediane = _quantile(triees, 0.5)
    q1 = _quantile(triees, 0.25)
    q3 = _quantile(triees, 0.75)
    mad = _quantile(sorted(abs(v - mediane) for v in triees), 0.5)
    return {
        "n": n,
        "mediane": round(mediane, 3),
        "q1": round(q1, 3),
        "q3": round(q3, 3),
        "iqr": round(q3 - q1, 3),
        "mad": round(mad, 3),
        "min": round(triees[0], 3),
        "max": round(triees[-1], 3),
    }


def _sigma_robust(stats: dict[str, Any]) -> float:
    """Estimation robuste de σ : IQR/1,349 sinon 1,4826·MAD (0 si constant)."""
    iqr = stats.get("iqr")
    if iqr is not None and iqr > 0:
        return float(iqr) / 1.349
    mad = stats.get("mad")
    if mad is not None and mad > 0:
        return 1.4826 * float(mad)
    return 0.0


def _score_dimension(valeur: float, stats: dict[str, Any]) -> tuple[float | None, float | None]:
    """Score 0–100 d'une valeur face à une distribution robuste (+ z robuste)."""
    mediane = stats.get("mediane")
    if mediane is None:
        return None, None
    ecart = abs(float(valeur) - float(mediane))
    sigma = _sigma_robust(stats)
    if sigma > 0:
        z = ecart / sigma
        return round(100.0 * math.exp(-0.5 * z * z), 1), round(z, 3)
    if ecart == 0:
        return 100.0, 0.0
    base = abs(float(mediane)) if mediane else 1.0
    relatif = ecart / base
    return round(max(0.0, min(100.0, 100.0 * (1.0 - relatif / 0.10))), 1), None


def _dimension_heterogene(stats: dict[str, Any]) -> bool:
    mediane = stats.get("mediane")
    iqr = stats.get("iqr")
    if mediane is None or iqr is None:
        return False
    base = abs(float(mediane)) if mediane else 0.0
    if base == 0:
        return float(iqr) > 0
    return float(iqr) / base > SEUIL_HETEROGENEITE_REL


def _justification_dimension(
    champ: str,
    valeur_candidate: float | None,
    stats: dict[str, Any],
) -> dict[str, Any]:
    score_dim: float | None = None
    z: float | None = None
    ecart_pct: float | None = None
    if valeur_candidate is not None and stats["n"] >= _MIN_N_SCORE_DIMENSION:
        score_dim, z = _score_dimension(float(valeur_candidate), stats)
    if valeur_candidate is not None and stats.get("mediane"):
        ecart_pct = round(
            (float(valeur_candidate) - float(stats["mediane"]))
            / abs(float(stats["mediane"]))
            * 100.0,
            2,
        )
    return {
        "champ": champ,
        "n": stats["n"],
        "mediane": stats["mediane"],
        "q1": stats["q1"],
        "q3": stats["q3"],
        "iqr": stats["iqr"],
        "mad": stats["mad"],
        "min": stats["min"],
        "max": stats["max"],
        "heterogene": _dimension_heterogene(stats),
        "valeur_candidate": valeur_candidate,
        "ecart_pct_mediane": ecart_pct,
        "z_robuste": z,
        "score_dim": score_dim,
    }


def _justification_materiaux(
    candidat: ExtractionVoile,
    population: list[ExtractionVoile],
) -> dict[str, Any]:
    """Concordance matériaux : part de la population au même rôle/niveau/désignation."""

    def _cle(role: str, niveau: int | None) -> tuple[str, int | None]:
        return (role.strip().lower(), niveau)

    par_role: dict[tuple[str, int | None], list[str]] = {}
    for fiche in population:
        for materiau in fiche.materiaux:
            par_role.setdefault(_cle(materiau.role, materiau.niveau), []).append(
                (materiau.designation or "").strip().lower()
            )
    detail: list[dict[str, Any]] = []
    parts: list[float] = []
    for materiau in candidat.materiaux:
        homologues = par_role.get(_cle(materiau.role, materiau.niveau), [])
        designation = (materiau.designation or "").strip().lower()
        part = (
            round(sum(1 for h in homologues if h == designation) / len(homologues), 3)
            if homologues
            else None
        )
        if part is not None:
            parts.append(part)
        detail.append(
            {
                "role": materiau.role,
                "niveau": materiau.niveau,
                "designation": materiau.designation,
                "grammage_g_m2": materiau.grammage_g_m2,
                "n_population_avec_role": len(homologues),
                "part_meme_materiau": part,
            }
        )
    return {
        "part_meme_materiau": round(sum(parts) / len(parts), 3) if parts else None,
        "detail": detail,
    }


def evaluer_population(
    candidat: ExtractionVoile,
    population: list[ExtractionVoile],
) -> dict[str, Any]:
    """Verdict de confiance pur (sans base) : candidate vs population de référence."""
    n = len(population)
    dimensions: list[dict[str, Any]] = []
    for champ in DIMENSIONS_COMPAREES:
        valeurs = [
            float(getattr(fiche, champ))
            for fiche in population
            if getattr(fiche, champ) is not None
        ]
        valeur_candidate = getattr(candidat, champ)
        if valeurs or valeur_candidate is not None:
            dimensions.append(
                _justification_dimension(
                    champ,
                    None if valeur_candidate is None else float(valeur_candidate),
                    statistiques(valeurs),
                )
            )
    justification: dict[str, Any] = {
        "n_population": n,
        "seuil_min_echantillons": CONFIANCE_MIN_ECHANTILLONS,
        "seuil_alerte": CONFIANCE_SEUIL_ALERTE,
        "dimensions": dimensions,
        "materiaux": _justification_materiaux(candidat, population),
        "sources": [
            {
                "id_extraction": fiche.id_extraction,
                "code_fiche": fiche.code_fiche,
                "nom_fichier_source": fiche.nom_fichier_source,
            }
            for fiche in population
        ],
    }
    communes = [d for d in dimensions if d["n"] >= _MIN_N_SCORE_DIMENSION]
    if n < CONFIANCE_MIN_ECHANTILLONS:
        return {
            "n_echantillons": n,
            "score": None,
            "verdict": "donnees_insuffisantes",
            "libelle_verdict": f"Données insuffisantes ({n}/{CONFIANCE_MIN_ECHANTILLONS})",
            "justification": justification,
        }
    heterogenes = [d for d in communes if d["heterogene"]]
    if communes and 2 * len(heterogenes) >= len(communes):
        return {
            "n_echantillons": n,
            "score": None,
            "verdict": "heterogene",
            "libelle_verdict": "Population hétérogène — score non calculé",
            "justification": justification,
        }
    scores = [d["score_dim"] for d in communes if d["score_dim"] is not None]
    if len(scores) < 2:
        return {
            "n_echantillons": n,
            "score": None,
            "verdict": "donnees_insuffisantes",
            "libelle_verdict": (
                "Données insuffisantes — dimensions communes trop peu nombreuses"
            ),
            "justification": justification,
        }
    score = round(float(statistiques(scores)["mediane"]), 1)
    verdict = "compatible" if score >= CONFIANCE_SEUIL_ALERTE else "a_examiner"
    return {
        "n_echantillons": n,
        "score": score,
        "verdict": verdict,
        "libelle_verdict": (
            f"{score} % — cohérente avec la population de référence"
            if verdict == "compatible"
            else f"{score} % — à examiner (sous le seuil d'alerte de "
            f"{CONFIANCE_SEUIL_ALERTE} %)"
        ),
        "justification": justification,
    }


# ---------------------------------------------------------------------------
# Couche base de données (PostgreSQL, via l'index applicatif)
# ---------------------------------------------------------------------------


def _charger_extraction(curseur: Any, id_extraction: int) -> ExtractionVoile:  # noqa: ANN401
    curseur.execute(
        f"SELECT {_COLONNES_SQL} FROM sail_extractions WHERE id_extraction = %s",
        (int(id_extraction),),
    )
    ligne = curseur.fetchone()
    if ligne is None:
        raise ExtractionIntrouvable(f"Extraction {id_extraction} inconnue.")
    return _extraction_depuis_ligne(tuple(ligne))


def _charger_population(
    curseur: Any,  # noqa: ANN401
    boat_model_id: int,
    type_voile: str | None,
    *,
    exclure_id: int | None,
) -> list[ExtractionVoile]:
    curseur.execute(
        f"SELECT {_COLONNES_SQL} FROM sail_extractions "
        "WHERE boat_model_id = %s AND statut_plausibilite <> 'implausible' "
        "ORDER BY id_extraction ASC",
        (int(boat_model_id),),
    )
    type_norm = normaliser_type_voile(type_voile)
    population: list[ExtractionVoile] = []
    for ligne in curseur.fetchall():
        extraction = _extraction_depuis_ligne(tuple(ligne))
        if exclure_id is not None and extraction.id_extraction == exclure_id:
            continue
        if normaliser_type_voile(extraction.type_voile) != type_norm:
            continue
        if all(getattr(extraction, champ) is None for champ in DIMENSIONS_COMPAREES):
            continue
        population.append(extraction)
    return population


def evaluer_extraction(
    index: Any,  # noqa: ANN401
    id_extraction: int,
    *,
    id_utilisateur: int,
) -> dict[str, Any]:
    """Évalue une candidate contre sa population et persiste le verdict (030)."""
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            candidat = _charger_extraction(curseur, id_extraction)
            if candidat.boat_model_id is None:
                raise ConfianceRefusee(
                    "Le modèle de bateau de cette extraction n'est pas résolu — "
                    "aucune population de référence n'existe."
                )
            population = _charger_population(
                curseur,
                candidat.boat_model_id,
                candidat.type_voile,
                exclure_id=candidat.id_extraction,
            )
            resultat = evaluer_population(candidat, population)
            # Liens fiches métier des sources (Lot 6) : résolus seulement si
            # la source est réellement rattachée à une fiche — l'écran ne
            # rend un lien que dans ce cas (jamais de lien mort).
            ids_fiches = {
                fiche.id_extraction: fiche.id_fiche
                for fiche in population
                if fiche.id_fiche is not None
            }
            if ids_fiches:
                curseur.execute(
                    "SELECT id_fiche, code FROM fiche WHERE id_fiche = ANY(%s)",
                    (list(ids_fiches.values()),),
                )
                codes = {int(id_fiche): str(code) for id_fiche, code in curseur.fetchall()}
                for source in resultat["justification"]["sources"]:
                    id_fiche = ids_fiches.get(source.get("id_extraction"))
                    source["code_fiche_metier"] = (
                        codes.get(int(id_fiche)) if id_fiche is not None else None
                    )
            curseur.execute(
                "INSERT INTO evaluations_confiance "
                "(id_extraction, boat_model_id, type_voile, n_echantillons, "
                "score, verdict, justification, cree_par) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s) "
                "RETURNING id_evaluation",
                (
                    candidat.id_extraction,
                    candidat.boat_model_id,
                    normaliser_type_voile(candidat.type_voile) or "type inconnu",
                    int(resultat["n_echantillons"]),
                    resultat["score"],
                    resultat["verdict"],
                    json.dumps(resultat["justification"], ensure_ascii=False),
                    int(id_utilisateur),
                ),
            )
            row = curseur.fetchone()
            id_evaluation = int(row[0]) if row else None
    return {
        "id_evaluation": id_evaluation,
        "id_extraction": candidat.id_extraction,
        "boat_model_id": candidat.boat_model_id,
        "type_voile": normaliser_type_voile(candidat.type_voile),
        **resultat,
    }


def lister_evaluations(
    index: Any,  # noqa: ANN401
    id_extraction: int,
    *,
    limite: int = 50,
) -> dict[str, Any]:
    """Historique des évaluations persistées d'une extraction (lecture seule)."""
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            _charger_extraction(curseur, id_extraction)  # 404 si inconnue
            curseur.execute(
                "SELECT id_evaluation, id_extraction, boat_model_id, type_voile, "
                "n_echantillons, score, verdict, justification, cree_par, cree_le "
                "FROM evaluations_confiance WHERE id_extraction = %s "
                "ORDER BY cree_le DESC, id_evaluation DESC LIMIT %s",
                (int(id_extraction), max(1, min(int(limite), 200))),
            )
            evaluations: list[dict[str, Any]] = []
            for ligne in curseur.fetchall():
                (
                    id_evaluation,
                    id_extraction_lu,
                    boat_model_id,
                    type_voile,
                    n_echantillons,
                    score,
                    verdict,
                    justification,
                    cree_par,
                    cree_le,
                ) = ligne
                evaluations.append(
                    {
                        "id_evaluation": int(id_evaluation),
                        "id_extraction": int(id_extraction_lu),
                        "boat_model_id": int(boat_model_id),
                        "type_voile": type_voile,
                        "n_echantillons": int(n_echantillons),
                        "score": None if score is None else float(score),
                        "verdict": verdict,
                        "justification": (
                            json.loads(justification)
                            if isinstance(justification, str)
                            else justification
                        ),
                        "cree_par": int(cree_par) if cree_par is not None else None,
                        "cree_le": cree_le.isoformat() if cree_le else None,
                    }
                )
    return {"evaluations": evaluations}


def population_par_modele(
    index: Any,  # noqa: ANN401
    boat_model_id: int,
    type_voile: str,
) -> dict[str, Any]:
    """Vue lecture seule d'une population complète (aucune candidate exclue)."""
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT nom_canonique FROM boat_models WHERE id_modele = %s",
                (int(boat_model_id),),
            )
            ligne = curseur.fetchone()
            population = _charger_population(
                curseur, boat_model_id, type_voile, exclure_id=None
            )
    dimensions: list[dict[str, Any]] = []
    for champ in DIMENSIONS_COMPAREES:
        valeurs = [
            float(getattr(fiche, champ))
            for fiche in population
            if getattr(fiche, champ) is not None
        ]
        if valeurs:
            dimensions.append({"champ": champ, **statistiques(valeurs)})
    return {
        "boat_model_id": int(boat_model_id),
        "modele_bateau": ligne[0] if ligne else None,
        "type_voile": normaliser_type_voile(type_voile),
        "n_echantillons": len(population),
        "seuil_min_echantillons": CONFIANCE_MIN_ECHANTILLONS,
        "dimensions": dimensions,
        "membres": [
            {
                "id_extraction": fiche.id_extraction,
                "code_fiche": fiche.code_fiche,
                "nom_fichier_source": fiche.nom_fichier_source,
                "statut_plausibilite": fiche.statut_plausibilite,
                "valeurs": {
                    champ: getattr(fiche, champ) for champ in DIMENSIONS_COMPAREES
                },
            }
            for fiche in population
        ],
    }
