"""Fail-closed du texte PDF quand la vue n'a pas pu être inspectée."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pdfplumber
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from seamtech_search import crawler
from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.extractors import extract_file
from seamtech_search.import_pipeline import extract_structured_pdf
from seamtech_search.indexer import SearchIndex


def generer(tmp_path: Path, mode: str) -> Path:
    base = tmp_path / 'brut.pdf'
    pdf = canvas.Canvas(str(base), pagesize=(500, 200))
    pdf.drawString(30, 100, 'VISIBLE SYNTHETIQUE')
    if mode == 'clip':
        chemin = pdf.beginPath()
        chemin.rect(0, 0, 250, 200)
        pdf.saveState()
        pdf.clipPath(chemin, fill=0, stroke=0)
    pdf.drawString(300 if mode != 'media' else 550, 100, 'HORSVUE SYNTHETIQUE')
    if mode == 'clip':
        pdf.restoreState()
    pdf.save()
    writer = PdfWriter()
    writer.add_page(PdfReader(base).pages[0])
    if mode == 'crop':
        writer.pages[0].cropbox.upper_right = (250, 200)
    source = tmp_path / 'source.pdf'
    with source.open('wb') as fichier:
        writer.write(fichier)
    return source


def panne(monkeypatch):
    def erreur(*_args, **_kwargs):
        raise RuntimeError('panne synthétique sans données')
    monkeypatch.setattr(pdfplumber, 'open', erreur)


def configuration(tmp_path: Path) -> AppConfig:
    return AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'index.db', enable_ocr=False)


@pytest.mark.parametrize('mode', ['crop', 'media', 'clip'])
@pytest.mark.parametrize('ocr', [False, True])
def test_repli_inverifie_non_publie_ni_indexe(tmp_path, monkeypatch, mode, ocr):
    source = generer(tmp_path, mode)
    avant = hashlib.sha256(source.read_bytes()).digest()
    assert 'HORSVUE' in PdfReader(source).pages[0].extract_text()  # précondition de la faille
    panne(monkeypatch)
    appels_ocr = []
    monkeypatch.setattr('seamtech_search.extractors._has_command', lambda *_: True)
    monkeypatch.setattr('seamtech_search.extractors._extract_with_ocrmypdf',
                        lambda *_: appels_ocr.append('ocr') or 'faux succès')
    resultat = extract_file(source, enable_ocr=ocr)
    assert resultat.status == 'unavailable'
    assert 'inspection' in resultat.detail.lower()
    assert not resultat.text  # ni hors vue ni préfixe dont on ne peut démontrer la visibilité
    assert not appels_ocr
    cfg = configuration(tmp_path)
    monkeypatch.setattr(crawler, '_extract_with_timeout', lambda *_: resultat)
    document = crawler._document_from_path(source, cfg, False, {})
    index = SearchIndex(cfg.database_path)
    index.initialize()
    index.upsert_document(document)
    try:
        assert not index.search('HORSVUE')
        assert not index.search('VISIBLE')
        assert index.search('source')[0]['extraction_status'] == 'unavailable'
    finally:
        index.close()
    assert hashlib.sha256(source.read_bytes()).digest() == avant


def test_import_et_preview_ne_publient_pas_texte_inverifie(tmp_path, monkeypatch):
    source = generer(tmp_path, 'crop')
    cfg = configuration(tmp_path)
    panne(monkeypatch)
    resultat = extract_structured_pdf(source, cfg)
    assert resultat.extraction_status == 'failed'
    assert not resultat.raw_text
    assert 'HORSVUE' not in str(resultat.model_dump())
    with TestClient(create_app(cfg)) as client:
        reponse = client.get('/preview', params={'path': str(source)})
    assert reponse.status_code == 200
    assert reponse.json()['extraction_status'] == 'unavailable'
    assert reponse.json()['text'] == ''
    assert 'HORSVUE' not in reponse.text


def test_worker_reel_ne_transmet_pas_repli_inverifie(tmp_path, monkeypatch):
    source = generer(tmp_path, 'crop')
    avant = hashlib.sha256(source.read_bytes()).digest()
    site = tmp_path / 'site'
    site.mkdir()
    (site / 'sitecustomize.py').write_text(
        'import pdfplumber\n'
        'def panne(*_args, **_kwargs): raise RuntimeError("panne synthétique")\n'
        'pdfplumber.open = panne\n', encoding='utf-8')
    precedent = os.environ.get('PYTHONPATH', '')
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join((str(site), precedent)))
    resultat = crawler._extract_in_process(source, configuration(tmp_path))
    assert resultat.status == 'unavailable'
    assert 'inspection' in resultat.detail.lower()
    assert not resultat.text
    assert hashlib.sha256(source.read_bytes()).digest() == avant
