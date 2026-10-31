"""Garde conservatrice des clips et formes ignorés par pdfminer.

Ce module ne prétend pas reproduire le rendu PDF : il refuse les cas dont la
visibilité ne peut pas être établie avec les informations du lecteur. Les clips
rectangulaires ne sont acceptés que si les objets du lecteur restent entièrement
à l'intérieur ; les autres clips et les formes incertaines sont refusés.
"""

from __future__ import annotations

import math
from typing import Any

from pdfminer.layout import LTChar, LTCurve, LTFigure, LTImage
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


def _objet_visible_sous_clip(objet: Any, vue: tuple[float, float, float, float]) -> bool:
    """Contenu extractible : entièrement visible. Géométrie : au moins visible.

    Un glyphe ou une image partiellement coupés sont refusés : le lecteur
    publierait la totalité de leur contenu, y compris la partie masquée. Un
    tracé vectoriel partiellement coupé est accepté dès qu'une portion est
    visible, faute de quoi le moindre clip décoratif rendrait la page
    indisponible ; un tracé entièrement hors clip reste refusé car il ne
    produirait que des arêtes fictives.
    """
    if isinstance(objet, (LTChar, LTImage)):
        return _contenu_dans(objet.bbox, vue)
    if isinstance(objet, LTCurve):
        return _au_moins_visible(objet.bbox, vue)
    return _contenu_dans(objet.bbox, vue)


_CLIP_INDETERMINE = object()


class _CollecteurClips(PDFPageAggregatorWithMarkedContent):
    """Contrôle les objets émis dans le même repère que le lecteur."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.clip_courant: Any = None

    def _verifier_objets(self, objets: Any) -> None:
        if not objets or self.clip_courant is None:
            return
        if self.clip_courant is _CLIP_INDETERMINE:
            raise VueGraphiqueNonVerifiee("Clipping non rectangulaire")
        if any(not _objet_visible_sous_clip(objet, self.clip_courant) for objet in objets):
            raise VueGraphiqueNonVerifiee("Objet hors clipping rectangulaire")

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
        if self._chemin_incertain or [segment[0] for segment in chemin] != ["m", "l", "l", "l", "h"]:
            self._clip = _CLIP_INDETERMINE
        else:
            points = [apply_matrix_pt(self.ctm, (segment[1], segment[2])) for segment in chemin[:4]]
            xs = sorted({x for x, _y in points})
            ys = sorted({y for _x, y in points})
            aretes = zip(points, points[1:] + points[:1])
            rectangle = (
                len(xs) == 2
                and len(ys) == 2
                and set(points) == {(x, y) for x in xs for y in ys}
                and all((a[0] == b[0]) != (a[1] == b[1]) for a, b in aretes)
            )
            if not rectangle:
                self._clip = _CLIP_INDETERMINE
            elif self._clip is not _CLIP_INDETERMINE:
                vue = _rectangle((xs[0], ys[0], xs[1], ys[1]))
                if self._clip is not None:
                    vue = (
                        max(vue[0], self._clip[0]),
                        max(vue[1], self._clip[1]),
                        min(vue[2], self._clip[2]),
                        min(vue[3], self._clip[3]),
                    )
                self._clip = vue
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
    from pdfminer.layout import LTAnno, LTFigure, LTImage, LTItem

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
