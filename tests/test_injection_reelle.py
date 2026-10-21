"""Lot 0b — audit filtres/recherches : injection réelle + vérif statique paramétrisation.

- Vérifie que get_audit_logs_filtered traite une injection comme valeur (pas d'erreur, pas de DROP)
- Vérifie que aucune requête n'utilise de concat/f-string pour les valeurs (voir script verif)
"""
from __future__ import annotations

import os
import pathlib
import uuid
from pathlib import Path

import pytest

from seamtech_search.indexer import SearchIndex

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")


@pytest.mark.postgres
def test_audit_logs_injection_reelle(tmp_path: Path):
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL")
    import psycopg2
    from psycopg2 import sql as pgsql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom = f"inject_{uuid.uuid4().hex[:8]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cur:
            cur.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(nom)))
    finally:
        admin.close()

    url_base = f"{URL_PG.rsplit('/',1)[0]}/{nom}"
    index = SearchIndex(tmp_path / f"{nom}.db", url_base)
    try:
        index.initialize()
        index.run_migrations()

        from seamtech_search.audit_logs import get_audit_logs_filtered, record_audit_log
        from seamtech_search.comptes.comptes import creer_utilisateur

        # Crée un vrai utilisateur pour FK (sinon actor_user_id=1 viole FK et l'insert est avalé)
        u = creer_utilisateur(index, "alice", "Alice Test", "Secret123!", "administrateur")
        alice_id = u["id_utilisateur"]

        # Écrit un événement normal (acteur système None est aussi valide, mais on teste avec FK valide)
        record_audit_log(index, actor_user_id=alice_id, actor_username="alice", actor_role="administrateur", action="AUTH_LOGIN_SUCCESS", target_type="utilisateur", target_id="alice", outcome="success", details={"test": 1})
        # Vérifie que l'événement de référence existe
        ref = get_audit_logs_filtered(index, limit=10, offset=0, action="AUTH_LOGIN_SUCCESS")
        assert len(ref) >= 1, "événement de référence non retrouvé"
        assert any(r["actor_username"] == "alice" for r in ref)

        # Injection dans filtres : doit être traitée comme valeur, pas exécutée, et retourner 0 ligne
        payloads = [
            "'; DROP TABLE audit_logs; --",
            "' OR '1'='1",
            "admin'--",
            "test\"; SELECT pg_sleep(1); --",
            "action = 'success' OR outcome='success'",
        ]
        for payload in payloads:
            # Ces appels ne doivent jamais lever, et la table doit survivre
            res = get_audit_logs_filtered(index, limit=10, offset=0, action=payload)
            assert isinstance(res, list)
            assert len(res) == 0, f"payload {payload!r} aurait dû retourner 0 ligne, got {len(res)}"
            # Vérifie que la table et les données existent toujours
            with index.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.audit_logs')")
                    assert cur.fetchone()[0] is not None, f"table audit_logs détruite par payload {payload!r}"
                    cur.execute("SELECT COUNT(*) FROM audit_logs WHERE actor_username='alice'")
                    assert int(cur.fetchone()[0]) >= 1, "données altérées par injection"

            # Test idem pour actor_role, target_type (doivent aussi retourner 0)
            assert len(get_audit_logs_filtered(index, limit=5, actor_role=payload)) == 0
            assert len(get_audit_logs_filtered(index, limit=5, target_type=payload)) == 0

        # outcome invalide doit être rejeté avant SQL (422)
        with pytest.raises(ValueError):
            get_audit_logs_filtered(index, outcome="invalid_payload")

        # Tri injection : sort est whitelisté, doit lever
        with pytest.raises(ValueError):
            get_audit_logs_filtered(index, sort="'; DROP TABLE audit_logs; --")

    finally:
        index.close()
        admin = psycopg2.connect(URL_PG)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin.cursor() as cur:
                cur.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nom)))
        finally:
            admin.close()


def test_verif_statique_parametrisation():
    """Aucune requête avec concat/f-string pour les valeurs."""
    p = pathlib.Path("seamtech_search/audit_logs.py").read_text(encoding="utf-8")
    # L'ancien code avait where_sql/order_sql concat + f-string; le nouveau doit avoir des constantes statiques
    assert "where_sql" not in p.lower() or "_SQL_AUDIT_" in p, "ancien where_sql concat encore présent sans constantes statiques"
    # Vérifie que les constantes statiques existent
    assert "_SQL_AUDIT_PG_CREATED_AT_DESC" in p
    assert "_SQL_AUDIT_SQLITE_CREATED_AT_DESC" in p
    # Aucune f-string qui interpole sort/order/where
    assert 'f"ORDER BY {sort}' not in p
    assert "f'ORDER BY {sort}" not in p
    assert 'f"SELECT' not in p or "_SQL_AUDIT" in p  # seul les constantes sont des f-strings autorisées? Ici on n'a plus de f-string SQL du tout

    # Vérifie via le script dédié
    import subprocess
    import sys
    res = subprocess.run([sys.executable, "scripts/verif_sql_parametrise.py"], capture_output=True, text=True)
    assert res.returncode == 0, f"verif_sql_parametrise échoué: {res.stdout} {res.stderr}"
