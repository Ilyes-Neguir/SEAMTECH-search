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
import shutil
import subprocess
import tempfile
import time
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
    """Extrait le texte natif par page (pdfplumber puis pypdf)."""
    try:
        import pdfplumber  # type: ignore

        texts: list[str] = []
        with pdfplumber.open(str(chemin)) as pdf:
            for page in pdf.pages:
                try:
                    t = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
                except Exception:
                    t = ""
                texts.append(t)
        if texts:
            return texts
    except Exception:
        pass

    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(chemin), strict=False)
        texts = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            texts.append(t)
        return texts
    except Exception:
        return []


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
                "motif": f"tesseract erreur rc={result.returncode}: {result.stderr[:200]}",
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
                confs: list[int] = []
                for ligne in lignes[1:]:  # skip header
                    parts = ligne.split("\t")
                    if len(parts) >= 11:
                        try:
                            c = int(parts[10])
                            if c >= 0:
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
            "motif": f"erreur tesseract: {type(exc).__name__}: {exc}",
            "moteur": "tesseract",
            "version_moteur": version,
        }


def _extraire_images_pypdf(
    pdf_path: Path, numero_page: int, dossier_temp: Path
) -> list[Path]:
    """Extrait les images d'une page PDF via pypdf, les sauve en temp.

    Retourne liste de chemins d'images extraites.
    """
    images_extraites: list[Path] = []
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(pdf_path), strict=False)
        if numero_page >= len(reader.pages):
            return []
        page = reader.pages[numero_page]

        # pypdf >= 3.0 : page.images
        try:
            for idx, img in enumerate(page.images):
                try:
                    # img.name peut contenir extension
                    nom = getattr(img, "name", f"image_{numero_page}_{idx}")
                    # Assure une extension
                    if "." not in nom:
                        nom = f"{nom}.png"
                    chemin_img = dossier_temp / f"page{numero_page}_{idx}_{nom}"
                    # img.image est PIL Image si disponible
                    pil_img = getattr(img, "image", None)
                    if pil_img is not None:
                        pil_img.save(str(chemin_img))
                        images_extraites.append(chemin_img)
                    else:
                        # Fallback : data brut
                        data = getattr(img, "data", None)
                        if data:
                            chemin_img.write_bytes(data)
                            images_extraites.append(chemin_img)
                except Exception:
                    continue
        except Exception:
            # Ancienne API ou pas d'images
            pass

        # Si rien via page.images, tente extraction manuelle via XObject
        if not images_extraites:
            try:
                xobjects = page["/Resources"]["/XObject"].get_object()  # type: ignore
                for nom_obj, obj in xobjects.items():
                    try:
                        obj_resolved = obj.get_object()
                        if obj_resolved.get("/Subtype") == "/Image":
                            # Sauve brut si possible
                            data = obj_resolved.get_data()
                            if data:
                                # Détermine extension via filtre
                                filtre = obj_resolved.get("/Filter")
                                ext = ".png"
                                if filtre == "/DCTDecode":
                                    ext = ".jpg"
                                chemin_img = dossier_temp / f"page{numero_page}_{nom_obj}{ext}"
                                chemin_img.write_bytes(data)
                                images_extraites.append(chemin_img)
                    except Exception:
                        continue
            except Exception:
                pass

    except Exception:
        pass

    return images_extraites


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


