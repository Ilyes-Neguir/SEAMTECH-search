"""Une orientation non vérifiée ne doit jamais devenir une extraction certifiée."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from seamtech_search.config import AppConfig
from seamtech_search.extractors import extract_file
from seamtech_search.fiches.extraction import analyser_pdf


def _pdf(path: Path, cas: str) -> Path:
    pdf = canvas.Canvas(str(path), pagesize=(595, 842))
    if not cas.startswith('homogene_'):
        pdf.drawString(30, 750, 'TEXTEBASE')
    pdf.saveState()
    pdf.translate(400, 500)
    if cas == 'oblique':
        pdf.rotate(30)
    elif cas == 'miroir':
        pdf.scale(-1, 1)
    elif cas == 'cisaillement':
        pdf.transform(1, 0, 0.4, 1, 0, 0)
    elif cas == 'mixte_verification_refusee':
        pdf.rotate(90)
    elif cas.startswith('homogene_'):
        pdf.rotate(int(cas.removeprefix('homogene_')))
    else:
        raise AssertionError('cas non prévu')
    pdf.drawString(0, 0, 'ORIENTATION')
    pdf.restoreState()
    pdf.save()
    return path


@pytest.mark.parametrize('cas', ['oblique', 'miroir', 'cisaillement'])
@pytest.mark.parametrize('ocr', [False, True])
def test_orientation_non_verifiee_reste_partielle_et_a_revoir(tmp_path, monkeypatch, cas, ocr):
    source = _pdf(tmp_path / 'source.pdf', cas)
    avant = hashlib.sha256(source.read_bytes()).digest()
    appels_ocr = []
    monkeypatch.setattr('seamtech_search.extractors._has_command', lambda commande: True)
    monkeypatch.setattr('seamtech_search.extractors._extract_with_ocrmypdf',
                        lambda *args: appels_ocr.append('ocrmypdf') or 'faux succès')
    monkeypatch.setattr('seamtech_search.extractors._extract_pdf_via_pipeline_ocr',
                        lambda *args: (appels_ocr.append('tesseract') or 'faux succès', ''))
    resultat = extract_file(source, enable_ocr=ocr)
    assert resultat.status == 'unavailable'
    assert 'orientation' in resultat.detail.lower()
    assert resultat.text.strip()
    assert 'TEXTEBASE' in resultat.text
    assert appels_ocr == []
    page = analyser_pdf(source)[0]
    assert page.orientations_non_verifiees
    from seamtech_search.fiches.extraction import _signaler_orientations_mixtes
    from seamtech_search.fiches.modeles import FicheExtraite

    fiche = FicheExtraite()
    _signaler_orientations_mixtes(fiche, [page])
    assert any(anomalie.code == 'orientation_pdf_non_verifiee' and anomalie.gravite == 'forte'
               for anomalie in fiche.anomalies)
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize('angle', [90, 180, 270])
@pytest.mark.parametrize('ocr', [False, True])
def test_orientation_homogene_verifiee_reste_lisible_et_reprojete_les_zones(tmp_path, angle, ocr):
    import pdfplumber

    from seamtech_search.fiches.extraction import _zone_des_mots
    from seamtech_search.fiches.gabarit_brouillon import _extraire_mots_avec_bbox
    from seamtech_search.pdf_orientation import boite_vue, lectures_mixtes_verifiees

    source = _pdf(tmp_path / 'source.pdf', f'homogene_{angle}')
    avant = hashlib.sha256(source.read_bytes()).digest()
    resultat = extract_file(source, enable_ocr=ocr)
    assert resultat.status == 'extracted'
    assert 'ORIENTATION' in resultat.text
    analysee = analyser_pdf(source)[0]
    assert not analysee.orientations_mixtes and not analysee.orientations_non_verifiees
    mot = next(mot for ligne in analysee.lignes for mot in ligne.mots if mot.texte == 'ORIENTATION')
    with pdfplumber.open(source) as document:
        page = document.pages[0]
        with lectures_mixtes_verifiees(page) as lectures:
            assert lectures is not None and len(lectures) == 1
        vue = boite_vue(page)
        caracteres = [c for c in page.chars if c['text'].strip()]
        attendu = (min(c['x0'] for c in caracteres) - vue[0], max(c['x1'] for c in caracteres) - vue[0],
                   min(c['top'] for c in caracteres) - vue[1], max(c['bottom'] for c in caracteres) - vue[1])
        zone = _zone_des_mots([mot])
        assert (zone.x0, zone.x1, zone.y0, zone.y1) == pytest.approx(attendu, abs=0.01)
    brouillon = next(m for m in _extraire_mots_avec_bbox(source) if m['texte'] == 'ORIENTATION')
    assert (brouillon['x0'], brouillon['x1'], brouillon['y0'], brouillon['y1']) == pytest.approx(attendu, abs=0.01)
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize('ocr', [False, True])
def test_projection_homogene_refusee_reste_partielle(tmp_path, monkeypatch, ocr):
    source = _pdf(tmp_path / 'source.pdf', 'homogene_90')
    avant = hashlib.sha256(source.read_bytes()).digest()

    @contextmanager
    def verification_impossible(_page):
        yield None

    monkeypatch.setattr('seamtech_search.pdf_orientation.lectures_mixtes_verifiees', verification_impossible)
    resultat = extract_file(source, enable_ocr=ocr)
    assert resultat.status == 'unavailable'
    assert 'orientation' in resultat.detail.lower()
    assert resultat.text.strip()
    assert analyser_pdf(source)[0].orientations_non_verifiees
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize('ocr', [False, True])
def test_rejet_de_projection_sur_page_mixte_ne_devient_pas_un_succes(tmp_path, monkeypatch, ocr):
    source = _pdf(tmp_path / 'source.pdf', 'mixte_verification_refusee')
    avant = hashlib.sha256(source.read_bytes()).digest()
    appels_ocr = []

    @contextmanager
    def verification_impossible(_page):
        yield None

    monkeypatch.setattr('seamtech_search.pdf_orientation.lectures_mixtes_verifiees', verification_impossible)
    monkeypatch.setattr('seamtech_search.extractors._has_command', lambda commande: True)
    monkeypatch.setattr('seamtech_search.extractors._extract_with_ocrmypdf',
                        lambda *args: appels_ocr.append('ocrmypdf') or 'faux succès')
    resultat = extract_file(source, enable_ocr=ocr)
    assert resultat.status == 'unavailable'
    assert 'orientation' in resultat.detail.lower()
    assert 'TEXTEBASE' in resultat.text
    assert not appels_ocr
    page = analyser_pdf(source)[0]
    assert page.orientations_mixtes and page.orientations_non_verifiees
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize('ocr', [False, True])
def test_limite_avant_page_incertaine_ne_certifie_pas_le_document(tmp_path, ocr):
    source = tmp_path / 'plusieurs-pages.pdf'
    pdf = canvas.Canvas(str(source), pagesize=(595, 842))
    pdf.drawString(30, 750, 'PREMIEREPAGE TEXTE ASSEZ LONG')
    pdf.showPage()
    pdf.drawString(30, 750, 'TEXTEBASE')
    pdf.saveState()
    pdf.translate(400, 500)
    pdf.rotate(30)
    pdf.drawString(0, 0, 'ORIENTATION')
    pdf.restoreState()
    pdf.save()
    avant = hashlib.sha256(source.read_bytes()).digest()
    resultat = extract_file(source, max_chars=8, enable_ocr=ocr)
    assert resultat.status == 'unavailable'
    assert 'limit' in resultat.detail.lower() or 'tronqu' in resultat.detail.lower()
    assert resultat.text == 'PREMIERE'
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize(('maximum', 'statut', 'texte'), [
    (8, 'unavailable', 'PREMIERE'),
    (9, 'extracted', 'PREMIEREX'),
    (10, 'extracted', 'PREMIEREX'),
])
def test_limite_sur_derniere_page_distingue_exactement_la_troncature(tmp_path, maximum, statut, texte):
    source = tmp_path / 'page.pdf'
    pdf = canvas.Canvas(str(source))
    pdf.drawString(40, 700, 'PREMIEREX')
    pdf.save()
    resultat = extract_file(source, max_chars=maximum, enable_ocr=False)
    assert (resultat.status, resultat.text) == (statut, texte)
    if statut == 'unavailable':
        assert 'tronqu' in resultat.detail.lower()


@pytest.mark.parametrize('ocr', [False, True])
def test_repli_pypdf_ne_certifie_pas_une_inspection_en_echec(tmp_path, monkeypatch, ocr):
    import pdfplumber

    source = _pdf(tmp_path / 'source.pdf', 'oblique')
    avant = hashlib.sha256(source.read_bytes()).digest()
    appels_ocr = []
    def indisponible(*_args, **_kwargs):
        raise RuntimeError('panne synthétique sans données')
    monkeypatch.setattr(pdfplumber, 'open', indisponible)
    monkeypatch.setattr('seamtech_search.extractors._has_command', lambda commande: True)
    monkeypatch.setattr('seamtech_search.extractors._extract_with_ocrmypdf',
                        lambda *args: appels_ocr.append('ocrmypdf') or 'faux succès')
    resultat = extract_file(source, enable_ocr=ocr)
    assert resultat.status == 'unavailable'
    assert 'inspection' in resultat.detail.lower()
    # Le texte existe dans l'original mais ne peut pas être publié sans preuve
    # qu'il appartient à la vue : pypdf ignore notamment les chemins de clipping.
    from pypdf import PdfReader
    assert 'TEXTEBASE' in PdfReader(source).pages[0].extract_text()
    assert not resultat.text
    assert not appels_ocr
    assert hashlib.sha256(source.read_bytes()).digest() == avant


def test_indexeur_reel_conserve_le_texte_et_le_statut_non_verifie(tmp_path):
    from seamtech_search import crawler
    from seamtech_search.indexer import SearchIndex

    source = _pdf(tmp_path / 'source.pdf', 'oblique')
    avant = hashlib.sha256(source.read_bytes()).digest()
    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'index.db', enable_ocr=False)
    document = crawler._document_from_path(source, cfg, is_dir=False, existing_metadata={})
    assert document.extraction_status == 'unavailable'
    assert 'orientation' in document.extraction_detail.lower()
    assert 'TEXTEBASE' in document.text
    index = SearchIndex(cfg.database_path)
    index.initialize()
    index.upsert_document(document)
    with index.connect() as connexion:
        ligne = connexion.execute('SELECT extraction_status, extraction_detail FROM documents WHERE name = ?',
                                  (source.name,)).fetchone()
        texte = connexion.execute('SELECT content FROM documents_fts WHERE name = ?', (source.name,)).fetchone()
    assert ligne['extraction_status'] == 'unavailable'
    assert 'orientation' in ligne['extraction_detail'].lower()
    assert 'TEXTEBASE' in texte['content']
    assert hashlib.sha256(source.read_bytes()).digest() == avant


def test_brouillon_ne_cree_pas_de_zones_pour_une_orientation_non_verifiee(tmp_path):
    from seamtech_search.fiches.extraction import OrientationsMixtesBrouillon
    from seamtech_search.fiches.gabarit_brouillon import _extraire_mots_avec_bbox

    source = _pdf(tmp_path / 'source.pdf', 'cisaillement')
    with pytest.raises(OrientationsMixtesBrouillon):
        _extraire_mots_avec_bbox(source)


def test_detection_signale_une_orientation_non_verifiee(tmp_path, monkeypatch):
    from seamtech_search.fiches import routes

    source = _pdf(tmp_path / 'source.pdf', 'cisaillement')
    monkeypatch.setattr(routes, '_exiger_postgres', lambda _index: None)
    monkeypatch.setattr(routes, 'charger_gabarits', lambda _index: [])
    resultat = routes.detecter_pdf(object(), source)
    assert resultat['orientations_pdf_non_verifiees'] is True
    assert resultat['voie'] == 'reprise_complete'
    assert 'orientation' in resultat['avertissement'].lower()


def test_orientation_non_verifiee_impose_une_reprise_complete():
    from seamtech_search.fiches.modeles import Anomalie, FicheExtraite
    from seamtech_search.fiches.persistance import routage

    fiche = FicheExtraite(gabarit_code='TEST', anomalies=[Anomalie(
        code='orientation_pdf_non_verifiee', gravite='forte', message='Relecture intégrale requise',
    )])
    decision = routage(fiche)
    assert decision['voie'] == 'reprise_complete'
    assert 'orientation' in decision['motif'].lower()


def test_validation_lot_refuse_orientation_non_verifiee_meme_acquittee(monkeypatch):
    from seamtech_search.fiches import routes

    class Curseur:
        resultat = None
        mise_a_jour = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=()):
            if sql.startswith('SELECT id_fiche, statut, revision'):
                self.resultat = (1, 'a_valider', 1)
            elif sql.startswith('SELECT EXISTS') and 'fiche_anomalie' in sql:
                self.resultat = ('orientation_pdf_non_verifiee' in (sql + repr(params)),)
            elif sql.startswith('UPDATE fiche'):
                self.mise_a_jour = True
                self.resultat = (2,)
            else:
                self.resultat = None

        def fetchone(self):
            return self.resultat

    curseur = Curseur()

    class Connexion:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return curseur

    class Index:
        def connect(self):
            return Connexion()

    monkeypatch.setattr(routes, 'verifier_autorisation_validation_lot', lambda *_: (True, ''))
    monkeypatch.setattr(routes, 'divergences_bloquantes_sous_curseur', lambda *_: [])
    decision = routes.valider_lot(Index(), ['SYNTH-TEST'], None, acquittement_humain=True,
                                  revisions={'SYNTH-TEST': 1})
    assert decision['nb_validees'] == 0 and decision['nb_ignorees'] == 1
    assert 'orientation' in decision['ignorees'][0]['raison'].lower()
    assert not curseur.mise_a_jour


def test_import_metier_ne_certifie_pas_une_orientation_non_verifiee(tmp_path):
    from seamtech_search.import_pipeline import extract_structured_pdf

    source = _pdf(tmp_path / 'source.pdf', 'cisaillement')
    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'index.db', enable_ocr=False)
    resultat = extract_structured_pdf(source, cfg)
    assert resultat.extraction_status == 'failed'
    assert 'TEXTEBASE' in resultat.raw_text
    assert any('orientation' in alerte.lower() for alerte in resultat.warnings)
