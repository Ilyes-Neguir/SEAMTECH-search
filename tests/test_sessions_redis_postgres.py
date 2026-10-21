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
8. un changement de mot de passe révoque toutes les sessions ouvertes ;
9. quand Redis refuse la révocation au moment exact de la mutation (ACL réelle
   `-del -srem`, aucun double), le changement/reset de mot de passe et la
   désactivation restent DURABLES : les clés Redis subsistent, puis les anciens
   ``sid`` répondent 401 grâce à la GÉNÉRATION D'IDENTIFIANTS comparée depuis
   PostgreSQL, et le comportement documenté du nouveau mot de passe est
   vérifié. ``revoquer_toutes_sessions_pour_identifiant()`` lève au lieu de
   rendre ``0`` quand Redis est injoignable ;
10. une connexion qui a vérifié l'ANCIEN mot de passe puis créé sa session APRÈS
   une réinitialisation est refusée (course fermée par la génération capturée à
   la vérification, pas par un horodatage) — session navigateur ET session
   historique —, avec un contrôle positif sur le nouveau mot de passe. La
   connexion passe par la VRAIE route `/auth/connexion`, suspendue juste après
   la vérification du mot de passe : si la route cessait de transmettre la
   génération capturée, le test échouerait. Le même test vérifie que
   `/auth/connexion` et `/auth/session` ne renvoient jamais
   `etat_identifiants` ;
11. la génération ne dépend d'aucune horloge (identique sur deux lectures
   successives, changée par chaque mutation) ;
12. la mutation du mot de passe et la révocation des lignes ``session_ui`` sont
   ATOMIQUES : un trigger PostgreSQL qui refuse la révocation annule aussi le
   changement (ancien mot de passe toujours valide, session historique
   intacte).

Marqueurs : ``session_redis`` (Redis réel) et ``postgres`` (comptes réels).
L'étape CI dédiée exige ``passed > 0`` et ``skipped == 0``.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from seamtech_search.comptes.comptes import (
    creer_utilisateur,
    session_valide,
    verifier_identifiants,
)
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


def _client(base: dict[str, Any], *, sans_exception_serveur: bool = False, **extra: Any) -> TestClient:
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
    return TestClient(
        create_app(AppConfig(**valeurs)), raise_server_exceptions=not sans_exception_serveur
    )


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


# ---------------------------------------------------------------------------
# 9. Panne de Redis À LA FRONTIÈRE mutation → révocation (revue Lot 0a)
# ---------------------------------------------------------------------------
# Ces tests ne simulent RIEN : ils utilisent un serveur Redis RÉEL sur lequel un
# utilisateur ACL réel a le droit de lire et d'écrire, mais PAS de supprimer
# (`-del -srem`), exactement ce que fait une révocation. Le magasin de sessions
# fonctionne donc pour l'authentification, échoue au moment de révoquer (503),
# et les clés restent en place — l'état exact laissé par une panne de Redis
# suivie d'un redémarrage sans perte. La base PostgreSQL, elle aussi réelle,
# doit alors faire échouer les anciens SID par elle-même.


def _redis_sans_revocation(redis_url: str) -> tuple[str, str]:
    """URL d'un utilisateur ACL réel : lecture/écriture oui, suppression NON.

    Aucun double du magasin : c'est le serveur Redis qui refuse `DEL`/`SREM`
    avec une vraie erreur `NOPERM`, que `SessionStore` traduit en
    ``SessionStoreIndisponible`` (→ 503).
    """
    from urllib.parse import quote, urlsplit, urlunsplit

    import redis

    admin = redis.from_url(redis_url)
    nom = f"e2e-sans-revocation-{uuid.uuid4().hex[:10]}"
    secret = f"acl-{uuid.uuid4().hex}"
    admin.execute_command(
        "ACL", "SETUSER", nom, "reset", "on", f">{secret}", "~*", "+@all", "-del", "-srem",
    )
    morceaux = urlsplit(redis_url)
    netloc = f"{nom}:{quote(secret, safe='')}@{morceaux.hostname or '127.0.0.1'}:{morceaux.port or 6379}"
    url = urlunsplit((morceaux.scheme or "redis", netloc, morceaux.path, morceaux.query, ""))
    return url, nom


def _liberer_acl(redis_url: str, nom: str) -> None:
    import redis

    redis.from_url(redis_url).execute_command("ACL", "DELUSER", nom)


def _redis_brut(redis_url: str) -> Any:  # noqa: ANN401 - client redis réel
    import redis

    return redis.from_url(redis_url, decode_responses=True)


