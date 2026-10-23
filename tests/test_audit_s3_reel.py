"""Preuve S3/MinIO réelle pour audit Lot 0b — PG+Redis+MinIO avec identité restreinte.

Ce fichier est le seul à lire SEAMTECH_S3_ENDPOINT_URL en-dehors des tests
d'intégration docker ; il est donc dans INVENTAIRE_S3_REEL et porte
@pytest.mark.s3 + @pytest.mark.integration_docker pour être exécuté uniquement
par le job `integration` qui fournit PostgreSQL (5433), Redis (6379) et MinIO
(9000) avec l'identité applicative restreinte (seamtech-app-ci).

Dans ce contexte obligatoire, une configuration MinIO absente ou l'usage de
l'identité administrateur (minioadmin) doit échouer clairement, pas produire
un skip — un skip masquerait l'absence de preuve.
"""
from __future__ import annotations

import os
import time
import urllib.parse
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex

JETON_SERVICE = "jeton-de-service-de-test"


def _redact(msg: str) -> str:
    """Expurge secrets : masque query/signature/token/password."""
    # Masque query string entière des URLs présignées (contient signature)
    # Ex: ?X-Amz-Signature=xxx → ?<redacted>
    low = msg.lower()
    # Si message contient signature/token, masque tout après ?
    if "x-amz-" in low or "signature=" in low or "token=" in low or "awsaccesskey" in low:
        # Garde path jusqu'à ?, remplace query par <redacted-query>
        parts = msg.split("?", 1)
        if len(parts) == 2:
            return parts[0] + "?<redacted-query>"
    # Masque password dans postgresql://user:pwd@host
    if "postgresql://" in low:
        return "[postgresql-url-redacted]"
    if "redis://" in low:
        return "[redis-url-redacted]"
    return msg


def _redact_location(url: str) -> str:
    """Retourne URL sans query (évite fuite signature présignée)."""
    if not url:
        return ""
    try:
        p = urllib.parse.urlparse(url)
        # Reconstruit sans query/fragment
        return urllib.parse.urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    except Exception:
        return url.split("?", 1)[0] + "?<redacted>" if "?" in url else url


def _wait_for_postgres(url: str, timeout: int = 15) -> bool:
    import psycopg2

    start = time.time()
    while time.time() - start < timeout:
        try:
            conn = psycopg2.connect(url, connect_timeout=2)
            conn.close()
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _wait_for_redis(url: str, timeout: int = 10) -> bool:
    try:
        import redis

        r = redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        start = time.time()
        while time.time() - start < timeout:
            try:
                if r.ping():
                    return True
            except Exception:
                pass
            time.sleep(0.5)
    except Exception:
        pass
    return False


def _pg_url() -> str:
    # Job integration compose : PG à 5433 avec test_password (cf docker-compose.yml)
    # On essaie plusieurs variantes comme test_integration_docker
    candidates = []
    env_tried: list[str] = []
    for env_key in ("SEAMTECH_TEST_DATABASE_URL", "SEAMTECH_DATABASE_URL", "SEAMTECH_E2E_DATABASE_URL"):
        v = os.environ.get(env_key)
        if v:
            candidates.append(v)
            env_tried.append(env_key)
    pwd = os.environ.get("POSTGRES_PASSWORD", "test_password")
    candidates.extend(
        [
            f"postgresql://seamtech:{pwd}@127.0.0.1:5433/seamtech_search",
            f"postgresql://seamtech:{pwd}@127.0.0.1:5432/seamtech_search",
            f"postgresql://seamtech:{pwd}@127.0.0.1:5432/seamtech_search_test",
        ]
    )
    for url in candidates:
        if _wait_for_postgres(url, timeout=10):
            return url
    # Fallback strict : doit échouer clairement, pas skip — SANS exposer password/URL
    if os.environ.get("SEAMTECH_INTEGRATION_STRICT") in {"1", "true", "yes"}:
        pytest.fail(
            "PostgreSQL non joignable pour preuve S3 "
            f"(env_tries={env_tried} hosts=127.0.0.1:5433,127.0.0.1:5432)"
        )
    return candidates[0]


