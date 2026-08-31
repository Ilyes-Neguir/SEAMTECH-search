from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from .extractors import CURRENT_EXTRACTOR_VERSION


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
    # Version of the extraction logic that produced `text`. Lets the indexer
    # tell "unchanged file, old extractor" apart from "unchanged file,
    # current extractor" so a parser fix can force a re-extract even when
    # size/modified_at haven't moved.
    extractor_version: int = CURRENT_EXTRACTOR_VERSION
    # Hash of the extracted text (not the raw file). Computed only when
    # extraction actually runs, so it costs nothing on skipped files.
    content_hash: str = ""

    @staticmethod
    def hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

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