def test_helper_revocation_ne_confond_pas_panne_et_zero() -> None:
    """``0`` ne doit JAMAIS vouloir dire « Redis injoignable » (revue, blocage 1).

    L'ancien helper avalait l'indisponibilité et renvoyait ``0`` : un appelant
    pouvait lire « rien à révoquer, confirmé ». Il lève désormais, et ``0``
    n'est plus rendu que par une révocation réellement exécutée.
    """
    from seamtech_search.sessions_redis import revoquer_toutes_sessions_pour_identifiant

    with pytest.raises(SessionStoreIndisponible):
        revoquer_toutes_sessions_pour_identifiant("redis://127.0.0.1:1/0", 1)


def test_changement_mot_de_passe_survit_a_une_panne_redis(base_sessions: dict[str, Any]) -> None:
    """Mot de passe changé + révocation impossible ⇒ anciens SID morts quand même."""
    admin_client = _client(base_sessions)
    premiere = _connexion(admin_client)
    s1, s2 = premiere["sid"], _connexion(admin_client)["sid"]
    # Session interne HISTORIQUE (en-têtes X-SEAMTECH-SESSION + jeton) : elle
    # ne vit pas dans Redis. Elle doit elle aussi cesser de fonctionner, sans
    # dépendre de la révocation Redis qui va échouer.
    entetes_historique = {
        "X-SEAMTECH-TOKEN": JETON_SERVICE,
        "X-SEAMTECH-SESSION": str(premiere["id_session"]),
        "X-SEAMTECH-SESSION-JETON": str(premiere["jeton_session"]),
    }
    assert admin_client.get("/auth/session", headers=entetes_historique).status_code == 200

    url_refus, nom_acl = _redis_sans_revocation(base_sessions["redis_url"])
    try:
        client_panne = _client(base_sessions, redis_url=url_refus)
        changement = client_panne.post(
            "/auth/mot-de-passe",
            json={"mot_de_passe_actuel": "Secret!2026", "nouveau_mot_de_passe": "Nouveau!2026"},
            headers=_entetes(s1),
        )
        # 503 : la révocation Redis n'a PAS eu lieu, l'API ne prétend pas le contraire.
        assert changement.status_code == 503, changement.text
        assert "indisponible" in changement.json()["detail"].lower()
    finally:
        _liberer_acl(base_sessions["redis_url"], nom_acl)

    # Les clés Redis sont TOUJOURS là : c'est la panne qu'on vient de provoquer.
    brut = _redis_brut(base_sessions["redis_url"])
    assert brut.exists(f"seamtech:session:{s1}")
    assert brut.exists(f"seamtech:session:{s2}")

    # Redis « revenu » (client normal) : la BASE invalide les sessions d'avant
    # le changement, sans dépendre d'une quelconque révocation Redis.
    apres = _client(base_sessions)
    for sid in (s1, s2):
        refus = apres.get("/auth/session", headers=_entetes(sid))
        assert refus.status_code == 401, refus.text
        assert "invalidée" in refus.json()["detail"].lower()

    # La session interne historique est morte elle aussi : sa révocation SQL a
    # été acquise AVANT la tentative Redis (dans la même requête).
    historique_apres = apres.get("/auth/session", headers=entetes_historique)
    assert historique_apres.status_code == 401, historique_apres.text

    # Le mot de passe a bien changé : l'ancien est refusé, le nouveau ouvre.
    ancien = apres.post(
        "/auth/connexion",
        json={"identifiant": "imrane", "mot_de_passe": "Secret!2026"},
        headers=_entetes(),
    )
    assert ancien.status_code == 401, ancien.text
    nouvelle = _connexion(apres, mot_de_passe="Nouveau!2026")
    assert apres.get("/auth/session", headers=_entetes(nouvelle["sid"])).status_code == 200


def test_reinitialisation_admin_survit_a_une_panne_redis(base_sessions: dict[str, Any]) -> None:
    """Réinitialisation par un administrateur + révocation impossible ⇒ ancien SID mort."""
    admin_client = _client(base_sessions)
    sid_imrane = _connexion(admin_client)["sid"]
    sid_admin = _connexion(admin_client, "chef", "Chef!2026")["sid"]

    url_refus, nom_acl = _redis_sans_revocation(base_sessions["redis_url"])
    try:
        client_panne = _client(base_sessions, redis_url=url_refus)
        reset = client_panne.post(
            "/auth/utilisateurs/imrane/mot-de-passe",
            json={"mot_de_passe": "Provisoire!2026"},
            headers=_entetes(sid_admin),
        )
        assert reset.status_code == 503, reset.text
    finally:
        _liberer_acl(base_sessions["redis_url"], nom_acl)

    brut = _redis_brut(base_sessions["redis_url"])
    assert brut.exists(f"seamtech:session:{sid_imrane}"), (
        "la révocation a échoué : la clé doit encore exister avant le retour du service"
    )

    apres = _client(base_sessions)
    refus = apres.get("/auth/session", headers=_entetes(sid_imrane))
    assert refus.status_code == 401, refus.text

    # Le mot de passe provisoire fonctionne et impose son changement.
    provisoire = _connexion(apres, mot_de_passe="Provisoire!2026")
    assert apres.get("/auth/session", headers=_entetes(provisoire["sid"])).json()[
        "doit_changer_mot_de_passe"
    ] is True


