"""Layout tests for the PDF technical report.

The old ``generate_report`` hard-truncated every string it drew -- ``[:100]`` for
field values, ``[:110]`` for warnings and Excel lines, ``[:20]`` for table cells
-- and decremented ``y`` in three loops without ever checking the bottom margin.
Both failure modes were silent. A long description lost its tail mid-word with no
ellipsis, and the warnings / additional-sheets / Excel-row loops walked straight
off the page, so the overflow content was drawn at negative coordinates and
never appeared in the PDF at all. The Excel loop did check, but answered with
``break``, discarding whichever rows it had not yet drawn.

These tests pull the text back out of the generated PDF with pdfplumber and
assert the content survives. A regression to truncation or to off-page drawing
fails here instead of surfacing when someone opens a report for a real
fabrication sheet and notices the description is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seamtech_search.import_pipeline import (
    AnalyzedSheet,
    Dimensions,
    ExcelSheetSummary,
    ExcelSummary,
    ExtractedData,
    _pdf_ensure_room,
    _pdf_fit,
    _pdf_wrap,
    generate_report,
)

pytest.importorskip("reportlab", reason="reportlab is required to build the PDF report")
pdfplumber = pytest.importorskip("pdfplumber", reason="pdfplumber is used to read the PDF back")


def _read_pdf_text(path: Path) -> str:
    """Extract all text, collapsing the line breaks that wrapping introduced.

    Wrapping splits at word boundaries, so rejoining lines with a single space
    reproduces the original string exactly -- that is what makes "the whole
    value is present" assertable.
    """
    chunks: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return " ".join(" ".join(chunks).split())


def _page_count(path: Path) -> int:
    with pdfplumber.open(path) as pdf:
        return len(pdf.pages)


def _data(**overrides) -> ExtractedData:
    payload = {
        "reference": "REF-VOILE-001",
        "material": "Chêne massif",
        "dimensions": Dimensions(length=1200, width=600, height=19, unit="mm", length_mm=1200, width_mm=600),
        "quantity": 12,
        "description": "Voile de latte standard",
        "extraction_status": "complete",
        "confidence": 0.92,
        "warnings": [],
    }
    payload.update(overrides)
    return ExtractedData(**payload)


# --- long field values -----------------------------------------------------


def test_long_description_is_wrapped_not_truncated(tmp_path: Path) -> None:
    """A description far past the old 100-character cut must survive in full."""
    description = (
        "Voile de latte contrecollée sur chant frêne massif avec rainure et languette, "
        "finition huilée naturelle appliquée en deux couches et égrenée au grain 180, "
        "usinage CNC cinq axes avec contrôle dimensionnel après ponçage, "
        "numérotation des lames au dos pour remontage à blanc, "
        "emballage individuel en carton double cannelure avec cale d'angle, "
        "lot de fabrication tracé par référence client et date de presse, TAIL-MARKER-XYZ"
    )
    assert len(description) > 200

    out = tmp_path / "report.pdf"
    generate_report(data=_data(description=description), output_path=out)

    text = _read_pdf_text(out)
    assert "TAIL-MARKER-XYZ" in text, "the tail of the description was cut off"
    assert description in text, "the description was altered rather than wrapped"


def test_long_reference_and_material_are_wrapped(tmp_path: Path) -> None:
    """Reference/material are free text from the sheet, so they can be long too."""
    reference = "REF-" + "LONGSEGMENT-" * 20 + "END-OF-REFERENCE"
    material = "Panneau contreplaqué bouleau filmé phénolique marron classe E1, TAIL-OF-MATERIAL"

    out = tmp_path / "report.pdf"
    generate_report(data=_data(reference=reference, material=material), output_path=out)

    text = _read_pdf_text(out)
    assert "END-OF-REFERENCE" in text
    assert "TAIL-OF-MATERIAL" in text


# --- warnings --------------------------------------------------------------


def test_every_warning_survives_and_overflows_to_a_new_page(tmp_path: Path) -> None:
    """The warnings loop had no page-break guard: it drew off the bottom."""
    warnings = [
        f"ALERTE-{i:02d}: dimension introuvable pour la position {i}, valeur par défaut appliquée "
        f"après vérification manuelle de la fiche, marqueur de fin WEND-{i:02d}"
        for i in range(40)
    ]

    out = tmp_path / "report.pdf"
    generate_report(data=_data(warnings=warnings), output_path=out)

    text = _read_pdf_text(out)
    missing = [i for i in range(40) if f"WEND-{i:02d}" not in text]
    assert not missing, f"warnings drawn off the page and lost: {missing}"
    assert _page_count(out) > 1, "40 warnings cannot fit on one page; pagination never happened"


def test_single_very_long_warning_is_wrapped(tmp_path: Path) -> None:
    """The old `warning[:110]` cut left no trace of the missing text."""
    warning = "Champ manquant: " + "détail du contrôle " * 20 + "WARN-TAIL"

    out = tmp_path / "report.pdf"
    generate_report(data=_data(warnings=[warning]), output_path=out)

    assert "WARN-TAIL" in _read_pdf_text(out)


# --- Excel summary ---------------------------------------------------------


def _excel(summary_text: str, headers: list[str] | None = None, rows: list[list[str]] | None = None) -> ExcelSummary:
    return ExcelSummary(
        filename="nomenclature-voile.xlsx",
        path="data/jobs/j1/nomenclature-voile.xlsx",
        sheets=[
            ExcelSheetSummary(
                sheet_name="BOM",
                row_count=120,
                column_count=len(headers or ["Item", "Qty"]),
                headers=headers or ["Item", "Qty"],
                sample_rows=rows or [["Voile de latte", "12"]],
                metrics={},
            )
        ],
        total_sheets=1,
        total_rows=120,
        sheet_names=["BOM"],
        detected_reference=None,
        detected_material=None,
        detected_quantity=None,
        summary_text=summary_text,
    )


def test_long_excel_summary_line_is_wrapped(tmp_path: Path) -> None:
    summary_text = (
        "1 feuille analysée, colonnes détectées Item/Qty/Longueur/Largeur/Épaisseur/Commentaire, "
        "aucune colonne de référence exploitable, quantités hétérogènes, XLS-TAIL-MARKER"
    )

    out = tmp_path / "report.pdf"
    generate_report(data=_data(), output_path=out, excel_summary=_excel(summary_text))

    assert "XLS-TAIL-MARKER" in _read_pdf_text(out)


def test_wide_table_cells_show_a_visible_ellipsis(tmp_path: Path) -> None:
    """Cells cannot wrap without desyncing columns, but the cut must be visible.

    The old `str(cell)[:20]` was silent: nothing in the PDF said content had
    been dropped, and it cut by character count rather than by the width the
    glyphs actually render at. Six headers are used because column width is
    `(width - margins) / len(headers)`; with a single header the column is wide
    enough that nothing needs truncating at all.
    """
    headers = ["Description", "Commentaire", "Longueur", "Largeur", "Épaisseur", "Référence"]
    long_cell = "Voile de latte contrecollée chant frêne finition huilée naturelle"
    rows = [[long_cell, "Pièce de rechange", "1200", "600", "19", "REF-001"]]

    out = tmp_path / "report.pdf"
    generate_report(data=_data(), output_path=out, excel_summary=_excel("résumé", headers, rows))

    text = _read_pdf_text(out)
    assert "..." in text, "a cell was truncated with no visible marker"
    assert long_cell not in text, "the cell was not truncated at all"
    assert text.startswith("SEAMTECH Technical Report")  # sanity: text extraction works
    # The surviving prefix is still readable, not just an ellipsis.
    assert "Voile de latte" in text
    # Narrow cells that fit must pass through untouched.
    assert "REF-001" in text and "Pièce de rechange" in text


# --- additional sheets -----------------------------------------------------


def test_many_additional_sheets_paginate_instead_of_running_off_page(tmp_path: Path) -> None:
    """The additional-sheets loop had no page-break guard at all."""
    sheets = [
        AnalyzedSheet(
            filename=f"fiche-technique-{i:03d}.pdf",
            path=f"data/jobs/j1/fiche-technique-{i:03d}.pdf",
            reference=f"REF-{i:03d}",
            material="Chêne massif",
            dimensions=Dimensions(length=1200, width=600, length_mm=1200, width_mm=600),
            quantity=3,
            description=None,
            extraction_status="complete",
            confidence=0.88,
        )
        for i in range(80)
    ]

    out = tmp_path / "report.pdf"
    generate_report(data=_data(additional_sheets=sheets), output_path=out)

    text = _read_pdf_text(out)
    assert "fiche-technique-079.pdf" in text, "the last sheets ran off the page and were lost"
    assert _page_count(out) > 1


# --- helper units ----------------------------------------------------------


class _FakeCanvas:
    """Minimal stand-in exposing only what the layout helpers use."""

    def __init__(self) -> None:
        self.pages_shown = 0
        self.drawn: list[tuple[float, float, str]] = []
        self.fonts: list[tuple[str, float]] = []

    def stringWidth(self, text: str, font_name: str, font_size: float) -> float:
        # Monospace-ish model: width scales with font size, as in real fonts.
        return len(text) * font_size * 0.5

    def showPage(self) -> None:
        self.pages_shown += 1

    def setFont(self, font_name: str, font_size: float) -> None:
        self.fonts.append((font_name, font_size))

    def drawString(self, x: float, y: float, text: str) -> None:
        self.drawn.append((x, y, text))


def test_wrap_keeps_every_line_within_the_width() -> None:
    pdf = _FakeCanvas()
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    lines = _pdf_wrap(pdf, text, "Helvetica", 10, 60)

    assert len(lines) > 1
    for line in lines:
        assert pdf.stringWidth(line, "Helvetica", 10) <= 60
    # No content lost, and rejoining with spaces reproduces the input.
    assert " ".join(lines) == text


def test_wrap_splits_tokens_longer_than_the_width() -> None:
    """A single long reference code must not be dropped or drawn overflowing."""
    pdf = _FakeCanvas()
    token = "REF" + "X" * 100

    lines = _pdf_wrap(pdf, token, "Helvetica", 10, 40)

    assert len(lines) > 1
    for line in lines:
        assert pdf.stringWidth(line, "Helvetica", 10) <= 40
    assert "".join(lines) == token


def test_wrap_preserves_embedded_newlines_as_separate_paragraphs() -> None:
    pdf = _FakeCanvas()
    lines = _pdf_wrap(pdf, "one\ntwo", "Helvetica", 10, 400)
    assert lines == ["one", "two"]


def test_fit_returns_short_text_unchanged() -> None:
    pdf = _FakeCanvas()
    assert _pdf_fit(pdf, "Item", "Helvetica", 8, 200) == "Item"


def test_fit_truncates_by_measured_width_with_an_ellipsis() -> None:
    pdf = _FakeCanvas()
    out = _pdf_fit(pdf, "Voile de latte contrecollée", "Helvetica", 8, 60)

    assert out.endswith("...")
    assert pdf.stringWidth(out, "Helvetica", 8) <= 60
    assert out != "..."  # something readable survived


def test_fit_never_loops_forever_on_a_width_smaller_than_the_ellipsis() -> None:
    pdf = _FakeCanvas()
    assert _pdf_fit(pdf, "longvalue", "Helvetica", 8, 1) == "..."


def test_ensure_room_returns_y_unchanged_when_there_is_space() -> None:
    pdf = _FakeCanvas()
    assert _pdf_ensure_room(pdf, 500, 20, 842) == 500
    assert pdf.pages_shown == 0


def test_ensure_room_starts_a_new_page_when_content_would_overflow() -> None:
    """This is the page-break guard the warnings loop was missing."""
    pdf = _FakeCanvas()
    y = _pdf_ensure_room(pdf, 40, 20, 842)

    assert pdf.pages_shown == 1
    assert y == 842 - 60


def test_ensure_room_draws_a_title_on_the_new_page() -> None:
    pdf = _FakeCanvas()
    y = _pdf_ensure_room(pdf, 40, 20, 842, title="Résumé Excel (suite)", title_size=14)

    assert ("Helvetica-Bold", 14) in pdf.fonts
    assert any(text == "Résumé Excel (suite)" for _, _, text in pdf.drawn)
    assert y < 842 - 60, "the title must consume vertical space"


def test_report_falls_back_to_json_without_reportlab(monkeypatch, tmp_path: Path) -> None:
    """The ImportError fallback must still work after the layout rewrite."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "reportlab.pdfgen" or name.startswith("reportlab."):
            raise ImportError("no reportlab")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    out = tmp_path / "report.pdf"
    result = generate_report(data=_data(), output_path=out)

    assert result.suffix == ".json"
    assert result.exists()
    assert not out.exists()
