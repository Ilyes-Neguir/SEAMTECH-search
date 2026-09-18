"""Accent parity between the SQLite and PostgreSQL backends (audit issue #5b).

The SQLite backend indexes into an FTS5 table using the DEFAULT tokenizer,
which folds diacritics: "lattee" finds a document containing "lattée".
Postgres was using `to_tsvector('simple', ...)`, and `simple` only lowercases —
so the same query returned different rows depending on the backend, and
accented French fabrication sheets were unfindable by their unaccented
spelling on Postgres.

Three layers are covered here:

1. The SQLite baseline, so "parity" can never be satisfied by both backends
   returning nothing.
2. The rendered Postgres SQL, mocked — this is what catches a regression back
   to a hardcoded `'simple'`, and it runs in every environment.
3. A live PostgreSQL run (`-m postgres`) asserting the two backends return the
   SAME documents for accented and unaccented spellings.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from seamtech_search.indexer import (
    PG_FALLBACK_CONFIG,
    PG_TS_CONFIGS,
    PG_UNACCENT_CONFIG,
    SearchIndex,
)
from seamtech_search.models import Document

POSTGRES_URL = "postgresql://user:pass@localhost/db"

# Accented and unaccented spellings of the same words, plus the documents that
# contain them. Both backends must return BOTH documents for EVERY query.
ACCENT_QUERIES = ["lattee", "lattée", "eleve", "élève", "reference", "référence"]
EXPECTED_NAMES = {"voile-lattee.txt", "voile-lattée.txt"}


def _make_documents(tmp_path: Path) -> list[Document]:
    return [
        Document(
            path=tmp_path / "voile-lattée.txt",
            name="voile-lattée.txt",
            parent_path=tmp_path,
            extension=".txt",
            size=10,
            modified_at=1.0,
            is_dir=False,
            text="Grand voile lattée, élève de coupe, référence atelier",
        ),
        Document(
            path=tmp_path / "voile-lattee.txt",
            name="voile-lattee.txt",
            parent_path=tmp_path,
            extension=".txt",
            size=10,
            modified_at=1.0,
            is_dir=False,
            text="Grand voile lattee, eleve de coupe, reference atelier",
        ),
    ]


# ---------------------------------------------------------------------------
# 1. SQLite baseline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", ACCENT_QUERIES)
def test_sqlite_finds_accented_and_unaccented_spellings(tmp_path: Path, query: str) -> None:
    """The behaviour Postgres must match: accents do not change the result set."""
    index = SearchIndex(tmp_path / "sqlite.db")
    index.initialize()
    index.run_migrations()
    assert index.upsert_documents(_make_documents(tmp_path)) == 2

    names = {result["name"] for result in index.search(query)}

    assert names == EXPECTED_NAMES, f"SQLite: {query!r} returned {sorted(names)}"


def test_sqlite_migration_005_is_a_noop(tmp_path: Path) -> None:
    """Accent folding is a Postgres-only concern; SQLite must be untouched."""
    index = SearchIndex(tmp_path / "sqlite.db")
    index.initialize()

    with index.connect() as connection:
        index._migration_005_unaccent_search_vector(connection)  # must not raise

    assert index.upsert_documents(_make_documents(tmp_path)) == 2
    assert {r["name"] for r in index.search("lattée")} == EXPECTED_NAMES


# ---------------------------------------------------------------------------
# 2. Rendered Postgres SQL (mocked — runs everywhere)
# ---------------------------------------------------------------------------


class _RecordingCursor:
    def __init__(self, fetchone_value: Any = None) -> None:
        self.sql: list[str] = []
        self.params: list[Any] = []
        self._fetchone_value = fetchone_value
        self.rowcount = 1

    def __enter__(self) -> "_RecordingCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self.sql.append(sql)
        self.params.append(params)

    def fetchone(self) -> Any:
        return self._fetchone_value

    def fetchall(self) -> list[Any]:
        return []


class _RecordingConnection:
    def __init__(self, cursor: _RecordingCursor) -> None:
        self._cursor = cursor

    def cursor(self, *args: Any, **kwargs: Any) -> _RecordingCursor:
        return self._cursor


def _postgres_index(tmp_path: Path, monkeypatch, cursor: _RecordingCursor) -> SearchIndex:
    index = SearchIndex(tmp_path / "unused.db", POSTGRES_URL)

    @contextmanager
    def _fake_connect():
        yield _RecordingConnection(cursor)

    monkeypatch.setattr(index, "connect", _fake_connect)
    return index


def test_search_sql_uses_the_accent_folding_configuration(tmp_path: Path, monkeypatch) -> None:
    """Regression guard: the query must not hardcode 'simple' again."""
    cursor = _RecordingCursor()
    index = _postgres_index(tmp_path, monkeypatch, cursor)
    index._pg_ts_config = PG_UNACCENT_CONFIG

    index.search("lattée")

    assert cursor.sql, "no SQL was executed"
    sql = cursor.sql[-1]
    assert f"to_tsquery('{PG_UNACCENT_CONFIG}'" in sql
    assert f"'{PG_UNACCENT_CONFIG}'," in sql  # ts_headline configuration
    assert "simple" not in sql, "search SQL fell back to the accent-blind 'simple' configuration"


def test_search_sql_falls_back_to_simple_when_unaccent_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    cursor = _RecordingCursor()
    index = _postgres_index(tmp_path, monkeypatch, cursor)
    index._pg_ts_config = PG_FALLBACK_CONFIG

    index.search("lattée")

    assert f"to_tsquery('{PG_FALLBACK_CONFIG}'" in cursor.sql[-1]


def test_upsert_template_uses_the_accent_folding_configuration(tmp_path: Path, monkeypatch) -> None:
    """Indexed lexemes must be folded with the same configuration as queries."""
    cursor = _RecordingCursor()
    index = _postgres_index(tmp_path, monkeypatch, cursor)
    index._pg_ts_config = PG_UNACCENT_CONFIG

    captured: dict[str, Any] = {}

    def _fake_execute_values(cur, sql, argslist, template=None, **kwargs):
        captured["sql"] = sql
        captured["template"] = template
        return 2

    with patch("psycopg2.extras.execute_values", side_effect=_fake_execute_values):
        index.upsert_documents(_make_documents(tmp_path))

    assert captured["template"], "execute_values was not called with a template"
    assert f"to_tsvector('{PG_UNACCENT_CONFIG}'" in captured["template"]
    assert "simple" not in captured["template"]


def test_postgres_ts_config_resolves_from_the_catalog(tmp_path: Path, monkeypatch) -> None:
    present = _RecordingCursor(fetchone_value=(1,))
    index = _postgres_index(tmp_path, monkeypatch, present)
    with index.connect() as connection:
        assert index._postgres_ts_config(connection) == PG_UNACCENT_CONFIG

    absent = _RecordingCursor(fetchone_value=None)
    index2 = _postgres_index(tmp_path, monkeypatch, absent)
    with index2.connect() as connection:
        assert index2._postgres_ts_config(connection) == PG_FALLBACK_CONFIG


def test_postgres_ts_config_is_cached(tmp_path: Path, monkeypatch) -> None:
    cursor = _RecordingCursor(fetchone_value=(1,))
    index = _postgres_index(tmp_path, monkeypatch, cursor)
    with index.connect() as connection:
        first = index._postgres_ts_config(connection)
        calls_after_first = len(cursor.sql)
        second = index._postgres_ts_config(connection)

    assert first == second == PG_UNACCENT_CONFIG
    assert len(cursor.sql) == calls_after_first, "resolution should be cached, not re-queried"


def test_ensure_unaccent_config_survives_a_permission_failure(tmp_path: Path, monkeypatch) -> None:
    """An unprivileged role must degrade to 'simple', not abort startup."""

    class _FailingCursor(_RecordingCursor):
        def execute(self, sql: str, params: Any = None) -> None:
            self.sql.append(sql)
            if sql.strip().upper().startswith("CREATE EXTENSION"):
                raise RuntimeError('permission denied for database "seamtech"')

    cursor = _FailingCursor()
    index = _postgres_index(tmp_path, monkeypatch, cursor)

    with index.connect() as connection:
        assert index._ensure_unaccent_config(connection) is False

    assert index._pg_ts_config == PG_FALLBACK_CONFIG
    # The failure must be contained with a savepoint, or the surrounding
    # transaction stays aborted and every later statement fails too.
    assert any("ROLLBACK TO SAVEPOINT" in s.upper() for s in cursor.sql)


def test_ensure_unaccent_config_creates_the_configuration_once(tmp_path: Path, monkeypatch) -> None:
    cursor = _RecordingCursor(fetchone_value=(1,))
    index = _postgres_index(tmp_path, monkeypatch, cursor)

    with index.connect() as connection:
        assert index._ensure_unaccent_config(connection) is True

    joined = "\n".join(cursor.sql)
    assert "CREATE EXTENSION IF NOT EXISTS unaccent" in joined
    # fetchone returned a row, so the configuration already existed and must
    # NOT be recreated (Postgres has no IF NOT EXISTS for this statement).
    assert "CREATE TEXT SEARCH CONFIGURATION" not in joined
    assert index._pg_ts_config == PG_UNACCENT_CONFIG


class _AbsentThenPresentCursor(_RecordingCursor):
    """First pg_ts_config lookup misses (needs creating); the read-back hits."""

    def __init__(self) -> None:
        super().__init__()
        self._config_checks = 0

    def fetchone(self) -> Any:
        self._config_checks += 1
        return None if self._config_checks == 1 else (1,)


def test_ensure_unaccent_config_creates_configuration_when_absent(tmp_path: Path, monkeypatch) -> None:
    """First call sees no configuration, second (read-back) sees it created."""
    cursor = _AbsentThenPresentCursor()
    index = _postgres_index(tmp_path, monkeypatch, cursor)

    with index.connect() as connection:
        assert index._ensure_unaccent_config(connection) is True

    joined = " ".join(" ".join(sql.split()) for sql in cursor.sql)
    assert f"CREATE TEXT SEARCH CONFIGURATION {PG_UNACCENT_CONFIG} (COPY = simple)" in joined
    assert (
        f"ALTER TEXT SEARCH CONFIGURATION {PG_UNACCENT_CONFIG} "
        "ALTER MAPPING FOR hword, hword_part, word WITH unaccent, simple"
    ) in joined


def test_unaccent_ddl_uses_postgres_documented_grammar(tmp_path: Path, monkeypatch) -> None:
    """Regression guard for a real CI failure caused by invented SQL grammar.

    The first implementation of this migration used
    `ALTER TEXT SEARCH CONFIGURATION ... FOR ANY TO unaccent, simple`. That form
    does not exist: PostgreSQL 16's ALTER TEXT SEARCH CONFIGURATION synopsis
    only offers ADD/ALTER/DROP MAPPING ... WITH ..., and the unaccent chapter
    (F.48, "Usage") gives the recipe as

        CREATE TEXT SEARCH CONFIGURATION fr ( COPY = french );
        ALTER TEXT SEARCH CONFIGURATION fr
            ALTER MAPPING FOR hword, hword_part, word
            WITH unaccent, french_stem;

    The bogus statement raised a syntax error inside initialize(), which is not
    wrapped in a try/except, so the web container crashed on startup and the
    whole compose stack never became healthy. Mocked cursors happily recorded
    the invalid SQL and every test still passed -- which is why the grammar is
    now asserted explicitly rather than left to a live database to reject.
    """
    cursor = _AbsentThenPresentCursor()
    index = _postgres_index(tmp_path, monkeypatch, cursor)

    with index.connect() as connection:
        index._ensure_unaccent_config(connection)

    alter_statements = [
        " ".join(sql.split()) for sql in cursor.sql if sql.strip().upper().startswith("ALTER TEXT SEARCH")
    ]
    assert alter_statements, "no ALTER TEXT SEARCH CONFIGURATION statement was issued"
    for statement in alter_statements:
        assert "ALTER MAPPING FOR" in statement, statement
        assert " WITH " in statement, statement
        # The grammar that broke CI must never come back.
        assert " TO " not in statement, f"invalid ALTER TEXT SEARCH CONFIGURATION grammar: {statement}"
        assert "ADD MAPPING" not in statement, (
            "ADD MAPPING errors when the mapping already exists, and COPY = simple installs it"
        )


def test_unaccent_setup_degrades_instead_of_crashing_on_bad_ddl(tmp_path: Path, monkeypatch) -> None:
    """A DDL error must not escape initialize() and take the whole app down."""

    class _BadGrammarCursor(_RecordingCursor):
        def execute(self, sql: str, params: Any = None) -> None:
            self.sql.append(sql)
            if sql.strip().upper().startswith("ALTER TEXT SEARCH"):
                raise RuntimeError('syntax error at or near "TO"')

    cursor = _BadGrammarCursor()
    index = _postgres_index(tmp_path, monkeypatch, cursor)

    with index.connect() as connection:
        assert index._ensure_unaccent_config(connection) is False

    assert index._pg_ts_config == PG_FALLBACK_CONFIG
    assert any("ROLLBACK TO SAVEPOINT" in s.upper() for s in cursor.sql)


def test_ts_config_constants_are_the_only_allowed_values() -> None:
    """The names are interpolated into SQL, so they must stay a closed set."""
    assert PG_TS_CONFIGS == (PG_UNACCENT_CONFIG, PG_FALLBACK_CONFIG)
    assert all(name.replace("_", "").isalnum() for name in PG_TS_CONFIGS)


# ---------------------------------------------------------------------------
# 3. Live PostgreSQL parity
# ---------------------------------------------------------------------------


def _live_index(tmp_path: Path) -> tuple[SearchIndex, str]:
    database_url = os.environ.get("SEAMTECH_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    index = SearchIndex(tmp_path / "unused.db", database_url)
    index.initialize()
    index.run_migrations()
    return index, database_url


@pytest.mark.postgres
def test_postgres_folds_accents_like_sqlite(tmp_path: Path) -> None:
    """Index a word with and without accents on BOTH backends; results must match."""
    documents = _make_documents(tmp_path)
    marker = uuid.uuid4().hex

    # A per-run marker keeps this test isolated from other rows in the shared
    # CI database, and lets the cleanup target exactly what it inserted.
    tagged = [
        Document(
            path=doc.path.with_name(f"{marker}-{doc.name}"),
            name=f"{marker}-{doc.name}",
            parent_path=doc.parent_path,
            extension=doc.extension,
            size=doc.size,
            modified_at=doc.modified_at,
            is_dir=False,
            text=doc.text,
        )
        for doc in documents
    ]
    expected = {doc.name for doc in tagged}

    sqlite_index = SearchIndex(tmp_path / "parity.db")
    sqlite_index.initialize()
    sqlite_index.run_migrations()
    assert sqlite_index.upsert_documents(tagged) == 2

    postgres_index, _ = _live_index(tmp_path)
    try:
        assert postgres_index.upsert_documents(tagged) == 2

        # Migration 005 must have run and the accent-folding config must exist.
        with postgres_index.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version FROM schema_migrations")
                applied = {row[0] for row in cursor.fetchall()}
        assert "005_unaccent_search_vector" in applied
        assert postgres_index._pg_ts_config == PG_UNACCENT_CONFIG

        for query in ACCENT_QUERIES:
            sqlite_names = {
                r["name"]
                for r in sqlite_index.search(query, limit=200)
                if r["name"].startswith(marker)
            }
            postgres_names = {
                r["name"]
                for r in postgres_index.search(query, limit=200)
                if r["name"].startswith(marker)
            }

            # Guard against a vacuous pass: SQLite is the known-good baseline
            # and must find both documents before parity means anything.
            assert sqlite_names == expected, f"SQLite baseline broke for {query!r}: {sorted(sqlite_names)}"
            assert postgres_names == sqlite_names, (
                f"accent parity broken for {query!r}: "
                f"postgres={sorted(postgres_names)} sqlite={sorted(sqlite_names)}"
            )
    finally:
        with postgres_index.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM documents WHERE path_key = ANY(%s)", ([d.path_key for d in tagged],))


@pytest.mark.postgres
def test_postgres_rebuilds_existing_vectors_on_migration(tmp_path: Path) -> None:
    """Rows indexed BEFORE migration 005 must become findable by unaccented query.

    Migration 005 rebuilds every search_vector, not just the empty ones; this
    proves a pre-existing deployment gets fixed rather than staying broken.
    """
    postgres_index, _ = _live_index(tmp_path)
    marker = uuid.uuid4().hex
    document = Document(
        path=tmp_path / f"{marker}-préexistante.txt",
        name=f"{marker}-préexistante.txt",
        parent_path=tmp_path,
        extension=".txt",
        size=10,
        modified_at=1.0,
        is_dir=False,
        text="Document indexé avant la migration unaccent",
    )

    try:
        assert postgres_index.upsert_documents([document]) == 1

        # Simulate a legacy row: rewrite its vector with the accent-blind config.
        with postgres_index.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE documents SET search_vector = to_tsvector('simple', %s) WHERE path_key = %s",
                    (document.searchable_text, document.path_key),
                )
        legacy_hits = {
            r["name"] for r in postgres_index.search(f"{marker}-preexistante", limit=50)
        }
        assert not legacy_hits, "a 'simple'-indexed row should not match an unaccented query"

        # Re-running the migration must fold the legacy row's lexemes.
        postgres_index._pg_ts_config = None
        with postgres_index.connect() as connection:
            postgres_index._migration_005_unaccent_search_vector(connection)

        fixed_hits = {r["name"] for r in postgres_index.search(f"{marker}-preexistante", limit=50)}
        assert document.name in fixed_hits
    finally:
        with postgres_index.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM documents WHERE path_key = %s", (document.path_key,))


@pytest.mark.postgres
def test_postgres_snippet_keeps_original_accents(tmp_path: Path) -> None:
    """Folding must not strip accents out of the snippet shown in the UI.

    This is why the fix is a text-search configuration (which lexizes the
    original text) rather than `unaccent()` applied to the text itself.
    """
    postgres_index, _ = _live_index(tmp_path)
    marker = uuid.uuid4().hex
    document = Document(
        path=tmp_path / f"{marker}-snippet.txt",
        name=f"{marker}-snippet.txt",
        parent_path=tmp_path,
        extension=".txt",
        size=10,
        modified_at=1.0,
        is_dir=False,
        text="Grand voile lattée avec deux ris de réduction",
    )

    try:
        assert postgres_index.upsert_documents([document]) == 1
        results = postgres_index.search(f"{marker}-snippet lattee", limit=10)
        matching = [r for r in results if r["name"] == document.name]
        assert matching, "unaccented query did not find the accented document"
        snippet = matching[0].get("snippet") or ""
        assert "lattée" in snippet, f"snippet lost its accents: {snippet!r}"
    finally:
        with postgres_index.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM documents WHERE path_key = %s", (document.path_key,))

