"""Routes qualité — Lot K.1."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Header

from .cartographie import cartographie
from .tableau import tableau_de_bord


def enregistrer_routes_qualite(app: Any, index: Any, config: Any, verifier_auth: Any) -> None:
    @app.get("/qualite/tableau-de-bord")
    def route_tableau_de_bord(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        return tableau_de_bord(index)

    @app.get("/qualite/cartographie")
    def route_cartographie(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        """Cartographie du corpus — Lot 0 du plan « comparaison et réutilisation ».

        Lecture seule. Mesure ce qui conditionne tout le reste : taille des
        groupes comparables, variantes de valeurs, effet des seuils de
        validation automatique, complétude des champs, atteignabilité par la
        recherche. Aucune donnée nominative n'en sort.
        """
        verifier_auth(config, token)
        return cartographie(index)
