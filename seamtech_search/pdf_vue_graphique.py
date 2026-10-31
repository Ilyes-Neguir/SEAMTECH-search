"""Garde conservatrice des clips et formes ignorés par pdfminer.

Ce module ne prétend pas reproduire le rendu PDF : il refuse les cas dont la
visibilité ne peut pas être établie avec les informations du lecteur. Les clips
rectangulaires ne sont acceptés que si les objets du lecteur restent entièrement
à l'intérieur ; les autres clips et les formes incertaines sont refusés.
"""

from __future__ import annotations

import math
from typing import Any

from pdfminer.layout import LTCurve, LTFigure, LTImage
from pdfminer.pdffont import PDFType3Font
from pdfminer.pdfinterp import PDFPageInterpreter
from pdfminer.utils import apply_matrix_pt
from pdfplumber.page import PDFPageAggregatorWithMarkedContent


class VueGraphiqueNonVerifiee(ValueError):
    """La visibilité des objets PDF ne peut pas être établie prudemment."""


_MAX_OPERATEURS = 1_000_000
_MAX_PROFONDEUR = 20
_MAX_OPERANDES = 256
_MAX_ETATS = 256


def _scanner_flux(
    flux: Any, ressources: Any, ancetres: frozenset[tuple[str, int]], profondeur: int, budget: list[int]
) -> None:
    """Suit les états q/Q et les Form XObjects effectivement invoqués."""
    from pdfminer.pdfinterp import PDFContentParser
    from pdfminer.pdftypes import dict_value, resolve1, stream_value
    from pdfminer.psparser import PSEOF, PSKeyword, keyword_name, literal_name

    if profondeur > _MAX_PROFONDEUR:
        raise VueGraphiqueNonVerifiee("Imbrication PDF excessive")
    try:
        analyseur = PDFContentParser(flux)
    except PSEOF:
        return
    actif = False
    pile: list[bool] = []
    operandes: list[Any] = []
    while True:
        try:
            _, objet = analyseur.nextobject()
        except PSEOF:
            break
        budget[0] += 1
        if budget[0] > _MAX_OPERATEURS:
            raise VueGraphiqueNonVerifiee("Limite d'inspection graphique atteinte")
        if not isinstance(objet, PSKeyword):
            operandes.append(objet)
            if len(operandes) > _MAX_OPERANDES:
                raise VueGraphiqueNonVerifiee("Opérandes graphiques excessifs")
            continue
        operateur = keyword_name(objet)
        if operateur == "q":
            pile.append(actif)
            if len(pile) > _MAX_ETATS:
                raise VueGraphiqueNonVerifiee("États graphiques excessifs")
        elif operateur == "Q":
            if not pile:
                raise VueGraphiqueNonVerifiee("État graphique invalide")
            actif = pile.pop()
        elif operateur in {"W", "W*"}:
            actif = True
        elif operateur == "Tr":
            if not operandes or isinstance(operandes[-1], bool) or not isinstance(operandes[-1], (int, float)):
                raise VueGraphiqueNonVerifiee("Mode de texte invalide")
            if operandes[-1] >= 4 or operandes[-1] < 0:
                raise VueGraphiqueNonVerifiee("Clipping par texte non vérifié")
        elif operateur == "Do":
            if not operandes:
                raise VueGraphiqueNonVerifiee("XObject invalide")
            nom = literal_name(operandes[-1])
            objets = dict_value(dict_value(ressources).get("XObject", {}))
            if nom not in objets:
                raise VueGraphiqueNonVerifiee("XObject non résolu")
            xobject = stream_value(objets[nom])
            sous_type = literal_name(resolve1(xobject.get("Subtype")))
            if sous_type == "Form":
                identite = ("objet", xobject.objid) if xobject.objid is not None else ("memoire", id(xobject))
                if identite in ancetres:
                    raise VueGraphiqueNonVerifiee("XObject cyclique")
                internes = resolve1(xobject.get("Resources")) or ressources
                _scanner_flux([xobject], internes, ancetres | {identite}, profondeur + 1, budget)
            elif sous_type != "Image":
                raise VueGraphiqueNonVerifiee("XObject non pris en charge")
        operandes.clear()
    if pile or operandes:
        raise VueGraphiqueNonVerifiee("Flux graphique incomplet")


