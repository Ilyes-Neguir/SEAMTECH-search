"""Parcours OCR réel (extracteurs + index). Sans tesseract : skip explicite, pas un PASS.

Le job CI `ocr` installe tesseract 5.x + fra/eng (sans PostgreSQL) : les tests
d'extraction s'y exécutent. Le parcours import→index→recherche→téléchargement
exige aussi PostgreSQL : il se saute sans DSN (exécuté par qualification-reserves).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from seamtech_search.extractors import extract_file

pytestmark = pytest.mark.ocr_suite

RACINE = Path(__file__).resolve().parent.parent
FIXTURES = RACINE / "tests" / "fixtures" / "ocr"
PDF_PROPRE = FIXTURES / "ocr_propre.pdf"
PDF_DEGRADE = FIXTURES / "ocr_degrade.pdf"
REF_PROPRE = FIXTURES / "reference_propre.txt"
PDF_TEXTE = RACINE / "sample_data" / "CLIENT-7792-SO" / "fiche-7792-SO_ffab.pdf"


def _tesseract() -> str:
    cmd = os.environ.get("SEAMTECH_TESSERACT") or shutil.which("tesseract")
    if cmd and Path(cmd).is_file() and os.access(cmd, os.X_OK):
        return cmd
    if os.environ.get("SEAMTECH_OCR_OBLIGATOIRE") == "1":
        pytest.fail("tesseract obligatoire (SEAMTECH_OCR_OBLIGATOIRE=1) mais absent")
    pytest.skip("tesseract absent")


def _langs(cmd: str) -> set[str]:
    try:
        sortie = subprocess.run(
            [cmd, "--list-langs"], capture_output=True, text=True, timeout=10, check=False
        )
        return {ligne.strip() for ligne in (sortie.stdout or "").splitlines()}
    except Exception:
        return set()


def _pdf_scan_anglais(destination: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas

    texte = "GENOA SAIL ATLANTIC WORKSHOP\nTechnical drawing 42"
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
    except Exception:
        font = ImageFont.load_default()
    y = 80
    for ligne in texte.splitlines():
        draw.text((60, y), ligne, fill="black", font=font)
        y += 60
    tmp_img = destination.with_suffix(".tmp.png")
    image.save(tmp_img, "PNG")
    c = pdf_canvas.Canvas(str(destination), pagesize=A4)
    largeur, hauteur = A4
    c.drawImage(str(tmp_img), 20, 20, width=largeur - 40, height=hauteur - 40, preserveAspectRatio=True)
    c.showPage()
    c.save()
    tmp_img.unlink(missing_ok=True)
    return destination


def test_scan_fra_extrait_les_mots_de_reference() -> None:
    cmd = _tesseract()
    avant = hashlib.sha256(PDF_PROPRE.read_bytes()).hexdigest()
    resultat = extract_file(PDF_PROPRE, max_chars=20_000, enable_ocr=True, tesseract_command=cmd)
    assert hashlib.sha256(PDF_PROPRE.read_bytes()).hexdigest() == avant
    assert resultat.status == "extracted", resultat.detail
    ref = REF_PROPRE.read_text(encoding="utf-8") if REF_PROPRE.is_file() else ""
    mots = [m.lower() for m in ref.split() if len(m) >= 4][:8]
    texte = resultat.text.lower()
    hits = [m for m in mots if m in texte]
    assert hits, f"référence absente du texte OCR : {texte[:300]!r}"


def test_scan_degrade_n_est_pas_un_succes_vide() -> None:
    cmd = _tesseract()
    resultat = extract_file(PDF_DEGRADE, max_chars=20_000, enable_ocr=True, tesseract_command=cmd)
    assert resultat.status in {"extracted", "unavailable", "error"}
    if resultat.status == "extracted":
        assert resultat.text.strip()
    else:
        assert resultat.detail


def test_pdf_texte_natif_non_ocerise_regression() -> None:
    cmd = _tesseract()
    if not PDF_TEXTE.is_file():
        pytest.skip("fiche texte native absente")
    avant = hashlib.sha256(PDF_TEXTE.read_bytes()).hexdigest()
    resultat = extract_file(PDF_TEXTE, max_chars=20_000, enable_ocr=True, tesseract_command=cmd)
    assert hashlib.sha256(PDF_TEXTE.read_bytes()).hexdigest() == avant
    assert resultat.status == "extracted"
    assert "7792" in resultat.text or len(resultat.text) > 50


def test_xlsx_sans_regression_ocr() -> None:
    from zipfile import ZipFile

    cmd = _tesseract()
    xlsx = Path("/tmp/qualif-ocr-nomenclature.xlsx")
    with ZipFile(xlsx, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", "<sst><t>DACRON-OCR</t></sst>")
    resultat = extract_file(xlsx, max_chars=1000, enable_ocr=True, tesseract_command=cmd)
    assert "DACRON-OCR" in resultat.text


def test_pdf_invalide_erreur_visible(tmp_path: Path) -> None:
    cmd = _tesseract()
    mauvais = tmp_path / "pas.pdf"
    mauvais.write_bytes(b"ceci n'est pas un pdf")
    resultat = extract_file(mauvais, max_chars=1000, enable_ocr=True, tesseract_command=cmd)
    assert resultat.status in {"unavailable", "error"}
    assert resultat.detail


def test_scan_anglais_retrouve_les_mots(tmp_path: Path) -> None:
    cmd = _tesseract()
    if "eng" not in _langs(cmd):
        pytest.skip("paquet tesseract-ocr-eng absent")
    from seamtech_search.ocr.pipeline import ocriser_fichier

    pdf = _pdf_scan_anglais(tmp_path / "scan-eng.pdf")
    avant = hashlib.sha256(pdf.read_bytes()).hexdigest()
    rapport = ocriser_fichier(pdf, langue="eng", tesseract_command=cmd)
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == avant
    texte = " ".join(p.get("texte_ocr") or "" for p in rapport["pages"]).upper()
    hits = [mot for mot in ("GENOA", "SAIL", "ATLANTIC") if mot in texte]
    assert hits, f"mots anglais absents de l'OCR : {texte[:300]!r}"


def test_parcours_import_ocr_index_recherche_telechargement(tmp_path: Path) -> None:
    """Import réel → OCR → index PostgreSQL → GET /search → POST /open."""
    cmd = _tesseract()
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL — parcours complet exige PostgreSQL")

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig
    from seamtech_search.crawler import crawl
    from seamtech_search.indexer import SearchIndex
    from tests.conftest import _creer_base_jetable, _supprimer_base_jetable

    racine = tmp_path / "archive"
    (racine / "fra").mkdir(parents=True)
    (racine / "eng").mkdir()
    shutil.copyfile(PDF_PROPRE, racine / "fra" / "scan-fra.pdf")
    shutil.copyfile(PDF_DEGRADE, racine / "fra" / "scan-degrade.pdf")
    _pdf_scan_anglais(racine / "eng" / "scan-eng.pdf")
    empreintes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in racine.rglob("*.pdf")}

    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / "unused.db", url)
    index.initialize()
    index.run_migrations()
    config = AppConfig(
        root_paths=[racine],
        database_path=tmp_path / "search.db",
        database_url=url,
        enable_ocr=True,
        tesseract_command=cmd,
        min_free_bytes=0,
        storage_backend="local",
        extraction_timeout_seconds=90,
    )
    try:
        documents = list(crawl(config))
        assert index.upsert_documents(documents) >= 1
        for chemin, empreinte in empreintes.items():
            assert hashlib.sha256(chemin.read_bytes()).hexdigest() == empreinte

        app = create_app(config)
        with TestClient(app) as client:
            fra = client.get("/search", params={"q": "SEAMTECH"})
            assert fra.status_code == 200, fra.text
            hits_fra = fra.json()["results"]
            assert hits_fra, "GET /search n'a pas retrouvé le scan français OCR"

            eng = client.get("/search", params={"q": "GENOA"})
            assert eng.status_code == 200, eng.text
            hits_eng = eng.json()["results"]
            assert hits_eng, "GET /search n'a pas retrouvé le scan anglais OCR"

            ident = int(hits_fra[0]["id"])
            with index.connect() as connexion:
                with connexion.cursor() as cursor:
                    cursor.execute("SELECT path FROM documents WHERE id = %s", (ident,))
                    ligne = cursor.fetchone()
            assert ligne and ligne[0]
            ouvert = client.post("/open", params={"path": ligne[0]})
            assert ouvert.status_code == 200, ouvert.text
            assert ouvert.content
            assert hashlib.sha256(ouvert.content).hexdigest() in empreintes.values()

        xlsx = tmp_path / "nomenclature.xlsx"
        from zipfile import ZipFile

        with ZipFile(xlsx, "w") as archive:
            archive.writestr("xl/sharedStrings.xml", "<sst><t>DACRON-OCR</t></sst>")
        extra_xlsx = extract_file(xlsx, max_chars=1000, enable_ocr=True, tesseract_command=cmd)
        assert "DACRON-OCR" in extra_xlsx.text

        if PDF_TEXTE.is_file():
            extra_texte = extract_file(PDF_TEXTE, max_chars=20_000, enable_ocr=True, tesseract_command=cmd)
            assert extra_texte.status == "extracted"
            assert "7792" in extra_texte.text or len(extra_texte.text) > 50
    finally:
        index.close()
        _supprimer_base_jetable(nom)
