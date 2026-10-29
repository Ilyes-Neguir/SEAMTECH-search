"""Helper to generate real image-only PDFs without text layer for OCR validation.

These PDFs contain NO embedded text (pdfplumber returns empty), so search must
use OCR output. Terms are unique and absent from filenames/metadata to prove
OCR is used.

Usage:
- gen_pdf_fr(tmp_path) -> Path
- gen_pdf_en(tmp_path) -> Path
- gen_pdf_multi(tmp_path) -> Path
- gen_pdf_degrade(tmp_path) -> Path
- gen_pdf_custom(tmp_path, terme, ...) -> Path
- terme_unique_ocr_sur(prefixe) -> str

Termes uniques et lisibilité OCR
--------------------------------
Un terme unique sert de preuve : s'il ressort de l'OCR, c'est bien CE document
qui a été océrisé puis indexé. Il doit donc être **lisible sans ambiguïté** par
tesseract. Un tirage hexadécimal contient « 0 », que tesseract lit « O » sur une
image de synthèse — d'où l'échec de CI du 28/10 (« GENOA0FFF… » océrisé
« GENOAOFFF… »). Le terme est désormais tiré dans ``ALPHABET_OCR_SUR`` :
majuscules sans I ni O, donc aucune paire 0/O, 1/I/l, 5/S, 8/B, 2/Z à confondre.
L'assertion du test reste stricte : aucune tolérance n'est ajoutée côté test.
"""

from __future__ import annotations

import hashlib
import random
import secrets
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdf_canvas

#: Polices candidates, dans l'ordre de préférence. La MONOSPACE d'abord :
#: mesuré le 28/10 sur ce dépôt, la police proportionnelle fait halluciner des
#: caractères à tesseract sur les termes techniques (mesure reproductible :
#: « GENOABMWFX » océrisé « GENOABMWEFX » en DejaVu Sans 32 px, correct en
#: DejaVu Sans Mono 32 px ; « …FXJ » océrisé « …FX] » en proportionnelle,
#: correct en monospace). L'écart de largeur entre glyphes d'une police
#: proportionnelle est ce que tesseract interprète comme un caractère absent.
POLICES_OCR = (
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 32),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 32),
    ("DejaVuSansMono.ttf", 32),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32),
    ("DejaVuSans.ttf", 32),
)


def _get_font(size: int = 32):
    """Police de rendu des échantillons : monospace si disponible.

    Une police à chasse fixe donne un pas régulier, seule condition pour que
    l'OCR ne « voit » pas de caractère là où il n'y a qu'un blanc de kerning.
    """
    for chemin, taille_defaut in POLICES_OCR:
        try:
            return ImageFont.truetype(chemin, size or taille_defaut)
        except Exception:
            continue
    return ImageFont.load_default()


#: Majuscules « sûres » pour l'OCR, mesurées et non supposées (28/10/2026) :
#:
#: * aucun chiffre — « 0 » est océrisé « O » de façon reproductible, y compris
#:   en monospace (« GENOA0FFF… » → « GENOAOFFF… », l'échec de CI du 28/10) ;
#: * ni ``I`` ni ``O`` — confusions 1/I et 0/O avec n'importe quel contexte ;
#: * ni ``J`` — mesuré : « QZMJNP » océrisé « QZMJNP » mais « …HJX… » océrisé
#:   « …HJIX… » et « …FXJ » océrisé « …FX] » selon le glyphe suivant ; le J
#:   n'est pas fiable sur une image de synthèse.
#:
#: 23 lettres, 23**10 ≈ 4,1e13 combinaisons : l'unicité n'est pas compromise.
ALPHABET_OCR_SUR = "ABCDEFGHKLMNPQRSTUVWXYZ"

#: Longueur du suffixe aléatoire : assez pour l'unicité, assez court pour tenir
#: sur une ligne à 32 px.
LONGUEUR_SUFFIXE_OCR = 10


def terme_unique_ocr_sur(prefixe: str = "TERME") -> str:
    """Terme unique fait de caractères qu'aucun OCR ne peut confondre.

    Préfixe nettoyé (majuscules, alphabet sûr uniquement) + suffixe aléatoire
    cryptographiquement tiré dans :data:`ALPHABET_OCR_SUR`.
    """
    propre = "".join(caractere for caractere in prefixe.upper() if caractere in ALPHABET_OCR_SUR)
    suffixe = "".join(secrets.choice(ALPHABET_OCR_SUR) for _ in range(LONGUEUR_SUFFIXE_OCR))
    return f"{propre or 'TERME'}{suffixe}"


