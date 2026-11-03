"""La correction n'a pas le droit d'annoncer une préservation partielle comme complète."""
from pathlib import Path

import pytest

from seamtech_search import import_pipeline as pipeline
from seamtech_search.config import AppConfig
from seamtech_search.storage import UploadBatch, UploadedArtifact


def generer_fichier(_data, chemin, **_kwargs):
    chemin.write_bytes(b'rapport-synthetique')
    return chemin


def preuve(path, status='uploaded', verified=True):
    import hashlib

    return {'path': str(path), 'name': path.name, 'key': 'objet-' + path.name if verified else None,
            'bucket': 'test', 'status': status, 'verified': verified, 'verification': 'relecture_sha256',
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if verified and path.is_file() else None}


def test_rapports_deja_verifies_retrouves_dans_manifeste(tmp_path):
    rapport = tmp_path / 'rapport.pdf'
    manifeste = pipeline.construire_manifeste({'files': [], 'report_path': str(rapport),
                                               'artifacts': [preuve(rapport)]})
    assert manifeste[0]['status'] == 'uploaded'
    assert manifeste[0]['verified'] is True
    assert pipeline.agreger_preservation(manifeste, destination_configuree=True) == ('uploaded', True)


def test_correction_preserve_tout_inventaire_sans_reouvrir_sources(tmp_path, monkeypatch):
    source = tmp_path / 'src'
    source.mkdir()
    pdf, piece = source / 'fiche.pdf', source / 'annexe.bin'
    rapports = tmp_path / 'reports' / 'test'
    rapports.mkdir(parents=True)
    rp, rd = rapports / 'technical-report.pdf', rapports / 'technical-report.docx'
    payload = {'source_path': str(source), 'technical_pdf': str(pdf), 'excel_file': None,
               'data': {'raw_text': 'texte synthétique', 'reference': 'A', 'material': 'B', 'quantity': 1},
               'files': [{'path': str(pdf), 'name': pdf.name, 'category': 'technical_pdf',
                          'upload_status': 'uploaded', 'object_key': 'objet-fiche.pdf'},
                         {'path': str(piece), 'name': piece.name, 'category': 'piece',
                          'upload_status': 'failed', 'object_key': None}],
               'report_path': str(rp), 'report_docx_path': str(rd),
               'artifacts': [preuve(pdf), preuve(piece, 'failed', False), preuve(rp), preuve(rd)],
               'upload_status': 'partial', 'all_verified': False, 'warnings': [],
               'cleanup': {'purge': True}, 'preservation': {'integrite_prouvee': True}}
    monkeypatch.setattr(pipeline, 'get_import', lambda *args: payload)
    sauvegardes = []
    monkeypatch.setattr(pipeline, 'update_import', lambda _index, _id, _statut, data: sauvegardes.append(data))
    monkeypatch.setattr(pipeline, 'generate_report', generer_fichier)
    monkeypatch.setattr(pipeline, 'generate_docx_report', generer_fichier)
    def envoyer(**kwargs):
        import hashlib

        chemins = [Path(p) for p in kwargs['files_to_upload'] if p]
        assert {p.name for p in chemins} == {rp.name, rd.name}
        assert all(p.read_bytes() == b'rapport-synthetique' for p in chemins)
        assert not {pdf, piece} & set(chemins), 'les sources ne doivent pas être rouvertes à la correction'
        return UploadBatch('uploaded', [UploadedArtifact(str(p), p.name, 'nouveau-' + p.name, 'test',
                                                          'uploaded', True, 'relecture_sha256',
                                                          sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                                        for p in chemins])
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', envoyer)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db')
    resultat = pipeline.correct_import(None, config, 'test', {'quantity': 2})
    assert resultat['all_verified'] is False and resultat['upload_status'] == 'partial'
    assert resultat['cleanup']['purge'] is False
    assert resultat['preservation']['integrite_prouvee'] is False
    manifeste = {e['path']: e for e in resultat['artifacts']}
    assert set(manifeste) == {str(pdf), str(piece), str(rp), str(rd)}
    assert manifeste[str(pdf)]['verified'] and manifeste[str(pdf)]['key'] == 'objet-fiche.pdf'
    assert not manifeste[str(piece)]['verified'] and manifeste[str(piece)]['status'] == 'failed'
    assert manifeste[str(rp)]['key'] == 'nouveau-technical-report.pdf'
    assert manifeste[str(rd)]['key'] == 'nouveau-technical-report.docx'
    relecture = {e['path']: e for e in pipeline.construire_manifeste(resultat)}
    assert relecture[str(rp)]['verified'] and relecture[str(rd)]['verified']
    assert sauvegardes and sauvegardes[0]['all_verified'] is False


