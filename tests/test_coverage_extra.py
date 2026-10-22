"""Extra coverage to reach 85% floor — covers low-coverage modules without external deps."""

from pathlib import Path

from seamtech_search.audit_logs import _cle_est_secrete, _tronquer_details


def test_indexer_tables_metier():
    from seamtech_search.schema_metier import TABLES_METIER

    assert "audit_logs" in TABLES_METIER
    assert len(TABLES_METIER) == 38


def test_config_defaults():
    from seamtech_search.config import AppConfig

    cfg = AppConfig(root_paths=[Path("/tmp")])
    assert cfg.root_paths == [Path("/tmp")]
    assert cfg.expose_docs is False


def test_recherche_helpers():
    # Covers recherche.py helpers that are low coverage in SQLite run
    from seamtech_search.recherche import normaliser_requete

    assert normaliser_requete("  Voilier  ") == "voilier"
    assert normaliser_requete("") == ""


def test_bateaux_normalisation():
    from seamtech_search.bateaux.normalisation import normaliser_texte

    assert normaliser_texte("Élève") == "eleve"
    assert normaliser_texte("OEuvre") == "oeuvre"


def test_audit_logs_tronquer_et_filtre():
    assert _cle_est_secrete("token") is True
    assert _tronquer_details({"a": "x" * 10000}) is not None
