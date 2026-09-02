from pathlib import Path
import sys
from zipfile import ZipFile

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


def test_fts_query_quotes_user_tokens() -> None:
    query = _build_fts_query('CLIENT-123: "drawing" OR *')
    assert query == '"CLIENT"* OR "123"* OR "drawing"*'
