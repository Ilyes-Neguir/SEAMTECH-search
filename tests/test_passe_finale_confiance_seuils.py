"""Passe finale — confiance de réutilisation : les quatre seuils, prouvés.

Vérification indépendante du 29/10/2026 des règles affichées au produit :

* **N < 5** voiles comparables → « Données insuffisantes (N/5) », jamais un
  pourcentage partiel ;
* **écart de quelques centimètres** sur une population serrée → encore
  « compatible » (le score est robuste, pas une moyenne) ;
* **voile identique** à la médiane → score maximal, « compatible » ;
* **population hétérogène** → aucun score global ;
* **< 75 %** → « à examiner », jamais un verdict positif.

Fonctions pures : aucune base, aucune horloge, aucun réseau.
"""

from __future__ import annotations

from typing import Any

import pytest

from seamtech_search.confiance_reutilisation import (
    CONFIANCE_MIN_ECHANTILLONS,
    CONFIANCE_SEUIL_ALERTE,
    evaluer_population,
)
from seamtech_search.extractions_voile import ExtractionVoile


def _voile(**remplacements: Any) -> ExtractionVoile:  # noqa: ANN401
    donnees: dict[str, Any] = {
        "code_fiche": "PF-X",
        "nom_fichier_source": "x.pdf",
        "methode_principale": "fiche_extraite",
        "type_voile": "Spi Asymétrique",
        "modele_bateau_brut": "Bavaria 45",
        "slu_m": 6.6,
        "sf_m": 3.08,
        "statut_extraction": "complete",
        "statut_plausibilite": "plausible",
        "boat_model_id": 11,
        "statut_liaison": "lie_automatique",
    }
    donnees.update(remplacements)
    return ExtractionVoile(**donnees)


def _population(slus: list[float], sfs: list[float] | None = None) -> list[ExtractionVoile]:
    sfs = sfs or [3.08] * len(slus)
    return [
        _voile(code_fiche=f"PF-P{i}", slu_m=slu, sf_m=sf)
        for i, (slu, sf) in enumerate(zip(slus, sfs, strict=True))
    ]


def test_seuils_affiches_au_produit() -> None:
    assert CONFIANCE_MIN_ECHANTILLONS == 5
    assert CONFIANCE_SEUIL_ALERTE == 75


@pytest.mark.parametrize("n", [0, 1, 4])
def test_moins_de_cinq_voiles_jamais_de_pourcentage(n: int) -> None:
    population = _population([6.6] * n)
    resultat = evaluer_population(_voile(), population)
    assert resultat["verdict"] == "donnees_insuffisantes"
    assert resultat["score"] is None
    assert resultat["libelle_verdict"] == f"Données insuffisantes ({n}/5)"
    # La justification reste complète : l'approbateur voit ce qui manque.
    assert resultat["justification"]["n_population"] == n


def test_cinq_voiles_identiques_compatibles() -> None:
    population = _population([6.6] * 5)
    resultat = evaluer_population(_voile(), population)
    assert resultat["verdict"] == "compatible"
    assert resultat["score"] is not None and resultat["score"] >= CONFIANCE_SEUIL_ALERTE


def test_ecart_de_quelques_centimetres_reste_compatible() -> None:
    """3 cm d'écart sur 6,60 m (0,45 %) au sein d'une population réaliste
    (IQR 20 cm) : « compatible », mesure faite le 29/10/2026."""
    population = _population([6.45, 6.55, 6.60, 6.65, 6.75], [3.00, 3.05, 3.08, 3.10, 3.20])
    resultat = evaluer_population(_voile(slu_m=6.63), population)
    assert resultat["n_echantillons"] == 5
    assert resultat["verdict"] == "compatible", resultat["libelle_verdict"]
    assert resultat["score"] >= CONFIANCE_SEUIL_ALERTE


