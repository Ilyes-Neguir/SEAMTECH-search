"""Bump global to 85% — covers storage/config paths missed in CI (PG present)."""

def test_storage_s3_client_not_configured(tmp_path):
    from seamtech_search.config import AppConfig
    from seamtech_search.storage import S3StorageClient

    cfg = AppConfig(root_paths=[tmp_path])
    client = S3StorageClient(config=cfg)
    # covers is_configured False branch
    assert not client.is_configured()
    assert not client.credentials_presentes()


def test_storage_s3_client_configured(tmp_path):
    from seamtech_search.config import AppConfig
    from seamtech_search.storage import S3StorageClient

    cfg = AppConfig(
        root_paths=[tmp_path],
        s3_endpoint_url="http://127.0.0.1:9000",
        s3_bucket="b",
        s3_access_key="k",
        s3_secret_key="s",
    )
    client = S3StorageClient(config=cfg)
    assert client.is_configured()
    assert client.credentials_presentes()


def test_config_from_env(tmp_path, monkeypatch):
    import json

    from seamtech_search.config import AppConfig

    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(json.dumps({"root_paths": [str(tmp_path)], "database_path": str(tmp_path / "db.db")}))
    monkeypatch.setenv("SEAMTECH_S3_BUCKET", "env-bucket")
    cfg = AppConfig.load(str(cfg_file))
    # couvre branche env var via load
    assert cfg.s3_bucket == "env-bucket"
    # couvre expose_docs default
    assert cfg.expose_docs is False
