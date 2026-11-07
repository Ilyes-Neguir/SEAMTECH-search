#!/usr/bin/env python3
"""Étape 0 — constituer une base de fiches RÉELLE et reproductible.

Le corpus de l'atelier vit dans Dropbox. Aucun transfert en masse n'est
possible vers l'environnement de travail (voir ``docs/ETAPE0_ACCES_CORPUS.md``) :
les hôtes ``dl.dropboxusercontent.com`` et ``objects.githubusercontent.com`` y
sont injoignables, seuls ``github.com`` / ``codeload.github.com`` répondent.
Le corpus est donc relevé dossier par dossier, via le connecteur, et chaque
mesure est consignée ici.

Ce module est la mémoire de ce relevé. Deux règles le gouvernent :

1. **Rien de nominatif n'entre dans le dépôt.** Le chemin Dropbox d'un dossier
   sert uniquement à fabriquer un identifiant haché ; il n'est jamais écrit.
   Seuls subsistent l'année, la famille, la provenance et les valeurs d'axes —
   le vocabulaire générique de l'atelier, pas le nom d'un client.

2. **La base se reconstruit depuis l'accumulateur, jamais l'inverse.** Une
   réinitialisation de l'environnement efface PostgreSQL ; elle n'efface pas ce
   fichier, qui est versionné. ``charger`` rejoue tout, à l'identique.

Sous-commandes :

    stats                     état du relevé, par racine
    ajouter                   consigner une fiche mesurée (idempotent)
    charger                   reconstruire la base et prouver le compte
    mesurer                   lancer l'instrument 1.1 (table A.1)

Exemple :

    python3 scripts/etape0_corpus.py ajouter \
        --chemin "/VOILES 2024/UN DOSSIER" \
        --racine "VOILES 2024" --source dropbox \
        --gamme "C0" --type-voile "Grand-voile" --matiere "Dacron"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from collections import Counter
from pathlib import Path
from typing import Any

#: Racine du dépôt, déduite de la position de ce fichier.
RACINE_DEPOT = Path(__file__).resolve().parent.parent

#: Fichier versionné qui conserve le relevé entre les sessions.
FICHIER_ACCUMULATEUR = RACINE_DEPOT / "donnees" / "etape0" / "accumulateur.json"

VERSION_ACCUMULATEUR = 1

#: Marqueur qui distingue les lignes écrites par cet outil de celles écrites
#: par le pipeline d'extraction. Indispensable : ``charger`` ne doit jamais
#: effacer une extraction réelle sous prétexte de faire le ménage.
MARQUEUR_CLE = "etape0:"

#: Familles et années admises dans une racine, par exemple ``VOILES 2024``.
_MOTIF_RACINE = re.compile(r"^(VOILES|ACCESSOIRES)\s+(\d{4})$")

#: Valeurs admises pour la catégorie (contrainte CHECK de la table ``fiche``).
CATEGORIES_ADMISES = ("voile", "accessoire")

#: Provenances admises.
SOURCES_ADMISES = ("dropbox", "archive")


# ---------------------------------------------------------------------------
# Accumulateur : lecture, écriture, identifiants
# ---------------------------------------------------------------------------


def nouvel_accumulateur() -> dict[str, Any]:
    """Un accumulateur vide, avec un sel tiré une seule fois.

    Le sel rend les identifiants incomparables avec un autre jeu de données,
    ce qui interdit de recouper deux corpus par leurs empreintes.
    """
    return {
        "version": VERSION_ACCUMULATEUR,
        "sel": secrets.token_hex(16),
        "fiches": [],
    }


def charger_accumulateur(chemin: Path = FICHIER_ACCUMULATEUR) -> dict[str, Any]:
    """Lire l'accumulateur ; le créer vide s'il n'existe pas encore."""
    if not chemin.exists():
        return nouvel_accumulateur()
    with chemin.open(encoding="utf-8") as flux:
        acc = json.load(flux)
    if int(acc.get("version", 0)) != VERSION_ACCUMULATEUR:
        raise ValueError(
            f"Version d'accumulateur inattendue : {acc.get('version')!r} "
            f"(attendue : {VERSION_ACCUMULATEUR})."
        )
    acc.setdefault("fiches", [])
    return acc


