"""Titre lu dans l'EN-TÊTE quand le gabarit n'en porte aucun (29/10/2026).

Mesuré sur `/VOILES 2026/ETAP 32S ST685/FFAB_gse234269.pdf` — fiche SAILONET
« FICHE FABRICATION / GENOIS / FOCS », ligne « TYPE GÉNOIS SUR ENROULEUR ».
Le gabarit générique génois n'a pas de règle de titre : la fiche partait en
validation avec ``titre = NULL`` alors que le type est imprimé.

Le PDF de test est synthétique (reportlab) : seuls les libellés mesurés sont
reproduits, les valeurs sont inventées — aucun document client dans Git.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen import canvas

from seamtech_search.fiches.extraction import _poser_titre_depuis_entete, extraire_fiche
from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES

LARGEUR, HAUTEUR = 595.0, 842.0


def _ecrire(chemin: Path, lignes: list[str]) -> Path:
    pdf = canvas.Canvas(str(chemin), pagesize=(LARGEUR, HAUTEUR))
    pdf.setFont("Helvetica", 7)
    for indice, texte in enumerate(lignes):
        pdf.drawString(30.0, HAUTEUR - 40.0 - indice * 16.0, texte)
    pdf.save()
    return chemin


def _fiche_sailonet(chemin: Path) -> Path:
    return _ecrire(
        chemin,
        [
            "SAILONET",
            "FICHE FABRICATION",
            "GENOIS / FOCS",
            "FICHE FAB SAILONET GÉNOIS / VOILE D'AVANT",
            "TYPE GÉNOIS SUR ENROULEURref STARVOILES GSE000001 BATEAU bateau client",
            "TISSU 1 280 AP TISSU 2 BANDE UV weathermax LT COULEUR 00000 blanc",
            "cotes guindant bordure chute têtière SURFACE poids (tissu) LP HHW HTW HUW",
        ],
    )


def test_titre_lu_dans_l_entete_quand_le_gabarit_n_en_porte_pas(tmp_path: Path) -> None:
    pdf = _fiche_sailonet(tmp_path / "FFAB_demo000001.pdf")
    fiche = extraire_fiche(pdf, gabarits=list(GABARITS_EMBARQUES))
    assert fiche.titre is not None, "le type imprimé ne doit plus être perdu"
    assert "genois" in fiche.titre.lower().replace("é", "e")
    trace = [c for c in fiche.champs if c.champ == "fiche.titre"]
    assert trace and trace[0].methode == "document", "la provenance du titre est tracée"
    assert "GENOIS" in trace[0].valeur_brute.upper(), "la ligne imprimée reste la preuve"


def test_aucun_titre_invente_sans_famille_imprimee(tmp_path: Path) -> None:
    """Sans nom de famille de voile en tête, rien n'est posé (jamais « Voile »)."""

    class _Ligne:
        def __init__(self, texte: str) -> None:
            self.mots = [type("M", (), {"texte": mot})() for mot in texte.split()]

    class _Page:
        numero = 0

        def __init__(self, texte: str) -> None:
            self.lignes = [_Ligne(texte)]

    class _Fiche:
        titre = None
        champs: list = []

    fiche = _Fiche()
    _poser_titre_depuis_entete(fiche, [_Page("ATTESTATION DE CONFORMITE 2026")])
    assert fiche.titre is None
    assert fiche.champs == []


def test_grille_de_cotes_sailonet_lue_dans_l_ordre_de_l_entete(tmp_path: Path) -> None:
    """En-tête « cotes guindant bordure chute têtière SURFACE poids » + valeurs.

    Mesuré sur le même document : la grille n'était lue par aucun gabarit, la
    fiche sortait sans aucune cote alors que les valeurs sont imprimées.
    """
    pdf = _ecrire(
        tmp_path / "FFAB_demo000002.pdf",
        [
            "SAILONET",
            "FICHE FABRICATION",
            "GENOIS / FOCS",
            "TYPE GÉNOIS SUR ENROULEUR",
            "cotes guindant bordure chute têtière SURFACE poids (tissu) LP HHW HTW HUW",
            "tissu 7.65 2.92 6.87 0.04 9.8 4",
        ],
    )
    fiche = extraire_fiche(pdf, gabarits=list(GABARITS_EMBARQUES))
    assert fiche.cotes, "la grille imprimée doit être lue"
    finie = fiche.cotes[0]
    assert (finie.slu_m, finie.sf_m, finie.sle_m) == (7.65, 2.92, 6.87)
    assert (finie.spa_m2, finie.tetiere_cm, finie.poids_kg) == (9.8, 4.0, 4.0)
