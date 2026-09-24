"""Génère les échantillons scannés de test pour le Lot M.

Les PDF produits sont des scans (page image SANS couche texte) — le texte
natif est absent, donc l'étage 3 doit les océriser.

Régénération déterministe :
  python3 tests/fixtures/ocr/generate_fixtures.py

Dépendances :
  - Pillow (PIL) : déjà dans requirements.txt
  - reportlab : déjà dans requirements.txt
  - tesseract non requis pour la génération (seulement pour la mesure)

Si Pillow ou reportlab sont absents, le script documente la commande et
ne fait pas échouer la suite (RG14 : aucun appel réseau).

Les PDF générés sont petits (< 150 Ko chacun) et commités.
Le texte source utilisé pour fabriquer l'échantillon est CONNU du test
(pour mesurer la qualité) — voir reference_propre.txt et reference_degrade.txt.

Qualité attendue :
  - échantillon propre : taux de mots retrouvés >= 0,90
  - échantillon dégradé : mesuré et publié même si < 0,60 (signalé dans limites)
"""

from __future__ import annotations

import random
from pathlib import Path

# Textes de référence connus du test
REFERENCE_PROPRE = """SEAMTECH Search
Test OCR propre
Voile grand voile 123
Client Sailonet chantier Cruette
Reference 7792-SO
Bonjour le monde
SEAMTECH voile technique
12345"""

REFERENCE_DEGRADE = REFERENCE_PROPRE  # même texte, mais image dégradée

DOSSIER = Path(__file__).parent


def _creer_image_texte(texte: str, degrade: bool = False):
    """Crée une image blanche avec texte noir."""
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            f"Pillow manquant : {exc}. "
            "Installez-le via pip install Pillow (déjà dans requirements.txt) "
            "ou via apt-get install python3-pil."
        )

    # Image 1200x800 blanche — assez grande pour que tesseract lise bien
    # Pour le dégradé, on réduit à 900x600 pour limiter la taille PDF
    if degrade:
        largeur, hauteur = 900, 600
    else:
        largeur, hauteur = 1200, 800
    image = Image.new("RGB", (largeur, hauteur), "white")
    draw = ImageDraw.Draw(image)

    # Essaie une police truetype si disponible, sinon défaut
    try:
        # DejaVu est présent sur Debian
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if Path(font_path).exists():
            font = ImageFont.truetype(font_path, 32)
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    # Dessine le texte ligne par ligne
    x, y = 50, 50
    for ligne in texte.splitlines():
        draw.text((x, y), ligne, fill="black", font=font)
        y += 60

    if degrade:
        # Dégradations volontaires : bruit + léger flou + rotation
        # Bruit : pixels aléatoires gris
        random.seed(42)  # déterministe
        for _ in range(800):
            bx = random.randint(0, largeur - 1)
            by = random.randint(0, hauteur - 1)
            gris = random.randint(100, 200)
            draw.point((bx, by), fill=(gris, gris, gris))

        # Léger flou
        image = image.filter(ImageFilter.GaussianBlur(radius=1.2))

        # Rotation de 3 degrés (simule scan de travers)
        image = image.rotate(3, resample=Image.BICUBIC, fillcolor="white")

    return image


def _image_vers_pdf(image, pdf_path: Path, degrade: bool = False) -> None:
    """Embed une image PIL dans un PDF via reportlab."""
    try:
        from reportlab.lib.pagesizes import A4  # type: ignore
        from reportlab.pdfgen import canvas  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            f"reportlab manquant : {exc}. "
            "Installez-le via pip install reportlab (déjà dans requirements.txt)."
        )

    # Sauve image temporaire
    # Pour le propre : PNG (qualité max pour OCR)
    # Pour le dégradé : JPEG qualité 75 pour rester < 150 Ko (idéalement)
    if degrade:
        temp_img = pdf_path.with_suffix(".tmp.jpg")
        image.save(str(temp_img), "JPEG", quality=75, optimize=True)
    else:
        temp_img = pdf_path.with_suffix(".tmp.png")
        image.save(str(temp_img), "PNG")

    # Crée PDF A4 avec l'image
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    largeur_a4, hauteur_a4 = A4

    # Dessine l'image en conservant ratio, marges 20
    c.drawImage(str(temp_img), 20, 20, width=largeur_a4 - 40, height=hauteur_a4 - 40, preserveAspectRatio=True)
    c.showPage()
    c.save()

    # Nettoie temp
    try:
        temp_img.unlink()
    except OSError:
        pass


def generer() -> None:
    DOSSIER.mkdir(parents=True, exist_ok=True)

    # Références
    (DOSSIER / "reference_propre.txt").write_text(REFERENCE_PROPRE, encoding="utf-8")
    (DOSSIER / "reference_degrade.txt").write_text(REFERENCE_DEGRADE, encoding="utf-8")

    # Propre
    img_propre = _creer_image_texte(REFERENCE_PROPRE, degrade=False)
    pdf_propre = DOSSIER / "ocr_propre.pdf"
    _image_vers_pdf(img_propre, pdf_propre, degrade=False)
    taille_propre = pdf_propre.stat().st_size
    print(f"Généré {pdf_propre} : {taille_propre} octets (cible < 150 Ko)")

    # Dégradé
    img_degrade = _creer_image_texte(REFERENCE_DEGRADE, degrade=True)
    pdf_degrade = DOSSIER / "ocr_degrade.pdf"
    _image_vers_pdf(img_degrade, pdf_degrade, degrade=True)
    taille_degrade = pdf_degrade.stat().st_size
    print(f"Généré {pdf_degrade} : {taille_degrade} octets (cible < 150 Ko)")

    # Vérifie tailles
    for pdf_path in (pdf_propre, pdf_degrade):
        taille = pdf_path.stat().st_size
        if taille > 150 * 1024:
            print(f"ATTENTION : {pdf_path.name} fait {taille} octets > 150 Ko (exigence idéalement < 150 Ko)")

    # Vérifie que le texte natif est bien absent (étage 3 doit océriser)
    try:
        import pdfplumber  # type: ignore

        for pdf_path in (pdf_propre, pdf_degrade):
            with pdfplumber.open(str(pdf_path)) as pdf:
                textes = [p.extract_text() or "" for p in pdf.pages]
                total_chars = sum(len(t.strip()) for t in textes)
                print(f"{pdf_path.name} : texte natif extrait = {total_chars} caractères (attendu 0 pour scan)")
                if total_chars > 20:
                    print(f"  ATTENTION : texte natif présent ({total_chars} chars) — le PDF n'est pas un scan pur")
    except ImportError:
        print("pdfplumber absent : impossible de vérifier l'absence de texte natif (non bloquant)")


if __name__ == "__main__":
    generer()
