"""Traitements d'extraction v1 — tests d'ingénierie SYNTHÉTIQUES.

Depuis le réglage du gabarit v2 sur la vraie fiche 7792-SO (Tâche 2), les
gestionnaires « mot à mot » de la v1 (``_traiter_galons``, ``_traiter_jonctions``,
``_traiter_epaisseurs``… écrits pour la reconstruction) ne s'exécutent plus sur
le document réel : la v2 lit des grilles tracées. Ils restent pourtant vivants :
le registre conserve la v1 (lisible, désactivée — jamais supprimée) et tout
gabarit futur peut référencer ces traitements.

Ce fichier les exerce donc sur des chaînes synthétiques (pas un PDF) pour que
leur comportement reste mesuré et que la couverture ne glisse pas. Statut :
tests SYNTHÉTIQUES — ils ne valident aucun gabarit sur document réel
(``docs/DETECTION_FICHES.md`` : un gabarit n'est « fait » que mesuré sur un
document réel).
"""

from __future__ import annotations

from seamtech_search.fiches.extraction import (
    _traiter_dessinateur,
    _traiter_epaisseurs,
    _traiter_fichier_source,
    _traiter_finitions,
    _traiter_galons,
    _traiter_jonctions,
    _traiter_montage,
    _traiter_notes,
    _traiter_options,
)
from seamtech_search.fiches.modeles import ChampExtrait, FicheExtraite


def _base(champ: str = "fiche.tests") -> ChampExtrait:
    return ChampExtrait(champ=champ, valeur_brute="brut", confiance=0.5)


class TestDessinateurV1:
    def test_forme_document_client_nom_le_date(self) -> None:
        fiche = FicheExtraite()
        base = _base("fiche.dessinateur")
        _traiter_dessinateur(fiche, "Yann le 6 mars 2026", base)
        assert fiche.dessinateur == "Yann"
        assert fiche.date_dessin == "2026-03-06"
        assert base.valeur_normalisee == "Yann | 2026-03-06"
        assert base.confiance >= 0.85

    def test_forme_separateur_point_median(self) -> None:
        fiche = FicheExtraite()
        base = _base("fiche.dessinateur")
        _traiter_dessinateur(fiche, "Yann · 6 mars 2026", base)
        assert fiche.dessinateur == "Yann"
        assert fiche.date_dessin == "2026-03-06"

    def test_tiret_mesuré_comme_partie_du_nom(self) -> None:
        # Comportement MESURÉ (pas celui de la docstring) : « - » n'est pas un
        # séparateur de scinder_sur_tirets — le bloc entier reste le nom, sans
        # date. Le document réel n'utilise de toute façon pas cette forme.
        fiche = FicheExtraite()
        base = _base("fiche.dessinateur")
        _traiter_dessinateur(fiche, "Yann - 6 mars 2026", base)
        assert fiche.dessinateur == "Yann - 6 mars 2026"
        assert fiche.date_dessin is None

    def test_sans_date_conserve_le_nom(self) -> None:
        fiche = FicheExtraite()
        base = _base("fiche.dessinateur")
        _traiter_dessinateur(fiche, "Yann", base)
        assert fiche.dessinateur == "Yann"
        assert fiche.date_dessin is None

    def test_vide_ne_fait_rien(self) -> None:
        fiche = FicheExtraite()
        base = _base("fiche.dessinateur")
        _traiter_dessinateur(fiche, "   ", base)
        assert fiche.dessinateur is None


class TestFichierSourceV1:
    def test_fichier_et_date_edition(self) -> None:
        fiche = FicheExtraite()
        base = _base("fiche.fichier_source")
        _traiter_fichier_source(fiche, "7792-SO.xlsm édité le 06/03/2026", base)
        assert fiche.fichier_source == "7792-SO.xlsm"
        assert fiche.date_edition == "2026-03-06"
        assert base.confiance >= 0.9

    def test_sans_extension_ignoré(self) -> None:
        fiche = FicheExtraite()
        base = _base("fiche.fichier_source")
        _traiter_fichier_source(fiche, "pas un fichier", base)
        assert fiche.fichier_source is None


