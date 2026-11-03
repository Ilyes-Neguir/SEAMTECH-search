"""Publication de l'import classique sous perte de claim Redis/PG."""
from __future__ import annotations

from pathlib import Path

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.jobs import create_job, marquer_job_claim
from seamtech_search.worker import process_import_task
from tests.test_file_durable_postgres import base_durable  # noqa: F401
from tests.test_worker_process import RACINE_DEPOT

pytestmark = [pytest.mark.postgres, pytest.mark.redis_queue]


def test_perte_claim_durant_upsert_annule_documents_et_import(base_durable, tmp_path: Path, monkeypatch):  # noqa: F811
    index, store = base_durable['index'], base_durable['redis']
    source = tmp_path / 'archive' / 'dossier'
    source.mkdir(parents=True)
    (source / 'fiche.pdf').write_bytes((RACINE_DEPOT / 'sample_data/CLIENT-123/fiche-technique.pdf').read_bytes())
    config = AppConfig(root_paths=[source.parent], database_path=tmp_path / 'index.db',
                       database_url=base_durable['url'], min_free_bytes=0, redis_url=store.redis_url)
    job_id = 'job-perte-claim-publication'
    create_job(index, job_id, str(source), durability='durable')
    assert marquer_job_claim(index, job_id, 'worker-A')
    assert store.revendiquer_tache('imports', job_id, 'worker-A', ttl_seconds=60)
    upsert = index.upsert_documents
    def publier_puis_perdre(documents, **kwargs):
        resultat = upsert(documents, **kwargs)
        assert store.revendiquer_tache('imports', job_id, 'worker-B', ttl_seconds=60, reprendre=True)
        return resultat
    monkeypatch.setattr(index, 'upsert_documents', publier_puis_perdre)
    retour = process_import_task({'job_id': job_id, 'source_path': str(source)}, config, index, store, worker_id='worker-A')
    assert retour['status'] == 'stale_worker'
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM documents')
            assert cursor.fetchone()[0] == 0
            cursor.execute('SELECT count(*) FROM imports WHERE id = %s', (job_id,))
            assert cursor.fetchone()[0] == 0
    assert source.exists()


def test_panne_enregistrement_import_annule_documents(base_durable, tmp_path: Path, monkeypatch):  # noqa: F811
    from seamtech_search import import_pipeline
    index, store = base_durable['index'], base_durable['redis']
    source = tmp_path / 'archive' / 'dossier'
    source.mkdir(parents=True)
    (source / 'fiche.pdf').write_bytes((RACINE_DEPOT / 'sample_data/CLIENT-123/fiche-technique.pdf').read_bytes())
    config = AppConfig(root_paths=[source.parent], database_path=tmp_path / 'index.db',
                       database_url=base_durable['url'], min_free_bytes=0, redis_url=store.redis_url)
    job_id = 'job-panne-save-import'
    create_job(index, job_id, str(source), durability='durable')
    assert marquer_job_claim(index, job_id, 'worker-A')
    assert store.revendiquer_tache('imports', job_id, 'worker-A', ttl_seconds=60)
    def echec(*_args, **_kwargs):
        raise RuntimeError('panne enregistrement')
    monkeypatch.setattr(import_pipeline, '_save_import', echec)
    retour = process_import_task({'job_id': job_id, 'source_path': str(source)}, config, index, store, worker_id='worker-A')
    assert retour['status'] == 'failed'
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM documents')
            assert cursor.fetchone()[0] == 0
            cursor.execute('SELECT count(*) FROM imports WHERE id = %s', (job_id,))
            assert cursor.fetchone()[0] == 0


