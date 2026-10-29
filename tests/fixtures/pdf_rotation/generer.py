"""Génère les PDF de référence « orientations » (0°, 90°, 180°, 270°).

Régénération déterministe :

    python3 tests/fixtures/pdf_rotation/generer.py

Les fichiers produits sont petits (< 10 Ko) et commités, pour que la suite
n'exige ni reportlab ni une police système au moment du test — et pour que le
contenu de référence soit identique d'une machine à l'autre.

Chaque PDF contient le même contenu logique, dont :

* des accents et des caractères que l'OCR et les expressions régulières de
  champs doivent retrouver (« Référence », « Élégance », « génois ») ;
* un tableau de cotes (cellules texte, pas d'image) ;
* un second page dans certains cas, pour vérifier que la reconstruction page
  par page ne mélange pas les contenus.

Trois familles, qui correspondent à trois situations réelles distinctes :

``page_XX.pdf``
    Contenu dessiné à l'horizontale, page déclarée pivotée par ``/Rotate``.
    C'est la forme que produisent les scanners et les logiciels de DAO quand
    la page est en paysage : à l'affichage, le texte est couché. Cas bloquant
    du rapport : l'extraction rendait un mot par ligne (90/270) ou le texte à
    l'envers (180).

``scan_paysage_90.pdf``
    Contenu dessiné couché (paysage) ET ``/Rotate`` qui le redresse : à
    l'affichage le texte est droit. L'extraction doit rester la même que pour
    ``page_00.pdf`` — c'est le cas « déjà lisible » qu'il est interdit de
    dégrader.

``couche_90.pdf`` / ``couche_180.pdf``
    Contenu dessiné couché, aucun ``/Rotate`` : à l'affichage le texte est
    couché (photo prise de travers, export d'un outil tiers). Le texte doit
    tout de même être extrait dans l'ordre de lecture.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

DOSSIER = Path(__file__).parent

LIGNES = [
    "FICHE TECHNIQUE VOILE",
    "Client : ATELIER DU VENT",
    "Matière : DACRON AZUR 380",
    "Référence : ROT-2026-001",
    "Élégance du génois — 12,50 m",
]

COTES = [
    ("Guindant", "12.50 m"),
    ("Bordure", "4.80 m"),
    ("Chute", "11.95 m"),
]


def _texte(c: canvas.Canvas, x: float, y: float) -> None:
    c.setFont("Helvetica", 12)
    ordonnee = y
    for ligne in LIGNES:
        c.drawString(x, ordonnee, ligne)
        ordonnee -= 18


def _tableau(c: canvas.Canvas, x: float, y: float) -> None:
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, "Cote")
    c.drawString(x + 90, y, "Valeur")
    c.setFont("Helvetica", 11)
    ordonnee = y - 16
    for cote, valeur in COTES:
        c.drawString(x, ordonnee, cote)
        c.drawString(x + 90, ordonnee, valeur)
        ordonnee -= 16


def _poser_rotation(chemin: Path, angle: int) -> None:
    """Déclare ``/Rotate = angle`` sans toucher au contenu (ce que fait un scanner."""
    from pypdf import PdfReader, PdfWriter

    lecteur = PdfReader(str(chemin))
    ecrivain = PdfWriter()
    for page in lecteur.pages:
        if angle % 360:
            page.rotate(angle % 360)
        ecrivain.add_page(page)
    with chemin.open("wb") as flux:
        ecrivain.write(flux)


def page_avec_rotation(chemin: Path, angle: int) -> Path:
    """Contenu horizontal, page déclarée pivotée de ``angle`` degrés."""
    c = canvas.Canvas(str(chemin), pagesize=A4)
    largeur, hauteur = A4
    _texte(c, 40 * mm, hauteur - 30 * mm)
    _tableau(c, 40 * mm, hauteur - 120 * mm)
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(40 * mm, 25 * mm, "SEAMTECH Search — échantillon synthétique, aucune donnée client")
    c.showPage()

    # Deuxième page : contenu distinct, même rotation, pour vérifier que la
    # reconstruction ne mélange pas les pages.
    _texte(c, 40 * mm, hauteur - 40 * mm)
    c.setFont("Helvetica", 12)
    c.drawString(40 * mm, hauteur - 150 * mm, "Page 2 — annexe de mesure")
    c.showPage()
    c.save()
    _poser_rotation(chemin, angle)
    return chemin


def scan_paysage(chemin: Path, angle_contenu: int = 90) -> Path:
    """Contenu dessiné couché puis ``/Rotate`` qui le redresse à l'affichage.

    Résultat : le document est DÉJÀ lisible pour un humain (texte droit à
    l'écran) ; l'extraction doit donc être identique au cas non pivoté.
    """
    c = canvas.Canvas(str(chemin), pagesize=A4)
    largeur, hauteur = A4
    c.saveState()
    c.translate(largeur / 2, hauteur / 2)
    c.rotate(angle_contenu)
    c.translate(-largeur / 2, -hauteur / 2)
    c.setFont("Helvetica", 12)
    ordonnee = hauteur - 30 * mm
    for ligne in LIGNES:
        c.drawString(40 * mm, ordonnee, ligne)
        ordonnee -= 18
    c.restoreState()
    c.showPage()
    c.save()
    # Le contenu a été tourné de +90 (sens trigonométrique) : /Rotate 270 le
    # redresse à l'affichage.
    _poser_rotation(chemin, (360 - angle_contenu) % 360)
    return chemin


def couche_sans_rotate(chemin: Path, angle: int) -> Path:
    """Contenu dessiné couché, aucun ``/Rotate`` : le document s'affiche couché."""
    c = canvas.Canvas(str(chemin), pagesize=A4)
    largeur, hauteur = A4
    c.saveState()
    c.translate(largeur / 2, hauteur / 2)
    c.rotate(angle)
    c.translate(-largeur / 2, -hauteur / 2)
    _texte(c, 40 * mm, hauteur - 30 * mm)
    _tableau(c, 40 * mm, hauteur - 120 * mm)
    c.restoreState()
    c.showPage()
    c.save()
    return chemin


def generer(dossier: Path = DOSSIER) -> dict[str, Path]:
    dossier.mkdir(parents=True, exist_ok=True)
    produits: dict[str, Path] = {}
    for angle in (0, 90, 180, 270):
        produits[f"page_{angle:02d}"] = page_avec_rotation(dossier / f"page_{angle:02d}.pdf", angle)
    produits["scan_paysage_90"] = scan_paysage(dossier / "scan_paysage_90.pdf")
    produits["couche_90"] = couche_sans_rotate(dossier / "couche_90.pdf", 90)
    produits["couche_180"] = couche_sans_rotate(dossier / "couche_180.pdf", 180)
    return produits


if __name__ == "__main__":
    for nom, chemin in generer().items():
        print(f"{nom}: {chemin} ({chemin.stat().st_size} octets)")
