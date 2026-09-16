"""Coverage for storage.py — 90% target."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from seamtech_search.config import AppConfig
from seamtech_search.storage import (
    S3StorageClient,
    _suffixed_key,
    _versioning_failure,
    artifact_object_key,
    upload_artifacts_to_storage,
)


def make_cfg(tmp_path: Path, s3: bool = False) -> AppConfig:
    base = {"root_paths": [tmp_path], "database_path": tmp_path / "search.db", "min_free_bytes": 0}
    if s3:
        base.update({"s3_endpoint_url": "http://localhost:9000", "s3_bucket": "b", "s3_access_key": "a", "s3_secret_key": "s"})
    return AppConfig(**base)


def test_artifact_key_and_suffix():
    import tempfile
    tmp = Path(tempfile.gettempdir())
    root = tmp / "root"
    root.mkdir(exist_ok=True)
    f = root / "sub" / "plan.pdf"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x")
    k1 = artifact_object_key("id1", f, source_root=root, prefix="pfx")
    assert k1.startswith("pfx/id1/")
    assert k1.endswith("/plan.pdf")
    k2 = artifact_object_key("id2", f, source_root=root, prefix="pfx")
    assert k1 != k2
    assert _suffixed_key("a/b/plan.pdf", 2) == "a/b/plan-2.pdf"
    assert _suffixed_key("a/b/plan", 3) == "a/b/plan-3"


def test_versioning_failure():
    err = ClientError({"Error": {"Code": "NotImplemented"}}, "Put")
    assert _versioning_failure(err)["versioning_available"] is False
    err2 = ClientError({"Error": {"Code": "MethodNotAllowed"}}, "Get")
    assert _versioning_failure(err2)["versioning_available"] is False
    assert _versioning_failure(Exception("net"))["versioning_available"] is None


def test_is_configured_and_prefix():
    c1 = S3StorageClient(bucket_name="b", access_key_id="a")
    assert c1.is_configured() is True
    c2 = S3StorageClient(bucket_name="b")
    assert c2.is_configured() is False
    c = S3StorageClient(bucket_name="b", access_key_id="a", prefix="my/prefix")
    assert c._apply_prefix("my/prefix/file.pdf") == "my/prefix/file.pdf"
    assert c._apply_prefix("file.pdf") == "my/prefix/file.pdf"
    c2 = S3StorageClient(bucket_name="b", access_key_id="a", prefix="")
    assert c2._apply_prefix("file.pdf") == "file.pdf"


def test_object_taken_exists():
    client = S3StorageClient(bucket_name="b", access_key_id="a")
    mock = MagicMock()
    mock.head_object.return_value = {}
    with patch.object(client, "_get_client", return_value=mock):
        assert client.object_key_taken("k.pdf") is True
        assert client.object_exists("k.pdf") is True

    mock2 = MagicMock()
    mock2.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "Head")
    with patch.object(client, "_get_client", return_value=mock2):
        assert client.object_key_taken("m.pdf") is False
        assert client.object_exists("m.pdf") is False

    mock3 = MagicMock()
    mock3.head_object.side_effect = ClientError({"Error": {"Code": "403"}}, "Head")
    with patch.object(client, "_get_client", return_value=mock3):
        with pytest.raises(Exception):
            client.object_key_taken("f.pdf")
        assert client.object_exists("f.pdf") is False

    mock4 = MagicMock()
    mock4.head_object.side_effect = Exception("net")
    with patch.object(client, "_get_client", return_value=mock4):
        with pytest.raises(Exception):
            client.object_key_taken("net.pdf")
        assert client.object_exists("net.pdf") is False


def test_first_free_key():
    client = S3StorageClient(bucket_name="b", access_key_id="a")
    with patch.object(client, "object_key_taken", side_effect=[True, False]):
        free = client.first_free_key("base/file.pdf", max_candidates=5)
        assert free == "base/file-2.pdf"
    with patch.object(client, "object_key_taken", return_value=True):
        with pytest.raises(Exception):
            client.first_free_key("base/file.pdf", max_candidates=2)


def test_upload_download_presigned(tmp_path: Path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"data")
    client = S3StorageClient(bucket_name="b", access_key_id="a")
    mock = MagicMock()
    mock.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "Head")
    with patch.object(client, "_get_client", return_value=mock):
        with patch.object(client, "ensure_bucket_exists"):
            k = client.upload_file(f, remote_key="my/k.pdf")
            assert k == "my/k.pdf"
            k2 = client.upload_bytes(b"bytes", remote_key="my/b2.pdf")
            assert k2 == "my/b2.pdf"
    with pytest.raises(Exception):
        S3StorageClient(bucket_name="b", access_key_id="a").upload_file(tmp_path / "nope.pdf")

    mock_fail = MagicMock()
    mock_fail.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "Head")
    mock_fail.upload_file.side_effect = Exception("fail")
    client3 = S3StorageClient(bucket_name="b", access_key_id="a")
    with patch.object(client3, "_get_client", return_value=mock_fail):
        with patch.object(client3, "ensure_bucket_exists"):
            with pytest.raises(Exception):
                client3.upload_file(f, remote_key="k.pdf")

    mock_fail2 = MagicMock()
    mock_fail2.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "Head")
    mock_fail2.put_object.side_effect = Exception("fail")
    with patch.object(client3, "_get_client", return_value=mock_fail2):
        with patch.object(client3, "ensure_bucket_exists"):
            with pytest.raises(Exception):
                client3.upload_bytes(b"x", remote_key="k.pdf")

    client4 = S3StorageClient(bucket_name="b", access_key_id="a")
    mock4 = MagicMock()
    mock4.generate_presigned_url.return_value = "https://presigned"
    with patch.object(client4, "_get_client", return_value=mock4):
        url = client4.get_presigned_url("k.pdf", 900)
        assert "https://" in url
        dest = tmp_path / "out.pdf"
        out = client4.download_file("k.pdf", dest)
        assert out == dest
        mock4.delete_object.return_value = {}
        assert client4.delete_file("k.pdf") is True
        mock4.delete_object.side_effect = Exception("fail")
        assert client4.delete_file("k.pdf") is False
        mock4.generate_presigned_url.side_effect = Exception("fail")
        with pytest.raises(Exception):
            client4.get_presigned_url("k.pdf")


def test_ensure_bucket_and_versioning():
    client = S3StorageClient(bucket_name="b", access_key_id="a")
    mock = MagicMock()
    mock.head_bucket.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadBucket")
    mock.create_bucket.return_value = {}
    mock.put_bucket_versioning.return_value = {}
    with patch.object(client, "_get_client", return_value=mock):
        client.ensure_bucket_exists()
        mock.create_bucket.assert_called_once()

    client2 = S3StorageClient(bucket_name="b", access_key_id="a")
    mock2 = MagicMock()
    mock2.head_bucket.return_value = {}
    mock2.put_bucket_versioning.side_effect = ClientError({"Error": {"Code": "NotImplemented"}}, "Put")
    with patch.object(client2, "_get_client", return_value=mock2):
        client2.ensure_bucket_exists()
        assert client2._versioning_state["versioning_available"] is False

    client3 = S3StorageClient(bucket_name="b", access_key_id="a")
    mock3 = MagicMock()
    mock3.head_bucket.side_effect = Exception("net")
    mock3.put_bucket_versioning.return_value = {}
    with patch.object(client3, "_get_client", return_value=mock3):
        client3.ensure_bucket_exists()


def test_versioning_status():
    client = S3StorageClient(bucket_name="b")
    assert client.versioning_status()["versioning_available"] is None

    client2 = S3StorageClient(bucket_name="b", access_key_id="a")
    client2._versioning_state = {"versioning_available": True, "versioning_detail": "ok"}
    client2._versioning_checked_at = time.monotonic()
    assert client2.versioning_status()["versioning_available"] is True

    mock_probe = MagicMock()
    mock_probe.get_bucket_versioning.return_value = {"Status": "Enabled"}
    with patch.object(client2, "_get_probe_client", return_value=mock_probe):
        assert client2.versioning_status(refresh=True)["versioning_available"] is True

    mock_probe2 = MagicMock()
    mock_probe2.get_bucket_versioning.return_value = {"Status": ""}
    with patch.object(client2, "_get_probe_client", return_value=mock_probe2):
        assert client2.versioning_status(refresh=True)["versioning_available"] is False

    mock_probe3 = MagicMock()
    mock_probe3.get_bucket_versioning.side_effect = Exception("fail")
    with patch.object(client2, "_get_probe_client", return_value=mock_probe3):
        res3 = client2.versioning_status(refresh=True)
        assert res3["versioning_available"] in (None, False)


def test_upload_artifacts_branches(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    batch = upload_artifacts_to_storage("folder", [None, tmp_path / "nope.pdf"], cfg, import_id="id", source_root=tmp_path)
    assert batch.status == "not_applicable"

    f1 = tmp_path / "a.pdf"
    f1.write_bytes(b"x")
    cfg2 = make_cfg(tmp_path)
    batch2 = upload_artifacts_to_storage("folder", [f1], cfg2, import_id="id", source_root=tmp_path)
    assert batch2.status == "not_configured"

    f2 = tmp_path / "b.pdf"
    f2.write_bytes(b"y")
    cfg3 = make_cfg(tmp_path, s3=True)
    with patch("seamtech_search.storage.S3StorageClient.upload_file", return_value="id/hash/b.pdf"):
        with patch("seamtech_search.storage.S3StorageClient.object_exists", return_value=True):
            batch3 = upload_artifacts_to_storage("folder", [f2], cfg3, import_id="id", source_root=tmp_path)
            assert batch3.status == "uploaded"
            assert batch3.all_verified is True

    with patch("seamtech_search.storage.S3StorageClient.upload_file", side_effect=[Exception("fail"), "id/hash/b.pdf"]):
        with patch("seamtech_search.storage.S3StorageClient.object_exists", return_value=True):
            batch4 = upload_artifacts_to_storage("folder", [f1, f2], cfg3, import_id="id", source_root=tmp_path)
            assert batch4.status in ("partial", "failed")

    with patch("seamtech_search.storage.S3StorageClient.upload_file", side_effect=Exception("fail")):
        batch5 = upload_artifacts_to_storage("folder", [f1], cfg3, import_id="id", source_root=tmp_path)
        assert batch5.status == "failed"

    with patch("seamtech_search.storage.S3StorageClient.upload_file", return_value="k") as mock_up:
        with patch("seamtech_search.storage.S3StorageClient.object_exists", return_value=True):
            upload_artifacts_to_storage("folder", [f1, f1], cfg3, import_id="id", source_root=tmp_path)
            assert mock_up.call_count == 1
