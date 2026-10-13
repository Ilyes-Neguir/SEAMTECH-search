"""Helper to generate real image-only PDFs without text layer for OCR validation.

These PDFs contain NO embedded text (pdfplumber returns empty), so search must
use OCR output. Terms are unique and absent from filenames/metadata to prove
OCR is used.

Usage:
- gen_pdf_fr(tmp_path) -> Path
- gen_pdf_en(tmp_path) -> Path
- gen_pdf_multi(tmp_path) -> Path
- gen_pdf_degrade(tmp_path) -> Path
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdf_canvas


def _get_font(size: int = 32):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except Exception:
        try:
            return ImageFont.truetype("DejaVuSans.ttf", size)
        except Exception:
            return ImageFont.load_default()


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
