#!/usr/bin/env python3
"""Garde-fou JUnit du job Qualification reserves.

Échoue (code ≠ 0) si le rapport est absent, malformé, vide, si un test est
ignoré, en échec ou en erreur, ou si un contrôle critique n'a pas été
collecté. Compte les ``<testcase>`` (pas les totaux d'en-tête emboîtés).

Identité : ``git rev-parse HEAD`` est le commit réellement exécuté.
``GITHUB_SHA`` sur pull_request peut être le merge synthétique — on le
distingue de ``SEAMTECH_PR_HEAD_SHA`` (tête de branche).
"""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


class JunitInvalide(Exception):
    """Rapport inexploitable ou critère de garde-fou violé."""


# Contrôles qui doivent figurer COMME testcase exécuté (pas seulement « 0 skip »
# sur un autre sous-ensemble). Noms de fonction pytest, sans fichier.
CRITIQUES: tuple[str, ...] = (
    "test_aller_retour_objets_minio_sources_indisponibles",
    "test_parcours_import_ocr_index_recherche_telechargement",
    "test_scan_anglais_retrouve_les_mots",
)


@dataclass(frozen=True)
class Rapport:
    tests: int
    skipped: int
    failures: int
    errors: int
    noms: tuple[str, ...]
    skipped_noms: tuple[str, ...]
    git_head: str
    github_sha: str
    event: str
    pr_head_sha: str


def _identite() -> tuple[str, str, str, str]:
    try:
        git_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        git_head = ""
    return (
        git_head,
        os.environ.get("GITHUB_SHA", "").strip(),
        os.environ.get("GITHUB_EVENT_NAME", "").strip(),
        os.environ.get("SEAMTECH_PR_HEAD_SHA", "").strip(),
    )


def _nom_fonction(case: ET.Element) -> str:
    brut = case.get("name") or ""
    return brut.split("[", 1)[0]


def lire(chemin: Path) -> Rapport:
    git_head, github_sha, event, pr_head = _identite()
    if not chemin.is_file():
        raise JunitInvalide(
            "JUnit absent ou invalide : fichier manquant "
            f"({chemin}). Un code de sortie 0 est interdit ici."
        )
    try:
        racine = ET.parse(chemin).getroot()
    except ET.ParseError as exc:
        raise JunitInvalide(f"JUnit absent ou invalide : XML malformé ({exc})") from exc

    cases = list(racine.iter("testcase"))
    skipped_noms: list[str] = []
    failures = 0
    errors = 0
    noms: list[str] = []
    for case in cases:
        nom = _nom_fonction(case)
        noms.append(nom)
        if case.find("skipped") is not None:
            skipped_noms.append(nom)
        if case.find("failure") is not None:
            failures += 1
        if case.find("error") is not None:
            errors += 1
    return Rapport(
        tests=len(cases),
        skipped=len(skipped_noms),
        failures=failures,
        errors=errors,
        noms=tuple(noms),
        skipped_noms=tuple(skipped_noms),
        git_head=git_head,
        github_sha=github_sha,
        event=event,
        pr_head_sha=pr_head,
    )


def verifier(rapport: Rapport, critiques: tuple[str, ...] = CRITIQUES) -> None:
    if rapport.tests <= 0:
        raise JunitInvalide("JUnit : aucun test exécuté (0 testcase).")
    if rapport.skipped > 0:
        raise JunitInvalide(
            f"JUnit : skipped={rapport.skipped} > 0 "
            f"({', '.join(rapport.skipped_noms) or 'sans nom'})."
        )
    if rapport.failures > 0:
        raise JunitInvalide(f"JUnit : failures={rapport.failures} > 0.")
    if rapport.errors > 0:
        raise JunitInvalide(f"JUnit : errors={rapport.errors} > 0.")
    manquants = [c for c in critiques if c not in rapport.noms]
    if manquants:
        raise JunitInvalide(
            "contrôle critique absent de la collecte JUnit : " + ", ".join(manquants)
        )


def _annoter(rapport: Rapport) -> None:
    print(
        f"::notice title=qualification-summary::"
        f"tests={rapport.tests} skipped={rapport.skipped} "
        f"failures={rapport.failures} errors={rapport.errors}"
    )
    print(
        f"::notice title=qualification-identite::"
        f"git_head={rapport.git_head or '?'} "
        f"github_sha={rapport.github_sha or '?'} "
        f"event={rapport.event or '?'} "
        f"pr_head_sha={rapport.pr_head_sha or '(push: N/A)'}"
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: qualification_junit.py RAPPORT.xml", file=sys.stderr)
        return 2
    try:
        rapport = lire(Path(args[0]))
        _annoter(rapport)
        verifier(rapport)
    except JunitInvalide as exc:
        print(f"::error title=qualification-junit::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
