"""Reference-folder import workflow.

Two-phase design:

1. **Scan** (:func:`scan_folder`) — walk a folder, classify every PDF with the
   shared fixed-layout anchors and return the technical-PDF *candidates*
   without writing anything to the index.
2. **Confirm** (:func:`import_folder` with ``selected_pdf``) — extract the
   chosen PDF, generate PDF + Word reports, index the folder and upload the
   three files (source PDF, PDF report, Word report) to OneDrive.

:func:`import_folder` without ``selected_pdf`` keeps the legacy one-shot
behaviour (first deterministic candidate wins) so existing callers and the
``POST /imports`` endpoint keep working; the candidates are still included
in the result so the UI can offer a choice retroactively.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .anchors import TECHNICAL_ANCHORS, classify_pdf_text, matched_anchors
from .config import AppConfig
from .extractors import extract_file
from .indexer import SearchIndex
from .models import Document

__all__ = [
    "TECHNICAL_ANCHORS",
    "classify_pdf_text",
    "classify_path",
    "extract_structured_pdf",
    "generate_report",
    "generate_docx_report",
    "import_folder",
    "scan_folder",
    "get_import",
    "update_import",
    "correct_import",
    "retry_upload",
    "upload_to_onedrive",
    "staging_root",
    "normalize_unit_to_mm",
    "Dimensions",
    "ExtractedData",
    "ImportCandidate",
    "ImportFile",
    "ImportResult",
    "FIELD_PATTERNS",
    "DIMENSION_PATTERN",
    "UNIT_TO_MM",
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
    # Raw values exactly as printed on the sheet (backward compatible).
    length: float | None = None
    width: float | None = None
    height: float | None = None
    unit: str | None = None
    # Normalized values, always in millimetres when the unit is known.
    length_mm: float | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    unit_normalized: str | None = None


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
    upload_status: str = "not_configured"


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


# ---------------------------------------------------------------------------
# Fixed-layout rules
# ---------------------------------------------------------------------------

FIELD_PATTERNS: dict[str, tuple[str, ...]] = {
    "reference": (r"reference\s*[:\-]?\s*([^\n]+)", r"référence\s*[:\-]?\s*([^\n]+)", r"(?:fichier|commande)\s+([A-Z0-9][A-Z0-9_-]+)"),
    "material": (r"material\s*[:\-]?\s*([^\n]+)", r"mati(?:è|e)re\s*[:\-]?\s*([^\n]+)", r"matériau(?:x)?\s*[:\-]?\s*([^\n]+)", r"tissu\(s\)\s*:\s*([^\n]+)"),
    "quantity": (r"quantity\s*[:\-]?\s*(\d+)", r"quantit(?:y|é)\s*[:\-]?\s*(\d+)"),
    # ``\s`` includes newlines. Restrict the separator after a label to
    # horizontal whitespace so a bare header cannot capture the next field.
    "description": (r"description[^\S\r\n]*[:\-]?[^\S\r\n]*([^\n]+)", r"fiche de fabrication[^\S\r\n]*[\"']?([^\n\"']+)"),
}
DIMENSION_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)(?:\s*[x×]\s*(\d+(?:[.,]\d+)?))?\s*(mm|cm|m)?", re.I)
# Labeled fallback for sheets that print dimensions as discrete fields
# ("Longueur: 1200 mm / Largeur: 800 mm") instead of "1200 x 800 mm".
LABELED_DIMENSION_PATTERNS: dict[str, tuple[str, ...]] = {
    "length": (r"longueur\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?", r"length\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?"),
    "width": (r"largeur\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?", r"width\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?"),
    "height": (r"hauteur\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?", r"height\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)?"),
}


def classify_path(path: Path, text: str = "") -> str:
    if path.is_dir():
        return "folder"
    if path.suffix.lower() != ".pdf":
        return "storage_direct"
    return classify_pdf_text(text) if text else "pdf_candidate"


def _extract_dimensions(text: str) -> dict[str, Any] | None:
    """Extract raw dimensions dict, or None when nothing matches."""
    dimension = DIMENSION_PATTERN.search(text)
    if dimension:
        unit = (dimension.group(4) or "mm").lower()
        return {
            "length": float(dimension.group(1).replace(",", ".")),
            "width": float(dimension.group(2).replace(",", ".")),
            "height": float(dimension.group(3).replace(",", ".")) if dimension.group(3) else None,
            "unit": unit,
        }
    drawing = re.search(r"mesures dessin\s+((?:\d+(?:[.,]\d+)?\s*m\s*){2,})", text, re.I)
    if drawing:
        measurements = re.findall(r"\d+(?:[.,]\d+)?", drawing.group(1))
        if len(measurements) >= 2:
            return {"length": float(measurements[0].replace(",", ".")), "width": float(measurements[1].replace(",", ".")), "unit": "m"}
    labeled: dict[str, Any] = {}
    for axis, patterns in LABELED_DIMENSION_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                labeled[axis] = float(match.group(1).replace(",", "."))
                labeled.setdefault("unit", (match.group(2) or "mm").lower())
                break
    if "length" in labeled or "width" in labeled:
        labeled.setdefault("unit", "mm")
        return labeled
    return None


def _with_normalized_units(raw: dict[str, Any]) -> dict[str, Any]:
    """Add *_mm normalized values to a raw dimensions dict."""
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
        return ExtractedData(raw_text=result.text, extraction_status="failed", warnings=[result.detail or result.status])

    text = "\n".join(line.strip() for line in result.text.splitlines() if line.strip())
    values: dict[str, Any] = {"raw_text": text, "extraction_status": "partial", "confidence": 0.0}
    matched = 0
    for field, patterns in FIELD_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                values[field] = match.group(1).strip()
                matched += 1
                break
    raw_dimensions = _extract_dimensions(text)
    if raw_dimensions:
        values["dimensions"] = _with_normalized_units(raw_dimensions)
        matched += 1
    if "quantity" in values:
        values["quantity"] = int(values["quantity"])
    values["confidence"] = matched / (len(FIELD_PATTERNS) + 1)
    values["extraction_status"] = "success" if matched == len(FIELD_PATTERNS) + 1 else "partial"
    if not text:
        values["extraction_status"] = "failed"
        values["warnings"] = ["No extractable text found; OCR may be required."]
    elif values["extraction_status"] == "partial":
        values["warnings"] = ["One or more configured fields were not found."]
    try:
        return ExtractedData.model_validate(values)
    except ValidationError as exc:
        return ExtractedData(raw_text=text, extraction_status="failed", warnings=[str(exc)])


# ---------------------------------------------------------------------------
# Reports (PDF + Word)
# ---------------------------------------------------------------------------


def generate_report(data: ExtractedData, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:
        output_path.with_suffix(".json").write_text(data.model_dump_json(indent=2), encoding="utf-8")
        return output_path.with_suffix(".json")
    pdf = canvas.Canvas(str(output_path), pagesize=A4)
    _, height = A4
    y = height - 60
    pdf.setTitle("SEAMTECH Technical Report")
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(50, y, "SEAMTECH Technical Report")
    y -= 35
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
    for label, value in rows:
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(50, y, label)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(170, y, str(value)[:100])
        y -= 22
    if data.warnings:
        y -= 10
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(50, y, "Warnings")
        y -= 18
        pdf.setFont("Helvetica", 9)
        for warning in data.warnings:
            pdf.drawString(60, y, warning[:110])
            y -= 16
    pdf.save()
    return output_path


def generate_docx_report(data: ExtractedData, output_path: Path) -> Path:
    """Generate the Word twin of the PDF technical report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
    """Directory holding browser-uploaded folders staged for import."""
    return config.database_path.parent / "uploads"


