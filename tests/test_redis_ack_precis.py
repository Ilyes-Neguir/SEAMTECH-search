"""L'acquittement ne doit pas viser une autre livraison du même job."""
from __future__ import annotations

import json
import os
import uuid

import pytest

from seamtech_search.redis_store import RedisStore, queue_keys

pytestmark = pytest.mark.redis_queue


def test_ack_claim_ne_supprime_pas_autre_livraison(redis_isole):
    url = os.environ.get('SEAMTECH_TEST_REDIS_URL')
    if not url:
        pytest.skip('Redis de test requis')
    store = RedisStore(redis_url=redis_isole)
    assert store.ping()
    queue = 'ack-' + uuid.uuid4().hex
    keys = queue_keys(queue)
    client = store._get_client()
    try:
        livraison = {'job_id': 'job', 'task_id': 'ancien', 'attempt': 0}
        autre = {'job_id': 'job', 'task_id': 'autre', 'attempt': 0}
        client.rpush(keys['processing'], json.dumps(autre))
        assert store.revendiquer_tache(queue, 'job', 'worker')
        assert not store.ack_task(queue, livraison, worker_id='worker')
        assert json.loads(client.lindex(keys['processing'], 0)) == autre
    finally:
        client.delete(keys['processing'], f"{keys['claim_prefix']}:job")


def test_reenfilage_conditionnel_refuse_claim_et_livraisons_existantes(redis_isole):
    url = os.environ.get('SEAMTECH_TEST_REDIS_URL')
    if not url:
        pytest.skip('Redis de test requis')
    store = RedisStore(redis_url=redis_isole)
    assert store.ping()
    queue = 'absence-' + uuid.uuid4().hex
    keys = queue_keys(queue)
    client = store._get_client()
    try:
        assert store.revendiquer_tache(queue, 'job', 'worker')
        assert not store.enqueue_task_si_absent(queue, {'job_id': 'job'})
        assert store.liberer_revendication(queue, 'job', 'worker') == 1
        assert store.enqueue_task_si_absent(queue, {'job_id': 'job'})
        assert not store.enqueue_task_si_absent(queue, {'job_id': 'job'})
        tache = store.dequeue_task(queue, timeout=1)
        assert tache and not store.enqueue_task_si_absent(queue, {'job_id': 'job'})
        assert store.retry_task(queue, tache, delay_seconds=30)
        assert not store.enqueue_task_si_absent(queue, {'job_id': 'job'})
        assert client.zcard(keys['retry']) == 1
    finally:
        client.delete(keys['queue'], keys['processing'], keys['retry'], f"{keys['claim_prefix']}:job")
