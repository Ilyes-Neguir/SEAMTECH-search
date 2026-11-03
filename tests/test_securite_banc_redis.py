"""Le banc PG/Redis ne doit jamais effacer la base Redis fournie en entrée."""
from __future__ import annotations

import os
import uuid

import pytest
import redis

from tests.test_file_durable import store as store_durable  # noqa: F401
from tests.test_file_durable_postgres import base_durable  # noqa: F401

pytestmark = [pytest.mark.redis_queue, pytest.mark.postgres]


@pytest.fixture
def redis_exterieur(monkeypatch):
    from tests import test_file_durable as durable_sqlite
    from tests import test_file_durable_postgres as durable_pg
    from tests.helpers_redis_isole import serveur_redis_isole

    # Un second serveur privé joue le rôle de serveur utilisateur : aucun
    # témoin n'est écrit sur l'URL réellement fournie par l'environnement.
    with serveur_redis_isole() as url:
        monkeypatch.setenv("SEAMTECH_TEST_REDIS_URL", url)
        monkeypatch.setattr(durable_sqlite, "REDIS_URL", url)
        monkeypatch.setattr(durable_pg, "URL_REDIS", url)
        client = redis.Redis.from_url(url)
        cle = f"seamtech:temoin-protection-banc:{uuid.uuid4().hex}"
        client.set(cle, "temoin-synthetique")
        try:
            yield client, cle
        finally:
            client.close()


def test_fixture_pg_redis_n_efface_jamais_redis_exterieur(redis_exterieur, base_durable):  # noqa: F811
    client, cle = redis_exterieur
    assert client.get(cle) == b"temoin-synthetique"
    assert base_durable["redis"].redis_url != os.environ["SEAMTECH_TEST_REDIS_URL"]


def test_fixture_ferme_serveur_et_repertoire_si_demarrage_echoue(monkeypatch):
    import subprocess
    from pathlib import Path

    from tests import helpers_redis_isole as helper
    from tests import test_file_durable_postgres as durable

    binaire = helper.binaire_redis()
    if not binaire or os.name == "nt":
        pytest.skip("redis-server Unix requis")
    demarrer = subprocess.Popen
    processus = []
    arguments = []

    def capturer(*args, **kwargs):
        arguments.append(args[0])
        p = demarrer(*args, **kwargs)
        processus.append(p)
        return p

    monkeypatch.setattr(durable, "URL_PG", "postgresql://ne-pas-contacter")
    monkeypatch.setattr(durable, "URL_REDIS", "redis://ne-pas-contacter")
    monkeypatch.setattr(helper, "attendre_redis", lambda _url, _processus: False)
    monkeypatch.setattr(helper.subprocess, "Popen", capturer)
    generation = durable.base_durable.__wrapped__(monkeypatch)
    with pytest.raises(RuntimeError, match="n'a pas démarré"):
        next(generation)
    assert len(processus) == 1
    assert processus[0].poll() is not None
    chemin = Path(arguments[0][arguments[0].index("--unixsocket") + 1])
    assert not chemin.parent.exists()


def test_scenario_quarantaine_ne_vide_pas_le_redis_exterieur(redis_exterieur, tmp_path, redis_isole):
    from tests.test_audit_a06_a07_reprise import test_r3_reprise_conserve_chemins_et_selections

    client, cle = redis_exterieur
    test_r3_reprise_conserve_chemins_et_selections(tmp_path, redis_isole)
    assert client.get(cle) == b"temoin-synthetique"


def test_fixture_file_sqlite_ne_supprime_pas_les_files_exterieures(redis_exterieur, store_durable):  # noqa: F811
    client, cle = redis_exterieur
    # Le motif correspond réellement à la purge historique de la fixture.
    cle_file = "seamtech:queue:temoin-" + uuid.uuid4().hex
    client.rpush(cle_file, "message-synthetique")
    try:
        assert store_durable.redis_url != os.environ["SEAMTECH_TEST_REDIS_URL"]
        assert client.get(cle) == b"temoin-synthetique"
        assert client.lrange(cle_file, 0, -1) == [b"message-synthetique"]
    finally:
        client.delete(cle_file)


def test_binaire_redislite_est_utilise_si_redis_server_absent(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tests import helpers_redis_isole as helper

    binaire = helper.binaire_redis()
    if not binaire:
        pytest.skip("Un binaire de test est requis pour simuler redislite")
    paquet = tmp_path / "redislite"
    (paquet / "bin").mkdir(parents=True)
    (paquet / "bin" / "redis-server").symlink_to(binaire)
    monkeypatch.delenv("SEAMTECH_TEST_REDIS_SERVER", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("importlib.util.find_spec", lambda nom: SimpleNamespace(origin=str(paquet / "__init__.py")) if nom == "redislite" else None)
    assert helper.binaire_redis() == str(paquet / "bin" / "redis-server")
    with helper.serveur_redis_isole() as url:
        assert redis.Redis.from_url(url).ping()


def test_qualification_redis7_refuse_serveur_ancienne_version(monkeypatch):
    from tests import helpers_redis_isole as helper

    # Plus de « os.name == "nt" » ici : depuis que l'isolement Windows passe par
    # un port TCP dédié, cette preuve est exécutable là aussi. L'écarter aurait
    # privé Windows d'une vérification qui n'a rien de spécifique à POSIX.
    if not helper.binaire_redis():
        pytest.skip("binaire redis-server requis (SEAMTECH_TEST_REDIS_SERVER)")
    monkeypatch.setattr(redis.Redis, "info", lambda *_args, **_kwargs: {"redis_version": "6.2.0"})
    with pytest.raises(RuntimeError, match="Redis 7"):
        with helper.serveur_redis_isole(version_minimale=7):
            pass
