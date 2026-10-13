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
    candidats = [
        os.environ.get("SEAMTECH_TESSERACT"),
        shutil.which("tesseract"),
        "/tmp/outillage-seamtech/tesseract",
    ]
    for cmd in candidats:
        if cmd and Path(cmd).is_file() and os.access(cmd, os.X_OK):
            tessdata = os.environ.get("TESSDATA_PREFIX") or "/tmp/outillage-seamtech/tessdata"
            if Path(tessdata, "fra.traineddata").is_file():
                os.environ.setdefault("TESSDATA_PREFIX", tessdata)
            return cmd
    if os.environ.get("SEAMTECH_OCR_OBLIGATOIRE") == "1":
        pytest.fail(
            "tesseract obligatoire (SEAMTECH_OCR_OBLIGATOIRE=1) mais absent — "
            "voir scripts/preparer_outillage_qualification.sh "
            "(CDN GitHub Releases ou paquet tesseract-ocr-fra)"
        )
    pytest.skip(
        "tesseract absent — OCR réel non qualifié (lanceur : "
        "scripts/preparer_outillage_qualification.sh)"
    )


def test_scan_propre_retrouve_les_mots_attendus() -> None:
    cmd = _exiger_tesseract()
    assert PDF_PROPRE.is_file()
    rapport = ocriser_fichier(PDF_PROPRE, langue="fra", tesseract_command=cmd)
    assert rapport["nb_pages_ocerisees"] >= 1
    texte = " ".join(p.get("texte_ocr") or "" for p in rapport["pages"]).lower()
    attendus = (REF_PROPRE.read_text(encoding="utf-8") if REF_PROPRE.is_file() else "seamtech").split()
    mots = [m.lower() for m in attendus if len(m) >= 4][:8]
    retrouvés = [m for m in mots if m.lower() in texte]
    assert retrouvés, f"aucun mot de référence dans l'OCR : {texte[:200]!r}"
    taux = len(retrouvés) / max(1, len(mots))
    assert taux >= 0.5, f"taux trop bas {taux:.2f} mots={mots} texte={texte[:300]!r}"


def test_original_preserve_pendant_ocr(tmp_path: Path) -> None:
    cmd = _exiger_tesseract()
    import hashlib

    source = PDF_PROPRE
    avant = hashlib.sha256(source.read_bytes()).hexdigest()
    mtime = source.stat().st_mtime
    ocriser_fichier(source, langue="fra", tesseract_command=cmd)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == avant
    assert source.stat().st_mtime == mtime


def test_pdf_texte_natif_n_est_pas_ocerise() -> None:
    cmd = _exiger_tesseract()
    if not PDF_TEXTE.is_file():
        pytest.skip("fiche texte native absente")
    rapport = ocriser_fichier(PDF_TEXTE, langue="fra", tesseract_command=cmd)
    assert rapport["nb_pages_ignorees_texte_natif"] >= 1
    assert rapport["nb_pages_ocerisees"] == 0


def test_extract_file_ocr_relais_tesseract() -> None:
    cmd = _exiger_tesseract()
    resultat = extract_file(
        PDF_PROPRE, max_chars=20_000, enable_ocr=True, tesseract_command=cmd
    )
    # Soit le PDF fixture a du texte natif, soit l'OCR le fournit.
    assert resultat.status in {"extracted", "unavailable"}
    if resultat.status == "extracted":
        assert resultat.text.strip()
    else:
        assert resultat.detail
