"""Tableau qualité — Lot K.1 (données réelles, GROUP BY, <100ms sur 1000 fiches)."""

import time

import pytest

from seamtech_search.qualite.tableau import (
    anomalies_frequentes,
    lots_stats,
    taux_correction_par_champ,
    taux_extraction_auto,
    temps_validation,
    usage_recherches,
    volume_par_statut,
)

pytestmark = pytest.mark.postgres


def test_qualite_sources_reelles(base_recherche):
    """Sources fiche_champ_extrait, fiche_validation, fiche_anomalie, recherche_log, lots existent."""
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            # Au moins les tables existent (migrations 006-015)
            cur.execute("SELECT COUNT(*) FROM fiche_champ_extrait")
            cur.execute("SELECT COUNT(*) FROM fiche_validation")
            cur.execute("SELECT COUNT(*) FROM fiche_anomalie")
            cur.execute("SELECT COUNT(*) FROM recherche_log")
            cur.execute("SELECT COUNT(*) FROM lot_import")
            cur.execute("SELECT COUNT(*) FROM lot_dossier")


def test_taux_extraction_auto_definition(base_recherche):
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            res = taux_extraction_auto(cur)
            assert "definition" in res
            assert "unite" in res
            assert "periode" in res
            assert "taux_auto" in res


def test_taux_correction_par_champ_group_by(base_recherche):
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            res = taux_correction_par_champ(cur)
            assert isinstance(res, list)
            # Si données présentes, tri desc par taux_correction
            if len(res) >= 2:
                assert res[0]["taux_correction"] >= res[1]["taux_correction"]


def test_temps_validation_mediane_p95(base_recherche):
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            res = temps_validation(cur)
            assert "definition" in res
            assert "mediane_s" in res
            assert "p95_s" in res
            assert "nb_fiches_validees" in res


def test_anomalies_frequentes_group_by(base_recherche):
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            res = anomalies_frequentes(cur)
            assert isinstance(res, list)


def test_volume_par_statut(base_recherche):
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            res = volume_par_statut(cur)
            assert "par_statut" in res
            assert "en_attente_validation" in res


def test_usage_recherches_split_canal(base_recherche):
    """Distinguer recherches utilisateur vs questions assistant (canal filtres->>'canal')."""
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            # Insère deux recherches : une utilisateur, une assistant
            cur.execute(
                "INSERT INTO recherche_log (requete, filtres, nb_resultats) VALUES (%s, %s, %s)",
                ("test utilisateur", '{"canal": "utilisateur"}', 5),
            )
            cur.execute(
                "INSERT INTO recherche_log (requete, filtres, nb_resultats) VALUES (%s, %s, %s)",
                ("test assistant", '{"canal": "assistant"}', 0),
            )
            res = usage_recherches(cur)
            assert "par_canal" in res
            canaux = {c["canal"] for c in res["par_canal"]}
            # Doit distinguer au moins utilisateur et assistant si présents
            assert "utilisateur" in canaux or "assistant" in canaux
            assert "definition" in res
            assert "par_jour" in res


def test_lots_stats(base_recherche):
    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            res = lots_stats(cur)
            assert "total_lots" in res
            assert "total_dossiers" in res


def test_qualite_perf_1000_fiches(base_recherche):
    """Requêtes <100ms sur 1000 fiches — mesure n+p50/p95."""
    import json
    import os
    from pathlib import Path

    index = base_recherche["index"]
    # Prépare 1000 fiches si pas déjà
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM fiche")
            nb = int(cur.fetchone()[0])
            if nb < 1000:
                # Crée 1000 fiches via bulk insert minimal
                for i in range(1000 - nb):
                    cur.execute(
                        "INSERT INTO fiche (code, titre, statut) VALUES (%s, %s, 'a_valider') ON CONFLICT (code) DO NOTHING",
                        (f"PERF_Q_{i}_{time.time()}", f"Fiche perf {i}"),
                    )
                # Ajoute champs extraits pour taux
                cur.execute("SELECT id_fiche FROM fiche WHERE code LIKE 'PERF_Q_%' LIMIT 500")
                ids = [r[0] for r in cur.fetchall()]
                for fid in ids[:500]:
                    cur.execute(
                        "INSERT INTO fiche_champ_extrait (id_fiche, champ, valeur_brute, valeur_normalisee, methode, confiance, corrige) "
                        "VALUES (%s, %s, %s, %s, 'test', 0.9, false) ON CONFLICT (id_fiche, champ, rang) DO NOTHING",
                        (fid, "fiche.code", "BRUT", "NORM"),
                    )

    # Mesure p50/p95 sur 60 runs de tableau_de_bord partiel (une requête)
    n = 60
    durees = []
    for _ in range(n):
        t0 = time.perf_counter()
        with index.connect() as conn:
            with conn.cursor() as cur:
                taux_extraction_auto(cur)
                taux_correction_par_champ(cur)
                volume_par_statut(cur)
        durees.append((time.perf_counter() - t0) * 1000)

    durees.sort()
    p50 = durees[n // 2]
    p95 = durees[int(n * 0.95)]
    max_ms = durees[-1]

    # Publie mesure pour CI (même mécanisme que perf existantes)
    perf_json = os.environ.get("SEAMTECH_PERF_JSON")
    if perf_json:
        Path(perf_json).parent.mkdir(parents=True, exist_ok=True)
        with open(perf_json, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "nom": "qualite_1000_fiches",
                        "n": n,
                        "p50_ms": p50,
                        "p95_ms": p95,
                        "max_ms": max_ms,
                        "seuil_p95_ms": 100.0,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    # Garde-fou : <100ms produit
    assert p95 < 100.0, f"qualite p95={p95:.1f}ms >=100ms sur 1000 fiches"
    assert p50 < 100.0


def test_qualite_cli_sans_reseau():
    """CLI ne fait pas d'appel réseau (RG14)."""
    from pathlib import Path

    src = Path("seamtech_search/qualite/tableau.py").read_text(encoding="utf-8")
    assert "requests" not in src
    assert "httpx" not in src
    # Pas d'appel réseau sortant
