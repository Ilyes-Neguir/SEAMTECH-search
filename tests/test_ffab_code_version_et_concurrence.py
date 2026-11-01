"""Deux défauts trouvés sur des dossiers d'affaire COMPLETS (29/10/2026).

Dossier ouvert : `/YACHET CLASSIC ST508` — huit fichiers, un sous-dossier
`TISSU`, **six PDF** : les planches SailPack (`ST 508.pdf`, `ST 508V2.pdf`),
les deux fiches de fabrication (`ST 508_ffab.pdf`, `ST 508V2_ffab.pdf`), le
cahier des charges client et un détail d'œillets sans couche texte.

1. **Code à suffixe de révision jamais lu.** Le motif de référence FFab
   s'arrêtait à une lettre après les chiffres (`ST 660b`, `ST449A`). Sur
   « … ST 508V2 » et « Fichier ST 508V2 (commande ST 508V2) », le mot
   `\\b` qui suivait échouait sur le « V » : la fiche sortait **sans code**,
   donc jugée « non viable » par le scanner, alors que le document est une
   vraie fiche de fabrication entièrement lisible.
2. **Deux fiches concurrentes dans un même dossier**, départagées en SILENCE.
   Le scanner choisit de façon déterministe (score, puis nom « …ffab », puis
   ordre alphabétique) et conserve l'autre en pièce jointe — mais rien ne le
   disait à l'opérateur. Le dépôt expose maintenant `autres_fiches`.

Les PDF de ce module sont synthétiques : seuls la mise en page et les libellés
génériques mesurés sont reproduits, avec des valeurs inventées.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen import canvas

from seamtech_search.fiches.depot import scanner_dossier
from seamtech_search.fiches.extraction import extraire_fiche
from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES

LARGEUR, HAUTEUR = 595.0, 842.0


def _ecrire(chemin: Path, cellules: list[tuple[float, float, str]]) -> Path:
    pdf = canvas.Canvas(str(chemin), pagesize=(LARGEUR, HAUTEUR))
    pdf.setFont("Helvetica", 7)
    for x, haut, texte in cellules:
        pdf.drawString(x, HAUTEUR - haut, texte)
    pdf.save()
    return chemin


def _fiche_ffab(chemin: Path, code: str) -> Path:
    """Fiche FFab complète, au code demandé (« ZZ 508 » ou « ZZ 508V2 »)."""
    return _ecrire(
        chemin,
        [
            (150.0, 100.0, 'Fiche de fabrication "Grand-voile"'),
            (
                20.0,
                121.0,
                "Grand-voile classique semi-lattée, gamme Ocean, pour Bateau Demo 45 "
                f"(45') de Client Demo (Commande #508 DEMO1234) {code}",
            ),
            (20.0, 143.0, "Découpe et fabrication Atelier Démo"),
            (330.0, 143.4, f"Fichier  {code} (commande {code}) dessiné par A. le 28 janvier 2031"),
            (40.0, 165.0, "Tissu(s) (Pas de carbon):"),
            (160.0, 165.4, "Newport 9.45 AP Blanc - White"),
            (560.0, 165.8, "Quantité : 1"),
            (40.0, 190.0, "Cotes (tissu):"),
            (40.0, 205.0, "Guindant Bordure Chute Têtière Surface Poids (tissu) MHW MTW MUW"),
            (40.0, 220.0, "10,70 m 5,28 m 11,63 m 5 cm 27,3 m² 13,2 kg 2,51 m 1,21 m 0,61 m"),
            (40.0, 240.0, "◄ Mesures voile finie ►"),
        ],
    )


# ---------------------------------------------------------------------------
# 1. Le suffixe de révision fait partie du code
# ---------------------------------------------------------------------------


def test_code_avec_suffixe_de_revision_est_lu(tmp_path) -> None:
    """« ST 508V2 » / « ZZ 508V2 » : le code entier, jamais tronqué."""
    fiche = extraire_fiche(_fiche_ffab(tmp_path / "v2.pdf", "ZZ 508V2"), gabarits=list(GABARITS_EMBARQUES))
    assert fiche.code == "ZZ 508V2"
    assert fiche.gabarit_code == "FICHE_GV_FFAB_V1"


def test_code_sans_suffixe_reste_inchange(tmp_path) -> None:
    fiche = extraire_fiche(_fiche_ffab(tmp_path / "v1.pdf", "ZZ 508"), gabarits=list(GABARITS_EMBARQUES))
    assert fiche.code == "ZZ 508"


def test_suffixe_lettre_seule_toujours_lu(tmp_path) -> None:
    """Contre-épreuve : « ZZ 660b » (une lettre) continue de passer."""
    fiche = extraire_fiche(_fiche_ffab(tmp_path / "b.pdf", "ZZ 660b"), gabarits=list(GABARITS_EMBARQUES))
    assert fiche.code == "ZZ 660B"


# ---------------------------------------------------------------------------
# 2. Deux fiches dans un dossier : choix déterministe, et DIT
# ---------------------------------------------------------------------------


def _dossier_complet(racine: Path) -> Path:
    dossier = racine / "ZZ 508"
    dossier.mkdir()
    _fiche_ffab(dossier / "ZZ 508_ffab.pdf", "ZZ 508")
    _fiche_ffab(dossier / "ZZ 508V2_ffab.pdf", "ZZ 508V2")
    _ecrire(dossier / "plan de coupe.pdf", [(40.0, 100.0, "Plan de coupe atelier")])
    (dossier / "1.dxf").write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    (dossier / "TISSU").mkdir()
    return dossier


def test_deux_fiches_concurrentes_sont_signalees(tmp_path) -> None:
    scan = scanner_dossier(_dossier_complet(tmp_path), list(GABARITS_EMBARQUES))
    assert scan.pdf_fiche is not None and scan.pdf_fiche.name == "ZZ 508_ffab.pdf"
    assert [chemin.name for chemin in scan.autres_fiches] == ["ZZ 508V2_ffab.pdf"]
    # La seconde fiche n'est ni supprimée ni ignorée : elle reste une pièce.
    noms_pieces = {piece.chemin.name for piece in scan.pieces}
    assert {"ZZ 508V2_ffab.pdf", "plan de coupe.pdf", "1.dxf"} <= noms_pieces


def test_dossier_avec_une_seule_fiche_ne_signale_rien(tmp_path) -> None:
    dossier = tmp_path / "ZZ 509"
    dossier.mkdir()
    _fiche_ffab(dossier / "ZZ 509_ffab.pdf", "ZZ 509")
    scan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
    assert scan.pdf_fiche is not None
    assert scan.autres_fiches == []


def test_le_choix_ne_depend_pas_de_l_ordre_du_systeme_de_fichiers(tmp_path) -> None:
    """Même dossier créé dans l'ordre inverse : même fiche retenue."""
    dossier = tmp_path / "ZZ 510"
    dossier.mkdir()
    _fiche_ffab(dossier / "ZZ 510V2_ffab.pdf", "ZZ 510V2")
    _fiche_ffab(dossier / "ZZ 510_ffab.pdf", "ZZ 510")
    scan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
    assert scan.pdf_fiche is not None and scan.pdf_fiche.name == "ZZ 510_ffab.pdf"
    assert [chemin.name for chemin in scan.autres_fiches] == ["ZZ 510V2_ffab.pdf"]


