"""Garde-fou des skills ECC vendorés sous ``.agents/``.

Même esprit que ``tests/test_confidentialite_depots.py`` : un inventaire
ÉPINGLÉ (``.agents/ecc-manifest.json``, SHA-256 par fichier) rend toute
modification des copies ECC un acte conscient. Les skills sont des copies
verbatim de affaan-m/ECC (MIT) ; on ne les édite jamais à la main — pour
changer de version ou d'inventaire, on passe par
``scripts/sync_ecc_skills.py --update``, qui réécrit le manifeste.

Ce test verrouille quatre cohérences, sans réseau :
- disque == manifeste (aucun fichier modifié, manquant ou non épinglé) ;
- allowlist du script de sync == manifeste ;
- aucun fichier non-Markdown vendoré (les hooks/scripts ECC ne doivent
  JAMAIS entrer dans le dépôt : rien sous ``.agents/`` ne s'exécute) ;
- ``AGENTS.md`` route chaque skill épinglé, et chaque ``SKILL.md`` porte
  un frontmatter ``name:`` égal à son répertoire.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO / ".agents"
MANIFESTE = AGENTS_DIR / "ecc-manifest.json"


def _charger_manifeste() -> dict:
    return json.loads(MANIFESTE.read_text(encoding="utf-8"))


def _fichiers_sur_disque() -> dict[str, Path]:
    fichiers: dict[str, Path] = {}
    for dossier in (AGENTS_DIR / "skills", AGENTS_DIR / "agents"):
        for chemin in sorted(dossier.rglob("*")):
            if chemin.is_file():
                fichiers[chemin.relative_to(REPO).as_posix()] = chemin
    licence = AGENTS_DIR / "ECC-LICENSE"
    if licence.is_file():
        fichiers[licence.relative_to(REPO).as_posix()] = licence
    return fichiers


def test_disque_conforme_au_manifeste_epingle() -> None:
    """Chaque fichier vendoré existe, non modifié ; aucun fichier non épinglé."""
    manifeste = _charger_manifeste()
    epingles: dict[str, dict] = manifeste["files"]
    sur_disque = _fichiers_sur_disque()

    manquants = sorted(set(epingles) - set(sur_disque))
    assert not manquants, f"Fichier(s) ECC épinglé(s) absent(s) du disque : {manquants}"
    non_epingles = sorted(set(sur_disque) - set(epingles))
    assert not non_epingles, (
        f"Fichier(s) sous .agents/ hors manifeste : {non_epingles}. "
        "Ils s'ajoutent via scripts/sync_ecc_skills.py --update, jamais à la main."
    )
    modifies = [
        rel
        for rel, meta in sorted(epingles.items())
        if hashlib.sha256(sur_disque[rel].read_bytes()).hexdigest() != meta["sha256"]
    ]
    assert not modifies, (
        f"Copie(s) ECC modifiée(s) localement : {modifies}. Les skills vendorés sont "
        "verbatim ; les adaptations locales vivent dans AGENTS.md / .agents/README.md."
    )


def test_allowlist_du_script_egale_manifeste() -> None:
    """SKILLS/AGENTS de scripts/sync_ecc_skills.py == inventaire du manifeste."""
    spec = importlib.util.spec_from_file_location(
        "sync_ecc_skills", REPO / "scripts" / "sync_ecc_skills.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    manifeste = _charger_manifeste()
    assert sorted(module.SKILLS) == sorted(manifeste["skills"])
    assert sorted(module.AGENTS) == sorted(manifeste["agents"])
    assert manifeste["ecc_commit"] == module.PINNED_COMMIT


def test_aucun_fichier_executable_vendore() -> None:
    """Que du Markdown (+ la licence) : aucun hook/script ECC dans le dépôt."""
    suspects = [
        rel
        for rel in _charger_manifeste()["files"]
        if not rel.endswith(".md") and not rel.endswith("ECC-LICENSE")
    ]
    assert not suspects, f"Fichier(s) non-Markdown vendoré(s) (interdit) : {suspects}"
    assert not (AGENTS_DIR / "hooks").exists(), "les hooks ECC ne sont pas installés"


def test_chaque_skill_frontmatter_coherent() -> None:
    """Chaque répertoire skills/<nom>/ contient SKILL.md avec name: <nom>."""
    for skill in _charger_manifeste()["skills"]:
        chemin = AGENTS_DIR / "skills" / skill / "SKILL.md"
        assert chemin.is_file(), f"SKILL.md manquant pour le skill épinglé « {skill} »"
        frontmatter = re.match(
            r"^---\n(.*?)\n---", chemin.read_text(encoding="utf-8"), re.DOTALL
        )
        assert frontmatter, f"frontmatter absent dans {chemin.relative_to(REPO)}"
        nom = re.search(r"^name:\s*(.+)$", frontmatter.group(1), re.MULTILINE)
        assert nom and nom.group(1).strip().strip('"') == skill, (
            f"frontmatter name: incohérent dans {chemin.relative_to(REPO)}"
        )


def test_agents_md_route_chaque_skill() -> None:
    """La table de routage d'AGENTS.md référence exactement les skills épinglés."""
    texte = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    routes = set(re.findall(r"\.agents/skills/([a-z0-9-]+)/SKILL\.md", texte))
    assert routes == set(_charger_manifeste()["skills"]), (
        "AGENTS.md doit router chaque skill épinglé (ni plus, ni moins). "
        f"Différence : {sorted(routes ^ set(_charger_manifeste()['skills']))}"
    )
