"""Qualification OCR réelle (import → OCR → texte).

Sans tesseract : le test s'AUTO-IGNORE avec un motif explicite — ce n'est PAS
un PASS. La matrice de qualification consigne BLOQUÉ / NON EXÉCUTÉ.
Si SEAMTECH_OCR_OBLIGATOIRE=1, l'absence de tesseract fait ÉCHOUER le test.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from seamtech_search.extractors import extract_file
from seamtech_search.ocr.pipeline import ocriser_fichier

RACINE = Path(__file__).resolve().parent.parent
FIXTURES = RACINE / "tests" / "fixtures" / "ocr"
PDF_PROPRE = FIXTURES / "ocr_propre.pdf"
PDF_DEGRADE = FIXTURES / "ocr_degrade.pdf"
REF_PROPRE = FIXTURES / "reference_propre.txt"
PDF_TEXTE = RACINE / "sample_data" / "CLIENT-7792-SO" / "fiche-7792-SO_ffab.pdf"

pytestmark = pytest.mark.ocr_suite


def _exiger_tesseract() -> str:
    cmd = shutil.which("tesseract")
    if cmd:
        return cmd
    if os.environ.get("SEAMTECH_OCR_OBLIGATOIRE") == "1":
        pytest.fail("tesseract obligatoire (SEAMTECH_OCR_OBLIGATOIRE=1) mais absent du PATH")
    pytest.skip("tesseract absent — OCR réel non qualifié dans cet environnement")


def test_scan_propre_retrouve_les_mots_attendus() -> None:
    _exiger_tesseract()
    assert PDF_PROPRE.is_file()
    rapport = ocriser_fichier(PDF_PROPRE, langue="fra")
    assert rapport["nb_pages_ocerisees"] >= 1
    texte = " ".join(p.get("texte_ocr") or "" for p in rapport["pages"]).lower()
    attendus = (REF_PROPRE.read_text(encoding="utf-8") if REF_PROPRE.is_file() else "seamtech").split()
    mots = [m.lower() for m in attendus if len(m) >= 4][:8]
    retrouvés = [m for m in mots if m.lower() in texte]
    assert retrouvés, f"aucun mot de référence dans l'OCR : {texte[:200]!r}"
    taux = len(retrouvés) / max(1, len(mots))
    assert taux >= 0.5, f"taux trop bas {taux:.2f} mots={mots} texte={texte[:300]!r}"


def test_original_preserve_pendant_ocr(tmp_path: Path) -> None:
    _exiger_tesseract()
    import hashlib

    source = PDF_PROPRE
    avant = hashlib.sha256(source.read_bytes()).hexdigest()
    mtime = source.stat().st_mtime
    ocriser_fichier(source, langue="fra")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == avant
    assert source.stat().st_mtime == mtime


def test_pdf_texte_natif_n_est_pas_ocerise() -> None:
    _exiger_tesseract()
    if not PDF_TEXTE.is_file():
        pytest.skip("fiche texte native absente")
    rapport = ocriser_fichier(PDF_TEXTE, langue="fra")
    assert rapport["nb_pages_ignorees_texte_natif"] >= 1
    assert rapport["nb_pages_ocerisees"] == 0


def test_extract_file_ocr_relais_tesseract() -> None:
    _exiger_tesseract()
    resultat = extract_file(PDF_PROPRE, max_chars=20_000, enable_ocr=True)
    # Soit le PDF fixture a du texte natif, soit l'OCR le fournit.
    assert resultat.status in {"extracted", "unavailable"}
    if resultat.status == "extracted":
        assert resultat.text.strip()
    else:
        assert resultat.detail
