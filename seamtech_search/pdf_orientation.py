"""Repère de lecture et repère affiché pour les pages PDF pivotées.

On ne modifie jamais le PDF : des copies privées de l'objet de page pdfminer
permettent de lire les orientations orthogonales vérifiables. Les pages
mixtes conservent un signal de revue ; une transformation non vérifiable
conserve le comportement du lecteur d'origine.
"""
from __future__ import annotations

import copy
import logging
import math
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Iterator

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RotationAffichage:
    angle: int
    dx: float
    dy: float

    def boite(self, x0: float, x1: float, haut: float, bas: float) -> tuple[float, float, float, float]:
        if self.angle == 90:
            return self.dx - bas, self.dx - haut, self.dy + x0, self.dy + x1
        if self.angle == 180:
            return self.dx - x1, self.dx - x0, self.dy - bas, self.dy - haut
        return self.dx + haut, self.dx + bas, self.dy - x1, self.dy - x0

    def inverse(self, x0: float, x1: float, haut: float, bas: float) -> tuple[float, float, float, float]:
        if self.angle == 90:
            return haut - self.dy, bas - self.dy, self.dx - x1, self.dx - x0
        if self.angle == 180:
            return self.dx - x1, self.dx - x0, self.dy - bas, self.dy - haut
        return self.dy - bas, self.dy - haut, x0 - self.dx, x1 - self.dx


def boite_vue(page: object) -> tuple[float, float, float, float]:
    """Intersection MediaBox/CropBox utilisée par PDF.js, ou MediaBox si vide."""
    # pdfplumber 0.11.10 échange les axes de CropBox pour 90/270 mais
    # n'applique pas les inversions de 90/180 : ses bornes peuvent donc
    # correspondre à la moitié opposée de la page. Reconstituer la vue à
    # partir des coordonnées PDF non pivotées, puis projeter les coins dans
    # le repère des objets pdfplumber.
    from pdfminer.pdftypes import resolve_all

    def rectangle(brut: object) -> tuple[float, float, float, float] | None:
        valeur = resolve_all(brut)
        if not isinstance(valeur, (list, tuple)) or len(valeur) != 4:
            return None
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in valeur):
            return None
        x0, x1 = sorted((valeur[0], valeur[2]))
        y0, y1 = sorted((valeur[1], valeur[3]))
        return x0, y0, x1, y1

    media = rectangle(page.page_obj.mediabox)
    if media is None:
        raise ValueError("MediaBox PDF invalide")
    crop = rectangle(page.page_obj.attrs.get("CropBox")) or media
    vue = (max(media[0], crop[0]), max(media[1], crop[1]), min(media[2], crop[2]), min(media[3], crop[3]))
    if vue[2] <= vue[0] or vue[3] <= vue[1]:
        vue = media
    mx0, my0, mx1, my1 = media
    mb_x, mb_top = page.mediabox[:2]
    def projeter(x: float, y: float) -> tuple[float, float]:
        if page.rotation == 90:
            px, py = y - my0, mx1 - x
        elif page.rotation == 180:
            px, py = mx1 - x, my1 - y
        elif page.rotation == 270:
            px, py = my1 - y, x - mx0
        else:
            px, py = x - mx0, y - my0
        return px + mb_x, page.height - py + mb_top
    coins = [projeter(x, y) for x in (vue[0], vue[2]) for y in (vue[1], vue[3])]
    return (min(p[0] for p in coins), min(p[1] for p in coins),
            max(p[0] for p in coins), max(p[1] for p in coins))


def unite_vue(page: object) -> float:
    """Facteur UserUnit appliqué par PDF.js au viewport (défaut 1)."""
    from pdfminer.pdftypes import resolve1

    unite = resolve1(page.page_obj.attrs.get("UserUnit", 1))
    if isinstance(unite, bool) or not isinstance(unite, (int, float)) or not 0 < unite < float("inf"):
        return 1.0
    return float(unite)


