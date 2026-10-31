"""Variantes structurelles de PDF : rotations, chiffrement, corruption.

Les archives réelles contiennent des scans pivotés, des PDF protégés par
mot de passe et des fichiers abîmés. Le contrat de l'analyseur est binaire :
soit le texte est extrait proprement, soit ``ExtractionImpossible`` est
levée avec un message exploitable — jamais un crash d'une autre nature,
jamais un texte silencieusement faux.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from seamtech_search.fiches.extraction import ExtractionImpossible, analyser_pdf

LARGEUR, HAUTEUR = 595.0, 842.0


def _pdf_texte(chemin: Path, *, pages_vides_avant: int = 0, nb_pages_texte: int = 1) -> Path:
    pdf = canvas.Canvas(str(chemin), pagesize=(LARGEUR, HAUTEUR))
    for _ in range(pages_vides_avant):
        pdf.showPage()
    for numero in range(nb_pages_texte):
        pdf.drawString(72, HAUTEUR - 100, "FICHE DE FABRICATION")
        pdf.drawString(72, HAUTEUR - 130, f"REFERENCE VARIANTE-{numero:03d}")
        pdf.drawString(72, HAUTEUR - 160, "GUINDANT 10,50 CHUTE 9,40 BORDURE 5,20")
        pdf.showPage()
    pdf.save()
    return chemin


def _texte_complet(chemin: Path) -> str:
    pages = analyser_pdf(chemin)
    return "\n".join(
        " ".join(mot.texte for mot in ligne.mots) for page in pages for ligne in page.lignes
    )


def _transformer(source: Path, destination: Path, operation) -> Path:  # noqa: ANN001, ANN202
    lecteur = PdfReader(str(source))
    redacteur = PdfWriter()
    for page in lecteur.pages:
        redacteur.add_page(page)
    operation(redacteur)
    with destination.open("wb") as sortie:
        redacteur.write(sortie)
    return destination


class TestRotations:
    @pytest.mark.parametrize("angle", [90, 180, 270])
    def test_page_pivotee_reste_lisible(self, tmp_path: Path, angle: int) -> None:
        source = _pdf_texte(tmp_path / "base.pdf")
        pivote = _transformer(
            source,
            tmp_path / f"pivote-{angle}.pdf",
            lambda redacteur: [page.rotate(angle) for page in redacteur.pages],
        )
        texte = _texte_complet(pivote)
        assert "FICHE DE FABRICATION" in texte
        assert "VARIANTE-000" in texte
        # L'index de recherche plein texte doit recevoir le même texte lu
        # dans le bon sens, sinon la fiche pivotée devient introuvable.
        from seamtech_search.extractors import extract_text

        assert "FICHE DE FABRICATION" in extract_text(pivote, 100_000)


class TestChiffrement:
    def test_mot_de_passe_utilisateur_refus_propre(self, tmp_path: Path) -> None:
        source = _pdf_texte(tmp_path / "base.pdf")
        protege = _transformer(
            source,
            tmp_path / "protege.pdf",
            lambda redacteur: redacteur.encrypt(user_password="secret-variante", owner_password="proprio"),
        )
        with pytest.raises(ExtractionImpossible):
            analyser_pdf(protege)

    def test_mot_de_passe_proprietaire_seul_lisible(self, tmp_path: Path) -> None:
        """Protection « propriétaire » sans mot de passe d'ouverture : le
        lecteur ouvre avec le mot de passe vide, comme les visionneuses."""
        source = _pdf_texte(tmp_path / "base.pdf")
        protege = _transformer(
            source,
            tmp_path / "proprio.pdf",
            lambda redacteur: redacteur.encrypt(user_password="", owner_password="proprio-seul"),
        )
        assert "VARIANTE-000" in _texte_complet(protege)


class TestFichiersAbimes:
    def test_pdf_tronque_refus_propre(self, tmp_path: Path) -> None:
        source = _pdf_texte(tmp_path / "base.pdf").read_bytes()
        tronque = tmp_path / "tronque.pdf"
        tronque.write_bytes(source[: len(source) // 3])
        with pytest.raises(ExtractionImpossible):
            analyser_pdf(tronque)

    def test_octets_corrompus_jamais_dautre_crash(self, tmp_path: Path) -> None:
        source = bytearray(_pdf_texte(tmp_path / "base.pdf").read_bytes())
        # Corruption au milieu du fichier (zones xref/flux) : le parseur peut
        # récupérer ou refuser, mais seule ExtractionImpossible peut sortir.
        for indice in range(len(source) // 2, len(source) // 2 + 400):
            source[indice] ^= 0xFF
        corrompu = tmp_path / "corrompu.pdf"
        corrompu.write_bytes(bytes(source))
        try:
            _texte_complet(corrompu)
        except ExtractionImpossible:
            pass  # refus propre : contrat tenu

    def test_pdf_image_sans_texte_signale_sans_texte(self, tmp_path: Path) -> None:
        chemin = tmp_path / "image-seule.pdf"
        pdf = canvas.Canvas(str(chemin), pagesize=(LARGEUR, HAUTEUR))
        pdf.rect(72, HAUTEUR - 300, 300, 200, stroke=1, fill=1)
        pdf.save()
        with pytest.raises(ExtractionImpossible, match="sans texte"):
            analyser_pdf(chemin)


class TestVolumesDePages:
    def test_texte_en_page_2_apres_page_blanche(self, tmp_path: Path) -> None:
        chemin = _pdf_texte(tmp_path / "page2.pdf", pages_vides_avant=1)
        pages = analyser_pdf(chemin)
        assert len(pages) == 2
        assert "VARIANTE-000" in _texte_complet(chemin)

    def test_cent_vingt_pages(self, tmp_path: Path) -> None:
        chemin = _pdf_texte(tmp_path / "volumineux.pdf", nb_pages_texte=120)
        pages = analyser_pdf(chemin)
        assert len(pages) == 120
        assert "VARIANTE-119" in _texte_complet(chemin)


def _pdf_tableau(chemin: Path) -> Path:
    pdf = canvas.Canvas(str(chemin), pagesize=(LARGEUR, HAUTEUR))
    for x in (50, 200, 350):
        pdf.line(x, 600, x, 680)
    for y in (600, 640, 680):
        pdf.line(50, y, 350, y)
    for x, y, mot in ((65, 655, "MESURE"), (215, 655, "VALEUR"),
                       (65, 615, "GUINDANT"), (215, 615, "10,50")):
        pdf.drawString(x, y, mot)
    pdf.save()
    return chemin


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_rotation_preserve_tableau_et_boites_dans_la_page_affichee(tmp_path: Path, angle: int) -> None:
    source = _pdf_tableau(tmp_path / "base.pdf")
    pivote = _transformer(source, tmp_path / "pivote.pdf", lambda w: [p.rotate(angle) for p in w.pages])
    initiale = analyser_pdf(source)[0]
    analysee = analyser_pdf(pivote)[0]
    assert analysee.tableaux == initiale.tableaux == [[['MESURE', 'VALEUR'], ['GUINDANT', '10,50']]]
    assert [[m.texte for m in ligne.mots] for ligne in analysee.lignes] == [
        ["MESURE", "VALEUR"], ["GUINDANT", "10,50"],
    ]
    mots_initiaux = {m.texte: m for ligne in initiale.lignes for m in ligne.mots}
    mots_pivotes = {m.texte: m for ligne in analysee.lignes for m in ligne.mots}
    assert set(mots_pivotes) == set(mots_initiaux)
    for texte, ancien in mots_initiaux.items():
        nouveau = mots_pivotes[texte]
        if angle == 90:
            attendus = (HAUTEUR - ancien.bas, HAUTEUR - ancien.haut, ancien.x0, ancien.x1)
        elif angle == 180:
            attendus = (LARGEUR - ancien.x1, LARGEUR - ancien.x0, HAUTEUR - ancien.bas, HAUTEUR - ancien.haut)
        else:
            attendus = (ancien.haut, ancien.bas, LARGEUR - ancien.x1, LARGEUR - ancien.x0)
        # Le repère logique sert aux règles de lecture ; la zone publique doit
        # toujours suivre le PDF affiché et non ce repère normalisé interne.
        from seamtech_search.fiches.extraction import _zone_des_mots

        assert (nouveau.x0, nouveau.x1, nouveau.haut, nouveau.bas) == pytest.approx(
            (ancien.x0, ancien.x1, ancien.haut, ancien.bas), abs=0.1
        )
        zone = _zone_des_mots([nouveau])
        assert (zone.x0, zone.x1, zone.y0, zone.y1) == pytest.approx(attendus, abs=0.1)


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_rotation_compensee_deja_lisible_ne_doit_pas_etre_modifiee(tmp_path: Path, angle: int) -> None:
    source = _pdf_tableau(tmp_path / "base.pdf")
    def compenser(redacteur):
        for page in redacteur.pages:
            page.rotate(angle)
            page.transfer_rotation_to_content()
            page.rotate(-angle)
    deja_lisible = _transformer(source, tmp_path / "compense.pdf", compenser)
    attendu = analyser_pdf(source)[0]
    obtenu = analyser_pdf(deja_lisible)[0]
    assert [[m.texte for m in ligne.mots] for ligne in obtenu.lignes] == [
        ["MESURE", "VALEUR"], ["GUINDANT", "10,50"],
    ]
    assert obtenu.tableaux == attendu.tableaux
    mots = {m.texte: m for ligne in obtenu.lignes for m in ligne.mots}
    originaux = {m.texte: m for ligne in attendu.lignes for m in ligne.mots}
    for texte in mots:
        observe, original = mots[texte], originaux[texte]
        assert (observe.x0, observe.x1, observe.haut, observe.bas) == pytest.approx(
            (original.x0, original.x1, original.haut, original.bas), abs=0.1
        )


def test_tableau_correction_quasi_axe_ne_redresse_pas_les_diagonales():
    from types import SimpleNamespace

    from seamtech_search.fiches.extraction import _corriger_lignes_quasi_horizontales

    horizontale = {"object_type": "line", "orientation": "v", "x0": 0.0, "x1": 100.0,
                   "top": 5.0, "bottom": 5.0 + 1e-13, "height": 1e-13}
    diagonale = {"object_type": "line", "orientation": "v", "x0": 0.0, "x1": 100.0,
                 "top": 5.0, "bottom": 5.001, "height": 0.001}
    verticale = {"object_type": "line", "orientation": "v", "x0": 5.0, "x1": 5.0,
                 "top": 0.0, "bottom": 100.0, "height": 100.0}
    courbe = {"object_type": "curve", "orientation": "v", "x0": 0.0, "x1": 100.0,
              "top": 5.0, "bottom": 5.0 + 1e-13, "height": 1e-13}
    page = SimpleNamespace(edges=[horizontale, diagonale, verticale, courbe])
    _corriger_lignes_quasi_horizontales(page)
    assert horizontale["orientation"] == "h" and horizontale["top"] == horizontale["bottom"]
    assert all(edge["orientation"] == "v" for edge in (diagonale, verticale, courbe))
    assert diagonale["bottom"] == 5.001


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_rotation_media_decale_zone_correspond_aux_glyphes_visibles(tmp_path: Path, angle: int) -> None:
    import pdfplumber

    from seamtech_search.fiches.extraction import _zone_des_mots

    source = _pdf_tableau(tmp_path / "base.pdf")
    def decaler_et_pivoter(redacteur):
        for page in redacteur.pages:
            page.mediabox.lower_left = (10, 20)
            page.mediabox.upper_right = (605, 862)
            page.cropbox.lower_left = (10, 20)
            page.cropbox.upper_right = (605, 862)
            page.rotate(angle)
    pivot = _transformer(source, tmp_path / "decale.pdf", decaler_et_pivoter)
    mots = [mot for ligne in analyser_pdf(pivot)[0].lignes for mot in ligne.mots]
    mot = next(m for m in mots if m.texte == "MESURE")
    zone = _zone_des_mots([mot])
    with pdfplumber.open(pivot) as pdf:
        caracteres = pdf.pages[0].chars[:6]
        assert "".join(c["text"] for c in caracteres) == "MESURE"
        # Le canevas PDF.js commence au coin du CropBox, pas à l'origine
        # absolue pdfplumber ; le décalage doit aussi s'appliquer aux zones.
        x_vue, y_vue = pdf.pages[0].cropbox[:2]
        attendu = (min(c["x0"] for c in caracteres) - x_vue, max(c["x1"] for c in caracteres) - x_vue,
                   min(c["top"] for c in caracteres) - y_vue, max(c["bottom"] for c in caracteres) - y_vue)
    assert (zone.x0, zone.x1, zone.y0, zone.y1) == pytest.approx(attendu, abs=0.01)


def test_rotation_mixte_non_devinee_et_crop_normalise(tmp_path: Path) -> None:
    import pdfplumber

    from seamtech_search.pdf_orientation import page_de_lecture

    source = tmp_path / "mixte.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(LARGEUR, HAUTEUR))
    pdf.drawString(40, 700, "TEXTE HORIZONTAL")
    pdf.saveState()
    pdf.translate(200, 500)
    pdf.rotate(90)
    pdf.drawString(0, 0, "TEXTE VERTICAL")
    pdf.restoreState()
    pdf.save()
    mixte = _transformer(source, tmp_path / "mixte-90.pdf", lambda w: [p.rotate(90) for p in w.pages])
    with pdfplumber.open(mixte) as lecteur:
        page = lecteur.pages[0]
        attributs = dict(page.page_obj.attrs)
        for _ in range(2):
            lecture, rotation = page_de_lecture(page)
            assert lecture is page and rotation is None
            assert page.page_obj.attrs == attributs and page.page_obj.rotate == 90
    source_simple = _pdf_tableau(tmp_path / "tableau.pdf")
    def rogner(redacteur):
        for page in redacteur.pages:
            page.cropbox.lower_left = (20, 20)
            page.cropbox.upper_right = (500, 800)
            page.rotate(90)
    coupe = _transformer(source_simple, tmp_path / "coupe.pdf", rogner)
    with pdfplumber.open(coupe) as lecteur:
        page = lecteur.pages[0]
        assert page.cropbox != page.mediabox
        lecture, rotation = page_de_lecture(page)
        assert lecture is not page and rotation is not None
        assert [w["text"] for w in lecture.extract_words()] == ["MESURE", "VALEUR", "GUINDANT", "10,50"]
        from seamtech_search.pdf_orientation import fermer_page_lecture
        fermer_page_lecture(page, lecture)


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_index_rotation_compensee_reste_lisible(tmp_path: Path, angle: int) -> None:
    from seamtech_search.extractors import extract_text

    source = _pdf_texte(tmp_path / "source.pdf")
    def compenser(redacteur):
        for page in redacteur.pages:
            page.rotate(angle)
            page.transfer_rotation_to_content()
            page.rotate(-angle)
    compense = _transformer(source, tmp_path / "compense.pdf", compenser)
    assert "FICHE DE FABRICATION" in extract_text(compense, 100_000)


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_fiche_structurale_pivotee_conserve_tous_les_champs_et_zones(tmp_path: Path, angle: int) -> None:
    import pdfplumber

    from seamtech_search.fiches.extraction import extraire_fiche
    from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES

    source = Path(__file__).resolve().parent.parent / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
    pivot = _transformer(source, tmp_path / "fiche.pdf", lambda w: [p.rotate(angle) for p in w.pages])
    initiale = extraire_fiche(source, gabarits=list(GABARITS_EMBARQUES))
    obtenue = extraire_fiche(pivot, gabarits=list(GABARITS_EMBARQUES))
    champs_initiaux = {(c.champ, c.rang): c for c in initiale.tous_les_champs()}
    champs_pivotes = {(c.champ, c.rang): c for c in obtenue.tous_les_champs()}
    from seamtech_search.fiches.persistance import VERITE_7792, evaluer_verite

    assert len(champs_initiaux) == 48
    assert len(VERITE_7792) == 74
    assert evaluer_verite(obtenue, VERITE_7792)[0] == 1.0
    assert champs_pivotes.keys() == champs_initiaux.keys()
    with pdfplumber.open(source) as pdf:
        largeur, hauteur = pdf.pages[0].width, pdf.pages[0].height
    for cle, ancien in champs_initiaux.items():
        nouveau = champs_pivotes[cle]
        assert (nouveau.valeur_brute, nouveau.valeur_normalisee, nouveau.methode, nouveau.confiance, nouveau.page) == (
            ancien.valeur_brute, ancien.valeur_normalisee, ancien.methode, ancien.confiance, ancien.page
        )
        if ancien.zone is None:
            assert nouveau.zone is None
            continue
        assert nouveau.zone is not None and ancien.zone.page == nouveau.zone.page == 0
        z = ancien.zone
        if angle == 90:
            attendue = (hauteur - z.y1, hauteur - z.y0, z.x0, z.x1)
        elif angle == 180:
            attendue = (largeur - z.x1, largeur - z.x0, hauteur - z.y1, hauteur - z.y0)
        else:
            attendue = (z.y0, z.y1, largeur - z.x1, largeur - z.x0)
        assert (nouveau.zone.x0, nouveau.zone.x1, nouveau.zone.y0, nouveau.zone.y1) == pytest.approx(
            attendue, abs=0.02
        ), cle


def test_pdf_multipage_rotations_differentes_original_inchange(tmp_path: Path) -> None:
    import hashlib

    from seamtech_search.extractors import extract_text

    source = _pdf_texte(tmp_path / "source.pdf", nb_pages_texte=4)
    pivot = _transformer(source, tmp_path / "multipage.pdf", lambda w: [
        p.rotate(angle) for p, angle in zip(w.pages, (0, 90, 180, 270), strict=True)
    ])
    empreinte = hashlib.sha256(pivot.read_bytes()).digest()
    pages = analyser_pdf(pivot)
    index = extract_text(pivot, 100_000)
    assert len(pages) == 4
    for numero, page in enumerate(pages):
        texte = " ".join(m.texte for ligne in page.lignes for m in ligne.mots)
        assert f"VARIANTE-{numero:03d}" in texte
        assert f"VARIANTE-{numero:03d}" in index
        assert all(m.page == numero for ligne in page.lignes for m in ligne.mots)
    assert hashlib.sha256(pivot.read_bytes()).digest() == empreinte


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_zone_tient_compte_de_userunit_pdfjs(tmp_path: Path, angle: int) -> None:
    from pypdf.generic import NameObject, NumberObject

    from seamtech_search.fiches.extraction import _zone_des_mots

    source = _pdf_tableau(tmp_path / "source.pdf")
    pivot = _transformer(source, tmp_path / "pivot.pdf", lambda w: [p.rotate(angle) for p in w.pages])
    def grossir(redacteur):
        for page in redacteur.pages:
            page[NameObject("/UserUnit")] = NumberObject(2)
    grossi = _transformer(pivot, tmp_path / "grossi.pdf", grossir)
    def zone(path):
        mots = [m for ligne in analyser_pdf(path)[0].lignes for m in ligne.mots]
        return _zone_des_mots([next(m for m in mots if m.texte == "MESURE")])
    reference, resultat = zone(pivot), zone(grossi)
    assert (resultat.x0, resultat.y0, resultat.x1, resultat.y1) == pytest.approx(
        (reference.x0 * 2, reference.y0 * 2, reference.x1 * 2, reference.y1 * 2), abs=0.01
    )


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [(-20, 20, 500, 900), (-100, -100, -50, -50)])
def test_vue_rognage_depasse_media_ou_vide_utilise_intersection_pdfjs(
    tmp_path: Path, angle: int, crop: tuple[int, int, int, int],
) -> None:
    import pdfplumber

    from seamtech_search.fiches.extraction import _zone_des_mots

    source = _pdf_tableau(tmp_path / "source.pdf")
    def rogner(redacteur):
        for page in redacteur.pages:
            page.cropbox.lower_left = crop[:2]
            page.cropbox.upper_right = crop[2:]
            page.rotate(angle)
    pdf_rogné = _transformer(source, tmp_path / "rogne.pdf", rogner)
    with pdfplumber.open(pdf_rogné) as doc:
        page = doc.pages[0]
        # Calcul indépendant sur les coordonnées PDF non pivotées, puis
        # origine de la vue à l'écran selon la rotation PDF.js.
        x0, y0, x1, y1 = max(0, crop[0]), max(0, crop[1]), min(LARGEUR, crop[2]), min(HAUTEUR, crop[3])
        if x1 <= x0 or y1 <= y0:
            x0, y0, x1, y1 = 0, 0, LARGEUR, HAUTEUR
        origines = {0: (x0, HAUTEUR - y1), 90: (y0, x0),
                    180: (LARGEUR - x1, y0), 270: (HAUTEUR - y1, LARGEUR - x1)}
        ox, oy = origines[angle]
        chars = page.chars[:6]
        attendu = (min(c["x0"] for c in chars) - ox, max(c["x1"] for c in chars) - ox,
                   min(c["top"] for c in chars) - oy, max(c["bottom"] for c in chars) - oy)
    mots = [m for ligne in analyser_pdf(pdf_rogné)[0].lignes for m in ligne.mots]
    zone_mot = _zone_des_mots([next(m for m in mots if m.texte == "MESURE")])
    assert (zone_mot.x0, zone_mot.x1, zone_mot.y0, zone_mot.y1) == pytest.approx(attendu, abs=0.01)


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_rotation_avec_crop_reste_lisible(tmp_path: Path, angle: int) -> None:
    from seamtech_search.extractors import extract_text

    source = _pdf_tableau(tmp_path / "base.pdf")
    def rogner(redacteur):
        for page in redacteur.pages:
            page.cropbox.lower_left = (20, 20)
            page.cropbox.upper_right = (500, 800)
            page.rotate(angle)
    pivot = _transformer(source, tmp_path / "crop.pdf", rogner)
    analyse = analyser_pdf(pivot)[0]
    assert analyse.tableaux == [[['MESURE', 'VALEUR'], ['GUINDANT', '10,50']]]
    assert [[m.texte for m in ligne.mots] for ligne in analyse.lignes] == [
        ["MESURE", "VALEUR"], ["GUINDANT", "10,50"]
    ]
    assert "MESURE VALEUR" in extract_text(pivot, 100_000)


def test_rotation_texte_mixte_conserve_tous_les_mots(tmp_path: Path) -> None:
    source = tmp_path / "mixte.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(LARGEUR, HAUTEUR))
    pdf.drawString(40, 700, "LIGNE HORIZONTALE")
    pdf.saveState()
    pdf.translate(300, 400)
    pdf.rotate(90)
    pdf.drawString(0, 0, "LEGENDE VERTICALE")
    pdf.restoreState()
    pdf.save()
    pivot = _transformer(source, tmp_path / "mixte-pivote.pdf", lambda w: [p.rotate(90) for p in w.pages])
    texte = _texte_complet(pivot)
    assert "LIGNE HORIZONTALE" in texte
    assert "LEGENDE VERTICALE" in texte


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_crop_exclut_texte_et_tableau_hors_vue(tmp_path: Path, angle: int) -> None:
    from seamtech_search.extractors import extract_text

    source = tmp_path / "base.pdf"
    c = canvas.Canvas(str(source), pagesize=(LARGEUR, HAUTEUR))
    c.drawString(50, 700, "VISIBLE DANS VUE")
    c.drawString(400, 700, "HORS VUE")
    for x in (400, 470, 540):
        c.line(x, 600, x, 680)
    for y in (600, 640, 680):
        c.line(400, y, 540, y)
    c.drawString(405, 655, "CACHE")
    c.drawString(475, 655, "SECRET")
    c.save()
    def rogner(redacteur):
        for page in redacteur.pages:
            page.cropbox.lower_left = (0, 0)
            page.cropbox.upper_right = (300, HAUTEUR)
            page.rotate(angle)
    rogne = _transformer(source, tmp_path / "rogne.pdf", rogner)
    pages = analyser_pdf(rogne)
    texte = " ".join(m.texte for ligne in pages[0].lignes for m in ligne.mots)
    assert "VISIBLE DANS VUE" in texte
    assert "HORS" not in texte and "CACHE" not in texte and "SECRET" not in texte
    assert pages[0].tableaux == []
    index = extract_text(rogne, 100_000)
    assert "VISIBLE DANS VUE" in index
    assert "HORS" not in index and "CACHE" not in index and "SECRET" not in index


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_crop_tronque_les_mots_et_boites_aux_limites_visibles(tmp_path: Path, angle: int) -> None:
    import pdfplumber

    from seamtech_search.fiches.extraction import _zone_des_mots

    source = _pdf_tableau(tmp_path / "source.pdf")
    def rogner(redacteur):
        for page in redacteur.pages:
            page.cropbox.lower_left = (75, 600)
            page.cropbox.upper_right = (250, 680)
            page.rotate(angle)
    rogne = _transformer(source, tmp_path / "rogne.pdf", rogner)
    analyse = analyser_pdf(rogne)[0]
    assert [[m.texte for m in ligne.mots] for ligne in analyse.lignes] == [
        ["ESURE", "VALEU"], ["UINDANT", "10,50"]
    ]
    assert analyse.tableaux == []
    with pdfplumber.open(rogne) as doc:
        # Les dimensions du viewport correspondant à la zone PDF de
        # 175 × 80 points sont permutées sur les rotations quart de tour.
        largeur, hauteur = ((80, 175) if angle in (90, 270) else (175, 80))
        assert doc.pages[0].rotation == angle
    for ligne in analyse.lignes:
        for mot in ligne.mots:
            z = _zone_des_mots([mot])
            assert 0 <= z.x0 < z.x1 <= largeur + 0.01
            assert 0 <= z.y0 < z.y1 <= hauteur + 0.01


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_crop_limite_aretes_de_chemins_composites(tmp_path: Path, angle: int) -> None:
    import pdfplumber

    from seamtech_search.pdf_orientation import fermer_page_lecture, page_de_lecture

    source = tmp_path / "chemin.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(LARGEUR, HAUTEUR))
    pdf.drawString(20, 700, "VISIBLE")
    chemin = pdf.beginPath()
    chemin.moveTo(20, 650)
    chemin.lineTo(500, 650)
    chemin.lineTo(500, 550)
    chemin.lineTo(400, 550)
    chemin.lineTo(400, 650)
    pdf.drawPath(chemin, stroke=1)
    pdf.save()
    def rogner(redacteur):
        for page in redacteur.pages:
            page.cropbox.lower_left = (0, 0)
            page.cropbox.upper_right = (300, HAUTEUR)
            page.rotate(angle)
    rogne = _transformer(source, tmp_path / "rognage.pdf", rogner)
    with pdfplumber.open(rogne) as doc:
        originale = doc.pages[0]
        lecture, _ = page_de_lecture(originale)
        try:
            x0, y0, x1, y1 = lecture.bbox
            assert lecture.edges
            for edge in lecture.edges:
                assert x0 - 1e-7 <= edge["x0"] <= edge["x1"] <= x1 + 1e-7
                assert y0 - 1e-7 <= edge["top"] <= edge["bottom"] <= y1 + 1e-7
        finally:
            fermer_page_lecture(originale, lecture)


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_brouillon_bbox_correspond_a_la_vue_sur_pdf_pivote_et_rogne(tmp_path: Path, angle: int) -> None:
    from pypdf.generic import NameObject, NumberObject

    from seamtech_search.fiches.extraction import _zone_des_mots
    from seamtech_search.fiches.gabarit_brouillon import _extraire_mots_avec_bbox

    source = _pdf_tableau(tmp_path / "source.pdf")
    def rogner(redacteur):
        for page in redacteur.pages:
            page.mediabox.lower_left = (10, 20)
            page.mediabox.upper_right = (605, 862)
            page.cropbox.lower_left = (30, 100)
            page.cropbox.upper_right = (510, 800)
            page[NameObject("/UserUnit")] = NumberObject(2)
            page.rotate(angle)
    pdf = _transformer(source, tmp_path / "pivote.pdf", rogner)
    pages = analyser_pdf(pdf)
    cible = next(m for ligne in pages[0].lignes for m in ligne.mots if m.texte == "MESURE")
    zone = _zone_des_mots([cible])
    mots = _extraire_mots_avec_bbox(pdf)
    mot = next(m for m in mots if m["texte"] == "MESURE")
    assert (mot["x0"], mot["x1"], mot["y0"], mot["y1"]) == pytest.approx(
        (zone.x0, zone.x1, zone.y0, zone.y1), abs=0.01
    )
    assert (mot["largeur_page"], mot["hauteur_page"]) == ((1400, 960) if angle in (90, 270) else (960, 1400))


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_orientation_mixte_signalee_sans_divulgation(tmp_path: Path, caplog, angle: int) -> None:
    from seamtech_search.extractors import extract_file
    from seamtech_search.fiches.gabarit_brouillon import _extraire_mots_avec_bbox

    source = tmp_path / "mixte.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(LARGEUR, HAUTEUR))
    pdf.drawString(40, 700, "SECRET SYNTHETIQUE HORIZONTAL")
    pdf.saveState()
    pdf.translate(300, 400)
    pdf.rotate(90)
    pdf.drawString(0, 0, "SECRET SYNTHETIQUE VERTICAL")
    pdf.restoreState()
    pdf.save()
    pivote = _transformer(source, tmp_path / "pivote.pdf", lambda w: [p.rotate(angle) for p in w.pages])
    for fonction in (analyser_pdf, lambda p: extract_file(p), _extraire_mots_avec_bbox):
        caplog.clear()
        with caplog.at_level("WARNING", logger="seamtech_search.pdf_orientation"):
            if fonction is _extraire_mots_avec_bbox:
                with pytest.raises(ExtractionImpossible, match="orientations mixtes"):
                    fonction(pivote)
            else:
                fonction(pivote)
        assert any("orientation" in record.message.lower() for record in caplog.records)
        assert "SECRET" not in caplog.text and str(tmp_path) not in caplog.text
        assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_orientation_homogene_ne_signale_pas_fausse_ambiguite(tmp_path: Path, caplog, angle: int) -> None:
    source = _pdf_texte(tmp_path / "homogene.pdf")
    pivote = _transformer(source, tmp_path / "pivote.pdf", lambda w: [p.rotate(angle) for p in w.pages])
    with caplog.at_level("WARNING", logger="seamtech_search.pdf_orientation"):
        assert "FICHE DE FABRICATION" in _texte_complet(pivote)
    assert not caplog.records


def test_index_libere_page_normalisee_si_lecture_echoue(tmp_path: Path, monkeypatch) -> None:
    import pdfplumber.page

    from seamtech_search import extractors, pdf_orientation

    source = _pdf_texte(tmp_path / "base.pdf")
    pivote = _transformer(source, tmp_path / "pivote.pdf", lambda w: [p.rotate(90) for p in w.pages])
    fermees = []
    fermer = pdf_orientation.fermer_page_lecture
    def suivre(originale, lecture):
        fermees.append(lecture is not originale)
        fermer(originale, lecture)
    def panne(*_args, **_kwargs):
        raise ValueError("panne synthétique")
    monkeypatch.setattr(pdf_orientation, "fermer_page_lecture", suivre)
    monkeypatch.setattr(pdfplumber.page.Page, "extract_text", panne)
    resultat = extractors.extract_file(pivote)
    assert resultat.status == "unavailable" and not resultat.text
    # Le PDF d'origine est préservé, mais le repli sans vue vérifiée n'est
    # plus publié : il pourrait contenir du texte hors clipping.
    from pypdf import PdfReader
    assert "FICHE" in PdfReader(pivote).pages[0].extract_text()
    assert fermees == [True]


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_mixte_visible_meme_sans_caractere_compatible_a_rotation(tmp_path: Path, caplog, angle: int) -> None:
    # Deux orientations qui ne sont pas celle de normalisation : elles ne
    # doivent pas passer sous silence simplement parce que la cible est absente.
    source = tmp_path / "mixte.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(LARGEUR, HAUTEUR))
    for rotation, x in ((45, 100), (-45, 300)):
        pdf.saveState()
        pdf.translate(x, 500)
        pdf.rotate(rotation)
        pdf.drawString(0, 0, "SYNTHETIQUE")
        pdf.restoreState()
    pdf.save()
    pivote = _transformer(source, tmp_path / "pivote.pdf", lambda w: [p.rotate(angle) for p in w.pages])
    with caplog.at_level("WARNING", logger="seamtech_search.pdf_orientation"):
        analyser_pdf(pivote)
    assert any("orientations mixtes" in record.message for record in caplog.records)


def test_brouillon_extraction_mots_ne_calcule_pas_tableaux(tmp_path: Path, monkeypatch) -> None:
    import pdfplumber.page

    from seamtech_search.fiches.gabarit_brouillon import _extraire_mots_avec_bbox

    source = _pdf_tableau(tmp_path / "tableau.pdf")
    def interdit(*_args, **_kwargs):
        pytest.fail("Le brouillon de mots ne doit pas calculer les tableaux")
    monkeypatch.setattr(pdfplumber.page.Page, "extract_tables", interdit)
    mots = _extraire_mots_avec_bbox(source)
    assert any(m["texte"] == "MESURE" and m["x1"] > m["x0"] for m in mots)


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_orientation_hors_crop_ne_degrade_pas_lecture_visible(tmp_path: Path, caplog, angle: int) -> None:
    source = tmp_path / "base.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(LARGEUR, HAUTEUR))
    pdf.drawString(60, 700, "VISIBLE HORIZONTAL")
    pdf.saveState()
    pdf.translate(540, 300)
    pdf.rotate(90)
    pdf.drawString(0, 0, "CACHE VERTICAL")
    pdf.restoreState()
    pdf.save()
    def rogner(redacteur):
        page = redacteur.pages[0]
        page.cropbox.lower_left = (30, 100)
        page.cropbox.upper_right = (450, 800)
        page.rotate(angle)
    pivote = _transformer(source, tmp_path / "pivote.pdf", rogner)
    with caplog.at_level("WARNING", logger="seamtech_search.pdf_orientation"):
        texte = _texte_complet(pivote)
    assert "VISIBLE HORIZONTAL" in texte and "CACHE" not in texte
    assert not caplog.records


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_orientation_mixte_fiche_a_reprendre_explicite(tmp_path: Path, angle: int) -> None:
    from seamtech_search.fiches.extraction import extraire_fiche
    from seamtech_search.fiches.gabarits import GabaritDef, RegleChamp
    from seamtech_search.fiches.persistance import routage

    source = tmp_path / "fiche.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(LARGEUR, HAUTEUR))
    pdf.drawString(40, 700, "FICHE CODE : TEST")
    pdf.saveState()
    pdf.translate(300, 400)
    pdf.rotate(90)
    pdf.drawString(0, 0, "LEGENDE")
    pdf.restoreState()
    pdf.save()
    pivote = _transformer(source, tmp_path / "pivote.pdf", lambda w: [p.rotate(angle) for p in w.pages])
    gabarit = GabaritDef(code="TEST", ancres_detection=["fiche"], champs=[RegleChamp(cible="fiche.code", ancres=["code"])])
    fiche = extraire_fiche(pivote, [gabarit], gabarit_code="TEST")
    anomalies = [a for a in fiche.anomalies if a.code == "orientations_pdf_mixtes"]
    assert len(anomalies) == 1 and anomalies[0].gravite == "forte"
    assert routage(fiche)["voie"] == "reprise_complete"


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_brouillon_refuse_orientation_mixte_sans_donner_fausse_confiance(tmp_path: Path, angle: int) -> None:
    from seamtech_search.fiches.gabarit_brouillon import generer_brouillon_depuis_pdf
    source = tmp_path / "source.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(LARGEUR, HAUTEUR))
    pdf.drawString(40, 700, "FICHE CODE : SYNTHETIQUE")
    pdf.saveState()
    pdf.translate(300, 400)
    pdf.rotate(90)
    pdf.drawString(0, 0, "LEGENDE")
    pdf.restoreState()
    pdf.save()
    pivote = _transformer(source, tmp_path / "pivote.pdf", lambda w: [p.rotate(angle) for p in w.pages])
    with pytest.raises(ExtractionImpossible, match="orientations mixtes"):
        generer_brouillon_depuis_pdf(pivote)


def test_brouillon_repli_ne_contourne_pas_refus_mixte(tmp_path: Path, monkeypatch) -> None:
    import pdfplumber

    from seamtech_search.fiches.gabarit_brouillon import generer_brouillon_depuis_pdf
    source = tmp_path / "source.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(LARGEUR, HAUTEUR))
    pdf.drawString(40, 700, "FICHE CODE : SYNTHETIQUE")
    pdf.saveState()
    pdf.translate(300, 400)
    pdf.rotate(90)
    pdf.drawString(0, 0, "LEGENDE")
    pdf.restoreState()
    pdf.save()
    ouvrir = pdfplumber.open
    appels = 0
    def panne_une_fois(*args, **kwargs):
        nonlocal appels
        appels += 1
        if appels == 1:
            raise RuntimeError("panne synthétique")
        return ouvrir(*args, **kwargs)
    monkeypatch.setattr(pdfplumber, "open", panne_une_fois)
    with pytest.raises(ExtractionImpossible, match="orientations mixtes"):
        generer_brouillon_depuis_pdf(source)
    assert appels == 2


@pytest.mark.parametrize("reconnu", [True, False])
def test_detection_avertit_meme_si_gabarit_non_reconnu(tmp_path: Path, monkeypatch, reconnu: bool) -> None:
    from seamtech_search.fiches import routes
    from seamtech_search.fiches.gabarits import GabaritDef

    source = tmp_path / "source.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(LARGEUR, HAUTEUR))
    pdf.drawString(40, 700, "FICHE SYNTHETIQUE")
    pdf.saveState()
    pdf.translate(300, 400)
    pdf.rotate(90)
    pdf.drawString(0, 0, "LEGENDE")
    pdf.restoreState()
    pdf.save()
    class Index:
        is_postgres = True
    gabarit = GabaritDef(code="TEST", ancres_detection=["fiche" if reconnu else "ABSENT"])
    monkeypatch.setattr(routes, "charger_gabarits", lambda _index: [gabarit])
    resultat = routes.detecter_pdf(Index(), source)
    assert resultat["detecte"] is reconnu
    assert resultat["orientations_pdf_mixtes"] is True
    assert resultat["voie"] == "reprise_complete"
    assert "orientations mixtes" in resultat["avertissement"].lower()


@pytest.mark.parametrize("reconnu", [True, False])
def test_scanner_refus_mixte_explicite_si_aucune_fiche_selectionnable(tmp_path: Path, reconnu: bool) -> None:
    from seamtech_search.fiches.depot import scanner_dossier
    from seamtech_search.fiches.gabarits import GabaritDef

    dossier = tmp_path / "lot"
    dossier.mkdir()
    chemin = dossier / "document.pdf"
    pdf = canvas.Canvas(str(chemin), pagesize=(LARGEUR, HAUTEUR))
    pdf.drawString(40, 700, "FICHE SYNTHETIQUE")
    pdf.saveState()
    pdf.translate(300, 400)
    pdf.rotate(90)
    pdf.drawString(0, 0, "LEGENDE")
    pdf.restoreState()
    pdf.save()
    gabarit = GabaritDef(code="TEST", ancres_detection=["fiche" if reconnu else "ABSENT"])
    plan = scanner_dossier(dossier, [gabarit])
    assert not plan.accepte
    assert "orientations mixtes" in plan.raison_refus.lower()
    assert "relecture" in plan.raison_refus.lower()
