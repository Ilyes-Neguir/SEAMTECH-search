"""OneDrive client via Microsoft Graph using standard library urllib.

Supports:
- Client-credentials grant flow (unattended daemon / service principal)
- Refresh token flow with proactive refresh
- Static access token flow
- 0600 token cache file permissions
- Rotated refresh token persistence
- Distinct pending_reauth vs pending_retry status reporting
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AppConfig

logger = logging.getLogger("seamtech_search.onedrive")


class OneDriveError(Exception):
    """Base error for OneDrive / Graph operations."""


class ReauthRequiredError(OneDriveError):
    """Raised when authentication credentials have expired or become invalid, requiring human re-auth."""


class OneDriveClient:
    """Microsoft Graph client for OneDrive uploads."""

    def __init__(
        self,
        config: AppConfig | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str | None = None,
        refresh_token: str | None = None,
        access_token: str | None = None,
        token_cache_path: str | Path | None = None,
        drive_id: str | None = None,
        remote_folder: str | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.client_id = (
            client_id
            or (getattr(config, "onedrive_client_id", None) if config else None)
            or os.getenv("SEAMTECH_ONEDRIVE_CLIENT_ID")
        )
        self.client_secret = (
            client_secret
            or (getattr(config, "onedrive_client_secret", None) if config else None)
            or os.getenv("SEAMTECH_ONEDRIVE_CLIENT_SECRET")
        )
        self.tenant_id = (
            tenant_id
            or (getattr(config, "onedrive_tenant_id", None) if config else None)
            or os.getenv("SEAMTECH_ONEDRIVE_TENANT_ID")
            or "common"
        )
        self.refresh_token = (
            refresh_token
            or (getattr(config, "onedrive_refresh_token", None) if config else None)
            or os.getenv("SEAMTECH_ONEDRIVE_REFRESH_TOKEN")
        )
        self.access_token = (
            access_token
            or (getattr(config, "onedrive_access_token", None) if config else None)
            or os.getenv("SEAMTECH_GRAPH_ACCESS_TOKEN")
        )

        cache_path = (
            token_cache_path
            or (getattr(config, "onedrive_token_cache_path", None) if config else None)
            or os.getenv("SEAMTECH_ONEDRIVE_TOKEN_CACHE_PATH")
        )
        if cache_path:
            self.token_cache_path: Path | None = Path(cache_path).expanduser().resolve()
        else:
            self.token_cache_path = Path.home() / ".seamtech" / "onedrive_token.json"

        self.drive_id = (
            drive_id
            or (getattr(config, "onedrive_drive_id", None) if config else None)
            or os.getenv("SEAMTECH_ONEDRIVE_DRIVE_ID")
        )
        self.remote_folder = (
            remote_folder
            or (getattr(config, "onedrive_remote_folder", None) if config else None)
            or os.getenv("SEAMTECH_ONEDRIVE_REMOTE_FOLDER")
        )

        if max_retries is not None:
            self.max_retries = max_retries
        elif config is not None and hasattr(config, "onedrive_max_retries"):
            self.max_retries = config.onedrive_max_retries
        elif os.getenv("SEAMTECH_UPLOAD_MAX_RETRIES"):
            try:
                self.max_retries = int(os.environ["SEAMTECH_UPLOAD_MAX_RETRIES"])
            except ValueError:
                self.max_retries = 3
        else:
            self.max_retries = 3

        self.token_expires_at: float = 0.0
        self._load_cache()

    def _load_cache(self) -> None:
        if not self.token_cache_path or not self.token_cache_path.exists():
            return
        try:
            with self.token_cache_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    if data.get("access_token"):
                        self.access_token = data["access_token"]
                    if data.get("expires_at"):
                        self.token_expires_at = float(data["expires_at"])
                    if data.get("refresh_token"):
                        self.refresh_token = data["refresh_token"]
        except Exception as exc:
            logger.warning("Failed to load OneDrive token cache from %s: %s", self.token_cache_path, exc)

    def _save_cache(self, access_token: str, expires_in: int, refresh_token: str | None = None) -> None:
        if not self.token_cache_path:
            return
        self.access_token = access_token
        self.token_expires_at = time.time() + expires_in
        if refresh_token:
            self.refresh_token = refresh_token

        try:
            self.token_cache_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "access_token": self.access_token,
                "expires_at": self.token_expires_at,
                "refresh_token": self.refresh_token,
                "updated_at": time.time(),
            }
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            fd = os.open(str(self.token_cache_path), flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            os.chmod(self.token_cache_path, 0o600)
        except Exception as exc:
            logger.warning("Failed to save OneDrive token cache to %s: %s", self.token_cache_path, exc)

    def is_configured(self) -> bool:
        has_auth = bool(self.access_token or self.refresh_token or (self.client_id and self.client_secret))
        return bool(has_auth and self.drive_id)

    def get_access_token(self, force_refresh: bool = False) -> str:
        """Obtain a valid access token, proactively refreshing if close to expiration (within 60s)."""
        now = time.time()
        # If we have a cached/static access token that is valid and not forcing refresh:
        if not force_refresh and self.access_token:
            if self.token_expires_at == 0.0 or self.token_expires_at > now + 60:
                return self.access_token

        # 1. If refresh token is available, attempt refresh-token exchange
        if self.refresh_token:
            return self._refresh_access_token()

        # 2. If client_id & client_secret are provided without refresh_token, use client-credentials grant
        if self.client_id and self.client_secret:
            return self._fetch_client_credentials_token()

        # 3. If static token is set without expiration, return it
        if self.access_token and not force_refresh:
            return self.access_token

        raise OneDriveError("No valid access token, refresh token, or client credentials available")

    def _refresh_access_token(self) -> str:
        """Exchange refresh_token for a new access_token at Microsoft OAuth endpoint."""
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        params: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token or "",
            "scope": "https://graph.microsoft.com/.default offline_access",
        }
        if self.client_id:
            params["client_id"] = self.client_id
        if self.client_secret:
            params["client_secret"] = self.client_secret

        return self._post_token_request(token_url, params, "refresh_token")

    def _fetch_client_credentials_token(self) -> str:
        """Obtain access_token using OAuth2 client-credentials grant (service principal)."""
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        params: dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": self.client_id or "",
            "client_secret": self.client_secret or "",
            "scope": "https://graph.microsoft.com/.default",
        }
        return self._post_token_request(token_url, params, "client_credentials")

    def _post_token_request(self, token_url: str, params: dict[str, str], grant_name: str) -> str:
        body = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(
            token_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            err_body = ""
            try:
                err_body = err.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if err.code in (400, 401, 403):
                logger.error("OneDrive OAuth %s failed with HTTP %s: %s", grant_name, err.code, err_body)
                raise ReauthRequiredError(f"OAuth {grant_name} failed ({err.code}): {err_body}") from err
            raise OneDriveError(f"OAuth token endpoint server error ({err.code}): {err_body}") from err
        except Exception as exc:
            raise OneDriveError(f"Failed to connect to Microsoft token endpoint: {exc}") from exc

        new_access_token = resp_data.get("access_token")
        if not new_access_token:
            raise ReauthRequiredError("OAuth token response missing access_token")
        expires_in = int(resp_data.get("expires_in", 3600))
        new_refresh_token = resp_data.get("refresh_token")

        self._save_cache(new_access_token, expires_in, new_refresh_token)
        return new_access_token

    def upload_file(self, file_path: Path, remote_folder_name: str) -> None:
        """Upload a single file to OneDrive."""
        token = self.get_access_token()
        folder_prefix = f"{self.remote_folder}/{remote_folder_name}" if self.remote_folder else remote_folder_name
        folder_prefix = folder_prefix.strip("/")

        target_url = (
            f"https://graph.microsoft.com/v1.0/drives/{self.drive_id}/root:/{folder_prefix}/{file_path.name}:/content"
        )

        data = file_path.read_bytes()
        req = urllib.request.Request(
            target_url,
            data=data,
            method="PUT",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            status_code = getattr(resp, "status", 200)
            if status_code not in (200, 201):
                raise OneDriveError(f"Unexpected status code {status_code} during file upload")

    def upload_files(
        self,
        targets: list[Path],
        folder_name: str,
    ) -> str:
        """Upload target files to OneDrive with retry logic and error classification.

        Returns one of:
        - "uploaded": all files uploaded successfully
        - "pending_reauth": authentication failed and human re-auth is needed
        - "pending_retry": transient network/server error; retry later
        - "pending_not_configured": OneDrive credentials or drive_id not provided
        """
        valid_targets = [p for p in targets if p is not None and p.exists()]
        if not valid_targets:
            return "not_applicable"

        if not self.is_configured():
            return "pending_not_configured"

        # Try obtaining token first to catch reauth errors early
        try:
            self.get_access_token()
        except ReauthRequiredError as exc:
            logger.warning("OneDrive re-authentication required: %s", exc)
            return "pending_reauth"
        except Exception as exc:
            logger.warning("OneDrive token error: %s", exc)

        attempts = 1 + self.max_retries
        last_error: str | None = None

        for attempt in range(attempts):
            try:
                for target in valid_targets:
                    self.upload_file(target, folder_name)
                return "uploaded"
            except ReauthRequiredError as exc:
                logger.warning("OneDrive re-authentication required: %s", exc)
                return "pending_reauth"
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    try:
                        self.get_access_token(force_refresh=True)
                        for target in valid_targets:
                            self.upload_file(target, folder_name)
                        return "uploaded"
                    except ReauthRequiredError:
                        return "pending_reauth"
                    except Exception as refresh_exc:
                        last_error = f"HTTP 401 and refresh failed: {refresh_exc}"
                else:
                    last_error = f"HTTPError {exc.code}: {exc.reason}"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < attempts - 1:
                time.sleep(2**attempt)

        logger.warning(
            "OneDrive upload failed for folder %s (%s): %s",
            folder_name,
            ", ".join(p.name for p in valid_targets),
            last_error or "unknown error",
        )
        return "pending_retry"


def upload_to_onedrive(
    pdf: Path | None,
    report: Path | None,
    folder: str,
    config: AppConfig | None = None,
    report_docx: Path | None = None,
    max_retries: int | None = None,
    extra_files: list[Path] | None = None,
) -> str:
    """Convenience functional wrapper supporting source files, reports, and extra files like Excel."""
    client = OneDriveClient(config=config, max_retries=max_retries)
    targets = [p for p in (pdf, report, report_docx) if p is not None]
    if extra_files:
        targets.extend([p for p in extra_files if p is not None])
    if not targets:
        return "not_applicable"
    return client.upload_files(targets, folder)