def test_transfert_claim_pg_attend_la_fin_publication(base_durable, tmp_path: Path, monkeypatch):  # noqa: F811
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from seamtech_search import import_pipeline
    from seamtech_search.indexer import SearchIndex
    index, store = base_durable['index'], base_durable['redis']
    autre_index = SearchIndex(tmp_path / 'autre.db', base_durable['url'])
    source = tmp_path / 'archive' / 'dossier'
    source.mkdir(parents=True)
    (source / 'fiche.pdf').write_bytes((RACINE_DEPOT / 'sample_data/CLIENT-123/fiche-technique.pdf').read_bytes())
    config = AppConfig(root_paths=[source.parent], database_path=tmp_path / 'index.db',
                       database_url=base_durable['url'], min_free_bytes=0, redis_url=store.redis_url)
    job_id = 'job-concurrence-publication'
    create_job(index, job_id, str(source), durability='durable')
    assert marquer_job_claim(index, job_id, 'worker-A')
    assert store.revendiquer_tache('imports', job_id, 'worker-A', ttl_seconds=60)
    original = import_pipeline._save_import
    entre = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    futurs = []
    def transfert():
        entre.set()
        return marquer_job_claim(autre_index, job_id, 'worker-B')
    def enregistrer(*args, **kwargs):
        original(*args, **kwargs)
        futur = pool.submit(transfert)
        futurs.append(futur)
        assert entre.wait(timeout=2)
        # La requête du second registre attend bien le verrou de la publication.
        with pytest.raises(TimeoutError):
            futur.result(timeout=0.3)
    monkeypatch.setattr(import_pipeline, '_save_import', enregistrer)
    try:
        process_import_task({'job_id': job_id, 'source_path': str(source)}, config, index, store, worker_id='worker-A')
        assert futurs and futurs[0].result(timeout=5)
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute('SELECT count(*) FROM documents')
                assert cursor.fetchone()[0] == 1
                cursor.execute('SELECT count(*) FROM imports WHERE id = %s', (job_id,))
                assert cursor.fetchone()[0] == 1
    finally:
        pool.shutdown(wait=True)
        autre_index.close()


def test_annulation_pendant_publication_annule_documents_et_import(base_durable, tmp_path: Path, monkeypatch):  # noqa: F811
    from seamtech_search import import_pipeline
    index, store = base_durable['index'], base_durable['redis']
    source = tmp_path / 'archive' / 'dossier'
    source.mkdir(parents=True)
    (source / 'fiche.pdf').write_bytes((RACINE_DEPOT / 'sample_data/CLIENT-123/fiche-technique.pdf').read_bytes())
    config = AppConfig(root_paths=[source.parent], database_path=tmp_path / 'index.db',
                       database_url=base_durable['url'], min_free_bytes=0, redis_url=store.redis_url)
    job_id = 'job-annulation-publication'
    create_job(index, job_id, str(source), durability='durable')
    assert marquer_job_claim(index, job_id, 'worker-A')
    assert store.revendiquer_tache('imports', job_id, 'worker-A', ttl_seconds=60)
    original = import_pipeline._save_import
    def annuler_apres_save(*args, **kwargs):
        original(*args, **kwargs)
        assert store.set_cancel_flag(job_id)
    monkeypatch.setattr(import_pipeline, '_save_import', annuler_apres_save)
    resultat = process_import_task({'job_id': job_id, 'source_path': str(source)}, config, index, store, worker_id='worker-A')
    assert resultat['status'] == 'cancelled'
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM documents')
            assert cursor.fetchone()[0] == 0
            cursor.execute('SELECT count(*) FROM imports WHERE id = %s', (job_id,))
            assert cursor.fetchone()[0] == 0
    assert (source / 'fiche.pdf').exists()


