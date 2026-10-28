"""Lot 0a — registre Redis des sessions nominatives (PostgreSQL + Redis réels).

Ces tests prouvent le contrat de session côté serveur, sans mock :

1. l'ouverture publie uniquement l'empreinte SHA-256 du jeton dans Redis ;
2. une déconnexion supprime immédiatement l'état Redis et rend le jeton inerte ;
3. une clé Redis absente refuse la session même si la ligne PostgreSQL existe ;
4. l'inactivité de 30 minutes et l'expiration absolue ferment la session ;
5. logout-all et changement de mot de passe révoquent toutes les sessions ;
6. Redis exigé mais injoignable échoue fermé (HTTP 503), aussi bien à
   l'ouverture qu'à la validation — jamais d'acceptation silencieuse.

Marqueurs ``postgres`` + ``redis_queue`` : l'étape CI ``redis_queue`` fournit
les deux services réels (``SEAMTECH_TEST_DATABASE_URL`` et
``SEAMTECH_TEST_REDIS_URL``). Hors de cette étape, le fichier est sauté
proprement au lieu de simuler une preuve.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from fastapi import HTTPException

from seamtech_search.comptes.comptes import (
    changer_mot_de_passe,
    creer_utilisateur,
    ouvrir_session,
    revoquer_session,
    revoquer_sessions_utilisateur,
    session_valide,
    sessions_dun_utilisateur,
)
from seamtech_search.comptes.securite import empreinte_jeton
from seamtech_search.indexer import SearchIndex
from seamtech_search.sessions_redis import (
    RegistreSessionsRedis,
    SessionsRedisIndisponible,
    registre_depuis_config,
)

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
URL_REDIS = os.environ.get("SEAMTECH_TEST_REDIS_URL", "")
MOT_DE_PASSE = "MotDePasse!2026"
NOUVEAU_MOT_DE_PASSE = "NouveauMotDePasse!2026"
IDENTIFIANT = "lot0a-sessions"

pytestmark = [pytest.mark.postgres, pytest.mark.redis_queue]


def _url_redis_injoignable() -> str:
    """URL d'un Redis VOLONTAIREMENT injoignable, choisie à l'exécution.

    Le port était écrit en dur (6390) : sur toute machine où Redis écoute
    réellement sur ce port — un bac à sable, un poste de développement, un
    second service — le « registre mort » répondait et le test échouait alors
    que le code testé était correct. On demande donc au système un port libre
    et on le rend immédiatement : plus aucune collision possible.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sonde:
        sonde.bind(("127.0.0.1", 0))
        port = sonde.getsockname()[1]
    return f"redis://127.0.0.1:{port}/15"


