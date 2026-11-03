"""Publier la TRACE COMPLÈTE de quelques tests, quand la dernière ligne « E »
ne suffit pas.

Une cause comme « une sauvegarde est déjà en cours » dit QUOI, pas OÙ. Pour
savoir si un test échoue parce que la plateforme n'a pas la primitive ou
parce que le produit se trompe, il faut la trace : le fichier, la ligne, et
les cinq lignes qui précèdent l'assertion. C'est ce que ce script publie —
une annotation par test, six au plus par étape (plafond de GitHub).

Usage : python ci/publier_traces.py            (lit ci/tests-trace.txt)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
LIMITE = 2400


def _identifiants() -> list[str]:
    liste = RACINE / "ci" / "tests-trace.txt"
    if not liste.exists():
        return []
    return [
        ligne.strip()
        for ligne in liste.read_text(encoding="utf-8").splitlines()
        if ligne.strip() and not ligne.strip().startswith("#")
    ]


def _publier(titre: str, texte: str) -> None:
    propre = " ".join((texte or "").split()).replace("|", "/").replace("%", " ")
    if len(propre) > LIMITE:
        propre = propre[:LIMITE] + "…"
    print(f"::notice title={titre}::{propre}")


def _trace(identifiant: str) -> str:
    execution = subprocess.run(
        [sys.executable, "-m", "pytest", identifiant, "-x", "--tb=long", "-p", "no:randomly", "-q"],
        cwd=RACINE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    sortie = (execution.stdout or "") + (execution.stderr or "")
    if "=== FAILURES ===" not in sortie and "=== ERRORS ===" not in sortie:
        return "AUCUNE TRACE — le test PASSE rejoué seul : " + " ".join(sortie.split())[-300:]
    corps = sortie.split("=== FAILURES ===", 1)[-1].split("=== ERRORS ===", 1)[-1]
    return corps


def main() -> int:
    for identifiant in _identifiants()[:6]:
        titre = "diag-trace-" + identifiant.split("::")[-1][:40]
        _publier(titre, _trace(identifiant))
    return 0


if __name__ == "__main__":
    sys.exit(main())
