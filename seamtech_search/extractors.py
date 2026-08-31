from __future__ import annotations

import re
from pathlib import Path
from zipfile import BadZipFile, ZipFile

# Bump this whenever extraction logic changes for any format. The indexer
# uses it (alongside size/modified_at) to decide whether a previously
# indexed file needs to be re-extracted even though it hasn't changed on
# disk, so old content isn't silently kept forever after a parser fix.
CURRENT_EXTRACTOR_VERSION = 1

TEXT_EXTENSIONS = {
    ".txt", ".csv", ".tsv", ".md", ".log", ".json", ".xml", ".html", ".htm",
    ".yaml", ".yml", ".ini", ".cfg", ".conf", ".sql", ".py", ".js", ".ts",
    ".tsx", ".jsx", ".css", ".scss", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".php", ".sh", ".ps1", ".bat", ".properties",
}
OFFICE_XML_EXTENSIONS = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}
ARCHIVE_EXTENSIONS = {".zip"}


def extract_text(path: Path, max_chars: int, max_file_size_bytes: int = 512 * 1024 * 1024) -> str:
    """Extract bounded searchable text; return an explicit status for unsupported/unsafe work."""
    try:
        size = path.stat().st_size
        if size > max_file_size_bytes:
            return f"[extraction skipped: file exceeds {max_file_size_bytes} bytes]"
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return _extract_pdf(path, max_chars)
        if suffix == ".docx":
            return _extract_docx(path, max_chars)
        if suffix in OFFICE_XML_EXTENSIONS:
            return _extract_zip_xml(path, max_chars)
        if suffix in ARCHIVE_EXTENSIONS:
            return _extract_archive_manifest(path, max_chars)
        if suffix in TEXT_EXTENSIONS:
            return _extract_plain_text(path, max_chars)
    except (OSError, BadZipFile, ValueError) as exc:
        return f"[extraction error: {type(exc).__name__}]"
    except Exception as exc:
        return f"[extraction error: {type(exc).__name__}]"
    return "[extraction unavailable: unsupported file type]"


def _extract_pdf(path: Path, max_chars: int) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path), strict=False)
    chunks: list[str] = []
    total = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            chunks.append(text)
            total += len(text)
        if total >= max_chars:
            break
    return "\n".join(chunks)[:max_chars] or "[extraction unavailable: PDF contains no embedded text]"


def _extract_docx(path: Path, max_chars: int) -> str:
    from docx import Document as DocxDocument

    doc = DocxDocument(str(path))
    chunks = [paragraph.text for paragraph in doc.paragraphs]
    for table in doc.tables:
        chunks.extend(cell.text for row in table.rows for cell in row.cells)
    text = "\n".join(chunks).strip()
    return text[:max_chars] or "[extraction unavailable: document contains no text]"


def _extract_zip_xml(path: Path, max_chars: int) -> str:
    chunks: list[str] = []
    with ZipFile(path) as archive:
        members = [info for info in archive.infolist() if not info.is_dir() and info.file_size <= 10 * 1024 * 1024]
        for info in members[:500]:
            if not info.filename.lower().endswith((".xml", ".rels")):
                continue
            raw = archive.read(info)
            text = re.sub(r"<[^>]+>", " ", raw.decode("utf-8", errors="ignore"))
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                chunks.append(text)
            if sum(map(len, chunks)) >= max_chars:
                break
    return "\n".join(chunks)[:max_chars] or "[extraction unavailable: archive contains no readable text]"


def _extract_archive_manifest(path: Path, max_chars: int) -> str:
    with ZipFile(path) as archive:
        names = [info.filename for info in archive.infolist()[:10_000]]
    return "[archive members]\n" + "\n".join(names)[:max_chars]


def _extract_plain_text(path: Path, max_chars: int) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
