"""La pièce téléchargée doit correspondre aux octets analysés par l'import."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search import api
from seamtech_search.storage import S3StorageClient
from tests.test_snapshot_import import _import_pdf_stocke


@pytest.mark.parametrize('mode', ['modifie', 'absent', 'lien', 'mutation_apres_ouverture'])
def test_telechargement_source_sert_capture_et_non_version_modifiee(tmp_path: Path, monkeypatch, mode: str):
    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    piece = source / 'piece.pdf'
    original = piece.read_bytes()
    if mode == 'modifie':
        piece.write_bytes(b'MODIFICATION-SYNTHETIQUE')
    elif mode == 'absent':
        piece.unlink()
    elif mode == 'lien':
        externe = tmp_path / 'externe'
        externe.write_bytes(b'EXTERIEUR-SYNTHETIQUE')
        piece.unlink()
        piece.symlink_to(externe)
    else:
        ouvrir = api._ouvrir_artefact_confine
        def modifier_apres_ouverture(*args, **kwargs):
            fd = ouvrir(*args, **kwargs)
            piece.write_bytes(b'MODIFICATION-SYNTHETIQUE')
            return fd
        monkeypatch.setattr(api, '_ouvrir_artefact_confine', modifier_apres_ouverture)
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/' + initial.import_id + '/artifacts/source_pdf')
    assert reponse.status_code == 200 and reponse.content == original
    assert len(magasin.envois) == 3
    index.close()


def test_telechargement_refuse_source_et_objet_corrompus(tmp_path: Path, monkeypatch):
    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    piece = source / 'piece.pdf'
    piece.write_bytes(b'VERSION-LOCALE-MODIFIEE')
    cle = initial.files[0].object_key
    assert cle
    magasin.corrompre(cle, octets=b'VERSION-OBJET-MODIFIEE')
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/' + initial.import_id + '/artifacts/source_pdf')
    assert reponse.status_code in (404, 409, 503)
    assert b'VERSION-LOCALE-MODIFIEE' not in reponse.content
    assert b'VERSION-OBJET-MODIFIEE' not in reponse.content
    index.close()


def test_telechargement_source_modifiee_et_stockage_indisponible_echoue(tmp_path: Path, monkeypatch):
    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    (source / 'piece.pdf').write_bytes(b'VERSION-LOCALE-MODIFIEE')
    def indisponible(*_args, **_kwargs):
        raise OSError('panne-synthetique')
    monkeypatch.setattr(S3StorageClient, 'get_object', indisponible)
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/' + initial.import_id + '/artifacts/source_pdf')
    assert reponse.status_code in (404, 409, 503) and b'VERSION-LOCALE-MODIFIEE' not in reponse.content
    assert len(magasin.envois) == 3
    index.close()


def test_telechargement_capture_objet_conserve_plages_http(tmp_path: Path, monkeypatch):
    source, cfg, index, _magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    piece = source / 'piece.pdf'
    original = piece.read_bytes()
    piece.write_bytes(b'VERSION-LOCALE-MODIFIEE')
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/' + initial.import_id + '/artifacts/source_pdf', headers={'Range': 'bytes=0-9'})
    assert reponse.status_code == 206 and reponse.content == original[:10]
    assert reponse.headers['content-range'] == f'bytes 0-9/{len(original)}'
    index.close()


@pytest.mark.parametrize('artefact,champ', [('report_pdf', 'report_path'), ('report_docx', 'report_docx_path')])
def test_telechargement_rapport_modifie_sert_la_version_verifiee(tmp_path: Path, monkeypatch, artefact, champ):
    _source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    rapport = Path(getattr(initial, champ))
    original = rapport.read_bytes()
    rapport.write_bytes(b'RAPPORT-MODIFIE-SYNTHETIQUE')
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/' + initial.import_id + '/artifacts/' + artefact)
    assert reponse.status_code == 200 and reponse.content == original
    assert len(magasin.envois) == 3
    index.close()


def test_telechargement_fichiers_corrompus_refuse_rapport(tmp_path: Path, monkeypatch):
    _source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    rapport = Path(initial.report_path)
    rapport.write_bytes(b'RAPPORT-LOCAL-MODIFIE')
    cle = next(a['key'] for a in initial.artifacts if a['path'] == str(rapport))
    magasin.corrompre(cle, octets=b'RAPPORT-OBJET-MODIFIE')
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/' + initial.import_id + '/artifacts/report_pdf')
    assert reponse.status_code in (404, 409, 503)
    assert b'RAPPORT-LOCAL-MODIFIE' not in reponse.content
    assert b'RAPPORT-OBJET-MODIFIE' not in reponse.content
    index.close()


def test_telechargement_capture_resiste_mutation_apres_copie(tmp_path: Path, monkeypatch):
    source, cfg, index, _magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    piece = source / 'piece.pdf'
    original = piece.read_bytes()
    copier = api._copie_flux_verifiee
    def mutation_apres_copie(*args, **kwargs):
        fd = copier(*args, **kwargs)
        piece.write_bytes(b'AUTRE-VERSION-SYNTHETIQUE')
        return fd
    monkeypatch.setattr(api, '_copie_flux_verifiee', mutation_apres_copie)
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/' + initial.import_id + '/artifacts/source_pdf')
    assert reponse.status_code == 200 and reponse.content == original
    assert piece.read_bytes() == b'AUTRE-VERSION-SYNTHETIQUE'
    index.close()


@pytest.mark.parametrize('artefact,champ', [('source_pdf', None), ('report_pdf', 'report_path')])
def test_telechargement_verifie_ne_redirige_pas_vers_s3(tmp_path: Path, monkeypatch, artefact, champ):
    source, cfg, index, _magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    chemin = source / 'piece.pdf' if champ is None else Path(getattr(initial, champ))
    original = chemin.read_bytes()
    chemin.write_bytes(b'VERSION-LOCALE-MODIFIEE')
    appels = []
    monkeypatch.setattr(api, '_url_presignee_navigateur', lambda *a: appels.append(a) or 'https://public.invalid/object')
    with TestClient(api.create_app(cfg), follow_redirects=False) as client:
        reponse = client.get('/imports/' + initial.import_id + '/artifacts/' + artefact)
    assert reponse.status_code == 200 and reponse.content == original
    assert not appels and 'location' not in reponse.headers
    index.close()


@pytest.mark.parametrize('identite', ['malformee', 'dupliquee', 'objet_different'])
def test_telechargement_source_refuse_identite_ou_preuve_incoherente(tmp_path: Path, monkeypatch, identite):
    from seamtech_search.import_pipeline import get_import, update_import

    source, cfg, index, _magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    (source / 'piece.pdf').write_bytes(b'VERSION-LOCALE-MODIFIEE')
    payload = get_import(index, initial.import_id)
    assert payload
    if identite == 'malformee':
        payload['files'][0]['source_sha256'] = 'sha-invalide'
    elif identite == 'dupliquee':
        payload['files'].append(dict(payload['files'][0]))
    else:
        payload['artifacts'][0]['sha256'] = '0' * 64
    update_import(index, initial.import_id, payload['status'], payload)
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/' + initial.import_id + '/artifacts/source_pdf')
    assert reponse.status_code in (404, 409)
    assert b'VERSION-LOCALE-MODIFIEE' not in reponse.content
    index.close()


def test_telechargement_refuse_rapport_en_regeneration(tmp_path: Path, monkeypatch):
    from seamtech_search.import_pipeline import get_import, update_import

    _source, cfg, index, _magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    payload = get_import(index, initial.import_id)
    assert payload
    payload['report_generation_pending'] = True
    update_import(index, initial.import_id, payload['status'], payload)
    with TestClient(api.create_app(cfg)) as client:
        assert client.get('/imports/' + initial.import_id + '/artifacts/report_pdf').status_code == 409
        assert client.get('/imports/' + initial.import_id + '/artifacts/report_docx').status_code == 409
        assert client.get('/imports/' + initial.import_id + '/artifacts/source_pdf').status_code == 200
    index.close()


def test_telechargement_limite_et_ferme_corps_s3(tmp_path: Path, monkeypatch):
    from io import BytesIO

    source, cfg, index, _magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    (source / 'piece.pdf').write_bytes(b'VERSION-LOCALE-MODIFIEE')
    cfg = cfg.model_copy(update={'max_file_size_bytes': 100})
    corps = BytesIO(b'x' * 101)
    monkeypatch.setattr(S3StorageClient, 'get_object', lambda *_args, **_kwargs: {'Body': corps})
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/' + initial.import_id + '/artifacts/source_pdf')
    assert reponse.status_code == 404 and corps.closed
    assert b'VERSION-LOCALE-MODIFIEE' not in reponse.content
    index.close()


@pytest.mark.parametrize('plage,statut', [(None, 200), ('bytes=0-9', 206), ('bytes=999999-', 416)])
def test_telechargement_ferme_descripteur_meme_pour_plage_invalide(tmp_path: Path, monkeypatch, plage, statut):
    import os

    _source, cfg, index, _magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    copier = api._copie_flux_verifiee
    descripteurs = []
    def observer(*args, **kwargs):
        fd = copier(*args, **kwargs)
        descripteurs.append(fd)
        return fd
    monkeypatch.setattr(api, '_copie_flux_verifiee', observer)
    with TestClient(api.create_app(cfg)) as client:
        reponse = client.get('/imports/' + initial.import_id + '/artifacts/source_pdf',
                             headers={'Range': plage} if plage else {})
    assert reponse.status_code == statut and len(descripteurs) == 1
    with pytest.raises(OSError):
        os.fstat(descripteurs[0])
    index.close()
