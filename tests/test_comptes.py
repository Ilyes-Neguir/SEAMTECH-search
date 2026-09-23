"""Lot L.2 — comptes nominatifs : « qui a validé quoi » (plan v3.0 §10.1, §17.5).

Ce que ces tests prouvent, dans l'ordre du contrat :

1. **Empreinte** — ``scrypt$n=16384$r=8$p=1$…``, vérification à temps constant,
   aucun plantage sur une empreinte malformée ou aux paramètres absurdes.
2. **Session nominative** — la connexion ouvre une session en base, le jeton
   n'y est stocké que sous forme d'empreinte SHA-256.
3. **Déconnexion réellement effective** — après ``revoque_le``, le cookie,
   bien que toujours signé, n'ouvre plus rien (401).
4. **Verrouillage** — 5 échecs en 5 minutes ⇒ 429 même avec le BON mot de passe.
5. **Attribution** — ``test_validation_attribuee_au_compte_connecte`` : une
   validation faite sous la session nominative est journalisée au nom de CE
   compte (sabotage visé : ``id_utilisateur = 1`` en dur → ROUGE).
6. **Frontière de confiance** — les en-têtes d'attribution
   (``X-SEAMTECH-UTILISATEUR`` / ``X-SEAMTECH-ROLE``) sans
   ``X-SEAMTECH-TOKEN`` valide ⇒ 401 : le navigateur ne peut pas usurper.
7. **Sécrétion** — aucune réponse de l'API ne contient ``scrypt$``, aucune
   ligne de ``utilisateur`` ne contient le mot de passe en clair.
8. **Gestion des comptes** — réservée au rôle ``administrateur`` : 403 pour un
   opérateur authentifié, 401 sans session.
9. **CLI** — mot de passe lu sur STDIN, jamais en argument (vérifié sur
   l'analyseur d'arguments réel).

Ces tests tournent sur PostgreSQL réel (``-m postgres``) : les tables
``utilisateur`` / ``session_ui`` n'existent pas côté SQLite (§17.1).
"""

from __future__ import annotations

import ast
import io
import os
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from seamtech_search.comptes import cli as cli_comptes
from seamtech_search.comptes.comptes import (
    changer_mot_de_passe,
    correspondance_jeton,
    creer_utilisateur,
    desactiver_utilisateur,
    lister_utilisateurs,
    ouvrir_session,
    reinitialiser_mot_de_passe,
    revoquer_session,
    session_valide,
    verifier_identifiants,
    verrou_actif,
)
from seamtech_search.comptes.securite import (
    empreinte_jeton,
    empreinte_mot_de_passe,
    nouveau_jeton,
    verifier_mot_de_passe,
)
from seamtech_search.indexer import SearchIndex
from seamtech_search.qualite import tableau

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
RACINE = Path(__file__).resolve().parent.parent
JETON_SERVICE = "jeton-de-service-de-test"