def sauvegarder_accumulateur(acc: dict[str, Any], chemin: Path = FICHIER_ACCUMULATEUR) -> None:
    """Écrire l'accumulateur de façon stable et relisible."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    acc = dict(acc)
    acc["fiches"] = sorted(acc["fiches"], key=lambda f: f["id"])
    with chemin.open("w", encoding="utf-8") as flux:
        json.dump(acc, flux, ensure_ascii=False, indent=2, sort_keys=True)
        flux.write("\n")


def identifiant(sel: str, chemin: str) -> str:
    """L'identifiant d'un dossier : une empreinte, jamais le chemin lui-même."""
    return hashlib.sha256(f"{sel}|{chemin}".encode("utf-8")).hexdigest()[:16]


def decouper_racine(racine: str) -> tuple[str, int]:
    """``"VOILES 2024"`` → ``("voiles", 2024)``.

    Une racine inconnue est refusée : mieux vaut échouer que d'introduire une
    année ou une famille qui n'a jamais existé.
    """
    m = _MOTIF_RACINE.match(racine.strip())
    if not m:
        raise ValueError(
            f"Racine inattendue : {racine!r}. Attendu : « VOILES <année> » "
            f"ou « ACCESSOIRES <année> »."
        )
    return m.group(1).lower(), int(m.group(2))


def categorie_de_racine(famille: str) -> str:
    """La famille porte la catégorie : ``voiles`` → ``voile``."""
    return "voile" if famille == "voiles" else "accessoire"


# ---------------------------------------------------------------------------
# Consignation
# ---------------------------------------------------------------------------


def ajouter_fiche(
    acc: dict[str, Any],
    *,
    chemin: str,
    racine: str,
    source: str,
    gamme: str | None = None,
    type_voile: str | None = None,
    matiere: str | None = None,
    categorie: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Consigner une fiche mesurée. Rend ``(accumulateur, création)``.

    L'appel est idempotent : rejouer le même chemin met à jour la ligne
    existante au lieu d'en créer une seconde. C'est ce qui permet de reprendre
    un relevé interrompu sans jamais double-compter.
    """
    if source not in SOURCES_ADMISES:
        raise ValueError(f"Source inattendue : {source!r}. Attendues : {list(SOURCES_ADMISES)}.")

    famille, annee = decouper_racine(racine)
    categorie_retenue = categorie or categorie_de_racine(famille)
    if categorie_retenue not in CATEGORIES_ADMISES:
        raise ValueError(
            f"Catégorie inattendue : {categorie_retenue!r}. Attendues : {list(CATEGORIES_ADMISES)}."
        )

    def _propre(valeur: str | None) -> str | None:
        if valeur is None:
            return None
        valeur = valeur.strip()
        return valeur or None

    fiche = {
        "id": identifiant(acc["sel"], chemin),
        "racine": racine.strip(),
        "famille": famille,
        "annee": annee,
        "source": source,
        "gamme": _propre(gamme),
        "type_voile": _propre(type_voile),
        "matiere": _propre(matiere),
        "categorie": categorie_retenue,
    }

    for rang, existante in enumerate(acc["fiches"]):
        if existante["id"] == fiche["id"]:
            acc["fiches"][rang] = fiche
            return acc, False

    acc["fiches"].append(fiche)
    return acc, True


def statistiques(acc: dict[str, Any]) -> dict[str, Any]:
    """Le relevé, agrégé : total, par racine, par provenance, par axe renseigné."""
    fiches = acc["fiches"]
    par_racine = Counter(f["racine"] for f in fiches)
    par_source = Counter(f["source"] for f in fiches)
    axes = ("gamme", "type_voile", "matiere")
    renseignes = {axe: sum(1 for f in fiches if f.get(axe)) for axe in axes}
    return {
        "total": len(fiches),
        "par_racine": dict(sorted(par_racine.items())),
        "par_source": dict(sorted(par_source.items())),
        "axes_renseignes": renseignes,
    }


# ---------------------------------------------------------------------------
# Dépôt en base
# ---------------------------------------------------------------------------


