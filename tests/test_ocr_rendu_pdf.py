"""Rendu OCR borné de pages synthétiques, sans accès aux éléments cachés."""
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from seamtech_search.ocr import pipeline


def pdf_couleurs(tmp_path: Path, crop=False, cache=False) -> Path:
    image = tmp_path / 'image.png'
    pixels = Image.new('RGB', (100, 100), 'black')
    for x in range(50, 100):
        for y in range(100):
            pixels.putpixel((x, y), (255, 255, 255))
    pixels.save(image)
    source = tmp_path / 'source.pdf'
    dessin = canvas.Canvas(str(source), pagesize=(100, 100))
    dessin.drawImage(str(image), 0, 0, width=100, height=100)
    if cache:
        dessin.setFillColorRGB(1, 1, 1)
        dessin.rect(0, 0, 50, 100, fill=1, stroke=0)
    dessin.save()
    if not crop:
        return source
    sortie = PdfWriter()
    sortie.add_page(PdfReader(source).pages[0])
    sortie.pages[0].cropbox.lower_left = (50, 0)
    recadre = tmp_path / 'recadre.pdf'
    with recadre.open('wb') as fichier:
        sortie.write(fichier)
    return recadre


@pytest.mark.parametrize('cas', ['crop', 'cache'])
def test_rendu_ocr_montre_seulement_la_vue_visible(tmp_path, cas):
    pdf = pdf_couleurs(tmp_path, crop=cas == 'crop', cache=cas == 'cache')
    original = pdf.read_bytes()
    resultat = pipeline._rendre_page_pdf_borne(pdf, 0, tmp_path, dpi=72, timeout_s=10)
    assert resultat is not None
    with Image.open(resultat) as rendu:
        assert rendu.size == ((50, 100) if cas == 'crop' else (100, 100))
        assert rendu.convert('RGB').getpixel((10, 50)) == (255, 255, 255)
    assert original == pdf.read_bytes()


def test_rendu_ocr_refuse_page_trop_grande_sans_creer_fichier(tmp_path):
    pdf = tmp_path / 'trop-grand.pdf'
    dessin = canvas.Canvas(str(pdf), pagesize=(100000, 100000))
    dessin.drawString(1, 1, 'synthétique')
    dessin.save()
    assert pipeline._rendre_page_pdf_borne(pdf, 0, tmp_path, dpi=300, timeout_s=10) is None
    assert not list(tmp_path.glob('rendu-ocr-*.png'))


def test_rendu_ocr_refuse_pdf_illisible_et_dpi_invalides(tmp_path):
    pdf = tmp_path / 'invalide.pdf'
    pdf.write_bytes(b'pdf synthetique invalide')
    assert pipeline._rendre_page_pdf_borne(pdf, 0, tmp_path, dpi=300, timeout_s=10) is None
    assert pipeline._rendre_page_pdf_borne(pdf, 0, tmp_path, dpi=0, timeout_s=10) is None
    assert not list(tmp_path.glob('rendu-ocr-*.png'))


@pytest.mark.parametrize('cas', ['crop', 'cache'])
def test_ocr_page_composite_utilise_le_rendu_visible_pas_image_brute(tmp_path, monkeypatch, cas):
    pdf = pdf_couleurs(tmp_path, crop=cas == 'crop', cache=cas == 'cache')
    monkeypatch.setattr(pipeline, '_tesseract_version', lambda *args: 'synthétique')
    lectures = []

    def ocr(image_path, *args):
        with Image.open(image_path) as rendu:
            lectures.append((rendu.size, rendu.convert('RGB').getpixel((10, 50))))
        return {'succes': True, 'texte': 'VISIBLE', 'confiance': 90., 'duree_s': .1, 'motif': ''}

    monkeypatch.setattr(pipeline, '_ocr_image_via_tesseract', ocr)
    resultat = pipeline._ocr_page_pdf(pdf, 0, 'fra', 10, 'tesseract', 72, tmp_path)
    assert resultat['succes']
    assert resultat['texte'] == 'VISIBLE'
    assert lectures == [(((50, 100) if cas == 'crop' else (100, 100)), (255, 255, 255))]


