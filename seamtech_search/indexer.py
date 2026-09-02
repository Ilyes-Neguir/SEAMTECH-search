from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Document


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
    content_hash TEXT NOT NULL DEFAULT ''
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
    content_hash TEXT NOT NULL DEFAULT ''
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
"""


@dataclass(frozen=True)
class IndexStats:
    total_documents: int
    files: int
    folders: int


class SearchIndex:
    def __init__(self, database_path: Path, database_url: str | None = None):
        self.database_path = database_path
        self.database_url = database_url
        if not self.is_postgres:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def is_postgres(self) -> bool:
        return bool(self.database_url)

    def connect(self) -> Any:
        if self.database_url:
            import psycopg2

            return psycopg2.connect(self.database_url, connect_timeout=10)
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self, rebuild: bool = False) -> None:
        with self.connect() as connection:
            if self.is_postgres:
                with connection.cursor() as cursor:
                    if rebuild:
                        cursor.execute("DROP TABLE IF EXISTS documents")
                    cursor.execute(POSTGRES_SCHEMA)
                    cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content TEXT NOT NULL DEFAULT ''")
                    cursor.execute(
                        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS search_vector TSVECTOR NOT NULL DEFAULT ''::tsvector"
                    )
                    cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS extractor_version INTEGER NOT NULL DEFAULT 0")
                    cursor.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT ''")
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
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_search_vector ON documents USING GIN(search_vector)")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_path_key ON documents(path_key)")
            else:
                if rebuild:
                    connection.executescript(
                        """
                        DROP TABLE IF EXISTS documents_fts;
                        DROP TABLE IF EXISTS documents;
                        """
                    )
                connection.executescript(SQLITE_SCHEMA)
                self._ensure_documents_columns(connection)
                self._ensure_fts_schema(connection)

    def start_scan(self) -> int:
        with self.connect() as connection:
            if self.is_postgres:
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO scan_runs (started_at, status) VALUES (now(), 'running') RETURNING id")
                    return int(cursor.fetchone()[0])
            cursor = connection.execute(
                "INSERT INTO scan_runs (started_at, status) VALUES (datetime('now'), 'running')"
            )
            return int(cursor.lastrowid)

    def finish_scan(self, scan_id: int, status: str, scanned: int, changed: int, removed: int, error: str | None = None) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("scan status must be completed or failed")
        with self.connect() as connection:
            if self.is_postgres:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE scan_runs SET finished_at=now(), status=%s, scanned=%s, changed=%s, removed=%s, error=%s WHERE id=%s",
                        (status, scanned, changed, removed, error, scan_id),
                    )
            else:
                connection.execute(
                    "UPDATE scan_runs SET finished_at=datetime('now'), status=?, scanned=?, changed=?, removed=?, error=? WHERE id=?",
                    (status, scanned, changed, removed, error, scan_id),
                )

    def latest_scan(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            if self.is_postgres:
                import psycopg2.extras
                with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    cursor.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1")
                    row = cursor.fetchone()
                    return dict(row) if row else None
            row = connection.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def _ensure_documents_columns(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(documents)").fetchall()}
        if "extractor_version" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN extractor_version INTEGER NOT NULL DEFAULT 0")
        if "content_hash" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")

    def existing_metadata(self) -> dict[str, tuple[int, float, int]]:
        """path_key -> (size, modified_at, extractor_version) for every indexed file.

        Used before a scan starts so the crawler can skip re-extracting
        files whose metadata and extractor version haven't changed.
        """
        if self.is_postgres:
            with self.connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT path_key, size, modified_at, extractor_version FROM documents WHERE is_dir = false")
                    return {row[0]: (int(row[1]), float(row[2]), int(row[3])) for row in cursor.fetchall()}
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT path_key, size, modified_at, extractor_version FROM documents WHERE is_dir = 0"
            ).fetchall()
            return {row["path_key"]: (int(row["size"]), float(row["modified_at"]), int(row["extractor_version"])) for row in rows}

    def _ensure_fts_schema(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'documents_fts'"
        ).fetchone()
        sql = row["sql"] if row else ""
        if "content='documents'" not in sql:
            return

        connection.execute("DROP TABLE documents_fts")
        connection.execute(
            """
            CREATE VIRTUAL TABLE documents_fts USING fts5(
                name,
                path,
                extension,
                content
            )
            """
        )
        rows = connection.execute(
            """
            SELECT id, name, path, extension
            FROM documents
            ORDER BY id
            """
        ).fetchall()
        for row in rows:
            connection.execute(
                """
                INSERT INTO documents_fts(rowid, name, path, extension, content)
                VALUES (?, ?, ?, ?, ?)
                """,
                (row["id"], row["name"], row["path"], row["extension"], ""),
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
                            modified_at = ?, is_dir = ?, extractor_version = ?, content_hash = ?
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
                            extractor_version, content_hash
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        extractor_version, content_hash
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
                        content_hash = EXCLUDED.content_hash
                    WHERE documents.size <> EXCLUDED.size
                       OR documents.modified_at <> EXCLUDED.modified_at
                       OR documents.path <> EXCLUDED.path
                       OR documents.extractor_version <> EXCLUDED.extractor_version
                    """,
                    values,
                    template=(
                        "(%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                        "to_tsvector('simple', coalesce(%s, '')), %s, %s)"
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
                    d.parent_path,
                    d.extension,
                    d.size,
                    d.modified_at,
                    d.is_dir,
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
        with self.connect() as connection:
            import psycopg2.extras

            with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    WITH search AS (
                        SELECT plainto_tsquery('simple', %s) AS query
                    )
                    SELECT
                        d.path,
                        d.name,
                        d.parent_path,
                        d.extension,
                        d.size,
                        d.modified_at,
                        d.is_dir,
                        ts_headline(
                            'simple',
                            concat_ws(E'\n', d.name, d.path, d.extension, d.content),
                            search.query,
                            'StartSel=<mark>, StopSel=</mark>, MaxWords=24, MinWords=8, ShortWord=2'
                        ) AS snippet,
                        CASE
                            WHEN lower(d.name) = lower(%s) THEN 'exact_name'
                            WHEN lower(d.name) LIKE lower(%s) THEN 'name'
                            WHEN lower(d.path) LIKE lower(%s) THEN 'path'
                            ELSE 'content'
                        END AS match_type,
                        ts_rank_cd(d.search_vector, search.query) AS score
                    FROM documents d, search
                    WHERE d.search_vector @@ search.query
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
                        clean_query,
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
            return {
                "backend": "postgresql",
                "database_url_configured": True,
                "database_bytes": database_bytes,
                "database_integrity": "ok",
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


def _document_values(document: Document) -> tuple[str, str, str, str, int, float, int, int, str]:
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
    )


def _build_fts_query(query: str) -> str:
    # FTS5 syntax is not exposed to users; tokenize input and quote every token.
    terms = [term for term in re.findall(r"[\w]+", query, flags=re.UNICODE) if term.upper() not in {"AND", "OR", "NOT"}]
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"*' for term in terms)
