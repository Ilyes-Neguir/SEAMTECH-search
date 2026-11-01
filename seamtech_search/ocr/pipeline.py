"""Pipeline OCR par page — Lot M.

Règle d'étage inviolable (plan v3.0 §4 bis) :
« Le texte des fiches est déjà dans le PDF : y appliquer de l'OCR n'ajouterait
que des erreurs. »

Donc :
- Étage 2 : si la page a du texte natif exploitable (>= seuil), on garde ce texte.
- Étage 3 : OCR seulement si la page n'a PAS de texte exploitable (< seuil).

La décision d'étage est une fonction pure testable doit_oceriser_page().

OCR via binaires (RG14 : aucun appel réseau) :
- tesseract -l fra (obligatoire pour l'étage 3)
- pdftoppm (poppler) en option pour rendre une page PDF en image
- ocrmypdf en option (déjà présent dans extractors.py)

Chaque page produit : texte, confiance, moteur+version, durée,
page_ocerisee bool, motif de non-OCR.

Aucune écriture dans le dossier source (RG13) : les images temporaires
vont dans tempfile.TemporaryDirectory, les résultats finaux dans le
répertoire de travail déclaré (SEAMTECH_OCR_TRAVAIL_DIR).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

# Seuil par défaut : en dessous de 20 caractères, la page est considérée
# sans texte exploitable. Justification :
# - La vraie fiche 7792-SO (texte natif) : chaque page > 200 caractères
# - Les scans d'archive (échantillons tests/fixtures/ocr/) : 0 caractère natif
# - Un numéro de page isolé ou un artefact fait typiquement < 10 caractères
# Donc 20 est une marge sûre entre bruit et contenu réel.
SEUIL_DEFAUT = 20

OCR_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def doit_oceriser_page(texte_natif: str, seuil: int = SEUIL_DEFAUT) -> bool:
    """Fonction pure testable : faut-il océriser cette page ?

    Règle inviolable : OCR seulement si la page n'a PAS de texte exploitable.

    Paramètres
    ----------
    texte_natif : str
        Texte extrait nativement (pdfplumber/pypdf).
    seuil : int
        Seuil en dessous duquel le texte est considéré inexploitable.
        Défaut 20 (justifié ci-dessus).

    Retour
    ------
    bool
        True si la page doit être océrisée (texte natif < seuil),
        False sinon (texte natif présent, on ne touche pas).
    """
    if texte_natif is None:
        return True
    return len(texte_natif.strip()) < seuil


def _has_command(command: str) -> bool:
    """Vérifie si un binaire est présent (sans appel réseau)."""
    return bool(shutil.which(command) or Path(command).is_file())


def _tesseract_version(tesseract_command: str = "tesseract") -> str | None:
    """Retourne la version de tesseract, ou None si absent."""
    if not _has_command(tesseract_command):
        return None
    try:
        result = subprocess.run(
            [tesseract_command, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Première ligne : "tesseract 5.3.0" etc.
        if result.returncode == 0:
            first_line = (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else ""
            return first_line.strip() or "tesseract (version inconnue)"
        return None
    except Exception:
        return None


def _texte_natif_par_page_pdf(chemin: Path) -> list[str]:
    """Lit les pages vérifiées, sans repli textuel ignorant la vue PDF."""
    if not chemin.is_file():
        raise RuntimeError("Fichier PDF indisponible pour inspection")
    try:
        import pdfplumber

        from seamtech_search.extractors import _texte_page_orientee

        textes: list[str] = []
        with pdfplumber.open(str(chemin)) as pdf:
            for page in pdf.pages:
                texte, _images, incertaine = _texte_page_orientee(page)
                if incertaine:
                    raise RuntimeError("Orientation PDF non vérifiée")
                textes.append(texte)
        return textes
    except Exception:
        # Aucun texte non vérifié ne doit être communiqué, ni déclencher un
        # OCR sur une vue dont on n'a pas pu inspecter les limites.
        raise RuntimeError("Inspection PDF indisponible ou vue non vérifiée") from None


def _ocr_image_via_tesseract(
    image_path: Path,
    langue: str = "fra",
    timeout_s: int = 30,
    tesseract_command: str = "tesseract",
) -> dict[str, Any]:
    """OCR d'une image via tesseract.

    Retour : dict avec texte, confiance, duree_s, succes, motif
    """
    debut = time.perf_counter()
    if not _has_command(tesseract_command):
        return {
            "texte": "",
            "confiance": None,
            "duree_s": time.perf_counter() - debut,
            "succes": False,
            "motif": "tesseract absent",
            "moteur": "tesseract",
            "version_moteur": None,
        }

    version = _tesseract_version(tesseract_command)

    try:
        # Texte
        result = subprocess.run(
            [tesseract_command, str(image_path), "stdout", "-l", langue],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if result.returncode != 0:
            return {
                "texte": "",
                "confiance": None,
                "duree_s": time.perf_counter() - debut,
                "succes": False,
                "motif": f"tesseract erreur rc={result.returncode}",
                "moteur": "tesseract",
                "version_moteur": version,
            }
        texte = result.stdout or ""

        # Confiance via TSV
        confiance: float | None = None
        try:
            result_tsv = subprocess.run(
                [tesseract_command, str(image_path), "stdout", "-l", langue, "tsv"],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            if result_tsv.returncode == 0:
                lignes = result_tsv.stdout.splitlines()
                confs: list[float] = []
                for ligne in lignes[1:]:  # Tesseract TSV : niveau 5 = mot
                    parts = ligne.split("\t", 11)
                    if len(parts) != 12 or parts[0] != "5" or not parts[11].strip():
                        continue
                    try:
                        c = float(parts[10])
                        # Refuse aussi NaN et les infinis ; -1 est un marqueur
                        # de niveau structurel sans mesure, pas une confiance.
                        if 0 <= c <= 100:
                            confs.append(c)
                    except ValueError:
                        continue
                if confs:
                    confiance = sum(confs) / len(confs)
        except Exception:
            confiance = None

        return {
            "texte": texte,
            "confiance": confiance,
            "duree_s": time.perf_counter() - debut,
            "succes": True,
            "motif": "",
            "moteur": "tesseract",
            "version_moteur": version,
        }

    except subprocess.TimeoutExpired:
        return {
            "texte": "",
            "confiance": None,
            "duree_s": time.perf_counter() - debut,
            "succes": False,
            "motif": f"timeout tesseract ({timeout_s}s)",
            "moteur": "tesseract",
            "version_moteur": version,
        }
    except Exception as exc:
        return {
            "texte": "",
            "confiance": None,
            "duree_s": time.perf_counter() - debut,
            "succes": False,
            "motif": f"erreur tesseract ({type(exc).__name__})",
            "moteur": "tesseract",
            "version_moteur": version,
        }


def _rendre_page_pdf_via_pdftoppm(
    pdf_path: Path, numero_page: int, dossier_temp: Path, dpi: int = 300
) -> Path | None:
    """Rend une page PDF en image PNG via pdftoppm (poppler).

    Retourne le chemin de l'image, ou None si pdftoppm absent ou échec.
    """
    if not _has_command("pdftoppm"):
        return None

    try:
        # pdftoppm numérote les pages à partir de 1 pour l'utilisateur
        # mais -f et -l attendent des numéros 1-based.
        prefix = dossier_temp / f"render_page_{numero_page}"
        # Supprime anciens rendus
        for old in dossier_temp.glob(f"render_page_{numero_page}*"):
            try:
                old.unlink()
            except OSError:
                pass

        result = subprocess.run(
            [
                "pdftoppm",
                "-f",
                str(numero_page + 1),
                "-l",
                str(numero_page + 1),
                "-r",
                str(dpi),
                "-png",
                str(pdf_path),
                str(prefix),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None

        # Cherche l'image produite : prefix-1.png ou prefix-000001.png etc.
        candidats = list(dossier_temp.glob(f"render_page_{numero_page}*.png"))
        if candidats:
            # Prend le plus récent
            candidats.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return candidats[0]
        return None
    except Exception:
        return None


def _rendre_page_pdf_borne(
    pdf_path: Path, numero_page: int, dossier_temp: Path, dpi: int = 300, timeout_s: int = 30
) -> Path | None:
    """Rend dans un sous-processus limité ; tout échec laisse l'OCR indisponible."""
    if os.name != "posix" or not 36 <= dpi <= 400 or not 1 <= timeout_s <= 120:
        return None
    sortie = dossier_temp / f"rendu-ocr-{uuid.uuid4().hex}.png"
    requete = json.dumps({
        "source": str(pdf_path), "page": numero_page, "sortie": str(sortie),
        "dpi": dpi, "timeout": timeout_s,
    })
    try:
        resultat = subprocess.run(
            [sys.executable, "-m", "seamtech_search.ocr.rendu_pdf"],
            input=requete, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, timeout=timeout_s, check=False,
        )
        if resultat.returncode == 0 and sortie.is_file():
            return sortie
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        sortie.unlink(missing_ok=True)
    except OSError:
        pass
    return None


