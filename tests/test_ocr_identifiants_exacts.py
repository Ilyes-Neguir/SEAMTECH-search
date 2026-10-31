"""Régression ouverte : identifiants alphanumériques sur PDF image synthétique.

Ce test est volontairement contraignant : il ne modifie pas l'alphabet de la
suite navigateur, ne substitue pas de texte attendu à l'OCR et n'utilise aucun
réessai. Les identifiants sont artificiels et ne proviennent d'aucun client.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from seamtech_search.extractors import extract_file
from tests.helpers_ocr_scan import gen_pdf_custom
from tests.test_ocr_qualification import _exiger_tesseract

pytestmark = pytest.mark.ocr_suite


@pytest.mark.parametrize(
    "terme",
    [
        "ZEPHYRINE0123456789ABCDEF0000",  # tout l'alphabet hexadécimal
        "ZEPHYRINE0B0B0B0B0001",
        "ZEPHYRINE0A7658930007",
        "ZEPHYRINE92C0E0D40017",
        "ZEPHYRINE085AE8C60023",
    ],
)
def test_ocr_identifiant_exact_et_original_preserve(tmp_path: Path, terme: str) -> None:
    tesseract = _exiger_tesseract()
    source = gen_pdf_custom(tmp_path, terme)
    avant = hashlib.sha256(source.read_bytes()).digest()

    resultat = extract_file(source, enable_ocr=True, tesseract_command=tesseract)

    assert hashlib.sha256(source.read_bytes()).digest() == avant
    assert resultat.status == "extracted"
    texte = re.sub(r"[^A-Z0-9]", "", resultat.text.upper())
    assert terme in texte, "l'identifiant imprimé n'a pas été reconnu exactement"
