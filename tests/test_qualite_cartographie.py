"""Cartographie du corpus — Lot 0 du plan de comparaison.

Pourquoi ce fichier existe.

Avant d'écrire une seule règle d'extraction ou une seule ligne de comparaison,
il faut savoir à quoi ressemble réellement le corpus : combien de fiches sont
comparables entre elles, combien de formes différentes prennent les mêmes
valeurs, combien de fiches passeraient une validation automatique. Ces
questions sont mesurables, et leurs réponses commandent tout le reste du plan —
notamment le fait que le seuil statistique de 5 échantillons n'est presque
jamais atteint.

Deux principes tenus dans ce module :

* **La logique est séparée du SQL.** Les fonctions pures sont testées sans base
  et donc réellement rejouées à chaque exécution ; les fonctions qui parlent à
  PostgreSQL sont minces et marquées ``postgres``, comme le reste du tableau de
  bord qualité. Aucune agrégation n'est faite en Python quand GROUP BY suffit.
* **On ne fusionne pas ce qu'on n'a pas mesuré.** Une différence de casse ou
  d'accent est une variante *certaine* ; le fait qu'une valeur soit contenue
  dans une autre n'est qu'une variante *probable*, signalée à l'exploitante et
  jamais fusionnée seule. C'est la règle d'honnêteté du lot 0.

Aucune donnée nominative ne sort de ces fonctions : uniquement des comptes,
des distributions et des clés normalisées génériques.
"""

from __future__ import annotations

import pytest

from seamtech_search.qualite.cartographie import (
    SEUIL_STATISTIQUE,
    cle_normalisation,
    completude_champs,
    distribution_scores,
    histogramme_groupes,
    regrouper_variantes,
    variantes_probables,
)

# ---------------------------------------------------------------------------
# Fonctions pures — exécutées partout, aucun serveur requis
# ---------------------------------------------------------------------------


def test_seuil_statistique_aligne_sur_le_module_de_confiance():
    """Le seuil de la cartographie est CELUI du module de confiance, pas un autre.

    Deux seuils différents pour la même promesse d'honnêteté produiraient deux
    réponses différentes à la même question. Le test verrouille l'alignement.
    """
    from seamtech_search.confiance_reutilisation import CONFIANCE_MIN_ECHANTILLONS

    assert SEUIL_STATISTIQUE == CONFIANCE_MIN_ECHANTILLONS


def test_cle_normalisation_ignore_casse_espaces_et_accents():
    """Trois écritures d'une même gamme doivent donner une seule clé."""
    assert cle_normalisation("medium Ocean") == cle_normalisation("Medium Ocean")
    assert cle_normalisation("  medium   Ocean ") == cle_normalisation("medium Ocean")
    assert cle_normalisation("semi-lattée") == cle_normalisation("SEMI LATTEE")


def test_cle_normalisation_ne_fusionne_pas_des_valeurs_distinctes():
    """Le regroupement ne doit pas écraser une distinction qui a du sens."""
    assert cle_normalisation("Ocean") != cle_normalisation("Prestige")
    assert cle_normalisation("full-batten") != cle_normalisation("semi-lattée")


def test_histogramme_groupes_compte_les_groupes_et_les_fiches():
    """Tailles 3, 3, 2, 1 → 4 groupes, 9 fiches, aucun groupe n'atteint 5."""
    resultat = histogramme_groupes([3, 3, 2, 1])
    assert resultat["nb_groupes"] == 4
    assert resultat["nb_fiches_couvertes"] == 9
    assert resultat["distribution"]["1"] == 1
    assert resultat["distribution"]["2"] == 1
    assert resultat["distribution"]["3"] == 2
    assert resultat["distribution"]["4"] == 0
    assert resultat["groupes_statistiques"] == 0


