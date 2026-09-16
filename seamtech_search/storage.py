"""S3-compatible Object Storage Client for MinIO, Cloudflare R2, and AWS S3.

Provides decoupled, stateless file storage:
- Stores raw drawings, Excel sheets, and generated PDF/Word reports in object storage.
- Enables zero permanent file retention on host/VPS disk.
- Automatically creates target bucket if missing.
- Generates presigned URLs for secure browser downloads and streaming.

Two guarantees protect the objects themselves, because a lost drawing cannot be
re-extracted from anywhere:

1. Every artifact is stored under a collision-free key
   (``{prefix}/{import_id}/{sha256(relpath)}/{filename}``), so two imports — or two
   files with the same name in different sub-folders — can never share a key.
2. An upload never reuses an occupied key. The next free ``-2``/``-3``/… key is
   used instead, and bucket versioning is requested (best effort: Cloudflare R2
   does not implement ``PutBucketVersioning``) so an overwrite would at least be
   recoverable. :meth:`S3StorageClient.versioning_status` reports honestly
   whether that layer is actually available.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import AppConfig

logger = logging.getLogger("seamtech_search.storage")

#: How long a versioning probe may take before giving up (a health check must
#: never hang on an unreachable object store).
VERSIONING_PROBE_TIMEOUT_SECONDS = 2.0

#: How long the versioning answer is reused before probing the bucket again.
VERSIONING_CACHE_SECONDS = 60.0


class StorageError(Exception):
    """Raised when an object storage operation fails."""


@dataclass
class UploadedArtifact:
    """One file that was (or was to be) stored in object storage."""

    path: str
    name: str
    key: str | None = None
    bucket: str | None = None
    status: str = "pending"
    verified: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "key": self.key,
            "bucket": self.bucket,
            "status": self.status,
            "verified": self.verified,
            "error": self.error,
        }


@dataclass
class UploadBatch:
    """Result of attempting to store a whole import's files."""

    status: str
    artifacts: list[UploadedArtifact] = field(default_factory=list)

    @property
    def all_verified(self) -> bool:
        return bool(self.artifacts) and all(
            a.status == "uploaded" and a.verified for a in self.artifacts
        )

    def to_dict(self) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self.artifacts]

    def to_payload(self) -> list[dict[str, Any]]:
        return self.to_dict()


def artifact_object_key(
    import_id: str,
    file_path: Path | str,
    *,
    source_root: Path | str | None = None,
    prefix: str = "",
) -> str:
    """Build the storage key of one import artifact: ``{prefix}/{import_id}/{sha256(relpath)}/{filename}``.

    The hash is taken over the file's path *relative to* ``source_root`` when the
    file lives inside it (stable across machines), and over the absolute path
    otherwise (reports are generated outside the customer's folder). Two imports
    of the same folder therefore get different keys (different ``import_id``), and
    two same-named files inside one import get different keys (different hash) —
    the collision that used to let a second import overwrite the first one's
    documents is gone.
    """
    path = Path(file_path)
    digest_input: str | None = None
    if source_root is not None:
        try:
            digest_input = path.resolve().relative_to(Path(source_root).resolve()).as_posix()
        except (ValueError, OSError):
            digest_input = None
    if digest_input is None:
        digest_input = str(path.resolve())
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    key = f"{import_id}/{digest}/{path.name}"
    return f"{prefix.rstrip('/')}/{key}" if prefix else key


def _suffixed_key(base_key: str, index: int) -> str:
    """``…/plan.pdf`` -> ``…/plan-2.pdf`` (the extension is kept for MIME detection)."""
    path = PurePosixPath(base_key)
    return str(path.parent / f"{path.stem}-{index}{path.suffix}")


