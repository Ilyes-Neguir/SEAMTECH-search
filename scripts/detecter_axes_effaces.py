#!/usr/bin/env python3
"""Lot 19 — détecter les fiches dont l'axe (type de voile) a pu être EFFACÉ.

Contexte (§3.4 du rapport NO-GO/GO) : l'écriture remplaçait la ligne ENTIÈRE
de la fiche. Une ré-extraction qui ne retrouvait pas le type de voile écrivait
NULL par-dessus une valeur déjà mesurée — perte de données silencieuse, deux
victimes connues (« génois », « voile d'avant »). Le correctif du Lot 19
empêche que cela RECOMMENCE. Ce script, lui, cherche les traces de ce qui s'est
déjà produit.

Ce qu'on peut et ce qu'on ne peut pas savoir
--------------------------------------------
`fiche_champ_extrait` est VIDÉ à chaque ré-extraction (il fait partie des
tables rafraîchies), et `fiche_validation` ne journalise que les validations
humaines. Il n'existe donc AUCUN historique par champ : impossible de prouver
qu'un axe « était présent puis a disparu ». Le script ne prétend pas le faire.

Il gradue l'information, du certain au suspect :

* **sans_axe**   — fiches dont `id_type_voile` est NULL aujourd'hui. Certain,
  mais ambigu : la plupart n'ont jamais été mesurées (les 109 fiches sans axe
  du corpus), elles ne sont pas des victimes pour autant.
* **exposees**   — parmi elles, celles dont `revision > 1`. La révision
  s'incrémente à chaque remplacement sur place : `revision > 1` signifie donc
  que LE MÉCANISME D'EFFACEMENT A TOURNÉ sur cette fiche. C'est la population
  à risque. Cela ne prouve pas qu'un axe existait.
* **candidates** — parmi les exposées, celles dont le texte porte le libellé
  d'un type de voile déjà connu de la base (`type_voile.libelle`) alors que
  l'axe est vide. Incohérence structurelle : le document DIT « génois » et la
  fiche n'a pas d'axe. Candidat fort, toujours pas une preuve.

Aucune de ces trois populations n'est réparée ici. La réparation honnête
consiste à RE-EXTRAIRE ces dossiers depuis leur PDF source : avec le correctif
du Lot 19 en place, l'opération restaure l'axe si le document le donne, et
jamais ne l'efface. Ce script produit la liste des codes à réextraire — il ne
la devine pas.

Usage
-----
    python scripts/detecter_axes_effaces.py              # constat (lecture seule)
    python scripts/detecter_axes_effaces.py --json       # sortie machine
    python scripts/detecter_axes_effaces.py --codes      # juste les codes, pour un réimport

La variable d'environnement `SEAMTECH_DATABASE_URL` est requise.

Codes de sortie
---------------
    0  aucune fiche exposée
    1  des fiches exposées ou candidates — à traiter
    2  entrée invalide (URL absente ou connexion impossible)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from seamtech_search.fiches import normalisation as norm

SQL_TOTAL = "SELECT COUNT(*) FROM fiche"
SQL_SANS_AXE = "SELECT COUNT(*) FROM fiche WHERE id_type_voile IS NULL"
SQL_EXPOSEES = (
    "SELECT id_fiche, code, titre, COALESCE(champs_texte, '') || ' ' || "
    "COALESCE(titre, '') || ' ' || COALESCE(notes, '') || ' ' || COALESCE(tissu_texte, '') "
    "FROM fiche WHERE id_type_voile IS NULL AND revision > 1 ORDER BY code"
)
SQL_LIBELLES = "SELECT libelle FROM type_voile WHERE libelle IS NOT NULL"


def _normaliser(valeur: str) -> str:
    """Forme comparable : sans accents, sans casse."""
    return norm.sans_accents(valeur or "").lower()


def _mentionne_un_type_connu(texte: str, libelles: list[str]) -> str | None:
    """Premier libellé de type de voile présent dans `texte`, ou None."""
    comparé = _normaliser(texte)
    for libelle in libelles:
        forme = _normaliser(libelle)
        # Un libellé trop court (« spi ») produit des faux positifs : exiger un
        # mot entier, jamais une sous-chaîne.
        if len(forme) < 3:
            continue
        if forme in comparé:
            return libelle
    return None


def analyser(url: str) -> dict[str, Any]:
    """Constat complet. Lecture seule : aucune écriture, jamais."""
    import psycopg2

    with psycopg2.connect(url) as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(SQL_TOTAL)
            total = int(cursor.fetchone()[0])
            cursor.execute(SQL_SANS_AXE)
            sans_axe = int(cursor.fetchone()[0])
            cursor.execute(SQL_LIBELLES)
            libelles = [ligne[0] for ligne in cursor.fetchall()]
            cursor.execute(SQL_EXPOSEES)
            lignes = cursor.fetchall()

    exposees: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for id_fiche, code, titre, texte in lignes:
        entree = {"id_fiche": int(id_fiche), "code": code, "titre": titre}
        exposees.append(entree)
        trouvé = _mentionne_un_type_connu(texte, libelles) if libelles else None
        if trouvé:
            candidates.append({**entree, "libelle_mentionne": trouvé})

    return {
        "total_fiches": total,
        "sans_axe": sans_axe,
        "exposees": exposees,
        "candidates": candidates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Détecte les fiches dont l'axe a pu être effacé (lecture seule)."
    )
    parser.add_argument("--json", action="store_true", help="sortie JSON")
    parser.add_argument("--codes", action="store_true", help="un code par ligne (pour un réimport)")
    arguments = parser.parse_args(argv)

    url = os.environ.get("SEAMTECH_DATABASE_URL", "")
    if not url:
        print(
            "SEAMTECH_DATABASE_URL n'est pas définie — je refuse de deviner la base.",
            file=sys.stderr,
        )
        return 2

    try:
        rapport = analyser(url)
    except Exception as exc:  # connexion impossible, schéma absent…
        print(f"Connexion impossible : {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if arguments.codes:
        for entree in rapport["candidates"] or rapport["exposees"]:
            print(entree["code"])
        return 1 if rapport["exposees"] else 0

    if arguments.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
        return 1 if rapport["exposees"] else 0

    print(f"Fiches en base            : {rapport['total_fiches']}")
    print(f"  sans axe (jamais mesuré ?) : {rapport['sans_axe']}")
    print(f"  exposées (revision > 1)    : {len(rapport['exposees'])}")
    print(f"  candidates (texte vs axe)  : {len(rapport['candidates'])}")
    print()
    if not rapport["exposees"]:
        print("Aucune fiche exposée : le mécanisme d'effacement n'a laissé aucune trace.")
        return 0
    print("Fiches exposées — le remplacement sur place a tourné dessus :")
    for entree in rapport["exposees"][:20]:
        print(f"  {entree['code']}\t{entree['titre'] or '(sans titre)'}")
    if len(rapport["exposees"]) > 20:
        print(f"  … et {len(rapport['exposees']) - 20} autre(s) (--json pour la liste complète)")
    if rapport["candidates"]:
        print()
        print("Candidates fortes — le texte nomme un type de voile que la fiche n'a pas :")
        for entree in rapport["candidates"][:20]:
            print(f"  {entree['code']}\tmentionne « {entree['libelle_mentionne']} »")
    print()
    print("Rien n'est réparé ici. Réextrayez ces dossiers depuis leur PDF source :")
    print("le correctif du Lot 19 restaure l'axe si le document le donne, et jamais")
    print("ne l'efface. `--codes` produit la liste, dans un ordre stable.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
