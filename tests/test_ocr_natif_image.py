"""Contrat des PDF mélangeant texte natif et pages image synthétiques."""
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from seamtech_search import extractors
from seamtech_search.ocr import pipeline


def creer_pdf(tmp_path: Path, ordre: tuple[str, ...], angle: int = 0) -> Path:
    image = tmp_path / 'img.png'
    Image.new('RGB', (80, 60), 'black').save(image)
    initial = tmp_path / 'initial.pdf'
    document = canvas.Canvas(str(initial))
    for element in ordre:
        if element in ('natif', 'logo'):
            document.drawString(40, 500, 'Texte natif synthetique')
        if element in ('image', 'logo'):
            document.drawImage(str(image), 40, 200)
        document.showPage()
    document.save()
    if not angle:
        return initial
    resultat = tmp_path / 'tourne.pdf'
    sortie = PdfWriter()
    for page in PdfReader(initial).pages:
        page.rotate(angle)
        sortie.add_page(page)
    with resultat.open('wb') as fichier:
        sortie.write(fichier)
    return resultat


def installer_ocr(monkeypatch, *, succes=True):
    appels = []
    monkeypatch.setattr(extractors, '_has_command', lambda command: command == 'tesseract-test')
    monkeypatch.setattr(extractors, '_langue_ocr_disponible', lambda *args: 'fra')
    monkeypatch.setattr(pipeline, '_has_command', lambda *args: True)
    monkeypatch.setattr(pipeline, '_tesseract_version', lambda *args: 'synthetique')

    def ocr(path, *args):
        appels.append(path)
        return {'succes': succes, 'texte': 'Texte image synthetique' if succes else '',
                'confiance': 96.0 if succes else None, 'duree_s': 0.1,
                'motif': '' if succes else 'echec synthetique'}

    monkeypatch.setattr(pipeline, '_ocr_image_via_tesseract', ocr)
    return appels


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
@pytest.mark.parametrize('ordre', [('natif', 'image'), ('image', 'natif')])
def test_pdf_mixte_preserve_ordre_natif_et_ocr(tmp_path, monkeypatch, angle, ordre):
    document = creer_pdf(tmp_path, ordre, angle)
    original = document.read_bytes()
    appels = installer_ocr(monkeypatch)
    resultat = extractors.extract_file(document, enable_ocr=True, tesseract_command='tesseract-test')
    assert resultat.status == 'extracted'
    termes = {'natif': 'Texte natif synthetique', 'image': 'Texte image synthetique'}
    assert all(termes[e] in resultat.text for e in ordre)
    assert resultat.text.index(termes[ordre[0]]) < resultat.text.index(termes[ordre[1]])
    assert len(appels) == 1
    assert document.read_bytes() == original


def test_page_natif_avec_image_n_est_pas_ocrisee(tmp_path, monkeypatch):
    document = creer_pdf(tmp_path, ('logo', 'natif'))
    appels = installer_ocr(monkeypatch)
    avant = extractors.extract_file(document, enable_ocr=False)
    apres = extractors.extract_file(document, enable_ocr=True, tesseract_command='tesseract-test')
    assert avant == apres
    assert not appels


def test_pdf_mixte_page_blanche_ignoree_et_ocr_des_images(tmp_path, monkeypatch):
    document = creer_pdf(tmp_path, ('vide', 'natif', 'image', 'vide'))
    appels = installer_ocr(monkeypatch)
    resultat = extractors.extract_file(document, enable_ocr=True, tesseract_command='tesseract-test')
    assert resultat.status == 'extracted'
    assert 'Texte natif synthetique' in resultat.text and 'Texte image synthetique' in resultat.text
    assert len(appels) == 1


def test_pdf_mixte_echec_ocr_signale_incomplet(tmp_path, monkeypatch):
    document = creer_pdf(tmp_path, ('natif', 'image'))
    appels = installer_ocr(monkeypatch, succes=False)
    resultat = extractors.extract_file(document, enable_ocr=True, tesseract_command='tesseract-test')
    assert len(appels) == 1
    assert resultat.status != 'extracted'
    assert resultat.detail


