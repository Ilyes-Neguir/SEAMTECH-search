"""Les échecs sur des images d'une même page ne doivent pas passer pour un OCR complet."""

from pathlib import Path

import pytest

from seamtech_search.ocr import pipeline


@pytest.mark.parametrize('image_en_echec', [0, 1])
def test_page_multi_images_refuse_succes_partiel(tmp_path: Path, monkeypatch, image_en_echec: int):
    images = [tmp_path / 'image0.png', tmp_path / 'image1.png']
    monkeypatch.setattr(pipeline, '_extraire_images_pdf_borne', lambda *args: images)
    monkeypatch.setattr(pipeline, '_page_pdf_vue_integrale_bornee', lambda *args: True)
    monkeypatch.setattr(pipeline, '_tesseract_version', lambda *args: 'synthetique')
    appels = []

    def ocr(path, *args):
        appels.append(path)
        echec = path == images[image_en_echec]
        return {'succes': not echec, 'texte': '' if echec else 'texte synthetique',
                'confiance': None if echec else 95.0, 'duree_s': 0.1,
                'motif': 'échec synthétique' if echec else ''}

    monkeypatch.setattr(pipeline, '_ocr_image_via_tesseract', ocr)
    resultat = pipeline._ocr_page_pdf(tmp_path / 'source.pdf', 0, 'fra', 30, 'tesseract', 300, tmp_path)
    assert appels == images
    assert resultat['succes'] is False
    assert resultat['confiance'] is None
    assert resultat['motif']


def test_fichier_multi_pages_ne_declare_pas_extraction_complete_si_une_page_echoue(tmp_path: Path, monkeypatch):
    from seamtech_search import extractors

    monkeypatch.setattr(pipeline, 'ocriser_fichier', lambda *args, **kwargs: {
        'nb_echecs': 1,
        'pages': [
            {'texte_final': 'texte reconnu', 'motif': '', 'page_ocerisee': True},
            {'texte_final': '', 'motif': 'erreur tesseract', 'page_ocerisee': False},
        ],
    })
    texte, motif = extractors._extract_pdf_via_pipeline_ocr(tmp_path / 'source.pdf', 1000, 'tesseract', 30)
    assert texte == ''
    assert motif


def test_pdf_multi_images_echec_ocr_ne_devient_pas_document_extrait(tmp_path, monkeypatch):
    from PIL import Image
    from reportlab.pdfgen import canvas

    from seamtech_search import extractors

    for index, couleur in enumerate(('black', 'grey')):
        Image.new('RGB', (40, 40), couleur).save(tmp_path / f'image{index}.png')
    pdf = tmp_path / 'document.pdf'
    writer = canvas.Canvas(str(pdf))
    writer.drawImage(str(tmp_path / 'image0.png'), 40, 40)
    writer.drawImage(str(tmp_path / 'image1.png'), 100, 40)
    writer.save()
    original = pdf.read_bytes()
    monkeypatch.setattr(pipeline, '_tesseract_version', lambda *args: 'synthetique')
    monkeypatch.setattr(pipeline, '_page_pdf_vue_integrale_bornee', lambda *args: True)
    monkeypatch.setattr(pipeline, '_extraire_images_pdf_borne', lambda *args: [tmp_path / 'image0.png', tmp_path / 'image1.png'])
    monkeypatch.setattr(pipeline, '_has_command', lambda *args: True)
    monkeypatch.setattr(extractors, '_has_command', lambda command: command == 'tesseract-test')
    monkeypatch.setattr(extractors, '_langue_ocr_disponible', lambda *args: 'fra')
    appels = []

    def ocr(path, *args):
        appels.append(path)
        echec = len(appels) == 2
        return {'succes': not echec, 'texte': '' if echec else 'texte synthétique',
                'confiance': None if echec else 95.0, 'duree_s': 0.1,
                'motif': 'erreur synthétique' if echec else ''}

    monkeypatch.setattr(pipeline, '_ocr_image_via_tesseract', ocr)
    resultat = extractors.extract_file(pdf, enable_ocr=True, tesseract_command='tesseract-test')
    assert len(appels) == 2
    assert resultat.status == 'unavailable'
    assert not resultat.text
    assert 'incomplet' in resultat.detail
    assert pdf.read_bytes() == original


