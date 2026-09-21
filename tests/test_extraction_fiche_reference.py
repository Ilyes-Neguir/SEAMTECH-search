"""Lot B — extraction de la fiche de référence 7792-SO (plan v3.0 §13, §17.11 : nom de fichier imposé).

Cible §13 : ≥ 90 % des champs corrects au banc (baseline « avant » : 16,7 %).
Traçabilité complète : chaque valeur porte méthode, confiance, page et zone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seamtech_search.fiches.extraction import extraire_fiche
from seamtech_search.fiches.gabarits import (
    GABARITS_EMBARQUES,
    GabaritDef,
    RegleChamp,
)
from seamtech_search.fiches.modeles import Cotes, FicheExtraite
from seamtech_search.fiches.persistance import (
    VERITE_7792,
    evaluer_verite,
)

RACINE = Path(__file__).resolve().parent.parent
PDF_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"


@pytest.fixture(scope="module")
def fiche_7792():
    from seamtech_search.fiches.extraction import extraire_fiche

    return extraire_fiche(PDF_7792, gabarits=list(GABARITS_EMBARQUES))


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
        # Le document réel n'imprime têtière/poids QUE sur la ligne
        # « Mesures Dessin » (la ligne « Mesures Finies » ne les porte pas) :
        # la vérité suit le document, pas l'ancienne reconstruction.
        dessin = next(c for c in fiche_7792.cotes if c.jeu == "dessin")
        assert dessin.tetiere_cm == 3.0
        assert dessin.poids_kg == 0.7

    def test_materiaux_epaisseurs(self, fiche_7792: FicheExtraite) -> None:
        niveau_1 = next(m for m in fiche_7792.materiaux if m.niveau == 1)
        assert niveau_1.designation == "Monofilm K903"
        assert niveau_1.mesure_mm == 190.0
        niveau_2 = next(m for m in fiche_7792.materiaux if m.niveau == 2)
        assert niveau_2.grammage_g_m2 == 270.0
        niveau_3 = next(m for m in fiche_7792.materiaux if m.niveau == 3)
        assert niveau_3.designation is None and niveau_3.mesure_mm == 260.0
        assert niveau_3.grammage_g_m2 == 210.0
        niveau_4 = next(m for m in fiche_7792.materiaux if m.niveau == 4)
        assert niveau_4.designation is None and niveau_4.mesure_mm == 300.0
        assert niveau_4.grammage_g_m2 == 170.0

    def test_galons_deux_bandes(self, fiche_7792: FicheExtraite) -> None:
        guindant = next(g for g in fiche_7792.galons if g.bande == "guindant")
        assert guindant.couleur == "Bleu"
        assert guindant.largeur_mm == 50.0
        assert guindant.matiere == "Nylon" and guindant.grammage_g_m2 == 65.0
        # Le document réel n'imprime que « Galon - Rouge » sur la chute
        # (pas de seconde couleur).
        chute = next(g for g in fiche_7792.galons if g.bande == "chute")
        assert chute.couleur == "Rouge" and chute.largeur_mm == 50.0

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

class TestJeuxDeCotes:
    """« fiche_cotes (jeux dessin et finie) » : le document réel imprime les
    DEUX lignes « Mesures Dessin » et « Mesures Finies » ; la grille tracée
    les lit chacune sous son jeu. Un gabarit minimal (une seule règle de
    grille) prouve que la lecture ne dépend pas du gabarit complet."""

    def test_regle_cotes_jeu_dessin(self) -> None:

        gabarit = GabaritDef(
            code="TEST_DESSIN",
            ancres_detection=["voile de portant"],
            champs=[
                RegleChamp(
                    cible="cotes.dessin.slu_m",
                    ancres=["guindant (slu)"],
                    type="texte",
                    traitement="grille_cotes",
                )
            ],
        )
        fiche = extraire_fiche(PDF_7792, gabarits=[gabarit])
        dessin = next(c for c in fiche.cotes if c.jeu == "dessin")
        assert dessin.slu_m == 6.6
        assert dessin.tetiere_cm == 3.0
        finie = next(c for c in fiche.cotes if c.jeu == "finie")
        assert finie.slu_m == 6.6 and finie.spa_m2 == 15.71
        trace = next(c for c in dessin.champs if c.champ == "cotes.dessin.slu_m")
        assert trace.table_cible == "fiche_cotes"

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
