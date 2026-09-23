"""Routes qualité — Lot K.1."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Header

from .tableau import tableau_de_bord


def enregistrer_routes_qualite(app: Any, index: Any, config: Any, verifier_auth: Any) -> None:
    @app.get("/qualite/tableau-de-bord")
    def route_tableau_de_bord(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        return tableau_de_bord(index)
