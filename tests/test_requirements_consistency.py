"""One version per package, repo-wide (audit: "Conflicting requirements pins").

The repository declared its Python dependencies in four places —
requirements.txt, requirements-dev.txt, requirements-local.txt and
pyproject.toml — and they disagreed. pytest was pinned at 8.4.1 in
requirements-dev.txt and 9.1.1 in both requirements-local.txt and pyproject,
so which pytest you got depended on which file you happened to install, and CI
(which installed requirements.txt + requirements-local.txt) silently tested a
different version than the one `pip install -e .[dev]` gave a contributor.

These tests parse all four sources and fail on any drift, so the conflict
cannot come back.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_FILES = ("requirements.txt", "requirements-dev.txt", "requirements-local.txt")

# Requirements lines are `name[op]version`; `-r`/`-e`/`--` lines are includes or
# flags and carry no pin of their own.
REQUIREMENT_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*(==|>=|<=|~=|!=|>|<)\s*([^\s;#]+)")


def _normalize(name: str) -> str:
    """PEP 503 name normalisation, so `PyYAML` and `pyyaml` are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirements(path: Path) -> dict[str, tuple[str, str]]:
    """Return {normalised name: (operator, version)} for direct pins only."""
    pins: dict[str, tuple[str, str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = REQUIREMENT_LINE.match(line)
        assert match, f"{path.name}: unparsable requirement line {raw!r}"
        name, operator, version = match.groups()
        # Strip extras, e.g. uvicorn[standard] -> uvicorn.
        name = name.split("[", 1)[0]
        pins[_normalize(name)] = (operator, version)
    return pins


def _pyproject() -> dict:
    if sys.version_info < (3, 11):
        pytest.skip("tomllib is stdlib from Python 3.11")
    import tomllib

    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _pyproject_pins(entries: list[str]) -> dict[str, tuple[str, str]]:
    pins: dict[str, tuple[str, str]] = {}
    for entry in entries:
        match = REQUIREMENT_LINE.match(entry.strip())
        assert match, f"pyproject: unparsable dependency {entry!r}"
        name, operator, version = match.groups()
        pins[_normalize(name.split("[", 1)[0])] = (operator, version)
    return pins


def test_every_requirements_file_exists_and_parses() -> None:
    for name in REQUIREMENTS_FILES:
        path = REPO_ROOT / name
        assert path.is_file(), f"{name} is missing"
        _parse_requirements(path)


def test_no_package_is_pinned_at_two_different_versions() -> None:
    """The core regression guard: one version per package, everywhere."""
    seen: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    for name in REQUIREMENTS_FILES:
        for package, spec in _parse_requirements(REPO_ROOT / name).items():
            seen[package][" ".join(spec)].append(name)

    project = _pyproject()["project"]
    for package, spec in _pyproject_pins(project.get("dependencies", [])).items():
        seen[package][" ".join(spec)].append("pyproject.toml[dependencies]")
    for extra, entries in (project.get("optional-dependencies") or {}).items():
        for package, spec in _pyproject_pins(entries).items():
            seen[package][" ".join(spec)].append(f"pyproject.toml[{extra}]")

    conflicts = {
        package: dict(versions) for package, versions in seen.items() if len(versions) > 1
    }
    assert not conflicts, f"packages pinned at more than one version: {conflicts}"


def test_runtime_requirements_carry_no_test_tooling() -> None:
    """requirements.txt is what the Docker image installs — keep tests out of it."""
    runtime = _parse_requirements(REPO_ROOT / "requirements.txt")
    forbidden = {"pytest", "pytest-cov", "ruff", "httpx", "pip-audit", "pyyaml"}
    leaked = sorted(forbidden & set(runtime))
    assert not leaked, (
        f"test-only packages in requirements.txt (shipped in the prod image): {leaked}. "
        "README documents the image as carrying no pytest/httpx."
    )


def test_dev_requirements_include_the_runtime_set() -> None:
    dev = (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert re.search(r"^-\s*r\s+requirements\.txt\s*$", dev, re.MULTILINE), (
        "requirements-dev.txt must include requirements.txt rather than "
        "re-declaring every runtime pin (that duplication is what let the "
        "versions drift apart)"
    )


def test_requirements_local_declares_no_pins_of_its_own() -> None:
    """It is an alias now; any direct pin here reintroduces the conflict."""
    path = REPO_ROOT / "requirements-local.txt"
    text = path.read_text(encoding="utf-8")
    assert _parse_requirements(path) == {}, "requirements-local.txt must not pin anything directly"
    assert re.search(r"^-\s*r\s+requirements-dev\.txt\s*$", text, re.MULTILINE), (
        "requirements-local.txt must resolve through requirements-dev.txt so "
        "existing `pip install -r requirements-local.txt` commands keep working"
    )


def test_pyproject_dependencies_mirror_requirements_txt() -> None:
    """Same packages, same versions — pyproject is not a second opinion."""
    runtime = _parse_requirements(REPO_ROOT / "requirements.txt")
    project = _pyproject()["project"]
    declared = _pyproject_pins(project.get("dependencies", []))
    assert declared == runtime, (
        f"pyproject [project].dependencies != requirements.txt\n"
        f"  only in pyproject: {sorted(set(declared) - set(runtime))}\n"
        f"  only in requirements.txt: {sorted(set(runtime) - set(declared))}\n"
        f"  differing versions: "
        f"{ {k: (declared[k], runtime[k]) for k in set(declared) & set(runtime) if declared[k] != runtime[k]} }"
    )


def test_pyproject_dev_extra_mirrors_requirements_dev() -> None:
    dev_file = _parse_requirements(REPO_ROOT / "requirements-dev.txt")
    project = _pyproject()["project"]
    dev_extra = _pyproject_pins((project.get("optional-dependencies") or {}).get("dev", []))
    assert dev_extra == dev_file, (
        f"pyproject dev extra != requirements-dev.txt\n"
        f"  only in pyproject: {sorted(set(dev_extra) - set(dev_file))}\n"
        f"  only in requirements-dev.txt: {sorted(set(dev_file) - set(dev_extra))}"
    )


def test_pdfplumber_is_declared_in_both_runtime_lists() -> None:
    """Audit issue #6: it was in requirements.txt but missing from pyproject."""
    assert "pdfplumber" in _parse_requirements(REPO_ROOT / "requirements.txt")
    project = _pyproject()["project"]
    assert "pdfplumber" in _pyproject_pins(project.get("dependencies", []))


def test_pyyaml_is_available_for_the_compose_hardening_tests() -> None:
    """tests/test_compose_hardening.py imports yaml; keep the pin honest."""
    dev_file = _parse_requirements(REPO_ROOT / "requirements-dev.txt")
    assert "pyyaml" in dev_file
    pytest.importorskip("yaml")
