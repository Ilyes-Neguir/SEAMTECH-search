"""API de comparaison de voiles — Lot 4b (Fonction A).

Lecture seule, ouverte à tout compte nominatif : comparer deux voiles ne
modifie rien. Le service métier vit dans `seamtech_search.comparaison_voile`
(écarts bloc par bloc, honnêteté même-modèle) ; ce module ne fait que
l'authentification, la validation HTTP et la traduction d'erreurs.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Header, HTTPException, Query

from .comparaison_voile import (
    ComparaisonRefusee,
    candidats_comparaison,
    comparaison_entre,
)
from .liaison_voile import ExtractionIntrouvable
from .securite_requetes import exiger_acteur_nominatif

LOGGER = logging.getLogger("seamtech_search.comparaison_voile_routes")


def _exiger_postgres(index: Any) -> None:  # noqa: ANN401
    if not getattr(index, "is_postgres", False):
        raise HTTPException(
            status_code=503,
            detail=(
                "Comparaison de voiles disponible sur PostgreSQL uniquement (décision "
                "de couche §17.1) : les tables sail_extractions n'existent pas côté SQLite."
            ),
        )


def enregistrer_routes_comparaison_voile(
    app: Any,  # noqa: ANN401
    index: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    verifier_auth: Any,  # noqa: ANN401
) -> None:
    """Monte `/comparaison-voiles` sur l'application FastAPI."""

    def _acteur(
        token: str | None,
        utilisateur: str | None,
        role: str | None,
        session: str | None,
        jeton: str | None,
    ) -> None:
        exiger_acteur_nominatif(
            index,
            config,
            None,
            token=token,
            entete_utilisateur=utilisateur,
            entete_role=role,
            entete_session=session,
            entete_jeton=jeton,
        )

    @app.get("/comparaison-voiles/candidats")
    def route_candidats_comparaison(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        id_extraction: int = Query(ge=1),
        limite: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        try:
            return candidats_comparaison(index, id_extraction, limite=limite)
        except ExtractionIntrouvable as erreur:
            raise HTTPException(status_code=404, detail=str(erreur)) from erreur
        except ComparaisonRefusee as erreur:
            raise HTTPException(status_code=422, detail=str(erreur)) from erreur

    @app.get("/comparaison-voiles")
    def route_comparaison_voiles(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
        a: int = Query(ge=1),
        b: int = Query(ge=1),
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        _acteur(token, entete_utilisateur, entete_role, entete_session, entete_jeton)
        try:
            return comparaison_entre(index, a, b)
        except ExtractionIntrouvable as erreur:
            raise HTTPException(status_code=404, detail=str(erreur)) from erreur
        except ComparaisonRefusee as erreur:
            raise HTTPException(status_code=422, detail=str(erreur)) from erreur