def test_pdf_mixte_ocr_absent_ne_se_declare_pas_complet(tmp_path, monkeypatch):
    document = creer_pdf(tmp_path, ('natif', 'image'))
    monkeypatch.setattr(extractors, '_has_command', lambda command: False)
    resultat = extractors.extract_file(document, enable_ocr=True)
    assert resultat.status != 'extracted'
    assert resultat.detail


def test_pdf_mixte_texte_natif_conserve_si_ocr_echoue(tmp_path, monkeypatch):
    document = creer_pdf(tmp_path, ('natif', 'image'))
    installer_ocr(monkeypatch, succes=False)
    resultat = extractors.extract_file(document, enable_ocr=True, tesseract_command='tesseract-test')
    assert resultat.status == 'unavailable'
    assert 'Texte natif synthetique' in resultat.text


def test_inspection_mixte_impossible_ne_signale_pas_document_complet(tmp_path, monkeypatch):
    document = creer_pdf(tmp_path, ('natif', 'image'))
    monkeypatch.setattr(extractors, '_extract_pdf', lambda *args: 'Texte natif synthetique')

    def echec(*args):
        raise RuntimeError('SECRET SYNTHETIQUE')

    monkeypatch.setattr(extractors, '_texte_page_plumber', echec)
    resultat = extractors.extract_file(document, enable_ocr=True)
    assert resultat.status == 'unavailable'
    assert resultat.text == 'Texte natif synthetique'
    assert 'SECRET SYNTHETIQUE' not in resultat.detail


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
def test_pdf_mixte_tesseract_cli_reel(tmp_path, monkeypatch, angle):
    import shutil

    from PIL import ImageDraw, ImageFont

    tesseract = shutil.which('tesseract')
    if not tesseract:
        pytest.skip('CLI Tesseract absent')
    image = tmp_path / 'scan.png'
    dessin = Image.new('RGB', (1000, 220), 'white')
    police = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 42)
    ImageDraw.Draw(dessin).text((30, 50), 'DOCUMENT SCAN SYNTHETIQUE', font=police, fill='black')
    dessin.save(image)
    source = tmp_path / 'base.pdf'
    document = canvas.Canvas(str(source))
    document.drawString(40, 500, 'Texte natif synthetique')
    document.showPage()
    document.drawImage(str(image), 40, 200, width=500, height=110)
    document.save()
    tourne = tmp_path / 'tourne.pdf'
    sortie = PdfWriter()
    for page in PdfReader(source).pages:
        page.rotate(angle)
        sortie.add_page(page)
    with tourne.open('wb') as flux:
        sortie.write(flux)
    original = tourne.read_bytes()
    monkeypatch.setattr(extractors, '_langue_ocr_disponible', lambda *args: 'eng')
    resultat = extractors.extract_file(tourne, enable_ocr=True, tesseract_command=tesseract)
    assert resultat.status == 'extracted'
    assert 'Texte natif synthetique' in resultat.text
    assert 'DOCUMENT SCAN SYNTHETIQUE' in resultat.text
    assert tourne.read_bytes() == original


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
def test_ocr_mixte_n_indexe_pas_le_texte_image_hors_crop(tmp_path, monkeypatch, angle):
    import shutil

    from PIL import ImageDraw, ImageFont

    tesseract = shutil.which('tesseract')
    if not tesseract:
        pytest.skip('CLI Tesseract absent')
    image = tmp_path / 'scan.png'
    dessin = Image.new('RGB', (1000, 200), 'white')
    police = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 32)
    crayon = ImageDraw.Draw(dessin)
    crayon.text((30, 50), 'VISIBLE SYNTHETIQUE', font=police, fill='black')
    crayon.text((550, 50), 'CACHE SYNTHETIQUE', font=police, fill='black')
    dessin.save(image)
    source = tmp_path / 'source.pdf'
    document = canvas.Canvas(str(source), pagesize=(500, 200))
    document.drawString(10, 150, 'Texte natif synthetique')
    document.showPage()
    document.drawImage(str(image), 0, 0, width=500, height=100)
    document.save()
    sortie = PdfWriter()
    for page in PdfReader(source).pages:
        sortie.add_page(page)
    sortie.pages[1].cropbox.upper_right = (250, 200)
    sortie.pages[1].rotate(angle)
    pdf = tmp_path / 'recadre.pdf'
    with pdf.open('wb') as f:
        sortie.write(f)
    monkeypatch.setattr(extractors, '_langue_ocr_disponible', lambda *args: 'eng')
    resultat = extractors.extract_file(pdf, enable_ocr=True, tesseract_command=tesseract)
    assert resultat.status == 'extracted'
    assert 'VISIBLE SYNTHETIQUE' in resultat.text
    assert 'CACHE SYNTHETIQUE' not in resultat.text


