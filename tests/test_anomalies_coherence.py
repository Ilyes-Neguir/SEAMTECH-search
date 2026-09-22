"""Lot B — contrôles RG16, routage par seuils et plafond de confiance (plan v3.0 §13, §17.11 : nom de fichier imposé).

RG16 : indépendants de la confiance (valeurs incohérentes injectées).
Constat A (revue) : tolérance surface FIGÉE par test sur les valeurs réelles
(SLU 6,60 / SLE 5,50 / SPA 15,71 → aucune anomalie ; formule tableur équilatéral).
Constat B (revue) : un champ parfaitement ancré atteint ≥ seuil le plus strict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seamtech_search.fiches.anomalies import evaluer_anomalies
from seamtech_search.fiches.extraction import extraire_avec_filet, extraire_fiche
from seamtech_search.fiches.gabarits import (
    GABARIT_PORTANT,
    GABARITS_EMBARQUES,
)
from seamtech_search.fiches.modeles import Anomalie, ChampExtrait, Cotes, FicheExtraite
from seamtech_search.fiches.persistance import (
    charger_seuils,
    famille_du_champ,
    routage,
)

RACINE = Path(__file__).resolve().parent.parent
PDF_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
PDF_GENOIS = RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf"
SEUILS_REELS = RACINE / "config/seuils_confiance.json"


@pytest.fixture(scope="module")
def fiche_7792():

    return extraire_fiche(PDF_7792, gabarits=list(GABARITS_EMBARQUES))


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


class TestToleranceSurfaceFigee:
    """Constat A (revue) : la tolérance surface est NOMMÉE et FIGÉE par test.

    La fiche de référence calcule sa surface en triangle ÉQUILATÉRAL :
    SPA = (√3/4)·SLU·SLE = 0,4328·SLU·SLE, soit un facteur 0,8655 par rapport
    à la référence ½·SLU·SLE du contrôle — écart −13,4 %. La bande retenue
    [0,55 ; 1,30] (= [−45 % ; +30 %] autour de ½·SLU·SLE) couvre à la fois la
    formule équilatérale du tableur (0,8655) et le triangle quelconque (1,0)
    : la fiche réelle ne doit JAMAIS être signalée (voir docs/CONTROLES_RG16.md).
    """

    def _fiche_portant(self, spa: float, slu: float = 6.6, sle: float = 5.5) -> FicheExtraite:
        fiche = FicheExtraite(code="TEST-SURFACE", type_voile_libelle="Spi Asymétrique")
        fiche.cotes.append(Cotes(jeu="finie", slu_m=slu, sle_m=sle, spa_m2=spa, sf_m=3.08))
        return fiche

    def test_valeurs_reelles_de_la_fiche_reference_sans_anomalie(self) -> None:
        # SLU 6,60 / SLE 5,50 / SPA 15,71 — les valeurs imprimées du 7792-SO
        assert evaluer_anomalies(self._fiche_portant(15.71)) == []

    def test_formule_equilaterale_du_tableur_sans_anomalie(self) -> None:
        spa_equilateral = (3**0.5 / 4) * 6.6 * 5.5  # 15,736…
        assert evaluer_anomalies(self._fiche_portant(round(spa_equilateral, 2))) == []

    def test_surface_hors_bande_basse_est_signalee(self) -> None:
        # facteur 0,496 (< 0,55) : incompatible même avec la formule équilatérale
        codes = {a.code for a in evaluer_anomalies(self._fiche_portant(9.0))}
        assert "surface_incoherente" in codes

    def test_surface_hors_bande_haute_est_signalee(self) -> None:
        # facteur 1,40 (> 1,30) : au-delà d'un triangle quelconque complet
        codes = {a.code for a in evaluer_anomalies(self._fiche_portant(25.4))}
        assert "surface_incoherente" in codes


class TestPlafondDeConfiance:
    """Constat B (revue) : le palier haut de l'échelle doit dépasser le seuil
    le plus strict — sinon « passage direct » est mort par construction."""

    def test_champ_parfaitement_ancre_atteint_le_seuil_le_plus_strict(self, fiche_7792) -> None:
        confiances = {c.champ: c.confiance for c in fiche_7792.champs}
        for champ in ("fiche.code", "fiche.client", "fiche.designation", "fiche.quantite"):
            assert confiances[champ] >= 0.98, f"{champ} = {confiances[champ]} < 0,98"

    def test_cotes_parfaitement_ancrees_atteignent_leur_seuil(self, fiche_7792) -> None:
        finie = next(c for c in fiche_7792.cotes if c.jeu == "finie")
        for champ in finie.champs:
            assert champ.confiance >= 0.95, f"{champ.champ} = {champ.confiance} < 0,95"

    def test_borne_tronquee_reste_sous_le_palier_haut(self) -> None:
        """Une valeur coupée par un libellé stop garde une ambiguïté résiduelle
        (0,90) : le palier 0,99 est réservé aux lectures pleinement bornées."""
        from seamtech_search.fiches.extraction import Ligne, Mot, _construire_et_ranger, _mots_valeur
        from seamtech_search.fiches.gabarits import RegleChamp
        from seamtech_search.lexique import normaliser_terme

        def mot(texte: str, x0: float) -> Mot:
            return Mot(texte=texte, normalise=normaliser_terme(texte), x0=x0, x1=x0 + 30, haut=100.0, bas=111.0, page=0)

        ligne = Ligne(mots=[mot("Quantité", 40.0), mot(":", 100.0), mot("2", 130.0), mot("Remarque", 160.0)], page=0)
        mots, naturelle = _mots_valeur(ligne, 0, ["remarque"])
        assert mots and not naturelle  # tronqué par le libellé stop, sans saut de colonne
        regle = RegleChamp(cible="fiche.quantite", ancres=["quantité"], type="entier")
        fiche_tronquee = FicheExtraite(code="X")
        _construire_et_ranger(fiche_tronquee, regle, "2", 0, mots, borne_naturelle=False)
        assert fiche_tronquee.champs[0].confiance == 0.90
        fiche_bornee = FicheExtraite(code="X")
        _construire_et_ranger(fiche_bornee, regle, "2", 0, mots, borne_naturelle=True)
        assert fiche_bornee.champs[0].confiance == 0.99

    def test_routage_passage_direct_aux_seuils_reels(self, fiche_7792) -> None:
        """Bout-en-bout : avec l'échelle 0,99 et les seuils non calibrés du
        dépôt, la reconstruction 7792 atteint la voie « passage direct » —
        la calibration réelle reste l'affaire de la Tâche 3."""
        decision = routage(fiche_7792, charger_seuils(SEUILS_REELS))
        assert decision["voie"] == "passage_direct", decision


