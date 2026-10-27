"""Lot 10 — calibration GV « FFab » et choix robuste du PDF principal.

Tous les PDF ci-dessous sont SYNTHÉTIQUES (reportlab) : seuls les libellés
génériques et l'organisation de la famille documentaire sont reproduits.
Aucun nom, code, bateau ni mesure d'un document réel n'apparaît ici.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from seamtech_search.fiches import depot
from seamtech_search.fiches.extraction import extraire_fiche
from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES, GabaritDef, RegleChamp

LARGEUR, HAUTEUR = 842.0, 595.0  # A4 paysage : la ligne de titre est longue.


def _ecrire_pdf(chemin: Path, lignes: list[tuple[float, list[tuple[float, str]]]]) -> Path:
    pdf = canvas.Canvas(str(chemin), pagesize=(LARGEUR, HAUTEUR))
    pdf.setFont("Helvetica", 7)
    for top, fragments in lignes:
        for x, texte in fragments:
            pdf.drawString(x, HAUTEUR - top, texte)
    pdf.save()
    return chemin


def _fiche_ffab(tmp_path: Path, *, code_dans_titre: bool = True, nom: str = "gv-demo_ffab.pdf") -> Path:
    code = "ZZ 001" if code_dans_titre else "ZZ 002"
    titre = (
        'Fiche de fabrication "Grand-voile" Grand-voile classique semi-lattée '
        "gamme Démo, pour Bateau Démo 40 (40') de Client Démo "
        "(Commande #CMD-DEMO-001)"
    )
    if code_dans_titre:
        titre += f" {code}"
    lignes: list[tuple[float, list[tuple[float, str]]]] = [
        (30.0, [(20, titre)]),
        (
            55.0,
            [
                (
                    20,
                    "Découpe et fabrication Atelier Démo "
                    f"Fichier {code} (commande {code}) Dessiné par Démo le 1 janvier 2031",
                )
            ],
        ),
        (80.0, [(20, "Tissu(s) : Matière Démo 300 g/m²")]),
        (105.0, [(20, "Cotes (tissu)")]),
        (
            130.0,
            [
                (40, "Guindant"),
                (130, "Bordure"),
                (220, "Chute"),
                (310, "Têtière"),
                (400, "Surface"),
                (490, "Poids (tissu)"),
                (600, "MHW"),
                (670, "MTW"),
                (740, "MUW"),
            ],
        ),
        (
            155.0,
            [
                (40, "10,00 m"),
                (130, "4,00 m"),
                (220, "10,50 m"),
                (310, "0,25 m"),
                (400, "30,0 m²"),
                (490, "8 kg"),
                (600, "3,00"),
                (670, "2,00"),
                (740, "1,00"),
            ],
        ),
        (185.0, [(20, "Montage fil V138 : 2 zigzag 6 temps")]),
        (210.0, [(20, "4 lattes")]),
        (
            235.0,
            [
                (
                    20,
                    "Mesures voile finie : Boitier guindant Boitier chute "
                    "Coulisseaux de guindant Ris 1",
                )
            ],
        ),
    ]
    return _ecrire_pdf(tmp_path / nom, lignes)


def _extraire(chemin: Path):
    return extraire_fiche(chemin, gabarits=list(GABARITS_EMBARQUES))


class TestCalibrationGvFfab:
    def test_detection_et_ligne_de_titre_complete(self, tmp_path: Path) -> None:
        fiche = _extraire(_fiche_ffab(tmp_path))

        assert fiche.gabarit_code == "FICHE_GV_FFAB_V1"
        assert fiche.code == "ZZ 001"
        assert fiche.commande_numero == "CMD-DEMO-001"
        assert fiche.client_nom == "Client Démo"
        assert fiche.bateau_nom == "Bateau Démo 40"
        assert fiche.bateau_taille == "40'"
        assert fiche.type_voile_libelle == "Grand-voile"
        assert fiche.gamme is not None and "classique" in fiche.gamme

    def test_code_corroboré_par_la_ligne_fichier_du_document(self, tmp_path: Path) -> None:
        fiche = _extraire(_fiche_ffab(tmp_path, code_dans_titre=False, nom="gv-sans-code-titre.pdf"))

        assert fiche.gabarit_code == "FICHE_GV_FFAB_V1"
        assert fiche.code == "ZZ 002"

    def test_grille_cotes_tissu_et_largeurs_non_modelees(self, tmp_path: Path) -> None:
        fiche = _extraire(_fiche_ffab(tmp_path))
        finie = next(cotes for cotes in fiche.cotes if cotes.jeu == "finie")

        assert finie.slu_m == pytest.approx(10.0)
        assert finie.sf_m == pytest.approx(4.0)
        assert finie.sle_m == pytest.approx(10.5)
        assert finie.spa_m2 == pytest.approx(30.0)
        assert finie.poids_kg == pytest.approx(8.0)
        # 0,25 m converti dans l'unité métier de la colonne (cm), jamais 0,25 cm.
        assert finie.tetiere_cm == pytest.approx(25.0)

        libres = {mesure.champ: mesure.valeur_normalisee for mesure in fiche.mesures_libres}
        assert libres["libre.mhw"] == "3.0"
        assert libres["libre.mtw"] == "2.0"
        assert libres["libre.muw"] == "1.0"

    def test_tissu_montage_et_traces_localisees(self, tmp_path: Path) -> None:
        fiche = _extraire(_fiche_ffab(tmp_path))

        assert fiche.tissu_texte is not None and "Matière Démo" in fiche.tissu_texte
        assert fiche.montage_fil == "138"
        traces = {champ.champ: champ for champ in fiche.tous_les_champs()}
        for cle in ("fiche.code", "fiche.client", "fiche.bateau", "cotes.finie.slu_m"):
            assert traces[cle].zone is not None, f"{cle} sans zone"
            assert traces[cle].valeur_normalisee is not None
        couples = [(champ.champ, champ.rang) for champ in fiche.tous_les_champs()]
        assert len(couples) == len(set(couples))


def _gabarit_haut_score_sans_code() -> GabaritDef:
    return GabaritDef(
        code="TEST_HAUT_SCORE_V1",
        version=1,
        description="Gabarit synthétique : beaucoup d'ancres, aucun code.",
        ancres_detection=["alpha", "beta", "gamma"],
        champs=[
            RegleChamp(cible="fiche.montage_type", ancres=["montage"], type="texte"),
        ],
    )


def _gabarit_viable_bas_score() -> GabaritDef:
    return GabaritDef(
        code="TEST_Viable_V1",
        version=1,
        description="Gabarit synthétique : code + identité + type.",
        ancres_detection=["delta"],
        champs=[
            RegleChamp(cible="fiche.code", ancres=["code fiche"], type="texte", obligatoire=True),
            RegleChamp(cible="fiche.client", ancres=["client"], type="texte", traitement="client"),
            RegleChamp(cible="fiche.designation", ancres=["désignation"], type="texte", traitement="designation"),
        ],
    )


def _pdf_haut_score(chemin: Path) -> Path:
    return _ecrire_pdf(
        chemin,
        [
            (40.0, [(30, "Alpha Beta Gamma")]),
            (70.0, [(30, "Montage : zigzag démo")]),
        ],
    )


def _pdf_viable(chemin: Path, *, code: str = "DEMO-001") -> Path:
    return _ecrire_pdf(
        chemin,
        [
            (40.0, [(30, "Delta")]),
            (70.0, [(30, f"Code fiche : {code}")]),
            (100.0, [(30, "Client : Atelier Démo")]),
            (130.0, [(30, "Désignation : Grand-voile Démo")]),
        ],
    )


class TestChoixPdfPrincipal:
    def test_repli_sur_un_pdf_viable_malgre_un_score_superieur_sans_code(self, tmp_path: Path) -> None:
        dossier = tmp_path / "DOSSIER-CHUTE"
        dossier.mkdir()
        _pdf_haut_score(dossier / "a-document-bruyant.pdf")
        _pdf_viable(dossier / "z-fiche.pdf")

        plan = depot.scanner_dossier(
            dossier,
            [_gabarit_haut_score_sans_code(), _gabarit_viable_bas_score()],
        )

        assert plan.accepte
        assert plan.pdf_fiche is not None and plan.pdf_fiche.name == "z-fiche.pdf"
        assert plan.gabarit_code == "TEST_Viable_V1"
        assert plan.fiche_extraite is not None and plan.fiche_extraite.code == "DEMO-001"
        assert [piece.chemin.name for piece in plan.pieces] == ["a-document-bruyant.pdf"]

    def test_ffab_ne_sert_que_de_departage_a_preuves_egales(self, tmp_path: Path) -> None:
        dossier = tmp_path / "DOSSIER-FFab"
        dossier.mkdir()
        _pdf_viable(dossier / "a-autre.pdf")
        _pdf_viable(dossier / "z-document_ffab.pdf")

        plan = depot.scanner_dossier(dossier, [_gabarit_viable_bas_score()])

        assert plan.pdf_fiche is not None
        assert plan.pdf_fiche.name == "z-document_ffab.pdf"

    def test_un_code_seul_ne_suffit_pas_a_rendre_un_pdf_viable(self, tmp_path: Path) -> None:
        gabarit_code_seul = GabaritDef(
            code="TEST_CODE_SEUL_V1",
            version=1,
            description="Gabarit synthétique volontairement pauvre.",
            ancres_detection=["epsilon"],
            champs=[RegleChamp(cible="fiche.code", ancres=["code fiche"], type="texte")],
        )
        dossier = tmp_path / "DOSSIER-CODE-SEUL"
        dossier.mkdir()
        _ecrire_pdf(
            dossier / "code-seul.pdf",
            [(40.0, [(30, "Epsilon")]), (70.0, [(30, "Code fiche : DEMO-404")])],
        )

        plan = depot.scanner_dossier(dossier, [gabarit_code_seul])

        assert not plan.accepte
        assert plan.raison_refus is not None
        assert "aucun PDF candidat" in plan.raison_refus

    def test_scanner_volumineux_sans_hacher_les_pieces(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dossier = tmp_path / "DOSSIER-VOLUMINEUX"
        dossier.mkdir()
        _fiche_ffab(dossier, nom="fiche_ffab.pdf")
        for numero in range(120):
            (dossier / f"piece-{numero:03d}.bin").write_bytes(b"donnees-synthetiques" * 8)

        appels: list[Path] = []
        vraie_empreinte = depot.empreinte_fichier

        def empreinte_comptee(chemin: Path) -> str:
            appels.append(Path(chemin))
            return vraie_empreinte(chemin)

        monkeypatch.setattr(depot, "empreinte_fichier", empreinte_comptee)
        plan = depot.scanner_dossier(dossier, list(GABARITS_EMBARQUES))

        assert plan.accepte and len(plan.pieces) == 120
        assert appels == [], "le scan ne doit pas hacher les pièces avant de savoir si la fiche est viable"
        premiere = plan.pieces[0]
        attendu = hashlib.sha256(premiere.chemin.read_bytes()).hexdigest()
        assert premiere.empreinte_sha256 == attendu
        assert appels == [premiere.chemin]


class TestDecouverteRecursive:
    def test_tout_dossier_avec_pdf_direct_est_candidat(self, tmp_path: Path) -> None:
        racine = tmp_path / "VOILES-DEMO"
        candidats = [
            racine / "2031" / "catégorie" / "COMMANDE-A",
            racine / "COMMANDE-B" / "sous-ensemble",
            racine / "COMMANDE-C",
        ]
        for dossier in candidats:
            dossier.mkdir(parents=True)
            (dossier / "fiche.pdf").write_bytes(b"%PDF-synthetique")
        (racine / "sans-pdf").mkdir()
        (racine / "sans-pdf" / "notes.txt").write_text("démo", encoding="utf-8")
        cache = racine / ".cache"
        cache.mkdir()
        (cache / "ignore.pdf").write_bytes(b"%PDF-synthetique")

        trouves = depot.decouvrir_dossiers_candidats(racine)

        assert trouves == sorted(candidats, key=lambda chemin: str(chemin).casefold())
