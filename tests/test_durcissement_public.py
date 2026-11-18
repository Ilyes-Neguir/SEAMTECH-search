"""Durcissement pour exposition internet publique (derrière Cloudflare).

L'application est servie derrière un proxy TLS. Ces tests prouvent que
l'origine pose elle-même les protections, sans dépendre du bord :

- en-têtes de sécurité sur l'API (HSTS, nosniff, anti-cadrage, referrer) ;
- validation de l'en-tête Host quand des hôtes sont déclarés
  (SEAMTECH_ALLOWED_HOSTS) — défense contre l'injection d'hôte.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig


def _client(tmp_path: Path, **surcharge) -> TestClient:
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    config = AppConfig(
        root_paths=[root],
        database_path=tmp_path / "search.db",
        auth_token="secret",
        **surcharge,
    )
    return TestClient(create_app(config))


def test_en_tetes_securite_sur_reponse_api(tmp_path: Path) -> None:
    client = _client(tmp_path)
    reponse = client.get("/live")
    assert reponse.status_code == 200
    assert reponse.headers["Strict-Transport-Security"].startswith("max-age=")
    assert "includeSubDomains" in reponse.headers["Strict-Transport-Security"]
    assert reponse.headers["X-Content-Type-Options"] == "nosniff"
    assert reponse.headers["X-Frame-Options"] == "DENY"
    assert reponse.headers["Referrer-Policy"] == "same-origin"


def test_en_tetes_securite_sur_reponse_refusee(tmp_path: Path) -> None:
    """Même les réponses de refus (401) portent les en-têtes : un navigateur
    ne doit jamais recevoir une page sans protection, même d'erreur."""
    client = _client(tmp_path)
    reponse = client.get("/search?q=x")
    assert reponse.status_code == 401
    assert reponse.headers["Strict-Transport-Security"].startswith("max-age=")
    assert reponse.headers["X-Frame-Options"] == "DENY"


def test_hote_non_declare_rejete(tmp_path: Path) -> None:
    client = _client(tmp_path, allowed_hosts=["app.example.com"])
    # Hôte déclaré : accepté.
    assert client.get("/live", headers={"Host": "app.example.com"}).status_code == 200
    # Hôte non déclaré : rejeté par TrustedHostMiddleware (400).
    assert client.get("/live", headers={"Host": "evil.example.com"}).status_code == 400


def test_sans_hotes_declares_aucun_controle(tmp_path: Path) -> None:
    """Sans SEAMTECH_ALLOWED_HOSTS, aucun contrôle d'hôte (développement
    local) : le middleware n'est pas posé et rien n'est rejeté."""
    client = _client(tmp_path)
    assert client.get("/live", headers={"Host": "nimporte.example.com"}).status_code == 200
