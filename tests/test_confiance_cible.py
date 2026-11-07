"""Lot 5 — évaluer une CIBLE, et répondre même quand la population est petite.

Le module de confiance sait déjà noter une candidate contre une population.
Mais il exige cinq échantillons, et le corpus réel de l'atelier n'a pas un
seul groupe de cinq : le plus grand en compte trois. Résultat, l'écran
répondait « Données insuffisantes (3/5) » et rien d'autre — une impasse,
alors que trois fiches suffisent amplement à MESURER des écarts.

Ce module ajoute donc ce qui manque, sans toucher à ce qui marche :

* les ÉCARTS MESURÉS sont calculés dès une seule fiche de référence : c'est
  de l'arithmétique, aucune condition d'effectif ;
* le SCORE STATISTIQUE reste soumis aux cinq échantillons, et on écrit
  COMBIEN il en manque — « il en manque 2 » se vérifie, « données
  insuffisantes » ne se vérifie pas ;
* une LECTURE en clair accompagne toujours le résultat : un verdict sans
  phrase qui le porte est un verdict qu'on interprète de travers.

Aucune base, aucun PDF : fonctions pures.
"""

from __future__ import annotations

from typing import Any

from seamtech_search.confiance_reutilisation import CONFIANCE_MIN_ECHANTILLONS, evaluer_cible
from seamtech_search.extractions_voile import ExtractionVoile

MODELE = 42


def _fiche(
    identifiant: int | None,
    *,
    slu: float = 10.0,
    sf: float = 3.5,
    spa: float = 25.0,
    type_voile: str = "Grand voile",
    gamme: str = "Ocean",
) -> ExtractionVoile:
    return ExtractionVoile(
        id_extraction=identifiant,
        cle_idempotence=f"cle-{identifiant}",
        code_fiche=f"F{identifiant}",
        nom_fichier_source=f"fiche-{identifiant}.pdf",
        gabarit_code="FICHE_PORTANT_V1",
        methode_principale="fiche_extraite",
        type_voile=type_voile,
        gamme=gamme,
        boat_model_id=MODELE,
        slu_m=slu,
        sf_m=sf,
        spa_m2=spa,
    )


def _cible(slu: float = 10.0, sf: float = 3.5, spa: float = 25.0) -> ExtractionVoile:
    return _fiche(None, slu=slu, sf=sf, spa=spa)


# ---------------------------------------------------------------------------
# Le cas réel de l'atelier : moins de cinq fiches
# ---------------------------------------------------------------------------


def test_avec_une_seule_fiche_les_ecarts_sont_deja_mesures() -> None:
    """Une fiche suffit à mesurer un écart : c'est une soustraction."""
    resultat = evaluer_cible(_cible(), [_fiche(1, slu=10.4)])
    assert resultat["ecarts"] is not None
    ligne = next(ligne for ligne in resultat["ecarts"]["blocs"]["dimensions"] if ligne["champ"] == "slu_m")
    assert ligne["valeurs"] == [10.0, 10.4]
    assert ligne["deltas_pct"] == [None, 4.0]


def test_avec_trois_fiches_le_score_est_absent_mais_les_ecarts_affiches() -> None:
    """Le cas de l'atelier : trois fiches, pas de pourcentage, mais des preuves."""
    population = [_fiche(1), _fiche(2, slu=10.1), _fiche(3, slu=9.9)]
    resultat = evaluer_cible(_cible(), population)
    assert resultat["n_population"] == 3
    assert resultat["confiance"]["score"] is None
    assert resultat["confiance"]["verdict"] == "donnees_insuffisantes"
    lignes = resultat["ecarts"]["blocs"]["dimensions"]
    assert any(ligne["nb_fiches_avec_valeur"] >= 3 for ligne in lignes)


def test_le_manque_est_chiffre_pas_sous_entendu() -> None:
    """« Il en manque 2 » se vérifie ; « données insuffisantes » ne se vérifie pas."""
    resultat = evaluer_cible(_cible(), [_fiche(1), _fiche(2), _fiche(3)])
    assert resultat["manquant_pour_score"] == CONFIANCE_MIN_ECHANTILLONS - 3
    assert "2" in resultat["lecture"]


def test_sans_aucune_fiche_aucun_ecart_n_est_invente() -> None:
    resultat = evaluer_cible(_cible(), [])
    assert resultat["ecarts"] is None
    assert resultat["n_population"] == 0
    assert resultat["manquant_pour_score"] == CONFIANCE_MIN_ECHANTILLONS
    assert resultat["confiance"]["verdict"] == "donnees_insuffisantes"


def test_la_lecture_dit_pourquoi_il_n_y_a_pas_de_pourcentage() -> None:
    resultat = evaluer_cible(_cible(), [_fiche(1)])
    assert "écart" in resultat["lecture"].lower()
    assert "score" in resultat["lecture"].lower()


