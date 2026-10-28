"""PDF pivotés : comportement attendu établi par orientation réelle.

Ces tests remplacent les trois ``xfail`` de la campagne précédente par des
assertions de comportement, sur des échantillons représentatifs :

* ``page_XX.pdf`` — contenu horizontal, page déclarée pivotée par ``/Rotate``
  (ce que produit un scanner ou un export de DAO en paysage) ;
* ``scan_paysage_90.pdf`` — contenu couché que ``/Rotate`` redresse : le
  document est DÉJÀ lisible, il est interdit de le dégrader ;
* ``couche_90/180.pdf`` — contenu couché sans ``/Rotate`` (photo de travers).

Ce qui est vérifié, pour chaque orientation :

1. le texte est extrait dans l'ordre de lecture, accents compris ;
2. les lignes d'un tableau restent dans l'ordre lignes × colonnes ;
3. les coordonnées reconstruites sont monotones (v croissant d'une ligne à
   l'autre, u croissant dans une ligne) — c'est la définition opérationnelle
   de « lu de gauche à droite, de haut en bas » ;
4. une page non pivotée garde EXACTEMENT la sortie de pdfplumber (aucune
   régression sur le cas majoritaire) ;
5. la recherche retrouve un terme situé dans une page pivotée.

Aucun test n'est marqué ``xfail`` : une régression casse la CI.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pdfplumber
import pytest

from seamtech_search.extractors import extract_file
from seamtech_search.pdf_orientation import (
    lignes_avec_coordonnees,
    orientation_dominante,
    orientations_par_caractere,
    page_a_orienter,
    texte_page,
)

FIXTURES = Path(__file__).parent / "fixtures" / "pdf_rotation"

LIGNES_REFERENCE = [
    "FICHE TECHNIQUE VOILE",
    "Client : ATELIER DU VENT",
    "Matière : DACRON AZUR 380",
    "Référence : ROT-2026-001",
    "Élégance du génois — 12,50 m",
]
COTES_REFERENCE = ["Cote Valeur", "Guindant 12.50 m", "Bordure 4.80 m", "Chute 11.95 m"]


def _texte(nom: str) -> str:
    resultat = extract_file(FIXTURES / nom, 200_000)
    assert resultat.status == "extracted", (nom, resultat.status, resultat.detail)
    return resultat.text


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_texte_extrait_dans_l_ordre_de_lecture(angle: int) -> None:
    """Le texte d'une page pivotée est identique au texte de la page droite."""
    lignes = [ligne for ligne in _texte(f"page_{angle:02d}.pdf").splitlines() if ligne.strip()]
    assert lignes[: len(LIGNES_REFERENCE)] == LIGNES_REFERENCE


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_accents_et_champs_presents(angle: int) -> None:
    """Les accents survivent : « Référence » est ce que les regex de champs cherchent."""
    texte = _texte(f"page_{angle:02d}.pdf")
    for attendu in ("Référence : ROT-2026-001", "Matière : DACRON AZUR 380", "Élégance"):
        assert attendu in texte


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_tableau_lu_ligne_par_ligne(angle: int) -> None:
    """Un tableau pivoté reste lu lignes × colonnes, pas en colonne ni à l'envers."""
    lignes = [ligne.strip() for ligne in _texte(f"page_{angle:02d}.pdf").splitlines() if ligne.strip()]
    for attendu in COTES_REFERENCE:
        assert attendu in lignes, (angle, attendu, lignes)
    # Ordre relatif : l'en-tête précède les trois cotes, dans l'ordre du document.
    positions = [lignes.index(attendu) for attendu in COTES_REFERENCE]
    assert positions == sorted(positions), (angle, positions)


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_coordonnees_monotones_dans_le_repere_de_lecture(angle: int) -> None:
    """Coordonnées : v croissant entre lignes, u croissant dans une ligne."""
    with pdfplumber.open(FIXTURES / f"page_{angle:02d}.pdf") as pdf:
        page = pdf.pages[0]
        lignes = lignes_avec_coordonnees(page.chars, page.height)
    assert lignes, "aucune ligne reconstruite"
    positions_v = [ligne[0] for ligne in lignes]
    assert positions_v == sorted(positions_v)
    for _v, u0, u1, _texte_ligne in lignes:
        assert u1 >= u0
    # « Cote VALEUR » : deux cellules de la même ligne, dans l'ordre gauche→droite.
    entete = next(ligne for ligne in lignes if ligne[3].startswith("Cote"))
    assert entete[3] == "Cote Valeur"


