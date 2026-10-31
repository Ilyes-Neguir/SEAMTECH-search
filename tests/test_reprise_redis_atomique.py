"""Reprise avec un vrai Redis et deux consommateurs concurrents."""
from __future__ import annotations

import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from seamtech_search.redis_store import RedisStore, queue_keys

pytestmark = pytest.mark.redis_queue


@pytest.fixture
def file_redis(redis_isole):
    url = os.environ.get('SEAMTECH_TEST_REDIS_URL')
    if not url:
        pytest.skip('Redis de test requis')
    store = RedisStore(redis_url=redis_isole)
    assert store.ping()
    queue = 'reprise-' + uuid.uuid4().hex
    keys = queue_keys(queue)
    yield store, queue, keys
    client = store._get_client()
    client.delete(keys['processing'], keys['queue'], keys['retry'], keys['deadletter'])
    for key in client.scan_iter(match=keys['claim_prefix'] + ':*'):
        client.delete(key)


def test_deux_repreneurs_ne_dupliquent_pas_la_meme_livraison(file_redis, monkeypatch):
    store, queue, keys = file_redis
    client = store._get_client()
    brute = json.dumps({'job_id': 'job', 'task_id': 'tache', 'attempt': 0})
    client.rpush(keys['processing'], brute)
    barriere = threading.Barrier(2)
    lrange = client.lrange

    def lecture_synchrone(key, start, end):
        resultat = lrange(key, start, end)
        if key == keys['processing']:
            barriere.wait(timeout=5)
        return resultat

    monkeypatch.setattr(client, 'lrange', lecture_synchrone)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futurs = [pool.submit(store.reprendre_taches_orphelines, queue) for _ in range(2)]
        decisions = [f.result(timeout=10) for f in futurs]
    assert sum(len(d) for d in decisions) == 1
    assert client.llen(keys['processing']) == 0
    assert client.llen(keys['queue']) == 1


def test_reprise_verifie_claim_au_moment_du_transfert(file_redis, monkeypatch):
    store, queue, keys = file_redis
    client = store._get_client()
    client.rpush(keys['processing'], json.dumps({'job_id': 'job', 'attempt': 0}))
    lecture = client.lrange

    def acquisition_apres_snapshot(key, start, end):
        resultat = lecture(key, start, end)
        assert store.revendiquer_tache(queue, 'job', 'worker-vivant', ttl_seconds=60)
        return resultat

    monkeypatch.setattr(client, 'lrange', acquisition_apres_snapshot)
    # Simule une observation périmée de l'absence de claim.
    monkeypatch.setattr(store, 'revendication', lambda *_args: None)
    assert store.reprendre_taches_orphelines(queue) == []
    assert client.llen(keys['processing']) == 1
    assert client.llen(keys['queue']) == 0


def test_destination_invalide_ne_perd_pas_la_tache(file_redis):
    store, queue, keys = file_redis
    client = store._get_client()
    client.rpush(keys['processing'], json.dumps({'job_id': 'job', 'attempt': 0}))
    client.set(keys['queue'], 'type incorrect')
    try:
        store.reprendre_taches_orphelines(queue)
    except Exception:
        pass
    assert client.llen(keys['processing']) == 1


def test_worker_ne_revendique_pas_livraison_deja_reprise(file_redis):
    store, queue, keys = file_redis
    assert store.enqueue_task(queue, {'job_id': 'job'})
    tache = store.dequeue_task(queue, timeout=1)
    assert tache is not None
    assert store.reprendre_taches_orphelines(queue)[0]['action'] == 'requeued'
    assert not store.revendiquer_tache(queue, 'job', 'worker-retarde', livraison=tache)
    assert store.revendication(queue, 'job') is None
    assert store._get_client().llen(keys['queue']) == 1


def test_retry_et_deadletter_avec_claim_ne_dupliquent_pas_si_livraison_absente(file_redis):
    store, queue, keys = file_redis
    assert store.enqueue_task(queue, {'job_id': 'job'})
    tache = store.dequeue_task(queue, timeout=1)
    assert store.revendiquer_tache(queue, 'job', 'worker', livraison=tache)
    assert store.retry_task(queue, tache, delay_seconds=60, worker_id='worker')
    assert not store.retry_task(queue, tache, delay_seconds=60, worker_id='worker')
    assert not store.deadletter_task(queue, tache, worker_id='worker')
    client = store._get_client()
    assert client.zcard(keys['retry']) == 1
    assert client.llen(keys['deadletter']) == 0


def test_retry_destination_erreur_conserve_traitement(file_redis):
    store, queue, keys = file_redis
    assert store.enqueue_task(queue, {'job_id': 'job'})
    tache = store.dequeue_task(queue, timeout=1)
    assert store.revendiquer_tache(queue, 'job', 'worker', livraison=tache)
    client = store._get_client()
    client.set(keys['retry'], 'type incorrect')
    assert not store.retry_task(queue, tache, delay_seconds=60, worker_id='worker')
    assert client.llen(keys['processing']) == 1


def test_deux_workers_promeuvent_une_seule_fois_une_tentative_differee(file_redis, monkeypatch):
    store, queue, keys = file_redis
    client = store._get_client()
    client.zadd(keys['retry'], {json.dumps({'job_id': 'job', 'attempt': 1}): 0})
    barriere = threading.Barrier(2)
    lire = client.zrangebyscore
    def lecture_synchrone(*args, **kwargs):
        resultat = lire(*args, **kwargs)
        barriere.wait(timeout=5)
        return resultat
    monkeypatch.setattr(client, 'zrangebyscore', lecture_synchrone)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futurs = [pool.submit(store.process_retry_queue, queue) for _ in range(2)]
        nombre = sum(f.result(timeout=10) for f in futurs)
    assert nombre == 1
    assert client.zcard(keys['retry']) == 0
    assert client.llen(keys['queue']) == 1


def test_heartbeat_ne_ressuscite_pas_un_claim_expire(file_redis):
    store, queue, _keys = file_redis
    assert store.revendiquer_tache(queue, 'job', 'worker', ttl_seconds=60)
    client = store._get_client()
    client.delete(f'seamtech:claim:{queue}:job')
    assert store.rafraichir_revendications(queue, ['job'], 'worker', 60) == 0
    assert store.revendication(queue, 'job') is None