def test_echec_rendu_page_composite_ne_lit_jamais_images_brutes(tmp_path, monkeypatch):
    pdf = pdf_couleurs(tmp_path, cache=True)
    monkeypatch.setattr(pipeline, '_tesseract_version', lambda *args: 'synthétique')
    monkeypatch.setattr(pipeline, '_rendre_page_pdf_borne', lambda *args, **kwargs: None)
    lectures = []
    monkeypatch.setattr(pipeline, '_extraire_images_pdf_borne', lambda *args: lectures.append('brut'))
    resultat = pipeline._ocr_page_pdf(pdf, 0, 'fra', 10, 'tesseract', 72, tmp_path)
    assert not resultat['succes']
    assert resultat['motif']
    assert not lectures


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
def test_rendu_ocr_recadre_image_est_redresse_selon_matrice(tmp_path, angle):
    source = pdf_couleurs(tmp_path, crop=True)
    sortie = PdfWriter()
    for page in PdfReader(source).pages:
        page.rotate(angle)
        sortie.add_page(page)
    tourne = tmp_path / 'tourne.pdf'
    with tourne.open('wb') as flux:
        sortie.write(flux)
    resultat = pipeline._rendre_page_pdf_borne(tourne, 0, tmp_path, dpi=72, timeout_s=10)
    assert resultat is not None
    with Image.open(resultat) as rendu:
        assert rendu.size == (50, 100)
        assert rendu.convert('RGB').getpixel((10, 50)) == (255, 255, 255)


@pytest.mark.parametrize('type_restriction', ['annotations', 'calques', 'formulaire'])
def test_rendu_refuse_visibilite_dynamique_non_qualifiee(tmp_path, type_restriction):
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject

    source = pdf_couleurs(tmp_path)
    sortie = PdfWriter()
    sortie.add_page(PdfReader(source).pages[0])
    if type_restriction == 'annotations':
        sortie.pages[0][NameObject('/Annots')] = ArrayObject()
    else:
        cle = '/OCProperties' if type_restriction == 'calques' else '/AcroForm'
        sortie._root_object[NameObject(cle)] = DictionaryObject()
    pdf = tmp_path / 'restrictions.pdf'
    with pdf.open('wb') as flux:
        sortie.write(flux)
    assert pipeline._rendre_page_pdf_borne(pdf, 0, tmp_path, dpi=72, timeout_s=10) is None
    assert not list(tmp_path.glob('rendu-ocr-*.png'))


def test_rendu_timeout_refuse_et_supprime_copie_partielle(tmp_path, monkeypatch):
    import subprocess

    pdf = pdf_couleurs(tmp_path)

    def echouer(*args, **kwargs):
        import json

        commande = json.loads(kwargs['input'])
        Path(commande['sortie']).write_bytes(b'partiel')
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])

    monkeypatch.setattr(pipeline.subprocess, 'run', echouer)
    assert pipeline._rendre_page_pdf_borne(pdf, 0, tmp_path, dpi=72, timeout_s=1) is None
    assert not list(tmp_path.glob('rendu-ocr-*.png'))


def test_rendu_masque_composition_ocr_reel(tmp_path):
    import shutil

    from PIL import ImageDraw, ImageFont

    tesseract = shutil.which('tesseract')
    if not tesseract:
        pytest.skip('CLI Tesseract absent')
    image = tmp_path / 'texte.png'
    dessin = Image.new('RGB', (1000, 200), 'white')
    police = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 32)
    crayon = ImageDraw.Draw(dessin)
    crayon.text((30, 50), 'VISIBLE SYNTHETIQUE', font=police, fill='black')
    crayon.text((550, 50), 'CACHE SYNTHETIQUE', font=police, fill='black')
    dessin.save(image)
    pdf = tmp_path / 'cache.pdf'
    document = canvas.Canvas(str(pdf), pagesize=(500, 150))
    document.drawImage(str(image), 0, 0, width=500, height=100)
    document.setFillColorRGB(1, 1, 1)
    document.rect(250, 0, 250, 100, stroke=0, fill=1)
    document.save()
    source = pdf.read_bytes()
    resultat = pipeline._ocr_page_pdf(pdf, 0, 'eng', 30, tesseract, 300, tmp_path)
    assert resultat['succes']
    assert 'VISIBLE SYNTHETIQUE' in resultat['texte']
    assert 'CACHE SYNTHETIQUE' not in resultat['texte']
    assert source == pdf.read_bytes()


