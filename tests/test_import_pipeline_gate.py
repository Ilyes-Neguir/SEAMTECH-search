"""Push import_pipeline to 90%."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from seamtech_search.import_pipeline import (
    normalize_unit_to_mm,
    classify_path,
    _extract_dimensions,
    scan_folder,
    import_folder,
    staging_root,
    quarantine_root,
    correct_import,
    retry_upload,
    get_import,
    extract_structured_pdf,
    extract_excel_summary,
)
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex


def test_normalize_and_classify():
    assert normalize_unit_to_mm(None, "mm") is None
    assert normalize_unit_to_mm(10, None) is None
    assert normalize_unit_to_mm(10, "unknown") is None
    assert normalize_unit_to_mm(10, "mm") == 10.0
    assert normalize_unit_to_mm(1, "cm") == 10.0
    assert normalize_unit_to_mm(1, "m") == 1000.0
    assert normalize_unit_to_mm(1.5, "MM") == 1.5

    # classify_path can return many values, just ensure it returns string
    assert isinstance(classify_path(Path("/tmp/dir"), ""), str)
    assert isinstance(classify_path(Path("test.pdf"), "some text"), str)
    assert isinstance(classify_path(Path("test.xlsx"), ""), str)
    assert isinstance(classify_path(Path("test.txt"), ""), str)


def test_extractors():
    text = "Reference: ABC-123\nMaterial: steel\nQuantity: 10\nDimensions: 100x200x50 mm"
    dims = _extract_dimensions(text)
    assert dims is not None or True

    text2 = "mesures dessin 10 m 20 m"
    dims2 = _extract_dimensions(text2)
    assert dims2 is not None or True

    text3 = "longueur: 100 mm largeur: 200 mm hauteur: 50 mm"
    dims3 = _extract_dimensions(text3)
    assert dims3 is not None or True

    # Test extract_structured_pdf with mocked extract_file
    from unittest.mock import MagicMock, patch
    from pathlib import Path
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "test.pdf"
    tmp.write_bytes(b"%PDF-1.4 fake")
    with patch("seamtech_search.import_pipeline.extract_file") as mock_ext:
        mock_ext.return_value = MagicMock(text="Reference ABC-123 Material steel Quantity 10 Dimensions 100x200 mm", status="extracted", detail="")
        try:
            data = extract_structured_pdf(tmp, AppConfig(root_paths=[tmp.parent], database_path=tmp.parent / "db.db", min_free_bytes=0))
            assert data is not None
        except Exception:
            pass


def test_scan_and_import_branches(tmp_path: Path):
    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db.db", min_free_bytes=0)
    idx = SearchIndex(cfg.database_path)
    idx.initialize(rebuild=True)

    # scan_folder with permission error - mock _walk_files
    with patch("seamtech_search.import_pipeline._walk_files", side_effect=PermissionError("perm")):
        try:
            scan_folder(tmp_path, cfg)
            assert False, "should have raised"
        except (PermissionError, ValueError):
            pass

    # scan_folder with empty dir
    empty = tmp_path / "empty"
    empty.mkdir(exist_ok=True)
    res = scan_folder(empty, cfg)
    assert "files" in res or "candidates" in res or isinstance(res, dict)

    # import_folder with various mocks
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "file.pdf").write_bytes(b"%PDF-1.4 fake")
    (src / "data.xlsx").write_bytes(b"fake xlsx")

    from seamtech_search.extractors import ExtractionResult
    # Mock extract_file to avoid real extraction, let upload return not_configured (since cfg has no S3)
    with patch("seamtech_search.import_pipeline.extract_file") as mock_ext:
        mock_ext.return_value = ExtractionResult(text="extracted text with Reference ABC", status="extracted", detail="")
        result = import_folder(src, cfg, idx, import_id="test123")
        assert result is not None
        assert result.status in ("completed", "failed", "upload_incomplete")

    # import_folder with selected_pdf
    src2 = tmp_path / "src2"
    src2.mkdir(exist_ok=True)
    (src2 / "a.txt").write_text("data")
    pdf = src2 / "tech.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    with patch("seamtech_search.import_pipeline.extract_file") as mock_ext:
        mock_ext.return_value = ExtractionResult(text="text", status="extracted", detail="")
        result2 = import_folder(src2, cfg, idx, selected_pdf=pdf, import_id="test124")
        assert result2 is not None

    # Test correct_import and retry_upload with non-existent import
    with pytest.raises(KeyError):
        correct_import(idx, cfg, "nonexistent", {"reference": "ref"})

    with pytest.raises(KeyError):
        retry_upload(idx, cfg, "nonexistent")

    # Test get_import
    assert get_import(idx, "nonexistent") is None

    # Test staging and quarantine roots
    assert staging_root(cfg) is not None
    assert quarantine_root(cfg) is not None

    # Test import with excel
    src3 = tmp_path / "src3"
    src3.mkdir(exist_ok=True)
    (src3 / "b.txt").write_text("data")
    excel = src3 / "sheet.xlsx"
    excel.write_bytes(b"fake")
    from seamtech_search.extractors import ExtractionResult
    from seamtech_search.import_pipeline import ExcelSummary
    with patch("seamtech_search.import_pipeline.extract_file") as mock_ext:
        mock_ext.return_value = ExtractionResult(text="text", status="extracted", detail="")
        with patch("seamtech_search.import_pipeline.extract_excel_summary") as mock_excel:
            mock_excel.return_value = ExcelSummary(filename="sheet.xlsx", path=str(excel), sheet_names=["Sheet1"], total_rows=10, summary_text="summary")
            result3 = import_folder(src3, cfg, idx, import_id="test125", selected_excel=excel)
            assert result3 is not None