def _ocr_page_pdf(
    pdf_path: Path,
    numero_page: int,
    langue: str,
    timeout_s: int,
    tesseract_command: str,
    dpi: int,
    dossier_temp: Path,
) -> dict[str, Any]:
    """OCR d'une page PDF (scannée).

    Stratégie :
    1. Extraire images via pypdf, OCR chaque image, concaténer.
    2. Sinon, rendre la page en PNG via pdftoppm, OCR l'image rendue.
    3. Sinon, tenter tesseract directement sur PDF mono-page (certains builds).
    4. Sinon, échec avec motif lisible.
    """
    version = _tesseract_version(tesseract_command)

    # 1. Images extraites
    images = _extraire_images_pypdf(pdf_path, numero_page, dossier_temp)
    if images:
        textes: list[str] = []
        confs: list[float] = []
        duree_tot = 0.0
        succes_any = False
        motifs: list[str] = []

        for img_path in images:
            res = _ocr_image_via_tesseract(img_path, langue, timeout_s, tesseract_command)
            duree_tot += res["duree_s"]
            if res["succes"]:
                succes_any = True
                if res["texte"].strip():
                    textes.append(res["texte"])
                if res["confiance"] is not None:
                    confs.append(res["confiance"])
            else:
                motifs.append(res["motif"])

        if succes_any:
            return {
                "texte": "\n".join(textes),
                "confiance": sum(confs) / len(confs) if confs else None,
                "duree_s": duree_tot,
                "succes": True,
                "motif": "",
                "moteur": "tesseract",
                "version_moteur": version,
            }
        else:
            # Images trouvées mais OCR a échoué
            return {
                "texte": "",
                "confiance": None,
                "duree_s": duree_tot,
                "succes": False,
                "motif": "; ".join(motifs) or "échec OCR sur images extraites",
                "moteur": "tesseract",
                "version_moteur": version,
            }

    # 2. Rendu via pdftoppm
    rendu = _rendre_page_pdf_via_pdftoppm(pdf_path, numero_page, dossier_temp, dpi)
    if rendu is not None and rendu.exists():
        return _ocr_image_via_tesseract(rendu, langue, timeout_s, tesseract_command)

    # 3. Tesseract direct sur PDF mono-page (fallback)
    try:
        from pypdf import PdfReader, PdfWriter  # type: ignore

        reader = PdfReader(str(pdf_path), strict=False)
        if numero_page < len(reader.pages):
            writer = PdfWriter()
            writer.add_page(reader.pages[numero_page])
            mono_pdf = dossier_temp / f"mono_page_{numero_page}.pdf"
            with open(mono_pdf, "wb") as f:
                writer.write(f)

            # Tente tesseract sur ce PDF mono-page
            # Certaines compilations de tesseract acceptent le PDF via leptonica
            debut = time.perf_counter()
            if not _has_command(tesseract_command):
                return {
                    "texte": "",
                    "confiance": None,
                    "duree_s": time.perf_counter() - debut,
                    "succes": False,
                    "motif": "tesseract absent",
                    "moteur": "tesseract",
                    "version_moteur": version,
                }

            result = subprocess.run(
                [tesseract_command, str(mono_pdf), "stdout", "-l", langue],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            duree = time.perf_counter() - debut
            if result.returncode == 0 and result.stdout.strip():
                return {
                    "texte": result.stdout,
                    "confiance": None,
                    "duree_s": duree,
                    "succes": True,
                    "motif": "",
                    "moteur": "tesseract",
                    "version_moteur": version,
                }
            else:
                # Échec mais on a tenté
                return {
                    "texte": "",
                    "confiance": None,
                    "duree_s": duree,
                    "succes": False,
                    "motif": f"tesseract sur PDF mono-page sans résultat (rc={result.returncode})",
                    "moteur": "tesseract",
                    "version_moteur": version,
                }
    except Exception:
        # Ignore, passe au motif final
        pass

    # 4. Échec final
    return {
        "texte": "",
        "confiance": None,
        "duree_s": 0.0,
        "succes": False,
        "motif": "aucune image extraite et pdftoppm absent ou échec rendu",
        "moteur": "tesseract",
        "version_moteur": version,
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

    # Vérifie existence tesseract pour message lisible
    tesseract_present = _has_command(tesseract_command)

    resultats: list[dict[str, Any]] = []

    if ext in OCR_IMAGE_EXTENSIONS:
        # Image isolée : 1 page, toujours à océriser (M.3)
        # Texte natif = "" donc doit_oceriser_page = True
        with tempfile.TemporaryDirectory(prefix="seamtech-ocr-img-") as tmpdir:
            tmp_path = Path(tmpdir)
            # Pour uniformité, on copie ou utilise directement
            ocr_res = _ocr_image_via_tesseract(
                chemin, langue, timeout_par_page_s, tesseract_command
            )
            texte_natif = ""
            page_ocerisee = ocr_res["succes"]
            motif = "" if page_ocerisee else ocr_res["motif"]

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
                if not tesseract_present:
                    resultats.append(
                        {
                            "page": num_page,
                            "texte_natif": texte_natif,
                            "texte_ocr": "",
                            "texte_final": texte_natif,
                            "confiance": None,
                            "moteur": "tesseract",
                            "version_moteur": None,
                            "duree_s": 0.0,
                            "page_ocerisee": False,
                            "motif": "tesseract absent (OCR désactivé, statut unavailable)",
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
                )

                page_ocerisee = ocr_res["succes"] and bool(ocr_res["texte"].strip())
                motif = "" if page_ocerisee else ocr_res["motif"]

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
