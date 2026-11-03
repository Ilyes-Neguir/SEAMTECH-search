#!/usr/bin/env python3
"""Regroupe les échecs d'une suite pytest par CAUSE, depuis un rapport JUnit.

Pourquoi ce script existe.

Le verdict Windows doit dire **pourquoi** 314 tests échouent, pas seulement
lesquels. Deux obstacles rendent la lecture du journal inutilisable :

* avec ``-v``, pytest écrit ``tests/x.py::test FAILED`` en fin de ligne de
  progression : un ``grep '^FAILED'`` ne les voit pas ;
* la ligne de résumé court (``FAILED tests/x.py::test - message``) ne porte
  presque jamais la cause : « Regex pattern did not match. » ne dit rien de ce
  qui manque. La cause est dans la DERNIÈRE ligne « E » de la trace, qui n'est
  PAS attenante au résumé.

Un rapport JUnit (``--junitxml``) porte, pour chaque échec, l'identifiant
complet ET le texte de la trace. Ce script le lit et produit une ligne par
famille de causes, prête à être publiée :

    <nombre d'échecs>|<cause>|<trois exemples séparés par des espaces>

Usage :
    python ci/resumer_echecs.py logs/junit.xml logs/causes.txt
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

#: Lignes que pytest ajoute pour EXPLIQUER une assertion (« + where 409 = … ») :
#: elles ne sont pas la cause, elles la décortiquent. Les garder ferait publier
#: « + where 409 = … » là où « assert 409 == 200 » est la cause.
_MARQUEURS_PYTEST = ("+", "-", "?", ">", "~", "^")

#: Lignes que pytest ajoute pour ENCADRER un diff (« Full diff: », le crochet
#: fermant du diff, une accolade seule) : elles ne sont pas la cause, elles la
#: cachent. Vu au run 41551125074 : cinq échecs ne portaient comme cause que
#: « Full diff: » ou « ] », alors que l'assertion fautive était deux lignes
#: plus haut (« assert b'mauvaise-cible' == b'source-attendue' »). Les garder
#: ferait publier du décor à la place de la cause.
_LIGNES_DECOR: tuple[str, ...] = ("]", "}", ")", ",", ":", "")


def _est_decor(ligne: str) -> bool:
    return ligne in _LIGNES_DECOR or ligne.startswith("Full diff:")


def _cause(texte: str | None, message: str | None, *, longueur_max: int = 240) -> str:
    """La cause d'un échec : la DERNIÈRE ligne « E » utile de la trace, à défaut le message."""
    lignes = [ligne[2:].strip() for ligne in (texte or "").splitlines() if ligne.startswith("E ")]
    utiles = [ligne for ligne in lignes if ligne[:1] not in _MARQUEURS_PYTEST and not _est_decor(ligne)]
    brut = ""
    if utiles:
        brut = utiles[-1]
    elif lignes:
        brut = lignes[0]
    else:
        brut = (message or "").strip()
    brut = " ".join(brut.split()).replace("|", "/")
    if not brut:
        return "(aucune cause dans le rapport)"
    if len(brut) > longueur_max:
        brut = brut[:longueur_max] + "…"
    return brut


def _identifiant(cas: ET.Element) -> str:
    fichier = cas.get("file") or (cas.get("classname", "").replace(".", "/") + ".py")
    nom = cas.get("name", "?")
    return f"{fichier}::{nom}"


def _familles(chemin: Path) -> dict[str, list[str]]:
    racine = ET.parse(chemin).getroot()
    familles: dict[str, list[str]] = defaultdict(list)
    for cas in racine.iter("testcase"):
        for enfant in cas:
            if enfant.tag not in ("failure", "error"):
                continue
            cause = _cause(enfant.text, enfant.get("message"))
            familles[cause].append(_identifiant(cas))
            break  # un échec, une cause : inutile de compter deux fois
    return familles


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("rapport", type=Path, help="rapport JUnit produit par pytest")
    analyseur.add_argument("sortie", type=Path, help="fichier des familles, une par ligne")
    analyseur.add_argument(
        "--exemples", type=int, default=3,
        help="nombre d'identifiants publiés par famille (défaut 3 ; 25 pour tout lister)",
    )
    analyseur.add_argument(
        "--details", type=Path, default=None,
        help="fichier de détail : une ligne « identifiant|cause » par test échoué",
    )
    arguments = analyseur.parse_args(argv)

    if not arguments.rapport.is_file():
        print(f"rapport absent : {arguments.rapport}", file=sys.stderr)
        return 1
    familles = _familles(arguments.rapport)
    if arguments.details is not None:
        arguments.details.parent.mkdir(parents=True, exist_ok=True)
        lignes_details = sorted(
            f"{identifiant}|{cause}"
            for cause, identifiants in familles.items()
            for identifiant in identifiants
        )
        arguments.details.write_text(
            "\n".join(lignes_details) + ("\n" if lignes_details else ""), encoding="utf-8"
        )
    ordonnees = sorted(familles.items(), key=lambda couple: (-len(couple[1]), couple[0]))
    lignes = [
        f"{len(identifiants)}|{cause}|{' '.join(identifiants[: arguments.exemples])}"
        for cause, identifiants in ordonnees
    ]
    arguments.sortie.parent.mkdir(parents=True, exist_ok=True)
    arguments.sortie.write_text("\n".join(lignes) + ("\n" if lignes else ""), encoding="utf-8")
    total = sum(len(i) for _, i in ordonnees)
    print(f"{len(ordonnees)} familles pour {total} échecs → {arguments.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
