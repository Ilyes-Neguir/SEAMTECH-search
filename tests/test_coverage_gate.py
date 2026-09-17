"""Tests for scripts/coverage_gate.py — the gate itself must be provably fail-able."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_PATH = REPO_ROOT / "scripts" / "coverage_gate.py"

_spec = importlib.util.spec_from_file_location("coverage_gate", GATE_PATH)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def make_report(overall: float, modules: dict[str, float]) -> dict:
    """Build a minimal coverage.json-shaped document."""
    return {
        "totals": {"covered": 100, "num_statements": 100, "percent_covered": overall},
        "files": {
            path: {"summary": {"covered": 100, "num_statements": 100, "percent_covered": pct}}
            for path, pct in modules.items()
        },
    }


ALL_MODULES = {
    "seamtech_search/api.py": 90.0,
    "seamtech_search/import_pipeline.py": 95.0,
    "seamtech_search/indexer.py": 92.0,
    "seamtech_search/jobs.py": 95.0,
    "seamtech_search/redis_store.py": 93.0,
    "seamtech_search/storage.py": 98.0,
    "seamtech_search/worker.py": 93.0,
}


def test_gate_passes_when_all_modules_above_threshold() -> None:
    failures = gate.check_coverage(make_report(90.0, ALL_MODULES))
    assert failures == []


def test_gate_fails_when_overall_below_floor() -> None:
    failures = gate.check_coverage(make_report(84.9, ALL_MODULES))
    assert any("overall" in f for f in failures)


def test_gate_fails_when_one_module_drops_below_its_gate() -> None:
    modules = dict(ALL_MODULES, **{"seamtech_search/worker.py": 40.0})
    failures = gate.check_coverage(make_report(90.0, modules))
    assert len(failures) == 1
    assert "seamtech_search/worker.py" in failures[0]
    assert "40.0%" in failures[0]


def test_gate_fails_when_a_module_is_missing_from_the_report() -> None:
    modules = {k: v for k, v in ALL_MODULES.items() if k != "seamtech_search/storage.py"}
    failures = gate.check_coverage(make_report(90.0, modules))
    assert len(failures) == 1
    assert "seamtech_search/storage.py" in failures[0]
    assert "missing" in failures[0]


def test_gate_accepts_the_one_decimal_rounding_boundary() -> None:
    # 692/769 = 89.987% displays as 90.0% in coverage.py and was accepted at
    # "90%": it must pass a 90% gate...
    modules = dict(ALL_MODULES, **{"seamtech_search/import_pipeline.py": round(692 / 769 * 100, 2)})
    assert gate.check_coverage(make_report(89.0, modules)) == []
    # ...while a genuine drop to 89.9% must fail it.
    modules = dict(ALL_MODULES, **{"seamtech_search/import_pipeline.py": 89.94})
    failures = gate.check_coverage(make_report(89.0, modules))
    assert len(failures) == 1
    assert "import_pipeline.py" in failures[0]


def test_gate_fails_on_multiple_breaches_and_reports_each() -> None:
    modules = dict(ALL_MODULES, **{"seamtech_search/api.py": 80.0, "seamtech_search/jobs.py": 50.0})
    failures = gate.check_coverage(make_report(80.0, modules))
    assert len(failures) == 3  # overall + api + jobs


def test_gate_main_returns_1_on_breach_and_writes_failures(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    modules = dict(ALL_MODULES, **{"seamtech_search/indexer.py": 61.0})
    report.write_text(json.dumps(make_report(89.0, modules)), encoding="utf-8")
    assert gate.main(["coverage_gate.py", str(report)]) == 1


def test_gate_main_returns_0_when_all_gates_met(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps(make_report(89.0, ALL_MODULES)), encoding="utf-8")
    assert gate.main(["coverage_gate.py", str(report)]) == 0


def test_gate_main_fails_when_report_missing(tmp_path: Path) -> None:
    assert gate.main(["coverage_gate.py", str(tmp_path / "does_not_exist.json")]) == 1


def test_gate_main_fails_on_invalid_json(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    report.write_text("{not json", encoding="utf-8")
    assert gate.main(["coverage_gate.py", str(report)]) == 1
