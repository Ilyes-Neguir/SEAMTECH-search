"""Import sur émulateur S3 réseau isolé ; ne qualifie pas MinIO/R2."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from seamtech_search import import_pipeline as pipeline
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.storage import S3StorageClient, StoragePreflightError

pytestmark = [pytest.mark.s3, pytest.mark.s3rver]


@pytest.fixture()
def environnement_reseau(tmp_path: Path):
    endpoint = os.environ.get('SEAMTECH_TEST_S3RVER_URL')
    if not endpoint:
        pytest.skip('émulateur réseau isolé absent')
    url = urlsplit(endpoint)
    if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.password:
        pytest.fail('émulateur de test isolé sur loopback requis')
    source = tmp_path / 'source'
    source.mkdir()
    cfg = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db', min_free_bytes=0,
                    s3_endpoint_url=endpoint, s3_access_key='S3RVER', s3_secret_key='S3RVER',
                    s3_bucket='capture-test-' + uuid.uuid4().hex)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    client = S3StorageClient(config=cfg)
    client._get_client().create_bucket(Bucket=client.bucket_name)
    try:
        yield source, cfg, index, client
    finally:
        for cle in client.list_keys(''):
            assert client.delete_file(cle)
        client._get_client().delete_bucket(Bucket=client.bucket_name)
        index.close()


def test_capture_envoi_reel_ignore_substitution_source(environnement_reseau, tmp_path: Path):
    source, cfg, index, client = environnement_reseau
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'original-synthetique')
    externe = tmp_path / 'externe.txt'
    externe.write_bytes(b'exterieur-synthetique')
    def substituer(phase, _):
        if phase == 'uploading':
            fichier.unlink()
            fichier.symlink_to(externe)
    resultat = pipeline.import_folder(source, cfg, index, import_id='capture-reseau', progress_callback=substituer)
    assert resultat.transport_verifie and not resultat.all_verified and not resultat.publication_pending
    cle = resultat.files[0].object_key
    assert cle
    objet = client.get_object(cle)
    try:
        assert objet['Body'].read() == b'original-synthetique'
    finally:
        objet['Body'].close()
    with index.connect() as connexion:
        document = connexion.execute('SELECT path_key, object_key FROM documents WHERE path = ?', (str(fichier),)).fetchone()
    assert document['path_key'] == str(fichier) and document['object_key'] == cle


def test_crash_apres_envoi_reseau_conserve_intention_sans_double_envoi(environnement_reseau, monkeypatch):
    source, cfg, index, client = environnement_reseau
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'original-synthetique')
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise RuntimeError('panne-synthetique')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
    with pytest.raises(RuntimeError, match='panne-synthetique'):
        pipeline.import_folder(source, cfg, index, import_id='intention-reseau')
    intention = pipeline.get_import(index, 'intention-reseau')
    assert intention['publication_pending'] and not intention['all_verified']
    cles = client.list_keys('intention-reseau')
    assert len(cles) == 1 and cles[0] == intention['artifacts'][0]['expected_key_base']
    corps = client.get_object(cles[0])['Body']
    try:
        assert corps.read() == fichier.read_bytes()
    finally:
        corps.close()
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.retry_upload(index, cfg, 'intention-reseau')
    assert client.list_keys('intention-reseau') == cles and fichier.exists()


def test_reprise_reseau_ne_reexpedie_pas_source_modifiee(environnement_reseau, monkeypatch):
    source, cfg, index, client = environnement_reseau
    (source / 'a.txt').write_bytes(b'original-a')
    piece = source / 'b.txt'
    piece.write_bytes(b'original-b')
    envoyer = S3StorageClient.upload_file
    def panne_b(self, chemin, *args, **kwargs):
        if kwargs.get('relative_path') == 'b.txt':
            raise StoragePreflightError('panne avant écriture synthétique')
        return envoyer(self, chemin, *args, **kwargs)
    with monkeypatch.context() as contexte:
        contexte.setattr(S3StorageClient, 'upload_file', panne_b)
        initial = pipeline.import_folder(source, cfg, index, import_id='reprise-reseau')
    assert not initial.all_verified and not initial.transport_verifie
    cles_avant = client.list_keys('reprise-reseau')
    assert len(cles_avant) == 1
    piece.write_bytes(b'version-modifiee')
    repris = pipeline.retry_upload(index, cfg, 'reprise-reseau')
    assert not repris['all_verified'] and not repris['preservation']['transport_verifie']
    assert repris['inventaire']['sources_modifiees'] == [str(piece)]
    assert client.list_keys('reprise-reseau') == cles_avant and piece.read_bytes() == b'version-modifiee'


def _pdf_avec_rapports(source, cfg, index, monkeypatch, identifiant):
    piece = source / 'piece.pdf'
    piece.write_bytes(b'FICHE DE FABRICATION\nReference: SYNTHETIQUE')
    monkeypatch.setattr(pipeline, 'extract_structured_pdf', lambda path, _cfg: pipeline.ExtractedData(
        raw_text=path.read_text(), reference='SYNTHETIQUE', extraction_status='partial'))
    initial = pipeline.import_folder(source, cfg, index, import_id=identifiant)
    assert initial.transport_verifie and not initial.publication_pending and not initial.all_verified
    return piece, initial


def test_correction_reseau_reponse_perdue_conserve_cles_sans_renvoi(environnement_reseau, monkeypatch):
    from seamtech_search.storage import StorageWriteUncertain

    source, cfg, index, client = environnement_reseau
    piece, initial = _pdf_avec_rapports(source, cfg, index, monkeypatch, 'correction-reseau')
    original = S3StorageClient.upload_file
    def reponse_perdue(self, *args, **kwargs):
        cle = original(self, *args, **kwargs)
        raise StorageWriteUncertain(cle)
    with monkeypatch.context() as contexte:
        contexte.setattr(S3StorageClient, 'upload_file', reponse_perdue)
        corrige = pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    assert corrige['publication_pending'] and not corrige['all_verified']
    assert corrige['status'] == 'needs_review' and not corrige['transport_verifie']
    rapports = [a for a in corrige['artifacts'] if a['role'] in ('rapport_pdf', 'rapport_docx')]
    assert len(rapports) == 2 and all(a['key'] and a['expected_sha256'] and not a['verified'] for a in rapports)
    for rapport in rapports:
        assert rapport['key'] != rapport['expected_key_base']  # la clé d'origine est déjà occupée
        corps = client.get_object(rapport['key'])['Body']
        try:
            import hashlib
            assert hashlib.sha256(corps.read()).hexdigest() == rapport['expected_sha256']
        finally:
            corps.close()
    cles = client.list_keys(initial.import_id)
    assert len(cles) == 5
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.retry_upload(index, cfg, initial.import_id)
    assert client.list_keys(initial.import_id) == cles and piece.exists()


def test_correction_reseau_echec_preflight_permet_reprise_rapports(environnement_reseau, monkeypatch):
    source, cfg, index, client = environnement_reseau
    piece, initial = _pdf_avec_rapports(source, cfg, index, monkeypatch, 'correction-reprise-reseau')
    def avant_ecriture(*_args, **_kwargs):
        raise StoragePreflightError('panne avant écriture synthétique')
    with monkeypatch.context() as contexte:
        contexte.setattr(S3StorageClient, 'upload_file', avant_ecriture)
        corrige = pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    assert not corrige['publication_pending'] and not corrige['transport_verifie']
    assert len(client.list_keys(initial.import_id)) == 3
    repris = pipeline.retry_upload(index, cfg, initial.import_id)
    assert not repris['publication_pending'] and repris['preservation']['transport_verifie']
    assert not repris['all_verified'] and len(client.list_keys(initial.import_id)) == 5 and piece.exists()


def test_acces_reseau_refuse_objet_corrompu_et_sert_copie_conforme(environnement_reseau, monkeypatch):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    source, cfg, index, client = environnement_reseau
    piece, initial = _pdf_avec_rapports(source, cfg, index, monkeypatch, 'acces-reseau')
    original = piece.read_bytes()
    cle = initial.files[0].object_key
    assert cle
    with index.connect() as connexion:
        piece_id = connexion.execute('SELECT id FROM documents WHERE path_key = ?', (str(piece),)).fetchone()[0]
    piece.write_bytes(b'LOCAL-MODIFIE')
    with TestClient(create_app(cfg)) as navigateur:
        url_piece = f'/pieces/{piece_id}/apercu'
        url_import = f'/imports/{initial.import_id}/artifacts/source_pdf'
        assert navigateur.get(url_piece).content == original
        assert navigateur.get(url_import, headers={'Range': 'bytes=0-7'}).content == original[:8]
        client._get_client().put_object(Bucket=client.bucket_name, Key=cle, Body=b'OBJET-MODIFIE')
        for url in (url_piece, url_import):
            reponse = navigateur.get(url)
            assert reponse.status_code in (404, 409, 502, 503)
            assert b'LOCAL-MODIFIE' not in reponse.content and b'OBJET-MODIFIE' not in reponse.content
        assert navigateur.head(url_piece).status_code in (404, 409, 502, 503)
        piece.write_bytes(original)
        assert navigateur.get(url_piece).content == original
        assert navigateur.get(url_import).content == original


def test_reimport_reseau_conserve_piece_catalogue_et_expose_conflit(environnement_reseau, monkeypatch):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    source, cfg, index, client = environnement_reseau
    piece, initial = _pdf_avec_rapports(source, cfg, index, monkeypatch, 'reimport-reseau-initial')
    original = piece.read_bytes()
    piece.write_bytes(original + b'\n%version-synthetique-modifiee')
    avec_modification = piece.read_bytes()
    second = pipeline.import_folder(source, cfg, index, import_id='reimport-reseau-second')
    assert second.status == 'needs_review' and not second.all_verified
    assert second.files[0].object_key and second.files[0].object_key != initial.files[0].object_key
    assert second.files[0].catalogue_lie is False
    from contextlib import closing

    with closing(client.get_object(initial.files[0].object_key)['Body']) as corps:
        assert corps.read() == original
    with closing(client.get_object(second.files[0].object_key)['Body']) as corps:
        assert corps.read() == avec_modification
    with index.connect() as connexion:
        ligne = connexion.execute('SELECT id, source_sha256, object_key FROM documents WHERE path_key = ?',
                                  (str(piece),)).fetchone()
    assert ligne['source_sha256'] == initial.files[0].source_sha256
    assert ligne['object_key'] == initial.files[0].object_key
    with TestClient(create_app(cfg)) as navigateur:
        assert navigateur.get(f"/pieces/{ligne['id']}/apercu").content == original
        assert navigateur.get(f'/imports/{second.import_id}/artifacts/source_pdf').content == avec_modification
