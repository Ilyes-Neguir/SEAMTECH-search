import json
from pathlib import Path

import pytest

from seamtech_search.cli import run_index
from seamtech_search.crawler import ScanIncompleteError
from seamtech_search.indexer import ScanAlreadyRunningError, SearchIndex
from seamtech_search.models import Document


def test_incomplete_scan_preserves_existing_index(tmp_path: Path) -> None:
    database = tmp_path / "search.db"
    index = SearchIndex(database)
    index.initialize(rebuild=True)
    path = tmp_path / "existing.txt"
    document = Document(
        path=path,
        name=path.name,
        parent_path=tmp_path,
        extension=".txt",
        size=1,
        modified_at=1.0,
        is_dir=False,
        text="important factory reference",
    )
    index.upsert_document(document)

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({
            "root_paths": [str(tmp_path / "unavailable-share")],
            "database_path": str(database),
        }),
        encoding="utf-8",
    )

    with pytest.raises(ScanIncompleteError):
        run_index(str(config_path))

    assert index.search("important")
    latest = index.latest_scan()
    assert latest is not None
    assert latest["status"] == "failed"


def test_successful_scan_records_completion(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "reference.txt").write_text("factory reference", encoding="utf-8")
    database = tmp_path / "search.db"
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"root_paths": [str(root)], "database_path": str(database)}),
        encoding="utf-8",
    )

    run_index(str(config_path))
    latest = SearchIndex(database).latest_scan()
    assert latest is not None
    assert latest["status"] == "completed"
    assert latest["scanned"] >= 2


def test_concurrent_scan_is_rejected(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search.db")
    with index.scan_lock():
        with pytest.raises(ScanAlreadyRunningError):
            with index.scan_lock():
                pass
