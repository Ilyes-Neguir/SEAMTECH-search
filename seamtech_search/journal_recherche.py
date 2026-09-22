"""Lot J — exploitation du journal de recherche (plan v3.0 §11.4).

`recherche_log` est écrit à chaque recherche (requête, filtres JSON, nb_resultats)
mais jamais lu avant ce lot. Ce module expose :

- `top_requetes` : les requêtes les plus fréquentes, avec période paramétrable ;
- `recherches_sans_resultat` : les requêtes qui n'ont rien remonté ;
- `rapport_journal` : agrège les deux depuis la table réelle.

Surface fine :
- fonction testable `rapport_journal(index, ...)` ;
- route `GET /recherche/journal` (enregistrée dans recherche.py) ;
- CLI `python -m seamtech_search.journal_recherche` ou via `seamtech_search.cli`.

Aucun échantillon inventé : tout est agrégé depuis la table réelle.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import AppConfig
from .indexer import SearchIndex


def _periode_clause(jours: int | None) -> tuple[str, list[Any]]:
    if jours is None:
        return "", []
    # created_at >= now() - interval
    return "WHERE created_at >= %s", [datetime.now(timezone.utc) - timedelta(days=jours)]


def top_requetes(
    index: Any,
    periode_jours: int | None = None,
    limite: int = 20,
) -> list[dict[str, Any]]:
    """Top des requêtes les plus fréquentes, agrégé depuis recherche_log.

    - periode_jours : filtre sur created_at (None = tout)
    - limite : nombre max de requêtes retournées
    Retour : [{requete, nb_occurrences, nb_sans_resultat, dernier}]
    """
    clause, params = _periode_clause(periode_jours)
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT requete,
                       count(*)::int AS nb_occurrences,
                       sum(CASE WHEN nb_resultats = 0 THEN 1 ELSE 0 END)::int AS nb_sans_resultat,
                       max(created_at) AS dernier
                FROM recherche_log
                {clause}
                GROUP BY requete
                ORDER BY nb_occurrences DESC, requete
                LIMIT %s
                """,
                (*params, limite),
            )
            rows = cur.fetchall()
    result = []
    for requete, nb_occ, nb_sans, dernier in rows:
        result.append({
            "requete": str(requete),
            "nb_occurrences": int(nb_occ),
            "nb_sans_resultat": int(nb_sans),
            "dernier": dernier.isoformat() if hasattr(dernier, "isoformat") else str(dernier) if dernier else None,
        })
    return result


def recherches_sans_resultat(
    index: Any,
    periode_jours: int | None = None,
    limite: int = 100,
) -> list[dict[str, Any]]:
    """Recherches sans résultat, agrégé depuis recherche_log.

    - periode_jours : filtre sur created_at
    - limite : max de requêtes distinctes retournées
    Retour : [{requete, nb_occurrences, dernier, exemples_filtres}]
    """
    clause, params = _periode_clause(periode_jours)
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT requete,
                       count(*)::int AS nb_occurrences,
                       max(created_at) AS dernier,
                       (array_agg(filtres ORDER BY created_at DESC))[1] AS exemple_filtres
                FROM recherche_log
                {clause + (' AND' if clause else 'WHERE')} nb_resultats = 0
                GROUP BY requete
                ORDER BY nb_occurrences DESC, requete
                LIMIT %s
                """,
                (*params, limite),
            )
            rows = cur.fetchall()
    result = []
    for requete, nb_occ, dernier, exemple_filtres in rows:
        # exemple_filtres peut être dict ou JSON string
        if isinstance(exemple_filtres, dict):
            filtres = exemple_filtres
        elif isinstance(exemple_filtres, str):
            try:
                filtres = json.loads(exemple_filtres)
            except Exception:
                filtres = {}
        else:
            filtres = {}
        result.append({
            "requete": str(requete),
            "nb_occurrences": int(nb_occ),
            "dernier": dernier.isoformat() if hasattr(dernier, "isoformat") else str(dernier) if dernier else None,
            "exemple_filtres": filtres,
        })
    return result


def rapport_journal(
    index: Any,
    periode_jours: int | None = None,
    limite_top: int = 20,
    limite_sans_resultat: int = 100,
) -> dict[str, Any]:
    """Rapport complet agrégé depuis la table réelle.

    Retour :
    {
      periode_jours,
      total_recherches,
      total_sans_resultat,
      top_requetes: [...],
      sans_resultat: [...]
    }
    """
    clause, params = _periode_clause(periode_jours)
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*)::int FROM recherche_log {clause}", params)
            total = int(cur.fetchone()[0])
            cur.execute(f"SELECT count(*)::int FROM recherche_log {clause + (' AND' if clause else 'WHERE')} nb_resultats = 0", params)
            total_sans = int(cur.fetchone()[0])

    top = top_requetes(index, periode_jours=periode_jours, limite=limite_top)
    sans = recherches_sans_resultat(index, periode_jours=periode_jours, limite=limite_sans_resultat)

    return {
        "periode_jours": periode_jours,
        "total_recherches": total,
        "total_sans_resultat": total_sans,
        "top_requetes": top,
        "sans_resultat": sans,
    }


def _build_index_from_config(config_path: str) -> SearchIndex:
    config = AppConfig.load(config_path)
    return SearchIndex(
        config.database_path,
        config.database_url,
        pool_min=config.pool_min,
        pool_max=config.pool_max,
        pool_timeout=config.pool_timeout,
        statement_timeout_ms=config.statement_timeout_ms,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="journal-recherche", description="Rapport du journal de recherche")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "config" / "config.json"))
    parser.add_argument("--jours", type=int, default=None, help="Période en jours (défaut : tout)")
    parser.add_argument("--limite-top", type=int, default=20)
    parser.add_argument("--limite-sans", type=int, default=100)
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    args = parser.parse_args(argv)

    index = _build_index_from_config(args.config)
    try:
        index.initialize()
        rapport = rapport_journal(index, periode_jours=args.jours, limite_top=args.limite_top, limite_sans_resultat=args.limite_sans)
        if args.json:
            print(json.dumps(rapport, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"Période : {args.jours or 'tout'} jours")
            print(f"Total recherches : {rapport['total_recherches']}")
            print(f"Total sans résultat : {rapport['total_sans_resultat']}")
            print("\nTop requêtes :")
            for entry in rapport["top_requetes"]:
                print(f"  {entry['nb_occurrences']:4d}x  {entry['requete']!r}  (sans résultat : {entry['nb_sans_resultat']})")
            print("\nSans résultat :")
            for entry in rapport["sans_resultat"]:
                print(f"  {entry['nb_occurrences']:4d}x  {entry['requete']!r}  filtres={entry['exemple_filtres']}")
    finally:
        index.close()


if __name__ == "__main__":
    main()
