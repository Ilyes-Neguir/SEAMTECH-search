"""Parcours OCR réel (extracteurs). Sans tesseract : skip explicite, pas un PASS.

Le job CI `ocr` installe tesseract 5.x + fra. Un parcours import→index→recherche
exige aussi PostgreSQL : il se saute sans DSN (le job ocr n'a pas de PG).
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

from seamtech_search.extractors import extract_file

pytestmark = pytest.mark.ocr_suite

RACINE = Path(__file__).resolve().parent.parent
FIXTURES = RACINE / "tests" / "fixtures" / "ocr"
PDF_PROPRE = FIXTURES / "ocr_propre.pdf"
PDF_DEGRADE = FIXTURES / "ocr_degrade.pdf"
REF_PROPRE = FIXTURES / "reference_propre.txt"
PDF_TEXTE = RACINE / "sample_data" / "CLIENT-7792-SO" / "fiche-7792-SO_ffab.pdf"


def _tesseract() -> str:
    cmd = os.environ.get("SEAMTECH_TESSERACT") or shutil.which("tesseract")
    if cmd and Path(cmd).is_file():
        return cmd
    pytest.skip("tesseract absent")


def test_scan_fra_extrait_les_mots_de_reference() -> None:
    cmd = _tesseract()
    avant = hashlib.sha256(PDF_PROPRE.read_bytes()).hexdigest()
    resultat = extract_file(PDF_PROPRE, max_chars=20_000, enable_ocr=True, tesseract_command=cmd)
    assert hashlib.sha256(PDF_PROPRE.read_bytes()).hexdigest() == avant
    assert resultat.status == "extracted", resultat.detail
    ref = REF_PROPRE.read_text(encoding="utf-8") if REF_PROPRE.is_file() else ""
    mots = [m.lower() for m in ref.split() if len(m) >= 4][:8]
    texte = resultat.text.lower()
    hits = [m for m in mots if m in texte]
    assert hits, f"référence absente du texte OCR : {texte[:300]!r}"


def test_scan_degrade_n_est_pas_un_succes_vide() -> None:
    cmd = _tesseract()
    resultat = extract_file(PDF_DEGRADE, max_chars=20_000, enable_ocr=True, tesseract_command=cmd)
    assert resultat.status in {"extracted", "unavailable", "error"}
    if resultat.status == "extracted":
        assert resultat.text.strip()
    else:
        assert resultat.detail


def test_pdf_texte_natif_non_ocerise_regression() -> None:
    cmd = _tesseract()
    if not PDF_TEXTE.is_file():
        pytest.skip("fiche texte native absente")
    avant = hashlib.sha256(PDF_TEXTE.read_bytes()).hexdigest()
    resultat = extract_file(PDF_TEXTE, max_chars=20_000, enable_ocr=True, tesseract_command=cmd)
    assert hashlib.sha256(PDF_TEXTE.read_bytes()).hexdigest() == avant
    assert resultat.status == "extracted"
    assert "7792" in resultat.text or len(resultat.text) > 50


def test_xlsx_sans_regression_ocr() -> None:
    from zipfile import ZipFile

    cmd = _tesseract()
    xlsx = Path("/tmp/qualif-ocr-nomenclature.xlsx")
    with ZipFile(xlsx, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", "<sst><t>DACRON-OCR</t></sst>")
    resultat = extract_file(xlsx, max_chars=1000, enable_ocr=True, tesseract_command=cmd)
    assert "DACRON-OCR" in resultat.text


def test_pdf_invalide_erreur_visible(tmp_path: Path) -> None:
    cmd = _tesseract()
    mauvais = tmp_path / "pas.pdf"
    mauvais.write_bytes(b"ceci n'est pas un pdf")
    resultat = extract_file(mauvais, max_chars=1000, enable_ocr=True, tesseract_command=cmd)
    assert resultat.status in {"unavailable", "error"}
    assert resultat.detail
