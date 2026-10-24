"""Réindexation de la recherche métier — Lot 2.

Usage opérateur (simulation par défaut) ::

    python -m seamtech_search.reindex_recherche --json
    python -m seamtech_search.reindex_recherche --appliquer --json

L'outil mesure l'état AVANT, puis — seulement avec ``--appliquer`` —
resynchronise le référentiel canonique des bateaux depuis les libellés
historiques, recopie les synonymes hérités dans ``search_synonyms`` et
rafraîchit le texte/vector de recherche de toutes les fiches par la fonction
SQL ensembliste existante. Le rapport APRES permet de prouver l'effet :
bateaux sans modèle, fiches sans vecteur, synonymes présents.

Comme les autres CLI opérateur du dépôt, le démarrage applique d'abord les
migrations en attente : une base non migrée n'est jamais mesurée comme si
elle était à jour.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from seamtech_search.indexer import SearchIndex
from seamtech_search.referentiel_bateaux import (
    normaliser_modele_bateau,
    synchroniser_referentiel_bateaux,
)


def _compter(cursor: Any, sql: str) -> int:  # noqa: ANN401
    cursor.execute(sql)
    ligne = cursor.fetchone()
    return int(ligne[0] or 0) if ligne is not None else 0


def _colonne_existe(cursor: Any, table: str, colonne: str) -> bool:  # noqa: ANN401
    cursor.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
        (table, colonne),
    )
    return cursor.fetchone() is not None


def _mesurer(cursor: Any) -> dict[str, int]:  # noqa: ANN401
    mesures = {
        "fiches_total": _compter(cursor, "SELECT count(*) FROM fiche"),
        "fiches_sans_search_vector": _compter(
            cursor,
            "SELECT count(*) FROM fiche WHERE search_vector IS NULL OR search_vector = ''::tsvector",
        ),
        "fiches_sans_champs_texte": _compter(
            cursor,
            "SELECT count(*) FROM fiche WHERE champs_texte IS NULL OR btrim(champs_texte) = ''",
        ),
        "bateaux_total": _compter(cursor, "SELECT count(*) FROM bateau"),
        "synonymes_legacy": _compter(cursor, "SELECT count(*) FROM synonyme"),
    }
    if _colonne_existe(cursor, "bateau", "boat_model_id"):
        mesures["bateaux_sans_modele"] = _compter(
            cursor, "SELECT count(*) FROM bateau WHERE boat_model_id IS NULL"
        )
        mesures["modeles_actifs"] = _compter(
            cursor, "SELECT count(*) FROM boat_models WHERE actif"
        )
        mesures["modeles_fusionnes"] = _compter(
            cursor, "SELECT count(*) FROM boat_models WHERE id_modele_fusion IS NOT NULL"
        )
        mesures["alias_bateaux"] = _compter(cursor, "SELECT count(*) FROM boat_model_aliases")
        mesures["synonymes_recherche"] = _compter(
            cursor, "SELECT count(*) FROM search_synonyms WHERE actif"
        )
    else:
        mesures["bateaux_sans_modele"] = mesures["bateaux_total"]
        mesures["modeles_actifs"] = 0
        mesures["modeles_fusionnes"] = 0
        mesures["alias_bateaux"] = 0
        mesures["synonymes_recherche"] = 0
    return mesures


def _groupes_modeles_estime(cursor: Any) -> int:  # noqa: ANN401
    cursor.execute("SELECT nom FROM bateau")
    return len(
        {
            normaliser_modele_bateau(str(ligne[0] or ""))
            for ligne in cursor.fetchall()
            if normaliser_modele_bateau(str(ligne[0] or ""))
        }
    )


def _synchroniser_synonymes(cursor: Any) -> int:  # noqa: ANN401
    cursor.execute("SELECT terme, cible, domaine FROM synonyme")
    copies = 0
    for terme, cible, domaine in cursor.fetchall():
        terme_normalise = normaliser_modele_bateau(str(terme or ""))
        cible_normalisee = normaliser_modele_bateau(str(cible or ""))
        if not terme_normalise or not cible_normalisee:
            continue
        cursor.execute(
            "INSERT INTO search_synonyms "
            "(terme, terme_normalise, cible, cible_normalisee, domaine, source) "
            "VALUES (%s, %s, %s, %s, %s, 'reindex_lot2') "
            "ON CONFLICT (terme_normalise, cible_normalisee) DO NOTHING",
            (str(terme).strip(), terme_normalise, str(cible).strip(), cible_normalisee, domaine),
        )
        copies += int(cursor.rowcount or 0)
    return copies


def rapport_reindex(index: Any, *, appliquer: bool = False) -> dict[str, Any]:  # noqa: ANN401
    """Mesure avant/après et, si demandé, applique la réindexation Lot 2."""
    if not getattr(index, "is_postgres", False):
        raise ValueError("La réindexation de la recherche métier exige PostgreSQL.")
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            avant = _mesurer(cursor)
            if appliquer:
                actions_bateaux = synchroniser_referentiel_bateaux(
                    cursor, source="reindex_lot2"
                )
                synonymes_copies = _synchroniser_synonymes(cursor)
                cursor.execute("SELECT rafraichir_texte_recherche_toutes()")
                fiches_rafraichies = int(cursor.fetchone()[0] or 0)
                actions: dict[str, int] = {
                    **actions_bateaux,
                    "synonymes_copies": synonymes_copies,
                    "fiches_rafraichies": fiches_rafraichies,
                }
            else:
                actions = {
                    "bateaux_lus": avant["bateaux_total"],
                    "bateaux_lies": 0,
                    "modeles_crees": 0,
                    "groupes_modeles_estime": _groupes_modeles_estime(cursor),
                    "synonymes_copies": 0,
                    "fiches_rafraichies": 0,
                }
            apres = _mesurer(cursor)
    return {
        "dry_run": not appliquer,
        "avant": avant,
        "apres": apres,
        "actions": actions,
    }


def _construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        prog="reindex-recherche",
        description="Réindexation Lot 2 : référentiel bateaux, synonymes et vecteurs de fiches.",
    )
    groupe = parseur.add_mutually_exclusive_group()
    groupe.add_argument(
        "--dry-run",
        action="store_true",
        help="Mesure seulement (comportement par défaut, aucune réindexation).",
    )
    groupe.add_argument(
        "--appliquer",
        action="store_true",
        help="Applique réellement la synchronisation et le rafraîchissement.",
    )
    parseur.add_argument("--json", action="store_true", help="Sortie JSON")
    parseur.add_argument(
        "--rapport",
        default=None,
        help="Écrit aussi le rapport JSON dans ce fichier (avant/après).",
    )
    parseur.add_argument("--database-url", default=None, help="URL PostgreSQL (sinon environnement)")
    return parseur


def _afficher_texte(rapport: dict[str, Any]) -> None:
    print("=== Réindexation recherche (Lot 2) ===")
    print(f"Mode : {'SIMULATION (aucune réindexation)' if rapport['dry_run'] else 'APPLICATION'}")
    for etape in ("avant", "apres"):
        mesures = rapport[etape]
        print(
            f"{etape:>5} : fiches={mesures['fiches_total']} "
            f"sans_vecteur={mesures['fiches_sans_search_vector']} "
            f"bateaux_sans_modele={mesures['bateaux_sans_modele']} "
            f"modeles_actifs={mesures['modeles_actifs']} "
            f"alias={mesures['alias_bateaux']} "
            f"synonymes={mesures['synonymes_recherche']}"
        )
    actions = rapport["actions"]
    print(
        "actions : "
        f"bateaux_lies={actions.get('bateaux_lies', 0)} "
        f"modeles_crees={actions.get('modeles_crees', 0)} "
        f"synonymes_copies={actions.get('synonymes_copies', 0)} "
        f"fiches_rafraichies={actions.get('fiches_rafraichies', 0)}"
    )
    if rapport["dry_run"]:
        print("Rien n'a été réindexé (simulation). Relancer avec --appliquer pour écrire.")


def main(argv: list[str] | None = None) -> int:
    args = _construire_parseur().parse_args(argv)
    db_url = (
        args.database_url
        or os.environ.get("SEAMTECH_TEST_DATABASE_URL")
        or os.environ.get("SEAMTECH_DATABASE_URL")
    )
    if not db_url:
        print(
            "URL PostgreSQL absente : passer --database-url ou SEAMTECH_DATABASE_URL.",
            file=sys.stderr,
        )
        return 2
    index = SearchIndex(database_path=Path(":memory:"), database_url=db_url)
    index.initialize()
    index.run_migrations()
    try:
        rapport = rapport_reindex(index, appliquer=args.appliquer)
    finally:
        index.close()
    if args.rapport:
        Path(args.rapport).write_text(
            json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    else:
        _afficher_texte(rapport)
    return 0


if __name__ == "__main__":  # pragma: no cover - point d'entrée
    raise SystemExit(main())
