"""Le registre reste récupérable avant la transition Redis de retry."""
from __future__ import annotations

import os
import uuid

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import create_job, get_job, marquer_job_claim
from seamtech_search.redis_store import RedisStore
from seamtech_search.worker import process_import_task
from tests.test_worker_coverage import fake_result

pytestmark = pytest.mark.redis_queue


@pytest.mark.parametrize('cas', ['exception', 'upload_incomplete'])
def test_echec_non_terminal_reste_recuperable_avant_retry(tmp_path, monkeypatch, cas, redis_isole):
    url = os.environ.get('SEAMTECH_TEST_REDIS_URL')
    if not url:
        pytest.skip('Redis de test requis')
    url = redis_isole
    store = RedisStore(redis_url=url)
    assert store.ping()
    job_id = 'reprise-simple-' + uuid.uuid4().hex
    source = tmp_path / 'source'
    source.mkdir()
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'index.db', min_free_bytes=0, redis_url=url)
    index = SearchIndex(config.database_path)
    index.initialize()
    index.run_migrations()
    create_job(index, job_id, str(source), durability='durable')
    assert marquer_job_claim(index, job_id, 'worker')
    assert store.revendiquer_tache('imports', job_id, 'worker')
    if cas == 'exception':
        def panne(**_kwargs):
            raise RuntimeError('erreur synthetique')
        monkeypatch.setattr('seamtech_search.worker.import_folder', panne)
    else:
        resultat = fake_result(tmp_path, 'failed', False, False, source)
        monkeypatch.setattr('seamtech_search.worker.import_folder', lambda **_kwargs: resultat)
    try:
        resultat = process_import_task({'job_id': job_id, 'source_path': str(source)}, config, index, store, worker_id='worker')
        assert resultat['status'] in ('failed', 'upload_incomplete')
        job = get_job(index, job_id)
        assert job['status'] == 'running' and job['claimed_by'] == 'worker'
    finally:
        store.liberer_revendication('imports', job_id, 'worker')
        index.close()