def test_manifeste_ne_transfere_pas_preuve_sur_une_autre_cle(tmp_path):
    piece = tmp_path / 'piece.pdf'
    payload = {'files': [{'path': str(piece), 'name': piece.name, 'upload_status': 'uploaded',
                          'object_key': 'cle-differente'}],
               'artifacts': [preuve(piece)]}
    entree = pipeline.construire_manifeste(payload)[0]
    assert not entree['verified']
    assert pipeline.agreger_preservation([entree], destination_configuree=True)[1] is False


def test_manifeste_ne_transfere_pas_preuve_sur_un_autre_bucket(tmp_path):
    piece = tmp_path / 'piece.pdf'
    ancien = preuve(piece)
    payload = {'files': [{'path': str(piece), 'name': piece.name, 'upload_status': 'uploaded',
                          'object_key': ancien['key'], 'object_bucket': 'autre-bucket'}],
               'artifacts': [ancien]}
    entree = pipeline.construire_manifeste(payload)[0]
    assert not entree['verified']
    assert pipeline.agreger_preservation([entree], destination_configuree=True)[1] is False


def test_correction_ne_peut_ecraser_un_fichier_source_comme_rapport(tmp_path, monkeypatch):
    import pytest

    source = tmp_path / 'reports' / 'test'
    source.mkdir(parents=True)
    pdf = source / 'technical-report.pdf'
    pdf.write_bytes(b'original-synthetique')
    payload = {'source_path': str(source), 'technical_pdf': str(pdf),
               'data': {'raw_text': 'synthese', 'reference': 'A'},
               'files': [{'path': str(pdf), 'name': pdf.name, 'category': 'technical_pdf'}],
               'report_path': str(source / 'rapport-ancien.pdf'), 'artifacts': [], 'warnings': []}
    monkeypatch.setattr(pipeline, 'get_import', lambda *args: payload)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db')
    with pytest.raises(ValueError):
        pipeline.correct_import(None, config, 'test', {'reference': 'B'})
    assert pdf.read_bytes() == b'original-synthetique'


def test_correction_refuse_rapport_lie_physiquement_a_un_original(tmp_path, monkeypatch):
    import os

    import pytest

    source, rapports = tmp_path / 'src', tmp_path / 'reports' / 'test'
    source.mkdir()
    rapports.mkdir(parents=True)
    original = source / 'fiche.pdf'
    original.write_bytes(b'original-synthetique')
    os.link(original, rapports / 'technical-report.pdf')
    payload = {'source_path': str(source), 'technical_pdf': str(original),
               'data': {'raw_text': 'synthese', 'reference': 'A'},
               'files': [{'path': str(original), 'name': original.name, 'category': 'technical_pdf'}],
               'report_path': str(rapports / 'rapport-ancien.pdf'), 'artifacts': [], 'warnings': []}
    monkeypatch.setattr(pipeline, 'get_import', lambda *args: payload)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db')
    with pytest.raises((ValueError, OSError)):
        pipeline.correct_import(None, config, 'test', {'reference': 'B'})
    assert original.read_bytes() == b'original-synthetique'


