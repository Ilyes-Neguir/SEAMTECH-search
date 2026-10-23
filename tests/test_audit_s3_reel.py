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
import re
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

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_ORIGIN_RE = re.compile(r"https?://[^/\s\"'<>]+")

# Sentinel values that must never appear in redacted output
_SENTINELS = ["user:secret", "my-t0ken", "s3cr3t-v4lue"]


def _strip_url_userinfo_and_query(raw_url: str) -> str:
    """Retourne URL sans userinfo ni query (pour expurge)."""
    try:
        p = urllib.parse.urlparse(raw_url)
        host = p.hostname or ""
        if p.port:
            host = f"{host}:{p.port}"
        # Fallback si hostname vide (ex: url malformée)
        safe_netloc = host if host else p.netloc.split("@")[-1] if "@" in p.netloc else p.netloc
        base = urllib.parse.urlunparse((p.scheme, safe_netloc, p.path or "", "", "", ""))
        # Si URL avait query ou marqueur sensible → masque
        if p.query or "x-amz-" in raw_url.lower() or "signature=" in raw_url.lower() or "token=" in raw_url.lower():
            return base + "?<redacted-query>"
        return base
    except Exception:
        # Fallback manuel : coupe userinfo et query
        without_query = raw_url.split("?", 1)[0]
        if "://" in without_query and "@" in without_query:
            without_query = without_query.split("://", 1)[0] + "://" + without_query.split("@")[-1]
        return without_query + ("?<redacted-query>" if "?" in raw_url else "")


def _redact(msg: str) -> str:
    """Expurge secrets : masque query/signature/token/password et userinfo — jamais d'exception brute."""
    if not isinstance(msg, str):
        msg = str(msg)
    low = msg.lower()
    # Secrets infra globaux → redaction totale (évite fuite via exception)
    if "postgresql://" in low:
        return "[postgresql-url-redacted]"
    if "redis://" in low:
        return "[redis-url-redacted]"
    # Remplace toutes les URLs embarquées (même préfixées "loc=..., err=...") sans userinfo/query
    if "://" in msg:
        # Remplace chaque URL trouvée
        def _repl(m: re.Match) -> str:
            return _strip_url_userinfo_and_query(m.group(0))

        msg = _URL_RE.sub(_repl, msg)
        low = msg.lower()
    # Masque générique si signature/token reste hors URL capturée
    if "x-amz-" in low or "signature=" in low or "awsaccesskey" in low:
        if "?" in msg and "?<redacted-query>" not in msg:
            return msg.split("?", 1)[0] + "?<redacted-query>"
        if "?<redacted-query>" in msg:
            return msg
        # Si pas de ?, mais marqueur sensible, évite de retourner valeur brute
        # On a déjà expurgé les URLs ; on retourne msg tel quel s'il ne contient plus de secret brut
        # (les sentinelles de test seront vérifiées par _assert_no_secret_in_msg)
    # Bannit toute occurrence résiduelle de sentinelles connues (défense en profondeur)
    for sent in _SENTINELS:
        if sent.lower() in low:
            msg = msg.replace(sent, "[redacted]")
            msg = msg.replace(sent.lower(), "[redacted]")
    # Ne jamais laisser user:secret type pattern même hors URL formelle
    # (ex: user:pass@host sans scheme) — masque grossièrement
    if "@" in msg and ":" in msg and "://" not in msg:
        # Ne pas trop agressif, mais si un pattern user:xxx@host apparaît, masque
        msg = re.sub(r"\b[^:\s]+:[^\s@]+@[^\s]+\b", "[redacted-userinfo]", msg)
    return msg


def _redact_location(url: str) -> str:
    """Retourne URL sans userinfo ni query (évite fuite signature présignée)."""
    if not url:
        return ""
    url = str(url)
    try:
        p = urllib.parse.urlparse(url)
        host = p.hostname or ""
        if p.port:
            host = f"{host}:{p.port}"
        safe_netloc = host if host else (p.netloc.split("@")[-1] if "@" in p.netloc else p.netloc)
        return urllib.parse.urlunparse((p.scheme, safe_netloc, p.path or "", "", "", ""))
    except Exception:
        # Fallback : coupe query puis userinfo
        base = url.split("?", 1)[0]
        if "://" in base and "@" in base:
            base = base.split("://", 1)[0] + "://" + base.split("@")[-1]
        return base