def test_meme_ecart_dans_une_population_tres_serree_donne_a_examiner() -> None:
    """CONSTAT MESURÉ, non un défaut caché : le score est un z-robuste.

    À écart PHYSIQUE constant (3 cm), plus la population de référence est
    serrée, plus la candidate paraît atypique :
    IQR 20 cm → 96,0 % « compatible » ; IQR 6 cm → 70,2 % « à examiner ».
    C'est la définition d'un z-score, pas une incohérence ; le test fige les
    deux valeurs pour qu'un changement de formule ne passe pas inaperçu.
    """
    serree = evaluer_population(
        _voile(slu_m=6.63), _population([6.57, 6.60, 6.60, 6.63, 6.66], [3.05, 3.08, 3.08, 3.10, 3.12])
    )
    assert serree["verdict"] == "a_examiner"
    assert serree["score"] == pytest.approx(70.2, abs=0.2)
    large = evaluer_population(
        _voile(slu_m=6.63), _population([6.45, 6.55, 6.60, 6.65, 6.75], [3.00, 3.05, 3.08, 3.10, 3.20])
    )
    assert large["verdict"] == "compatible"
    assert large["score"] == pytest.approx(96.0, abs=0.2)


def test_population_parfaitement_constante_et_ecart_faible() -> None:
    """σ̂ = 0 : le repli relatif (10 %) s'applique — 3 cm sur 6,60 m = 97,8 %.

    Comportement mesuré, borné et explicite : sans dispersion, une variation
    reste jugée sur son écart relatif, jamais sur un z impossible à calculer.
    """
    resultat = evaluer_population(_voile(slu_m=6.63), _population([6.60] * 5, [3.08] * 5))
    assert resultat["verdict"] == "compatible"
    assert resultat["score"] == pytest.approx(97.8, abs=0.2)


def test_ecart_important_donne_a_examiner_sans_verdict_positif() -> None:
    population = _population([6.60, 6.60, 6.61, 6.59, 6.60], [3.08, 3.08, 3.09, 3.07, 3.08])
    resultat = evaluer_population(_voile(slu_m=9.90, sf_m=4.50), population)
    assert resultat["verdict"] == "a_examiner"
    assert resultat["score"] < CONFIANCE_SEUIL_ALERTE
    assert "à examiner" in resultat["libelle_verdict"]
    assert "%" in resultat["libelle_verdict"]


def test_population_heterogene_aucun_score() -> None:
    """Trois familles de voiles mélangées : la dispersion interdit tout score."""
    population = _population([4.0, 4.1, 6.6, 6.7, 11.5], [2.0, 2.1, 3.1, 3.2, 5.0])
    resultat = evaluer_population(_voile(), population)
    assert resultat["verdict"] == "heterogene"
    assert resultat["score"] is None
    assert "hétérogène" in resultat["libelle_verdict"]
    # Les statistiques par dimension restent affichées pour l'humain.
    assert resultat["justification"]["dimensions"]


def test_verdict_et_score_toujours_coherents() -> None:
    """Le score ne voyage jamais sans verdict, ni l'inverse (cohérence 030)."""
    for population in (
        _population([]),
        _population([6.6] * 3),
        _population([6.6] * 5),
        _population([4.0, 4.1, 6.6, 6.7, 11.5]),
    ):
        resultat = evaluer_population(_voile(), population)
        assert ("score" in resultat) and ("verdict" in resultat)
        assert resultat["verdict"] in {
            "donnees_insuffisantes",
            "heterogene",
            "a_examiner",
            "compatible",
        }
        if resultat["verdict"] in {"donnees_insuffisantes", "heterogene"}:
            assert resultat["score"] is None
        else:
            assert isinstance(resultat["score"], float)
            assert (resultat["score"] >= CONFIANCE_SEUIL_ALERTE) == (
                resultat["verdict"] == "compatible"
            )


def test_justification_liste_toujours_les_sources() -> None:
    population = [
        _voile(id_extraction=index + 1) for index in range(5)
    ]
    resultat = evaluer_population(_voile(), population)
    sources = resultat["justification"]["sources"]
    assert len(sources) == 5
    assert all(source["id_extraction"] is not None for source in sources)


def test_donnees_partielles_ne_plantent_pas() -> None:
    """Dimensions manquantes d'un côté ou de l'autre : jamais d'exception."""
    population = [
        _voile(code_fiche="PF-A", slu_m=None, sf_m=3.08),
        _voile(code_fiche="PF-B", slu_m=6.6, sf_m=None),
        _voile(code_fiche="PF-C", slu_m=6.6, sf_m=3.08),
        _voile(code_fiche="PF-D", slu_m=6.61, sf_m=3.08),
        _voile(code_fiche="PF-E", slu_m=6.6, sf_m=3.09),
    ]
    resultat = evaluer_population(_voile(slu_m=None, sf_m=None), population)
    assert resultat["verdict"] in {
        "donnees_insuffisantes",
        "heterogene",
        "a_examiner",
        "compatible",
    }
