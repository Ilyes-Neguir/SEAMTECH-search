"""Lot B — extraction pilotée par gabarit sur les fixtures du dépôt.

Cible §13 du plan v3.0 : ≥ 90 % des champs corrects au banc (baseline
« avant » : 16,7 %). Les deux variantes de la vérité terrain sont couvertes :
la reconstruction 7792-SO (fiche portant, tableau réglé de cotes) et la fiche
génois (mono-colonne — c'est le test qui prouve que le mécanisme généralise).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seamtech_search.fiches.anomalies import evaluer_anomalies
from seamtech_search.fiches.extraction import extraire_avec_filet, extraire_fiche
from seamtech_search.fiches.gabarits import GABARIT_GENOIS, GABARIT_PORTANT, GABARITS_EMBARQUES
from seamtech_search.fiches.modeles import Anomalie, ChampExtrait, Cotes, FicheExtraite
from seamtech_search.fiches.persistance import VERITE_7792, evaluer_verite, famille_du_champ, routage

RACINE = Path(__file__).resolve().parent.parent
PDF_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
PDF_GENOIS = RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf"


@pytest.fixture(scope="module")
def fiche_7792() -> FicheExtraite:
    return extraire_fiche(PDF_7792, gabarits=list(GABARITS_EMBARQUES))


@pytest.fixture(scope="module")
def fiche_genois() -> FicheExtraite:
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


class TestFiche7792Verite:
    def test_taux_superieur_a_90_pourcent(self, fiche_7792: FicheExtraite) -> None:
        taux, ecarts = evaluer_verite(fiche_7792, VERITE_7792)
        assert taux >= 0.90, f"taux {taux:.1%} < 90 % : {ecarts}"

    def test_champs_structurels(self, fiche_7792: FicheExtraite) -> None:
        assert fiche_7792.code == "7792-SO"
        assert fiche_7792.titre == "Voile de portant"
        assert fiche_7792.client_nom == "Sailonet"
        assert fiche_7792.client_chantier == "Cruette"
        assert fiche_7792.bateau_nom == "29er"
        assert fiche_7792.bateau_taille == "15'"
        assert fiche_7792.type_voile_libelle == "Spi Asymétrique"
        assert fiche_7792.gamme == "Medium Régate"
        assert fiche_7792.quantite == 1
        assert fiche_7792.date_dessin == "2026-03-06"

    def test_cotes_nommees_jeu_finie(self, fiche_7792: FicheExtraite) -> None:
        finie = next(c for c in fiche_7792.cotes if c.jeu == "finie")
        assert finie.slu_m == 6.6
        assert finie.sle_m == 5.5
        assert finie.sf_m == 3.08
        assert finie.shw_m == 3.14
        assert finie.spa_m2 == 15.71
        assert finie.tetiere_cm == 3.0
        assert finie.poids_kg == 0.7

    def test_materiaux_epaisseurs(self, fiche_7792: FicheExtraite) -> None:
        niveau_1 = next(m for m in fiche_7792.materiaux if m.niveau == 1)
        assert niveau_1.designation == "Monofilm K903"
        assert niveau_1.mesure_mm == 190.0
        niveau_2 = next(m for m in fiche_7792.materiaux if m.niveau == 2)
        assert niveau_2.grammage_g_m2 == 270.0
        niveau_3 = next(m for m in fiche_7792.materiaux if m.niveau == 3)
        assert niveau_3.designation is None and niveau_3.mesure_mm == 220.0

    def test_galons_deux_bandes(self, fiche_7792: FicheExtraite) -> None:
        guindant = next(g for g in fiche_7792.galons if g.bande == "guindant")
        assert guindant.couleur == "Bleu"
        assert guindant.largeur_mm == 50.0
        assert guindant.matiere == "Nylon" and guindant.grammage_g_m2 == 65.0
        chute = next(g for g in fiche_7792.galons if g.bande == "chute")
        assert chute.couleur == "Rouge · Blanc" and chute.largeur_mm == 50.0

    def test_jonctions_et_surplus(self, fiche_7792: FicheExtraite) -> None:
        laizes = next(j for j in fiche_7792.jonctions if j.nature == "laizes")
        assert (laizes.nb_zigzag, laizes.nb_points, laizes.espacement_mm) == (1, 6, 15.0)
        horizontale = next(j for j in fiche_7792.jonctions if j.nature == "horizontale")
        assert (horizontale.nb_zigzag, horizontale.nb_points, horizontale.espacement_mm) == (2, 6, 30.0)
        surplus = next(j for j in fiche_7792.jonctions if j.nature == "surplus")
        assert surplus.surplus == "~"  # RG5 : sans objet, conservé tel quel

    def test_finitions_options_renfort(self, fiche_7792: FicheExtraite) -> None:
        postes = {f.poste for f in fiche_7792.finitions}
        assert postes == {"amure", "ecoute", "drisse"}
        amure = next(f for f in fiche_7792.finitions if f.poste == "amure")
        assert amure.oeillet_type == "SR12" and amure.sangle is False
        velcro = next(o for o in fiche_7792.options if o.code == "velcro_anti_deroulement")
        assert velcro.valeur_bool is False
        emagasineur = next(o for o in fiche_7792.options if o.code == "emmagasineur")
        assert emagasineur.valeur_bool is None  # « ~ » : sans objet
        renfort = fiche_7792.renforts[0]
        assert (renfort.quantite, renfort.forme, renfort.diametre_mm) == (2, "œillets", 200.0)

    def test_montage(self, fiche_7792: FicheExtraite) -> None:
        assert fiche_7792.montage_type == "Collé/Cousu"
        assert fiche_7792.montage_fil == "V46"


class TestTracabilite:
    """Le contrat du projet : toute valeur répond « d'où, confiance, où »."""

    def test_chaque_champ_porte_zone_page_et_methode(self, fiche_7792: FicheExtraite) -> None:
        for champ in fiche_7792.tous_les_champs():
            assert champ.valeur_brute is not None or champ.valeur_normalisee is not None
            assert champ.methode == "gabarit"
            if champ.valeur_normalisee is not None:
                assert champ.zone is not None, f"{champ.champ} sans zone"

    def test_zone_exploitable(self, fiche_7792: FicheExtraite) -> None:
        champ_code = next(c for c in fiche_7792.champs if c.champ == "fiche.code")
        zone = champ_code.zone.en_dict()
        assert zone["x1"] > zone["x0"] > 0
        assert zone["y1"] > zone["y0"] > 0
        assert zone["page"] == 0

    def test_aucun_doublon_champ_rang(self, fiche_7792: FicheExtraite) -> None:
        cles = [(c.champ, c.rang) for c in fiche_7792.tous_les_champs()]
        assert len(cles) == len(set(cles))

    def test_confiances_dans_les_limites(self, fiche_7792: FicheExtraite) -> None:
        for champ in fiche_7792.tous_les_champs():
            assert 0.0 <= champ.confiance <= 1.0


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
        from seamtech_search.fiches.gabarits import GabaritInconnu

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


