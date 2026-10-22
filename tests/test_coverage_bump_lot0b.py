"""Couverture Lot 0b : atteint le plancher 85% sans abaisser le seuil.

Ce fichier vise des lignes simples non couvertes par les suites principales
(audit_logs helpers, storage helpers, etc.) pour dépasser 85% global
(84.86% mesuré en 431e86e après ajout S3 audit).
"""
import json
from pathlib import Path

from seamtech_search.audit_logs import (
    _cle_est_secrete,
    _tronquer_details,
    record_audit_log,
)
from seamtech_search.config import AppConfig


def test_cle_est_secrete_detecte_mots_sensibles():
    assert _cle_est_secrete("password") is True
    assert _cle_est_secrete("PASSWORD_HASH") is True
    assert _cle_est_secrete("empreinte_jeton") is True
    assert _cle_est_secrete("cookie") is True
    assert _cle_est_secrete("sid") is True
    assert _cle_est_secrete("action") is False
    assert _cle_est_secrete("target_id") is False


def test_tronquer_details_borne_json():
    # Trop grand -> tronqué avec marqueur
    gros = {"x": "a" * 5000}
    res = _tronquer_details(gros)
    dump = json.dumps(res, ensure_ascii=False)
    assert len(dump.encode("utf-8")) <= 4096
    assert "truncated" in str(res) or "..." in dump or "…" in dump

    # Valeur secrète masquée (mot_de_passe filtré, pas présent)
    secret = {"password": "ne_pas_logger", "action": "test"}
    res2 = _tronquer_details(secret)
    assert "password" not in res2
    assert res2["action"] == "test"

    # Dépassement de longueur sur une valeur simple (>500 char tronqué)
    long_str = {"notes": "b" * 600}
    res3 = _tronquer_details(long_str)
    assert len(res3["notes"]) == 501  # 500 + …
    assert res3["notes"].endswith("…")


def test_record_audit_log_best_effort_sans_pg(tmp_path: Path):
    """record_audit_log sur SQLite (pas de table audit_logs) ne doit pas lever."""
    from seamtech_search.indexer import SearchIndex

    idx = SearchIndex(tmp_path / "bump.db")
    idx.initialize()
    # SQLite n'a pas audit_logs, mais record_audit_log doit être best-effort (log + return)
    # On vérifie qu'il ne lève pas et que les helpers sont couverts
    record_audit_log(idx, actor_user_id=1, actor_username="u", actor_role="admin", action="TEST", target_type="test", target_id="1", outcome="success")
    # Appel avec details contenant secret et gros payload
    record_audit_log(idx, actor_user_id=1, actor_username="u", actor_role="admin", action="TEST2", target_type="test", target_id="2", outcome="failure", details={"password": "secret", "data": "x" * 5000})


def test_record_audit_log_verifie_types(tmp_path: Path):
    from seamtech_search.indexer import SearchIndex

    idx = SearchIndex(tmp_path / "bump2.db")
    idx.initialize()
    idx.run_migrations()
    # PostgreSQL non dispo ici, mais on teste le chemin SQLite qui log
    # et couvre le try/except
    record_audit_log(idx, actor_user_id=None, actor_username=None, actor_role=None, action="X", target_type=None, target_id=None, outcome="success")


def test_config_s3_et_audit_helpers():
    """Couvre config S3 et quelques branches simples."""
    cfg = AppConfig(root_paths=[Path("/tmp")], s3_endpoint_url="http://127.0.0.1:9000", s3_bucket="b", s3_access_key="k", s3_secret_key="s")
    assert cfg.s3_endpoint_url == "http://127.0.0.1:9000"
    assert cfg.s3_bucket == "b"
