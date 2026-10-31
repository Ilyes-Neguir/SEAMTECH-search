"""Les résultats partiels ne doivent pas devenir un succès ou perdre leur texte."""
from pathlib import Path

import pytest

from seamtech_search import crawler
from seamtech_search.config import AppConfig
from tests.test_ocr_natif_image import creer_pdf


def config(tmp_path: Path) -> AppConfig:
    return AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'index.db', enable_ocr=True,
                     tesseract_command='tesseract-absent-synthetique',
                     ocrmypdf_command='ocrmypdf-absent-synthetique')


def test_worker_reel_preserve_natif_et_indisponibilite_ocr(tmp_path):
    pdf = creer_pdf(tmp_path, ('natif', 'image'))
    resultat = crawler._extract_in_process(pdf, config(tmp_path))
    assert resultat.status == 'unavailable'
    assert 'Texte natif synthetique' in resultat.text
    assert resultat.detail


def test_texte_commencant_par_marqueur_n_est_pas_statut(tmp_path):
    fichier = tmp_path / 'note.txt'
    fichier.write_text('[extraction unavailable: ceci est un texte]\nsuite', encoding='utf-8')
    resultat = crawler._extract_in_process(fichier, config(tmp_path))
    assert resultat.status == 'extracted'
    assert resultat.text == fichier.read_text(encoding='utf-8')


@pytest.mark.parametrize('contenu', ['', 'texte arbitraire', '{}', '{"text":42,"status":"extracted","detail":""}',
                                     '{"text":"a","status":"invented","detail":""}'])
def test_worker_reponse_invalide_echoue_sans_exposer_contenu(tmp_path, monkeypatch, contenu):
    class FauxProcessus:
        returncode = 0

        def communicate(self, **kwargs):
            return contenu, ''

    monkeypatch.setattr(crawler.subprocess, 'Popen', lambda *args, **kwargs: FauxProcessus())
    resultat = crawler._extract_in_process(tmp_path / 'fichier.txt', config(tmp_path))
    assert resultat.status == 'error'
    assert not resultat.text
    assert contenu not in resultat.detail if contenu else True


def test_resultat_partiel_est_persiste_sans_devenir_complet(tmp_path):
    from seamtech_search.indexer import SearchIndex

    pdf = creer_pdf(tmp_path, ('natif', 'image'))
    cfg = config(tmp_path)
    document = crawler._document_from_path(pdf, cfg, is_dir=False, existing_metadata={})
    assert document.extraction_status == 'unavailable'
    assert 'Texte natif synthetique' in document.text
    index = SearchIndex(cfg.database_path)
    index.initialize()
    index.upsert_document(document)
    with index.connect() as connexion:
        ligne = connexion.execute('SELECT extraction_status, extraction_detail, extractor_version FROM documents WHERE name = ?',
                                  (pdf.name,)).fetchone()
        texte = connexion.execute('SELECT content FROM documents_fts WHERE name = ?', (pdf.name,)).fetchone()
    assert ligne['extraction_status'] == 'unavailable'
    assert ligne['extraction_detail']
    assert ligne['extractor_version'] >= 9
    assert 'Texte natif synthetique' in texte['content']


def test_version_huit_force_reextraction(tmp_path):
    from seamtech_search.extractors import CURRENT_EXTRACTOR_VERSION

    assert CURRENT_EXTRACTOR_VERSION >= 9
    pdf = creer_pdf(tmp_path, ('natif', 'image'))
    stat = pdf.stat()
    cle = str(pdf.resolve())
    document = crawler._document_from_path(pdf, config(tmp_path), is_dir=False,
                                           existing_metadata={cle: (stat.st_size, stat.st_mtime, 8)})
    assert document.extraction_status == 'unavailable'
    assert 'Texte natif synthetique' in document.text


def test_worker_chemin_et_options_ne_sont_pas_arguments_processus(tmp_path, monkeypatch):
    import json

    fichier = tmp_path / 'SECRET-SYNTHETIQUE.txt'
    commandes = []

    class FauxProcessus:
        returncode = 0

        def communicate(self, **kwargs):
            requete = json.loads(kwargs['input'])
            assert requete['path'] == str(fichier)
            return json.dumps({'text': 'test', 'status': 'extracted', 'detail': ''}), ''

    def simulation(commande, **kwargs):
        commandes.append(commande)
        return FauxProcessus()

    monkeypatch.setattr(crawler.subprocess, 'Popen', simulation)
    resultat = crawler._extract_in_process(fichier, config(tmp_path))
    assert resultat.text == 'test'
    assert all('SECRET-SYNTHETIQUE' not in str(commande) for commande in commandes)


@pytest.mark.skipif(__import__('os').name != 'posix', reason='isolation de groupe POSIX')
def test_timeout_worker_termine_aussi_les_enfants(tmp_path):
    import sys
    import time

    fichier = tmp_path / 'document.dwg'
    fichier.write_bytes(b'synthetique')
    temoin = tmp_path / 'enfant-survivant'
    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'index.db', extraction_timeout_seconds=1,
                    external_extraction_timeout_seconds=15,
                    external_extractors={'.dwg': [sys.executable, '-c',
                        "import time,pathlib; time.sleep(2); pathlib.Path(__import__('sys').argv[1]).write_text('survivant')",
                        str(temoin)]})
    resultat = crawler._extract_in_process(fichier, cfg)
    assert resultat.status == 'timeout'
    time.sleep(2.5)
    assert not temoin.exists()
