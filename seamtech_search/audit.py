"""Audit logging module.

Records all mutating operations (and queries where configured).
Actors are always token fingerprints or 'local'/'anonymous' — raw secrets are never persisted.
Writes are best-effort so audit failures never block business operations.
Note: retention policy may prune old audit entries (see retention.py), so this is not strictly
append-only immutable — it is a regular table with configurable retention.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .indexer import SearchIndex

logger = logging.getLogger("seamtech_search.audit")


def actor_fingerprint(token: str | None, client_host: str | None = None) -> str:
    """Generate a safe, irreversible fingerprint for the actor.

    Never exposes raw tokens or secrets.
    """
    if token:
        digest = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()[:12]
        return f"token:{digest}"
    if client_host in ("127.0.0.1", "localhost", "::1", "testclient"):
        return "local"
    return client_host or "anonymous"


def record_audit_event(
    index: SearchIndex,
    action: str,
    actor: str,
    resource: str = "",
    status: str = "success",
    details: dict[str, Any] | None = None,
) -> None:
    """Record an audit event. Best-effort: catches all exceptions and logs a warning."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        details_json = json.dumps(details or {}, ensure_ascii=False)
        with index.connect() as conn:
            if index.is_postgres:
                import psycopg2.extras

                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO audit_log (timestamp, actor, action, resource, status, details)
                        VALUES (now(), %s, %s, %s, %s, %s)
                        """,
                        (actor, action, resource, status, psycopg2.extras.Json(details or {})),
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO audit_log (timestamp, actor, action, resource, status, details)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (now_iso, actor, action, resource, status, details_json),
                )
    except Exception as exc:
        logger.warning("Failed to record audit log event (action=%s, actor=%s): %s", action, actor, exc)


def get_audit_logs(
    index: SearchIndex,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Retrieve recent audit logs in descending chronological order."""
    with index.connect() as conn:
        if index.is_postgres:
            import psycopg2.extras

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, timestamp, actor, action, resource, status, details
                    FROM audit_log
                    ORDER BY timestamp DESC, id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                rows = cursor.fetchall()
                results: list[dict[str, Any]] = []
                for row in rows:
                    entry = dict(row)
                    if isinstance(entry.get("timestamp"), datetime):
                        entry["timestamp"] = entry["timestamp"].isoformat()
                    results.append(entry)
                return results

        cursor = conn.execute(
            """
            SELECT id, timestamp, actor, action, resource, status, details
            FROM audit_log
            ORDER BY timestamp DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = cursor.fetchall()
        results = []
        for row in rows:
            details_val = row["details"]
            try:
                details_parsed = json.loads(details_val) if isinstance(details_val, str) else details_val
            except Exception:
                details_parsed = {}
            results.append(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "actor": row["actor"],
                    "action": row["action"],
                    "resource": row["resource"],
                    "status": row["status"],
                    "details": details_parsed,
                }
            )
        return results
