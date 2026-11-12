"""Lot 1.1 — l'instrument de mesure de la table de correspondance.

Ce fichier prouve que l'instrument est juste AVANT qu'on s'en serve sur le
corpus : la table proposée, l'écriture retenue, le statut des fusions
probables, et le quatrième axe (catégorie) ajouté à la boîte à outils.

Aucune donnée d'atelier : uniquement des libellés génériques de voilerie.
Aucune base requise.
"""

from __future__ import annotations

from typing import Any

from pathlib import Path

import pytest

from scripts.etape0_corpus import charger_decisions
from scripts.mesurer_correspondances import AXES, formater, lire_axe, proposer_table
from seamtech_search.qualite.cartographie import variantes_categorie


def _decisions(*fusions: dict[str, Any]) -> dict[str, Any]:
    """Un jeu d'arbitrages prêt à l'emploi, sans toucher au fichier du dépôt."""
    # Un chemin inexistant rend un jeu vide — comportement déjà établi et
    # testé de `charger_decisions`, donc pas de doublon de construction.
    return charger_decisions(Path("/tmp/inexistant-lot19/decisions.json")) | {"fusions": list(fusions)}


# ---------------------------------------------------------------------------
# Les arbitrages DÉJÀ RENDUS ne se redemandent pas
# ---------------------------------------------------------------------------


def test_un_refus_deja_rendu_nest_plus_repropose() -> None:
    """RÉGRESSION : l'exploitante était invitée à re-trancher ce qu'elle a tranché.

    `decisions.json` porte `ne_pas_fusionner` sur « enrouleur » /
    « enrouleur <nom propre> » depuis le 2026-11-09. Malgré cela, `mesurer`
    continuait de proposer la paire comme « à trancher par l'exploitante » :
    le pipeline appelle `proposer_table` sans jamais consulter les décisions
    (`charger_decisions` n'était appelé que par `decisions` et `decider`).

    « Que rien n'attende deux fois » vaut aussi pour une décision humaine.
    """
    dec = _decisions(
        {
            "axe": "gamme",
            "valeur_a": "enrouleur",
            "valeur_b": "enrouleur <nom propre>",
            "decision": "ne_pas_fusionner",
            "motif": "Deux gammes distinctes : la valeur courte est une gamme, "
            "la longue une gamme précisée par son support.",
            "decideur": "exploitante",
            "graphie_retenue": None,
        }
    )
    lignes = [("enrouleur", 3), ("enrouleur <nom propre>", 5)]

    sans = proposer_table(lignes, axe="gamme")
    assert len(sans["probables"]) == 1, "sans décision, la paire est bien proposée"

    avec = proposer_table(lignes, axe="gamme", decisions=dec)
    assert avec["probables"] == [], (
        "RÉGRESSION : une fusion REFUSÉE par l'exploitante est encore proposée. "
        "On lui redemande un arbitrage qu'elle a déjà rendu."
    )
    # Et le refus reste TRACÉ : il ne disparaît pas en silence.
    assert any(e["valeur_courte"] == "enrouleur" for e in avec["ecartees"])


def test_un_arbitrage_fusionner_est_applique_et_non_repropose() -> None:
    """Une décision « fusionner » s'APPLIQUE : elle ne se redemande pas non plus.

    `decisions.json` porte `fusionner` sur « enrouleur » / « enrouleur surface »
    avec graphie retenue « enrouleur surface ». La paire doit donc sortir de la
    zone « à trancher » et devenir une fusion certaine, avec l'écriture imposée
    par l'humain — et non l'écriture la plus fréquente choisie par la machine.
    """
    dec = _decisions(
        {
            "axe": "gamme",
            "valeur_a": "enrouleur",
            "valeur_b": "enrouleur surface",
            "decision": "fusionner",
            "motif": "Variante d'écriture d'une même gamme.",
            "decideur": "exploitante",
            "graphie_retenue": "enrouleur surface",
        }
    )
    # « enrouleur » est le PLUS FRÉQUENT : la machine choisirait donc « enrouleur ».
    lignes = [("enrouleur", 30), ("enrouleur surface", 2)]

    table = proposer_table(lignes, axe="gamme", decisions=dec)
    assert table["probables"] == [], "un arbitrage rendu ne se redemande pas"
    imposees = [c for c in table["certaines"] if c.get("source") == "arbitrage"]
    assert len(imposees) == 1
    # L'écriture imposée par l'humain l'emporte sur la plus fréquente.
    assert imposees[0]["ecriture_retenue"] == "enrouleur surface"
    assert imposees[0]["ecritures_a_remplacer"] == ["enrouleur"]


def test_sans_arbitrage_rien_ne_change() -> None:
    """Le défaut de comportement doit rester identique là où rien n'a été tranché."""
    lignes = [("enrouleur", 3), ("enrouleur <nom propre>", 5)]
    vide = proposer_table(lignes, axe="gamme")
    assert len(vide["probables"]) == 1
    assert vide["ecartees"] == []


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
        # Une paire déjà tranchée n'est plus reproposée : la clé fait partie
        # du contrat depuis le Lot 19.
        "ecartees": [],
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