def test_rendu_composite_pendant_ocr_ne_modifie_pas_source(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    pdf = pdf_couleurs(source, cache=True)
    avant = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
    monkeypatch.setattr(pipeline, '_has_command', lambda *args: True)
    monkeypatch.setattr(pipeline, '_tesseract_version', lambda *args: 'synthétique')
    monkeypatch.setattr(pipeline, '_ocr_image_via_tesseract', lambda *args: {
        'succes': True, 'texte': '', 'confiance': None, 'duree_s': 0.1,
        'motif': '', 'moteur': 'tesseract', 'version_moteur': 'synthétique',
    })
    pipeline.ocriser_fichier(pdf)
    apres = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.iterdir()}
    assert avant == apres


def test_image_brute_geante_ne_peut_pas_etre_decompressee_dans_le_parent(tmp_path, monkeypatch):
    from pypdf.generic import NameObject, NumberObject

    source = pdf_couleurs(tmp_path)
    sortie = PdfWriter()
    sortie.add_page(PdfReader(source).pages[0])
    objets = sortie.pages[0]['/Resources']['/XObject'].get_object()
    for objet in objets.values():
        image = objet.get_object()
        image[NameObject('/Width')] = NumberObject(100000)
        image[NameObject('/Height')] = NumberObject(100000)
    pdf = tmp_path / 'image-geante.pdf'
    with pdf.open('wb') as flux:
        sortie.write(flux)
    monkeypatch.setattr(pipeline, '_tesseract_version', lambda *args: 'synthétique')
    monkeypatch.setattr(pipeline, '_rendre_page_pdf_borne', lambda *args, **kwargs: None)
    lectures = []
    monkeypatch.setattr(pipeline, '_extraire_images_pdf_borne', lambda *args: lectures.append('brut'))
    resultat = pipeline._ocr_page_pdf(pdf, 0, 'fra', 10, 'tesseract', 72, tmp_path)
    assert not resultat['succes']
    assert not lectures


def test_ocr_ne_lit_pas_image_inutilisee_dans_ressources(tmp_path, monkeypatch):
    from pypdf.generic import NameObject

    source = pdf_couleurs(tmp_path)
    sortie = PdfWriter()
    sortie.add_page(PdfReader(source).pages[0])
    # Même objet image exposé sous un deuxième nom non dessiné : l'énumération
    # pypdf des ressources ne prouve pas que toutes sont visibles dans la page.
    objets = sortie.pages[0]['/Resources']['/XObject'].get_object()
    objet = next(iter(objets.values()))
    objets[NameObject('/image-non-dessinee')] = objet
    pdf = tmp_path / 'inutilisee.pdf'
    with pdf.open('wb') as flux:
        sortie.write(flux)
    monkeypatch.setattr(pipeline, '_tesseract_version', lambda *args: 'synthétique')
    monkeypatch.setattr(pipeline, '_rendre_page_pdf_borne', lambda *args, **kwargs: None)
    lectures = []
    monkeypatch.setattr(pipeline, '_extraire_images_pdf_borne', lambda *args: lectures.append('brut'))
    resultat = pipeline._ocr_page_pdf(pdf, 0, 'fra', 10, 'tesseract', 72, tmp_path)
    assert not resultat['succes']
    assert not lectures


def test_extraction_image_simple_isolee_et_identique(tmp_path):
    image = tmp_path / 'image.png'
    original = Image.new('RGB', (100, 100), 'white')
    original.putpixel((10, 20), (1, 2, 3))
    original.save(image)
    pdf = tmp_path / 'source.pdf'
    document = canvas.Canvas(str(pdf), pagesize=(100, 100))
    document.drawImage(str(image), 0, 0, width=100, height=100)
    document.save()
    sortie = tmp_path / 'travail'
    sortie.mkdir()
    copies = pipeline._extraire_images_pdf_borne(pdf, 0, sortie, timeout_s=10)
    assert len(copies) == 1
    with Image.open(copies[0]) as copie:
        assert copie.size == (100, 100)
        assert copie.convert('RGB').getpixel((10, 20)) == (1, 2, 3)
    assert copies[0].parent == sortie


