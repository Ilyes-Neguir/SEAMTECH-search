import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig


def test_async_import_job_lifecycle(tmp_path: Path) -> None:
    source = Path("sample_data/CLIENT-123").resolve()
    config = AppConfig(
        root_paths=[source.parent],
        database_path=tmp_path / "search.db",
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))

    # 1. Async POST /imports returns 202 Accepted with job_id
    resp = client.post("/imports", json={"source_path": str(source)})
    assert resp.status_code == 202
    data = resp.json()
    job_id = data["job_id"]
    assert data["status"] in ("pending", "running", "completed")

    # 2. Poll GET /imports/{id} until completed
    for _ in range(50):
        poll = client.get(f"/imports/{job_id}")
        assert poll.status_code == 200
        poll_data = poll.json()
        if poll_data["status"] == "completed":
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"Job {job_id} did not complete in time, last status: {poll_data}")

    assert poll_data["status"] == "completed"
    assert poll_data["progress"] == 100
    assert poll_data.get("data") is not None
    assert poll_data["data"]["reference"] == "REF-2026-CLIENT123"


def test_sync_import_with_wait_parameter(tmp_path: Path) -> None:
    source = Path("sample_data/CLIENT-123").resolve()
    config = AppConfig(
        root_paths=[source.parent],
        database_path=tmp_path / "search.db",
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))

    # ?wait=true returns 200 with complete result immediately
    resp = client.post("/imports?wait=true", json={"source_path": str(source)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["data"]["reference"] == "REF-2026-CLIENT123"


def test_cancel_import_job(tmp_path: Path, monkeypatch) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from seamtech_search.jobs import ImportCancelledError, create_job

    started, release = threading.Event(), threading.Event()
    job_ids = []

    def enregistrer_job(*args, **kwargs):
        result = create_job(*args, **kwargs)
        job_ids.append(result["id"])
        return result

    monkeypatch.setattr("seamtech_search.api.create_job", enregistrer_job)

    def import_bloque(*args, cancel_check, **kwargs):
        started.set()
        assert release.wait(10), "le test doit libérer le worker"
        assert cancel_check(), "le worker doit observer la demande d'annulation"
        raise ImportCancelledError("annulé")

    monkeypatch.setattr("seamtech_search.api.import_folder", import_bloque)
    source = Path("sample_data/CLIENT-123").resolve()
    config = AppConfig(
        root_paths=[source.parent],
        database_path=tmp_path / "search.db",
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))

    # TestClient attend les tâches de son event loop : la requête de création
    # doit être sur un autre thread pour annuler pendant l'exécution réelle.
    with ThreadPoolExecutor(max_workers=1) as pool:
        creation = pool.submit(client.post, "/imports", json={"source_path": str(source)})
        try:
            assert started.wait(10)
            assert len(job_ids) == 1
            job_id = job_ids[0]
            cancel_resp = client.post(f"/imports/{job_id}/cancel")
            assert cancel_resp.status_code == 200
            assert cancel_resp.json()["status"] == "cancelled"
        finally:
            release.set()
        resp = creation.result(timeout=10)
        assert resp.status_code == 202
        assert resp.json()["job_id"] == job_id

    # Reading the cancelled job
    poll = client.get(f"/imports/{job_id}")
    assert poll.status_code == 200
    assert poll.json()["status"] == "cancelled"


def test_disk_guard_insufficient_storage_507(tmp_path: Path) -> None:
    source = Path("sample_data/CLIENT-123").resolve()
    # Require an impossible 1000 TB of free space
    config = AppConfig(
        root_paths=[source.parent],
        database_path=tmp_path / "search.db",
        min_free_bytes=10**16,
    )
    client = TestClient(create_app(config))

    resp = client.post("/imports", json={"source_path": str(source)})
    assert resp.status_code == 507
    assert "Insufficient disk space" in resp.json()["detail"]


def test_probes_liveness_and_readiness(tmp_path: Path) -> None:
    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "search.db",
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))

    live = client.get("/live")
    assert live.status_code == 200
    assert live.json() == {"status": "alive"}

    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_rate_limiting_and_retry_after(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = AppConfig(
        root_paths=[root],
        database_path=tmp_path / "search.db",
        rate_limit_per_minute=3,
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))

    # 3 allowed requests
    for _ in range(3):
        res = client.get("/search?q=test")
        assert res.status_code == 200
        assert "X-Request-ID" in res.headers

    # 4th request exceeds rate limit -> 429
    limited = client.get("/search?q=test")
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
    assert int(limited.headers["Retry-After"]) >= 1

    # Exempt endpoints still work
    assert client.get("/live").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/health").status_code == 200