def test_histogramme_groupes_mesure_la_part_couverte_par_la_statistique():
    """Seuls les groupes d'au moins 5 fiches peuvent porter un score statistique."""
    # Un groupe de 6 et deux groupes de 2 : 6 fiches sur 10 sont couvertes.
    resultat = histogramme_groupes([6, 2, 2])
    assert resultat["nb_fiches_couvertes"] == 10
    assert resultat["fiches_en_groupe_statistique"] == 6
    assert resultat["part_couverte_statistique"] == pytest.approx(0.6)
    assert resultat["groupes_statistiques"] == 1


def test_histogramme_groupes_sur_corpus_vide_ne_divise_jamais_par_zero():
    """Un corpus vide produit des zéros et des « None », jamais une exception."""
    resultat = histogramme_groupes([])
    assert resultat["nb_groupes"] == 0
    assert resultat["nb_fiches_couvertes"] == 0
    assert resultat["part_couverte_statistique"] is None


def test_histogramme_groupes_classe_les_gros_groupes_dans_une_tranche_ouverte():
    """Au-delà de 9, les tailles sont regroupées : le rapport reste lisible."""
    resultat = histogramme_groupes([12, 30, 2])
    assert resultat["distribution"]["10+"] == 2
    assert resultat["nb_fiches_couvertes"] == 44


def test_regrouper_variantes_ne_garde_que_les_ecritures_multiples():
    """Une valeur écrite d'une seule façon n'intéresse personne : on la retire."""
    lignes = [("medium Ocean", 73), ("Medium Ocean", 8), ("Prestige", 6)]
    groupes = regrouper_variantes(lignes)
    assert [groupe["cle"] for groupe in groupes] == ["medium ocean"]
    assert groupes[0]["nb_formes"] == 2
    assert groupes[0]["total"] == 81


def test_regrouper_variantes_trie_par_impact_decroissant():
    """Les variantes les plus fréquentes d'abord : ce sont celles à corriger."""
    lignes = [("Ocean", 2), ("ocean", 2), ("medium Ocean", 100), ("Medium Ocean", 100)]
    groupes = regrouper_variantes(lignes)
    assert groupes[0]["total"] > groupes[1]["total"]


def test_regrouper_variantes_ignore_les_valeurs_vides():
    """Une valeur vide n'est pas une variante : c'est une donnée manquante."""
    assert regrouper_variantes([("", 12), ("   ", 3), (None, 5)]) == []


def test_regrouper_variantes_sur_aucune_ligne():
    assert regrouper_variantes([]) == []


def test_variantes_probables_signale_sans_jamais_fusionner():
    """« Ocean » dans « medium Ocean » : signalé comme candidat, pas fusionné.

    C'est la différence entre une variante certaine (casse, accent) et une
    variante probable (une valeur contenue dans une autre). Seule l'exploitante
    peut trancher la seconde : le système la lui présente avec les comptes.
    """
    lignes = [("medium Ocean", 73), ("Medium Ocean", 8), ("Ocean", 8)]
    probables = variantes_probables(lignes)
    assert len(probables) == 1
    candidat = probables[0]
    assert candidat["cle_courte"] == "ocean"
    assert candidat["cle_longue"] == "medium ocean"
    assert candidat["nb_court"] == 8
    assert candidat["nb_long"] == 81
    # Le signal explicite : personne n'a confirmé cette fusion.
    assert candidat["confirmee_par"] is None


def test_variantes_probables_ignore_les_valeurs_identiques():
    """Deux écritures d'une même clé ne sont pas une « variante probable »."""
    assert variantes_probables([("Ocean", 5), ("ocean", 5)]) == []


def test_variantes_probables_ne_propose_pas_de_fusion_absurde():
    """Une clé d'une seule lettre ne doit pas être proposée comme variante."""
    lignes = [("Ocean", 40), ("O", 3)]
    assert variantes_probables(lignes) == []


def test_distribution_scores_dit_combien_passeraient_la_validation_auto():
    """La question du lot 3 : à 0,90, combien de fiches seraient validées d'office ?"""
    scores = [0.95, 0.92, 0.90, 0.899, 0.80, 0.40]
    resultat = distribution_scores(scores, seuils=(0.9,))
    palier = resultat["paliers"][0]
    assert palier["seuil"] == 0.9
    # 0,95 / 0,92 / 0,90 : le seuil est inclusif, 0,899 est dessous.
    assert palier["nb_fiches"] == 3
    assert palier["part"] == pytest.approx(0.5)


