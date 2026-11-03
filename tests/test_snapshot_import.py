"""Copies privées bornées pour l'import initial (données synthétiques)."""
from pathlib import Path

import pytest

from seamtech_search import import_pipeline as pipeline
from seamtech_search.config import AppConfig


def _config(tmp_path: Path, source: Path) -> AppConfig:
    return AppConfig(root_paths=[source], database_path=tmp_path / 'db.sqlite', min_free_bytes=0,
                     max_file_size_bytes=100)


def test_capture_conserve_octets_et_identites_logiques(tmp_path: Path):
    source = tmp_path / 'source'
    (source / 'sous').mkdir(parents=True)
    fichier = source / 'sous' / 'piece.pdf'
    fichier.write_bytes(b'original-synthetique')
    with pipeline._capturer_fichiers_import(source, _config(tmp_path, source)) as capture:
        original = capture[fichier]
        fichier.write_bytes(b'modification-synthetique')
        assert original.copie.read_bytes() == b'original-synthetique'
        assert original.taille == len(b'original-synthetique')
        copie = original.copie
    assert not copie.exists()


def test_capture_refuse_lien_substitue_apres_inventaire(tmp_path: Path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.pdf'
    fichier.write_bytes(b'original')
    externe = tmp_path / 'externe.pdf'
    externe.write_bytes(b'externe')
    inventorier = pipeline._inventaire_scan_confine
    def substituer(fd, root):
        liste = inventorier(fd, root)
        fichier.unlink()
        fichier.symlink_to(externe)
        return liste
    monkeypatch.setattr(pipeline, '_inventaire_scan_confine', substituer)
    with pytest.raises((OSError, ValueError)):
        with pipeline._capturer_fichiers_import(source, _config(tmp_path, source)):
            pass


def test_capture_refuse_fichier_trop_grand_ou_lien_physique(tmp_path: Path):
    import os

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'x' * 101)
    with pytest.raises(ValueError):
        with pipeline._capturer_fichiers_import(source, _config(tmp_path, source)):
            pass
    fichier.unlink()
    externe = tmp_path / 'externe.txt'
    externe.write_bytes(b'original')
    os.link(externe, fichier)
    with pytest.raises(ValueError):
        with pipeline._capturer_fichiers_import(source, _config(tmp_path, source)):
            pass


def _config_stockage(tmp_path: Path, source: Path) -> AppConfig:
    return _config(tmp_path, source).model_copy(update={
        's3_endpoint_url': 'https://s3.invalide', 's3_bucket': 'bucket-synthetique',
        's3_access_key': 'cle-fictive', 's3_secret_key': 'secret-fictif',
    })


def _brancher_stockage(monkeypatch):
    from seamtech_search.storage import S3StorageClient
    from tests.s3_en_memoire import S3EnMemoire

    magasin = S3EnMemoire()
    monkeypatch.setattr(S3StorageClient, '_get_client', lambda self: magasin)
    monkeypatch.setattr(S3StorageClient, 'ensure_bucket_exists', lambda self: True)
    monkeypatch.setattr(S3StorageClient, 'first_free_key', lambda self, cle: cle)
    return magasin


def test_import_extrait_envoie_et_indexe_la_meme_copie_malgre_substitution(tmp_path: Path, monkeypatch):
    import hashlib

    from seamtech_search.indexer import SearchIndex

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.pdf'
    original = b'FICHE DE FABRICATION\nReference: SYNTHESE\nMaterial: TEST\nQuantity: 1'
    fichier.write_bytes(original)
    externe = tmp_path / 'externe.pdf'
    externe.write_bytes(b'exterieur synthetique')
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    magasin = _brancher_stockage(monkeypatch)
    copies_lues = []
    def extraire(chemin, _cfg):
        copies_lues.append(chemin)
        return pipeline.ExtractedData(raw_text=chemin.read_text(), extraction_status='partial')
    def substituer(phase, _pourcentage):
        if phase == 'uploading':
            fichier.unlink()
            fichier.symlink_to(externe)
    monkeypatch.setattr(pipeline, 'extract_structured_pdf', extraire)
    result = pipeline.import_folder(source, cfg, index, import_id='capture-01', progress_callback=substituer)
    assert len(copies_lues) == 1 and copies_lues[0] != fichier and not copies_lues[0].exists()
    entree = next(f for f in result.files if f.path == str(fichier))
    assert entree.source_sha256 == hashlib.sha256(original).hexdigest()
    assert entree.object_key and magasin.objets[entree.object_key]['donnees'] == original
    assert result.transport_verifie and not result.all_verified
    with index.connect() as connexion:
        document = connexion.execute('SELECT path, path_key, object_key FROM documents WHERE path = ?', (str(fichier),)).fetchone()
        texte = connexion.execute('SELECT content FROM documents_fts WHERE path = ?', (str(fichier),)).fetchone()
    assert document['path_key'] == str(fichier) and document['object_key'] == entree.object_key
    assert 'SYNTHESE' in texte['content'] and b'exterieur synthetique' != magasin.objets[entree.object_key]['donnees']
    index.close()


def test_import_ne_publie_pas_un_objet_devenu_different_de_la_capture(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.storage import S3StorageClient

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'original-synthetique')
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    magasin = _brancher_stockage(monkeypatch)
    envoyer = S3StorageClient.upload_file
    def modifier_avant_envoi(self, chemin, *args, **kwargs):
        Path(chemin).chmod(0o600)
        Path(chemin).write_bytes(b'autres-octets')
        return envoyer(self, chemin, *args, **kwargs)
    monkeypatch.setattr(S3StorageClient, 'upload_file', modifier_avant_envoi)
    result = pipeline.import_folder(source, cfg, index, import_id='capture-02')
    assert magasin.envois  # la course a effectivement eu lieu après le dernier contrôle
    assert not result.transport_verifie and not result.all_verified
    assert result.files[0].object_key is None
    assert result.artifacts[0]['status'] == 'failed' and not result.artifacts[0]['verified']
    with index.connect() as connexion:
        assert connexion.execute('SELECT object_key FROM documents').fetchone()['object_key'] is None
    assert fichier.read_bytes() == b'original-synthetique'
    index.close()