def test_maintenance_cleanup_and_audit_log(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = AppConfig(
        root_paths=[root],
        database_path=tmp_path / "search.db",
        auth_token="super-secret-token",
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))
    headers = {"X-SEAMTECH-TOKEN": "super-secret-token"}

    # Run cleanup
    cleanup_resp = client.post("/maintenance/cleanup", headers=headers)
    assert cleanup_resp.status_code == 200
    assert "cleanup" in cleanup_resp.json()

    # Query audit logs
    audit_resp = client.get("/audit", headers=headers)
    assert audit_resp.status_code == 200
    audit_data = audit_resp.json()
    assert audit_data["count"] >= 1

    # Check actor fingerprint: raw secret must NEVER appear
    for entry in audit_data["results"]:
        assert "super-secret-token" not in entry["actor"]
        assert entry["actor"].startswith("token:")


@pytest.mark.parametrize('job_status', ['failed', 'upload_incomplete', 'needs_review'])
def test_suivi_ne_masque_pas_etat_terminal_non_reussi_par_un_import_ancien(tmp_path, monkeypatch, job_status):
    """Un résultat d'extraction périmé ne transforme pas l'échec du job en succès."""
    from fastapi.testclient import TestClient

    from seamtech_search import api
    from seamtech_search.config import AppConfig

    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'index.db', min_free_bytes=0)
    monkeypatch.setattr(api, 'get_job', lambda _index, _id: {
        'id': 'synthetique', 'status': job_status, 'progress': 100, 'stage': job_status,
        'error': 'Echec synthétique', 'result': {'status': job_status, 'import_id': 'synthetique'},
    })
    monkeypatch.setattr(api, 'get_import', lambda _index, _id: {
        'import_id': 'synthetique', 'status': 'completed', 'transport_verifie': False,
        'all_verified': False, 'warnings': ['resultat ancien'],
    })
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/synthetique')
    assert reponse.status_code == 200
    assert reponse.json()['status'] == job_status
    assert reponse.json()['job_status'] == job_status
    assert reponse.json()['import_status'] == 'completed'
    assert reponse.json()['error'] == 'Echec synthétique'
    assert reponse.json()['warnings'] == ['resultat ancien']


def test_suivi_preserve_etat_import_corrige_apres_job_reussi(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from seamtech_search import api
    from seamtech_search.config import AppConfig

    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'index.db', min_free_bytes=0)
    monkeypatch.setattr(api, 'get_job', lambda _index, _id: {
        'id': 'synthetique', 'status': 'completed', 'progress': 100, 'stage': 'done',
    })
    monkeypatch.setattr(api, 'get_import', lambda _index, _id: {
        'import_id': 'synthetique', 'status': 'needs_review', 'warnings': ['Correction ultérieure'],
    })
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/synthetique')
    assert reponse.status_code == 200 and reponse.json()['status'] == 'needs_review'
    assert reponse.json()['warnings'] == ['Correction ultérieure']