@pytest.mark.xfail(strict=True, reason="Fenêtre entre contrôle Redis et commit PostgreSQL : atomicité distribuée non garantie")
def test_transfert_redis_apres_dernier_controle_ne_doit_pas_publier(base_durable, tmp_path: Path, monkeypatch):  # noqa: F811
    from contextlib import contextmanager

    index, store = base_durable['index'], base_durable['redis']
    source = tmp_path / 'archive' / 'dossier'
    source.mkdir(parents=True)
    (source / 'fiche.pdf').write_bytes((RACINE_DEPOT / 'sample_data/CLIENT-123/fiche-technique.pdf').read_bytes())
    config = AppConfig(root_paths=[source.parent], database_path=tmp_path / 'index.db',
                       database_url=base_durable['url'], min_free_bytes=0, redis_url=store.redis_url)
    job_id = 'job-interstice-commit'
    create_job(index, job_id, str(source), durability='durable')
    assert marquer_job_claim(index, job_id, 'worker-A')
    assert store.revendiquer_tache('imports', job_id, 'worker-A', ttl_seconds=60)
    transaction = index.scan_transaction

    @contextmanager
    def transfert_avant_commit():
        with transaction():
            yield
            assert store.revendiquer_tache('imports', job_id, 'worker-B', ttl_seconds=60, reprendre=True)

    monkeypatch.setattr(index, 'scan_transaction', transfert_avant_commit)
    resultat = process_import_task({'job_id': job_id, 'source_path': str(source)}, config, index, store, worker_id='worker-A')
    assert resultat['status'] == 'stale_worker'
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM documents')
            assert cursor.fetchone()[0] == 0
            cursor.execute('SELECT count(*) FROM imports WHERE id = %s', (job_id,))
            assert cursor.fetchone()[0] == 0


def test_annulation_par_autre_processus_interrompt_import(base_durable, tmp_path: Path, monkeypatch):  # noqa: F811
    import subprocess
    import sys

    from seamtech_search.jobs import get_job

    index, store = base_durable['index'], base_durable['redis']
    source = tmp_path / 'source'
    source.mkdir()
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db',
                       database_url=base_durable['url'], min_free_bytes=0, redis_url=store.redis_url)
    job_id = 'annulation-par-autre-processus'
    create_job(index, job_id, str(source), durability='durable')
    assert marquer_job_claim(index, job_id, 'worker-A')
    assert store.revendiquer_tache('imports', job_id, 'worker-A', ttl_seconds=60)
    def traitement(**kwargs):
        programme = (
            'import sys; from pathlib import Path; '
            'from seamtech_search.indexer import SearchIndex; '
            'from seamtech_search.jobs import cancel_job; '
            'idx=SearchIndex(Path(sys.argv[1]), sys.argv[2]); '
            'result=cancel_job(idx, sys.argv[3]); '
            'assert result and result["status"] == "cancelled"; idx.close()'
        )
        subprocess.run([sys.executable, '-c', programme, str(tmp_path / 'autre.db'),
                        base_durable['url'], job_id], check=True, timeout=15)
        assert kwargs['cancel_check'](), 'annulation PG doit être visible sans mémoire partagée ni drapeau Redis'
        kwargs['progress_callback']('en_cours', 30)
        raise AssertionError('traitement poursuivi après annulation')

    monkeypatch.setattr('seamtech_search.worker.import_folder', traitement)
    resultat = process_import_task({'job_id': job_id, 'source_path': str(source)}, config, index, store, worker_id='worker-A')
    assert resultat['status'] == 'cancelled'
    assert get_job(index, job_id)['status'] == 'cancelled'
    assert source.exists()