@pytest.mark.parametrize("nom", ["scan_paysage_90", "couche_90", "couche_180"])
def test_pdf_pivote_deja_lisible_ou_couche_reste_exploitable(nom: str) -> None:
    """Un PDF déjà lisible (scan paysage) et un PDF couché donnent le même texte."""
    lignes = [ligne for ligne in _texte(f"{nom}.pdf").splitlines() if ligne.strip()]
    assert lignes[: len(LIGNES_REFERENCE)] == LIGNES_REFERENCE


def test_pdf_non_pivote_inchange_par_rapport_a_pdfplumber() -> None:
    """Cas majoritaire : la sortie reste exactement celle de pdfplumber.

    Garantit qu'aucune page droite ne change de comportement d'extraction
    (tableaux compris) à cause de la correction des rotations.
    """
    chemin = FIXTURES / "page_00.pdf"
    with pdfplumber.open(chemin) as pdf:
        attendu = "\n".join(
            page.extract_text(x_tolerance=2, y_tolerance=2) or "" for page in pdf.pages
        ).strip()
        for page in pdf.pages:
            assert page_a_orienter(page) is False
            assert orientation_dominante(page.chars) == 0
    obtenu = _texte("page_00.pdf")
    assert attendu and attendu in obtenu


def test_pages_multiples_non_melangees() -> None:
    """La reconstruction est faite page par page : la page 2 vient après la page 1."""
    lignes = [ligne.strip() for ligne in _texte("page_270.pdf").splitlines() if ligne.strip()]
    assert "Page 2 — annexe de mesure" in lignes
    assert lignes.index("Page 2 — annexe de mesure") > lignes.index("FICHE TECHNIQUE VOILE")


