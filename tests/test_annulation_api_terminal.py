"""Le contrat HTTP ne prétend pas annuler un import déjà terminé."""
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import create_job, get_job


@pytest.mark.parametrize("statut", ["completed", "needs_review", "failed", "upload_incomplete"])
def test_annulation_job_clos_retourne_conflit(tmp_path: Path, statut: str):
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "index.db", min_free_bytes=0)
    client = TestClient(create_app(config))
    index = SearchIndex(config.database_path)
    job_id = uuid.uuid4().hex
    create_job(index, job_id, str(tmp_path), status=statut)
    avant = get_job(index, job_id)
    reponse = client.post(f"/imports/{job_id}/cancel")
    assert reponse.status_code == 409
    assert get_job(index, job_id) == avant
    index.close()


def test_import_historique_sans_job_ne_se_dit_pas_annule(tmp_path: Path):
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "index.db", min_free_bytes=0)
    client = TestClient(create_app(config))
    index = SearchIndex(config.database_path)
    identifiant = uuid.uuid4().hex
    with index.connect() as conn:
        conn.execute(
            "INSERT INTO imports (id, source_path, status, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (identifiant, str(tmp_path), "completed", '{"status":"completed"}', "2026-01-01"),
        )
    assert client.post(f"/imports/{identifiant}/cancel").status_code == 409
    assert client.post(f"/imports/{uuid.uuid4().hex}/cancel").status_code == 404
    with index.connect() as conn:
        assert conn.execute("SELECT status FROM imports WHERE id = ?", (identifiant,)).fetchone()[0] == "completed"
        assert conn.execute(
            "SELECT status FROM audit_log WHERE action = 'import_cancel' AND resource = ?", (identifiant,)
        ).fetchone()[0] == "409"
    index.close()
