"""Crash réel du worker séparé : reprise PostgreSQL + Redis."""
from __future__ import annotations

import json
import os
import signal
from pathlib import Path

import pytest

from seamtech_search.jobs import create_job, get_job
from tests.test_file_durable_postgres import base_durable  # noqa: F401
from tests.test_worker_process import RACINE_DEPOT, _attendre, _lancer_worker, _pdf_volumineux, _rediger_config

pytestmark = [pytest.mark.redis_queue, pytest.mark.postgres]


def test_worker_pg_tue_repris_par_autre_processus(base_durable, tmp_path: Path):  # noqa: F811
    index, redis = base_durable['index'], base_durable['redis']
    client = redis._get_client()
    archive = tmp_path / 'archive'
    source = archive / 'dossier'
    source.mkdir(parents=True)
    fiche = RACINE_DEPOT / 'sample_data/CLIENT-123/fiche-technique.pdf'
    (source / 'fiche-technique.pdf').write_bytes(fiche.read_bytes())
    _pdf_volumineux(source / 'annexe.pdf', pages=120)
    config_path = _rediger_config(tmp_path, archive, 15)
    config = json.loads(config_path.read_text())
    config['database_url'] = base_durable['url']
    config_path.write_text(json.dumps(config))
    job_id = 'job-processus-pg'
    create_job(index, job_id, str(source), durability='durable')
    assert redis.enqueue_task('imports', {'job_id': job_id, 'source_path': str(source)})
    premier = _lancer_worker(config_path, une_passe=False)
    try:
        _attendre(lambda: (get_job(index, job_id) or {}).get('status') == 'running', 30, 'claim PG absent')
        assert client.llen('seamtech:processing:imports') == 1
        os.kill(premier.pid, signal.SIGKILL)
        premier.wait(timeout=10)
        assert premier.returncode == -signal.SIGKILL
    finally:
        if premier.poll() is None:
            premier.kill()
            premier.wait(timeout=10)
    assert get_job(index, job_id)['status'] == 'running'
    _attendre(lambda: client.exists('seamtech:claim:imports:' + job_id) == 0, 25, 'claim non expiré')
    second = _lancer_worker(config_path, une_passe=True)
    try:
        sortie, _ = second.communicate(timeout=90)
        assert second.returncode == 0, sortie[-4000:]
    finally:
        if second.poll() is None:
            second.kill()
            second.wait(timeout=10)
    job = get_job(index, job_id)
    assert job['status'] == 'completed', job
    assert job['attempts'] >= 2
    assert redis.profondeur_file('imports')['processing'] == 0
    assert source.exists() and (source / 'annexe.pdf').exists()
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute('SELECT COUNT(*), COUNT(DISTINCT path) FROM documents')
            total, uniques = cursor.fetchone()
    assert total == uniques and total >= 2


@pytest.mark.parametrize('point', ['retry_avant', 'retry_apres', 'ack_avant', 'ack_apres'])
def test_sigkill_frontieres_transfert_pg_redis(base_durable, tmp_path: Path, point: str):  # noqa: F811
    """Point d'arrêt instrumenté : kill réel, données fictives, services réels."""
    import subprocess
    import sys

    index, redis = base_durable['index'], base_durable['redis']
    client = redis._get_client()
    archive = tmp_path / 'archive-frontiere'
    source = archive / 'dossier'
    source.mkdir(parents=True)
    (source / 'fiche-technique.pdf').write_bytes(
        (RACINE_DEPOT / 'sample_data/CLIENT-123/fiche-technique.pdf').read_bytes()
    )
    config_path = _rediger_config(tmp_path, archive, 15)
    config = json.loads(config_path.read_text())
    config['database_url'] = base_durable['url']
    config_path.write_text(json.dumps(config))
    job_id = 'job-frontiere'
    create_job(index, job_id, str(source), durability='durable')
    assert redis.enqueue_task('imports', {'job_id': job_id, 'source_path': str(source)})
    marqueur = tmp_path / 'frontiere-atteinte'
    script = tmp_path / 'worker-instrumente.py'
    script.write_text('''
import os, sys, time
from pathlib import Path
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import update_job
from seamtech_search.redis_store import RedisStore
from seamtech_search import worker
config = AppConfig.load(Path(sys.argv[1]))
index = SearchIndex(config.database_path, config.database_url)
redis = RedisStore(redis_url=config.redis_url)
point, marqueur = sys.argv[2], Path(sys.argv[3])
def traiter(task, config, index, redis_store, *, worker_id, **kwargs):
    if point.startswith('retry'):
        return {'job_id': task['job_id'], 'status': 'failed', 'error': 'panne synthetique'}
    update_job(index, task['job_id'], status='completed', result={'preuve': 'termine'}, expected_worker=worker_id)
    return {'job_id': task['job_id'], 'status': 'completed'}
worker.process_import_task = traiter
nom = 'retry_task' if point.startswith('retry') else 'ack_task'
operation = getattr(redis, nom)
def intercepter(*args, **kwargs):
    if point.endswith('apres'):
        resultat = operation(*args, **kwargs)
        assert resultat
    marqueur.write_text('atteint')
    while True:
        time.sleep(1)
setattr(redis, nom, intercepter)
worker.worker_loop(config, index, redis, worker_id='worker-frontiere', run_once=True)
''', encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(RACINE_DEPOT))
    enfant = subprocess.Popen([sys.executable, str(script), str(config_path), point, str(marqueur)],
                              cwd=RACINE_DEPOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _attendre(lambda: marqueur.exists() or enfant.poll() is not None, 30, 'point non atteint')
        assert marqueur.exists(), f'enfant arrêté avant le point : {enfant.returncode}'
        enfant.kill()
        enfant.wait(timeout=10)
        assert enfant.returncode == -signal.SIGKILL
    finally:
        if enfant.poll() is None:
            enfant.kill()
            enfant.wait(timeout=10)
    job = get_job(index, job_id)
    profondeur = redis.profondeur_file('imports')
    if point.startswith('retry'):
        assert job['status'] == 'running'
        assert profondeur['processing' if point.endswith('avant') else 'retry'] == 1
    else:
        assert job['status'] == 'completed' and job['result'] == {'preuve': 'termine'}
        assert profondeur['processing'] == (1 if point.endswith('avant') else 0)
    _attendre(lambda: client.exists('seamtech:claim:imports:' + job_id) == 0, 25, 'claim non expiré')
    second = _lancer_worker(config_path, une_passe=True)
    try:
        sortie, _ = second.communicate(timeout=90)
        assert second.returncode == 0, sortie[-4000:]
    finally:
        if second.poll() is None:
            second.kill()
            second.wait(timeout=10)
    job = get_job(index, job_id)
    assert job['status'] == 'completed', job
    assert redis.profondeur_file('imports')['processing'] == 0
    assert source.exists() and (source / 'fiche-technique.pdf').exists()
