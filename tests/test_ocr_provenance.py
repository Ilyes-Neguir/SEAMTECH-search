"""Le compte rendu OCR doit nommer le moteur qui A tourné, pas un autre.

Constat réel, pas une précaution : l'annotation CI du job ``ocr`` affichait

    taux mots retrouvés propre=1.000 ... moteur=rapidocr version=tesseract 5.3.4

RapidOCR avait fait le travail, et le rapport attribuait la version de
Tesseract. La cause : ``ocriser_page`` lisait ``moteur.version`` AVANT la
première reconnaissance. Un moteur paresseux — RapidOCR ne charge ses modèles
qu'au premier appel — ne connaît pas encore sa version à cet instant : elle
valait ``None``, et l'agrégat se rabattait alors sur ``_tesseract_version()``.

Pourquoi c'est grave et pas cosmétique : la provenance d'un texte extrait est
une preuve. Un rapport qui nomme le mauvais moteur empêche de répondre à
« qui a produit ce texte, et avec quelle version » après un incident
d'extraction — exactement la question qu'on pose quand une cote est
reconnue de travers.

Le test ci-dessous fait tourner un moteur factice PARESSEUX (version inconnue
jusqu'au premier appel) sur l'échantillon propre, avec une version Tesseract
disponible à côté : c'est la configuration exacte du défaut. Sans le
correctif, le rapport répond la version de Tesseract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from seamtech_search.ocr import moteur as module_moteur
from seamtech_search.ocr import pipeline
from seamtech_search.ocr.pipeline import ocriser_fichier

FIXTURES = Path(__file__).parent / "fixtures" / "ocr"
PDF_PROPRE = FIXTURES / "ocr_propre.pdf"

VERSION_TESSERACT = "tesseract 5.3.4-faux"
VERSION_RAPIDE = "rapidocr 9.9.9-faux"
NOM_MOTEUR = "rapidocr-faux"


class _MoteurParesseux:
    """Moteur factice : sa version n'existe qu'APRÈS le premier appel.

    C'est le comportement de ``MoteurRapidOCR``, dont l'esclave n'est démarré
    qu'à la première reconnaissance — et la source du défaut corrigé ici.
    """

    nom = NOM_MOTEUR

    def __init__(self) -> None:
        self._version: str | None = None
        self.appels = 0

    @property
    def version(self) -> str | None:
        return self._version

    def reconnaitre(self, image: Path, timeout_s: int, langue: str = "fra"):
        self.appels += 1
        self._version = VERSION_RAPIDE
        return module_moteur.ResultatOcr(
            texte="SEAMTECH provenance",
            confiance=91.0,
            succes=True,
            motif="",
            moteur=self.nom,
            version_moteur=self._version,
        )

    def fermer(self) -> None:  # pragma: no cover - rien à libérer
        return None


@pytest.fixture()
def moteur_injecte(monkeypatch):
    """Installe le moteur factice et une version Tesseract concurrente."""
    instance = _MoteurParesseux()
    monkeypatch.setattr(module_moteur, "choisir_moteur", lambda *_args, **_kwargs: instance)
    # Une version Tesseract existe sur le poste : c'est elle que l'ancien
    # agrégat utilisait en repli, et c'est donc elle que le test doit REFUSER.
    monkeypatch.setattr(pipeline, "_tesseract_version", lambda *_args, **_kwargs: VERSION_TESSERACT)
    return instance


def test_le_rapport_nomme_la_version_du_moteur_qui_a_tourne(moteur_injecte) -> None:
    if not PDF_PROPRE.is_file():  # pragma: no cover - échantillon commité
        pytest.skip("échantillon propre absent")
    resume = ocriser_fichier(PDF_PROPRE, langue="fra", tesseract_command="tesseract")

    assert moteur_injecte.appels >= 1, "le moteur factice n'a rien reconnu"
    assert resume["moteur"] == NOM_MOTEUR
    # Le point du test : ni la version de Tesseract, ni le vide.
    assert resume["version_moteur"] == VERSION_RAPIDE, (
        "le compte rendu attribue la version d'un moteur qui n'a pas tourné : "
        f"{resume['version_moteur']!r} (attendu {VERSION_RAPIDE!r})"
    )
    for page in resume["pages"]:
        if page["moteur"]:
            assert page["version_moteur"] == VERSION_RAPIDE, page


def test_aucune_page_ne_doit_une_version_a_un_autre_moteur(moteur_injecte) -> None:
    """Une page en échec ne rapporte pas davantage la version d'un tiers."""
    if not PDF_PROPRE.is_file():  # pragma: no cover - échantillon commité
        pytest.skip("échantillon propre absent")
    vide = [p for p in ocriser_fichier(PDF_PROPRE, langue="fra")["pages"] if not p["texte_ocr"]]
    for page in vide:
        assert page["version_moteur"] in (None, VERSION_RAPIDE), page
