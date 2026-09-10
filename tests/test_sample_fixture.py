from pathlib import Path

from seamtech_search.anchors import classify_pdf_text
from seamtech_search.config import AppConfig
from seamtech_search.extractors import extract_file
from seamtech_search.import_pipeline import extract_structured_pdf, import_folder
from seamtech_search.indexer import SearchIndex


def test_sample_fixture_classification() -> None:
    fixture_path = Path("sample_data/CLIENT-123/fiche-technique.pdf")
    assert fixture_path.exists()
    extraction = extract_file(fixture_path)
    assert extraction.status == "extracted"
    assert classify_pdf_text(extraction.text) == "technical_pdf"


def test_sample_fixture_structured_extraction() -> None:
    fixture_path = Path("sample_data/CLIENT-123/fiche-technique.pdf")
    config = AppConfig(root_paths=[Path("sample_data")], min_free_bytes=0)
    data = extract_structured_pdf(fixture_path, config)

    assert data.extraction_status == "success"
    assert data.confidence >= 0.8
    assert data.reference == "REF-2026-CLIENT123"
    assert "Dacron" in (data.material or "")
    assert data.quantity == 2
    assert "Grand voile" in (data.description or "")
    assert data.dimensions.length == 12.5
    assert data.dimensions.width == 4.2
    assert data.dimensions.unit == "m"
    assert data.dimensions.length_mm == 12500.0
    assert data.dimensions.width_mm == 4200.0


def test_sample_fixture_end_to_end_import(tmp_path: Path) -> None:
    source_folder = Path("sample_data/CLIENT-123")
    config = AppConfig(
        root_paths=[Path("sample_data").resolve()],
        database_path=tmp_path / "search.db",
        min_free_bytes=0,
    )
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)

    result = import_folder(source_folder, config, index)

    assert result.status == "completed"
    assert result.technical_pdf is not None
    assert "fiche-technique.pdf" in result.technical_pdf
    assert result.data is not None
    assert result.data["reference"] == "REF-2026-CLIENT123"
    assert result.report_path is not None
    assert Path(result.report_path).exists()
    assert result.report_docx_path is not None
    assert Path(result.report_docx_path).exists()

    # Verify indexed in DB
    search_results = index.search("CLIENT123")
    assert len(search_results) >= 1
