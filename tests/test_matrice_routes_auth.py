"""Matrice systématique des routes backend × authentification de service.

Chaque route/méthode réellement enregistrée dans FastAPI est énumérée au
lieu de maintenir une liste manuelle qui vieillirait. Hors sondes de vie et
de disponibilité, aucune route ne doit atteindre la validation métier avec
un jeton absent ou faux ; le jeton de service valide ne doit jamais produire
un 500 sur une demande volontairement factice (503 = PostgreSQL absent dans
ce banc SQLite, explicitement accepté).
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig

_JETON_SERVICE = "jeton-service-de-test"
_EXEMPTES = {("GET", "/live"), ("GET", "/ready")}


def _chemin_concret(chemin: str) -> str:
    def remplacer(match: re.Match[str]) -> str:
        nom = match.group(1).split(":", 1)[0]
        if "jeton" in nom:
            return "A" * 32
        if "id" in nom:
            return "999999"
        if nom == "artifact":
            return "report_pdf"
        return "INCONNU"

    return re.sub(r"\{([^}]+)\}", remplacer, chemin)


def test_chaque_route_refuse_les_jetons_absents_ou_faux_avant_validation(tmp_path: Path) -> None:
    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "search.db",
        auth_token=_JETON_SERVICE,
    )
    app = create_app(config)
    client = TestClient(app, raise_server_exceptions=False)

    routes: list[tuple[str, str, str]] = []
    for route in app.routes:
        for methode in sorted((route.methods or set()) - {"HEAD", "OPTIONS"}):
            routes.append((methode, route.path, _chemin_concret(route.path)))
    assert len(routes) >= 100, f"inventaire de routes anormalement petit : {len(routes)}"

    anomalies: list[str] = []
    for methode, chemin, url in routes:
        kwargs = {"json": {}} if methode in {"POST", "PUT", "PATCH"} else {}
        for etiquette, headers in (
            ("sans jeton", {}),
            ("faux jeton", {"X-SEAMTECH-TOKEN": "faux"}),
        ):
            reponse = client.request(methode, url, headers=headers, **kwargs)
            attendu = 200 if (methode, chemin) in _EXEMPTES else 401
            if reponse.status_code != attendu:
                anomalies.append(
                    f"{etiquette} {methode} {chemin} -> {reponse.status_code}, attendu {attendu}"
                )

        reponse_service = client.request(
            methode,
            url,
            headers={"X-SEAMTECH-TOKEN": _JETON_SERVICE},
            **kwargs,
        )
        if reponse_service.status_code in {500, 502}:
            anomalies.append(
                f"jeton de service {methode} {chemin} -> {reponse_service.status_code}"
            )

    assert not anomalies, "\n".join(anomalies)
