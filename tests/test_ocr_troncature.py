"""Une limite de texte OCR ne doit jamais être présentée comme complète."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from seamtech_search import extractors
from tests.test_ocr_natif_image import creer_pdf


@pytest.mark.parametrize('moteur', ['image', 'ocrmypdf', 'pipeline', 'mixte'])
@pytest.mark.parametrize('taille', [40, 41])
def test_limite_ocr_est_signalee_sans_perdre_le_texte(tmp_path: Path, monkeypatch, moteur, taille):
    source = tmp_path / ('source.png' if moteur == 'image' else 'source.pdf')
    if moteur == 'mixte':
        source = creer_pdf(tmp_path, ('natif', 'image'))
        natif = 'Texte natif synthetique'
        texte = 'X' * (taille - len(natif) - 1)
        attendu = natif + '\n' + texte
    else:
        source.write_bytes(b'contenu synthetique')
        texte = 'X' * taille
        attendu = texte
    avant = hashlib.sha256(source.read_bytes()).digest()
    monkeypatch.setattr(extractors, '_has_command', lambda cmd: cmd in {'tesseract-factice', 'ocrmypdf-factice'})
    if moteur != 'mixte' and moteur != 'image':
        monkeypatch.setattr(extractors, '_extract_pdf', lambda *_: '[extraction unavailable: no text]')
    if moteur == 'image':
        def tesseract(args, **_kwargs):
            return SimpleNamespace(returncode=0, stdout=texte, stderr='')
        monkeypatch.setattr(extractors.subprocess, 'run', tesseract)
    elif moteur == 'ocrmypdf':
        def ocrmypdf(args, **_kwargs):
            Path(args[args.index('--sidecar') + 1]).write_text(texte, encoding='utf-8')
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        monkeypatch.setattr(extractors.subprocess, 'run', ocrmypdf)
    else:
        from seamtech_search.ocr import pipeline
        monkeypatch.setattr(extractors, '_langue_ocr_disponible', lambda *_: 'eng')
        if moteur == 'mixte':
            resultat_page = {'page': 1, 'page_ocerisee': True, 'texte_ocr': texte}
        else:
            resultat_page = {'page': 0, 'page_ocerisee': True, 'texte_final': texte}
        monkeypatch.setattr(pipeline, 'ocriser_fichier', lambda *_args, **_kwargs: {
            'nb_echecs': 0, 'pages': [resultat_page],
        })
    resultat = extractors.extract_file(
        source, max_chars=40, enable_ocr=True, tesseract_command='tesseract-factice',
        ocrmypdf_command='ocrmypdf-factice' if moteur == 'ocrmypdf' else 'absent-factice',
    )
    assert resultat.text == attendu[:40]
    if taille == 40:
        assert resultat.status == 'extracted'
    else:
        assert resultat.status == 'unavailable'
        assert 'tronqu' in resultat.detail.lower() or 'limit' in resultat.detail.lower()
    assert hashlib.sha256(source.read_bytes()).digest() == avant
