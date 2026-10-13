import io
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from seamtech_search.extractors import extract_file, extract_text
from seamtech_search.indexer import _build_fts_query


def test_text_and_structured_extensions_are_extractable(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"client": "CLIENT-123"}', encoding="utf-8")
    text = extract_text(path, max_chars=1_000)
    assert "CLIENT-123" in text


def test_ooxml_content_is_extractable(tmp_path: Path) -> None:
    path = tmp_path / "drawing.xlsx"
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", "<sst><t>CLIENT-456</t></sst>")
    assert "CLIENT-456" in extract_text(path, max_chars=1_000)


def test_oversized_files_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "large.txt"
    path.write_text("0123456789", encoding="utf-8")
    text = extract_text(path, max_chars=1_000, max_file_size_bytes=3)
    assert text.startswith("[extraction skipped:")


def test_structured_result_does_not_put_status_in_content(tmp_path: Path) -> None:
    path = tmp_path / "drawing.dwg"
    path.write_bytes(b"binary")

    result = extract_file(path, max_chars=1_000)

    assert result.status == "unavailable"
    assert result.detail == "unsupported file type"
    assert result.text == ""


@pytest.mark.ocr_suite
def test_pdf_scanne_ocr_absent_n_est_pas_un_succes(tmp_path: Path) -> None:
    """enable_ocr=true sans binaire : unavailable explicite, jamais extracted vide."""
    from pypdf import PdfWriter

    pdf = tmp_path / "scan.pdf"
    # PDF sans couche texte (page vide) → extraction native unavailable.
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(pdf)

    sans = extract_file(pdf, max_chars=1_000, enable_ocr=False)
    assert sans.status == "unavailable"

    avec = extract_file(
        pdf,
        max_chars=1_000,
        enable_ocr=True,
        tesseract_command="/bin/seamtech-tesseract-absent",
        ocrmypdf_command="/bin/seamtech-ocrmypdf-absent",
    )
    assert avec.status == "unavailable"
    assert "tesseract" in avec.detail.lower() or "ocr" in avec.detail.lower()
    assert avec.text == ""


def test_pdf_texte_et_xlsx_sans_regression_ocr(tmp_path: Path) -> None:
    pdf = tmp_path / "texte.pdf"
    tampon = io.BytesIO()
    c = canvas.Canvas(tampon, pagesize=A4)
    c.drawString(72, 720, "Grand voile 7792-SO")
    c.save()
    pdf.write_bytes(tampon.getvalue())
    resultat = extract_file(pdf, max_chars=2_000, enable_ocr=True)
    assert resultat.status == "extracted"
    assert "7792-SO" in resultat.text

    xlsx = tmp_path / "nomenclature.xlsx"
    with ZipFile(xlsx, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", "<sst><t>DACRON</t></sst>")
    assert "DACRON" in extract_file(xlsx, max_chars=1_000, enable_ocr=True).text


def test_optional_legacy_and_ocr_formats_are_transparent_when_disabled(tmp_path: Path) -> None:
    legacy = tmp_path / "old.doc"
    image = tmp_path / "drawing.png"
    legacy.write_bytes(b"legacy")
    image.write_bytes(b"image")

    legacy_result = extract_file(legacy, max_chars=1_000)
    image_result = extract_file(image, max_chars=1_000)

    assert legacy_result.status == "unavailable"
    assert legacy_result.detail == "legacy Office extraction is disabled"
    assert image_result.status == "unavailable"
    assert image_result.detail == "OCR is disabled"


def test_configured_external_parser_extracts_content(tmp_path: Path) -> None:
    path = tmp_path / "drawing.dwg"
    path.write_bytes(b"cad")

    result = extract_file(
        path,
        max_chars=1_000,
        external_extractors={".dwg": [sys.executable, "-c", "print('CAD CONTENT')"]},
    )

    assert result.status == "extracted"
    assert result.text.strip() == "CAD CONTENT"


def test_pdf_table_rows_are_not_duplicated_in_extracted_text(tmp_path: Path) -> None:
    """Audit 4f: extract_text() already yields the words inside table cells.

    Re-appending the same rows from extract_tables() stored every document
    twice over, inflating the index and letting field regexes double-match.
    Rows whose cells all appear in the page text must not be appended again.
    """
    pdf_path = tmp_path / "table.pdf"
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.drawString(72, 780, "FICHE TECHNIQUE")
    # Ruled 2x2 table so pdfplumber's default "lines" strategy detects it.
    y0, y1 = 700, 740
    x0, xm, x1 = 72, 200, 328
    for x in (x0, xm, x1):
        c.line(x, y0, x, y1)
    for y in (y0, (y0 + y1) / 2, y1):
        c.line(x0, y, x1, y)
    c.drawString(x0 + 8, y0 + 8, "UniqueCellAlpha")
    c.drawString(xm + 8, y0 + 8, "UniqueCellBeta")
    c.save()
    pdf_path.write_bytes(buffer.getvalue())

    result = extract_file(pdf_path, max_chars=20_000)

    assert result.status == "extracted"
    # The body line and each table cell appear exactly once: nothing doubled.
    assert result.text.count("FICHE TECHNIQUE") == 1
    assert result.text.count("UniqueCellAlpha") == 1
    assert result.text.count("UniqueCellBeta") == 1


def test_fts_query_quotes_user_tokens() -> None:
    query = _build_fts_query('CLIENT-123: "drawing" OR *')
    assert query == '"CLIENT"* OR "123"* OR "drawing"*'