class TestMontageV1:
    def test_colle_cousu_avec_fil(self) -> None:
        fiche = FicheExtraite()
        base = _base("fiche.montage")
        _traiter_montage(fiche, "Collé/Cousu · V46", base)
        assert fiche.montage_type == "Collé/Cousu"
        assert fiche.montage_fil == "V46"
        assert "V46" in (base.valeur_normalisee or "")

    def test_second_morceau_sans_fil_devient_fil(self) -> None:
        fiche = FicheExtraite()
        base = _base("fiche.montage")
        _traiter_montage(fiche, "Cousu · T60", base)
        assert fiche.montage_fil == "T60"


class TestGalonsV1:
    def test_guindant_complet(self) -> None:
        fiche = FicheExtraite()
        base = _base("galon.guindant")
        _traiter_galons(fiche, "Bleu · 50 mm · Nylon 65 g/m2", base, "galon guindant")
        assert len(fiche.galons) == 1
        galon = fiche.galons[0]
        assert galon.bande == "guindant"
        assert galon.couleur == "Bleu"
        assert galon.largeur_mm == 50.0
        assert galon.grammage_g_m2 == 65.0
        assert galon.matiere == "Nylon"

    def test_bande_lue_dans_l_ancre(self) -> None:
        fiche = FicheExtraite()
        _traiter_galons(fiche, "Rouge", _base("galon.chute"), "galon chute")
        _traiter_galons(fiche, "Blanc", _base("galon.bordure"), "galon bordure")
        bandes = {g.bande: g.couleur for g in fiche.galons}
        assert bandes == {"chute": "Rouge", "bordure": "Blanc"}

    def test_seconde_lecture_complete_le_galon_existant(self) -> None:
        fiche = FicheExtraite()
        _traiter_galons(fiche, "Bleu", _base("galon.guindant"), "galon guindant")
        _traiter_galons(fiche, "50 mm", _base("galon.guindant"), "galon guindant")
        assert len(fiche.galons) == 1
        assert fiche.galons[0].couleur == "Bleu"
        assert fiche.galons[0].largeur_mm == 50.0


class TestJonctionsV1:
    def test_laizes_decomposees(self) -> None:
        fiche = FicheExtraite()
        base = _base("jonction.laizes")
        _traiter_jonctions(fiche, "2 zigzag 6 tps 30 mm", base, "jonction laizes")
        assert len(fiche.jonctions) == 1
        jonction = fiche.jonctions[0]
        assert jonction.nature == "laizes"
        assert jonction.nb_zigzag == 2
        assert jonction.nb_points == 6
        assert jonction.espacement_mm == 30.0

    def test_horizontale_et_verticale_ordre(self) -> None:
        fiche = FicheExtraite()
        _traiter_jonctions(fiche, "1 zigzag 4 tps 20 mm", _base(), "jonction horizontale")
        _traiter_jonctions(fiche, "2 zigzag 4 tps 20 mm", _base(), "jonction verticale 2")
        natures = {(j.nature, j.ordre) for j in fiche.jonctions}
        assert ("horizontale", 1) in natures
        assert ("verticale", 2) in natures

    def test_surplus_garde_le_texte(self) -> None:
        fiche = FicheExtraite()
        _traiter_jonctions(fiche, "surplus 100 mm", _base(), "jonction surplus")
        assert fiche.jonctions[0].nature == "surplus"
        assert fiche.jonctions[0].surplus == "surplus 100 mm"

    def test_seconde_lecture_complete_sans_dupliquer(self) -> None:
        fiche = FicheExtraite()
        _traiter_jonctions(fiche, "2 zigzag", _base(), "jonction laizes")
        _traiter_jonctions(fiche, "6 tps 30 mm", _base(), "jonction laizes")
        assert len(fiche.jonctions) == 1
        assert fiche.jonctions[0].nb_points == 6


