"""Tests for collision-free object keys (Phase 1.2 & 1.3).

Every artifact must be stored under ``{prefix}/{import_id}/{sha256(relpath)}/{filename}``
so two imports of the same folder — or two same-named files in different
sub-folders — can never share a key.
"""

from pathlib import Path

from seamtech_search.storage import artifact_object_key


def test_artifact_object_key_namespaced_by_import_id(tmp_path: Path) -> None:
    source_root = tmp_path / "order"
    source_root.mkdir()
    file_a = source_root / "plan.pdf"
    file_a.write_bytes(b"a")

    key1 = artifact_object_key("import-1", file_a, source_root=source_root)
    key2 = artifact_object_key("import-2", file_a, source_root=source_root)

    assert key1 != key2
    assert key1.startswith("import-1/")
    assert key2.startswith("import-2/")
    assert key1.endswith("/plan.pdf")
    assert key2.endswith("/plan.pdf")


def test_artifact_object_key_hashes_relative_path(tmp_path: Path) -> None:
    source_root = tmp_path / "order"
    (source_root / "sub1").mkdir(parents=True)
    (source_root / "sub2").mkdir(parents=True)
    file1 = source_root / "sub1" / "notes.txt"
    file2 = source_root / "sub2" / "notes.txt"
    file1.write_text("a")
    file2.write_text("b")

    key1 = artifact_object_key("REF-001", file1, source_root=source_root)
    key2 = artifact_object_key("REF-001", file2, source_root=source_root)

    assert key1 != key2, "same-named files in different sub-folders must have different keys"
    assert key1.endswith("/notes.txt")
    assert key2.endswith("/notes.txt")
    assert key1.startswith("REF-001/")
    assert key2.startswith("REF-001/")


def test_artifact_object_key_with_prefix(tmp_path: Path) -> None:
    file_path = tmp_path / "doc.pdf"
    file_path.write_bytes(b"test")

    key = artifact_object_key("import-xyz", file_path, source_root=tmp_path, prefix="seamtech")

    assert key.startswith("seamtech/import-xyz/")
    assert key.endswith("/doc.pdf")


def test_artifact_object_key_outside_root_uses_absolute(tmp_path: Path) -> None:
    source_root = tmp_path / "order"
    source_root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")

    key_inside = artifact_object_key("import-1", source_root / "inside.pdf", source_root=source_root)
    key_outside = artifact_object_key("import-1", outside, source_root=source_root)

    # Both are namespaced by import id, but hashes differ because one is relative, one absolute
    assert key_inside.startswith("import-1/")
    assert key_outside.startswith("import-1/")
    assert key_inside != key_outside
