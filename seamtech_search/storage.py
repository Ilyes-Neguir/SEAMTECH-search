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

import base64
import hashlib
import logging
import mimetypes
import os
import threading
import time
import urllib.parse
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


class StoragePreflightError(StorageError):
    """Aucune écriture d'objet n'a été tentée par ce client."""


class StorageWriteUncertain(StorageError):
    """Une écriture a été tentée ; sa réponse peut avoir été perdue."""

    def __init__(self, key: str) -> None:
        super().__init__("Upload failed; remote state may be ambiguous")
        self.key = key  # la clé n'est jamais incluse dans le message d'erreur


@dataclass
class UploadedArtifact:
    """One file that was (or was to be) stored in object storage."""

    path: str
    name: str
    key: str | None = None
    bucket: str | None = None
    status: str = "pending"
    #: Vrai UNIQUEMENT si les OCTETS stockés ont été vérifiés (relecture) ou si
    #: le fournisseur a lui-même calculé l'empreinte sur l'objet stocké. Jamais
    #: vrai sur une simple comparaison de métadonnées que NOUS avons fournies.
    verified: bool = False
    #: Methode réellement employée : "relecture_sha256", "checksum_serveur",
    #: "metadata_seule" (déclaratif, non prouvé), "taille_seule", "echec".
    verification: str = "non_verifie"
    verification_detail: str | None = None
    error: str | None = None
    sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        resultat = {
            "path": self.path,
            "name": self.name,
            "key": self.key,
            "bucket": self.bucket,
            "status": self.status,
            "verified": self.verified,
            "verification": self.verification,
            "verification_detail": self.verification_detail,
            "error": self.error,
        }
        if self.sha256 is not None:
            resultat["sha256"] = self.sha256
        return resultat