def test_distribution_scores_ecarte_les_scores_inconnus():
    """Une fiche sans score n'est ni au-dessus ni en dessous : elle est ignorée."""
    resultat = distribution_scores([0.95, None, None], seuils=(0.9,))
    assert resultat["nb_scores_connus"] == 1
    assert resultat["nb_scores_inconnus"] == 2
    assert resultat["paliers"][0]["nb_fiches"] == 1


def test_distribution_scores_sur_aucune_fiche():
    resultat = distribution_scores([], seuils=(0.9,))
    assert resultat["nb_scores_connus"] == 0
    assert resultat["paliers"][0]["nb_fiches"] == 0
    assert resultat["paliers"][0]["part"] is None


def test_distribution_scores_refuse_un_seuil_hors_bornes():
    """Un seuil hors [0,1] est une erreur de conception, pas une valeur à tolérer."""
    with pytest.raises(ValueError):
        distribution_scores([0.5], seuils=(1.5,))


def test_completude_champs_classe_les_champs_les_moins_renseignes_en_premier():
    """Le champ le plus souvent absent est celui qu'il faut réparer d'abord."""
    lignes = [("slu_m", 300, 338), ("tetiere_m", 100, 338), ("spa_m2", 330, 338)]
    resultat = completude_champs(lignes)
    assert resultat[0]["champ"] == "tetiere_m"
    assert resultat[0]["taux_pct"] == pytest.approx(29.59, abs=0.01)
    assert resultat[-1]["champ"] == "spa_m2"


def test_completude_champs_ne_divise_jamais_par_zero():
    resultat = completude_champs([("slu_m", 0, 0)])
    assert resultat[0]["nb_renseignes"] == 0
    assert resultat[0]["taux"] is None


# ---------------------------------------------------------------------------
# Intégration SQL — exécutée sur PostgreSQL (intégration continue)
# ---------------------------------------------------------------------------


@pytest.mark.postgres
def test_cartographie_complete(base_recherche):
    """La cartographie s'assemble sans exception et porte ses définitions."""
    from seamtech_search.qualite.cartographie import cartographie

    index = base_recherche["index"]
    rapport = cartographie(index)
    for cle in (
        "groupes_comparables",
        "variantes_gamme",
        "variantes_type_voile",
        "variantes_matiere",
        "distribution_scores",
        "completude_dimensions",
        "atteignabilite_recherche",
    ):
        assert cle in rapport, f"indicateur manquant : {cle}"
        assert "definition" in rapport[cle]
        assert "unite" in rapport[cle]
        assert "periode" in rapport[cle]


@pytest.mark.postgres
def test_atteignabilite_recherche_compare_fiches_et_fiches_rattachees(base_recherche):
    """Diagnostic « 100 fiches sur 338 » : combien sont rattachées à une extraction ?"""
    from seamtech_search.qualite.cartographie import atteignabilite_recherche

    index = base_recherche["index"]
    with index.connect() as connexion:
        with connexion.cursor() as cur:
            resultat = atteignabilite_recherche(cur)
    assert resultat["nb_fiches"] >= 0
    assert resultat["nb_fiches_rattachees"] <= resultat["nb_fiches"]
    # L'écart est le nombre à expliquer ; il n'est jamais négatif.
    assert resultat["nb_fiches_non_rattachees"] >= 0


@pytest.mark.postgres
def test_colonne_de_variantes_refuse_une_colonne_arbitraire(base_recherche):
    """Une colonne non autorisée est refusée : aucune injection SQL possible."""
    from seamtech_search.qualite.cartographie import variantes_valeurs

    index = base_recherche["index"]
    with index.connect() as connexion:
        with connexion.cursor() as cur:
            with pytest.raises(ValueError):
                variantes_valeurs(cur, "code_fiche; DROP TABLE fiche--")
