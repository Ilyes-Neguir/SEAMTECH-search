#!/usr/bin/env python3
"""Sync the curated ECC skills vendored under ``.agents/``.

Source: https://github.com/affaan-m/ECC (MIT — see ``.agents/ECC-LICENSE``).

Only an allowlist of skills/agents matching the SEAMTECH stack (Python +
FastAPI + PostgreSQL + Redis + Next.js/React) is vendored, and ONLY Markdown
files are ever copied: ECC hooks, MCP configs, installers and other
executable scripts are deliberately excluded, so nothing vendored here can
run automatically. The synced files are pinned by SHA-256 in
``.agents/ecc-manifest.json`` (verified by ``tests/test_ecc_skills.py``).

Usage:
    python3 scripts/sync_ecc_skills.py --check            # verify local files vs manifest (offline)
    python3 scripts/sync_ecc_skills.py --list             # print the allowlist
    python3 scripts/sync_ecc_skills.py --update           # re-sync from the pinned ECC commit
    python3 scripts/sync_ecc_skills.py --update --commit <sha>   # sync a newer ECC commit
    python3 scripts/sync_ecc_skills.py --update --source /path/to/ECC  # sync from a local clone

Exit code: 0 on success, 1 on any mismatch/failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ECC_REPO = "https://github.com/affaan-m/ECC.git"
PINNED_COMMIT = "90606c8315ee09cafc303515c5f33024db6b9882"  # ECC v2.2.3, 2026-10-20

# Curated allowlist — every entry MUST exist upstream and contain only .md
# files. Deliberately excluded examples: backend-patterns (Node/Express
# oriented), benchmark-methodology (competitor scoring, not perf),
# security-scan (audits a Claude Code .claude/ dir), docs-governance /
# rules-distill (ship executable scripts). See .agents/README.md.
SKILLS: tuple[str, ...] = (
    # Core workflow
    "tdd-workflow",
    "verification-loop",
    "search-first",
    "coding-standards",
    "error-handling",
    "git-workflow",
    "github-ops",
    "production-audit",
    # Python / FastAPI backend
    "python-patterns",
    "python-testing",
    "fastapi-patterns",
    "api-design",
    "postgres-patterns",
    "redis-patterns",
    "database-migrations",
    "docker-patterns",
    "deployment-patterns",
    "security-review",
    "regex-vs-llm-structured-text",
    "ai-regression-testing",
    # Next.js / React frontend
    "react-patterns",
    "react-testing",
    "react-performance",
    "nextjs-turbopack",
    "frontend-patterns",
    "frontend-a11y",
    "e2e-testing",
    # Architecture / documentation
    "architecture-decision-records",
    "documentation-lookup",
    "contract-first",
)

# Reviewer/role prompts used as checklists (Arena cannot spawn ECC
# subagents; the agent applies the matching checklist itself).
AGENTS: tuple[str, ...] = (
    "planner",
    "architect",
    "tdd-guide",
    "code-reviewer",
    "security-reviewer",
    "python-reviewer",
    "fastapi-reviewer",
    "database-reviewer",
    "typescript-reviewer",
    "react-reviewer",
    "performance-optimizer",
    "e2e-runner",
    "doc-updater",
    "build-error-resolver",
    "refactor-cleaner",
    "rag-pipeline-reviewer",
)

RACINE = Path(__file__).resolve().parents[1]
DEST = RACINE / ".agents"
MANIFESTE = DEST / "ecc-manifest.json"
LICENCE_DEST = DEST / "ECC-LICENSE"


def sha256_fichier(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def fichiers_vendors() -> list[Path]:
    """All vendored files currently on disk (skills + agents + licence)."""
    fichiers: list[Path] = []
    for dossier in (DEST / "skills", DEST / "agents"):
        if dossier.is_dir():
            fichiers += sorted(p for p in dossier.rglob("*") if p.is_file())
    if LICENCE_DEST.is_file():
        fichiers.append(LICENCE_DEST)
    return fichiers


def construire_manifeste(commit: str, version: str) -> dict:
    fichiers = {
        p.relative_to(RACINE).as_posix(): {"sha256": sha256_fichier(p), "size": p.stat().st_size}
        for p in fichiers_vendors()
    }
    return {
        "source": "https://github.com/affaan-m/ECC",
        "license": "MIT (see ECC-LICENSE)",
        "ecc_version": version,
        "ecc_commit": commit,
        "skills": sorted(SKILLS),
        "agents": sorted(AGENTS),
        "files": fichiers,
    }


def verifier() -> list[str]:
    """Return the list of divergences between disk and the pinned manifest."""
    erreurs: list[str] = []
    if not MANIFESTE.is_file():
        return [f"manifest missing: {MANIFESTE.relative_to(RACINE)}"]
    manifeste = json.loads(MANIFESTE.read_text(encoding="utf-8"))
    epingles: dict = manifeste.get("files", {})
    sur_disque = {p.relative_to(RACINE).as_posix(): p for p in fichiers_vendors()}

    for rel, meta in sorted(epingles.items()):
        chemin = sur_disque.get(rel)
        if chemin is None:
            erreurs.append(f"missing vendored file: {rel}")
        elif sha256_fichier(chemin) != meta.get("sha256"):
            erreurs.append(f"modified vendored file (SHA-256 mismatch): {rel}")
    for rel in sorted(sur_disque):
        if rel not in epingles:
            erreurs.append(f"unpinned file under .agents/ (add it via --update): {rel}")
    if sorted(manifeste.get("skills", [])) != sorted(SKILLS):
        erreurs.append("manifest skills != SKILLS allowlist in scripts/sync_ecc_skills.py")
    if sorted(manifeste.get("agents", [])) != sorted(AGENTS):
        erreurs.append("manifest agents != AGENTS allowlist in scripts/sync_ecc_skills.py")
    return erreurs


def _git(*args: str, cwd: Path) -> str:
    sortie = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=600
    )
    return sortie.stdout.strip()


def _preparer_source(commit: str, source_local: Path | None, tmp: Path) -> Path:
    if source_local is not None:
        tete = _git("rev-parse", "HEAD", cwd=source_local)
        if tete != commit:
            raise SystemExit(
                f"local ECC clone is at {tete}, expected {commit}.\n"
                f"Run: git -C {source_local} checkout {commit}  (this script never mutates a local clone)"
            )
        return source_local
    clone = tmp / "ECC"
    print(f"cloning {ECC_REPO} @ {commit} ...")
    _git("clone", "--quiet", ECC_REPO, str(clone), cwd=tmp)
    _git("checkout", "--quiet", commit, cwd=clone)
    return clone


def mettre_a_jour(commit: str, source_local: Path | None) -> None:
    divergences = [e for e in verifier() if "modified" in e]
    if divergences:
        print("WARNING — locally modified vendored files will be overwritten:")
        for ligne in divergences:
            print(f"  - {ligne}")

    with tempfile.TemporaryDirectory(prefix="seamtech-ecc-") as tmp:
        source = _preparer_source(commit, source_local, Path(tmp))
        version = (source / "VERSION").read_text(encoding="utf-8").strip() if (source / "VERSION").is_file() else "?"

        # Validate the full allowlist BEFORE touching the working tree.
        ignores: list[str] = []
        for skill in SKILLS:
            dossier = source / "skills" / skill
            if not (dossier / "SKILL.md").is_file():
                raise SystemExit(f"upstream skill missing SKILL.md: skills/{skill}")
            ignores += [
                str(p.relative_to(source))
                for p in sorted(dossier.rglob("*"))
                if p.is_file() and p.suffix != ".md"
            ]
        for agent in AGENTS:
            if not (source / "agents" / f"{agent}.md").is_file():
                raise SystemExit(f"upstream agent missing: agents/{agent}.md")
        if not (source / "LICENSE").is_file():
            raise SystemExit("upstream LICENSE missing — refusing to vendor without it")
        if ignores:
            print("non-Markdown upstream files NOT copied (executables are never vendored):")
            for ligne in ignores:
                print(f"  - {ligne}")

        # Copy skills (Markdown only), replacing each skill dir wholesale so
        # upstream deletions propagate.
        for skill in SKILLS:
            cible = DEST / "skills" / skill
            if cible.exists():
                shutil.rmtree(cible)
            for p in sorted((source / "skills" / skill).rglob("*.md")):
                dest = cible / p.relative_to(source / "skills" / skill)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, dest)
        # Copy agents + licence.
        (DEST / "agents").mkdir(parents=True, exist_ok=True)
        for agent in AGENTS:
            shutil.copyfile(source / "agents" / f"{agent}.md", DEST / "agents" / f"{agent}.md")
        shutil.copyfile(source / "LICENSE", LICENCE_DEST)

        # Remove previously vendored skills/agents dropped from the allowlist.
        for dossier in sorted((DEST / "skills").iterdir()):
            if dossier.is_dir() and dossier.name not in SKILLS:
                print(f"removing skill no longer allowlisted: {dossier.name}")
                shutil.rmtree(dossier)
        for fichier in sorted((DEST / "agents").glob("*.md")):
            if fichier.stem not in AGENTS:
                print(f"removing agent no longer allowlisted: {fichier.name}")
                fichier.unlink()

        manifeste = construire_manifeste(commit, version)
        MANIFESTE.write_text(json.dumps(manifeste, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(
            f"synced {len(SKILLS)} skills + {len(AGENTS)} agents from ECC v{version} ({commit[:12]}), "
            f"{len(manifeste['files'])} files pinned in {MANIFESTE.relative_to(RACINE)}"
        )


def main() -> int:
    parseur = argparse.ArgumentParser(description="Sync curated ECC skills vendored under .agents/")
    action = parseur.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="verify local files against the pinned manifest (offline)")
    action.add_argument("--update", action="store_true", help="re-sync from ECC and rewrite the manifest")
    action.add_argument("--list", action="store_true", help="print the curated allowlist")
    parseur.add_argument("--commit", default=PINNED_COMMIT, help=f"ECC commit to sync (default: pinned {PINNED_COMMIT[:12]})")
    parseur.add_argument("--source", type=Path, default=None, help="existing local ECC clone (must be at --commit)")
    args = parseur.parse_args()

    if args.list:
        print(f"ECC skills ({len(SKILLS)}):")
        for skill in SKILLS:
            print(f"  .agents/skills/{skill}/SKILL.md")
        print(f"ECC agents ({len(AGENTS)}):")
        for agent in AGENTS:
            print(f"  .agents/agents/{agent}.md")
        return 0
    if args.update:
        mettre_a_jour(args.commit, args.source)
        erreurs = verifier()
    else:
        erreurs = verifier()
    if erreurs:
        print("ECC skills check FAILED:")
        for erreur in erreurs:
            print(f"  - {erreur}")
        return 1
    print(f"ECC skills check OK — {len(SKILLS)} skills, {len(AGENTS)} agents, manifest matches disk.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
