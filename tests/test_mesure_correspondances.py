"""Lot 1.1 — l'instrument de mesure de la table de correspondance.

Ce fichier prouve que l'instrument est juste AVANT qu'on s'en serve sur le
corpus : la table proposée, l'écriture retenue, le statut des fusions
probables, et le quatrième axe (catégorie) ajouté à la boîte à outils.

Aucune donnée d'atelier : uniquement des libellés génériques de voilerie.
Aucune base requise.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.mesurer_correspondances import AXES, formater, lire_axe, proposer_table
from seamtech_search.qualite.cartographie import variantes_categorie

# ---------------------------------------------------------------------------
# Fusions CERTAINES — une même valeur écrite de plusieurs façons
# ---------------------------------------------------------------------------


def test_ecritures_multiples_proposent_une_fusion() -> None:
    """Cas qui ÉCHOUE : « Medium Ocean » s'écrit de trois façons."""
    lignes = [("Medium Ocean", 40), ("medium ocean", 7), ("MEDIUM OCEAN", 3)]
    table = proposer_table(lignes)
    assert len(table["certaines"]) == 1
    groupe = table["certaines"][0]
    assert groupe["ecriture_retenue"] == "Medium Ocean"
    assert set(groupe["ecritures_a_remplacer"]) == {"medium ocean", "MEDIUM OCEAN"}
    assert groupe["nb_fiches"] == 50


def test_ecriture_unique_ne_propose_rien() -> None:
    """Cas qui PASSE : une seule écriture, rien à fusionner."""
    table = proposer_table([("Ocean", 40), ("Cruising", 12)])
    assert table["certaines"] == []


def test_l_ecriture_retenue_est_la_plus_frequente() -> None:
    """Choix mécanique : la forme la plus représentée l'emporte."""
    table = proposer_table([("ocean", 2), ("Ocean", 25)])
    assert table["certaines"][0]["ecriture_retenue"] == "Ocean"


def test_les_accents_et_la_ponctuation_ne_font_pas_deux_notions() -> None:
    """« semi-lattée » et « semi lattée » sont la même chose."""
    table = proposer_table([("semi-lattée", 9), ("semi lattée", 4)])
    assert table["certaines"][0]["ecriture_retenue"] == "semi-lattée"


# ---------------------------------------------------------------------------
# Fusions PROBABLES — à trancher par l'exploitante, jamais par la machine
# ---------------------------------------------------------------------------


def test_une_valeur_engrainee_est_signalee_pas_fusionnee() -> None:
    """« ocean » dans « medium ocean » : même gamme ou deux gammes ? On ne devine pas."""
    table = proposer_table([("medium ocean", 40), ("ocean", 9)])
    assert len(table["probables"]) == 1
    candidat = table["probables"][0]
    assert candidat["valeur_courte"] == "ocean"
    assert candidat["valeur_longue"] == "medium ocean"
    assert candidat["nb_court"] == 9 and candidat["nb_long"] == 40


def test_une_fusion_probable_n_est_jamais_confirmee_par_la_machine() -> None:
    """`confirmee_par` reste None : la décision appartient à l'exploitante."""
    table = proposer_table([("medium ocean", 40), ("ocean", 9)])
    assert table["probables"][0]["confirmee_par"] is None
    assert "exploitante" in table["probables"][0]["decision"]


def test_une_fusion_certaine_reste_a_valider() -> None:
    """Même certaine, la fusion est soumise : rien n'est appliqué en silence."""
    table = proposer_table([("Ocean", 25), ("ocean", 2)])
    assert table["certaines"][0]["decision"] == "a_valider"


# ---------------------------------------------------------------------------
# Comptes et robustesse
# ---------------------------------------------------------------------------


def test_les_comptes_sont_exacts() -> None:
    table = proposer_table([("Ocean", 25), ("ocean", 2), ("Cruising", 3)])
    assert table["nb_fiches_mesurees"] == 30
    assert table["nb_ecritures_distinctes"] == 3
    assert table["nb_valeurs_distinctes"] == 2


def test_les_valeurs_vides_ne_sont_pas_des_variantes() -> None:
    """Une donnée manquante n'est pas une variante : elle n'entre pas dans la table."""
    table = proposer_table([("Ocean", 5), ("", 12), (None, 7), ("   ", 3)])
    assert table["nb_fiches_mesurees"] == 5
    assert table["nb_valeurs_distinctes"] == 1
    assert table["certaines"] == []


def test_corpus_vide_ne_fabrique_rien() -> None:
    """Sur un axe non renseigné, la table est vide — pas inventée."""
    table = proposer_table([])
    assert table == {
        "nb_valeurs_distinctes": 0,
        "nb_ecritures_distinctes": 0,
        "nb_fiches_mesurees": 0,
        "certaines": [],
        "probables": [],
    }


def test_les_quatre_axes_sont_couverts() -> None:
    assert set(AXES) == {"gamme", "matiere", "type_voile", "categorie"}


def test_un_axe_inconnu_est_refuse() -> None:
    """Plutôt une erreur nette qu'une mesure silencieuse sur le mauvais axe."""
    with pytest.raises(ValueError, match="Axe inconnu"):
        lire_axe(object(), "invention")


def test_la_sortie_est_lisible_par_un_humain() -> None:
    """C'est cette sortie qui est validée ligne à ligne : elle doit se lire."""
    rapport = {"gamme": proposer_table([("Ocean", 25), ("ocean", 2), ("medium ocean", 40)])}
    texte = formater(rapport)
    assert "GAMME" in texte
    assert "écriture retenue" in texte
    assert "PROPOSITION" in texte


# ---------------------------------------------------------------------------
# Le quatrième axe : la catégorie, ajouté à la boîte à outils existante
# ---------------------------------------------------------------------------


class _CurseurFactice:
    """Curseur minimal : rend ce que la requête demande, sans base."""

    def __init__(self, valeurs: list[tuple[Any, ...]], sources: list[tuple[Any, ...]]) -> None:
        self._valeurs = valeurs
        self._sources = sources

    def execute(self, sql: str, params: Any = None) -> None:
        self._lignes = self._sources if "categorie_source" in sql else self._valeurs

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._lignes


def test_la_categorie_est_mesuree_avec_sa_source() -> None:
    """Lot 1.1 : la catégorie est le seul axe déjà fermé — mesuré, pas supposé.

    `fiche.categorie` porte une contrainte CHECK : on attend donc ZÉRO variante
    d'écriture. La fonction le constate au lieu de le décréter, et rapporte la
    source, qui est la vraie question sur cet axe.
    """
    curseur = _CurseurFactice(
        valeurs=[("voile", 300), ("accessoire", 38)],
        sources=[("document", 200), ("dossier", 138)],
    )
    rapport = variantes_categorie(curseur)
    assert rapport["nb_valeurs_distinctes"] == 2
    assert rapport["certaines"] == []  # aucune variante : contrainte CHECK
    assert rapport["probables"] == []  # ni l'une ni l'autre n'est engrainée
    assert rapport["sources"] == [{"source": "document", "nb": 200}, {"source": "dossier", "nb": 138}]


def test_une_categorie_hors_contrainte_serait_vue() -> None:
    """Si la contrainte sautait un jour, la mesure le verrait au lieu de le couvrir."""
    curseur = _CurseurFactice(valeurs=[("Voile", 10), ("voile", 4)], sources=[])
    rapport = variantes_categorie(curseur)
    assert len(rapport["certaines"]) == 1
    assert rapport["certaines"][0]["nb_formes"] == 2