@pytest.fixture()
def base_sessions() -> Iterator[dict[str, Any]]:
    """PostgreSQL jetable migrée + vrai Redis + un compte de test isolé."""
    if not URL_PG or not URL_REDIS:
        pytest.skip(
            "Set SEAMTECH_TEST_DATABASE_URL and SEAMTECH_TEST_REDIS_URL "
            "to run PostgreSQL+Redis session integration tests"
        )

    from tests.conftest import _creer_base_jetable, _supprimer_base_jetable

    nom_base, url_base = _creer_base_jetable()
    index = SearchIndex(Path(f"/tmp/unused-sessions-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()

    registre = RegistreSessionsRedis(URL_REDIS)
    try:
        assert registre.ping(), "Redis injoignable : SEAMTECH_TEST_REDIS_URL est-il correct ?"
    except SessionsRedisIndisponible as erreur:  # pragma: no cover - dépend de l'environnement CI
        index.close()
        _supprimer_base_jetable(nom_base)
        pytest.fail(f"Redis de test injoignable malgré SEAMTECH_TEST_REDIS_URL : {erreur}")

    utilisateur = creer_utilisateur(
        index,
        IDENTIFIANT,
        "Compte Lot 0a",
        MOT_DE_PASSE,
        "approbateur",
        doit_changer_mot_de_passe=False,
    )
    try:
        yield {
            "index": index,
            "url": url_base,
            "nom": nom_base,
            "registre": registre,
            "utilisateur": utilisateur,
        }
    finally:
        try:
            registre.revoquer_utilisateur(int(utilisateur["id_utilisateur"]))
        except SessionsRedisIndisponible:
            # Le nettoyage ne doit pas masquer le résultat du test ; les clés
            # ont de toute façon un TTL aligné sur l'expiration de session.
            pass
        index.close()
        _supprimer_base_jetable(nom_base)


def _ouvrir(base: dict[str, Any]) -> dict[str, Any]:
    return ouvrir_session(
        base["index"],
        int(base["utilisateur"]["id_utilisateur"]),
        registre=base["registre"],
    )


def _valide(base: dict[str, Any], session: dict[str, Any], **kwargs: Any) -> bool:
    options: dict[str, Any] = {"registre": base["registre"]}
    options.update(kwargs)
    return session_valide(base["index"], int(session["id_session"]), str(session["jeton"]), **options)


def test_ouverture_publie_empreinte_puis_revocation_rend_jeton_inerte(
    base_sessions: dict[str, Any],
) -> None:
    session = _ouvrir(base_sessions)
    registre: RegistreSessionsRedis = base_sessions["registre"]

    etat = registre.etat(int(session["id_session"]))
    assert etat is not None
    assert etat["id_utilisateur"] == int(base_sessions["utilisateur"]["id_utilisateur"])
    assert etat["empreinte_jeton"] == empreinte_jeton(str(session["jeton"]))
    assert str(session["jeton"]) not in str(etat), "le jeton en clair ne doit jamais vivre dans Redis"
    assert _valide(base_sessions, session) is True

    revoquer_session(base_sessions["index"], int(session["id_session"]), registre=registre)

    assert registre.etat(int(session["id_session"])) is None
    assert _valide(base_sessions, session) is False


def test_cle_redis_absente_refuse_malgre_ligne_postgres(base_sessions: dict[str, Any]) -> None:
    session = _ouvrir(base_sessions)
    registre: RegistreSessionsRedis = base_sessions["registre"]

    # Suppression Redis seule : logout-all, purge du registre, ou session
    # antérieure au Lot 0a. La ligne PostgreSQL seule ne suffit plus.
    registre.revoquer(int(session["id_session"]), int(base_sessions["utilisateur"]["id_utilisateur"]))

    assert _valide(base_sessions, session) is False


def test_inactivite_superieure_a_30_minutes_revoque(base_sessions: dict[str, Any]) -> None:
    session = _ouvrir(base_sessions)
    registre: RegistreSessionsRedis = base_sessions["registre"]

    with base_sessions["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "UPDATE session_ui SET derniere_activite = now() - interval '31 minutes' "
                "WHERE id_session = %s",
                (int(session["id_session"]),),
            )
    registre.toucher(int(session["id_session"]), time.time() - 31 * 60)

    assert _valide(base_sessions, session, idle_minutes=30) is False
    assert registre.etat(int(session["id_session"])) is None
    sessions = sessions_dun_utilisateur(base_sessions["index"], IDENTIFIANT)
    assert sessions and sessions[0]["revoquee"] is True


def test_expiration_absolue_refuse(base_sessions: dict[str, Any]) -> None:
    session = _ouvrir(base_sessions)

    with base_sessions["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "UPDATE session_ui SET expire_le = now() - interval '1 minute' WHERE id_session = %s",
                (int(session["id_session"]),),
            )

    assert _valide(base_sessions, session) is False


def test_logout_all_revoque_toutes_les_sessions(base_sessions: dict[str, Any]) -> None:
    premiere = _ouvrir(base_sessions)
    deuxieme = _ouvrir(base_sessions)
    assert _valide(base_sessions, premiere) is True
    assert _valide(base_sessions, deuxieme) is True

    resultat = revoquer_sessions_utilisateur(
        base_sessions["index"],
        int(base_sessions["utilisateur"]["id_utilisateur"]),
        registre=base_sessions["registre"],
    )

    assert resultat["sessions_revoquees"] == 2
    assert _valide(base_sessions, premiere) is False
    assert _valide(base_sessions, deuxieme) is False


def test_changement_mot_de_passe_revoque_sessions(base_sessions: dict[str, Any]) -> None:
    session = _ouvrir(base_sessions)
    assert _valide(base_sessions, session) is True

    changer_mot_de_passe(
        base_sessions["index"],
        int(base_sessions["utilisateur"]["id_utilisateur"]),
        MOT_DE_PASSE,
        NOUVEAU_MOT_DE_PASSE,
        registre=base_sessions["registre"],
    )

    assert _valide(base_sessions, session) is False
    assert base_sessions["registre"].etat(int(session["id_session"])) is None


def test_redis_indisponible_ouverture_et_validation_fail_closed(
    base_sessions: dict[str, Any],
) -> None:
    registre_mort = RegistreSessionsRedis(_url_redis_injoignable())

    with pytest.raises(HTTPException) as erreur_ouverture:
        ouvrir_session(
            base_sessions["index"],
            int(base_sessions["utilisateur"]["id_utilisateur"]),
            registre=registre_mort,
        )
    assert erreur_ouverture.value.status_code == 503

    # Aucune session invérifiable n'a survécu en base.
    sessions = sessions_dun_utilisateur(base_sessions["index"], IDENTIFIANT)
    assert sessions and all(session["revoquee"] for session in sessions)

    session = _ouvrir(base_sessions)
    with pytest.raises(HTTPException) as erreur_validation:
        session_valide(
            base_sessions["index"],
            int(session["id_session"]),
            str(session["jeton"]),
            registre=registre_mort,
        )
    assert erreur_validation.value.status_code == 503


def test_registre_exige_sans_url_redis_leve() -> None:
    config = SimpleNamespace(
        redis_url=None,
        require_durable_queue=False,
        require_redis_sessions=True,
    )
    with pytest.raises(SessionsRedisIndisponible):
        registre_depuis_config(config)