def test_extraction_image_timeout_echoue_sans_fuite_de_chemin(tmp_path, monkeypatch):
    import json
    import subprocess

    pdf = tmp_path / 'SECRET-SYNTHETIQUE.pdf'
    pdf.write_bytes(b'pdf synthetique')
    commandes = []

    def echouer(args, **kwargs):
        commandes.append(args)
        chemin = Path(json.loads(kwargs['input'])['sortie'])
        chemin.write_bytes(b'partiel')
        raise subprocess.TimeoutExpired(args, kwargs['timeout'])

    monkeypatch.setattr(pipeline.subprocess, 'run', echouer)
    assert pipeline._extraire_images_pdf_borne(pdf, 0, tmp_path, timeout_s=1) == []
    assert all('SECRET-SYNTHETIQUE' not in str(cmd) for cmd in commandes)
    assert not list(tmp_path.glob('image-ocr-*.png'))


def test_image_brute_est_revalidee_si_pdf_change_apres_precontrole(tmp_path, monkeypatch):
    import shutil

    dossier1 = tmp_path / 'simple'
    dossier2 = tmp_path / 'composite'
    dossier1.mkdir()
    dossier2.mkdir()
    pdf = pdf_couleurs(dossier1)
    composite = pdf_couleurs(dossier2, cache=True)
    verifier = pipeline._page_pdf_vue_integrale_bornee
    appels = []

    def substituer(chemin, numero, timeout):
        verdict = verifier(chemin, numero, timeout)
        assert verdict
        shutil.copyfile(composite, pdf)
        return verdict

    monkeypatch.setattr(pipeline, '_page_pdf_vue_integrale_bornee', substituer)
    monkeypatch.setattr(pipeline, '_tesseract_version', lambda *args: 'synthétique')
    monkeypatch.setattr(pipeline, '_ocr_image_via_tesseract', lambda *args: appels.append('ocr'))
    resultat = pipeline._ocr_page_pdf(pdf, 0, 'fra', 10, 'tesseract', 72, tmp_path)
    assert not resultat['succes']
    assert appels == []
    assert not list(tmp_path.glob('image-ocr-*.png'))


@pytest.mark.parametrize('cle', ['/OCProperties', '/AcroForm'])
def test_image_brute_refuse_configuration_globale_non_qualifiee(tmp_path, monkeypatch, cle):
    from pypdf.generic import DictionaryObject, NameObject

    source = pdf_couleurs(tmp_path)
    sortie = PdfWriter()
    sortie.add_page(PdfReader(source).pages[0])
    sortie._root_object[NameObject(cle)] = DictionaryObject()
    pdf = tmp_path / 'configuration.pdf'
    with pdf.open('wb') as flux:
        sortie.write(flux)
    monkeypatch.setattr(pipeline, '_tesseract_version', lambda *args: 'synthétique')
    monkeypatch.setattr(pipeline, '_rendre_page_pdf_borne', lambda *args, **kwargs: None)
    appels = []
    monkeypatch.setattr(pipeline, '_extraire_images_pdf_borne', lambda *args: appels.append('brut'))
    resultat = pipeline._ocr_page_pdf(pdf, 0, 'fra', 10, 'tesseract', 72, tmp_path)
    assert not resultat['succes']
    assert not appels


def test_precontrole_page_ne_parse_pas_dans_processus_parent(tmp_path, monkeypatch):
    pdf = pdf_couleurs(tmp_path)
    monkeypatch.setattr(pipeline, '_tesseract_version', lambda *args: 'synthétique')

    def interdit(*args):
        raise AssertionError('précontrôle pypdf non isolé')

    monkeypatch.setattr(pipeline, '_page_pdf_vue_integrale', interdit)
    monkeypatch.setattr(pipeline, '_ocr_image_via_tesseract', lambda *args: {
        'texte': 'SYNTHETIQUE', 'confiance': 99.0, 'duree_s': 0.1,
        'succes': True, 'motif': '',
    })
    resultat = pipeline._ocr_page_pdf(pdf, 0, 'fra', 10, 'tesseract', 72, tmp_path)
    assert resultat['succes']
    assert resultat['texte'] == 'SYNTHETIQUE'


def test_precontrole_isole_timeout_ou_erreur_refuse_sans_fuite(tmp_path, monkeypatch):
    import subprocess

    secret = tmp_path / 'SECRET-SYNTHETIQUE.pdf'
    commandes = []

    def echouer(cmd, **kwargs):
        commandes.append(cmd)
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(pipeline.subprocess, 'run', echouer)
    assert not pipeline._page_pdf_vue_integrale_bornee(secret, 0, 1)
    assert commandes
    assert all('SECRET-SYNTHETIQUE' not in str(cmd) for cmd in commandes)