def _validated_source(source: Path, config: AppConfig) -> Path:
    """Resolve *source* and ensure it is an allowed import directory.

    Allowed locations are the configured ``root_paths`` (Windows shares) and
    the server-side upload staging directory (browser DnD flow). Source files
    are only ever read — never moved or modified.
    """
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
    """Scan a folder and return technical-PDF candidates.

    Pure read-only step: no indexing, no reports, no uploads. The caller
    (UI) lets the user pick a candidate, then calls :func:`import_folder`
    with ``selected_pdf``.
    """
    resolved = _validated_source(source, config)
    candidates: list[dict[str, Any]] = []
    files_detected = 0
    for path in _walk_files(resolved):
        files_detected += 1
        if path.suffix.lower() != ".pdf":
            continue
        extracted = extract_structured_pdf(path, config)
        anchors = matched_anchors(extracted.raw_text) if extracted.raw_text else []
        if classify_pdf_text(extracted.raw_text) == "technical_pdf":
            candidates.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "size": path.stat().st_size,
                    "anchors_matched": anchors,
                    "anchor_count": len(anchors),
                }
            )
    warnings: list[str] = []
    if not candidates:
        warnings.append("No technical PDF found")
    elif len(candidates) > 1:
        warnings.append(f"{len(candidates)} technical PDFs detected; select the one to import")
    return {
        "source_path": str(resolved),
        "files_detected": files_detected,
        "candidates": candidates,
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
) -> ImportResult:
    resolved = _validated_source(source, config)

    import_id = uuid.uuid4().hex
    report_dir = config.database_path.parent / "reports" / import_id
    files: list[ImportFile] = []
    pdfs: list[Path] = []
    candidates: list[dict[str, Any]] = []
    for path in _walk_files(resolved):
        category = classify_path(path)
        status = "pending" if category == "pdf_candidate" else "not_applicable"
        if category == "pdf_candidate":
            extracted = extract_structured_pdf(path, config)
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

    data = extract_structured_pdf(technical_pdf, config) if technical_pdf else None
    report_path = generate_report(data, report_dir / "technical-report.pdf") if data else None
    report_docx_path = generate_docx_report(data, report_dir / "technical-report.docx") if data else None
    upload_status = (
        upload_to_onedrive(technical_pdf, report_path, resolved.name, config, report_docx=report_docx_path)
        if technical_pdf
        else "not_applicable"
    )
    indexed_documents = []
    for item in files:
        item_path = Path(item.path)
        indexed_documents.append(
            Document(
                path=item_path,
                name=item.name,
                parent_path=item_path.parent,
                extension=item.extension,
                size=item.size,
                modified_at=item_path.stat().st_mtime,
                is_dir=False,
                text=data.raw_text if technical_pdf and item.path == str(technical_pdf) and data else "",
                category=item.category,
            )
        )
    index.upsert_documents(indexed_documents)
    final_files = [
        ImportFile(
            f.path,
            f.name,
            f.category,
            f.size,
            f.extension,
            data.extraction_status if f.path == str(technical_pdf) and data else f.extraction_status,
            str(report_path) if f.path == str(technical_pdf) else None,
            upload_status if f.path == str(technical_pdf) else "not_applicable",
        )
        for f in files
    ]
    status = "completed" if data and data.extraction_status == "success" else "needs_review" if data else "failed"
    result = ImportResult(
        import_id,
        str(resolved),
        status,
        len(files),
        len(pdfs),
        str(technical_pdf) if technical_pdf else None,
        data.model_dump() if data else None,
        str(report_path) if report_path else None,
        upload_status,
        warnings + (data.warnings if data else []),
        final_files,
        candidates,
        str(report_docx_path) if report_docx_path else None,
    )
    _save_import(index, result)
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
                # Sent as an untyped literal so it works whether the column
                # is already JSONB (migrated) or legacy TEXT.
                cursor.execute(
                    "INSERT INTO imports (id, source_path, status, payload, created_at) VALUES (%s, %s, %s, %s, now())",
                    (result.import_id, result.source_path, result.status, payload),
                )
        else:
            connection.execute(
                "INSERT INTO imports (id, source_path, status, payload, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
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
            cursor = connection.execute("UPDATE imports SET status = ?, payload = ? WHERE id = ?", (status, text, import_id))
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
                # psycopg2 returns JSONB columns as dict, legacy TEXT as str.
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


def correct_import(index: SearchIndex, config: AppConfig, import_id: str, corrections: dict[str, Any]) -> dict[str, Any]:
    """Apply a manual correction, regenerate both reports and re-upload.

    Raises KeyError when the import does not exist and ValueError when the
    corrected data fails validation.
    """
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
    report_pdf = generate_report(data, report_dir / "technical-report.pdf")
    report_docx = generate_docx_report(data, report_dir / "technical-report.docx")

    technical_pdf = Path(payload["technical_pdf"]) if payload.get("technical_pdf") else None
    folder = Path(payload.get("source_path", "")).name or "import"
    upload_status = (
        upload_to_onedrive(technical_pdf, report_pdf, folder, config, report_docx=report_docx) if technical_pdf else "not_applicable"
    )

    payload["data"] = data.model_dump()
    payload["report_path"] = str(report_pdf)
    payload["report_docx_path"] = str(report_docx)
    payload["upload_status"] = upload_status
    payload["status"] = "completed" if status == "success" else "needs_review"
    payload["warnings"] = [w for w in payload.get("warnings", []) if "first PDF was selected" in w or "technical PDFs detected" in w] + warnings
    for entry in payload.get("files", []):
        if entry.get("path") == payload.get("technical_pdf"):
            entry["extraction_status"] = status
            entry["report_path"] = str(report_pdf)
            entry["upload_status"] = upload_status
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
    """Re-attempt the OneDrive upload for an existing import."""
    payload = get_import(index, import_id)
    if payload is None:
        raise KeyError(f"Import not found: {import_id}")
    technical_pdf = Path(payload["technical_pdf"]) if payload.get("technical_pdf") else None
    report_pdf = Path(payload["report_path"]) if payload.get("report_path") else None
    report_docx = Path(payload["report_docx_path"]) if payload.get("report_docx_path") else None
    folder = Path(payload.get("source_path", "")).name or "import"
    upload_status = (
        upload_to_onedrive(technical_pdf, report_pdf, folder, config, report_docx=report_docx) if technical_pdf else "not_applicable"
    )
    payload["upload_status"] = upload_status
    for entry in payload.get("files", []):
        if entry.get("path") == payload.get("technical_pdf"):
            entry["upload_status"] = upload_status
    update_import(index, import_id, payload.get("status", "needs_review"), payload)
    return payload


# ---------------------------------------------------------------------------
# OneDrive upload with retry + exponential backoff
# ---------------------------------------------------------------------------


def _upload_max_retries(config: AppConfig | None = None) -> int:
    if os.getenv("SEAMTECH_UPLOAD_MAX_RETRIES"):
        try:
            return max(0, int(os.environ["SEAMTECH_UPLOAD_MAX_RETRIES"]))
        except ValueError:
            pass
    if config is not None:
        return max(0, int(getattr(config, "onedrive_max_retries", 3)))
    return 3


def upload_to_onedrive(
    pdf: Path | None,
    report: Path | None,
    folder: str,
    config: AppConfig,
    report_docx: Path | None = None,
    max_retries: int | None = None,
) -> str:
    """Upload source PDF + PDF report + Word report to OneDrive.

    Returns ``uploaded``, ``pending_not_configured`` or ``pending_retry``.
    Transient network/Graph failures are retried with exponential backoff
    (1s, 2s, 4s, ...). Never raises for upload problems — the import itself
    must not fail because OneDrive is down.
    """
    token = os.getenv("SEAMTECH_GRAPH_ACCESS_TOKEN")
    drive_id = os.getenv("SEAMTECH_ONEDRIVE_DRIVE_ID")
    targets = [path for path in (pdf, report, report_docx) if path is not None]
    if not token or not drive_id or not pdf or not report:
        return "pending_not_configured"
    attempts = 1 + (max_retries if max_retries is not None else _upload_max_retries(config))
    last_error: str | None = None
    for attempt in range(attempts):
        try:
            for path in targets:
                target = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{folder}/{path.name}:/content"
                request = urllib.request.Request(
                    target,
                    data=path.read_bytes(),
                    method="PUT",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"},
                )
                with urllib.request.urlopen(request, timeout=30):
                    pass
            return "uploaded"
        except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts - 1:
                time.sleep(2**attempt)
    _record_upload_failure(folder, targets, last_error)
    return "pending_retry"


def _record_upload_failure(folder: str, targets: list[Path], error: str | None) -> None:
    import logging

    logging.getLogger("seamtech_search").warning(
        "OneDrive upload failed for folder %s (%s): %s", folder, ", ".join(p.name for p in targets), error or "unknown error"
    )
