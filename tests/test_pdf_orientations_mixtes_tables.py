"""Tables et zones PDF synthétiques, à deux orientations sur une même page."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pdfplumber
import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from seamtech_search.extractors import extract_text
from seamtech_search.fiches.extraction import _zone_des_mots, analyser_pdf
from seamtech_search.pdf_orientation import boite_vue


def _table(pdf, gauche: float, bas: float, rows: list[tuple[str, str]]) -> None:
    for x in (gauche, gauche + 100, gauche + 200):
        pdf.line(x, bas, x, bas + 50)
    for y in (bas, bas + 25, bas + 50):
        pdf.line(gauche, y, gauche + 200, y)
    for i, (a, b) in enumerate(rows):
        y = bas + 33 - i * 25
        pdf.drawString(gauche + 5, y, a)
        pdf.drawString(gauche + 105, y, b)


def _fabriquer(path: Path, angle: int, crop: bool) -> Path:
    base = path.with_name('source.pdf')
    pdf = canvas.Canvas(str(base), pagesize=(595, 842))
    _table(pdf, 35, 600, [('MESURE', 'VALEUR'), ('GUINDANT', '10,50')])
    pdf.saveState()
    pdf.translate(420, 300)
    pdf.rotate(90)
    _table(pdf, 0, 0, [('TENSION', 'UNITE'), ('CORDAGE', '15,25')])
    pdf.restoreState()
    pdf.drawString(500, 740, 'HORSVUE')
    pdf.save()
    reader = PdfReader(base)
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    page = writer.pages[0]
    if crop:
        page.cropbox.lower_left = (10, 100)
        page.cropbox.upper_right = (450, 700)
    page.rotate(angle)
    with path.open('wb') as flux:
        writer.write(flux)
    return path


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
@pytest.mark.parametrize('crop', [False, True])
def test_mixte_tables_mots_zones_et_vue(tmp_path: Path, angle: int, crop: bool) -> None:
    source = _fabriquer(tmp_path / 'mixte.pdf', angle, crop)
    avant = hashlib.sha256(source.read_bytes()).digest()
    resultat = analyser_pdf(source)[0]
    assert resultat.orientations_mixtes  # la voie métier reste en revue
    mots = [mot for ligne in resultat.lignes for mot in ligne.mots]
    chaines = [[mot.texte for mot in ligne.mots] for ligne in resultat.lignes]
    for rang in (['MESURE', 'VALEUR'], ['GUINDANT', '10,50'], ['TENSION', 'UNITE'], ['CORDAGE', '15,25']):
        assert rang in chaines
    for table in ([['MESURE', 'VALEUR'], ['GUINDANT', '10,50']],
                  [['TENSION', 'UNITE'], ['CORDAGE', '15,25']]):
        assert table in resultat.tableaux
    if crop:
        assert all(mot.texte != 'HORSVUE' for mot in mots)
    else:
        assert any(mot.texte == 'HORSVUE' for mot in mots)

    with pdfplumber.open(source) as doc:
        page = doc.pages[0]
        vue = boite_vue(page)
        caracteres = page.chars
        contenu = ''.join(caractere['text'] for caractere in caracteres)
        for terme in ('MESURE', 'TENSION', 'CORDAGE'):
            position = contenu.index(terme)
            groupe = caracteres[position:position + len(terme)]
            attendu = (min(c['x0'] for c in groupe) - vue[0], max(c['x1'] for c in groupe) - vue[0],
                       min(c['top'] for c in groupe) - vue[1], max(c['bottom'] for c in groupe) - vue[1])
            zone = _zone_des_mots([next(m for m in mots if m.texte == terme)])
            assert (zone.x0, zone.x1, zone.y0, zone.y1) == pytest.approx(attendu, abs=0.01)

    texte = extract_text(source, 100_000)
    assert all(terme in texte for terme in ('MESURE', 'GUINDANT', 'TENSION', 'CORDAGE'))
    if crop:
        assert 'HORSVUE' not in texte
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
def test_mixte_aretes_composites_reste_confinees_a_la_vue(tmp_path: Path, angle: int) -> None:
    from seamtech_search.pdf_orientation import lectures_mixtes_verifiees

    brut = tmp_path / 'brut.pdf'
    pdf = canvas.Canvas(str(brut), pagesize=(595, 842))
    pdf.drawString(30, 700, 'HORIZONTAL')
    pdf.saveState()
    pdf.translate(200, 450)
    pdf.rotate(90)
    pdf.drawString(0, 0, 'VERTICAL')
    pdf.restoreState()
    chemin = pdf.beginPath()
    chemin.moveTo(20, 650)
    chemin.lineTo(500, 650)
    chemin.lineTo(500, 550)
    chemin.lineTo(400, 550)
    chemin.lineTo(400, 650)
    pdf.drawPath(chemin, stroke=1)
    pdf.save()
    writer = PdfWriter()
    writer.add_page(PdfReader(brut).pages[0])
    writer.pages[0].cropbox.upper_right = (300, 842)
    writer.pages[0].rotate(angle)
    source = tmp_path / 'mixte.pdf'
    with source.open('wb') as flux:
        writer.write(flux)
    with pdfplumber.open(source) as document:
        with lectures_mixtes_verifiees(document.pages[0]) as lectures:
            assert lectures is not None
            for lecture, _ in lectures:
                x0, y0, x1, y1 = lecture.bbox
                assert lecture.edges
                for edge in lecture.edges:
                    assert x0 - 1e-7 <= edge['x0'] <= edge['x1'] <= x1 + 1e-7
                    assert y0 - 1e-7 <= edge['top'] <= edge['bottom'] <= y1 + 1e-7


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
def test_quatre_orientations_simultanees_restituent_mots_et_zones(tmp_path: Path, angle: int) -> None:
    brut = tmp_path / 'brut.pdf'
    pdf = canvas.Canvas(str(brut), pagesize=(595, 842))
    elements = [('ALPHA', 0, 50, 750), ('BRAVO', 90, 180, 250),
                ('CHARLIE', 180, 450, 610), ('DELTA', 270, 520, 650)]
    for terme, orientation, x, y in elements:
        pdf.saveState()
        pdf.translate(x, y)
        pdf.rotate(orientation)
        pdf.drawString(0, 0, terme)
        pdf.restoreState()
    pdf.save()
    writer = PdfWriter()
    writer.add_page(PdfReader(brut).pages[0])
    writer.pages[0].rotate(angle)
    source = tmp_path / 'quatre.pdf'
    with source.open('wb') as flux:
        writer.write(flux)
    avant = hashlib.sha256(source.read_bytes()).digest()
    page_analysee = analyser_pdf(source)[0]
    assert page_analysee.orientations_mixtes
    mots = [mot for ligne in page_analysee.lignes for mot in ligne.mots]
    assert {mot.texte for mot in mots} == {element[0] for element in elements}
    with pdfplumber.open(source) as document:
        page = document.pages[0]
        vue = boite_vue(page)
        contenu = ''.join(c['text'] for c in page.chars)
        for terme, *_ in elements:
            pos = contenu.index(terme)
            groupe = page.chars[pos:pos + len(terme)]
            attendu = (min(c['x0'] for c in groupe) - vue[0], max(c['x1'] for c in groupe) - vue[0],
                       min(c['top'] for c in groupe) - vue[1], max(c['bottom'] for c in groupe) - vue[1])
            zone = _zone_des_mots([next(m for m in mots if m.texte == terme)])
            assert (zone.x0, zone.x1, zone.y0, zone.y1) == pytest.approx(attendu, abs=0.01)
    index = extract_text(source, 100_000)
    assert all(terme in index for terme, *_ in elements)
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize('matrice', [(1, 0, .2, 1, 0, 0), (-1, 0, 0, 1, 0, 0),
                                    (1, 1, -1, 1, 0, 0), (0, 0, 0, 1, 0, 0)])
def test_matrice_inexploitable_ne_donne_pas_une_lecture_mixte_certifiee(tmp_path: Path, matrice) -> None:
    from seamtech_search.pdf_orientation import lectures_mixtes_verifiees

    source = _fabriquer(tmp_path / 'mixte.pdf', 0, False)
    avant = hashlib.sha256(source.read_bytes()).digest()
    with pdfplumber.open(source) as document:
        page = document.pages[0]
        page.chars[0]['matrix'] = matrice
        with lectures_mixtes_verifiees(page) as lectures:
            assert lectures is None
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
def test_mixte_media_decale_crop_et_userunit_zones(tmp_path: Path, angle: int) -> None:
    from pypdf.generic import NameObject, NumberObject

    base = _fabriquer(tmp_path / 'base.pdf', 0, False)
    writer = PdfWriter()
    writer.add_page(PdfReader(base).pages[0])
    page = writer.pages[0]
    page.mediabox.lower_left = (10, 20)
    page.mediabox.upper_right = (605, 862)
    page.cropbox.lower_left = (20, 100)
    page.cropbox.upper_right = (450, 700)
    page[NameObject('/UserUnit')] = NumberObject(2)
    page.rotate(angle)
    source = tmp_path / 'decale.pdf'
    with source.open('wb') as flux:
        writer.write(flux)
    avant = hashlib.sha256(source.read_bytes()).digest()
    resultat = analyser_pdf(source)[0]
    mots = [mot for ligne in resultat.lignes for mot in ligne.mots]
    assert resultat.orientations_mixtes
    assert all(any(mot.texte == texte for mot in mots) for texte in ('MESURE', 'TENSION', 'CORDAGE'))
    assert all(mot.texte != 'HORSVUE' for mot in mots)
    with pdfplumber.open(source) as document:
        page = document.pages[0]
        vue = boite_vue(page)
        caracteres = page.chars
        contenu = ''.join(c['text'] for c in caracteres)
        for terme in ('MESURE', 'TENSION', 'CORDAGE'):
            debut = contenu.index(terme)
            groupe = caracteres[debut:debut + len(terme)]
            attendu = ((min(c['x0'] for c in groupe) - vue[0]) * 2,
                       (max(c['x1'] for c in groupe) - vue[0]) * 2,
                       (min(c['top'] for c in groupe) - vue[1]) * 2,
                       (max(c['bottom'] for c in groupe) - vue[1]) * 2)
            zone = _zone_des_mots([next(m for m in mots if m.texte == terme)])
            assert (zone.x0, zone.x1, zone.y0, zone.y1) == pytest.approx(attendu, abs=0.01)
    assert hashlib.sha256(source.read_bytes()).digest() == avant


def test_mixte_ferme_vues_privees_meme_si_consommateur_echoue(tmp_path: Path, monkeypatch) -> None:
    from pdfplumber.page import FilteredPage

    from seamtech_search.pdf_orientation import lectures_mixtes_verifiees

    source = _fabriquer(tmp_path / 'mixte.pdf', 90, True)
    avant = hashlib.sha256(source.read_bytes()).digest()
    fermees = []
    fermer = FilteredPage.close

    def suivre(page):
        fermees.append(page)
        return fermer(page)

    monkeypatch.setattr(FilteredPage, 'close', suivre)
    with pdfplumber.open(source) as document:
        originale = document.pages[0]
        attributs = dict(originale.page_obj.attrs)
        with pytest.raises(RuntimeError, match='panne synthétique'):
            with lectures_mixtes_verifiees(originale) as lectures:
                assert lectures is not None and len(lectures) == 2
                raise RuntimeError('panne synthétique')
        assert len(fermees) == 2 and len({id(p) for p in fermees}) == 2
        assert originale.page_obj.attrs == attributs and originale.chars
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize('difference', ['texte', 'boite'])
def test_mixte_refuse_projection_si_glyphes_ne_correspondent_pas(tmp_path: Path, difference: str) -> None:
    from seamtech_search.pdf_orientation import lectures_mixtes_verifiees

    source = _fabriquer(tmp_path / 'mixte.pdf', 0, False)
    avant = hashlib.sha256(source.read_bytes()).digest()
    with pdfplumber.open(source) as document:
        originale = document.pages[0]
        if difference == 'texte':
            originale.chars[0]['text'] = 'X'
        else:
            originale.chars[0]['x0'] += 10
        with lectures_mixtes_verifiees(originale) as lectures:
            assert lectures is None
    assert hashlib.sha256(source.read_bytes()).digest() == avant
