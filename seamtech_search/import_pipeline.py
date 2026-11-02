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

import errno
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import asdict, dataclass, field, replace
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .anchors import TECHNICAL_ANCHORS, classify_pdf_text, matched_anchors
from .config import AppConfig
from .extractors import extract_file
from .flux_bornes import lire_par_blocs
from .indexer import SearchIndex
from .jobs import ImportCancelledError
from .models import Document
from .plateforme import exiger
from .retention import ensure_free_space
from .storage import S3StorageClient, UploadBatch, artifact_object_key, empreinte_sha256, upload_artifacts_to_storage

logger = logging.getLogger("seamtech_search.import_pipeline")


class ImportMutationEnCours(Exception):
    """Une autre correction ou reprise de cet import est déjà active."""


class ImportMutationIncertitude(Exception):
    """Le verrou a été perdu ; le résultat de la mutation peut être incertain."""


class RapportsEnRegeneration(Exception):
    """Une génération de rapports interrompue doit être relancée avant reprise."""


class PublicationIncertaine(RuntimeError):
    """Une intention d'envoi interrompue doit être réconciliée avant mutation."""


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
    # Empreinte de la copie capturée, y compris si l'envoi a échoué. Ce n'est
    # pas une preuve que le dossier entier formait un snapshot atomique.
    source_sha256: str | None = None
    # None : aucune liaison objet à établir ; False : conflit non résolu.
    catalogue_lie: bool | None = None


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
    transport_verifie: bool = False
    # Vrai entre la persistance de l'intention et la publication transactionnelle.
    # Après une interruption, ne jamais supposer qu'aucun objet n'a été écrit.
    publication_pending: bool = False


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
        try:
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
        finally:
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
        logger.warning("Could not extract Excel file (%s)", type(exc).__name__)
        return ExcelSummary(
            filename=path.name,
            path=str(path),
            summary_text=f"Analyse Excel indisponible ({type(exc).__name__}).",
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
    # Examiner tout l'arbre avant d'ouvrir un fichier. Un lien, y compris de
    # répertoire ou cassé, n'est pas un fichier déposé et peut sortir de la
    # racine autorisée. Ce contrôle ne verrouille pas le système de fichiers
    # contre une substitution concurrente après l'inventaire.
    fichiers = []
    for chemin in source.rglob("*"):
        if chemin.is_symlink():
            raise ValueError("Symbolic link or file outside the scanned folder is not allowed")
        if chemin.is_file():
            if source not in chemin.resolve().parents:
                raise ValueError("File outside the scanned folder is not allowed")
            fichiers.append(chemin)
    return sorted(fichiers, key=lambda p: str(p).lower())


@contextmanager
def _racine_scan_confinee(source: Path):
    """Ancre le scan à une arborescence ouverte sans suivre de liens POSIX."""
    # Le refus est centralisé dans ``plateforme`` : le message nomme la capacité
    # manquante et dit qu'elle ne se contourne pas par un réglage, au lieu d'un
    # ``OSError`` anonyme au milieu d'un import (vu pendant la qualification
    # Windows : rien n'indiquait s'il fallait changer de poste ou de produit).
    exiger("scan_ancre_descripteur")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(source.anchor, flags)
    try:
        for composant in source.parts[1:]:
            suivant = os.open(composant, flags, dir_fd=fd)
            os.close(fd)
            fd = suivant
        yield fd
    finally:
        os.close(fd)


#: Erreurs tolérées quand on vérifie qu'un chemin résolu est atteignable :
#: le fichier n'existe pas encore (ENOENT), les droits manquent (EACCES,
#: EPERM) ou un composant intermédiaire n'est pas un dossier (ENOTDIR).
#: ELOOP, ENAMETOOLONG, EIO… traduisent un chemin qui ne désigne rien de
#: stable et doivent faire refuser le confinement.
_ERREURS_RESOLUTION_TOLEREES = frozenset(
    valeur for valeur in (getattr(errno, nom, None) for nom in ("ENOENT", "EACCES", "EPERM", "ENOTDIR")) if valeur is not None
)


def resoudre_chemin_confine(chemin: Path) -> Path:
    """Résout ``chemin`` et refuse les arborescences qui ne se résolvent pas.

    ``Path.resolve()`` a changé de comportement : jusqu'à Python 3.12 il lève
    RuntimeError sur une boucle de liens symboliques, depuis Python 3.13 il
    s'appuie sur ``os.path.realpath(strict=False)`` qui rend le chemin
    pratiquement inchangé sans rien signaler. Un confinement fondé sur la
    résolution doit donc refuser explicitement un chemin non résoluble,
    quelle que soit la version de l'interpréteur.
    """
    try:
        # Python ≤ 3.12 lève RuntimeError sur une boucle détectée par pathlib.
        resolu = Path(chemin).expanduser().resolve()
    except (OSError, RuntimeError) as erreur:
        raise ValueError(f"Chemin non résoluble : {erreur}") from None
    try:
        # stat (et non lstat) : une boucle n'est visible qu'en suivant les liens,
        # qu'elle porte sur le dernier composant ou sur un parent.
        os.stat(resolu)
    except OSError as erreur:
        if erreur.errno not in _ERREURS_RESOLUTION_TOLEREES:
            raise ValueError(f"Chemin non résoluble : {erreur.strerror or erreur.errno}") from None
    return resolu


def _inventaire_scan_confine(racine_fd: int, source: Path) -> list[Path]:
    """Inventorie sans traverser de liens, uniquement depuis des répertoires épinglés."""
    fichiers: list[Path] = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def parcourir(fd: int, chemin: Path) -> None:
        with os.scandir(fd) as entrees:
            for entree in entrees:
                mode = entree.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    raise ValueError("Symbolic link or file outside the scanned folder is not allowed")
                enfant = chemin / entree.name
                if stat.S_ISDIR(mode):
                    enfant_fd = os.open(entree.name, flags, dir_fd=fd)
                    try:
                        parcourir(enfant_fd, enfant)
                    finally:
                        os.close(enfant_fd)
                elif stat.S_ISREG(mode):
                    fichiers.append(enfant)
                else:
                    raise ValueError("Only regular files and directories can be scanned")

    parcourir(racine_fd, source)
    return sorted(fichiers, key=lambda p: str(p).lower())


def _extension_copie(chemin: Path) -> str:
    """Garde l'extension utile sans dépasser les limites usuelles du système de fichiers."""
    extension = chemin.suffix.lower()
    return extension if len(os.fsencode(extension)) <= 200 else ""


@contextmanager
def _copie_scan_confinee(
    racine_fd: int, source: Path, chemin: Path, limite: int, *,
    metadata: dict[str, Any] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    allocation_dir: Path | None = None,
    reserve_min_bytes: int | None = None,
):
    """Lit une entrée relative sans suivre de liens, puis analyse une copie privée."""
    relatif = chemin.relative_to(source)
    if not relatif.parts or any(part in ("", ".", "..") for part in relatif.parts):
        raise ValueError("Invalid path in scanned folder")
    flags_dir = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.dup(racine_fd)
    fd = None
    try:
        for part in relatif.parts[:-1]:
            suivant = os.open(part, flags_dir, dir_fd=parent)
            os.close(parent)
            parent = suivant
        fd = os.open(relatif.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        avant = os.fstat(fd)
        if metadata is not None:
            metadata["modified_at"] = avant.st_mtime
        if not stat.S_ISREG(avant.st_mode):
            raise ValueError("Only regular files can be scanned")
        if avant.st_nlink != 1:
            raise ValueError("Files with hard links cannot be scanned")
        if avant.st_size > limite:
            raise ValueError("File exceeds scanning size limit")
        with tempfile.TemporaryDirectory(prefix="seamtech-scan-", dir=allocation_dir) as temporaire:
            if reserve_min_bytes is not None:
                ensure_free_space(temporaire, reserve_min_bytes + avant.st_size)
            copie = Path(temporaire) / ("document" + _extension_copie(chemin))
            entree = os.fdopen(fd, "rb")
            fd = None  # le contexte possède désormais le descripteur
            empreinte = hashlib.sha256() if metadata is not None else None
            with entree, copie.open("xb") as sortie:
                total = 0
                while True:
                    if cancel_check and cancel_check():
                        raise ImportCancelledError("Import cancelled by user")
                    bloc = entree.read(min(1024 * 1024, limite - total + 1))
                    if not bloc:
                        break
                    total += len(bloc)
                    if total > limite:
                        raise ValueError("File exceeds scanning size limit")
                    sortie.write(bloc)
                    if empreinte is not None:
                        empreinte.update(bloc)
                apres = os.fstat(entree.fileno())
                if total != avant.st_size or (avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns, avant.st_nlink) != (apres.st_size, apres.st_mtime_ns, apres.st_ctime_ns, apres.st_nlink):
                    raise ValueError("File changed during scanning")
            if metadata is not None and empreinte is not None:
                metadata["sha256"] = empreinte.hexdigest()
            yield copie, avant.st_size
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


@dataclass(frozen=True)
class _FichierCapture:
    copie: Path
    taille: int
    modified_at: float
    sha256: str


@contextmanager
def _capturer_fichiers_import(
    source: Path, config: AppConfig, *, cancel_check: Callable[[], bool] | None = None,
):
    """Capture chaque fichier sans lien dans un répertoire privé éphémère.

    Les copies garantissent l'identité des lectures ultérieures, mais leur
    collecte séquentielle n'est PAS un snapshot atomique de tout le dossier.
    """
    with _racine_scan_confinee(source) as racine_fd, tempfile.TemporaryDirectory(prefix="seamtech-import-") as dossier:
        temporaire = Path(dossier)
        captures: dict[Path, _FichierCapture] = {}
        inventaire = _inventaire_scan_confine(racine_fd, source)
        for nombre, chemin in enumerate(inventaire):
            if cancel_check and cancel_check():
                raise ImportCancelledError("Import cancelled by user")
            metadata: dict[str, Any] = {}
            with _copie_scan_confinee(
                racine_fd, source, chemin, config.max_file_size_bytes, metadata=metadata,
                cancel_check=cancel_check, allocation_dir=temporaire, reserve_min_bytes=config.min_free_bytes,
            ) as (copie, taille):
                destination = temporaire / f"{nombre:012d}{_extension_copie(chemin)}"
                os.replace(copie, destination)
                os.chmod(destination, 0o400)
            captures[chemin] = _FichierCapture(destination, taille, metadata["modified_at"], metadata["sha256"])
        yield captures


def _verifier_copie_capture(capture: _FichierCapture, cancel_check: Callable[[], bool] | None) -> bool:
    """Compare les octets de la copie à ceux lus pendant la capture.

    Ce contrôle ponctuel ne protège pas contre un processus du même UID qui
    modifierait puis restaurerait la copie entre deux lectures.
    """
    fd = os.open(capture.copie, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as fichier:
        avant = os.fstat(fichier.fileno())
        if not stat.S_ISREG(avant.st_mode) or avant.st_nlink != 1 or avant.st_size != capture.taille:
            return False
        digest = hashlib.sha256()
        total = 0
        while True:
            if cancel_check and cancel_check():
                raise ImportCancelledError("Import cancelled by user")
            bloc = fichier.read(1024 * 1024)
            if not bloc:
                break
            total += len(bloc)
            if total > capture.taille:
                return False
            digest.update(bloc)
        apres = os.fstat(fichier.fileno())
        if (avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns, avant.st_nlink) != (
            apres.st_size, apres.st_mtime_ns, apres.st_ctime_ns, apres.st_nlink,
        ):
            return False
        return total == capture.taille and digest.hexdigest() == capture.sha256


# ---------------------------------------------------------------------------
# Phase 1 — scan & classify (no side effects)
# ---------------------------------------------------------------------------


def scan_folder(source: Path, config: AppConfig) -> dict[str, Any]:
    resolved = _validated_source(source, config)
    candidates: list[dict[str, Any]] = []
    excel_files: list[dict[str, Any]] = []
    files_detected = 0

    with _racine_scan_confinee(resolved) as racine_fd:
        for path in _inventaire_scan_confine(racine_fd, resolved):
            files_detected += 1
            ext = path.suffix.lower()
            if ext not in {".pdf", ".xlsx", ".xls"}:
                continue
            with _copie_scan_confinee(racine_fd, resolved, path, config.max_file_size_bytes) as (copie, taille):
                if ext == ".pdf":
                    extracted = extract_structured_pdf(copie, config)
                    anchors = matched_anchors(extracted.raw_text) if extracted.raw_text else []
                    classification = classify_pdf_text(extracted.raw_text) if extracted.raw_text else "plan_pdf"
                    candidates.append(
                        {
                            "path": str(path),
                            "name": path.name,
                            "size": taille,
                            "anchors_matched": anchors,
                            "anchor_count": len(anchors),
                            "classification": classification,
                            "is_technical": classification == "technical_pdf",
                        }
                    )
                else:
                    summary = extract_excel_summary(copie)
                    excel_files.append(
                        {
                            "path": str(path),
                            "name": path.name,
                            "size": taille,
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


def _serialiser_import_initial(fonction):
    @wraps(fonction)
    def executer(
        source: Path, config: AppConfig, index: SearchIndex,
        selected_pdf: str | Path | None = None, import_id: str | None = None,
        progress_callback: Callable[[str, int], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        selected_excel: str | Path | None = None,
        publication_context: Callable[[], AbstractContextManager[Any]] | None = None,
    ) -> ImportResult:
        identifiant = import_id if import_id is not None else uuid.uuid4().hex
        with _verrou_mutation_import(index, config, identifiant):
            return fonction(source, config, index, selected_pdf, identifiant, progress_callback,
                            cancel_check, selected_excel, publication_context)
    return executer


@_serialiser_import_initial
def import_folder(
    source: Path,
    config: AppConfig,
    index: SearchIndex,
    selected_pdf: str | Path | None = None,
    import_id: str | None = None,
    progress_callback: Callable[[str, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    selected_excel: str | Path | None = None,
    publication_context: Callable[[], AbstractContextManager[Any]] | None = None,
) -> ImportResult:
    # 1. Disk space check
    ensure_free_space(config.database_path.parent, config.min_free_bytes)

    if cancel_check and cancel_check():
        raise ImportCancelledError("Import cancelled by user")

    if progress_callback:
        progress_callback("scanning", 10)

    resolved = _validated_source(source, config)

    actual_import_id = import_id if import_id is not None else uuid.uuid4().hex
    # La même contrainte que pour la correction empêche un identifiant de
    # choisir un répertoire de rapport en dehors de reports/<import_id>.
    report_dir = _report_dir_for({}, config, actual_import_id)
    if report_dir.is_relative_to(resolved) or report_dir.resolve().is_relative_to(resolved):
        raise ValueError("Le répertoire des rapports ne peut pas être dans l'arbre source")
    precedent = None
    if import_id is not None:
        precedent = get_import(index, actual_import_id)
        if precedent and precedent.get("source_path") != str(resolved):
            raise PublicationIncertaine("La source de l'import a changé : reprise suspendue.")
        if precedent and precedent.get("publication_pending"):
            raise PublicationIncertaine("Publication interrompue : réconciliation requise avant reprise.")
        if precedent and (
            any(a.get("key") for a in precedent.get("artifacts", []) if isinstance(a, dict))
            or any(f.get("object_key") for f in precedent.get("files", []) if isinstance(f, dict))
        ):
            # Même sans preuve, une clé peut désigner un objet écrit avec une
            # réponse perdue. Ne pas régénérer ni réenvoyer avant réconciliation.
            raise RuntimeError("Import déjà partiellement préservé : reprise complète suspendue")
    # La copie est gardée jusqu'à la fin des lectures, de l'envoi et de la
    # publication. Ce n'est pas un instantané atomique du dossier entier.
    with _capturer_fichiers_import(resolved, config, cancel_check=cancel_check) as captures:
        anciens_fichiers = precedent.get("files", []) if precedent else []
        if any(isinstance(f, dict) and f.get("source_sha256") is not None for f in anciens_fichiers):
            empreintes: dict[str, str] = {}
            for ancien in anciens_fichiers:
                if not isinstance(ancien, dict) or not isinstance(ancien.get("path"), str):
                    raise PublicationIncertaine("Inventaire initial invalide : reprise suspendue.")
                chemin, sha = ancien["path"], ancien.get("source_sha256")
                if chemin in empreintes or not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
                    raise PublicationIncertaine("Empreinte initiale invalide : reprise suspendue.")
                empreintes[chemin] = sha
            if (set(empreintes) != {str(chemin) for chemin in captures}
                    or any(empreintes[str(chemin)] != capture.sha256 for chemin, capture in captures.items())):
                raise PublicationIncertaine("Source différente de la capture initiale : reprise suspendue.")
        return _traiter_import_capture(
            resolved, config, index, captures, actual_import_id, report_dir,
            selected_pdf, selected_excel, progress_callback, cancel_check, publication_context,
        )


def _traiter_import_capture(
    resolved: Path, config: AppConfig, index: SearchIndex, captures: dict[Path, _FichierCapture],
    actual_import_id: str, report_dir: Path, selected_pdf: str | Path | None,
    selected_excel: str | Path | None, progress_callback: Callable[[str, int], None] | None,
    cancel_check: Callable[[], bool] | None,
    publication_context: Callable[[], AbstractContextManager[Any]] | None,
) -> ImportResult:
    files: list[ImportFile] = []
    pdfs: list[Path] = []
    excels: list[Path] = []
    candidates: list[dict[str, Any]] = []
    # Cache extractions to avoid extracting each PDF twice (Phase 4.9)
    extracted_cache: dict[str, ExtractedData] = {}

    for path, capture in captures.items():
        if cancel_check and cancel_check():
            raise ImportCancelledError("Import cancelled by user")
        category = classify_path(path)
        status = "pending" if category in ("pdf_candidate", "excel_sheet") else "not_applicable"

        if path.suffix.lower() == ".pdf":
            extracted = extract_structured_pdf(capture.copie, config)
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
                        "size": capture.taille,
                        "anchors_matched": anchors,
                        "anchor_count": len(anchors),
                    }
                )
        elif path.suffix.lower() in {".xlsx", ".xls"}:
            excels.append(path)
            category = "excel_sheet"
            status = "pending"

        files.append(ImportFile(str(path), path.name, category, capture.taille, path.suffix.lower(), status))

    warnings: list[str] = []
    technical_pdf: Path | None
    if selected_pdf is not None:
        chosen = Path(selected_pdf).expanduser()
        chosen = Path(os.path.abspath(chosen if chosen.is_absolute() else resolved / chosen))
        if resolved not in chosen.parents:
            raise ValueError("Selected PDF is outside the scanned folder")
        if chosen not in captures:
            raise ValueError("Selected PDF does not exist")
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
        c_excel = Path(os.path.abspath(c_excel if c_excel.is_absolute() else resolved / c_excel))
        if resolved not in c_excel.parents:
            raise ValueError("Selected Excel file is outside the scanned folder")
        if c_excel not in captures:
            raise ValueError("Selected Excel file does not exist")
        chosen_excel = c_excel
    elif excels:
        chosen_excel = excels[0]

    if cancel_check and cancel_check():
        raise ImportCancelledError("Import cancelled by user")

    if progress_callback:
        progress_callback("extracting", 35)

    if technical_pdf:
        cached = extracted_cache.get(str(technical_pdf))
        data = cached if cached is not None else extract_structured_pdf(captures[technical_pdf].copie, config)
    else:
        data = None
    extra_pdfs = [p for p in pdfs if p != technical_pdf]
    extra_extractions: dict[str, ExtractedData] = {}
    if data and extra_pdfs:
        for p in extra_pdfs:
            cached_p = extracted_cache.get(str(p))
            extracted_p = cached_p if cached_p is not None else extract_structured_pdf(captures[p].copie, config)
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

    excel_summary = extract_excel_summary(captures[chosen_excel].copie) if chosen_excel else None
    if excel_summary and chosen_excel:
        # Le chemin physique éphémère ne doit pas devenir l'identité métier.
        excel_summary = excel_summary.model_copy(update={"filename": chosen_excel.name, "path": str(chosen_excel)})

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

    contexte_rapports = (
        _generer_rapports_confines(data, config, actual_import_id, report_dir, excel_summary)
        if data else nullcontext((None, None, None, None))
    )
    # L'envoi utilise les copies privées produites sous le descripteur épinglé,
    # non les chemins publiés qui peuvent être substitués pendant l'envoi.
    with contexte_rapports as (report_path, report_docx_path, pdf_prive, docx_prive):
        if cancel_check and cancel_check():
            raise ImportCancelledError("Import cancelled by user")

        if progress_callback:
            progress_callback("uploading", 75)

        chemins_logiques = {str(capture.copie): chemin for chemin, capture in captures.items()}
        attendues = {str(chemin): capture.sha256 for chemin, capture in captures.items()}
        for capture in captures.values():
            if not _verifier_copie_capture(capture, cancel_check):
                raise ValueError("Une copie de capture a changé avant l'envoi")
        for prive, public in ((pdf_prive, report_path), (docx_prive, report_docx_path)):
            if prive is not None and public is not None:
                chemins_logiques[str(prive)] = public
                attendues[str(public)] = empreinte_sha256(prive)
        all_upload_targets = [capture.copie for capture in captures.values()] + [p for p in (pdf_prive, docx_prive) if p]
        stockage = S3StorageClient(config=config)
        if all_upload_targets and stockage.is_configured():
            # Persister l'intention AVANT la première écriture distante. En cas
            # de crash ou de réponse perdue, elle interdit un renvoi aveugle ;
            # elle ne prouve pas à elle seule quels objets ont été écrits.
            intention = ImportResult(
                import_id=actual_import_id, source_path=str(resolved), status="needs_review",
                files_detected=len(files), analyzed_files=len(pdfs),
                technical_pdf=str(technical_pdf) if technical_pdf else None,
                data=data.model_dump() if data else None,
                report_path=str(report_path) if report_path else None,
                report_docx_path=str(report_docx_path) if report_docx_path else None,
                upload_status="pending", warnings=warnings + (data.warnings if data else []),
                files=[replace(f, source_sha256=captures[Path(f.path)].sha256) for f in files],
                candidates=candidates, excel_file=str(chosen_excel) if chosen_excel else None,
                excel_summary=excel_summary.model_dump() if excel_summary else None,
                artifacts=[
                    {"path": chemin, "name": Path(chemin).name, "status": "pending", "verified": False,
                     "expected_sha256": sha, "expected_bucket": stockage.bucket_name,
                     "expected_key_base": artifact_object_key(
                         actual_import_id, chemin, source_root=resolved, prefix=stockage.prefix, lexical=True,
                     )}
                    for chemin, sha in attendues.items()
                ],
                publication_pending=True,
            )
            with publication_context() if publication_context is not None else index.scan_transaction():
                _save_import(index, intention)
        upload_batch = upload_artifacts_to_storage(
            folder_name=resolved.name,
            files_to_upload=all_upload_targets,
            config=config,
            import_id=actual_import_id,
            source_root=resolved,
            logical_paths=chemins_logiques,
        )
        invalide = False
        for artefact in upload_batch.artifacts:
            # Compatibilité des doubles : le stockage réel renvoie déjà le
            # chemin logique, pas le chemin de la copie éphémère.
            if artefact.path in chemins_logiques:
                logique = chemins_logiques[artefact.path]
                artefact.path, artefact.name = str(logique), logique.name
            if artefact.verified and (
                not _preuve_identifiable(artefact.to_dict())
                or artefact.sha256 != attendues.get(artefact.path)
            ):
                # L'objet peut exister, mais ce n'est pas une preuve de la
                # capture : conserver sa clé pour diagnostic, ne pas la lier.
                artefact.status = "failed"
                artefact.verified = False
                artefact.error = "Preuve absente ou objet différent de la capture"
                invalide = True
        if invalide:
            upload_batch.status = "partial" if any(a.status == "uploaded" for a in upload_batch.artifacts) else "failed"
    upload_status = upload_batch.status
    upload_status_by_path = {a.path: a.status for a in upload_batch.artifacts}
    artifact_by_path = {a.path: a for a in upload_batch.artifacts}
    chemins_attendus = {f.path for f in files} | {str(p) for p in (report_path, report_docx_path) if p}
    transport_verifie = (
        bool(chemins_attendus) and set(artifact_by_path) == chemins_attendus
        and len(upload_batch.artifacts) == len(chemins_attendus) and upload_batch.all_verified
        and all(_preuve_identifiable(a.to_dict()) and a.sha256 == attendues.get(a.path)
                for a in upload_batch.artifacts)
    )
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
        objet_verifie = bool(
            artifact and _preuve_identifiable(artifact.to_dict())
            and artifact.sha256 == captures[item_path].sha256
        )
        obj_key = artifact.key if objet_verifie else None
        obj_bucket = artifact.bucket if objet_verifie else None
        obj_status = artifact.status if artifact else upload_status_by_path.get(item.path, upload_status)
        uploaded_at = _time.time() if objet_verifie else None

        indexed_documents.append(
            Document(
                path=item_path,
                name=item.name,
                parent_path=item_path.parent,
                extension=item.extension,
                size=item.size,
                modified_at=captures[item_path].modified_at,
                is_dir=False,
                text=doc_text,
                category=item.category,
                object_key=obj_key,
                object_bucket=obj_bucket,
                uploaded_at=uploaded_at,
                upload_status=obj_status,
                lexical_path_key=True,
                source_sha256=captures[item_path].sha256,
            )
        )
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
        objet_verifie = bool(
            artifact and _preuve_identifiable(artifact.to_dict())
            and artifact.sha256 == captures[Path(f.path)].sha256
        )
        obj_key = artifact.key if objet_verifie else None
        obj_bucket = artifact.bucket if objet_verifie else None
        uploaded_at = _time.time() if objet_verifie else None

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
                source_sha256=captures[Path(f.path)].sha256,
            )
        )

    # Une clé non prouvée, un résultat omis ou un doublon peut masquer une
    # écriture distante. L'intention n'est effacée que si chaque cible possède
    # un résultat unique (y compris un échec explicite avant écriture).
    resultat_incomplet = bool(stockage.is_configured() and all_upload_targets and (
        len(upload_batch.artifacts) != len(chemins_attendus) or set(artifact_by_path) != chemins_attendus
    ))
    publication_pending = resultat_incomplet or any(
        a.key and not _preuve_identifiable(a.to_dict()) for a in upload_batch.artifacts
    )
    status = "completed" if data and data.extraction_status == "success" else "needs_review" if data else "failed"
    # « uploaded » exige une PREUVE par artefact (clé + empreinte relue ou
    # checksum serveur). Sans cette preuve, le statut global est rabaissé à
    # « partial » et l'import reste à revoir : jamais un succès annoncé sur
    # une préservation non prouvée.
    #
    # Le rabaissement est CONDITIONNEL, jamais systématique : une version
    # précédente rabaissait tout envoi réussi dès qu'une destination était
    # configurée (« partial » sur un import intégralement prouvé), ce qui
    # interdisait de qualifier la moindre réussite et faisait échouer la
    # recette sur un envoi parfaitement préservé (CI : recette-corpus-reel,
    # recette-locale). La règle tient donc aux preuves, pas à la destination.
    # « uploaded » ne s'annonce que sur PREUVE : chaque artefact porte une clé
    # et une empreinte vérifiée (relecture SHA-256 ou checksum serveur). Une
    # version antérieure rabaissait tout envoi dès qu'une destination était
    # configurée (« partial » sur un transport intégralement prouvé) : la
    # recette sur services réels ne pouvait donc jamais qualifier un envoi
    # réussi (« recette-corpus-reel » REF-001..007, « recette-locale »
    # « confirm HTTP 200 upload=partial »). La règle tient aux preuves, pas à
    # la destination.
    preservation_prouvee = not publication_pending and all(
        _preuve_identifiable(a.to_dict()) for a in upload_batch.artifacts if a.key
    )
    if upload_status == "uploaded" and not preservation_prouvee:
        upload_status = "partial"
    # Le SUCCÈS GLOBAL reste suspendu : ni l'observation ponctuelle ni les
    # preuves d'objets ne garantissent un snapshot durable ni une liaison
    # transactionnelle au catalogue. Un import reste donc « à revoir » dès
    # qu'une destination objet est configurée — « completed » ne s'annonce
    # que sur un dossier sans destination durable.
    if status == "completed" and upload_status not in ("not_configured", "not_applicable"):
        status = "needs_review"
    # Une publication INCERTAINE n'est jamais un échec clos : « failed »
    # laisserait croire que rien n'a été écrit, alors qu'un objet existe
    # peut-être. Tant que l'intention de publication n'est pas levée, le
    # dossier reste À REVOIR — c'est le seul état qui oblige à regarder.
    if publication_pending and status == "failed":
        status = "needs_review"
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
                "size": captures[p].taille,
                "anchors_matched": ["excel_workbook"],
                "anchor_count": 1,
            }
            for p in excels
        ],
        # La vérification d'un lot d'objets ne prouve pas le snapshot de
        # l'inventaire ni l'identité des octets analysés et indexés.
        all_verified=False,
        artifacts=[
            {
                **a.to_dict(),
                **({
                    "expected_sha256": attendues.get(a.path), "expected_bucket": a.bucket,
                    "expected_key_base": artifact_object_key(
                        actual_import_id, a.path, source_root=resolved, prefix=stockage.prefix, lexical=True,
                    ),
                } if not _preuve_identifiable(a.to_dict()) else {}),
            }
            for a in upload_batch.artifacts
        ] + ([
            {
                "path": chemin, "name": Path(chemin).name, "status": "pending", "verified": False,
                "error": "résultat d'envoi absent : publication incertaine",
                "expected_sha256": attendues[chemin], "expected_bucket": stockage.bucket_name,
                "expected_key_base": artifact_object_key(
                    actual_import_id, chemin, source_root=resolved, prefix=stockage.prefix, lexical=True,
                ),
            }
            for chemin in sorted(chemins_attendus - set(artifact_by_path))
        ] if resultat_incomplet else []),
        transport_verifie=transport_verifie,
        publication_pending=publication_pending,
    )
    # La publication de l'import et du catalogue partage toujours la même
    # transaction. Dans le worker durable, le contexte protège aussi le claim.
    with publication_context() if publication_context is not None else index.scan_transaction():
        # Une ligne protégée reste intacte. La conserver ne suffit cependant
        # pas à prétendre que la nouvelle capture lui est liée : on compare
        # sous la même transaction les identités et les clés réellement écrites.
        proteges: set[str] = set()
        index.upsert_documents(indexed_documents, strict=False, proteges=proteges)
        etat = index.etat_captures_catalogue([document.path_key for document in indexed_documents])
        fichiers_publication = []
        conflits = 0
        for fichier in result.files:
            cle = os.path.normcase(os.path.abspath(fichier.path))
            ligne = etat.get(cle)
            identique = bool(ligne and ligne[0] == fichier.source_sha256)
            objet_identique = bool(identique and fichier.object_key and
                                   ligne[1:] == (fichier.object_key, fichier.object_bucket))
            conflit = cle in proteges or not identique or (fichier.object_key is not None and not objet_identique)
            if conflit:
                conflits += 1
            liaison = (objet_identique and not conflit) if fichier.object_key is not None else (False if conflit else None)
            fichiers_publication.append(replace(fichier, catalogue_lie=liaison))
        if conflits:
            result = replace(
                result,
                status="needs_review" if result.status != "failed" else result.status,
                warnings=result.warnings + [
                    f"Conflit catalogue pour {conflits} fichier(s) : identité ou objet non liés, revue requise."
                ],
            )
        result = replace(result, files=fichiers_publication)
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



@contextmanager
def _generer_rapports_confines(
    data: ExtractedData, config: AppConfig, import_id: str, report_dir: Path,
    excel_summary: ExcelSummary | None,
) -> Any:
    """Publie les rapports depuis un dossier privé, ancré aux descripteurs POSIX.

    Les deux fichiers sont d'abord entièrement produits, puis renommés sans
    suivre les liens de destination. L'état persistant reste en régénération
    tant que l'ensemble n'a pas été envoyé et enregistré.
    """
    exiger("procfs_fd")
    base = Path(os.path.abspath(config.database_path.parent.expanduser()))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with _racine_scan_confinee(base) as base_fd:
        parent = os.dup(base_fd)
        try:
            for composant in ("reports", import_id):
                try:
                    os.mkdir(composant, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                suivant = os.open(composant, flags, dir_fd=parent)
                os.close(parent)
                parent = suivant
            with tempfile.TemporaryDirectory(prefix=".correction-", dir=f"/proc/self/fd/{parent}") as temporaire:
                tmp = Path(temporaire)
                pdf = generate_report(data, tmp / "technical-report.pdf", excel_summary=excel_summary)
                docx = generate_docx_report(data, tmp / "technical-report.docx", excel_summary=excel_summary)
                if pdf == docx or pdf.parent != tmp or docx.parent != tmp:
                    raise ValueError("Les rapports générés doivent avoir des chemins distincts et confinés")
                if pdf.name not in {"technical-report.pdf", "technical-report.json"} or docx.name not in {"technical-report.docx", "technical-report.json"}:
                    raise ValueError("Nom de rapport généré invalide")
                tmp_fd = os.open(tmp, flags)
                try:
                    for rapport in (pdf, docx):
                        fd = os.open(rapport.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=tmp_fd)
                        try:
                            info = os.fstat(fd)
                            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                                raise ValueError("Rapport généré non ordinaire ou lié")
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                    # Le répertoire logique peut avoir été remplacé pendant
                    # la génération : ne pas annoncer/publier ces rapports.
                    with _racine_scan_confinee(report_dir) as parent_actuel:
                        attendu, actuel = os.fstat(parent), os.fstat(parent_actuel)
                        if (attendu.st_dev, attendu.st_ino) != (actuel.st_dev, actuel.st_ino):
                            raise ValueError("Répertoire de rapports remplacé")
                    for rapport in (pdf, docx):
                        # Deux inodes distincts : une mutation du rapport
                        # publié ne doit pas changer la copie privée à envoyer.
                        nom_publication = ".rapport-" + uuid.uuid4().hex
                        try:
                            source_fd = os.open(rapport.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=tmp_fd)
                            try:
                                destination_fd = os.open(nom_publication, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                                         0o600, dir_fd=parent)
                                with os.fdopen(destination_fd, "wb") as destination, os.fdopen(source_fd, "rb", closefd=False) as source:
                                    shutil.copyfileobj(source, destination, length=1024 * 1024)
                                    destination.flush()
                                    os.fsync(destination.fileno())
                            finally:
                                os.close(source_fd)
                            os.replace(nom_publication, rapport.name, src_dir_fd=parent, dst_dir_fd=parent)
                        finally:
                            try:
                                os.unlink(nom_publication, dir_fd=parent)
                            except FileNotFoundError:
                                pass
                    os.fsync(parent)
                    yield (report_dir / pdf.name, report_dir / docx.name, pdf, docx)
                finally:
                    os.close(tmp_fd)
        finally:
            os.close(parent)

@contextmanager
def _verrou_mutation_import(index: SearchIndex | None, config: AppConfig, import_id: str):
    """Exclusion coopérative par import ; les anciennes versions ne participent pas."""
    if not isinstance(import_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", import_id):
        raise ValueError("Identifiant d'import invalide")
    digest = hashlib.sha256(("seamtech-import-mutation:" + import_id).encode("ascii")).digest()
    if index is not None and index.is_postgres:
        import psycopg2

        # Connexion indépendante du pool : elle garde le verrou de session
        # pendant les commits nécessaires à la révocation des anciens rapports.
        cle = int.from_bytes(digest[:8], byteorder="big", signed=True)
        connexion = psycopg2.connect(index.database_url, connect_timeout=10)
        connexion.autocommit = True
        verrouille = False
        erreur_operation = False
        try:
            with connexion.cursor() as curseur:
                curseur.execute("SELECT pg_try_advisory_lock(%s)", (cle,))
                verrouille = bool(curseur.fetchone()[0])
            if not verrouille:
                raise ImportMutationEnCours("Une opération est déjà en cours sur cet import.")
            try:
                yield
            except BaseException:
                erreur_operation = True
                raise
        finally:
            try:
                if verrouille:
                    try:
                        with connexion.cursor() as curseur:
                            curseur.execute("SELECT pg_advisory_unlock(%s)", (cle,))
                            libere = bool(curseur.fetchone()[0])
                        if not libere and not erreur_operation:
                            raise ImportMutationIncertitude("État de la mutation incertain : verrou perdu.")
                    except psycopg2.Error:
                        logger.warning("Libération du verrou d'import impossible : connexion abandonnée")
                        if not erreur_operation:
                            raise ImportMutationIncertitude("État de la mutation incertain : verrou perdu.") from None
            finally:
                connexion.close()
        return

    exiger("verrou_flock")
    import fcntl

    base = Path(os.path.abspath(config.database_path.parent.expanduser()))
    with _racine_scan_confinee(base) as base_fd:
        try:
            os.mkdir("import-locks", mode=0o700, dir_fd=base_fd)
        except FileExistsError:
            pass
        dossier_fd = os.open("import-locks", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base_fd)
        try:
            fd = os.open(digest.hex(), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=dossier_fd)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("Fichier de verrou d'import non sûr")
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ImportMutationEnCours("Une opération est déjà en cours sur cet import.") from None
                try:
                    yield
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        finally:
            os.close(dossier_fd)


def _serialiser_mutation_import(fonction):
    @wraps(fonction)
    def executer(index: SearchIndex, config: AppConfig, import_id: str, *args: Any, **kwargs: Any):
        with _verrou_mutation_import(index, config, import_id):
            return fonction(index, config, import_id, *args, **kwargs)
    return executer


@_serialiser_mutation_import
def correct_import(
    index: SearchIndex, config: AppConfig, import_id: str, corrections: dict[str, Any]
) -> dict[str, Any]:
    payload = get_import(index, import_id)
    if payload is None:
        raise KeyError(f"Import not found: {import_id}")
    if payload.get("publication_pending"):
        raise PublicationIncertaine("Publication interrompue : réconciliation requise avant correction.")
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
    # Contrôle AVANT la génération : ces fonctions ouvrent leurs sorties en
    # écriture et ne doivent jamais réutiliser une pièce d'origine.
    source_path = payload.get("source_path")
    racine_source = Path(source_path).resolve() if source_path else None
    fichiers_sources = {Path(e["path"]).resolve() for e in payload.get("files", []) if e.get("path")}
    sorties_possibles = [report_dir / f"technical-report.{ext}" for ext in ("pdf", "docx", "json")]
    for sortie in sorties_possibles:
        cible = sortie.resolve()
        if cible in fichiers_sources or (racine_source is not None and cible.is_relative_to(racine_source)):
            raise ValueError("Un rapport ne peut pas remplacer un fichier source")
        try:
            existant = sortie.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(existant.st_mode) or existant.st_nlink != 1:
                raise ValueError("Le rapport existant doit être un fichier ordinaire non lié")
    # Révoquer et persister les preuves AVANT toute écriture. Une panne pendant
    # la régénération ne doit pas laisser une ancienne preuve valable en base.
    payload["report_path"] = str(report_dir / "technical-report.pdf")
    payload["report_docx_path"] = str(report_dir / "technical-report.docx")
    manifeste_avant = construire_manifeste(payload)
    for entree in manifeste_avant:
        if entree["role"] in ("rapport_pdf", "rapport_docx"):
            entree.update({"status": "pending", "verified": False, "key": None, "bucket": None,
                           "verification": "non_verifie", "verification_detail": None, "sha256": None,
                           "error": "rapport en cours de régénération"})
    payload["artifacts"] = manifeste_avant
    payload["report_generation_pending"] = True
    # Les données corrigées restent distinctes des données déjà publiées tant
    # que les nouveaux rapports n'ont pas été enregistrés avec leur preuve.
    payload["pending_correction_data"] = data.model_dump()
    if payload.get("status") != "cancelled":
        payload["status"] = "needs_review"
    payload["all_verified"] = False
    payload["transport_verifie"] = False
    payload["upload_status"] = "partial"
    payload["cleanup"] = {"purge": False, "raison": "régénération des rapports en cours"}
    payload["preservation"] = {
        "upload_status": "partial", "integrite_prouvee": False,
        "artefacts_total": len(manifeste_avant),
        "artefacts_verifies": sum(_preuve_artefact_valide(e) for e in manifeste_avant),
        "artefacts_non_verifies": [e["path"] for e in manifeste_avant if not _preuve_artefact_valide(e)],
        "destination_configuree": any(e.get("key") for e in manifeste_avant),
        "transport_verifie": False,
    }
    payload["inventaire"] = {
        "total": len(manifeste_avant), "verifies": payload["preservation"]["artefacts_verifies"],
        "manquants_localement": [], "renvoyes": 0,
        "conserves": payload["preservation"]["artefacts_verifies"],
    }
    payload["files_uploaded"] = payload["preservation"]["artefacts_verifies"]
    update_import(index, import_id, str(payload.get("status") or "needs_review"), payload)
    excel_summary = (
        ExcelSummary.model_validate(stored["excel_summary"]) if stored.get("excel_summary") else None
    )
    # Envoi depuis les fichiers temporaires ancrés au descripteur ; le chemin
    # public du rapport peut être remplacé après publication.
    with _generer_rapports_confines(data, config, import_id, report_dir, excel_summary) as sorties:
        report_pdf, report_docx, copie_pdf, copie_docx = sorties
        ancien_manifeste = {e["path"]: e for e in construire_manifeste(payload)}
        chemins_rapports = {str(report_pdf), str(report_docx)}
        if any(e["path"] in chemins_rapports for e in ancien_manifeste.values()
               if e["role"] not in ("rapport_pdf", "rapport_docx")):
            raise ValueError("Un rapport ne peut pas remplacer un fichier source")
        folder = Path(payload.get("source_path", "")).name or "import"
        chemins_logiques = {str(copie_pdf): report_pdf, str(copie_docx): report_docx}
        attendues = {str(report_pdf): empreinte_sha256(copie_pdf), str(report_docx): empreinte_sha256(copie_docx)}
        stockage = S3StorageClient(config=config)
        if stockage.is_configured():
            for rapport, role in ((report_pdf, "rapport_pdf"), (report_docx, "rapport_docx")):
                chemin = str(rapport)
                entree = ancien_manifeste.setdefault(chemin, {
                    "path": chemin, "name": rapport.name, "role": role, "required": True,
                    "status": "pending", "verified": False,
                })
                entree.update({
                    "expected_sha256": attendues[chemin], "expected_bucket": stockage.bucket_name,
                    "expected_key_base": artifact_object_key(
                        import_id, chemin, source_root=payload.get("source_path") or None,
                        prefix=stockage.prefix, lexical=True,
                    ),
                })
            payload["artifacts"] = list(ancien_manifeste.values())
            payload["publication_pending"] = True
            # La révocation et l'intention sont durables avant l'envoi des
            # nouveaux rapports ; un crash impose une réconciliation.
            update_import(index, import_id, str(payload.get("status") or "needs_review"), payload)
        upload_batch = upload_artifacts_to_storage(
            folder_name=folder, files_to_upload=[copie_pdf, copie_docx], config=config,
            import_id=import_id, source_root=payload.get("source_path") or None, logical_paths=chemins_logiques,
        )
        invalide = False
        for artefact in upload_batch.artifacts:
            # Compatibilité des doubles : le stockage réel renvoie déjà
            # l'identité logique, et non le chemin de la copie temporaire.
            if artefact.path in chemins_logiques:
                logique = chemins_logiques[artefact.path]
                artefact.path, artefact.name = str(logique), logique.name
            if artefact.path not in chemins_rapports:
                raise ValueError("Résultat de préservation hors des rapports")
            if artefact.verified and (
                not _preuve_identifiable(artefact.to_dict()) or artefact.sha256 != attendues[artefact.path]
            ):
                artefact.status, artefact.verified = "failed", False
                artefact.error = "Preuve absente ou objet différent du rapport généré"
                invalide = True
        if invalide:
            upload_batch.status = "partial" if any(a.status == "uploaded" for a in upload_batch.artifacts) else "failed"
    resultats = {a.path: a for a in upload_batch.artifacts}
    if set(resultats) - chemins_rapports:
        raise ValueError("Résultat de préservation hors des rapports")
    for rapport, role in ((report_pdf, "rapport_pdf"), (report_docx, "rapport_docx")):
        chemin = str(rapport)
        artefact = resultats.get(chemin)
        # Les rapports viennent d'être régénérés : une preuve d'une version
        # antérieure du même chemin ne prouve pas ces nouveaux octets.
        ancien_manifeste[chemin] = {
            "path": chemin, "name": rapport.name, "key": artefact.key if artefact else None,
            "bucket": artefact.bucket if artefact else None,
            "status": artefact.status if artefact else "pending",
            "verified": bool(artefact and artefact.verified),
            "verification": artefact.verification if artefact else "non_verifie",
            "verification_detail": artefact.verification_detail if artefact else None,
            "sha256": artefact.sha256 if artefact and artefact.verified else None,
            "error": artefact.error if artefact else "nouveau rapport non préservé",
            "required": True, "role": role, "uploaded_at": None,
            **({
                "expected_sha256": attendues[chemin], "expected_bucket": stockage.bucket_name,
                "expected_key_base": artifact_object_key(
                    import_id, chemin, source_root=payload.get("source_path") or None,
                    prefix=stockage.prefix, lexical=True,
                ),
            } if not artefact or not _preuve_identifiable(artefact.to_dict()) else {}),
        }
    manifeste = [ancien_manifeste[chemin] for chemin in sorted(ancien_manifeste)]
    destination_configuree = upload_batch.status not in ("not_configured", "not_applicable") or any(
        e.get("key") for e in manifeste
    )
    upload_status, preuves_acquises = agreger_preservation(manifeste, destination_configuree=destination_configuree)
    # Un résultat omis ou dupliqué n'établit pas qu'aucun effet distant n'a eu
    # lieu. Conserver l'intention, même si toutes les preuves reçues sont bonnes.
    resultat_incomplet = stockage.is_configured() and (
        len(upload_batch.artifacts) != len(chemins_rapports) or set(resultats) != chemins_rapports
    )
    publication_pending = bool(resultat_incomplet or any(
        e.get("key") and not _preuve_identifiable(e) for e in manifeste
    ))
    transport_verifie = not publication_pending and preuves_acquises and all(
        _preuve_identifiable(e) for e in manifeste
    )
    # Le transport prouvé ne prouve ni le snapshot source ni la liaison des
    # documents : la correction ne peut pas rétablir le succès global.
    all_verified = False
    if upload_status == "uploaded":
        upload_status = "partial"
    non_verifies = [e["path"] for e in manifeste if not _preuve_artefact_valide(e)]
    payload["data"] = data.model_dump()
    payload.pop("pending_correction_data", None)
    payload["report_generation_pending"] = False
    payload["report_path"] = str(report_pdf)
    payload["report_docx_path"] = str(report_docx)
    payload["upload_status"] = upload_status
    payload["all_verified"] = all_verified
    payload["transport_verifie"] = transport_verifie
    payload["publication_pending"] = publication_pending
    payload["artifacts"] = manifeste
    payload["inventaire"] = {
        "total": len(manifeste), "verifies": len(manifeste) - len(non_verifies),
        "manquants_localement": [], "renvoyes": len(resultats), "conserves": len(manifeste) - len(chemins_rapports),
    }
    payload["preservation"] = {
        "upload_status": upload_status, "integrite_prouvee": all_verified,
        "artefacts_total": len(manifeste), "artefacts_verifies": len(manifeste) - len(non_verifies),
        "artefacts_non_verifies": non_verifies, "destination_configuree": destination_configuree,
        "transport_verifie": transport_verifie,
        "raison_integrite": "succès global suspendu : snapshot et liaison catalogue non prouvés",
    }
    payload["cleanup"] = {
        "purge": False,
        "raison": "purge automatique suspendue : identité actuelle du dossier non prouvée",
        "artefacts_non_verifies": non_verifies,
    }
    payload["files_uploaded"] = len(manifeste) - len(non_verifies)
    payload["status"] = "completed" if status == "success" and not destination_configuree else "needs_review"
    payload["warnings"] = (
        [w for w in payload.get("warnings", []) if "first PDF was selected" in w or "technical PDFs detected" in w]
        + warnings
    )
    for entry in payload.get("files", []):
        if entry.get("path") in (payload.get("technical_pdf"), payload.get("excel_file")):
            entry["extraction_status"] = status
            entry["report_path"] = str(report_pdf)
    update_import(index, import_id, payload["status"], payload)
    return payload


def _report_dir_for(payload: dict[str, Any], config: AppConfig, import_id: str) -> Path:
    # Un chemin persistant n'autorise pas à écrire arbitrairement sur le disque.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", import_id):
        raise ValueError("Identifiant d'import invalide")
    attendu = Path(os.path.abspath(config.database_path.parent / "reports" / import_id))
    for cle in ("report_path", "report_docx_path"):
        existant = payload.get(cle)
        if existant:
            try:
                parent = Path(os.path.abspath(Path(existant).expanduser())).parent
            except (TypeError, ValueError, OSError):
                raise ValueError("Chemin de rapport invalide") from None
            if parent != attendu:
                raise ValueError("Chemin de rapport hors du dossier de l'import")
    return attendu


def construire_manifeste(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Inventaire COMPLET et autoritatif de ce que l'import doit préserver.

    Défaut corrigé (A07 de l'audit du 2026-10-08) : ``retry_upload`` remplaçait
    ``payload["artifacts"]`` par les seuls fichiers qu'il venait de renvoyer et
    recalculait ``all_verified`` sur ce sous-ensemble. Un renvoi de rapports
    seuls — les originaux restant en échec — produisait donc
    ``upload_status=uploaded`` et ``all_verified=true`` : un succès TOTAL
    annoncé sur une préservation PARTIELLE, et une purge locale autorisée sur
    cette base.

    Le manifeste est la liste de TOUT ce que l'import a produit et qui doit
    survivre : chaque fichier détecté, PLUS les rapports générés. Les preuves
    déjà acquises (clé d'objet, empreinte vérifiée) sont RECONDUITES telles
    quelles — un renvoi partiel ne réécrit jamais le passé.
    """
    entetes_connues = {
        "path", "name", "key", "bucket", "status", "verified", "verification",
        "verification_detail", "error", "required", "role", "uploaded_at", "sha256",
        "expected_sha256", "expected_bucket", "expected_key_base",
    }
    # 1. Preuves acquises lors des envois PRÉCÉDENTS (jamais perdues).
    preuves: dict[str, dict[str, Any]] = {}
    for artefact in payload.get("artifacts") or []:
        if not isinstance(artefact, dict) or not artefact.get("path"):
            continue
        preuves[str(artefact["path"])] = {
            cle: artefact[cle] for cle in entetes_connues if cle in artefact
        }

    manifeste: dict[str, dict[str, Any]] = {}

    def _entree(chemin: str, nom: str, role: str, statut: str, cle_objet: str | None,
                bucket_objet: str | None = None) -> dict[str, Any]:
        precedente = preuves.get(chemin, {})
        cle = cle_objet or precedente.get("key")
        bucket = bucket_objet or precedente.get("bucket")
        preuve_coherente = (cle == precedente.get("key") and bucket == precedente.get("bucket"))
        entree: dict[str, Any] = {
            "path": chemin,
            "name": nom,
            "key": cle,
            "bucket": bucket,
            "status": statut,
            "verified": bool(precedente.get("verified")) and statut == "uploaded" and preuve_coherente and bool(cle) and bool(bucket),
            "verification": precedente.get("verification", "non_verifie"),
            "verification_detail": precedente.get("verification_detail"),
            "sha256": precedente.get("sha256") if preuve_coherente and precedente.get("verified") else None,
            "error": precedente.get("error"),
            "required": True,
            "role": role,
            "uploaded_at": precedente.get("uploaded_at"),
        }
        for cle_attendue in ("expected_sha256", "expected_bucket", "expected_key_base"):
            if cle_attendue in precedente:
                entree[cle_attendue] = precedente[cle_attendue]
        if entree["status"] == "uploaded" and not entree["verified"]:
            # « uploaded » sans preuve d'intégrité n'est PAS une préservation :
            # l'entrée reste à renvoyer (ou à re-vérifier), jamais comptée comme
            # acquise.
            entree["error"] = entree["error"] or "intégrité non prouvée lors d'un envoi antérieur"
        return entree

    for entree_fichier in payload.get("files") or []:
        chemin = str(entree_fichier.get("path") or "")
        if not chemin:
            continue
        manifeste[chemin] = _entree(
            chemin,
            str(entree_fichier.get("name") or Path(chemin).name),
            str(entree_fichier.get("category") or "piece"),
            str(entree_fichier.get("upload_status") or "pending"),
            entree_fichier.get("object_key"),
            entree_fichier.get("object_bucket"),
        )

    # 2. Les rapports générés ne figurent pas dans ``files`` : sans cette
    #    entrée, « tout préserver » ne les couvrirait pas — et c'est justement
    #    par là que le sous-ensemble passait pour l'ensemble.
    for cle, role in (("report_path", "rapport_pdf"), ("report_docx_path", "rapport_docx")):
        chemin = payload.get(cle)
        if not chemin:
            continue
        chemin = str(chemin)
        if chemin in manifeste:
            continue
        manifeste[chemin] = _entree(
            chemin, Path(chemin).name, role, str(preuves.get(chemin, {}).get("status") or "pending"), None
        )

    # 3. Un rapport annoncé « envoyé » seulement parce que le drapeau global
    #    l'était n'a aucune preuve : la clé d'objet manquante le trahit.
    for entree in manifeste.values():
        if entree["status"] == "uploaded" and not entree["key"]:
            entree["status"] = "pending"
            entree["verified"] = False
            entree["error"] = "clé d'objet absente : envoi non prouvé"
    return [manifeste[chemin] for chemin in sorted(manifeste)]


def _preuve_artefact_valide(entree: dict[str, Any]) -> bool:
    return bool(entree.get("status") == "uploaded" and entree.get("verified") and entree.get("key") and entree.get("bucket"))


def _preuve_identifiable(entree: dict[str, Any]) -> bool:
    sha = entree.get("sha256")
    return bool(
        _preuve_artefact_valide(entree)
        and entree.get("verification") in {"relecture_sha256", "checksum_serveur"}
        and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)
    )


def agreger_preservation(
    manifeste: list[dict[str, Any]], *, destination_configuree: bool
) -> tuple[str, bool]:
    """Statut global et ``all_verified`` calculés sur l'INVENTAIRE ENTIER.

    Retourne ``(upload_status, all_verified)``. ``all_verified`` n'est vrai que
    si CHAQUE entrée requise porte une clé d'objet et une intégrité vérifiée —
    jamais parce que le sous-ensemble renvoyé à l'instant a réussi.
    """
    if not manifeste:
        return ("not_applicable", False)
    if not destination_configuree:
        return ("not_configured", False)
    verifies = [e for e in manifeste if _preuve_artefact_valide(e)]
    if len(verifies) == len(manifeste):
        return ("uploaded", True)
    if verifies:
        return ("partial", False)
    return ("failed", False)


def _identite_locale_reprise(
    manifeste: list[dict[str, Any]], source: Path, report_dir: Path, limite: int,
) -> bool:
    """Comparer l'inventaire et les octets à la preuve stockée, sans suivre de liens.

    Cette observation n'est pas un snapshot transactionnel : elle ne justifie
    jamais de supprimer la source et peut devenir périmée dès son retour.
    """
    try:
        with ExitStack() as pile:
            racines: dict[Path, int] = {}
            for entree in manifeste:
                racine = report_dir if entree["role"] in ("rapport_pdf", "rapport_docx") else source
                if racine not in racines:
                    racines[racine] = pile.enter_context(_racine_scan_confinee(racine))
            if source not in racines:
                return False
            attendus = {Path(e["path"]) for e in manifeste if e["role"] not in ("rapport_pdf", "rapport_docx")}
            if set(_inventaire_scan_confine(racines[source], source)) != attendus:
                return False
            for entree in manifeste:
                sha = entree.get("sha256")
                if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
                    return False
                racine = report_dir if entree["role"] in ("rapport_pdf", "rapport_docx") else source
                with _copie_scan_confinee(racines[racine], racine, Path(entree["path"]), limite) as (copie, _):
                    digest = hashlib.sha256()
                    with copie.open("rb") as flux:
                        for bloc in lire_par_blocs(flux, 1024 * 1024, nom="vérification de manifeste"):
                            digest.update(bloc)
                    if digest.hexdigest() != sha:
                        return False
            if set(_inventaire_scan_confine(racines[source], source)) != attendus:
                return False
            for racine, fd in racines.items():
                with _racine_scan_confinee(racine) as courant:
                    avant, apres = os.fstat(fd), os.fstat(courant)
                    if (avant.st_dev, avant.st_ino) != (apres.st_dev, apres.st_ino):
                        return False
            return True
    except (OSError, ValueError, RuntimeError):
        return False


@_serialiser_mutation_import
def retry_upload(
    index: SearchIndex, config: AppConfig, import_id: str,
    publication_context: Callable[[], AbstractContextManager[Any]] | None = None,
) -> dict[str, Any]:
    payload = get_import(index, import_id)
    if payload is None:
        raise KeyError(f"Import not found: {import_id}")
    if payload.get("publication_pending"):
        raise PublicationIncertaine("Publication interrompue : réconciliation requise avant reprise.")
    if payload.get("report_generation_pending"):
        raise RapportsEnRegeneration("Régénération des rapports inachevée : relancer la correction avant l'envoi.")

    manifeste = construire_manifeste(payload)
    empreintes_capture: dict[str, str] = {}
    for fichier in payload.get("files") or []:
        if not isinstance(fichier, dict) or fichier.get("source_sha256") is None:
            continue  # anciens imports : aucune identité initiale n'est inventée
        sha = fichier["source_sha256"]
        chemin = str(fichier.get("path") or "")
        if not chemin or not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
            raise ValueError("Empreinte de capture invalide pour la reprise")
        if chemin in empreintes_capture and empreintes_capture[chemin] != sha:
            raise ValueError("Empreintes de capture contradictoires")
        empreintes_capture[chemin] = sha
    # Les chemins persistés ne sont pas des autorisations. Contrôler l'ensemble
    # avant le premier envoi et ancrer ensuite chaque lecture à sa racine.
    source_brute = Path(str(payload.get("source_path") or "")).expanduser()
    if not source_brute.is_absolute() or ".." in source_brute.parts:
        raise ValueError("Racine de source invalide pour la reprise")
    source = Path(os.path.abspath(source_brute))
    racines = [Path(os.path.abspath(Path(r).expanduser())) for r in config.root_paths]
    racines += [Path(os.path.abspath(staging_root(config))), Path(os.path.abspath(quarantine_root(config)))]
    if not any(source == racine or source.is_relative_to(racine) for racine in racines):
        raise PermissionError("Source hors racines autorisées pour la reprise")
    report_dir = _report_dir_for(payload, config, import_id)
    try:
        # Python ≥ 3.13 ne lève plus sur une boucle de liens symboliques :
        # la vérification explicite garde le même refus sur toutes les versions.
        rapport_resolu = resoudre_chemin_confine(report_dir)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("Répertoire de rapports invalide pour la reprise") from None
    if report_dir.is_relative_to(source) or rapport_resolu.is_relative_to(source):
        raise ValueError("Le répertoire des rapports ne peut pas être dans l'arbre source")
    a_renvoyer: list[tuple[Path, Path]] = []
    absents: list[str] = []
    sources_modifiees: list[str] = []
    rapports_modifies: list[str] = []
    for entree in manifeste:
        chemin = Path(entree["path"])
        if not chemin.is_absolute() or ".." in chemin.parts:
            raise ValueError("Chemin d'artefact invalide pour la reprise")
        chemin = Path(os.path.abspath(chemin))
        if entree["role"] in ("rapport_pdf", "rapport_docx"):
            if chemin.parent != report_dir or chemin.name not in {"technical-report.pdf", "technical-report.docx", "technical-report.json"}:
                raise ValueError("Rapport hors du dossier de l'import")
            racine = report_dir
        else:
            if not chemin.is_relative_to(source) or chemin == source:
                raise ValueError("Fichier hors de la source de l'import")
            racine = source
        if not _preuve_artefact_valide(entree):
            a_renvoyer.append((chemin, racine))

    stockage = S3StorageClient(config=config)
    if a_renvoyer and stockage.is_configured():
        # Même pour une reprise, journaliser l'intention avant tout effet S3.
        # Le manifeste conserve les preuves précédemment acquises.
        for entree in manifeste:
            if not _preuve_artefact_valide(entree):
                empreinte = empreintes_capture.get(entree["path"]) or entree.get("expected_sha256")
                if empreinte is not None and (
                    not isinstance(empreinte, str) or re.fullmatch(r"[0-9a-f]{64}", empreinte) is None
                ):
                    raise ValueError("Empreinte d'artefact invalide pour la reprise")
                entree.update({
                    "expected_sha256": empreinte, "expected_bucket": stockage.bucket_name,
                    "expected_key_base": artifact_object_key(
                        import_id, entree["path"], source_root=source, prefix=stockage.prefix, lexical=True,
                    ),
                })
        payload["publication_pending"] = True
        payload["all_verified"] = False
        payload["artifacts"] = manifeste
        contexte = (publication_context() if publication_context is not None
                    else index.scan_transaction() if index is not None else nullcontext())
        with contexte:
            update_import(index, import_id, str(payload.get("status") or "needs_review"), payload)

    attendues_manifeste = {e["path"]: e.get("expected_sha256") for e in manifeste}
    folder = source.name or "import"
    artefacts_renvoyes = []
    statuts_lots = []
    resultat_incomplet = False
    with ExitStack() as racines_epinglees:
        descripteurs: dict[Path, int | None] = {}
        for racine in dict.fromkeys(racine for _chemin, racine in a_renvoyer):
            try:
                descripteurs[racine] = racines_epinglees.enter_context(_racine_scan_confinee(racine))
            except FileNotFoundError:
                descripteurs[racine] = None

        def verifier_racine(racine: Path, fd: int) -> None:
            with _racine_scan_confinee(racine) as courant:
                avant, apres = os.fstat(fd), os.fstat(courant)
                if (avant.st_dev, avant.st_ino) != (apres.st_dev, apres.st_ino):
                    raise ValueError("Racine de reprise remplacée pendant l'envoi")

        for chemin, racine in a_renvoyer:
            racine_fd = descripteurs[racine]
            if racine_fd is None:
                absents.append(str(chemin))
                continue
            verifier_racine(racine, racine_fd)
            with ExitStack() as pile:
                metadata: dict[str, Any] = {}
                try:
                    copie, _taille = pile.enter_context(_copie_scan_confinee(
                        racine_fd, racine, chemin, config.max_file_size_bytes, metadata=metadata,
                    ))
                except FileNotFoundError:
                    absents.append(str(chemin))
                    continue
                verifier_racine(racine, racine_fd)
                attendue = empreintes_capture.get(str(chemin)) or attendues_manifeste.get(str(chemin))
                if attendue is not None and metadata["sha256"] != attendue:
                    # Ne pas envoyer une autre version sous l'identité de la
                    # source analysée ou du rapport initialement produit.
                    (sources_modifiees if racine == source else rapports_modifies).append(str(chemin))
                    continue
                lot = upload_artifacts_to_storage(
                    folder_name=folder,
                    files_to_upload=[copie],
                    config=config,
                    import_id=import_id,
                    source_root=source,
                    logical_paths={str(copie): chemin},
                )
                verifier_racine(racine, racine_fd)
                # Compatibilité des doubles de tests ; l'implémentation de
                # stockage rend déjà le chemin logique, jamais la copie.
                invalide = False
                for artefact in lot.artifacts:
                    if artefact.path == str(copie):
                        artefact.path, artefact.name = str(chemin), chemin.name
                    if artefact.verified and (
                        artefact.path != str(chemin) or not _preuve_identifiable(artefact.to_dict())
                        or artefact.sha256 != metadata["sha256"]
                    ):
                        artefact.status = "failed"
                        artefact.verified = False
                        artefact.error = "Preuve absente ou objet différent de la copie de reprise"
                        invalide = True
                if invalide:
                    lot.status = "partial" if any(a.status == "uploaded" for a in lot.artifacts) else "failed"
                if stockage.is_configured() and (
                    len(lot.artifacts) != 1 or any(a.path != str(chemin) for a in lot.artifacts)
                ):
                    # La fonction appelée a pu écrire avant d'omettre son
                    # résultat : ne pas assimiler cette absence à un préflight.
                    resultat_incomplet = True
                artefacts_renvoyes.extend(lot.artifacts)
                statuts_lots.append(lot.status)
        for racine, fd in descripteurs.items():
            if fd is not None:
                verifier_racine(racine, fd)
    if not statuts_lots:
        statut_lot = "not_applicable"
    elif all(statut == "not_configured" for statut in statuts_lots):
        statut_lot = "not_configured"
    elif all(statut == "uploaded" for statut in statuts_lots):
        statut_lot = "uploaded"
    elif any(statut in ("uploaded", "partial") for statut in statuts_lots):
        statut_lot = "partial"
    else:
        statut_lot = "failed"
    upload_batch = UploadBatch(status=statut_lot, artifacts=artefacts_renvoyes)
    resultats = {a.path: a.to_dict() for a in upload_batch.artifacts}

    # Fusion : le manifeste reste COMPLET. Les entrées déjà vérifiées gardent
    # leurs preuves, les entrées renvoyées prennent le nouveau résultat, et les
    # fichiers localement absents portent un échec nommé.
    par_chemin = {entree["path"]: entree for entree in manifeste}
    for chemin, resultat in resultats.items():
        cible = par_chemin.get(chemin)
        if cible is None:
            par_chemin[chemin] = {**resultat, "required": True, "role": "hors_manifeste"}
            continue
        cible.update(
            {
                "key": resultat.get("key"),
                "bucket": resultat.get("bucket"),
                "status": resultat.get("status", cible["status"]),
                "verified": bool(resultat.get("verified")),
                "verification": resultat.get("verification", "non_verifie"),
                "verification_detail": resultat.get("verification_detail"),
                "sha256": resultat.get("sha256") if resultat.get("verified") else None,
                "error": resultat.get("error"),
            }
        )
    for chemin in absents:
        cible = par_chemin.get(chemin)
        if cible is not None:
            cible.update(
                {
                    "status": "failed",
                    "verified": False,
                    "error": "fichier source absent localement : copie durable non prouvée",
                }
            )
    for chemin in sources_modifiees + rapports_modifies:
        cible = par_chemin.get(chemin)
        if cible is not None:
            cible.update({
                "status": "failed", "verified": False, "sha256": None,
                "error": "artefact différent de la capture initiale : reprise suspendue",
            })

    manifeste = [par_chemin[chemin] for chemin in sorted(par_chemin)]
    destination_configuree = (
        S3StorageClient(config=config).is_configured()
        or upload_batch.status not in ("not_configured", "not_applicable")
        or any(e.get("key") for e in manifeste)
    )
    upload_status, all_verified = agreger_preservation(
        manifeste, destination_configuree=destination_configuree
    )
    transport_verifie = not resultat_incomplet and all_verified and all(
        _preuve_identifiable(e) and (
            e["path"] not in empreintes_capture or e.get("sha256") == empreintes_capture[e["path"]]
        ) for e in manifeste
    )
    identite_locale_observee = transport_verifie and _identite_locale_reprise(
        manifeste, source, report_dir, config.max_file_size_bytes
    )
    # Ni l'observation ponctuelle ni les preuves d'objets ne garantissent
    # un snapshot durable ou une liaison transactionnelle au catalogue.
    # Suspendre le succès global jusqu'à ce que ces garanties existent.
    all_verified = False
    if upload_status == "uploaded":
        upload_status = "partial"
    if not destination_configuree:
        # Aucune destination : les preuves antérieures, s'il y en a, font foi ;
        # sinon on l'annonce tel quel au lieu d'un « uploaded » vide de sens.
        upload_status = payload.get("upload_status") or "not_configured"

    payload["upload_status"] = upload_status
    payload["all_verified"] = all_verified
    payload["transport_verifie"] = transport_verifie
    payload["publication_pending"] = bool(resultat_incomplet or any(
        e.get("key") and not _preuve_identifiable(e) for e in manifeste
    ))
    if payload["publication_pending"] and payload.get("status") != "cancelled":
        payload["status"] = "needs_review"
    payload["artifacts"] = manifeste
    payload["inventaire"] = {
        "total": len(manifeste),
        "verifies": sum(1 for e in manifeste if _preuve_artefact_valide(e)),
        "manquants_localement": absents,
        "sources_modifiees": sources_modifiees,
        "rapports_modifies": rapports_modifies,
        "renvoyes": len(upload_batch.artifacts),
        "conserves": len(manifeste) - len(resultats) - len(absents) - len(sources_modifiees) - len(rapports_modifies),
    }
    payload["preservation"] = {
        "upload_status": upload_status,
        "integrite_prouvee": all_verified,
        "artefacts_total": len(manifeste),
        "artefacts_verifies": payload["inventaire"]["verifies"],
        "artefacts_non_verifies": [
            e["path"] for e in manifeste if not _preuve_artefact_valide(e)
        ],
        "destination_configuree": destination_configuree,
        "identite_locale_observee": identite_locale_observee,
        "transport_verifie": transport_verifie,
        "raison_integrite": "succès global suspendu : snapshot et liaison catalogue non prouvés",
    }
    # La preuve historique ne couvre pas les mutations locales ultérieures.
    payload["cleanup"] = {
        "purge": False,
        "raison": "purge automatique suspendue : identité actuelle du dossier non prouvée",
        "artefacts_non_verifies": payload["preservation"]["artefacts_non_verifies"],
    }

    # Statuts par fichier : mis à jour depuis le MANIFESTE (source unique), donc
    # jamais en contradiction avec lui.
    for entree_fichier in payload.get("files") or []:
        cible = par_chemin.get(str(entree_fichier.get("path") or ""))
        if cible is None:
            continue
        entree_fichier["upload_status"] = cible["status"]
        preuve_valide = _preuve_artefact_valide(cible)
        entree_fichier["object_key"] = cible.get("key") if preuve_valide else None
        entree_fichier["object_bucket"] = cible.get("bucket") if preuve_valide else None
        entree_fichier["verified"] = preuve_valide
        entree_fichier["uploaded_at"] = (entree_fichier.get("uploaded_at") or time.time()) if preuve_valide else None
    payload["files_uploaded"] = sum(
        1 for e in manifeste if _preuve_artefact_valide(e)
    )

    # Ne lier que la preuve identifiable des octets initiaux, et uniquement à
    # la ligne qui porte encore cette identité. Le chemin seul, une taille ou
    # content_hash (ambigu entre texte et octets) ne sont pas des preuves.
    correspondances: list[tuple[str, str, str, str, float]] = []
    fichiers_par_cle: dict[str, dict[str, Any]] = {}
    preuves_sans_identite = 0
    for fichier in payload.get("files") or []:
        if not isinstance(fichier, dict):
            continue
        fichier["catalogue_lie"] = False
        chemin = str(fichier.get("path") or "")
        preuve = par_chemin.get(chemin)
        if not preuve or not _preuve_identifiable(preuve):
            continue
        sha = empreintes_capture.get(chemin)
        if not sha or preuve.get("sha256") != sha:
            preuves_sans_identite += 1
            continue
        cle_chemin = os.path.normcase(os.path.abspath(chemin))
        if cle_chemin in fichiers_par_cle:
            raise ValueError("Fichier dupliqué dans le manifeste de reprise")
        fichiers_par_cle[cle_chemin] = fichier
        correspondances.append((cle_chemin, sha, str(preuve["key"]), str(preuve["bucket"]), time.time()))

    contexte = (publication_context() if publication_context is not None
                else index.scan_transaction() if index is not None else nullcontext())
    with contexte:
        # Un ancien manifeste sans identité n'a aucune liaison à effectuer.
        # Ne pas appeler le catalogue lorsqu'il n'y a aucune correspondance.
        liees = index.lier_objets_capture(correspondances) if correspondances else set()
        for cle_chemin in liees:
            fichiers_par_cle[cle_chemin]["catalogue_lie"] = True
        conflits = preuves_sans_identite + len(correspondances) - len(liees)
        payload["liaison_catalogue"] = {
            "preuves_identifiables": len(correspondances), "reliees": len(liees), "conflits": conflits,
        }
        if conflits and payload.get("status") != "cancelled":
            payload["status"] = "needs_review"
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
    config: Any | None = None,
) -> dict[str, Any]:
    """Porte A : dépose un dossier complet (fiche PDF + pièces) → fiche en
    ``a_valider``. Délègue à :func:`seamtech_search.fiches.depot.deposer_dossier`
    (idempotence normcase + SHA-256, écriture entière ou pas du tout)."""
    from .fiches.depot import deposer_dossier

    return deposer_dossier(index, Path(dossier), gabarits=gabarits, config=config)


def importer_lot_dossiers(
    index: SearchIndex,
    racine: Path,
    *,
    notes: str | None = None,
    interrompre_apres: int | None = None,
    recursif: bool = True,
    config: Any | None = None,
) -> dict[str, Any]:
    """Porte A bis : crée et exécute un lot de dossiers (archive historique).
    Délègue à :func:`seamtech_search.fiches.depot.creer_lot` +
    :func:`seamtech_search.fiches.depot.executer_lot` (reprise = rappeler)."""
    from .fiches.depot import creer_lot, executer_lot

    id_lot = creer_lot(index, Path(racine), notes=notes, recursif=recursif)
    return {
        "id_lot": id_lot,
        "etat": executer_lot(index, id_lot, interrompre_apres=interrompre_apres, config=config),
    }