class TestRG16Incoherences:
    """RG16 : contrôles INDEPENDANTS de la confiance (valeurs injectées)."""

    def _fiche(self, spa: float | None, slu: float = 6.6, sle: float = 5.5, sf: float = 3.08) -> FicheExtraite:
        fiche = FicheExtraite(code="TEST-RG16", type_voile_libelle="Spi Asymétrique")
        fiche.cotes.append(Cotes(jeu="finie", slu_m=slu, sle_m=sle, sf_m=sf, spa_m2=spa))
        return fiche

    def test_surface_absurde_meme_a_confiance_maximale(self) -> None:
        fiche = self._fiche(spa=999.0)
        # la valeur « absurde » est portée avec une confiance MAXIMALE :
        # RG16 doit quand même la signaler (indépendance de la confiance).
        fiche.champs.append(
            ChampExtrait(champ="cotes.finie.spa_m2", valeur_brute="999 m2", valeur_normalisee="999.0", confiance=1.0)
        )
        codes = {a.code for a in evaluer_anomalies(fiche)}
        assert "surface_incoherente" in codes
        assert "cote_hors_plage" in codes  # 999 m² hors plage aussi

    def test_cotes_ordonnees_impossibles(self) -> None:
        fiche = self._fiche(spa=15.0, slu=2.0, sf=8.0)  # bordure > guindant
        codes = {a.code for a in evaluer_anomalies(fiche)}
        assert "cotes_incoherentes" in codes

    def test_portant_slu_doit_depasser_sle(self) -> None:
        fiche = self._fiche(spa=15.0, slu=4.0, sle=6.0)
        codes = {a.code for a in evaluer_anomalies(fiche)}
        assert "cotes_incoherentes" in codes

    def test_fiche_coherente_passe(self, fiche_7792: FicheExtraite) -> None:
        assert evaluer_anomalies(fiche_7792) == []

    def test_cotes_absentes_sont_signalees(self) -> None:
        fiche = FicheExtraite(code="TEST-VIDE")
        codes = {a.code for a in evaluer_anomalies(fiche)}
        assert "champ_manquant" in codes