class TestFinitionsV1:
    def test_postes_multiples_et_oeillet(self) -> None:
        fiche = FicheExtraite()
        base = _base("finitions")
        _traiter_finitions(fiche, "amure/écoute/drisse — Œillet SR12, non-sanglé", base)
        postes = [f.poste for f in fiche.finitions]
        # normaliser_terme retire les accents : « écoute » → « ecoute ».
        assert "amure" in postes and "ecoute" in postes and "drisse" in postes
        amure = next(f for f in fiche.finitions if f.poste == "amure")
        assert amure.oeillet_type == "SR12"
        assert amure.sangle is False

    def test_sangle_explicite(self) -> None:
        fiche = FicheExtraite()
        _traiter_finitions(fiche, "drisse — sanglée", _base("finitions"))
        assert fiche.finitions[0].sangle is True

    def test_sans_poste_repli_finition(self) -> None:
        fiche = FicheExtraite()
        _traiter_finitions(fiche, "— Œillet SR12", _base("finitions"))
        assert fiche.finitions[0].poste == "finition"


class TestOptionsV1:
    def test_booleens_et_sans_objet(self) -> None:
        fiche = FicheExtraite()
        base = _base("options")
        _traiter_options(fiche, "Velcro anti-déroulement : Non · Protection anti-UV : Oui · Chaussette : ~", base)
        codes = {o.code: o for o in fiche.options}
        assert codes["velcro_anti_deroulement"].valeur_bool is False
        assert codes["protection_anti_uv"].valeur_bool is True
        # RG5 : « ~ » = sans objet → pas de booléen inventé, le texte demeure.
        assert codes["chaussette"].valeur_bool is None
        assert codes["chaussette"].valeur_texte == "~"

    def test_libelle_vide_ignoré(self) -> None:
        fiche = FicheExtraite()
        _traiter_options(fiche, ": Oui", _base("options"))
        assert fiche.options == []


class TestEpaisseursV1:
    def test_niveaux_consecutifs_avec_tilde(self) -> None:
        fiche = FicheExtraite()
        base = _base("materiaux")
        _traiter_epaisseurs(fiche, "Monofilm K903 · 190 mm ; 270 g/m2 ; 220 mm ; ~", base)
        # « ~ » est un niveau vide conservé sans matériau (RG5) : 3 matériaux.
        assert len(fiche.materiaux) == 3
        niveaux = [m.niveau for m in fiche.materiaux]
        assert niveaux == [1, 2, 3]
        assert fiche.materiaux[0].designation == "Monofilm K903"
        assert fiche.materiaux[0].mesure_mm == 190.0
        assert fiche.materiaux[1].grammage_g_m2 == 270.0

    def test_texte_sans_motif_conservé(self) -> None:
        fiche = FicheExtraite()
        _traiter_epaisseurs(fiche, "Voir avec JFC", _base("materiaux"))
        assert fiche.materiaux[0].designation == "Voir avec JFC"


class TestNotesV1:
    def test_renforts_extraits_des_notes(self) -> None:
        fiche = FicheExtraite()
        base = _base("fiche.notes")
        _traiter_notes(fiche, "2 x œillets n°1 Ø 12 mm Dacron", base)
        assert fiche.notes == "2 x œillets n°1 Ø 12 mm Dacron"
        assert len(fiche.renforts) == 1
        renfort = fiche.renforts[0]
        assert renfort.quantite == 2
        assert renfort.repere == "n°1"
        assert renfort.diametre_mm == 12.0
        assert renfort.matiere == "Dacron"

    def test_notes_vides_aucun_renfort(self) -> None:
        fiche = FicheExtraite()
        _traiter_notes(fiche, "   ", _base("fiche.notes"))
        assert fiche.notes is None
        assert fiche.renforts == []
