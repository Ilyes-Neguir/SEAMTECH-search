"""Gabarits du format atelier — FICHE_JADE_V1 / FICHE_GV_FULLBATTEN_V1.

Ces tests valident l'extension du parseur sur la structure des fiches réelles
du corpus 2026-09-28 avec des PDF SYNTHÉTIQUES générés par les tests eux-mêmes
(reportlab) : mêmes libellés génériques et même organisation de page (en-tête
en colonnes, cotes par bord, BDF, points d'ancrage, planning atelier), mais
des valeurs intégralement FICTICES. AUCUNE donnée client (nom, référence,
bateau, cote réelle) n'apparaît ici — le dépôt est public.

Non-régression incluse : les gabarits historiques (portant 7792, génois)
continuent de gagner la détection sur leurs propres documents, et le filet
RG6 conserve les libellés connus quand aucun gabarit ne reconnaît le PDF.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from seamtech_search.anchors import classify_pdf_text
from seamtech_search.extractors import extract_file
from seamtech_search.fiches.extraction import extraire_avec_filet, extraire_fiche
from seamtech_search.fiches.gabarits import (
    GABARIT_GENOIS,
    GABARIT_GV_FULLBATTEN,
    GABARIT_JADE,
    GABARITS_EMBARQUES,
    GabaritInconnu,
)
from seamtech_search.fiches.modeles import FicheExtraite
from seamtech_search.lexique import normaliser_terme

LARGEUR, HAUTEUR = 595.0, 842.0  # A4 portrait, comme les fiches réelles


def _ecrire_pdf(chemin: Path, lignes: list[tuple[float, list[tuple[float, str]]]]) -> Path:
    """Écrit un PDF une page : ``lignes`` = [(top, [(x, texte), …]), …].

    Les positions reproduisent l'organisation du format atelier (top = distance
    depuis le haut de page, x = origine gauche du fragment)."""
    pdf = canvas.Canvas(str(chemin), pagesize=(LARGEUR, HAUTEUR))
    for top, fragments in lignes:
        for x, texte in fragments:
            pdf.drawString(x, HAUTEUR - top, texte)
    pdf.save()
    return chemin


# ---------------------------------------------------------------------------
# Feuilles synthétiques (valeurs fictives)
# ---------------------------------------------------------------------------


def _remplacer(lignes: list, top: float, nouvelle: tuple) -> None:
    """Remplace la ligne placée à ``top`` (repère stable, pas d'indice)."""
    for indice, (top_ligne, _fragments) in enumerate(lignes):
        if abs(top_ligne - top) < 0.1:
            lignes[indice] = nouvelle
            return
    raise AssertionError(f"ligne à top={top} introuvable")


def _fiche_jade(tmp_path: Path, *, gennaker: bool = False, genois: bool = False) -> Path:
    """Fiche atelier « GSE » : en-tête colonnes, D-NP, cotes, BDF, ancrages."""
    lignes: list[tuple[float, list[tuple[float, str]]]] = [
        (19.8, [(133, "FICHE DE FABRICATION")]),
        # Variante « N° Commande 310101 AA » (libellé puis référence).
        (44.8, [(343, "N° Commande 310101 AA")]),
        # En-tête en colonnes — le bateau occupe sa colonne.
        (
            57.1,
            [
                (15, "CLIENT"),
                (70, "ATELIER"),
                (126, "DEMO"),
                (200, "BATEAU"),
                (273, "DEMO 31"),
                (343, "TYPE DE VOILE"),
                (506, "GSE"),
            ],
        ),
        (
            81.9,
            [
                (15, "DATE 01.01.2031"),
                (200, "D"),
                (212, "7,45"),
                (240, "NP"),
                (273, "SURFACE 30,00 M²"),
                (451, "Expédition 11.01.2031"),
            ],
        ),
        (105.9, [(15, "GUINDANT10,50")]),
        (118.2, [(70, "BDF a plat 60 X 270 g")]),
        (130.6, [(70, "RALINGUE Jonc 5 mm DEMO")]),
        (167.7, [(15, "CHUTE 9,40 à la corde")]),
        (180.0, [(70, "BDF a plat NERF")]),
        (192.4, [(70, "BDF pliée 80 x300g Diametre 4 pré-étiré")]),
        (204.7, [(70, "BDF decalée cleat DEMO")]),
        (241.8, [(15, "BORDURE 5,20 à la corde")]),
        (254.2, [(70, "BDF a plat NERF")]),
        (266.5, [(70, "BDF pliée 80 x 300 g DEMO")]),
        (326.1, [(15, "RENFORTS Dessus DEMO et dessous 2 ép DEMO")]),
        (365.4, [(15, "POINT D'ANCRAGE"), (400, "PROTECTION")]),
        (390.1, [(70, "AMURE SANGLE 30 epaisse DEMO")]),
        (414.9, [(70, "ECOUTE Œillet DEMO 25 SANGLE 25 Epaisse")]),
        (439.6, [(70, "DRISSE SANGLE 30 epaisse DEMO")]),
        (464.3, [(70, "RIS CHUTE SANGLE")]),
        (489.0, [(70, "RIS GT SANGLE")]),
        (553.0, [(15, "Coutures 2 Zig Zag 6 temps, FIL 138 blanc DEMO")]),
        (563.1, [(15, "DIVERS FIL 92 blanc DEMO")]),
        (629.2, [(15, "PREPARATION renf X 1 FABRICATION MACHINE")]),
        (717.0, [(15, "RENFORTS + UV FINITIONS")]),
        (793.8, [(15, "TEMPS TOTAL=")]),
    ]
    if gennaker:
        # Variante gennaker : « Commande N° » inversé, bateau débordant sur la
        # ligne suivante (reconstruction fusionnée), matière sur deux lignes.
        _remplacer(lignes, 44.8, (44.8, [(339, "Commande N° 310102 BB")]))
        _remplacer(
            lignes,
            57.1,
            (
                57.1,
                [
                    (11, "CLIENT"),
                    (66, "ATELIER"),
                    (122, "DEMO"),
                    (196, "BATEAU"),
                    (339, "TYPE DE VOILE"),
                    (503, "GENNAKER"),
                    (578, "C1"),
                ],
            ),
        )
        lignes.insert(3, (58.7, [(269, "DEMO 32")]))
        _remplacer(
            lignes,
            81.9,
            (
                78.9,
                [
                    (11, "DATE 02.02.2031"),
                    (269, "SURFACE 40,40 M²"),
                    (406, "Date Expédition 12.02.2031"),
                ],
            ),
        )
        lignes.insert(5, (80.4, [(196, "DEMO 210")]))
        lignes.insert(6, (90.3, [(196, "VERT")]))
        _remplacer(lignes, 105.9, (103.6, [(11, "GUINDANT12,90")]))
    if genois:
        # Variante génois (cas REF-004 réel) : le gabarit JADE doit couvrir ce
        # document AUSSI — il porte la même mise en page atelier.
        _remplacer(
            lignes,
            57.1,
            (
                57.1,
                [
                    (9, "CLIENT"),
                    (64, "ATELIER"),
                    (120, "DEMO"),
                    (200, "BATEAU"),
                    (273, "DEMO 33"),
                    (343, "TYPE DE VOILE"),
                    (507, "GENOIS"),
                ],
            ),
        )
    return _ecrire_pdf(tmp_path / ("fiche-atelier-%s.pdf" % ("gennaker" if gennaker else "genois" if genois else "gse")), lignes)


def _fiche_gv(tmp_path: Path) -> Path:
    """Fiche grand-voile fullbatten : sections RIS, LATTES, goussets, bôme."""
    lignes: list[tuple[float, list[tuple[float, str]]]] = [
        (19.8, [(129, "FICHE DE FABRICATION")]),
        (44.8, [(269, "Commande : 310103 CC")]),
        (
            57.1,
            [
                (11, "CLIENT"),
                (66, "ATELIER"),
                (122, "DEMO"),
                (196, "BATEAU"),
                (269, "DEMO 39"),
                (339, "TYPE DE VOILE"),
                (504, "GV Fullbatten"),
            ],
        ),
        (
            81.9,
            [
                (11, "DATE 03.03.2031"),
                (196, "344/394"),
                (269, "Surf = 45 m2"),
                (408, "Expédition: 13.03.203"),
            ],
        ),
        (92.1, [(196, "TISSU DEMO RADIAL")]),
        (103.6, [(11, "RIS"), (66, "Ris 1 = 4,98 long"), (196, "Ris 2 = 4,44 long"), (339, "Ris 3 = 3,90 long")]),
        (115.9, [(66, "BDF 80 x 300g et renforts DEMO"), (288, "BDF pliée 80 œillets DEMO")]),
        (128.3, [(11, "LATTES Plates 20 x 9 mm type OR FULLBATTEN Pas de coulisseaux DEMO")]),
        (153.0, [(66, "L7 = L4 = 4,59 SANGLE RAGAGE oui"), (339, "recto/verso")]),
        (165.4, [(66, "L6 = L3 = 3,65 L1 = 1,40 Haut sur goussets")]),
        (177.7, [(66, "L5 = L2 = 2,58 lattes")]),
        (214.8, [(11, "CHUTE 15,76 corde")]),
        (227.2, [(66, "BDF a plat NERF RETOUR au Guindant")]),
        (301.3, [(11, "BORDURE 5,40 corde")]),
        (313.7, [(66, "BDF a plat 60 x 280g NERF")]),
        (388.6, [(11, "GUINDANT14,80 corde RETOUR GT OUI NERF")]),
        (400.9, [(66, "BDF a plat 70 x 280g Diametre 4 mm DEMO")]),
        (413.3, [(66, "BDF pliée 100 x 300g cleat DEMO")]),
        (450.4, [(15, "POINT D'ANCRAGE"), (400, "PROTECTION")]),
        (475.1, [(66, "AMURE DEMO 30x6 3 SANGLES 20 Epaisse surliures main")]),
        (499.8, [(66, "ECOUTE DEMO 35 SANGLE 25 épaisse SANGLE DEMO")]),
        (524.5, [(66, "DRISSE DEMO 4mm épaisseur SANGLES 30 épaisse")]),
        (512.2, [(400, "Bôme Diam 50 cm")]),
        (549.3, [(66, "3 ris RIS CHUTE DEMO 35 Non Sanglé DEMO")]),
        (573.9, [(66, "3 ris RIS DEMO 25 Non Sanglé")]),
        (622.0, [(15, "PREPARATION FABRICATION MACHINE")]),
        (756.9, [(15, "TEMPS TOTAL=")]),
        (770.2, [(15, "Zigzag 6temps. FIL 138 avec traitement UV DEMO")]),
        (782.5, [(15, "DIVERS Ris DEMO Ris1 Normal, Ris2 Plus long 5 cm")]),
    ]
    return _ecrire_pdf(tmp_path / "fiche-atelier-gv.pdf", lignes)


def _plan_synthetique(tmp_path: Path) -> Path:
    """Plan de coupe mono-page : du vocabulaire de plan, aucun libellé fiche."""
    return _ecrire_pdf(
        tmp_path / "plan-synthetique.pdf",
        [
            (30.0, [(100, "PLAN DE COUPE — DEMO")]),
            (60.0, [(100, "Material: DEMO-LAMINATE")]),
            (90.0, [(100, "Echelle 1:20 — mise en plan DEMO")]),
            (120.0, [(100, "Contour de voile — tracé DEMO")]),
        ],
    )


@pytest.fixture(scope="module")
def fiche_jade(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _fiche_jade(tmp_path_factory.mktemp("jade-gse"))


@pytest.fixture(scope="module")
def fiche_gennaker(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _fiche_jade(tmp_path_factory.mktemp("jade-gennaker"), gennaker=True)


@pytest.fixture(scope="module")
def fiche_genois_atelier(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _fiche_jade(tmp_path_factory.mktemp("jade-genois"), genois=True)


@pytest.fixture(scope="module")
def fiche_gv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _fiche_gv(tmp_path_factory.mktemp("jade-gv"))


def _extraire(chemin: Path) -> FicheExtraite:
    return extraire_fiche(chemin, gabarits=list(GABARITS_EMBARQUES))


# ---------------------------------------------------------------------------
# 1. Détection : les gabarits du corpus reconnaissent leurs structures
# ---------------------------------------------------------------------------


class TestDetection:
    def test_fiche_gse_reconnue_jade(self, fiche_jade: Path) -> None:
        assert _extraire(fiche_jade).gabarit_code == "FICHE_JADE_V1"

    def test_fiche_gennaker_reconnue_jade(self, fiche_gennaker: Path) -> None:
        assert _extraire(fiche_gennaker).gabarit_code == "FICHE_JADE_V1"

    def test_fiche_genois_atelier_reconnue_jade(self, fiche_genois_atelier: Path) -> None:
        # Le génois du format atelier relève du gabarit JADE (même mise en
        # page) ; le gabarit historique FICHE_GENOIS_V1 reste attribué à la
        # fiche mono-colonne de démonstration (test ci-dessous).
        assert _extraire(fiche_genois_atelier).gabarit_code == "FICHE_JADE_V1"

    def test_fiche_gv_reconnue_fullbatten(self, fiche_gv: Path) -> None:
        assert _extraire(fiche_gv).gabarit_code == "FICHE_GV_FULLBATTEN_V1"

    def test_ancres_discriminantes(self) -> None:
        # Le texte du 7792 (portant) et du génois de démonstration ne doivent
        # PAS être capturés par les gabarits atelier : « fiche de fabrication »
        # n'est PAS une ancre JADE (libellé générique à toutes les fiches —
        # une seule occurrence capturerait un document hors gabarit).
        texte_portant = normaliser_terme(
            "fiche de fabrication voile de portant spi asymétrique spinnaker"
        )
        assert GABARIT_JADE.score_detection(texte_portant) == 0
        assert GABARIT_GV_FULLBATTEN.score_detection(texte_portant) == 0
        texte_genois = normaliser_terme("fiche de fabrication - genois navire : demo 2000")
        assert GABARIT_GV_FULLBATTEN.score_detection(texte_genois) == 0
        assert GABARIT_JADE.score_detection(texte_genois) == 0
        # Une fiche atelier porte les 4 ancres JADE, toutes discriminantes.
        texte_jade = normaliser_terme(
            "fiche de fabrication type de voile point d'ancrage bande de visu expédition"
        )
        assert GABARIT_JADE.score_detection(texte_jade) == 4

    def test_gabarits_historiques_non_regresses(self) -> None:
        racine = Path(__file__).resolve().parent.parent
        f7792 = _extraire(racine / "sample_data" / "CLIENT-7792-SO" / "fiche-7792-SO_ffab.pdf")
        assert f7792.gabarit_code == "FICHE_PORTANT_V1" and f7792.gabarit_version == 2
        fgenois = _extraire(racine / "sample_data" / "CLIENT-GENOA" / "fiche-genois.pdf")
        assert fgenois.gabarit_code == "FICHE_GENOIS_V1"
        assert fgenois.code == "0901-MM" and fgenois.client_nom == "Moreau"


# ---------------------------------------------------------------------------
# 2. Champs proposés par le gabarit JADE (propositions machine, jamais validées)
# ---------------------------------------------------------------------------


class TestChampsJade:
    def test_entete(self, fiche_jade: Path) -> None:
        fiche = _extraire(fiche_jade)
        assert fiche.titre == "FICHE DE FABRICATION"
        assert fiche.code == "310101 AA"
        assert fiche.client_nom == "ATELIER DEMO"
        assert fiche.bateau_nom == "DEMO 31"
        assert fiche.type_voile_libelle == "GSE"  # acronyme inconnu : conservé tel quel
        assert fiche.gamme is None
        assert fiche.date_edition == "2031-01-01"

    def test_cotes_et_surface(self, fiche_jade: Path) -> None:
        fiche = _extraire(fiche_jade)
        finie = next(c for c in fiche.cotes if c.jeu == "finie")
        assert (finie.slu_m, finie.sle_m, finie.sf_m, finie.spa_m2) == (10.5, 9.4, 5.2, 30.0)

    def test_tissu_absent_sans_zone_matiere(self, fiche_jade: Path) -> None:
        # Pas de matière sous la ligne DATE (uniquement la diagonale D/NP) :
        # le champ reste absent, jamais inventé.
        fiche = _extraire(fiche_jade)
        assert fiche.tissu_texte is None
        assert fiche.materiaux == []

    def test_galons_bdf_par_bord(self, fiche_jade: Path) -> None:
        fiche = _extraire(fiche_jade)
        # Une ligne fiche_galon par bande (contrainte UNIQUE id_fiche+bande) :
        # les bandes de force multiples d'un même bord vivent en TRACES.
        assert {g.bande for g in fiche.galons} == {"guindant", "chute", "bordure"}
        assert len(fiche.galons) == 3
        cles = {c.champ for g in fiche.galons for c in g.champs}
        assert cles == {
            "galon.guindant.a_plat",
            "galon.chute.a_plat",
            "galon.chute.pliee",
            "galon.chute.decalee",
            "galon.bordure.a_plat",
            "galon.bordure.pliee",
        }
        # Chaque trace reste rattachée à la ligne de SA bande.
        for g in fiche.galons:
            assert all(c.champ.startswith(f"galon.{g.bande}.") for c in g.champs)
        # Sans unité explicite : aucune valeur numérique convertie (RG16 honnête).
        for galon in fiche.galons:
            assert galon.largeur_mm is None and galon.grammage_g_m2 is None
            assert all(c.valeur_brute and c.zone is not None for c in galon.champs)

    def test_finitions_points_ancrage(self, fiche_jade: Path) -> None:
        fiche = _extraire(fiche_jade)
        postes = {f.poste: f for f in fiche.finitions}
        assert set(postes) == {"amure", "ecoute", "drisse", "ris chute", "ris gt"}
        assert postes["amure"].valeur_texte == "SANGLE 30 epaisse DEMO"
        assert postes["ecoute"].oeillet_type == "DEMO"
        assert postes["amure"].sangle is True
        # chaque finition porte sa trace avec zone
        for finition in fiche.finitions:
            assert finition.champs[0].zone is not None
            assert finition.champs[0].table_cible == "fiche_finition"

    def test_montage_zigzag_et_fil(self, fiche_jade: Path) -> None:
        fiche = _extraire(fiche_jade)
        assert fiche.montage_type == "2 zigzag 6 temps"
        assert fiche.montage_fil == "138"
        trace = next(c for c in fiche.champs if c.champ == "fiche.montage_type")
        assert trace.zone is not None

    def test_notes_divers_et_renforts(self, fiche_jade: Path) -> None:
        fiche = _extraire(fiche_jade)
        assert fiche.notes == "FIL 92 blanc DEMO"
        assert len(fiche.renforts) == 1
        assert fiche.renforts[0].description == "Dessous DEMO et dessous 2 ép DEMO".replace("Dessous", "Dessus")

    def test_variante_gennaker(self, fiche_gennaker: Path) -> None:
        fiche = _extraire(fiche_gennaker)
        assert fiche.code == "310102 BB"  # « Commande N° » inversé
        assert fiche.bateau_nom == "DEMO 32"  # bateau débordant, lu dans sa colonne
        assert fiche.type_voile_libelle == "Gennaker" and fiche.gamme == "C1"
        finie = next(c for c in fiche.cotes if c.jeu == "finie")
        assert finie.slu_m == 12.9 and finie.spa_m2 == 40.4
        assert fiche.tissu_texte == "DEMO 210 VERT"  # matière sur deux lignes
        assert fiche.materiaux[0].designation == "DEMO 210 VERT"
        assert fiche.materiaux[0].grammage_g_m2 is None  # pas d'unité g/m² → jamais converti

    def test_chaque_champ_porte_sa_trace_complete(self, fiche_jade: Path) -> None:
        fiche = _extraire(fiche_jade)
        champs = fiche.tous_les_champs()
        assert champs, "aucune proposition machine"
        for champ in champs:
            assert champ.valeur_normalisee is not None
            assert 0.0 < champ.confiance <= 0.99
            assert champ.methode == "gabarit"
        # les champs de tête localisés portent une zone (page 0)
        for cle in ("fiche.code", "fiche.client", "fiche.bateau", "fiche.designation"):
            trace = next(c for c in champs if c.champ == cle)
            assert trace.zone is not None, f"{cle} sans zone"

    def test_aucun_doublon_champ_rang(self, fiche_jade: Path) -> None:
        fiche = _extraire(fiche_jade)
        couples = [(c.champ, c.rang) for c in fiche.tous_les_champs()]
        assert len(couples) == len(set(couples))


# ---------------------------------------------------------------------------
# 3. Champs proposés par le gabarit GV fullbatten
# ---------------------------------------------------------------------------


class TestChampsGV:
    def test_entete_et_cotes(self, fiche_gv: Path) -> None:
        fiche = _extraire(fiche_gv)
        assert fiche.code == "310103 CC"  # variante « Commande : »
        assert fiche.bateau_nom == "DEMO 39"
        assert fiche.type_voile_libelle == "Grand-voile" and fiche.gamme == "Fullbatten"
        finie = next(c for c in fiche.cotes if c.jeu == "finie")
        assert (finie.slu_m, finie.sle_m, finie.sf_m, finie.spa_m2) == (14.8, 15.76, 5.4, 45.0)
        assert fiche.tissu_texte == "TISSU DEMO RADIAL"

    def test_sections_gv_en_mesures_libres(self, fiche_gv: Path) -> None:
        fiche = _extraire(fiche_gv)
        libres = {m.champ: m.valeur_normalisee for m in fiche.mesures_libres}
        assert libres["libre.ris_1"] == "4.98"
        assert libres["libre.ris_2"] == "4.44"
        # le moteur normalise la décimale (« 3,90 » → 3.9), comme sur le
        # corpus réel (« ris 3 = 3,90 long »)
        assert libres["libre.ris_3"] == "3.9"
        # « L7 = L4 = 4,59 » : les deux positions sont proposées (RG6).
        assert libres["libre.gousset_l7"] == "4.59"
        assert libres["libre.gousset_l4"] == "4.59"
        assert libres["libre.gousset_l6"] == "3.65"
        assert libres["libre.gousset_l3"] == "3.65"
        assert libres["libre.gousset_l1"] == "1.4"
        assert "libre.lattes" in libres
        assert "libre.bome" in libres

    def test_finitions_gv_avec_prefixe_nombre(self, fiche_gv: Path) -> None:
        fiche = _extraire(fiche_gv)
        postes = {f.poste for f in fiche.finitions}
        assert {"amure", "ecoute", "drisse", "ris", "ris chute"} <= postes
        non_sangle = next(f for f in fiche.finitions if f.poste == "ris")
        assert non_sangle.sangle is False  # « Non Sanglé » (espace ou tiret)
        # la négation est retirée de la valeur, pas collée dedans
        assert "non sangle" not in normaliser_terme(non_sangle.valeur_texte or "")
        sanglee = next(f for f in fiche.finitions if f.poste == "amure")
        assert sanglee.sangle is True  # « Sanglé » positif

    def test_montage_et_notes_gv(self, fiche_gv: Path) -> None:
        fiche = _extraire(fiche_gv)
        # « Zigzag 6temps. » sans nombre de points : le compte n'est pas
        # inventé, le montage reste « zigzag 6 temps ».
        assert fiche.montage_type == "zigzag 6 temps"
        assert fiche.montage_fil == "138"
        assert fiche.notes.startswith("Ris DEMO")


# ---------------------------------------------------------------------------
# 4. Classement automatique « technique » au parcours upload
# ---------------------------------------------------------------------------


class TestClassementAutomatique:
    def test_fiche_atelier_classee_technique(self, fiche_jade: Path, fiche_gv: Path) -> None:
        for chemin in (fiche_jade, fiche_gv):
            texte = extract_file(chemin).text
            assert classify_pdf_text(texte) == "technical_pdf", chemin.name

    def test_plan_synthetique_reste_un_plan(self, tmp_path: Path) -> None:
        plan = _plan_synthetique(tmp_path)
        assert classify_pdf_text(extract_file(plan).text) == "plan_pdf"

    def test_scan_folder_propose_le_candidat_technique(self, tmp_path: Path, fiche_jade: Path) -> None:
        from seamtech_search.config import AppConfig
        from seamtech_search.import_pipeline import scan_folder

        source = tmp_path / "dossier-demo"
        source.mkdir()
        (source / "fiche.pdf").write_bytes(fiche_jade.read_bytes())
        plan = _plan_synthetique(tmp_path)
        (source / "plan.pdf").write_bytes(plan.read_bytes())
        config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "data" / "search.db", min_free_bytes=0)
        resultat = scan_folder(source, config)
        candidats = {c["name"]: c for c in resultat["candidates"]}
        assert candidats["fiche.pdf"]["is_technical"] is True
        assert candidats["fiche.pdf"]["classification"] == "technical_pdf"
        assert candidats["plan.pdf"]["is_technical"] is False
        # le candidat technique est classé en tête (indice de classement 4.8)
        assert resultat["candidates"][0]["name"] == "fiche.pdf"


# ---------------------------------------------------------------------------
# 5. Filet de reprise RG6 — aucun gabarit ne reconnaît le PDF
# ---------------------------------------------------------------------------


class TestFiletReprise:
    def test_gabarit_inconnu_leve(self, fiche_jade: Path) -> None:
        with pytest.raises(GabaritInconnu, match="Aucun gabarit"):
            extraire_fiche(fiche_jade, gabarits=[GABARIT_GENOIS])

    def test_filet_degrade_sans_invention(self, fiche_jade: Path) -> None:
        fiche = extraire_avec_filet(fiche_jade, [GABARIT_GENOIS])
        assert fiche.gabarit_code is None
        assert "gabarit_inconnu" in {a.code for a in fiche.anomalies}
        # Le format atelier est en colonnes, sans paires « libellé : valeur » :
        # la reprise complète ne conserve RIEN (RG5) plutôt que de fabriquer
        # des champs — contrairement au génois mono-colonne qui garde ses
        # libellés connus en mesures libres (RG6, cf. test_detection_gabarit).
        assert fiche.mesures_libres == []
        assert fiche.cotes == []
        assert fiche.champs == []

    def test_extraire_avec_filet_reussit_sur_toutes_les_feuilles(self, fiche_jade: Path, fiche_gv: Path) -> None:
        # La voie protégée ne lève jamais : gabarit reconnu ou filet.
        for chemin in (fiche_jade, fiche_gv):
            fiche = extraire_avec_filet(chemin, list(GABARITS_EMBARQUES))
            assert fiche.gabarit_code is not None


class TestValeurNumeriqueLibre:
    """Régression du run CI 36488649403 : le dépôt de la fiche GV (REF-006)
    plantait en écriture car la persistance appliquait ``float()`` à la
    valeur normalisée de TOUTE mesure libre — or le handler GV stocke la
    ligne LATTES complète en texte. La colonne numérique ne doit recevoir
    un nombre QUE si la valeur en contient exactement un (RG16 : jamais
    d'invention, jamais d'exception)."""

    def test_ligne_descriptive_multi_nombres(self) -> None:
        from seamtech_search.fiches.persistance import _valeur_numerique_libre

        # Ligne réelle du corpus (vocabulaire générique de voilerie).
        ligne = "LATTES Plates 20 x 9 mm type OR FULLBATTEN Pas de coulisseaux cardan inox 3000 Sailman filetage M10"
        assert _valeur_numerique_libre(ligne) is None

    def test_nombre_avec_unite(self) -> None:
        from seamtech_search.fiches.persistance import _valeur_numerique_libre

        assert _valeur_numerique_libre("50 mm") == 50.0
        assert _valeur_numerique_libre("12,5") == 12.5
        assert _valeur_numerique_libre("12.5") == 12.5

    def test_valeurs_vides_et_texte_sans_nombre(self) -> None:
        from seamtech_search.fiches.persistance import _valeur_numerique_libre

        assert _valeur_numerique_libre(None) is None
        assert _valeur_numerique_libre("") is None
        assert _valeur_numerique_libre("Pas de coulisseaux") is None

    def test_nombre_seul_un_chiffre_isole(self) -> None:
        from seamtech_search.fiches.persistance import _valeur_numerique_libre

        # Un seul nombre présent : il est proposé tel quel (jamais inventé,
        # jamais converti) ; le texte intégral reste en valeur_texte.
        assert _valeur_numerique_libre("cardan inox 3000 Sailman") == 3000.0
