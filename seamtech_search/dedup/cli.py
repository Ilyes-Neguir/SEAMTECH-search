"""CLI de détection de doublons — Lot L.1.

Usage:
  python3 -m seamtech_search.dedup.cli scan [--seuil 0.55] [--dry-run] [--json]
  python3 -m seamtech_search.dedup.cli scan --appliquer          # écrit les liens

Entry point installé : ``dedup-scan`` (homogène avec ``recherche-log``).

SÛRETÉ : ``scan`` est en DRY-RUN par défaut. Il faut ``--appliquer`` pour
écrire des liens dans ``fiche_lien``. ``--dry-run`` reste accepté (et redondant)
pour que la commande documentée par le plan fonctionne telle quelle. Rien de ce
que fait ce CLI n'efface, ne fusionne ni ne change un statut : au pire, il
ajoute des LIGNES DE LIEN que la décision humaine exploitera — ou ignorera.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from seamtech_search.dedup.detection import SEUIL_PROBABLE_DEFAUT, scanner
from seamtech_search.indexer import SearchIndex


def _construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        prog="dedup-scan",
        description="Détection de doublons de fiches (propositive : aucun effacement, aucune fusion).",
    )
    sous = parseur.add_subparsers(dest="commande", required=True)

    scan = sous.add_parser("scan", help="Détecte les doublons exacts (SHA-256) et probables (titres).")
    scan.add_argument(
        "--seuil",
        type=float,
        default=SEUIL_PROBABLE_DEFAUT,
        help=f"Similarité de titre minimale pour un doublon probable (défaut {SEUIL_PROBABLE_DEFAUT}).",
    )
    scan.add_argument(
        "--dry-run",
        action="store_true",
        help="N'écrit RIEN (déjà le comportement par défaut — accepté pour lisibilité).",
    )
    scan.add_argument(
        "--appliquer",
        action="store_true",
        help="Écrit réellement les liens dans fiche_lien (par défaut : simulation seule).",
    )
    scan.add_argument("--json", action="store_true", help="Sortie JSON")
    scan.add_argument("--database-url", default=None, help="URL PostgreSQL (sinon variable d'environnement)")
    return parseur


def _afficher_texte(rapport: dict[str, Any]) -> None:
    critere = "similarité de trigrammes (pg_trgm)" if rapport["trigrammes_disponibles"] else (
        "REPLI déterministe (pg_trgm indisponible : titre identique + même client/bateau/gamme/année)"
    )
    print("=== Détection de doublons ===")
    print(f"Mode : {'SIMULATION (aucune écriture)' if rapport['dry_run'] else 'APPLICATION (écrit des liens)'}")
    print(f"Seuil doublon probable : {rapport['seuil']}")
    print(f"Critère « probable » réellement appliqué : {critere}")
    print(f"Groupes de doublons EXACTS (même empreinte SHA-256) : {rapport['groupes_exacts']}")
    print(f"Paires de doublons PROBABLES : {rapport['paires_probables']}")
    for groupe in rapport["groupes"]:
        codes = ", ".join(str(c) for c in groupe["codes"])
        print(f"  [exact] sha256 {groupe['empreinte_sha256'][:12]}… → {groupe['nb_fiches']} fiches : {codes}")
    for probable in rapport["probables"][:50]:
        motifs = " ; ".join(probable["motifs"])
        print(
            f"  [probable] fiche #{probable['id_fiche_source']} ↔ #{probable['id_fiche_cible']} "
            f"score {probable['score']} — {motifs}"
        )
    if len(rapport["probables"]) > 50:
        print(f"  … {len(rapport['probables']) - 50} paire(s) probable(s) supplémentaire(s) (voir --json)")
    print(f"Liens créés : {rapport['liens_crees']}")
    print(f"Liens déjà présents : {rapport['liens_deja_presents']}")
    print(f"Fiches concernées : {rapport['fiches_concernees']}")
    if rapport["dry_run"]:
        print("Rien n'a été écrit (simulation). Relancer avec --appliquer pour enregistrer les liens.")
    print("Rappel : ces liens sont des PROPOSITIONS. Aucune fiche n'a été fusionnée, modifiée ni supprimée.")


def main(argv: list[str] | None = None) -> int:
    args = _construire_parseur().parse_args(argv)
    if args.commande != "scan":  # pragma: no cover - argparse n'expose que scan
        return 2

    if args.appliquer and args.dry_run:
        print("--dry-run et --appliquer sont exclusifs : choisir l'un ou l'autre.", file=sys.stderr)
        return 2
    dry_run = not args.appliquer

    db_url = (
        args.database_url
        or os.environ.get("SEAMTECH_TEST_DATABASE_URL")
        or os.environ.get("SEAMTECH_DATABASE_URL")
    )
    index = SearchIndex(database_path=Path(":memory:"), database_url=db_url)
    index.initialize()
    index.run_migrations()
    try:
        rapport = scanner(index, seuil=args.seuil, dry_run=dry_run)
    finally:
        index.close()

    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2, default=str))
    else:
        _afficher_texte(rapport)
    return 0


if __name__ == "__main__":  # pragma: no cover - point d'entrée
    raise SystemExit(main())