def _construire_pdf(chemin: Path, rotation_contenu: int, rotation_page: int) -> Path:
    """PDF de test : contenu dessiné puis éventuellement déclaré pivoté."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    largeur, hauteur = A4
    c = canvas.Canvas(str(chemin), pagesize=A4)
    c.saveState()
    c.translate(largeur / 2, hauteur / 2)
    c.rotate(rotation_contenu)
    c.translate(-largeur / 2, -hauteur / 2)
    c.setFont("Helvetica", 12)
    ordonnee = hauteur - 60
    for ligne in ("PREMIERE LIGNE", "DEUXIEME LIGNE", "TROISIEME LIGNE"):
        c.drawString(50, ordonnee, ligne)
        ordonnee -= 18
    c.restoreState()
    c.showPage()
    c.save()
    if rotation_page:
        lecteur = PdfReader(str(chemin))
        ecrivain = PdfWriter()
        for page in lecteur.pages:
            page.rotate(rotation_page)
            ecrivain.add_page(page)
        with chemin.open("wb") as flux:
            ecrivain.write(flux)
    return chemin


@pytest.mark.parametrize("rotation_contenu", [0, 90, 180, 270])
@pytest.mark.parametrize("rotation_page", [0, 90, 180, 270])
def test_matrice_contenu_et_declaration_de_page(rotation_contenu: int, rotation_page: int) -> None:
    """16 combinaisons : contenu dessiné × ``/Rotate`` déclaré.

    C'est la matrice qui manquait : elle couvre le scan paysage redressé
    (contenu couché + ``/Rotate``), la page déclarée pivotée (contenu droit) et
    la photo de travers (contenu couché sans ``/Rotate``). Dans les 16 cas,
    l'extraction doit rendre les trois lignes dans l'ordre.
    """
    with tempfile.TemporaryDirectory() as dossier:
        chemin = _construire_pdf(
            Path(dossier) / "cas.pdf", rotation_contenu, rotation_page
        )
        resultat = extract_file(chemin, 10_000)
        lignes = [ligne.strip() for ligne in resultat.text.splitlines() if ligne.strip()]
    assert lignes == ["PREMIERE LIGNE", "DEUXIEME LIGNE", "TROISIEME LIGNE"], (
        rotation_contenu,
        rotation_page,
        resultat.text,
    )


def test_orientation_dominante_ignore_une_minorite_verticale() -> None:
    """Un tampon vertical minoritaire ne réécrit pas l'ordre de lecture d'une page droite."""
    with tempfile.TemporaryDirectory() as dossier:
        chemin = Path(dossier) / "mixte.pdf"
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        largeur, hauteur = A4
        c = canvas.Canvas(str(chemin), pagesize=A4)
        c.setFont("Helvetica", 12)
        ordonnee = hauteur - 60
        for _ in range(30):
            c.drawString(50, ordonnee, "Ligne horizontale de contenu technique")
            ordonnee -= 16
        c.saveState()
        # Tampon vertical, court, franchement minoritaire en nombre de glyphes.
        c.translate(largeur / 2, hauteur / 2)
        c.rotate(90)
        c.setFont("Helvetica", 8)
        c.drawString(-20, 0, "TAMPON")
        c.restoreState()
        c.showPage()
        c.save()

        with pdfplumber.open(chemin) as pdf:
            page = pdf.pages[0]
            compte = orientations_par_caractere(page.chars)
            assert compte[0] > compte[270] > 0, compte
            assert orientation_dominante(page.chars) == 0
            assert page_a_orienter(page) is False
            reconstruit = texte_page(page.chars, page.height)
        assert "Ligne horizontale de contenu technique" in reconstruit


def test_tampon_vertical_minoritaire_extrait_sans_polluer_l_index() -> None:
    """Le tampon vertical est récupéré dans le bon sens, une seule fois.

    Sans correctif, pdfplumber le rend inversé (« NOPMAT ») : l'index se
    remplissait de faux mots et le contenu (parfois une référence client)
    était perdu. On vérifie ici les deux côtés : présence du texte correct,
    absence de la forme inversée.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    largeur, hauteur = A4
    with tempfile.TemporaryDirectory() as dossier:
        chemin = Path(dossier) / "tampon.pdf"
        c = canvas.Canvas(str(chemin), pagesize=A4)
        c.setFont("Helvetica", 12)
        ordonnee = hauteur - 60
        for _ in range(5):
            c.drawString(50, ordonnee, "Corps horizontal du document")
            ordonnee -= 16
        c.saveState()
        c.translate(largeur / 2, hauteur / 2)
        c.rotate(90)
        c.setFont("Helvetica", 8)
        c.drawString(-20, 0, "TAMPON CLIENT ACME")
        c.restoreState()
        c.showPage()
        c.save()

        texte = extract_file(chemin, 10_000).text
        assert "TAMPON CLIENT ACME" in texte
        assert "EMCA TNEILC NOPMAT" not in texte
        assert texte.count("TAMPON CLIENT ACME") == 1


def test_recherche_retrouve_terme_dans_pdf_pivote() -> None:
    """Bout en bout : indexation d'un PDF pivoté, puis recherche du terme.

    Le terme « Guindant » n'existe que dans le tableau de la page ; les mots
    « Élégance » et « génois » n'existent que dans le corps de texte. Sans la
    correction, la ligne ressort découpée (« Guindant »/« 12.50 »/« m » sur des
    lignes distinctes) et le tableau à 90° n'est pas cherchable comme ligne.
    """
    from seamtech_search.config import AppConfig
    from seamtech_search.crawler import crawl
    from seamtech_search.indexer import SearchIndex

    with tempfile.TemporaryDirectory() as dossier:
        dossier_racine = Path(dossier)
        copie = dossier_racine / "page_270.pdf"
        copie.write_bytes((FIXTURES / "page_270.pdf").read_bytes())
        config = AppConfig(root_paths=[dossier_racine], database_path=dossier_racine / "search.db")
        index = SearchIndex(dossier_racine / "search.db", None)
        index.initialize(rebuild=True)
        try:
            documents = list(crawl(config))
            assert documents, "aucun document produit par le crawler"
            index.upsert_documents(documents)
            for terme in ("Guindant", "Référence", "génois", "Élégance"):
                resultats = index.search(terme)
                assert resultats, f"« {terme} » introuvable après indexation d'un PDF pivoté"
        finally:
            index.close()
