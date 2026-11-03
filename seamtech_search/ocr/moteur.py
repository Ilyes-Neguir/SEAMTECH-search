"""Moteurs de reconnaissance OCR locaux — RG14 : aucun appel réseau.

Deux moteurs coexistent, derrière une seule interface :

``rapidocr`` (PP-OCRv6, ONNX)
    Moteur PAR DÉFAUT dès que la roue est installée. Les modèles sont livrés
    dans la roue et leurs chemins sont passés explicitement au moteur : aucun
    téléchargement n'est jamais tenté, y compris au premier appel. Il tourne
    dans un sous-processus borné (mémoire, CPU, délai) comme le rendu PDF.

``tesseract`` (binaire externe)
    Conservé pour les environnements où la roue ONNX n'est pas installable.
    Mesuré nettement moins fiable sur les identifiants alphanumériques
    alphabétiques/décimaux que porte une fiche (voir ``docs/OCR_MOTEURS.md``),
    il reste un repli assumé, jamais le moteur par défaut quand RapidOCR est
    disponible.

Mesures de référence (jeu synthétique d'identifiants, images réellement
soumises par le produit) : RapidOCR 5/5 reconnus exactement, Tesseract 1/5
(confusions 0/O et 1/I).

Sélection
    ``SEAMTECH_OCR_MOTEUR`` = ``auto`` (défaut) | ``rapidocr`` | ``tesseract``.
    Un ``tesseract_command`` explicite — c'est-à-dire différent de la valeur
    par défaut ``tesseract`` — désigne un binaire Tesseract précis et sélectionne
    donc Tesseract : c'est ainsi que les tests branchent un faux moteur.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, NamedTuple, Protocol

MOTEUR_RAPIDOCR = "rapidocr"
MOTEUR_TESSERACT = "tesseract"
MOTEUR_AUTO = "auto"

_COMMANDE_PAR_DEFAUT = "tesseract"
_ENV_MOTEUR = "SEAMTECH_OCR_MOTEUR"
_MODULE_ESCLAVE = "seamtech_search.ocr.reconnaissance"

LOGGER = logging.getLogger("seamtech_search.ocr.moteur")

# Chargement des modèles ONNX au démarrage du sous-processus esclave.
_DELAI_AMORCAGE_S = 180


class ResultatOcr(NamedTuple):
    """Résultat brut d'un moteur, sans aucun contenu dans les motifs d'échec.

    Les noms des champs sont ceux attendus par ``ocr/pipline.py`` : le
    résultat se convertit en dict par ``_asdict()`` sans recopie.
    """

    texte: str
    confiance: float | None
    succes: bool
    motif: str
    moteur: str
    version_moteur: str | None


class MoteurOcr(Protocol):
    """Interface commune : une image entre, du texte et une confiance sortent."""

    nom: str
    version: str | None

    def reconnaitre(self, image: Path, timeout_s: int, langue: str = "fra") -> ResultatOcr: ...

    def fermer(self) -> None: ...


class MoteurTesseract:
    """Tesseract externe : binaire + données de langue installés à côté."""

    nom = MOTEUR_TESSERACT

    def __init__(self, commande: str = _COMMANDE_PAR_DEFAUT) -> None:
        self._commande = commande

    @property
    def version(self) -> str | None:
        from seamtech_search.ocr.pipeline import _tesseract_version

        return _tesseract_version(self._commande)

    def reconnaitre(self, image: Path, timeout_s: int, langue: str = "fra") -> ResultatOcr:
        from seamtech_search.ocr.pipeline import _ocr_image_via_tesseract

        brut = _ocr_image_via_tesseract(image, langue, timeout_s, self._commande)
        return ResultatOcr(
            texte=brut.get("texte", ""),
            confiance=brut.get("confiance"),
            succes=bool(brut.get("succes")),
            motif=brut.get("motif", ""),
            moteur=brut.get("moteur", MOTEUR_TESSERACT),
            version_moteur=brut.get("version_moteur"),
        )

    def fermer(self) -> None:
        return None


class MoteurRapidOCR:
    """RapidOCR dans un sous-processus esclave, modèle chargé une seule fois.

    L'esclave est démarré paresseusement au premier appel (les images à
    reconnaître sont celles que l'appelant a déposées dans son
    ``tempfile.TemporaryDirectory``, jamais un document d'origine) et réutilisé pour
    toutes les pages : charger les modèles ONNX à chaque page coûterait plus
    que la reconnaissance elle-même. Un délai dépassé tue l'esclave et rend le
    moteur définitivement indisponible pour ce lot (jamais de relance en
    boucle), ce qui fait échouer la page proprement au lieu de la publier à
    moitié.
    """

    nom = MOTEUR_RAPIDOCR

    def __init__(self, langue: str = "fra") -> None:
        self._langue = langue
        self._processus: subprocess.Popen[str] | None = None
        self._files: queue.Queue[str | None] = queue.Queue()
        self._version: str | None = None
        self._mort = False

    @property
    def version(self) -> str | None:
        return self._version

    # -- esclave -------------------------------------------------------------
    def _demarrer(self, timeout_s: int) -> bool:
        if self._mort:
            return False
        try:
            processus = subprocess.Popen(
                [sys.executable, "-m", _MODULE_ESCLAVE],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError:
            self._mort = True
            return False
        self._processus = processus
        threading.Thread(target=self._lire, args=(processus,), daemon=True).start()
        try:
            processus.stdin.write(json.dumps({"timeout": int(timeout_s)}) + "\n")
            processus.stdin.flush()
        except (OSError, ValueError):
            self.fermer()
            return False
        accuse = self._lire_reponse(_DELAI_AMORCAGE_S)
        if not isinstance(accuse, dict) or not accuse.get("pret"):
            self.fermer()
            return False
        self._version = str(accuse.get("version") or MOTEUR_RAPIDOCR)
        return True

    def _lire(self, processus: subprocess.Popen[str]) -> None:
        try:
            assert processus.stdout is not None
            for ligne in processus.stdout:
                self._files.put(ligne)
        except (OSError, ValueError):
            pass
        finally:
            self._files.put(None)

    def _lire_reponse(self, timeout_s: int) -> dict[str, Any] | None:
        try:
            ligne = self._files.get(timeout=max(0.1, timeout_s))
        except queue.Empty:
            self.fermer()
            return None
        if ligne is None:
            self._mort = True
            return None
        try:
            reponse = json.loads(ligne)
        except ValueError:
            return None
        return reponse if isinstance(reponse, dict) else None

    # -- interface -----------------------------------------------------------
    def reconnaitre(self, image: Path, timeout_s: int, langue: str = "fra") -> ResultatOcr:
        if self._processus is None and not self._demarrer(timeout_s):
            return ResultatOcr("", None, False, "moteur RapidOCR indisponible", self.nom, self._version)
        assert self._processus is not None and self._processus.stdin is not None
        try:
            self._processus.stdin.write(
                json.dumps({"image": str(image), "timeout": int(timeout_s), "langue": langue or self._langue}) + "\n"
            )
            self._processus.stdin.flush()
        except (OSError, ValueError):
            self.fermer()
            return ResultatOcr("", None, False, "moteur RapidOCR interrompu", self.nom, self._version)
        reponse = self._lire_reponse(timeout_s)
        if reponse is None:
            return ResultatOcr("", None, False, f"timeout moteur RapidOCR ({timeout_s}s)", self.nom, self._version)
        return ResultatOcr(
            texte=str(reponse.get("texte") or ""),
            confiance=reponse.get("confiance"),
            succes=bool(reponse.get("succes")),
            motif=str(reponse.get("motif") or ""),
            moteur=self.nom,
            version_moteur=self._version,
        )

    def fermer(self) -> None:
        processus, self._processus = self._processus, None
        if processus is None:
            return
        try:
            if processus.stdin is not None:
                processus.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            processus.wait(timeout=5)
        except subprocess.TimeoutExpired:
            processus.kill()
            try:
                processus.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        except (OSError, ValueError):
            pass


def _rapidocr_installable() -> bool:
    """True si la roue ET ses modèles groupés sont présents (sans les charger)."""
    try:
        import importlib.util

        if importlib.util.find_spec("rapidocr") is None:
            return False
        import rapidocr

        dossier = Path(rapidocr.__file__).parent / "models"
        return any(p.is_file() and p.suffix == ".onnx" for p in dossier.glob("*.onnx"))
    except Exception:
        return False


def moteur_demande() -> str:
    """Moteur demandé par l'environnement, normalisé."""
    demande = (os.environ.get(_ENV_MOTEUR) or MOTEUR_AUTO).strip().lower()
    return demande if demande in {MOTEUR_RAPIDOCR, MOTEUR_TESSERACT} else MOTEUR_AUTO


