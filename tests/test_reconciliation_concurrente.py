"""Courses de réconciliation entre PostgreSQL et Redis réels."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import create_job, get_job, marquer_job_claim
from seamtech_search.worker import reconcilier_file
from tests.test_file_durable_postgres import base_durable  # noqa: F401

pytestmark = [pytest.mark.redis_queue, pytest.mark.postgres]


def test_deux_reconciliateurs_ne_creent_pas_deux_livraisons(base_durable, tmp_path: Path, monkeypatch):  # noqa: F811
    index, store = base_durable['index'], base_durable['redis']
    autre_index = SearchIndex(tmp_path / 'unused.db', database_url=base_durable['url'])
    create_job(index, 'job-sans-file', str(tmp_path), durability='durable')
    attente = threading.Barrier(2)
    verifier = store.job_est_dans_file
    def simultane(*args, **kwargs):
        present = verifier(*args, **kwargs)
        attente.wait(timeout=5)
        return present
    monkeypatch.setattr(store, 'job_est_dans_file', simultane)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(reconcilier_file, idx, store) for idx in (index, autre_index)]
            [future.result(timeout=15) for future in futures]
        assert store.profondeur_file('imports')['queue'] == 1
    finally:
        autre_index.close()


def test_reconciliation_sans_file_preserve_une_prise_postgres_concurrente(base_durable, tmp_path: Path, monkeypatch):  # noqa: F811
    index, store = base_durable['index'], base_durable['redis']
    create_job(index, 'job-sans-file-claim', str(tmp_path), durability='durable')
    verifier = store.job_est_dans_file
    def prise_apres_lecture(*args, **kwargs):
        present = verifier(*args, **kwargs)
        assert marquer_job_claim(index, 'job-sans-file-claim', 'worker-nouveau')
        return present
    monkeypatch.setattr(store, 'job_est_dans_file', prise_apres_lecture)
    reconcilier_file(index, store)
    job = get_job(index, 'job-sans-file-claim')
    assert job['claimed_by'] == 'worker-nouveau' and job['status'] == 'running'
