"""La reprise des lettres mortes ne doit jamais retirer avant publication."""
from __future__ import annotations

import os
import uuid

import pytest

from seamtech_search.redis_store import RedisStore, queue_keys

pytestmark = pytest.mark.redis_queue


def test_rejeu_destination_invalide_ne_perd_pas_lettre(redis_isole):
    url = os.environ.get('SEAMTECH_TEST_REDIS_URL')
    if not url:
        pytest.skip('Redis de test requis')
    store = RedisStore(redis_url=redis_isole)
    assert store.ping()
    queue = 'rejeu-' + uuid.uuid4().hex
    keys = queue_keys(queue)
    client = store._get_client()
    try:
        client.rpush(keys['deadletter'], '{"job_id":"synthetique"}')
        client.set(keys['queue'], 'mauvais type')
        assert store.replay_deadletters(queue) == 0
        assert client.llen(keys['deadletter']) == 1
    finally:
        client.delete(keys['deadletter'], keys['queue'])


def test_client_sans_blmove_ne_depile_pas_sans_garantie():
    class AncienClient:
        depile = False
        def blpop(self, *_args, **_kwargs):
            self.depile = True
            return ('queue', '{"job_id":"job"}')
    client = AncienClient()
    store = RedisStore(redis_url='redis://unused')
    store._get_client = lambda: client
    assert store.dequeue_task('imports', timeout=1) is None
    assert not client.depile
