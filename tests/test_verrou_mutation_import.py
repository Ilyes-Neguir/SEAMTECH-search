"""Conflits des corrections et reprises d'un import synthétique."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from seamtech_search import import_pipeline as pipeline
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from tests.test_import_workflow import make_source


def test_correction_et_reprise_concurrentes_sont_refusees_sans_ecrasement(tmp_path: Path, monkeypatch):
    source = make_source(tmp_path)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    initial = pipeline.import_folder(source, config, index, import_id='id-concurrent')
    debut, fin = Event(), Event()
    generate = pipeline.generate_report
    def suspendre(*args, **kwargs):
        debut.set()
        assert fin.wait(10)
        return generate(*args, **kwargs)
    monkeypatch.setattr(pipeline, 'generate_report', suspendre)
    with ThreadPoolExecutor(max_workers=1) as executor:
        resultat = executor.submit(pipeline.correct_import, index, config, initial.import_id, {'quantity': 12})
        try:
            assert debut.wait(10)
            with pytest.raises(pipeline.ImportMutationEnCours):
                pipeline.correct_import(index, config, initial.import_id, {'reference': 'NOUVELLE'})
            with pytest.raises(pipeline.ImportMutationEnCours):
                pipeline.retry_upload(index, config, initial.import_id)
        finally:
            fin.set()
        resultat.result(timeout=10)
    final = pipeline.correct_import(index, config, initial.import_id, {'reference': 'NOUVELLE'})
    assert final['data']['quantity'] == 12
    assert final['data']['reference'] == 'NOUVELLE'
    index.close()


@pytest.mark.parametrize('methode', ['patch', 'post'])
def test_conflit_mutation_import_est_409_sans_exception_brute(tmp_path: Path, methode: str):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    source = make_source(tmp_path)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    client = TestClient(create_app(config))
    initial = client.post('/imports?wait=true', json={'source_path': str(source)}).json()
    identifiant = initial['import_id']
    with pipeline._verrou_mutation_import(None, config, identifiant):
        reponse = (client.patch(f'/imports/{identifiant}', json={'quantity': 12}) if methode == 'patch'
                   else client.post(f'/imports/{identifiant}/retry-upload'))
    assert reponse.status_code == 409
    assert 'en cours' in reponse.json()['detail'].lower()


def test_verrou_sqlite_est_partage_entre_processus_et_libere_apres_crash(tmp_path: Path):
    import subprocess
    import sys

    source = tmp_path / 'source'
    source.mkdir()
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite')
    script = '''
import sys, time
from pathlib import Path
from seamtech_search.config import AppConfig
from seamtech_search.import_pipeline import _verrou_mutation_import
base = Path(sys.argv[1])
config = AppConfig(root_paths=[base / "source"], database_path=base / "db.sqlite")
with _verrou_mutation_import(None, config, "id-concurrent"):
    print("PRET", flush=True)
    time.sleep(60)
'''
    enfant = subprocess.Popen([sys.executable, '-c', script, str(tmp_path)], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
    try:
        assert enfant.stdout is not None and enfant.stdout.readline().strip() == 'PRET'
        with pytest.raises(pipeline.ImportMutationEnCours):
            with pipeline._verrou_mutation_import(None, config, 'id-concurrent'):
                pass
        with pipeline._verrou_mutation_import(None, config, 'autre-id'):
            pass
    finally:
        enfant.kill()
        enfant.communicate(timeout=5)
    with pipeline._verrou_mutation_import(None, config, 'id-concurrent'):
        pass


def test_verrou_refuse_repertoire_de_liens(tmp_path: Path):
    source = tmp_path / 'source'
    source.mkdir()
    externe = tmp_path / 'externe'
    externe.mkdir()
    (tmp_path / 'import-locks').symlink_to(externe, target_is_directory=True)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite')
    with pytest.raises(OSError):
        with pipeline._verrou_mutation_import(None, config, 'id-test'):
            pass
    assert list(externe.iterdir()) == []


@pytest.mark.postgres
def test_verrou_postgresql_est_partage_par_sessions_independantes(tmp_path: Path):
    import os

    url = os.environ.get('SEAMTECH_TEST_DATABASE_URL')
    if not url:
        pytest.skip('PostgreSQL privé requis')
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite', database_url=url)
    premier = SearchIndex(config.database_path, url)
    second = SearchIndex(config.database_path, url)
    with pipeline._verrou_mutation_import(premier, config, 'id-concurrent'):
        with pytest.raises(pipeline.ImportMutationEnCours):
            with pipeline._verrou_mutation_import(second, config, 'id-concurrent'):
                pass
        with pipeline._verrou_mutation_import(second, config, 'id-autre'):
            pass
    with pipeline._verrou_mutation_import(second, config, 'id-concurrent'):
        pass
    premier.close()
    second.close()


@pytest.mark.xfail(strict=True, reason='Remplacement du fichier de verrou SQLite par le même UID non empêché')
def test_remplacement_verrou_sqlite_ne_permet_pas_deux_mutations(tmp_path: Path):
    import hashlib

    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite')
    nom = hashlib.sha256(b'seamtech-import-mutation:id-test').hexdigest()
    with pipeline._verrou_mutation_import(None, config, 'id-test'):
        (tmp_path / 'import-locks' / nom).unlink()
        with pytest.raises(pipeline.ImportMutationEnCours):
            with pipeline._verrou_mutation_import(None, config, 'id-test'):
                pass


@pytest.mark.postgres
@pytest.mark.xfail(strict=True, reason='Perte de session advisory sans fencing des écritures et du stockage')
def test_perte_session_pg_arrete_mutation_avant_publication(tmp_path: Path):
    import os

    import psycopg2

    url = os.environ.get('SEAMTECH_TEST_DATABASE_URL')
    if not url:
        pytest.skip('PostgreSQL privé requis')
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite', database_url=url)
    index = SearchIndex(config.database_path, url)
    publication = []
    with pytest.raises(pipeline.ImportMutationIncertitude):
        with pipeline._verrou_mutation_import(index, config, 'test-rupture'):
            with psycopg2.connect(url) as admin, admin.cursor() as curseur:
                curseur.execute("SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted AND pid <> pg_backend_pid()")
                pids = [ligne[0] for ligne in curseur.fetchall()]
                assert len(pids) == 1
                curseur.execute('SELECT pg_terminate_backend(%s)', (pids[0],))
            with pipeline._verrou_mutation_import(index, config, 'test-rupture'):
                pass
            publication.append(True)  # la publication serait déjà survenue avant la détection
    assert not publication
    index.close()


@pytest.mark.parametrize('methode,fonction', [('patch', 'correct_import'), ('post', 'retry_upload')])
def test_perte_verrou_api_signale_resultat_incertain(tmp_path: Path, methode: str, fonction: str, monkeypatch):
    from fastapi.testclient import TestClient

    from seamtech_search import api

    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    client = TestClient(api.create_app(config))
    def incertain(*_args, **_kwargs):
        raise pipeline.ImportMutationIncertitude('chemin-confidentiel-synthetique')
    monkeypatch.setattr(api, fonction, incertain)
    reponse = (client.patch('/imports/id-test', json={'quantity': 12}) if methode == 'patch'
               else client.post('/imports/id-test/retry-upload'))
    assert reponse.status_code == 503
    assert 'incertain' in reponse.json()['detail'].lower()
    assert 'chemin-confidentiel-synthetique' not in reponse.text


@pytest.mark.postgres
def test_corrections_pg_concurrentes_refusees_avant_mutation(tmp_path: Path, monkeypatch):
    import json
    import os

    if not os.environ.get('SEAMTECH_TEST_DATABASE_URL'):
        pytest.skip('PostgreSQL privé requis')
    from tests.conftest import _creer_base_jetable, _supprimer_base_jetable

    nom, url = _creer_base_jetable()
    source = tmp_path / 'source'
    source.mkdir()
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite', database_url=url, min_free_bytes=0)
    index = SearchIndex(config.database_path, url)
    payload = {'source_path': str(source), 'status': 'needs_review', 'files': [], 'artifacts': [],
               'data': {'reference': 'ANCIENNE', 'quantity': 1}, 'warnings': []}
    try:
        with index.connect() as conn, conn.cursor() as cur:
            cur.execute('CREATE TABLE imports (id text PRIMARY KEY, status text, payload jsonb)')
            cur.execute('INSERT INTO imports VALUES (%s, %s, %s)', ('id-pg', 'needs_review', json.dumps(payload)))
        debut, fin = Event(), Event()
        generate = pipeline.generate_report
        def suspendre(*args, **kwargs):
            debut.set()
            assert fin.wait(10)
            return generate(*args, **kwargs)
        monkeypatch.setattr(pipeline, 'generate_report', suspendre)
        with ThreadPoolExecutor(max_workers=1) as executor:
            resultat = executor.submit(pipeline.correct_import, index, config, 'id-pg', {'quantity': 12})
            try:
                # Un « assert False » sur ``debut.wait(10)`` ne disait pas
                # POURQUOI le traitement n'avait pas démarré : l'exception du
                # thread restait enfermée dans le Future. Sur Windows (run
                # 41420706781), l'échec est donc resté une ligne rouge sans
                # cause. Elle est désormais propagée : si le thread a échoué,
                # c'est SA panne qu'on lit, pas le constat d'attente déçue.
                if not debut.wait(10):
                    if resultat.done():
                        resultat.result()  # propage la vraie panne du thread
                    raise AssertionError(
                        "le traitement n'a pas atteint generate_report en 10 s et tourne encore"
                    )
                with pytest.raises(pipeline.ImportMutationEnCours):
                    pipeline.correct_import(index, config, 'id-pg', {'reference': 'NOUVELLE'})
                with pytest.raises(pipeline.ImportMutationEnCours):
                    pipeline.retry_upload(index, config, 'id-pg')
            finally:
                fin.set()
            resultat.result(timeout=10)
        final = pipeline.correct_import(index, config, 'id-pg', {'reference': 'NOUVELLE'})
        assert final['data']['quantity'] == 12
        assert final['data']['reference'] == 'NOUVELLE'
    finally:
        index.close()
        _supprimer_base_jetable(nom)


def test_reimport_et_correction_ne_perdent_pas_correction(tmp_path: Path, monkeypatch):
    import threading

    source = make_source(tmp_path)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    initial = pipeline.import_folder(source, config, index, import_id='id-reimport')
    attente, continuer = Event(), Event()
    generate = pipeline.generate_report
    def suspendre(*args, **kwargs):
        if threading.current_thread().name.startswith('reimport'):
            attente.set()
            assert continuer.wait(10)
        return generate(*args, **kwargs)
    monkeypatch.setattr(pipeline, 'generate_report', suspendre)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='reimport') as executor:
        future = executor.submit(pipeline.import_folder, source, config, index, None, initial.import_id)
        try:
            assert attente.wait(10)
            with pytest.raises(pipeline.ImportMutationEnCours):
                pipeline.correct_import(index, config, initial.import_id, {'reference': 'CORRECTION'})
        finally:
            continuer.set()
        future.result(timeout=10)
    pipeline.correct_import(index, config, initial.import_id, {'reference': 'CORRECTION'})
    assert pipeline.get_import(index, initial.import_id)['data']['reference'] == 'CORRECTION'
    index.close()


def test_resultat_corrige_en_base_prime_sur_redis_pending_perime(tmp_path: Path):
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.redis_store import RedisStore

    source = make_source(tmp_path)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    client = TestClient(create_app(config))
    initial = client.post('/imports?wait=true', json={'source_path': str(source)}).json()
    identifiant = initial['import_id']
    assert client.patch(f'/imports/{identifiant}', json={'reference': 'REFERENCE-CORRIGEE'}).status_code == 200
    ancien = {'id': identifiant, 'status': 'pending', 'result': {'data': {'reference': 'ANCIENNE'}}}
    with patch.object(RedisStore, 'is_configured', return_value=True), \
         patch.object(RedisStore, 'get_job', return_value=ancien):
        response = client.get(f'/imports/{identifiant}')
    assert response.status_code == 200
    assert response.json()['data']['reference'] == 'REFERENCE-CORRIGEE'


def test_progression_job_active_reste_visible_avec_ancien_resultat_persistant(tmp_path: Path):
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from seamtech_search import api
    from seamtech_search.redis_store import RedisStore

    source = make_source(tmp_path)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    client = TestClient(api.create_app(config))
    initial = client.post('/imports?wait=true', json={'source_path': str(source)}).json()
    identifiant = initial['import_id']
    record = {'status': 'completed', 'data': {'reference': 'PERSISTEE'}}
    job = {'id': identifiant, 'status': 'running', 'progress': 43, 'stage': 'extracting'}
    ancien = {'id': identifiant, 'status': 'pending', 'result': {'data': {'reference': 'PERIMEE'}}}
    with patch.object(api, 'get_import', return_value=record), patch.object(api, 'get_job', return_value=job), \
         patch.object(RedisStore, 'is_configured', return_value=True), \
         patch.object(RedisStore, 'get_job', return_value=ancien):
        response = client.get(f'/imports/{identifiant}')
    assert response.status_code == 200
    assert response.json()['status'] == 'running'
    assert response.json()['progress'] == 43
    assert response.json()['data']['reference'] == 'PERSISTEE'


@pytest.mark.postgres
def test_import_et_correction_partagent_verrou_pg(tmp_path: Path):
    import os

    url = os.environ.get('SEAMTECH_TEST_DATABASE_URL')
    if not url:
        pytest.skip('PostgreSQL privé requis')
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite', database_url=url)
    index = SearchIndex(config.database_path, url)
    debut, fin = Event(), Event()
    @pipeline._serialiser_import_initial
    def import_simule(*_args):
        debut.set()
        assert fin.wait(10)
        return 'termine'
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(import_simule, tmp_path, config, index, None, 'id-pg-initial')
        try:
            assert debut.wait(10)
            with pytest.raises(pipeline.ImportMutationEnCours):
                pipeline.correct_import(index, config, 'id-pg-initial', {'quantity': 12})
            with pytest.raises(pipeline.ImportMutationEnCours):
                pipeline.retry_upload(index, config, 'id-pg-initial')
        finally:
            fin.set()
        assert future.result(timeout=10) == 'termine'
    index.close()


def test_job_annule_en_base_prime_sur_redis_pending(tmp_path: Path):
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from seamtech_search import api
    from seamtech_search.redis_store import RedisStore

    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    client = TestClient(api.create_app(config))
    job = {'id': 'id-test', 'status': 'cancelled', 'progress': 8, 'stage': 'cancelled'}
    ancien = {'id': 'id-test', 'status': 'pending', 'result': {}}
    with patch.object(api, 'get_import', return_value=None), patch.object(api, 'get_job', return_value=job), \
         patch.object(RedisStore, 'is_configured', return_value=True), \
         patch.object(RedisStore, 'get_job', return_value=ancien):
        response = client.get('/imports/id-test')
    assert response.status_code == 200
    assert response.json()['status'] == 'cancelled'


def test_job_annule_prime_meme_si_rapport_est_inacheve(tmp_path: Path):
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from seamtech_search import api

    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    client = TestClient(api.create_app(config))
    job = {'id': 'id-test', 'status': 'cancelled', 'progress': 8, 'stage': 'cancelled'}
    record = {'status': 'completed', 'report_generation_pending': True, 'all_verified': False}
    with patch.object(api, 'get_import', return_value=record), patch.object(api, 'get_job', return_value=job):
        response = client.get('/imports/id-test')
    assert response.status_code == 200
    assert response.json()['status'] == 'cancelled'
    assert response.json()['report_generation_pending'] is True
    assert response.json()['all_verified'] is False


def test_job_revision_globale_non_prouvee_ne_devient_pas_completed_a_la_lecture(tmp_path: Path):
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from seamtech_search import api

    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    client = TestClient(api.create_app(config))
    job = {'id': 'id-test', 'status': 'needs_review', 'progress': 100, 'stage': 'done',
           'result': {'all_verified': False, 'preservation': {'transport_verifie': True}}}
    record = {'status': 'completed', 'all_verified': False, 'transport_verifie': True}
    with patch.object(api, 'get_import', return_value=record), patch.object(api, 'get_job', return_value=job):
        reponse = client.get('/imports/id-test')
    assert reponse.status_code == 200
    assert reponse.json()['status'] == 'needs_review'
    assert reponse.json()['all_verified'] is False
