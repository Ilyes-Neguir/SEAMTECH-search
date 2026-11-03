"""Publier, en annotations GitHub, les causes des tests qui échouent SEULS.

Pourquoi ce script : le banc Windows ne peut pas être fouillé à la main — les
journaux bruts et les artefacts ne sont pas téléchargeables depuis ce poste
(erreur SSL vers le blob Azure). La seule voie de retour est l'annotation, et
une annotation se perd si l'étape en publie plus de dix. Ce script publie
donc, par paquet de fichiers, une annotation par fichier : les familles de
causes, puis la cause exacte de chaque test échoué.

Usage : python ci/publier_details_diag.py <premier> <combien>
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
LIMITE = 2400


def _fichiers() -> list[str]:
    liste = RACINE / "ci" / "fichiers-diagnostic.txt"
    return [
        ligne.strip()
        for ligne in liste.read_text(encoding="utf-8").splitlines()
        if ligne.strip() and not ligne.strip().startswith("#")
    ]


def _publier(titre: str, texte: str) -> None:
    propre = " ".join((texte or "").split())
    if len(propre) > LIMITE:
        propre = propre[:LIMITE] + "…"
    # Les « | » et les retours à la ligne casseraient la commande de workflow.
    propre = propre.replace("|", "/").replace("%", " ").replace("\r", " ").replace("\n", " ")
    print(f"::notice title={titre}::{propre}")


def main() -> int:
    if len(sys.argv) < 3:
        print("usage : publier_details_diag.py <premier> <combien>", file=sys.stderr)
        return 2
    premier, combien = int(sys.argv[1]), int(sys.argv[2])
    fichiers = _fichiers()[premier : premier + combien]
    if not fichiers:
        return 0
    for fichier in fichiers:
        nom = fichier.replace("tests/", "").removesuffix(".py")
        xml = RACINE / "logs" / f"junit-diag-{nom}.xml"
        if not xml.exists():
            _publier(f"diag-det-{nom}", "rapport JUnit absent : le fichier n'a pas été rejoué")
            continue
        with tempfile.TemporaryDirectory() as dossier:
            familles = Path(dossier) / "fam.txt"
            details = Path(dossier) / "det.txt"
            subprocess.run(
                [
                    sys.executable,
                    str(RACINE / "ci" / "resumer_echecs.py"),
                    str(xml),
                    str(familles),
                    "--details",
                    str(details),
                    "--exemples",
                    "40",
                ],
                check=False,
                capture_output=True,
            )
            texte_fam = familles.read_text(encoding="utf-8") if familles.exists() else ""
            texte_det = details.read_text(encoding="utf-8") if details.exists() else ""
        if not texte_det.strip():
            _publier(f"diag-det-{nom}", "AUCUN ECHEC quand le fichier est rejoue seul")
            continue
        tetes = [ligne for ligne in texte_fam.splitlines() if ligne.strip()][:6]
        lignes = [ligne.strip() for ligne in texte_det.splitlines() if ligne.strip()][:22]
        _publier(
            f"diag-det-{nom}",
            "FAMILLES : " + " ;; ".join(tetes) + " || DETAIL : " + " || ".join(lignes),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
