from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path

from .config import AppConfig
from .extractors import CURRENT_EXTRACTOR_VERSION, ExtractionResult, extract_text
from .models import Document

LOGGER = logging.getLogger("seamtech_search")

# path_key -> (size, modified_at, extractor_version) for everything already
# in the index, so unchanged files can skip extraction entirely.
ExistingMetadata = dict[str, tuple[int, float, int]]


def _extract_with_timeout(path: Path, config: AppConfig) -> ExtractionResult:
    # A fresh single-use executor per call: a genuinely hung parser leaves
    # its thread running in the background (Python can't kill a thread),
    # but a reused pool would let that one stuck file wedge every file
    # after it forever. A throwaway executor keeps a timeout isolated to
    # the file that caused it.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="seamtech-extract")
    future = executor.submit(
        extract_text,
        path,
        config.max_extract_chars,
        config.max_file_size_bytes,
        enable_legacy_office=config.enable_legacy_office,
        libreoffice_command=config.libreoffice_command,
        enable_ocr=config.enable_ocr,
        tesseract_command=config.tesseract_command,
        ocrmypdf_command=config.ocrmypdf_command,
        external_extraction_timeout_seconds=config.external_extraction_timeout_seconds,
        external_extractors=config.external_extractors,
    )
    try:
        result = future.result(timeout=config.extraction_timeout_seconds)
        executor.shutdown(wait=False)
        return _structured_from_legacy(result)
    except FutureTimeoutError:
        LOGGER.warning("Extraction timed out after %ss: %s", config.extraction_timeout_seconds, path)
        # wait=False: don't block the scan on a thread that may never return.
        executor.shutdown(wait=False)
        return ExtractionResult("", "timeout")


def _structured_from_legacy(text: str) -> ExtractionResult:
    if text.startswith("[extraction skipped:"):
        return ExtractionResult("", "skipped", text[len("[extraction skipped:") : -1].strip())
    if text.startswith("[extraction unavailable:"):
        return ExtractionResult("", "unavailable", text[len("[extraction unavailable:") : -1].strip())
    if text.startswith("[extraction error:"):
        return ExtractionResult("", "error", text[len("[extraction error:") : -1].strip())
    if text == "[extraction timed out]":
        return ExtractionResult("", "timeout")
    return ExtractionResult(text, "extracted")


class ScanIncompleteError(RuntimeError):
    """Raised when a scan cannot prove that all configured roots were traversed."""


def crawl(config: AppConfig, existing_metadata: ExistingMetadata | None = None) -> Iterator[Document]:
    existing_metadata = existing_metadata or {}
    for root in config.root_paths:
        root_path = Path(root)
        if not root_path.exists() or not root_path.is_dir():
            raise ScanIncompleteError(f"Configured root is unavailable or not a directory: {root_path}")

        def on_walk_error(error: OSError) -> None:
            raise ScanIncompleteError(f"Unable to traverse {error.filename or root_path}: {error}") from error

        for current_root, dir_names, file_names in os.walk(root_path, onerror=on_walk_error, followlinks=False):
            current = Path(current_root)
            dir_names[:] = [
                name for name in dir_names if not _is_excluded(name, Path(name), config)
            ]

            yield _document_from_path(current, config, is_dir=True, existing_metadata=existing_metadata)

            for file_name in file_names:
                file_path = current / file_name
                if _is_excluded(file_name, file_path, config):
                    continue
                yield _document_from_path(file_path, config, is_dir=False, existing_metadata=existing_metadata)


def _is_excluded(name: str, path: Path, config: AppConfig) -> bool:
    if name.startswith("~$"):
        return True
    if name in config.excluded_names:
        return True
    return path.suffix.lower() in config.excluded_extensions


def _document_from_path(
    path: Path, config: AppConfig, is_dir: bool, existing_metadata: ExistingMetadata
) -> Document:
    try:
        stat = path.stat()
        size = 0 if is_dir else stat.st_size
        modified_at = stat.st_mtime
    except OSError as exc:
        raise ScanIncompleteError(f"Unable to stat {path}: {exc}") from exc

    text = ""
    content_hash = ""
    extraction_status = "not_applicable" if is_dir else "extracted"
    extraction_detail = ""
    if not is_dir:
        path_key = os.path.normcase(str(path.resolve()))
        existing = existing_metadata.get(path_key)
        unchanged = (
            existing is not None
            and existing[0] == size
            and existing[1] == modified_at
            and existing[2] == CURRENT_EXTRACTOR_VERSION
        )
        if not unchanged:
            result = _extract_with_timeout(path, config)
            text = result.text
            extraction_status = result.status
            extraction_detail = result.detail
            content_hash = Document.hash_text(text)

    return Document(
        path=path,
        name=path.name,
        parent_path=path.parent,
        extension="" if is_dir else path.suffix.lower(),
        size=size,
        modified_at=modified_at,
        is_dir=is_dir,
        text=text,
        content_hash=content_hash,
        extraction_status=extraction_status,
        extraction_detail=extraction_detail,
    )
