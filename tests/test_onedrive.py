import io
import json
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

from seamtech_search.onedrive import OneDriveClient


def test_static_token_upload_success(tmp_path: Path) -> None:
    test_file = tmp_path / "sheet.pdf"
    test_file.write_bytes(b"%PDF-1.4 dummy")
    report_file = tmp_path / "report.pdf"
    report_file.write_bytes(b"%PDF-1.4 report")

    client = OneDriveClient(
        access_token="test-access-token",
        drive_id="drive-123",
        token_cache_path=tmp_path / "cache.json",
        max_retries=0,
    )

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        status = client.upload_files([test_file, report_file], "FOLDER-1")
        assert status == "uploaded"
        assert mock_urlopen.call_count == 2

        # Verify Authorization header
        first_req = mock_urlopen.call_args_list[0][0][0]
        assert first_req.get_header("Authorization") == "Bearer test-access-token"
        assert "drive-123" in first_req.full_url


def test_client_credentials_grant_flow(tmp_path: Path) -> None:
    test_file = tmp_path / "sheet.pdf"
    test_file.write_bytes(b"%PDF-1.4 dummy")

    cache_file = tmp_path / "cache.json"
    client = OneDriveClient(
        client_id="daemon-app-id",
        client_secret="daemon-secret-key",
        tenant_id="contoso-tenant",
        drive_id="drive-123",
        token_cache_path=cache_file,
        max_retries=0,
    )

    def mock_urlopen_router(req, timeout=30):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        if "login.microsoftonline.com" in req.full_url:
            assert b"grant_type=client_credentials" in req.data
            assert b"client_id=daemon-app-id" in req.data
            mock_resp.read.return_value = json.dumps({
                "access_token": "daemon-access-token",
                "expires_in": 3600,
            }).encode("utf-8")
        else:
            assert req.get_header("Authorization") == "Bearer daemon-access-token"
            mock_resp.read.return_value = b""
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen_router):
        status = client.upload_files([test_file], "FOLDER-1")
        assert status == "uploaded"
        assert client.access_token == "daemon-access-token"
        assert cache_file.exists()


def test_refresh_token_flow(tmp_path: Path) -> None:
    test_file = tmp_path / "sheet.pdf"
    test_file.write_bytes(b"%PDF-1.4 dummy")

    cache_file = tmp_path / "cache.json"
    client = OneDriveClient(
        client_id="app-id",
        refresh_token="initial-refresh-token",
        drive_id="drive-123",
        token_cache_path=cache_file,
        max_retries=0,
    )

    def mock_urlopen_router(req, timeout=30):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        if "login.microsoftonline.com" in req.full_url:
            mock_resp.read.return_value = json.dumps({
                "access_token": "newly-minted-token",
                "expires_in": 3600,
                "refresh_token": "next-refresh-token",
            }).encode("utf-8")
        else:
            assert req.get_header("Authorization") == "Bearer newly-minted-token"
            mock_resp.read.return_value = b""
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen_router):
        status = client.upload_files([test_file], "FOLDER-1")
        assert status == "uploaded"
        assert client.access_token == "newly-minted-token"
        assert client.refresh_token == "next-refresh-token"
        assert cache_file.exists()


def test_proactive_refresh(tmp_path: Path) -> None:
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(
        json.dumps({
            "access_token": "expiring-soon-token",
            "expires_at": time.time() + 10,
            "refresh_token": "valid-refresh-token",
        })
    )

    client = OneDriveClient(
        client_id="app-id",
        drive_id="drive-123",
        token_cache_path=cache_file,
    )

    refresh_called = False

    def mock_urlopen(req, timeout=30):
        nonlocal refresh_called
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        if "login.microsoftonline.com" in req.full_url:
            refresh_called = True
            mock_resp.read.return_value = json.dumps({
                "access_token": "proactively-refreshed-token",
                "expires_in": 3600,
            }).encode("utf-8")
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        token = client.get_access_token()
        assert refresh_called is True
        assert token == "proactively-refreshed-token"


def test_token_cache_permissions(tmp_path: Path) -> None:
    cache_file = tmp_path / "subdir" / "token_cache.json"
    client = OneDriveClient(
        client_id="app-id",
        refresh_token="my-refresh",
        drive_id="drive-123",
        token_cache_path=cache_file,
    )

    client._save_cache("tok-123", 3600, "ref-456")
    assert cache_file.exists()
    mode = stat.S_IMODE(cache_file.stat().st_mode)
    assert mode == 0o600


def test_rotated_refresh_token_persistence(tmp_path: Path) -> None:
    cache_file = tmp_path / "cache.json"
    client = OneDriveClient(
        client_id="app-id",
        refresh_token="original-refresh-token",
        drive_id="drive-123",
        token_cache_path=cache_file,
    )

    def mock_urlopen(req, timeout=30):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        if "login.microsoftonline.com" in req.full_url:
            mock_resp.read.return_value = json.dumps({
                "access_token": "fresh-access-token",
                "expires_in": 3600,
                "refresh_token": "rotated-refresh-token-v2",
            }).encode("utf-8")
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        client.get_access_token(force_refresh=True)

    with cache_file.open("r", encoding="utf-8") as f:
        saved_data = json.load(f)
    assert saved_data["refresh_token"] == "rotated-refresh-token-v2"
    assert saved_data["access_token"] == "fresh-access-token"


def test_pending_reauth_vs_pending_retry(tmp_path: Path) -> None:
    test_file = tmp_path / "sheet.pdf"
    test_file.write_bytes(b"%PDF-1.4 dummy")

    # 1. Invalid grant / client_secret failed -> pending_reauth
    client_reauth = OneDriveClient(
        client_id="app-id",
        client_secret="bad-secret",
        drive_id="drive-123",
        token_cache_path=tmp_path / "cache1.json",
        max_retries=0,
    )

    def mock_reauth_fail(req, timeout=30):
        fp = io.BytesIO(b'{"error": "invalid_client", "error_description": "Bad client secret"}')
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=fp,
        )

    with patch("urllib.request.urlopen", side_effect=mock_reauth_fail):
        status = client_reauth.upload_files([test_file], "FOLDER-1")
        assert status == "pending_reauth"

    # 2. Transient 503 error during upload -> pending_retry
    client_retry = OneDriveClient(
        access_token="valid-static-token",
        drive_id="drive-123",
        token_cache_path=tmp_path / "cache2.json",
        max_retries=1,
    )

    def mock_server_error(req, timeout=30):
        fp = io.BytesIO(b'{"error": "server_error"}')
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=fp,
        )

    with patch("urllib.request.urlopen", side_effect=mock_server_error), patch("time.sleep"):
        status = client_retry.upload_files([test_file], "FOLDER-1")
        assert status == "pending_retry"
