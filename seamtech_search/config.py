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
    if config_file.exists():
        return config_file
    if (config_dir / "config.example.json").exists():
        return config_dir / "config.example.json"
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
    allow_network_access: bool = False
    auth_token: str | None = None

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
        return self

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppConfig":
        if path is None:
            config_path = default_config_path()
        else:
            config_path = Path(path).expanduser()
            if not config_path.is_absolute():
                config_path = (Path.cwd() / config_path).resolve()
        with config_path.open("r", encoding="utf-8") as file:
            data: dict[str, Any] = json.load(file)

        if os.environ.get("SEAMTECH_DATABASE_URL"):
            data["database_url"] = os.environ["SEAMTECH_DATABASE_URL"]
        if os.environ.get("SEAMTECH_AUTH_TOKEN"):
            data["auth_token"] = os.environ["SEAMTECH_AUTH_TOKEN"]
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

        config = cls.model_validate(data)

        base_path = config_path.parent.parent if config_path.parent.name == "config" else config_path.parent
        config.root_paths = [
            root_path if root_path.is_absolute() else base_path / root_path
            for root_path in config.root_paths
        ]
        if not config.database_path.is_absolute():
            config.database_path = base_path / config.database_path
        config.excluded_extensions = {
            extension.lower() if extension.startswith(".") else f".{extension.lower()}"
            for extension in config.excluded_extensions
        }
        return config