def _extraire_images_pdf_borne(pdf_path: Path, numero_page: int, dossier_temp: Path, timeout_s: int) -> list[Path]:
    """Décode une image unique dans un processus aux ressources bornées."""
    if os.name != "posix" or not 1 <= timeout_s <= 120:
        return []
    sortie = dossier_temp / f"image-ocr-{uuid.uuid4().hex}.png"
    requete = json.dumps({
        "operation": "extraire_image", "source": str(pdf_path), "page": numero_page,
        "sortie": str(sortie), "timeout": timeout_s,
    })
    try:
        resultat = subprocess.run(
            [sys.executable, "-m", "seamtech_search.ocr.rendu_pdf"],
            input=requete, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, timeout=timeout_s, check=False,
        )
        if resultat.returncode == 0 and sortie.is_file():
            return [sortie]
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        sortie.unlink(missing_ok=True)
    except OSError:
        pass
    return []


def _page_pdf_vue_integrale_bornee(pdf_path: Path, numero_page: int, timeout_s: int) -> bool:
    """Précontrôle exécuté hors du parent, sous limites de temps et mémoire."""
    if os.name != "posix" or not 1 <= timeout_s <= 120:
        return False
    requete = json.dumps({"operation": "verifier_image", "source": str(pdf_path),
                          "page": numero_page, "timeout": timeout_s})
    try:
        resultat = subprocess.run(
            [sys.executable, "-m", "seamtech_search.ocr.rendu_pdf"],
            input=requete, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, timeout=timeout_s, check=False,
        )
        return resultat.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _page_pdf_vue_integrale(pdf_path: Path, numero_page: int) -> bool:
    """Vérifie une image unique sans masque ni composition hors page.

    OCR d'images brutes ne respecte ni clipping ni éléments qui les masquent.
    La grammaire acceptée est volontairement restrictive ; un rendu PDF
    complet est nécessaire pour prendre en charge les autres cas sans fuite.
    """
    try:
        from pypdf import PdfReader
        from pypdf.generic import ContentStream

        source = pdf_path if hasattr(pdf_path, "read") else str(pdf_path)
        lecteur = PdfReader(source, strict=False)
        catalogue = lecteur.trailer["/Root"]
        if any(catalogue.get(cle) is not None for cle in ("/OCProperties", "/AcroForm")):
            return False
        page = lecteur.pages[numero_page]
        if any(page.get(cle) is not None for cle in ("/Annots", "/Group", "/AA")):
            return False
        media = tuple(float(v) for v in page.mediabox)
        crop = tuple(float(v) for v in page.cropbox)
        if not all(math.isfinite(v) for v in (*media, *crop)) or media != crop:
            return False
        contenu = page.get_contents()
        if contenu is None:
            return False
        operations = ContentStream(contenu, page.pdf).operations
        ressources = page["/Resources"].get_object()
        objets = ressources["/XObject"].get_object()
        # page.images énumère les ressources, y compris celles qui ne sont
        # jamais dessinées : une seule ressource doit être présente.
        if len(objets) != 1:
            return False
        matrice = (1., 0., 0., 1., 0., 0.)
        pile: list[tuple[float, ...]] = []
        images = 0
        for arguments, op in operations:
            if op == b"q":
                pile.append(matrice)
            elif op == b"Q":
                if not pile:
                    return False
                matrice = pile.pop()
            elif op == b"cm":
                a, b, c, d, e, f = (float(v) for v in arguments)
                A, B, C, D, E, F = matrice
                matrice = (A*a+C*b, B*a+D*b, A*c+C*d, B*c+D*d, A*e+C*f+E, B*e+D*f+F)
            elif op == b"Do":
                objet = objets[arguments[0]].get_object()
                if objet.get("/Subtype") != "/Image" or any(
                    objet.get(cle) is not None for cle in ("/SMask", "/Mask", "/ImageMask", "/Decode", "/OC", "/SMaskInData")
                ):
                    return False
                largeur, hauteur = int(objet["/Width"]), int(objet["/Height"])
                if min(largeur, hauteur) < 1 or max(largeur, hauteur) > 8192 or largeur * hauteur > 25_000_000:
                    return False
                images += 1
                a, b, c, d, e, f = matrice
                if not all(math.isfinite(v) for v in matrice):
                    return False
                # Pas de rotation, inversion ou cisaillement : l'image brute
                # doit être présentée telle quelle, entièrement dans la page.
                if b != 0 or c != 0 or a <= 0 or d <= 0:
                    return False
                if e < media[0] or f < media[1] or e+a > media[2] or f+d > media[3]:
                    return False
            elif op not in (b"BT", b"ET", b"Tf", b"TL"):
                # Texte, formes, image inline, transparence et clipping
                # pourraient masquer/modifier l'image ; ne pas les contourner.
                return False
        return images == 1 and not pile
    except Exception:
        return False


