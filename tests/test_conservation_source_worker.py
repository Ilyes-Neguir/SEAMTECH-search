"""Une preuve d'envoi passée ne couvre pas les mutations ultérieures d'un dossier."""
from dataclasses import replace
from unittest.mock import patch

import pytest

from seamtech_search.import_pipeline import staging_root
from seamtech_search.worker import process_import_task
from tests.test_worker_coverage import fake_result, make_config, make_index


@pytest.mark.parametrize('staging', [False, True])
def test_worker_conserve_fichiers_ajoutes_apres_inventaire(tmp_path, staging):
    cfg = make_config(tmp_path, {'delete_local_after_upload': True})
    source = (staging_root(cfg) if staging else tmp_path) / 'depot'
    source.mkdir(parents=True)
    index = make_index(tmp_path / 'index')
    result = fake_result(tmp_path, 'uploaded', True, True, source)
    result = replace(result, source_path=str(source))
    nouveau = source / 'ajout-tardif.txt'

    def importation(*args, **kwargs):
        nouveau.write_bytes(b'original-nouveau-synthetique')
        return result

    with patch('seamtech_search.worker.import_folder', side_effect=importation), \
         patch('seamtech_search.worker.update_job'):
        sortie = process_import_task({'job_id': 'test-conservation', 'source_path': str(source)}, cfg, index)
    assert sortie['status'] == 'completed'
    assert nouveau.read_bytes() == b'original-nouveau-synthetique'


def test_retention_ne_supprime_pas_fichier_depose_sans_preuve(tmp_path):
    import os
    import time

    from seamtech_search.retention import run_retention_cleanup

    cfg = make_config(tmp_path, {'staged_retention_days': 1})
    source = staging_root(cfg) / 'depot'
    source.mkdir(parents=True)
    fichier = source / 'fichier.txt'
    fichier.write_bytes(b'unique-copie-synthetique')
    ancien = time.time() - 30 * 86400
    os.utime(source, (ancien, ancien))
    index = make_index(tmp_path)
    resultat = run_retention_cleanup(cfg, index)
    assert fichier.read_bytes() == b'unique-copie-synthetique'
    assert resultat['pruned_staged_uploads'] == 0
