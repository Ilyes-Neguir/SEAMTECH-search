from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    root_paths: list[Path]
    database_path: Path = Path("data/search.db")
    host: str = "127.0.0.1"
    port: int = 8000
    excluded_names: set[str] = Field(default_factory=set)
    excluded_extensions: set[str] = Field(default_factory=set)
    max_extract_chars: int = 200_000

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        config_path = Path(path)
        with config_path.open("r", encoding="utf-8") as file:
            data: dict[str, Any] = json.load(file)

        config = cls.model_validate(data)
        if not config.database_path.is_absolute():
            config.database_path = config_path.parent / config.database_path
        return config