def deposer(acc: dict[str, Any], index: Any) -> dict[str, int]:  # noqa: ANN401 — indexeur réel
    """Reconstruire la base depuis l'accumulateur. Rend les comptes obtenus.

    Seules les lignes marquées ``etape0:`` sont supprimées puis réécrites : une
    extraction réelle déjà présente est conservée.
    """
    with index.connect() as connexion:
        with connexion.cursor() as cur:
            cur.execute("DELETE FROM sail_extractions WHERE cle_idempotence LIKE %s", (MARQUEUR_CLE + "%",))
            cur.execute("DELETE FROM fiche WHERE code LIKE %s", (MARQUEUR_CLE.replace(":", "-") + "%",))

            nb_fiches = 0
            nb_extractions = 0
            for fiche in sorted(acc["fiches"], key=lambda f: f["id"]):
                code = f'{MARQUEUR_CLE.replace(":", "-")}{fiche["id"]}'
                cur.execute(
                    """
                    INSERT INTO fiche (code, titre, categorie, categorie_source, statut)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id_fiche
                    """,
                    (code, fiche["racine"], fiche["categorie"], "dossier", "importe"),
                )
                id_fiche = cur.fetchone()[0]
                nb_fiches += 1

                materiaux = []
                if fiche.get("matiere"):
                    materiaux = [{"designation": fiche["matiere"]}]

                cur.execute(
                    """
                    INSERT INTO sail_extractions (
                        cle_idempotence, id_fiche, version_extracteur, methode_principale,
                        type_voile, gamme, materiaux, champs, anomalies,
                        statut_extraction, statut_plausibilite, statut_liaison
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        f'{MARQUEUR_CLE}{fiche["id"]}',
                        id_fiche,
                        "etape0-v1",
                        "inconnue",
                        fiche.get("type_voile"),
                        fiche.get("gamme"),
                        json.dumps(materiaux, ensure_ascii=False),
                        json.dumps([], ensure_ascii=False),
                        json.dumps([], ensure_ascii=False),
                        "partielle",
                        "indeterminee",
                        "sans_modele",
                    ),
                )
                nb_extractions += 1

        connexion.commit()

    with index.connect() as connexion:
        with connexion.cursor() as cur:
            cur.execute("SELECT count(*) FROM fiche")
            total_fiche = int(cur.fetchone()[0])
            cur.execute("SELECT count(*) FROM sail_extractions")
            total_extraction = int(cur.fetchone()[0])

    return {
        "fiches_ecrites": nb_fiches,
        "extractions_ecrites": nb_extractions,
        "fiche_total": total_fiche,
        "sail_extractions_total": total_extraction,
    }


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------


def _construire_index(base: str) -> Any:  # noqa: ANN401
    """Ouvrir l'indexeur sur la base demandée."""
    sys.path.insert(0, str(RACINE_DEPOT))
    from seamtech_search.indexer import SearchIndex  # import local : dépend de la base

    return SearchIndex(Path(os.environ.get("SEAMTECH_INDEX_PATH", "/tmp/etape0-index.db")), base)


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée de la ligne de commande."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fichier",
        type=Path,
        default=FICHIER_ACCUMULATEUR,
        help="Chemin de l'accumulateur (défaut : donnees/etape0/accumulateur.json).",
    )
    sous = parser.add_subparsers(dest="commande", required=True)

    sous.add_parser("stats", help="État du relevé.")

    p_ajouter = sous.add_parser("ajouter", help="Consigner une fiche mesurée.")
    p_ajouter.add_argument("--chemin", required=True, help="Chemin Dropbox du dossier (jamais stocké).")
    p_ajouter.add_argument("--racine", required=True, help="Racine, par exemple « VOILES 2024 ».")
    p_ajouter.add_argument("--source", required=True, choices=list(SOURCES_ADMISES))
    p_ajouter.add_argument("--gamme", default=None)
    p_ajouter.add_argument("--type-voile", default=None, dest="type_voile")
    p_ajouter.add_argument("--matiere", default=None)
    p_ajouter.add_argument("--categorie", default=None, choices=list(CATEGORIES_ADMISES))

    sous.add_parser("charger", help="Reconstruire la base depuis l'accumulateur.")
    sous.add_parser("mesurer", help="Lancer l'instrument 1.1 sur la base chargée.")

    options = parser.parse_args(argv)

    if options.commande == "stats":
        acc = charger_accumulateur(options.fichier)
        print(json.dumps(statistiques(acc), ensure_ascii=False, indent=2))
        return 0

    if options.commande == "ajouter":
        acc = charger_accumulateur(options.fichier)
        acc, cree = ajouter_fiche(
            acc,
            chemin=options.chemin,
            racine=options.racine,
            source=options.source,
            gamme=options.gamme,
            type_voile=options.type_voile,
            matiere=options.matiere,
            categorie=options.categorie,
        )
        sauvegarder_accumulateur(acc, options.fichier)
        stats = statistiques(acc)
        print(
            f"{'créée' if cree else 'mise à jour'} : "
            f"{identifiant(acc['sel'], options.chemin)} — total {stats['total']} fiche(s)"
        )
        return 0

    if options.commande == "charger":
        acc = charger_accumulateur(options.fichier)
        base = os.environ.get("SEAMTECH_DATABASE_URL")
        if not base:
            print(
                "SEAMTECH_DATABASE_URL n'est pas définie : je ne peux pas charger.\n"
                "Variable attendue, par exemple :\n"
                "  postgresql://postgres:@/atelier_reel?host=/home/user/.pgdata",
                file=sys.stderr,
            )
            return 2
        index = _construire_index(base)
        try:
            comptes = deposer(acc, index)
        finally:
            index.close()
        print(json.dumps(comptes, ensure_ascii=False, indent=2))
        return 0

    if options.commande == "mesurer":
        sys.path.insert(0, str(RACINE_DEPOT / "scripts"))
        from mesurer_correspondances import main as mesurer_main

        return int(mesurer_main([]) or 0)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