def test_pdf_avec_page_native_et_page_image_extrait_les_deux(tmp_path, monkeypatch):
    from PIL import Image
    from reportlab.pdfgen import canvas

    from seamtech_search import extractors

    img = tmp_path / 'image.png'
    Image.new('RGB', (40, 40), 'black').save(img)
    pdf = tmp_path / 'mixte.pdf'
    writer = canvas.Canvas(str(pdf))
    writer.drawString(30, 300, 'Page native synthetique')
    writer.showPage()
    writer.drawImage(str(img), 40, 40)
    writer.save()
    monkeypatch.setattr(extractors, '_has_command', lambda command: command == 'tesseract-test')
    monkeypatch.setattr(extractors, '_langue_ocr_disponible', lambda *args: 'fra')
    monkeypatch.setattr(pipeline, '_has_command', lambda *args: True)
    monkeypatch.setattr(pipeline, '_tesseract_version', lambda *args: 'synthetique')
    monkeypatch.setattr(pipeline, '_ocr_image_via_tesseract', lambda *args: {
        'succes': True, 'texte': 'page image synthetique', 'confiance': 95., 'duree_s': .1, 'motif': ''
    })
    resultat = extractors.extract_file(pdf, enable_ocr=True, tesseract_command='tesseract-test')
    assert 'Page native' in resultat.text
    assert 'page image synthetique' in resultat.text


def test_ocrmypdf_sans_texte_ne_peut_pas_etre_marque_extrait(tmp_path, monkeypatch):
    from seamtech_search import extractors

    pdf = tmp_path / 'document.pdf'
    pdf.write_bytes(b'pdf synthetique')
    monkeypatch.setattr(extractors, '_extract_pdf', lambda *args: '[extraction unavailable: texte absent]')
    monkeypatch.setattr(extractors, '_has_command', lambda command: command == 'ocrmypdf-test')
    monkeypatch.setattr(extractors, '_extract_with_ocrmypdf', lambda *args: '  \n')
    resultat = extractors.extract_file(pdf, enable_ocr=True, ocrmypdf_command='ocrmypdf-test')
    assert resultat.status == 'unavailable'
    assert not resultat.text


def test_tesseract_image_sans_texte_ne_peut_pas_etre_marque_extrait(tmp_path, monkeypatch):
    from seamtech_search import extractors

    image = tmp_path / 'document.png'
    image.write_bytes(b'image synthetique')
    monkeypatch.setattr(extractors, '_has_command', lambda command: command == 'tesseract-test')
    monkeypatch.setattr(extractors, '_extract_with_tesseract', lambda *args: '  \n')
    resultat = extractors.extract_file(image, enable_ocr=True, tesseract_command='tesseract-test')
    assert resultat.status == 'unavailable'
    assert not resultat.text


def test_rapport_image_ocr_vide_signale_absence_de_texte(tmp_path, monkeypatch):
    image = tmp_path / 'image.png'
    image.write_bytes(b'image synthetique')
    monkeypatch.setattr(pipeline, '_has_command', lambda *args: True)
    monkeypatch.setattr(pipeline, '_ocr_image_via_tesseract', lambda *args: {
        'succes': True, 'texte': ' \n', 'confiance': None, 'duree_s': 0.1,
        'motif': '', 'moteur': 'tesseract', 'version_moteur': 'synthetique',
    })
    rapport = pipeline.ocriser_fichier(image)
    assert rapport['nb_pages_ocerisees'] == 0
    assert rapport['nb_echecs'] == 1
    assert rapport['pages'][0]['motif']


def test_pdf_multi_pages_ocr_vide_ne_peut_pas_passer_pour_complet(tmp_path, monkeypatch):
    from reportlab.pdfgen import canvas

    from seamtech_search import extractors

    pdf = tmp_path / 'document.pdf'
    writer = canvas.Canvas(str(pdf))
    writer.showPage()
    writer.showPage()
    writer.save()
    monkeypatch.setattr(pipeline, '_texte_natif_par_page_pdf', lambda *args: ['', ''])
    monkeypatch.setattr(pipeline, '_has_command', lambda *args: True)
    monkeypatch.setattr(pipeline, '_tesseract_version', lambda *args: 'synthetique')
    monkeypatch.setattr(extractors, '_langue_ocr_disponible', lambda *args: 'eng')

    def ocr(_pdf, numero, *args, **kwargs):
        return {'texte': 'texte synthetique' if numero == 0 else '', 'confiance': None,
                'duree_s': 0.1, 'succes': True, 'motif': '', 'moteur': 'tesseract',
                'version_moteur': 'synthetique'}

    monkeypatch.setattr(pipeline, '_ocr_page_pdf', ocr)
    rapport = pipeline.ocriser_fichier(pdf)
    assert rapport['nb_pages_ocerisees'] == 1
    assert rapport['nb_echecs'] == 1
    assert rapport['pages'][1]['motif']
    texte, motif = extractors._extract_pdf_via_pipeline_ocr(pdf, 1000, 'tesseract', 30)
    assert not texte
    assert 'incomplet' in motif
