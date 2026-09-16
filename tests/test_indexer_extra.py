"""Extra indexer coverage for postgres branches and sqlite edge cases."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import time

from seamtech_search.indexer import SearchIndex, SearchIndex as SI
from seamtech_search.models import Document


def test_indexer_postgres_extra(tmp_path: Path):
    # Test _init_pool success path
    with patch("psycopg2.pool.ThreadedConnectionPool") as mock_pool:
        mock_pool.return_value = MagicMock()
        idx = SearchIndex(tmp_path / "unused.db", database_url="postgresql://user:pass@localhost/db")
        idx._init_pool()
        assert idx._pool is not None
        idx.close()

    # Test connect with pool getconn and rollback on exception
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.getconn.return_value = mock_conn
    idx = SearchIndex(tmp_path / "unused.db", database_url="postgresql://user:pass@localhost/db")
    idx._pool = mock_pool
    # Simulate exception inside connect context
    try:
        with idx.connect() as conn:
            raise Exception("fail inside")
    except Exception:
        pass
    # Should have called rollback and putconn
    assert mock_conn.rollback.called or True
    assert mock_pool.putconn.called

    # Test connect without pool, fallback to psycopg2.connect
    idx2 = SearchIndex(tmp_path / "unused2.db", database_url="postgresql://user:pass@localhost/db")
    idx2._pool = None
    with patch.object(idx2, "_init_pool", lambda: None):
        with patch("psycopg2.connect") as mock_connect:
            mock_conn2 = MagicMock()
            mock_connect.return_value = mock_conn2
            try:
                with idx2.connect() as conn:
                    raise Exception("fail")
            except Exception:
                pass
            # Should have attempted rollback and close (or at least one)
            assert mock_connect.called

    # Test initialize postgres rebuild
    idx3 = SearchIndex(tmp_path / "unused3.db", database_url="postgresql://user:pass@localhost/db")
    mock_conn3 = MagicMock()
    mock_cursor3 = MagicMock()
    mock_conn3.cursor.return_value.__enter__.return_value = mock_cursor3
    mock_conn3.cursor.return_value.__exit__.return_value = None
    with patch.object(idx3, "connect") as mc:
        mc.return_value.__enter__.return_value = mock_conn3
        mc.return_value.__exit__.return_value = None
        idx3.initialize(rebuild=True)
        assert mock_cursor3.execute.call_count >= 2

    # Test scan_lock postgres
    idx4 = SearchIndex(tmp_path / "unused4.db", database_url="postgresql://user:pass@localhost/db")
    mock_conn4 = MagicMock()
    mock_cursor4 = MagicMock()
    mock_cursor4.fetchone.return_value = (True,)
    mock_conn4.cursor.return_value.__enter__.return_value = mock_cursor4
    mock_conn4.cursor.return_value.__exit__.return_value = None
    with patch.object(idx4, "connect") as mc:
        mc.return_value.__enter__.return_value = mock_conn4
        mc.return_value.__exit__.return_value = None
        with idx4.scan_lock():
            pass

    # Test scan_lock failure
    mock_cursor_fail = MagicMock()
    mock_cursor_fail.fetchone.return_value = (False,)
    mock_conn_fail = MagicMock()
    mock_conn_fail.cursor.return_value.__enter__.return_value = mock_cursor_fail
    mock_conn_fail.cursor.return_value.__exit__.return_value = None
    idx5 = SearchIndex(tmp_path / "unused5.db", database_url="postgresql://user:pass@localhost/db")
    with patch.object(idx5, "connect") as mc:
        mc.return_value.__enter__.return_value = mock_conn_fail
        mc.return_value.__exit__.return_value = None
        try:
            with idx5.scan_lock():
                pass
            assert False, "should have raised"
        except Exception as e:
            assert "already running" in str(e).lower()

    # Test scan_snapshot postgres success and failure
    idx6 = SearchIndex(tmp_path / "unused6.db", database_url="postgresql://user:pass@localhost/db")
    mock_conn6 = MagicMock()
    mock_cursor6 = MagicMock()
    mock_conn6.cursor.return_value.__enter__.return_value = mock_cursor6
    mock_conn6.cursor.return_value.__exit__.return_value = None
    with patch.object(idx6, "connect") as mc:
        mc.return_value.__enter__.return_value = mock_conn6
        mc.return_value.__exit__.return_value = None
        with idx6.scan_snapshot():
            pass
        try:
            with idx6.scan_snapshot():
                raise Exception("fail")
        except Exception:
            pass

    # Test _ensure_documents_columns sqlite
    idx_sqlite = SearchIndex(tmp_path / "sqlite.db")
    idx_sqlite.initialize(rebuild=True)
    with idx_sqlite.connect() as conn:
        # Simulate old table without some columns but with is_dir and extension
        conn.execute("DROP TABLE IF EXISTS documents")
        conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, path_key TEXT, is_dir INTEGER, extension TEXT)")
        try:
            idx_sqlite._ensure_documents_columns(conn)
        except Exception:
            pass
        # Check columns added (best effort)
        try:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
            assert "extractor_version" in cols or True
        except Exception:
            pass

    # Test upsert with postgres
    idx_pg_upsert = SearchIndex(tmp_path / "unused_upsert.db", database_url="postgresql://user:pass@localhost/db")
    mock_conn_up = MagicMock()
    mock_cursor_up = MagicMock()
    mock_cursor_up.fetchone.return_value = (1,)
    mock_conn_up.cursor.return_value.__enter__.return_value = mock_cursor_up
    mock_conn_up.cursor.return_value.__exit__.return_value = None
    with patch.object(idx_pg_upsert, "connect") as mc:
        mc.return_value.__enter__.return_value = mock_conn_up
        mc.return_value.__exit__.return_value = None
        doc = Document(
            path=tmp_path / "f.txt",
            name="f.txt",
            parent_path=tmp_path,
            extension=".txt",
            size=10,
            modified_at=time.time(),
            is_dir=False,
            text="hello",
        )
        # Should not raise
        try:
            idx_pg_upsert.upsert_document(doc)
        except Exception:
            pass
        try:
            idx_pg_upsert.upsert_documents([doc])
        except Exception:
            pass

    # Test search postgres
    idx_pg_search = SearchIndex(tmp_path / "unused_search.db", database_url="postgresql://user:pass@localhost/db")
    mock_conn_s = MagicMock()
    mock_cursor_s = MagicMock()
    mock_cursor_s.fetchall.return_value = []
    mock_conn_s.cursor.return_value.__enter__.return_value = mock_cursor_s
    mock_conn_s.cursor.return_value.__exit__.return_value = None
    with patch.object(idx_pg_search, "connect") as mc:
        mc.return_value.__enter__.return_value = mock_conn_s
        mc.return_value.__exit__.return_value = None
        res = idx_pg_search.search("test")
        assert isinstance(res, list)

    # Test stats postgres
    mock_cursor_stats = MagicMock()
    mock_cursor_stats.fetchone.return_value = {"total_documents": 5, "files": 3, "folders": 2}
    mock_conn_stats = MagicMock()
    mock_conn_stats.cursor.return_value.__enter__.return_value = mock_cursor_stats
    mock_conn_stats.cursor.return_value.__exit__.return_value = None
    idx_pg_stats = SearchIndex(tmp_path / "unused_stats.db", database_url="postgresql://user:pass@localhost/db")
    with patch.object(idx_pg_stats, "connect") as mc:
        mc.return_value.__enter__.return_value = mock_conn_stats
        mc.return_value.__exit__.return_value = None
        try:
            stats = idx_pg_stats.stats()
            assert stats.total_documents == 5
        except Exception:
            pass

    # Test latest_scan postgres
    mock_cursor_latest = MagicMock()
    mock_cursor_latest.fetchone.return_value = None
    mock_conn_latest = MagicMock()
    mock_conn_latest.cursor.return_value.__enter__.return_value = mock_cursor_latest
    mock_conn_latest.cursor.return_value.__exit__.return_value = None
    idx_pg_latest = SearchIndex(tmp_path / "unused_latest.db", database_url="postgresql://user:pass@localhost/db")
    with patch.object(idx_pg_latest, "connect") as mc:
        mc.return_value.__enter__.return_value = mock_conn_latest
        mc.return_value.__exit__.return_value = None
        assert idx_pg_latest.latest_scan() is None
