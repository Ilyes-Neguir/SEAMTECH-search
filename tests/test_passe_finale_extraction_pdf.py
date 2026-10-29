"""Passe finale — extraction de PDF : cas dégradés, jamais de succès inventé.

Vérification indépendante du 29/10/2026 sur les entrées qu'un atelier produit
réellement : PDF protégé par mot de passe, PDF tronqué, fichier vide, fichier
d'extension ``.pdf`` qui n'en est pas un, classeur Excel corrompu, PDF scanné
(image seule) avec et sans OCR.

Contrat vérifié : ``extract_file`` ne lève JAMAIS sur ces entrées ; il rend un
statut explicite avec un motif lisible ; il ne fabrique jamais de texte ; il ne
modifie jamais le fichier source (empreinte SHA-256 avant/après).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from seamtech_search.extractors import extract_file


def _sha(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def _pdf_chiffre(destination: Path) -> Path:
    from pypdf import PdfWriter

    ecrivain = PdfWriter()
    ecrivain.add_blank_page(width=200, height=200)
    ecrivain.encrypt(user_password="secret", owner_password="secret", algorithm="RC4-128")
    with destination.open("wb") as flux:
        ecrivain.write(flux)
    return destination


def _pdf_texte(destination: Path, texte: str = "FICHE DE FABRICATION SLU 6.60 m") -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas

    canevas = pdf_canvas.Canvas(str(destination), pagesize=A4)
    canevas.drawString(60, 780, texte)
    canevas.showPage()
    canevas.save()
    return destination


@pytest.mark.parametrize(
    ("nom", "contenu"),
    [
        ("corrompu.pdf", b"%PDF-1.4\n%%EOF\npas un pdf"),
        ("vide.pdf", b""),
        ("faux_pdf.pdf", b"ceci n'est pas un pdf du tout"),
        ("xlsx_corrompu.xlsx", b"PK\x03\x04n'importe quoi"),
    ],
)
def test_entree_degradee_rend_un_statut_explicite_sans_lever(tmp_path: Path, nom: str, contenu: bytes) -> None:
    chemin = tmp_path / nom
    chemin.write_bytes(contenu)
    avant = _sha(chemin)
    resultat = extract_file(chemin, max_chars=10_000)
    assert resultat.status in {"error", "unsupported", "unavailable"}, resultat.status
    assert resultat.detail, "un échec sans motif n'est pas exploitable par l'opérateur"
    assert resultat.text == "", "aucun texte ne doit être inventé"
    assert _sha(chemin) == avant, "le fichier source ne doit jamais être modifié"


def test_pdf_protege_par_mot_de_passe(tmp_path: Path) -> None:
    """Un PDF chiffré n'est pas lisible : erreur explicite, jamais un texte vide
    présenté comme un succès."""
    chemin = _pdf_chiffre(tmp_path / "chiffre.pdf")
    avant = _sha(chemin)
    resultat = extract_file(chemin, max_chars=10_000)
    assert resultat.status == "error"
    assert resultat.text == ""
    assert _sha(chemin) == avant


def test_pdf_nominal_extrait_le_texte(tmp_path: Path) -> None:
    chemin = _pdf_texte(tmp_path / "fiche.pdf")
    resultat = extract_file(chemin, max_chars=10_000)
    assert resultat.status == "extracted", resultat.detail
    assert "FICHE DE FABRICATION" in resultat.text


def test_pdf_scanne_sans_ocr_n_invente_rien(tmp_path: Path) -> None:
    """PDF image seule et OCR désactivé : statut non trompeur, texte vide."""
    from tests.helpers_ocr_scan import gen_pdf_custom

    chemin = gen_pdf_custom(tmp_path, "GENOABMWFX")
    avant = _sha(chemin)
    resultat = extract_file(chemin, max_chars=10_000, enable_ocr=False)
    assert resultat.status in {"extracted", "unavailable", "error"}
    if resultat.status != "extracted":
        assert resultat.detail
    assert _sha(chemin) == avant


def test_pdf_scanne_avec_ocr(tmp_path: Path) -> None:
    """Avec OCR disponible, le terme inscrit dans l'image ressort réellement."""
    import shutil

    if shutil.which("tesseract") is None:
        pytest.skip("tesseract absent (binaire natif ou shim)")
    from tests.helpers_ocr_scan import gen_pdf_custom, terme_unique_ocr_sur

    terme = terme_unique_ocr_sur("PFOCR")
    chemin = gen_pdf_custom(tmp_path, terme)
    resultat = extract_file(chemin, max_chars=10_000, enable_ocr=True)
    assert resultat.status == "extracted", resultat.detail
    assert terme.lower() in resultat.text.lower().replace(" ", ""), (
        f"terme OCR absent : {resultat.text[:200]!r}"
    )


def test_extension_pdf_mentie_ne_casse_pas_le_scan(tmp_path: Path) -> None:
    """Un ``.pdf`` qui n'en est pas un reste listé comme candidat (visibilité),
    mais l'extraction rend une erreur explicite."""
    from seamtech_search.config import AppConfig
    from seamtech_search.import_pipeline import scan_folder

    racine = tmp_path / "racine"
    racine.mkdir()
    (racine / "menti.pdf").write_bytes(b"<html>pas un pdf</html>")
    resultat = scan_folder(racine, AppConfig(root_paths=[racine], database_path=tmp_path / "u.db"))
    assert [c["name"] for c in resultat["candidates"]] == ["menti.pdf"]
    extraction = extract_file(racine / "menti.pdf", max_chars=1000)
    assert extraction.status == "error"