def test_desactivation_survit_a_une_panne_redis(base_sessions: dict[str, Any]) -> None:
    """Désactivation + révocation impossible ⇒ ancien SID mort, connexion refusée."""
    admin_client = _client(base_sessions)
    sid_imrane = _connexion(admin_client)["sid"]
    sid_admin = _connexion(admin_client, "chef", "Chef!2026")["sid"]

    url_refus, nom_acl = _redis_sans_revocation(base_sessions["redis_url"])
    try:
        client_panne = _client(base_sessions, redis_url=url_refus)
        desactivation = client_panne.post(
            "/auth/utilisateurs/imrane/desactiver", headers=_entetes(sid_admin)
        )
        assert desactivation.status_code == 503, desactivation.text
    finally:
        _liberer_acl(base_sessions["redis_url"], nom_acl)

    brut = _redis_brut(base_sessions["redis_url"])
    assert brut.exists(f"seamtech:session:{sid_imrane}")

    apres = _client(base_sessions)
    refus = apres.get("/auth/session", headers=_entetes(sid_imrane))
    assert refus.status_code == 401, refus.text
    assert "désactivé" in refus.json()["detail"].lower()

    connexion = apres.post(
        "/auth/connexion",
        json={"identifiant": "imrane", "mot_de_passe": "Secret!2026"},
        headers=_entetes(),
    )
    assert connexion.status_code == 401, connexion.text


# ---------------------------------------------------------------------------
# 10. COURSE connexion / réinitialisation (revue Lot 0a, B8)
# ---------------------------------------------------------------------------
# Le défaut : un login peut vérifier l'ANCIEN mot de passe, puis créer ses
# sessions APRÈS qu'une réinitialisation a été validée. Son ``issued_at`` est
# alors POSTÉRIEUR à l'invalidation, donc une comparaison d'instants l'accepte.
#
# Ce test ne double NI PostgreSQL NI Redis, et il n'appelle PAS les fonctions
# internes à la place de la route : il passe par **/auth/connexion**, la vraie
# route, et l'interrompt à l'instant précis qui compte — juste après la
# vérification du mot de passe, avant la création des sessions. La
# synchronisation est déterministe (deux événements, aucun ``sleep``) : le fil
# du login est suspendu, l'administrateur réinitialise, puis on le laisse
# reprendre. Si la route cessait de transmettre la génération capturée, la
# session serait créée avec l'état NOUVEAU et ce test échouerait.


