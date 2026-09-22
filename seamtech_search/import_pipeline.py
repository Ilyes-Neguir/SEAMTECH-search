"""Reference-folder import workflow with PDF and Excel twin analysis.

Features:
1. **Scan** (:func:`scan_folder`) — walk a folder, classify every PDF with the
   shared fixed-layout anchors and discover associated Excel sheets (.xlsx/.xls).
2. **Confirm** (:func:`import_folder` with ``selected_pdf`` & optional ``selected_excel``) —
   extract technical PDF, analyze Excel sheet/BOM, generate combined PDF + Word reports,
   index everything, and upload all files (PDF source, Excel source, PDF report, Word report)
   to OneDrive.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .anchors import TECHNICAL_ANCHORS, classify_pdf_text, matched_anchors
from .config import AppConfig
from .extractors import extract_file
from .indexer import SearchIndex
from .jobs import ImportCancelledError
from .models import Document
from .retention import ensure_free_space
from .storage import UploadBatch, upload_artifacts_to_storage

logger = logging.getLogger("seamtech_search.import_pipeline")

__all__ = [
    "TECHNICAL_ANCHORS",
    "classify_pdf_text",
    "classify_path",
    "extract_structured_pdf",
    "extract_excel_summary",
    "generate_report",
    "generate_docx_report",
    "import_folder",
    "scan_folder",
    "get_import",
    "update_import",
    "correct_import",
    "retry_upload",
    "staging_root",
    "quarantine_root",
    "normalize_unit_to_mm",
    "Dimensions",
    "ExcelSheetSummary",
    "ExcelSummary",
    "ExtractedData",
    "ImportCandidate",
    "ImportFile",
    "ImportResult",
    "FIELD_PATTERNS",
    "DIMENSION_PATTERN",
    "UNIT_TO_MM",
    "ImportCancelledError",
    "importer_dossier_complet",
    "importer_lot_dossiers",
]


# ---------------------------------------------------------------------------
# Structured extraction models
# ---------------------------------------------------------------------------


UNIT_TO_MM: dict[str, float] = {"mm": 1.0, "cm": 10.0, "m": 1000.0}


def normalize_unit_to_mm(value: float | None, unit: str | None) -> float | None:
    """Convert a length to millimetres. Unknown unit/value -> None."""
    if value is None or unit is None:
        return None
    factor = UNIT_TO_MM.get(unit.strip().lower())
    if factor is None:
        return None
    return round(value * factor, 3)


class Dimensions(BaseModel):
    model_config = ConfigDict(extra="ignore")
    length: float | None = None
    width: float | None = None
    height: float | None = None
    unit: str | None = None
    length_mm: float | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    unit_normalized: str | None = None


class ExcelSheetSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sheet_name: str
    row_count: int
    column_count: int
    headers: list[str] = Field(default_factory=list)
    sample_rows: list[list[str]] = Field(default_factory=list)
    metrics: dict[str, str] = Field(default_factory=dict)


class ExcelSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    filename: str
    path: str
    sheets: list[ExcelSheetSummary] = Field(default_factory=list)
    total_sheets: int = 0
    total_rows: int = 0
    sheet_names: list[str] = Field(default_factory=list)
    detected_reference: str | None = None
    detected_material: str | None = None
    detected_quantity: int | None = None
    summary_text: str = ""


class AnalyzedSheet(BaseModel):
    model_config = ConfigDict(extra="ignore")
    filename: str
    path: str
    reference: str | None = None
    material: str | None = None
    dimensions: Dimensions = Field(default_factory=Dimensions)
    quantity: int | None = None
    description: str | None = None
    extraction_status: str = "success"
    confidence: float = 0.0


class ExtractedData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reference: str | None = None
    material: str | None = None
    dimensions: Dimensions = Field(default_factory=Dimensions)
    quantity: int | None = None
    description: str | None = None
    raw_text: str = ""
    extraction_status: str = "failed"
    confidence: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    excel_summary: ExcelSummary | None = None
    additional_sheets: list[AnalyzedSheet] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, value: float) -> float:
        return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class ImportFile:
    path: str
    name: str
    category: str
    size: int
    extension: str
    extraction_status: str
    report_path: str | None = None
    upload_status: str = "pending"
    object_key: str | None = None
    object_bucket: str | None = None
    uploaded_at: float | None = None


@dataclass(frozen=True)
class ImportCandidate:
    path: str
    name: str
    size: int
    anchors_matched: list[str] = field(default_factory=list)

    @property
    def anchor_count(self) -> int:
        return len(self.anchors_matched)


@dataclass(frozen=True)
class ImportResult:
    import_id: str
    source_path: str
    status: str
    files_detected: int
    analyzed_files: int
    technical_pdf: str | None
    data: dict[str, Any] | None
    report_path: str | None
    upload_status: str
    warnings: list[str]
    files: list[ImportFile]
    candidates: list[dict[str, Any]] = field(default_factory=list)
    report_docx_path: str | None = None
    excel_file: str | None = None
    excel_summary: dict[str, Any] | None = None
    excel_candidates: list[dict[str, Any]] = field(default_factory=list)
    analyzed_items: list[dict[str, Any]] = field(default_factory=list)
    all_verified: bool = False
    artifacts: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fixed-layout rules & Text/Excel Extractors
# ---------------------------------------------------------------------------

# Words that mark the start of the NEXT column/field on a fixed-layout sheet.
# When pdfplumber flattens a table row (or a multi-column line) into a single
# line of text, a greedy `([^\n]+)` capture has no way to know where one
# field ends and the next begins, so it swallows the whole rest of the line —
# this is what let "reference" absorb "Client NAUTIC SARL" in production
# documents that the synthetic test fixture never exercised. `_FIELD_STOP` is
# a lookahead every field capture must respect: stop at a table-cell pipe
# (from `_extract_pdf_with_plumber`'s `" | ".join(...)`), a run of 2+ spaces
# (the column gap pdfplumber's coordinate-aware text extraction leaves
# between adjacent columns), the start of another known label, a newline, or
# end of string — whichever comes first.
_FIELD_LABEL_WORDS: tuple[str, ...] = (
    "reference",
    "référence",
    "material",
    "matière",
    "matériau",
    "matériaux",
    "tissu",
    "quantity",
    "quantité",
    "description",
    "longueur",
    "largeur",
    "hauteur",
    "length",
    "width",
    "height",
    "client",
    "date",
    "commande",
)
_FIELD_STOP = (
    r"(?=\s{2,}"  # column gap
    r"|\s*\|"  # table-cell separator
    r"|\s+(?:" + "|".join(_FIELD_LABEL_WORDS) + r")\s*[:\-]?\s*\S"  # next label starting
    r"|\n|$)"
)

FIELD_PATTERNS: dict[str, tuple[str, ...]] = {
    "reference": (
        r"reference\s*[:\-]?\s*([^\n]+?)" + _FIELD_STOP,
        r"référence\s*[:\-]?\s*([^\n]+?)" + _FIELD_STOP,
        r"(?:fichier|commande)\s+([A-Z0-9][A-Z0-9_-]+)",
    ),
    "material": (
        r"material\s*[:\-]?\s*([^\n]+?)" + _FIELD_STOP,
        r"mati(?:è|e)re\s*[:\-]?\s*([^\n]+?)" + _FIELD_STOP,
        r"matériau(?:x)?\s*[:\-]?\s*([^\n]+?)" + _FIELD_STOP,
        r"tissu\(s\)\s*:\s*([^\n]+?)" + _FIELD_STOP,
    ),
    "quantity": (
        r"quantity\s*[:\-]?\s*(\d+)",
        r"quantit(?:y|é)\s*[:\-]?\s*(\d+)",
    ),
    "description": (
        r"description[^\S\r\n]*[:\-]?[^\S\r\n]*([^\n]+?)" + _FIELD_STOP,
        r"fiche de fabrication[^\S\r\n]*[\"']?([^\n\"']+?)" + _FIELD_STOP,
    ),
}
# Anything captured for a non-description field that still contains another
# label word, or that runs suspiciously long for what should be a short
# value, is treated as a probable bleed rather than a trustworthy match —
# see `_looks_bled`.
_LABEL_WORD_RE = re.compile(r"\b(?:" + "|".join(_FIELD_LABEL_WORDS) + r")\b", re.I)
_MAX_PLAUSIBLE_FIELD_LENGTH = 120

DIMENSION_PATTERN = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)(?:\s*[x×]\s*(\d+(?:[.,]\d+)?))?\s*(mm|cm|m)?", re.I
)
LABELED_DIMENSION_PATTERNS: dict[str, tuple[str, ...]] = {
    # "length" patterns carry an extra optional group: some sheets write
    # "longueur: 2 x 1.5 m" (length and width paired on the same labelled
    # line) rather than separate "longueur:"/"largeur:" lines.
    "length": (
        r"longueur\s*[:\-]?\s*(\d+(?:[.,]\d+)?)(?:\s*[x×]\s*(\d+(?:[.,]\d+)?))?\s*(mm|cm|m)?",
        r"length\s*[:\-]?\s*(\d+(?:[.,]\d+)?)(?:\s*[x×]\s*(\d+(?:[.,]\d+)?))?\s*(mm|cm|m)?",
    ),
    "width": (
        r"largeur\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?",
        r"width\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?",
    ),
    "height": (
        r"hauteur\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?",
        r"height\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?",
    ),
}


def classify_path(path: Path, text: str = "") -> str:
    if path.is_dir():
        return "folder"
    ext = path.suffix.lower()
    if ext == ".pdf":
        return classify_pdf_text(text) if text else "pdf_candidate"
    if ext in {".xlsx", ".xls"}:
        return "excel_sheet"
    return "storage_direct"


def _extract_dimensions(text: str) -> dict[str, Any] | None:
    # 1. Labelled dimensions ("longueur:", "width:", ...) win first. They're
    #    the only signal that's actually tied to a dimension by the document
    #    itself, so they must be checked before the naked "N x M" pattern
    #    below, which will happily match a date or a part code anywhere in
    #    the page and previously ran first, silently overriding these.
    labeled: dict[str, Any] = {}
    paired_width: float | None = None
    for axis, patterns in LABELED_DIMENSION_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                labeled[axis] = float(match.group(1).replace(",", "."))
                if axis == "length":
                    if match.group(2):
                        paired_width = float(match.group(2).replace(",", "."))
                    found_unit = (match.group(3) or "").strip().lower() or None
                else:
                    found_unit = (match.group(2) or "").strip().lower() or None
                if "unit" not in labeled and found_unit:
                    labeled["unit"] = found_unit
                break
    if "width" not in labeled and paired_width is not None:
        labeled["width"] = paired_width
    if "length" in labeled or "width" in labeled:
        # Unit genuinely absent from the document: report it as unknown
        # rather than assuming "mm", which previously turned a 12m x 4m sail
        # into 12mm x 4mm with no indication anything was guessed.
        labeled.setdefault("unit", None)
        return labeled

    # 2. "mesures dessin" free-form measurement list — unit is always metres.
    drawing = re.search(r"mesures dessin\s+((?:\d+(?:[.,]\d+)?\s*m\s*){2,})", text, re.I)
    if drawing:
        measurements = re.findall(r"\d+(?:[.,]\d+)?", drawing.group(1))
        if len(measurements) >= 2:
            return {
                "length": float(measurements[0].replace(",", ".")),
                "width": float(measurements[1].replace(",", ".")),
                "unit": "m",
            }

    # 3. Last resort: a bare "N x M [unit]" anywhere in the text. Weakest
    #    signal (no label ties it to a dimension at all), so an absent unit
    #    is reported as unknown instead of assumed.
    dimension = DIMENSION_PATTERN.search(text)
    if dimension:
        return {
            "length": float(dimension.group(1).replace(",", ".")),
            "width": float(dimension.group(2).replace(",", ".")),
            "height": float(dimension.group(3).replace(",", ".")) if dimension.group(3) else None,
            "unit": (dimension.group(4) or "").strip().lower() or None,
        }
    return None


def _looks_bled(value: str) -> bool:
    """True if a captured field value probably swallowed a neighbouring column."""
    return bool(_LABEL_WORD_RE.search(value)) or len(value) > _MAX_PLAUSIBLE_FIELD_LENGTH


def _with_normalized_units(raw: dict[str, Any]) -> dict[str, Any]:
    unit = raw.get("unit")
    enriched = dict(raw)
    for axis in ("length", "width", "height"):
        enriched[f"{axis}_mm"] = normalize_unit_to_mm(raw.get(axis), unit)
    enriched["unit_normalized"] = "mm" if unit in UNIT_TO_MM else None
    return enriched


def extract_structured_pdf(path: Path, config: AppConfig) -> ExtractedData:
    result = extract_file(
        path,
        max_chars=config.max_extract_chars,
        max_file_size_bytes=config.max_file_size_bytes,
        enable_legacy_office=config.enable_legacy_office,
        libreoffice_command=config.libreoffice_command,
        enable_ocr=config.enable_ocr,
        tesseract_command=config.tesseract_command,
        ocrmypdf_command=config.ocrmypdf_command,
        external_extraction_timeout_seconds=config.external_extraction_timeout_seconds,
        external_extractors=config.external_extractors,
    )
    if result.status != "extracted":
        return ExtractedData(
            raw_text=result.text,
            extraction_status="failed",
            warnings=[result.detail or result.status],
        )

    text = "\n".join(line.strip() for line in result.text.splitlines() if line.strip())
    values: dict[str, Any] = {"raw_text": text, "extraction_status": "partial", "confidence": 0.0}
    warnings: list[str] = []
    matched = 0
    suspect = 0
    for field_name, patterns in FIELD_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                value = match.group(1).strip()
                values[field_name] = value
                # "description" is expected to be a free sentence, so the
                # bleed heuristic (built from short-field label words) would
                # false-positive on it constantly; every other field should
                # be a short atomic value and is checked.
                if field_name != "description" and _looks_bled(value):
                    suspect += 1
                    warnings.append(f"{field_name}: à vérifier — valeur suspecte ({value!r})")
                else:
                    matched += 1
                break
    raw_dimensions = _extract_dimensions(text)
    total_checks = len(FIELD_PATTERNS) + 1  # +1 for dimensions
    if raw_dimensions:
        values["dimensions"] = _with_normalized_units(raw_dimensions)
        if raw_dimensions.get("unit") is None:
            suspect += 1
            warnings.append("dimensions: unité non détectée — valeurs non converties en mm, à vérifier")
        else:
            matched += 1
    if "quantity" in values:
        try:
            values["quantity"] = int(values["quantity"])
        except ValueError:
            pass
    # Confidence only credits fields that passed the sanity check. A field
    # that matched a regex but looks bled counts against completeness the
    # same as a field that never matched at all — it is not usable data.
    values["confidence"] = matched / total_checks
    if not text:
        values["extraction_status"] = "failed"
        warnings = ["No extractable text found; OCR may be required."]
    elif suspect:
        values["extraction_status"] = "partial"
    elif matched < total_checks:
        values["extraction_status"] = "partial"
        warnings.append("One or more configured fields were not found.")
    else:
        values["extraction_status"] = "success"
    if warnings:
        values["warnings"] = warnings
    try:
        return ExtractedData.model_validate(values)
    except ValidationError as exc:
        return ExtractedData(raw_text=text, extraction_status="failed", warnings=[str(exc)])


def extract_excel_summary(path: Path) -> ExcelSummary:
    """Extract structured sheet summaries, tables, and metadata from an Excel workbook (.xlsx/.xls)."""
    try:
        import openpyxl

        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        sheets: list[ExcelSheetSummary] = []
        sheet_names: list[str] = []
        total_rows = 0
        detected_ref: str | None = None
        detected_mat: str | None = None
        detected_qty: int | None = None

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows_data: list[list[str]] = []
            for row in ws.iter_rows(values_only=True):
                cleaned_row = [str(cell).strip() if cell is not None else "" for cell in row]
                if any(cleaned_row):
                    rows_data.append(cleaned_row)

            if not rows_data:
                continue

            sheet_names.append(sheet_name)
            total_rows += len(rows_data)
            headers = rows_data[0]
            col_count = len(headers)
            sample_rows = rows_data[1:20]

            metrics: dict[str, str] = {
                "Total Lignes": str(len(rows_data)),
                "Colonnes": str(col_count),
            }

            for row in rows_data:
                row_str = " ".join(row)
                if not detected_ref:
                    ref_m = re.search(r"(?:réf|ref|reference|commande)\s*[:\-]?\s*([A-Z0-9_\-]+)", row_str, re.I)
                    if ref_m:
                        detected_ref = ref_m.group(1).strip()
                if not detected_mat:
                    mat_m = re.search(r"(?:tissu|mati[èe]re|material)\s*[:\-]?\s*([^\n,;]+)", row_str, re.I)
                    if mat_m:
                        detected_mat = mat_m.group(1).strip()
                if detected_qty is None:
                    qty_m = re.search(r"quantit[ée]\s*[:\-]?\s*(\d+)", row_str, re.I)
                    if qty_m:
                        try:
                            detected_qty = int(qty_m.group(1))
                        except ValueError:
                            pass

            sheets.append(
                ExcelSheetSummary(
                    sheet_name=sheet_name,
                    row_count=len(rows_data),
                    column_count=col_count,
                    headers=headers,
                    sample_rows=sample_rows,
                    metrics=metrics,
                )
            )
        wb.close()

        sheets_desc = ", ".join(f"{s.sheet_name} ({s.row_count} lig.)" for s in sheets)
        summary_text = f"{len(sheets)} feuille(s): {sheets_desc}" if sheets else "Feuille vide"

        return ExcelSummary(
            filename=path.name,
            path=str(path),
            sheets=sheets,
            sheet_names=sheet_names,
            total_sheets=len(sheets),
            total_rows=total_rows,
            detected_reference=detected_ref,
            detected_material=detected_mat,
            detected_quantity=detected_qty,
            summary_text=summary_text,
        )
    except Exception as exc:
        logger.warning("Could not extract Excel file %s: %s", path, exc)
        return ExcelSummary(
            filename=path.name,
            path=str(path),
            summary_text=f"Analyse Excel indisponible: {exc}",
        )


# ---------------------------------------------------------------------------
# Combined Reports (PDF + Word) with Excel Resume
# ---------------------------------------------------------------------------


# --- PDF report layout -----------------------------------------------------
#
# `generate_report` used to hard-truncate everything it drew: field values at
# `str(value)[:100]`, warnings at `[:110]`, the Excel summary at `[:110]`, table
# cells at `[:20]`. The cut was silent -- a long description ended mid-word with
# no ellipsis and nothing recorded that content had been lost. Worse, the
# warnings and additional-sheets loops decremented `y` on every line without
# ever comparing it to the bottom margin, so a sheet with enough warnings drew
# them straight off the page and they vanished from the PDF entirely. The Excel
# row loop did check, but answered with `break`, silently dropping the rows it
# had not yet drawn.
#
# Values are now word-wrapped to the printable width, table cells are fitted
# with a visible ellipsis, and every loop that walks down the page starts a new
# page instead of overflowing or truncating.
_PDF_MARGIN_X = 50
_PDF_BOTTOM_MARGIN = 55
_PDF_TOP_OFFSET = 60
_ELLIPSIS = "..."


def _pdf_wrap(pdf: Any, text: str, font_name: str, font_size: float, max_width: float) -> list[str]:
    """Word-wrap `text` into lines no wider than `max_width` in the given font.

    Measures with the canvas' own `stringWidth`, so wrapping follows the width
    actually rendered rather than a guessed character count -- which is why the
    old `[:100]` limits were both too generous for wide glyphs and too strict
    for narrow ones. Tokens longer than the whole width (a long reference code,
    an unbroken path) are split across lines rather than dropped.
    """
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}" if current else word
            if pdf.stringWidth(candidate, font_name, font_size) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            while word and pdf.stringWidth(word, font_name, font_size) > max_width:
                cut = len(word)
                while cut > 1 and pdf.stringWidth(word[:cut], font_name, font_size) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
    return lines


def _pdf_fit(pdf: Any, text: str, font_name: str, font_size: float, max_width: float) -> str:
    """Truncate to `max_width` with a visible ellipsis.

    For table cells only: the column width is fixed by the number of headers, so
    wrapping there would desynchronise cells across rows. Unlike the old `[:20]`
    the cut is by measured width and is visible in the output.
    """
    value = str(text)
    if pdf.stringWidth(value, font_name, font_size) <= max_width:
        return value
    while value and pdf.stringWidth(value + _ELLIPSIS, font_name, font_size) > max_width:
        value = value[:-1]
    return value + _ELLIPSIS


def _pdf_ensure_room(
    pdf: Any,
    y: float,
    needed: float,
    height: float,
    title: str | None = None,
    title_size: float = 13,
) -> float:
    """Return a usable `y`, starting a new page when `needed` would overflow.

    `showPage` resets the font, so callers must set it again afterwards.
    """
    if y - needed >= _PDF_BOTTOM_MARGIN:
        return y
    pdf.showPage()
    y = height - _PDF_TOP_OFFSET
    if title:
        pdf.setFont("Helvetica-Bold", title_size)
        pdf.drawString(_PDF_MARGIN_X, y, title)
        y -= 22
    return y


def generate_report(
    data: ExtractedData,
    output_path: Path,
    excel_summary: ExcelSummary | None = None,
) -> Path:
    """Generate comprehensive PDF report combining technical sheet and Excel summary."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    active_excel = excel_summary or data.excel_summary

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:
        output_path.with_suffix(".json").write_text(data.model_dump_json(indent=2), encoding="utf-8")
        return output_path.with_suffix(".json")

    pdf = canvas.Canvas(str(output_path), pagesize=A4)
    width, height = A4

    # --- Page 1: Technical Sheet Data ---
    y = height - 60
    pdf.setTitle("SEAMTECH Technical Report")
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(50, y, "SEAMTECH Technical Report")
    y -= 25
    pdf.setFont("Helvetica-Oblique", 10)
    pdf.drawString(50, y, "Fiche de Fabrication & Données de Coupe")

    y -= 30
    pdf.setFont("Helvetica", 11)
    rows = [
        ("Reference", data.reference or "À vérifier"),
        ("Material", data.material or "À vérifier"),
        ("Dimensions", _format_dimensions(data)),
        ("Quantity", str(data.quantity) if data.quantity is not None else "À vérifier"),
        ("Description", data.description or "À vérifier"),
        ("Status", data.extraction_status),
        ("Confidence", f"{data.confidence:.0%}"),
    ]
    value_x = 170
    value_width = width - value_x - _PDF_MARGIN_X
    for label, value in rows:
        wrapped = _pdf_wrap(pdf, value, "Helvetica", 10, value_width)
        y = _pdf_ensure_room(pdf, y, 12 * len(wrapped) + 8, height)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(_PDF_MARGIN_X, y, label)
        pdf.setFont("Helvetica", 10)
        for offset, line in enumerate(wrapped):
            pdf.drawString(value_x, y - offset * 12, line)
        # 12pt per line plus 8pt of leading: identical to the old fixed 20pt
        # step for single-line values, so ordinary reports look unchanged.
        y -= 12 * len(wrapped) + 8

    if data.warnings:
        y -= 8
        # Room for the heading plus the first line before drawing either.
        y = _pdf_ensure_room(pdf, y, 40, height)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(_PDF_MARGIN_X, y, "Warnings")
        y -= 16
        warning_width = width - 60 - _PDF_MARGIN_X
        for warning in data.warnings:
            for line in _pdf_wrap(pdf, warning, "Helvetica", 9, warning_width):
                # Page-break guard. Without it this loop walked `y` below the
                # bottom margin and the remaining warnings were drawn off the
                # page, so they disappeared from the PDF without any error.
                y = _pdf_ensure_room(pdf, y, 14, height)
                pdf.setFont("Helvetica", 9)
                pdf.drawString(60, y, line)
                y -= 14

    # If Excel summary exists, render Excel Resume section
    if active_excel and active_excel.sheets:
        y -= 15
        pdf.setStrokeColor(colors.HexColor("#CBD5E1"))
        pdf.line(50, y, width - 50, y)
        y -= 20

        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(50, y, f"Résumé Fichier Excel — {active_excel.filename}")
        y -= 16
        pdf.setFont("Helvetica", 9)
        summary_line = (
            f"Total: {active_excel.total_sheets} feuille(s), {active_excel.total_rows} lignes. "
            f"{active_excel.summary_text}"
        )
        summary_width = width - 2 * _PDF_MARGIN_X
        for line in _pdf_wrap(pdf, summary_line, "Helvetica", 9, summary_width):
            y = _pdf_ensure_room(pdf, y, 12, height)
            pdf.setFont("Helvetica", 9)
            pdf.drawString(_PDF_MARGIN_X, y, line)
            y -= 12
        y -= 8

        # Draw first sheet table summary
        for sheet in active_excel.sheets[:2]:
            if y < 140:
                pdf.showPage()
                y = height - 60
                pdf.setFont("Helvetica-Bold", 14)
                pdf.drawString(50, y, f"Résumé Excel (suite) — {active_excel.filename}")
                y -= 25

            pdf.setFont("Helvetica-Bold", 10)
            pdf.drawString(50, y, f"Feuille: {sheet.sheet_name} ({sheet.row_count} lignes)")
            y -= 16

            # Cells are fitted to the measured column width with a visible
            # ellipsis. A fixed-width column cannot wrap without
            # desynchronising cells across rows, but `[:20]` cut by character
            # count regardless of how wide the text rendered, and cut silently.
            col_width = (width - 2 * _PDF_MARGIN_X) / max(1, min(6, len(sheet.headers)))
            cell_pad = 4

            # Render table header
            col_x = _PDF_MARGIN_X
            for h in sheet.headers[:6]:
                pdf.setFont("Helvetica-Bold", 8)
                pdf.drawString(col_x, y, _pdf_fit(pdf, h, "Helvetica-Bold", 8, col_width - cell_pad))
                col_x += col_width
            y -= 12

            # Render rows. This loop used to end with `if y < 60: break`, which
            # silently discarded every row not yet drawn; rows now continue onto
            # a new page.
            for r in sheet.sample_rows[:6]:
                y = _pdf_ensure_room(pdf, y, 11, height)
                pdf.setFont("Helvetica", 8)
                col_x = _PDF_MARGIN_X
                for cell in r[:6]:
                    pdf.drawString(col_x, y, _pdf_fit(pdf, cell, "Helvetica", 8, col_width - cell_pad))
                    col_x += col_width
                y -= 11
            y -= 15

    # If dossier contains additional technical sheets, render them
    if data.additional_sheets:
        if y < 140:
            pdf.showPage()
            y = height - 60
            pdf.setFont("Helvetica-Bold", 13)
            pdf.drawString(50, y, f"Autres Fiches Techniques du Dossier ({len(data.additional_sheets)})")
            y -= 20
        else:
            y -= 15
            pdf.setFont("Helvetica-Bold", 11)
            pdf.drawString(50, y, f"Autres Fiches Techniques du Dossier ({len(data.additional_sheets)})")
            y -= 16

        for extra in data.additional_sheets:
            dims_str = (
                f"{extra.dimensions.length_mm}x{extra.dimensions.width_mm} mm"
                if extra.dimensions.length_mm
                else "N/A"
            )
            line = (
                f"• {extra.filename}: Réf {extra.reference or 'N/A'} | Mat {extra.material or 'N/A'} | "
                f"Dim {dims_str} | Qté {extra.quantity or 1}"
            )
            sheet_width = width - 55 - _PDF_MARGIN_X
            for wrapped in _pdf_wrap(pdf, line, "Helvetica", 8, sheet_width):
                # No page-break guard existed here at all: a dossier with enough
                # extra sheets ran the list off the bottom of the page.
                y = _pdf_ensure_room(pdf, y, 12, height)
                pdf.setFont("Helvetica", 8)
                pdf.drawString(55, y, wrapped)
                y -= 12

    pdf.save()
    return output_path