def test_annulation_pendant_copie_ne_lit_pas_le_reste_et_ne_touche_pas_aux_originaux(tmp_path: Path):
    from seamtech_search.jobs import ImportCancelledError

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'volume.txt'
    original = b'x' * (2 * 1024 * 1024 + 1)
    fichier.write_bytes(original)
    cfg = _config(tmp_path, source).model_copy(update={'max_file_size_bytes': len(original) + 1})
    appels = 0
    def annuler():
        nonlocal appels
        appels += 1
        return appels >= 3
    with pytest.raises(ImportCancelledError):
        with pipeline._capturer_fichiers_import(source, cfg, cancel_check=annuler):
            pytest.fail('capture interrompue ne doit pas être livrée')
    assert fichier.read_bytes() == original
    assert appels >= 3


def test_import_refuse_copie_alteree_avant_tout_envoi(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.pdf'
    original = b'FICHE DE FABRICATION\nReference: SYNTHESE\nMaterial: TEST'
    fichier.write_bytes(original)
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    magasin = _brancher_stockage(monkeypatch)
    copies = []
    def extraire(chemin, _cfg):
        copies.append(chemin)
        return pipeline.ExtractedData(raw_text=chemin.read_text(), extraction_status='partial')
    def modifier(phase, _pourcentage):
        if phase == 'uploading':
            copies[0].chmod(0o600)
            copies[0].write_bytes(b'alteration-synthetique')
    monkeypatch.setattr(pipeline, 'extract_structured_pdf', extraire)
    with pytest.raises(ValueError, match='capture'):
        pipeline.import_folder(source, cfg, index, import_id='capture-03', progress_callback=modifier)
    assert not magasin.envois and fichier.read_bytes() == original
    assert copies and not copies[0].exists()
    index.close()


def test_capture_accepte_nom_avec_extension_tres_longue(tmp_path: Path):
    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / ('a.' + 'x' * 246)
    fichier.write_bytes(b'original')
    with pipeline._capturer_fichiers_import(source, _config(tmp_path, source)) as capture:
        assert capture[fichier].copie.read_bytes() == b'original'


def test_capture_reserve_espace_pour_fichier_et_marge(tmp_path: Path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_bytes(b'12345')
    requis = []
    monkeypatch.setattr(pipeline, 'ensure_free_space', lambda _path, taille: requis.append(taille))
    cfg = _config(tmp_path, source).model_copy(update={'min_free_bytes': 100})
    with pipeline._capturer_fichiers_import(source, cfg):
        pass
    assert requis == [105]


def test_reimport_refuse_etat_objet_ambigu_meme_sans_preuve(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.storage import UploadBatch, UploadedArtifact

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_bytes(b'original')
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    appels = []
    def envoyer(**kwargs):
        appels.append(True)
        chemin = kwargs['files_to_upload'][0]
        # Réponse perdue : l'objet peut exister même si son intégrité n'est pas prouvée.
        return UploadBatch('failed', [UploadedArtifact(str(chemin), chemin.name, key='cle-ambigue',
                                                       bucket='bucket-synthetique', status='failed')])
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', envoyer)
    initial = pipeline.import_folder(source, cfg, index, import_id='ambigu')
    assert not initial.all_verified and initial.files[0].object_key is None
    assert initial.artifacts[0]['key'] == 'cle-ambigue'
    with pytest.raises(RuntimeError, match='reprise'):
        pipeline.import_folder(source, cfg, index, import_id='ambigu')
    assert appels == [True] and (source / 'a.txt').read_bytes() == b'original'
    index.close()


def _import_avec_panne_partielle(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.storage import S3StorageClient, StoragePreflightError

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_bytes(b'original-a-synthetique')
    (source / 'b.txt').write_bytes(b'original-b-synthetique')
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    magasin = _brancher_stockage(monkeypatch)
    envoyer = S3StorageClient.upload_file
    def panne_sur_b(self, chemin, *args, **kwargs):
        if kwargs.get('relative_path') == 'b.txt':
            raise StoragePreflightError('panne avant écriture synthétique')
        return envoyer(self, chemin, *args, **kwargs)
    monkeypatch.setattr(S3StorageClient, 'upload_file', panne_sur_b)
    initial = pipeline.import_folder(source, cfg, index, import_id='reprise-capture')
    assert len(magasin.envois) == 1 and not initial.transport_verifie
    assert all(f.source_sha256 for f in initial.files)
    monkeypatch.setattr(S3StorageClient, 'upload_file', envoyer)
    return source, cfg, index, magasin


def test_reprise_n_envoie_pas_source_modifiee_depuis_capture_initiale(tmp_path: Path, monkeypatch):
    source, cfg, index, magasin = _import_avec_panne_partielle(tmp_path, monkeypatch)
    fichier = source / 'b.txt'
    fichier.write_bytes(b'autre-version-synthetique')
    avant = len(magasin.envois)
    repris = pipeline.retry_upload(index, cfg, 'reprise-capture')
    assert len(magasin.envois) == avant
    assert not repris['all_verified'] and not repris['preservation']['transport_verifie']
    assert str(fichier) in repris['inventaire']['sources_modifiees']
    assert next(f for f in repris['files'] if f['path'] == str(fichier))['object_key'] is None
    assert fichier.read_bytes() == b'autre-version-synthetique'
    index.close()


def test_reprise_transporte_source_inchangee_mais_ne_leve_pas_la_suspension(tmp_path: Path, monkeypatch):
    import hashlib

    source, cfg, index, magasin = _import_avec_panne_partielle(tmp_path, monkeypatch)
    repris = pipeline.retry_upload(index, cfg, 'reprise-capture')
    fichier = next(f for f in repris['files'] if f['name'] == 'b.txt')
    assert len(magasin.envois) == 2
    assert fichier['source_sha256'] == hashlib.sha256((source / 'b.txt').read_bytes()).hexdigest()
    assert magasin.objets[fichier['object_key']]['donnees'] == (source / 'b.txt').read_bytes()
    assert repris['preservation']['transport_verifie'] and not repris['all_verified']
    assert repris['inventaire']['sources_modifiees'] == []
    index.close()


def test_reprise_rejette_objet_mutant_apres_la_copie(tmp_path: Path, monkeypatch):
    from seamtech_search.storage import S3StorageClient

    source, cfg, index, magasin = _import_avec_panne_partielle(tmp_path, monkeypatch)
    envoyer = S3StorageClient.upload_file
    def modifier_copie(self, chemin, *args, **kwargs):
        Path(chemin).write_bytes(b'copie-modifiee')
        return envoyer(self, chemin, *args, **kwargs)
    monkeypatch.setattr(S3StorageClient, 'upload_file', modifier_copie)
    repris = pipeline.retry_upload(index, cfg, 'reprise-capture')
    assert len(magasin.envois) == 2  # le scénario d'interstice a réellement eu lieu
    assert not repris['all_verified'] and not repris['preservation']['transport_verifie']
    assert next(f for f in repris['files'] if f['name'] == 'b.txt')['object_key'] is None
    assert (source / 'b.txt').read_bytes() == b'original-b-synthetique'
    index.close()


def test_reprise_signale_stockage_configure_meme_si_toutes_sources_modifiees(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'original')
    cfg = _config(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    initial = pipeline.import_folder(source, cfg, index, import_id='nouvelle-destination')
    assert initial.upload_status == 'not_configured'
    fichier.write_bytes(b'nouvelle-version')
    magasin = _brancher_stockage(monkeypatch)
    repris = pipeline.retry_upload(index, _config_stockage(tmp_path, source), 'nouvelle-destination')
    assert not magasin.envois
    assert repris['preservation']['destination_configuree']
    assert repris['upload_status'] == 'failed'
    assert repris['inventaire']['sources_modifiees'] == [str(fichier)]
    index.close()


def test_reprise_refuse_sha_initial_malforme_avant_tout_envoi(tmp_path: Path, monkeypatch):
    source, cfg, index, magasin = _import_avec_panne_partielle(tmp_path, monkeypatch)
    payload = pipeline.get_import(index, 'reprise-capture')
    payload['files'][0]['source_sha256'] = 'empreinte-invalide'
    pipeline.update_import(index, 'reprise-capture', payload['status'], payload)
    avant = len(magasin.envois)
    with pytest.raises(ValueError, match='Empreinte de capture invalide'):
        pipeline.retry_upload(index, cfg, 'reprise-capture')
    assert len(magasin.envois) == avant and (source / 'b.txt').exists()
    index.close()


@pytest.mark.parametrize('phase', ['avant_persistance_import', 'apres_indexation'])
def test_publication_synchrone_annule_catalogue_et_import_ensemble(tmp_path: Path, monkeypatch, phase: str):
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.models import Document

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'original-synthetique')
    cfg = _config(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    marqueur = tmp_path / 'existant.txt'
    index.upsert_document(Document(marqueur, marqueur.name, tmp_path, '.txt', 1, 1.0, False, 'marqueur'))
    if phase == 'avant_persistance_import':
        monkeypatch.setattr(pipeline, '_save_import', lambda *_args: (_ for _ in ()).throw(RuntimeError('panne-synthetique')))
    else:
        indexer = index.upsert_documents
        def panne_apres_indexation(*args, **kwargs):
            indexer(*args, **kwargs)
            raise RuntimeError('panne-synthetique')
        monkeypatch.setattr(index, 'upsert_documents', panne_apres_indexation)
    with pytest.raises(RuntimeError, match='panne-synthetique'):
        pipeline.import_folder(source, cfg, index, import_id='rollback-publication')
    with index.connect() as connexion:
        chemins = [row['path'] for row in connexion.execute('SELECT path FROM documents').fetchall()]
        imports = connexion.execute('SELECT count(*) FROM imports').fetchone()[0]
    assert chemins == [str(marqueur)] and imports == 0
    assert fichier.read_bytes() == b'original-synthetique'
    index.close()


@pytest.mark.parametrize('panne', ['pendant_envoi', 'avant_publication'])
def test_echec_apres_effet_externe_garde_intention_et_interdit_renvoi_aveugle(tmp_path: Path, monkeypatch, panne: str):
    import hashlib

    from seamtech_search.indexer import SearchIndex

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'original-synthetique')
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    magasin = _brancher_stockage(monkeypatch)
    if panne == 'pendant_envoi':
        envoyer = pipeline.upload_artifacts_to_storage
        def reponse_perdue(**kwargs):
            envoyer(**kwargs)
            raise RuntimeError('panne-synthetique')
        monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', reponse_perdue)
    else:
        monkeypatch.setattr(index, 'upsert_documents', lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError('panne-synthetique')))
    with pytest.raises(RuntimeError, match='panne-synthetique'):
        pipeline.import_folder(source, cfg, index, import_id='intention')
    assert len(magasin.envois) == 1
    attendu = pipeline.get_import(index, 'intention')
    assert attendu and attendu['publication_pending'] and not attendu['all_verified']
    assert attendu['files'][0]['source_sha256'] == hashlib.sha256(fichier.read_bytes()).hexdigest()
    from seamtech_search.storage import artifact_object_key
    planifie = attendu['artifacts'][0]
    assert planifie['expected_sha256'] == attendu['files'][0]['source_sha256']
    assert planifie['expected_key_base'] == artifact_object_key('intention', fichier, source_root=source, lexical=True)
    assert planifie['expected_bucket'] == 'bucket-synthetique'
    with index.connect() as connexion:
        assert connexion.execute('SELECT count(*) FROM documents').fetchone()[0] == 0
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.import_folder(source, cfg, index, import_id='intention')
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.retry_upload(index, cfg, 'intention')
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.correct_import(index, cfg, 'intention', {'reference': 'AUTRE'})
    assert len(magasin.envois) == 1 and fichier.exists()
    index.close()


def _intention_interrompue(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'piece.txt').write_bytes(b'original-synthetique')
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    magasin = _brancher_stockage(monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise RuntimeError('panne-synthetique')
    with monkeypatch.context() as contexte:
        contexte.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
        with pytest.raises(RuntimeError, match='panne-synthetique'):
            pipeline.import_folder(source, cfg, index, import_id='intention')
    return source, cfg, index, magasin


def test_api_intention_interrompue_signale_revue_et_refuse_mutations(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    source, cfg, index, magasin = _intention_interrompue(tmp_path, monkeypatch)
    with TestClient(create_app(cfg)) as client:
        lecture = client.get('/imports/intention')
        assert lecture.status_code == 200 and lecture.json()['publication_pending']
        assert lecture.json()['status'] == 'needs_review' and not lecture.json()['all_verified']
        reprise = client.post('/imports/intention/retry-upload')
        correction = client.patch('/imports/intention', json={'reference': 'SYNTHESE'})
        assert reprise.status_code == correction.status_code == 409
        assert 'réconciliation' in reprise.text and 'réconciliation' in correction.text
    assert len(magasin.envois) == 1 and (source / 'piece.txt').exists()
    index.close()


def test_worker_place_intention_interrompue_en_revue_sans_renvoi(tmp_path: Path, monkeypatch):
    from seamtech_search.jobs import create_job, get_job
    from seamtech_search.worker import process_import_task

    source, cfg, index, magasin = _intention_interrompue(tmp_path, monkeypatch)
    create_job(index, 'intention', str(source))
    charge = {'job_id': 'intention', 'source_path': str(source)}
    resultat = process_import_task(charge, cfg, index)
    assert resultat['status'] == 'needs_review' and resultat['publication_pending']
    assert get_job(index, 'intention')['status'] == 'needs_review'
    doublon = process_import_task(charge, cfg, index)
    assert doublon['status'] == 'needs_review' and doublon['duplicate_delivery']
    assert len(magasin.envois) == 1 and (source / 'piece.txt').exists()
    index.close()


def test_api_sync_expose_intention_si_panne_apres_ecriture_objet(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'piece.txt').write_bytes(b'original-synthetique')
    cfg = _config_stockage(tmp_path, source)
    magasin = _brancher_stockage(monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise RuntimeError('SECRET-SYNTHETIQUE-PANNE')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
    with TestClient(create_app(cfg)) as client:
        reponse = client.post('/imports?wait=true', json={'source_path': str(source)})
        assert reponse.status_code == 200
        resultat = reponse.json()
        assert resultat['status'] == 'needs_review' and resultat['publication_pending']
        assert resultat['import_id'] and not resultat['all_verified']
        assert 'SECRET-SYNTHETIQUE-PANNE' not in reponse.text
        assert client.get('/imports/' + resultat['import_id']).json()['publication_pending']
        assert client.post('/imports/' + resultat['import_id'] + '/retry-upload').status_code == 409
    assert len(magasin.envois) == 1 and (source / 'piece.txt').exists()


def test_worker_panne_pendant_envoi_garde_intention_en_revue(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.jobs import create_job, get_job
    from seamtech_search.worker import process_import_task

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'piece.txt').write_bytes(b'original-synthetique')
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    create_job(index, 'intention', str(source))
    magasin = _brancher_stockage(monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise RuntimeError('SECRET-SYNTHETIQUE-PANNE')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
    charge = {'job_id': 'intention', 'source_path': str(source)}
    resultat = process_import_task(charge, cfg, index)
    assert resultat['status'] == 'needs_review' and resultat['publication_pending']
    assert get_job(index, 'intention')['status'] == 'needs_review'
    assert 'SECRET-SYNTHETIQUE-PANNE' not in str(resultat)
    assert process_import_task(charge, cfg, index)['duplicate_delivery']
    assert len(magasin.envois) == 1 and (source / 'piece.txt').exists()
    index.close()


def test_api_async_panne_apres_envoi_conserve_revue_et_expurge_erreur(tmp_path: Path, monkeypatch, caplog):
    import time

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.jobs import get_job

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'piece.txt').write_bytes(b'original-synthetique')
    cfg = _config_stockage(tmp_path, source)
    magasin = _brancher_stockage(monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise RuntimeError('SECRET-SYNTHETIQUE-PANNE')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
    with caplog.at_level('ERROR'), TestClient(create_app(cfg)) as client:
        reponse = client.post('/imports', json={'source_path': str(source)})
        assert reponse.status_code == 202
        job_id = reponse.json()['job_id']
        probe = SearchIndex(cfg.database_path)
        for _ in range(100):
            job = get_job(probe, job_id)
            if job and job['status'] not in {'pending', 'running'}:
                break
            time.sleep(0.05)
        else:
            pytest.fail('le traitement asynchrone ne se termine pas')
        assert job['status'] == 'needs_review' and job['result']['publication_pending']
        assert 'SECRET-SYNTHETIQUE-PANNE' not in str(job) and 'SECRET-SYNTHETIQUE-PANNE' not in caplog.text
        assert client.get('/imports/' + job_id).json()['publication_pending']
        probe.close()
    assert len(magasin.envois) == 1 and (source / 'piece.txt').exists()


def test_worker_annule_apres_envoi_signale_intention_incertaine(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.jobs import cancel_job, create_job, get_job
    from seamtech_search.worker import process_import_task

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'piece.txt').write_bytes(b'original-synthetique')
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    create_job(index, 'intention', str(source))
    magasin = _brancher_stockage(monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def annuler_apres_envoi(**kwargs):
        lot = envoyer(**kwargs)
        assert cancel_job(index, 'intention')['status'] == 'cancelled'
        return lot
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', annuler_apres_envoi)
    resultat = process_import_task({'job_id': 'intention', 'source_path': str(source)}, cfg, index)
    assert resultat['status'] == 'cancelled' and resultat['publication_pending']
    assert get_job(index, 'intention')['result']['publication_pending']
    with TestClient(create_app(cfg)) as client:
        lecture = client.get('/imports/intention')
        assert lecture.json()['status'] == 'cancelled' and lecture.json()['publication_pending']
        assert client.post('/imports/intention/retry-upload').status_code == 409
    assert len(magasin.envois) == 1 and (source / 'piece.txt').exists()
    index.close()


def test_api_confirm_sync_expose_intention_apres_ecriture_objet(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    source = tmp_path / 'source'
    source.mkdir()
    pdf = source / 'piece.pdf'
    pdf.write_bytes(b'FICHE DE FABRICATION\nReference: SYNTHESE')
    cfg = _config_stockage(tmp_path, source)
    magasin = _brancher_stockage(monkeypatch)
    monkeypatch.setattr(pipeline, 'extract_structured_pdf', lambda path, _cfg: pipeline.ExtractedData(
        raw_text=path.read_text(), extraction_status='partial'))
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise RuntimeError('SECRET-SYNTHETIQUE-PANNE')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
    with TestClient(create_app(cfg)) as client:
        reponse = client.post('/imports/confirm?wait=true', json={'source_path': str(source), 'technical_pdf': str(pdf)})
        assert reponse.status_code == 200
        resultat = reponse.json()
        assert resultat['status'] == 'needs_review' and resultat['publication_pending']
        assert resultat['technical_pdf'] == str(pdf) and not resultat['all_verified']
        assert 'SECRET-SYNTHETIQUE-PANNE' not in reponse.text
        assert client.get('/imports/' + resultat['import_id']).json()['publication_pending']
    assert len(magasin.envois) == 3 and pdf.exists()


def test_panne_avant_ecriture_ne_cree_pas_de_cle_objet_ambigue(tmp_path: Path, monkeypatch):
    from seamtech_search.storage import S3StorageClient, StorageError, upload_artifacts_to_storage

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'original')
    cfg = _config_stockage(tmp_path, source)
    monkeypatch.setattr(S3StorageClient, '_get_client', lambda self: (_ for _ in ()).throw(StorageError('indisponible')))
    resultat = upload_artifacts_to_storage('source', [fichier], cfg, import_id='preflight', source_root=source)
    assert resultat.status == 'failed' and len(resultat.artifacts) == 1
    assert resultat.artifacts[0].key is None and not resultat.artifacts[0].verified


def test_reponse_perdue_conserve_cle_exacte_si_cle_de_base_occupee(tmp_path: Path, monkeypatch):
    from seamtech_search.storage import S3StorageClient, artifact_object_key, upload_artifacts_to_storage
    from tests.s3_en_memoire import S3EnMemoire

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'original')
    cfg = _config_stockage(tmp_path, source)
    magasin = S3EnMemoire()
    base = artifact_object_key('reponse-perdue', fichier, source_root=source)
    magasin._ecrire(base, b'ancienne-version', {})
    envoyer = magasin.upload_file
    def reponse_perdue(*args, **kwargs):
        envoyer(*args, **kwargs)
        raise OSError('reponse-perdue-synthetique')
    monkeypatch.setattr(magasin, 'upload_file', reponse_perdue)
    monkeypatch.setattr(S3StorageClient, '_get_client', lambda self: magasin)
    monkeypatch.setattr(S3StorageClient, 'ensure_bucket_exists', lambda self: True)
    resultat = upload_artifacts_to_storage('source', [fichier], cfg, import_id='reponse-perdue', source_root=source)
    artefact = resultat.artifacts[0]
    assert artefact.status == 'failed' and not artefact.verified
    assert artefact.key != base and artefact.key in magasin.objets
    assert magasin.objets[artefact.key]['donnees'] == b'original'
    assert magasin.objets[base]['donnees'] == b'ancienne-version'


def test_reimport_apres_panne_avant_ecriture_refuse_source_modifiee(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.storage import S3StorageClient, StorageError

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'original')
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    with monkeypatch.context() as contexte:
        contexte.setattr(S3StorageClient, '_get_client', lambda self: (_ for _ in ()).throw(StorageError('indisponible')))
        initial = pipeline.import_folder(source, cfg, index, import_id='preflight-modifie')
    assert initial.artifacts[0]['key'] is None and not initial.all_verified
    fichier.write_bytes(b'nouvelle-version')
    magasin = _brancher_stockage(monkeypatch)
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.import_folder(source, cfg, index, import_id='preflight-modifie')
    assert not magasin.envois and fichier.read_bytes() == b'nouvelle-version'
    index.close()


def test_erreur_de_verification_apres_envoi_ne_efface_pas_cle_reelle(tmp_path: Path, monkeypatch):
    from seamtech_search.storage import S3StorageClient, StoragePreflightError, upload_artifacts_to_storage

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'original')
    cfg = _config_stockage(tmp_path, source)
    magasin = _brancher_stockage(monkeypatch)
    monkeypatch.setattr(S3StorageClient, 'verifier_integrite', lambda *_a, **_kw: (_ for _ in ()).throw(StoragePreflightError('panne')))
    resultat = upload_artifacts_to_storage('source', [fichier], cfg, import_id='verification', source_root=source)
    assert len(magasin.envois) == 1 and resultat.artifacts[0].key in magasin.objets
    assert not resultat.artifacts[0].verified and resultat.artifacts[0].status == 'failed'


@pytest.mark.parametrize('echec', ['reponse_perdue', 'verification'])
def test_envoi_incertain_retourne_en_revue_et_bloque_renvoi(tmp_path: Path, monkeypatch, echec: str):
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.storage import S3StorageClient

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'original-synthetique')
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    magasin = _brancher_stockage(monkeypatch)
    if echec == 'reponse_perdue':
        envoyer = magasin.upload_file
        def envoyer_puis_perdre(*args, **kwargs):
            envoyer(*args, **kwargs)
            raise OSError('panne-synthetique')
        monkeypatch.setattr(magasin, 'upload_file', envoyer_puis_perdre)
    else:
        monkeypatch.setattr(S3StorageClient, 'verifier_integrite', lambda *_a, **_kw: (_ for _ in ()).throw(OSError('panne-synthetique')))
    resultat = pipeline.import_folder(source, cfg, index, import_id='ecriture-incertaine')
    assert len(magasin.envois) == 1 and resultat.artifacts[0]['key'] in magasin.objets
    assert resultat.publication_pending and resultat.status == 'needs_review' and not resultat.all_verified
    assert resultat.artifacts[0]['expected_sha256'] == resultat.files[0].source_sha256
    assert pipeline.get_import(index, 'ecriture-incertaine')['publication_pending']
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.retry_upload(index, cfg, 'ecriture-incertaine')
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.import_folder(source, cfg, index, import_id='ecriture-incertaine')
    assert len(magasin.envois) == 1 and fichier.exists()
    index.close()


def test_echec_avant_ecriture_permet_reimport_de_source_inchangee(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.storage import S3StorageClient, StorageError

    source = tmp_path / 'source'
    source.mkdir()
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'original-synthetique')
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    with monkeypatch.context() as contexte:
        contexte.setattr(S3StorageClient, '_get_client', lambda self: (_ for _ in ()).throw(StorageError('indisponible')))
        initial = pipeline.import_folder(source, cfg, index, import_id='preflight-identique')
    assert not initial.publication_pending and initial.artifacts[0]['key'] is None
    magasin = _brancher_stockage(monkeypatch)
    repris = pipeline.import_folder(source, cfg, index, import_id='preflight-identique')
    assert repris.transport_verifie and not repris.all_verified and not repris.publication_pending
    assert len(magasin.envois) == 1 and fichier.exists()
    index.close()


def test_reprise_interrompue_apres_ecriture_garde_intention_et_bloque_double_envoi(tmp_path: Path, monkeypatch):
    source, cfg, index, magasin = _import_avec_panne_partielle(tmp_path, monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise RuntimeError('panne-reprise-synthetique')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
    with pytest.raises(RuntimeError, match='panne-reprise-synthetique'):
        pipeline.retry_upload(index, cfg, 'reprise-capture')
    assert len(magasin.envois) == 2
    intention = pipeline.get_import(index, 'reprise-capture')
    assert intention['publication_pending'] and not intention['all_verified']
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.retry_upload(index, cfg, 'reprise-capture')
    assert len(magasin.envois) == 2 and (source / 'b.txt').exists()
    index.close()


def test_worker_reprend_sous_ensemble_sans_renvoyer_objet_deja_prouve(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.jobs import create_job, get_job
    from seamtech_search.storage import S3StorageClient, StoragePreflightError
    from seamtech_search.worker import process_import_task

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_bytes(b'original-a')
    (source / 'b.txt').write_bytes(b'original-b')
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    create_job(index, 'partiel-worker', str(source))
    magasin = _brancher_stockage(monkeypatch)
    envoyer = S3StorageClient.upload_file
    def panne_b(self, chemin, *args, **kwargs):
        if kwargs.get('relative_path') == 'b.txt':
            raise StoragePreflightError('panne avant écriture synthétique')
        return envoyer(self, chemin, *args, **kwargs)
    charge = {'job_id': 'partiel-worker', 'source_path': str(source)}
    with monkeypatch.context() as contexte:
        contexte.setattr(S3StorageClient, 'upload_file', panne_b)
        initial = process_import_task(charge, cfg, index)
    assert initial['status'] == 'upload_incomplete' and len(magasin.envois) == 1
    ancien = magasin.envois[0]['key']
    repris = process_import_task(charge, cfg, index)
    assert repris['status'] == 'needs_review' and not repris['all_verified']
    assert repris['preservation']['transport_verifie'] and len(magasin.envois) == 2
    assert sum(e['key'] == ancien for e in magasin.envois) == 1
    assert get_job(index, 'partiel-worker')['status'] == 'needs_review'
    assert (source / 'a.txt').read_bytes() == b'original-a' and (source / 'b.txt').read_bytes() == b'original-b'
    index.close()


def test_reprise_ne_televerse_pas_rapport_modifie_depuis_capture(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.storage import S3StorageClient, StoragePreflightError

    source = tmp_path / 'source'
    source.mkdir()
    pdf = source / 'piece.pdf'
    pdf.write_bytes(b'FICHE DE FABRICATION\nReference: SYNTHESE\nMaterial: TEST')
    cfg = _config_stockage(tmp_path, source).model_copy(update={'max_file_size_bytes': 2 * 1024 * 1024})
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    magasin = _brancher_stockage(monkeypatch)
    monkeypatch.setattr(pipeline, 'extract_structured_pdf', lambda path, _cfg: pipeline.ExtractedData(
        raw_text=path.read_text(), extraction_status='partial'))
    envoyer = S3StorageClient.upload_file
    def panne_rapport(self, chemin, *args, **kwargs):
        if kwargs.get('relative_path') == 'technical-report.pdf':
            raise StoragePreflightError('panne avant écriture synthétique')
        return envoyer(self, chemin, *args, **kwargs)
    with monkeypatch.context() as contexte:
        contexte.setattr(S3StorageClient, 'upload_file', panne_rapport)
        initial = pipeline.import_folder(source, cfg, index, import_id='rapport-modifie')
    assert not initial.publication_pending and len(magasin.envois) == 2
    rapport = Path(initial.report_path)
    preuve = next(a for a in initial.artifacts if a['path'] == str(rapport))
    assert preuve['key'] is None and preuve['expected_sha256']
    rapport.write_bytes(b'rapport-modifie-synthetique')
    repris = pipeline.retry_upload(index, cfg, 'rapport-modifie')
    assert repris['inventaire']['rapports_modifies'] == [str(rapport)]
    assert not repris['all_verified'] and not repris['preservation']['transport_verifie']
    assert len(magasin.envois) == 2 and rapport.read_bytes() == b'rapport-modifie-synthetique'
    index.close()


def test_api_reprise_interrompue_retourne_revue_avec_intention(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    source, cfg, index, magasin = _import_avec_panne_partielle(tmp_path, monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise OSError('SECRET-SYNTHETIQUE-PANNE')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
    with TestClient(create_app(cfg)) as client:
        reponse = client.post('/imports/reprise-capture/retry-upload')
        assert reponse.status_code == 200
        resultat = reponse.json()
        assert resultat['status'] == 'needs_review' and resultat['publication_pending']
        assert not resultat['all_verified'] and 'SECRET-SYNTHETIQUE-PANNE' not in reponse.text
        assert client.get('/imports/reprise-capture').json()['publication_pending']
        assert client.post('/imports/reprise-capture/retry-upload').status_code == 409
    assert len(magasin.envois) == 2 and (source / 'b.txt').exists()
    index.close()


def _import_pdf_stocke(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex

    source = tmp_path / 'source'
    source.mkdir()
    pdf = source / 'piece.pdf'
    pdf.write_bytes(b'FICHE DE FABRICATION\nReference: SYNTHESE\nMaterial: TEST')
    cfg = _config_stockage(tmp_path, source).model_copy(update={'max_file_size_bytes': 2 * 1024 * 1024})
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    magasin = _brancher_stockage(monkeypatch)
    monkeypatch.setattr(pipeline, 'extract_structured_pdf', lambda path, _cfg: pipeline.ExtractedData(
        raw_text=path.read_text(), reference='SYNTHESE', extraction_status='partial'))
    initial = pipeline.import_folder(source, cfg, index, import_id='correction-intention')
    assert initial.transport_verifie and not initial.publication_pending and len(magasin.envois) == 3
    return source, cfg, index, magasin, initial


def test_correction_interrompue_apres_envoi_conserve_intention(tmp_path: Path, monkeypatch):
    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise RuntimeError('panne-correction-synthetique')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
    with pytest.raises(RuntimeError, match='panne-correction-synthetique'):
        pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    assert len(magasin.envois) == 5
    intention = pipeline.get_import(index, initial.import_id)
    assert intention['publication_pending'] and not intention['all_verified']
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.retry_upload(index, cfg, initial.import_id)
    assert len(magasin.envois) == 5 and (source / 'piece.pdf').exists()
    index.close()


def test_correction_reponse_perdue_conserve_intention_sans_renvoi(tmp_path: Path, monkeypatch):
    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    envoyer = magasin.upload_file
    def reponse_perdue(*args, **kwargs):
        envoyer(*args, **kwargs)
        raise OSError('panne-synthetique')
    monkeypatch.setattr(magasin, 'upload_file', reponse_perdue)
    corrige = pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    assert corrige['publication_pending'] and corrige['status'] == 'needs_review' and not corrige['all_verified']
    assert len(magasin.envois) == 5
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    assert len(magasin.envois) == 5 and (source / 'piece.pdf').exists()
    index.close()


def test_correction_reussie_efface_intention_et_garde_preuve_complete(tmp_path: Path, monkeypatch):
    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    corrige = pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    assert not corrige['publication_pending'] and not corrige['report_generation_pending']
    assert corrige['transport_verifie'] and corrige['preservation']['transport_verifie']
    assert not corrige['all_verified'] and not corrige['cleanup']['purge']
    assert corrige['status'] == 'needs_review' and len(magasin.envois) == 5
    reprise = pipeline.retry_upload(index, cfg, initial.import_id)
    assert reprise['preservation']['transport_verifie'] and len(magasin.envois) == 5
    assert (source / 'piece.pdf').exists()
    index.close()


def test_correction_echec_avant_ecriture_autorise_reprise_des_rapports_identiques(tmp_path: Path, monkeypatch):
    from seamtech_search.storage import S3StorageClient, StoragePreflightError

    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    with monkeypatch.context() as contexte:
        def refuser(*_args, **_kwargs):
            raise StoragePreflightError('panne avant envoi synthetique')
        contexte.setattr(S3StorageClient, 'upload_file', refuser)
        corrige = pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    assert not corrige['publication_pending'] and not corrige['report_generation_pending']
    assert not corrige['transport_verifie'] and not corrige['preservation']['transport_verifie']
    rapports = [a for a in corrige['artifacts'] if a['role'] in ('rapport_pdf', 'rapport_docx')]
    assert len(rapports) == 2 and all(a['key'] is None and a['expected_sha256'] for a in rapports)
    assert len(magasin.envois) == 3
    repris = pipeline.retry_upload(index, cfg, initial.import_id)
    assert not repris['publication_pending'] and repris['preservation']['transport_verifie']
    assert len(magasin.envois) == 5 and (source / 'piece.pdf').exists()
    index.close()


def test_correction_resultat_omis_apres_envoi_conserve_intention(tmp_path: Path, monkeypatch):
    from seamtech_search.storage import UploadBatch

    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def omettre(**kwargs):
        lot = envoyer(**kwargs)
        assert len(lot.artifacts) == 2
        return UploadBatch('partial', lot.artifacts[:1])
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', omettre)
    corrige = pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    assert corrige['publication_pending'] and not corrige['transport_verifie']
    assert len(magasin.envois) == 5 and (source / 'piece.pdf').exists()
    rapports = [a for a in corrige['artifacts'] if a['role'] in ('rapport_pdf', 'rapport_docx')]
    assert len(rapports) == 2 and any(not a['verified'] and a['expected_sha256'] for a in rapports)
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.retry_upload(index, cfg, initial.import_id)
    assert len(magasin.envois) == 5
    index.close()


def test_correction_revoque_transport_avant_regeneration(tmp_path: Path, monkeypatch):
    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    assert initial.transport_verifie
    def interrompre(*_args, **_kwargs):
        raise OSError('panne-generation-synthetique')
    monkeypatch.setattr(pipeline, 'generate_report', interrompre)
    with pytest.raises(OSError, match='panne-generation-synthetique'):
        pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    suspendu = pipeline.get_import(index, initial.import_id)
    assert suspendu['report_generation_pending'] and not suspendu['transport_verifie']
    assert not suspendu['preservation']['transport_verifie'] and not suspendu['cleanup']['purge']
    assert len(magasin.envois) == 3 and (source / 'piece.pdf').exists()
    index.close()


def test_api_correction_interrompue_retourne_revue_sans_fuite(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise OSError('SECRET-SYNTHETIQUE-PANNE-CORRECTION')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
    with TestClient(create_app(cfg), raise_server_exceptions=False) as client:
        reponse = client.patch('/imports/' + initial.import_id, json={'reference': 'AUTRE'})
        assert reponse.status_code == 200
        resultat = reponse.json()
        assert resultat['status'] == 'needs_review' and resultat['publication_pending']
        assert not resultat['all_verified'] and 'SECRET-SYNTHETIQUE-PANNE-CORRECTION' not in reponse.text
        assert client.get('/imports/' + initial.import_id).json()['publication_pending']
        assert client.patch('/imports/' + initial.import_id, json={'reference': 'AUTRE'}).status_code == 409
        assert client.post('/imports/' + initial.import_id + '/retry-upload').status_code == 409
    assert len(magasin.envois) == 5 and (source / 'piece.pdf').exists()
    index.close()


def test_import_resultat_omis_apres_envoi_conserve_inventaire_et_intention(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.storage import UploadBatch

    source = tmp_path / 'source'
    source.mkdir()
    for nom in ('a.txt', 'b.txt'):
        (source / nom).write_bytes(('synthetique-' + nom).encode())
    cfg = _config_stockage(tmp_path, source)
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    magasin = _brancher_stockage(monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def omettre(**kwargs):
        lot = envoyer(**kwargs)
        assert len(lot.artifacts) == 2
        return UploadBatch('partial', lot.artifacts[:1])
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', omettre)
    resultat = pipeline.import_folder(source, cfg, index, import_id='omission-initiale')
    assert resultat.publication_pending and not resultat.transport_verifie and not resultat.all_verified
    assert resultat.status == 'needs_review' and len(magasin.envois) == 2
    assert {a['path'] for a in resultat.artifacts} == {str(source / 'a.txt'), str(source / 'b.txt')}
    manquant = next(a for a in resultat.artifacts if not a.get('verified'))
    assert manquant['expected_sha256'] and manquant['expected_key_base']
    avec_intention = pipeline.get_import(index, resultat.import_id)
    assert avec_intention['publication_pending']
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.retry_upload(index, cfg, resultat.import_id)
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.import_folder(source, cfg, index, import_id=resultat.import_id)
    assert len(magasin.envois) == 2 and all((source / n).exists() for n in ('a.txt', 'b.txt'))
    index.close()


def test_reprise_resultat_omis_apres_envoi_conserve_inventaire_et_intention(tmp_path: Path, monkeypatch):
    from seamtech_search.storage import UploadBatch

    source, cfg, index, magasin = _import_avec_panne_partielle(tmp_path, monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def omettre(**kwargs):
        lot = envoyer(**kwargs)
        assert len(lot.artifacts) == 1
        return UploadBatch('not_applicable')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', omettre)
    resultat = pipeline.retry_upload(index, cfg, 'reprise-capture')
    assert resultat['publication_pending'] and not resultat['preservation']['transport_verifie']
    assert resultat['status'] == 'needs_review' and len(magasin.envois) == 2
    manquant = next(a for a in resultat['artifacts'] if a['path'] == str(source / 'b.txt'))
    assert not manquant['verified'] and manquant['expected_sha256'] and manquant['expected_key_base']
    with pytest.raises(pipeline.PublicationIncertaine):
        pipeline.retry_upload(index, cfg, 'reprise-capture')
    assert len(magasin.envois) == 2 and (source / 'b.txt').exists()
    index.close()


@pytest.mark.parametrize('erreur', [KeyError, ValueError, OSError])
def test_api_correction_erreur_apres_envoi_privilegie_intention(tmp_path: Path, monkeypatch, erreur):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise erreur('SECRET-SYNTHETIQUE-CORRECTION')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
    with TestClient(create_app(cfg), raise_server_exceptions=False) as client:
        reponse = client.patch('/imports/' + initial.import_id, json={'reference': 'AUTRE'})
        assert reponse.status_code == 200
        assert reponse.json()['publication_pending'] and reponse.json()['status'] == 'needs_review'
        assert 'SECRET-SYNTHETIQUE-CORRECTION' not in reponse.text
        assert client.patch('/imports/' + initial.import_id, json={'reference': 'AUTRE'}).status_code == 409
    assert len(magasin.envois) == 5 and (source / 'piece.pdf').exists()
    index.close()


@pytest.mark.parametrize('erreur', [KeyError, ValueError, OSError])
def test_api_reprise_erreur_apres_envoi_privilegie_intention(tmp_path: Path, monkeypatch, erreur):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    source, cfg, index, magasin = _import_avec_panne_partielle(tmp_path, monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise erreur('SECRET-SYNTHETIQUE-REPRISE')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
    with TestClient(create_app(cfg), raise_server_exceptions=False) as client:
        reponse = client.post('/imports/reprise-capture/retry-upload')
        assert reponse.status_code == 200
        assert reponse.json()['publication_pending'] and reponse.json()['status'] == 'needs_review'
        assert 'SECRET-SYNTHETIQUE-REPRISE' not in reponse.text
        assert client.post('/imports/reprise-capture/retry-upload').status_code == 409
    assert len(magasin.envois) == 2 and (source / 'b.txt').exists()
    index.close()


def test_correction_intention_durable_contient_donnees_visees_apres_crash(tmp_path: Path, monkeypatch):
    source, cfg, index, magasin, initial = _import_pdf_stocke(tmp_path, monkeypatch)
    envoyer = pipeline.upload_artifacts_to_storage
    def interrompre(**kwargs):
        envoyer(**kwargs)
        raise RuntimeError('panne-synthetique')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', interrompre)
    with pytest.raises(RuntimeError, match='panne-synthetique'):
        pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    intention = pipeline.get_import(index, initial.import_id)
    assert intention['publication_pending'] and intention['report_generation_pending']
    assert intention['data']['reference'] == 'SYNTHESE'
    assert intention['pending_correction_data']['reference'] == 'AUTRE'
    rapports = [a for a in intention['artifacts'] if a['role'] in ('rapport_pdf', 'rapport_docx')]
    assert len(rapports) == 2 and all(a['expected_sha256'] and a['expected_key_base'] for a in rapports)
    assert len(magasin.envois) == 5 and (source / 'piece.pdf').exists()
    index.close()


def test_correction_crash_avant_envoi_ne_rapporte_pas_terminer(tmp_path: Path, monkeypatch):
    from seamtech_search.indexer import SearchIndex

    source = tmp_path / 'source'
    source.mkdir()
    pdf = source / 'piece.pdf'
    pdf.write_bytes(b'FICHE DE FABRICATION\nReference: SYNTHESE\nMaterial: TEST')
    cfg = _config(tmp_path, source).model_copy(update={'max_file_size_bytes': 2 * 1024 * 1024})
    index = SearchIndex(cfg.database_path)
    index.initialize(rebuild=True)
    monkeypatch.setattr(pipeline, 'extract_structured_pdf', lambda path, _cfg: pipeline.ExtractedData(
        raw_text=path.read_text(), reference='SYNTHESE', material='TISSU', quantity=1,
        description='synthese', dimensions={'length': 10, 'width': 5}, extraction_status='success'))
    initial = pipeline.import_folder(source, cfg, index, import_id='correction-sans-stockage')
    assert initial.status == 'completed' and initial.upload_status == 'not_configured'
    def interrompre(*_args, **_kwargs):
        raise OSError('panne-generation-synthetique')
    monkeypatch.setattr(pipeline, 'generate_report', interrompre)
    with pytest.raises(OSError):
        pipeline.correct_import(index, cfg, initial.import_id, {'reference': 'AUTRE'})
    intention = pipeline.get_import(index, initial.import_id)
    assert intention['status'] == 'needs_review' and intention['report_generation_pending']
    assert not intention['transport_verifie'] and not intention['cleanup']['purge']
    assert pdf.exists()
    index.close()