def test_ocr_mixte_crop_ne_revele_pas_texte_cache(tmp_path, monkeypatch):
    image = tmp_path / 'scan.png'
    Image.new('RGB', (100, 100), 'black').save(image)
    source = tmp_path / 'source.pdf'
    document = canvas.Canvas(str(source), pagesize=(500, 200))
    document.drawString(10, 150, 'Texte natif synthetique')
    document.showPage()
    document.drawImage(str(image), 0, 0, width=500, height=100)
    document.save()
    sortie = PdfWriter()
    for page in PdfReader(source).pages:
        sortie.add_page(page)
    sortie.pages[1].cropbox.upper_right = (250, 200)
    pdf = tmp_path / 'recadre.pdf'
    with pdf.open('wb') as flux:
        sortie.write(flux)
    monkeypatch.setattr(pipeline, '_rendre_page_pdf_borne', lambda *args, **kwargs: None)
    appels = installer_ocr(monkeypatch)
    resultat = extractors.extract_file(pdf, enable_ocr=True, tesseract_command='tesseract-test')
    assert resultat.status == 'unavailable'
    assert 'Texte natif synthetique' in resultat.text
    assert not appels


def test_ocr_mixte_texte_image_masque_ne_fuit_pas(tmp_path, monkeypatch):
    """Un cache opaque ne doit jamais être contourné par l'extraction d'images brutes."""
    image = tmp_path / 'scan.png'
    Image.new('RGB', (100, 100), 'black').save(image)
    source = tmp_path / 'source.pdf'
    document = canvas.Canvas(str(source), pagesize=(500, 200))
    document.drawString(10, 150, 'Texte natif synthetique')
    document.showPage()
    document.drawImage(str(image), 0, 0, width=500, height=100)
    document.setFillColorRGB(1, 1, 1)
    document.rect(0, 0, 500, 100, fill=1, stroke=0)
    document.save()
    monkeypatch.setattr(pipeline, '_rendre_page_pdf_borne', lambda *args, **kwargs: None)
    appels = installer_ocr(monkeypatch)
    # Si l'image brute est transmise à OCR, elle peut révéler un texte caché.
    resultat = extractors.extract_file(source, enable_ocr=True, tesseract_command='tesseract-test')
    assert resultat.status == 'unavailable'
    assert not appels


def test_ocr_mixte_refuse_image_debordant_mediabox(tmp_path, monkeypatch):
    image = tmp_path / 'scan.png'
    Image.new('RGB', (100, 100), 'black').save(image)
    source = tmp_path / 'source.pdf'
    document = canvas.Canvas(str(source), pagesize=(500, 200))
    document.drawString(10, 150, 'Texte natif synthetique')
    document.showPage()
    document.drawImage(str(image), 0, 0, width=800, height=100)
    document.save()
    monkeypatch.setattr(pipeline, '_rendre_page_pdf_borne', lambda *args, **kwargs: None)
    appels = installer_ocr(monkeypatch)
    resultat = extractors.extract_file(source, enable_ocr=True, tesseract_command='tesseract-test')
    assert resultat.status == 'unavailable'
    assert not appels


