from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Document:
    path: Path
    name: str
    parent_path: Path
    extension: str
    size: int
    modified_at: float
    is_dir: bool
    text: str = ""

    @property
    def path_key(self) -> str:
        return os.path.normcase(str(self.path.resolve()))

    @property
    def searchable_text(self) -> str:
        parts = [
            self.name,
            str(self.path),
            self.extension,
            self.text,
        ]
        return "\n".join(part for part in parts if part)

