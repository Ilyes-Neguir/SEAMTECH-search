#!/usr/bin/env python3
"""Bounded synthetic 10k-fiche PostgreSQL search/API benchmark (never customer data)."""
from __future__ import annotations

import json
import math
import os
import statistics
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg2.extras import execute_values

from seamtech_search.config import AppConfig
from seamtech_search.fiches.gabarits import initialiser_gabarits
from seamtech_search.indexer import SearchIndex
from seamtech_search.recherche import enregistrer_routes_recherche

N = int(os.environ.get("SCALE_BENCH_FICHES", "10000"))
SAMPLES = int(os.environ.get("SCALE_BENCH_SAMPLES", "50"))
URL = os.environ["SEAMTECH_SCALE_DATABASE_URL"]
OUTPUT = Path(os.environ.get("SCALE_BENCH_OUTPUT", "/tmp/scale-bench.json"))
observations: list[dict[str, Any]] = []
active_capture = True


class CursorProxy:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    def execute(self, query: Any, params: Any = None) -> Any:
        sql = ""
        try:
            raw = self._cursor.mogrify(query, params) if params is not None else query
            sql = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        except Exception:
            sql = str(query)
        debut = time.perf_counter()
        resultat = self._cursor.execute(query, params) if params is not None else self._cursor.execute(query)
        duree = (time.perf_counter() - debut) * 1000
        if active_capture and sql.lstrip().lower().startswith(("select", "with")):
            observations.append({"sql": sql, "ms": duree})
        return resultat

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)

    def __enter__(self) -> "CursorProxy":
        self._cursor.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        return self._cursor.__exit__(*args)


