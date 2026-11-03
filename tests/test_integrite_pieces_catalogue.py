"""Les pièces de catalogue ne doivent jamais changer d'identité à l'accès."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search import api
from tests.test_fichiers_par_id import TOKEN, _client_avec_fichiers
from tests.test_liaison_catalogue_capture import _import_avant_envoi
from tests.test_snapshot_import import _import_pdf_stocke


def _id(index, piece):
    with index.connect() as connexion:
        return connexion.execute('SELECT id FROM documents WHERE path_key = ?', (str(piece),)).fetchone()[0]


@pytest.mark.parametrize('route,methode,plage', [
    ('apercu', 'get', None), ('telecharger', 'get', None), ('apercu', 'get', 'bytes=0-9'), ('apercu', 'head', None),
])
def test_piece_refuse_objet_corrompu_et_recupere_source_identique(tmp_path: Path, monkeypatch, route, methode, plage):
    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    piece = source / 'piece.pdf'
    original = piece.read_bytes()
    piece_id = _id(index, piece)
    cle = initial.files[0].object_key
    assert cle
    magasin.corrompre(cle, octets=b'OBJET-CORROMPU-SYNTHETIQUE')
    with TestClient(api.create_app(cfg)) as client:
        reponse = getattr(client, methode)(f'/pieces/{piece_id}/{route}', headers={'Range': plage} if plage else {})
    assert reponse.status_code == (206 if plage else 200)
    assert reponse.content == (b'' if methode == 'head' else original[:10] if plage else original)
    assert reponse.headers['content-length'] == str(10 if plage else len(original))
    index.close()


@pytest.mark.parametrize('methode', ['get', 'head'])
def test_piece_refuse_si_les_deux_copies_different(tmp_path: Path, monkeypatch, methode):
    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    piece = source / 'piece.pdf'
    piece_id = _id(index, piece)
    piece.write_bytes(b'SOURCE-MODIFIEE')
    magasin.corrompre(initial.files[0].object_key, octets=b'OBJET-CORROMPU')
    with TestClient(api.create_app(cfg)) as client:
        reponse = getattr(client, methode)(f'/pieces/{piece_id}/apercu')
    assert reponse.status_code in (404, 409, 502, 503)
    assert b'SOURCE-MODIFIEE' not in reponse.content and b'OBJET-CORROMPU' not in reponse.content
    index.close()


def test_piece_locale_capturee_modifiee_ne_sert_pas_une_autre_version(tmp_path: Path, monkeypatch):
    _source, piece, cfg, index, _magasin, _initial = _import_avant_envoi(tmp_path, monkeypatch)
    piece_id = _id(index, piece)
    piece.write_bytes(b'LOCAL-MODIFIE')
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get(f'/pieces/{piece_id}/apercu')
    assert reponse.status_code in (404, 409, 502, 503) and b'LOCAL-MODIFIE' not in reponse.content
    index.close()


def test_piece_heritee_refuse_substitution_en_lien_apres_autorisation(tmp_path: Path, monkeypatch):
    from seamtech_search.fiches import routes

    piece = tmp_path / 'archive' / 'dossier' / 'piece.pdf'
    externe = tmp_path / 'externe.pdf'
    externe.write_bytes(b'CONTENU-EXTERIEUR-SYNTHETIQUE')
    client, ids = _client_avec_fichiers(tmp_path, [piece])
    autoriser = routes._chemin_lexical_autorise
    substitutions = []
    def substituer(path, config):
        resultat = autoriser(path, config)
        piece.unlink()
        piece.symlink_to(externe)
        substitutions.append(True)
        return resultat
    monkeypatch.setattr(routes, '_chemin_lexical_autorise', substituer)
    reponse = client.get(f'/pieces/{ids[piece.name]}/apercu', headers={'X-SEAMTECH-TOKEN': TOKEN})
    assert substitutions == [True]
    assert reponse.status_code in (403, 404, 409) and b'CONTENU-EXTERIEUR-SYNTHETIQUE' not in reponse.content


def test_open_capture_refuse_copie_locale_modifiee(tmp_path: Path, monkeypatch):
    source, cfg, index, _magasin, _initial = _import_pdf_stocke(tmp_path, monkeypatch)
    piece = source / 'piece.pdf'
    original = piece.read_bytes()
    piece.write_bytes(b'LOCAL-MODIFIE')
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.post('/open', params={'path': str(piece)})
    assert reponse.status_code == 200 and reponse.content == original
    index.close()


@pytest.mark.parametrize('capture', [True, False])
def test_head_ignore_plage_et_retourne_taille_complete(tmp_path: Path, monkeypatch, capture):
    if capture:
        source, cfg, index, _magasin, _initial = _import_pdf_stocke(tmp_path, monkeypatch)
        piece = source / 'piece.pdf'
        piece_id = _id(index, piece)
        client = TestClient(api.create_app(cfg))
        headers = {'Range': 'bytes=0-4'}
    else:
        piece = tmp_path / 'archive' / 'dossier' / 'piece.pdf'
        client, ids = _client_avec_fichiers(tmp_path, [piece])
        piece_id = ids[piece.name]
        headers = {'Range': 'bytes=0-4', 'X-SEAMTECH-TOKEN': TOKEN}
    reponse = client.head(f'/pieces/{piece_id}/apercu', headers=headers)
    assert reponse.status_code == 200 and reponse.content == b''
    assert reponse.headers['content-length'] == str(piece.stat().st_size)
    if capture:
        index.close()


def test_piece_s3_heritee_ferme_le_corps_apres_stream(tmp_path: Path, monkeypatch):
    from seamtech_search.storage import S3StorageClient
    from tests.s3_en_memoire import S3EnMemoire

    piece = tmp_path / 'archive' / 'dossier' / 'piece.pdf'
    magasin = S3EnMemoire()
    magasin._ecrire('objet-synthetique', b'%PDF-contenu-synthetique', {})
    get = magasin.get_object
    corps = []
    def capturer(*args, **kwargs):
        reponse = get(*args, **kwargs)
        corps.append(reponse['Body'])
        return reponse
    monkeypatch.setattr(magasin, 'get_object', capturer)
    monkeypatch.setattr(S3StorageClient, '_get_client', lambda _self: magasin)
    client, ids = _client_avec_fichiers(tmp_path, [piece], object_keys={piece.name: 'objet-synthetique'})
    reponse = client.get(f'/pieces/{ids[piece.name]}/apercu', headers={'X-SEAMTECH-TOKEN': TOKEN})
    assert reponse.status_code == 200 and reponse.content == b'%PDF-contenu-synthetique'
    assert len(corps) == 1 and corps[0].closed


def test_flux_s3_herite_ferme_si_client_interrompt_avant_premier_bloc():
    import asyncio
    from io import BytesIO

    corps = BytesIO(b'contenu-synthetique')
    class Stockage:
        def head_object(self, _cle): return {'ContentLength': len(corps.getvalue())}
        def get_object(self, _cle): return {'Body': corps}
    reponse = api._reponse_objet_s3('objet', 'piece.pdf', 'application/pdf', Stockage())
    async def recevoir(): return {'type': 'http.disconnect'}
    async def envoyer(_message): raise OSError('client interrompu')
    async def executer():
        with pytest.raises(Exception):
            await reponse({'type': 'http', 'method': 'GET', 'headers': [], 'asgi': {'spec_version': '2.4'}},
                          recevoir, envoyer)
    asyncio.run(executer())
    assert corps.closed


def test_fd_epingle_ferme_si_client_interrompt(tmp_path: Path):
    import asyncio
    import os

    from seamtech_search.fichiers_securises import _ReponseFichierEpingle

    piece = tmp_path / 'piece.pdf'
    piece.write_bytes(b'contenu-synthetique')
    fd = os.open(piece, os.O_RDONLY)
    reponse = _ReponseFichierEpingle(fd, filename='piece.pdf')
    async def recevoir(): return {'type': 'http.disconnect'}
    async def envoyer(_message): raise OSError('client interrompu')
    async def executer():
        with pytest.raises(Exception):
            await reponse({'type': 'http', 'method': 'GET', 'headers': [], 'extensions': {}}, recevoir, envoyer)
    asyncio.run(executer())
    with pytest.raises(OSError):
        os.fstat(fd)


def test_flux_s3_herite_ferme_si_audit_echoue_avant_reponse(tmp_path: Path, monkeypatch):
    from io import BytesIO

    from seamtech_search.storage import S3StorageClient
    from tests.test_url_presignee_302 import _config, _import_en_base

    cfg = _config(tmp_path, 'audit.db', endpoint_public=None)
    _import_en_base(cfg, tmp_path, avec_cle=True, rapport_existe=False)
    corps = BytesIO(b'rapport-synthetique')
    monkeypatch.setattr(S3StorageClient, 'head_object', lambda *_args, **_kwargs: {'ContentLength': 20})
    monkeypatch.setattr(S3StorageClient, 'get_object', lambda *_args, **_kwargs: {'Body': corps})
    audit = api.record_audit_event
    def audit_en_panne(*args, **kwargs):
        if kwargs.get('action') == 'artifact_download' and kwargs.get('status') == '200':
            raise RuntimeError('panne-synthetique')
        return audit(*args, **kwargs)
    monkeypatch.setattr(api, 'record_audit_event', audit_en_panne)
    with TestClient(api.create_app(cfg)) as navigateur:
        reponse = navigateur.get('/imports/IMP-1/artifacts/report_pdf')
    assert reponse.status_code != 200 and corps.closed


def test_open_ne_deduit_pas_identite_du_chemin_symlink_interne(tmp_path: Path, monkeypatch):
    source, cfg, index, _magasin, _initial = _import_pdf_stocke(tmp_path, monkeypatch)
    piece = source / 'piece.pdf'
    original = piece.read_bytes()
    autre = source / 'autre.pdf'
    autre.write_bytes(b'AUTRE-VERSION-SYNTHETIQUE')
    piece.unlink()
    piece.symlink_to(autre)
    with TestClient(api.create_app(cfg)) as navigateur:
        reponse = navigateur.post('/open', params={'path': str(piece)})
    assert reponse.status_code in (200, 404, 409)
    assert b'AUTRE-VERSION-SYNTHETIQUE' not in reponse.content
    if reponse.status_code == 200:
        assert reponse.content == original
    index.close()
