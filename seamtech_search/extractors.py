from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile

logger = logging.getLogger("seamtech_search.extractors")


# Bump this whenever extraction logic changes for any format. The indexer
# uses it (alongside size/modified_at) to decide whether a previously
# indexed file needs to be re-extracted even though it hasn't changed on
# disk, so old content isn't silently kept forever after a parser fix.
#
# v4: PDF extraction moved to pdfplumber (layout-aware text + tables) with
# pypdf kept as a fallback, so every PDF is re-parsed on the next scan.
# v5: scanned PDFs with enable_ocr=true fall back to the tesseract page
# pipeline when ocrmypdf is absent — previously they stayed « unavailable »
# even with tesseract installed.
# v6: OCR tesseract fra+eng when both language packs are present.
# v7: normalize verified, uniformly rotated PDF text for indexing.
# v8: inspect mixed native/image PDFs and reject unsafe or incomplete OCR.
# v9: preserve partial native text and extraction status across worker IPC.
# v10: read verified orthogonal mixed orientations separately for indexing.
# v11: retain partial text without certifying unsupported PDF orientations.
# v12: do not publish uninspected fallback text; clip to the actual page view.
# v13: read minority-orientation glyphs (vertical stamps, margin notes) apart
# from the page body instead of letting pdfplumber reverse them ("NOPMAT").
CURRENT_EXTRACTOR_VERSION = 13

EXTRACTION_STATUSES = {"extracted", "unavailable", "skipped", "error", "timeout", "not_applicable"}


@dataclass(frozen=True, eq=False)
class ExtractionResult:
    text: str
    status: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in EXTRACTION_STATUSES:
            raise ValueError(f"Unsupported extraction status: {self.status}")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ExtractionResult):
            return (self.text, self.status, self.detail) == (other.text, other.status, other.detail)
        if isinstance(other, str):
            return self.legacy_text == other
        return NotImplemented

    @property
    def legacy_text(self) -> str:
        if self.status == "extracted":
            return self.text
        prefix = {
            "unavailable": "extraction unavailable",
            "skipped": "extraction skipped",
            "error": "extraction error",
            "timeout": "extraction timed out",
        }.get(self.status, "extraction unavailable")
        return f"[{prefix}: {self.detail}]" if self.detail else f"[{prefix}]"


def _resultat_ocr_borne(texte: str, max_chars: int) -> ExtractionResult:
    """Conserve un éventuel préfixe OCR sans qualifier de complet un texte tronqué."""
    if len(texte) > max_chars:
        return ExtractionResult(texte[:max_chars], "unavailable", "OCR tronqué : limite de texte atteinte")
    if not texte.strip():
        return ExtractionResult("", "unavailable", "OCR n'a produit aucun texte")
    return ExtractionResult(texte, "extracted")