class ConnectionProxy:
    def __init__(self, connection: Any):
        self._connection = connection

    def cursor(self, *args: Any, **kwargs: Any) -> CursorProxy:
        return CursorProxy(self._connection.cursor(*args, **kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def seed(index: SearchIndex) -> None:
    """Populate only deterministic SYNTHETIC records and dimensions."""
    with index.connect() as connection:
        with connection.cursor() as cursor:
            values = [
                (
                    f"SCALE-{i:05d}",
                    f"Voile synthétique {i:05d} croisière régate dimension",
                    "Régate" if i % 2 == 0 else "Croisière",
                    "a_valider" if i % 10 == 0 else "valide",
                    date(2020 + i % 7, 1 + i % 12, 1 + i % 27),
                )
                for i in range(1, N + 1)
            ]
            returned = execute_values(
                cursor,
                "INSERT INTO fiche (code, titre, gamme, statut, date_edition) VALUES %s RETURNING id_fiche",
                values,
                page_size=1000,
                fetch=True,
            )
            ids = [int(row[0]) for row in returned]
            cotes = [
                (id_fiche, "finie", 5.0 + (i % 500) / 100, 4.0 + (i % 300) / 100, 2.0 + (i % 200) / 100)
                for i, id_fiche in enumerate(ids, start=1)
            ]
            execute_values(
                cursor,
                "INSERT INTO fiche_cotes (id_fiche, jeu, slu_m, sle_m, sf_m) VALUES %s",
                cotes,
                page_size=1000,
            )
            cursor.execute("SELECT rafraichir_texte_recherche_toutes()")
    with index.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE fiche")
            cursor.execute("ANALYZE fiche_cotes")


def percentiles(values: list[float]) -> dict[str, float]:
    sorted_values = sorted(values)
    def nearest_rank(percentile: float) -> float:
        return sorted_values[max(0, math.ceil(percentile * len(sorted_values)) - 1)]
    return {
        "p50_ms": round(statistics.median(sorted_values), 2),
        "p95_ms": round(nearest_rank(0.95), 2),
        "p99_ms": round(nearest_rank(0.99), 2),
        "max_ms": round(sorted_values[-1], 2),
    }


def main() -> int:
    if N != 10000:
        raise ValueError(f"Ce banc est défini pour exactement 10 000 fiches SYNTHÉTIQUES, pas {N}.")
    if not 50 <= SAMPLES <= 100:
        raise ValueError("SCALE_BENCH_SAMPLES doit être compris entre 50 et 100 pour respecter le budget <20 min.")
    index = SearchIndex(Path("/tmp/scale-bench-unused.db"), URL)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)
    seed(index)

    original_connect = index.connect

    @contextmanager
    def traced_connect():
        with original_connect() as connection:
            yield ConnectionProxy(connection)

    index.connect = traced_connect  # type: ignore[method-assign]
    app = FastAPI()
    config = AppConfig(root_paths=[Path("/tmp")], database_url=URL, min_free_bytes=0)
    enregistrer_routes_recherche(app, index, config, lambda *_args: None)

    cases = {
        "mot_simple": ("/recherche", {"q": "voile", "limit": "20"}),
        "multi_mots": ("/recherche", {"q": "voile croisière synthétique", "limit": "20"}),
        "code": ("/recherche", {"q": "SCALE-00001", "limit": "20"}),
        "facette_dimension": ("/recherche", {"q": "voile", "cote": "slu_m", "min": "5.5", "max": "6.5", "limit": "20"}),
        "filtres": ("/recherche", {"q": "voile", "gamme": "Régate", "annee_min": "2022", "annee_max": "2025", "limit": "20"}),
        "suggestions": ("/recherche/suggestions", {"prefix": "vo", "limit": "10"}),
    }
    summary: dict[str, Any] = {
        "label": "SYNTHÉTIQUE — CI PostgreSQL jetable; ne prouve ni VPS01 ni R2",
        "fiches": N,
        "echantillons_par_requete": SAMPLES,
        "target_p95_ms_indicatif": 250,
        "requêtes": {},
    }
    with TestClient(app) as client:
        for name, (path, params) in cases.items():
            for _ in range(5):
                response = client.get(path, params=params)
                if response.status_code != 200:
                    raise RuntimeError(f"chauffe {name}: HTTP {response.status_code}: {response.text[:500]}")
            durations: list[float] = []
            for _ in range(SAMPLES):
                debut = time.perf_counter()
                response = client.get(path, params=params)
                durations.append((time.perf_counter() - debut) * 1000)
                if response.status_code != 200:
                    raise RuntimeError(f"mesure {name}: HTTP {response.status_code}: {response.text[:500]}")
            metrics = percentiles(durations)
            summary["requêtes"][name] = metrics
            statut = "OK" if metrics["p95_ms"] < 250 else "AU-DESSUS CIBLE INDICATIVE"
            print(f"SYNTHÉTIQUE {name}: n={SAMPLES}, p50={metrics['p50_ms']:.2f} ms, p95={metrics['p95_ms']:.2f} ms, p99={metrics['p99_ms']:.2f} ms, max={metrics['max_ms']:.2f} ms — {statut}")

    # EXPLAIN the 3 slowest distinct SELECT statements actually executed by
    # these measured /recherche and /suggestions endpoint requests.
    top: dict[str, float] = {}
    for item in observations:
        sql = item["sql"].strip().rstrip(";")
        if sql and sql not in top:
            top[sql] = float(item["ms"])
        elif sql:
            top[sql] = max(top[sql], float(item["ms"]))
    slowest = sorted(top.items(), key=lambda pair: pair[1], reverse=True)[:3]
    global active_capture
    active_capture = False
    plans = []
    with index.connect() as connection:
        with connection.cursor() as cursor:
            for rank, (sql, measured_ms) in enumerate(slowest, start=1):
                cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql)
                plan = "\n".join(str(row[0]) for row in cursor.fetchall())
                plans.append({"rank": rank, "observed_execute_ms": round(measured_ms, 3), "plan": plan})
                print(f"\nEXPLAIN (ANALYZE, BUFFERS) — requête observée la plus lente #{rank}; execute={measured_ms:.3f} ms\n{plan}")
    summary["explain_top3"] = plans
    summary["sql_selects_captured"] = len(observations)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Rapport SYNTHÉTIQUE JSON : {OUTPUT}")
    index.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