def test_cle_sans_bucket_ne_prouve_pas_preservation(tmp_path):
    piece = tmp_path / 'piece.pdf'
    ancien = preuve(piece)
    ancien['bucket'] = None
    payload = {'files': [{'path': str(piece), 'name': piece.name, 'upload_status': 'uploaded',
                          'object_key': ancien['key']}], 'artifacts': [ancien]}
    manifeste = pipeline.construire_manifeste(payload)
    assert pipeline.agreger_preservation(manifeste, destination_configuree=True)[1] is False


def test_batch_sans_cle_ou_bucket_ne_doit_pas_autoriser_purge():
    for cle, bucket in ((None, 'bucket'), ('cle', None), ('', 'bucket'), ('cle', '')):
        artefact = UploadedArtifact('fichier', 'fichier', key=cle, bucket=bucket,
                                    status='uploaded', verified=True)
        assert UploadBatch('uploaded', [artefact]).all_verified is False


def test_correction_echec_rapport_invalide_preuve_ancienne(tmp_path, monkeypatch):
    source = tmp_path / 'src'
    source.mkdir()
    pdf = source / 'fiche.pdf'
    rapports = tmp_path / 'reports' / 'test'
    rapports.mkdir(parents=True)
    rp, rd = rapports / 'technical-report.pdf', rapports / 'technical-report.docx'
    payload = {'source_path': str(source), 'technical_pdf': str(pdf), 'data': {'raw_text': 'synthese'},
               'files': [{'path': str(pdf), 'name': pdf.name, 'category': 'technical_pdf',
                          'upload_status': 'uploaded', 'object_key': 'objet-fiche.pdf', 'object_bucket': 'test'}],
               'report_path': str(rp), 'report_docx_path': str(rd),
               'artifacts': [preuve(pdf), preuve(rp), preuve(rd)], 'all_verified': True,
               'cleanup': {'purge': True}, 'warnings': []}
    monkeypatch.setattr(pipeline, 'get_import', lambda *args: payload)
    monkeypatch.setattr(pipeline, 'update_import', lambda *args: None)
    monkeypatch.setattr(pipeline, 'generate_report', generer_fichier)
    monkeypatch.setattr(pipeline, 'generate_docx_report', generer_fichier)
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', lambda **kwargs: UploadBatch('not_configured'))
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db', delete_local_after_upload=True)
    resultat = pipeline.correct_import(None, config, 'test', {'reference': 'B'})
    assert not resultat['all_verified'] and not resultat['cleanup']['purge']
    manifeste = {e['path']: e for e in resultat['artifacts']}
    assert manifeste[str(pdf)]['verified']
    assert not manifeste[str(rp)]['verified'] and manifeste[str(rp)]['key'] is None
    assert not manifeste[str(rd)]['verified'] and manifeste[str(rd)]['key'] is None


def test_preuve_rapport_invalidee_avant_regeneration_en_cas_de_crash(tmp_path, monkeypatch):
    import copy

    import pytest

    source = tmp_path / 'src'
    source.mkdir()
    pdf = source / 'fiche.pdf'
    rapport = tmp_path / 'reports' / 'test' / 'technical-report.pdf'
    payload = {'source_path': str(source), 'technical_pdf': str(pdf), 'data': {'raw_text': 'synthese'},
               'files': [{'path': str(pdf), 'name': pdf.name, 'category': 'technical_pdf',
                          'upload_status': 'uploaded', 'object_key': 'objet-fiche.pdf', 'object_bucket': 'test'}],
               'report_path': str(rapport), 'artifacts': [preuve(pdf), preuve(rapport)],
               'all_verified': True, 'upload_status': 'uploaded', 'cleanup': {'purge': True}, 'warnings': []}
    sauvegardes = []
    monkeypatch.setattr(pipeline, 'get_import', lambda *args: payload)
    monkeypatch.setattr(pipeline, 'update_import', lambda *args: sauvegardes.append(copy.deepcopy(args[-1])))
    def echec(*args, **kwargs):
        raise OSError('panne simulée')
    monkeypatch.setattr(pipeline, 'generate_report', echec)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db')
    with pytest.raises(OSError):
        pipeline.correct_import(None, config, 'test', {'reference': 'B'})
    assert sauvegardes and sauvegardes[-1]['all_verified'] is False
    assert not sauvegardes[-1]['cleanup']['purge']
    assert not next(a for a in sauvegardes[-1]['artifacts'] if a['path'] == str(rapport))['verified']


