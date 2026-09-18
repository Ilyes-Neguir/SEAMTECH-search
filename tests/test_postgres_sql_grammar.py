"""Parse every PostgreSQL statement the indexer emits, using PostgreSQL's grammar.

Why this file exists
--------------------
An invented statement reached CI and took the deployment down:

    ALTER TEXT SEARCH CONFIGURATION seamtech_unaccent FOR ANY TO unaccent, simple

PostgreSQL has no `FOR ... TO ...` form; the grammar is
`ALTER MAPPING FOR <token types> WITH <dictionaries>`. The statement raised a
syntax error inside `initialize()`, which is not wrapped in try/except, so
uvicorn died, the web container never became healthy, and `docker compose up`
failed with "dependency failed to start". Every mocked test passed, because a
recording cursor accepts any string -- it cannot tell valid SQL from nonsense.

`pglast` embeds libpg_query, PostgreSQL's own parser, so the statements below
are checked against the real grammar with no server running. That closes the gap
between "the mock recorded something" and "PostgreSQL would accept it", in an
environment (this sandbox, and any contributor laptop without Postgres) where a
live database is not available.

The placeholder substitution is deliberately mechanical: psycopg2's `%s` is
replaced with `NULL`, which is a valid expression in every position these
statements use it. This validates grammar, not types -- type errors still need
the live `-m postgres` suite, which CI runs against a real server.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pglast
import pytest

from seamtech_search.indexer import SearchIndex
from seamtech_search.models import Document

POSTGRES_URL = "postgresql://user:pass@localhost/db"
PLACEHOLDER = "%s"
SUBSTITUTE = "NULL"


class _GrammarCursor:
    """Records SQL and answers catalog probes so every branch is exercised.

    The `pg_ts_config` existence check must answer "absent" on first call,
    otherwise the CREATE/ALTER DDL is skipped and never recorded -- which is how
    an earlier version of this harness silently passed while the buggy statement
    was still in the source. Once the configuration has been created, later
    probes answer "present", matching a real database.

    `fetchall()` returns no applied migrations, so `run_migrations()` runs the
    whole chain (001-005) and every migration's SQL is validated.
    """

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.rowcount = 1
        self._last_sql = ""
        self._config_created = False

    def __enter__(self) -> "_GrammarCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self.sql.append(sql)
        self._last_sql = sql
        if "CREATE TEXT SEARCH CONFIGURATION" in sql.upper():
            self._config_created = True

    def executemany(self, sql: str, seq: Any) -> None:
        self.sql.append(sql)

    def fetchone(self) -> Any:
        if "pg_ts_config" in self._last_sql:
            return (1,) if self._config_created else None
        return (1,)

    def fetchall(self) -> list[Any]:
        return []


class _GrammarConnection:
    def __init__(self, cursor: _GrammarCursor) -> None:
        self._cursor = cursor

    def cursor(self, *args: Any, **kwargs: Any) -> _GrammarCursor:
        return self._cursor


def _collect_statements(tmp_path: Path) -> list[str]:
    """Drive every Postgres code path and return the SQL it would execute."""
    cursor = _GrammarCursor()
    index = SearchIndex(tmp_path / "unused.db", POSTGRES_URL)

    @contextmanager
    def _fake_connect():
        yield _GrammarConnection(cursor)

    index.connect = _fake_connect  # type: ignore[method-assign]

    index.initialize()
    index.run_migrations()
    index.search("lattée")
    index.search("lattee OR voile")

    document = Document(
        path=tmp_path / "voile-lattée.txt",
        name="voile-lattée.txt",
        parent_path=tmp_path,
        extension=".txt",
        size=10,
        modified_at=1.0,
        is_dir=False,
        text="Grand voile lattée, élève de coupe, référence atelier",
    )

    captured: dict[str, Any] = {}

    def _fake_execute_values(cur: Any, sql: str, argslist: Any, template: str | None = None, **kwargs: Any):
        captured["sql"] = sql
        captured["template"] = template
        return 1

    with patch("psycopg2.extras.execute_values", side_effect=_fake_execute_values):
        index.upsert_documents([document])

    statements = list(cursor.sql)
    # execute_values receives the INSERT with `VALUES %s` plus a per-row
    # template; recombining them yields the statement Postgres actually parses.
    if captured.get("sql") and captured.get("template"):
        statements.append(captured["sql"].replace(PLACEHOLDER, captured["template"], 1))
    return statements


def _parse(statement: str) -> None:
    pglast.parse_sql(statement.replace(PLACEHOLDER, SUBSTITUTE))


@pytest.fixture(scope="module")
def statements(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    return _collect_statements(tmp_path_factory.mktemp("grammar"))


def test_parser_rejects_the_statement_that_broke_ci() -> None:
    """The harness must have teeth: prove libpg_query rejects the invalid form.

    Without this, a passing suite could mean "the SQL is valid" or "the parser
    is not actually parsing".
    """
    with pytest.raises(pglast.parser.ParseError):
        pglast.parse_sql("ALTER TEXT SEARCH CONFIGURATION seamtech_unaccent FOR ANY TO unaccent, simple")

    # And accepts the documented replacement.
    pglast.parse_sql(
        "ALTER TEXT SEARCH CONFIGURATION seamtech_unaccent "
        "ALTER MAPPING FOR hword, hword_part, word WITH unaccent, simple"
    )


def test_statements_were_actually_collected(statements: list[str]) -> None:
    """Guard against a vacuous pass if the driver stops reaching the database."""
    assert len(statements) >= 20, f"only {len(statements)} statements collected; driver is not exercising the paths"
    joined = "\n".join(statements).upper()
    for expected in ("CREATE TABLE", "CREATE EXTENSION", "TEXT SEARCH CONFIGURATION", "TO_TSQUERY"):
        assert expected in joined, f"no statement mentions {expected}; that code path was not exercised"


def test_every_postgres_statement_is_grammatically_valid(statements: list[str]) -> None:
    failures: list[str] = []
    for statement in statements:
        text = " ".join(statement.split())
        if not text:
            continue
        try:
            _parse(statement)
        except pglast.parser.ParseError as exc:
            failures.append(f"{exc}\n    {text[:200]}")

    assert not failures, "PostgreSQL would reject these statements:\n  " + "\n  ".join(failures)


def test_unaccent_ddl_is_emitted_and_valid(statements: list[str]) -> None:
    """The accent-folding setup must appear, in the documented order."""
    ddl = [s for s in statements if "TEXT SEARCH CONFIGURATION" in s.upper()]

    assert any("CREATE EXTENSION" in s.upper() for s in statements), "unaccent extension is never created"
    assert any(s.strip().upper().startswith("CREATE TEXT SEARCH CONFIGURATION") for s in ddl)
    assert any("ALTER MAPPING FOR" in s.upper() for s in ddl), "the mapping is never altered"

    for statement in ddl:
        _parse(statement)


def test_execute_values_template_forms_a_valid_insert(statements: list[str]) -> None:
    """The upsert template is interpolated into SQL, so validate it recombined."""
    inserts = [s for s in statements if s.strip().upper().startswith("INSERT INTO DOCUMENTS")]

    assert inserts, "the upsert INSERT was not captured"
    for statement in inserts:
        assert "to_tsvector(" in statement, "search_vector is not built in the INSERT"
        _parse(statement)


def test_no_percent_s_survives_inside_a_string_literal(statements: list[str]) -> None:
    """The NULL substitution is only safe if `%s` never appears inside quotes.

    If a future statement embeds a placeholder in a literal, this test fails and
    the substitution here needs to become quote-aware rather than silently
    validating a mangled statement.
    """
    for statement in statements:
        in_single = False
        for i, char in enumerate(statement):
            if char == "'":
                in_single = not in_single
            elif in_single and statement.startswith(PLACEHOLDER, i):
                pytest.fail(f"placeholder inside a string literal: {' '.join(statement.split())[:160]}")


def test_statements_parse_with_a_fresh_temp_directory(tmp_path: Path) -> None:
    """Independent run, so a module-scoped fixture cannot mask a failure."""
    with tempfile.TemporaryDirectory() as scratch:
        collected = _collect_statements(Path(scratch))
    assert collected
    for statement in collected:
        if statement.strip():
            _parse(statement)
