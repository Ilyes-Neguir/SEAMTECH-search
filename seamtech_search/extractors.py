from __future__ import annotations

from pathlib import Path
from zipfile import BadZipFile


def extract_text(path: Path, max_chars: int) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            return _extract_pdf(path, max_chars)
        if suffix == ".docx":
            return _extract_docx(path, max_chars)
        if suffix in {".txt", ".csv", ".md", ".log"}:
            return _extract_plain_text(path, max_chars)
    except Exception as exc:
        return f"[extraction error: {type(exc).__name__}]"
    return ""


def _extract_pdf(path: Path, max_chars: int) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    chunks: list[str] = []
    total = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        if not text:
            continue
        chunks.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return "\n".join(chunks)[:max_chars]


def _extract_docx(path: Path, max_chars: int) -> str:
    from docx import Document as DocxDocument

    try:
        doc = DocxDocument(str(path))
    except BadZipFile:
        return "[extraction error: BadZipFile]"

    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    return text[:max_chars]


def _extract_plain_text(path: Path, max_chars: int) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]