# ---------------------------------------------------------------------------
# 3. Sous-dossiers réels : leurs fichiers ne disparaissent pas
# ---------------------------------------------------------------------------


def test_fichiers_d_un_sous_dossier_sans_pdf_deviennent_des_pieces(tmp_path) -> None:
    """Forme mesurée : `/YACHET CLASSIC ST508/TISSU`, `…/PLC`, `…/toile`.

    Ces sous-dossiers ne portent aucun PDF : ils ne sont donc jamais un
    dossier de lot, et leurs fichiers n'étaient référencés par rien.
    """
    dossier = tmp_path / "ZZ 611"
    dossier.mkdir()
    _fiche_ffab(dossier / "ZZ 611_ffab.pdf", "ZZ 611")
    tissu = dossier / "TISSU"
    tissu.mkdir()
    (tissu / "ECHANTILLON.PLX").write_text("PLX", encoding="utf-8")
    (tissu / "photo.jpg").write_bytes(b"\xff\xd8\xff")
    scan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
    noms = {piece.chemin.name for piece in scan.pieces}
    assert {"ECHANTILLON.PLX", "photo.jpg"} <= noms


def test_sous_dossier_qui_porte_un_pdf_n_est_pas_compte_deux_fois(tmp_path) -> None:
    """Un sous-dossier avec PDF est un dossier de lot à part : pas de doublon."""
    dossier = tmp_path / "ZZ 612"
    dossier.mkdir()
    _fiche_ffab(dossier / "ZZ 612_ffab.pdf", "ZZ 612")
    sous = dossier / "AUTRE AFFAIRE"
    sous.mkdir()
    _fiche_ffab(sous / "ZZ 613_ffab.pdf", "ZZ 613")
    (sous / "1.dxf").write_text("0\nSECTION\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    scan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
    assert scan.pdf_fiche is not None and scan.pdf_fiche.name == "ZZ 612_ffab.pdf"
    assert {piece.chemin.name for piece in scan.pieces} == set()