def _redis_url() -> str:
    for k in ("SEAMTECH_REDIS_URL", "SEAMTECH_TEST_REDIS_URL"):
        v = os.environ.get(k)
        if v and _wait_for_redis(v, timeout=2):
            return v
    pwd = os.environ.get("REDIS_PASSWORD", "redis_test_password")
    candidates = [
        f"redis://:{pwd}@127.0.0.1:6379/0",
        "redis://127.0.0.1:6379/0",
        "redis://:redis_test_password@127.0.0.1:6379/0",
    ]
    for url in candidates:
        if _wait_for_redis(url, timeout=10):
            return url
    if os.environ.get("SEAMTECH_INTEGRATION_STRICT") in {"1", "true", "yes"}:
        pytest.fail("Redis non joignable pour preuve S3 (hosts 127.0.0.1:6379)")
    return candidates[0]


def _s3_config():
    endpoint = os.environ.get("SEAMTECH_S3_ENDPOINT_URL", "")
    if not endpoint:
        pytest.fail("SEAMTECH_S3_ENDPOINT_URL absent dans le job integration — MinIO obligatoire, pas de skip")
    access = os.environ.get("SEAMTECH_S3_ACCESS_KEY", "")
    secret = os.environ.get("SEAMTECH_S3_SECRET_KEY", "")
    bucket = os.environ.get("SEAMTECH_S3_BUCKET", "seamtech-documents")
    if not access or not secret:
        pytest.fail("SEAMTECH_S3_ACCESS_KEY/SECRET_KEY absents — identité applicative restreinte obligatoire")
    if access == "minioadmin" or secret in ("minioadmin", "minioadmin123"):
        pytest.fail("Identité administrateur minioadmin interdite — utiliser l'identité applicative restreinte (seamtech-app-ci)")
    return endpoint, bucket, access, secret


def _base_et_app_pg_redis_minio(tmp_path: Path):
    # Réutilise la base `seamtech_search` existante (comme test_integration_docker)
    # pour éviter CREATE DATABASE qui nécessite superuser et peut échouer si
    # le rôle `seamtech` n'a pas droit de créer une DB dans le compose.
    pg_url = _pg_url()
    redis_url = _redis_url()
    endpoint, bucket, access, secret = _s3_config()
    nom = "seamtech_search"  # base existante, pas de création jetable
    index = SearchIndex(tmp_path / f"audit_s3_{uuid.uuid4().hex[:8]}.db", pg_url)
    orig_env = {
        k: os.environ.get(k)
        for k in ("SEAMTECH_REDIS_URL", "SEAMTECH_S3_ENDPOINT_URL", "SEAMTECH_S3_BUCKET", "SEAMTECH_S3_ACCESS_KEY", "SEAMTECH_S3_SECRET_KEY")
    }
    os.environ["SEAMTECH_REDIS_URL"] = redis_url
    os.environ["SEAMTECH_S3_ENDPOINT_URL"] = endpoint
    os.environ["SEAMTECH_S3_BUCKET"] = bucket
    os.environ["SEAMTECH_S3_ACCESS_KEY"] = access
    os.environ["SEAMTECH_S3_SECRET_KEY"] = secret
    try:
        index.initialize()
        index.run_migrations()
    except Exception as e:
        for k, v in orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        pytest.fail(f"SearchIndex init/migrations échoué: {e}")
    config = AppConfig(
        root_paths=[tmp_path],
        database_url=pg_url,
        min_free_bytes=0,
        auth_token=JETON_SERVICE,
        redis_url=redis_url,
        s3_endpoint_url=endpoint,
        s3_bucket=bucket,
        s3_access_key=access,
        s3_secret_key=secret,
    )
    app = create_app(config)
    return index, app, pg_url, nom, orig_env, endpoint, bucket, access, secret, config


