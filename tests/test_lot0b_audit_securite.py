"""Lot 0b — journal d'audit, anti-IDOR et réduction de surface.

Les tests PostgreSQL créent une base jetable réelle (même patron que les
autres suites d'intégration) : la migration 025, la chaîne d'empreintes, le
trigger append-only et les gardes de session sont prouvés contre le moteur
qui les portera en production, pas contre un double.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.audit import get_audit_logs, record_audit_event
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.models import Document
from seamtech_search.securite_requetes import (
    exiger_administrateur_requete,
    resoudre_acteur_audit,
    resoudre_acteur_requete,
)

JETON_SERVICE = "jeton-service-lot0b"


def _config_sqlite(tmp_path: Path) -> AppConfig:
    racine = tmp_path / "archive"
    racine.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        root_paths=[racine],
        database_path=tmp_path / "data" / "search.db",
        auth_token=JETON_SERVICE,
        min_free_bytes=0,
    )


def test_audit_expurgation_des_secrets_sqlite(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "audit.db")
    index.initialize(rebuild=True)
    try:
        record_audit_event(
            index,
            action="test_secret",
            actor="token:abc",
            resource="ressource",
            status="200",
            details={
                "mot_de_passe": "ne-jamais-persister",
                "nested": {"jeton_session": "secret-de-session"},
                "authorization": "Bearer secret",
                "visible": "valeur-ok",
                "erreur": (
                    "postgresql://chef:motdepasse-url@db/base "
                    "Authorization: Bearer jeton-dans-erreur password=secret-texte"
                ),
            },
        )
        logs = get_audit_logs(index, limit=5)
    finally:
        index.close()

    assert logs
    details = logs[0]["details"]
    assert details["mot_de_passe"] == "[REDACTED]"
    assert details["nested"]["jeton_session"] == "[REDACTED]"
    assert details["authorization"] == "[REDACTED]"
    assert details["visible"] == "valeur-ok"
    assert "ne-jamais-persister" not in repr(logs)
    assert "secret-de-session" not in repr(logs)
    assert "motdepasse-url" not in repr(logs)
    assert "jeton-dans-erreur" not in repr(logs)
    assert "secret-texte" not in repr(logs)


def test_resolveur_acteur_service_secours_et_refus() -> None:
    config = SimpleNamespace(auth_token=JETON_SERVICE, session_idle_minutes=30)
    index_sqlite = SimpleNamespace(is_postgres=False)
    index_pg = SimpleNamespace(is_postgres=True)

    service = resoudre_acteur_requete(index_pg, config, token=JETON_SERVICE)
    assert service.type_acteur == "service"
    assert not service.est_humain

    secours = resoudre_acteur_requete(
        index_pg,
        config,
        token=JETON_SERVICE,
        entete_utilisateur="secours",
        entete_role="admin",
    )
    assert secours.type_acteur == "secours"
    assert secours.est_administrateur

    with pytest.raises(HTTPException) as refus:
        resoudre_acteur_requete(
            index_pg,
            config,
            token="mauvais",
            entete_utilisateur="secours",
            entete_role="admin",
        )
    assert refus.value.status_code == 401

    with pytest.raises(HTTPException) as sans_session:
        resoudre_acteur_requete(
            index_pg,
            config,
            token=JETON_SERVICE,
            entete_utilisateur="alice",
            entete_role="approbateur",
        )
    assert sans_session.value.status_code == 401

    with pytest.raises(HTTPException) as admin_pg:
        exiger_administrateur_requete(index_pg, config, token=JETON_SERVICE)
    assert admin_pg.value.status_code == 401

    # SQLite : pas de comptes nominatifs, l'acteur de service déjà authentifié
    # par la route reste le contrat d'outillage explicite.
    assert exiger_administrateur_requete(index_sqlite, config, token=JETON_SERVICE).type_acteur == "service"


def test_acteur_audit_ne_croit_jamais_des_entetes_non_verifies() -> None:
    """Une identité usurpée ne doit pas devenir une preuve dans le journal."""
    config = SimpleNamespace(auth_token=JETON_SERVICE, session_idle_minutes=30)
    index_pg = SimpleNamespace(is_postgres=True)

    acteur = resoudre_acteur_audit(
        index_pg,
        config,
        {
            "X-SEAMTECH-TOKEN": JETON_SERVICE,
            "X-SEAMTECH-UTILISATEUR": "secours",
            "X-SEAMTECH-ROLE": "approbateur",  # incompatible avec le secours
            "X-SEAMTECH-SESSION": "999",
        },
        client_host="127.0.0.1",
    )

    assert acteur.type_acteur == "service"
    assert acteur.identifiant is None
    assert acteur.id_session is None


def test_sql_dynamique_identifiants_bornes() -> None:
    """Les identifiants SQL non liables restent sur liste fermée ou cités."""
    from seamtech_search.jobs import COLONNES_MAJ_JOB_AUTORISEES

    assert COLONNES_MAJ_JOB_AUTORISEES == frozenset(
        {"status", "progress", "stage", "error", "failure_reason", "result"}
    )
    assert SearchIndex._ident_sqlite('table"; DROP TABLE documents; --') == (
        '"table""; DROP TABLE documents; --"'
    )


def test_middleware_journalise_les_mutations_sqlite(tmp_path: Path) -> None:
    config = _config_sqlite(tmp_path)
    with TestClient(create_app(config)) as client:
        reponse = client.post(
            "/maintenance/cleanup",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE},
        )
        assert reponse.status_code == 200
        audit = client.get("/audit", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        assert audit.status_code == 200

    resultats = audit.json()["results"]
    http_events = [
        evenement
        for evenement in resultats
        if evenement["action"] == "http_request" and evenement["resource"] == "/maintenance/cleanup"
    ]
    assert http_events
    assert http_events[0]["status"] == "200"
    assert http_events[0]["details"] == {"method": "POST", "path": "/maintenance/cleanup"}


def test_artefact_hors_racines_nest_jamais_servi(tmp_path: Path) -> None:
    config = _config_sqlite(tmp_path)
    secret = tmp_path / "hors-racine" / "technical-report.pdf"
    secret.parent.mkdir(parents=True)
    secret.write_bytes(b"%PDF-secret-hors-racine")

    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    try:
        with index.connect() as connexion:
            connexion.execute(
                "INSERT INTO imports (id, source_path, status, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    "IMP-LOT0B",
                    str(tmp_path / "archive"),
                    "completed",
                    json.dumps({"report_path": str(secret), "artifacts": [], "files": []}),
                    "2026-10-24T00:00:00+00:00",
                ),
            )
    finally:
        index.close()

    with TestClient(create_app(config)) as client:
        reponse = client.get(
            "/imports/IMP-LOT0B/artifacts/report_pdf",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE},
        )

    assert reponse.status_code == 404
    assert b"secret-hors-racine" not in reponse.content


def test_frontiere_frontend_transmet_la_session_aux_routes_de_donnees() -> None:
    """Garde statique : hors auth/health, aucun proxy de données en service seul."""
    racine = Path(__file__).resolve().parents[1] / "frontend" / "app" / "api"
    exclus = {
        Path("auth/login/route.ts"),
        Path("auth/logout/route.ts"),
        Path("auth/logout-all/route.ts"),
        Path("auth/session/route.ts"),
        Path("health/route.ts"),
    }
    fautifs: list[str] = []
    for chemin in sorted(racine.rglob("route.ts")):
        relatif = chemin.relative_to(racine)
        if relatif in exclus:
            continue
        if "authHeaders()" in chemin.read_text(encoding="utf-8"):
            fautifs.append(str(relatif))
    assert fautifs == []

    artefact = (racine / "imports/[id]/artifacts/[artifact]/route.ts").read_text(encoding="utf-8")
    assert "await backendHeaders()" in artefact


@pytest.mark.postgres
def test_lot0b_postgres_audit_append_only_et_gardes(tmp_path: Path) -> None:
    url_admin = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
    if not url_admin:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    from seamtech_search.comptes.comptes import creer_utilisateur, ouvrir_session

    nom_base = f"lot0b_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(url_admin)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as curseur:
            curseur.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        admin.close()

    url_base = url_admin.rsplit("/", 1)[0] + f"/{nom_base}"
    racine = tmp_path / "archive"
    racine.mkdir()
    index = SearchIndex(tmp_path / "inutilise.db", url_base)
    try:
        index.initialize()
        # Événement antérieur à la migration : il doit être RECOPIÉ et chaîné.
        record_audit_event(
            index,
            action="connexion_refusee",
            actor="ancien-compte",
            resource="/auth/connexion",
            status="401",
            details={"mot_de_passe": "ancien-secret-a-expurger"},
        )
        index.run_migrations()

        with index.connect() as connexion:
            with connexion.cursor() as curseur:
                curseur.execute("SELECT version FROM schema_migrations ORDER BY version")
                versions = [ligne[0] for ligne in curseur.fetchall()]
                curseur.execute(
                    "SELECT action, actor, details, empreinte_precedente, empreinte "
                    "FROM audit_logs ORDER BY id_audit"
                )
                lignes = curseur.fetchall()
        assert versions[-6:] == [
            "024_sessions_redis",
            "025_audit_logs",
            "026_boat_models_aliases",
            "027_roles_approbations",
            "028_sail_extractions",
            "029_extraction_liaison_corrections",
        ]
        assert len(lignes) == 1
        assert lignes[0][0] == "connexion_refusee"
        assert lignes[0][2]["mot_de_passe"] == "[REDACTED]"
        assert lignes[0][2]["migration"] == "025_audit_logs"
        assert lignes[0][3] is None
        assert len(lignes[0][4]) == 64

        administrateur_compte = creer_utilisateur(
            index,
            "admin-lot0b",
            "Administrateur Lot 0b",
            "mot-de-passe-admin-lot0b",
            role="admin",
            doit_changer_mot_de_passe=False,
        )
        operateur_compte = creer_utilisateur(
            index,
            "operateur-lot0b",
            "Opérateur Lot 0b",
            "mot-de-passe-operateur-lot0b",
            role="approbateur",
            doit_changer_mot_de_passe=False,
        )
        session_admin = ouvrir_session(index, administrateur_compte["id_utilisateur"])
        session_operateur = ouvrir_session(index, operateur_compte["id_utilisateur"])

        config = AppConfig(
            root_paths=[racine],
            database_path=tmp_path / "api-inutilise.db",
            database_url=url_base,
            auth_token=JETON_SERVICE,
            min_free_bytes=0,
        )

        def entetes_session(compte: dict[str, Any], session: dict[str, Any], role: str) -> dict[str, str]:
            return {
                "X-SEAMTECH-TOKEN": JETON_SERVICE,
                "X-SEAMTECH-UTILISATEUR": str(compte["identifiant"]),
                "X-SEAMTECH-ROLE": role,
                "X-SEAMTECH-SESSION": str(session["id_session"]),
                "X-SEAMTECH-SESSION-JETON": str(session["jeton"]),
            }

        entetes_admin = entetes_session(administrateur_compte, session_admin, "admin")
        entetes_operateur = entetes_session(operateur_compte, session_operateur, "approbateur")

        # Le résolveur relit l'identité en base : un en-tête usurpé est refusé.
        with pytest.raises(HTTPException) as usurpation:
            resoudre_acteur_requete(
                index,
                config,
                token=JETON_SERVICE,
                entete_utilisateur="admin-lot0b",
                entete_role="admin",
                entete_session=str(session_operateur["id_session"]),
                entete_jeton=str(session_operateur["jeton"]),
            )
        assert usurpation.value.status_code == 401

        # /open : sous racine ne suffit plus en PostgreSQL, il faut le catalogue.
        non_catalogue = racine / "non-catalogue.pdf"
        non_catalogue.write_bytes(b"%PDF-non-catalogue")
        catalogue = racine / "catalogue.pdf"
        contenu_catalogue = b"%PDF-catalogue-lot0b"
        catalogue.write_bytes(contenu_catalogue)
        index.upsert_document(
            Document(
                path=catalogue,
                name=catalogue.name,
                parent_path=racine,
                extension=".pdf",
                size=catalogue.stat().st_size,
                modified_at=catalogue.stat().st_mtime,
                is_dir=False,
            )
        )

        with TestClient(create_app(config)) as client:
            service_seul = {"X-SEAMTECH-TOKEN": JETON_SERVICE}
            assert client.get("/audit", headers=service_seul).status_code == 401
            assert client.get("/audit", headers=entetes_operateur).status_code == 403
            audit_admin = client.get("/audit", headers=entetes_admin)
            assert audit_admin.status_code == 200
            assert audit_admin.json()["results"]
            assert "empreinte" in audit_admin.json()["results"][0]

            assert client.get("/metrics", headers=service_seul).status_code == 401
            assert client.get("/metrics", headers=entetes_operateur).status_code == 403

            refuse = client.post("/open", params={"path": str(non_catalogue)}, headers=service_seul)
            assert refuse.status_code == 404
            accepte = client.post("/open", params={"path": str(catalogue)}, headers=service_seul)
            assert accepte.status_code == 200
            assert accepte.content == contenu_catalogue

        # Chaîne : chaque nouvel événement pointe sur l'empreinte précédente.
        record_audit_event(
            index,
            action="lot0b_chaine",
            actor="admin-lot0b",
            resource="piece:42",
            status="200",
            details={"jeton_session": "a-ne-jamais-voir", "visible": True},
            actor_type="utilisateur",
            id_utilisateur=int(administrateur_compte["id_utilisateur"]),
            identifiant="admin-lot0b",
            role="admin",
            id_session=int(session_admin["id_session"]),
            resource_type="piece",
            resource_id="42",
            code_statut=200,
        )
        with index.connect() as connexion:
            with connexion.cursor() as curseur:
                curseur.execute(
                    "SELECT action, details, empreinte_precedente, empreinte FROM audit_logs ORDER BY id_audit"
                )
                chaine = curseur.fetchall()
        assert len(chaine) >= 2
        for precedente, courante in zip(chaine, chaine[1:]):
            assert courante[2] == precedente[3]
        assert chaine[-1][0] == "lot0b_chaine"
        assert chaine[-1][1]["jeton_session"] == "[REDACTED]"

        # Append-only porté par PostgreSQL : UPDATE et DELETE refusés.
        with pytest.raises(Exception):
            with index.connect() as connexion:
                with connexion.cursor() as curseur:
                    curseur.execute("UPDATE audit_logs SET action = 'modifie' WHERE id_audit = 1")
        with pytest.raises(Exception):
            with index.connect() as connexion:
                with connexion.cursor() as curseur:
                    curseur.execute("DELETE FROM audit_logs WHERE id_audit = 1")
    finally:
        index.close()
        admin = psycopg2.connect(url_admin)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin.cursor() as curseur:
                curseur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (nom_base,),
                )
                curseur.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
        finally:
            admin.close()