def _rogner_vue(originale: object, lecture: object, rotation: RotationAffichage | None) -> object:
    vue = boite_vue(originale)
    # pdfplumber peut livrer des objets hors MediaBox même lorsque CropBox
    # et MediaBox coïncident. Ne conserver la page originale que si tous ses
    # objets sont réellement contenus dans la vue ; en cas de doute, rogner.
    if lecture is originale and rotation is None and vue == tuple(originale.mediabox):
        try:
            objets = (objet for groupe in lecture.objects.values() for objet in groupe)
            if all(
                all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                    for v in (objet['x0'], objet['top'], objet['x1'], objet['bottom']))
                and vue[0] <= objet['x0'] <= objet['x1'] <= vue[2]
                and vue[1] <= objet['top'] <= objet['bottom'] <= vue[3]
                for objet in objets
            ):
                return lecture
        except (KeyError, TypeError, ValueError):
            pass
    if rotation is not None:
        x0, x1, haut, bas = rotation.inverse(vue[0], vue[2], vue[1], vue[3])
        vue = (x0, haut, x1, bas)
    # L'inversion des transformations peut introduire des écarts machine aux
    # limites ; strict=False laisse pdfplumber appliquer l'intersection.
    rognee = lecture.crop(vue, strict=False)
    # pdfplumber tronque le rectangle des courbes mais conserve leurs points
    # originaux ; curve_edges peut ainsi reconstruire des traits hors vue.
    # Reborner les arêtes calculées, sans modifier l'objet page original.
    from pdfplumber.utils import crop_to_bbox

    rognee._edges = crop_to_bbox(rognee.edges, vue)
    return rognee


def fermer_page_lecture(originale: object, lecture: object) -> None:
    """Libère aussi le parent normalisé d'une page recadrée, pas l'originale."""
    while lecture is not originale:
        parent = getattr(lecture, "parent_page", None)
        lecture.close()
        if parent is None:
            return
        lecture = parent


def orientations_visibles(page: object) -> tuple[bool, bool, bool]:
    """(mélangées, toutes compatibles avec la rotation, matrices exploitables)."""
    angle = getattr(page, "rotation", 0)
    vue = boite_vue(page)
    visibles = [c for c in page.chars if c.get("text", "").strip()
                and c["x1"] > vue[0] and c["x0"] < vue[2]
                and c["bottom"] > vue[1] and c["top"] < vue[3]]
    if not visibles:
        return False, False, True
    cible = math.radians(-angle)
    autres = False
    premiere_orientation = None
    mixte = False
    exploitable = True
    for char in visibles:
        matrice = char.get("matrix")
        if not isinstance(matrice, (tuple, list)) or len(matrice) < 4:
            exploitable = False
            continue
        a, b = matrice[:2]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (a, b)):
            exploitable = False
            continue
        if math.hypot(a, b) < 1e-9:
            exploitable = False
            continue
        orientation = math.atan2(b, a)
        if premiere_orientation is None:
            premiere_orientation = orientation
        ecart = math.atan2(math.sin(orientation - premiere_orientation), math.cos(orientation - premiere_orientation))
        mixte |= abs(ecart) > math.radians(0.1)
        difference = math.atan2(math.sin(orientation - cible), math.cos(orientation - cible))
        autres |= abs(difference) > math.radians(0.1)
    return mixte, exploitable and not autres, exploitable


def _quadrant_caractere(caractere: dict) -> int | None:
    """Angle orthogonal exploitable, sans miroir ni cisaillement significatif."""
    matrice = caractere.get("matrix")
    if not isinstance(matrice, (list, tuple)) or len(matrice) < 4:
        return None
    a, b, c, d = matrice[:4]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (a, b, c, d)):
        return None
    longueur_x, longueur_y = math.hypot(a, b), math.hypot(c, d)
    if longueur_x < 1e-9 or longueur_y < 1e-9:
        return None
    produit = longueur_x * longueur_y
    if a * d - b * c <= 0 or abs(a * c + b * d) / produit > math.sin(math.radians(0.1)):
        return None
    orientation = math.atan2(b, a)
    quart = round(orientation / (math.pi / 2))
    ecart = math.atan2(math.sin(orientation - quart * math.pi / 2),
                       math.cos(orientation - quart * math.pi / 2))
    return quart % 4 if abs(ecart) <= math.radians(0.1) else None


def _caractere_visible(caractere: dict, vue: tuple[float, float, float, float]) -> bool:
    return (caractere["x1"] > vue[0] and caractere["x0"] < vue[2]
            and caractere["bottom"] > vue[1] and caractere["top"] < vue[3])


