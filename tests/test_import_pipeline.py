from pathlib import Path

from seamtech_search.config import AppConfig
from seamtech_search.extractors import ExtractionResult
from seamtech_search.import_pipeline import extract_structured_pdf


def _extract_with_text(monkeypatch, text: str) -> None:
    monkeypatch.setattr(
        "seamtech_search.import_pipeline.extract_file",
        lambda *args, **kwargs: ExtractionResult(text, "extracted"),
    )


def test_description_extracts_value_on_same_line(monkeypatch, tmp_path: Path) -> None:
    _extract_with_text(monkeypatch, "Fiche de fabrication 7792-SO - Porte coulissante")

    result = extract_structured_pdf(tmp_path / "drawing.pdf", AppConfig(root_paths=[tmp_path]))

    assert result.description == "7792-SO - Porte coulissante"


def test_description_header_does_not_capture_following_line(monkeypatch, tmp_path: Path) -> None:
    _extract_with_text(
        monkeypatch,
        "FICHE DE FABRICATION\nCommande 7792-SO\nReference: 7792-SO-GV",
    )

    result = extract_structured_pdf(tmp_path / "drawing.pdf", AppConfig(root_paths=[tmp_path]))

    assert result.description is None
    assert result.reference == "7792-SO-GV"
