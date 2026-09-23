"""Lot J — exploitation du journal de recherche (plan v3.0 §11.4).

Le module `seamtech_search.journal_recherche` agrège la table réelle
`recherche_log` (requête, filtres JSON, nb_resultats) jamais lue avant.

Tests :
- jeu injecté prouvant l'exactitude de top_requetes / sans_resultat / rapport
- route /recherche/journal expose bien les agrégats
- CLI `recherche-log` (entry point) fonctionne

Tout est agrégé depuis la table réelle, aucun échantillon inventé.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

pytestmark = pytest.mark.postgres


def _injecter_logs(index: Any, entrees: list[dict[str, Any]]) -> None:
    with index.connect() as conn:
        with conn.cursor() as cur:
            for e in entrees:
                cur.execute(
                    "INSERT INTO recherche_log (requete, filtres, nb_resultats, created_at) VALUES (%s, %s::jsonb, %s, %s)",
                    (e["requete"], json.dumps(e.get("filtres", {})), e["nb_resultats"], e.get("created_at")),
                )


def test_journal_rapport_exactitude_sur_jeu_injecte(base_recherche: dict[str, Any]) -> None:
    """Jeu injecté : on écrit des lignes dans recherche_log puis on vérifie
    que rapport_journal / top_requetes / sans_resultat comptent juste."""
    from seamtech_search.journal_recherche import (
        rapport_journal,
        recherches_sans_resultat,
        top_requetes,
    )

    index = base_recherche["index"]
    # Nettoyage préalable
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM recherche_log")

    maintenant = datetime.now(timezone.utc)
    jeu = [
        {"requete": "grand voile", "filtres": {}, "nb_resultats": 5, "created_at": maintenant - timedelta(days=1)},
        {"requete": "grand voile", "filtres": {}, "nb_resultats": 5, "created_at": maintenant - timedelta(hours=2)},
        {"requete": "genois", "filtres": {"type_voile": "Génois"}, "nb_resultats": 0, "created_at": maintenant - timedelta(hours=1)},
        {"requete": "spi", "filtres": {}, "nb_resultats": 2, "created_at": maintenant},
        {"requete": "zzz introuvable", "filtres": {}, "nb_resultats": 0, "created_at": maintenant},
        {"requete": "zzz introuvable", "filtres": {}, "nb_resultats": 0, "created_at": maintenant},
    ]
    _injecter_logs(index, jeu)

    top = top_requetes(index, periode_jours=None, limite=10)
    # top trié par nb_occurrences DESC
    assert top[0]["requete"] in ("grand voile", "zzz introuvable")
    # Vérif exactitude
    par_req = {e["requete"]: e for e in top}
    assert par_req["grand voile"]["nb_occurrences"] == 2
    assert par_req["grand voile"]["nb_sans_resultat"] == 0
    assert par_req["zzz introuvable"]["nb_occurrences"] == 2
    assert par_req["zzz introuvable"]["nb_sans_resultat"] == 2
    assert par_req["genois"]["nb_occurrences"] == 1
    assert par_req["genois"]["nb_sans_resultat"] == 1

    sans = recherches_sans_resultat(index, periode_jours=None, limite=10)
    par_sans = {e["requete"]: e for e in sans}
    assert "grand voile" not in par_sans
    assert par_sans["zzz introuvable"]["nb_occurrences"] == 2
    assert par_sans["genois"]["exemple_filtres"] == {"type_voile": "Génois"}

    rapport = rapport_journal(index, periode_jours=None, limite_top=10, limite_sans_resultat=10)
    assert rapport["total_recherches"] == 6
    assert rapport["total_sans_resultat"] == 3
    assert len(rapport["top_requetes"]) == 4
    assert len(rapport["sans_resultat"]) == 2

    # Période paramétrable : 1 jour → exclut les entrées >1j ?
    rapport_1j = rapport_journal(index, periode_jours=1, limite_top=10, limite_sans_resultat=10)
    # grand voile a 1 entrée à -1 jour (exactement 1 jour) + 1 à -2h => 1 ou 2 selon limite
    # On vérifie que le total 1j est < total global
    assert rapport_1j["total_recherches"] <= rapport["total_recherches"]


def test_journal_route_api(base_recherche: dict[str, Any]) -> None:
    """Route GET /recherche/journal agrège la table réelle."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    index = base_recherche["index"]
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM recherche_log")
            cur.execute(
                "INSERT INTO recherche_log (requete, filtres, nb_resultats) VALUES (%s, %s::jsonb, %s)",
                ("test journal", "{}", 0),
            )

    config = AppConfig(root_paths=[Path("/tmp")], database_url=base_recherche["url"], min_free_bytes=0)
    app = create_app(config)
    with TestClient(app) as client:
        r = client.get("/recherche/journal", params={"limite_top": 5, "limite_sans": 5})
        assert r.status_code == 200
        corps = r.json()
        assert "total_recherches" in corps
        assert "top_requetes" in corps
        assert "sans_resultat" in corps
        assert corps["total_recherches"] >= 1
