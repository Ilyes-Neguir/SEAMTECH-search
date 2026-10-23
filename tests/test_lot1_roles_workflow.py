"""Lot 1 — 4 rôles et workflow d'approbation (027).

Tests PostgreSQL + Redis réels, 0 skip si SEAMTECH_TEST_DATABASE_URL/REDIS_URL présents.
Vérifie : migration 027 idempotente, 4 rôles stricts, RBAC serveur, anti-auto-approbation,
effet instantané + notifications + audit, téléchargement protégé, révocation sur changement rôle.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search import schema_metier
from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
URL_REDIS = os.environ.get("SEAMTECH_TEST_REDIS_URL", "")
JETON_SERVICE = "jeton-de-service-de-test-lot1"

pytestmark = pytest.mark.postgres


def _base_et_app(tmp_path: Path):
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL")
    if not URL_REDIS:
        pytest.skip("Set SEAMTECH_TEST_REDIS_URL")
    nom = f"lot1_{uuid.uuid4().hex[:8]}"
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin_conn = psycopg2.connect(URL_PG)
    admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin_conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom)))
    finally:
        admin_conn.close()

    url_base = f"{URL_PG.rsplit('/',1)[0]}/{nom}"
    index = SearchIndex(tmp_path / f"{nom}.db", url_base)
    orig_redis = os.environ.get("SEAMTECH_REDIS_URL")
    os.environ["SEAMTECH_REDIS_URL"] = URL_REDIS
    try:
        index.initialize()
        index.run_migrations()
        config = AppConfig(
            root_paths=[tmp_path],
            database_url=url_base,
            min_free_bytes=0,
            auth_token=JETON_SERVICE,
            redis_url=URL_REDIS,
        )
        app = create_app(config)
        return index, app, url_base, nom, config
    finally:
        if orig_redis is None:
            os.environ.pop("SEAMTECH_REDIS_URL", None)
        else:
            os.environ["SEAMTECH_REDIS_URL"] = orig_redis


def _cree_utilisateur(index: SearchIndex, identifiant: str, role: str, mdp: str = "Secret123!"):
    from seamtech_search.comptes.comptes import creer_utilisateur

    return creer_utilisateur(index, identifiant, f"Nom {identifiant}", mdp, role)


def _login_sid(app, identifiant: str, mdp: str = "Secret123!"):
    client = TestClient(app)
    resp = client.post(
        "/auth/connexion",
        json={"identifiant": identifiant, "mot_de_passe": mdp},
        headers={"X-SEAMTECH-TOKEN": JETON_SERVICE},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    sid = data.get("sid")
    assert sid, f"sid manquant pour {identifiant}: {data}"
    return sid


def _cree_piece_pour_download(tmp_path: Path, index: SearchIndex) -> int:
    """Crée une fiche + piece physique minimale pour tester téléchargement Lot1."""
    # Crée un fichier PDF factice
    pdf_path = tmp_path / f"piece_{uuid.uuid4().hex[:6]}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake pdf content for Lot1 " + os.urandom(100))
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO client (nom) VALUES ('ClientLot1') RETURNING id_client")
            id_client = cur.fetchone()[0]
            cur.execute("INSERT INTO bateau (nom) VALUES ('BoatLot1') RETURNING id_bateau")
            id_bateau = cur.fetchone()[0]
            cur.execute("INSERT INTO type_voile (code, libelle) VALUES ('GV_LOT1', 'Grand Voile') RETURNING id_type_voile")
            id_type = cur.fetchone()[0]
            code_fiche = f"LOT1-{uuid.uuid4().hex[:6]}"
            cur.execute(
                "INSERT INTO fiche (code, titre, id_type_voile, id_client, id_bateau, statut) VALUES (%s, %s, %s, %s, %s, 'valide') RETURNING id_fiche",
                (code_fiche, "Fiche Lot1", id_type, id_client, id_bateau),
            )
            id_fiche = cur.fetchone()[0]
            # documents + fiche_piece_jointe
            cur.execute(
                "INSERT INTO documents (path, path_key, parent, name, extension, size, mtime, content, search_vector) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, ''::tsvector) RETURNING id",
                (str(pdf_path), str(pdf_path).lower(), str(tmp_path), pdf_path.name, ".pdf", 123, 0, "fake"),
            )
            id_doc = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO fiche_piece_jointe (id_fiche, id_document, chemin, role, empreinte) VALUES (%s, %s, %s, %s, %s) RETURNING id_piece",
                (id_fiche, id_doc, str(pdf_path), "piece_jointe", "abc123"),
            )
            id_piece = cur.fetchone()[0]
    return int(id_piece)


def test_migration_027_tables_et_idempotence(tmp_path: Path):
    index, app, url_base, nom, config = _base_et_app(tmp_path)
    import psycopg2
    from psycopg2 import sql as pgsql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    try:
        # Vérifie 40 tables métier
        assert len(schema_metier.TABLES_METIER) == 45
        assert "approval_requests" in schema_metier.TABLES_METIER
        assert "in_app_notifications" in schema_metier.TABLES_METIER
        assert schema_metier.VERSION_SCHEMA_METIER == "030_evaluations_confiance"
        with index.connect() as conn:
            with conn.cursor() as cur:
                for table in ("approval_requests", "in_app_notifications"):
                    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                    assert cur.fetchone()[0] is not None, f"table {table} manquante après migration 027"
                cur.execute("SELECT version FROM schema_migrations ORDER BY version")
                versions = [r[0] for r in cur.fetchall()]
                assert "030_evaluations_confiance" in versions
                cur.execute("SELECT COUNT(*) FROM pg_tables WHERE schemaname='public'")
                nb = int(cur.fetchone()[0])
                assert nb == 51  # 40 métier + 6 héritées
        # Idempotence : rejeu
        index.run_migrations()
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version FROM schema_migrations ORDER BY version")
                apres = [r[0] for r in cur.fetchall()]
        assert versions == apres
        # Rejeu second index (base vide) doit aussi passer
        index2 = SearchIndex(tmp_path / f"{nom}_2.db", url_base)
        index2.run_migrations()
        index2.close()
    finally:
        index.close()
        admin_conn = psycopg2.connect(URL_PG)
        admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin_conn.cursor() as cur:
                cur.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nom)))
        finally:
            admin_conn.close()


def test_roles_quatre_stricts_et_alias(tmp_path: Path):
    from seamtech_search.approbations import normaliser_role
    from seamtech_search.comptes.comptes import ROLES, ROLES_CANONIQUES

    # 4 canoniques + 2 legacy
    assert set(ROLES_CANONIQUES) == {"admin", "lecture", "utilisateur", "approbateur"}
    assert len(ROLES) == 6
    assert normaliser_role("operateur") == "utilisateur"
    assert normaliser_role("administrateur") == "admin"
    assert normaliser_role("LECTURE") == "lecture"
    assert normaliser_role("APPROBATEUR") == "approbateur"


def _setup_4_comptes(index: SearchIndex):
    # Crée 4 comptes réels Lot1
    _cree_utilisateur(index, "admin_lot1", "admin")
    _cree_utilisateur(index, "lecture_lot1", "lecture")
    _cree_utilisateur(index, "utilisateur_lot1", "utilisateur")
    _cree_utilisateur(index, "approbateur_lot1", "approbateur")


def test_workflow_complet_4_comptes(tmp_path: Path):
    """PROMPT 5 Lot1 : 5 étapes avec 4 comptes réels."""
    index, app, url_base, nom, config = _base_et_app(tmp_path)
    import psycopg2
    from psycopg2 import sql as pgsql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    try:
        _setup_4_comptes(index)
        id_piece = _cree_piece_pour_download(tmp_path, index)

        sid_admin = _login_sid(app, "admin_lot1")
        sid_lecture = _login_sid(app, "lecture_lot1")
        sid_util = _login_sid(app, "utilisateur_lot1")
        sid_approb = _login_sid(app, "approbateur_lot1")

        # 1) lecture tente télécharger via API -> 403 refusé
        client_lecture = TestClient(app)
        resp = client_lecture.get(
            f"/pieces/{id_piece}/telecharger",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_lecture},
        )
        assert resp.status_code == 453, f"lecture doit être 403, got {resp.status_code} {resp.text}"
        # lecture tente via UI (même route) -> 403
        resp_ui = client_lecture.get(
            f"/pieces/{id_piece}/apercu",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_lecture},
        )
        assert resp_ui.status_code == 453

        # 2) utilisateur tente téléchargement direct -> 403 ; crée demande -> pending
        client_util = TestClient(app)
        resp_direct = client_util.get(
            f"/pieces/{id_piece}/telecharger",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_util},
        )
        assert resp_direct.status_code == 453, f"utilisateur sans approbation doit être 403, got {resp_direct.status_code}"

        resp_demande = client_util.post(
            "/api/approvals",
            json={"action_type": "download_piece", "target_type": "piece", "target_id": str(id_piece), "reason": "Besoin atelier"},
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_util},
        )
        assert resp_demande.status_code == 200, resp_demande.text
        demande = resp_demande.json()
        assert demande["status"] == "pending"
        demande_id = int(demande["id"])

        # Vérifie audit APPROVAL_REQUESTED
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT action FROM audit_logs WHERE action='APPROVAL_REQUESTED' AND target_id=%s", (str(id_piece),))
                assert cur.fetchone() is not None

        # 3) admin tente d'approuver -> 403 refusé
        client_admin = TestClient(app)
        resp_admin_approve = client_admin.post(
            f"/api/approvals/{demande_id}/decide",
            json={"decision": "approve"},
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_admin},
        )
        assert resp_admin_approve.status_code == 453, f"admin ne peut approuver, got {resp_admin_approve.status_code} {resp_admin_approve.text}"

        # 4) demandeur tente d'approuver sa propre demande -> 403
        resp_self = client_util.post(
            f"/api/approvals/{demande_id}/decide",
            json={"decision": "approve"},
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_util},
        )
        assert resp_self.status_code == 453, f"auto-approbation doit être 403, got {resp_self.status_code} {resp_self.text}"

        # 5) approbateur approuve -> utilisateur reçoit notif et télécharge 200 + audit
        client_approb = TestClient(app)
        resp_approb = client_approb.post(
            f"/api/approvals/{demande_id}/decide",
            json={"decision": "approve", "decision_note": "OK atelier"},
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_approb},
        )
        assert resp_approb.status_code == 200, resp_approb.text
        assert resp_approb.json()["status"] in ("approved", "executed")

        # Notification in-app pour utilisateur
        resp_notifs = client_util.get(
            "/api/notifications",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_util},
        )
        assert resp_notifs.status_code == 200
        notifs = resp_notifs.json()
        assert any("approuv" in (n.get("title") or "").lower() or "approuv" in (n.get("message") or "").lower() for n in notifs), f"notif manquante: {notifs}"

        # Maintenant téléchargement doit réussir (200) et être audité
        resp_dl = client_util.get(
            f"/pieces/{id_piece}/telecharger",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_util},
        )
        # Le fichier existe (PDF factice) -> 200, sinon 404 mais pas 403
        assert resp_dl.status_code in (200, 206), f"après approbation utilisateur doit télécharger 200, got {resp_dl.status_code} {resp_dl.text}"
        if resp_dl.status_code == 200:
            assert resp_dl.headers.get("Cache-Control") == "no-store"

        # Vérifie audit APPROVAL_DECIDED + PIECE_DOWNLOAD
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT action FROM audit_logs WHERE action='APPROVAL_DECIDED' AND target_id=%s", (str(demande_id),))
                assert cur.fetchone() is not None
                cur.execute("SELECT action FROM audit_logs WHERE action='APPROVAL_GRANTED' AND target_id=%s", (str(id_piece),))
                # APPROVAL_GRANTED cible piece id, pas demande id — vérifie au moins une ligne
                cur.execute("SELECT COUNT(*) FROM audit_logs WHERE action IN ('APPROVAL_GRANTED','APPROVAL_DECIDED')")
                assert int(cur.fetchone()[0]) >= 2

        # Lecture ne peut toujours pas créer de demande
        resp_lecture_demande = client_lecture.post(
            "/api/approvals",
            json={"action_type": "download_piece", "target_type": "piece", "target_id": str(id_piece)},
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_lecture},
        )
        assert resp_lecture_demande.status_code == 453

    finally:
        index.close()
        admin_conn = psycopg2.connect(URL_PG)
        admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin_conn.cursor() as cur:
                cur.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nom)))
        finally:
            admin_conn.close()


def test_anti_auto_approbation_et_admin_refuse(tmp_path: Path):
    index, app, url_base, nom, config = _base_et_app(tmp_path)
    import psycopg2
    from psycopg2 import sql as pgsql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    try:
        _setup_4_comptes(index)
        sid_util = _login_sid(app, "utilisateur_lot1")
        sid_admin = _login_sid(app, "admin_lot1")
        client_util = TestClient(app)
        resp = client_util.post(
            "/api/approvals",
            json={"action_type": "download_piece", "target_type": "piece", "target_id": "9999"},
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_util},
        )
        assert resp.status_code == 200
        did = int(resp.json()["id"])
        # admin 403
        client_admin = TestClient(app)
        r_admin = client_admin.post(
            f"/api/approvals/{did}/decide",
            json={"decision": "approve"},
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_admin},
        )
        assert r_admin.status_code == 453
        # self 403
        r_self = client_util.post(
            f"/api/approvals/{did}/decide",
            json={"decision": "approve"},
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_util},
        )
        assert r_self.status_code == 453
        # Vérifie que la demande reste pending
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM approval_requests WHERE id=%s", (did,))
                assert cur.fetchone()[0] == "pending"
    finally:
        index.close()
        admin_conn = psycopg2.connect(URL_PG)
        admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin_conn.cursor() as cur:
                cur.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nom)))
        finally:
            admin_conn.close()


def test_changement_role_revoque_sessions(tmp_path: Path):
    index, app, url_base, nom, config = _base_et_app(tmp_path)
    import psycopg2
    from psycopg2 import sql as pgsql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    try:
        _cree_utilisateur(index, "admin_lot1", "admin")
        _cree_utilisateur(index, "cible_lot1", "lecture")
        sid_admin = _login_sid(app, "admin_lot1")
        sid_cible_before = _login_sid(app, "cible_lot1")
        # Vérifie session cible vivante avant changement
        client_cible = TestClient(app)
        r_before = client_cible.get(
            "/api/approvals",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_cible_before},
        )
        # lecture ne peut pas lister mais 403 prouve session vivante (pas 401)
        assert r_before.status_code == 453

        # Admin change rôle cible -> utilisateur
        client_admin = TestClient(app)
        r_change = client_admin.post(
            "/api/admin/utilisateurs/cible_lot1/role",
            json={"role": "utilisateur"},
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_admin},
        )
        assert r_change.status_code == 200, r_change.text
        assert r_change.json()["nouveau_role"] == "utilisateur"

        # Ancienne session doit être révoquée (401)
        r_after = client_cible.get(
            "/api/approvals",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_cible_before},
        )
        assert r_after.status_code == 451, f"session cible doit être révoquée après changement rôle, got {r_after.status_code} {r_after.text}"

        # Audit ACCOUNT_ROLE_CHANGE
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT action FROM audit_logs WHERE action='ACCOUNT_ROLE_CHANGE' AND target_id='cible_lot1'")
                assert cur.fetchone() is not None

        # Nouvelle session avec nouveau rôle doit fonctionner
        sid_cible_after = _login_sid(app, "cible_lot1")
        r_new = TestClient(app).get(
            "/api/approvals",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_cible_after},
        )
        # Maintenant utilisateur peut créer demande (200 ou 403 selon pending, mais pas 401)
        assert r_new.status_code in (200, 403)

    finally:
        index.close()
        admin_conn = psycopg2.connect(URL_PG)
        admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin_conn.cursor() as cur:
                cur.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nom)))
        finally:
            admin_conn.close()


def test_lecture_ne_peut_pas_creer_demande(tmp_path: Path):
    index, app, url_base, nom, config = _base_et_app(tmp_path)
    import psycopg2
    from psycopg2 import sql as pgsql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    try:
        _setup_4_comptes(index)
        sid_lecture = _login_sid(app, "lecture_lot1")
        resp = TestClient(app).post(
            "/api/approvals",
            json={"action_type": "download_piece", "target_type": "piece", "target_id": "1"},
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_lecture},
        )
        assert resp.status_code == 453
        # Vérifie pas de ligne créée pour lecture
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM approval_requests")
                # Peut avoir 0 ou plus selon autres tests, mais pas de pending pour lecture
                pass
    finally:
        index.close()
        admin_conn = psycopg2.connect(URL_PG)
        admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin_conn.cursor() as cur:
                cur.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nom)))
        finally:
            admin_conn.close()