def test_annulation_concurrente_avant_resultat_ne_declenche_pas_purge(base_durable, tmp_path: Path, monkeypatch):  # noqa: F811
    from seamtech_search import worker
    from seamtech_search.indexer import SearchIndex
    from tests.test_worker_coverage import fake_result

    index, store = base_durable['index'], base_durable['redis']
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'unique.txt').write_text('original')
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db',
                       database_url=base_durable['url'], min_free_bytes=0, redis_url=store.redis_url)
    job_id = 'annulation-avant-resultat'
    create_job(index, job_id, str(source), durability='durable')
    assert marquer_job_claim(index, job_id, 'worker-A')
    assert store.revendiquer_tache('imports', job_id, 'worker-A', ttl_seconds=60)
    autre_index = SearchIndex(tmp_path / 'autre.db', base_durable['url'])
    resultat = fake_result(tmp_path, 'uploaded', True, True, source)
    monkeypatch.setattr(worker, 'import_folder', lambda **kwargs: resultat)
    monkeypatch.setattr(worker, 'decider_purge', lambda *_args: (True, 'test'))
    original = worker.update_job
    def annuler_avant_resultat(index, job_id, **kwargs):
        if kwargs.get('result') is not None and kwargs.get('stage') == 'done':
            with autre_index.connect() as connexion:
                with connexion.cursor() as cursor:
                    cursor.execute("UPDATE import_jobs SET status = 'cancelled' WHERE id = %s", (job_id,))
        return original(index, job_id, **kwargs)
    monkeypatch.setattr(worker, 'update_job', annuler_avant_resultat)
    try:
        reponse = process_import_task({'job_id': job_id, 'source_path': str(source)}, config, index, store, worker_id='worker-A')
        assert reponse['status'] == 'cancelled'
        assert (source / 'unique.txt').read_text() == 'original'
    finally:
        autre_index.close()


def test_annulation_apres_resultat_avant_purge_conserve_original(base_durable, tmp_path: Path, monkeypatch):  # noqa: F811
    from seamtech_search import worker
    from seamtech_search.indexer import SearchIndex
    from tests.test_worker_coverage import fake_result

    index, store = base_durable['index'], base_durable['redis']
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'unique.txt').write_text('original')
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db',
                       database_url=base_durable['url'], min_free_bytes=0, redis_url=store.redis_url)
    job_id = 'annulation-apres-resultat'
    create_job(index, job_id, str(source), durability='durable')
    assert marquer_job_claim(index, job_id, 'worker-A')
    assert store.revendiquer_tache('imports', job_id, 'worker-A', ttl_seconds=60)
    autre_index = SearchIndex(tmp_path / 'autre.db', base_durable['url'])
    monkeypatch.setattr(worker, 'import_folder', lambda **kwargs: fake_result(tmp_path, 'uploaded', True, True, source))
    def annuler_avant_purge(*_args):
        with autre_index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("UPDATE import_jobs SET status = 'cancelled' WHERE id = %s", (job_id,))
        return True, 'test'
    monkeypatch.setattr(worker, 'decider_purge', annuler_avant_purge)
    try:
        reponse = process_import_task({'job_id': job_id, 'source_path': str(source)}, config, index, store, worker_id='worker-A')
        assert reponse['status'] == 'cancelled'
        assert (source / 'unique.txt').read_text() == 'original'
    finally:
        autre_index.close()


def test_intention_interrompue_pg_redis_est_acquittee_en_revue_sans_renvoi(base_durable, tmp_path: Path, monkeypatch):  # noqa: F811
    from seamtech_search import import_pipeline
    from seamtech_search.jobs import get_job
    from seamtech_search.storage import S3StorageClient
    from seamtech_search.worker import worker_loop
    from tests.s3_en_memoire import S3EnMemoire

    index, store = base_durable['index'], base_durable['redis']
    source = tmp_path / 'source'
    source.mkdir()
    piece = source / 'piece.txt'
    piece.write_bytes(b'objet-synthetique')
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db', database_url=base_durable['url'],
                       min_free_bytes=0, redis_url=store.redis_url, s3_endpoint_url='https://s3.invalide',
                       s3_bucket='bucket-synthetique', s3_access_key='cle-synthetique', s3_secret_key='secret-synthetique')
    magasin = S3EnMemoire()
    monkeypatch.setattr(S3StorageClient, '_get_client', lambda self, **_kwargs: magasin)
    monkeypatch.setattr(S3StorageClient, 'ensure_bucket_exists', lambda self: True)
    envoyer = import_pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise RuntimeError('panne-synthetique')
    monkeypatch.setattr(import_pipeline, 'upload_artifacts_to_storage', interrompre)
    create_job(index, 'intention-pg-redis', str(source), durability='durable')
    charge = {'job_id': 'intention-pg-redis', 'source_path': str(source)}
    store.enqueue_task('imports', charge)
    worker_loop(config, index, store, worker_id='worker-intention', run_once=True)
    job = get_job(index, 'intention-pg-redis')
    assert job['status'] == 'needs_review' and job['result']['publication_pending']
    assert job['claimed_by'] is None and store.profondeur_file('imports')['queue'] == 0
    assert not store.get_deadletters('imports') and len(magasin.envois) == 1
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('SELECT count(*) FROM documents WHERE path = %s', (str(piece),))
        assert curseur.fetchone()[0] == 0
        curseur.execute('SELECT payload FROM imports WHERE id = %s', ('intention-pg-redis',))
        assert curseur.fetchone()[0]['publication_pending']
    store.enqueue_task('imports', charge)
    worker_loop(config, index, store, worker_id='worker-intention-2', run_once=True)
    assert len(magasin.envois) == 1 and store.profondeur_file('imports')['queue'] == 0
    assert piece.read_bytes() == b'objet-synthetique'