def _nom_moteur_absent(tesseract_command: str) -> str:
    """Nom déclaré quand aucun moteur n'est exploitable."""
    from seamtech_search.ocr.moteur import MOTEUR_TESSERACT, moteur_demande

    demande = moteur_demande()
    if demande == MOTEUR_TESSERACT or tesseract_command != "tesseract":
        return MOTEUR_TESSERACT
    return MOTEUR_TESSERACT if demande != "rapidocr" else "rapidocr"


def _motif_moteur_absent(tesseract_command: str) -> str:
    """Motif lisible quand l'OCR ne peut pas être exécuté (jamais de repli)."""
    from seamtech_search.ocr.moteur import MOTEUR_TESSERACT, moteur_demande

    demande = moteur_demande()
    if demande == MOTEUR_TESSERACT or tesseract_command != "tesseract":
        return "tesseract absent (OCR désactivé, statut unavailable)"
    if demande == "rapidocr":
        return "RapidOCR absent (OCR désactivé, statut unavailable)"
    return "moteur OCR absent : ni RapidOCR ni tesseract (OCR désactivé, statut unavailable)"


def _ocr_page_pdf(
    pdf_path: Path,
    numero_page: int,
    langue: str,
    timeout_s: int,
    tesseract_command: str,
    dpi: int,
    dossier_temp: Path,
    moteur: Any = None,
) -> dict[str, Any]:
    """OCR d'une page PDF (scannée).

    Stratégie :
    1. Pour une image unique vérifiée, la décoder dans un sous-processus borné.
    2. Sinon, rendre la vue PDF dans un sous-processus borné.
    3. En cas d'échec, refuser sans repli vers une lecture brute non confinée.

    ``moteur`` est l'interface commune (``ocr/moteur.py``) : RapidOCR par
    défaut, Tesseract en repli. Sans moteur fourni, Tesseract est utilisé
    avec ``tesseract_command`` — comportement historique.
    """
    from seamtech_search.ocr.moteur import MoteurTesseract

    moteur = moteur or MoteurTesseract(tesseract_command)
    version = moteur.version
    nom_moteur = moteur.nom

    def reconnaitre(image: Path) -> dict[str, Any]:
        debut = time.perf_counter()
        resultat = moteur.reconnaitre(image, timeout_s, langue)._asdict()
        resultat["duree_s"] = time.perf_counter() - debut
        return resultat

    if not _page_pdf_vue_integrale_bornee(pdf_path, numero_page, timeout_s):
        rendu = _rendre_page_pdf_borne(pdf_path, numero_page, dossier_temp, dpi, timeout_s)
        if rendu is not None:
            return reconnaitre(rendu)
        return {
            "texte": "", "confiance": None, "duree_s": 0.0, "succes": False,
            "motif": "OCR impossible : vue PDF recadrée, composée ou indéterminée",
            "moteur": nom_moteur, "version_moteur": version,
        }

    # 1. Images extraites
    images = _extraire_images_pdf_borne(pdf_path, numero_page, dossier_temp, timeout_s)
    if images:
        textes: list[str] = []
        confs: list[float] = []
        duree_tot = 0.0
        echec_any = False
        timeout_any = False

        for img_path in images:
            res = reconnaitre(img_path)
            duree_tot += res["duree_s"]
            if res["succes"]:
                if res["texte"].strip():
                    textes.append(res["texte"])
                if res["confiance"] is not None:
                    confs.append(res["confiance"])
            else:
                echec_any = True
                timeout_any = timeout_any or "timeout" in res["motif"].lower()

        if not echec_any:
            return {
                "texte": "\n".join(textes),
                "confiance": sum(confs) / len(confs) if confs else None,
                "duree_s": duree_tot,
                "succes": True,
                "motif": "",
                "moteur": nom_moteur,
                "version_moteur": version,
            }
        else:
            # Images trouvées mais OCR a échoué
            return {
                "texte": "",
                "confiance": None,
                "duree_s": duree_tot,
                "succes": False,
                "motif": (
                    f"timeout du moteur {nom_moteur} sur une ou plusieurs images de la page"
                    if timeout_any else f"échec du moteur {nom_moteur} sur une ou plusieurs images de la page"
                ),
                "moteur": nom_moteur,
                "version_moteur": version,
            }

    return {
        "texte": "", "confiance": None, "duree_s": 0.0, "succes": False,
        "motif": "OCR impossible : extraction bornée de l'image avant reconnaissance en échec",
        "moteur": nom_moteur, "version_moteur": version,
    }


