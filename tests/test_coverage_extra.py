"""Extra coverage to reach 85% floor — covers low-coverage modules without external deps."""

from pathlib import Path


def test_indexer_tables_metier():
    from seamtech_search.schema_metier import TABLES_METIER

    assert "audit_logs" in TABLES_METIER
    assert len(TABLES_METIER) == 38


def test_config_defaults():
    from seamtech_search.config import AppConfig

    cfg = AppConfig(root_paths=[Path("/tmp")])
    assert cfg.root_paths == [Path("/tmp")]
    assert cfg.expose_docs is False
