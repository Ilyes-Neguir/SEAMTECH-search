"""Crash entre clôture effective et acquittement d'une livraison."""
from __future__ import annotations

from pathlib import Path

import pytest

from seamtech_search.jobs import create_job, get_job
from seamtech_search.worker import worker_loop
from tests.test_file_durable_operations import _config, _index, store  # noqa: F401
from tests.test_worker_coverage import fake_result

pytestmark = pytest.mark.redis_queue


def test_crash_avant_ack_terminal_ne_reexecute_pas_import(store, tmp_path: Path, monkeypatch):  # noqa: F811
    config = _config(tmp_path)
    index = _index(config)
    source = tmp_path / 'archive'
    source.mkdir()
    job_id = 'job-crash-avant-ack'
    create_job(index, job_id, str(source), durability='durable')
    assert store.enqueue_task('imports', {'job_id': job_id, 'source_path': str(source)})
    resultat = fake_result(tmp_path, 'not_configured', False, False, source)
    appels = []
    def importer(**_kwargs):
        appels.append(1)
        return resultat
    monkeypatch.setattr('seamtech_search.worker.import_folder', importer)
    ack = store.ack_task
    def interruption(*_args, **_kwargs):
        raise KeyboardInterrupt('crash avant acquittement')
    monkeypatch.setattr(store, 'ack_task', interruption)
    with pytest.raises(KeyboardInterrupt):
        worker_loop(config, index, store, worker_id='worker-a', run_once=True)
    assert get_job(index, job_id)['status'] == 'completed'
    assert store.profondeur_file('imports')['processing'] == 1
    monkeypatch.setattr(store, 'ack_task', ack)
    worker_loop(config, index, store, worker_id='worker-b', run_once=True)
    assert get_job(index, job_id)['status'] == 'completed'
    assert len(appels) == 1
    assert store.profondeur_file('imports')['processing'] == 0
    assert source.exists()
    index.close()
