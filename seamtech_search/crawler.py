from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from .config import AppConfig
from .extractors import extract_text
from .models import Document


def crawl(config: AppConfig) -> Iterator[Document]:
    for root in config.root_paths:
        root_path = Path(root)
        if not root_path.exists():
            print(f"Warning: root path does not exist: {root_path}")
            continue

        for current_root, dir_names, file_names in os.walk(root_path):
            current = Path(current_root)
            dir_names[:] = [
                name for name in dir_names if not _is_excluded(name, Path(name), config)
            ]

            yield _document_from_path(current, config, is_dir=True)

            for file_name in file_names:
                file_path = current / file_name
                if _is_excluded(file_name, file_path, config):
                    continue
                yield _document_from_path(file_path, config, is_dir=False)


def _is_excluded(name: str, path: Path, config: AppConfig) -> bool:
    if name.startswith("~$"):
        return True
    if name in config.excluded_names:
        return True
    return path.suffix.lower() in config.excluded_extensions


def _document_from_path(path: Path, config: AppConfig, is_dir: bool) -> Document:
    try:
        stat = path.stat()
        size = 0 if is_dir else stat.st_size
        modified_at = stat.st_mtime
    except OSError:
        size = 0
        modified_at = 0

    text = "" if is_dir else extract_text(path, config.max_extract_chars)
    return Document(
        path=path,
        name=path.name,
        parent_path=path.parent,
        extension="" if is_dir else path.suffix.lower(),
        size=size,
        modified_at=modified_at,
        is_dir=is_dir,
        text=text,
    )

