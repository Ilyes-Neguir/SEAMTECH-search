"""Sessions serveur Redis — Lot 0a.

Ce que ces tests PROUVENT, sur un Redis RÉEL et une base PostgreSQL réelle :

1. le ``sid`` fait au moins 128 bits, est NEUF à chaque connexion (rotation /
   anti-fixation) et l'état serveur porte user_id, issued_at, last_seen_at,
   expires_at ;
2. un cookie valide ouvre une route protégée (200) ; après ``POST
   /auth/deconnexion``, le MÊME cookie répond 401 partout ;
3. ``POST /auth/deconnexion-tout`` révoque DEUX sessions actives du même
   compte : les deux passent à 401 ;
4. l'inactivité (> 30 min) et l'expiration absolue (> 8 h) sont appliquées
   côté serveur, avec des paramètres de test injectés ;
5. Redis indisponible ⇒ 503 explicite (fail-closed), jamais 200 ni 401
   silencieux ;
6. Cache-Control: no-store sur les réponses de session ;
7. GET sur les endpoints de déconnexion ⇒ 405 ;
8. un changement de mot de passe révoque toutes les sessions ouvertes.

Marqueurs : ``session_redis`` (Redis réel) et ``postgres`` (comptes réels).
L'étape CI dédiée exige ``passed > 0`` et ``skipped == 0``.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from seamtech_search.comptes.comptes import creer_utilisateur
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.sessions_redis import (
    SessionRecord,
    SessionStore,
    SessionStoreIndisponible,
    sid_valide,
)

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
URL_REDIS = os.environ.get("SEAMTECH_TEST_REDIS_URL", "")
RACINE = Path(__file__).resolve().parent.parent
JETON_SERVICE = "jeton-de-service-lot-0a"

pytestmark = [pytest.mark.session_redis, pytest.mark.postgres]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def redis_url() -> str:
    if not URL_REDIS:
        pytest.skip("Set SEAMTECH_TEST_REDIS_URL to run the real-Redis session suite")
    return URL_REDIS


@pytest.fixture()
def base_sessions(redis_url: str) -> Iterator[dict[str, Any]]:
    """Base PostgreSQL jetable + comptes réels + Redis nettoyé."""
    assert redis_url  # noqa: S101 - le fixture a déjà sauté si absent
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom_base = f"session_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            # Nom composé comme IDENTIFIANT SQL (jamais de f-string) : un nom
            # de base est une donnée, pas du code.
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom_base)))
    finally:
        admin.close()
    url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"
    index = SearchIndex(Path(f"/tmp/unused-sessions-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()

    creer_utilisateur(index, "imrane", "Imrane", "Secret!2026", "operateur")
    creer_utilisateur(index, "chef", "Chef", "Chef!2026", "administrateur")

    _vider_redis(redis_url)
    try:
        yield {"index": index, "url": url_base, "nom": nom_base, "redis_url": redis_url}
    finally:
        index.close()
        _vider_redis(redis_url)
        admin = psycopg2.connect(URL_PG)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (nom_base,),
                )
                cursor.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nom_base))
                )
        finally:
            admin.close()


def _vider_redis(url: str) -> None:
    import redis

    client = redis.from_url(url, decode_responses=True)
    for motif in ("seamtech:session:*", "seamtech:user_sessions:*"):
        for cle in client.scan_iter(match=motif, count=100):
            client.delete(cle)


def _client(base: dict[str, Any], **extra: Any) -> TestClient:
    from seamtech_search.api import create_app

    valeurs: dict[str, Any] = {
        "root_paths": [RACINE],
        "database_path": RACINE / "unused-sessions.db",
        "database_url": base["url"],
        "auth_token": JETON_SERVICE,
        "redis_url": base["redis_url"],
        "min_free_bytes": 0,
        "session_idle_minutes": 30,
        "session_hours": 8,
    }
    valeurs.update(extra)
    return TestClient(create_app(AppConfig(**valeurs)))


def _entetes(sid: str | None = None) -> dict[str, str]:
    entetes = {"X-SEAMTECH-TOKEN": JETON_SERVICE}
    if sid:
        entetes["X-SEAMTECH-SESSION-SID"] = sid
    return entetes


def _connexion(client: TestClient, identifiant: str = "imrane", mot_de_passe: str = "Secret!2026") -> dict[str, Any]:
    reponse = client.post(
        "/auth/connexion",
        json={"identifiant": identifiant, "mot_de_passe": mot_de_passe},
        headers=_entetes(),
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


# ---------------------------------------------------------------------------
# 1. sid : entropie, rotation, état serveur
# ---------------------------------------------------------------------------


def test_sid_128_bits_rotation_et_etat_serveur(base_sessions: dict[str, Any]) -> None:
    client = _client(base_sessions)

    premiere = _connexion(client)
    seconde = _connexion(client)
    sid1, sid2 = premiere["sid"], seconde["sid"]

    # ≥ 128 bits : 32 octets CSPRNG → 43 caractères base64url.
    assert sid_valide(sid1)
    assert len(sid1) >= 43
    assert sid1 != sid2, "le sid DOIT changer à chaque connexion (anti-fixation)"

    magasin = SessionStore(base_sessions["redis_url"], idle_minutes=30, session_hours=8)
    record = magasin.valider_session(sid1)
    assert isinstance(record, SessionRecord)
    assert record is not None
    assert record.user_id == str(premiere["id_utilisateur"])
    assert record.identifiant == "imrane"
    assert record.issued_at <= record.last_seen_at < record.expires_at
    # Expiration absolue par défaut : 8 h.
    assert record.expires_at - record.issued_at == pytest.approx(8 * 3600, abs=2)

    # Redis porte bien les deux clés documentées.
    import redis

    brut = redis.from_url(base_sessions["redis_url"], decode_responses=True)
    assert brut.exists(f"seamtech:session:{sid1}")
    assert brut.sismember(f"seamtech:user_sessions:{premiere['id_utilisateur']}", sid1)
    assert 0 < brut.ttl(f"seamtech:session:{sid1}") <= 8 * 3600


# ---------------------------------------------------------------------------
# 2. Avant/après logout : même cookie, 200 puis 401
# ---------------------------------------------------------------------------


def test_cookie_valide_200_puis_logout_401(base_sessions: dict[str, Any]) -> None:
    client = _client(base_sessions)
    session = _connexion(client)
    sid = session["sid"]

    avant = client.get("/auth/session", headers=_entetes(sid))
    assert avant.status_code == 200, avant.text
    assert avant.json()["identifiant"] == "imrane"
    assert avant.headers.get("cache-control") == "no-store"

    deconnexion = client.post("/auth/deconnexion", json={"sid": sid}, headers=_entetes())
    assert deconnexion.status_code == 200, deconnexion.text
    assert deconnexion.json() == {"ok": True, "revoquee": True}

    # LE scénario du bug : rejouer le MÊME cookie après logout.
    apres = client.get("/auth/session", headers=_entetes(sid))
    assert apres.status_code == 401, apres.text
    assert apres.headers.get("cache-control") == "no-store"

    import redis

    brut = redis.from_url(base_sessions["redis_url"], decode_responses=True)
    assert not brut.exists(f"seamtech:session:{sid}")
    # Idempotence : une seconde déconnexion ne prétend pas avoir révoqué.
    encore = client.post("/auth/deconnexion", json={"sid": sid}, headers=_entetes())
    assert encore.status_code == 200
    assert encore.json()["revoquee"] is False


# ---------------------------------------------------------------------------
# 3. logout-all : deux sessions du même utilisateur → 401 partout
# ---------------------------------------------------------------------------


def test_logout_all_revoque_deux_sessions(base_sessions: dict[str, Any]) -> None:
    client = _client(base_sessions)
    s1 = _connexion(client)["sid"]
    s2 = _connexion(client)["sid"]
    assert s1 != s2
    assert client.get("/auth/session", headers=_entetes(s1)).status_code == 200
    assert client.get("/auth/session", headers=_entetes(s2)).status_code == 200

    reponse = client.post("/auth/deconnexion-tout", headers=_entetes(s1))
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["ok"] is True
    assert corps["sessions_revoquees"] >= 2
    assert corps["sessions_sql_revoquees"] >= 2

    for sid in (s1, s2):
        refus = client.get("/auth/session", headers=_entetes(sid))
        assert refus.status_code == 401, f"session {sid[:8]}… encore vivante : {refus.text}"


# ---------------------------------------------------------------------------
# 4. Expiration d'inactivité et expiration absolue (horloge injectée)
# ---------------------------------------------------------------------------


def test_expiration_inactivite_30_minutes(redis_url: str) -> None:
    _vider_redis(redis_url)
    magasin = SessionStore(redis_url, idle_minutes=30, session_hours=100)
    t0 = 1_800_000_000.0
    session = magasin.creer_session(
        user_id=1, identifiant="imrane", nom="Imrane", role="operateur", now=t0
    )
    # 29 min d'inactivité : encore valide (et le last_seen est rafraîchi).
    assert magasin.valider_session(session.sid, now=t0 + 29 * 60) is not None
    # 31 min sans activité depuis la dernière trace : refus serveur.
    fraiche = magasin.creer_session(
        user_id=1, identifiant="imrane", nom="Imrane", role="operateur", now=t0
    )
    assert magasin.valider_session(fraiche.sid, now=t0 + 31 * 60) is None
    assert magasin.session_existe(fraiche.sid) is False


def test_expiration_absolue_8_heures(redis_url: str) -> None:
    _vider_redis(redis_url)
    magasin = SessionStore(redis_url, idle_minutes=10_000, session_hours=8)
    t0 = 1_800_000_000.0
    session = magasin.creer_session(
        user_id=1, identifiant="imrane", nom="Imrane", role="operateur", now=t0
    )
    # Juste avant 8 h : valide. (On ne valide pas entre-temps : l'inactivité
    # serait le motif du refus, pas l'expiration absolue.)
    assert magasin.valider_session(session.sid, now=t0 + 8 * 3600 - 1) is not None
    autre = magasin.creer_session(
        user_id=1, identifiant="imrane", nom="Imrane", role="operateur", now=t0
    )
    assert magasin.valider_session(autre.sid, now=t0 + 8 * 3600 + 1) is None


# ---------------------------------------------------------------------------
# 5. Redis indisponible ⇒ 503 explicite (fail-closed)
# ---------------------------------------------------------------------------


def test_redis_indisponible_503(base_sessions: dict[str, Any]) -> None:
    # Port fermé : le client Redis échoue à la connexion.
    client = _client(base_sessions, redis_url="redis://127.0.0.1:6399/0")

    connexion = client.post(
        "/auth/connexion",
        json={"identifiant": "imrane", "mot_de_passe": "Secret!2026"},
        headers=_entetes(),
    )
    assert connexion.status_code == 503, connexion.text
    assert "indisponible" in connexion.json()["detail"].lower()

    session = client.get("/auth/session", headers=_entetes("A" * 43))
    assert session.status_code == 503, session.text
    assert "indisponible" in session.json()["detail"].lower()


def test_magasin_sans_url_refuse_explicitement() -> None:
    magasin = SessionStore(None)
    with pytest.raises(SessionStoreIndisponible):
        magasin.creer_session(user_id=1, identifiant="x", nom=None, role="operateur")
    with pytest.raises(SessionStoreIndisponible):
        magasin.valider_session("A" * 43)


# ---------------------------------------------------------------------------
# 6. Méthodes HTTP et révocation par changement de mot de passe
# ---------------------------------------------------------------------------


def test_get_sur_deconnexion_est_405(base_sessions: dict[str, Any]) -> None:
    client = _client(base_sessions)
    assert client.get("/auth/deconnexion", headers=_entetes()).status_code == 405
    assert client.get("/auth/deconnexion-tout", headers=_entetes()).status_code == 405


def test_changement_mot_de_passe_revoque_toutes_les_sessions(base_sessions: dict[str, Any]) -> None:
    client = _client(base_sessions)
    s1 = _connexion(client)["sid"]
    s2 = _connexion(client)["sid"]

    changement = client.post(
        "/auth/mot-de-passe",
        json={"mot_de_passe_actuel": "Secret!2026", "nouveau_mot_de_passe": "Nouveau!2026"},
        headers=_entetes(s1),
    )
    assert changement.status_code == 200, changement.text
    assert changement.json()["sessions_revoquees"] >= 2

    for sid in (s1, s2):
        assert client.get("/auth/session", headers=_entetes(sid)).status_code == 401

    # Le nouveau mot de passe ouvre une nouvelle session.
    nouvelle = _connexion(client, mot_de_passe="Nouveau!2026")
    assert client.get("/auth/session", headers=_entetes(nouvelle["sid"])).status_code == 200


def test_desactivation_revoque_les_sessions_redis(base_sessions: dict[str, Any]) -> None:
    client = _client(base_sessions)
    sid_imrane = _connexion(client)["sid"]
    sid_admin = _connexion(client, "chef", "Chef!2026")["sid"]

    desactivation = client.post(
        "/auth/utilisateurs/imrane/desactiver", headers=_entetes(sid_admin)
    )
    assert desactivation.status_code == 200, desactivation.text
    assert desactivation.json()["sessions_redis_revoquees"] >= 1
    assert client.get("/auth/session", headers=_entetes(sid_imrane)).status_code == 401
