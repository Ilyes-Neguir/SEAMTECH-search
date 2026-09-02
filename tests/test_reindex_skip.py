import json
import time
from pathlib import Path

import pytest

import seamtech_search.crawler as crawler_module
from seamtech_search.cli import run_index
from seamtech_search.config import AppConfig
from seamtech_search.crawler import _extract_with_timeout, crawl
from seamtech_search.extractors import CURRENT_EXTRACTOR_VERSION
from seamtech_search.indexer import SearchIndex


def _write_config(tmp_path: Path, root: Path, database: Path, **extra) -> Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"root_paths": [str(root)], "database_path": str(database), **extra}),
        encoding="utf-8",
    )
    return config_path


def test_unchanged_file_skips_extraction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "reference.txt").write_text("factory reference ABC", encoding="utf-8")

    calls: list[Path] = []
    real_extract = crawler_module.extract_text

    def counting_extract(path: Path, *args, **kwargs):
        calls.append(path)
        return real_extract(path, *args, **kwargs)

    monkeypatch.setattr(crawler_module, "extract_text", counting_extract)

    config = AppConfig(root_paths=[root], database_path=tmp_path / "search.db")
    # First pass: nothing indexed yet, must extract.
    list(crawl(config))
    assert len(calls) == 1

    # Second pass with metadata reflecting what's now stored: same size/mtime/version.
    stat = (root / "reference.txt").stat()
    existing = {
        str((root / "reference.txt").resolve()).lower(): (
            stat.st_size,
            stat.st_mtime,
            CURRENT_EXTRACTOR_VERSION,
        ),
    }
    calls.clear()
    documents = list(crawl(config, existing))
    assert len(calls) == 0
    file_doc = next(d for d in documents if not d.is_dir)
    assert file_doc.text == ""
    assert file_doc.content_hash == ""


def test_extractor_version_bump_forces_reextraction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "reference.txt"
    target.write_text("factory reference ABC", encoding="utf-8")

    calls: list[Path] = []
    real_extract = crawler_module.extract_text

    def counting_extract(path: Path, *args, **kwargs):
        calls.append(path)
        return real_extract(path, *args, **kwargs)

    monkeypatch.setattr(crawler_module, "extract_text", counting_extract)
    monkeypatch.setattr(crawler_module, "CURRENT_EXTRACTOR_VERSION", 2)

    config = AppConfig(root_paths=[root], database_path=tmp_path / "search.db")
    stat = target.stat()
    # Existing metadata says extractor_version=1 (stale), same size/mtime.
    existing = {str(target.resolve()).lower(): (stat.st_size, stat.st_mtime, 1)}

    list(crawl(config, existing))
    assert len(calls) == 1, "a version bump must force re-extraction even with unchanged size/mtime"


def test_full_reindex_only_extracts_changed_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("alpha reference", encoding="utf-8")
    (root / "b.txt").write_text("bravo reference", encoding="utf-8")
    database = tmp_path / "search.db"
    config_path = _write_config(tmp_path, root, database)

    run_index(str(config_path))

    calls: list[Path] = []
    real_extract = crawler_module.extract_text

    def counting_extract(path: Path, *args, **kwargs):
        calls.append(path)
        return real_extract(path, *args, **kwargs)

    monkeypatch.setattr(crawler_module, "extract_text", counting_extract)

    # Modify only b.txt before the second run.
    time.sleep(0.01)
    (root / "b.txt").write_text("bravo reference updated", encoding="utf-8")

    run_index(str(config_path))

    extracted_names = {path.name for path in calls}
    assert extracted_names == {"b.txt"}

    index = SearchIndex(database)
    results = index.search("updated")
    assert len(results) == 1
    assert results[0]["name"] == "b.txt"


def test_extraction_timeout_returns_marker_without_hanging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "slow.txt"
    path.write_text("content", encoding="utf-8")

    def slow_extract(*args, **kwargs):
        time.sleep(5)
        return "should not get here"

    monkeypatch.setattr(crawler_module, "extract_text", slow_extract)
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "search.db", extraction_timeout_seconds=1)

    started = time.perf_counter()
    result = _extract_with_timeout(path, config)
    elapsed = time.perf_counter() - started

    assert result == "[extraction timed out]"
    assert elapsed < 4, "must return promptly instead of waiting for the slow call"
