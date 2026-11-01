from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

from .anchors import TECHNICAL_ANCHORS, classify_pdf_text
from .config import AppConfig
from .extractors import CURRENT_EXTRACTOR_VERSION, ExtractionResult, extract_text
from .models import Document

LOGGER = logging.getLogger("seamtech_search")
# Backwards-compatible alias; the canonical constant lives in anchors.py.
TECHNICAL_PDF_ANCHORS = TECHNICAL_ANCHORS

# path_key -> (size, modified_at, extractor_version) for everything already
# in the index, so unchanged files can skip extraction entirely.
ExistingMetadata = dict[str, tuple[int, float, int]]


def _extract_with_timeout(path: Path, config: AppConfig) -> ExtractionResult:
    if extract_text.__module__ == "seamtech_search.extractors":
        return _extract_in_process(path, config)

    # Keep the direct callable seam for unit tests that monkeypatch extract_text.
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
        LOGGER.warning("Extraction timed out after %ss", config.extraction_timeout_seconds)
        # wait=False: don't block the scan on a thread that may never return.
        executor.shutdown(wait=False)
        return ExtractionResult("", "timeout")


def _extract_in_process(path: Path, config: AppConfig) -> ExtractionResult:
    options = {
        "enable_legacy_office": config.enable_legacy_office,
        "libreoffice_command": config.libreoffice_command,
        "enable_ocr": config.enable_ocr,
        "tesseract_command": config.tesseract_command,
        "ocrmypdf_command": config.ocrmypdf_command,
        "external_extraction_timeout_seconds": config.external_extraction_timeout_seconds,
        "external_extractors": config.external_extractors,
    }
    command = [sys.executable, "-m", "seamtech_search.extraction_worker"]
    requete = json.dumps({"path": str(path), "max_chars": config.max_extract_chars,
                          "max_file_size_bytes": config.max_file_size_bytes, "options": options})
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=Path(__file__).resolve().parents[1], start_new_session=(os.name == "posix"),
        )
    except OSError:
        return ExtractionResult("", "error", "extraction worker failed")
    try:
        stdout, _ = process.communicate(input=requete, timeout=config.extraction_timeout_seconds)
    except BaseException as exc:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            # An independently detached descendant may still hold a pipe.
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
            process.wait()
        if isinstance(exc, subprocess.TimeoutExpired):
            LOGGER.warning("Extraction timed out after %ss", config.extraction_timeout_seconds)
            return ExtractionResult("", "timeout")
        raise
    if process.returncode != 0:
        return ExtractionResult("", "error", "extraction worker failed")
    try:
        payload = json.loads(stdout)
        if not isinstance(payload, dict) or set(payload) != {"text", "status", "detail"}:
            raise ValueError("invalid extraction response")
        if not all(isinstance(payload[key], str) for key in ("text", "status", "detail")):
            raise ValueError("invalid extraction response")
        return ExtractionResult(payload["text"], payload["status"], payload["detail"])
    except (ValueError, TypeError):
        return ExtractionResult("", "error", "invalid extraction worker response")


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


def zones_reservees(config: AppConfig) -> list[Path]:
    """Répertoires de TRAVAIL qu'un scan ne doit jamais indexer.

    Le staging (dépôts téléversés, copies de travail) et la quarantaine
    (imports incomplets) vivent sous le même répertoire de données que la base.
    Les indexer ferait apparaître des doublons dans la recherche — et ce sont
    des copies de travail, pas l'archive.

    Les deux formes (résolue et lexicale) sont retenues : un lien symbolique ne
    doit pas permettre d'atteindre une zone par un autre nom, et une résolution
    qui échoue ne doit pas valoir exclusion.
    """
    base = Path(config.database_path).parent
    zones: list[Path] = []
    for nom in ("uploads", "quarantine"):
        candidat = base / nom
        zones.append(Path(os.path.abspath(candidat)))
        try:
            zones.append(candidat.resolve(strict=False))
        except (OSError, RuntimeError):
            pass
    vues: set[str] = set()
    uniques: list[Path] = []
    for zone in zones:
        cle = os.path.normcase(str(zone))
        if cle not in vues:
            vues.add(cle)
            uniques.append(zone)
    return uniques