def _create_image(text: str, dest: Path, degraded: bool = False, size=(1200, 800)) -> Path:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    font = _get_font(32)
    y = 60
    for line in text.splitlines():
        draw.text((40, y), line, fill="black", font=font)
        y += 50
    if degraded:
        for _ in range(5000):
            x = random.randint(0, size[0] - 1)
            yy = random.randint(0, size[1] - 1)
            draw.point((x, yy), fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))
        img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
        img = img.point(lambda p: int(p * 0.8 + 20))
    img.save(dest, "PNG")
    return dest


def _images_to_pdf(image_paths: list[Path], pdf_path: Path) -> Path:
    c = pdf_canvas.Canvas(str(pdf_path), pagesize=A4)
    w, h = A4
    for img_path in image_paths:
        c.drawImage(str(img_path), 20, 20, width=w - 40, height=h - 40, preserveAspectRatio=True)
        c.showPage()
    c.save()
    return pdf_path


def gen_pdf_custom(tmp_dir: Path, unique_term: str, lang: str = "fr", filename: str = "scan-custom.pdf") -> Path:
    """Image-only PDF with a caller-provided unique term (absent from filename/metadata)."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    if lang == "fr":
        text = (
            f"{unique_term}\n"
            "Voile d'artimon en Dacron azur\n"
            "Référence: FR-2026-001\n"
            "Client: Atelier du Vent\n"
            f"Ce document contient le terme unique {unique_term} pour validation OCR.\n"
            "Génois léger 6,60 m\n"
            "Élégance du génois"
        )
    else:
        text = (
            f"{unique_term}\n"
            "Atlantic workshop genoa sail\n"
            "Reference: EN-2026-002\n"
            "Client: Ocean Sails Ltd\n"
            f"Unique term {unique_term} for OCR validation.\n"
            "Dimension 6.6 m\n"
            "Elegance"
        )
    img = tmp_dir / f"{lang}_{unique_term[:8]}.png"
    _create_image(text, img, degraded=False)
    pdf = tmp_dir / filename
    _images_to_pdf([img], pdf)
    img.unlink(missing_ok=True)
    return pdf


def gen_pdf_fr(tmp_dir: Path) -> Path:
    """French with accents, unique term ZEPHYRINE_ÉLÉGANCE_42"""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    text = (
        "ZEPHYRINE_ÉLÉGANCE_42\n"
        "Voile d'artimon en Dacron azur\n"
        "Référence: FR-2026-001\n"
        "Client: Atelier du Vent\n"
        "Ce document contient le terme unique ZEPHYRINE_ÉLÉGANCE_42 pour validation OCR.\n"
        "Génois léger 6,60 m"
    )
    img = tmp_dir / "fr.png"
    _create_image(text, img, degraded=False)
    pdf = tmp_dir / "scan-fr.pdf"
    _images_to_pdf([img], pdf)
    img.unlink(missing_ok=True)
    return pdf


def gen_pdf_en(tmp_dir: Path) -> Path:
    """English, unique term QUXTRON_GALLOPER_99"""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    text = (
        "QUXTRON_GALLOPER_99\n"
        "Atlantic workshop genoa sail\n"
        "Reference: EN-2026-002\n"
        "Client: Ocean Sails Ltd\n"
        "Unique term QUXTRON_GALLOPER_99 for OCR validation.\n"
        "Dimension 6.6 m"
    )
    img = tmp_dir / "en.png"
    _create_image(text, img)
    pdf = tmp_dir / "scan-en.pdf"
    _images_to_pdf([img], pdf)
    img.unlink(missing_ok=True)
    return pdf


def gen_pdf_multi(tmp_dir: Path) -> Path:
    """Multipage, terms KRYPTONITE_MNEMOS_1/2/3"""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    terms = ["KRYPTONITE_MNEMOS_1", "KRYPTONITE_MNEMOS_2", "KRYPTONITE_MNEMOS_3"]
    imgs = []
    for i, term in enumerate(terms):
        txt = f"Page {i+1}\n{term}\nContenu technique voile {term}\nRéférence MULTI-{i+1}\nGénois {i+1}"
        img_path = tmp_dir / f"multi_{i}.png"
        _create_image(txt, img_path)
        imgs.append(img_path)
    pdf = tmp_dir / "scan-multi.pdf"
    _images_to_pdf(imgs, pdf)
    for p in imgs:
        p.unlink(missing_ok=True)
    return pdf


def gen_pdf_degrade(tmp_dir: Path) -> Path:
    """Degraded but readable, unique term BRUME_DEGRADEE_77"""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    text = (
        "BRUME_DEGRADEE_77\n"
        "Voile dégradée mais lisible\n"
        "Référence DEG-2026-077\n"
        "Terme unique BRUME_DEGRADEE_77 pour test OCR dégradé.\n"
        "Élégance du génois"
    )
    img = tmp_dir / "deg.png"
    _create_image(text, img, degraded=True)
    pdf = tmp_dir / "scan-degrade.pdf"
    _images_to_pdf([img], pdf)
    img.unlink(missing_ok=True)
    return pdf


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()
