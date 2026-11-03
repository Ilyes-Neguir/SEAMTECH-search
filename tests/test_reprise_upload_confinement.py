"""Tests synthétiques du confinement du renvoi d'artefacts."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from seamtech_search import import_pipeline as p
from seamtech_search.config import AppConfig
from seamtech_search.storage import UploadBatch


def _payload(source: Path, fichier: Path):
    return {'source_path': str(source), 'status': 'needs_review', 'files': [
        {'path': str(fichier), 'name': fichier.name, 'upload_status': 'failed', 'object_key': None}
    ], 'artifacts': []}


def test_reprise_refuse_fichier_manifeste_exterieur(tmp_path: Path):
    source = tmp_path / 'source'
    source.mkdir()
    secret = tmp_path / 'hors-source.txt'
    secret.write_bytes(b'secret-synthetique')
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite')
    envois = []
    with patch.object(p, 'get_import', return_value=_payload(source, secret)), \
         patch.object(p, 'update_import'), \
         patch.object(p, 'upload_artifacts_to_storage', side_effect=lambda **kw: envois.extend(kw['files_to_upload']) or UploadBatch('failed')):
        with pytest.raises((ValueError, OSError, PermissionError)):
            p.retry_upload(None, config, 'id-test')
    assert not envois


def test_reprise_ne_suit_pas_lien_vers_fichier_exterieur(tmp_path: Path):
    source = tmp_path / 'source'
    source.mkdir()
    secret = tmp_path / 'hors-source.txt'
    secret.write_bytes(b'secret-synthetique')
    piece = source / 'piece.txt'
    piece.symlink_to(secret)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite')
    envois = []
    with patch.object(p, 'get_import', return_value=_payload(source, piece)), \
         patch.object(p, 'update_import'), \
         patch.object(p, 'upload_artifacts_to_storage', side_effect=lambda **kw: envois.extend(Path(f).read_bytes() for f in kw['files_to_upload']) or UploadBatch('failed')):
        with pytest.raises((ValueError, OSError, PermissionError)):
            p.retry_upload(None, config, 'id-test')
    assert not envois


def test_reprise_televerse_copie_epinglee_malgre_substitution_du_chemin(tmp_path: Path):
    from seamtech_search.storage import UploadedArtifact

    source = tmp_path / 'source'
    source.mkdir()
    secret = tmp_path / 'hors-source.txt'
    secret.write_bytes(b'secret-synthetique')
    piece = source / 'piece.txt'
    piece.write_bytes(b'original-synthetique')
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite')
    contenus = []
    def envoyer(**kw):
        copie = kw['files_to_upload'][0]
        piece.unlink()
        piece.symlink_to(secret)
        contenus.append(copie.read_bytes())
        return UploadBatch('uploaded', [UploadedArtifact(str(copie), copie.name, key='cle',
                           bucket='bucket-synthetique', status='uploaded', verified=True)])
    with patch.object(p, 'get_import', return_value=_payload(source, piece)), \
         patch.object(p, 'update_import'), patch.object(p, 'upload_artifacts_to_storage', side_effect=envoyer):
        resultat = p.retry_upload(None, config, 'id-test')
    assert contenus == [b'original-synthetique']
    assert resultat['artifacts'][0]['path'] == str(piece)
    assert resultat['artifacts'][0]['name'] == piece.name


@pytest.mark.parametrize('forme', ['lien-dossier', 'lien-physique', 'fichier-trop-grand'])
def test_reprise_refuse_entree_non_sure(tmp_path: Path, forme: str):
    import os

    source = tmp_path / 'source'
    source.mkdir()
    externe = tmp_path / 'secret.txt'
    externe.write_bytes(b'12345678')
    if forme == 'lien-dossier':
        (source / 'dir').symlink_to(tmp_path, target_is_directory=True)
        piece = source / 'dir' / 'secret.txt'
    else:
        piece = source / 'piece.txt'
        if forme == 'lien-physique':
            os.link(externe, piece)
        else:
            piece.write_bytes(b'12345678')
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite', max_file_size_bytes=4)
    envois = []
    with patch.object(p, 'get_import', return_value=_payload(source, piece)), \
         patch.object(p, 'update_import'), \
         patch.object(p, 'upload_artifacts_to_storage', side_effect=lambda **kw: envois.append(kw)):
        with pytest.raises((ValueError, OSError)):
            p.retry_upload(None, config, 'id-test')
    assert not envois


def test_reprise_stocke_nom_et_cle_logiques_sans_dependre_de_la_copie(tmp_path: Path):
    from seamtech_search.storage import S3StorageClient, artifact_object_key
    from tests.s3_en_memoire import S3EnMemoire

    source = tmp_path / 'source'
    source.mkdir()
    piece = source / 'piece.txt'
    piece.write_bytes(b'original-synthetique')
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite',
                       s3_endpoint_url='https://stockage.invalide', s3_access_key='cle-synthetique',
                       s3_secret_key='secret-synthetique', s3_bucket='bucket-synthetique')
    magasin = S3EnMemoire()
    attendu = artifact_object_key('id-test', piece, source_root=source)
    with patch.object(p, 'get_import', return_value=_payload(source, piece)), \
         patch.object(p, 'update_import'), \
         patch.object(S3StorageClient, '_get_client', lambda *_args, **_kw: magasin), \
         patch.object(S3StorageClient, 'ensure_bucket_exists', lambda _self: True):
        resultat = p.retry_upload(None, config, 'id-test')
    assert not resultat['all_verified']
    assert resultat['preservation']['transport_verifie']
    assert resultat['preservation']['identite_locale_observee']
    entree = resultat['artifacts'][0]
    assert entree['path'] == str(piece)
    assert entree['name'] == 'piece.txt'
    assert entree['key'] == attendu
    assert magasin.get_object(Bucket='bucket-synthetique', Key=attendu)['Body'].read() == b'original-synthetique'


@pytest.mark.parametrize('erreur', [ValueError, PermissionError, OSError])
def test_api_reprise_signale_refus_sans_divulguer_chemin(tmp_path: Path, erreur):
    from fastapi.testclient import TestClient

    from seamtech_search import api

    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    client = TestClient(api.create_app(config))
    with patch.object(api, 'retry_upload', side_effect=erreur('NOM-SYNTHETIQUE-CONFIDENTIEL')):
        reponse = client.post('/imports/id-test/retry-upload')
    assert reponse.status_code == 409
    assert 'NOM-SYNTHETIQUE-CONFIDENTIEL' not in reponse.text


def test_cle_et_metadonnees_reprise_ne_suivent_pas_substitution(tmp_path: Path):
    from seamtech_search.storage import S3StorageClient, artifact_object_key
    from tests.s3_en_memoire import S3EnMemoire

    source = tmp_path / 'source'
    source.mkdir()
    piece = source / 'piece.txt'
    piece.write_bytes(b'original')
    secret = tmp_path / 'secret.txt'
    secret.write_bytes(b'secret')
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite',
                       s3_endpoint_url='https://stockage.invalide', s3_access_key='cle-synthetique',
                       s3_secret_key='secret-synthetique', s3_bucket='bucket-synthetique')
    magasin = S3EnMemoire()
    attendu = artifact_object_key('id-test', piece, source_root=source)
    vrai_envoi = p.upload_artifacts_to_storage
    def substituer(**kwargs):
        piece.unlink()
        piece.symlink_to(secret)
        return vrai_envoi(**kwargs)
    with patch.object(p, 'get_import', return_value=_payload(source, piece)), \
         patch.object(p, 'update_import'), \
         patch.object(p, 'upload_artifacts_to_storage', side_effect=substituer), \
         patch.object(S3StorageClient, '_get_client', lambda *_args, **_kw: magasin), \
         patch.object(S3StorageClient, 'ensure_bucket_exists', lambda _self: True):
        resultat = p.retry_upload(None, config, 'id-test')
    entree = resultat['artifacts'][0]
    assert entree['key'] == attendu
    corps = magasin.get_object(Bucket='bucket-synthetique', Key=attendu)['Body']
    try:
        assert corps.read() == b'original'
    finally:
        corps.close()


def test_reprise_refuse_racine_remplacee_pendant_envois(tmp_path: Path):
    from seamtech_search.storage import UploadedArtifact

    source = tmp_path / 'source'
    source.mkdir()
    a = source / 'a.txt'
    b = source / 'b.txt'
    a.write_bytes(b'a-original')
    b.write_bytes(b'b-original')
    externe = tmp_path / 'externe'
    externe.mkdir()
    (externe / 'b.txt').write_bytes(b'secret')
    payload = _payload(source, a)
    payload['files'].append({'path': str(b), 'name': b.name, 'upload_status': 'failed'})
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db.sqlite')
    recus = []
    def envoyer(**kw):
        copie = kw['files_to_upload'][0]
        recus.append(copie.read_bytes())
        if len(recus) == 1:
            source.rename(tmp_path / 'source-originale')
            externe.rename(source)
        return UploadBatch('uploaded', [UploadedArtifact(str(copie), copie.name, key=f'k{len(recus)}',
                           bucket='bucket-synthetique', status='uploaded', verified=True)])
    with patch.object(p, 'get_import', return_value=payload), patch.object(p, 'update_import'), \
         patch.object(p, 'upload_artifacts_to_storage', side_effect=envoyer):
        with pytest.raises((ValueError, OSError)):
            p.retry_upload(None, config, 'id-test')
    assert recus == [b'a-original']


def test_reprise_boucle_lien_rapport_refuse_sans_fuite_api(tmp_path: Path):
    from fastapi.testclient import TestClient

    from seamtech_search import api

    source = tmp_path / 'source'
    source.mkdir()
    piece = source / 'piece.txt'
    piece.write_bytes(b'original')
    dossier = tmp_path / 'reports'
    dossier.symlink_to(dossier, target_is_directory=True)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    client = TestClient(api.create_app(config))
    with patch.object(p, 'get_import', return_value=_payload(source, piece)), patch.object(p, 'update_import'):
        reponse = client.post('/imports/id-test/retry-upload')
    assert reponse.status_code == 409


def test_resolution_refuse_boucle_de_liens_symboliques(tmp_path: Path):
    """Python ≤ 3.12 lève RuntimeError sur une boucle : le confinement le refuse."""
    boucle = tmp_path / 'boucle'
    boucle.symlink_to(boucle, target_is_directory=True)
    with pytest.raises(ValueError):
        p.resoudre_chemin_confine(boucle)


def test_resolution_refuse_boucle_meme_si_resolve_reste_muet(tmp_path: Path, monkeypatch):
    """Simule Python ≥ 3.13 : resolve() ne lève plus, la vérification doit refuser.

    Depuis Python 3.13, ``Path.resolve()`` s'appuie sur ``os.path.realpath`` en
    mode non strict : une boucle de liens symboliques est rendue telle quelle
    au lieu de lever. Sans vérification explicite, le confinement croirait le
    chemin résolu et laisserait passer une reprise.
    """
    aller = tmp_path / 'aller'
    retour = tmp_path / 'retour'
    aller.symlink_to(retour, target_is_directory=True)
    retour.symlink_to(aller, target_is_directory=True)

    def resolve_muet(self, strict=False):
        return Path(os.path.realpath(str(self)))

    monkeypatch.setattr(Path, 'resolve', resolve_muet)
    with pytest.raises(ValueError):
        p.resoudre_chemin_confine(aller)


def test_resolution_accepte_chemins_absents_et_reels(tmp_path: Path):
    reel = tmp_path / 'reel'
    reel.mkdir()
    assert p.resoudre_chemin_confine(reel) == reel.resolve()
    absent = tmp_path / 'absent' / 'sous-dossier'
    assert p.resoudre_chemin_confine(absent) == absent.resolve()


def test_reprise_boucle_croisee_liens_rapports_refuse_sans_fuite_api(tmp_path: Path):
    """Deux liens qui se renvoient l'un l'autre : refus 409 sans fuite d'artefact."""
    from fastapi.testclient import TestClient

    from seamtech_search import api

    source = tmp_path / 'source'
    source.mkdir()
    piece = source / 'piece.txt'
    piece.write_bytes(b'original')
    aller = tmp_path / 'reports'
    retour = tmp_path / 'reports-cible'
    aller.symlink_to(retour, target_is_directory=True)
    retour.symlink_to(aller, target_is_directory=True)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite', min_free_bytes=0)
    client = TestClient(api.create_app(config))
    with patch.object(p, 'get_import', return_value=_payload(source, piece)), patch.object(p, 'update_import'):
        reponse = client.post('/imports/id-test/retry-upload')
    assert reponse.status_code == 409
