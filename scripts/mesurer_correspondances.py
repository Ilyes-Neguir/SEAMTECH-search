#!/usr/bin/env python3
"""Lot 1.1 — table de correspondance PROPOSÉE, à partir de la mesure du corpus.

Quatre axes : **gamme**, **matière**, **type de voile**, **catégorie**.
L'objectif du Lot 1.1 est « une seule écriture » par notion ; ce script produit
la table qui montre, pour chaque axe, CE QUI SERAIT FUSIONNÉ et avec quels
comptes. Il N'ÉCRIT RIEN en base : il mesure et propose.

Deux natures de lignes, et elles n'ont pas le même statut :

* **certaines** — une même clé normalisée écrite de plusieurs façons
  (« Medium Ocean » / « medium ocean »). L'écriture retenue est la plus
  fréquente. C'est une PROPOSITION soumise à validation, jamais une
  application silencieuse.
* **probables** — une valeur entièrement contenue dans une autre (« ocean »
  dans « medium ocean »). Fusionner serait un pari : est-ce la même notion ou
  deux notions distinctes ? La machine ne le sait pas. ``confirmee_par`` reste
  ``None`` : SEULE L'EXPLOITANTE TRANCHE.

Ce script s'appuie sur ``seamtech_search.qualite.cartographie`` — il ne
redéveloppe ni la normalisation, ni le regroupement.

Usage ::

    export SEAMTECH_DATABASE_URL=postgresql://…   # base de l'atelier
    python scripts/mesurer_correspondances.py
    python scripts/mesurer_correspondances.py --axe gamme --json

Compétences mobilisées : ``search-first`` (l'outillage existait, il est
réutilisé), ``verification-loop`` (sortie opposable, chiffrée, reproductible).

Aucune donnée nominative ne sort de ce script : uniquement des écritures
génériques et des comptes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seamtech_search.qualite.cartographie import (  # noqa: E402
    cle_normalisation,
    lignes_categorie,
    lignes_matiere,
    lignes_valeurs,
    regrouper_variantes,
    variantes_probables,
)

#: Les quatre axes du Lot 1.1, dans l'ordre du plan.
AXES: tuple[str, ...] = ("gamme", "matiere", "type_voile", "categorie")


def ecriture_retenue(formes: Sequence[dict[str, Any]]) -> str:
    """L'écriture retenue : la PLUS FRÉQUENTE du groupe.

    Choix mécanique et révisable — c'est exactement pour ça que la table est
    soumise à validation. ``regrouper_variantes`` trie déjà les formes par
    nombre d'occurrences décroissant.
    """
    return str(formes[0]["valeur"])


def proposer_table(lignes: Sequence[tuple[object, int]]) -> dict[str, Any]:
    """Construit la table de correspondance PROPOSÉE d'un axe.

    Entrée : ``(valeur brute, nombre d'occurrences)``, telle que la rendent les
    lecteurs de :mod:`~seamtech_search.qualite.cartographie`. Sortie : des
    comptes et des propositions. Aucune écriture.
    """
    valeurs = [(valeur, int(nb or 0)) for valeur, nb in lignes if str(valeur or "").strip()]
    certaines = [
        {
            "cle": groupe["cle"],
            "ecriture_retenue": ecriture_retenue(groupe["formes"]),
            "ecritures_a_remplacer": [forme["valeur"] for forme in groupe["formes"][1:]],
            "nb_formes": groupe["nb_formes"],
            "nb_fiches": groupe["total"],
            "decision": "a_valider",
        }
        for groupe in regrouper_variantes(valeurs)
    ]
    probables = [
        {
            "valeur_courte": candidat["cle_courte"],
            "valeur_longue": candidat["cle_longue"],
            "nb_court": candidat["nb_court"],
            "nb_long": candidat["nb_long"],
            "motif": candidat["motif"],
            "confirmee_par": None,
            "decision": "fusion_ou_non_a_trancher_par_l_exploitante",
        }
        for candidat in variantes_probables(valeurs)
    ]
    return {
        "nb_valeurs_distinctes": len({cle_normalisation(valeur) for valeur, _ in valeurs}),
        "nb_ecritures_distinctes": len({str(valeur) for valeur, _ in valeurs}),
        "nb_fiches_mesurees": sum(nb for _, nb in valeurs),
        "certaines": certaines,
        "probables": probables,
    }


def lire_axe(cursor: Any, axe: str) -> list[tuple[object, int]]:  # noqa: ANN401 — curseur psycopg2 réel
    """Les lignes brutes d'un axe, lues par la boîte à outils existante."""
    if axe == "gamme":
        return lignes_valeurs(cursor, "gamme")
    if axe == "type_voile":
        return lignes_valeurs(cursor, "type_voile")
    if axe == "matiere":
        return lignes_matiere(cursor)
    if axe == "categorie":
        return lignes_categorie(cursor)
    raise ValueError(f"Axe inconnu : {axe!r}. Attendus : {list(AXES)}.")


def mesurer(index: Any, axes: Sequence[str] = AXES) -> dict[str, Any]:  # noqa: ANN401 — index réel
    """Mesure les axes demandés. Lecture seule, une seule connexion."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            return {axe: proposer_table(lire_axe(cursor, axe)) for axe in axes}


# ---------------------------------------------------------------------------
# Sortie lisible
# ---------------------------------------------------------------------------


def _titre(axe: str) -> str:
    return {"type_voile": "TYPE DE VOILE", "matiere": "MATIÈRE"}.get(axe, axe.upper())


def formater(rapport: dict[str, Any]) -> str:
    """Rend la table lisible par un humain — celle qu'on valide ligne à ligne."""
    lignes: list[str] = []
    for axe, table in rapport.items():
        lignes.append("")
        lignes.append(f"=== {_titre(axe)} ===")
        lignes.append(
            f"  {table['nb_fiches_mesurees']} fiche(s) mesurée(s), "
            f"{table['nb_ecritures_distinctes']} écriture(s) distincte(s), "
            f"{table['nb_valeurs_distinctes']} valeur(s) distincte(s) après normalisation."
        )
        certaines = table["certaines"]
        lignes.append(f"  Fusions CERTAINES (même valeur, écritures multiples) : {len(certaines)}")
        for groupe in certaines:
            remplacements = ", ".join(f"« {valeur} »" for valeur in groupe["ecritures_a_remplacer"])
            lignes.append(
                f"    · {groupe['nb_fiches']:>4} fiche(s) → écriture retenue « {groupe['ecriture_retenue']} » ; "
                f"remplace {remplacements}"
            )
        probables = table["probables"]
        lignes.append(f"  Fusions PROBABLES (à trancher par l'exploitante) : {len(probables)}")
        for candidat in probables:
            lignes.append(
                f"    · « {candidat['valeur_courte']} » ({candidat['nb_court']}) dans "
                f"« {candidat['valeur_longue']} » ({candidat['nb_long']}) — "
                "même notion ou deux notions ?"
            )
        if not certaines and not probables:
            lignes.append("    (aucune fusion proposée : l'axe est déjà à écriture unique)")
    lignes.append("")
    lignes.append("Aucune écriture en base n'a eu lieu : cette sortie est une PROPOSITION.")
    return "\n".join(lignes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lot 1.1 — table de correspondance proposée (gamme, matière, type de voile, catégorie)."
    )
    parser.add_argument(
        "--axe",
        action="append",
        choices=list(AXES),
        help="limiter la mesure à un axe (répétable) ; défaut : les quatre",
    )
    parser.add_argument("--json", action="store_true", help="sortie JSON brute")
    options = parser.parse_args(argv)

    url = os.environ.get("SEAMTECH_DATABASE_URL", "").strip()
    if not url:
        raise SystemExit(
            "SEAMTECH_DATABASE_URL est requis (base PostgreSQL de l'atelier).\n"
            "Ce script ne devine rien : sans le corpus, il n'y a pas de mesure."
        )

    from seamtech_search.indexer import SearchIndex

    index = SearchIndex(Path("./data/search.db"), url)
    try:
        rapport = mesurer(index, tuple(options.axe) if options.axe else AXES)
    finally:
        index.close()

    if options.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    else:
        print(formater(rapport))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
