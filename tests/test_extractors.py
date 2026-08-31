from pathlib import Path
from zipfile import ZipFile

from seamtech_search.extractors import extract_text
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


def test_fts_query_quotes_user_tokens() -> None:
    query = _build_fts_query('CLIENT-123: "drawing" OR *')
    assert query == '"CLIENT"* OR "123"* OR "drawing"*'