def _get_allowed_origins(config) -> list[str]:
    """Retourne liste d'origines autorisées (scheme+host+port) depuis config — fail-closed si vide."""
    origins: list[str] = []
    raw = getattr(config, "s3_public_endpoint_url", None)
    if raw:
        try:
            p = urllib.parse.urlparse(str(raw))
            if p.scheme and p.netloc:
                origins.append(f"{p.scheme}://{p.netloc}")
        except Exception:
            pass
    return origins


def _validate_redirect_url(url: str, allowed_origins: list[str], bucket: str, key: str) -> urllib.parse.ParseResult:
    """Valide une URL de redirection présignée avant tout appel réseau — fail-closed, origine exacte."""
    if not url:
        raise AssertionError("Location vide")
    parsed = urllib.parse.urlparse(url)
    # Pas d'userinfo — fail-closed
    assert not parsed.username and not parsed.password, "userinfo interdit dans Location"
    # Pas de fragment
    assert not parsed.fragment, "fragment interdit dans Location"
    # Host présent et origine allowlistée — fail-closed si liste vide
    assert parsed.netloc, "host manquant dans Location"
    assert allowed_origins, "aucun endpoint public autorisé configuré — 302 inattendu (fail-closed)"
    # Validation origine exacte (schéma + hôte + port), pas seulement netloc
    origin = f"{parsed.scheme}://{parsed.netloc}"
    assert origin in allowed_origins, f"origine non autorisée: {origin} attendu {allowed_origins}"
    # Schéma déjà validé via origine : si origine autorisée est https, http même hôte/port sera rejeté
    assert parsed.scheme in ("http", "https"), f"scheme non autorisé: {parsed.scheme} (attendu http/https)"
    # Bucket et clé doivent être dans le chemin parsé, pas dans la query
    path = urllib.parse.unquote(parsed.path or "")
    assert path, "path vide dans Location"
    if parsed.query:
        assert bucket not in parsed.query or bucket in path, (
            "bucket trouvé en query mais pas en path — sous-chaîne interdite"
        )
    expected = f"/{bucket}/{key}"
    assert path == expected or path == f"/{bucket}/{key}", (
        f"path non conforme: {path} attendu {expected} (redacted={_redact_location(url)})"
    )
    return parsed


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
    if os.environ.get("SEAMTECH_INTEGRATION_STRICT") in {"1", "true", "yes"}:
        pytest.fail(
            f"PostgreSQL non joignable pour preuve S3 (env_tries={env_tried} hosts=127.0.0.1:5433,127.0.0.1:5432)"
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
        pytest.fail(
            "Identité administrateur minioadmin interdite — utiliser l'identité applicative restreinte (seamtech-app-ci)"
        )
    return endpoint, bucket, access, secret


def _base_et_app_pg_redis_minio(tmp_path: Path):
    pg_url = _pg_url()
    redis_url = _redis_url()
    endpoint, bucket, access, secret = _s3_config()
    nom = "seamtech_search"
    index = SearchIndex(tmp_path / f"audit_s3_{uuid.uuid4().hex[:8]}.db", pg_url)
    orig_env = {
        k: os.environ.get(k)
        for k in (
            "SEAMTECH_REDIS_URL",
            "SEAMTECH_S3_ENDPOINT_URL",
            "SEAMTECH_S3_BUCKET",
            "SEAMTECH_S3_ACCESS_KEY",
            "SEAMTECH_S3_SECRET_KEY",
        )
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
        pytest.fail(f"SearchIndex init/migrations échoué: {_redact(str(e))}")
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
    """Garde-fou : un diagnostic ne doit jamais contenir secret (scan URLs intégrées)."""
    if not isinstance(msg, str):
        msg = str(msg)
    low = msg.lower()
    assert "test_password" not in low, "diagnostic contient password PG"
    assert "redis_test_password" not in low, "diagnostic contient password Redis"
    assert "minioadmin" not in low, "diagnostic contient secret minioadmin"
    assert "x-amz-signature" not in low, "diagnostic contient signature S3"
    assert "x-amz-credential" not in low, "diagnostic contient credential S3"
    for sent in _SENTINELS:
        assert sent.lower() not in low, f"diagnostic contient sentinelle {sent}"
        assert sent not in msg, f"diagnostic contient sentinelle {sent}"
    # Scan toutes les URLs embarquées pour userinfo (pas seulement msg entier)
    for m in _URL_RE.finditer(msg):
        raw = m.group(0)
        try:
            p = urllib.parse.urlparse(raw)
            assert not p.username and not p.password, f"diagnostic contient userinfo: {_redact_location(raw)}"
        except AssertionError:
            raise
        except Exception:
            # Fallback : détection brute userinfo
            assert "@" not in (p.netloc if "p" in locals() else ""), "diagnostic contient userinfo (fallback)"


@pytest.mark.s3
@pytest.mark.integration_docker
@pytest.mark.postgres
def test_audit_s3_piece_et_artifact_minio(tmp_path: Path):
    """PIECE_DOWNLOAD + ARTIFACT_DOWNLOAD avec MinIO réel — vérifie octets + audit complet."""
    from seamtech_search.storage import S3StorageClient

    index, app, url_base, nom, orig_env, endpoint, bucket, access, secret, config = _base_et_app_pg_redis_minio(
        tmp_path
    )
    remote_key_piece = None
    remote_key_artifact = None
    s3_client = None
    piece_id = None
    job_id = None
    admin_identifiant = f"admin_s3_{uuid.uuid4().hex[:8]}"
    contenu_piece = b"%PDF-1.4 S3 proof piece " + uuid.uuid4().hex.encode()
    contenu_artifact = b"%PDF-1.4 artifact proof " + uuid.uuid4().hex.encode()

    def _annotate(msg: str):
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

        created = creer_utilisateur(
            index, admin_identifiant, f"Nom {admin_identifiant}", "Secret123!", "administrateur"
        )
        client = TestClient(app)
        resp = client.post(
            "/auth/connexion",
            json={"identifiant": admin_identifiant, "mot_de_passe": "Secret123!"},
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE},
        )
        assert resp.status_code == 200, _redact(resp.text)
        sid = resp.json().get("sid")
        assert sid, "sid manquant après connexion"

        # --- PIECE via S3 ---
        local_pdf = tmp_path / "s3_piece.pdf"
        local_pdf.write_bytes(contenu_piece)
        remote_key_piece = f"audit-test/{uuid.uuid4().hex}/s3_piece.pdf"
        uploaded = s3_client.upload_file(local_pdf, remote_key=remote_key_piece)
        assert uploaded == remote_key_piece, (
            f"upload_file a retourné {_redact(str(uploaded))}, attendu {_redact(remote_key_piece)}"
        )
        assert s3_client.object_exists(remote_key_piece) is True, "objet S3 pièce non trouvé après upload"
        local_pdf.unlink()

        with index.connect() as conn:
            with conn.cursor() as cur:
                path_key = f"s3://{bucket}/{remote_key_piece}"
                try:
                    cur.execute(
                        "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir, object_key, object_bucket) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s, FALSE, %s, %s) RETURNING id",
                        (
                            path_key,
                            str(local_pdf),
                            "s3_piece.pdf",
                            str(tmp_path),
                            ".pdf",
                            len(contenu_piece),
                            time.time(),
                            remote_key_piece,
                            bucket,
                        ),
                    )
                    piece_id = int(cur.fetchone()[0])
                except Exception as e:
                    _annotate(f"INSERT document piece failed: {_redact(str(e))}")
                    raise

        resp_dl = client.get(
            f"/pieces/{piece_id}/telecharger",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid},
            follow_redirects=False,
        )
        print(
            f"[s3-audit] PIECE telecharger status={resp_dl.status_code} len={len(resp_dl.content) if hasattr(resp_dl, 'content') else 'n/a'}"
        )
        if resp_dl.status_code not in (200, 302):
            _annotate(f"PIECE S3 status {resp_dl.status_code}: {_redact(resp_dl.text[:500])}")
        assert resp_dl.status_code in (200, 302), (
            f"PIECE S3 status {resp_dl.status_code}: {_redact(resp_dl.text[:500])}"
        )
        if resp_dl.status_code == 200:
            assert resp_dl.content == contenu_piece, (
                f"octets PIECE via S3 diffèrent: got {len(resp_dl.content)} vs {len(contenu_piece)}"
            )
            assert resp_dl.headers.get("X-Content-Type-Options") == "nosniff", (
                f"X-Content-Type-Options manquant PIECE: {_redact(str(resp_dl.headers.get('X-Content-Type-Options')))}"
            )
            assert resp_dl.headers.get("content-type", "").startswith("application/"), (
                f"content-type PIECE invalide: {_redact(str(resp_dl.headers.get('content-type')))}"
            )
        else:
            loc = resp_dl.headers.get("location", "") or resp_dl.headers.get("Location", "")
            allowed = _get_allowed_origins(config)
            _validate_redirect_url(loc, allowed, bucket, remote_key_piece)
            redacted_loc = _redact_location(loc)
            import httpx

            try:
                with httpx.Client(follow_redirects=False, timeout=10) as hc:
                    r2 = hc.get(loc)
                    if r2.status_code == 200:
                        assert r2.content == contenu_piece, (
                            f"octets destination 302 PIECE diffèrent: {len(r2.content)} vs {len(contenu_piece)}"
                        )
                    elif r2.status_code in (302, 307, 308):
                        loc2 = r2.headers.get("location", "")
                        assert loc2, f"302 PIECE chaîné sans Location: {r2.status_code}"
                        _validate_redirect_url(loc2, allowed, bucket, remote_key_piece)
                        r3 = hc.get(loc2)
                        assert r3.status_code == 200 and r3.content == contenu_piece, (
                            "octets 302 chaîné PIECE diffèrent"
                        )
                    else:
                        pytest.fail(f"Destination 302 PIECE status {r2.status_code} — {redacted_loc}")
            except AssertionError:
                raise
            except Exception as e:
                _annotate(f"GET destination 302 PIECE échoué: {_redact(str(e))} loc={redacted_loc}")
                raise

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
                assert row[0] == created["id_utilisateur"], f"actor_user_id PIECE: {_redact(str(row))}"
                assert row[1] == admin_identifiant and row[2] == "administrateur", f"actor PIECE: {_redact(str(row))}"
                assert row[3] == "PIECE_DOWNLOAD" and row[4] == "success", f"outcome PIECE: {_redact(str(row))}"
                assert row[5] == "piece" and row[6] == str(piece_id), f"target PIECE: {_redact(str(row))}"
                if row[8] is None:
                    _annotate(f"PIECE ip_address None row={_redact(str(row))}")
                assert row[8] is not None
                assert row[7] is not None

        # --- ARTIFACT via S3 (succès) ---
        artifact_pdf = tmp_path / "artifact_report.pdf"
        artifact_pdf.write_bytes(contenu_artifact)
        remote_key_artifact = f"audit-test/{uuid.uuid4().hex}/artifact_report.pdf"
        s3_client.upload_file(artifact_pdf, remote_key=remote_key_artifact)
        assert s3_client.object_exists(remote_key_artifact) is True
        artifact_pdf.unlink()

        import json as _json

        job_id = uuid.uuid4().hex
        fake_local_path = str(tmp_path / f"missing_{job_id}.pdf")
        result_payload = {
            "report_path": fake_local_path,
            "report_docx_path": None,
            "artifacts": [{"name": "technical-report.pdf", "key": remote_key_artifact}],
        }
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
            _annotate(f"INSERT imports failed: {_redact(str(e))}")
            raise

        resp_art = client.get(
            f"/imports/{job_id}/artifacts/report_pdf",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid},
            follow_redirects=False,
        )
        print(
            f"[s3-audit] ARTIFACT status={resp_art.status_code} len={len(resp_art.content) if hasattr(resp_art, 'content') else 'n/a'}"
        )
        if resp_art.status_code not in (200, 302):
            _annotate(f"ARTIFACT S3 status {resp_art.status_code}: {_redact(resp_art.text[:500])}")
        assert resp_art.status_code in (200, 302), (
            f"ARTIFACT S3 status {resp_art.status_code}: {_redact(resp_art.text[:500])}"
        )
        if resp_art.status_code == 200:
            assert resp_art.content == contenu_artifact, (
                f"octets ARTIFACT via S3 diffèrent: got {len(resp_art.content)} vs {len(contenu_artifact)}"
            )
        else:
            loc = resp_art.headers.get("location", "") or resp_art.headers.get("Location", "")
            allowed_art = _get_allowed_origins(config)
            _validate_redirect_url(loc, allowed_art, bucket, remote_key_artifact)
            redacted_loc = _redact_location(loc)
            import httpx

            try:
                with httpx.Client(follow_redirects=False, timeout=10) as hc:
                    r2 = hc.get(loc)
                    if r2.status_code == 200:
                        assert r2.content == contenu_artifact, "octets destination 302 ARTIFACT diffèrent"
                    elif r2.status_code in (302, 307, 308):
                        loc2 = r2.headers.get("location", "")
                        assert loc2, f"302 ARTIFACT chaîné sans Location: {r2.status_code}"
                        _validate_redirect_url(loc2, allowed_art, bucket, remote_key_artifact)
                        r3 = hc.get(loc2)
                        assert r3.status_code == 200 and r3.content == contenu_artifact, (
                            "octets 302 chaîné ARTIFACT diffèrent"
                        )
                    else:
                        pytest.fail(f"Destination 302 ARTIFACT status {r2.status_code} — {redacted_loc}")
            except AssertionError:
                raise
            except Exception as e:
                _annotate(f"GET destination 302 ARTIFACT échoué: {_redact(str(e))} loc={redacted_loc}")
                raise

        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT actor_user_id, actor_username, actor_role, action, outcome, target_type, target_id, details_json FROM audit_logs WHERE action='ARTIFACT_DOWNLOAD' AND target_id=%s ORDER BY id DESC LIMIT 1",
                    (f"{job_id}/report_pdf",),
                )
                row = cur.fetchone()
                assert row is not None, (
                    f"ARTIFACT_DOWNLOAD S3 non journalisé pour target_id={_redact(f'{job_id}/report_pdf')}"
                )
                assert row[0] == created["id_utilisateur"], f"actor ARTIFACT: {_redact(str(row))}"
                assert row[1] == admin_identifiant and row[2] == "administrateur", (
                    f"actor ARTIFACT: {_redact(str(row))}"
                )
                assert row[3] == "ARTIFACT_DOWNLOAD" and row[4] == "success", f"outcome ARTIFACT: {_redact(str(row))}"
                assert row[5] == "import_artifact", f"target_type ARTIFACT: {_redact(str(row))}"
                assert row[6] == f"{job_id}/report_pdf", (
                    f"target_id ARTIFACT attendu {_redact(job_id)}/report_pdf got {_redact(str(row[6]))}"
                )

        assert s3_client.object_exists(remote_key_piece) is True
        assert s3_client.object_exists(remote_key_artifact) is True

    finally:
        cleanup_errors: list[str] = []
        sid_to_revoke = locals().get("sid")
        if sid_to_revoke:
            try:
                resp_logout = client.post(
                    "/auth/deconnexion",
                    headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_to_revoke},
                )
                if resp_logout.status_code != 200:
                    cleanup_errors.append(f"logout status {resp_logout.status_code}: {_redact(resp_logout.text[:200])}")
                else:
                    chk = client.get(
                        "/auth/session",
                        headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_to_revoke},
                    )
                    if chk.status_code != 401:
                        cleanup_errors.append(f"session not revoked, /auth/session status {chk.status_code}")
            except Exception as e:
                cleanup_errors.append(f"revoke session: {_redact(str(e))}")
        if s3_client is not None:
            for key in (remote_key_piece, remote_key_artifact):
                if key:
                    try:
                        try:
                            s3_client.delete_file(key)
                        except Exception:
                            s3_client._get_client().delete_object(Bucket=bucket, Key=key)
                        for _ in range(3):
                            if not s3_client.object_exists(key):
                                break
                            time.sleep(0.5)
                        if s3_client.object_exists(key):
                            cleanup_errors.append(f"S3 delete not effective for {_redact(key)}")
                    except Exception as cleanup_exc:
                        cleanup_errors.append(f"S3 delete {_redact(str(key))} failed: {_redact(str(cleanup_exc))}")
        _restore_env(orig_env)
        try:
            with index.connect() as conn:
                with conn.cursor() as cur:
                    if piece_id is not None:
                        cur.execute("DELETE FROM documents WHERE id = %s", (piece_id,))
                        cur.execute("SELECT 1 FROM documents WHERE id=%s", (piece_id,))
                        if cur.fetchone() is not None:
                            cleanup_errors.append(f"documents {piece_id} not deleted")
                    if job_id is not None:
                        cur.execute("DELETE FROM imports WHERE id = %s", (job_id,))
                        cur.execute("SELECT 1 FROM imports WHERE id=%s", (job_id,))
                        if cur.fetchone() is not None:
                            cleanup_errors.append(f"imports {job_id} not deleted")
                    try:
                        created_id = created.get("id_utilisateur") if isinstance(created, dict) else None
                        if created_id is not None:
                            cur.execute("DELETE FROM session_ui WHERE id_utilisateur=%s", (created_id,))
                            cur.execute("SELECT 1 FROM session_ui WHERE id_utilisateur=%s", (created_id,))
                            if cur.fetchone() is not None:
                                cleanup_errors.append(f"session_ui for utilisateur {created_id} not deleted")
                            cur.execute("DELETE FROM utilisateur WHERE id_utilisateur=%s", (created_id,))
                            cur.execute("SELECT 1 FROM utilisateur WHERE id_utilisateur=%s", (created_id,))
                            if cur.fetchone() is not None:
                                cleanup_errors.append(f"utilisateur {admin_identifiant} not deleted — FK?")
                            else:
                                cur.execute(
                                    "SELECT COUNT(*) FROM audit_logs WHERE actor_username=%s", (admin_identifiant,)
                                )
                                cnt_row = cur.fetchone()
                                cnt = cnt_row[0] if cnt_row else None
                                if cnt is None or cnt < 2:
                                    cleanup_errors.append(
                                        f"audit residual missing: expected >=2 rows for {admin_identifiant}, got {cnt} — politique conserve 2 lignes audit_logs"
                                    )
                    except Exception as e:
                        cleanup_errors.append(f"delete utilisateur/session failed: {_redact(str(e))}")
                conn.commit()
        except Exception as e:
            cleanup_errors.append(f"cleanup PG failed: {_redact(str(e))}")
        try:
            index.close()
        except Exception:
            pass
        if cleanup_errors:
            safe_errs = " | ".join(_redact(c) for c in cleanup_errors)
            _assert_no_secret_in_msg(safe_errs)
            pytest.fail("Cleanup S3/BDD incomplet: " + safe_errs)


