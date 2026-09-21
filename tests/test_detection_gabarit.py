"""Lot B — détection de gabarit et variantes (plan v3.0 §13, §17.11 : nom de fichier imposé).

Le génois (mono-colonne) prouve que le mécanisme généralise ; le filet RG6
conserve les libellés connus quand aucun gabarit ne reconnaît le PDF.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seamtech_search.fiches.extraction import extraire_avec_filet, extraire_fiche
from seamtech_search.fiches.gabarits import (
    GABARIT_GENOIS,
    GABARIT_PORTANT,
    GABARITS_EMBARQUES,
    GabaritInconnu,
)
from seamtech_search.fiches.modeles import FicheExtraite

RACINE = Path(__file__).resolve().parent.parent
PDF_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
PDF_GENOIS = RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf"


@pytest.fixture(scope="module")
def fiche_7792():
    from seamtech_search.fiches.extraction import extraire_fiche

    return extraire_fiche(PDF_7792, gabarits=list(GABARITS_EMBARQUES))


@pytest.fixture(scope="module")
def fiche_genois():
    from seamtech_search.fiches.extraction import extraire_fiche

    return extraire_fiche(PDF_GENOIS, gabarits=list(GABARITS_EMBARQUES))


class TestDetectionGabarit:
    def test_7792_reconnu_comme_portant(self, fiche_7792: FicheExtraite) -> None:
        assert fiche_7792.gabarit_code == "FICHE_PORTANT_V1"
        assert fiche_7792.gabarit_version == 1

    def test_genois_reconnu_comme_genois(self, fiche_genois: FicheExtraite) -> None:
        # « fiche de fabrication » est commune aux deux variantes : les ancres
        # de détection du portant ne doivent PAS attraper un génois.
        assert fiche_genois.gabarit_code == "FICHE_GENOIS_V1"

    def test_ancres_de_detection_discriminantes(self) -> None:
        texte_genois = "fiche de fabrication - genois\nnavire : sun 2000"
        assert GABARIT_GENOIS.score_detection(texte_genois) >= 1
        assert GABARIT_PORTANT.score_detection(texte_genois) == 0

class TestVarianteGenois:
    def test_champs_principaux_lus(self, fiche_genois: FicheExtraite) -> None:
        assert fiche_genois.code == "0901-MM"
        assert fiche_genois.client_nom == "Moreau"
        assert fiche_genois.bateau_nom == "Sun 2000"
        assert fiche_genois.type_voile_libelle == "Génois"
        finie = next(c for c in fiche_genois.cotes if c.jeu == "finie")
        assert finie.slu_m == 8.2 and finie.sf_m == 3.9 and finie.spa_m2 == 18.2
        assert fiche_genois.tissu_texte == "Dacron 260"

    def test_genois_tissu_principal_dans_fiche_materiau(self, fiche_genois: FicheExtraite) -> None:
        """« Tissu : Dacron 260 » alimente fiche_materiau (rôle tissu_principal)."""
        tissu = next(m for m in fiche_genois.materiaux if m.role == "tissu_principal")
        assert tissu.designation == "Dacron 260"
        assert tissu.grammage_g_m2 is None  # « 260 » seul n'est pas un grammage g/m²
        trace = tissu.champs[0]
        assert trace.table_cible == "fiche_materiau" and trace.zone is not None
        # la fiche garde aussi le texte brut (fiche.tissu_texte)
        assert fiche_genois.tissu_texte == "Dacron 260"

    def test_genois_sans_anomalie_rg16(self, fiche_genois: FicheExtraite) -> None:
        # la surface d'une interface ne suit pas le ratio des portants :
        # le contrôle croisé ne doit pas la condamner à tort.
        assert not [a for a in fiche_genois.anomalies if a.code == "surface_incoherente"]

class TestVoieDegradeeRG6:
    def test_gabarit_inconnu_leve_avec_explication(self) -> None:

        with pytest.raises(GabaritInconnu, match="Aucun gabarit"):
            extraire_fiche(PDF_GENOIS, gabarits=[GABARIT_PORTANT])

    def test_filet_conserve_les_libelles_connus(self) -> None:
        fiche = extraire_avec_filet(PDF_GENOIS, [GABARIT_PORTANT])
        assert fiche.gabarit_code is None
        codes = {a.code for a in fiche.anomalies}
        assert "gabarit_inconnu" in codes
        libres = {m.champ: m for m in fiche.mesures_libres}
        # le vocabulaire connu de la fiche portant retient les libellés génois
        for attendu in ("navire", "client", "guindant", "bordure", "tissu", "surface"):
            assert any(attendu in code for code in libres), f"libellé « {attendu} » perdu (RG6)"
        guindant = next(m for code, m in libres.items() if "guindant" in code)
        assert guindant.valeur_normalisee == "8.2"
        assert guindant.zone is not None