def orientation_non_verifiee(
    page: object,
    rotation: RotationAffichage | None,
    lectures: list[tuple[object, RotationAffichage | None]] | None,
) -> bool:
    """Signale une lecture de glyphes que nos transformations ne certifient pas.

    Une page sans texte reste éligible à l'OCR. Les transformations avec
    miroir, cisaillement ou angle oblique, ainsi qu'un refus de projection,
    gardent leur texte de repli mais ne doivent pas être déclarées complètes.
    """
    vue = boite_vue(page)
    visibles = [c for c in page.chars if c.get("text", "").strip() and _caractere_visible(c, vue)]
    if not visibles:
        return False
    mixte, compatible, exploitable = orientations_visibles(page)
    if (not exploitable or getattr(page, "rotation", 0) not in (0, 90, 180, 270)
            or any(_quadrant_caractere(c) is None for c in visibles)):
        return True
    if mixte or not compatible:
        return not lectures
    return page.rotation in (90, 180, 270) and rotation is None


@contextmanager
def lectures_mixtes_verifiees(page: object) -> Iterator[list[tuple[object, RotationAffichage | None]] | None]:
    """Lectures orthogonales vérifiées, mixtes ou homogènes mal orientées.

    Les copies privées de PDFPage ne mutent jamais le document d'origine.
    Chaque transformation est confrontée à tous les glyphes, puis chaque vue
    est recadrée et filtrée à une seule orientation. L'appelant garde le
    signal de revue métier : un tableau à contenus mixtes reste ambigu.
    Une page homogène déjà compatible est traitée par ``page_de_lecture``.
    """
    mixte, compatible, exploitable = orientations_visibles(page)
    if ((not mixte and compatible) or not exploitable
            or page.rotation not in (0, 90, 180, 270)):
        yield None
        return

    from pdfplumber.page import Page

    # Les objets dont la visibilité n'est pas démontrée sont retirés de chaque
    # lecture : ces vues, et non la page d'origine, fournissent le texte publié
    # pour une page à orientations mixtes. La garde lève pour une faute
    # structurelle, comme dans ``page_de_lecture``.
    from seamtech_search.pdf_vue_graphique import objets_a_supprimer, verifier_vue_graphique

    boites = objets_a_supprimer(verifier_vue_graphique(page))
    hauteur = float(getattr(page, "height", 0.0) or 0.0)
    interdits = _boites_retirees_en_haut_de_page(boites, hauteur)
    vue = boite_vue(page)
    originaux = [c for c in page.chars if c.get("text", "").strip()]
    visibles = [c for c in originaux if _caractere_visible(c, vue) and not _objet_retire(c, interdits)]
    quadrants = {_quadrant_caractere(c) for c in visibles}
    if None in quadrants or not quadrants:
        yield None
        return

    with ExitStack() as ressources:
        lectures: list[tuple[object, RotationAffichage | None]] = []
        try:
            for quadrant in sorted(quadrants):
                cible = (page.rotation + 90 * quadrant) % 360
                objet = copy.copy(page.page_obj)
                objet.attrs = page.page_obj.attrs.copy()
                objet.attrs["Rotate"] = cible
                objet.rotate = cible
                normalisee = Page(page.pdf, objet, page.page_number, page.initial_doctop)
                ressources.callback(normalisee.close)
                bruts = [c for c in normalisee.chars if c.get("text", "").strip()]
                if len(bruts) != len(originaux) or not bruts:
                    raise ValueError("nombre de glyphes différent")

                delta = (page.rotation - cible) % 360
                rotation = None
                premier, affiche = bruts[0], originaux[0]
                if delta == 90:
                    rotation = RotationAffichage(delta, affiche["x0"] + premier["bottom"],
                                                 affiche["top"] - premier["x0"])
                elif delta == 180:
                    rotation = RotationAffichage(delta, affiche["x0"] + premier["x1"],
                                                 affiche["top"] + premier["bottom"])
                elif delta == 270:
                    rotation = RotationAffichage(delta, affiche["x0"] - premier["top"],
                                                 affiche["top"] + premier["x1"])
                for brut, original in zip(bruts, originaux, strict=True):
                    if brut.get("text") != original.get("text"):
                        raise ValueError("glyphes différents")
                    estimee = (brut["x0"], brut["x1"], brut["top"], brut["bottom"])
                    if rotation is not None:
                        estimee = rotation.boite(*estimee)
                    reelle = (original["x0"], original["x1"], original["top"], original["bottom"])
                    if any(not math.isfinite(v) for v in (*estimee, *reelle)) or any(
                        abs(a - b) > 0.01 for a, b in zip(estimee, reelle, strict=True)
                    ):
                        raise ValueError("projection des glyphes incertaine")

                normale_filtree = _page_sans_objets_non_demontres(normalisee, boites, rotation, hauteur)
                if normale_filtree is not normalisee:
                    ressources.callback(normale_filtree.close)
                rognee = _rogner_vue(page, normale_filtree, rotation)
                if rognee is not normale_filtree:
                    ressources.callback(rognee.close)
                filtree = rognee.filter(
                    lambda objet: objet.get("object_type") != "char" or _quadrant_caractere(objet) == 0
                )
                ressources.callback(filtree.close)
                # FilteredPage reconstruit sinon les arêtes des courbes à
                # partir de leurs points hors CropBox. Le filtre ne retire
                # aucun objet graphique : conserver les arêtes déjà bornées.
                filtree._edges = [dict(arete) for arete in rognee.edges]
                attendus = [c.get("text") for c in visibles if _quadrant_caractere(c) == quadrant]
                observes = [c.get("text") for c in filtree.chars if c.get("text", "").strip()]
                if attendus != observes:
                    raise ValueError("glyphes visibles différents")
                lectures.append((filtree, rotation))
        except Exception:
            # Les pages mixtes restent signalées et traitées comme ambiguës ;
            # ne jamais appliquer une transformation impossible à vérifier.
            yield None
        else:
            yield lectures


