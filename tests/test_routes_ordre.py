"""Ordre d'enregistrement des routes FastAPI — un chemin fixe ne doit jamais
tomber dans une route paramétrée.

Ces contrôles ne touchent aucune base : ils lisent la table de routes de
l'application construite par `create_app`.
"""

from __future__ import annotations

from pathlib import Path

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig

RACINE = Path(__file__).resolve().parent.parent


def test_la_route_doublons_n_est_pas_avalee_par_fiches_code() -> None:
    """`GET /fiches/doublons` doit atteindre le module de doublons.

    FastAPI résout dans l'ordre d'enregistrement : quand `enregistrer_routes_fiches`
    passait avant `enregistrer_routes_dedup`, `/fiches/doublons` tombait dans
    `GET /fiches/{code}` et répondait
    `404 {"detail": "Fiche doublons inconnue."}` — l'écran des doublons affichait
    une erreur alors que `POST /fiches/doublons/scan` répondait 200.
    Constaté sur la pile réelle (API + interface Next) le 2026-10-30 ; ce test
    verrouille l'ORDRE, pas seulement l'existence des routes.
    """
    chemins = [
        (route.path, sorted(getattr(route, "methods", []) or []))
        for route in create_app(
            AppConfig(root_paths=[RACINE], database_path=RACINE / "data" / "ordre-routes.db", min_free_bytes=0)
        ).routes
    ]
    rang_doublons = next(i for i, (chemin, methodes) in enumerate(chemins)
                         if chemin == "/fiches/doublons" and "GET" in methodes)
    rang_fiche_code = next(i for i, (chemin, methodes) in enumerate(chemins)
                           if chemin == "/fiches/{code}" and "GET" in methodes)
    assert rang_doublons < rang_fiche_code, (
        "GET /fiches/doublons est enregistré APRÈS GET /fiches/{code} : "
        "FastAPI répondra « Fiche doublons inconnue »."
    )