TEXT_EXTENSIONS = {
    ".txt",
    ".csv",
    ".tsv",
    ".md",
    ".log",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".conf",
    ".sql",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".css",
    ".scss",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".go",
    ".rs",
    ".php",
    ".sh",
    ".ps1",
    ".bat",
    ".properties",
}
OFFICE_XML_EXTENSIONS = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}
ARCHIVE_EXTENSIONS = {".zip"}
LEGACY_OFFICE_EXTENSIONS = {".doc", ".xls", ".ppt"}
OCR_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def extract_file(
    path: Path,
    max_chars: int = 200_000,
    max_file_size_bytes: int = 512 * 1024 * 1024,
    enable_legacy_office: bool = False,
    libreoffice_command: str = "soffice",
    enable_ocr: bool = False,
    tesseract_command: str = "tesseract",
    ocrmypdf_command: str = "ocrmypdf",
    external_extraction_timeout_seconds: int = 120,
    external_extractors: dict[str, list[str]] | None = None,
) -> ExtractionResult:
    try:
        size = path.stat().st_size
        if size > max_file_size_bytes:
            return ExtractionResult("", "skipped", f"file exceeds {max_file_size_bytes} bytes")
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            resultat_pdf = _extract_pdf(path, max_chars)
            if isinstance(resultat_pdf, ExtractionResult):
                # Un texte natif partiel ne doit pas être remplacé par un OCR
                # qui pourrait paraître complet sans vérifier l'orientation.
                return resultat_pdf
            result = _from_legacy(resultat_pdf)
            if not enable_ocr:
                return result
            if result.status != "unavailable":
                return _completer_pdf_natif_images(
                    path, result, max_chars, tesseract_command, external_extraction_timeout_seconds
                )
            if _has_command(ocrmypdf_command):
                try:
                    texte_ocr = _extract_with_ocrmypdf(
                        path, max_chars, ocrmypdf_command, external_extraction_timeout_seconds
                    )
                    return _resultat_ocr_borne(texte_ocr, max_chars)
                except Exception as exc:
                    logger.warning("ocrmypdf a échoué (%s) — repli tesseract si présent", type(exc).__name__)
            if _moteur_ocr_disponible(tesseract_command):
                texte, motif = _extract_pdf_via_pipeline_ocr(
                    path, max_chars, tesseract_command, external_extraction_timeout_seconds
                )
                if motif:
                    return ExtractionResult(texte, "unavailable", motif)
                return _resultat_ocr_borne(texte, max_chars)
            return ExtractionResult("", "unavailable", "OCR activé mais ocrmypdf et aucun moteur OCR disponibles")
        if suffix == ".docx":
            return _from_legacy(_extract_docx(path, max_chars))
        if suffix in OFFICE_XML_EXTENSIONS:
            return _from_legacy(_extract_zip_xml(path, max_chars))
        if suffix in ARCHIVE_EXTENSIONS:
            return _from_legacy(_extract_archive_manifest(path, max_chars))
        if suffix in LEGACY_OFFICE_EXTENSIONS:
            if not enable_legacy_office:
                return ExtractionResult("", "unavailable", "legacy Office extraction is disabled")
            if not _has_command(libreoffice_command):
                return ExtractionResult("", "unavailable", "LibreOffice is not installed")
            return ExtractionResult(
                _extract_with_libreoffice(path, max_chars, libreoffice_command, external_extraction_timeout_seconds),
                "extracted",
            )
        if suffix in OCR_IMAGE_EXTENSIONS:
            if not enable_ocr:
                return ExtractionResult("", "unavailable", "OCR is disabled")
            if not _moteur_ocr_disponible(tesseract_command):
                return ExtractionResult("", "unavailable", "OCR engine is not installed")
            texte_ocr = _extract_with_tesseract(path, max_chars, tesseract_command, external_extraction_timeout_seconds)
            return _resultat_ocr_borne(texte_ocr, max_chars)
        if external_extractors and suffix in external_extractors:
            command = external_extractors[suffix]
            if not command or not _has_command(command[0]):
                return ExtractionResult("", "unavailable", f"parser is not installed for {suffix}")
            return ExtractionResult(
                _extract_with_plugin(path, max_chars, command, external_extraction_timeout_seconds),
                "extracted",
            )
        if suffix in TEXT_EXTENSIONS:
            return ExtractionResult(_extract_plain_text(path, max_chars), "extracted")
    except subprocess.TimeoutExpired:
        return ExtractionResult("", "timeout", "OCR ou extracteur externe a dépassé le délai")
    except (OSError, BadZipFile, ValueError) as exc:
        return ExtractionResult("", "error", type(exc).__name__)
    except Exception as exc:
        return ExtractionResult("", "error", type(exc).__name__)
    return ExtractionResult("", "unavailable", "unsupported file type")


def extract_text(path: Path, max_chars: int, max_file_size_bytes: int = 512 * 1024 * 1024, **options: object) -> str:
    """Compatibility wrapper returning the historical marker format."""
    return extract_file(path, max_chars, max_file_size_bytes, **options).legacy_text


def _from_legacy(text: str) -> ExtractionResult:
    if text.startswith("[extraction unavailable:"):
        return ExtractionResult("", "unavailable", text[len("[extraction unavailable:") : -1].strip())
    return ExtractionResult(text, "extracted")


