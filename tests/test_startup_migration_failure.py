"""Schema migration failures must stop the API from starting (audit medium pile).

`create_app` swallowed them in two places, in two different ways:

    index.initialize()
    try:
        index.run_migrations()
    except Exception as exc:
        logger.warning("Schema migrations failed: %s", exc)   # lifespan

    index.initialize()
    try:
        index.run_migrations()
    except Exception:
        pass                                                  # create_app body

The second was completely silent. Either way the process carried on and served
traffic against a half-migrated schema, where every later query fails in a way
that points at the query rather than at the schema -- and nothing in the logs
said a migration had been skipped. A container that refuses to start is
diagnosable; one that quietly serves a wrong schema is not.

Failures that are *expected* to be survivable degrade inside the migration
instead of raising (see SearchIndex._ensure_unaccent_config, which falls back to
the `simple` text-search configuration when the role cannot CREATE EXTENSION),
so failing fast here costs nothing that was previously recoverable.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig


def _config(tmp_path: Path, name: str = "startup.db") -> AppConfig:
    return AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / name,
        min_free_bytes=0,
    )


def test_create_app_refuses_to_start_when_migrations_fail(tmp_path: Path) -> None:
    """The old `except Exception: pass` let this return a usable app."""
    with patch(
        "seamtech_search.api.SearchIndex.run_migrations",
        side_effect=RuntimeError('column "search_vector" does not exist'),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            create_app(_config(tmp_path, "construction.db"))

    message = str(excinfo.value)
    assert "Schema migrations failed" in message
    assert "app construction" in message, "the error must say which stage failed"
    assert "half-migrated" in message


def test_the_underlying_database_error_is_chained(tmp_path: Path) -> None:
    """`raise ... from exc` must preserve the real cause for the traceback."""
    cause = RuntimeError('relation "documents" does not exist')
    with patch("seamtech_search.api.SearchIndex.run_migrations", side_effect=cause):
        with pytest.raises(RuntimeError) as excinfo:
            create_app(_config(tmp_path, "chained.db"))

    assert excinfo.value.__cause__ is cause


def test_lifespan_refuses_to_serve_when_migrations_fail(tmp_path: Path) -> None:
    """The second call site: migrations fail only once the app actually starts."""
    config = _config(tmp_path, "lifespan.db")

    # First call is create_app's body, second is the lifespan startup.
    with patch(
        "seamtech_search.api.SearchIndex.run_migrations",
        side_effect=[None, RuntimeError("disk I/O error")],
    ):
        with patch("seamtech_search.api.start_background_worker"):
            with patch("seamtech_search.api.stop_background_worker"):
                app = create_app(config)
                with pytest.raises(RuntimeError) as excinfo:
                    with TestClient(app):
                        pass

    assert "startup" in str(excinfo.value)


def test_successful_migrations_still_boot(tmp_path: Path) -> None:
    """Failing fast must not become failing always."""
    app = create_app(_config(tmp_path, "healthy.db"))

    with patch("seamtech_search.api.start_background_worker"):
        with patch("seamtech_search.api.stop_background_worker"):
            with TestClient(app) as client:
                assert client.get("/live").status_code == 200


def test_migrations_run_exactly_once_per_stage(tmp_path: Path) -> None:
    """The two call sites are intentional (construction + lifespan), not doubled."""
    calls: list[str] = []

    def _record(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        calls.append("run")

    with patch("seamtech_search.api.SearchIndex.run_migrations", _record):
        app = create_app(_config(tmp_path, "counted.db"))
        assert calls == ["run"], "create_app itself must run migrations once"

        with patch("seamtech_search.api.start_background_worker"):
            with patch("seamtech_search.api.stop_background_worker"):
                with TestClient(app):
                    pass

    assert calls == ["run", "run"], "lifespan must run them again on real startup"


def test_initialize_is_called_before_migrations(tmp_path: Path) -> None:
    """Order matters: migrations ALTER tables that initialize() creates."""
    order: list[str] = []

    with patch(
        "seamtech_search.api.SearchIndex.initialize",
        lambda self, *a, **k: order.append("initialize"),
    ):
        with patch(
            "seamtech_search.api.SearchIndex.run_migrations",
            lambda self, *a, **k: order.append("migrate"),
        ):
            create_app(_config(tmp_path, "ordered.db"))

    assert order == ["initialize", "migrate"]


def test_api_module_no_longer_swallows_migration_errors() -> None:
    """Source-level guard against the bare `except Exception: pass` returning.

    Item 4h audits silent exception handlers repo-wide; this pins the specific
    one that hid a half-migrated schema, so it cannot be reintroduced by a
    well-meaning "make startup resilient" change.
    """
    source = (Path(__file__).resolve().parents[1] / "seamtech_search" / "api.py").read_text(encoding="utf-8")

    assert "_initialize_schema(index" in source, "the shared fail-fast helper is gone"
    assert "except Exception:\n        pass" not in source
    assert 'logger.warning("Schema migrations failed' not in source
