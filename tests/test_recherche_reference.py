"""Lot 7 — chercher une fiche par sa RÉFÉRENCE.

Le constat de l'atelier : taper une référence de commande ne trouvait rien.
La raison est simple et vérifiable — la référence n'est PAS dans le texte
cherché. Le texte de recherche agrège le code, le titre, la gamme, le client,
le bateau, les matériaux… jamais ``fiche.commande``, qui est l'endroit où la
référence « ST… » est réellement lue. Chercher une référence revenait donc à
chercher un mot absent du document.

Ce module ajoute la reconnaissance de référence, en fonctions pures — la seule
partie qu'on puisse exécuter ici, sans base. Deux règles gouvernent :

* une référence se RECONNAÎT à sa forme (lettres collées à des chiffres), pas
  à un annuaire de préfixes : le jour où l'atelier change de série, rien n'est
  à réécrire ;
* une correspondance EXACTE sur une référence est un FAIT, pas un rang de
  pertinence. Elle passe devant les résultats approchants : quand on tape une
  référence, on ne veut pas « ce qui y ressemble », on veut la fiche.

Un faux positif (« Sun 2000 » ressemble à une référence) ne coûte qu'une
requête de plus qui ne renvoie rien, et ne déplace aucun résultat : c'est ce
qui permet de rester large dans la reconnaissance.
"""

from __future__ import annotations

from seamtech_search.recherche import cle_reference, extraire_reference

# ---------------------------------------------------------------------------
# Reconnaissance
# ---------------------------------------------------------------------------


def test_une_reference_lettres_chiffres_est_reconnue() -> None:
    assert extraire_reference("ST101") == "ST101"
    assert extraire_reference("ST545") == "ST545"
    assert extraire_reference("st545") == "ST545"


def test_les_separateurs_ne_font_pas_deux_references_differentes() -> None:
    """« ST-101 », « ST 101 » et « ST101 » sont la même commande."""
    assert extraire_reference("ST-101") == "ST101"
    assert extraire_reference("ST 101") == "ST101"


def test_la_reference_dans_une_requete_composee_est_retrouvee() -> None:
    assert extraire_reference("Lazy bag ST545") == "ST545"
    assert extraire_reference("grand voile ST-575 fini") == "ST575"


def test_une_reference_chiffres_lettres_est_reconnue() -> None:
    assert extraire_reference("7792-SO") == "7792SO"


def test_un_texte_sans_chiffre_n_est_pas_une_reference() -> None:
    assert extraire_reference("Grand voile") is None
    assert extraire_reference("Ocean") is None
    assert extraire_reference("") is None
    assert extraire_reference("   ") is None


def test_un_nombre_seul_n_est_pas_une_reference() -> None:
    """Une cote n'est pas une référence : 2026, 6,60 et 18 restent des nombres."""
    assert extraire_reference("2026") is None
    assert extraire_reference("6,60") is None
    assert extraire_reference("18") is None


def test_un_materiau_avec_un_chiffre_n_est_pas_une_reference() -> None:
    """« Monofilm K903 » est une matière, pas une commande : ne pas la confondre.

    Une seule lettre suivie de chiffres, c'est une désignation de tissu ; une
    référence d'atelier en porte au moins deux. La borne n'est pas arbitraire,
    elle sépare deux mondes qui se ressemblent.
    """
    assert extraire_reference("Dacron 220") is None
    assert extraire_reference("Monofilm K903") is None
    # Deux lettres, en revanche, c'est bien une commande.
    assert extraire_reference("ST92") == "ST92"


# ---------------------------------------------------------------------------
# Clé de comparaison
# ---------------------------------------------------------------------------


def test_la_cle_ignore_casse_et_separateurs() -> None:
    assert cle_reference("ST-101") == "ST101"
    assert cle_reference("st 101") == "ST101"
    assert cle_reference("  ST101  ") == "ST101"


def test_la_cle_d_une_valeur_vide_est_vide() -> None:
    assert cle_reference("") == ""
    assert cle_reference(None) == ""


def test_deux_graphies_d_une_meme_reference_partagent_la_cle() -> None:
    """C'est tout l'objet de la clé : « ST-101 » et « st101 » sont une commande."""
    assert cle_reference(extraire_reference("ST 101") or "") == cle_reference("ST-101")