@dataclass
class UploadBatch:
    """Result of attempting to store a whole import's files."""

    status: str
    artifacts: list[UploadedArtifact] = field(default_factory=list)

    @property
    def all_verified(self) -> bool:
        return bool(self.artifacts) and all(
            a.status == "uploaded" and a.verified and bool(a.key) and bool(a.bucket) for a in self.artifacts
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
    lexical: bool = False,
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
    # Lorsqu'une copie épinglée est envoyée, le chemin logique ne doit pas
    # être rouvert/résolu : il a pu être substitué depuis la prise de copie.
    identite = Path(os.path.abspath(path)) if lexical else path.resolve()
    digest_input: str | None = None
    if source_root is not None:
        try:
            racine = Path(os.path.abspath(source_root)) if lexical else Path(source_root).resolve()
            digest_input = identite.relative_to(racine).as_posix()
        except (ValueError, OSError):
            digest_input = None
    if digest_input is None:
        digest_input = str(identite)
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    key = f"{import_id}/{digest}/{path.name}"
    return f"{prefix.rstrip('/')}/{key}" if prefix else key


#: Clé de métadonnée S3 portant l'empreinte du contenu. Volontairement une
#: métadonnée applicative et PAS l'ETag : sur un upload multipart (au-delà de
#: 8 Mio par défaut) l'ETag S3/MinIO n'est pas un MD5, et sur R2 il peut ne pas
#: l'être du tout. Comparer un ETag à un MD5 « marche » sur les petits fichiers
#: et se casse silencieusement sur les gros.
METADATA_SHA256 = "seamtech-sha256"
METADATA_TAILLE = "seamtech-taille"
#: Chemin RELATIF d'origine, conservé avec l'objet. L'empreinte du chemin est
#: dans la CLÉ (impossible à inverser) et le nom du fichier est en fin de clé ;
#: la base garde aussi ``documents.path``. Cette métadonnée rend le chemin
#: reconstructible depuis le seul bucket, ce qui est ce qu'on veut le jour où
#: l'on doit retrouver d'où vient un objet. Encodée en pourcentage : les
#: en-têtes S3 n'acceptent pas d'octets non-ASCII (accents, espaces).
METADATA_CHEMIN = "seamtech-chemin"


def chemin_relatif_origine(chemin: Path, racine: Path | str | None = None, *, lexical: bool = False) -> str:
    """Chemin d'origine d'un artefact : relatif à ``racine`` si possible, sinon son nom."""
    if racine is not None:
        try:
            fichier = Path(os.path.abspath(chemin)) if lexical else Path(chemin).resolve()
            base = Path(os.path.abspath(racine)) if lexical else Path(racine).resolve()
            return fichier.relative_to(base).as_posix()
        except (ValueError, OSError):
            pass
    return Path(chemin).name


@dataclass(frozen=True)
class ResultatVerification:
    """Ce qu'une vérification d'objet a RÉELLEMENT prouvé — et par quel moyen.

    Distinction essentielle, souvent confondue :

    * ``seamtech-sha256`` est une métadonnée **que nous fournissons** à l'envoi.
      La relire ne prouve rien sur les octets stockés : un envoi tronqué
      conserverait la métadonnée intacte. Ce n'est donc PAS une vérification.
    * ``relecture_sha256`` relit l'objet par l'API et recalcule l'empreinte sur
      les octets REÇUS : c'est une preuve sur le contenu.
    * ``checksum_serveur`` est l'empreinte calculée par le fournisseur sur
      l'objet stocké (``ChecksumSHA256`` avec ``ChecksumType: FULL_OBJECT``).
      Elle aussi porte sur les octets — mais uniquement pour un objet non
      multipart, d'où la garde ``FULL_OBJECT``.

    Seul ``integrite_prouvee=True`` autorise la purge de la copie locale.
    """

    integrite_prouvee: bool
    methode: str
    detail: str
    sha256: str | None = None


def empreinte_sha256(chemin: Path) -> str:
    """SHA-256 d'un fichier, lu par blocs (jamais tout en mémoire)."""
    digest = hashlib.sha256()
    with Path(chemin).open("rb") as flux:
        for bloc in iter(lambda: flux.read(1024 * 1024), b""):
            digest.update(bloc)
    return digest.hexdigest()


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
        "versioning_detail": "bucket versioning state could not be determined",
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
        # Clients de signature liés à un endpoint public (jamais pour lire).
        self._clients_publics: dict[str, Any] = {}
        self._probe_s3 = None
        self._versioning_attempted = False
        self._versioning_state: dict[str, Any] | None = None
        self._versioning_checked_at = 0.0
        self._versioning_lock = threading.Lock()

    def is_configured(self) -> bool:
        """Check if S3 credentials/endpoint are configured."""
        return bool(self.bucket_name and (self.access_key_id or self.endpoint_url))

    def credentials_presentes(self) -> bool:
        """Les DEUX identifiants sont-ils fournis (access + secret) ?

        Distinct de :meth:`is_configured` : on peut avoir un endpoint et un
        bucket sans identifiants. C'est l'état que /health expose et que
        :meth:`_make_client` refuse — un client S3 sans identifiants enverrait
        des requêtes anonymes, dont l'échec ressemblerait à une panne du
        stockage au lieu d'un défaut de configuration.
        """
        return bool((self.access_key_id or "").strip() and (self.secret_access_key or "").strip())

    def _make_client(self, probe_timeout: float | None = None):
        """Build a boto3 client; ``probe_timeout`` shortens it for health checks.

        REFUS EXPLICITE sans identifiants : la composition de production exige
        une identité applicative DÉDIÉE (``SEAMTECH_S3_ACCESS_KEY`` /
        ``SEAMTECH_S3_SECRET_KEY``, créée par scripts/provisionner_stockage.sh).
        Il n'existe aucun repli sur les identifiants administrateur : un client
        anonyme échouerait plus tard, au milieu d'un import, avec un message qui
        accuserait le stockage.
        """
        import boto3
        from botocore.config import Config

        if self.endpoint_url and not self.credentials_presentes():
            raise StorageError(
                "Identifiants S3 applicatifs absents : renseigner SEAMTECH_S3_ACCESS_KEY et "
                "SEAMTECH_S3_SECRET_KEY (identité dédiée créée par "
                "scripts/provisionner_stockage.sh). Aucun repli sur les identifiants "
                "administrateur MinIO n'existe."
            )

        settings: dict[str, Any] = {
            "signature_version": "s3v4",
            "s3": {"addressing_style": "path" if self.force_path_style else "auto"},
            # Un PUT dont la réponse a été perdue peut déjà avoir abouti ;
            # un réessai automatique écraserait un écrivain concurrent. Le
            # client partagé ne réessaie donc aucune opération automatiquement.
            # Cela réduit aussi la résilience des lectures et ne résout pas la
            # course entre vérification de clé et première écriture.
            "retries": {"total_max_attempts": 1, "mode": "standard"},
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

    def _client_pour_endpoint(self, endpoint_url: str):
        """Client supplémentaire, lié à un endpoint donné (signature publique).

        Un client n'est créé qu'une fois par endpoint, et il n'est jamais
        utilisé pour des lectures d'objets : seulement pour signer une URL
        destinée à un navigateur.
        """
        client = self._clients_publics.get(endpoint_url)
        if client is not None:
            return client
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region_name,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if self.force_path_style else "auto"},
                retries={"max_attempts": 1, "mode": "standard"},
                connect_timeout=5,
                read_timeout=5,
            ),
        )
        self._clients_publics[endpoint_url] = client
        return client

    def _get_client(self):
        if self._s3 is not None:
            return self._s3

        try:
            self._s3 = self._make_client()
            return self._s3
        except StorageError:
            raise
        except Exception as exc:
            logger.error("Failed to initialize boto3 S3 client (%s)", type(exc).__name__)
            raise StorageError("S3 initialization failed") from None

    def _get_probe_client(self):
        """A short-timeout client, so a health probe cannot hang on a dead endpoint."""
        if self._probe_s3 is None:
            try:
                self._probe_s3 = self._make_client(VERSIONING_PROBE_TIMEOUT_SECONDS)
            except StorageError:
                raise
            except Exception as exc:
                logger.error("Failed to initialize S3 probe client (%s)", type(exc).__name__)
                raise StorageError("S3 initialization failed") from None
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
                    logger.info("S3 bucket does not exist; creating it")
                    create_kwargs: dict[str, Any] = {"Bucket": self.bucket_name}
                    if self.region_name and self.region_name not in ("us-east-1", "auto"):
                        create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region_name}
                    s3.create_bucket(**create_kwargs)
                    logger.info("S3 bucket created")
                else:
                    raise
        except Exception as exc:
            logger.warning("Could not verify or create S3 bucket (%s)", type(exc).__name__)

        # Always attempted once per client: versioning is what makes an overwrite
        # recoverable, and the outcome is reported by versioning_status().
        self._enable_versioning_once()

    def _enable_versioning_once(self) -> None:
        """Best-effort ``put_bucket_versioning``, précédé d'une LECTURE.

        A failure here must never fail an upload: Cloudflare R2 does not implement
        ``PutBucketVersioning`` at all (it is absent from its S3 compatibility
        matrix), and on such an endpoint the overwrite guarantee is carried by
        refusing to reuse an occupied key instead.

        La lecture d'abord n'est pas une optimisation, c'est une conséquence de
        la séparation des identités (revue du 2026-10-07) : le versioning est
        désormais activé par ``scripts/provisionner_stockage.sh``, et l'identité
        applicative RESTREINTE n'a plus le droit ``PutBucketVersioning``. Sans
        cette lecture, une application correctement configurée se croirait sans
        versioning (donc sans protection contre l'écrasement) alors que le
        bucket l'est déjà.
        """
        if self._versioning_attempted:
            return
        self._versioning_attempted = True
        etat = self._read_versioning()
        if etat.get("versioning_available") is True:
            self._versioning_state = etat
            logger.info("S3 object versioning already enabled")
            return
        try:
            s3 = self._get_client()
            s3.put_bucket_versioning(Bucket=self.bucket_name, VersioningConfiguration={"Status": "Enabled"})
            self._versioning_state = {
                "versioning_available": True,
                "versioning_detail": "bucket versioning is enabled",
            }
            logger.info("S3 object versioning enabled")
        except Exception as exc:
            self._versioning_state = _versioning_failure(exc)
            logger.warning("Object versioning unavailable (%s)", type(exc).__name__)
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
            logger.warning("Could not read S3 versioning state (%s)", type(exc).__name__)
            return _versioning_failure(exc)
        status = response.get("Status")
        if status == "Enabled":
            return {"versioning_available": True, "versioning_detail": "bucket versioning is enabled"}
        if status is None or status in ("", "Suspended"):
            return {"versioning_available": False, "versioning_detail": "bucket versioning is not enabled"}
        return {"versioning_available": None, "versioning_detail": "bucket versioning state is unrecognized"}

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
            raise StorageError("Could not check S3 object occupancy") from None
        except Exception:
            raise StorageError("Could not check S3 object occupancy") from None

    def first_free_key(self, base_key: str, max_candidates: int = 50) -> str:
        """Return ``base_key`` or the first ``-2``/``-3``/… variant that is unused.

        Occupied keys observed by HEAD are skipped. HEAD followed by upload is
        not atomic and cannot prevent a concurrent writer from using that key.
        """
        for index, candidate in enumerate([base_key, *(_suffixed_key(base_key, i) for i in range(2, max_candidates + 2))]):
            if not self.object_key_taken(candidate):
                if index:
                    logger.warning("S3 object key already in use; using an alternative")
                return candidate
        raise StorageError(f"Could not find a free object key after {max_candidates} attempts")

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
            logger.warning("Could not confirm S3 object (%s)", type(err).__name__)
            return False
        except Exception as exc:
            logger.warning("Could not confirm S3 object (%s)", type(exc).__name__)
            return False

    def upload_file(
        self,
        local_path: Path,
        remote_key: str | None = None,
        content_type: str | None = None,
        *,
        avoid_overwrite: bool = True,
        relative_path: str | None = None,
    ) -> str:
        """Upload a local file to S3/MinIO and return the key it was stored under.

        With ``avoid_overwrite`` (the default), occupied keys observed before
        upload are skipped. This check is not atomic with the write and does
        not protect against concurrent writers. Callers must use the returned key.
        """
        local = Path(local_path)
        try:
            if not local.exists() or not local.is_file():
                raise StoragePreflightError("Local file does not exist or is not a regular file")
            s3 = self._get_client()
            self.ensure_bucket_exists()
            key = self._apply_prefix(remote_key or local.name)
            if avoid_overwrite:
                key = self.first_free_key(key)
            mime = content_type or mimetypes.guess_type(local.name)[0] or "application/octet-stream"
            taille = local.stat().st_size
            empreinte = empreinte_sha256(local)
            metadonnees = {METADATA_SHA256: empreinte, METADATA_TAILLE: str(taille)}
            if relative_path:
                metadonnees[METADATA_CHEMIN] = urllib.parse.quote(relative_path, safe="/")
            extra_args = {"ContentType": mime, "Metadata": metadonnees}
        except StoragePreflightError:
            raise
        except Exception as exc:
            logger.error("S3 upload preparation failed (%s)", type(exc).__name__)
            raise StoragePreflightError("Upload preparation failed before object write") from None

        try:
            # Ne pas réémettre un PUT sans checksum après un échec : une réponse
            # perdue peut cacher une écriture réussie ou un écrivain concurrent.
            # Les fournisseurs sans checksum d'ingestion sont refusés.
            s3.upload_file(Filename=str(local), Bucket=self.bucket_name, Key=key,
                           ExtraArgs={**extra_args, "ChecksumAlgorithm": "SHA256"})
            logger.info("Uploaded S3 object")
            return key
        except Exception as exc:
            logger.error("Failed to upload S3 object (%s)", type(exc).__name__)
            raise StorageWriteUncertain(key) from None

    def upload_bytes(
        self,
        data: bytes,
        remote_key: str,
        content_type: str | None = None,
        *,
        avoid_overwrite: bool = True,
        relative_path: str | None = None,
    ) -> str:
        """Upload in-memory bytes directly to S3/MinIO without writing to local disk.

        Checks occupied keys as in :meth:`upload_file`; the check is not atomic
        against concurrent writers. Returns the key the bytes were stored under.
        """
        s3 = self._get_client()
        self.ensure_bucket_exists()

        key = self._apply_prefix(remote_key)
        if avoid_overwrite:
            key = self.first_free_key(key)

        mime = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        try:
            parametres: dict[str, Any] = {
                "Bucket": self.bucket_name,
                "Key": key,
                "Body": data,
                "ContentType": mime,
                "Metadata": {
                    METADATA_SHA256: hashlib.sha256(data).hexdigest(),
                    METADATA_TAILLE: str(len(data)),
                },
            }
            s3.put_object(**parametres, ChecksumAlgorithm="SHA256")
            return key
        except Exception as exc:
            logger.error("Failed to upload S3 bytes (%s)", type(exc).__name__)
            raise StorageError("Upload bytes failed; remote state may be ambiguous") from None

    def download_file(self, remote_key: str, destination_path: Path, *, version_id: str | None = None) -> Path:
        """Download an object, optionally pinning an S3 version, to a scratch destination."""
        s3 = self._get_client()
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            extra = {"ExtraArgs": {"VersionId": version_id}} if version_id is not None else {}
            s3.download_file(
                Bucket=self.bucket_name,
                Key=remote_key,
                Filename=str(destination_path),
                **extra,
            )
            return destination_path
        except Exception as exc:
            logger.error("Failed to download S3 object (%s)", type(exc).__name__)
            raise StorageError("Download failed") from None

    def get_presigned_url(
        self, remote_key: str, expiration_seconds: int = 3600, *, endpoint_url: str | None = None
    ) -> str:
        """URL présignée pour une lecture directe, limitée dans le temps.

        ``endpoint_url`` permet de SIGNER pour un autre endpoint que celui du
        client courant : c'est le cas de l'endpoint public déclaré pour les
        navigateurs de l'atelier. Par défaut, on signe pour l'endpoint interne,
        ce qui ne doit JAMAIS être remis à un navigateur (nom d'hôte non
        résolvable depuis un autre poste).
        """
        try:
            if endpoint_url and endpoint_url != self.endpoint_url:
                s3 = self._client_pour_endpoint(endpoint_url)
            else:
                s3 = self._get_client()
            return s3.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket_name, "Key": remote_key},
                ExpiresIn=expiration_seconds,
            )
        except Exception as exc:
            logger.error("Failed to generate presigned S3 URL (%s)", type(exc).__name__)
            raise StorageError("Presigned URL generation failed") from None

    def head_object(self, remote_key: str) -> dict[str, Any]:
        """Return object metadata for an authenticated API HEAD request."""
        s3 = self._get_client()
        try:
            return s3.head_object(Bucket=self.bucket_name, Key=remote_key)
        except Exception as exc:
            logger.warning("Could not read S3 object metadata (%s)", type(exc).__name__)
            raise StorageError("Object metadata lookup failed") from None

    def head_object_avec_checksum(self, remote_key: str) -> dict[str, Any]:
        """Demande explicitement les checksums calculés par le fournisseur."""
        try:
            return self._get_client().head_object(Bucket=self.bucket_name, Key=remote_key, ChecksumMode="ENABLED")
        except Exception as exc:
            logger.warning("Could not read S3 object checksum (%s)", type(exc).__name__)
            raise StorageError("Object checksum lookup failed") from None

    def get_object(self, remote_key: str, *, range_header: str | None = None) -> dict[str, Any]:
        """Ouvre un objet, éventuellement sur une plage d'octets.

        Utilisé à deux endroits : les requêtes par plage (l'API renvoie alors un
        vrai 206 à PDF.js) et le **proxy de téléchargement** — servir les octets
        par l'API authentifiée quand aucun endpoint public n'est déclaré, parce
        qu'une redirection présignée vers un endpoint interne casse le
        téléchargement depuis un autre poste de l'atelier.
        """
        params: dict[str, Any] = {"Bucket": self.bucket_name, "Key": remote_key}
        if range_header:
            params["Range"] = range_header
        try:
            return self._get_client().get_object(**params)
        except Exception as exc:
            logger.warning("Could not stream S3 object (%s)", type(exc).__name__)
            raise StorageError("Object stream lookup failed") from None

    def verifier_integrite(
        self,
        remote_key: str,
        chemin_local: Path | str,
        *,
        relire: bool = True,
    ) -> ResultatVerification:
        """Vérifie les OCTETS stockés, pas la déclaration que nous en avons faite.

        Ordre des preuves, du plus fort au plus faible :

        1. ``relecture_sha256`` (défaut) — l'objet est relu par l'API et
           l'empreinte est recalculée sur les octets reçus. C'est la seule
           méthode qui détecte un envoi tronqué ou altéré quel que soit le
           fournisseur. Coût : une lecture de l'objet.
        2. ``checksum_serveur`` — le fournisseur a calculé ``ChecksumSHA256``
           sur l'objet stocké ET déclare ``ChecksumType: FULL_OBJECT``. Utilisé
           quand la relecture est désactivée par configuration. La garde
           ``FULL_OBJECT`` est indispensable : sur un envoi multipart, le
           checksum renvoyé n'est PAS celui du fichier entier.
        3. ``metadata_seule`` — la métadonnée ``seamtech-sha256`` correspond.
           C'est déclaratif : elle a été fournie par nous à l'envoi. Compté
           comme NON prouvé, et ne permet PAS la suppression locale.
        4. ``taille_seule`` — taille identique, aucune empreinte : NON prouvé.

        Toute inégalité (taille, empreinte recalculée) est un ``echec`` : la
        copie locale n'est jamais supprimée.
        """
        local = Path(chemin_local)
        try:
            entete = self.head_object(remote_key) if relire else self.head_object_avec_checksum(remote_key)
        except StorageError as exc:
            return ResultatVerification(False, "echec", f"objet illisible: {exc}")

        taille_locale = local.stat().st_size
        taille_distante = entete.get("ContentLength")
        if taille_distante is not None and int(taille_distante) != taille_locale:
            return ResultatVerification(
                False, "echec", f"taille differente: local={taille_locale} distant={taille_distante}"
            )

        empreinte_locale = empreinte_sha256(local)

        if relire:
            try:
                corps = self.get_object(remote_key)["Body"]
                try:
                    digest = hashlib.sha256()
                    taille_lue = 0
                    while True:
                        bloc = corps.read(min(1024 * 1024, taille_locale - taille_lue + 1))
                        if not bloc:
                            break
                        taille_lue += len(bloc)
                        if taille_lue > taille_locale:
                            return ResultatVerification(False, "echec", "taille du corps supérieure à la taille locale")
                        digest.update(bloc)
                    if taille_lue != taille_locale:
                        return ResultatVerification(False, "echec", "taille du corps inférieure à la taille locale")
                    empreinte_distante = digest.hexdigest()
                finally:
                    corps.close()
            except Exception:
                return ResultatVerification(False, "echec", "relecture impossible")
            if empreinte_distante != empreinte_locale:
                return ResultatVerification(
                    False,
                    "echec",
                    f"relecture: sha256 different (local={empreinte_locale[:12]} distant={empreinte_distante[:12]})",
                )
            return ResultatVerification(
                True, "relecture_sha256", "octets relus et empreinte recalculée sur l'objet stocké", empreinte_distante
            )

        checksum = entete.get("ChecksumSHA256")
        type_checksum = str(entete.get("ChecksumType") or "")
        if checksum and type_checksum.upper() == "FULL_OBJECT":
            attendu = base64.b64encode(bytes.fromhex(empreinte_locale)).decode("ascii")
            if str(checksum) != attendu:
                return ResultatVerification(
                    False, "echec", f"checksum serveur different: {str(checksum)[:16]}…"
                )
            return ResultatVerification(
                True, "checksum_serveur", "checksum du fournisseur sur l'objet entier (FULL_OBJECT)", empreinte_locale
            )

        metadonnees = {str(cle).lower(): str(valeur) for cle, valeur in (entete.get("Metadata") or {}).items()}
        declaree = metadonnees.get(METADATA_SHA256)
        if declaree and declaree == empreinte_locale:
            return ResultatVerification(
                False,
                "metadata_seule",
                "metadonnee fournie par l'application identique — NON prouve les octets stockes",
            )
        if not declaree:
            return ResultatVerification(
                False, "taille_seule", "aucune empreinte disponible — seule la taille concorde"
            )
        return ResultatVerification(
            False, "echec", f"metadonnee divergente: local={empreinte_locale[:12]} declaré={declaree[:12]}"
        )

    def delete_file(self, remote_key: str) -> bool:
        """Request deletion; False means an unknown outcome, not preserved data."""
        s3 = self._get_client()
        try:
            s3.delete_object(Bucket=self.bucket_name, Key=remote_key)
            return True
        except Exception as exc:
            logger.warning("Failed to delete S3 object (%s)", type(exc).__name__)
            return False

    def list_keys(self, prefix: str) -> list[str]:
        """Liste les clés du bucket portant ce préfixe (rétention des
        sauvegardes, Lot H.1). Cohérent avec les autres méthodes : le préfixe
        applicatif ``s3_prefix`` est appliqué avant la recherche."""
        s3 = self._get_client()
        prefixe_complet = self._apply_prefix(prefix)
        try:
            cles: list[str] = []
            paginateur = s3.get_paginator("list_objects_v2")
            for page in paginateur.paginate(Bucket=self.bucket_name, Prefix=prefixe_complet):
                for objet in page.get("Contents", []):
                    cles.append(objet["Key"])
            return sorted(cles)
        except Exception as exc:
            logger.error("Failed to list S3 keys (%s)", type(exc).__name__)
            raise StorageError("List failed") from None