def _has_command(command: str) -> bool:
    return bool(shutil.which(command) or Path(command).is_file())


def _extract_with_plugin(path: Path, max_chars: int, command: list[str], timeout: int) -> str:
    completed = subprocess.run(
        [*command, str(path)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("configured parser returned an error")
    return completed.stdout[:max_chars]


def _extract_with_libreoffice(path: Path, max_chars: int, command: str, timeout: int) -> str:
    with tempfile.TemporaryDirectory(prefix="seamtech-office-") as output_dir:
        completed = subprocess.run(
            [command, "--headless", "--convert-to", "txt:Text", "--outdir", output_dir, str(path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output_path = Path(output_dir) / f"{path.stem}.txt"
        if completed.returncode != 0 or not output_path.exists():
            raise RuntimeError("LibreOffice could not convert the document")
        return output_path.read_text(encoding="utf-8", errors="ignore")[:max_chars]


def _extract_with_tesseract(path: Path, max_chars: int, command: str, timeout: int) -> str:
    completed = subprocess.run(
        [command, str(path), "stdout"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Tesseract could not read the image")
    # Garder un caractère supplémentaire pour détecter la troncature.
    return completed.stdout[:max_chars + 1]


def _extract_with_ocrmypdf(path: Path, max_chars: int, command: str, timeout: int) -> str:
    with tempfile.TemporaryDirectory(prefix="seamtech-ocr-") as output_dir:
        output_path = Path(output_dir) / "extracted.txt"
        completed = subprocess.run(
            [
                command,
                "--sidecar",
                str(output_path),
                "--output-type",
                "pdf",
                str(path),
                str(Path(output_dir) / "output.pdf"),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0 or not output_path.exists():
            raise RuntimeError("OCRmyPDF could not extract text from the PDF")
        with output_path.open(encoding="utf-8", errors="ignore") as sidecar:
            return sidecar.read(max_chars + 1)


def _moteur_ocr_disponible(tesseract_command: str) -> bool:
    """Un moteur OCR local est-il exploitable ? (RapidOCR ou Tesseract)."""
    from seamtech_search.ocr.moteur import moteur_disponible

    return bool(moteur_disponible(tesseract_command, sonde=_has_command))


def _langue_ocr_disponible(tesseract_command: str) -> str:
    """fra+eng si les deux paquets sont installés, sinon fra, sinon eng.

    RapidOCR (PP-OCRv6) reconnaît le français et l'anglais avec un seul jeu de
    modèles multilingues : la question des paquets de langue ne se pose pas, la
    langue déclarée reste ``fra`` par cohérence avec le reste du produit.
    """
    from seamtech_search.ocr.moteur import MOTEUR_TESSERACT, choisir_moteur

    moteur = choisir_moteur(tesseract_command)
    if moteur is not None and moteur.nom != MOTEUR_TESSERACT:
        return "fra"
    try:
        completed = subprocess.run(
            [tesseract_command, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        disponibles = {ligne.strip() for ligne in (completed.stdout or "").splitlines()}
    except Exception:
        disponibles = set()
    choisis = [code for code in ("fra", "eng") if code in disponibles]
    return "+".join(choisis) if choisis else "fra"


def _extract_pdf_via_pipeline_ocr(
    path: Path, max_chars: int, tesseract_command: str, timeout: int
) -> tuple[str, str]:
    """Repli PDF scanné : pipeline OCR par page déjà implémenté (tesseract).

    Ne mocke rien. Si tesseract est absent ou échoue, le texte est vide et le
    motif est renvoyé — jamais un succès fictif. L'original n'est pas modifié.
    """
    from seamtech_search.ocr.pipeline import ocriser_fichier

    timeout_page = max(5, min(int(timeout), 60))
    try:
        rapport = ocriser_fichier(
            path,
            tesseract_command=tesseract_command,
            timeout_par_page_s=timeout_page,
            langue=_langue_ocr_disponible(tesseract_command),
        )
    except subprocess.TimeoutExpired:
        return "", "OCR a dépassé le délai"
    except Exception as exc:
        return "", f"échec OCR : {type(exc).__name__}"
    pages = rapport.get("pages") or []
    if rapport.get("nb_echecs", 0):
        return "", "OCR incomplet : une ou plusieurs pages n'ont pas pu être traitées"
    textes = [str(p.get("texte_final") or "") for p in pages]
    texte = "\n".join(morceau for morceau in textes if morceau.strip()).strip()
    if texte:
        if len(texte) > max_chars:
            return texte[:max_chars], "OCR tronqué : limite de texte atteinte"
        return texte, ""
    motifs = [str(p.get("motif") or "") for p in pages if p.get("motif")]
    return "", "; ".join(m for m in motifs if m) or "OCR n'a produit aucun texte"


#: Longueur d'en-tête inspectée : la spécification tolère un préfixe quelconque
#: avant la signature ``%PDF-``.
_SIGNATURE_PDF = b"%PDF-"
_TAILLE_ENTETE_PDF = 1024


def _verdict_pdf_illisible(path: Path) -> ExtractionResult | None:
    """Classe un PDF inexploitable, ou ``None`` s'il faut laisser tenter pypdf.

    MESURÉ (passe finale du 29/10/2026) : sans ce tri, un PDF chiffré et un
    fichier ``.pdf`` qui n'en est pas un rendent ``unavailable`` — exactement le
    statut d'une page image à relire. L'opérateur ne peut alors pas distinguer
    un fichier à réparer d'un PDF simplement non exploitable.

    Seuls deux cas non ambigus sont classés ``error`` : signature PDF absente,
    et chiffrement non ouvrable sans mot de passe. Un PDF que pypdf n'arrive pas
    à analyser reste confié au statut « relecture » : des PDF imparfaits (xref
    abîmée, page tronquée) restent parfois lisibles et ne doivent pas être
    déclarés en erreur sur le seul avis de pypdf.
    """
    try:
        with path.open("rb") as flux:
            entete = flux.read(_TAILLE_ENTETE_PDF)
    except OSError:
        return None
    if _SIGNATURE_PDF not in entete:
        return ExtractionResult("", "error", "Signature %PDF- absente : ce fichier n'est pas un PDF")
    try:
        from pypdf import PdfReader

        lecteur = PdfReader(str(path))
        if not lecteur.is_encrypted:
            return None
        try:
            ouvert = lecteur.decrypt("")
        except Exception:
            ouvert = 0
    except Exception:
        # pypdf n'analyse pas le fichier : pdfplumber reste juge.
        return None
    if not ouvert:
        return ExtractionResult("", "error", "PDF chiffré : mot de passe requis")
    return None


def _extract_pdf(path: Path, max_chars: int) -> str | ExtractionResult:
    """Extract PDF text and tables only when the page inspection is available.

    A text-only fallback cannot check clipping or orientation. Publishing it
    would index content outside the displayed page even with ``unavailable``
    status, so fail closed and leave the source PDF untouched instead.
    """
    plumber_text = _extract_pdf_with_plumber(path, max_chars)
    if plumber_text is not None:
        return plumber_text
    # L'inspection a échoué : dire si le fichier est inexploitable (erreur
    # explicite) ou si son texte n'a pas pu être lu (relecture nécessaire).
    return _verdict_pdf_illisible(path) or ExtractionResult(
        "", "unavailable", "Inspection PDF indisponible : relecture nécessaire"
    )


def _table_row_already_in_text(cells: list[str], normalized_text: str) -> bool:
    """True when every non-empty cell of a table row already appears in the
    page text (whitespace-insensitive).

    pdfplumber's ``extract_text`` emits the words inside table cells as part of
    the page text, so re-appending the same row via ``extract_tables`` would
    store the document's content twice. Comparison is done on the normalized
    (whitespace-collapsed) page text; a row whose cells are all present adds
    nothing, while a row that introduces new content (e.g. a table the
    plain-text layer mangled) is kept.
    """
    cells = [cell.strip() for cell in cells if cell and cell.strip()]
    if not cells:
        return True
    return all(" ".join(cell.split()) in normalized_text for cell in cells)


def _texte_page_plumber(page: object, *, filtrer_orientations_minoritaires: bool = True) -> str:
    """Même lecture native et tableaux pour l'extraction classique et mixte.

    Sur une page non pivotée, les caractères d'orientation minoritaire (tampon
    vertical, mention en marge) sont retirés du corps avant ``extract_text`` :
    pdfplumber les regroupe par ordonnée d'affichage et les rend donc INVERSÉS
    (« NOPMAT » pour « TAMPON »), ce qui remplit l'index de faux mots. Ils sont
    relus à part, dans leur ordre réel, et ajoutés une seule fois à la suite.

    Sur une vue déjà pivotée (``filtrer_orientations_minoritaires=False``), ce
    tri ne s'applique PAS : les caractères y sont projetés dans le repère
    d'affichage alors que leur matrice reste celle du flux de contenu, tous
    seraient donc vus comme minoritaires. Les groupes d'orientation y sont déjà
    séparés par ``lectures_mixtes_verifiees``.
    """
    page_travail = page
    try:
        if filtrer_orientations_minoritaires:
            from seamtech_search.pdf_orientation import caracteres_minoritaires

            ids_minoritaires = {id(c) for c in caracteres_minoritaires(page.chars)}
            if ids_minoritaires:
                page_travail = page.filter(lambda objet: id(objet) not in ids_minoritaires)
    except Exception as exc:
        logger.debug("Tri des orientations minoritaires indisponible (%s)", type(exc).__name__)
    text = page_travail.extract_text(x_tolerance=2, y_tolerance=2) or ""
    try:
        normalized = " ".join(text.split())
        for table in page_travail.extract_tables() or []:
            fresh_rows = [
                " | ".join(cell.strip() if cell else "" for cell in cells)
                for cells in table
                if not _table_row_already_in_text(cells, normalized)
            ]
            fresh_rows = [row for row in fresh_rows if row.strip(" |")]
            if fresh_rows:
                text += "\n" + "\n".join(fresh_rows)
    except Exception as exc:
        logger.debug("Table extraction failed (%s)", type(exc).__name__)
    if filtrer_orientations_minoritaires:
        # Le texte minoritaire est ajouté UNE fois, jamais dupliqué : sans lui
        # un tampon vertical disparaîtrait purement et simplement de l'index.
        try:
            from seamtech_search.pdf_orientation import lignes_minoritaires

            corps = " ".join(text.split())
            marginales = [
                ligne
                for ligne in lignes_minoritaires(page.chars, float(page.height))
                if " ".join(ligne.split()) not in corps
            ]
            if marginales:
                text += "\n" + "\n".join(marginales)
        except Exception as exc:
            logger.debug("Texte d'orientation minoritaire non extrait (%s)", type(exc).__name__)
    return text


def _texte_glyphes(caracteres: list, hauteur: float) -> str:
    """Lignes reconstruites le long de la direction réelle des glyphes.

    Les orientations minoritaires (tampon vertical) sont ajoutées à la suite :
    ``texte_page`` ne couvre que l'orientation dominante.
    """
    from seamtech_search.pdf_orientation import lignes_minoritaires, texte_page

    texte = texte_page(caracteres, hauteur) or ""
    corps = " ".join(texte.split())
    try:
        marginales = [
            ligne
            for ligne in lignes_minoritaires(caracteres, hauteur)
            if " ".join(ligne.split()) not in corps
        ]
    except Exception:
        marginales = []
    if marginales:
        texte += "\n" + "\n".join(marginales)
    return texte


def _texte_page_orientee(originale: object, *, inspecter_images: bool = False) -> tuple[str, bool, bool]:
    """Lit une page et signale une éventuelle lecture incertaine.

    Deux mécanismes complémentaires, selon ce que porte réellement la page :

    * les GLYPHES sont tournés dans le repère d'affichage (contenu couché,
      ``/Rotate`` absent ou qui ne compense pas) : les lignes sont reconstruites
      le long de la direction réelle des caractères. Les regrouper par ordonnée
      d'affichage — ce que fait pdfplumber — rendrait un mot par ligne (90°/270°)
      ou le texte à l'envers (180°) ;
    * sinon, la lecture se fait dans le repère du lecteur : groupes d'orientation
      vérifiés lus séparément (``lectures_mixtes_verifiees``), caractères
      d'orientation minoritaire extraits à part, tableaux compris.

    Cas mesuré (matrice contenu × ``/Rotate``) : une page déclarée pivotée de 90°
    ou 270° voit ses axes échangés ; la vue normalisée peut alors ne plus porter
    les glyphes. On ne publie jamais une page vide pour autant : si la lecture
    dans le repère du lecteur ne rend rien alors que la page porte des caractères
    démontrés, la reconstruction par direction des glyphes est utilisée.

    La garde de vue s'applique aux deux mécanismes : un glyphe masqué par un clip
    n'est jamais publié. Une transformation non orthogonale (oblique, miroir,
    cisaillement) ne se rattache à aucune orientation : elle reste signalée
    « incertaine » et le texte disponible est conservé sans être certifié.
    """
    from seamtech_search.pdf_orientation import (
        caracteres_demontres,
        fermer_page_lecture,
        lectures_mixtes_verifiees,
        orientation_dominante,
        orientation_non_verifiee,
        orientations_visibles,
        page_de_lecture,
    )

    mixte, compatible, _ = orientations_visibles(originale)
    page, rotation = page_de_lecture(originale)
    try:
        with lectures_mixtes_verifiees(originale) if mixte or not compatible else nullcontext(None) as lectures:
            incertaine = orientation_non_verifiee(originale, rotation, lectures)
            if orientation_dominante(originale.chars) != 0:
                gardes, hauteur = caracteres_demontres(originale)
                texte = _texte_glyphes(gardes, hauteur) if gardes else ""
            else:
                if lectures is None:
                    texte = _texte_page_plumber(page)
                else:
                    # Vues déjà pivotées : pas de tri minoritaire (leurs
                    # caractères sont projetés mais leur matrice reste celle du
                    # flux de contenu).
                    texte = "\n".join(
                        _texte_page_plumber(vue, filtrer_orientations_minoritaires=False) for vue, _ in lectures
                    ).strip()
                if not texte.strip():
                    gardes, hauteur = caracteres_demontres(originale)
                    if gardes:
                        texte = _texte_glyphes(gardes, hauteur)
        return texte, bool(page.images) if inspecter_images else False, incertaine
    finally:
        fermer_page_lecture(originale, page)


def _completer_pdf_natif_images(
    path: Path, native: ExtractionResult, max_chars: int, command: str, timeout: int
) -> ExtractionResult:
    """Complète les pages image sans modifier les pages à texte natif.

    Refuse le succès si une des pages image identifiées ne peut être traitée.
    Les PDF purement natifs conservent strictement l'extraction antérieure.
    """
    try:
        import pdfplumber

        textes: list[str] = []
        pages_images: list[int] = []
        with pdfplumber.open(str(path)) as pdf:
            for numero, originale in enumerate(pdf.pages):
                texte, a_images, incertaine = _texte_page_orientee(originale, inspecter_images=True)
                if incertaine:
                    return ExtractionResult(native.text, "unavailable", "Orientation PDF non vérifiée : relecture nécessaire")
                # Une page avec texte natif n'est jamais océrisée : son logo
                # ou ses figures ne justifient pas de remplacer ce texte.
                if not texte.strip() and a_images:
                    pages_images.append(numero)
                textes.append(texte)
    except Exception:
        # Ne pas déclarer complète une extraction quand les pages image
        # n'ont pas pu être inspectées ; conserver le texte natif disponible.
        return ExtractionResult(native.text, "unavailable", "Inspection des pages image indisponible")

    if not pages_images:
        return native
    if not _has_command(command):
        return ExtractionResult(native.text, "unavailable", "OCR incomplet : Tesseract indisponible")
    try:
        from seamtech_search.ocr.pipeline import ocriser_fichier

        rapport = ocriser_fichier(
            path, pages=pages_images, tesseract_command=command,
            timeout_par_page_s=max(5, min(int(timeout), 60)),
            langue=_langue_ocr_disponible(command),
        )
        par_page = {page["page"]: page for page in rapport.get("pages", [])}
        for numero in pages_images:
            page = par_page.get(numero)
            if not page or not page.get("page_ocerisee") or not str(page.get("texte_ocr") or "").strip():
                return ExtractionResult(native.text, "unavailable", "OCR incomplet : page image non reconnue")
            textes[numero] = str(page["texte_ocr"])
        return _resultat_ocr_borne("\n".join(t for t in textes if t.strip()).strip(), max_chars)
    except Exception as exc:
        return ExtractionResult(native.text, "unavailable", f"OCR incomplet : {type(exc).__name__}")


def _extract_pdf_with_plumber(path: Path, max_chars: int) -> str | ExtractionResult | None:
    """Return text, an explicitly partial result, or None for the pypdf fallback."""
    try:
        import pdfplumber
    except ImportError:
        logger.warning("Inspection PDF indisponible : pdfplumber absent")
        return None
    try:
        chunks: list[str] = []
        total = 0
        incertaine = False
        tronquee = False
        with pdfplumber.open(str(path)) as pdf:
            for numero, originale in enumerate(pdf.pages):
                text, _a_images, page_incertaine = _texte_page_orientee(originale)
                incertaine |= page_incertaine
                if text.strip():
                    total += len(text) + (1 if chunks else 0)
                    chunks.append(text)
                if total >= max_chars:
                    tronquee = total > max_chars or numero + 1 < len(pdf.pages)
                    break
    except Exception as erreur:
        logger.warning("Inspection PDF indisponible (%s) : aucun texte non vérifié publié", type(erreur).__name__)
        return None
    text = "\n".join(chunks)[:max_chars].strip()
    if incertaine or tronquee:
        motifs = []
        if incertaine:
            motifs.append("Orientation PDF non vérifiée")
        if tronquee:
            motifs.append("Limite de texte atteinte : extraction tronquée ou pages non inspectées")
        return ExtractionResult(text, "unavailable", "; ".join(motifs) + " : relecture nécessaire")
    return text or "[extraction unavailable: PDF contains no embedded text]"


def _extract_docx(path: Path, max_chars: int) -> str:
    from docx import Document as DocxDocument

    doc = DocxDocument(str(path))
    chunks = [paragraph.text for paragraph in doc.paragraphs]
    for table in doc.tables:
        chunks.extend(cell.text for row in table.rows for cell in row.cells)
    text = "\n".join(chunks).strip()
    return text[:max_chars] or "[extraction unavailable: document contains no text]"


def _extract_zip_xml(path: Path, max_chars: int) -> str:
    chunks: list[str] = []
    with ZipFile(path) as archive:
        members = [info for info in archive.infolist() if not info.is_dir() and info.file_size <= 10 * 1024 * 1024]
        for info in members[:500]:
            if not info.filename.lower().endswith((".xml", ".rels")):
                continue
            raw = archive.read(info)
            text = re.sub(r"<[^>]+>", " ", raw.decode("utf-8", errors="ignore"))
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                chunks.append(text)
            if sum(map(len, chunks)) >= max_chars:
                break
    return "\n".join(chunks)[:max_chars] or "[extraction unavailable: archive contains no readable text]"


def _extract_archive_manifest(path: Path, max_chars: int) -> str:
    with ZipFile(path) as archive:
        names = [info.filename for info in archive.infolist()[:10_000]]
    return "[archive members]\n" + "\n".join(names)[:max_chars]


def _extract_plain_text(path: Path, max_chars: int) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