def test_diagnostics_expurgés_ne_fuient_pas_secrets():
    """Vérifie que les helpers de redaction ne laissent pas passer secrets."""
    # URL avec query sensible → query masquée, userinfo jamais conservé
    red = _redact("https://minio:9000/bucket/key?X-Amz-Signature=abc123&token=xyz")
    assert red == "https://minio:9000/bucket/key?<redacted-query>"
    assert "user:secret" not in red
    # postgresql/redis toujours entièrement expurgés
    assert _redact("postgresql://user:test_password@host/db") == "[postgresql-url-redacted]"
    assert _redact("redis://:redis_test_password@127.0.0.1:6379/0") == "[redis-url-redacted]"
    # _redact_location ne conserve ni query ni userinfo
    assert "X-Amz-Signature" not in _redact_location("https://s3.example.com/b/k?X-Amz-Signature=xxx&Expires=123")
    assert (
        _redact_location("https://user:secret@s3.example.com/b/k?X-Amz-Signature=xxx") == "https://s3.example.com/b/k"
    )
    assert "user:secret" not in _redact_location("https://user:secret@s3.example.com/b/k?X-Amz-Signature=xxx")
    # URL intégrée dans message préfixé → userinfo détecté/expurgé
    msg_prefixed = (
        "GET destination 302 PIECE échoué: err loc=https://user:secret@s3.example.com/b/k?X-Amz-Signature=abc"
    )
    red_prefixed = _redact(msg_prefixed)
    assert "user:secret" not in red_prefixed
    assert "?<redacted-query>" in red_prefixed
    # userinfo masqué + signature → doit échouer (secret détecté)
    with pytest.raises(AssertionError):
        _assert_no_secret_in_msg(msg_prefixed)
    # _assert_no_secret_in_msg doit détecter userinfo dans URL intégrée
    with pytest.raises(AssertionError, match="userinfo"):
        _assert_no_secret_in_msg("prefix https://user:pass@s3.example.com/b/k suffix")
    # Sentinelles ne doivent jamais fuir
    with pytest.raises(AssertionError):
        _assert_no_secret_in_msg("oups s3cr3t-v4lue leaked")
    with pytest.raises(AssertionError):
        _assert_no_secret_in_msg("token my-t0ken in logs")
    # Générique : postgresql/redis/signatures
    _assert_no_secret_in_msg("PostgreSQL non joignable (hosts 127.0.0.1:5433)")
    with pytest.raises(AssertionError, match="password PG"):
        _assert_no_secret_in_msg("url postgresql://user:test_password@host")
    with pytest.raises(AssertionError, match="signature S3"):
        _assert_no_secret_in_msg("https://s3.example.com/b/k?X-Amz-Signature=abc")
    # Vérifie expurge : sortie ne doit JAMAIS contenir userinfo ni sentinelle
    out = _redact("https://user:secret@s3.example.com/b/k?X-Amz-Signature=abc&token=my-t0ken")
    assert "user:secret" not in out
    assert "s3cr3t-v4lue" not in out.lower()
    assert "my-t0ken" not in out.lower()
    assert "X-Amz-Signature" not in out
    assert "?<redacted-query>" in out
    # _redact ne doit pas retourner une sortie contenant volontairement user:secret
    assert out == "https://s3.example.com/b/k?<redacted-query>"