def _rectangle(rectangle: Any) -> tuple[float, float, float, float]:
    if not isinstance(rectangle, (tuple, list)) or len(rectangle) != 4:
        raise VueGraphiqueNonVerifiee("Bornes graphiques invalides")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in rectangle):
        raise VueGraphiqueNonVerifiee("Bornes graphiques non finies")
    x0, y0, x1, y1 = rectangle
    if x0 > x1 or y0 > y1:
        raise VueGraphiqueNonVerifiee("Bornes graphiques inversées")
    return x0, y0, x1, y1


def _contenu_dans(rectangle: Any, vue: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = _rectangle(rectangle)
    return vue[0] <= x0 <= x1 <= vue[2] and vue[1] <= y0 <= y1 <= vue[3]


def _au_moins_visible(rectangle: Any, vue: tuple[float, float, float, float]) -> bool:
    """Vrai si le rectangle et la vue se recouvrent sur une surface non vide.

    Les traits horizontaux ou verticaux ont une BBox d'épaisseur nulle : le
    recouvrement est alors jugé sur l'autre axe, avec tolérance nulle sur les
    bordures exactes du clip.
    """
    x0, y0, x1, y1 = _rectangle(rectangle)
    recouvrement_x = min(x1, vue[2]) - max(x0, vue[0])
    recouvrement_y = min(y1, vue[3]) - max(y0, vue[1])
    if recouvrement_x < 0 or recouvrement_y < 0:
        return False
    return recouvrement_x > 0 or recouvrement_y > 0


def _objet_visible_sous_clip(objet: Any, clip: _Clip) -> bool:
    """Contenu extractible : entièrement visible. Géométrie : au moins visible.

    Un glyphe ou une image partiellement coupés sont refusés : le lecteur
    publierait la totalité de leur contenu, y compris la partie masquée. Un
    tracé vectoriel partiellement coupé est accepté dès qu'une portion est
    visible, faute de quoi le moindre clip décoratif rendrait la page
    indisponible ; un tracé entièrement hors clip reste refusé car il ne
    produirait que des arêtes fictives.
    """
    if isinstance(objet, LTCurve):
        return _visible_dans_clip(objet.bbox, clip)
    return _contenu_dans_clip(objet.bbox, clip)


_CLIP_INDETERMINE = object()
_MAX_SOMMETS = 4096
_ECHANTILLONS_COURBE = 8


def _bezier_cubique(
    p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float], t: float
) -> tuple[float, float]:
    u = 1 - t
    return (
        u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
        u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
    )


def _bezier_quadratique(
    p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], t: float
) -> tuple[float, float]:
    u = 1 - t
    return (
        u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
        u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
    )


