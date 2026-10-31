"""Régression ouverte : le clipping PDF doit borner tout texte publié.

Les PDF sont purement synthétiques. pypdf/pdfminer lisent les opérateurs
textuels même si le moteur graphique masque ces glyphes : leur présence dans
une extraction n'est donc pas une preuve que l'opérateur les voit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pdfplumber
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject, NumberObject
from reportlab.pdfgen import canvas

from seamtech_search import crawler
from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.extractors import extract_file
from seamtech_search.fiches.extraction import ExtractionImpossible, analyser_pdf
from seamtech_search.fiches.gabarit_brouillon import _extraire_mots_avec_bbox
from seamtech_search.import_pipeline import extract_structured_pdf
from seamtech_search.indexer import SearchIndex


def pdf_avec_clipping(tmp_path: Path, cas: str) -> Path:
    source = tmp_path / "source.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(500, 200))
    pdf.drawString(30, 150, "VISIBLE SYNTHETIQUE")
    if cas in {"forme-interne", "forme-bbox", "forme-parent"}:
        largeur = 250 if cas == "forme-bbox" else 500
        pdf.beginForm("dessin", 0, 0, largeur, 200)
    if cas in {"direct", "forme-interne"}:
        chemin = pdf.beginPath()
        chemin.rect(0, 0, 250, 200)
        pdf.saveState()
        pdf.clipPath(chemin, fill=0, stroke=0)
    if cas != "forme-parent":
        pdf.drawString(300, 100, "HORSVUE SYNTHETIQUE")
    if cas in {"direct", "forme-interne"}:
        pdf.restoreState()
    if cas in {"forme-interne", "forme-bbox", "forme-parent"}:
        if cas == "forme-parent":
            pdf.drawString(300, 100, "HORSVUE SYNTHETIQUE")
        pdf.endForm()
        if cas == "forme-parent":
            chemin = pdf.beginPath()
            chemin.rect(0, 0, 250, 200)
            pdf.saveState()
            pdf.clipPath(chemin, fill=0, stroke=0)
        pdf.doForm("dessin")
        if cas == "forme-parent":
            pdf.restoreState()
    pdf.save()
    return source


def config(tmp_path: Path) -> AppConfig:
    return AppConfig(root_paths=[tmp_path], database_path=tmp_path / "index.db", enable_ocr=False)


@pytest.mark.parametrize("cas", ["sans-clip", "clip-graphique", "clip-texte-visible", "forme-contenue"])
def test_pdf_lisible_non_masque_reste_exploitable(tmp_path, cas):
    source = tmp_path / "visible.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(500, 200))
    if cas == "clip-graphique":
        chemin = pdf.beginPath()
        chemin.rect(0, 0, 30, 30)
        pdf.saveState()
        pdf.clipPath(chemin, fill=0, stroke=0)
        pdf.rect(0, 0, 50, 50, fill=1)
        pdf.restoreState()
    if cas == "clip-texte-visible":
        chemin = pdf.beginPath()
        chemin.rect(20, 90, 220, 35)
        pdf.saveState()
        pdf.clipPath(chemin, fill=0, stroke=0)
    if cas == "forme-contenue":
        pdf.beginForm("texte", 0, 0, 250, 200)
    pdf.drawString(30, 100, "VISIBLE SYNTHETIQUE")
    if cas == "forme-contenue":
        pdf.endForm()
        pdf.doForm("texte")
    if cas == "clip-texte-visible":
        pdf.restoreState()
    pdf.save()
    avant = hashlib.sha256(source.read_bytes()).digest()
    resultat = extract_file(source)
    assert resultat.status == "extracted"
    assert "VISIBLE" in resultat.text
    mots = [mot.texte for page in analyser_pdf(source) for ligne in page.lignes for mot in ligne.mots]
    assert "VISIBLE" in mots
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
@pytest.mark.parametrize("forme", ["aucune", "contenue", "bbox-depassee"])
def test_figures_images_et_formes_sous_rotation(tmp_path, angle, forme):
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    source = tmp_path / "image.pdf"
    image = Image.new("RGB", (8, 8), color=(110, 150, 210))
    pdf = canvas.Canvas(str(source), pagesize=(300, 200))
    pdf.drawString(20, 165, "TEXTE VISIBLE")
    if forme != "aucune":
        pdf.beginForm("image", 0, 0, 100, 100)
    pdf.saveState()
    # Certaines bibliothèques encodent les images presque, mais non exactement,
    # orthogonales. L'image elle-même n'est pas une BBox de Form.
    pdf.transform(1, 0.000001, -0.000001, 1, 20, 20)
    coord = 40 if forme == "bbox-depassee" else 5
    pdf.drawImage(ImageReader(image), coord, coord, width=65, height=65)
    pdf.restoreState()
    if forme != "aucune":
        pdf.endForm()
        pdf.doForm("image")
    pdf.save()
    if angle:
        lecteur = PdfReader(source)
        writer = PdfWriter()
        writer.append(lecteur)
        writer.pages[0].rotate(angle)
        with source.open("wb") as sortie:
            writer.write(sortie)
    avant = hashlib.sha256(source.read_bytes()).digest()
    resultat = extract_file(source)
    if forme == "bbox-depassee":
        assert resultat.status == "unavailable"
        assert resultat.text == ""
    else:
        assert resultat.status == "extracted"
        assert "TEXTE" in resultat.text and "VISIBLE" in resultat.text
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize("cas", ["direct", "forme-interne", "forme-bbox", "forme-parent"])
@pytest.mark.parametrize("ocr", [False, True])
def test_extraction_clip_ne_publie_pas_hors_vue(tmp_path, cas, ocr):
    source = pdf_avec_clipping(tmp_path, cas)
    avant = hashlib.sha256(source.read_bytes()).digest()
    assert "HORSVUE" in PdfReader(source).pages[0].extract_text()
    resultat = extract_file(source, enable_ocr=ocr)
    assert resultat.status in {"extracted", "unavailable"}
    assert "HORSVUE" not in resultat.text
    if resultat.status == "extracted":
        assert "VISIBLE" in resultat.text
    else:
        assert resultat.detail
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize("voie", ["analyse", "brouillon", "index", "import", "preview", "ocr"])
def test_clip_ne_contamine_pas_les_parcours_metier(tmp_path, voie):
    source = pdf_avec_clipping(tmp_path, "direct")
    cfg = config(tmp_path)
    if voie == "analyse":
        try:
            pages = analyser_pdf(source)
        except ExtractionImpossible:
            return  # refus explicite : aucune fiche issue de ces glyphes
        texte = " ".join(mot.texte for page in pages for ligne in page.lignes for mot in ligne.mots)
    elif voie == "brouillon":
        try:
            mots = _extraire_mots_avec_bbox(source)
        except ExtractionImpossible:
            return
        texte = " ".join(mot.get("texte", "") for mot in mots)
    elif voie == "index":
        document = crawler._document_from_path(source, cfg, False, {})
        index = SearchIndex(cfg.database_path)
        index.initialize()
        try:
            index.upsert_document(document)
            assert not index.search("HORSVUE")
        finally:
            index.close()
        texte = document.text
    elif voie == "import":
        resultat = extract_structured_pdf(source, cfg)
        texte = resultat.raw_text
    elif voie == "ocr":
        from seamtech_search.ocr.pipeline import ocriser_fichier

        try:
            rapport = ocriser_fichier(source, tesseract_command="absent-synthetique")
        except RuntimeError:
            return
        texte = " ".join(str(page.get("texte_final", "")) for page in rapport["pages"])
        assert all("HORSVUE" not in str(page) for page in rapport["pages"])
    else:
        with TestClient(create_app(cfg)) as client:
            reponse = client.get("/preview", params={"path": str(source)})
        assert reponse.status_code == 200
        texte = reponse.json()["text"]
    assert "HORSVUE" not in texte


@pytest.mark.parametrize("defaut", ["clip", "lecture"])
def test_inventaire_et_ocr_refusent_une_vue_non_verifiee_sans_repli(tmp_path, monkeypatch, defaut):
    from seamtech_search.ocr import inventaire, pipeline

    source = pdf_avec_clipping(tmp_path, "direct")
    appels = []
    monkeypatch.setattr(pipeline, "_ocr_page_pdf", lambda *args, **kwargs: appels.append(True))
    if defaut == "lecture":
        monkeypatch.setattr("pdfplumber.open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("simule")))
    with pytest.raises(RuntimeError, match="Inspection PDF"):
        pipeline.ocriser_fichier(source)
    with pytest.raises(RuntimeError, match="Inspection PDF"):
        inventaire.inventaire_etage3(None, tmp_path)
    assert appels == []


@pytest.mark.parametrize(
    "nature, attendu",
    [("glyphe", "refus"), ("image", "refus"), ("trace", "extrait"), ("trace-hors", "refus")],
)
def test_objet_partiellement_coupe_selon_sa_nature(tmp_path, nature, attendu):
    """Un glyphe ou une image coupés sont refusés ; un tracé visible est accepté.

    Le lecteur publierait le contenu entier d'un glyphe ou d'une image à moitié
    masqués, y compris la partie que l'opérateur ne voit pas. Un tracé
    vectoriel ne porte aucun contenu extractible : le refuser dès qu'il dépasse
    un clip rendrait indisponible la moindre page utilisant un clip décoratif,
    mais un tracé entièrement hors clip ne fabriquerait que des arêtes fictives.
    """
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    source = tmp_path / "partiel.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(300, 200))
    pdf.drawString(30, 150, "VISIBLE SYNTHETIQUE")
    chemin = pdf.beginPath()
    chemin.rect(0, 0, 60, 120)
    pdf.saveState()
    pdf.clipPath(chemin, fill=0, stroke=0)
    if nature == "glyphe":
        pdf.drawString(45, 100, "HORSVUE SYNTHETIQUE")
    elif nature == "image":
        image = Image.new("RGB", (8, 8), color=(110, 150, 210))
        pdf.drawImage(ImageReader(image), 40, 40, width=40, height=40)
    elif nature == "trace":
        pdf.rect(40, 40, 40, 40, fill=1)
    else:
        pdf.rect(150, 150, 40, 40, fill=1)
    pdf.restoreState()
    pdf.save()
    # Sans la garde, le lecteur verrait bien l'objet : le test n'est pas vide.
    with pdfplumber.open(source) as doc:
        page = doc.pages[0]
        brut = bool(page.chars or page.images or page.rects)
    assert brut
    avant = hashlib.sha256(source.read_bytes()).digest()
    resultat = extract_file(source)
    if attendu == "extrait":
        assert resultat.status == "extracted"
        assert "VISIBLE" in resultat.text
    else:
        assert resultat.status == "unavailable"
        assert resultat.text == ""
    assert hashlib.sha256(source.read_bytes()).digest() == avant


def test_police_a_largeurs_nulles_refusee_sous_clip(tmp_path):
    """Des largeurs déclarées nulles écrasent les boîtes : plus rien n'est prouvé.

    Un PDF peut déclarer /Widths à zéro alors que le moteur de rendu se sert des
    métriques réelles de la police incorporée : les boîtes du lecteur se
    replient alors sur l'origine du texte et un clip paraît les contenir. La
    garde refuse toute avance nulle sous clip plutôt que de publier.
    """
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    police = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    assert police.exists()
    pdfmetrics.registerFont(TTFont("VeraSynthetique", str(police)))

    def construire(chemin: Path) -> Path:
        pdf = canvas.Canvas(str(chemin), pagesize=(300, 200))
        pdf.setFont("VeraSynthetique", 12)
        voie = pdf.beginPath()
        voie.rect(0, 0, 60, 200)
        pdf.saveState()
        pdf.clipPath(voie, fill=0, stroke=0)
        pdf.drawString(20, 100, "AB")
        pdf.restoreState()
        pdf.drawString(20, 50, "VISIBLE SYNTHETIQUE")
        pdf.showPage()
        pdf.save()
        return chemin

    def largeurs_nulles(entree: Path, sortie: Path) -> Path:
        lecteur = PdfReader(entree)
        writer = PdfWriter()
        writer.append(lecteur)
        for _nom, ref in writer.pages[0]["/Resources"]["/Font"].items():
            fonte = ref.get_object()
            if "/Widths" in fonte:
                fonte[NameObject("/Widths")] = ArrayObject([NumberObject(0)] * len(fonte["/Widths"]))
        with sortie.open("wb") as flux:
            writer.write(flux)
        return sortie

    sain = construire(tmp_path / "sain.pdf")
    assert extract_file(sain).status == "extracted"
    falsifie = largeurs_nulles(sain, tmp_path / "largeurs-nulles.pdf")
    with pdfplumber.open(falsifie) as doc:
        boites = [(round(c["x0"], 2), round(c["x1"], 2)) for c in doc.pages[0].chars]
    assert boites and all(x0 == x1 for x0, x1 in boites)
    resultat = extract_file(falsifie)
    assert resultat.status == "unavailable"
    assert resultat.text == ""