def test_redirect_validation_rejects_bad_inputs():
    """Redirections: mauvais origine/schéma/host/bucket/clé/chaîné doivent échouer avant réseau."""
    bucket = "seamtech-documents"
    key = "audit-test/abc/s3_piece.pdf"
    allowed_https = ["https://s3.example.com:9000"]
    allowed_http = ["http://s3.example.com:9000"]

    ok_url = f"https://s3.example.com:9000/{bucket}/{key}?X-Amz-Signature=xxx"
    _validate_redirect_url(ok_url, allowed_https, bucket, key)

    # Même hôte/port mais schéma http alors qu'origine autorisée est https → rejet
    with pytest.raises(AssertionError, match="origine non autorisée"):
        _validate_redirect_url(f"http://s3.example.com:9000/{bucket}/{key}", allowed_https, bucket, key)
    # Origine http autorisée → http passe
    _validate_redirect_url(f"http://s3.example.com:9000/{bucket}/{key}", allowed_http, bucket, key)
    # Mais https vers origine http → rejet (schéma lié)
    with pytest.raises(AssertionError, match="origine non autorisée"):
        _validate_redirect_url(ok_url, allowed_http, bucket, key)

    # Mauvais hôte
    with pytest.raises(AssertionError, match="origine non autorisée"):
        _validate_redirect_url(f"https://evil.com/{bucket}/{key}", allowed_https, bucket, key)
    # Liste vide → fail-closed
    with pytest.raises(AssertionError, match="aucun endpoint"):
        _validate_redirect_url(ok_url, [], bucket, key)
    # Mauvais bucket
    with pytest.raises(AssertionError, match="path non conforme"):
        _validate_redirect_url(f"https://s3.example.com:9000/wrong-bucket/{key}", allowed_https, bucket, key)
    # Mauvaise clé
    with pytest.raises(AssertionError, match="path non conforme"):
        _validate_redirect_url(f"https://s3.example.com:9000/{bucket}/wrong/key.pdf", allowed_https, bucket, key)
    # Bucket/clé seulement en query
    with pytest.raises(AssertionError, match="path non conforme"):
        _validate_redirect_url(
            f"https://s3.example.com:9000/other/path?bucket={bucket}&key={key}", allowed_https, bucket, key
        )
    # Userinfo interdit
    with pytest.raises(AssertionError, match="userinfo"):
        _validate_redirect_url(f"https://user:pass@s3.example.com:9000/{bucket}/{key}", allowed_https, bucket, key)
    # Fragment interdit
    with pytest.raises(AssertionError, match="fragment"):
        _validate_redirect_url(f"https://s3.example.com:9000/{bucket}/{key}#frag", allowed_https, bucket, key)
    # Scheme non autorisé
    with pytest.raises(AssertionError, match="origine non autorisée"):
        _validate_redirect_url(f"ftp://s3.example.com:9000/{bucket}/{key}", allowed_https, bucket, key)
