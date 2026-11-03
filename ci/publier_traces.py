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

# La console Windows du runner encode en cp1252 : une trace contenant un
# caractère non latin (U+FFFD, accents d'un chemin) faisait échouer
# ``print`` lui-même (run 41559119322 : UnicodeEncodeError à l'impression, et
# plus aucune trace publiée). On force donc une sortie UTF-8 tolérante.
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # noqa: PERF203 - vieux Python, flux sans reconfigure
        pass


def _identifiants() -> list[str]:
    liste = RACINE / "ci" / "tests-trace.txt"
    if not liste.exists():
        return []
    return [
        ligne.strip()
        for ligne in liste.read_text(encoding="utf-8").splitlines()
        if ligne.strip() and not ligne.strip().startswith("#")
    ]


def _publier(titre: str, texte: str, *, queue: bool = False) -> None:
    propre = " ".join((texte or "").split()).replace("|", "/").replace("%", " ")
    if len(propre) > LIMITE:
        # Une trace se lit par la FIN : c'est là qu'est l'erreur. Couper le
        # début a fait publier six traces sans jamais montrer la ligne fautive
        # (run 41560558673).
        propre = ("…" + propre[-LIMITE:]) if queue else (propre[:LIMITE] + "…")
    print(f"::notice title={titre}::{propre}")


def _trace(identifiant: str) -> str:
    try:
        execution = subprocess.run(
            [sys.executable, "-m", "pytest", identifiant, "-x", "--tb=long", "-p", "no:randomly", "-q"],
            cwd=RACINE,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        # Un test qui ne rend pas la main est un résultat, pas une panne de
        # l'instrument : il doit être dit, et les suivants doivent être publiés.
        return "TEMPS DEPASSE (180 s) — le test ne rend pas la main rejoué seul"
    sortie = (execution.stdout or "") + (execution.stderr or "")
    if "=== FAILURES ===" not in sortie and "=== ERRORS ===" not in sortie:
        return "AUCUNE TRACE — le test PASSE rejoué seul : " + " ".join(sortie.split())[-300:]
    corps = sortie.split("=== FAILURES ===", 1)[-1].split("=== ERRORS ===", 1)[-1]
    return corps


def main() -> int:
    for identifiant in _identifiants()[:6]:
        titre = "diag-trace-" + identifiant.split("::")[-1][:40]
        try:
            _publier(titre, _trace(identifiant), queue=True)
        except Exception as erreur:  # noqa: BLE001 - l'instrument ne doit pas taire les autres
            _publier(titre, f"INSTRUMENT EN PANNE : {type(erreur).__name__} : {erreur}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