def test_reprise_refuse_rapport_regeneration_interrompue(tmp_path, monkeypatch):
    import pytest

    source = tmp_path / 'src'
    source.mkdir()
    rapport = tmp_path / 'technical-report.pdf'
    rapport.write_bytes(b'rapport-partiel')
    payload = {'source_path': str(source), 'report_path': str(rapport), 'files': [],
               'artifacts': [], 'report_generation_pending': True}
    monkeypatch.setattr(pipeline, 'get_import', lambda *args: payload)
    uploads = []
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', lambda **kw: uploads.append(kw))
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db')
    with pytest.raises(pipeline.RapportsEnRegeneration):
        pipeline.retry_upload(None, config, 'test')
    assert uploads == []


def test_deux_corrections_concurrentes_ne_perdent_aucune_modification(tmp_path, monkeypatch):
    import copy
    import threading

    source = tmp_path / 'src'
    source.mkdir()
    pdf = source / 'fiche.pdf'
    rapports = tmp_path / 'reports' / 'test'
    rapports.mkdir(parents=True)
    state = {'source_path': str(source), 'technical_pdf': str(pdf),
             'data': {'raw_text': 'synthese', 'reference': 'ancienne', 'quantity': 1},
             'files': [{'path': str(pdf), 'name': pdf.name, 'category': 'technical_pdf'}],
             'report_path': str(rapports / 'technical-report.pdf'), 'artifacts': [],
             'all_verified': False, 'upload_status': 'not_configured', 'warnings': []}
    pret, continuer = threading.Event(), threading.Event()
    verrou = threading.Lock()
    def lire(*_args):
        with verrou:
            return copy.deepcopy(state)
    def ecrire(_index, _id, _status, payload):
        with verrou:
            state.clear()
            state.update(copy.deepcopy(payload))
    def generer(_data, chemin, **_kwargs):
        if threading.current_thread().name == 'correction-a':
            pret.set()
            assert continuer.wait(10)
        chemin.write_bytes(b'rapport-synthetique')
        return chemin
    monkeypatch.setattr(pipeline, 'get_import', lire)
    monkeypatch.setattr(pipeline, 'update_import', ecrire)
    monkeypatch.setattr(pipeline, 'generate_report', generer)
    monkeypatch.setattr(pipeline, 'generate_docx_report', generer_fichier)
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', lambda **_kwargs: UploadBatch('not_configured'))
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db')
    erreurs = []
    def premiere():
        try:
            pipeline.correct_import(None, config, 'test', {'quantity': 2})
        except Exception as erreur:
            erreurs.append(erreur)
    fil = threading.Thread(target=premiere, name='correction-a')
    fil.start()
    try:
        assert pret.wait(10)
        # Le concurrent est refusé explicitement : il ne doit pas lire un état
        # périmé ni écraser la première correction.
        with pytest.raises(pipeline.ImportMutationEnCours):
            pipeline.correct_import(None, config, 'test', {'reference': 'nouvelle'})
    finally:
        continuer.set()
        fil.join(10)
    assert not fil.is_alive() and not erreurs
    assert state['data']['quantity'] == 2
    # Après résolution du conflit, la seconde modification repart de l'état
    # durable et les deux champs restent présents.
    pipeline.correct_import(None, config, 'test', {'reference': 'nouvelle'})
    assert state['data']['quantity'] == 2
    assert state['data']['reference'] == 'nouvelle'