def generate_docx_report(
    data: ExtractedData,
    output_path: Path,
    excel_summary: ExcelSummary | None = None,
) -> Path:
    """Generate Word report combining PDF technical sheet data and Excel BOM summary."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    active_excel = excel_summary or data.excel_summary

    try:
        from docx import Document as DocxDocument
        from docx.shared import Pt
    except ImportError:
        fallback = output_path.with_suffix(".json")
        fallback.write_text(data.model_dump_json(indent=2), encoding="utf-8")
        return fallback

    doc = DocxDocument()
    style = doc.styles["Normal"]
    style.font.size = Pt(11)

    # 1. Technical Sheet Section
    doc.add_heading("SEAMTECH Technical Report", level=1)
    table = doc.add_table(rows=1, cols=2)
    table.style = "Light Grid Accent 1"
    header = table.rows[0].cells
    header[0].text = "Field"
    header[1].text = "Value"
    for label, value in [
        ("Reference", data.reference or "À vérifier"),
        ("Material", data.material or "À vérifier"),
        ("Dimensions", _format_dimensions(data)),
        ("Quantity", str(data.quantity) if data.quantity is not None else "À vérifier"),
        ("Description", data.description or "À vérifier"),
        ("Status", data.extraction_status),
        ("Confidence", f"{data.confidence:.0%}"),
    ]:
        row = table.add_row().cells
        row[0].text = label
        row[1].text = str(value)

    if data.warnings:
        doc.add_heading("Warnings", level=2)
        for warning in data.warnings:
            doc.add_paragraph(warning, style="List Bullet")

    # 2. Excel Resume Section
    if active_excel and active_excel.sheets:
        doc.add_heading(f"Résumé Fichier Excel — {active_excel.filename}", level=1)
        doc.add_paragraph(
            f"Fichier associé: {active_excel.filename} | Total: {active_excel.total_sheets} feuille(s), "
            f"{active_excel.total_rows} lignes. {active_excel.summary_text}"
        )

        for sheet in active_excel.sheets:
            doc.add_heading(f"Feuille: {sheet.sheet_name} ({sheet.row_count} lignes)", level=2)
            if sheet.headers:
                excel_table = doc.add_table(rows=1, cols=len(sheet.headers))
                excel_table.style = "Table Grid"
                hdr_cells = excel_table.rows[0].cells
                for i, title in enumerate(sheet.headers):
                    hdr_cells[i].text = str(title)

                for r in sheet.sample_rows:
                    row_cells = excel_table.add_row().cells
                    for i in range(len(sheet.headers)):
                        val = r[i] if i < len(r) else ""
                        row_cells[i].text = str(val)

    # 3. Multi-Sheet Technical Dossier Section
    if data.additional_sheets:
        doc.add_heading(f"Autres Fiches Techniques du Dossier ({len(data.additional_sheets)})", level=1)
        extra_table = doc.add_table(rows=1, cols=5)
        extra_table.style = "Table Grid"
        hdr = extra_table.rows[0].cells
        hdr[0].text = "Fichier"
        hdr[1].text = "Référence"
        hdr[2].text = "Matière"
        hdr[3].text = "Dimensions"
        hdr[4].text = "Quantité"
        for extra in data.additional_sheets:
            row = extra_table.add_row().cells
            row[0].text = extra.filename
            row[1].text = extra.reference or "À vérifier"
            row[2].text = extra.material or "À vérifier"
            dims_str = (
                f"{extra.dimensions.length_mm} × {extra.dimensions.width_mm} mm"
                if extra.dimensions.length_mm
                else "À vérifier"
            )
            row[3].text = dims_str
            row[4].text = str(extra.quantity or "1")

    doc.save(str(output_path))
    return output_path


def _format_dimensions(data: ExtractedData) -> str:
    d = data.dimensions
    values = [d.length, d.width, d.height]
    present = [str(int(v)) if v is not None and float(v).is_integer() else str(v) for v in values if v is not None]
    text = " × ".join(present) + (f" {d.unit}" if present and d.unit else "")
    if text and d.unit_normalized and d.length_mm is not None and d.width_mm is not None:
        text += f" ({_compact(d.length_mm)} × {_compact(d.width_mm)} mm)"
    return text or "À vérifier"


def _compact(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


# ---------------------------------------------------------------------------
# Paths & validation
# ---------------------------------------------------------------------------


def staging_root(config: AppConfig) -> Path:
    return config.database_path.parent / "uploads"


def quarantine_root(config: AppConfig) -> Path:
    """Directory for imports whose S3 upload failed — never swept by retention."""
    return config.database_path.parent / "quarantine"


def _validated_source(source: Path, config: AppConfig) -> Path:
    resolved = source.expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError("Import path must be an existing directory")
    allowed = [root.expanduser().resolve() for root in config.root_paths]
    try:
        staging = staging_root(config).resolve()
    except OSError:
        staging = staging_root(config)
    allowed.append(staging)
    if not any(resolved == root or root in resolved.parents for root in allowed):
        raise PermissionError("Import path is outside configured search roots")
    return resolved


def _walk_files(source: Path) -> list[Path]:
    return sorted((p for p in source.rglob("*") if p.is_file()), key=lambda p: str(p).lower())


# ---------------------------------------------------------------------------
# Phase 1 — scan & classify (no side effects)
# ---------------------------------------------------------------------------


def scan_folder(source: Path, config: AppConfig) -> dict[str, Any]:
    resolved = _validated_source(source, config)
    candidates: list[dict[str, Any]] = []
    excel_files: list[dict[str, Any]] = []
    files_detected = 0

    for path in _walk_files(resolved):
        files_detected += 1
        ext = path.suffix.lower()
        if ext == ".pdf":
            extracted = extract_structured_pdf(path, config)
            anchors = matched_anchors(extracted.raw_text) if extracted.raw_text else []
            classification = classify_pdf_text(extracted.raw_text) if extracted.raw_text else "plan_pdf"
            # Return ALL PDFs with anchor score as ranking hint (4.8), not filter
            candidates.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "size": path.stat().st_size,
                    "anchors_matched": anchors,
                    "anchor_count": len(anchors),
                    "classification": classification,
                    "is_technical": classification == "technical_pdf",
                }
            )
        elif ext in {".xlsx", ".xls"}:
            summary = extract_excel_summary(path)
            excel_files.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "size": path.stat().st_size,
                    "sheets": summary.sheet_names,
                    "total_rows": summary.total_rows,
                    "summary_text": summary.summary_text,
                }
            )

    warnings: list[str] = []
    technical_count = sum(1 for c in candidates if c.get("is_technical"))
    if not candidates:
        warnings.append("No PDF found")
    elif technical_count == 0:
        warnings.append("No technical PDF found — all PDFs listed with anchor scores for manual selection")
    elif technical_count > 1:
        warnings.append(f"{technical_count} technical PDFs detected; select the one to import")
    # Sort candidates by technical first, then anchor_count descending (ranking hint)
    candidates.sort(key=lambda c: (not c.get("is_technical", False), -c.get("anchor_count", 0), c.get("name", "")))

    return {
        "source_path": str(resolved),
        "files_detected": files_detected,
        "candidates": candidates,
        "excel_candidates": excel_files,
        "excel_files": excel_files,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Phase 2 — confirm & process
# ---------------------------------------------------------------------------


def import_folder(
    source: Path,
    config: AppConfig,
    index: SearchIndex,
    selected_pdf: str | Path | None = None,
    import_id: str | None = None,
    progress_callback: Callable[[str, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    selected_excel: str | Path | None = None,
) -> ImportResult:
    # 1. Disk space check
    ensure_free_space(config.database_path.parent, config.min_free_bytes)

    if cancel_check and cancel_check():
        raise ImportCancelledError("Import cancelled by user")

    if progress_callback:
        progress_callback("scanning", 10)

    resolved = _validated_source(source, config)

    actual_import_id = import_id or uuid.uuid4().hex
    report_dir = config.database_path.parent / "reports" / actual_import_id
    files: list[ImportFile] = []
    pdfs: list[Path] = []
    excels: list[Path] = []
    candidates: list[dict[str, Any]] = []
    # Cache extractions to avoid extracting each PDF twice (Phase 4.9)
    extracted_cache: dict[str, ExtractedData] = {}

    for path in _walk_files(resolved):
        if cancel_check and cancel_check():
            raise ImportCancelledError("Import cancelled by user")
        category = classify_path(path)
        status = "pending" if category in ("pdf_candidate", "excel_sheet") else "not_applicable"

        if path.suffix.lower() == ".pdf":
            extracted = extract_structured_pdf(path, config)
            extracted_cache[str(path)] = extracted
            category = classify_pdf_text(extracted.raw_text) if extracted.raw_text else "plan_pdf"
            status = "pending" if category == "technical_pdf" else "not_applicable"
            if category == "technical_pdf":
                pdfs.append(path)
                anchors = matched_anchors(extracted.raw_text)
                candidates.append(
                    {
                        "path": str(path),
                        "name": path.name,
                        "size": path.stat().st_size,
                        "anchors_matched": anchors,
                        "anchor_count": len(anchors),
                    }
                )
        elif path.suffix.lower() in {".xlsx", ".xls"}:
            excels.append(path)
            category = "excel_sheet"
            status = "pending"

        files.append(ImportFile(str(path), path.name, category, path.stat().st_size, path.suffix.lower(), status))

    warnings: list[str] = []
    technical_pdf: Path | None
    if selected_pdf is not None:
        chosen = Path(selected_pdf).expanduser()
        chosen = chosen if chosen.is_absolute() else (resolved / chosen)
        chosen = chosen.resolve()
        if not chosen.exists() or not chosen.is_file():
            raise ValueError("Selected PDF does not exist")
        if not (chosen == resolved or resolved in chosen.parents):
            raise ValueError("Selected PDF is outside the scanned folder")
        technical_pdf = chosen
        if str(chosen) not in {str(p) for p in pdfs}:
            warnings.append("Selected PDF was not classified as technical; importing it anyway")
    else:
        technical_pdf = pdfs[0] if pdfs else None
        if not pdfs:
            warnings.append("No technical PDF found")
        elif len(pdfs) > 1:
            warnings.append("Multiple PDF files detected; the first PDF was selected")

    # Detect chosen Excel sheet if any
    chosen_excel: Path | None = None
    if selected_excel is not None:
        c_excel = Path(selected_excel).expanduser()
        c_excel = c_excel if c_excel.is_absolute() else (resolved / c_excel)
        if c_excel.exists() and c_excel.is_file():
            chosen_excel = c_excel.resolve()
    elif excels:
        chosen_excel = excels[0]

    if cancel_check and cancel_check():
        raise ImportCancelledError("Import cancelled by user")

    if progress_callback:
        progress_callback("extracting", 35)

    if technical_pdf:
        cached = extracted_cache.get(str(technical_pdf))
        data = cached if cached is not None else extract_structured_pdf(technical_pdf, config)
    else:
        data = None
    extra_pdfs = [p for p in pdfs if p != technical_pdf]
    extra_extractions: dict[str, ExtractedData] = {}
    if data and extra_pdfs:
        for p in extra_pdfs:
            cached_p = extracted_cache.get(str(p))
            extracted_p = cached_p if cached_p is not None else extract_structured_pdf(p, config)
            extra_extractions[str(p)] = extracted_p
            data.additional_sheets.append(
                AnalyzedSheet(
                    filename=p.name,
                    path=str(p),
                    reference=extracted_p.reference,
                    material=extracted_p.material,
                    dimensions=extracted_p.dimensions,
                    quantity=extracted_p.quantity,
                    description=extracted_p.description,
                    extraction_status=extracted_p.extraction_status,
                    confidence=extracted_p.confidence,
                )
            )

    excel_summary = extract_excel_summary(chosen_excel) if chosen_excel else None

    # Enrich PDF data from Excel if available
    if data and excel_summary:
        data.excel_summary = excel_summary
        if not data.reference and excel_summary.detected_reference:
            data.reference = excel_summary.detected_reference
        if not data.material and excel_summary.detected_material:
            data.material = excel_summary.detected_material
        if data.quantity is None and excel_summary.detected_quantity is not None:
            data.quantity = excel_summary.detected_quantity

    if cancel_check and cancel_check():
        raise ImportCancelledError("Import cancelled by user")

    if progress_callback:
        progress_callback("generating_reports", 55)

    report_path = (
        generate_report(data, report_dir / "technical-report.pdf", excel_summary=excel_summary) if data else None
    )
    report_docx_path = (
        generate_docx_report(data, report_dir / "technical-report.docx", excel_summary=excel_summary) if data else None
    )

    if cancel_check and cancel_check():
        raise ImportCancelledError("Import cancelled by user")

    if progress_callback:
        progress_callback("uploading", 75)

    all_upload_targets = [Path(f.path) for f in files] + [p for p in (report_path, report_docx_path) if p]
    upload_batch = upload_artifacts_to_storage(
        folder_name=resolved.name,
        files_to_upload=all_upload_targets,
        config=config,
        import_id=actual_import_id,
        source_root=resolved,
    )
    upload_status = upload_batch.status
    upload_status_by_path = {a.path: a.status for a in upload_batch.artifacts}
    artifact_by_path = {a.path: a for a in upload_batch.artifacts}
    # Use wall-clock for uploaded_at; tests mock object_exists so time is fine.
    import time as _time

    if cancel_check and cancel_check():
        raise ImportCancelledError("Import cancelled by user")

    if progress_callback:
        progress_callback("indexing", 90)

    indexed_documents = []
    for item in files:
        item_path = Path(item.path)
        doc_text = ""
        if technical_pdf and item.path == str(technical_pdf) and data:
            doc_text = data.raw_text
        elif item.path in extra_extractions:
            doc_text = extra_extractions[item.path].raw_text
        elif chosen_excel and item.path == str(chosen_excel) and excel_summary:
            doc_text = excel_summary.summary_text

        artifact = artifact_by_path.get(item.path)
        obj_key = artifact.key if artifact else None
        obj_bucket = artifact.bucket if artifact else None
        obj_status = artifact.status if artifact else upload_status_by_path.get(item.path, upload_status)
        uploaded_at = _time.time() if obj_status == "uploaded" else None

        indexed_documents.append(
            Document(
                path=item_path,
                name=item.name,
                parent_path=item_path.parent,
                extension=item.extension,
                size=item.size,
                modified_at=item_path.stat().st_mtime,
                is_dir=False,
                text=doc_text,
                category=item.category,
                object_key=obj_key,
                object_bucket=obj_bucket,
                uploaded_at=uploaded_at,
                upload_status=obj_status,
            )
        )
    index.upsert_documents(indexed_documents)

    final_files = []
    for f in files:
        if f.path == str(technical_pdf) and data:
            ext_status = data.extraction_status
            rep = str(report_path) if report_path else None
        elif f.path in extra_extractions:
            ext_status = extra_extractions[f.path].extraction_status
            rep = str(report_path) if report_path else None
        elif f.path == str(chosen_excel) and excel_summary:
            ext_status = "success"
            rep = str(report_path) if report_path else None
        else:
            ext_status = f.extraction_status
            rep = None

        artifact = artifact_by_path.get(f.path)
        per_file_status = upload_status_by_path.get(f.path, upload_status)
        obj_key = artifact.key if artifact else None
        obj_bucket = artifact.bucket if artifact else None
        uploaded_at = _time.time() if per_file_status == "uploaded" else None

        final_files.append(
            ImportFile(
                path=f.path,
                name=f.name,
                category=f.category,
                size=f.size,
                extension=f.extension,
                extraction_status=ext_status,
                report_path=rep,
                upload_status=per_file_status,
                object_key=obj_key,
                object_bucket=obj_bucket,
                uploaded_at=uploaded_at,
            )
        )

    status = "completed" if data and data.extraction_status == "success" else "needs_review" if data else "failed"
    result = ImportResult(
        import_id=actual_import_id,
        source_path=str(resolved),
        status=status,
        files_detected=len(files),
        analyzed_files=len(pdfs),
        technical_pdf=str(technical_pdf) if technical_pdf else None,
        data=data.model_dump() if data else None,
        report_path=str(report_path) if report_path else None,
        upload_status=upload_status,
        warnings=warnings + (data.warnings if data else []),
        files=final_files,
        candidates=candidates,
        report_docx_path=str(report_docx_path) if report_docx_path else None,
        excel_file=str(chosen_excel) if chosen_excel else None,
        excel_summary=excel_summary.model_dump() if excel_summary else None,
        excel_candidates=[
            {
                "path": str(p),
                "name": p.name,
                "size": p.stat().st_size,
                "anchors_matched": ["excel_workbook"],
                "anchor_count": 1,
            }
            for p in excels
        ],
        all_verified=upload_batch.all_verified,
        artifacts=[a.to_dict() for a in upload_batch.artifacts],
    )
    _save_import(index, result)

    if progress_callback:
        progress_callback("done", 100)

    return result


# ---------------------------------------------------------------------------
# Import record persistence (JSONB on Postgres, JSON TEXT on SQLite)
# ---------------------------------------------------------------------------


def _payload_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _save_import(index: SearchIndex, result: ImportResult) -> None:
    payload = _payload_text(asdict(result))
    with index.connect() as connection:
        if index.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO imports (id, source_path, status, payload, created_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (id) DO UPDATE SET
                        source_path = EXCLUDED.source_path,
                        status = EXCLUDED.status,
                        payload = EXCLUDED.payload
                    """,
                    (result.import_id, result.source_path, result.status, payload),
                )
        else:
            connection.execute(
                """
                INSERT INTO imports (id, source_path, status, payload, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    source_path = excluded.source_path,
                    status = excluded.status,
                    payload = excluded.payload
                """,
                (result.import_id, result.source_path, result.status, payload),
            )