def upload_artifacts_to_storage(
    folder_name: str,
    files_to_upload: list[Path | None],
    config: AppConfig,
    import_id: str | None = None,
    source_root: Path | str | None = None,
    logical_paths: dict[str, Path] | None = None,
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
    #: Relecture des objets après envoi. Désactiver la relecture ne rend pas la
    #: vérification « plus rapide mais suffisante » : cela la rend IMPOSSIBLE
    #: (aucune preuve sur les octets), donc la purge locale n'a plus lieu.
    relire_objets = bool(getattr(config, "storage_verify_reread", True))

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
            logical_path = (logical_paths or {}).get(str(file_path), file_path)
            base_key = artifact_object_key(
                namespace,
                logical_path,
                source_root=source_root,
                prefix=s3_client.prefix,
                lexical=logical_paths is not None,
            )
            artifact = UploadedArtifact(
                path=str(logical_path),
                name=logical_path.name,
                key=base_key,
                bucket=s3_client.bucket_name,
            )
            envoi_termine = False
            try:
                # The client refuses to reuse an occupied key and tells us which
                # key it really used; verifying that exact key is what authorises
                # deleting the local copy.
                key = s3_client.upload_file(
                    file_path,
                    remote_key=base_key,
                    relative_path=chemin_relatif_origine(logical_path, source_root, lexical=logical_paths is not None),
                )
                artifact.key = key
                envoi_termine = True
                artifact.status = "uploaded"
                # Vérification des OCTETS stockés (relecture + empreinte
                # recalculée, ou checksum du fournisseur sur objet entier).
                # C'est cette réponse — et elle seule — qui autorise la purge
                # locale. Une comparaison de la métadonnée que NOUS avons
                # fournie ne suffit pas : elle resterait intacte sur un envoi
                # tronqué.
                resultat = s3_client.verifier_integrite(key, file_path, relire=relire_objets)
                artifact.verified = resultat.integrite_prouvee
                artifact.verification = resultat.methode
                artifact.verification_detail = resultat.detail
                artifact.sha256 = resultat.sha256 if resultat.integrite_prouvee else None
                if not resultat.integrite_prouvee and resultat.methode == "echec":
                    artifact.status = "failed"
                    artifact.error = f"objet {key} non conforme après envoi ({resultat.detail})"
                elif not resultat.integrite_prouvee:
                    # Objet présent, taille correcte, mais intégrité NON
                    # prouvée (relecture désactivée) : on ne supprime pas la
                    # copie locale et on le dit.
                    artifact.error = (
                        f"intégrité non prouvée ({resultat.methode}) — copie locale conservée"
                    )
            except StoragePreflightError as exc:
                # Aucune écriture d'objet n'a été tentée : la clé de base est
                # seulement une prévision et ne doit pas devenir ambiguë.
                artifact.status = "failed"
                if not envoi_termine:
                    artifact.key = None
                artifact.error = f"Échec de la préservation ({type(exc).__name__})"
            except StorageWriteUncertain as exc:
                # La clé sélectionnée peut être suffixée ; la conserver même
                # lorsque la réponse de l'écriture est perdue.
                artifact.status = "failed"
                if not envoi_termine:
                    artifact.key = exc.key
                artifact.error = f"Échec de la préservation ({type(exc).__name__})"
            except Exception as exc:
                artifact.status = "failed"
                artifact.error = f"Échec de la préservation ({type(exc).__name__})"
            artifacts.append(artifact)

        if not artifacts:
            return UploadBatch(status="not_applicable")

        if all(a.status == "uploaded" and a.verified for a in artifacts):
            status = "uploaded"
        elif any(a.status == "uploaded" for a in artifacts):
            status = "partial"
        else:
            status = "failed"

        return UploadBatch(status=status, artifacts=artifacts)

    return UploadBatch(status="not_configured")