@pytest.mark.parametrize('job_status', ['failed', 'upload_incomplete', 'needs_review'])
def test_suivi_terminal_persistant_ne_promet_pas_succes(tmp_path, job_status):
    """Le contrat passe par le registre SQLite et la route HTTP réels."""
    from seamtech_search.import_pipeline import ImportResult, _save_import
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.jobs import create_job, update_job

    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'registre.db', min_free_bytes=0)
    app = create_app(cfg)
    index = SearchIndex(cfg.database_path)
    try:
        create_job(index, 'test-suivi-terminal', str(tmp_path))
        _save_import(index, ImportResult(
            import_id='test-suivi-terminal', source_path=str(tmp_path), status='completed',
            files_detected=0, analyzed_files=0, technical_pdf=None, data=None, report_path=None,
            upload_status='not_configured', warnings=[], files=[],
        ))
        update_job(index, 'test-suivi-terminal', status=job_status, progress=100,
                   error='Interruption synthétique')
        with TestClient(app) as client:
            reponse = client.get('/imports/test-suivi-terminal')
        assert reponse.status_code == 200
        assert reponse.json()['status'] == reponse.json()['job_status'] == job_status
        assert reponse.json()['import_status'] == 'completed'
        assert reponse.json()['error'] == 'Interruption synthétique'
    finally:
        index.close()


@pytest.mark.parametrize('job_status', ['failed', 'upload_incomplete', 'needs_review'])
def test_suivi_garde_echec_et_intention_incertaine(tmp_path, monkeypatch, job_status):
    from seamtech_search import api

    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'index.db', min_free_bytes=0)
    monkeypatch.setattr(api, 'get_job', lambda _index, _id: {
        'id': 'synthetique', 'status': job_status, 'progress': 100, 'stage': job_status,
        'error': 'Echec synthétique',
    })
    monkeypatch.setattr(api, 'get_import', lambda _index, _id: {
        'import_id': 'synthetique', 'status': 'needs_review', 'publication_pending': True,
    })
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/synthetique')
    assert reponse.status_code == 200
    assert reponse.json()['status'] == job_status
    assert reponse.json()['publication_pending'] is True
    assert reponse.json()['error'] == 'Echec synthétique'


@pytest.mark.parametrize('etat_job', [None, 'completed'])
def test_intention_incertaine_ne_peut_pas_etre_un_succes(tmp_path, monkeypatch, etat_job):
    from seamtech_search import api

    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'index.db', min_free_bytes=0)
    monkeypatch.setattr(api, 'get_job', lambda _index, _id: (
        {'id': 'synthetique', 'status': etat_job, 'progress': 100} if etat_job else None
    ))
    monkeypatch.setattr(api, 'get_import', lambda _index, _id: {
        'import_id': 'synthetique', 'status': 'completed', 'publication_pending': True,
    })
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/synthetique')
    assert reponse.status_code == 200
    assert reponse.json()['status'] == 'needs_review' and reponse.json()['publication_pending'] is True


@pytest.mark.postgres
@pytest.mark.parametrize('job_status', ['failed', 'upload_incomplete', 'needs_review', 'completed', 'cancelled', 'running'])
def test_suivi_postgres_distingue_etat_reel_et_intention_incertaine(base_recherche, tmp_path, job_status):
    """Lignes durables PostgreSQL puis route HTTP, sans faux état d'import ni succès."""
    from seamtech_search.import_pipeline import ImportResult, _save_import, get_import
    from seamtech_search.jobs import create_job

    index = base_recherche['index']
    identifiant = 'suivi-pg-synthetique'
    create_job(index, identifiant, str(tmp_path), status=job_status, stage=job_status)
    _save_import(index, ImportResult(
        import_id=identifiant, source_path=str(tmp_path), status='completed',
        files_detected=0, analyzed_files=0, technical_pdf=None, data=None, report_path=None,
        upload_status='not_configured', warnings=[], files=[], publication_pending=True,
    ))
    assert get_import(index, identifiant)['status'] == 'completed'
    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'unused.db',
                    database_url=base_recherche['url'], min_free_bytes=0)
    with TestClient(create_app(cfg)) as client:
        reponse = client.get(f'/imports/{identifiant}')
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps['job_status'] == job_status
    assert corps['import_status'] == 'completed'  # état réellement enregistré
    assert corps['status'] == ('needs_review' if job_status == 'completed' else job_status)
    assert corps['result']['status'] == 'needs_review'
    assert corps['publication_pending'] is True
    assert corps['result']['publication_pending'] is True
    assert corps['all_verified'] is False