def ocriser_pages(
    chemin: Path,
    pages: list[int] | None = None,
    seuil: int = SEUIL_DEFAUT,
    langue: str = "fra",
    timeout_par_page_s: int = 30,
    tesseract_command: str = "tesseract",
    resolution_dpi: int = 300,
) -> list[dict[str, Any]]:
    """OCR par pages — cœur testable (choix du moteur inclus)."""
    from seamtech_search.ocr.moteur import choisir_moteur

    moteur = choisir_moteur(tesseract_command)
    try:
        return _ocriser_pages_avec_moteur(
            chemin,
            pages=pages,
            seuil=seuil,
            langue=langue,
            timeout_par_page_s=timeout_par_page_s,
            tesseract_command=tesseract_command,
            resolution_dpi=resolution_dpi,
            moteur=moteur,
        )
    finally:
        # L'esclave RapidOCR (s'il a été démarré) ne survit pas au fichier.
        if moteur is not None:
            moteur.fermer()


def _ocriser_pages_avec_moteur(
    chemin: Path,
    pages: list[int] | None = None,
    seuil: int = SEUIL_DEFAUT,
    langue: str = "fra",
    timeout_par_page_s: int = 30,
    tesseract_command: str = "tesseract",
    resolution_dpi: int = 300,
    moteur: Any = None,
) -> list[dict[str, Any]]:
    """OCR par pages — cœur testable.

    Paramètres
    ----------
    chemin : Path
        Fichier PDF ou image.
    pages : list[int] | None
        Pages à traiter (0-based). None = toutes.
    seuil : int
        Seuil de texte natif.
    langue : str
        Langue tesseract (défaut fra).
    timeout_par_page_s : int
        Timeout par page.
    tesseract_command : str
        Binaire tesseract.
    resolution_dpi : int
        Résolution pour rendu PDF (pdftoppm).

    Retour
    ------
    list[dict] par page : {
        page, texte_natif, texte_ocr, texte_final,
        confiance, moteur, version_moteur, duree_s,
        page_ocerisee bool, motif
    }
    """
    chemin = Path(chemin)
    ext = chemin.suffix.lower()

    moteur_present = moteur is not None
    nom_absent = _nom_moteur_absent(tesseract_command)
    motif_absent = _motif_moteur_absent(tesseract_command)

    resultats: list[dict[str, Any]] = []

    if ext in OCR_IMAGE_EXTENSIONS:
        # Image isolée : 1 page, toujours à océriser (M.3)
        # Texte natif = "" donc doit_oceriser_page = True
        with tempfile.TemporaryDirectory(prefix="seamtech-ocr-img-") as tmpdir:
            tmp_path = Path(tmpdir)
            # Pour uniformité, on copie ou utilise directement
            if moteur is None:
                ocr_res = {
                    "texte": "", "confiance": None, "succes": False, "motif": motif_absent,
                    "moteur": nom_absent, "version_moteur": None, "duree_s": 0.0,
                }
            else:
                debut = time.perf_counter()
                ocr_res = moteur.reconnaitre(chemin, timeout_par_page_s, langue)._asdict()
                ocr_res["duree_s"] = time.perf_counter() - debut
            texte_natif = ""
            page_ocerisee = ocr_res["succes"] and bool(ocr_res["texte"].strip())
            motif = "" if page_ocerisee else ocr_res["motif"] or "aucun texte détecté"

            resultats.append(
                {
                    "page": 0,
                    "texte_natif": texte_natif,
                    "texte_ocr": ocr_res["texte"],
                    "texte_final": ocr_res["texte"] if page_ocerisee else texte_natif,
                    "confiance": ocr_res["confiance"],
                    "moteur": ocr_res["moteur"],
                    "version_moteur": ocr_res["version_moteur"],
                    "duree_s": ocr_res["duree_s"],
                    "page_ocerisee": page_ocerisee,
                    "motif": motif,
                    "fichier": str(chemin),
                }
            )
        return resultats

    elif ext == ".pdf":
        textes_natifs = _texte_natif_par_page_pdf(chemin)
        nb_pages = len(textes_natifs)

        if pages is None:
            pages_a_traiter = list(range(nb_pages))
        else:
            pages_a_traiter = [p for p in pages if 0 <= p < nb_pages]

        with tempfile.TemporaryDirectory(prefix="seamtech-ocr-pdf-") as tmpdir:
            tmp_path = Path(tmpdir)

            for num_page in pages_a_traiter:
                texte_natif = textes_natifs[num_page] if num_page < len(textes_natifs) else ""
                doit_ocr = doit_oceriser_page(texte_natif, seuil)

                if not doit_ocr:
                    # Texte natif présent : on NE TOUCHE PAS (règle inviolable)
                    resultats.append(
                        {
                            "page": num_page,
                            "texte_natif": texte_natif,
                            "texte_ocr": "",
                            "texte_final": texte_natif,
                            "confiance": None,
                            "moteur": "natif",
                            "version_moteur": None,
                            "duree_s": 0.0,
                            "page_ocerisee": False,
                            "motif": "texte natif présent",
                            "fichier": str(chemin),
                        }
                    )
                    continue

                # Doit océriser
                if not moteur_present:
                    resultats.append(
                        {
                            "page": num_page,
                            "texte_natif": texte_natif,
                            "texte_ocr": "",
                            "texte_final": texte_natif,
                            "confiance": None,
                            "moteur": nom_absent,
                            "version_moteur": None,
                            "duree_s": 0.0,
                            "page_ocerisee": False,
                            "motif": motif_absent,
                            "fichier": str(chemin),
                        }
                    )
                    continue

                ocr_res = _ocr_page_pdf(
                    chemin,
                    num_page,
                    langue,
                    timeout_par_page_s,
                    tesseract_command,
                    resolution_dpi,
                    tmp_path,
                    moteur=moteur,
                )

                page_ocerisee = ocr_res["succes"] and bool(ocr_res["texte"].strip())
                motif = "" if page_ocerisee else ocr_res["motif"] or "aucun texte détecté"

                resultats.append(
                    {
                        "page": num_page,
                        "texte_natif": texte_natif,
                        "texte_ocr": ocr_res["texte"],
                        "texte_final": ocr_res["texte"] if page_ocerisee else texte_natif,
                        "confiance": ocr_res["confiance"],
                        "moteur": ocr_res["moteur"],
                        "version_moteur": ocr_res["version_moteur"],
                        "duree_s": ocr_res["duree_s"],
                        "page_ocerisee": page_ocerisee,
                        "motif": motif,
                        "fichier": str(chemin),
                    }
                )

            # Pages non listées ? Si pages=None on a tout, sinon on peut
            # compléter avec les pages ignorées (texte natif) pour rapport complet
            if pages is not None:
                # Ajoute les pages non demandées comme ignorées (pour info)
                for num_page in range(nb_pages):
                    if num_page not in pages_a_traiter:
                        texte_natif = textes_natifs[num_page] if num_page < len(textes_natifs) else ""
                        resultats.append(
                            {
                                "page": num_page,
                                "texte_natif": texte_natif,
                                "texte_ocr": "",
                                "texte_final": texte_natif,
                                "confiance": None,
                                "moteur": "natif",
                                "version_moteur": None,
                                "duree_s": 0.0,
                                "page_ocerisee": False,
                                "motif": "page non demandée",
                                "fichier": str(chemin),
                            }
                        )

        # Tri par page
        resultats.sort(key=lambda r: r["page"])
        return resultats

    else:
        # Type non supporté
        return [
            {
                "page": 0,
                "texte_natif": "",
                "texte_ocr": "",
                "texte_final": "",
                "confiance": None,
                "moteur": "",
                "version_moteur": None,
                "duree_s": 0.0,
                "page_ocerisee": False,
                "motif": f"type non supporté: {ext}",
                "fichier": str(chemin),
            }
        ]