def _versioning_failure(exc: Exception) -> dict[str, Any]:
    """Turn a failed versioning call into an honest report.

    ``False`` means the endpoint told us it cannot version buckets (Cloudflare R2
    answers 501 ``NotImplemented`` to ``Put/GetBucketVersioning``); ``None`` means
    we could not find out, because a network failure must never be reported as
    "not supported".
    """
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
    if code in {"NotImplemented", "MethodNotAllowed", "XNotImplemented", "501"}:
        return {
            "versioning_available": False,
            "versioning_detail": "this object storage endpoint does not implement bucket versioning",
        }
    return {
        "versioning_available": None,
        "versioning_detail": f"bucket versioning state could not be determined: {exc}",
    }


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
        self._probe_s3 = None
        self._versioning_attempted = False
        self._versioning_state: dict[str, Any] | None = None
        self._versioning_checked_at = 0.0
        self._versioning_lock = threading.Lock()

    def is_configured(self) -> bool:
        """Check if S3 credentials/endpoint are configured."""
        return bool(self.bucket_name and (self.access_key_id or self.endpoint_url))

    def _make_client(self, probe_timeout: float | None = None):
        """Build a boto3 client; ``probe_timeout`` shortens it for health checks."""
        import boto3
        from botocore.config import Config

        settings: dict[str, Any] = {
            "signature_version": "s3v4",
            "s3": {"addressing_style": "path" if self.force_path_style else "auto"},
            "retries": {"max_attempts": 3, "mode": "standard"},
        }
        if probe_timeout is not None:
            settings["connect_timeout"] = probe_timeout
            settings["read_timeout"] = probe_timeout
            settings["retries"] = {"max_attempts": 1, "mode": "standard"}

        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region_name,
            config=Config(**settings),
        )

    def _get_client(self):
        if self._s3 is not None:
            return self._s3

        try:
            self._s3 = self._make_client()
            return self._s3
        except Exception as exc:
            logger.error("Failed to initialize boto3 S3 client: %s", exc)
            raise StorageError(f"S3 initialization failed: {exc}") from exc

    def _get_probe_client(self):
        """A short-timeout client, so a health probe cannot hang on a dead endpoint."""
        if self._probe_s3 is None:
            try:
                self._probe_s3 = self._make_client(VERSIONING_PROBE_TIMEOUT_SECONDS)
            except Exception as exc:
                logger.error("Failed to initialize S3 probe client: %s", exc)
                raise StorageError(f"S3 initialization failed: {exc}") from exc
        return self._probe_s3

    def _apply_prefix(self, remote_key: str) -> str:
        """Apply the configured key prefix exactly once (segment-wise, so a key
        under ``seamtech-old/`` is not mistaken for one under ``seamtech/``)."""
        if not self.prefix:
            return remote_key
        normalized = self.prefix.rstrip("/")
        if remote_key == normalized or remote_key.startswith(f"{normalized}/"):
            return remote_key
        return f"{normalized}/{remote_key.lstrip('/')}"

    def ensure_bucket_exists(self) -> None:
        """Create the bucket if it does not already exist, and ask for versioning."""
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

        # Always attempted once per client: versioning is what makes an overwrite
        # recoverable, and the outcome is reported by versioning_status().
        self._enable_versioning_once()

    def _enable_versioning_once(self) -> None:
        """Best-effort ``put_bucket_versioning``.

        A failure here must never fail an upload: Cloudflare R2 does not implement
        ``PutBucketVersioning`` at all (it is absent from its S3 compatibility
        matrix), and on such an endpoint the overwrite guarantee is carried by
        refusing to reuse an occupied key instead.
        """
        if self._versioning_attempted:
            return
        self._versioning_attempted = True
        try:
            s3 = self._get_client()
            s3.put_bucket_versioning(Bucket=self.bucket_name, VersioningConfiguration={"Status": "Enabled"})
            self._versioning_state = {
                "versioning_available": True,
                "versioning_detail": "bucket versioning is enabled",
            }
            logger.info("Object versioning enabled on bucket %s", self.bucket_name)
        except Exception as exc:
            self._versioning_state = _versioning_failure(exc)
            logger.warning(
                "Object versioning unavailable on bucket %s (%s); overwrite protection relies on never "
                "reusing an existing object key",
                self.bucket_name,
                exc,
            )
        self._versioning_checked_at = time.monotonic()

    def versioning_status(self, *, refresh: bool = False) -> dict[str, Any]:
        """Report whether object versioning protects this bucket — read-only.

        Safe to call from a health probe: it never creates the bucket and never
        calls ``put_bucket_versioning``, and the answer is cached for
        :data:`VERSIONING_CACHE_SECONDS` so a health-check loop cannot turn into a
        stream of S3 calls. ``versioning_available`` is ``None`` when the answer is
        unknown (storage not configured, or the endpoint could not be reached).
        """
        if not self.is_configured():
            return {"versioning_available": None, "versioning_detail": "object storage is not configured"}

        with self._versioning_lock:
            cached = self._versioning_state
            checked_at = self._versioning_checked_at
        if not refresh and cached is not None and (time.monotonic() - checked_at) < VERSIONING_CACHE_SECONDS:
            return dict(cached)

        result = self._read_versioning()
        with self._versioning_lock:
            self._versioning_state = result
            self._versioning_checked_at = time.monotonic()
        return dict(result)

    def _read_versioning(self) -> dict[str, Any]:
        try:
            response = self._get_probe_client().get_bucket_versioning(Bucket=self.bucket_name)
        except Exception as exc:
            logger.warning("Could not read versioning state of bucket %s: %s", self.bucket_name, exc)
            return _versioning_failure(exc)
        status = str(response.get("Status") or "")
        if status == "Enabled":
            return {"versioning_available": True, "versioning_detail": "bucket versioning is enabled"}
        return {
            "versioning_available": False,
            "versioning_detail": f"bucket versioning is not enabled (status: {status or 'unset'})",
        }

    def object_key_taken(self, remote_key: str) -> bool:
        """Read-only check: is this exact key already occupied?

        :meth:`object_exists` (the post-upload read-back) answers ``False``
        whenever it cannot tell, which is the safe answer to "may I delete the
        local copy?". For "may I write to this key?" the safe answer is the
        opposite, so an inconclusive probe raises instead of inviting a write on
        top of an object that may well be there.
        """
        s3 = self._get_client()
        key = self._apply_prefix(remote_key)
        try:
            from botocore.exceptions import ClientError

            s3.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError as err:
            code = str(err.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise StorageError(
                f"Could not check whether {key} exists in bucket {self.bucket_name}: {err}"
            ) from err
        except Exception as exc:
            raise StorageError(
                f"Could not check whether {key} exists in bucket {self.bucket_name}: {exc}"
            ) from exc

    def first_free_key(self, base_key: str, max_candidates: int = 50) -> str:
        """Return ``base_key`` or the first ``-2``/``-3``/… variant that is unused.

        This is what stops an upload from destroying an object that is already
        stored: an occupied key is skipped, never overwritten.
        """
        for index, candidate in enumerate([base_key, *(_suffixed_key(base_key, i) for i in range(2, max_candidates + 2))]):
            if not self.object_key_taken(candidate):
                if index:
                    logger.warning(
                        "Object key %s is already in use; storing this upload under %s instead",
                        base_key,
                        candidate,
                    )
                return candidate
        raise StorageError(f"Could not find a free object key for {base_key} after {max_candidates} attempts")

    def object_exists(self, remote_key: str) -> bool:
        """Confirm an object really is in the bucket.

        This answers ``False`` whenever it cannot tell (the probe failed), so a
        caller that wants to delete the local copy never does so when the bucket
        itself could not be reached.
        """
        s3 = self._get_client()
        key = self._apply_prefix(remote_key)
        try:
            from botocore.exceptions import ClientError

            s3.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError as err:
            code = str(err.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            logger.warning("Could not confirm object %s in bucket %s: %s", key, self.bucket_name, err)
            return False
        except Exception as exc:
            logger.warning("Could not confirm object %s in bucket %s: %s", key, self.bucket_name, exc)
            return False

    def upload_file(
        self,
        local_path: Path,
        remote_key: str | None = None,
        content_type: str | None = None,
        *,
        avoid_overwrite: bool = True,
    ) -> str:
        """Upload a local file to S3/MinIO and return the key it was stored under.

        With ``avoid_overwrite`` (the default) an occupied key is never reused:
        the object already there is left untouched and this upload lands on the
        next free ``-2``/``-3``/… key. The caller must use the *returned* key,
        which is the only thing that is guaranteed to point at this upload.
        """
        local = Path(local_path)
        if not local.exists() or not local.is_file():
            raise StorageError(f"Local file does not exist: {local}")

        s3 = self._get_client()
        self.ensure_bucket_exists()

        key = self._apply_prefix(remote_key or local.name)
        if avoid_overwrite:
            key = self.first_free_key(key)

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
        *,
        avoid_overwrite: bool = True,
    ) -> str:
        """Upload in-memory bytes directly to S3/MinIO without writing to local disk.

        Refuses to overwrite an existing object in exactly the same way as
        :meth:`upload_file`, and returns the key the bytes were stored under.
        """
        s3 = self._get_client()
        self.ensure_bucket_exists()

        key = self._apply_prefix(remote_key)
        if avoid_overwrite:
            key = self.first_free_key(key)

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
    import_id: str | None = None,
    source_root: Path | str | None = None,
) -> UploadBatch:
    """Upload an import's files and report what really landed in storage.

    The caller receives a batch whose ``artifacts`` tell it, per file, whether the
    object is really in the bucket (read-back). The aggregate is ``uploaded`` or
    ``pending_retry`` when anything failed. A caller that wants to delete the
    local copies must check :attr:`UploadBatch.all_verified`, never the fact
    that this function returned without raising.

    Each file goes to a collision-free key
    (:func:`artifact_object_key`) and an occupied key is never reused, so this
    import cannot destroy — or be destroyed by — an earlier one that used the
    same folder name. The recorded ``key`` is the key the object actually has.
    """
    valid_files: list[Path] = []
    seen: set[str] = set()
    for candidate in files_to_upload:
        if candidate is None:
            continue
        path = Path(candidate)
        if not path.exists():
            continue
        identity = str(path.resolve())
        if identity in seen:  # the same file listed twice must not create two objects
            continue
        seen.add(identity)
        valid_files.append(path)
    if not valid_files:
        return UploadBatch(status="not_applicable")

    # 1. If S3 / MinIO / R2 is configured (default)
    s3_client = S3StorageClient(config=config)
    if s3_client.is_configured():
        namespace = import_id or folder_name or "import"
        artifacts: list[UploadedArtifact] = []
        for file_path in valid_files:
            base_key = artifact_object_key(
                namespace,
                file_path,
                source_root=source_root,
                prefix=s3_client.prefix,
            )
            artifact = UploadedArtifact(
                path=str(file_path),
                name=file_path.name,
                key=base_key,
                bucket=s3_client.bucket_name,
            )
            try:
                # The client refuses to reuse an occupied key and tells us which
                # key it really used; verifying that exact key is what authorises
                # deleting the local copy.
                key = s3_client.upload_file(file_path, remote_key=base_key)
                artifact.key = key
                artifact.status = "uploaded"
                artifact.verified = s3_client.object_exists(key)
                if not artifact.verified:
                    artifact.error = f"object {key} not found in bucket after upload"
            except Exception as exc:
                artifact.status = "failed"
                artifact.error = str(exc)
            artifacts.append(artifact)

        status = "uploaded" if all(a.status == "uploaded" and a.verified for a in artifacts) else "pending_retry"
        # Special case: if the endpoint cannot be reached, object_exists returns False
        # for every file, but we should not report pending_retry if upload itself failed?
        # The above logic already covers it: failed status -> pending_retry
        return UploadBatch(status=status, artifacts=artifacts)

    # 2. If OneDrive is configured (fallback / alternative)
    from .onedrive import OneDriveClient

    onedrive_client = OneDriveClient(config=config)
    if onedrive_client.is_configured():
        onedrive_status = onedrive_client.upload_files(valid_files, folder_name)
        artifacts = [
            UploadedArtifact(
                path=str(p),
                name=p.name,
                status="uploaded" if onedrive_status == "uploaded" else onedrive_status,
                verified=False,  # OneDrive has no head_object verification in this path
            )
            for p in valid_files
        ]
        return UploadBatch(status=onedrive_status, artifacts=artifacts)

    return UploadBatch(status="pending_not_configured")
