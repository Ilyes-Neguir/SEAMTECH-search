"""Cas synthétiques du garde-fou JUnit (harnais, pas le produit)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "qualification_junit.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("qualification_junit", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["qualification_junit"] = module
    spec.loader.exec_module(module)
    return module


harness = _load()

CRITIQUES = harness.CRITIQUES


def _xml(tmp_path: Path, corps: str) -> Path:
    cible = tmp_path / "rapport.xml"
    cible.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n' + corps,
        encoding="utf-8",
    )
    return cible


def _ok_cases() -> str:
    lignes = []
    for nom in CRITIQUES:
        lignes.append(f'    <testcase classname="tests.t" name="{nom}" time="0.01"/>')
    lignes.append('    <testcase classname="tests.t" name="test_autre_ok" time="0.01"/>')
    return "\n".join(lignes)


def test_valide(tmp_path: Path) -> None:
    chemin = _xml(
        tmp_path,
        "<testsuites tests='99'>\n"
        "  <testsuite name='pytest' tests='99' skipped='0' failures='0' errors='0'>\n"
        f"{_ok_cases()}\n"
        "  </testsuite>\n"
        "</testsuites>\n",
    )
    rapport = harness.lire(chemin)
    # 99 dans l'en-tête ≠ 4 testcase : pas de double comptage ni d'en-tête.
    assert rapport.tests == 4
    assert rapport.skipped == rapport.failures == rapport.errors == 0
    harness.verifier(rapport)
    assert harness.main([str(chemin)]) == 0


def test_absent(tmp_path: Path) -> None:
    manquant = tmp_path / "nexiste-pas.xml"
    with pytest.raises(harness.JunitInvalide, match="manquant"):
        harness.lire(manquant)
    assert harness.main([str(manquant)]) == 1


def test_malforme(tmp_path: Path) -> None:
    chemin = tmp_path / "casse.xml"
    chemin.write_text("<testsuites><not-closed>", encoding="utf-8")
    with pytest.raises(harness.JunitInvalide, match="malformé"):
        harness.lire(chemin)
    assert harness.main([str(chemin)]) == 1


def test_zero_test(tmp_path: Path) -> None:
    chemin = _xml(
        tmp_path,
        "<testsuite name='pytest' tests='0' skipped='0' failures='0' errors='0'/>\n",
    )
    rapport = harness.lire(chemin)
    assert rapport.tests == 0
    with pytest.raises(harness.JunitInvalide, match="aucun test"):
        harness.verifier(rapport)
    assert harness.main([str(chemin)]) == 1


def test_ignore(tmp_path: Path) -> None:
    chemin = _xml(
        tmp_path,
        "<testsuite name='pytest'>\n"
        f"{_ok_cases()}\n"
        '    <testcase classname="tests.t" name="test_saute" time="0">\n'
        '      <skipped message="skip volontaire"/>\n'
        "    </testcase>\n"
        "</testsuite>\n",
    )
    rapport = harness.lire(chemin)
    assert rapport.skipped == 1
    with pytest.raises(harness.JunitInvalide, match="skipped=1"):
        harness.verifier(rapport)
    assert harness.main([str(chemin)]) == 1


def test_echec(tmp_path: Path) -> None:
    chemin = _xml(
        tmp_path,
        "<testsuite name='pytest'>\n"
        f"{_ok_cases()}\n"
        '    <testcase classname="tests.t" name="test_rouge" time="0.1">\n'
        '      <failure message="assert False"/>\n'
        "    </testcase>\n"
        "</testsuite>\n",
    )
    rapport = harness.lire(chemin)
    assert rapport.failures == 1
    with pytest.raises(harness.JunitInvalide, match="failures=1"):
        harness.verifier(rapport)
    assert harness.main([str(chemin)]) == 1


def test_erreur(tmp_path: Path) -> None:
    chemin = _xml(
        tmp_path,
        "<testsuite name='pytest'>\n"
        f"{_ok_cases()}\n"
        '    <testcase classname="tests.t" name="test_boom" time="0.1">\n'
        "      <error message=\"ImportError\"/>\n"
        "    </testcase>\n"
        "</testsuite>\n",
    )
    rapport = harness.lire(chemin)
    assert rapport.errors == 1
    with pytest.raises(harness.JunitInvalide, match="errors=1"):
        harness.verifier(rapport)
    assert harness.main([str(chemin)]) == 1


def test_controle_critique_absent(tmp_path: Path) -> None:
    chemin = _xml(
        tmp_path,
        "<testsuite name='pytest'>\n"
        '    <testcase classname="tests.t" name="test_autre_ok" time="0.01"/>\n'
        "</testsuite>\n",
    )
    rapport = harness.lire(chemin)
    assert rapport.tests == 1
    assert rapport.skipped == 0
    with pytest.raises(harness.JunitInvalide, match="contrôle critique absent"):
        harness.verifier(rapport)
    assert harness.main([str(chemin)]) == 1
