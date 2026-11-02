"""Le refus de l'OCR hors POSIX est un COMPORTEMENT, pas un accident.

Pourquoi ce fichier existe.

La qualification Windows du 02/11/2026 (run 41467111570) a produit dix-huit
échecs dont le rapport ne disait rien : ``assert None is not None``,
``assert 0 == 1``, ``assert 'page image synthetique' in 'Page native…'``. Aucun
message, aucune cause — donc rien à réparer, et rien à déclarer honnêtement.

La cause est dans le produit, et elle est VOLONTAIRE : ``_rendre_page_pdf_borne``
et ``_extraire_images_pdf_borne`` rendent ``None`` / ``[]`` dès que
``os.name != "posix"``, parce que le confinement du rendu (sous-processus aux
ressources bornées, ``/proc``) n'existe que sur POSIX. Sur Windows, l'OCR d'une
page PDF est donc indisponible PAR CONSTRUCTION.

Un refus volontaire qui n'est pas testé est indistinguishable d'une panne. Ce
fichier le transforme en contrat : hors POSIX, le produit refuse, il ne crée
rien, il ne lance pas de sous-processus, et il DIT pourquoi — avec la
plateforme, comme ``plateforme.exiger`` le fait ailleurs.

Ces tests passent sur les deux plateformes : sur POSIX ils vérifient la garde
en simulant ``os.name = "nt"``, sur Windows ils vérifient le comportement réel.
C'est ce qui permet d'inscrire les écarts OCR de ``ci/ecarts-windows.txt``
comme une limite NOMMÉE et non comme une supposition.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from seamtech_search.ocr import pipeline


class OsNonPosix:
    """Module ``os`` factice : tout est délégué au vrai, sauf ``name``.

    On ne PATCHE PAS ``os.name`` globalement : ``pathlib`` instancie alors
    ``WindowsPath`` et pytest lui-même s'effondre en plein rendu d'échec
    (``NotImplementedError: cannot instantiate 'WindowsPath'``), ce qui
    masquerait le vrai résultat. La bascule est donc locale au module testé.
    """

    name = "nt"

    def __init__(self) -> None:
        self._reel = os

    def __getattr__(self, attribut: str):  # type: ignore[no-untyped-def]
        return getattr(self._reel, attribut)


class SousProcessusInterdit:
    """Remplace ``subprocess`` dans le module testé : tout lancement est une faute."""

    def __init__(self) -> None:
        self.appels: list[list[str]] = []

    def run(self, commande: list[str], *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        self.appels.append(list(commande))
        raise AssertionError(f"sous-processus lancé hors POSIX : {commande}")

    def __getattr__(self, attribut: str):  # type: ignore[no-untyped-def]
        return getattr(subprocess, attribut)


@pytest.fixture
def hors_posix(monkeypatch: pytest.MonkeyPatch, sous_processus: SousProcessusInterdit):
    """Bascule le module OCR en « système non POSIX » (ce qu'est Windows)."""
    monkeypatch.setattr(pipeline, "os", OsNonPosix())
    monkeypatch.setattr(pipeline, "subprocess", sous_processus)
    return sous_processus


@pytest.fixture
def sous_processus() -> SousProcessusInterdit:
    return SousProcessusInterdit()


def test_rendu_refuse_hors_posix_sans_sous_processus(
    tmp_path: Path, hors_posix: SousProcessusInterdit
) -> None:
    pdf = tmp_path / "page.pdf"
    pdf.write_bytes(b"%PDF-1.7 synthetique")

    assert pipeline._rendre_page_pdf_borne(pdf, 0, tmp_path, dpi=72, timeout_s=10) is None
    # Aucun fichier de rendu laissé derrière : un refus ne salit pas le dossier.
    assert list(tmp_path.glob("rendu-ocr-*.png")) == []
    assert hors_posix.appels == [], "un sous-processus de rendu a été lancé malgré le refus"


def test_extraction_image_refuse_hors_posix_sans_sous_processus(
    tmp_path: Path, hors_posix: SousProcessusInterdit
) -> None:
    pdf = tmp_path / "page.pdf"
    pdf.write_bytes(b"%PDF-1.7 synthetique")

    assert pipeline._extraire_images_pdf_borne(pdf, 0, tmp_path, 10) == []
    assert list(tmp_path.glob("image-ocr-*.png")) == []
    assert hors_posix.appels == [], "un sous-processus d'extraction a été lancé malgré le refus"


def test_ocr_page_refuse_hors_posix_en_nommant_la_plateforme(
    tmp_path: Path, hors_posix: SousProcessusInterdit
) -> None:
    """Un refus anonyme coûte une journée à l'atelier : il doit nommer la plateforme."""
    pdf = tmp_path / "page.pdf"
    pdf.write_bytes(b"%PDF-1.7 synthetique")

    resultat = pipeline._ocr_page_pdf(pdf, 0, "fra", 10, "tesseract", 72, tmp_path)

    assert resultat["succes"] is False, "un refus ne doit jamais se présenter comme une réussite"
    assert resultat["texte"] == ""
    motif = str(resultat["motif"])
    assert "POSIX" in motif, f"le motif doit nommer l'exigence POSIX : {motif!r}"
    assert "os.name=nt" in motif, f"le motif doit nommer la plateforme : {motif!r}"
    assert "réglage" in motif, f"le motif doit dire que ça ne se contourne pas : {motif!r}"
    assert hors_posix.appels == []


def test_sur_posix_la_garde_ne_mord_pas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Garde-fou de NON-régression : la garde ne doit pas mordre sur POSIX.

    Ce test ne vérifie pas que le rendu RÉUSSIT (il exigerait poppler et un PDF
    réel) mais que la branche « hors POSIX » n'est PAS prise : sinon un refus
    silencieux désactiverait l'OCR partout, y compris en production Linux, sans
    qu'aucun test ne bronche. La preuve est le LANCEMENT du sous-processus de
    rendu : la garde aurait rendu ``None`` avant de l'atteindre.
    """
    if os.name != "posix":  # pragma: no cover — exécuté tel quel sur Windows
        pytest.skip("la garde POSIX ne se vérifie que sur un système POSIX")

    appels: list[list[str]] = []

    class FauxSubprocess:
        @staticmethod
        def run(commande: list[str], *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            appels.append(list(commande))
            return subprocess.CompletedProcess(commande, 0, "", "")

        def __getattr__(self, attribut: str):  # type: ignore[no-untyped-def]
            return getattr(subprocess, attribut)

    monkeypatch.setattr(pipeline, "subprocess", FauxSubprocess())
    pdf = tmp_path / "page.pdf"
    pdf.write_bytes(b"%PDF-1.7 synthetique")

    pipeline._rendre_page_pdf_borne(pdf, 0, tmp_path, dpi=72, timeout_s=10)

    assert appels, "sur POSIX le rendu doit tenter le sous-processus : la garde a mordu à tort"
    assert appels[0][1:] == ["-m", "seamtech_search.ocr.rendu_pdf"], f"commande inattendue : {appels[0]}"
