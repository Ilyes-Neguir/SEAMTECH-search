from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .models import Document


SCHEMA = """
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
    is_dir INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    name,
    path,
    extension,
    content,
    content='documents',
    content_rowid='id'
);
"""


class SearchIndex:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self, rebuild: bool = False) -> None:
        with self.connect() as connection:
            if rebuild:
                connection.executescript(
                    """
                    DROP TABLE IF EXISTS documents_fts;
                    DROP TABLE IF EXISTS documents;
                    """
                )
            connection.executescript(SCHEMA)

    def upsert_document(self, document: Document) -> bool:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id, size, modified_at FROM documents WHERE path_key = ?",
                (document.path_key,),
            ).fetchone()
            if existing and existing["size"] == document.size and existing["modified_at"] == document.modified_at:
                return False

            if existing:
                row_id = int(existing["id"])
                connection.execute(
                    """
                    UPDATE documents
                    SET path = ?, name = ?, parent_path = ?, extension = ?, size = ?,
                        modified_at = ?, is_dir = ?
                    WHERE id = ?
                    """,
                    _document_values(document) + (row_id,),
                )
                connection.execute("DELETE FROM documents_fts WHERE rowid = ?", (row_id,))
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO documents (
                        path_key, path, name, parent_path, extension, size, modified_at, is_dir
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            return True

    def remove_missing(self, seen_path_keys: set[str]) -> int:
        if not seen_path_keys:
            return 0
        with self.connect() as connection:
            rows = connection.execute("SELECT id, path_key FROM documents").fetchall()
            missing = [(row["id"], row["path_key"]) for row in rows if row["path_key"] not in seen_path_keys]
            for row_id, _path_key in missing:
                connection.execute("DELETE FROM documents_fts WHERE rowid = ?", (row_id,))
                connection.execute("DELETE FROM documents WHERE id = ?", (row_id,))
            return len(missing)

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            return []

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
                LIMIT ?
                """,
                (fts_query, clean_query, f"%{clean_query}%", f"%{clean_query}%", limit),
            ).fetchall()

        return [dict(row) for row in rows]


def _document_values(document: Document) -> tuple[str, str, str, str, int, float, int]:
    return (
        str(document.path),
        document.name,
        str(document.parent_path),
        document.extension,
        document.size,
        document.modified_at,
        1 if document.is_dir else 0,
    )


def _build_fts_query(query: str) -> str:
    terms = [term.replace('"', "") for term in query.split() if term.strip()]
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"*' for term in terms)