def test_correction_refuse_chemin_rapport_deplace_dans_un_autre_dossier(tmp_path, monkeypatch):
    source = tmp_path / 'src'
    source.mkdir()
    autre = tmp_path / 'autre-dossier'
    autre.mkdir()
    victime = autre / 'technical-report.pdf'
    victime.write_bytes(b'original-autre-dossier')
    pdf = source / 'fiche.pdf'
    payload = {'source_path': str(source), 'technical_pdf': str(pdf), 'data': {'raw_text': 'synthese'},
               'files': [{'path': str(pdf), 'name': pdf.name}],
               'report_path': str(autre / 'ancien-rapport.pdf'), 'artifacts': [], 'warnings': []}
    monkeypatch.setattr(pipeline, 'get_import', lambda *args: payload)
    monkeypatch.setattr(pipeline, 'update_import', lambda *args: None)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db')
    with pytest.raises(ValueError):
        pipeline.correct_import(None, config, 'test', {'reference': 'B'})
    assert victime.read_bytes() == b'original-autre-dossier'


def test_correction_refuse_dossier_de_rapport_symlink(tmp_path, monkeypatch):
    source = tmp_path / 'src'
    source.mkdir()
    autre = tmp_path / 'autre-dossier'
    autre.mkdir()
    victime = autre / 'technical-report.pdf'
    victime.write_bytes(b'original-autre-dossier')
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / 'test').symlink_to(autre, target_is_directory=True)
    pdf = source / 'fiche.pdf'
    payload = {'source_path': str(source), 'technical_pdf': str(pdf), 'data': {'raw_text': 'synthese'},
               'files': [{'path': str(pdf), 'name': pdf.name}],
               'report_path': str(reports / 'test' / 'technical-report.pdf'), 'artifacts': [], 'warnings': []}
    monkeypatch.setattr(pipeline, 'get_import', lambda *args: payload)
    monkeypatch.setattr(pipeline, 'update_import', lambda *args: None)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db')
    with pytest.raises((ValueError, OSError)):
        pipeline.correct_import(None, config, 'test', {'reference': 'B'})
    assert victime.read_bytes() == b'original-autre-dossier'


def test_correction_refuse_repertoire_substitue_pendant_generation(tmp_path, monkeypatch):
    source = tmp_path / 'src'
    source.mkdir()
    pdf = source / 'fiche.pdf'
    rapport_dir = tmp_path / 'reports' / 'test'
    rapport_dir.mkdir(parents=True)
    autre = tmp_path / 'autre-dossier'
    autre.mkdir()
    (autre / 'technical-report.pdf').write_bytes(b'secret-synthetique')
    payload = {'source_path': str(source), 'technical_pdf': str(pdf), 'data': {'raw_text': 'synthese'},
               'files': [{'path': str(pdf), 'name': pdf.name}],
               'report_path': str(rapport_dir / 'technical-report.pdf'), 'artifacts': [], 'warnings': []}
    monkeypatch.setattr(pipeline, 'get_import', lambda *args: payload)
    monkeypatch.setattr(pipeline, 'update_import', lambda *args: None)
    def substituer(data, chemin, **kwargs):
        rapport_dir.rename(rapport_dir.with_name('ancien'))
        rapport_dir.symlink_to(autre, target_is_directory=True)
        return generer_fichier(data, chemin, **kwargs)
    monkeypatch.setattr(pipeline, 'generate_report', substituer)
    monkeypatch.setattr(pipeline, 'generate_docx_report', generer_fichier)
    envois = []
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', lambda **kw: envois.append(kw))
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db')
    with pytest.raises((ValueError, OSError)):
        pipeline.correct_import(None, config, 'test', {'reference': 'B'})
    assert not envois
    assert (autre / 'technical-report.pdf').read_bytes() == b'secret-synthetique'
    assert payload['report_generation_pending']


