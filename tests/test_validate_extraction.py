"""Tests for scripts/validate_extraction.py — the audit item-1 harness.

The harness exists so a human can run extraction over real fabrication sheets
and see exactly which fields are wrong. A harness that silently reports
"clean" on a bleeding field is worse than no harness, so these tests pin both
directions: it must flag a bleed, and it must stay quiet on a good sheet.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "validate_extraction.py"


def _load_harness() -> Any:
    spec = importlib.util.spec_from_file_location("validate_extraction", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_extraction"] = module
    spec.loader.exec_module(module)
    return module


harness = _load_harness()


def _extracted(**overrides: Any) -> Any:
    from seamtech_search.import_pipeline import ExtractedData as Model

    payload: dict[str, Any] = {"raw_text": "FICHE DE FABRICATION", "extraction_status": "success"}
    payload.update(overrides)
    return Model.model_validate(payload)


def test_discover_finds_repo_sample_pdf() -> None:
    """With no arguments the harness must find the committed sample sheet."""
    documents = harness.discover([])
    assert documents, "harness found no PDFs in sample_data/"
    assert any(doc.name == "fiche-technique.pdf" for doc in documents)


def test_discover_expands_directories_and_skips_missing(tmp_path: Path, capsys) -> None:
    nested = tmp_path / "dossier" / "CLIENT-1"
    nested.mkdir(parents=True)
    (nested / "sheet.pdf").write_bytes(b"%PDF-1.4\n")
    (nested / "notes.txt").write_text("not a pdf")

    documents = harness.discover([str(tmp_path / "dossier"), str(tmp_path / "does-not-exist")])

    assert [doc.name for doc in documents] == ["sheet.pdf"]
    assert "does not exist" in capsys.readouterr().err


def test_discover_accepts_a_single_file(tmp_path: Path) -> None:
    sheet = tmp_path / "one.pdf"
    sheet.write_bytes(b"%PDF-1.4\n")

    assert harness.discover([str(sheet)]) == [sheet]


def test_review_notes_flags_a_bled_reference() -> None:
    """A reference that swallowed the next column must be flagged, not passed."""
    bled = "REF-2026-778 " + "x" * 200
    data = _extracted(
        raw_text="some text",
        reference=bled,
        material="Dacron",
        quantity=2,
        description="Grand voile",
        dimensions={"length": 12.0, "width": 4.0, "unit": "m"},
        confidence=1.0,
    )

    notes = harness.review_notes(data, data.raw_text)

    assert any("probable bleed" in note for note in notes)


def test_review_notes_flags_missing_unit_and_missing_fields() -> None:
    data = _extracted(
        raw_text="FICHE DE FABRICATION\nEchelle 12 x 4",
        dimensions={"length": 12.0, "width": 4.0, "unit": None},
        extraction_status="partial",
        confidence=0.2,
    )

    notes = harness.review_notes(data, data.raw_text)
    joined = "\n".join(notes)

    assert "reference: None" in joined
    assert "material: None" in joined
    assert "unit is None" in joined
    assert "extraction_status=partial" in joined


def test_review_notes_flags_empty_text_as_needing_ocr() -> None:
    data = _extracted(raw_text="", extraction_status="failed", confidence=0.0)

    notes = harness.review_notes(data, "")

    assert any("OCR" in note for note in notes)


def test_review_notes_clean_on_a_good_sheet() -> None:
    data = _extracted(
        reference="REF-2026-778",
        material="Dacron Pro 340",
        quantity=2,
        description="Grand voile lattee",
        dimensions={"length": 12.5, "width": 4.2, "unit": "m"},
        confidence=1.0,
    )

    assert harness.review_notes(data, data.raw_text) == []


def test_main_exits_nonzero_when_a_document_needs_review(tmp_path: Path, monkeypatch) -> None:
    """End-to-end: main() must exit non-zero when something needs review."""
    sheet = tmp_path / "bled.pdf"
    sheet.write_bytes(b"%PDF-1.4\n")

    bled_value = "REF-1 " + "bleed " * 40
    monkeypatch.setattr(
        harness,
        "extract_structured_pdf",
        lambda *args, **kwargs: _extracted(
            raw_text="FICHE DE FABRICATION",
            reference=bled_value,
            extraction_status="partial",
            confidence=0.2,
        ),
    )

    assert harness.main(["validate_extraction.py", str(sheet)]) == 1


def test_main_exits_zero_on_a_clean_document(monkeypatch) -> None:
    """The committed sample sheet must pass the harness with no review notes."""
    sample = Path(harness.REPO_ROOT) / "sample_data" / "CLIENT-123" / "fiche-technique.pdf"

    assert harness.main(["validate_extraction.py", str(sample)]) == 0


def test_main_returns_2_when_no_pdfs_found(tmp_path: Path, capsys) -> None:
    empty = tmp_path / "nothing-here"
    empty.mkdir()

    assert harness.main(["validate_extraction.py", str(empty)]) == 2
    assert "No PDFs found" in capsys.readouterr().err


def test_main_survives_a_document_that_raises(tmp_path: Path, monkeypatch, capsys) -> None:
    """One unreadable file must not kill the whole validation run."""
    sheet = tmp_path / "broken.pdf"
    sheet.write_bytes(b"%PDF-1.4\n")

    def _boom(*args, **kwargs):
        raise RuntimeError("corrupt pdf")

    monkeypatch.setattr(harness, "extract_structured_pdf", _boom)

    assert harness.main(["validate_extraction.py", str(sheet)]) == 1
    assert "EXTRACTION RAISED" in capsys.readouterr().out
