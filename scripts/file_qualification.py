#!/usr/bin/env python3
"""File de qualification manuelle des axes manquants.

Au Lot 19 on a établi que les 109 fiches sans axe ne sont PAS un bug :
``ajouter`` n'ouvre aucun PDF, les axes sont saisis par un humain, et personne
n'a ouvert ces documents. Aucun correctif automatique n'existe — l'arborescence
ne dit pas le type de voile, il faut lire la fiche.

Ce script ne lit donc aucun PDF. Il fait le nécessaire pour que le travail
HUMAIN soit possible et traçable :

* **montrer** — la file : les fiches incomplètes, ce qui leur manque, et de
  quoi les retrouver dans Dropbox ;
* **saisir** — enregistrer un axe, avec qui, quand, et pourquoi ;
* **journal** — relire ce qui a été fait.

Toute saisie laisse une trace dans ``donnees/etape0/qualifications.json``,
avec la valeur d'avant : c'est la même exigence que pour les auto-validations.
Rien n'est déduit, rien n'est estimé — une valeur devinée entrerait dans le
dénominateur du taux d'appariement et le fausserait durablement.

**Une limite qu'il faut regarder en face.** On ne peut pas donner un lien
direct vers le PDF. Le chemin n'est jamais stocké : l'identifiant est
``sha256(sel|chemin)``, et c'est un choix de confidentialité assumé — un chemin
nomme un client et un bateau. La file donne donc de quoi RETROUVER le dossier
(racine, millésime, provenance), pas de quoi l'ouvrir d'un clic. C'est le prix
de la confidentialité, et il est préférable à l'alternative.

::

    python scripts/file_qualification.py montrer [--racine "VOILES 2024"] [--limite 20]
    python scripts/file_qualification.py saisir --id <id> --axe type_voile \\
        --valeur "Génois" --operateur "lea"
    python scripts/file_qualification.py journal
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE_DEPOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE_DEPOT))

from scripts.etape0_corpus import (  # noqa: E402
    AXES_MESURES,
    FICHIER_ACCUMULATEUR,
    assainir_valeur_axe,
    charger_accumulateur,
    fiches_a_qualifier,
    sauvegarder_accumulateur,
)

JOURNAL = RACINE_DEPOT / "donnees" / "etape0" / "qualifications.json"

#: Étiquettes lisibles, pour que la file parle le langage de l'atelier.
ETIQUETTES = {
    "gamme": "gamme",
    "type_voile": "type de voile",
    "matiere": "matière",
}


# ---------------------------------------------------------------------------
# Le journal
# ---------------------------------------------------------------------------


def charger_journal(chemin: Path = JOURNAL) -> dict:
    if chemin.exists():
        return json.loads(chemin.read_text(encoding="utf-8"))
    return {"version": 1, "saisies": []}


def _ecrire_journal(journal: dict, chemin: Path = JOURNAL) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(journal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# La saisie
# ---------------------------------------------------------------------------


def saisir(
    *,
    id_fiche: str,
    axe: str,
    valeur: str,
    operateur: str,
    motif: str = "",
    chemin_corpus: Path = FICHIER_ACCUMULATEUR,
    chemin_journal: Path = JOURNAL,
) -> dict:
    """Consigne un axe lu dans les documents. Rend l'entrée du journal."""
    if axe not in AXES_MESURES:
        raise ValueError(f"Axe inattendu : {axe!r}. Attendus : {list(AXES_MESURES)}.")
    if not operateur.strip():
        raise ValueError("Le nom de l'opérateur est requis : une saisie sans auteur n'est pas une trace.")

    propre = assainir_valeur_axe(axe, valeur.strip())
    if not propre:
        raise ValueError(
            "Valeur vide : on ne consigne pas un axe vide. Pour signaler « je ne sais pas », ne rien saisir."
        )

    acc = charger_accumulateur(chemin_corpus)
    fiche = next((f for f in acc["fiches"] if f["id"] == id_fiche), None)
    if fiche is None:
        raise ValueError(f"Aucune fiche {id_fiche} dans le corpus.")

    avant = fiche.get(axe)
    if avant == propre:
        raise ValueError(f"La fiche porte déjà {axe}={avant!r} : saisie sans effet, journal non écrit.")

    fiche[axe] = propre
    sauvegarder_accumulateur(acc, chemin_corpus)

    entree = {
        "id": id_fiche,
        "axe": axe,
        "avant": avant,
        "apres": propre,
        "operateur": operateur.strip(),
        "motif": motif.strip(),
        "horodatage": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "racine": fiche.get("racine"),
    }
    journal = charger_journal(chemin_journal)
    journal["saisies"].append(entree)
    _ecrire_journal(journal, chemin_journal)
    return entree


# ---------------------------------------------------------------------------
# L'affichage de la file
# ---------------------------------------------------------------------------


def _ou_trouver(fiche: dict) -> str:
    """De quoi retrouver le dossier, sans jamais donner le chemin."""
    return f"Dropbox / {fiche.get('racine')} — chercher par millésime {fiche.get('annee')}"


