"""Les échecs ou déplacements de dépôt ne divulguent pas les noms de source."""
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.import_pipeline import staging_root
from seamtech_search.worker import mettre_en_quarantaine


@pytest.mark.parametrize('cas', ['externe', 'staging'])
def test_source_en_echec_ne_journalise_pas_son_chemin(tmp_path, caplog, cas):
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite')
    base = staging_root(config) if cas == 'staging' else tmp_path / 'externe'
    source = base / 'NOM-SYNTHETIQUE-CONFIDENTIEL'
    source.mkdir(parents=True)
    original = source / 'piece'
    original.write_bytes(b'contenu-synthetique')
    payload = {'source_path': str(source), 'files': []}
    with caplog.at_level(logging.WARNING), \
         patch('seamtech_search.jobs.update_job_source_path') as maj:
        decision = mettre_en_quarantaine(None, 'id-synthetique', source, config, payload)
    assert not decision.deplace and decision.destination is None
    assert original.read_bytes() == b'contenu-synthetique'
    assert payload['source_path'] == str(source)
    maj.assert_not_called()
    assert 'NOM-SYNTHETIQUE-CONFIDENTIEL' not in caplog.text


def test_echec_worker_ne_divulgue_pas_lexception(tmp_path, caplog):
    from unittest.mock import MagicMock

    from seamtech_search.worker import process_import_task
    from tests.test_worker_coverage import make_config, make_index

    cfg = make_config(tmp_path)
    index = make_index(tmp_path / 'index')
    source = tmp_path / 'source'
    source.mkdir()
    erreur = OSError('SECRET-SYNTHETIQUE ' + str(source))
    with caplog.at_level(logging.ERROR), \
         patch('seamtech_search.worker.import_folder', side_effect=erreur), \
         patch('seamtech_search.worker.update_job', new=MagicMock()) as maj:
        resultat = process_import_task({'job_id': 'test-fuite', 'source_path': str(source)}, cfg, index)
    assert resultat['status'] == 'failed'
    assert 'SECRET-SYNTHETIQUE' not in str(resultat)
    assert 'SECRET-SYNTHETIQUE' not in caplog.text
    assert all('SECRET-SYNTHETIQUE' not in str(appel) for appel in maj.call_args_list)


def test_exception_boucle_worker_ne_fuit_pas_dans_journaux_ou_reprise(tmp_path, caplog):
    from unittest.mock import MagicMock

    from seamtech_search.jobs import create_job
    from seamtech_search.worker import worker_loop
    from tests.test_worker_coverage import make_config, make_index

    cfg = make_config(tmp_path)
    idx = make_index(tmp_path / 'index')
    create_job(idx, 'test-boucle', str(tmp_path))
    redis = MagicMock()
    redis.process_retry_queue.return_value = 0
    redis.dequeue_task.side_effect = [{'job_id': 'test-boucle', 'source_path': str(tmp_path), 'attempt': 0}, None]
    erreur = OSError('SECRET-SYNTHETIQUE ' + str(tmp_path))
    with caplog.at_level(logging.WARNING), \
         patch('seamtech_search.worker.process_import_task', side_effect=erreur), \
         patch('seamtech_search.worker.update_job') as maj:
        worker_loop(cfg, idx, redis, run_once=True)
    assert redis.retry_task.call_count == 1
    assert 'SECRET-SYNTHETIQUE' not in caplog.text
    assert 'SECRET-SYNTHETIQUE' not in str(maj.call_args_list)


def test_interruption_mise_a_jour_conserve_chemin_original_pour_reprise(tmp_path):
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite')
    source = staging_root(config) / 'depot'
    source.mkdir(parents=True)
    original = source / 'piece.pdf'
    original.write_bytes(b'original-synthetique')
    payload = {'source_path': str(source), 'files': []}
    with patch('seamtech_search.jobs.get_job', return_value={}), \
         patch('seamtech_search.jobs.update_job_source_path', side_effect=SystemExit('interruption')):
        try:
            mettre_en_quarantaine(None, 'id-synthetique', source, config, payload)
        except SystemExit:
            pass
    assert original.read_bytes() == b'original-synthetique'
    assert payload['source_path'] == str(source)


@pytest.mark.xfail(strict=True, reason='Le balayage indexe le staging conservé lorsque data est sous une racine')
def test_balayage_nindexe_pas_un_depot_conserve_sur_place(tmp_path):
    from seamtech_search.crawler import crawl

    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite')
    source = staging_root(config) / 'lot' / 'en-echec'
    source.mkdir(parents=True)
    (source / 'piece.txt').write_text('donnees synthetiques')
    decision = mettre_en_quarantaine(None, 'id-synthetique', source, config, {})
    assert not decision.deplace
    assert all(source not in Path(doc.path).parents and Path(doc.path) != source for doc in crawl(config))


@pytest.mark.parametrize('methode,fonction', [('patch', 'correct_import'), ('post', 'retry_upload')])
def test_exception_redis_apres_action_import_ne_divulgue_pas_les_details(tmp_path, monkeypatch, caplog, methode, fonction):
    from fastapi.testclient import TestClient

    from seamtech_search import api
    from seamtech_search.redis_store import RedisStore

    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    client = TestClient(api.create_app(config))
    monkeypatch.setattr(api, fonction, lambda *_args: {'status': 'needs_review'})
    monkeypatch.setattr(RedisStore, 'is_configured', lambda _self: True)
    def echec(*_args, **_kwargs):
        raise RuntimeError('NOM-SYNTHETIQUE-CONFIDENTIEL secret-synthetique')
    monkeypatch.setattr(RedisStore, 'update_job', echec)
    with caplog.at_level(logging.WARNING):
        reponse = client.patch('/imports/id', json={'reference': 'x'}) if methode == 'patch' else client.post('/imports/id/retry-upload')
    assert reponse.status_code == 200
    assert 'NOM-SYNTHETIQUE-CONFIDENTIEL' not in caplog.text
    assert 'secret-synthetique' not in caplog.text