class TestRoutageSeuils:
    """Les seuils config/seuils_confiance.json sont CONSOMMÉS (§10.3)."""

    def test_familles_couvertes(self) -> None:
        assert famille_du_champ("fiche.code") == "structurels"
        assert famille_du_champ("cotes.finie.slu_m") == "cotes"
        assert famille_du_champ("galon.guindant") == "materiaux"
        assert famille_du_champ("options") == "finitions_options"
        assert famille_du_champ("fiche.notes") == "notes_libres"

    def test_seuils_charges_du_fichier_du_depot(self) -> None:
        from seamtech_search.fiches.persistance import charger_seuils

        seuils = charger_seuils(RACINE / "config/seuils_confiance.json")
        assert seuils["structurels"] == 0.98
        assert seuils["cotes"] == 0.95

    def test_anomalie_bloque_le_passage_direct(self, fiche_7792: FicheExtraite) -> None:
        fiche = fiche_7792.model_copy(deep=True)
        fiche.anomalies.append(Anomalie(code="cote_hors_plage", gravite="moyenne", message="test"))
        decision = routage(fiche, {"structurels": 0.0, "cotes": 0.0, "materiaux": 0.0, "finitions_options": 0.0, "notes_libres": 0.0})
        assert decision["voie"] == "relecture_ciblee"

    def test_tout_au_dessus_des_seuils_et_sans_anomalie(self, fiche_7792: FicheExtraite) -> None:
        decision = routage(fiche_7792, {"structurels": 0.0, "cotes": 0.0, "materiaux": 0.0, "finitions_options": 0.0, "notes_libres": 0.0})
        assert decision["voie"] == "passage_direct"

    def test_gabarit_inconnu_reprise_complete(self) -> None:
        fiche = extraire_avec_filet(PDF_GENOIS, [GABARIT_PORTANT])
        assert routage(fiche)["voie"] == "reprise_complete"


class TestJeuxDeCotes:
    """« fiche_cotes (jeux dessin et finie) » : le JEU vient de la cible du
    gabarit — la fiche 7792 n'imprimant que « Mesures Finies », la règle
    générale est prouvée par un gabarit de test qui lit le même ancre en jeu
    « dessin », puis par la lecture de vérité par jeu."""

    def test_regle_cotes_jeu_dessin(self) -> None:
        from seamtech_search.fiches.gabarits import GabaritDef, RegleChamp

        gabarit = GabaritDef(
            code="TEST_DESSIN",
            ancres_detection=["voile de portant"],
            champs=[RegleChamp(cible="cotes.dessin.slu_m", ancres=["guindant (slu)"], type="decimal_m")],
        )
        fiche = extraire_fiche(PDF_7792, gabarits=[gabarit])
        dessin = next(c for c in fiche.cotes if c.jeu == "dessin")
        assert dessin.slu_m == 6.6
        trace = dessin.champs[0]
        assert trace.champ == "cotes.dessin.slu_m" and trace.table_cible == "fiche_cotes"

    def test_verite_lisible_sur_les_deux_jeux(self) -> None:
        fiche = FicheExtraite(
            code="X",
            cotes=[
                Cotes(jeu="dessin", slu_m=6.8, sf_m=3.2),
                Cotes(jeu="finie", slu_m=6.6, sf_m=3.08),
            ],
        )
        taux, ecarts = evaluer_verite(
            fiche,
            {"cotes.dessin.slu_m": 6.8, "cotes.dessin.sf_m": 3.2, "cotes.finie.slu_m": 6.6, "cotes.finie.sf_m": 3.08},
        )
        assert taux == 1.0 and not ecarts
