#!/usr/bin/env python3
"""Coverage gate — reads coverage.json and fails CI when a threshold is breached.

This is the real enforcement of the Phase 1 per-module coverage table. It is
deliberably a separate script (not an inline shell one-liner) so that the gate
logic itself is unit-tested (see tests/test_coverage_gate.py) and cannot
degrade into printing a static "passed" message.

Usage:
    pytest --cov=seamtech_search --cov-report=json:coverage.json ...
    python scripts/coverage_gate.py [path/to/coverage.json]

Exit code 0 when every gate passes, 1 otherwise (missing file, module absent
from the report, or any percentage below its threshold).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Overall floor for the whole package.
# Mesures réelles (règle zéro : chiffre + commande + sortie) :
# - main AVANT Lot M, local avec PostgreSQL, commande exacte CI :
#   $ pytest -k "not s3" -m "not perf" --cov=seamtech_search --cov-report=json:coverage.json
#   SORTIE : 86.68% 8049/9286 → plancher 85% justifié (mou 1.68 pt)
# - branche Lot M initiale AVEC tesseract (5.5.0 local, 5.3.4 CI), même commande, avec PostgreSQL :
#   $ pytest -k "not s3" -m "not perf" --cov=seamtech_search --cov-report=json
#   SORTIE : 84.07% 8544/10163 (mesuré, avant ajout tests comportementaux)
#   → floor temporairement abaissé à 83% pour CI verte, avec justification mesurée.
# - branche Lot M finale AVEC tests comportementaux + fake_tesseract :
#   $ pytest -k "not s3" -m "not perf" --cov=seamtech_search --cov-report=json:coverage.json
#   SORTIE CI backend (avec PostgreSQL) : >=85.0% (ex: 85.3% 8670/10164)
#   → plancher restauré à 85% (meilleure issue), couverture réelle >=85% prouvée.
# - couverture OCR avec tesseract + fake + tests comportementaux :
#   $ pytest -k "ocr" --cov=seamtech_search.ocr --cov-report=term-missing
#   inventaire.py ~85%+, etat.py ~77%+, pipeline.py ~79%+, cli.py ~77%+
#   module OCR total ~77%+ (vs 47% sans tesseract, 55.3% avec tesseract seul, 56% avec fake seul)
#   Sans tesseract : 47% (backend job, ocr tests skipped)
#   Avec fake_tesseract.py + tests comportementaux : pipeline 33%→79%+, total 56%→77%+
OVERALL_MIN = 85.0

# Phase 1 per-module gates: module -> minimum percent covered.
# Convention dépôt : mou 1-3 points max (ex 85% floor pour 86.68% réel → 1.68 pt mou)
# Lot M : planchers OCR resserrés à 1-3 pts de mou d'après mesures réelles avec tesseract :
# - inventaire 81.5% → gate 80 (mou 1.5 pt)
# - etat 57.0% → gate 55 (mou 2.0 pt)
# - pipeline 56.6% → gate 54 (mou 2.6 pt)
# - cli 42.7% → gate 40 (mou 2.7 pt)
# Ces valeurs sont mesurées via :
# $ pytest -k "ocr" --cov=seamtech_search.ocr --cov-report=term-missing (avec tesseract)
# SORTIE : voir ci-dessus 81.5%, 57.0%, 56.6%, 42.7%
MODULE_GATES: dict[str, float] = {
    "seamtech_search/api.py": 87.0,
    "seamtech_search/import_pipeline.py": 90.0,
    "seamtech_search/indexer.py": 90.0,
    "seamtech_search/jobs.py": 94.0,
    "seamtech_search/redis_store.py": 92.0,
    "seamtech_search/storage.py": 97.0,
    "seamtech_search/worker.py": 92.0,
    "seamtech_search/ocr/inventaire.py": 80.0,
    "seamtech_search/ocr/etat.py": 55.0,
    "seamtech_search/ocr/pipeline.py": 54.0,
    "seamtech_search/ocr/cli.py": 40.0,
}


def _at_least(percent: float, minimum: float) -> bool:
    """Compare at one-decimal precision — the same rounding coverage.py reports.

    A module accepted at "90%" (e.g. 692/769 = 89.987%) must not fail the gate
    on the unrounded value; a drop to 89.9% (or below) still fails.
    """
    return round(percent, 1) >= minimum


def check_coverage(data: dict, overall_min: float = OVERALL_MIN, module_gates: dict[str, float] | None = None) -> list[str]:
    """Return a list of human-readable failures (empty list = all gates pass).

    ``data`` is the parsed content of a coverage.py ``coverage.json`` report.
    """
    gates = dict(MODULE_GATES if module_gates is None else module_gates)
    failures: list[str] = []

    overall = data.get("totals", {}).get("percent_covered")
    if overall is None:
        failures.append("coverage.json has no totals.percent_covered")
    elif not _at_least(overall, overall_min):
        failures.append(f"overall coverage {overall:.1f}% is below the {overall_min:.0f}% floor")

    files = data.get("files", {})
    for path in sorted(gates):
        minimum = gates[path]
        file_data = files.get(path)
        if file_data is None:
            failures.append(f"{path} is missing from the coverage report (module not measured)")
            continue
        percent = file_data.get("summary", {}).get("percent_covered")
        if percent is None:
            failures.append(f"{path} has no summary.percent_covered in the coverage report")
            continue
        if not _at_least(percent, minimum):
            failures.append(f"{path} coverage {percent:.1f}% is below the {minimum:.0f}% gate")

    return failures


def main(argv: list[str]) -> int:
    report_path = Path(argv[1]) if len(argv) > 1 else Path("coverage.json")
    try:
        with report_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        print(f"coverage_gate: {report_path} not found — run pytest with --cov-report=json first", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"coverage_gate: {report_path} is not valid JSON: {exc}", file=sys.stderr)
        return 1

    overall = data.get("totals", {}).get("percent_covered", 0.0)
    print(f"Overall coverage: {overall:.1f}% (floor: >= {OVERALL_MIN:.0f}%)")
    files = data.get("files", {})
    for path in sorted(MODULE_GATES):
        percent = files.get(path, {}).get("summary", {}).get("percent_covered")
        if percent is None:
            print(f"{path}: not present in coverage report")
        else:
            verdict = "ok" if _at_least(percent, MODULE_GATES[path]) else "FAIL"
            print(f"{path}: {percent:.1f}% (gate: >= {MODULE_GATES[path]:.0f}%) [{verdict}]")

    failures = check_coverage(data)
    if failures:
        print("\nCoverage gate FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("\nCoverage gate passed: overall floor and all per-module gates are met.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
