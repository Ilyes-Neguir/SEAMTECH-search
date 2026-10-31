"""Capture, publication et intention sur PostgreSQL jetable réel."""
from pathlib import Path

import pytest

from seamtech_search import import_pipeline as pipeline
from seamtech_search.config import AppConfig
from seamtech_search.storage import S3StorageClient
from tests.s3_en_memoire import S3EnMemoire

pytestmark = pytest.mark.postgres


def _magasin(monkeypatch):
    magasin = S3EnMemoire()
    monkeypatch.setattr(S3StorageClient, '_get_client', lambda self, **_kwargs: magasin)
    monkeypatch.setattr(S3StorageClient, 'ensure_bucket_exists', lambda self: True)
    return magasin


def _config(tmp_path: Path, source: Path, url: str) -> AppConfig:
    return AppConfig(root_paths=[source], database_path=tmp_path / 'index.db', database_url=url,
                     min_free_bytes=0, s3_endpoint_url='https://s3.invalide', s3_bucket='bucket-synthetique',
                     s3_access_key='cle-synthetique', s3_secret_key='secret-synthetique')


def test_capture_et_liaison_initiale_pg(base_recherche, tmp_path: Path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    piece = source / 'piece.txt'
    piece.write_bytes(b'objet-synthetique')
    cfg = _config(tmp_path, source, base_recherche['url'])
    index = base_recherche['index']
    magasin = _magasin(monkeypatch)
    resultat = pipeline.import_folder(source, cfg, index, import_id='capture-pg')
    assert resultat.transport_verifie and not resultat.all_verified and not resultat.publication_pending
    fichier = resultat.files[0]
    assert fichier.source_sha256 and fichier.object_key
    assert magasin.objets[fichier.object_key]['donnees'] == piece.read_bytes()
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('SELECT path_key, object_key, object_bucket FROM documents WHERE path = %s', (str(piece),))
        assert curseur.fetchone() == (str(piece), fichier.object_key, fichier.object_bucket)
        curseur.execute('SELECT payload FROM imports WHERE id = %s', ('capture-pg',))
        contenu = curseur.fetchone()[0]
    assert not contenu['publication_pending'] and contenu['files'][0]['source_sha256'] == fichier.source_sha256


def test_panne_publication_pg_garde_intention_sans_document(base_recherche, tmp_path: Path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    piece = source / 'piece.txt'
    piece.write_bytes(b'objet-synthetique')
    cfg = _config(tmp_path, source, base_recherche['url'])
    index = base_recherche['index']
    magasin = _magasin(monkeypatch)
    sauvegarder = pipeline._save_import
    def echec_final(idx, resultat):
        if resultat.publication_pending:
            return sauvegarder(idx, resultat)
        raise RuntimeError('panne-synthetique')
    monkeypatch.setattr(pipeline, '_save_import', echec_final)
    with pytest.raises(RuntimeError, match='panne-synthetique'):
        pipeline.import_folder(source, cfg, index, import_id='intention-pg')
    assert len(magasin.envois) == 1 and piece.exists()
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('SELECT count(*) FROM documents WHERE path = %s', (str(piece),))
        assert curseur.fetchone()[0] == 0
        curseur.execute('SELECT payload FROM imports WHERE id = %s', ('intention-pg',))
        intention = curseur.fetchone()[0]
    assert intention['publication_pending'] and not intention['all_verified']
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.retry_upload(index, cfg, 'intention-pg')
    assert len(magasin.envois) == 1


def test_correction_interrompue_enregistre_intention_et_donnees_pg(base_recherche, tmp_path: Path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    piece = source / 'piece.pdf'
    piece.write_bytes(b'FICHE DE FABRICATION\nReference: SYNTHESE')
    cfg = _config(tmp_path, source, base_recherche['url'])
    index = base_recherche['index']
    magasin = _magasin(monkeypatch)
    monkeypatch.setattr(pipeline, 'extract_structured_pdf', lambda path, _cfg: pipeline.ExtractedData(
        raw_text=path.read_text(), reference='SYNTHESE', extraction_status='partial'))
    initial = pipeline.import_folder(source, cfg, index, import_id='correction-pg')
    assert initial.transport_verifie and len(magasin.envois) == 3
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise RuntimeError('panne-synthetique')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
    with pytest.raises(RuntimeError, match='panne-synthetique'):
        pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('SELECT status, payload FROM imports WHERE id = %s', (initial.import_id,))
        statut, intention = curseur.fetchone()
    assert statut == 'needs_review' and intention['publication_pending'] and intention['report_generation_pending']
    assert intention['data']['reference'] == 'SYNTHESE'
    assert intention['pending_correction_data']['reference'] == 'AUTRE'
    rapports = [a for a in intention['artifacts'] if a['role'] in ('rapport_pdf', 'rapport_docx')]
    assert len(rapports) == 2 and all(a['expected_sha256'] and a['expected_key_base'] for a in rapports)
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.retry_upload(index, cfg, initial.import_id)
    assert len(magasin.envois) == 5 and piece.exists()