def ocriser_fichier(
    chemin: str | Path,
    seuil: int = SEUIL_DEFAUT,
    langue: str = "fra",
    timeout_par_page_s: int = 30,
    tesseract_command: str = "tesseract",
    resolution_dpi: int = 300,
    pages: list[int] | None = None,
) -> dict[str, Any]:
    """OCR d'un fichier complet (PDF ou image).

    Retourne un dict avec métadonnées fichier + liste pages.
    """
    chemin_p = Path(chemin)
    debut = time.perf_counter()

    # Empreinte SHA-256 (pour idempotence)
    try:
        h = hashlib.sha256()
        with open(chemin_p, "rb") as f:
            for bloc in iter(lambda: f.read(1024 * 1024), b""):
                h.update(bloc)
        empreinte = h.hexdigest()
    except Exception:
        empreinte = ""

    pages_res = ocriser_pages(
        chemin_p,
        pages=pages,
        seuil=seuil,
        langue=langue,
        timeout_par_page_s=timeout_par_page_s,
        tesseract_command=tesseract_command,
        resolution_dpi=resolution_dpi,
    )

    duree_tot = time.perf_counter() - debut
    nb_ocerisees = sum(1 for p in pages_res if p["page_ocerisee"])
    nb_ignorees = sum(1 for p in pages_res if not p["page_ocerisee"] and p["motif"] == "texte natif présent")
    nb_echecs = sum(1 for p in pages_res if not p["page_ocerisee"] and p["motif"] not in ("texte natif présent", "page non demandée", ""))
    taille_texte = sum(len(p["texte_ocr"]) for p in pages_res)

    # Moteur le plus fréquent
    moteurs = [p["moteur"] for p in pages_res if p["moteur"]]
    moteur_principal = moteurs[0] if moteurs else ""
    versions = [p["version_moteur"] for p in pages_res if p["version_moteur"]]
    version_principale = versions[0] if versions else _tesseract_version(tesseract_command)

    return {
        "fichier": str(chemin_p),
        "empreinte_sha256": empreinte,
        "nb_pages": len(pages_res),
        "pages": pages_res,
        "nb_pages_ocerisees": nb_ocerisees,
        "nb_pages_ignorees_texte_natif": nb_ignorees,
        "nb_echecs": nb_echecs,
        "taille_texte_ocr": taille_texte,
        "duree_s": duree_tot,
        "moteur": moteur_principal,
        "version_moteur": version_principale,
        "seuil": seuil,
        "langue": langue,
        "resolution_dpi": resolution_dpi,
    }