def _distance_point_droite(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs(dx * (a[1] - p[1]) - dy * (a[0] - p[0])) / math.hypot(dx, dy)


def _ecart_courbe(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    operateur: str,
    polygone: list[tuple[float, float]],
) -> float:
    """Écart maximal prouvé entre la courbe et le polygone échantillonné.

    Chaque sous-intervalle est redécoupé par de Casteljau : la sous-courbe
    reste dans l'enveloppe convexe de ses points de contrôle, donc son écart à
    la corde (l'arête du polygone) est borné par l'écart de ces points
    (Nairn, Peters, Lutterkort). La borne porte sur l'arête réellement tracée,
    pas sur la corde entière : elle décroît comme le carré du nombre
    d'échantillons au lieu de rester aussi grossière.
    """
    if operateur == "v":
        controle = [p0, p2, p3]
        debut = len(polygone)
    elif operateur == "y":
        controle = [p0, p1, p3]
        debut = len(polygone)
    else:
        controle = [p0, p1, p2, p3]
        debut = len(polygone)
    if debut < 1:
        return math.inf
    ecart = 0.0
    for i in range(_ECHANTILLONS_COURBE):
        t0 = i / _ECHANTILLONS_COURBE
        t1 = (i + 1) / _ECHANTILLONS_COURBE
        sous = _sous_controle(controle, t0, t1)
        a = _evaluer_bezier(controle, t0)
        b = _evaluer_bezier(controle, t1)
        for point in sous:
            ecart = max(ecart, _distance_point_droite(point, a, b))
    return ecart * (0.5 if len(controle) == 3 else 0.75)


def _evaluer_bezier(controle: list[tuple[float, float]], t: float) -> tuple[float, float]:
    if len(controle) == 3:
        return _bezier_quadratique(controle[0], controle[1], controle[2], t)
    return _bezier_cubique(controle[0], controle[1], controle[2], controle[3], t)


def _sous_controle(controle: list[tuple[float, float]], t0: float, t1: float) -> list[tuple[float, float]]:
    """Points de contrôle de la sous-courbe sur [t0, t1] (de Casteljau)."""
    if t1 <= t0:
        return list(controle)

    def derivee(t: float) -> tuple[float, float]:
        if len(controle) == 3:
            p0, p1, p2 = controle
            return (
                2 * ((1 - t) * (p1[0] - p0[0]) + t * (p2[0] - p1[0])),
                2 * ((1 - t) * (p1[1] - p0[1]) + t * (p2[1] - p1[1])),
            )
        p0, p1, p2, p3 = controle
        return (
            3 * ((1 - t) ** 2 * (p1[0] - p0[0]) + 2 * (1 - t) * t * (p2[0] - p1[0]) + t * t * (p3[0] - p2[0])),
            3 * ((1 - t) ** 2 * (p1[1] - p0[1]) + 2 * (1 - t) * t * (p2[1] - p1[1]) + t * t * (p3[1] - p2[1])),
        )

    debut = _evaluer_bezier(controle, t0)
    fin = _evaluer_bezier(controle, t1)
    d0 = derivee(t0)
    d1 = derivee(t1)
    largeur = (t1 - t0) / (len(controle) - 1)
    if len(controle) == 3:
        return [
            debut,
            (debut[0] + largeur * d0[0], debut[1] + largeur * d0[1]),
            (fin[0] - largeur * d1[0], fin[1] - largeur * d1[1]),
            fin,
        ]
    return [
        debut,
        (debut[0] + largeur * d0[0], debut[1] + largeur * d0[1]),
        (fin[0] - largeur * d1[0], fin[1] - largeur * d1[1]),
        fin,
    ]


class _Clip:
    """Région de clipping modélisée : rectangle axe-aligné ou polygone.

    ``marge`` est l'écart maximal, prouvé, entre le polygone échantillonné et la
    courbe réelle (borne de Nairn/Peters/Lutterkort). Toute boîte doit rester
    dans le polygone après avoir été élargie de cette marge : la conclusion
    reste donc valable pour la courbe elle-même, pas seulement pour l'échantillon.
    """

    __slots__ = ("points", "marge", "boite", "rectangulaire")

    def __init__(self, points: list[tuple[float, float]], marge: float = 0.0, *, rectangulaire: bool = False) -> None:
        if len(points) < 3 or len(points) > _MAX_SOMMETS:
            raise VueGraphiqueNonVerifiee("Clipping non modélisable")
        if any(not math.isfinite(x) or not math.isfinite(y) for x, y in points):
            raise VueGraphiqueNonVerifiee("Clipping non fini")
        self.points = tuple(points)
        self.marge = marge if math.isfinite(marge) and marge > 0 else 0.0
        self.rectangulaire = rectangulaire
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        self.boite = (min(xs), min(ys), max(xs), max(ys))


def _rectangle_en_clip(boite: tuple[float, float, float, float]) -> _Clip:
    x0, y0, x1, y1 = boite
    return _Clip([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], 0.0, rectangulaire=True)


def _point_dans_polygone(point: tuple[float, float], points: tuple[tuple[float, float], ...]) -> bool:
    x, y = point
    dedans = False
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
        if (ay > y) != (by > y):
            intersection = ax + (y - ay) * (bx - ax) / (by - ay)
            if x < intersection:
                dedans = not dedans
    return dedans


def _produit_vectoriel(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _segments_croisent(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]
) -> bool:
    d1 = _produit_vectoriel(c[0], c[1], d[0], d[1], a[0], a[1])
    d2 = _produit_vectoriel(c[0], c[1], d[0], d[1], b[0], b[1])
    d3 = _produit_vectoriel(a[0], a[1], b[0], b[1], c[0], c[1])
    d4 = _produit_vectoriel(a[0], a[1], b[0], b[1], d[0], d[1])
    # Un simple contact est traité comme un croisement : refuser plutôt que
    # publier une boîte qui ne fait qu'effleurer la frontière.
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    return any(
        d == 0 and _sur_segment(a, b, p)
        for d, p, (a, b) in ((d1, a, (c, d)), (d2, b, (c, d)), (d3, c, (a, b)), (d4, d, (a, b)))
    )


def _sur_segment(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> bool:
    if min(a[0], b[0]) > p[0] or p[0] > max(a[0], b[0]):
        return False
    return min(a[1], b[1]) <= p[1] <= max(a[1], b[1])


def _boites_croisent(boite: tuple[float, float, float, float], clip: _Clip) -> bool:
    x0, y0, x1, y1 = boite
    cx0, cy0, cx1, cy1 = clip.boite
    return not (x1 < cx0 or cx1 < x0 or y1 < cy0 or cy1 < y0)


def _contenu_dans_clip(boite: tuple[float, float, float, float], clip: _Clip) -> bool:
    x0, y0, x1, y1 = _rectangle(boite)
    marge = clip.marge
    coins = [(x0 - marge, y0 - marge), (x1 + marge, y0 - marge), (x1 + marge, y1 + marge), (x0 - marge, y1 + marge)]
    if clip.rectangulaire:
        cx0, cy0, cx1, cy1 = clip.boite
        return all(cx0 <= x <= cx1 and cy0 <= y <= cy1 for x, y in coins)
    if not all(_point_dans_polygone(coin, clip.points) for coin in coins):
        return False
    aretes_boite = (coins[0], coins[1]), (coins[1], coins[2]), (coins[2], coins[3]), (coins[3], coins[0])
    for sommet, suivant in zip(clip.points, clip.points[1:] + clip.points[:1]):
        for a, b in aretes_boite:
            if _segments_croisent(sommet, suivant, a, b):
                return False
    return True


def _visible_dans_clip(boite: tuple[float, float, float, float], clip: _Clip) -> bool:
    """Vrai si une portion non vide de la boîte tombe dans la région visible."""
    x0, y0, x1, y1 = _rectangle(boite)
    if not _boites_croisent((x0, y0, x1, y1), clip):
        return False
    if clip.rectangulaire:
        return _au_moins_visible((x0, y0, x1, y1), clip.boite)
    coins = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if any(_point_dans_polygone(coin, clip.points) for coin in coins):
        return True
    if any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in clip.points):
        return True
    aretes_boite = (coins[0], coins[1]), (coins[1], coins[2]), (coins[2], coins[3]), (coins[3], coins[0])
    for sommet, suivant in zip(clip.points, clip.points[1:] + clip.points[:1]):
        for a, b in aretes_boite:
            if _segments_croisent(sommet, suivant, a, b):
                return True
    return False


def _intersection_clips(parent: _Clip, enfant: _Clip) -> _Clip:
    if parent.rectangulaire and enfant.rectangulaire:
        boite = (
            max(parent.boite[0], enfant.boite[0]),
            max(parent.boite[1], enfant.boite[1]),
            min(parent.boite[2], enfant.boite[2]),
            min(parent.boite[3], enfant.boite[3]),
        )
        return _rectangle_en_clip(_rectangle(boite))
    if parent.rectangulaire or enfant.rectangulaire:
        rect = parent if parent.rectangulaire else enfant
        polygone = enfant if parent.rectangulaire else parent
        return _Clip(
            _rogner_polygone(polygone.points, rect.boite),
            polygone.marge,
        )
    # Deux régions non rectangulaires : l'intersection exacte de polygones
    # quelconques n'est pas démontrée ici — refus plutôt que fausse preuve.
    raise VueGraphiqueNonVerifiee("Clipping emboîté non modélisable")


def _rogner_polygone(
    points: tuple[tuple[float, float], ...], boite: tuple[float, float, float, float]
) -> list[tuple[float, float]]:
    """Sutherland–Hodgman : exact car la région de coupe (rectangle) est convexe."""
    x0, y0, x1, y1 = boite
    # Chaque demi-plan : fonction « dedans » puis fonction d'intersection avec
    # le bord correspondant (vertical x = borne, ou horizontal y = borne).
    if x1 < x0 or y1 < y0:
        raise VueGraphiqueNonVerifiee("Bornes graphiques inversées")

    def dedans_gauche(p: tuple[float, float]) -> bool:
        return p[0] >= x0

    def dedans_droite(p: tuple[float, float]) -> bool:
        return p[0] <= x1

    def dedans_bas(p: tuple[float, float]) -> bool:
        return p[1] >= y0

    def dedans_haut(p: tuple[float, float]) -> bool:
        return p[1] <= y1

    def coupe_verticale(a: tuple[float, float], b: tuple[float, float], borne: float) -> tuple[float, float]:
        if a[0] == b[0]:
            return (borne, a[1])
        t = (borne - a[0]) / (b[0] - a[0])
        return (borne, a[1] + t * (b[1] - a[1]))

    def coupe_horizontale(a: tuple[float, float], b: tuple[float, float], borne: float) -> tuple[float, float]:
        if a[1] == b[1]:
            return (a[0], borne)
        t = (borne - a[1]) / (b[1] - a[1])
        return (a[0] + t * (b[0] - a[0]), borne)

    demi_plans = (
        (dedans_gauche, lambda a, b: coupe_verticale(a, b, x0)),
        (dedans_droite, lambda a, b: coupe_verticale(a, b, x1)),
        (dedans_bas, lambda a, b: coupe_horizontale(a, b, y0)),
        (dedans_haut, lambda a, b: coupe_horizontale(a, b, y1)),
    )
    resultat = list(points)
    for dedans, coupe in demi_plans:
        if not resultat:
            break
        suivant = resultat[1:] + resultat[:1]
        sortie: list[tuple[float, float]] = []
        for courant, prochain in zip(resultat, suivant):
            if dedans(courant):
                sortie.append(courant)
                if not dedans(prochain):
                    sortie.append(coupe(courant, prochain))
            elif dedans(prochain):
                sortie.append(coupe(courant, prochain))
        resultat = sortie
    return resultat


class _CollecteurClips(PDFPageAggregatorWithMarkedContent):
    """Contrôle les objets émis dans le même repère que le lecteur."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.clip_courant: Any = None

    def _verifier_objets(self, objets: Any) -> None:
        if not objets or self.clip_courant is None:
            return
        if self.clip_courant is _CLIP_INDETERMINE:
            raise VueGraphiqueNonVerifiee("Clipping non modélisable")
        if any(not _objet_visible_sous_clip(objet, self.clip_courant) for objet in objets):
            raise VueGraphiqueNonVerifiee("Objet hors clipping")

    def render_char(self, *args: Any, **kwargs: Any) -> float:
        police = args[1] if len(args) > 1 else kwargs.get("font")
        if isinstance(police, PDFType3Font) and (self.clip_courant is not None or isinstance(self.cur_item, LTFigure)):
            raise VueGraphiqueNonVerifiee("Glyphe Type 3 sous clipping non vérifié")
        avance = super().render_char(*args, **kwargs)
        objet = self.cur_item._objs[-1] if self.cur_item._objs else None
        if self.clip_courant is not None and objet is not None:
            # Une avance nulle ou une boîte réduite à un point empêchent de situer
            # le glyphe : le clip ne peut plus rien prouver, on refuse.
            if not isinstance(avance, (int, float)) or avance <= 0:
                raise VueGraphiqueNonVerifiee("Avance de glyphe non vérifiable")
            x0, y0, x1, y1 = _rectangle(objet.bbox)
            if x0 == x1 and y0 == y1:
                raise VueGraphiqueNonVerifiee("Boîte de glyphe dégénérée")
        self._verifier_objets(self.cur_item._objs[-1:])
        return avance

    def render_image(self, *args: Any, **kwargs: Any) -> None:
        super().render_image(*args, **kwargs)
        self._verifier_objets(self.cur_item._objs[-1:])

    def paint_path(self, *args: Any, **kwargs: Any) -> None:
        avant = len(self.cur_item._objs)
        super().paint_path(*args, **kwargs)
        self._verifier_objets(self.cur_item._objs[avant:])


class _InterpreteurClips(PDFPageInterpreter):
    """Suit une intersection de rectangles de clipping dans le repère affiché."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._clip_herite: Any = None

    def init_state(self, ctm: Any) -> None:
        super().init_state(ctm)
        self._clip = self._clip_herite
        self._clips_sauves: list[Any] = []
        self._chemin_incertain = False
        self.device.clip_courant = self._clip

    def subinterp(self) -> Any:
        enfant = super().subinterp()
        enfant._clip_herite = self._clip
        return enfant

    def do_q(self) -> None:
        super().do_q()
        self._clips_sauves.append(self._clip)

    def do_Q(self) -> None:
        avant = self.ctm
        super().do_Q()
        if self.curpath and self.ctm != avant:
            self._chemin_incertain = True
        if not self._clips_sauves:
            raise VueGraphiqueNonVerifiee("État graphique invalide")
        self._clip = self._clips_sauves.pop()
        self.device.clip_courant = self._clip

    def do_cm(self, a: Any, b: Any, c: Any, d: Any, e: Any, f: Any) -> None:
        if self.curpath:
            self._chemin_incertain = True
        super().do_cm(a, b, c, d, e, f)

    def do_m(self, x: Any, y: Any) -> None:
        if not self.curpath:
            self._chemin_incertain = False
        super().do_m(x, y)

    def do_re(self, x: Any, y: Any, w: Any, h: Any) -> None:
        if not self.curpath:
            self._chemin_incertain = False
        super().do_re(x, y, w, h)

    def _appliquer_clip(self) -> None:
        chemin = self.curpath
        if self._chemin_incertain:
            self._poser_indetermine()
            return
        operations = [segment[0] for segment in chemin]
        # Un tracé de clipping est fermé implicitement : « h » est facultatif.
        if (
            operations
            and operations.count("m") == 1
            and all(operation in {"m", "l", "h"} for operation in operations)
            and operations.count("l") in {2, 3}
        ):
            self._clip_rectangle(chemin)
            return
        self._clip_polygonal(chemin)

    def _clip_rectangle(self, chemin: list[Any]) -> None:
        points = [apply_matrix_pt(self.ctm, (segment[1], segment[2])) for segment in chemin if segment[0] in {"m", "l"}]
        xs = sorted({x for x, _y in points})
        ys = sorted({y for _x, y in points})
        aretes = zip(points, points[1:] + points[:1])
        rectangle = (
            len(points) == 4
            and len(xs) == 2
            and len(ys) == 2
            and set(points) == {(x, y) for x in xs for y in ys}
            and all((a[0] == b[0]) != (a[1] == b[1]) for a, b in aretes)
        )
        if rectangle:
            self._poser_clip(_rectangle_en_clip((xs[0], ys[0], xs[1], ys[1])))
        else:
            # Triangle ou quadrilatère quelconque : traité comme un polygone.
            self._poser_clip(_Clip([(round(x, 9), round(y, 9)) for x, y in points]))

    def _clip_polygonal(self, chemin: list[Any]) -> None:
        """Modélise un tracé fermé unique : polygone exact ou courbes bornées."""
        if not chemin or chemin[0][0] != "m":
            self._poser_indetermine()
            return
        if sum(1 for segment in chemin if segment[0] == "m") != 1:
            # Plusieurs sous-tracés (règle even-odd incluse) : non démontré.
            self._poser_indetermine()
            return
        polygone: list[tuple[float, float]] = []
        marge = 0.0
        origine = apply_matrix_pt(self.ctm, (chemin[0][1], chemin[0][2]))
        courant = origine
        polygone.append(origine)
        for segment in chemin[1:]:
            operateur = segment[0]
            if operateur == "l":
                point = apply_matrix_pt(self.ctm, (segment[1], segment[2]))
                polygone.append(point)
                courant = point
            elif operateur in {"c", "v", "y"}:
                if operateur == "c":
                    p1 = apply_matrix_pt(self.ctm, (segment[1], segment[2]))
                    p2 = apply_matrix_pt(self.ctm, (segment[3], segment[4]))
                elif operateur == "v":
                    p1 = courant
                    p2 = apply_matrix_pt(self.ctm, (segment[1], segment[2]))
                else:
                    p1 = apply_matrix_pt(self.ctm, (segment[1], segment[2]))
                    p2 = apply_matrix_pt(self.ctm, (segment[3], segment[4]))
                fin = apply_matrix_pt(self.ctm, (segment[-2], segment[-1]))
                marge = max(marge, _ecart_courbe(courant, p1, p2, fin, operateur, polygone))
                for i in range(1, _ECHANTILLONS_COURBE + 1):
                    t = i / _ECHANTILLONS_COURBE
                    if operateur == "v":
                        polygone.append(_bezier_quadratique(courant, p2, fin, t))
                    else:
                        polygone.append(_bezier_cubique(courant, p1, p2, fin, t))
                courant = fin
            elif operateur == "h":
                break
            else:
                self._poser_indetermine()
                return
        if len(polygone) < 3:
            self._poser_indetermine()
            return
        try:
            self._poser_clip(_Clip(polygone, marge))
        except VueGraphiqueNonVerifiee:
            self._poser_indetermine()

    def _poser_indetermine(self) -> None:
        self._clip = _CLIP_INDETERMINE
        self.device.clip_courant = self._clip

    def _poser_clip(self, clip: _Clip) -> None:
        if self._clip is _CLIP_INDETERMINE or self._clip is None:
            self._clip = clip
        else:
            try:
                self._clip = _intersection_clips(self._clip, clip)
            except VueGraphiqueNonVerifiee:
                self._clip = _CLIP_INDETERMINE
        self.device.clip_courant = self._clip

    def do_W(self) -> None:
        self._appliquer_clip()

    def do_W_a(self) -> None:
        self._appliquer_clip()

    def do_Do(self, objet: Any) -> None:
        try:
            super().do_Do(objet)
        finally:
            self.device.clip_courant = self._clip


def _verifier_clips(page: Any) -> None:
    collecteur = _CollecteurClips(page.pdf.rsrcmgr, pageno=page.page_number, laparams=page.pdf.laparams)
    interpreteur = _InterpreteurClips(page.pdf.rsrcmgr, collecteur)
    interpreteur.process_page(page.page_obj)


def _verifier_formes(objet: Any, vues: tuple[tuple[float, float, float, float], ...] = ()) -> None:
    """Vérifie les BBox des formes, y compris leurs descendants imbriqués."""
    from pdfminer.layout import LTAnno, LTFigure, LTItem

    enfants = list(objet) if hasattr(objet, "__iter__") else []
    if isinstance(objet, LTFigure):
        # pdfminer enveloppe chaque image dans une figure dont la BBox est
        # exactement celle de l'image elle-même. Sa rotation propre n'indique
        # pas un clip de forme ; les vues parentes restent toutefois vérifiées.
        if len(enfants) == 1 and isinstance(enfants[0], LTImage):
            _verifier_formes(enfants[0], vues)
            return
        matrice = objet.matrix
        if len(matrice) < 6 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in matrice
        ):
            raise VueGraphiqueNonVerifiee("Matrice de forme invalide")
        a, b, c, d = matrice[:4]
        alignee = (b == 0 and c == 0 and a * d != 0) or (a == 0 and d == 0 and b * c != 0)
        if not alignee:
            raise VueGraphiqueNonVerifiee("Forme non orthogonale")
        vues = (*vues, _rectangle(objet.bbox))
    if not enfants and isinstance(objet, LTItem) and not isinstance(objet, LTAnno) and hasattr(objet, "bbox"):
        if any(not _contenu_dans(objet.bbox, vue) for vue in vues):
            raise VueGraphiqueNonVerifiee("Objet hors BBox de forme")
    for enfant in enfants:
        _verifier_formes(enfant, vues)


def verifier_vue_graphique(page: Any) -> None:
    """Refuse les cas non démontrés ; ne révèle aucun contenu dans l'erreur."""
    try:
        _scanner_flux(page.page_obj.contents, page.page_obj.resources or {}, frozenset(), 0, [0])
        _verifier_clips(page)
        _verifier_formes(page.layout)
    except VueGraphiqueNonVerifiee:
        raise
    except Exception as erreur:
        raise VueGraphiqueNonVerifiee("Inspection graphique PDF impossible") from erreur