def _dans_zone(chemin: Path, zones: list[Path]) -> bool:
    """Ce chemin est-il une zone réservée, ou à l'intérieur de l'une d'elles ?"""
    try:
        lexical = Path(os.path.abspath(chemin))
        resolu = chemin.resolve(strict=False)
    except (OSError, RuntimeError):  # pragma: no cover - chemin exotique
        # Dans le doute, un chemin illisible n'est pas exclu : l'appelant
        # décidera (le scan est refusé si c'est une racine).
        return False
    for zone in zones:
        for vu in (lexical, resolu):
            if vu == zone or zone in vu.parents:
                return True
    return False


def _dans_zone_root(zone: Path, root: Path) -> bool:
    """Cette zone de travail est-elle située SOUS cette racine (ou égale) ?"""
    try:
        racines = (Path(os.path.abspath(root)), root.resolve(strict=False))
    except (OSError, RuntimeError):  # pragma: no cover - chemin exotique
        return False
    return any(zone == racine or racine in zone.parents for racine in racines)


def crawl(config: AppConfig, existing_metadata: ExistingMetadata | None = None) -> Iterator[Document]:
    existing_metadata = existing_metadata or {}
    zones = zones_reservees(config)
    try:
        for root in config.root_paths:
            racine = Path(root).resolve(strict=True)
            racine_lexicale = Path(os.path.abspath(root))
            # Une racine À L'INTÉRIEUR d'une zone de travail reste refusée :
            # cela reviendrait à indexer le staging ou la quarantaine.
            for vu in (racine, racine_lexicale):
                for zone in zones:
                    if vu == zone or zone in vu.parents:
                        raise ScanIncompleteError("Racine de scan en recouvrement avec le staging : scan refusé")
    except (OSError, RuntimeError) as erreur:
        if isinstance(erreur, ScanIncompleteError):
            raise
        raise ScanIncompleteError("Impossible de vérifier les racines du scan et du staging") from erreur
    for root in config.root_paths:
        root_path = Path(root)
        if not root_path.exists() or not root_path.is_dir():
            raise ScanIncompleteError(f"Configured root is unavailable or not a directory: {root_path}")

        def on_walk_error(error: OSError) -> None:
            raise ScanIncompleteError(f"Unable to traverse {error.filename or root_path}: {error}") from error

        # Zones de travail SITUÉES SOUS cette racine : elles sont élaguées, et
        # leurs lignes d'index sont protégées par `remove_missing` (l'élagage
        # ne doit jamais supprimer une ligne déjà indexée — voir cli.run_index).
        zones_racine = [zone for zone in zones if _dans_zone_root(zone, root_path)]
        noms_zones = {zone.name for zone in zones_racine}
        for current_root, dir_names, file_names in os.walk(root_path, onerror=on_walk_error, followlinks=False):
            current = Path(current_root)
            dir_names[:] = [name for name in dir_names if not _is_excluded(name, Path(name), config)]

            # Élaguer les zones de travail. Le filtre par nom évite de résoudre
            # chaque répertoire de l'archive ; un lien symbolique est toujours
            # résolu (il peut mener dans une zone sous un autre nom).
            retenus: list[str] = []
            for name in dir_names:
                candidat = current / name
                if name in noms_zones or os.path.islink(candidat):
                    if _dans_zone(candidat, zones_racine):
                        continue
                retenus.append(name)
            dir_names[:] = retenus

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


def _document_from_path(path: Path, config: AppConfig, is_dir: bool, existing_metadata: ExistingMetadata) -> Document:
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
    category = Document.classify("" if is_dir else path.suffix, is_dir)
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
            if path.suffix.lower() == ".pdf":
                category = classify_pdf_text(text)

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
        category=category,
    )
