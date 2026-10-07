from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


def default_config_path(project_root: str | Path | None = None) -> Path:
    root = Path(project_root).resolve() if project_root is not None else Path(__file__).resolve().parents[1]
    config_dir = root / "config"
    config_file = config_dir / "config.json"
    # Fail loudly if config.json is missing — do not silently fall back to example (booby trap in prod)
    if not config_file.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_file}. "
            f"Copy {config_dir / 'config.example.json'} to {config_file} and edit it, "
            f"or set SEAMTECH_ROOT_PATHS and other env vars."
        )
    return config_file


class AppConfig(BaseModel):
    root_paths: list[Path] = Field(min_length=1)
    database_path: Path = Path("data/search.db")
    database_url: str | None = None
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    excluded_names: set[str] = Field(default_factory=set)
    excluded_extensions: set[str] = Field(default_factory=set)
    max_extract_chars: int = Field(default=200_000, ge=1_000, le=2_000_000)
    max_file_size_bytes: int = Field(default=512 * 1024 * 1024, ge=1)
    extraction_timeout_seconds: int = Field(default=60, ge=1, le=3_600)
    enable_legacy_office: bool = False
    libreoffice_command: str = "soffice"
    enable_ocr: bool = False
    tesseract_command: str = "tesseract"
    ocrmypdf_command: str = "ocrmypdf"
    external_extraction_timeout_seconds: int = Field(default=120, ge=1, le=3_600)
    external_extractors: dict[str, list[str]] = Field(default_factory=dict)
    allow_network_access: bool = False
    auth_token: str | None = None
    behind_tls_proxy: bool = False
    rate_limit_per_minute: int = Field(default=600, ge=1)

    # Retention settings
    reports_retention_days: int = Field(default=90, ge=1)
    staged_retention_days: int = Field(default=7, ge=1)
    audit_retention_days: int = Field(default=365, ge=1)
    min_free_bytes: int = Field(default=1024 * 1024 * 1024, ge=0)

    # Storage backend selection: "s3" (MinIO / Cloudflare R2 / AWS S3) or "local"
    storage_backend: str = Field(default="s3")

    # S3 / MinIO / Cloudflare R2 settings
    s3_endpoint_url: str | None = None
    s3_bucket: str = Field(default="seamtech-documents")
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = Field(default="us-east-1")
    s3_force_path_style: bool = True
    s3_prefix: str = ""
    delete_local_after_upload: bool = False

    # Redis settings
    redis_url: str | None = None
    # Exiger une acceptation DURABLE : quand vrai, une API sans Redis joignable
    # refuse le job (503) au lieu de le garder en mémoire. Défaut False pour ne
    # pas casser les postes de développement ; la composition de production le
    # passe à true, et un repli mémoire est alors toujours ANNONCÉ dans la
    # réponse 202 (`durability: "process_memory"`).
    #: Relecture de chaque objet après envoi (empreinte recalculée sur les
    #: octets stockés). C'est la SEULE preuve d'intégrité côté fournisseur :
    #: une métadonnée envoyée par l'application ne prouve rien. La désactiver
    #: n'accélère pas « sans risque » — elle interdit la purge locale, puisque
    #: plus rien ne prouve que l'objet stocké est le bon.
    storage_verify_reread: bool = True
    require_durable_queue: bool = False
    #: Endpoint S3 **joignable par les navigateurs des postes de l'atelier**
    #: (par exemple le nom public du service MinIO de l'atelier). Vide par défaut : dans ce
    #: cas les téléchargements sont servis par l'API (proxy authentifié), parce
    #: qu'une redirection présignée vers l'endpoint interne du réseau des
    #: conteneurs n'est résolvable depuis aucun poste. Ne renseigner cette
    #: variable que si l'endpoint est réellement exposé sur le réseau de
    #: l'atelier ET en HTTPS si les navigateurs y accèdent.
    s3_public_endpoint_url: str | None = None
    # Durée de vie du verrou (claim) d'une tâche : au-delà, un worker muet est
    # considéré mort et sa tâche est reprise. À majorer sur un serveur lent.
    task_claim_ttl_seconds: int = Field(default=300, ge=15)
    # Nombre de tentatives avant lettre morte (le job reste visible, jamais perdu).
    max_task_attempts: int = Field(default=3, ge=1, le=20)
    # Intervalle de balayage des tâches orphelines dans la boucle du worker.
    queue_reclaim_interval_seconds: float = Field(default=30.0, ge=1.0)
    # Le processus web démarre-t-il un worker en fil d'arrière-plan ? Vrai par
    # défaut (compatibilité mono-conteneur) ; la composition de production le
    # met à false et fait tourner un service `worker` séparé, pour que
    # l'exécution des jobs ne dépende pas du cycle de vie du serveur web.
    web_worker_enabled: bool = True

    # Database connection pool settings
    pool_min: int = Field(default=1, ge=1)
    pool_max: int = Field(default=10, ge=1)
    pool_timeout: float = Field(default=30.0, ge=0.1)
    statement_timeout_ms: int = Field(default=5000, ge=100)

    @field_validator("root_paths")
    @classmethod
    def reject_duplicate_roots(cls, value: list[Path]) -> list[Path]:
        if len({str(path).lower() for path in value}) != len(value):
            raise ValueError("root_paths must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_network_policy(self) -> "AppConfig":
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        if self.host not in local_hosts and not self.allow_network_access:
            raise ValueError("non-local host requires allow_network_access=true")
        if self.allow_network_access and not self.auth_token:
            raise ValueError("auth_token is required when allow_network_access is enabled")
        if self.host not in local_hosts and self.auth_token and not self.behind_tls_proxy:
            raise ValueError("token authentication over non-local host requires behind_tls_proxy=true")
        return self

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppConfig":
        if path is None:
            config_path = default_config_path()
        else:
            config_path = Path(path).expanduser()
            if not config_path.is_absolute():
                config_path = (Path.cwd() / config_path).resolve()

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with config_path.open("r", encoding="utf-8") as file:
            data: dict[str, Any] = json.load(file)

        # Environment variable overrides
        if os.environ.get("SEAMTECH_DATABASE_URL"):
            data["database_url"] = os.environ["SEAMTECH_DATABASE_URL"]
        if os.environ.get("SEAMTECH_AUTH_TOKEN"):
            data["auth_token"] = os.environ["SEAMTECH_AUTH_TOKEN"]
        if os.environ.get("SEAMTECH_ROOT_PATHS"):
            raw = os.environ["SEAMTECH_ROOT_PATHS"].strip()
            # Support colon or comma separated
            sep = "," if "," in raw else ":" if ":" in raw else None
            parts = [p.strip() for p in raw.split(sep)] if sep else [raw] if raw else []
            data["root_paths"] = [p for p in parts if p]

        if os.environ.get("SEAMTECH_HOST"):
            data["host"] = os.environ["SEAMTECH_HOST"]
        if os.environ.get("SEAMTECH_PORT"):
            data["port"] = int(os.environ["SEAMTECH_PORT"])
        if os.environ.get("SEAMTECH_ALLOW_NETWORK_ACCESS"):
            data["allow_network_access"] = os.environ["SEAMTECH_ALLOW_NETWORK_ACCESS"].strip().lower() in {
                "1",
                "true",
                "yes",
            }
        if os.environ.get("SEAMTECH_BEHIND_TLS_PROXY"):
            data["behind_tls_proxy"] = os.environ["SEAMTECH_BEHIND_TLS_PROXY"].strip().lower() in {
                "1",
                "true",
                "yes",
            }
        if os.environ.get("SEAMTECH_RATE_LIMIT_PER_MINUTE"):
            data["rate_limit_per_minute"] = int(os.environ["SEAMTECH_RATE_LIMIT_PER_MINUTE"])
        if os.environ.get("SEAMTECH_STORAGE_BACKEND"):
            data["storage_backend"] = os.environ["SEAMTECH_STORAGE_BACKEND"]
        if os.environ.get("SEAMTECH_S3_ENDPOINT_URL"):
            data["s3_endpoint_url"] = os.environ["SEAMTECH_S3_ENDPOINT_URL"]
        if os.environ.get("SEAMTECH_S3_BUCKET"):
            data["s3_bucket"] = os.environ["SEAMTECH_S3_BUCKET"]
        if os.environ.get("SEAMTECH_S3_PUBLIC_ENDPOINT_URL"):
            data["s3_public_endpoint_url"] = os.environ["SEAMTECH_S3_PUBLIC_ENDPOINT_URL"]
        # NOTE : la variable lue est bien SEAMTECH_STORAGE_VERIFY_REREAD. Une
        # version antérieure testait SEAMTECH_REQUIRE_DURABLE_QUEUE puis
        # indexait SEAMTECH_STORAGE_VERIFY_REREAD : dès que la durabilité était
        # exigée sans que la variable de relecture soit posée, AppConfig.load
        # levait KeyError — le conteneur `web` documenté ne démarrait plus.
        if os.environ.get("SEAMTECH_STORAGE_VERIFY_REREAD") is not None:
            data["storage_verify_reread"] = os.environ["SEAMTECH_STORAGE_VERIFY_REREAD"].strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        if os.environ.get("SEAMTECH_REQUIRE_DURABLE_QUEUE"):
            data["require_durable_queue"] = os.environ["SEAMTECH_REQUIRE_DURABLE_QUEUE"].strip().lower() in {
                "1",
                "true",
                "yes",
            }
        if os.environ.get("SEAMTECH_TASK_CLAIM_TTL_SECONDS"):
            data["task_claim_ttl_seconds"] = int(os.environ["SEAMTECH_TASK_CLAIM_TTL_SECONDS"])
        if os.environ.get("SEAMTECH_MAX_TASK_ATTEMPTS"):
            data["max_task_attempts"] = int(os.environ["SEAMTECH_MAX_TASK_ATTEMPTS"])
        if os.environ.get("SEAMTECH_WEB_WORKER_ENABLED"):
            data["web_worker_enabled"] = os.environ["SEAMTECH_WEB_WORKER_ENABLED"].strip().lower() in {
                "1",
                "true",
                "yes",
            }
        if os.environ.get("SEAMTECH_S3_ACCESS_KEY"):
            data["s3_access_key"] = os.environ["SEAMTECH_S3_ACCESS_KEY"]
        if os.environ.get("SEAMTECH_S3_SECRET_KEY"):
            data["s3_secret_key"] = os.environ["SEAMTECH_S3_SECRET_KEY"]
        if os.environ.get("SEAMTECH_S3_REGION"):
            data["s3_region"] = os.environ["SEAMTECH_S3_REGION"]
        if os.environ.get("SEAMTECH_S3_FORCE_PATH_STYLE"):
            data["s3_force_path_style"] = os.environ["SEAMTECH_S3_FORCE_PATH_STYLE"].strip().lower() in {
                "1",
                "true",
                "yes",
            }
        if os.environ.get("SEAMTECH_S3_PREFIX"):
            data["s3_prefix"] = os.environ["SEAMTECH_S3_PREFIX"]
        if os.environ.get("SEAMTECH_REDIS_URL"):
            data["redis_url"] = os.environ["SEAMTECH_REDIS_URL"]
        if os.environ.get("SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD"):
            data["delete_local_after_upload"] = os.environ["SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD"].strip().lower() in {
                "1",
                "true",
                "yes",
            }
        if os.environ.get("SEAMTECH_REPORTS_RETENTION_DAYS"):
            data["reports_retention_days"] = int(os.environ["SEAMTECH_REPORTS_RETENTION_DAYS"])
        if os.environ.get("SEAMTECH_STAGED_RETENTION_DAYS"):
            data["staged_retention_days"] = int(os.environ["SEAMTECH_STAGED_RETENTION_DAYS"])
        if os.environ.get("SEAMTECH_AUDIT_RETENTION_DAYS"):
            data["audit_retention_days"] = int(os.environ["SEAMTECH_AUDIT_RETENTION_DAYS"])
        if os.environ.get("SEAMTECH_MIN_FREE_BYTES"):
            data["min_free_bytes"] = int(os.environ["SEAMTECH_MIN_FREE_BYTES"])
        if os.environ.get("SEAMTECH_POOL_MIN"):
            data["pool_min"] = int(os.environ["SEAMTECH_POOL_MIN"])
        if os.environ.get("SEAMTECH_POOL_MAX"):
            data["pool_max"] = int(os.environ["SEAMTECH_POOL_MAX"])
        if os.environ.get("SEAMTECH_POOL_TIMEOUT"):
            data["pool_timeout"] = float(os.environ["SEAMTECH_POOL_TIMEOUT"])
        if os.environ.get("SEAMTECH_STATEMENT_TIMEOUT_MS"):
            data["statement_timeout_ms"] = int(os.environ["SEAMTECH_STATEMENT_TIMEOUT_MS"])

        config = cls.model_validate(data)

        base_path = config_path.parent.parent if config_path.parent.name == "config" else config_path.parent
        config.root_paths = [
            root_path if root_path.is_absolute() else base_path / root_path for root_path in config.root_paths
        ]
        if not config.database_path.is_absolute():
            config.database_path = base_path / config.database_path
        config.excluded_extensions = {
            extension.lower() if extension.startswith(".") else f".{extension.lower()}"
            for extension in config.excluded_extensions
        }
        config.external_extractors = {
            extension.lower() if extension.startswith(".") else f".{extension.lower()}": command
            for extension, command in config.external_extractors.items()
        }
        return config
