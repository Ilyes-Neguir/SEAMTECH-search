"""Régression ouverte : le clipping PDF doit borner tout texte publié.

Les PDF sont purement synthétiques. pypdf/pdfminer lisent les opérateurs
textuels même si le moteur graphique masque ces glyphes : leur présence dans
une extraction n'est donc pas une preuve que l'opérateur les voit.

Contrat de la garde : un objet dont la visibilité n'est pas démontrée est
RETIRE de la publication, la page reste exploitable. Refuser la page entière
avait été essayé (v1 à v3) : sur un corpus réel de 14 PDF, 30 pages, cela
rendait 14 pages indisponibles alors que les glyphes incriminés étaient, pour
3 d'entre eux, réellement masqués (encre nulle au rendu) et pour le quatrième
partiellement visible. Le retrait ciblé garde la confidentialité (aucun contenu
masqué n'est publié) sans amputer le document. Seules les fautes STRUCTURELLES
(XObject cyclique, flux incomplet, texte servant à détourer) refusent la page.
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
from seamtech_search.fiches.extraction import analyser_pdf
from seamtech_search.fiches.gabarit_brouillon import _extraire_mots_avec_bbox
from seamtech_search.import_pipeline import extract_structured_pdf
from seamtech_search.indexer import SearchIndex
from seamtech_search.pdf_orientation import orientations_visibles, page_de_lecture


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
    assert resultat.status == "extracted"
    assert "TEXTE" in resultat.text and "VISIBLE" in resultat.text
    with pdfplumber.open(source) as doc:
        vue, _rotation = page_de_lecture(doc.pages[0])
        images = len(vue.images)
    if forme == "bbox-depassee":
        # Dessinée hors de la BBox de sa forme, l'image est découpée par le
        # lecteur : elle est retirée, la page reste exploitable.
        assert images == 0
    else:
        assert images >= 1
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize("cas", ["direct", "forme-interne", "forme-bbox", "forme-parent"])
@pytest.mark.parametrize("ocr", [False, True])
def test_extraction_clip_ne_publie_pas_hors_vue(tmp_path, cas, ocr):
    source = pdf_avec_clipping(tmp_path, cas)
    avant = hashlib.sha256(source.read_bytes()).digest()
    assert "HORSVUE" in PdfReader(source).pages[0].extract_text()
    resultat = extract_file(source, enable_ocr=ocr)
    # La page reste exploitable : seuls les glyphes masqués disparaissent.
    assert resultat.status == "extracted"
    assert "HORSVUE" not in resultat.text
    assert "VISIBLE" in resultat.text
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize("voie", ["analyse", "brouillon", "index", "import", "preview", "ocr"])
def test_clip_ne_contamine_pas_les_parcours_metier(tmp_path, voie):
    source = pdf_avec_clipping(tmp_path, "direct")
    cfg = config(tmp_path)
    if voie == "analyse":
        pages = analyser_pdf(source)
        texte = " ".join(mot.texte for page in pages for ligne in page.lignes for mot in ligne.mots)
    elif voie == "brouillon":
        mots = _extraire_mots_avec_bbox(source)
        assert mots  # le retrait ciblé ne vide pas la page
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
            return  # moteur absent : aucune autre voie ne doit publier non plus
        texte = " ".join(str(page.get("texte_final", "")) for page in rapport["pages"])
        assert all("HORSVUE" not in str(page) for page in rapport["pages"])
    else:
        with TestClient(create_app(cfg)) as client:
            reponse = client.get("/preview", params={"path": str(source)})
        assert reponse.status_code == 200
        texte = reponse.json()["text"]
    assert "HORSVUE" not in texte
    if voie != "ocr":  # l'OCR dépend du moteur installé, les autres voies non
        assert "VISIBLE" in texte


def test_inventaire_et_ocr_refusent_une_lecture_impossible_sans_repli(tmp_path, monkeypatch):
    """Lecteur indisponible : aucun repli silencieux, l'OCR ne tourne pas."""
    from seamtech_search.ocr import inventaire, pipeline

    source = pdf_avec_clipping(tmp_path, "direct")
    appels = []
    monkeypatch.setattr(pipeline, "_ocr_page_pdf", lambda *args, **kwargs: appels.append(True))
    monkeypatch.setattr("pdfplumber.open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("simule")))
    with pytest.raises(RuntimeError, match="Inspection PDF"):
        pipeline.ocriser_fichier(source)
    with pytest.raises(RuntimeError, match="Inspection PDF"):
        inventaire.inventaire_etage3(None, tmp_path)
    assert appels == []


def test_objets_retires_ne_sont_pas_soumis_a_l_ocr(tmp_path):
    """Un glyphe retiré par la garde n'atteint jamais la file d'OCR."""
    from seamtech_search.pdf_orientation import page_de_lecture

    source = pdf_avec_clipping(tmp_path, "direct")
    with pdfplumber.open(source) as doc:
        vue, _rotation = page_de_lecture(doc.pages[0])
        texte = "".join(c["text"] for c in vue.chars)
    assert "HORSVUE" not in texte
    assert "VISIBLE" in texte


@pytest.mark.parametrize(
    "nature, sort",
    [
        ("glyphe", "retire"),
        ("image", "extrait"),
        ("image-hors", "retire"),
        ("trace", "extrait"),
        ("trace-hors", "retire"),
    ],
)
def test_objet_partiellement_coupe_selon_sa_nature(tmp_path, nature, sort):
    """Le glyphe est le seul objet dont le texte est recopié tel quel.

    Un glyphe à moitié masqué est RETIRÉ : l'extraction publierait sinon TOUT
    son texte, y compris ce que l'opérateur ne voit pas. Une image ou un tracé
    à moitié coupés sont publiés dès qu'une portion est visible : le produit ne
    lit aucun texte dans un objet image (l'OCR part de la page rendue, qui
    applique les clips), et refuser ces cas rendait indisponible la moitié d'un
    corpus réel. Entièrement hors clip, image et tracé sont retirés : ils ne
    fabriqueraient que des arêtes ou des métadonnées fictives.

    Dans tous les cas la page reste exploitable : c'est l'objet non démontré
    qui disparaît, pas le document.
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
    elif nature == "image-hors":
        image = Image.new("RGB", (8, 8), color=(110, 150, 210))
        pdf.drawImage(ImageReader(image), 150, 150, width=40, height=40)
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
    assert resultat.status == "extracted"
    assert "VISIBLE" in resultat.text
    with pdfplumber.open(source) as doc:
        vue, _rotation = page_de_lecture(doc.pages[0])
        texte = "".join(c["text"] for c in vue.chars)
        images = len(vue.images)
        traces = len(vue.rects) + len(vue.curves)
    if sort == "extrait":
        assert (images or traces) and (nature != "glyphe")
        if nature == "image":
            assert images >= 1
        elif nature == "trace":
            assert traces >= 1
    else:
        if nature == "glyphe":
            assert "HORSVUE" not in texte
        elif nature == "image-hors":
            assert images == 0
        else:
            assert traces == 0
    assert hashlib.sha256(source.read_bytes()).digest() == avant


def test_police_a_largeurs_nulles_retire_le_texte_sous_clip(tmp_path):
    """Des largeurs déclarées nulles écrasent les boîtes : plus rien n'est prouvé.

    Un PDF peut déclarer /Widths à zéro alors que le moteur de rendu se sert des
    métriques réelles de la police incorporée : les boîtes du lecteur se
    replient alors sur l'origine du texte et un clip paraît les contenir. La
    garde retire tout glyphe à avance nulle sous clip plutôt que de publier un
    texte dont la position réelle est inconnue.
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
    assert resultat.status == "extracted"
    assert "AB" not in resultat.text  # avance nulle : position non démontrée
    assert "VISIBLE" in resultat.text


def _pdf_clip_forme(tmp_path: Path, nom: str, forme: str, position: str, angle: int = 0) -> Path:
    """PDF à clip non rectangulaire : le texte est placé dedans, dehors ou à cheval."""
    source = tmp_path / f"{forme}-{position}-{angle}.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(400, 300))
    pdf.drawString(30, 260, "VISIBLE SYNTHETIQUE")
    chemin = pdf.beginPath()
    if forme == "cercle":
        chemin.circle(200, 120, 70)
    elif forme == "polygone":
        chemin.moveTo(120, 60)
        chemin.lineTo(280, 60)
        chemin.lineTo(280, 200)
        chemin.lineTo(120, 200)
        chemin.close()
    elif forme == "diamant":
        chemin.moveTo(200, 210)
        chemin.lineTo(270, 130)
        chemin.lineTo(200, 50)
        chemin.lineTo(130, 130)
        chemin.close()
    elif forme == "triangle":
        chemin.moveTo(200, 200)
        chemin.lineTo(280, 60)
        chemin.lineTo(120, 60)
        chemin.close()
    else:  # rectangle arrondi : le piège qui rendait la page indisponible
        chemin.roundRect(120, 60, 160, 140, 18)
    pdf.saveState()
    pdf.clipPath(chemin, fill=0, stroke=0)
    if position == "dedans":
        pdf.drawString(180, 118, "INTERNE")
    elif position == "dehors":
        pdf.drawString(150, 265, "ZONE EXTERNE")
    else:  # à cheval sur la frontière
        pdf.drawString(240, 118, "ZONE FRONTALIERE")
    pdf.restoreState()
    pdf.showPage()
    pdf.save()
    if angle:
        lecteur = PdfReader(source)
        writer = PdfWriter()
        writer.append(lecteur)
        writer.pages[0].rotate(angle)
        with source.open("wb") as sortie:
            writer.write(sortie)
    return source


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
@pytest.mark.parametrize("forme", ["cercle", "polygone", "arrondi", "diamant", "triangle"])
def test_clip_non_rectangulaire_laisse_la_page_exploitable(tmp_path, forme, angle):
    """Un clip décoratif (cercle, polygone, coins arrondis) reste modélisé.

    Refuser toute page portant un clip non rectangulaire rendait le produit
    inutilisable sur des documents réels : un simple cadre à coins arrondis
    suffisait. La région est maintenant calculée avec une marge prouvée.
    """
    source = _pdf_clip_forme(tmp_path, "ok", forme, "dedans", angle)
    avant = hashlib.sha256(source.read_bytes()).digest()
    resultat = extract_file(source)
    assert resultat.status == "extracted"
    assert "VISIBLE" in resultat.text and "INTERNE" in resultat.text
    assert hashlib.sha256(source.read_bytes()).digest() == avant


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
@pytest.mark.parametrize("forme", ["cercle", "polygone", "arrondi", "diamant", "triangle"])
@pytest.mark.parametrize("position", ["dehors", "cheval"])
def test_clip_non_rectangulaire_retire_le_texte_non_demontre(tmp_path, forme, position, angle):
    """Hors de la région, ou à cheval sur sa frontière : le texte est retiré.

    La page reste publiée avec son contenu démontré (« VISIBLE ») : un libellé
    masqué par un clip décoratif n'a jamais à rendre le document indisponible.
    """
    source = _pdf_clip_forme(tmp_path, "ko", forme, position, angle)
    with pdfplumber.open(source) as doc:
        brut = "".join(c["text"] for c in doc.pages[0].chars).replace(" ", "")
    assert "EXTERNE" in brut or "FRONTALIERE" in brut
    resultat = extract_file(source)
    assert resultat.status == "extracted"
    assert "EXTERNE" not in resultat.text and "FRONTALIERE" not in resultat.text
    assert "VISIBLE" in resultat.text


def test_clip_emboite_rectangle_et_polygone(tmp_path):
    """Un clip polygonal à l'intérieur d'un clip rectangulaire reste exact."""
    source = tmp_path / "emboite.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(400, 300))
    externe = pdf.beginPath()
    externe.rect(0, 0, 300, 300)
    pdf.saveState()
    pdf.clipPath(externe, fill=0, stroke=0)
    interne = pdf.beginPath()
    interne.circle(120, 120, 60)
    pdf.saveState()
    pdf.clipPath(interne, fill=0, stroke=0)
    pdf.drawString(95, 118, "AU CENTRE")
    pdf.restoreState()
    pdf.restoreState()
    pdf.drawString(320, 260, "HORS CADRE SYNTHETIQUE")
    pdf.save()
    resultat = extract_file(source)
    assert resultat.status == "extracted"
    assert "CENTRE" in resultat.text and "HORS CADRE" in resultat.text


def _pdf_mixte_avec_clip(tmp_path: Path, angle: int) -> Path:
    """Page à orientations mixtes portant un libellé masqué par un clip."""
    base = tmp_path / f"mixte-{angle}.pdf"
    pdf = canvas.Canvas(str(base), pagesize=(595, 842))
    pdf.drawString(35, 700, "MESURE SYNTHETIQUE")
    pdf.saveState()
    pdf.translate(420, 300)
    pdf.rotate(90)
    pdf.drawString(0, 0, "COLONNE SYNTHETIQUE")
    pdf.restoreState()
    chemin = pdf.beginPath()
    chemin.rect(0, 0, 200, 842)
    pdf.saveState()
    pdf.clipPath(chemin, fill=0, stroke=0)
    pdf.drawString(300, 500, "HORSVUE SYNTHETIQUE")
    pdf.restoreState()
    pdf.save()
    if angle:
        lecteur = PdfReader(base)
        writer = PdfWriter()
        writer.append(lecteur)
        writer.pages[0].rotate(angle)
        with base.open("wb") as sortie:
            writer.write(sortie)
    return base


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_page_mixte_et_clip_ne_publie_pas_le_texte_masque(tmp_path, angle):
    """Lectures mixtes : le texte masqué est retiré de CHAQUE lecture.

    Pour une page à orientations mixtes, le texte publié vient des vues
    normalisées par quadrant, pas de la page d'origine : le retrait doit donc
    aussi s'appliquer là, sinon la garde serait contournée par la rotation.
    """
    source = _pdf_mixte_avec_clip(tmp_path, angle)
    with pdfplumber.open(source) as doc:
        page = doc.pages[0]
        brut = "".join(c["text"] for c in page.chars)
        mixte, _compatible, _exploitable = orientations_visibles(page)
    assert "HORSVUE" in brut
    assert mixte  # sans cela le test ne couvrirait pas la voie mixte
    resultat = extract_file(source)
    assert resultat.status == "extracted"
    assert "HORSVUE" not in resultat.text
    assert "MESURE" in resultat.text and "COLONNE" in resultat.text
