from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from seamtech_search.indexer import SearchIndex


def test_pool_initialization(tmp_path: Path) -> None:
    fake_pool = MagicMock()
    with patch("psycopg2.pool.ThreadedConnectionPool", return_value=fake_pool) as mock_pool_cls:
        index = SearchIndex(
            database_path=tmp_path / "search.db",
            database_url="postgresql://user:pass@localhost:5432/testdb",
            pool_min=2,
            pool_max=8,
            statement_timeout_ms=3000,
        )
        assert index.is_postgres is True
        mock_pool_cls.assert_called_once_with(
            minconn=2,
            maxconn=8,
            dsn="postgresql://user:pass@localhost:5432/testdb",
            options="-c statement_timeout=3000",
        )
        assert index._pool == fake_pool


def test_pool_connection_checkout_checkin(tmp_path: Path) -> None:
    fake_pool = MagicMock()
    fake_conn = MagicMock()
    fake_pool.getconn.return_value = fake_conn

    with patch("psycopg2.pool.ThreadedConnectionPool", return_value=fake_pool):
        index = SearchIndex(
            database_path=tmp_path / "search.db",
            database_url="postgresql://user:pass@localhost:5432/testdb",
        )

        with index.connect() as conn:
            assert conn == fake_conn

        fake_pool.getconn.assert_called_once()
        fake_conn.commit.assert_called_once()
        fake_pool.putconn.assert_called_once_with(fake_conn)


def test_pool_connection_rollback_on_error(tmp_path: Path) -> None:
    fake_pool = MagicMock()
    fake_conn = MagicMock()
    fake_pool.getconn.return_value = fake_conn

    with patch("psycopg2.pool.ThreadedConnectionPool", return_value=fake_pool):
        index = SearchIndex(
            database_path=tmp_path / "search.db",
            database_url="postgresql://user:pass@localhost:5432/testdb",
        )

        with pytest.raises(RuntimeError, match="simulated failure"):
            with index.connect():
                raise RuntimeError("simulated failure")

        fake_conn.rollback.assert_called_once()
        fake_pool.putconn.assert_called_once_with(fake_conn)


def test_pool_close_shuts_down_all_connections(tmp_path: Path) -> None:
    fake_pool = MagicMock()

    with patch("psycopg2.pool.ThreadedConnectionPool", return_value=fake_pool):
        index = SearchIndex(
            database_path=tmp_path / "search.db",
            database_url="postgresql://user:pass@localhost:5432/testdb",
        )
        index.close()
        fake_pool.closeall.assert_called_once()
        assert index._pool is None
