from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


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
    root_paths: list[Path]
    database_path: Path = Path("data/search.db")
    database_url: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    excluded_names: set[str] = Field(default_factory=set)
    excluded_extensions: set[str] = Field(default_factory=set)
    max_extract_chars: int = 200_000

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

        config = cls.model_validate(data)

        # Determine the base path for resolving relative paths
        # If config is in a 'config' subdirectory, resolve relative to project root
        # Otherwise, resolve relative to the config file's directory
        if config_path.parent.name == "config":
            base_path = config_path.parent.parent  # project root
        else:
            base_path = config_path.parent  # config directory

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
