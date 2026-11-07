"""Lot 1 — forme canonique et clé de variante des axes de comparaison.

Le piège est connu et mesuré : la gamme se lit « medium Ocean » sur 73 fiches,
« Medium Ocean » sur 8, « Ocean » sur 8 et « medium » sur 3. Quatre écritures,
et selon qu'on regroupe ou non, on obtient un groupe de 92 fiches ou quatre
groupes qui s'ignorent. C'est faux silencieusement : aucun message d'erreur,
juste des comparaisons qui n'ont pas lieu.

Ce module pose donc les deux outils, et ils ne font pas la même chose :

* ``cle_variante`` — la clé de REGROUPEMENT. Insensible à la casse, aux accents
  et à la ponctuation : « Medium Ocean » et « medium Ocean » sont une seule
  entrée. Elle ne s'affiche jamais ;
* ``forme_canonique`` — la forme NETTOYÉE. Elle ne touche ni à la casse ni au
  sens : elle retire les espaces en double et la ponctuation pendante, qui sont
  du bruit d'extraction, pas de l'information.

La nuance est tout l'ouvrage. Nettoyer n'est pas fusionner : « Ocean » et
« medium Ocean » partagent peut-être un mot, ce n'est pas une raison de les
déclarer identiques. Le choix de la forme affichée revient à
``forme_majoritaire``, qui ne fait qu'ÉLIRE une graphie existante parmi celles
du groupe — elle n'en invente jamais une.
"""

from __future__ import annotations

from seamtech_search.fiches.normalisation import (
    cle_variante,
    forme_canonique,
    forme_majoritaire,
)

# ---------------------------------------------------------------------------
# Forme canonique : le bruit d'extraction disparaît, le sens reste
# ---------------------------------------------------------------------------


def test_les_espaces_en_double_et_les_marges_sont_retires() -> None:
    assert forme_canonique("  medium   Ocean  ") == "medium Ocean"
    assert forme_canonique("Grand voile") == "Grand voile"


def test_la_ponctuation_pendante_disparait() -> None:
    assert forme_canonique("Ocean,") == "Ocean"
    assert forme_canonique("Ocean ;") == "Ocean"
    assert forme_canonique("Ocean.") == "Ocean"


def test_la_casse_est_preservee_ce_n_est_pas_du_bruit() -> None:
    """« Medium Ocean » et « medium Ocean » : on nettoie, on ne décide pas."""
    assert forme_canonique("Medium Ocean") == "Medium Ocean"
    assert forme_canonique("medium Ocean") == "medium Ocean"


def test_les_accents_sont_composes_pas_supprimes() -> None:
    assert forme_canonique("Oceán") is not None
    assert forme_canonique("Génois") == "Génois"


def test_une_valeur_vide_ou_blanche_donne_rien() -> None:
    assert forme_canonique("") is None
    assert forme_canonique("   ") is None
    assert forme_canonique(None) is None


def test_les_separateurs_internes_sont_conserves() -> None:
    """Un tiret peut faire partie du nom (« Lazy-bag ») : ne pas le retirer."""
    assert forme_canonique("Lazy-bag 2P") == "Lazy-bag 2P"


# ---------------------------------------------------------------------------
# Clé de variante : regrouper ce qui ne diffère que par l'écriture
# ---------------------------------------------------------------------------


def test_la_casse_ne_fait_pas_deux_variantes() -> None:
    assert cle_variante("Medium Ocean") == cle_variante("medium Ocean")
    assert cle_variante("OCEAN") == cle_variante("ocean")


def test_les_accents_ne_font_pas_deux_variantes() -> None:
    assert cle_variante("Océan") == cle_variante("Ocean")
    assert cle_variante("Génois") == cle_variante("genois")


def test_la_ponctuation_ne_fait_pas_deux_variantes() -> None:
    assert cle_variante("Grand-voile") == cle_variante("grand voile")
    assert cle_variante("Ocean,") == cle_variante("Ocean")


def test_deux_gammes_differentes_restent_differentes() -> None:
    """Le cœur du contrat : nettoyer n'est pas fusionner."""
    assert cle_variante("Ocean") != cle_variante("Racing")
    assert cle_variante("medium Ocean") != cle_variante("Ocean")


def test_une_valeur_vide_donne_une_cle_vide() -> None:
    assert cle_variante("") == ""
    assert cle_variante(None) == ""


# ---------------------------------------------------------------------------
# Forme majoritaire : élire, jamais inventer
# ---------------------------------------------------------------------------


def test_la_graphie_la_plus_frequente_est_elue() -> None:
    assert forme_majoritaire({"medium Ocean": 73, "Medium Ocean": 8}) == "medium Ocean"


def test_a_effectif_egal_la_plus_courte_puis_l_alphabet() -> None:
    """Un choix stable et écrit : jamais d'arbitraire qui change d'une exécution à l'autre."""
    assert forme_majoritaire({"Ocean": 5, "medium": 5}) == "Ocean"
    assert forme_majoritaire({"ZZ": 3, "AA": 3}) == "AA"


def test_un_groupe_vide_n_elit_rien() -> None:
    assert forme_majoritaire({}) is None


def test_la_forme_elue_existe_toujours_dans_le_groupe() -> None:
    """La règle la plus importante : on élit une graphie, on n'en écrit pas une nouvelle."""
    formes = {"medium Ocean": 2, "Medium Ocean": 1, "medium  ocean": 1}
    elue = forme_majoritaire(formes)
    assert elue in formes