def _page_sans_objets_non_demontres(
    vue: object,
    boites: tuple[tuple[float, float, float, float], ...],
    rotation: RotationAffichage | None,
    hauteur_affichee: float,
) -> object:
    """Copie de la vue dont les objets non démontrés sont retirés.

    ``boites`` sont les boîtes mesurées par la garde dans le repère DEVICE de la
    page AFFICHÉE (y vers le haut) ; pdfplumber mesure depuis le haut de page
    (``top``/``bottom``), d'où la conversion par ``hauteur_affichee``. Quand la
    vue a été normalisée (rotation appliquée), les boîtes sont projetées dans
    son repère par ``RotationAffichage.inverse``.

    pdfplumber publie des objets ``dict`` (pas d'attribut ``bbox``) ; une
    tolérance absorbe l'arrondi entre les repères. Un objet retiré n'est ni
    remplacé ni deviné.
    """
    if not boites:
        return vue
    affichees = [
        (x0, hauteur_affichee - y1, x1, hauteur_affichee - y0) for x0, y0, x1, y1 in boites
    ]
    if rotation is not None:
        projetees = []
        for x0, haut, x1, bas in affichees:
            bx0, bx1, bhaut, bbas = rotation.inverse(x0, x1, haut, bas)
            projetees.append((bx0, bhaut, bx1, bbas))
        cibles = projetees
    else:
        cibles = affichees

    def cle(objet: object) -> tuple[float, float, float, float] | None:
        if not isinstance(objet, dict):
            return None
        valeurs = (objet.get("x0"), objet.get("top"), objet.get("x1"), objet.get("bottom"))
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in valeurs):
            return None
        return (float(valeurs[0]), float(valeurs[1]), float(valeurs[2]), float(valeurs[3]))

    def autorise(objet: object) -> bool:
        boite = cle(objet)
        if boite is None:
            return True
        return not any(
            all(abs(a - b) <= 0.05 for a, b in zip(boite, cible, strict=True)) for cible in cibles
        )

    return vue.filter(autorise)


def _boites_retirees_en_haut_de_page(
    boites: tuple[tuple[float, float, float, float], ...], hauteur_affichee: float
) -> tuple[tuple[float, float, float, float], ...]:
    """Boîtes device de la page affichée -> (x0, top, x1, bottom) pdfplumber."""
    return tuple(
        (x0, hauteur_affichee - y1, x1, hauteur_affichee - y0) for x0, y0, x1, y1 in boites
    )


