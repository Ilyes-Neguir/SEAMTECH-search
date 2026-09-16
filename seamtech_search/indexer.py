from __future__ import annotations

import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .models import Document

logger = logging.getLogger("seamtech_search.indexer")


SQLITE_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    path_key TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_path TEXT NOT NULL,
    extension TEXT NOT NULL,
    size INTEGER NOT NULL,
    modified_at REAL NOT NULL,
    is_dir INTEGER NOT NULL,
    extractor_version INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    extraction_status TEXT NOT NULL DEFAULT 'extracted',
    extraction_detail TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'storage_direct',
    object_key TEXT,
    object_bucket TEXT,
    uploaded_at REAL,
    upload_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    name,
    path,
    extension,
    content
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    scanned INTEGER NOT NULL DEFAULT 0,
    changed INTEGER NOT NULL DEFAULT 0,
    removed INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    error TEXT,
    result TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'success',
    details TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    path_key TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_path TEXT NOT NULL,
    extension TEXT NOT NULL,
    size BIGINT NOT NULL,
    modified_at DOUBLE PRECISION NOT NULL,
    is_dir BOOLEAN NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    search_vector TSVECTOR NOT NULL DEFAULT ''::tsvector,
    extractor_version INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    extraction_status TEXT NOT NULL DEFAULT 'extracted',
    extraction_detail TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'storage_direct',
    object_key TEXT,
    object_bucket TEXT,
    uploaded_at TIMESTAMPTZ,
    upload_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_documents_search_vector ON documents USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS idx_documents_path_key ON documents(path_key);

