"""Moteurs OCR : sélection, modèles locaux, bornes et absence de réseau.

Le produit reconnaît le texte des scans avec RapidOCR (PP-OCRv6, ONNX) quand la
roue est installée, sinon avec le binaire Tesseract. Ce fichier éprouve
l'interface commune ``seamtech_search/ocr/moteur.py`` : qui est choisi, avec
quels modèles, sous quelles bornes, et la preuve qu'aucun modèle n'est jamais
téléchargé (RG14).

Les tests qui exigent réellement RapidOCR sautent proprement si la roue est
absente ; en CI, ``SEAMTECH_OCR_OBLIGATOIRE=1`` transforme ce saut en échec
(voir le garde-fou du job « ocr »).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from seamtech_search.ocr import moteur as mod_moteur
from seamtech_search.ocr.moteur import (
    MOTEUR_RAPIDOCR,
    MOTEUR_TESSERACT,
    MoteurRapidOCR,
    MoteurTesseract,
    choisir_moteur,
    moteur_demande,
    moteur_disponible,
)

pytestmark = pytest.mark.ocr_suite

_IMAGE = None


def _image_synthetique(tmp_path: Path, texte: str = "SEAMTECH 12345") -> Path:
    """PNG synthétique (aucune donnée réelle), texte noir sur blanc."""
    from PIL import Image, ImageDraw, ImageFont

    chemin = tmp_path / "synthese.png"
    image = Image.new("RGB", (640, 160), "white")
    dessin = ImageDraw.Draw(image)
    try:
        fonte = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 48)
    except Exception:
        fonte = ImageFont.load_default(size=48)
    dessin.text((20, 50), texte, fill="black", font=fonte)
    image.save(chemin, "PNG")
    return chemin


def _exiger_rapidocr() -> None:
    if not mod_moteur._rapidocr_installable():
        if os.environ.get("SEAMTECH_OCR_OBLIGATOIRE") == "1":
            pytest.fail("RapidOCR obligatoire (SEAMTECH_OCR_OBLIGATOIRE=1) mais absent")
        pytest.skip("roue rapidocr absente")


# ---------------------------------------------------------------------------
# Sélection du moteur
# ---------------------------------------------------------------------------


def test_auto_prefere_rapidocr_quand_la_roue_est_installee(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEAMTECH_OCR_MOTEUR", raising=False)
    monkeypatch.setattr(mod_moteur, "_rapidocr_installable", lambda: True)
    monkeypatch.setattr(mod_moteur, "_tesseract_disponible", lambda _cmd: True)
    assert isinstance(choisir_moteur("tesseract"), MoteurRapidOCR)


def test_auto_replie_sur_tesseract_sans_roue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEAMTECH_OCR_MOTEUR", raising=False)
    monkeypatch.setattr(mod_moteur, "_rapidocr_installable", lambda: False)
    monkeypatch.setattr(mod_moteur, "_tesseract_disponible", lambda _cmd: True)
    assert isinstance(choisir_moteur("tesseract"), MoteurTesseract)


def test_repli_tesseract_avertit_que_les_identifiants_seront_degrades(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Le repli n'est PAS équivalent au moteur par défaut : il doit le dire.

    Mesuré (docs/OCR_MOTEURS.md §3) : 5/5 identifiants avec RapidOCR, 1/5 avec
    Tesseract. Un repli silencieux laisserait croire à une reconnaissance
    égale sur les références de fiche — c'est-à-dire sur ce que l'on cherche.
    """
    monkeypatch.delenv("SEAMTECH_OCR_MOTEUR", raising=False)
    monkeypatch.setattr(mod_moteur, "_rapidocr_installable", lambda: False)
    monkeypatch.setattr(mod_moteur, "_tesseract_disponible", lambda _cmd: True)
    with caplog.at_level(logging.WARNING, logger="seamtech_search.ocr.moteur"):
        assert isinstance(choisir_moteur("tesseract"), MoteurTesseract)
    messages = [e.getMessage() for e in caplog.records]
    assert any("IDENTIFIANTS" in m and "1/5" in m and "5/5" in m for m in messages), messages