def update_import(index: SearchIndex, import_id: str, status: str, payload: dict[str, Any]) -> None:
    """Overwrite the stored status/payload of an existing import record."""
    text = _payload_text(payload)
    with index.connect() as connection:
        if index.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE imports SET status = %s, payload = %s WHERE id = %s", (status, text, import_id))
                if cursor.rowcount == 0:
                    raise KeyError(f"Import not found: {import_id}")
        else:
            cursor = connection.execute(
                "UPDATE imports SET status = ?, payload = ? WHERE id = ?", (status, text, import_id)
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Import not found: {import_id}")


def get_import(index: SearchIndex, import_id: str) -> dict[str, Any] | None:
    with index.connect() as connection:
        if index.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute("SELECT payload FROM imports WHERE id = %s", (import_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                value = row[0]
                return value if isinstance(value, dict) else json.loads(value)
        row = connection.execute("SELECT payload FROM imports WHERE id = ?", (import_id,)).fetchone()
        return json.loads(row["payload"]) if row else None


# ---------------------------------------------------------------------------
# Manual correction — PATCH /imports/{id}
# ---------------------------------------------------------------------------

CORRECTABLE_FIELDS = ("reference", "material", "quantity", "description", "dimensions")

REQUIRED_FOR_SUCCESS = ("reference", "material", "dimensions", "quantity", "description")


def _correction_status(data: dict[str, Any]) -> tuple[str, float, list[str]]:
    filled = 0
    for key in REQUIRED_FOR_SUCCESS:
        value = data.get(key)
        if key == "dimensions":
            dims = value or {}
            if dims.get("length") is not None and dims.get("width") is not None:
                filled += 1
        elif value not in (None, ""):
            filled += 1
    confidence = filled / len(REQUIRED_FOR_SUCCESS)
    if filled == len(REQUIRED_FOR_SUCCESS):
        return "success", confidence, []
    return "partial", confidence, ["One or more fields still need review after manual correction."]


def correct_import(
    index: SearchIndex, config: AppConfig, import_id: str, corrections: dict[str, Any]
) -> dict[str, Any]:
    payload = get_import(index, import_id)
    if payload is None:
        raise KeyError(f"Import not found: {import_id}")
    stored = payload.get("data") or {}
    if not stored:
        raise ValueError("This import has no extracted data to correct")

    unknown = sorted(set(corrections) - set(CORRECTABLE_FIELDS))
    if unknown:
        raise ValueError(f"Unknown correction fields: {', '.join(unknown)}")

    merged: dict[str, Any] = {**stored}
    for key, value in corrections.items():
        if value is None:
            continue
        if key == "dimensions" and isinstance(value, dict):
            dims = dict(merged.get("dimensions") or {})
            dims.update({k: v for k, v in value.items() if v is not None})
            merged["dimensions"] = _with_normalized_units(dims)
        else:
            merged[key] = value
    status, confidence, warnings = _correction_status(merged)
    merged["extraction_status"] = status
    merged["confidence"] = confidence
    merged["warnings"] = warnings
    try:
        data = ExtractedData.model_validate(merged)
    except ValidationError as exc:
        raise ValueError(f"Corrected data is invalid: {exc}") from exc

    report_dir = _report_dir_for(payload, config, import_id)
    excel_summary = (
        ExcelSummary.model_validate(stored["excel_summary"]) if stored.get("excel_summary") else None
    )
    report_pdf = generate_report(data, report_dir / "technical-report.pdf", excel_summary=excel_summary)
    report_docx = generate_docx_report(data, report_dir / "technical-report.docx", excel_summary=excel_summary)

    technical_pdf = Path(payload["technical_pdf"]) if payload.get("technical_pdf") else None
    excel_file = Path(payload["excel_file"]) if payload.get("excel_file") else None
    extra_files = [excel_file] if excel_file else []
    folder = Path(payload.get("source_path", "")).name or "import"
    upload_batch = (
        upload_artifacts_to_storage(
            folder_name=folder,
            files_to_upload=[technical_pdf, report_pdf, report_docx] + extra_files,
            config=config,
            import_id=import_id,
            source_root=payload.get("source_path") or None,
        )
        if technical_pdf
        else UploadBatch(status="not_applicable")
    )
    upload_status = upload_batch.status
    status_by_path = {a.path: a.status for a in upload_batch.artifacts}
    artifact_by_path = {a.path: a for a in upload_batch.artifacts}

    payload["data"] = data.model_dump()
    payload["report_path"] = str(report_pdf)
    payload["report_docx_path"] = str(report_docx)
    payload["upload_status"] = upload_status
    payload["all_verified"] = upload_batch.all_verified
    payload["artifacts"] = [a.to_dict() for a in upload_batch.artifacts]
    payload["status"] = "completed" if status == "success" else "needs_review"
    payload["warnings"] = (
        [w for w in payload.get("warnings", []) if "first PDF was selected" in w or "technical PDFs detected" in w]
        + warnings
    )
    for entry in payload.get("files", []):
        if entry.get("path") in (payload.get("technical_pdf"), payload.get("excel_file")):
            entry["extraction_status"] = status
            entry["report_path"] = str(report_pdf)
        # per-file upload status
        p = entry.get("path")
        if p in status_by_path:
            entry["upload_status"] = status_by_path[p]
            artifact = artifact_by_path.get(p)
            if artifact:
                entry["object_key"] = artifact.key
                entry["object_bucket"] = artifact.bucket
                entry["verified"] = artifact.verified
    update_import(index, import_id, payload["status"], payload)
    return payload


def _report_dir_for(payload: dict[str, Any], config: AppConfig, import_id: str) -> Path:
    existing = payload.get("report_path")
    if existing:
        try:
            return Path(existing).parent
        except (TypeError, ValueError):
            pass
    return config.database_path.parent / "reports" / import_id


def retry_upload(index: SearchIndex, config: AppConfig, import_id: str) -> dict[str, Any]:
    payload = get_import(index, import_id)
    if payload is None:
        raise KeyError(f"Import not found: {import_id}")
    technical_pdf = Path(payload["technical_pdf"]) if payload.get("technical_pdf") else None
    report_pdf = Path(payload["report_path"]) if payload.get("report_path") else None
    report_docx = Path(payload["report_docx_path"]) if payload.get("report_docx_path") else None
    excel_file = Path(payload["excel_file"]) if payload.get("excel_file") else None

    # Build list of files that still need upload (status != uploaded)
    files_needing_retry: list[Path] = []
    for entry in payload.get("files", []):
        if entry.get("upload_status") != "uploaded":
            p = Path(entry.get("path", ""))
            if p.exists() or entry.get("path") in (payload.get("technical_pdf"), payload.get("excel_file")):
                # Use the stored path even if file no longer exists locally; upload will fail and be reported
                files_needing_retry.append(Path(entry["path"]))
    # Reports are not in files list; retry them if overall status not uploaded or they are missing from verified set
    # If no specific files, fall back to full set (backward compat)
    if not files_needing_retry:
        files_needing_retry = [technical_pdf, report_pdf, report_docx] + ([excel_file] if excel_file else [])
        files_needing_retry = [p for p in files_needing_retry if p is not None]
        # If previous upload was fully successful, keep empty to avoid redundant upload
        if payload.get("upload_status") == "uploaded" and all(
            e.get("upload_status") == "uploaded" for e in payload.get("files", [])
        ):
            files_needing_retry = []
    else:
        # Also include reports if they exist and need retry
        for rep in (report_pdf, report_docx):
            if rep and rep not in files_needing_retry:
                files_needing_retry.append(rep)

    folder = Path(payload.get("source_path", "")).name or "import"
    upload_batch = (
        upload_artifacts_to_storage(
            folder_name=folder,
            files_to_upload=files_needing_retry,
            config=config,
            import_id=import_id,
            source_root=payload.get("source_path") or None,
        )
        if files_needing_retry
        else UploadBatch(status="uploaded" if payload.get("upload_status") == "uploaded" else "not_applicable")
    )
    upload_status = upload_batch.status
    artifact_by_path = {a.path: a for a in upload_batch.artifacts}
    status_by_path = {a.path: a.status for a in upload_batch.artifacts}

    payload["upload_status"] = upload_status
    # Update per-file statuses with new results
    for entry in payload.get("files", []):
        p = entry.get("path")
        if p in status_by_path:
            entry["upload_status"] = status_by_path[p]
            artifact = artifact_by_path.get(p)
            if artifact:
                entry["object_key"] = artifact.key
                entry["object_bucket"] = artifact.bucket
                entry["verified"] = artifact.verified
        # If file was already uploaded and not retried, keep its existing status

    # Update top-level artifacts for completeness
    payload["artifacts"] = [a.to_dict() for a in upload_batch.artifacts]
    payload["all_verified"] = upload_batch.all_verified

    update_import(index, import_id, payload.get("status", "needs_review"), payload)
    return payload


# ---------------------------------------------------------------------------
# Lot C — porte A : dossier complet (fiche + pièces) et lots historiques.
#
# Extension volontairement MINCE : la porte B (import unitaire ci-dessus,
# Phase 0) reste inchangée ; la porte A délègue au moteur de fiches du Lot B
# et à la couche de dépôt du Lot C. Un seul pipeline, trois portes (plan §9.1).
# ---------------------------------------------------------------------------


def importer_dossier_complet(
    index: SearchIndex,
    dossier: Path,
    *,
    gabarits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Porte A : dépose un dossier complet (fiche PDF + pièces) → fiche en
    ``a_valider``. Délègue à :func:`seamtech_search.fiches.depot.deposer_dossier`
    (idempotence normcase + SHA-256, écriture entière ou pas du tout)."""
    from .fiches.depot import deposer_dossier

    return deposer_dossier(index, Path(dossier), gabarits=gabarits)


def importer_lot_dossiers(
    index: SearchIndex,
    racine: Path,
    *,
    notes: str | None = None,
    interrompre_apres: int | None = None,
) -> dict[str, Any]:
    """Porte A bis : crée et exécute un lot de dossiers (archive historique).
    Délègue à :func:`seamtech_search.fiches.depot.creer_lot` +
    :func:`seamtech_search.fiches.depot.executer_lot` (reprise = rappeler)."""
    from .fiches.depot import creer_lot, executer_lot

    id_lot = creer_lot(index, Path(racine), notes=notes)
    return {"id_lot": id_lot, "etat": executer_lot(index, id_lot, interrompre_apres=interrompre_apres)}
