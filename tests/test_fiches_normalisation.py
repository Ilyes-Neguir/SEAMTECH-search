"""Lot B — normalisation des valeurs lues (unitaires, sans PDF ni base)."""

from __future__ import annotations

from seamtech_search.fiches import normalisation as norm


class TestDecimalesEtUnites:
    def test_decimal_virgule_francaise(self) -> None:
        assert norm.extraire_decimal("6,60 m") == 6.6

    def test_decimal_point(self) -> None:
        assert norm.extraire_decimal("3.08 m") == 3.08

    def test_decimal_absent(self) -> None:
        assert norm.extraire_decimal("Néant") is None
        assert norm.extraire_decimal("") is None
        assert norm.extraire_decimal(None) is None

    def test_cote_vers_metres_depuis_cm(self) -> None:
        assert norm.cote_en_metres("660 cm") == 6.6

    def test_cote_vers_metres_depuis_mm(self) -> None:
        assert norm.cote_en_metres("3080 mm") == 3.08

    def test_mesure_vers_mm_depuis_cm(self) -> None:
        assert norm.mesure_en_mm("19 cm") == 190.0

    def test_mesure_vers_mm_deja_mm(self) -> None:
        assert norm.mesure_en_mm("190 mm") == 190.0

    def test_entier(self) -> None:
        assert norm.extraire_entier("Quantité : 2") == 2
        assert norm.extraire_entier("aucun") is None


class TestGrammage:
    def test_grammage_g_m2_carre(self) -> None:
        assert norm.grammage_g_m2("270 g/m²") == 270.0

    def test_grammage_gr_sans_carre(self) -> None:
        assert norm.grammage_g_m2("65 gr/m2") == 65.0

    def test_grammage_colle(self) -> None:
        assert norm.grammage_g_m2("Nylon 65g/m2") == 65.0

    def test_grammage_absent(self) -> None:
        assert norm.grammage_g_m2("50 mm") is None


class TestDatesFrancaises:
    def test_date_litterale(self) -> None:
        assert norm.date_fr_vers_iso("6 mars 2026") == "2026-03-06"

    def test_date_chiffree_slash(self) -> None:
        # format français jour/mois/année assumé (dépôt français, plan §13)
        assert norm.date_fr_vers_iso("06/03/2026") == "2026-03-06"

    def test_date_illisible_renvoie_none(self) -> None:
        assert norm.date_fr_vers_iso("sans date") is None

    def test_date_invalide_renvoie_none(self) -> None:
        assert norm.date_fr_vers_iso("32/13/2026") is None


class TestBooleensEtTextes:
    def test_non_et_oui(self) -> None:
        assert norm.booleen_fr("Non") is False
        assert norm.booleen_fr("Oui") is True

    def test_tilde_sans_objet(self) -> None:
        # RG5 : « ~ » = sans objet, jamais inventé à faux
        assert norm.booleen_fr("~") is None

    def test_nom_et_detail_parentheses(self) -> None:
        assert norm.separer_nom_et_detail("29er (15')") == ("29er", "15'")
        assert norm.separer_nom_et_detail("Sailonet (Cruette)") == ("Sailonet", "Cruette")
        assert norm.separer_nom_et_detail("Sun 2000") == ("Sun 2000", None)

    def test_scinder_sur_tirets_medians(self) -> None:
        assert norm.scinder_sur_tirets("Bleu · 50 mm · Nylon 65 g/m2") == ["Bleu", "50 mm", "Nylon 65 g/m2"]

    def test_decomposer_jonction(self) -> None:
        assert norm.decomposer_jonction("2 zigzag 6 tps 30 mm") == (2, 6, 30.0, "2 zigzag 6 tps 30 mm")

    def test_decomposer_jonction_vide(self) -> None:
        assert norm.decomposer_jonction("~") == (None, None, None, "~")
