"""CLI tableau de bord qualité — Lot K.1.

Usage:
  python -m seamtech_search.qualite.cli
  python -m seamtech_search.qualite.cli --json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from seamtech_search.indexer import SearchIndex
from seamtech_search.qualite.tableau import tableau_de_bord


def main() -> None:
    parser = argparse.ArgumentParser(description="Tableau de bord qualité (Lot K.1)")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    parser.add_argument("--database-url", default=None, help="URL PostgreSQL (sinon config)")
    args = parser.parse_args()

    # Résout index via variable d'env ou config par défaut
    import os

    db_url = args.database_url or os.environ.get("SEAMTECH_TEST_DATABASE_URL") or os.environ.get("SEAMTECH_DATABASE_URL")
    # Utilise SearchIndex avec database_url
    index = SearchIndex(database_path=Path(":memory:"), database_url=db_url)
    index.initialize()
    index.run_migrations()

    data = tableau_de_bord(index)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print("=== Tableau de bord qualité ===")
        print(f"Taux extraction auto: {data['taux_extraction_auto']['taux_auto_pct']}% "
              f"({data['taux_extraction_auto']['auto']}/{data['taux_extraction_auto']['total_champs']})")
        print(f"Temps validation médiane: {data['temps_validation']['mediane_s']}s "
              f"p95: {data['temps_validation']['p95_s']}s "
              f"n={data['temps_validation']['nb_fiches_validees']}")
        print(f"Volume par statut: {data['volume_par_statut']['par_statut']} "
              f"total={data['volume_par_statut']['total']} "
              f"en_attente={data['volume_par_statut']['en_attente_validation']}")
        print(f"Recherches 30j: total={data['usage_recherches']['total_30j']} "
              f"sans_resultat={data['usage_recherches']['sans_resultat_30j']} "
              f"({data['usage_recherches']['part_sans_resultat_pct_30j']}%)")
        print("Par canal:")
        for c in data['usage_recherches']['par_canal']:
            print(f"  {c['canal']}: {c['total']} ({c['part_sans_resultat_pct']}% sans résultat)")
        print("Top corrections par champ:")
        for entry in data['taux_correction_par_champ'][:10]:
            print(f"  {entry['champ']}: {entry['taux_correction_pct']}% corrigés ({entry['corriges']}/{entry['total']})")
        print("Anomalies fréquentes:")
        for a in data['anomalies_frequentes'][:10]:
            print(f"  {a['code']} ({a['gravite']}): {a['nb']} dont {a['a_traiter']} à traiter")
        print(f"Lots: {data['lots']['total_lots']} lots, {data['lots']['total_dossiers']} dossiers")


if __name__ == "__main__":
    main()