def test_indexation_pdf_mixte_moteur_reel_et_recherche(tmp_path):
    import shutil

    from PIL import ImageDraw, ImageFont

    from seamtech_search.config import AppConfig
    from seamtech_search.crawler import crawl
    from seamtech_search.indexer import SearchIndex

    tesseract = shutil.which('tesseract')
    if not tesseract:
        pytest.skip('CLI Tesseract absent')
    source = tmp_path / 'source'
    source.mkdir()
    image = tmp_path / 'scan.png'
    dessin = Image.new('RGB', (1000, 220), 'white')
    police = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 42)
    ImageDraw.Draw(dessin).text((30, 50), 'DOCUMENT SCAN SYNTHETIQUE', font=police, fill='black')
    dessin.save(image)
    pdf = source / 'dossier.pdf'
    document = canvas.Canvas(str(pdf))
    document.drawString(40, 500, 'Texte natif synthetique')
    document.showPage()
    document.drawImage(str(image), 40, 200, width=500, height=110)
    document.save()
    original = pdf.read_bytes()
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db', min_free_bytes=0,
                       enable_ocr=True, tesseract_command=tesseract, ocrmypdf_command='absent-synthetique')
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    documents = list(crawl(config))
    entree = next(item for item in documents if not item.is_dir)
    assert entree.extraction_status == 'extracted'
    assert 'Texte natif synthetique' in entree.text
    assert 'DOCUMENT SCAN SYNTHETIQUE' in entree.text
    index.upsert_documents(documents)
    resultat = index.search('SYNTHETIQUE')
    assert any(item['name'] == 'dossier.pdf' for item in resultat)
    assert pdf.read_bytes() == original


def test_ocr_mixte_refuse_image_sur_calque_optionnel(tmp_path, monkeypatch):
    from pypdf.generic import DictionaryObject, NameObject

    source = creer_pdf(tmp_path, ('natif', 'image'))
    lecteur = PdfReader(source)
    sortie = PdfWriter()
    for page in lecteur.pages:
        sortie.add_page(page)
    objets = sortie.pages[1]['/Resources']['/XObject'].get_object()
    for objet in objets.values():
        objet.get_object()[NameObject('/OC')] = DictionaryObject()
    pdf = tmp_path / 'calque.pdf'
    with pdf.open('wb') as flux:
        sortie.write(flux)
    monkeypatch.setattr(pipeline, '_rendre_page_pdf_borne', lambda *args, **kwargs: None)
    appels = installer_ocr(monkeypatch)
    resultat = extractors.extract_file(pdf, enable_ocr=True, tesseract_command='tesseract-test')
    assert resultat.status == 'unavailable'
    assert not appels


def test_ocr_mixte_refuse_page_avec_annotations_non_analysees(tmp_path, monkeypatch):
    from pypdf.generic import ArrayObject, NameObject

    source = creer_pdf(tmp_path, ('natif', 'image'))
    sortie = PdfWriter()
    for page in PdfReader(source).pages:
        sortie.add_page(page)
    sortie.pages[1][NameObject('/Annots')] = ArrayObject()
    pdf = tmp_path / 'annote.pdf'
    with pdf.open('wb') as flux:
        sortie.write(flux)
    monkeypatch.setattr(pipeline, '_rendre_page_pdf_borne', lambda *args, **kwargs: None)
    appels = installer_ocr(monkeypatch)
    resultat = extractors.extract_file(pdf, enable_ocr=True, tesseract_command='tesseract-test')
    assert resultat.status == 'unavailable'
    assert not appels


@pytest.mark.parametrize('angle', [90, 180, 270])
def test_rendu_ocr_image_deja_compensee_garde_orientation(tmp_path, angle):
    import shutil

    from PIL import ImageDraw, ImageFont

    tesseract = shutil.which('tesseract')
    if not tesseract:
        pytest.skip('CLI Tesseract absent')
    image = tmp_path / 'scan.png'
    dessin = Image.new('RGB', (1000, 200), 'white')
    police = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 32)
    ImageDraw.Draw(dessin).text((30, 50), 'VISIBLE SYNTHETIQUE', font=police, fill='black')
    dessin.save(image)
    source = tmp_path / 'source.pdf'
    document = canvas.Canvas(str(source), pagesize=(500, 200))
    document.drawImage(str(image), 0, 0, width=500, height=100)
    document.save()
    sortie = PdfWriter()
    sortie.add_page(PdfReader(source).pages[0])
    page = sortie.pages[0]
    page.rotate(angle)
    page.transfer_rotation_to_content()
    page.rotate(-angle)
    pdf = tmp_path / 'compense.pdf'
    with pdf.open('wb') as flux:
        sortie.write(flux)
    original = pdf.read_bytes()
    resultat = pipeline._ocr_page_pdf(pdf, 0, 'eng', 30, tesseract, 300, tmp_path)
    assert resultat['succes']
    assert 'VISIBLE SYNTHETIQUE' in resultat['texte']
    assert pdf.read_bytes() == original