def test_correction_ne_doit_pas_envoyer_fichier_exterieur_si_dossier_substitue_avant_envoi(tmp_path, monkeypatch):
    from contextlib import contextmanager
    source = tmp_path / 'src'
    source.mkdir()
    pdf = source / 'fiche.pdf'
    rapport_dir = tmp_path / 'reports' / 'test'
    rapport_dir.mkdir(parents=True)
    autre = tmp_path / 'autre'
    autre.mkdir()
    for nom in ('technical-report.pdf', 'technical-report.docx'):
        (autre / nom).write_bytes(b'SECRET-SYNTHETIQUE')
    payload = {'source_path': str(source), 'technical_pdf': str(pdf), 'data': {'raw_text': 'synthese'},
               'files': [{'path': str(pdf), 'name': pdf.name}],
               'report_path': str(rapport_dir / 'technical-report.pdf'), 'artifacts': [], 'warnings': []}
    monkeypatch.setattr(pipeline, 'get_import', lambda *args: payload)
    monkeypatch.setattr(pipeline, 'update_import', lambda *args: None)
    monkeypatch.setattr(pipeline, 'generate_report', generer_fichier)
    monkeypatch.setattr(pipeline, 'generate_docx_report', generer_fichier)
    generer = pipeline._generer_rapports_confines
    @contextmanager
    def substituer(*args, **kwargs):
        with generer(*args, **kwargs) as resultat:
            rapport_dir.rename(rapport_dir.with_name('ancien'))
            rapport_dir.symlink_to(autre, target_is_directory=True)
            yield resultat
    monkeypatch.setattr(pipeline, '_generer_rapports_confines', substituer)
    envois = []
    def envoyer(**kwargs):
        envois.extend(Path(p).read_bytes() for p in kwargs['files_to_upload'])
        return UploadBatch('not_configured')
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', envoyer)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db')
    pipeline.correct_import(None, config, 'test', {'reference': 'B'})
    assert b'SECRET-SYNTHETIQUE' not in envois


def test_copie_privee_n_est_pas_un_lien_physique_du_rapport_publie(tmp_path):
    from unittest.mock import patch

    from seamtech_search.import_pipeline import ExtractedData, _generer_rapports_confines

    rapport_dir = tmp_path / 'reports' / 'test'
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'index.db')
    with patch.object(pipeline, 'generate_report', generer_fichier), \
         patch.object(pipeline, 'generate_docx_report', generer_fichier):
        with _generer_rapports_confines(ExtractedData(raw_text='synthese'), config, 'test', rapport_dir, None) as sorties:
            public_pdf, _, prive_pdf, _ = sorties
            public_pdf.write_bytes(b'mutation-externe')
            assert prive_pdf.read_bytes() == b'rapport-synthetique'


def test_correction_envoie_copies_privees_et_preserve_references_logiques(tmp_path, monkeypatch):
    from seamtech_search.storage import S3StorageClient
    from tests.s3_en_memoire import S3EnMemoire

    source = tmp_path / 'src'
    source.mkdir()
    pdf = source / 'fiche.pdf'
    pdf.write_bytes(b'original-synthetique')
    rapports = tmp_path / 'reports' / 'test'
    rapports.mkdir(parents=True)
    report = rapports / 'technical-report.pdf'
    payload = {'source_path': str(source), 'technical_pdf': str(pdf), 'data': {'raw_text': 'synthese'},
               'files': [{'path': str(pdf), 'name': pdf.name, 'upload_status': 'uploaded',
                          'object_key': 'objet-fiche.pdf', 'object_bucket': 'test'}],
               'report_path': str(report), 'artifacts': [preuve(pdf)], 'warnings': []}
    monkeypatch.setattr(pipeline, 'get_import', lambda *args: payload)
    monkeypatch.setattr(pipeline, 'update_import', lambda *args: None)
    magasin = S3EnMemoire()
    monkeypatch.setattr(S3StorageClient, '_get_client', lambda self, **kw: magasin)
    monkeypatch.setattr(S3StorageClient, 'ensure_bucket_exists', lambda self: True)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db',
                       s3_endpoint_url='https://s3.invalide', s3_bucket='test',
                       s3_access_key='cle-synthetique', s3_secret_key='secret-synthetique')
    resultat = pipeline.correct_import(None, config, 'test', {'reference': 'B'})
    assert resultat['all_verified'] is False
    assert resultat['status'] == 'needs_review'
    assert resultat['preservation']['transport_verifie'] is True
    for entree in resultat['artifacts']:
        if entree['role'] in ('rapport_pdf', 'rapport_docx'):
            assert entree['verified'] and entree['bucket'] == 'test'
            assert Path(entree['path']).parent == rapports
            assert magasin.objets[entree['key']]['donnees'] == Path(entree['path']).read_bytes()
            import hashlib
            assert entree['sha256'] == hashlib.sha256(Path(entree['path']).read_bytes()).hexdigest()
    assert pdf.read_bytes() == b'original-synthetique'