def formater_file(fiches: list[dict], *, limite: int | None = None) -> str:
    lignes: list[str] = []
    if not fiches:
        return "La file est vide : toutes les fiches ont leurs axes mesurés."

    par_racine: dict[str, int] = {}
    for fiche in fiches:
        cle = str(fiche.get("racine"))
        par_racine[cle] = par_racine.get(cle, 0) + 1

    lignes.append(f"{len(fiches)} fiche(s) à qualifier")
    lignes.append("")
    lignes.append("Par racine :")
    for racine, nombre in sorted(par_racine.items()):
        lignes.append(f"  {racine:<16} {nombre}")
    lignes.append("")
    lignes.append(
        "Aucun lien direct vers le PDF : le chemin n'est pas stocké, par choix de "
        "confidentialité (il nomme un client et un bateau)."
    )
    lignes.append("On retrouve le dossier par sa racine et son millésime.")
    lignes.append("")

    montrees = fiches if limite is None else fiches[:limite]
    for rang, fiche in enumerate(montrees, start=1):
        manquants = ", ".join(ETIQUETTES.get(a, a) for a in fiche["axes_manquants"])
        presents = ", ".join(ETIQUETTES.get(a, a) for a in fiche["axes_mesures"])
        lignes.append(f"  {rang:>3}. id={fiche['id']}")
        lignes.append(f"       manque    : {manquants}")
        if presents:
            lignes.append(f"       déjà mesuré : {presents}")
        lignes.append(f"       où trouver  : {_ou_trouver(fiche)}")
    if limite is not None and len(fiches) > limite:
        lignes.append(f"  … et {len(fiches) - limite} autre(s) (--limite pour en voir plus)")
    return "\n".join(lignes)


def formater_journal(saisies: list[dict]) -> str:
    if not saisies:
        return "Aucune saisie consignée."
    lignes = [f"{len(saisies)} saisie(s) consignée(s)", ""]
    for s in saisies:
        lignes.append(
            f"  {s['horodatage']}  {s['operateur']:<14} id={s['id']}  "
            f"{ETIQUETTES.get(s['axe'], s['axe'])} : {s['avant']!r} → {s['apres']!r}"
        )
        if s.get("motif"):
            lignes.append(f"       motif : {s['motif']}")
    return "\n".join(lignes)


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=Path, default=FICHIER_ACCUMULATEUR)
    parser.add_argument("--journal", type=Path, default=JOURNAL)
    sous = parser.add_subparsers(dest="commande", required=True)

    # Options communes, acceptées AVANT ou APRÈS la sous-commande : imposer
    # l'ordre surprendrait l'opérateur, qui écrit naturellement
    # « saisir --id … --corpus … ».
    commun = argparse.ArgumentParser(add_help=False)
    # SUPPRESS : sans lui, la valeur par défaut de la sous-commande ÉCRASERAIT
    # celle que l'opérateur aurait donnée avant elle. Avec lui, les deux ordres
    # fonctionnent et le dernier mot reste à l'option explicitement fournie.
    commun.add_argument("--corpus", type=Path, default=argparse.SUPPRESS)
    commun.add_argument("--journal", type=Path, default=argparse.SUPPRESS)

    p_montrer = sous.add_parser("montrer", parents=[commun], help="la file des fiches à qualifier")
    p_montrer.add_argument("--racine", default=None)
    p_montrer.add_argument("--limite", type=int, default=15)

    p_saisir = sous.add_parser("saisir", parents=[commun], help="consigner un axe lu dans les documents")
    p_saisir.add_argument("--id", required=True, dest="id_fiche")
    p_saisir.add_argument("--axe", required=True, choices=list(AXES_MESURES))
    p_saisir.add_argument("--valeur", required=True)
    p_saisir.add_argument("--operateur", required=True)
    p_saisir.add_argument("--motif", default="")

    p_journal = sous.add_parser("journal", parents=[commun], help="relire les saisies")
    p_journal.add_argument("--id", default=None, dest="id_fiche")

    options = parser.parse_args(argv)

    if options.commande == "montrer":
        acc = charger_accumulateur(options.corpus)
        fiches = fiches_a_qualifier(acc)
        if options.racine:
            fiches = [f for f in fiches if f.get("racine") == options.racine]
        print(formater_file(fiches, limite=options.limite))
        return 1 if fiches else 0

    if options.commande == "saisir":
        try:
            entree = saisir(
                id_fiche=options.id_fiche,
                axe=options.axe,
                valeur=options.valeur,
                operateur=options.operateur,
                motif=options.motif,
                chemin_corpus=options.corpus,
                chemin_journal=options.journal,
            )
        except ValueError as exc:
            print(f"Refusé : {exc}", file=sys.stderr)
            return 2
        print(
            f"consigné : id={entree['id']}  {ETIQUETTES.get(entree['axe'], entree['axe'])}"
            f" : {entree['avant']!r} → {entree['apres']!r}"
        )
        print(f"  par {entree['operateur']} — {entree['horodatage']}")
        return 0

    saisies = charger_journal(options.journal)["saisies"]
    if options.id_fiche:
        saisies = [s for s in saisies if s["id"] == options.id_fiche]
    print(formater_journal(saisies))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