pytestmark = pytest.mark.postgres


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def base_comptes() -> Iterator[dict[str, Any]]:
    """Base jetable migrée (001→016), portée FONCTION : aucun compte partagé.

    Portée fonction et non module : un test qui verrouille un identifiant après
    5 échecs ne doit pas rendre les suivants dépendants de son ordre d'exécution.
    """
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    nom_base = f"comptes_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        admin.close()
    url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"
    index = SearchIndex(Path(f"/tmp/unused-comptes-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    try:
        yield {"index": index, "url": url_base, "nom": nom_base}
    finally:
        index.close()
        admin = psycopg2.connect(URL_PG)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (nom_base,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
        finally:
            admin.close()


@pytest.fixture()
def base_comptes_corpus() -> Iterator[dict[str, Any]]:
    """Base jetable migrée + corpus semé (mêmes fiches que les tests de recherche)."""
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    from tests.conftest import _creer_base_jetable, _supprimer_base_jetable, semer_corpus

    nom_base, url_base = _creer_base_jetable()
    index = SearchIndex(Path(f"/tmp/unused-corpus-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    fiches = semer_corpus(index)
    try:
        yield {"index": index, "url": url_base, "nom": nom_base, "fiches": fiches}
    finally:
        index.close()
        _supprimer_base_jetable(nom_base)


@pytest.fixture()
def client_comptes(base_comptes: dict[str, Any]) -> TestClient:
    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(
        root_paths=[RACINE],
        database_path=RACINE / "unused-comptes.db",
        database_url=base_comptes["url"],
        auth_token=JETON_SERVICE,
    )
    return TestClient(create_app(config))


def _creer(base: dict[str, Any], identifiant: str, mot_de_passe: str, role: str = "operateur") -> dict[str, Any]:
    return creer_utilisateur(base["index"], identifiant, f"Nom {identifiant}", mot_de_passe, role)


def _ouvrir_cookie(base: dict[str, Any], identifiant: str, mot_de_passe: str) -> dict[str, Any]:
    """Ce que le front met dans le cookie : id_session + jeton."""
    utilisateur = verifier_identifiants(base["index"], identifiant, mot_de_passe)
    session = ouvrir_session(base["index"], utilisateur["id_utilisateur"])
    return {**utilisateur, "id_session": session["id_session"], "jeton_session": session["jeton"]}


def _entetes_session(cookie: dict[str, Any]) -> dict[str, str]:
    return {
        "X-SEAMTECH-TOKEN": JETON_SERVICE,
        "X-SEAMTECH-SESSION": str(cookie["id_session"]),
        "X-SEAMTECH-SESSION-JETON": cookie["jeton_session"],
    }


# ---------------------------------------------------------------------------
# 1. Empreintes et jetons — unitaire, sans base
# ---------------------------------------------------------------------------


def test_empreinte_scrypt_format_et_verification() -> None:
    empreinte = empreinte_mot_de_passe("MotDePasse!2026")
    assert empreinte.startswith("scrypt$n=16384$r=8$p=1$")
    assert len(empreinte.split("$")) == 6
    assert len(empreinte.split("$")[4]) == 24  # sel de 16 octets en base64
    assert "scrypt$" in empreinte and "MotDePasse!2026" not in empreinte

    assert verifier_mot_de_passe("MotDePasse!2026", empreinte) is True
    assert verifier_mot_de_passe("motdepasse!2026", empreinte) is False
    assert verifier_mot_de_passe("", empreinte) is False
    assert verifier_mot_de_passe(None, empreinte) is False

    # Deux empreintes du MÊME mot de passe diffèrent : le sel est aléatoire.
    assert empreinte_mot_de_passe("MotDePasse!2026") != empreinte

    # Empreintes malformées ou piégées : False, jamais d'exception.
    for invalide in (
        "",
        "clair-text",
        "scrypt$n=16384$r=8$p=1$@@@$@@@",
        "scrypt$n=16384$r=8$p=1$c2Vs",
        "scrypt$n=1073741824$r=8$p=1$c2VsZGVzZWxzZGVz$"
        + empreinte.split("$")[5],
        "argon2$n=16384$r=8$p=1$c2VsZGVzZWxzZGVz$c2VsZGVzZWxzZGVz",
    ):
        assert verifier_mot_de_passe("MotDePasse!2026", invalide) is False


def test_jeton_de_session_non_reversible() -> None:
    jeton = nouveau_jeton()
    assert len(jeton) >= 40
    assert nouveau_jeton() != jeton
    empreinte = empreinte_jeton(jeton)
    assert len(empreinte) == 64 and empreinte != jeton
    assert empreinte_jeton(jeton) == empreinte  # déterministe
    assert empreinte_jeton(jeton + "x") != empreinte


def test_cli_naccepte_jamais_le_mot_de_passe_en_argument() -> None:
    """Un mot de passe en argument finit dans l'historique du shell et dans `ps`."""
    analyseur = cli_comptes.construire_analyseur()
    for action in analyseur._actions:  # noqa: SLF001 - introspection volontaire
        for option in action.option_strings:
            assert "mot-de-passe" not in option.replace("--mot-de-passe-definitif", ""), option
            assert option not in ("--password", "-p")
    assert analyseur.parse_args(["creer", "--identifiant", "x", "--nom", "X"]).commande == "creer"


def test_aucun_appel_reseau_dans_le_module_comptes() -> None:
    """RG14 : aucun appel réseau sortant possible depuis les comptes nominatifs."""
    interdits = {"requests", "httpx", "urllib", "urllib2", "http.client", "socket", "aiohttp", "ftplib"}
    dossier = RACINE / "seamtech_search" / "comptes"
    for fichier in sorted(dossier.glob("*.py")):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.Import):
                for alias in noeud.names:
                    assert alias.name.split(".")[0] not in interdits, f"{fichier.name}: import {alias.name}"
            elif isinstance(noeud, ast.ImportFrom) and noeud.module:
                assert noeud.module.split(".")[0] not in interdits, f"{fichier.name}: from {noeud.module}"


# ---------------------------------------------------------------------------
# 2. Comptes — base réelle
# ---------------------------------------------------------------------------


def test_creation_compte_et_refus_des_doublons(base_comptes: dict[str, Any]) -> None:
    base = base_comptes
    compte = _creer(base, "imrane", "Secret!2026")
    assert compte["id_utilisateur"] > 0
    assert compte["role"] == "operateur"
    assert compte["doit_changer_mot_de_passe"] is True

    with pytest.raises(HTTPException) as doublon:
        _creer(base, "imrane", "Autre!2026")
    assert doublon.value.status_code == 409

    with pytest.raises(HTTPException) as role:
        _creer(base, "chef", "Secret!2026", role="superutilisateur")
    assert role.value.status_code == 422

    with pytest.raises(HTTPException) as vide:
        creer_utilisateur(base["index"], "   ", "Nom", "Secret!2026")
    assert vide.value.status_code == 422

    identifiants = {c["identifiant"] for c in lister_utilisateurs(base["index"])}
    assert "imrane" in identifiants


def test_connexion_et_session_nominative(base_comptes: dict[str, Any]) -> None:
    base = base_comptes
    _creer(base, "imrane", "Secret!2026")

    utilisateur = verifier_identifiants(base["index"], "imrane", "Secret!2026")
    assert utilisateur["identifiant"] == "imrane"
    assert utilisateur["role"] == "operateur"

    session = ouvrir_session(base["index"], utilisateur["id_utilisateur"], user_agent="pytest/1.0")
    assert session["id_session"] > 0
    assert session["jeton"] not in ("", None)
    assert session_valide(base["index"], session["id_session"], session["jeton"]) is True
    assert session_valide(base["index"], session["id_session"], session["jeton"] + "x") is False

    # Le jeton n'est PAS stocké en clair : la base ne contient que son SHA-256.
    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT empreinte_jeton, user_agent FROM session_ui WHERE id_session = %s", (session["id_session"],))
            empreinte, agent = cursor.fetchone()
    assert empreinte == empreinte_jeton(session["jeton"])
    assert empreinte != session["jeton"]
    assert agent == "pytest/1.0"


def test_mot_de_passe_jamais_en_clair_dans_la_base(base_comptes: dict[str, Any]) -> None:
    base = base_comptes
    mot_de_passe = "Secret-Tres-Particulier!2026"
    _creer(base, "imrane", mot_de_passe)

    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT identifiant, nom, role, empreinte_mot_de_passe FROM utilisateur")
            lignes = cursor.fetchall()
    for ligne in lignes:
        for valeur in ligne:
            assert mot_de_passe not in str(valeur)
    assert any(str(ligne[3]).startswith("scrypt$") for ligne in lignes)


def test_deconnexion_rend_la_session_invalide(base_comptes: dict[str, Any]) -> None:
    base = base_comptes
    _creer(base, "imrane", "Secret!2026")
    cookie = _ouvrir_cookie(base, "imrane", "Secret!2026")
    assert session_valide(base["index"], cookie["id_session"], cookie["jeton_session"]) is True

    resultat = revoquer_session(base["index"], cookie["id_session"])
    assert resultat == {"id_session": cookie["id_session"], "revoquee": True, "deja_revoquee": False}
    # Le cookie reste cryptographiquement valide côté navigateur, mais la
    # session est close : c'est TOUT l'intérêt de garder la vérité en base.
    assert session_valide(base["index"], cookie["id_session"], cookie["jeton_session"]) is False

    # Idempotence : rejouer la déconnexion ne réécrit rien.
    encore = revoquer_session(base["index"], cookie["id_session"])
    assert encore["deja_revoquee"] is True and encore["revoquee"] is False
    # Un mauvais jeton n'a pas le droit de révoquer la session d'un autre.
    assert correspondance_jeton(base["index"], cookie["id_session"], "mauvais-jeton") is False


def test_cinq_echecs_verrouillent_le_compte(base_comptes: dict[str, Any]) -> None:
    base = base_comptes
    _creer(base, "imrane", "Secret!2026")

    for _ in range(5):
        with pytest.raises(HTTPException) as echec:
            verifier_identifiants(base["index"], "imrane", "mauvais")
        assert echec.value.status_code == 401

    restant = verrou_actif(base["index"], "imrane")
    assert 0 < restant <= 5 * 60, restant

    # Même avec le BON mot de passe : 429, pas 200.
    with pytest.raises(HTTPException) as verrou:
        verifier_identifiants(base["index"], "imrane", "Secret!2026")
    assert verrou.value.status_code == 429
    assert int(verrou.value.headers["Retry-After"]) == restant

    # Un identifiant INCONNU répond 401 sans révéler qu'il n'existe pas.
    with pytest.raises(HTTPException) as inconnu:
        verifier_identifiants(base["index"], "inconnu", "mauvais")
    assert inconnu.value.status_code == 401
    assert "inconnu" in inconnu.value.detail or "incorrect" in inconnu.value.detail


def test_desactivation_revoque_les_sessions_et_bloque_la_connexion(base_comptes: dict[str, Any]) -> None:
    base = base_comptes
    _creer(base, "imrane", "Secret!2026")
    cookie = _ouvrir_cookie(base, "imrane", "Secret!2026")

    resultat = desactiver_utilisateur(base["index"], "imrane")
    assert resultat["actif"] is False
    assert resultat["sessions_revoquees"] >= 1
    assert session_valide(base["index"], cookie["id_session"], cookie["jeton_session"]) is False

    with pytest.raises(HTTPException) as refus:
        verifier_identifiants(base["index"], "imrane", "Secret!2026")
    assert refus.value.status_code == 401

    with pytest.raises(HTTPException) as absent:
        desactiver_utilisateur(base["index"], "fantome")
    assert absent.value.status_code == 404


def test_reinitialisation_puis_changement_du_mot_de_passe(base_comptes: dict[str, Any]) -> None:
    base = base_comptes
    compte = _creer(base, "imrane", "Provisoire!2026")

    reinitialiser_mot_de_passe(base["index"], "imrane", "Provisoire2!2026")
    with pytest.raises(HTTPException) as ancien:
        verifier_identifiants(base["index"], "imrane", "Provisoire!2026")
    assert ancien.value.status_code == 401
    assert verifier_identifiants(base["index"], "imrane", "Provisoire2!2026")["doit_changer_mot_de_passe"] is True

    with pytest.raises(HTTPException) as mauvais:
        changer_mot_de_passe(base["index"], compte["id_utilisateur"], "faux", "Nouveau!2026")
    assert mauvais.value.status_code == 401

    resultat = changer_mot_de_passe(base["index"], compte["id_utilisateur"], "Provisoire2!2026", "Nouveau!2026")
    assert resultat["doit_changer_mot_de_passe"] is False
    assert verifier_identifiants(base["index"], "imrane", "Nouveau!2026")["doit_changer_mot_de_passe"] is False


# ---------------------------------------------------------------------------
# 3. API /auth et frontière de confiance
# ---------------------------------------------------------------------------


def test_api_connexion_session_deconnexion(
    base_comptes: dict[str, Any], client_comptes: TestClient
) -> None:
    _creer(base_comptes, "imrane", "Secret!2026")

    connexion = client_comptes.post(
        "/auth/connexion",
        json={"identifiant": "imrane", "mot_de_passe": "Secret!2026"},
        headers={"X-SEAMTECH-TOKEN": JETON_SERVICE},
    )
    assert connexion.status_code == 200, connexion.text
    corps = connexion.json()
    assert corps["ok"] is True
    assert corps["identifiant"] == "imrane"
    assert corps["id_session"] > 0 and corps["jeton_session"]
    assert corps["doit_changer_mot_de_passe"] is True

    entetes = {
        "X-SEAMTECH-TOKEN": JETON_SERVICE,
        "X-SEAMTECH-SESSION": str(corps["id_session"]),
        "X-SEAMTECH-SESSION-JETON": corps["jeton_session"],
    }
    session = client_comptes.get("/auth/session", headers=entetes)
    assert session.status_code == 200, session.text
    assert session.json()["identifiant"] == "imrane"

    # Aucune réponse ne contient d'empreinte, de sel ni de mot de passe.
    for reponse in (connexion, session):
        assert "scrypt$" not in reponse.text
        assert "empreinte_mot_de_passe" not in reponse.text
        assert "Secret!2026" not in reponse.text

    deconnexion = client_comptes.post(
        "/auth/deconnexion",
        json={"id_session": corps["id_session"], "jeton_session": corps["jeton_session"]},
        headers={"X-SEAMTECH-TOKEN": JETON_SERVICE},
    )
    assert deconnexion.status_code == 200, deconnexion.text
    assert deconnexion.json()["revoquee"] is True

    # LE point du lot : le cookie est toujours signé, mais il ne vaut plus rien.
    apres = client_comptes.get("/auth/session", headers=entetes)
    assert apres.status_code == 401, apres.text

    # Un jeton non conforme ne révoque pas la session d'autrui.
    mauvais_jeton = client_comptes.post(
        "/auth/deconnexion",
        json={"id_session": corps["id_session"], "jeton_session": "faux-jeton"},
        headers={"X-SEAMTECH-TOKEN": JETON_SERVICE},
    )
    assert mauvais_jeton.status_code == 401, mauvais_jeton.text


def test_api_mauvais_mot_de_passe_et_jeton_de_service(
    base_comptes: dict[str, Any], client_comptes: TestClient
) -> None:
    _creer(base_comptes, "imrane", "Secret!2026")

    refuse = client_comptes.post(
        "/auth/connexion",
        json={"identifiant": "imrane", "mot_de_passe": "mauvais"},
        headers={"X-SEAMTECH-TOKEN": JETON_SERVICE},
    )
    assert refuse.status_code == 401
    assert "scrypt$" not in refuse.text

    # Sans jeton de service, pas même une tentative de connexion.
    sans_jeton = client_comptes.post(
        "/auth/connexion", json={"identifiant": "imrane", "mot_de_passe": "Secret!2026"}
    )
    assert sans_jeton.status_code == 401


def test_gestion_des_comptes_reservee_administrateur(
    base_comptes: dict[str, Any], client_comptes: TestClient
) -> None:
    base = base_comptes
    _creer(base, "imrane", "Secret!2026", role="operateur")
    _creer(base, "chef", "Secret!2026", role="administrateur")

    operateur = _ouvrir_cookie(base, "imrane", "Secret!2026")
    administrateur = _ouvrir_cookie(base, "chef", "Secret!2026")

    sans_session = client_comptes.get("/auth/utilisateurs", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
    assert sans_session.status_code == 401

    interdit = client_comptes.get("/auth/utilisateurs", headers=_entetes_session(operateur))
    assert interdit.status_code == 403, interdit.text
    assert "administrateur" in interdit.json()["detail"]

    autorise = client_comptes.get("/auth/utilisateurs", headers=_entetes_session(administrateur))
    assert autorise.status_code == 200, autorise.text
    identifiants = {compte["identifiant"] for compte in autorise.json()}
    assert {"imrane", "chef"} <= identifiants
    assert all("empreinte" not in compte for compte in autorise.json())

    creation = client_comptes.post(
        "/auth/utilisateurs",
        json={"identifiant": "nouveau", "nom": "Nouveau", "role": "operateur", "mot_de_passe": "Provisoire!2026"},
        headers=_entetes_session(administrateur),
    )
    assert creation.status_code == 200, creation.text
    assert creation.json()["identifiant"] == "nouveau"
    assert "scrypt$" not in creation.text and "Provisoire!2026" not in creation.text

    interdit_creation = client_comptes.post(
        "/auth/utilisateurs",
        json={"identifiant": "pirate", "nom": "P", "mot_de_passe": "Provisoire!2026"},
        headers=_entetes_session(operateur),
    )
    assert interdit_creation.status_code == 403


def test_attribution_refusee_sans_jeton_de_service(
    base_comptes_corpus: dict[str, Any],
) -> None:
    """Un en-tête d'attribution sans jeton de service valide ⇒ 401, pas de trace."""
    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    base = base_comptes_corpus
    config = AppConfig(
        root_paths=[RACINE],
        database_path=RACINE / "unused-attribution.db",
        database_url=base["url"],
        auth_token=JETON_SERVICE,
    )
    client = TestClient(create_app(config))
    code = "1001-GV-006"

    sans_jeton = client.post(
        f"/fiches/{code}/valider",
        json={"utilisateur": "usurpateur"},
        headers={"X-SEAMTECH-UTILISATEUR": "usurpateur", "X-SEAMTECH-ROLE": "administrateur"},
    )
    assert sans_jeton.status_code == 401, sans_jeton.text

    mauvais_jeton = client.post(
        f"/fiches/{code}/valider",
        json={"utilisateur": "usurpateur"},
        headers={
            "X-SEAMTECH-TOKEN": "mauvais",
            "X-SEAMTECH-UTILISATEUR": "usurpateur",
            "X-SEAMTECH-ROLE": "administrateur",
        },
    )
    assert mauvais_jeton.status_code == 401, mauvais_jeton.text

    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM fiche_validation WHERE action = 'valider'")
            assert int(cursor.fetchone()[0]) == 0


def test_validation_attribuee_au_compte_connecte(base_comptes_corpus: dict[str, Any]) -> None:
    """LE test du lot L.2 : « qui a validé quoi » — sabotage visé ``id_utilisateur = 1``.

    Deux comptes sont créés. La validation est faite sous la session du SECOND.
    Si le code écrivait un identifiant en dur (1), ou l'identifiant du premier
    compte, l'assertion porte sur une valeur calculée — jamais sur une
    constante — et le test ROUGIT.
    """
    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    base = base_comptes_corpus
    index = base["index"]
    premier = _creer(base, "premier", "Secret!2026")
    second = _creer(base, "second", "Secret!2026")
    assert premier["id_utilisateur"] != second["id_utilisateur"]

    config = AppConfig(
        root_paths=[RACINE],
        database_path=RACINE / "unused-attribution.db",
        database_url=base["url"],
        auth_token=JETON_SERVICE,
    )
    client = TestClient(create_app(config))
    cookie = _ouvrir_cookie(base, "second", "Secret!2026")
    assert cookie["id_utilisateur"] == second["id_utilisateur"]

    code = "1001-GV-006"  # fiche a_valider du corpus
    reponse = client.post(
        f"/fiches/{code}/valider",
        # Le corps prétend qu'un AUTRE a validé : il doit être ignoré.
        json={"utilisateur": "premier", "commentaire": "validation sous session nominative"},
        headers={
            **_entetes_session(cookie),
            "X-SEAMTECH-UTILISATEUR": "second",
            "X-SEAMTECH-ROLE": "operateur",
        },
    )
    assert reponse.status_code == 200, reponse.text

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT v.id_utilisateur, u.identifiant, v.etat_apres, v.commentaire "
                "FROM fiche_validation v LEFT JOIN utilisateur u ON u.id_utilisateur = v.id_utilisateur "
                "WHERE v.action = 'valider' ORDER BY v.id_validation DESC LIMIT 1"
            )
            id_utilisateur, identifiant, etat_apres, commentaire = cursor.fetchone()
            cursor.execute("SELECT statut FROM fiche WHERE code = %s", (code,))
            statut = cursor.fetchone()[0]

    assert id_utilisateur == second["id_utilisateur"], (
        f"attribution = {id_utilisateur} (attendu {second['id_utilisateur']}, "
        f"premier compte = {premier['id_utilisateur']}) : la session doit primer sur le corps."
    )
    assert identifiant == "second"
    assert etat_apres == "valide" and statut == "valide"
    assert commentaire == "validation sous session nominative"


def test_indicateur_qualite_taux_par_utilisateur(base_comptes_corpus: dict[str, Any]) -> None:
    base = base_comptes_corpus
    index = base["index"]
    second = _creer(base, "second", "Secret!2026")

    from seamtech_search.fiches.routes import valider_fiche

    valider_fiche(index, "1001-GV-006", "second", "via indicateur")
    assert second["id_utilisateur"] > 0

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            indicateur = tableau.taux_par_utilisateur(cursor)

    assert indicateur["actions_total"] >= 1
    assert indicateur["actions_attribuees"] >= 1
    assert indicateur["actions_sans_utilisateur"] == indicateur["actions_total"] - indicateur["actions_attribuees"]
    assert indicateur["part_attribuee"] == round(
        indicateur["actions_attribuees"] / indicateur["actions_total"], 4
    )
    entree = next(e for e in indicateur["par_utilisateur"] if e["identifiant"] == "second")
    assert entree["actions"] == indicateur["actions_attribuees"]
    assert entree["role"] == "operateur"
    assert "sans_utilisateur" not in " ".join(indicateur["par_utilisateur"][0].keys())


# ---------------------------------------------------------------------------
# 4. CLI
# ---------------------------------------------------------------------------


def test_cli_lit_le_mot_de_passe_sur_stdin(
    base_comptes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    base = base_comptes
    monkeypatch.setattr("sys.stdin", io.StringIO("Depuis-Stdin!2026\n"))

    assert cli_comptes.main(
        ["--database-url", base["url"], "creer", "--identifiant", "cli-op", "--nom", "Opérateur CLI"]
    ) == 0
    assert cli_comptes.main(["--database-url", base["url"], "--json", "lister"]) == 0
    assert cli_comptes.main(["--database-url", base["url"], "sessions", "--identifiant", "cli-op"]) == 0

    # Le mot de passe lu sur STDIN est bien celui qui a été enregistré.
    assert verifier_identifiants(base["index"], "cli-op", "Depuis-Stdin!2026")["identifiant"] == "cli-op"

    # Sans URL : refus explicite, jamais un demi-fonctionnement silencieux.
    # (`SearchIndex` ne connaît QUE SQLite sans URL et PostgreSQL avec : il n'y a
    # pas de troisième cas à tester ici.)
    monkeypatch.delenv("SEAMTECH_TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("SEAMTECH_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as sans_url:
        cli_comptes.main(["lister"])
    assert "PostgreSQL" in str(sans_url.value)
