"""Le contenu hors MediaBox ne doit jamais devenir du texte visible/indexé."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from seamtech_search import crawler
from seamtech_search.config import AppConfig
from seamtech_search.extractors import extract_file
from seamtech_search.fiches.extraction import analyser_pdf
from seamtech_search.fiches.gabarit_brouillon import _extraire_mots_avec_bbox
from seamtech_search.indexer import SearchIndex


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
@pytest.mark.parametrize('ocr', [False, True])
def test_hors_mediabox_isole_du_corpus_visible(tmp_path: Path, angle, ocr):
    brut = tmp_path / 'brut.pdf'
    pdf = canvas.Canvas(str(brut), pagesize=(500, 200))
    pdf.drawString(30, 100, 'VISIBLE SYNTHETIQUE')
    pdf.drawString(550, 100, 'HORSVUE SYNTHETIQUE')
    pdf.save()
    auteur = PdfWriter()
    auteur.add_page(PdfReader(brut).pages[0])
    auteur.pages[0].rotate(angle)
    source = tmp_path / 'source.pdf'
    with source.open('wb') as fichier:
        auteur.write(fichier)
    avant = hashlib.sha256(source.read_bytes()).digest()
    resultat = extract_file(source, enable_ocr=ocr)
    assert resultat.status == 'extracted'
    assert 'VISIBLE' in resultat.text
    assert 'HORSVUE' not in resultat.text
    mots = [mot.texte for page in analyser_pdf(source) for ligne in page.lignes for mot in ligne.mots]
    assert 'VISIBLE' in mots and 'HORSVUE' not in mots
    brouillon = _extraire_mots_avec_bbox(source)
    assert any(mot['texte'] == 'VISIBLE' for mot in brouillon)
    assert all(mot['texte'] != 'HORSVUE' for mot in brouillon)
    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path/'index.db', enable_ocr=ocr)
    doc = crawler._document_from_path(source, cfg, False, {})
    index = SearchIndex(cfg.database_path)
    index.initialize()
    index.upsert_document(doc)
    try:
        assert index.search('VISIBLE')
        assert not index.search('HORSVUE')
    finally:
        index.close()
    assert hashlib.sha256(source.read_bytes()).digest() == avant
