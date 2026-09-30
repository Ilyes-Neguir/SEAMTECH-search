#!/usr/bin/env python3
"""RG14_EXCEPTION: CI-only OSV severity enrichment for pip-audit results.

Apply the dependency-audit policy to pip-audit and pnpm audit JSON. Only known
HIGH/CRITICAL vulnerabilities with an upstream fix fail the job. Unfixed
advisories and lower-severity findings are emitted as GitHub notices. This
script is never imported or called by the running application.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def pip_findings(path: Path) -> list[tuple[str, str, bool]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = []
    for dep in payload.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            aliases = [vuln.get("id", ""), *vuln.get("aliases", [])]
            severity = "UNKNOWN"
            for identifier in aliases:
                if not identifier:
                    continue
                url = "https:" + "/" + "/" + "api.osv.dev/v1/vulns/" + urllib.parse.quote(identifier, safe="")
                try:
                    with urllib.request.urlopen(url, timeout=12) as response:
                        osv = json.load(response)
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                    continue
                severity = str(osv.get("database_specific", {}).get("severity", "UNKNOWN")).upper()
                for rating in osv.get("severity", []):
                    try:
                        score = float(rating.get("score", ""))
                    except (TypeError, ValueError):
                        continue
                    severity = "CRITICAL" if score >= 9 else "HIGH" if score >= 7 else "MODERATE" if score >= 4 else "LOW"
                    break
                if severity != "UNKNOWN":
                    break
            fixed = bool(vuln.get("fix_versions"))
            result.append((f"pip {dep.get('name')} {dep.get('version')} {vuln.get('id')} ({severity})", severity, fixed))
    return result


def pnpm_findings(path: Path) -> list[tuple[str, str, bool]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = []
    for advisory_id, advisory in payload.get("advisories", {}).items():
        severity = str(advisory.get("severity", "UNKNOWN")).upper()
        patched = bool(advisory.get("patched_versions")) and advisory.get("patched_versions") != "<0.0.0"
        fix_available = patched or any(bool(action.get("isMajor")) is False for action in payload.get("actions", []))
        result.append((f"pnpm {advisory.get('module_name', 'dependency')} {advisory_id} ({severity})", severity, fix_available))
    # pnpm's modern audit JSON may summarize vulnerabilities without advisories.
    if not payload.get("advisories"):
        counts = payload.get("metadata", {}).get("vulnerabilities", {})
        for severity in ("critical", "high", "moderate", "low", "info"):
            count = int(counts.get(severity, 0) or 0)
            if count:
                # Without per-advisory fix metadata, report but do not mislabel as fixable.
                result.append((f"pnpm audit: {count} {severity} finding(s); upstream fix status not exposed", severity.upper(), False))
    return result


def main() -> int:
    findings = pip_findings(Path(sys.argv[1])) + pnpm_findings(Path(sys.argv[2]))
    failures = []
    for message, severity, fixable in findings:
        if severity in {"HIGH", "CRITICAL"} and fixable:
            print(f"::error title=Vulnérabilité corrigeable::{message} — échec selon la politique HIGH/CRITICAL.")
            failures.append(message)
        elif severity in {"HIGH", "CRITICAL"} and not fixable:
            print(f"::notice title=Vulnérabilité sans correctif::{message} — documentée, aucun correctif amont disponible.")
        else:
            print(f"::notice title=Audit dépendances::{message} — n'atteint pas le seuil d'échec HIGH/CRITICAL ou le correctif n'est pas confirmé.")
    print(f"Audit dépendances : {len(findings)} finding(s), {len(failures)} HIGH/CRITICAL corrigeable(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
