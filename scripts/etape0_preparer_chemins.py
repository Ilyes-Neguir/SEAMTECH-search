#!/usr/bin/env python3
"""Pré-contrôle d'un import de fiches — avant d'écrire quoi que ce soit.

Pourquoi cet outil existe : une fiche est identifiée par
``sha256(sel | chemin)``, et **le chemin n'est jamais stocké** — c'est la règle
de confidentialité, et elle est juste. Conséquence qu'on ne voit pas venir :

    un chemin erroné est IRRÉCUPÉRABLE. On ne peut pas le corriger après coup,
    puisqu'il n'existe nulle part. Et comme l'import est idempotent PAR CHEMIN,
    le même dossier importé plus tard avec son vrai chemin produira un second
    identifiant — donc DEUX fiches pour une seule, sans aucun moyen de détecter
    le doublon : le corpus ne conserve aucune empreinte de contenu.

Importer avec un chemin deviné n'est donc pas « une approximation » : c'est une
erreur qu'on ne peut plus rattraper, et qui fausse le dénominateur de A.1.

Ce script calcule les identifiants AVANT l'écriture. Il ne modifie rien.

.. code-block:: shell

    # un chemin de dossier Dropbox par ligne, dans le même ordre que les pièces
    python scripts/etape0_preparer_chemins.py chemins.txt

Il indique pour chacun s'il serait NOUVEAU (une fiche de plus) ou DÉJÀ CONNU
(mise à jour de la ligne existante — aucune double comptabilisation).

Un chemin DÉJÀ CONNU qui porte des axes mesurés est signalé « AXES PRÉSENTS »
: c'est le verdict qui manquait et qui a coûté deux fiches (§6.2 de
``ETAPE0_ACCES_CORPUS.md``). Depuis le Lot 19, la fusion conserve ces axes —
le verdict est donc une INFORMATION et non un blocage : il rappelle que ces
valeurs ont été lues dans les documents, et qu'un import qui prétendrait les
réécrire doit les fournir.

Codes de retour : 0 = prêt à importer, 1 = collision détectée à examiner.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.etape0_corpus import (  # noqa: E402
    AXES_MESURES,
    FICHIER_ACCUMULATEUR,
    charger_accumulateur,
    identifiant,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="etape0_preparer_chemins",
        description="Calcule les identifiants d'un import projeté, sans rien écrire.",
    )
    parser.add_argument(
        "chemins",
        type=Path,
        help="Fichier texte : un chemin de dossier Dropbox par ligne "
        "« <chemin> » ou « <chemin>|<racine> ».",
    )
    parser.add_argument(
        "--fichier",
        type=Path,
        default=FICHIER_ACCUMULATEUR,
        help="Accumulateur à interroger (défaut : donnees/etape0/accumulateur.json).",
    )
    options = parser.parse_args(argv)

    if not options.chemins.is_file():
        print(f"Fichier de chemins introuvable : {options.chemins}", file=sys.stderr)
        return 1

    acc = charger_accumulateur(options.fichier)
    sel = acc["sel"]
    connus = {fiche["id"]: fiche for fiche in acc["fiches"]}

    lignes = [
        ligne.strip()
        for ligne in options.chemins.read_text(encoding="utf-8").splitlines()
        if ligne.strip() and not ligne.lstrip().startswith("#")
    ]

    print(f"Accumulateur : {len(connus)} fiche(s) — {options.fichier}")
    print(f"Chemins proposés : {len(lignes)}")
    print()

    ids_vus: dict[str, int] = {}
    problemes = 0

    for rang, ligne in enumerate(lignes, start=1):
        chemin, _, racine = ligne.partition("|")
        chemin = chemin.strip()
        racine = racine.strip()
        ident = identifiant(sel, chemin)

        if ident in ids_vus:
            print(f"  {rang:>3}. COLLISION INTERNE avec la ligne {ids_vus[ident]}")
            print(f"       deux chemins donnent la même fiche : « {chemin} »")
            problemes += 1
            continue

        ids_vus[ident] = rang
        existante = connus.get(ident)
        if existante is None:
            etat = "NOUVEAU"
        else:
            etat = "DÉJÀ CONNU"
            axes = [axe for axe in AXES_MESURES if existante.get(axe)]
            if axes:
                # Verdict ajouté au Lot 19 : sans lui, rien ne signalait qu'une
                # reprise allait toucher des valeurs lues dans les documents.
                etat = "AXES PRÉSENTS"
                print(
                    f"  {rang:>3}. {etat:<13} id={ident}  axes mesurés : "
                    f"{', '.join(axes)} — conservés par la fusion (Lot 19) ; "
                    f"les réécrire suppose de les fournir."
                )
            if racine and existante.get("racine") != racine:
                print(
                    f"  {rang:>3}. DIVERGENCE : ce chemin est déjà connu sous la "
                    f"racine « {existante.get('racine')} », pas « {racine} »"
                )
                problemes += 1
        if etat != "AXES PRÉSENTS":  # déjà annoncé en détail au-dessus
            print(f"  {rang:>3}. {etat:<13} id={ident}  racine={racine or '(non précisée)'}")

    print()
    if problemes:
        print(f"{problemes} problème(s) à examiner avant d'importer.")
        return 1
    print("Aucune collision : l'import peut être rejoué sans double comptage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
