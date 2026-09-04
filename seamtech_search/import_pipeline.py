from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .config import AppConfig
from .extractors import extract_file
from .indexer import SearchIndex
from .models import Document


class Dimensions(BaseModel):
    model_config = ConfigDict(extra="ignore")
    length: float | None = None
    width: float | None = None
    height: float | None = None
    unit: str | None = None


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


FIELD_PATTERNS: dict[str, tuple[str, ...]] = {
    "reference": (r"reference\s*[:\-]?\s*([^\n]+)", r"référence\s*[:\-]?\s*([^\n]+)", r"(?:fichier|commande)\s+([A-Z0-9][A-Z0-9_-]+)"),
    "material": (r"material\s*[:\-]?\s*([^\n]+)", r"mati(?:è|e)re\s*[:\-]?\s*([^\n]+)", r"tissu\(s\)\s*:\s*([^\n]+)"),
    "quantity": (r"quantity\s*[:\-]?\s*(\d+)", r"quantit(?:y|é)\s*[:\-]?\s*(\d+)"),
    "description": (r"description\s*[:\-]?\s*([^\n]+)", r"fiche de fabrication\s*[\"']?([^\n\"']+)"),
}
DIMENSION_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)(?:\s*[x×]\s*(\d+(?:[.,]\d+)?))?\s*(mm|cm|m)?", re.I)


TECHNICAL_ANCHORS = ("fiche de fabrication", "quantité", "quantity", "cotes", "mesures finies", "material", "matière")


def classify_pdf_text(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return "technical_pdf" if sum(anchor in normalized for anchor in TECHNICAL_ANCHORS) >= 2 else "plan_pdf"


def classify_path(path: Path, text: str = "") -> str:
    if path.is_dir():
        return "folder"
    if path.suffix.lower() != ".pdf":
        return "storage_direct"
    return classify_pdf_text(text) if text else "pdf_candidate"


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
    dimension = DIMENSION_PATTERN.search(text)
    if not dimension:
        drawing = re.search(r"mesures dessin\s+((?:\d+(?:[.,]\d+)?\s*m\s*){2,})", text, re.I)
        if drawing:
            measurements = re.findall(r"\d+(?:[.,]\d+)?", drawing.group(1))
            dimension = measurements
    if dimension:
        if isinstance(dimension, list):
            values["dimensions"] = {"length": float(dimension[0].replace(",", ".")), "width": float(dimension[1].replace(",", ".")), "unit": "m"}
        else:
            values["dimensions"] = {
                "length": float(dimension.group(1).replace(",", ".")),
                "width": float(dimension.group(2).replace(",", ".")),
                "height": float(dimension.group(3).replace(",", ".")) if dimension.group(3) else None,
                "unit": (dimension.group(4) or "mm").lower(),
            }
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


def _format_dimensions(data: ExtractedData) -> str:
    d = data.dimensions
    values = [d.length, d.width, d.height]
    present = [str(int(v)) if v is not None and float(v).is_integer() else str(v) for v in values if v is not None]
    return " × ".join(present) + (f" {d.unit}" if present and d.unit else "") or "À vérifier"


def import_folder(source: Path, config: AppConfig, index: SearchIndex) -> ImportResult:
    source = source.resolve()
    if not source.exists() or not source.is_dir():
        raise ValueError("Import path must be an existing directory")
    allowed = [root.resolve() for root in config.root_paths]
    if not any(source == root or root in source.parents for root in allowed):
        raise PermissionError("Import path is outside configured search roots")

    import_id = uuid.uuid4().hex
    report_dir = config.database_path.parent / "reports" / import_id
    files: list[ImportFile] = []
    pdfs: list[Path] = []
    for path in sorted((p for p in source.rglob("*") if p.is_file()), key=lambda p: str(p).lower()):
        category = classify_path(path)
        status = "pending" if category == "pdf_candidate" else "not_applicable"
        if category == "pdf_candidate":
            extracted = extract_structured_pdf(path, config)
            category = classify_pdf_text(extracted.raw_text) if extracted.raw_text else "plan_pdf"
            status = "pending" if category == "technical_pdf" else "not_applicable"
            if category == "technical_pdf":
                pdfs.append(path)
        files.append(ImportFile(str(path), path.name, category, path.stat().st_size, path.suffix.lower(), status))

    technical_pdf = pdfs[0] if pdfs else None
    warnings: list[str] = []
    if not pdfs:
        warnings.append("No technical PDF found")
    elif len(pdfs) > 1:
        warnings.append("Multiple PDF files detected; the first PDF was selected")
    data = extract_structured_pdf(technical_pdf, config) if technical_pdf else None
    report_path = generate_report(data, report_dir / "technical-report.pdf") if data else None
    upload_status = upload_to_onedrive(technical_pdf, report_path, source.name, config) if technical_pdf else "not_applicable"
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
    final_files = [ImportFile(f.path, f.name, f.category, f.size, f.extension, data.extraction_status if f.path == str(technical_pdf) and data else f.extraction_status, str(report_path) if f.path == str(technical_pdf) else None, upload_status if f.path == str(technical_pdf) else "not_applicable") for f in files]
    status = "completed" if data and data.extraction_status == "success" else "needs_review" if data else "failed"
    result = ImportResult(import_id, str(source), status, len(files), len(pdfs), str(technical_pdf) if technical_pdf else None, data.model_dump() if data else None, str(report_path) if report_path else None, upload_status, warnings + (data.warnings if data else []), final_files)
    _save_import(index, result)
    return result


def _save_import(index: SearchIndex, result: ImportResult) -> None:
    payload = json.dumps(asdict(result), ensure_ascii=False)
    with index.connect() as connection:
        if index.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO imports (id, source_path, status, payload, created_at) VALUES (%s, %s, %s, %s, now())", (result.import_id, result.source_path, result.status, payload))
        else:
            connection.execute("INSERT INTO imports (id, source_path, status, payload, created_at) VALUES (?, ?, ?, ?, datetime('now'))", (result.import_id, result.source_path, result.status, payload))


def get_import(index: SearchIndex, import_id: str) -> dict[str, Any] | None:
    with index.connect() as connection:
        if index.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute("SELECT payload FROM imports WHERE id = %s", (import_id,))
                row = cursor.fetchone()
                return json.loads(row[0]) if row else None
        row = connection.execute("SELECT payload FROM imports WHERE id = ?", (import_id,)).fetchone()
        return json.loads(row["payload"]) if row else None


def upload_to_onedrive(pdf: Path | None, report: Path | None, folder: str, config: AppConfig) -> str:
    token = os.getenv("SEAMTECH_GRAPH_ACCESS_TOKEN")
    drive_id = os.getenv("SEAMTECH_ONEDRIVE_DRIVE_ID")
    if not token or not drive_id or not pdf or not report:
        return "pending_not_configured"
    try:
        for path in (pdf, report):
            target = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{folder}/{path.name}:/content"
            request = urllib.request.Request(target, data=path.read_bytes(), method="PUT", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"})
            with urllib.request.urlopen(request, timeout=30):
                pass
        return "uploaded"
    except (OSError, urllib.error.HTTPError, urllib.error.URLError):
        return "pending_retry"
