"""Mesure de confiance Tesseract : format TSV réel et valeurs invalides."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from seamtech_search.ocr import pipeline


def _executer(monkeypatch, tsv: str):
    def commande(args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=tsv if "tsv" in args else "texte OCR exact\n", stderr="")
    monkeypatch.setattr(pipeline, "_has_command", lambda _cmd: True)
    monkeypatch.setattr(pipeline, "_tesseract_version", lambda _cmd: "tesseract-test")
    monkeypatch.setattr(pipeline.subprocess, "run", commande)
    return pipeline._ocr_image_via_tesseract(Path("synthese.png"))


def test_confiances_decimales_tesseract_sont_agrégées(monkeypatch):
    tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
           "1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t\n"
           "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t96.536941\tPREMIER\n"
           "5\t1\t1\t1\t1\t2\t0\t0\t20\t10\t81.250000\tSECOND\n")
    res = _executer(monkeypatch, tsv)
    assert res["succes"] is True
    assert res["texte"] == "texte OCR exact\n"
    assert res["confiance"] == pytest.approx((96.536941 + 81.25) / 2)


def test_confiance_ignore_valeurs_incorrectes_et_lignes_sans_mot(monkeypatch):
    tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
           "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t50.5\tMOT\n"
           "5\t1\t1\t1\t1\t2\t0\t0\t20\t10\tnan\tMOT\n"
           "5\t1\t1\t1\t1\t3\t0\t0\t20\t10\tinf\tMOT\n"
           "5\t1\t1\t1\t1\t4\t0\t0\t20\t10\t101\tMOT\n"
           "5\t1\t1\t1\t1\t5\t0\t0\t20\t10\t-1\tMOT\n"
           "5\t1\t1\t1\t1\t6\t0\t0\t20\t10\t80.0\t  \n"
           "4\t1\t1\t1\t1\t0\t0\t0\t20\t10\t99\tNON_MOT\n")
    assert _executer(monkeypatch, tsv)["confiance"] == 50.5


@pytest.mark.parametrize("tsv", ["", "malforme", "level\tconf\ttext\n5\tnan\tmot\n"])
def test_confiance_inconnue_si_aucune_valeur_valide(monkeypatch, tsv):
    resultat = _executer(monkeypatch, tsv)
    assert resultat["succes"] is True
    assert resultat["confiance"] is None


@pytest.mark.ocr_suite
def test_confiance_tesseract_cli_reelle_sur_image_synthetique(tmp_path):
    import shutil
    import subprocess

    from PIL import Image, ImageDraw, ImageFont

    binaire = shutil.which("tesseract")
    if not binaire:
        pytest.skip("Tesseract CLI absent")
    image = Image.new("RGB", (1100, 180), "white")
    try:
        police = ImageFont.truetype("DejaVuSans.ttf", 40)
    except OSError:
        police = ImageFont.load_default()
    ImageDraw.Draw(image).text((20, 30), "SAIL FABRICATION REFERENCE 12345", fill="black", font=police)
    fichier = tmp_path / "image-synthetique.png"
    image.save(fichier)
    tsv = subprocess.run([binaire, str(fichier), "stdout", "-l", "eng", "tsv"], capture_output=True,
                         text=True, check=True, timeout=30).stdout
    valeurs = []
    for ligne in tsv.splitlines()[1:]:
        parts = ligne.split("\t", 11)
        if len(parts) == 12 and parts[0] == "5" and parts[11].strip():
            c = float(parts[10])
            if 0 <= c <= 100:
                valeurs.append(c)
    assert valeurs, "aucun mot mesurable dans la sortie Tesseract réelle"
    resultat = pipeline._ocr_image_via_tesseract(fichier, langue="eng", tesseract_command=binaire)
    assert resultat["succes"] is True
    assert resultat["texte"].strip()
    assert resultat["confiance"] == pytest.approx(sum(valeurs) / len(valeurs), abs=1e-5)


@pytest.mark.parametrize("mode", ["stderr", "exception"])
def test_panne_moteur_ocr_ne_divulgue_pas_donnees_sensibles(monkeypatch, mode):
    def commande(_args, **_kwargs):
        if mode == "stderr":
            return SimpleNamespace(returncode=2, stdout="", stderr="chemin-et-secret-synthetiques")
        raise OSError("chemin-et-secret-synthetiques")
    monkeypatch.setattr(pipeline, "_has_command", lambda _cmd: True)
    monkeypatch.setattr(pipeline, "_tesseract_version", lambda _cmd: "tesseract-test")
    monkeypatch.setattr(pipeline.subprocess, "run", commande)
    resultat = pipeline._ocr_image_via_tesseract(Path("synthese.png"))
    assert resultat["succes"] is False
    assert "tesseract" in resultat["motif"]
    assert "chemin-et-secret-synthetiques" not in str(resultat)