def moteur_disponible(
    tesseract_command: str = _COMMANDE_PAR_DEFAUT,
    sonde: Callable[[str], bool] | None = None,
) -> bool:
    """Un moteur est-il utilisable ? (sans charger les modèles ONNX)

    ``sonde`` permet à l'appelant de fournir sa propre détection de binaire
    (les tests substituent celle de leur module) ; par défaut c'est
    ``ocr/pipeline._has_command``.
    """
    presente = sonde or _tesseract_disponible
    demande = moteur_demande()
    if demande == MOTEUR_RAPIDOCR:
        return _rapidocr_installable()
    if demande == MOTEUR_TESSERACT:
        return bool(presente(tesseract_command))
    if tesseract_command != _COMMANDE_PAR_DEFAUT:
        return bool(presente(tesseract_command))
    return _rapidocr_installable() or bool(presente(tesseract_command))


def _tesseract_disponible(tesseract_command: str) -> bool:
    from seamtech_search.ocr.pipeline import _has_command

    return bool(_has_command(tesseract_command))


def choisir_moteur(tesseract_command: str = _COMMANDE_PAR_DEFAUT) -> MoteurOcr | None:
    """Choisit le moteur à utiliser, ou None si aucun n'est exploitable."""
    demande = moteur_demande()
    if demande == MOTEUR_TESSERACT:
        moteur = MoteurTesseract(tesseract_command) if _tesseract_disponible(tesseract_command) else None
        if moteur is not None:
            _avertir_repli_tesseract("choix explicite (SEAMTECH_OCR_MOTEUR=tesseract)")
        return moteur
    if demande == MOTEUR_RAPIDOCR:
        return MoteurRapidOCR() if _rapidocr_installable() else None
    # auto
    if tesseract_command != _COMMANDE_PAR_DEFAUT:
        # Un binaire Tesseract explicitement désigné : l'opérateur (ou un test)
        # a choisi Tesseract, on ne lui substitue pas un autre moteur.
        moteur = MoteurTesseract(tesseract_command) if _tesseract_disponible(tesseract_command) else None
        if moteur is not None:
            _avertir_repli_tesseract("binaire Tesseract désigné nommément")
        return moteur
    if _rapidocr_installable():
        return MoteurRapidOCR()
    moteur = MoteurTesseract(tesseract_command) if _tesseract_disponible(tesseract_command) else None
    if moteur is not None:
        _avertir_repli_tesseract("roue rapidocr absente — repli automatique")
    return moteur


def _avertir_repli_tesseract(origine: str) -> None:
    """Dit ce que coûte le repli, au lieu de le laisser croire équivalent.

    Mesuré (``docs/OCR_MOTEURS.md``, §3) : RapidOCR reconnaît **5/5** les
    identifiants alphanumériques d'une fiche, Tesseract **1/5** — confusions
    ``0``/``O`` et ``1``/``I``. Le repli n'est donc pas une variante du
    moteur par défaut : c'est une qualité moindre sur **exactement ce que
    l'on recherche**. Le texte courant, lui, reste correct. Le taire
    laisserait croire à une reconnaissance égale.
    """
    LOGGER.warning(
        "OCR : moteur Tesseract retenu (%s). La reconnaissance des "
        "IDENTIFIANTS alphanumériques y est DÉGRADÉE : 1/5 mesuré contre 5/5 "
        "avec RapidOCR, le moteur par défaut (docs/OCR_MOTEURS.md §3). Le "
        "texte courant reste correct. Installer « pip install "
        "rapidocr==3.10.0 » (déjà dans requirements.txt) pour retrouver la "
        "qualité de référence.",
        origine,
    )