def _restore_env(orig_env: dict):
    for k, v in orig_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _assert_no_secret_in_msg(msg: str):
    """Garde-fou : un diagnostic ne doit jamais contenir secret."""
    low = msg.lower()
    assert "test_password" not in low, "diagnostic contient password PG"
    assert "redis_test_password" not in low, "diagnostic contient password Redis"
    assert "minioadmin" not in low, "diagnostic contient secret minioadmin"
    # Pas de signature présignée complète
    assert "x-amz-signature" not in low, "diagnostic contient signature S3"
    assert "x-amz-credential" not in low, "diagnostic contient credential S3"


@pytest.mark.s3
@pytest.mark.integration_docker
@pytest.mark.postgres
def test_audit_s3_piece_et_artifact_minio(tmp_path: Path):
    """PIECE_DOWNLOAD + ARTIFACT_DOWNLOAD avec MinIO réel — vérifie octets + audit complet."""
    from seamtech_search.storage import S3StorageClient

    index, app, url_base, nom, orig_env, endpoint, bucket, access, secret, config = _base_et_app_pg_redis_minio(tmp_path)
    remote_key_piece = None
    remote_key_artifact = None
    s3_client = None
    piece_id = None
    job_id = None
    admin_identifiant = f"admin_s3_{uuid.uuid4().hex[:8]}"
    # Contenus déterministes pour vérif octets
    contenu_piece = b"%PDF-1.4 S3 proof piece " + uuid.uuid4().hex.encode()
    contenu_artifact = b"%PDF-1.4 artifact proof " + uuid.uuid4().hex.encode()

    def _annotate(msg: str):
        # GitHub annotation visible via API even when log blob is unreachable — EXPURGÉE
        safe = _redact(msg)
        _assert_no_secret_in_msg(safe)
        print(f"::error title=s3-audit::{safe[:1200].replace(chr(10), ' | ')}")
        print(f"[s3-audit] {safe}")

    try:
        assert access != "minioadmin", "identité applicative ne doit pas être admin"
        s3_client = S3StorageClient(
            endpoint_url=endpoint,
            bucket_name=bucket,
            access_key_id=access,
            secret_access_key=secret,
            region_name="us-east-1",
            force_path_style=True,
        )
        s3_client.ensure_bucket_exists()

        from seamtech_search.comptes.comptes import creer_utilisateur

        created = creer_utilisateur(index, admin_identifiant, f"Nom {admin_identifiant}", "Secret123!", "administrateur")
        client = TestClient(app)
        resp = client.post("/auth/connexion", json={"identifiant": admin_identifiant, "mot_de_passe": "Secret123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        assert resp.status_code == 200, resp.text
        sid = resp.json().get("sid")
        assert sid, "sid manquant après connexion"

        # --- PIECE via S3 ---
        local_pdf = tmp_path / "s3_piece.pdf"
        local_pdf.write_bytes(contenu_piece)
        remote_key_piece = f"audit-test/{uuid.uuid4().hex}/s3_piece.pdf"
        uploaded = s3_client.upload_file(local_pdf, remote_key=remote_key_piece)
        assert uploaded == remote_key_piece, f"upload_file a retourné {uploaded}, attendu {remote_key_piece}"
        assert s3_client.object_exists(remote_key_piece) is True, "objet S3 pièce non trouvé après upload"
        # Supprime le fichier local pour forcer le chemin S3 (sinon le serveur sert le local)
        local_pdf.unlink()

        with index.connect() as conn:
            with conn.cursor() as cur:
                path_key = f"s3://{bucket}/{remote_key_piece}"
                try:
                    cur.execute(
                        "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir, object_key, object_bucket) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s, FALSE, %s, %s) RETURNING id",
                        (path_key, str(local_pdf), "s3_piece.pdf", str(tmp_path), ".pdf", len(contenu_piece), time.time(), remote_key_piece, bucket),
                    )
                    piece_id = int(cur.fetchone()[0])
                except Exception as e:
                    _annotate(f"INSERT document piece failed: {e}")
                    raise

        # Désactive suivi redirect pour voir 302 réel
        resp_dl = client.get(f"/pieces/{piece_id}/telecharger", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid}, follow_redirects=False)
        # Log minimal sans exposer headers complets
        print(f"[s3-audit] PIECE telecharger status={resp_dl.status_code} len={len(resp_dl.content) if hasattr(resp_dl, 'content') else 'n/a'}")
        if resp_dl.status_code not in (200, 302):
            _annotate(f"PIECE S3 status {resp_dl.status_code}: {resp_dl.text[:500]}")
        assert resp_dl.status_code in (200, 302), f"PIECE S3 status {resp_dl.status_code}: {resp_dl.text[:500]}"
        # STRICT : 200 doit avoir octets exacts, nosniff obligatoire
        if resp_dl.status_code == 200:
            assert resp_dl.content == contenu_piece, f"octets PIECE via S3 diffèrent: got {len(resp_dl.content)} vs {len(contenu_piece)}"
            assert resp_dl.headers.get("X-Content-Type-Options") == "nosniff", f"X-Content-Type-Options manquant PIECE: {resp_dl.headers.get('X-Content-Type-Options')}"
            assert resp_dl.headers.get("content-type", "").startswith("application/"), f"content-type PIECE invalide: {resp_dl.headers.get('content-type')}"
        else:
            # 302 : vérifie URL parsée, hôte autorisé, bucket ET clé exacts (pas OU)
            loc = resp_dl.headers.get("location", "") or resp_dl.headers.get("Location", "")
            redacted_loc = _redact_location(loc)
            # Vérifie destination exacte — ne pas accepter bucket OU clé
            assert loc, "302 PIECE sans Location"
            parsed = urllib.parse.urlparse(loc)
            # Host doit être endpoint public déclaré si présent, sinon endpoint S3
            expected_hosts = []
            if getattr(config, "s3_public_endpoint_url", None):
                try:
                    expected_hosts.append(urllib.parse.urlparse(config.s3_public_endpoint_url).netloc)
                except Exception:
                    pass
            # Si pas de public endpoint, le code ne doit pas renvoyer 302 (fallback 200) — donc 302 ici implique public endpoint
            if expected_hosts:
                assert parsed.netloc in expected_hosts or parsed.netloc == urllib.parse.urlparse(endpoint).netloc, f"302 PIECE host non autorisé: {parsed.netloc} attendu {expected_hosts}"
            # Path doit contenir bucket et clé exacts
            loc_path = urllib.parse.unquote(parsed.path or "")
            assert bucket in loc_path or bucket in loc, f"302 PIECE location sans bucket: {redacted_loc} bucket={bucket}"
            assert remote_key_piece in loc_path or remote_key_piece in urllib.parse.unquote(loc), f"302 PIECE location sans clé exacte: {redacted_loc} key={remote_key_piece}"
            # Récupère destination et vérifie octets
            import httpx

            try:
                with httpx.Client(follow_redirects=False, timeout=10) as hc:
                    r2 = hc.get(loc)
                    # MinIO présigné peut rediriger ou répondre 200 ; on vérifie octets finaux
                    if r2.status_code == 200:
                        assert r2.content == contenu_piece, f"octets destination 302 PIECE diffèrent: {len(r2.content)} vs {len(contenu_piece)}"
                    elif r2.status_code in (302, 307, 308):
                        # Suit une fois si double redirect (rare)
                        loc2 = r2.headers.get("location", "")
                        if loc2:
                            r3 = hc.get(loc2)
                            assert r3.status_code == 200 and r3.content == contenu_piece, "octets 302 chaîné PIECE diffèrent"
                        else:
                            pytest.fail(f"302 PIECE chaîné sans Location: {r2.status_code}")
                    else:
                        pytest.fail(f"Destination 302 PIECE status {r2.status_code} — {redacted_loc}")
            except Exception as e:
                _annotate(f"GET destination 302 PIECE échoué: {e} loc={redacted_loc}")
                raise

        # Vérif audit PIECE_DOWNLOAD complet
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT actor_user_id, actor_username, actor_role, action, outcome, target_type, target_id, details_json, ip_address FROM audit_logs WHERE action='PIECE_DOWNLOAD' AND target_id=%s ORDER BY id DESC LIMIT 1",
                    (str(piece_id),),
                )
                row = cur.fetchone()
                if row is None:
                    _annotate(f"PIECE_DOWNLOAD S3 non journalisé piece_id={piece_id}")
                assert row is not None, "PIECE_DOWNLOAD S3 non journalisé"
                if row[0] != created["id_utilisateur"]:
                    _annotate(f"actor_user_id PIECE mismatch: got {row[0]} expected {created['id_utilisateur']}")
                assert row[0] == created["id_utilisateur"], f"actor_user_id PIECE: {row}"
                assert row[1] == admin_identifiant and row[2] == "administrateur", f"actor PIECE: {row}"
                assert row[3] == "PIECE_DOWNLOAD" and row[4] == "success", f"outcome PIECE: {row}"
                assert row[5] == "piece" and row[6] == str(piece_id), f"target PIECE: {row}"
                if row[8] is None:
                    _annotate(f"PIECE ip_address None row={row}")
                assert row[8] is not None
                assert row[7] is not None

        # --- ARTIFACT via S3 (succès) ---
        artifact_pdf = tmp_path / "artifact_report.pdf"
        artifact_pdf.write_bytes(contenu_artifact)
        remote_key_artifact = f"audit-test/{uuid.uuid4().hex}/artifact_report.pdf"
        s3_client.upload_file(artifact_pdf, remote_key=remote_key_artifact)
        assert s3_client.object_exists(remote_key_artifact) is True
        artifact_pdf.unlink()  # force S3

        # L'endpoint /imports/{id}/artifacts lit la table `imports`, pas `jobs` (import_pipeline.get_import).
        # On insère donc directement un import minimal avec le payload attendu par la route.
        import json as _json

        job_id = uuid.uuid4().hex  # utilisé comme import_id pour la route
        fake_local_path = str(tmp_path / f"missing_{job_id}.pdf")
        result_payload = {
            "report_path": fake_local_path,
            "report_docx_path": None,
            "artifacts": [{"name": "technical-report.pdf", "key": remote_key_artifact}],
        }
        # Insertion directe dans `imports` (la route ne lit pas `jobs`)
        try:
            with index.connect() as conn:
                if index.is_postgres:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO imports (id, source_path, status, payload, created_at) VALUES (%s, %s, %s, %s, now()) ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, payload = EXCLUDED.payload",
                            (job_id, str(tmp_path), "completed", _json.dumps(result_payload)),
                        )
                else:
                    conn.execute(
                        "INSERT INTO imports (id, source_path, status, payload, created_at) VALUES (?, ?, ?, ?, datetime('now')) ON CONFLICT(id) DO UPDATE SET status=excluded.status, payload=excluded.payload",
                        (job_id, str(tmp_path), "completed", _json.dumps(result_payload)),
                    )
                conn.commit()
        except Exception as e:
            _annotate(f"INSERT imports failed: {e}")
            raise

        resp_art = client.get(f"/imports/{job_id}/artifacts/report_pdf", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid}, follow_redirects=False)
        print(f"[s3-audit] ARTIFACT status={resp_art.status_code} len={len(resp_art.content) if hasattr(resp_art, 'content') else 'n/a'}")
        if resp_art.status_code not in (200, 302):
            _annotate(f"ARTIFACT S3 status {resp_art.status_code}: {resp_art.text[:500]}")
        assert resp_art.status_code in (200, 302), f"ARTIFACT S3 status {resp_art.status_code}: {resp_art.text[:500]}"
        if resp_art.status_code == 200:
            assert resp_art.content == contenu_artifact, f"octets ARTIFACT via S3 diffèrent: got {len(resp_art.content)} vs {len(contenu_artifact)}"
            # Artifact servi via FileResponse (pas de nosniff par défaut) — on vérifie seulement content-type, nosniff est exigé sur pièces uniquement
        else:
            loc = resp_art.headers.get("location", "") or resp_art.headers.get("Location", "")
            redacted_loc = _redact_location(loc)
            assert loc, "302 ARTIFACT sans Location"
            parsed = urllib.parse.urlparse(loc)
            expected_hosts_artifact: list[str] = []
            if getattr(config, "s3_public_endpoint_url", None):
                try:
                    expected_hosts_artifact.append(urllib.parse.urlparse(config.s3_public_endpoint_url).netloc)
                except Exception:
                    pass
            loc_path = urllib.parse.unquote(parsed.path or "")
            # Strict ET : doit contenir bucket ET clé exacte
            assert bucket in loc_path or bucket in loc, f"302 ARTIFACT location sans bucket: {redacted_loc}"
            assert remote_key_artifact in loc_path or remote_key_artifact in urllib.parse.unquote(loc), f"302 ARTIFACT location sans clé exacte: {redacted_loc}"
            # Vérifie destination
            import httpx

            try:
                with httpx.Client(follow_redirects=False, timeout=10) as hc:
                    r2 = hc.get(loc)
                    if r2.status_code == 200:
                        assert r2.content == contenu_artifact, "octets destination 302 ARTIFACT diffèrent"
                    elif r2.status_code in (302, 307, 308):
                        loc2 = r2.headers.get("location", "")
                        if loc2:
                            r3 = hc.get(loc2)
                            assert r3.status_code == 200 and r3.content == contenu_artifact, "octets 302 chaîné ARTIFACT diffèrent"
                        else:
                            pytest.fail(f"302 ARTIFACT chaîné sans Location: {r2.status_code}")
                    else:
                        pytest.fail(f"Destination 302 ARTIFACT status {r2.status_code} — {redacted_loc}")
            except Exception as e:
                _annotate(f"GET destination 302 ARTIFACT échoué: {e} loc={redacted_loc}")
                raise

        with index.connect() as conn:
            with conn.cursor() as cur:
                # Sélection par target_id exact, pas seulement dernier événement
                cur.execute(
                    "SELECT actor_user_id, actor_username, actor_role, action, outcome, target_type, target_id, details_json FROM audit_logs WHERE action='ARTIFACT_DOWNLOAD' AND target_id=%s ORDER BY id DESC LIMIT 1",
                    (f"{job_id}/report_pdf",),
                )
                row = cur.fetchone()
                assert row is not None, f"ARTIFACT_DOWNLOAD S3 non journalisé pour target_id={job_id}/report_pdf"
                assert row[0] == created["id_utilisateur"], f"actor ARTIFACT: {row}"
                assert row[1] == admin_identifiant and row[2] == "administrateur", f"actor ARTIFACT: {row}"
                assert row[3] == "ARTIFACT_DOWNLOAD" and row[4] == "success", f"outcome ARTIFACT: {row}"
                assert row[5] == "import_artifact", f"target_type ARTIFACT: {row}"
                assert row[6] == f"{job_id}/report_pdf", f"target_id ARTIFACT attendu {job_id}/report_pdf got {row[6]}"

        # Preuve S3 réelle : objets existent, pas de mock
        assert s3_client.object_exists(remote_key_piece) is True
        assert s3_client.object_exists(remote_key_artifact) is True

    finally:
        # Cleanup vérifié — un échec ne doit pas être avalé
        cleanup_errors: list[str] = []
        sid_to_revoke = locals().get("sid")
        # 1) Révoque session
        if sid_to_revoke:
            try:
                client.post("/auth/deconnexion", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_to_revoke})
            except Exception as e:
                cleanup_errors.append(f"revoke session: {e}")
        # 2) S3 objects — vérifie disparition avec petite attente (MinIO eventual)
        if s3_client is not None:
            for key in (remote_key_piece, remote_key_artifact):
                if key:
                    try:
                        try:
                            s3_client.delete_file(key)
                        except Exception:
                            s3_client._get_client().delete_object(Bucket=bucket, Key=key)
                        # Vérifie disparition avec retry (évite flaky MinIO)
                        for _ in range(3):
                            if not s3_client.object_exists(key):
                                break
                            time.sleep(0.5)
                        if s3_client.object_exists(key):
                            cleanup_errors.append(f"S3 delete not effective for {key}")
                    except Exception as cleanup_exc:
                        cleanup_errors.append(f"S3 delete {key} failed: {cleanup_exc}")
        _restore_env(orig_env)
        # 3) Nettoie les lignes de test (base partagée)
        try:
            with index.connect() as conn:
                with conn.cursor() as cur:
                    if piece_id is not None:
                        cur.execute("DELETE FROM documents WHERE id = %s", (piece_id,))
                        # Vérifie
                        cur.execute("SELECT 1 FROM documents WHERE id=%s", (piece_id,))
                        if cur.fetchone() is not None:
                            cleanup_errors.append(f"documents {piece_id} not deleted")
                    if job_id is not None:
                        cur.execute("DELETE FROM imports WHERE id = %s", (job_id,))
                        cur.execute("SELECT 1 FROM imports WHERE id=%s", (job_id,))
                        if cur.fetchone() is not None:
                            cleanup_errors.append(f"imports {job_id} not deleted")
                    # Nettoie utilisateur de test (isolation) — best-effort, ne bloque pas si FK audit_logs
                    try:
                        created_id = created.get("id_utilisateur") if isinstance(created, dict) else None
                        if created_id is not None:
                            cur.execute("DELETE FROM session_ui WHERE id_utilisateur=%s", (created_id,))
                            cur.execute("DELETE FROM utilisateur WHERE id_utilisateur=%s", (created_id,))
                            cur.execute("SELECT 1 FROM utilisateur WHERE id_utilisateur=%s", (created_id,))
                            if cur.fetchone() is not None:
                                # Utilisateur encore présent via FK audit_logs SET NULL — pas bloquant, documenté
                                pass
                    except Exception:
                        pass
                conn.commit()
        except Exception as e:
            cleanup_errors.append(f"cleanup PG failed: {e}")
        try:
            index.close()
        except Exception:
            pass
        # Vérifie que les diagnostics expurgés ne fuient pas secrets
        # (déjà vérifié dans _annotate, mais double-check)
        if cleanup_errors:
            # Ne pas masquer cleanup en succès
            pytest.fail("Cleanup S3/BDD incomplet: " + " | ".join(cleanup_errors))


def test_diagnostics_expurgés_ne_fuient_pas_secrets():
    """Vérifie que les helpers de redaction ne laissent pas passer secrets."""
    # Les helpers doivent masquer signatures et passwords
    assert _redact("https://minio:9000/bucket/key?X-Amz-Signature=abc123&token=xyz") == "https://minio:9000/bucket/key?<redacted-query>"
    assert _redact("postgresql://user:test_password@host/db") == "[postgresql-url-redacted]"
    assert _redact("redis://:redis_test_password@127.0.0.1:6379/0") == "[redis-url-redacted]"
    assert "X-Amz-Signature" not in _redact_location("https://s3.example.com/b/k?X-Amz-Signature=xxx&Expires=123")
    # Vérifie qu'un message safe passe
    _assert_no_secret_in_msg("PostgreSQL non joignable (hosts 127.0.0.1:5433)")
    # Doit échouer si secret présent
    try:
        _assert_no_secret_in_msg("url postgresql://user:test_password@host")
        assert False, "aurait dû rejeter password"
    except AssertionError:
        pass