def test_tesseract_impose_avertit_aussi(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Forcer Tesseract par variable d'environnement n'efface pas l'avertissement."""
    monkeypatch.setenv("SEAMTECH_OCR_MOTEUR", "tesseract")
    monkeypatch.setattr(mod_moteur, "_rapidocr_installable", lambda: True)
    monkeypatch.setattr(mod_moteur, "_tesseract_disponible", lambda _cmd: True)
    with caplog.at_level(logging.WARNING, logger="seamtech_search.ocr.moteur"):
        assert isinstance(choisir_moteur("tesseract"), MoteurTesseract)
    messages = [e.getMessage() for e in caplog.records]
    assert any("choix explicite" in m for m in messages), messages


def test_moteur_par_defaut_ne_provoque_aucun_avertissement_de_repli(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Avec RapidOCR installé, aucun message : c'est la qualité de référence."""
    monkeypatch.delenv("SEAMTECH_OCR_MOTEUR", raising=False)
    monkeypatch.setattr(mod_moteur, "_rapidocr_installable", lambda: True)
    monkeypatch.setattr(mod_moteur, "_tesseract_disponible", lambda _cmd: True)
    with caplog.at_level(logging.WARNING, logger="seamtech_search.ocr.moteur"):
        assert isinstance(choisir_moteur("tesseract"), MoteurRapidOCR)
    assert [e.getMessage() for e in caplog.records] == []


def test_aucun_moteur_disponible_donne_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEAMTECH_OCR_MOTEUR", raising=False)
    monkeypatch.setattr(mod_moteur, "_rapidocr_installable", lambda: False)
    monkeypatch.setattr(mod_moteur, "_tesseract_disponible", lambda _cmd: False)
    assert choisir_moteur("tesseract") is None
    assert moteur_disponible("tesseract") is False


def test_binaire_tesseract_explicite_n_est_pas_substitue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un binaire désigné nommément reste Tesseract (faux moteur des tests)."""
    monkeypatch.delenv("SEAMTECH_OCR_MOTEUR", raising=False)
    monkeypatch.setattr(mod_moteur, "_rapidocr_installable", lambda: True)
    monkeypatch.setattr(mod_moteur, "_tesseract_disponible", lambda cmd: cmd == "/tmp/faux")
    assert isinstance(choisir_moteur("/tmp/faux"), MoteurTesseract)


def test_variable_environnement_force_le_moteur(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEAMTECH_OCR_MOTEUR", "tesseract")
    monkeypatch.setattr(mod_moteur, "_rapidocr_installable", lambda: True)
    monkeypatch.setattr(mod_moteur, "_tesseract_disponible", lambda _cmd: True)
    assert moteur_demande() == MOTEUR_TESSERACT
    assert isinstance(choisir_moteur("tesseract"), MoteurTesseract)

    monkeypatch.setenv("SEAMTECH_OCR_MOTEUR", "rapidocr")
    monkeypatch.setattr(mod_moteur, "_tesseract_disponible", lambda _cmd: False)
    assert isinstance(choisir_moteur("tesseract"), MoteurRapidOCR)


def test_valeur_environnement_inconnue_retombe_sur_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEAMTECH_OCR_MOTEUR", "inconnu-volontaire")
    assert moteur_demande() == "auto"


# ---------------------------------------------------------------------------
# Modèles : locaux, jamais téléchargés
# ---------------------------------------------------------------------------


def test_modeles_sont_des_fichiers_locaux_de_la_roue(monkeypatch: pytest.MonkeyPatch) -> None:
    """RG14 : le moteur reçoit des chemins locaux, jamais une URL."""
    _exiger_rapidocr()
    import rapidocr

    from seamtech_search.ocr import reconnaissance

    captured: dict[str, object] = {}

    class _FauxMoteur:
        def __init__(self, config_path=None, params=None) -> None:
            captured["params"] = params or {}

    monkeypatch.setattr(rapidocr, "RapidOCR", _FauxMoteur, raising=True)
    reconnaissance._Moteur()
    params = captured.get("params")
    assert isinstance(params, dict) and params
    chemins = [str(v) for k, v in params.items() if k.endswith("model_path")]
    assert chemins, "aucun chemin de modèle transmis"
    for chemin in chemins:
        assert Path(chemin).is_file(), f"modèle absent : {chemin}"
        assert not chemin.lower().startswith(("http://", "https://")), f"URL transmise : {chemin}"


def test_roue_sans_modele_n_est_pas_utilisable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans modèle groupé, le moteur est indisponible : il ne télécharge rien."""
    _exiger_rapidocr()
    monkeypatch.setattr(Path, "glob", lambda self, _motif: iter([]))
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    assert mod_moteur._rapidocr_installable() is False


# ---------------------------------------------------------------------------
# Reconnaissance réelle et bornes
# ---------------------------------------------------------------------------


def test_rapidocr_reconnait_un_texte_synthetique(tmp_path: Path) -> None:
    _exiger_rapidocr()
    image = _image_synthetique(tmp_path)
    moteur = MoteurRapidOCR()
    try:
        resultat = moteur.reconnaitre(image, timeout_s=60)
    finally:
        moteur.fermer()
    assert resultat.succes is True, resultat.motif
    assert resultat.moteur == MOTEUR_RAPIDOCR
    assert resultat.version_moteur
    assert "SEAMTECH" in resultat.texte.upper(), resultat.texte
    assert resultat.confiance is None or 0 <= resultat.confiance <= 100


def test_rapidocr_reutilise_un_seul_esclave(tmp_path: Path) -> None:
    """Les modèles ne sont chargés qu'une fois : un seul esclave par lot."""
    _exiger_rapidocr()
    image = _image_synthetique(tmp_path)
    moteur = MoteurRapidOCR()
    try:
        premier = moteur.reconnaitre(image, timeout_s=60)
        processus = moteur._processus
        second = moteur.reconnaitre(image, timeout_s=60)
        assert moteur._processus is processus
        assert premier.succes and second.succes
    finally:
        moteur.fermer()
    assert moteur._processus is None  # l'esclave ne survit pas au lot


def test_delai_depasse_ne_publie_rien(tmp_path: Path) -> None:
    """Un délai trop court échoue la page, il ne publie pas un texte partiel."""
    _exiger_rapidocr()
    image = _image_synthetique(tmp_path)
    moteur = MoteurRapidOCR()
    try:
        resultat = moteur.reconnaitre(image, timeout_s=0)
    finally:
        moteur.fermer()
    assert resultat.succes is False
    assert resultat.texte == ""
    assert "timeout" in resultat.motif


def test_scores_hors_bornes_ignores() -> None:
    """Les confiances hors [0,1] ne polluent pas la moyenne."""
    from seamtech_search.ocr import reconnaissance

    class _Resultat:
        txts = ("A", "B")
        scores = (0.9, 5.0)

    class _Faux:
        def __call__(self, _chemin: str) -> _Resultat:
            return _Resultat()

    moteur = reconnaissance._Moteur.__new__(reconnaissance._Moteur)
    moteur._ocr = _Faux()
    moteur.version = "test"
    retour = moteur.reconnaitre("image.png")
    assert retour["texte"] == "A\nB"
    assert retour["confiance"] == pytest.approx(90.0)


def test_tesseract_absent_donne_un_motif_lisible(tmp_path: Path) -> None:
    moteur = MoteurTesseract("binaire-inexistant-synthetique")
    resultat = moteur.reconnaitre(tmp_path / "image.png", timeout_s=5)
    assert resultat.succes is False
    assert "tesseract" in resultat.motif
    assert resultat.texte == ""