CREATE TABLE IF NOT EXISTS scan_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    scanned BIGINT NOT NULL DEFAULT 0,
    changed BIGINT NOT NULL DEFAULT 0,
    removed BIGINT NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    error TEXT,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'success',
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
"""


@dataclass(frozen=True)
class IndexStats:
    total_documents: int
    files: int
    folders: int


class ScanAlreadyRunningError(RuntimeError):
    """Raised when another indexing process already owns the scan lock."""


class SearchIndex:
    def __init__(
        self,
        database_path: Path,
        database_url: str | None = None,
        pool_min: int = 1,
        pool_max: int = 10,
        pool_timeout: float = 30.0,
        statement_timeout_ms: int = 5000,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_url = database_url
        self.pool_min = pool_min
        self.pool_max = pool_max
        self.pool_timeout = pool_timeout
        self.statement_timeout_ms = statement_timeout_ms
        self._pool: Any = None
        if not self.is_postgres:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self._init_pool()

    @property
    def is_postgres(self) -> bool:
        return bool(self.database_url)

    def _init_pool(self) -> None:
        if self.is_postgres and self.database_url:
            try:
                import psycopg2.pool

                options = f"-c statement_timeout={self.statement_timeout_ms}"
                self._pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=self.pool_min,
                    maxconn=self.pool_max,
                    dsn=self.database_url,
                    options=options,
                )
            except Exception as exc:
                logger.warning("Could not initialize psycopg2 ThreadedConnectionPool: %s", exc)
                self._pool = None

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if self.is_postgres:
            if self._pool is None and self.database_url:
                self._init_pool()
            if self._pool is not None:
                conn = self._pool.getconn()
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    self._pool.putconn(conn)
                return
            else:
                import psycopg2

                conn = psycopg2.connect(
                    self.database_url,
                    connect_timeout=10,
                    options=f"-c statement_timeout={self.statement_timeout_ms}",
                )
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
                return

        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def close(self) -> None:
        if self._pool is not None:
            try:
                self._pool.closeall()
            except Exception:
                pass
            self._pool = None

    # ------------------------------------------------------------------
    # Versioned migrations — runs once at startup, never from request handlers
    # ------------------------------------------------------------------

    def _ensure_migrations_table(self, connection: Any) -> None:
        if self.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
        else:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )

    def _get_applied_migrations(self, connection: Any) -> set[str]:
        if self.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version FROM schema_migrations")
                return {row[0] for row in cursor.fetchall()}
        else:
            rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
            return {row["version"] for row in rows}

    def _record_migration(self, connection: Any, version: str) -> None:
        if self.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING",
                    (version,),
                )
        else:
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                (version,),
            )

    def _migration_001_initial(self, connection: Any) -> None:
        # Base tables already created by POSTGRES_SCHEMA / SQLITE_SCHEMA
        pass

    def _migration_002_object_storage_columns(self, connection: Any) -> None:
        if self.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS object_key TEXT")
                cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS object_bucket TEXT")
                cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS uploaded_at DOUBLE PRECISION")
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS upload_status TEXT NOT NULL DEFAULT 'pending'"
                )
                # For backward compat, keep not_configured default if already exists, but ensure column exists
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS content TEXT NOT NULL DEFAULT ''"
                )
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS search_vector TSVECTOR NOT NULL DEFAULT ''::tsvector"
                )
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extractor_version INTEGER NOT NULL DEFAULT 0"
                )
                cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT ''")
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extraction_status TEXT NOT NULL DEFAULT 'extracted'"
                )
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extraction_detail TEXT NOT NULL DEFAULT ''"
                )
                cursor.execute(
                    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'storage_direct'"
                )
        else:
            self._ensure_documents_columns(connection)

    def _migration_003_category_backfill_guard(self, connection: Any) -> None:
        # Fix for 1.4: never overwrite technical_pdf / plan_pdf etc.
        # Only backfill where category is null/empty/default.
        if self.is_postgres:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE documents SET category = CASE
                        WHEN is_dir = false THEN
                            CASE WHEN lower(extension) = '.pdf' THEN 'analyzed' ELSE 'storage_direct' END
                        ELSE 'folder'
                    END
                    WHERE category IS NULL OR category = '' OR category = 'storage_direct'
                      AND path_key NOT IN (SELECT path_key FROM documents WHERE category IN ('technical_pdf','plan_pdf','excel_sheet','storage_direct','analyzed','folder'))
                    """
                )
                # Safer: only where category is null/empty
                cursor.execute(
                    """
                    UPDATE documents SET category = CASE
                        WHEN is_dir = false THEN
                            CASE WHEN lower(extension) = '.pdf' THEN 'analyzed' ELSE 'storage_direct' END
                        ELSE 'folder'
                    END
                    WHERE category IS NULL OR category = ''
                    """
                )
                cursor.execute(
                    """
                    UPDATE documents
                    SET search_vector = to_tsvector(
                        'simple',
                        concat_ws(E'\\n', name, path, extension, content)
                    )
                    WHERE search_vector = ''::tsvector
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_documents_search_vector ON documents USING GIN(search_vector)"
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_path_key ON documents(path_key)")
                cursor.execute(
                    """
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'imports' AND column_name = 'payload'
                    """
                )
                row = cursor.fetchone()
                payload_type = (row[0] if row else "jsonb")
                if payload_type == "text":
                    cursor.execute("ALTER TABLE imports ALTER COLUMN payload TYPE JSONB USING payload::jsonb")
        else:
            # SQLite: ensure FTS and backfill guarded
            self._ensure_fts_schema(connection)
            # Only backfill where category is null/empty, not overwriting technical_pdf etc.
            try:
                connection.execute(
                    """
                    UPDATE documents SET category = CASE
                        WHEN is_dir = 0 THEN
                            CASE WHEN lower(extension) = '.pdf' THEN 'analyzed' ELSE 'storage_direct' END
                        ELSE 'folder'
                    END
                    WHERE category IS NULL OR category = ''
                    """
                )
            except Exception:
                pass

    def run_migrations(self) -> None:
        """Run pending schema migrations once at startup."""
        with self.connect() as connection:
            self._ensure_migrations_table(connection)
            applied = self._get_applied_migrations(connection)

            migrations = [
                ("001_initial", self._migration_001_initial),
                ("002_object_storage_columns", self._migration_002_object_storage_columns),
                ("003_category_backfill_guard", self._migration_003_category_backfill_guard),
            ]

            for version, func in migrations:
                if version not in applied:
                    logger.info("Applying schema migration %s", version)
                    try:
                        func(connection)
                        self._record_migration(connection, version)
                        logger.info("Migration %s applied", version)
                    except Exception as exc:
                        logger.error("Migration %s failed: %s", version, exc)
                        raise

    def _ensure_documents_columns_postgres(self, cursor: Any) -> None:
        """Legacy helper kept for backward compat, now delegates to migration."""
        # This is now a no-op for new code; migrations handle it.
        # Kept to avoid breaking existing tests that call it indirectly.
        cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content TEXT NOT NULL DEFAULT ''")
        cursor.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS search_vector TSVECTOR NOT NULL DEFAULT ''::tsvector"
        )
        cursor.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extractor_version INTEGER NOT NULL DEFAULT 0"
        )
        cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT ''")
        cursor.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extraction_status TEXT NOT NULL DEFAULT 'extracted'"
        )
        cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS extraction_detail TEXT NOT NULL DEFAULT ''")
        cursor.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'storage_direct'"
        )
        cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS object_key TEXT")
        cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS object_bucket TEXT")
        cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS uploaded_at DOUBLE PRECISION")
        cursor.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS upload_status TEXT NOT NULL DEFAULT 'pending'"
        )

    def initialize(self, rebuild: bool = False) -> None:
        """Create tables if not exists — no DDL backfill, no category overwrite."""
        with self.connect() as connection:
            if self.is_postgres:
                with connection.cursor() as cursor:
                    if rebuild:
                        cursor.execute("DROP TABLE IF EXISTS documents")
                        cursor.execute("DROP TABLE IF EXISTS schema_migrations")
                    cursor.execute(POSTGRES_SCHEMA)
                    # Ensure migrations table exists, but do not run migrations here
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS schema_migrations (
                            version TEXT PRIMARY KEY,
                            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                        """
                    )
            else:
                if rebuild:
                    connection.executescript(
                        """
                        DROP TABLE IF EXISTS documents_fts;
                        DROP TABLE IF EXISTS documents;
                        DROP TABLE IF EXISTS schema_migrations;
                        """
                    )
                connection.executescript(SQLITE_SCHEMA)
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                # For SQLite, ensure FTS exists but do not overwrite category
                self._ensure_fts_schema(connection)

    @contextmanager
    def scan_lock(self) -> Iterator[None]:
        if self.is_postgres:
            with self.connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_lock(748321)")
                    acquired = bool(cursor.fetchone()[0])
                    if not acquired:
                        raise ScanAlreadyRunningError("Another indexing scan is already running")
                    try:
                        yield
                    finally:
                        cursor.execute("SELECT pg_advisory_unlock(748321)")
            return

        lock_path = self.database_path.with_suffix(self.database_path.suffix + ".scan.lock")
        while True:
            try:
                lock_handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError as exc:
                try:
                    lock_pid = int(lock_path.read_text(encoding="ascii"))
                    os.kill(lock_pid, 0)
                except (FileNotFoundError, ProcessLookupError, ValueError):
                    lock_path.unlink(missing_ok=True)
                    continue
                except PermissionError:
                    pass
                raise ScanAlreadyRunningError("Another indexing scan is already running") from exc
        try:
            os.write(lock_handle, str(os.getpid()).encode("ascii"))
            yield
        finally:
            os.close(lock_handle)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    @contextmanager
    def scan_snapshot(self) -> Iterator[None]:
        if self.is_postgres:
            backup_table = f"scan_backup_documents_{os.getpid()}"
            with self.connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DROP TABLE IF EXISTS " + backup_table)
                    cursor.execute("SELECT to_regclass('public.documents')")
                    has_documents = cursor.fetchone()[0] is not None
                    if has_documents:
                        cursor.execute(f"CREATE TABLE {backup_table} AS TABLE documents")
            try:
                yield
            except Exception:
                if has_documents:
                    with self.connect() as connection:
                        with connection.cursor() as cursor:
                            cursor.execute("TRUNCATE TABLE documents")
                            cursor.execute(f"INSERT INTO documents SELECT * FROM {backup_table}")
                raise
            finally:
                with self.connect() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("DROP TABLE IF EXISTS " + backup_table)
            return

        backup_path = self.database_path.with_suffix(self.database_path.suffix + ".scan-backup")
        backup_path.unlink(missing_ok=True)
        with sqlite3.connect(self.database_path) as source, sqlite3.connect(backup_path) as target:
            source.backup(target)
        try:
            yield
        except Exception:
            with self.connect() as connection:
                scan_runs = connection.execute("SELECT * FROM scan_runs ORDER BY id").fetchall()
            source = sqlite3.connect(backup_path)
            target = sqlite3.connect(self.database_path)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            with self.connect() as connection:
                connection.execute("DELETE FROM scan_runs")
                connection.executemany(
                    "INSERT INTO scan_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [tuple(row) for row in scan_runs],
                )
            raise
        finally:
            backup_path.unlink(missing_ok=True)

    def start_scan(self) -> int:
        with self.connect() as connection:
            if self.is_postgres:
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO scan_runs (status) VALUES ('running') RETURNING id")
                    return int(cursor.fetchone()[0])
            cursor = connection.execute(
                "INSERT INTO scan_runs (started_at, status) VALUES (datetime('now'), 'running')"
            )
            return int(cursor.lastrowid)

    def finish_scan(
        self, scan_id: int, status: str, scanned: int, changed: int, removed: int, error: str | None = None
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("scan status must be completed or failed")
        with self.connect() as connection:
            if self.is_postgres:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE scan_runs
                        SET finished_at = now(), status = %s, scanned = %s, changed = %s, removed = %s, error = %s
                        WHERE id = %s
                        """,
                        (status, scanned, changed, removed, error, scan_id),
                    )
                return
            connection.execute(
                """
                UPDATE scan_runs
                SET finished_at = datetime('now'), status = ?, scanned = ?, changed = ?, removed = ?, error = ?
                WHERE id = ?
                """,
                (status, scanned, changed, removed, error, scan_id),
            )

    def latest_scan(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            if self.is_postgres:
                import psycopg2.extras

                with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    cursor.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1")
                    row = cursor.fetchone()
                    if not row:
                        return None
                    return {
                        "id": row["id"],
                        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
                        "status": row["status"],
                        "scanned": row["scanned"],
                        "changed": row["changed"],
                        "removed": row["removed"],
                        "error": row["error"],
                    }

            row = connection.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                return None
            return dict(row)

    def stored_manifest(self) -> dict[str, tuple[int, float, int]]:
        """Return a mapping of path_key -> (size, modified_at, extractor_version)."""
        if self.is_postgres:
            with self.connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT path_key, size, modified_at, extractor_version FROM documents WHERE is_dir = false"
                    )
                    return {row[0]: (int(row[1]), float(row[2]), int(row[3])) for row in cursor.fetchall()}
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT path_key, size, modified_at, extractor_version FROM documents WHERE is_dir = 0"
            ).fetchall()
            return {
                row["path_key"]: (int(row["size"]), float(row["modified_at"]), int(row["extractor_version"]))
                for row in rows
            }

    def _ensure_documents_columns(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(documents)").fetchall()}
        if "extractor_version" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN extractor_version INTEGER NOT NULL DEFAULT 0")
        if "content_hash" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")
        if "extraction_status" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN extraction_status TEXT NOT NULL DEFAULT 'extracted'")
        if "extraction_detail" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN extraction_detail TEXT NOT NULL DEFAULT ''")
        if "category" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN category TEXT NOT NULL DEFAULT 'storage_direct'")
            connection.execute(
                """
                UPDATE documents SET category = CASE
                    WHEN is_dir = 0 THEN
                        CASE WHEN lower(extension) = '.pdf' THEN 'analyzed' ELSE 'storage_direct' END
                    ELSE 'folder'
                END
                """
            )
        if "object_key" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN object_key TEXT")
        if "object_bucket" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN object_bucket TEXT")
        if "uploaded_at" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN uploaded_at REAL")
        if "upload_status" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN upload_status TEXT NOT NULL DEFAULT 'pending'")

    def _ensure_fts_schema(self, connection: sqlite3.Connection) -> None:
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'documents_fts'"
        ).fetchone()
        if schema and "content" not in schema[0]:
            connection.executescript(
                """
                DROP TABLE documents_fts;
                CREATE VIRTUAL TABLE documents_fts USING fts5(
                    name,
                    path,
                    extension,
                    content
                );
                INSERT INTO documents_fts(rowid, name, path, extension, content)
                SELECT id, name, path, extension, '' FROM documents;
                """
            )

    def upsert_document(self, document: Document) -> bool:
        return self.upsert_documents([document]) > 0

    def upsert_documents(self, documents: list[Document]) -> int:
        if not documents:
            return 0
        if self.is_postgres:
            return self._upsert_documents_postgres(documents)
        return self._upsert_documents_sqlite(documents)

    def _upsert_documents_sqlite(self, documents: list[Document]) -> int:
        changed = 0
        with self.connect() as connection:
            for document in documents:
                existing = connection.execute(
                    "SELECT id, size, modified_at, extractor_version FROM documents WHERE path_key = ?",
                    (document.path_key,),
                ).fetchone()
                if (
                    existing
                    and existing["size"] == document.size
                    and existing["modified_at"] == document.modified_at
                    and existing["extractor_version"] == document.extractor_version
                ):
                    continue

                if existing:
                    row_id = int(existing["id"])
                    connection.execute(
                        """
                        UPDATE documents
                        SET path = ?, name = ?, parent_path = ?, extension = ?, size = ?,
                            modified_at = ?, is_dir = ?, extractor_version = ?, content_hash = ?,
                            extraction_status = ?, extraction_detail = ?, category = ?,
                            object_key = ?, object_bucket = ?, uploaded_at = ?, upload_status = ?
                        WHERE id = ?
                        """,
                        _document_values(document) + (row_id,),
                    )
                    connection.execute("DELETE FROM documents_fts WHERE rowid = ?", (row_id,))
                else:
                    cursor = connection.execute(
                        """
                        INSERT INTO documents (
                            path_key, path, name, parent_path, extension, size, modified_at, is_dir,
                            extractor_version, content_hash, extraction_status, extraction_detail, category,
                            object_key, object_bucket, uploaded_at, upload_status
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (document.path_key,) + _document_values(document),
                    )
                    row_id = int(cursor.lastrowid)

                connection.execute(
                    """
                    INSERT INTO documents_fts(rowid, name, path, extension, content)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row_id,
                        document.name,
                        str(document.path),
                        document.extension,
                        document.searchable_text,
                    ),
                )
                changed += 1
        return changed

    def _upsert_documents_postgres(self, documents: list[Document]) -> int:
        values = [
            (
                document.path_key,
                str(document.path),
                document.name,
                str(document.parent_path),
                document.extension,
                document.size,
                document.modified_at,
                document.is_dir,
                document.searchable_text,
                document.searchable_text,
                document.extractor_version,
                document.content_hash,
                document.extraction_status,
                document.extraction_detail,
                document.category,
                document.object_key,
                document.object_bucket,
                document.uploaded_at,
                document.upload_status,
            )
            for document in documents
        ]
        with self.connect() as connection:
            with connection.cursor() as cursor:
                import psycopg2.extras

                psycopg2.extras.execute_values(
                    cursor,
                    """
                    INSERT INTO documents (
                        path_key, path, name, parent_path, extension, size, modified_at, is_dir, content, search_vector,
                        extractor_version, content_hash, extraction_status, extraction_detail, category,
                        object_key, object_bucket, uploaded_at, upload_status
                    )
                    VALUES %s
                    ON CONFLICT (path_key) DO UPDATE SET
                        path = EXCLUDED.path,
                        name = EXCLUDED.name,
                        parent_path = EXCLUDED.parent_path,
                        extension = EXCLUDED.extension,
                        size = EXCLUDED.size,
                        modified_at = EXCLUDED.modified_at,
                        is_dir = EXCLUDED.is_dir,
                        content = EXCLUDED.content,
                        search_vector = EXCLUDED.search_vector,
                        extractor_version = EXCLUDED.extractor_version,
                        content_hash = EXCLUDED.content_hash,
                        extraction_status = EXCLUDED.extraction_status,
                        extraction_detail = EXCLUDED.extraction_detail,
                        category = EXCLUDED.category,
                        object_key = EXCLUDED.object_key,
                        object_bucket = EXCLUDED.object_bucket,
                        uploaded_at = EXCLUDED.uploaded_at,
                        upload_status = EXCLUDED.upload_status
                    """,
                    values,
                    template=(
                        "(%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                        "to_tsvector('simple', %s), "
                        "%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    ),
                )
                return cursor.rowcount

    def remove_missing(self, seen_path_keys: set[str], scan_complete: bool = False) -> int:
        if not seen_path_keys and not scan_complete:
            return 0
        if self.is_postgres:
            with self.connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT path_key FROM documents")
                    missing = [row[0] for row in cursor.fetchall() if row[0] not in seen_path_keys]
                    if missing:
                        cursor.execute("DELETE FROM documents WHERE path_key = ANY(%s)", (missing,))
                    return len(missing)
        with self.connect() as connection:
            rows = connection.execute("SELECT id, path_key FROM documents").fetchall()
            missing = [(row["id"], row["path_key"]) for row in rows if row["path_key"] not in seen_path_keys]
            for row_id, _path_key in missing:
                connection.execute("DELETE FROM documents_fts WHERE rowid = ?", (row_id,))
                connection.execute("DELETE FROM documents WHERE id = ?", (row_id,))
            return len(missing)

    def search(self, query: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            return []

        if offset < 0:
            raise ValueError("offset must be non-negative")
        if self.is_postgres:
            return self._search_postgres(clean_query, limit, offset)
        return self._search_sqlite(clean_query, limit, offset)

    def _search_sqlite(self, clean_query: str, limit: int, offset: int) -> list[dict[str, Any]]:
        fts_query = _build_fts_query(clean_query)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    d.path,
                    d.name,
                    d.parent_path AS parent,
                    d.extension,
                    d.size,
                    d.modified_at AS modified,
                    d.is_dir,
                    d.extraction_status,
                    d.extraction_detail,
                    d.category,
                    snippet(documents_fts, 3, '<mark>', '</mark>', '...', 24) AS snippet,
                    CASE
                        WHEN lower(d.name) = lower(?) THEN 'exact_name'
                        WHEN lower(d.name) LIKE lower(?) THEN 'name'
                        WHEN lower(d.path) LIKE lower(?) THEN 'path'
                        ELSE 'content'
                    END AS match_type,
                    bm25(documents_fts) AS score
                FROM documents_fts
                JOIN documents d ON d.id = documents_fts.rowid
                WHERE documents_fts MATCH ?
                ORDER BY
                    CASE
                        WHEN lower(d.name) = lower(?) THEN 0
                        WHEN lower(d.name) LIKE lower(?) THEN 1
                        WHEN lower(d.path) LIKE lower(?) THEN 2
                        ELSE 3
                    END,
                    score
                LIMIT ? OFFSET ?
                """,
                (
                    clean_query,
                    f"%{clean_query}%",
                    f"%{clean_query}%",
                    fts_query,
                    clean_query,
                    f"%{clean_query}%",
                    f"%{clean_query}%",
                    limit,
                    offset,
                ),
            ).fetchall()

        return [dict(row) for row in rows]

    def _search_postgres(self, clean_query: str, limit: int, offset: int) -> list[dict[str, Any]]:
        like_query = f"%{clean_query}%"
        # Build prefix-matching OR query for Postgres to match SQLite semantics (4.1)
        # e.g. "voile bleue" -> "voile:* | bleue:*" with rank boost for all terms
        terms = [t for t in re.findall(r"[\w]+", clean_query, flags=re.UNICODE) if t.upper() not in {"AND", "OR", "NOT"}]
        if not terms:
            return []
        # OR with prefix — avoid backslash in f-string expression
        def _clean_term(t: str) -> str:
            return re.sub(r"[^\w]", "", t)

        ts_query_or = " | ".join(f"{_clean_term(term)}:*" for term in terms)
        # AND for boost
        ts_query_and = " & ".join(f"{_clean_term(term)}:*" for term in terms)

        with self.connect() as connection:
            import psycopg2.extras

            with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    WITH search AS (
                        SELECT
                            to_tsquery('simple', %s) AS query_or,
                            to_tsquery('simple', %s) AS query_and
                    )
                    SELECT
                        d.path,
                        d.name,
                        d.parent_path AS parent,
                        d.extension,
                        d.size,
                        d.modified_at AS modified,
                        d.is_dir,
                        d.extraction_status,
                        d.extraction_detail,
                        d.category,
                        ts_headline(
                            'simple',
                            concat_ws(E'\\n', d.name, d.path, d.extension, d.content),
                            search.query_or,
                            'StartSel=<mark>, StopSel=</mark>, MaxWords=24, MinWords=8, ShortWord=2'
                        ) AS snippet,
                        CASE
                            WHEN lower(d.name) = lower(%s) THEN 'exact_name'
                            WHEN lower(d.name) LIKE lower(%s) THEN 'name'
                            WHEN lower(d.path) LIKE lower(%s) THEN 'path'
                            ELSE 'content'
                        END AS match_type,
                        -- Rank boost for docs matching all terms
                        ts_rank_cd(d.search_vector, search.query_or) +
                        CASE WHEN d.search_vector @@ search.query_and THEN 0.5 ELSE 0 END AS score
                    FROM documents d, search
                    WHERE d.search_vector @@ search.query_or
                    ORDER BY
                        CASE
                            WHEN lower(d.name) = lower(%s) THEN 0
                            WHEN lower(d.name) LIKE lower(%s) THEN 1
                            WHEN lower(d.path) LIKE lower(%s) THEN 2
                            ELSE 3
                        END,
                        score DESC
                    LIMIT %s OFFSET %s
                    """,
                    (
                        ts_query_or,
                        ts_query_and,
                        clean_query,
                        like_query,
                        like_query,
                        clean_query,
                        like_query,
                        like_query,
                        limit,
                        offset,
                    ),
                )
                return [dict(row) for row in cursor.fetchall()]

    def stats(self) -> IndexStats:
        if self.is_postgres:
            with self.connect() as connection:
                import psycopg2.extras

                with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    cursor.execute(
                        """
                        SELECT
                            COUNT(*) AS total_documents,
                            SUM(CASE WHEN is_dir = false THEN 1 ELSE 0 END) AS files,
                            SUM(CASE WHEN is_dir = true THEN 1 ELSE 0 END) AS folders
                        FROM documents
                        """
                    )
                    row = cursor.fetchone()
            return IndexStats(
                total_documents=int(row["total_documents"] or 0),
                files=int(row["files"] or 0),
                folders=int(row["folders"] or 0),
            )
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_documents,
                    SUM(CASE WHEN is_dir = 0 THEN 1 ELSE 0 END) AS files,
                    SUM(CASE WHEN is_dir = 1 THEN 1 ELSE 0 END) AS folders
                FROM documents
                """
            ).fetchone()
        return IndexStats(
            total_documents=int(row["total_documents"] or 0),
            files=int(row["files"] or 0),
            folders=int(row["folders"] or 0),
        )

    def health_details(self) -> dict[str, Any]:
        if self.is_postgres:
            with self.connect() as connection:
                import psycopg2.extras

                with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    cursor.execute("SELECT version() AS version")
                    version = cursor.fetchone()["version"]
                    cursor.execute("SELECT pg_database_size(current_database()) AS database_bytes")
                    database_bytes = int(cursor.fetchone()["database_bytes"])
                    # Real integrity check: verify documents table exists and indexes are valid (4.2)
                    try:
                        cursor.execute("SELECT COUNT(*) AS c FROM documents")
                        doc_count = cursor.fetchone()["c"]
                        cursor.execute(
                            "SELECT indexname FROM pg_indexes WHERE tablename = 'documents' AND schemaname = 'public'"
                        )
                        indexes = cursor.fetchall()
                        integrity = "ok" if doc_count >= 0 and len(indexes) >= 1 else "degraded"
                        # Check for invalid indexes
                        cursor.execute(
                            "SELECT COUNT(*) AS invalid FROM pg_index WHERE NOT indisvalid"
                        )
                        invalid = cursor.fetchone()["invalid"]
                        if invalid > 0:
                            integrity = f"invalid_indexes:{invalid}"
                    except Exception as exc:
                        integrity = f"check_failed:{exc}"

            return {
                "backend": "postgresql",
                "database_url_configured": True,
                "database_bytes": database_bytes,
                "database_integrity": integrity,
                "version": version,
            }

        with self.connect() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        return {
            "backend": "sqlite",
            "database_path": str(self.database_path),
            "database_exists": self.database_path.exists(),
            "database_bytes": self.database_path.stat().st_size if self.database_path.exists() else 0,
            "database_integrity": integrity,
        }


def _document_values(
    document: Document,
) -> tuple[str, str, str, str, int, float, int, int, str, str, str, str, str | None, str | None, float | None, str]:
    return (
        str(document.path),
        document.name,
        str(document.parent_path),
        document.extension,
        document.size,
        document.modified_at,
        1 if document.is_dir else 0,
        document.extractor_version,
        document.content_hash,
        document.extraction_status,
        document.extraction_detail,
        document.category,
        document.object_key,
        document.object_bucket,
        document.uploaded_at,
        document.upload_status,
    )


def _build_fts_query(query: str) -> str:
    # FTS5 syntax is not exposed to users; tokenize input and quote every token.
    terms = [term for term in re.findall(r"[\w]+", query, flags=re.UNICODE) if term.upper() not in {"AND", "OR", "NOT"}]
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"*' for term in terms)