def test_correction_refuse_collisions_des_rapports_de_repli(tmp_path, monkeypatch):
    source = tmp_path / 'src'
    source.mkdir()
    pdf = source / 'fiche.pdf'
    rapport = tmp_path / 'reports' / 'test' / 'technical-report.pdf'
    payload = {'source_path': str(source), 'technical_pdf': str(pdf), 'data': {'raw_text': 'synthese'},
               'files': [{'path': str(pdf), 'name': pdf.name}], 'report_path': str(rapport),
               'artifacts': [], 'warnings': []}
    monkeypatch.setattr(pipeline, 'get_import', lambda *args: payload)
    sauvegardes, envois = [], []
    monkeypatch.setattr(pipeline, 'update_import', lambda _i, _id, _status, p: sauvegardes.append(p.copy()))
    def json_repli(_data, chemin, **_kwargs):
        resultat = chemin.with_suffix('.json')
        resultat.write_bytes(b'repli-synthetique')
        return resultat
    monkeypatch.setattr(pipeline, 'generate_report', json_repli)
    monkeypatch.setattr(pipeline, 'generate_docx_report', json_repli)
    monkeypatch.setattr(pipeline, 'upload_artifacts_to_storage', lambda **kw: envois.append(kw))
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db')
    with pytest.raises(ValueError):
        pipeline.correct_import(None, config, 'test', {'reference': 'B'})
    assert sauvegardes[-1]['report_generation_pending'] is True and not envois
    assert not rapport.exists()


def test_correction_complete_ne_declare_pas_preservation_terminee(tmp_path, monkeypatch):
    source = tmp_path / 'src'
    source.mkdir()
    fichier = source / 'piece.txt'
    fichier.write_bytes(b'synthetique')
    payload = {'source_path': str(source), 'data': {'raw_text': 'synthese', 'reference': 'A',
               'material': 'tissu', 'quantity': 1, 'description': 'description',
               'dimensions': {'length': 10, 'width': 5}}, 'files': [], 'artifacts': [], 'warnings': []}
    monkeypatch.setattr(pipeline, 'get_import', lambda *args: payload)
    monkeypatch.setattr(pipeline, 'update_import', lambda *args: None)
    from seamtech_search.storage import S3StorageClient
    from tests.s3_en_memoire import S3EnMemoire

    magasin = S3EnMemoire()
    monkeypatch.setattr(S3StorageClient, '_get_client', lambda self, **kwargs: magasin)
    monkeypatch.setattr(S3StorageClient, 'ensure_bucket_exists', lambda self: True)
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db',
                       s3_endpoint_url='https://s3.invalide', s3_bucket='bucket-test',
                       s3_access_key='cle-synthetique', s3_secret_key='secret-synthetique')
    resultat = pipeline.correct_import(None, config, 'test', {'reference': 'B'})
    assert resultat['data']['extraction_status'] == 'success'
    assert resultat['preservation']['transport_verifie']
    assert not resultat['all_verified']
    assert resultat['status'] == 'needs_review'