def test_reprise_partielle_worker_pg_redis_conserve_objet_deja_prouve(base_durable, tmp_path: Path, monkeypatch):  # noqa: F811
    from seamtech_search.jobs import get_job
    from seamtech_search.storage import S3StorageClient, StoragePreflightError
    from seamtech_search.worker import worker_loop
    from tests.s3_en_memoire import S3EnMemoire

    index, store = base_durable['index'], base_durable['redis']
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_bytes(b'original-a-synthetique')
    (source / 'b.txt').write_bytes(b'original-b-synthetique')
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db', database_url=base_durable['url'],
                       min_free_bytes=0, redis_url=store.redis_url, s3_endpoint_url='https://s3.invalide',
                       s3_bucket='bucket-synthetique', s3_access_key='cle-synthetique', s3_secret_key='secret-synthetique')
    magasin = S3EnMemoire()
    monkeypatch.setattr(S3StorageClient, '_get_client', lambda self, **_kwargs: magasin)
    monkeypatch.setattr(S3StorageClient, 'ensure_bucket_exists', lambda self: True)
    envoyer = S3StorageClient.upload_file
    def panne_b(self, chemin, *args, **kwargs):
        if kwargs.get('relative_path') == 'b.txt':
            raise StoragePreflightError('panne avant écriture synthétique')
        return envoyer(self, chemin, *args, **kwargs)
    job_id = 'reprise-partielle-pg-redis'
    create_job(index, job_id, str(source), durability='durable')
    charge = {'job_id': job_id, 'source_path': str(source)}
    store.enqueue_task('imports', charge)
    with monkeypatch.context() as contexte:
        contexte.setattr(S3StorageClient, 'upload_file', panne_b)
        worker_loop(config, index, store, worker_id='worker-partiel-1', run_once=True)
    premier = get_job(index, job_id)
    assert premier['status'] == 'pending' and premier['stage'] == 'requeued'
    assert len(magasin.envois) == 1
    cle_deja_prouvee = magasin.envois[0]['key']
    worker_loop(config, index, store, worker_id='worker-partiel-2', run_once=True)
    repris = get_job(index, job_id)
    assert repris['status'] == 'needs_review' and not repris['result']['all_verified']
    assert repris['result']['preservation']['transport_verifie']
    assert repris['claimed_by'] is None and store.profondeur_file('imports')['queue'] == 0
    assert len(magasin.envois) == 2 and sum(e['key'] == cle_deja_prouvee for e in magasin.envois) == 1
    store.enqueue_task('imports', charge)
    worker_loop(config, index, store, worker_id='worker-partiel-3', run_once=True)
    assert len(magasin.envois) == 2 and not store.get_deadletters('imports')
    assert (source / 'a.txt').read_bytes() == b'original-a-synthetique'
    assert (source / 'b.txt').read_bytes() == b'original-b-synthetique'
