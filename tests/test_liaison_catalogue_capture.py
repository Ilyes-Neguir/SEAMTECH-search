"""Liaison atomique d'une reprise au document de mêmes octets (SQLite isolé)."""
from pathlib import Path

import pytest

from seamtech_search import import_pipeline as pipeline
from seamtech_search.indexer import SearchIndex
from seamtech_search.models import Document
from seamtech_search.storage import S3StorageClient, StoragePreflightError
from tests.test_snapshot_import import _brancher_stockage, _config_stockage


def _import_avant_envoi(tmp_path: Path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    piece = source / 'piece.txt'
    piece.write_bytes(b'octets-synthetiques-originaux')
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    magasin = _brancher_stockage(monkeypatch)
    with monkeypatch.context() as contexte:
        contexte.setattr(S3StorageClient, 'upload_file', lambda *_a, **_kw: (_ for _ in ()).throw(
            StoragePreflightError('panne avant ecriture synthetique')))
        initial = pipeline.import_folder(source, cfg, index, import_id='liaison-A')
    assert not initial.publication_pending and not initial.all_verified
    assert not magasin.envois and initial.files[0].source_sha256
    return source, piece, cfg, index, magasin, initial


def _document(index, piece):
    with index.connect() as connexion:
        return dict(connexion.execute(
            'SELECT id, path_key, content_hash, source_sha256, object_key, object_bucket, upload_status '
            'FROM documents WHERE path_key = ?', (str(piece),)).fetchone())


def test_reprise_lie_uniquement_les_octets_captures(tmp_path: Path, monkeypatch):
    _source, piece, cfg, index, magasin, initial = _import_avant_envoi(tmp_path, monkeypatch)
    avant = _document(index, piece)
    assert avant['source_sha256'] == initial.files[0].source_sha256 and avant['object_key'] is None
    assert avant['content_hash'] != avant['source_sha256']
    repris = pipeline.retry_upload(index, cfg, initial.import_id)
    apres = _document(index, piece)
    fichier = repris['files'][0]
    assert apres['id'] == avant['id'] and apres['source_sha256'] == fichier['source_sha256']
    assert apres['object_key'] == fichier['object_key'] and apres['object_bucket'] == fichier['object_bucket']
    assert fichier['catalogue_lie'] is True and magasin.objets[apres['object_key']]['donnees'] == piece.read_bytes()
    assert not repris['all_verified']
    index.close()


def test_reprise_ne_lie_pas_un_document_remplace_entre_temps(tmp_path: Path, monkeypatch):
    _source, piece, cfg, index, magasin, initial = _import_avant_envoi(tmp_path, monkeypatch)
    document = Document(path=piece, name=piece.name, parent_path=piece.parent, extension='.txt',
                        size=99, modified_at=2, is_dir=False, text='autre texte synthetique',
                        content_hash=Document.hash_text('autre texte synthetique'))
    # Ancien écrivain ignorant la capture : la migration doit invalider son
    # identité avant que la reprise ne tente une liaison sur ce chemin.
    index.run_migrations()
    with index.connect() as connexion:
        connexion.execute('UPDATE documents SET size = ?, content_hash = ? WHERE path_key = ?',
                          (document.size, document.content_hash, str(piece)))
    assert _document(index, piece)['source_sha256'] is None
    repris = pipeline.retry_upload(index, cfg, initial.import_id)
    apres = _document(index, piece)
    assert apres['object_key'] is None and apres['content_hash'] == document.content_hash
    assert repris['files'][0]['object_key'] and repris['files'][0]['catalogue_lie'] is False
    assert repris['status'] == 'needs_review' and not repris['all_verified'] and len(magasin.envois) == 1
    index.close()


def test_reprise_necrase_pas_la_liaison_dun_autre_import(tmp_path: Path, monkeypatch):
    source, piece, cfg, index, magasin, initial = _import_avant_envoi(tmp_path, monkeypatch)
    autre = pipeline.import_folder(source, cfg, index, import_id='liaison-B')
    avant = _document(index, piece)
    assert avant['object_key'] == autre.files[0].object_key
    repris = pipeline.retry_upload(index, cfg, initial.import_id)
    apres = _document(index, piece)
    assert apres == avant and repris['files'][0]['object_key'] != avant['object_key']
    assert repris['files'][0]['catalogue_lie'] is False and repris['status'] == 'needs_review'
    assert len(magasin.envois) == 2
    index.close()


def test_reprise_sans_identite_capture_ne_devine_pas_la_liaison(tmp_path: Path, monkeypatch):
    _source, piece, cfg, index, _magasin, initial = _import_avant_envoi(tmp_path, monkeypatch)
    payload = pipeline.get_import(index, initial.import_id)
    assert payload
    payload['files'][0]['source_sha256'] = None
    pipeline.update_import(index, initial.import_id, payload['status'], payload)
    repris = pipeline.retry_upload(index, cfg, initial.import_id)
    assert repris['files'][0]['object_key'] and repris['files'][0]['catalogue_lie'] is False
    assert _document(index, piece)['object_key'] is None
    index.close()


def test_reprise_annule_liaison_si_publication_import_echoue(tmp_path: Path, monkeypatch):
    _source, piece, cfg, index, magasin, initial = _import_avant_envoi(tmp_path, monkeypatch)
    sauvegarder = pipeline.update_import
    def echouer_publication(idx, identifiant, statut, payload):
        if not payload.get('publication_pending'):
            raise RuntimeError('panne-publication-synthetique')
        return sauvegarder(idx, identifiant, statut, payload)
    monkeypatch.setattr(pipeline, 'update_import', echouer_publication)
    with pytest.raises(RuntimeError, match='panne-publication-synthetique'):
        pipeline.retry_upload(index, cfg, initial.import_id)
    assert _document(index, piece)['object_key'] is None
    intention = pipeline.get_import(index, initial.import_id)
    assert intention and intention['publication_pending'] and len(magasin.envois) == 1
    index.close()


def test_reindexation_refuse_ecrasement_identite_octets(tmp_path: Path, monkeypatch):
    _source, piece, _cfg, index, _magasin, initial = _import_avant_envoi(tmp_path, monkeypatch)
    avant = _document(index, piece)
    assert avant['source_sha256'] == initial.files[0].source_sha256
    nouveau = Document(path=piece, name=piece.name, parent_path=piece.parent, extension='.txt',
                       size=avant['id'] + 100, modified_at=123, is_dir=False, text='texte nouveau')
    with pytest.raises(RuntimeError, match='capture'):
        index.upsert_documents([nouveau], strict=True)
    assert _document(index, piece) == avant
    index.close()


def test_migration_identite_est_additive_et_ne_devine_pas_les_anciennes_lignes(tmp_path: Path):
    import sqlite3

    db = tmp_path / 'ancienne.sqlite'
    with sqlite3.connect(db) as connexion:
        connexion.execute('''CREATE TABLE documents (
            id INTEGER PRIMARY KEY, path_key TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
            name TEXT NOT NULL, parent_path TEXT NOT NULL, extension TEXT NOT NULL,
            size INTEGER NOT NULL, modified_at REAL NOT NULL, is_dir INTEGER NOT NULL,
            content_hash TEXT NOT NULL DEFAULT '', object_key TEXT, object_bucket TEXT)''')
        connexion.execute('''INSERT INTO documents (id, path_key, path, name, parent_path, extension,
            size, modified_at, is_dir, content_hash, object_key, object_bucket)
            VALUES (1, '/ancien', '/ancien', 'ancien', '/', '.txt', 3, 1, 0, ?, ?, ?)''',
            ('f' * 64, 'objet-ancien', 'bucket-ancien'))
    index = SearchIndex(db)
    index.initialize()
    index.run_migrations()
    index.run_migrations()
    with index.connect() as connexion:
        ligne = connexion.execute('SELECT id, content_hash, source_sha256, object_key FROM documents WHERE id=1').fetchone()
        migrations = connexion.execute("SELECT count(*) FROM schema_migrations WHERE version = '032_identite_octets_documents'").fetchone()[0]
    assert tuple(ligne) == (1, 'f' * 64, None, 'objet-ancien') and migrations == 1
    index.close()


def test_mutation_par_code_ancien_invalide_identite_avant_reprise(tmp_path: Path, monkeypatch):
    _source, piece, cfg, index, _magasin, initial = _import_avant_envoi(tmp_path, monkeypatch)
    index.run_migrations()
    # Un ancien binaire ne connaît pas la colonne ajoutée ; il modifie pourtant
    # le document dans la même base. Son ancienne preuve ne doit pas survivre.
    with index.connect() as connexion:
        connexion.execute('UPDATE documents SET size = ?, content_hash = ? WHERE path_key = ?',
                          (999, 'texte-remplace', str(piece)))
    assert _document(index, piece)['source_sha256'] is None
    repris = pipeline.retry_upload(index, cfg, initial.import_id)
    assert repris['files'][0]['catalogue_lie'] is False and _document(index, piece)['object_key'] is None
    index.close()


def test_demarrage_refuse_migration_enregistree_sans_declencheur(tmp_path: Path):
    index = SearchIndex(tmp_path / 'db.sqlite')
    index.initialize()
    index.run_migrations()
    with index.connect() as connexion:
        connexion.execute('DROP TRIGGER documents_invalider_source_sha256')
    with pytest.raises(RuntimeError, match='identité|intégrité|déclencheur'):
        index.run_migrations()
    index.close()


def test_diagnostic_signale_declencheur_absent(tmp_path: Path):
    index = SearchIndex(tmp_path / 'db.sqlite')
    index.initialize()
    index.run_migrations()
    assert index.health_details()['schema_application_a_jour'] is True
    with index.connect() as connexion:
        connexion.execute('DROP TRIGGER documents_invalider_source_sha256')
    assert index.health_details()['schema_application_a_jour'] is False
    index.close()


def test_ready_refuse_declencheur_disparu_pendant_execution(tmp_path: Path):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    with TestClient(create_app(cfg)) as navigateur:
        assert navigateur.get('/ready').status_code == 200
        index = SearchIndex(cfg.database_path)
        with index.connect() as connexion:
            connexion.execute('DROP TRIGGER documents_invalider_source_sha256')
        assert navigateur.get('/health').json()['schema_application_a_jour'] is False
        assert navigateur.get('/ready').status_code == 503


def test_declencheur_invalide_meme_les_reecritures_identiques_sqlite(tmp_path: Path):
    index = SearchIndex(tmp_path / 'db.sqlite')
    index.initialize()
    index.run_migrations()
    piece = tmp_path / 'piece.pdf'
    piece.write_bytes(b'original-synthetique')
    document = Document(path=piece, name=piece.name, parent_path=piece.parent, extension='.pdf',
                        size=piece.stat().st_size, modified_at=piece.stat().st_mtime, is_dir=False,
                        source_sha256='a' * 64)
    index.upsert_document(document)
    colonnes = ('path_key', 'path', 'name', 'parent_path', 'extension', 'size', 'modified_at', 'is_dir',
                'extractor_version', 'content_hash', 'extraction_status', 'extraction_detail', 'category')
    for colonne in colonnes:
        with index.connect() as connexion:
            connexion.execute('UPDATE documents SET source_sha256 = ? WHERE path_key = ?', ('a' * 64, str(piece)))
            connexion.execute(f'UPDATE documents SET {colonne} = {colonne} WHERE path_key = ?', (str(piece),))
        assert _document(index, piece)['source_sha256'] is None, colonne
    index.upsert_document(document)
    index.upsert_document(document)
    assert _document(index, piece)['source_sha256'] == 'a' * 64
    with index.connect() as connexion:
        connexion.execute('UPDATE documents SET object_key = ? WHERE path_key = ?', ('cle', str(piece)))
    assert _document(index, piece)['source_sha256'] == 'a' * 64
    index.close()


def test_scan_et_rebuild_preservent_document_capture_sans_objet(tmp_path: Path, monkeypatch):
    _source, piece, _cfg, index, _magasin, _initial = _import_avant_envoi(tmp_path, monkeypatch)
    index.run_migrations()
    identite = _document(index, piece)['source_sha256']
    assert identite and _document(index, piece)['object_key'] is None
    non_capture = Document(path=piece, name=piece.name, parent_path=piece.parent, extension=piece.suffix,
                           size=999, modified_at=2, is_dir=False, text='texte autre')
    assert index.upsert_documents([non_capture]) == 0
    assert _document(index, piece)['source_sha256'] == identite
    assert index.remove_missing(set(), scan_complete=True) == 0
    assert _document(index, piece)['source_sha256'] == identite
    index.initialize(rebuild=True)
    assert index.rebuild_mode == 'en_place'
    index.run_migrations()
    assert _document(index, piece)['source_sha256'] == identite
    index.close()


def test_reimport_capture_preserve_objet_et_expose_conflit_sqlite(tmp_path: Path, monkeypatch):
    from tests.test_snapshot_import import _import_pdf_stocke

    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    piece = source / 'piece.pdf'
    initial_octets = piece.read_bytes()
    avant = _document(index, piece)
    assert avant['source_sha256'] and avant['object_key'] and initial.files[0].catalogue_lie is True
    piece.write_bytes(initial_octets + b'\n%version-synthetique-modifiee')
    second = pipeline.import_folder(source, cfg, index, import_id='reimport-conflit-sqlite')
    assert _document(index, piece) == avant
    fichier = second.files[0]
    assert fichier.object_key and fichier.object_key != avant['object_key'] and fichier.catalogue_lie is False
    assert magasin.objets[avant['object_key']]['donnees'] == initial_octets
    assert magasin.objets[fichier.object_key]['donnees'] == piece.read_bytes()
    assert second.status == 'needs_review' and not second.all_verified
    assert any('catalogue' in avertissement.lower() for avertissement in second.warnings)
    payload = pipeline.get_import(index, second.import_id)
    assert payload['files'][0]['catalogue_lie'] is False and payload['status'] == 'needs_review'
    index.close()


def test_reimport_sans_stockage_ne_promet_pas_indexation_protegee(tmp_path: Path, monkeypatch):
    from tests.test_snapshot_import import _import_pdf_stocke

    source, cfg, index, _magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    piece = source / 'piece.pdf'
    avant = _document(index, piece)
    cfg_local = cfg.model_copy(update={'s3_endpoint_url': None, 's3_access_key': None, 's3_secret_key': None})
    monkeypatch.setattr(pipeline, 'extract_structured_pdf', lambda path, _cfg: pipeline.ExtractedData(
        raw_text=path.read_text(), reference='SYNTHESE', extraction_status='success'))
    second = pipeline.import_folder(source, cfg_local, index, import_id='reimport-local-protege')
    assert _document(index, piece) == avant and initial.files[0].object_key
    assert second.status == 'needs_review' and second.files[0].catalogue_lie is False
    assert any('catalogue' in avertissement.lower() for avertissement in second.warnings)
    index.close()


def test_reimport_octets_differents_preserve_capture_sans_objet_sqlite(tmp_path: Path, monkeypatch):
    import hashlib

    source, piece, cfg, index, magasin, premier = _import_avant_envoi(tmp_path, monkeypatch)
    avant = _document(index, piece)
    assert avant['source_sha256'] == premier.files[0].source_sha256 and avant['object_key'] is None
    piece.write_bytes(b'nouvelle-version-synthetique')
    second = pipeline.import_folder(source, cfg, index, import_id='nouvelle-version-sqlite')

    # La nouvelle capture reçoit son propre objet, mais elle ne remplace pas
    # l'identité précédente dont l'envoi avait échoué avant écriture.
    assert _document(index, piece) == avant
    fichier = second.files[0]
    assert fichier.source_sha256 == hashlib.sha256(piece.read_bytes()).hexdigest()
    assert fichier.source_sha256 != avant['source_sha256']
    assert fichier.object_key and magasin.objets[fichier.object_key]['donnees'] == piece.read_bytes()
    assert fichier.catalogue_lie is False and not second.all_verified
    assert any('catalogue' in avertissement.lower() for avertissement in second.warnings)

    avant_reprise = len(magasin.envois)
    reprise = pipeline.retry_upload(index, cfg, premier.import_id)
    assert reprise['inventaire']['sources_modifiees'] == [str(piece)]
    assert len(magasin.envois) == avant_reprise and _document(index, piece) == avant
    index.close()