class TestVerrouCalibration:
    """Tâche 1a : le verrou de calibration protège la future validation groupée."""

    def test_seuils_non_calibres_interdits(self) -> None:
        from seamtech_search.fiches.persistance import lire_etat_calibration, verifier_autorisation_validation_lot

        verif = lire_etat_calibration(RACINE / "config/seuils_confiance.json")
        assert verif["calibre"] is False and verif["fiches_reelles_utilisees"] == 0
        autorise, motif = verifier_autorisation_validation_lot(RACINE / "config/seuils_confiance.json")
        assert autorise is False
        assert "NON calibrés" in motif and "acquittement" in motif  # prêt pour un 409

    def test_acquittement_humain_explicite_debloque(self) -> None:
        from seamtech_search.fiches.persistance import verifier_autorisation_validation_lot

        autorise, motif = verifier_autorisation_validation_lot(RACINE / "config/seuils_confiance.json", acquittement_humain=True)
        assert autorise is True and "acquittement" in motif.lower()

    def test_seuils_calibres_autorises(self, tmp_path: Path) -> None:
        import json

        from seamtech_search.fiches.persistance import verifier_autorisation_validation_lot

        chemin = tmp_path / "seuils.json"
        chemin.write_text(
            json.dumps({"calibre": True, "calibre_le": "2026-10-01", "fiches_reelles_utilisees": 24, "familles": {}}),
            encoding="utf-8",
        )
        autorise, motif = verifier_autorisation_validation_lot(chemin)
        assert autorise is True and "24" in motif

    def test_fichier_absent_est_non_calibre(self, tmp_path: Path) -> None:
        from seamtech_search.fiches.persistance import verifier_autorisation_validation_lot

        autorise, motif = verifier_autorisation_validation_lot(tmp_path / "absent.json")
        assert autorise is False  # défaut sûr : pas de preuve = interdit


class TestComptesParPalier:
    """Tâche 1b : l'échelle est ordinale — le tableau de bord comptera par palier."""

    def test_comptes_par_palier_pas_de_moyenne(self, fiche_7792) -> None:
        from seamtech_search.fiches.extraction import compter_par_palier

        comptes = compter_par_palier(fiche_7792)
        assert set(comptes) == {"certain", "lu", "decompose", "partiel"}
        assert sum(comptes.values()) == len([c for c in fiche_7792.tous_les_champs() if c.valeur_normalisee is not None])
        assert comptes["certain"] > 0  # les lectures déterministes de la reconstruction

    def test_paliers_ordinnaux_separes(self) -> None:
        from seamtech_search.fiches.extraction import compter_par_palier
        from seamtech_search.fiches.modeles import ChampExtrait

        fiche = FicheExtraite(code="PALIERS")
        for conf, etiquette in ((0.99, "certain"), (0.90, "lu"), (0.85, "decompose"), (0.50, "partiel")):
            champ = ChampExtrait(champ=f"c{conf}", valeur_brute="x", valeur_normalisee="x", confiance=conf)
            fiche.champs.append(champ)
        comptes = compter_par_palier(fiche)
        assert comptes == {"certain": 1, "lu": 1, "decompose": 1, "partiel": 1}
