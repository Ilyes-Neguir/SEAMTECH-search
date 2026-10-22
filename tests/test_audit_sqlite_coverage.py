"""Couverture audit_logs SQLite — exerce les 6 variantes SQLite pour pousser overall à 85%."""

from pathlib import Path


def test_audit_logs_sqlite_couverture(tmp_path: Path):
    """SQLite (is_postgres False) — couvre les 6 requêtes SQLite de get_audit_logs_filtered."""
    from seamtech_search.audit_logs import get_audit_logs_filtered, record_audit_log
    from seamtech_search.indexer import SearchIndex

    idx = SearchIndex(tmp_path / "audit_sqlite.db")  # pas de PG URL → is_postgres False
    idx.initialize()
    idx.run_migrations()

    try:
        for i in range(5):
            record_audit_log(
                idx,
                actor_user_id=None,  # SQLite FK pas stricte, None évite FK
                actor_username="admin_sqlite",
                actor_role="administrateur",
                action="PIECE_DOWNLOAD" if i % 2 == 0 else "AUTH_LOGIN_SUCCESS",
                target_type="piece",
                target_id=f"sqlite-cov-{i}",
                outcome="success" if i < 4 else "failure",
                details={"k": f"v{i}"},
                ip_address="127.0.0.1",
                user_agent="pytest",
            )

        for sort in ("created_at", "id", "action"):
            for order in ("asc", "desc"):
                rows = get_audit_logs_filtered(idx, limit=10, offset=0, sort=sort, order=order)
                assert isinstance(rows, list)
                assert len(rows) >= 5

        # filtres
        rows = get_audit_logs_filtered(idx, limit=10, outcome="success")
        assert len(rows) >= 4
        rows = get_audit_logs_filtered(idx, limit=10, action="PIECE_DOWNLOAD")
        assert len(rows) >= 2
    finally:
        try:
            idx.close()
        except Exception:
            pass
