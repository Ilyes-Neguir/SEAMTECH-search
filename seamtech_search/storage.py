"""S3-compatible Object Storage Client for MinIO, Cloudflare R2, and AWS S3.

Provides decoupled, stateless file storage:
- Stores raw drawings, Excel sheets, and generated PDF/Word reports in object storage.
- Enables zero permanent file retention on host/VPS disk.
- Automatically creates target bucket if missing.
- Generates presigned URLs for secure browser downloads and streaming.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import AppConfig

logger = logging.getLogger("seamtech_search.storage")


class StorageError(Exception):
    """Raised when an object storage operation fails."""


class S3StorageClient:
    """Client for S3-compatible APIs (MinIO in local development, Cloudflare R2 or AWS S3 in production)."""

    def __init__(
        self,
        endpoint_url: str | None = None,
        bucket_name: str = "seamtech-documents",
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region_name: str = "us-east-1",
        force_path_style: bool = True,
        prefix: str = "",
        config: AppConfig | None = None,
    ) -> None:
        if config is not None:
            self.endpoint_url = config.s3_endpoint_url
            self.bucket_name = config.s3_bucket or "seamtech-documents"
            self.access_key_id = config.s3_access_key
            self.secret_access_key = config.s3_secret_key
            self.region_name = config.s3_region or "us-east-1"
            self.force_path_style = config.s3_force_path_style
            self.prefix = config.s3_prefix or ""
        else:
            self.endpoint_url = endpoint_url
            self.bucket_name = bucket_name
            self.access_key_id = access_key_id
            self.secret_access_key = secret_access_key
            self.region_name = region_name
            self.force_path_style = force_path_style
            self.prefix = prefix

        self._s3 = None

    def is_configured(self) -> bool:
        """Check if S3 credentials/endpoint are configured."""
        return bool(self.bucket_name and (self.access_key_id or self.endpoint_url))

    def _get_client(self):
        if self._s3 is not None:
            return self._s3

        try:
            import boto3
            from botocore.config import Config

            boto_config = Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if self.force_path_style else "auto"},
                retries={"max_attempts": 3, "mode": "standard"},
            )

            self._s3 = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name=self.region_name,
                config=boto_config,
            )
            return self._s3
        except Exception as exc:
            logger.error("Failed to initialize boto3 S3 client: %s", exc)
            raise StorageError(f"S3 initialization failed: {exc}") from exc

    def ensure_bucket_exists(self) -> None:
        """Create the bucket if it does not already exist."""
        s3 = self._get_client()
        try:
            from botocore.exceptions import ClientError

            try:
                s3.head_bucket(Bucket=self.bucket_name)
            except ClientError as err:
                error_code = str(err.response.get("Error", {}).get("Code", ""))
                if error_code in ("404", "NoSuchBucket", "NotFound"):
                    logger.info("Bucket %s does not exist. Creating it...", self.bucket_name)
                    create_kwargs: dict[str, Any] = {"Bucket": self.bucket_name}
                    if self.region_name and self.region_name not in ("us-east-1", "auto"):
                        create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region_name}
                    s3.create_bucket(**create_kwargs)
                    logger.info("Bucket %s created successfully.", self.bucket_name)
                else:
                    raise
        except Exception as exc:
            logger.warning("Could not verify or create bucket %s: %s", self.bucket_name, exc)

    def upload_file(
        self,
        local_path: Path,
        remote_key: str | None = None,
        content_type: str | None = None,
    ) -> str:
        """Upload a local file to S3/MinIO and return the object key."""
        local = Path(local_path)
        if not local.exists() or not local.is_file():
            raise StorageError(f"Local file does not exist: {local}")

        s3 = self._get_client()
        self.ensure_bucket_exists()

        key = remote_key or local.name
        if self.prefix and not key.startswith(self.prefix):
            key = f"{self.prefix.rstrip('/')}/{key.lstrip('/')}"

        mime = content_type or mimetypes.guess_type(local.name)[0] or "application/octet-stream"
        extra_args = {"ContentType": mime}

        try:
            s3.upload_file(
                Filename=str(local),
                Bucket=self.bucket_name,
                Key=key,
                ExtraArgs=extra_args,
            )
            logger.info("Uploaded %s to S3 bucket %s with key %s", local.name, self.bucket_name, key)
            return key
        except Exception as exc:
            logger.error("Failed to upload %s to S3: %s", local, exc)
            raise StorageError(f"Upload failed: {exc}") from exc

    def upload_bytes(
        self,
        data: bytes,
        remote_key: str,
        content_type: str | None = None,
    ) -> str:
        """Upload in-memory bytes directly to S3/MinIO without writing to local disk."""
        s3 = self._get_client()
        self.ensure_bucket_exists()

        key = remote_key
        if self.prefix and not key.startswith(self.prefix):
            key = f"{self.prefix.rstrip('/')}/{key.lstrip('/')}"

        mime = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        try:
            s3.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=data,
                ContentType=mime,
            )
            return key
        except Exception as exc:
            logger.error("Failed to upload bytes to S3 key %s: %s", key, exc)
            raise StorageError(f"Upload bytes failed: {exc}") from exc

    def download_file(self, remote_key: str, destination_path: Path) -> Path:
        """Download an object from S3 to a local scratch destination."""
        s3 = self._get_client()
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            s3.download_file(
                Bucket=self.bucket_name,
                Key=remote_key,
                Filename=str(destination_path),
            )
            return destination_path
        except Exception as exc:
            logger.error("Failed to download S3 key %s: %s", remote_key, exc)
            raise StorageError(f"Download failed: {exc}") from exc

    def get_presigned_url(self, remote_key: str, expiration_seconds: int = 3600) -> str:
        """Generate a secure, time-limited presigned URL for direct downloading/previewing."""
        s3 = self._get_client()
        try:
            url = s3.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket_name, "Key": remote_key},
                ExpiresIn=expiration_seconds,
            )
            return url
        except Exception as exc:
            logger.error("Failed to generate presigned URL for %s: %s", remote_key, exc)
            raise StorageError(f"Presigned URL generation failed: {exc}") from exc

    def delete_file(self, remote_key: str) -> bool:
        """Delete an object from S3."""
        s3 = self._get_client()
        try:
            s3.delete_object(Bucket=self.bucket_name, Key=remote_key)
            return True
        except Exception as exc:
            logger.warning("Failed to delete S3 key %s: %s", remote_key, exc)
            return False


def upload_artifacts_to_storage(
    folder_name: str,
    files_to_upload: list[Path | None],
    config: AppConfig,
) -> str:
    """Upload multiple files to configured storage (S3/MinIO/R2 or OneDrive)."""
    valid_files = [p for p in files_to_upload if p is not None and p.exists()]
    if not valid_files:
        return "not_applicable"

    # 1. If S3 / MinIO / R2 is configured (default)
    s3_client = S3StorageClient(config=config)
    if s3_client.is_configured():
        try:
            for file_path in valid_files:
                key = f"{folder_name}/{file_path.name}"
                s3_client.upload_file(file_path, remote_key=key)
            return "uploaded"
        except Exception as exc:
            logger.error("S3 upload failed for folder %s: %s", folder_name, exc)
            return "pending_retry"

    # 2. If OneDrive is configured (fallback / alternative)
    from .onedrive import OneDriveClient

    onedrive_client = OneDriveClient(config=config)
    if onedrive_client.is_configured():
        return onedrive_client.upload_files(valid_files, folder_name)

    return "pending_not_configured"
