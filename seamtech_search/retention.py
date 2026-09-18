"""Retention and disk space guard utilities.

Provides:
- Disk space check guard (throws InsufficientStorageError -> HTTP 507)
- Pruning of old reports (default 90 days)
- Pruning of staged uploads (default 7 days)
- Pruning of audit log entries (default 365 days)
- Orchestration via run_retention_cleanup
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AppConfig
    from .indexer import SearchIndex

logger = logging.getLogger("seamtech_search.retention")


class InsufficientStorageError(Exception):
    """Raised when available disk space falls below the configured min_free_bytes threshold."""


def ensure_free_space(path: str | Path, min_free_bytes: int) -> None:
    """Check that the target filesystem has at least `min_free_bytes` available.

    If min_free_bytes <= 0, the check is disabled (useful for tests).
    Raises InsufficientStorageError if free space is insufficient.
    """
    if min_free_bytes <= 0:
        return
    target_path = Path(path).resolve()
    # Check parent if target does not yet exist
    check_path = target_path
    while not check_path.exists() and check_path.parent != check_path:
        check_path = check_path.parent
    try:
        usage = shutil.disk_usage(check_path)
        if usage.free < min_free_bytes:
            raise InsufficientStorageError(
                f"Insufficient disk space on {check_path}: {usage.free} bytes free, "
                f"required minimum is {min_free_bytes} bytes."
            )
    except OSError as exc:
        logger.warning("Could not determine disk usage for %s: %s", check_path, exc)


def prune_reports(base_dir: str | Path, max_age_days: int) -> int:
    """Prune generated PDF and Word report files older than max_age_days."""
    reports_dir = Path(base_dir) / "reports"
    if not reports_dir.exists() or not reports_dir.is_dir():
        return 0

    cutoff_seconds = time.time() - (max_age_days * 86400)
    pruned_count = 0

    for root, dirs, files in os.walk(reports_dir, topdown=False):
        for file_name in files:
            file_path = Path(root) / file_name
            try:
                if file_path.stat().st_mtime < cutoff_seconds:
                    file_path.unlink()
                    pruned_count += 1
            except OSError as exc:
                logger.warning("Failed to delete old report file %s: %s", file_path, exc)
        # Remove directory if empty
        if root != str(reports_dir):
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except OSError as exc:
                # Best-effort cleanup of an empty directory; harmless if it
                # stays, but not silently.
                logger.debug("Could not remove empty report directory %s: %s", root, exc)

    return pruned_count


def prune_staged_uploads(staging_dir: str | Path, max_age_days: int) -> int:
    """Prune temporary staged upload directories older than max_age_days."""
    staging_path = Path(staging_dir)
    if not staging_path.exists() or not staging_path.is_dir():
        return 0

    cutoff_seconds = time.time() - (max_age_days * 86400)
    pruned_count = 0

    for child in staging_path.iterdir():
        # Never delete quarantine even if it lives inside staging (defensive)
        if child.name == "quarantine":
            continue
        try:
            if child.stat().st_mtime < cutoff_seconds:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink()
                pruned_count += 1
        except OSError as exc:
            logger.warning("Failed to remove old staged upload %s: %s", child, exc)

    return pruned_count


def prune_audit_logs(index: SearchIndex, max_age_days: int) -> int:
    """Prune audit log entries older than max_age_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    deleted_rows = 0
    with index.connect() as conn:
        if index.is_postgres:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM audit_log WHERE timestamp < %s", (cutoff,))
                deleted_rows = cursor.rowcount
        else:
            cutoff_iso = cutoff.isoformat()
            cursor = conn.execute("DELETE FROM audit_log WHERE timestamp < ?", (cutoff_iso,))
            deleted_rows = cursor.rowcount
    return max(0, deleted_rows)


def run_retention_cleanup(config: AppConfig, index: SearchIndex) -> dict[str, int]:
    """Execute all retention cleanup policies and return a summary of deleted artifacts."""
    base_dir = config.database_path.parent
    # Fixed: use actual staging_root (data/uploads) not data/staging_uploads
    from .import_pipeline import quarantine_root, staging_root

    staging_dir = staging_root(config)
    quarantine_dir = quarantine_root(config)

    pruned_rep = prune_reports(base_dir, config.reports_retention_days)
    pruned_stg = prune_staged_uploads(staging_dir, config.staged_retention_days)
    # Never prune quarantine — failed uploads must be preserved for manual retry
    # Ensure quarantine dir exists but is excluded from staged pruning
    pruned_aud = prune_audit_logs(index, config.audit_retention_days)

    logger.info(
        "Retention cleanup completed: %d reports, %d staged uploads, %d audit log rows removed (quarantine preserved at %s).",
        pruned_rep,
        pruned_stg,
        pruned_aud,
        quarantine_dir,
    )

    return {
        "pruned_reports": pruned_rep,
        "pruned_staged_uploads": pruned_stg,
        "pruned_audit_logs": pruned_aud,
    }