def test_course_connexion_par_la_vraie_route_refusee_apres_reinitialisation(
    base_sessions: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from seamtech_search.comptes import routes as routes_module

    # Un client par fil : les deux requêtes tournent en parallèle, chacune avec
    # son propre pool PostgreSQL. Aucun service n'est remplacé.
    client_principal = _client(base_sessions)
    client_login = _client(base_sessions)

    # L'administrateur ouvre sa session AVANT l'installation de la barrière
    # (sinon sa propre connexion serait suspendue).
    admin = _connexion(client_principal, "chef", "Chef!2026")

    verification_atteinte = threading.Event()
    reprendre = threading.Event()
    original = routes_module.verifier_identifiants

    def verifier_puis_attendre(*args: Any, **kwargs: Any) -> dict[str, Any]:
        resultat = original(*args, **kwargs)
        # PREMIÈRE connexion seulement : celles du contrôle positif passent.
        if not verification_atteinte.is_set():
            verification_atteinte.set()
            assert reprendre.wait(timeout=60), "le test n'a jamais libéré la connexion"
        return resultat

    monkeypatch.setattr(routes_module, "verifier_identifiants", verifier_puis_attendre)

    resultat: dict[str, Any] = {}

    def connexion_lente() -> None:
        try:
            resultat["reponse"] = client_login.post(
                "/auth/connexion",
                json={"identifiant": "imrane", "mot_de_passe": "Secret!2026"},
                headers=_entetes(),
            )
        except Exception as exc:  # noqa: BLE001 - remonté à l'assertion
            resultat["erreur"] = exc

    fil = threading.Thread(target=connexion_lente, daemon=True)
    fil.start()
    try:
        assert verification_atteinte.wait(timeout=60), (
            "la route /auth/connexion n'a pas atteint la vérification du mot de passe"
        )
        # LA FENÊTRE : le mot de passe vient d'être vérifié, la session n'existe
        # pas encore. La réinitialisation est validée ici.
        reset = client_principal.post(
            "/auth/utilisateurs/imrane/mot-de-passe",
            json={"mot_de_passe": "Apres!2026"},
            headers=_entetes(admin["sid"]),
        )
        assert reset.status_code == 200, reset.text
    finally:
        reprendre.set()
        fil.join(timeout=60)
    assert not fil.is_alive(), "la connexion suspendue ne s'est jamais terminée"
    assert "erreur" not in resultat, resultat.get("erreur")

    # La connexion a RÉUSSI (elle avait vérifié l'ancien mot de passe) : elle a
    # donc créé ses deux sessions APRÈS la réinitialisation.
    reponse = resultat["reponse"]
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    sid = corps["sid"]

    # B7 : la génération d'identifiants n'est JAMAIS renvoyée par l'API.
    assert "etat_identifiants" not in corps, corps.keys()
    # La liste blanche ne doit pas avoir supprimé les champs utiles.
    for champ in ("id_utilisateur", "identifiant", "nom", "role", "doit_changer_mot_de_passe"):
        assert champ in corps, champ

    # La session NAVIGATEUR, créée après la réinitialisation mais obtenue avec
    # l'ANCIEN mot de passe, est refusée.
    refus = client_principal.get("/auth/session", headers=_entetes(sid))
    assert refus.status_code == 401, (
        "une session issue d'une connexion vérifiée AVANT la réinitialisation "
        f"a été acceptée après coup : {refus.text}"
    )
    assert "invalidée" in refus.json()["detail"].lower()
    magasin = SessionStore(base_sessions["redis_url"], idle_minutes=30, session_hours=8)
    assert magasin.session_existe(sid) is False, "la clé Redis aurait dû être retirée au refus"

    # La session HISTORIQUE de cette même connexion (en-têtes X-SEAMTECH-SESSION
    # + jeton) est refusée elle aussi, pour la même raison.
    entetes_historique = {
        "X-SEAMTECH-TOKEN": JETON_SERVICE,
        "X-SEAMTECH-SESSION": str(corps["id_session"]),
        "X-SEAMTECH-SESSION-JETON": str(corps["jeton_session"]),
    }
    refus_historique = client_principal.get("/auth/session", headers=entetes_historique)
    assert refus_historique.status_code == 401, refus_historique.text
    assert "etat_identifiants" not in refus_historique.json()

    # CONTRÔLE POSITIF : la même route, avec le nouveau mot de passe, ouvre des
    # sessions acceptées — et ne renvoie toujours pas la génération.
    positive = _connexion(client_principal, "imrane", "Apres!2026")
    assert "etat_identifiants" not in positive
    assert client_principal.get("/auth/session", headers=_entetes(positive["sid"])).status_code == 200
    entetes_positive = {
        "X-SEAMTECH-TOKEN": JETON_SERVICE,
        "X-SEAMTECH-SESSION": str(positive["id_session"]),
        "X-SEAMTECH-SESSION-JETON": str(positive["jeton_session"]),
    }
    vue = client_principal.get("/auth/session", headers=entetes_positive)
    assert vue.status_code == 200, vue.text
    assert "etat_identifiants" not in vue.json()


def test_generation_depend_de_chaque_mutation_et_pas_dune_horloge(
    base_sessions: dict[str, Any],
) -> None:
    """La génération change à CHAQUE mutation, et ne dépend d'aucun instant.

    Elle est calculée deux fois de suite sans mutation : identique (pas de
    dépendance à l'heure courante, donc deux répliques rendent le même verdict).
    Puis chaque mutation la change : réinitialisation admin, changement par
    l'intéressé, désactivation.
    """
    index = base_sessions["index"]
    client = _client(base_sessions)

    premier = str(verifier_identifiants(index, "imrane", "Secret!2026")["etat_identifiants"])
    second = str(verifier_identifiants(index, "imrane", "Secret!2026")["etat_identifiants"])
    assert premier == second

    from seamtech_search.comptes.comptes import profil_utilisateur

    admin = _connexion(client, "chef", "Chef!2026")
    assert client.post(
        "/auth/utilisateurs/imrane/mot-de-passe",
        json={"mot_de_passe": "Autre!2026"},
        headers=_entetes(admin["sid"]),
    ).status_code == 200
    apres_reset = str(verifier_identifiants(index, "imrane", "Autre!2026")["etat_identifiants"])
    assert apres_reset != premier

    # Changement par l'intéressé : sa PROPRE session tombe (génération changée),
    # ce qui est le comportement voulu — il se reconnecte avec le nouveau.
    assert client.post(
        "/auth/mot-de-passe",
        json={"mot_de_passe_actuel": "Chef!2026", "nouveau_mot_de_passe": "Chef!2027"},
        headers=_entetes(admin["sid"]),
    ).status_code == 200
    assert client.get("/auth/session", headers=_entetes(admin["sid"])).status_code == 401
    admin = _connexion(client, "chef", "Chef!2027")

    cible = int(verifier_identifiants(index, "imrane", "Autre!2026")["id_utilisateur"])
    avant_desactivation = profil_utilisateur(index, cible)
    assert avant_desactivation is not None
    assert client.post(
        "/auth/utilisateurs/imrane/desactiver", headers=_entetes(admin["sid"])
    ).status_code == 200
    apres_desactivation = profil_utilisateur(index, cible)
    assert apres_desactivation is not None
    assert apres_desactivation["actif"] is False
    assert apres_desactivation["etat_identifiants"] != avant_desactivation["etat_identifiants"]


# ---------------------------------------------------------------------------
# 11. ATOMICITÉ mutation ↔ révocation des sessions historiques (blocage 2)
# ---------------------------------------------------------------------------
# Le défaut : le changement de mot de passe était commité, puis la révocation
# des lignes ``session_ui`` faite dans une SECONDE transaction. Si celle-ci
# échouait, l'utilisateur croyait son mot de passe changé alors que sa session
# historique (en-têtes) continuait de fonctionner.
#
# Ici l'échec est RÉEL et déterministe : un trigger PostgreSQL refuse toute
# révocation. Le mot de passe ne doit alors PAS changer (ROLLBACK complet).


def test_changement_mot_de_passe_et_revocation_sql_sont_atomiques(
    base_sessions: dict[str, Any],
) -> None:
    index = base_sessions["index"]
    client = _client(base_sessions, sans_exception_serveur=True)
    session = _connexion(client)
    sid = session["sid"]

    entetes_historique = {
        "X-SEAMTECH-TOKEN": JETON_SERVICE,
        "X-SEAMTECH-SESSION": str(session["id_session"]),
        "X-SEAMTECH-SESSION-JETON": str(session["jeton_session"]),
    }
    assert client.get("/auth/session", headers=entetes_historique).status_code == 200

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "CREATE OR REPLACE FUNCTION test_refus_revocation() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'revocation refusee (test)'; END $$"
            )
            cursor.execute(
                "CREATE TRIGGER test_refus_revocation BEFORE UPDATE ON session_ui "
                "FOR EACH ROW WHEN (NEW.revoque_le IS NOT NULL AND OLD.revoque_le IS NULL) "
                "EXECUTE FUNCTION test_refus_revocation()"
            )
    try:
        changement = client.post(
            "/auth/mot-de-passe",
            json={"mot_de_passe_actuel": "Secret!2026", "nouveau_mot_de_passe": "Jamais!2026"},
            headers=_entetes(sid),
        )
        # La mutation a échoué : l'API ne répond PAS 200.
        assert changement.status_code >= 500, changement.text
    finally:
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("DROP TRIGGER IF EXISTS test_refus_revocation ON session_ui")
                cursor.execute("DROP FUNCTION IF EXISTS test_refus_revocation()")

    # ROLLBACK COMPLET : l'ancien mot de passe fonctionne toujours…
    assert verifier_identifiants(index, "imrane", "Secret!2026")["identifiant"] == "imrane"
    # …le nouveau n'a jamais été enregistré…
    with pytest.raises(Exception):
        verifier_identifiants(index, "imrane", "Jamais!2026")
    # …la ligne historique n'a pas été « à moitié » révoquée…
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT revoque_le, sessions_invalidees_le FROM session_ui s "
                "JOIN utilisateur u ON u.id_utilisateur = s.id_utilisateur "
                "WHERE s.id_session = %s",
                (int(session["id_session"]),),
            )
            revoque_le, invalidees_le = cursor.fetchone()
    assert revoque_le is None
    assert invalidees_le is None
    # …et la session historique est TOUJOURS utilisable (état inchangé).
    assert session_valide(index, int(session["id_session"]), str(session["jeton_session"])) is True
    assert client.get("/auth/session", headers=entetes_historique).status_code == 200
