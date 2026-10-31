"""Le bundle doit correspondre aux octets du fournisseur, pas seulement à son ETag."""
import base64
import hashlib

import pytest

from seamtech_search.sauvegarde import copier_objets_s3


def entete(octets, *, type_checksum='FULL_OBJECT'):
    return {'ContentLength': len(octets), 'ETag': 'opaque',
            'ChecksumSHA256': base64.b64encode(hashlib.sha256(octets).digest()).decode(),
            'ChecksumType': type_checksum}


@pytest.mark.parametrize('octets_recu,attendu', [(b'vraie', 1), (b'autre', 0)])
def test_checksum_serveur_compare_aux_octets_reellement_copies(tmp_path, octets_recu, attendu):
    class Client:
        def head_object(self, _cle):
            return entete(b'vraie')

        def download_file(self, _cle, destination):
            destination.write_bytes(octets_recu)

    resultat = copier_objets_s3({'documents': [{'id': 1, 'object_key': 'piece'}]}, Client(), tmp_path)
    assert resultat['nb_copies'] == attendu
    assert resultat['nb_manquants'] == 1 - attendu
    if attendu:
        assert resultat['copies'][0]['verification'] == 'checksum_sha256_fournisseur_full_object'
        assert resultat['copies'][0]['identite_catalogue'] == 'inconnue'
        assert (tmp_path / resultat['copies'][0]['path_relatif']).read_bytes() == b'vraie'
    else:
        assert not (tmp_path / 'objets' / '1' / 'piece').exists()


@pytest.mark.parametrize('type_checksum', ['', 'COMPOSITE', 'inconnu'])
def test_checksum_non_entier_ne_demontre_pas_integrite(tmp_path, type_checksum):
    class Client:
        def head_object(self, _cle):
            return entete(b'vraie', type_checksum=type_checksum)

        def download_file(self, _cle, destination):
            destination.write_bytes(b'vraie')

    resultat = copier_objets_s3({'documents': [{'id': 1, 'object_key': 'piece'}]}, Client(), tmp_path)
    assert resultat['nb_copies'] == 0
    assert resultat['nb_manquants'] == 1


def test_metadonnee_fournie_par_client_ne_prouve_rien(tmp_path):
    class Client:
        def head_object(self, _cle):
            return {'ContentLength': 5, 'ETag': 'stable',
                    'Metadata': {'seamtech-sha256': hashlib.sha256(b'vraie').hexdigest()}}

        def download_file(self, _cle, destination):
            destination.write_bytes(b'autre')

    resultat = copier_objets_s3({'documents': [{'id': 1, 'object_key': 'piece'}]}, Client(), tmp_path)
    assert resultat['nb_copies'] == 0 and resultat['nb_manquants'] == 1


def test_head_du_client_s3_demande_explicitement_checksum(monkeypatch):
    from seamtech_search.storage import S3StorageClient

    appels = []

    class S3:
        def head_object(self, **kwargs):
            appels.append(kwargs)
            return entete(b'vraie')

    client = S3StorageClient(endpoint_url='http://127.0.0.1:9000', bucket_name='test',
                             access_key_id='test', secret_access_key='test')
    monkeypatch.setattr(client, '_get_client', lambda: S3())
    assert client.head_object_avec_checksum('objet')['ChecksumType'] == 'FULL_OBJECT'
    assert appels == [{'Bucket': 'test', 'Key': 'objet', 'ChecksumMode': 'ENABLED'}]


@pytest.mark.parametrize('checksum', ['non-base64', 'QQ==', 123, None])
def test_checksum_malforme_ne_permet_pas_une_copie(tmp_path, checksum):
    class Client:
        def head_object(self, _cle):
            return {'ContentLength': 5, 'ETag': 'opaque', 'ChecksumType': 'FULL_OBJECT', 'ChecksumSHA256': checksum}

        def download_file(self, _cle, destination):
            destination.write_bytes(b'vraie')

    resultat = copier_objets_s3({'documents': [{'id': 1, 'object_key': 'piece'}]}, Client(), tmp_path)
    assert resultat['nb_copies'] == 0 and resultat['nb_manquants'] == 1


@pytest.mark.s3
@pytest.mark.s3rver
def test_s3rver_sans_checksum_complet_refuse_copie_plutot_que_faux_succes(tmp_path):
    import os
    import uuid
    from urllib.parse import urlsplit

    from seamtech_search.storage import S3StorageClient

    endpoint = os.environ.get('SEAMTECH_TEST_S3RVER_URL')
    if not endpoint:
        pytest.skip('banc S3rver isolé absent')
    url = urlsplit(endpoint)
    if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.password:
        pytest.fail('le banc S3rver doit être isolé sur loopback')
    client = S3StorageClient(endpoint_url=endpoint, bucket_name=f'test-checksum-{uuid.uuid4().hex}',
                             access_key_id='S3RVER', secret_access_key='S3RVER', force_path_style=True)
    client.ensure_bucket_exists()
    cle = client.upload_bytes(b'contenu synthetique', remote_key='piece')
    try:
        assert client.get_object(cle)['Body'].read() == b'contenu synthetique'
        resultat = copier_objets_s3({'documents': [{'id': 1, 'object_key': cle}]}, client, tmp_path)
        assert resultat['nb_copies'] == 0
        assert resultat['nb_manquants'] == 1
        assert not (tmp_path / 'objets' / '1' / 'piece').exists()
    finally:
        client.delete_file(cle)
        client._get_client().delete_bucket(Bucket=client.bucket_name)


@pytest.mark.parametrize('identique', [False, True])
def test_objet_copie_doit_correspondre_a_identite_octets_catalogue(tmp_path, identique):
    attendu = b'preuve-catalogue'
    actuel = attendu if identique else b'objet-different'

    class Client:
        def head_object(self, _cle):
            # Un checksum valide du fournisseur prouve l'objet actuel, PAS
            # qu'il corresponde aux octets référencés dans le dump.
            return entete(actuel)

        def download_file(self, _cle, destination):
            destination.write_bytes(actuel)

    refs = {'documents': [{'id': 1, 'object_key': 'piece',
                           'source_sha256': hashlib.sha256(attendu).hexdigest()}]}
    resultat = copier_objets_s3(refs, Client(), tmp_path)
    assert resultat['nb_copies'] == int(identique)
    assert resultat['nb_manquants'] == int(not identique)
    cible = tmp_path / 'objets' / '1' / 'piece'
    if identique:
        assert cible.read_bytes() == attendu
        assert resultat['copies'][0]['identite_catalogue'] == 'verifiee'
    else:
        assert resultat['manquants'][0]['erreur'] == 'identite_catalogue_differente'
        assert not cible.exists()


def test_empreinte_catalogue_malformee_refuse_objet_avant_lecture(tmp_path):
    from seamtech_search.sauvegarde import SauvegardeError

    class Client:
        def head_object(self, _cle):
            pytest.fail('aucune lecture distante si identité invalide')

    with pytest.raises(SauvegardeError, match='empreinte.*catalogue'):
        copier_objets_s3({'documents': [{'id': 1, 'object_key': 'piece', 'source_sha256': 'non-valide'}]},
                         Client(), tmp_path)