def _objet_retire(objet: dict, interdits: tuple[tuple[float, float, float, float], ...]) -> bool:
    """Vrai si la boîte pdfplumber de l'objet correspond à un objet retiré."""
    valeurs = (objet.get("x0"), objet.get("top"), objet.get("x1"), objet.get("bottom"))
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in valeurs):
        return False
    boite = (float(valeurs[0]), float(valeurs[1]), float(valeurs[2]), float(valeurs[3]))
    return any(all(abs(a - b) <= 0.05 for a, b in zip(boite, interdite, strict=True)) for interdite in interdits)


def page_de_lecture(page: object) -> tuple[object, RotationAffichage | None]:
    """Normalise uniquement une rotation homogène, vérifiée caractère par caractère.

    Les pages déjà lisibles, sans texte ou à orientations mixtes ne sont pas
    tournées. La lecture est bornée à la vue effective CropBox/MediaBox,
    y compris si une rotation ne peut pas être normalisée. L'API ``Page`` de pdfplumber est utilisée avec une copie privée
    de PDFPage, sans réécriture du fichier ou mutation de la page originale.
    """
    # pdfminer ne suit pas tous les clips graphiques : les objets dont la
    # visibilité n'est pas démontrée sont retirés de la vue publiée. La garde
    # est appelée sur la page affichée (une faute structurelle la refuse) ; le
    # retrait lui-même a lieu après la normalisation, sinon les glyphes retirés
    # empêcheraient d'apparier la vue normalisée avec la vue affichée.
    from seamtech_search.pdf_vue_graphique import objets_a_supprimer, verifier_vue_graphique

    a_supprimer = verifier_vue_graphique(page)
    boites = objets_a_supprimer(a_supprimer)
    if boites:
        LOGGER.warning(
            "Page PDF : %d objet(s) retiré(s) de la publication (visibilité non démontrée).",
            len(a_supprimer),
        )
    hauteur = float(getattr(page, "height", 0.0) or 0.0)
    angle = getattr(page, "rotation", 0)
    caracteres = [c for c in page.chars if c.get("text", "").strip()]
    mixte, compatible, exploitable = orientations_visibles(page)
    if mixte:
        LOGGER.warning("Page PDF à orientations mixtes : extraction potentiellement incorrecte, contrôle nécessaire.")
    if angle not in (90, 180, 270) or not compatible or not exploitable:
        vue = _page_sans_objets_non_demontres(page, boites, None, hauteur)
        return _rogner_vue(page, vue, None), None

    from pdfplumber.page import Page

    objet = copy.copy(page.page_obj)
    objet.attrs = page.page_obj.attrs.copy()
    objet.attrs["Rotate"] = 0
    objet.rotate = 0
    normalisee = Page(page.pdf, objet, page.page_number, page.initial_doctop)
    source = [c for c in normalisee.chars if c.get("text", "").strip()]
    if len(source) != len(caracteres):
        normalisee.close()
        vue = _page_sans_objets_non_demontres(page, boites, None, hauteur)
        return _rogner_vue(page, vue, None), None
    premier, affiche = source[0], caracteres[0]
    if angle == 90:
        dx, dy = affiche["x0"] + premier["bottom"], affiche["top"] - premier["x0"]
    elif angle == 180:
        dx, dy = affiche["x0"] + premier["x1"], affiche["top"] + premier["bottom"]
    else:
        dx, dy = affiche["x0"] - premier["top"], affiche["top"] + premier["x1"]
    rotation = RotationAffichage(angle, dx, dy)
    for brut, visible in zip(source, caracteres, strict=True):
        if brut.get("text") != visible.get("text"):
            normalisee.close()
            vue = _page_sans_objets_non_demontres(page, boites, None, hauteur)
            return _rogner_vue(page, vue, None), None
        estimee = rotation.boite(brut["x0"], brut["x1"], brut["top"], brut["bottom"])
        reelle = (visible["x0"], visible["x1"], visible["top"], visible["bottom"])
        if any(not math.isfinite(v) for v in (*estimee, *reelle)):
            normalisee.close()
            vue = _page_sans_objets_non_demontres(page, boites, None, hauteur)
            return _rogner_vue(page, vue, None), None
        if any(abs(a - b) > 0.01 for a, b in zip(estimee, reelle, strict=True)):
            normalisee.close()
            vue = _page_sans_objets_non_demontres(page, boites, None, hauteur)
            return _rogner_vue(page, vue, None), None
    vue = _page_sans_objets_non_demontres(normalisee, boites, rotation, hauteur)
    return _rogner_vue(page, vue, rotation), rotation
