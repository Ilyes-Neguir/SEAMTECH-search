"""Normalisation GÉNÉRALE de la clé de recherche — sans PostgreSQL.

Couvre casse Unicode, diacritiques, NFC/NFD, ligatures œ/æ/ß, espaces.
Aucun cas particulier « génois » : plusieurs familles de termes.
Les textes d'origine ne sont pas mutés (la fonction renvoie une clé).
"""

from __future__ import annotations

import unicodedata

import pytest

from seamtech_search.fiches.normalisation import normaliser_cle_recherche, sans_accents

FAMILLES = (
    ("génois", "genois", "GÉNOIS", "GeNoIs"),
    ("réparation", "reparation", "RÉPARATION", "Reparation"),
    ("été", "ete", "Été", "ETE"),
    ("façade", "facade", "FAÇADE", "Façade"),
    ("Noël", "noel", "NOEL", "NOËL"),
    ("câble", "cable", "CÂBLE", "Cable"),
    ("Müller", "muller", "MULLER", "müller"),
    ("São", "sao", "SAO", "são"),
)


@pytest.mark.parametrize("variantes", FAMILLES)
def test_variantes_casse_et_accents_meme_cle(variantes: tuple[str, ...]) -> None:
    cles = {normaliser_cle_recherche(v) for v in variantes}
    assert len(cles) == 1, f"{variantes!r} → {cles!r}"
    assert cles.pop() == normaliser_cle_recherche(variantes[1])


def test_nfc_et_nfd_sont_equivalents() -> None:
    nfc = "é"
    nfd = unicodedata.normalize("NFD", "é")
    assert nfc != nfd
    assert normaliser_cle_recherche(nfc) == normaliser_cle_recherche(nfd) == "e"


def test_ligatures_explicites() -> None:
    assert normaliser_cle_recherche("cœur") == normaliser_cle_recherche("coeur") == "coeur"
    assert normaliser_cle_recherche("CŒUR") == "coeur"
    assert normaliser_cle_recherche("æther") == normaliser_cle_recherche("aether") == "aether"
    assert normaliser_cle_recherche("straße") == normaliser_cle_recherche("strasse") == "strasse"


def test_espaces_tete_fin_repetes_insecables() -> None:
    assert normaliser_cle_recherche("  façade   nord  ") == "facade nord"
    assert normaliser_cle_recherche("façade\u00a0nord") == "facade nord"
    assert normaliser_cle_recherche("façade\u202fnord") == "facade nord"


def test_ne_mute_pas_l_entree() -> None:
    brut = "Génois"
    normaliser_cle_recherche(brut)
    assert brut == "Génois"


def test_sans_accents_delegue() -> None:
    assert sans_accents("Été") == normaliser_cle_recherche("Été") == "ete"


def test_vide_et_espaces_seuls() -> None:
    assert normaliser_cle_recherche("") == ""
    assert normaliser_cle_recherche("   ") == ""
    assert normaliser_cle_recherche("\u00a0") == ""


def test_ecriture_non_latine_non_transliteree() -> None:
    """L'arabe / cyrillique ne sont pas translittérés ; la casse cyrillique est pliée."""
    assert normaliser_cle_recherche("Москва") == normaliser_cle_recherche("москва")
    arabe = "شراع"
    assert normaliser_cle_recherche(arabe) == arabe


def test_code_chiffres_tirets_non_alteres() -> None:
    assert normaliser_cle_recherche("0701-GV-001") == "0701-gv-001"
    assert "0701" in normaliser_cle_recherche("0701-GV-001")