# ---------------------------------------------------------------------------
# Dès cinq fiches, le score statistique s'ajoute — sans remplacer les écarts
# ---------------------------------------------------------------------------


def _population_coherente(taille: int = 6) -> list[ExtractionVoile]:
    return [_fiche(i, slu=10.0 + 0.01 * i, sf=3.5, spa=25.0) for i in range(1, taille + 1)]


def test_avec_six_fiches_le_score_est_calcule_et_les_ecarts_restent() -> None:
    resultat = evaluer_cible(_cible(slu=10.03), _population_coherente())
    assert resultat["n_population"] == 6
    assert resultat["manquant_pour_score"] == 0
    assert resultat["confiance"]["score"] is not None
    assert resultat["confiance"]["verdict"] in {"compatible", "a_examiner", "heterogene"}
    assert resultat["ecarts"] is not None


def test_une_cible_eloignee_sur_la_majorite_des_cotes_fait_baisser_le_score() -> None:
    """
    Une cible éloignée sur la plupart des cotes doit être moins bien notée
    qu'une cible au centre : si ce test échoue, le score ne mesure plus ce
    qu'il prétend mesurer.
    """
    population = _population_coherente()
    au_centre = evaluer_cible(_cible(slu=10.03, sf=3.5), population)["confiance"]["score"]
    tres_loin = evaluer_cible(_cible(slu=20.0, sf=7.0), population)["confiance"]["score"]
    assert au_centre is not None and tres_loin is not None
    assert tres_loin < au_centre


def test_une_seule_cote_hors_norme_ne_fait_pas_baisser_le_score_et_c_est_pourquoi_les_ecarts_restent() -> None:
    """
    Le score est une MÉDIANE de scores par dimension : une seule cote très
    éloignée ne le fait donc pas bouger. Ce n'est pas un défaut, c'est une
    protection contre une cote mal lue — mais c'est une raison de plus pour
    afficher les écarts mesurés à côté du score : un score de 100 n'a jamais
    voulu dire « tout est conforme ».
    """
    population = _population_coherente()
    resultat = evaluer_cible(_cible(slu=20.0, sf=3.5, spa=25.0), population)
    ligne = next(ligne for ligne in resultat["ecarts"]["blocs"]["dimensions"] if ligne["champ"] == "slu_m")
    # Le score reste élevé…
    assert (resultat["confiance"]["score"] or 0) >= 75
    # …et l'écart, lui, crève les yeux : c'est lui qu'il faut regarder.
    assert ligne["deltas_pct"] is not None
    assert abs(ligne["deltas_pct"][-1] or 0) > 20


def test_le_verdict_compatible_vient_avec_sa_justification() -> None:
    """Un score ne voyage jamais sans sa justification : la base l'exige aussi."""
    resultat = evaluer_cible(_cible(slu=10.03), _population_coherente())
    justification = resultat["confiance"]["justification"]
    assert justification["n_population"] == 6
    assert justification["dimensions"]
    assert justification["sources"]


# ---------------------------------------------------------------------------
# La cible est la référence : sans elle, aucun pourcentage n'a de sens
# ---------------------------------------------------------------------------


def test_la_cible_est_la_reference_des_ecarts() -> None:
    resultat = evaluer_cible(_cible(), [_fiche(1), _fiche(2)])
    assert resultat["ecarts"]["reference"]["rang"] == 0
    assert resultat["ecarts"]["colonnes"][0]["id_extraction"] is None


def test_la_cible_est_nommee_dans_la_sortie() -> None:
    resultat = evaluer_cible(_cible(), [_fiche(1)])
    assert resultat["cible"]["slu_m"] == 10.0
    assert resultat["cible"]["type_voile"] == "Grand voile"


def test_une_cible_sans_dimension_ne_fabrique_pas_d_ecart() -> None:
    cible = ExtractionVoile(
        id_extraction=None,
        cle_idempotence="vide",
        type_voile="Grand voile",
        boat_model_id=MODELE,
    )
    resultat = evaluer_cible(cible, [_fiche(1)])
    ligne = next(ligne for ligne in resultat["ecarts"]["blocs"]["dimensions"] if ligne["champ"] == "slu_m")
    assert ligne["valeurs"][0] is None
    assert ligne["deltas_pct"] == [None, None]


def test_le_resultat_porte_toujours_les_deux_volets() -> None:
    """Écarts mesurés ET cohérence statistique : jamais l'un sans la place de l'autre."""
    resultat: dict[str, Any] = evaluer_cible(_cible(), [_fiche(1)])
    assert set(resultat) >= {"cible", "n_population", "manquant_pour_score", "ecarts", "confiance", "lecture"}
