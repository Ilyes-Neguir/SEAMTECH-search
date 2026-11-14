"""Section H — les sondes et les métriques, avec et sans jeton.

Quatre routes se ressemblent et n'ont pas le même contrat :

* ``/live`` et ``/ready`` — sondes de disponibilité. Ouvertes : c'est un
  orchestrateur qui les appelle, pas un utilisateur, et le poser derrière un
  jeton reviendrait à faire dépendre la supervision d'un secret.
* ``/health`` et ``/metrics`` — renseignements sur l'installation. Protégés :
  l'état des dépendances et les compteurs sont une carte de la production.

Cette distinction est dans le code (``api.py`` : ``exempt_paths`` d'un côté,
``path not in {"/live", "/ready"}`` de l'autre) mais elle n'était prouvée
qu'à moitié : un test vérifiait ``/health``, aucun ne vérifiait ``/metrics``.
Le test qui couvrait les quatre le faisait SANS jeton configuré, donc les
quatre répondaient 200 — ce qui ne prouve pas qu'elles sont ouvertes,
seulement que la porte n'était pas fermée à clé ce jour-là.

Un test de protection ne vaut que jeton configuré.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig

JETON = "jeton-de-recette-non-production"


def _client(tmp_path: Path) -> TestClient:
    racine = tmp_path / "root"
    racine.mkdir()
    config = AppConfig(
        root_paths=[racine],
        database_path=tmp_path / "search.db",
        auth_token=JETON,
        min_free_bytes=0,
    )
    return TestClient(create_app(config))


def test_les_sondes_de_disponibilite_repondent_sans_jeton(tmp_path: Path) -> None:
    """/live et /ready sont faites pour être appelées sans secret."""
    client = _client(tmp_path)
    assert client.get("/live").status_code == 200
    assert client.get("/ready").status_code == 200


def test_l_etat_de_sante_exige_le_jeton(tmp_path: Path) -> None:
    """/health dit où en sont les dépendances : ce n'est pas public."""
    client = _client(tmp_path)
    assert client.get("/health").status_code == 401
    reponse = client.get("/health", headers={"X-SEAMTECH-TOKEN": JETON})
    assert reponse.status_code == 200


def test_les_metriques_exigent_le_jeton(tmp_path: Path) -> None:
    """/metrics : les compteurs de l'installation ne se donnent pas.

    C'est la moitié manquante de la section H. ``/health`` était vérifié ;
    ``/metrics`` ne l'était nulle part, alors qu'il expose la même carte de
    la production sous une autre forme.
    """
    client = _client(tmp_path)
    assert client.get("/metrics").status_code == 401
    reponse = client.get("/metrics", headers={"X-SEAMTECH-TOKEN": JETON})
    assert reponse.status_code == 200


def test_les_sondes_ne_sont_pas_soumises_a_la_limitation_de_debit(tmp_path: Path) -> None:
    """/live et /ready doivent répondre même quand le quota est épuisé.

    Une supervision bloquée par son propre outil de limitation est un
    incident qu'on ne voit pas venir : la sonde cesse de répondre au moment
    précis où le service est le plus chargé. C'est pour cela que ces deux
    routes sont exemptées, et c'est ce que ce test vérifie — la limite
    atteinte, puis la sonde.
    """
    racine = tmp_path / "root"
    racine.mkdir()
    config = AppConfig(
        root_paths=[racine],
        database_path=tmp_path / "search.db",
        auth_token=JETON,
        rate_limit_per_minute=2,
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))

    for _ in range(2):
        assert client.get("/search?q=x", headers={"X-SEAMTECH-TOKEN": JETON}).status_code == 200
    assert client.get("/search?q=x", headers={"X-SEAMTECH-TOKEN": JETON}).status_code == 429

    assert client.get("/live").status_code == 200
    assert client.get("/ready").status_code == 200
