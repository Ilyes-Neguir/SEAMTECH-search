"""Lecture du texte d'une page PDF quel que soit son sens de lecture.

Pourquoi ce module existe
-------------------------
`pdfplumber.Page.extract_text()` regroupe les caractères par **ordonnée** dans
le repère d'affichage de la page. Tant que le texte est horizontal, c'est
exact. Dès qu'une page est pivotée, ce regroupement ne décrit plus l'ordre de
lecture :

* à 90° et 270°, les caractères d'une même ligne de texte n'ont plus la même
  ordonnée mais la même **abscisse** : le texte ressort un mot (voire un
  caractère) par ligne, entrelacé ;
* à 180°, ils ont bien une ordonnée commune mais l'ordre horizontal est
  inversé : le texte ressort à l'envers (« ELIOV » pour « VOILE »).

Or une fiche technique scannée sur le poste client arrive régulièrement
pivotée (scan paysage, photo de téléphone, export d'un logiciel de DAO). Le
texte alors extrait n'est plus cherchable, les expressions régulières de
champs ne reconnaissent plus « Référence : … », et la recherche ne retrouve
plus le document par son contenu.

Ce que fait ce module
---------------------
Il reconstruit les lignes **le long de la direction réelle des glyphes**,
mesurée sur la matrice de chaque caractère telle que la rapporte pdfminer
(donc après application du ``/Rotate`` de la page) :

* direction = ``(a, -b)`` de la matrice, exprimée dans le repère d'affichage
  (ordonnées vers le bas), quantifiée au quart de tour le plus proche ;
* l'orientation **dominante** est celle qui porte au moins 60 % des
  caractères ; en dessous, on considère la page comme horizontale et on ne
  touche à rien (une page avec un tampon vertical minoritaire reste lue dans
  son sens principal) ;
* les caractères sont projetés dans un repère « le long du texte » (u) et
  « en travers du texte » (v), regroupés en lignes par v, triés par u, puis
  assemblés avec un espace là où l'écart dépasse un quart de cadratin.

Conséquences volontaires :

* une page horizontale (cas de très loin majoritaire) suit exactement le
  chemin historique : ce module n'est appelé que si la page est pivotée ou
  contient des glyphes non verticaux, et il rend alors le même texte ;
* aucune donnée n'est inventée : si un caractère n'a pas de matrice
  exploitable, sa position dans le repère d'affichage est utilisée ;
* les tableaux d'une page pivotée ne sont pas détectés comme tableaux
  (pdfplumber s'appuie sur des segments droites/rectangles qui, eux, restent
  dans le repère d'affichage). Leur **contenu** reste extrait, dans l'ordre de
  lecture, via les caractères — c'est documenté dans ``docs/PDF_ORIENTATION.md``
  et vérifié par les tests.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable

#: Orientations reconnues (quarts de tour, dans le repère d'affichage).
ORIENTATIONS = (0, 90, 180, 270)

#: Part des caractères qu'une orientation doit porter pour être « dominante ».
PART_DOMINANTE = 0.60

#: Tolérance verticale de regroupement, en cadratins (bornée en points).
TOLERANCE_LIGNE_CADRATINS = 0.40
TOLERANCE_LIGNE_MIN_PT = 1.5

#: Écart horizontal, en cadratins, au-delà duquel on insère une espace.
ECART_ESPACE_CADRATINS = 0.25


def _matrice(caractere: Any) -> tuple[float, ...] | None:
    """Matrice du caractère, si pdfminer en fournit une exploitable."""
    matrice = caractere.get("matrix") if hasattr(caractere, "get") else None
    if not matrice or len(matrice) < 6:
        return None
    try:
        return tuple(float(valeur) for valeur in matrice[:6])
    except (TypeError, ValueError):
        return None


def _direction_affichage(caractere: Any) -> tuple[float, float]:
    """Direction d'avance du texte, dans le repère d'affichage (y vers le bas).

    La matrice pdfminer est exprimée dans le repère PDF (y vers le haut) : on
    inverse donc l'ordonnée pour se placer dans le repère de pdfplumber, celui
    dans lequel ``top``/``bottom`` sont donnés.
    """
    matrice = _matrice(caractere)
    if matrice is None:
        return (1.0, 0.0)
    a, b = matrice[0], matrice[1]
    longueur = math.hypot(a, b)
    if longueur == 0:
        return (1.0, 0.0)
    return (a / longueur, -b / longueur)


def _quart_de_tour(direction: tuple[float, float]) -> int:
    """Quantifie une direction au quart de tour le plus proche (0/90/180/270)."""
    angle = math.degrees(math.atan2(direction[1], direction[0]))
    return int(round(angle / 90.0) * 90) % 360


def orientations_par_caractere(caracteres: Iterable[Any]) -> Counter:
    """Nombre de caractères par orientation quantifiée (poids = longueur du texte)."""
    compte: Counter = Counter()
    for caractere in caracteres:
        texte = (caractere.get("text") if hasattr(caractere, "get") else None) or ""
        compte[_quart_de_tour(_direction_affichage(caractere))] += len(texte) or 1
    return compte


def orientation_dominante(caracteres: Iterable[Any]) -> int:
    """Orientation de lecture de la page : 0, 90, 180 ou 270.

    0 signifie « horizontale » — y compris pour une page dont les glyphes
    verticaux sont minoritaires (tampon, mention marginale) : on ne réécrit
    pas l'ordre de lecture d'une page lisible.
    """
    compte = orientations_par_caractere(caracteres)
    if not compte:
        return 0
    total = sum(compte.values())
    if total <= 0:
        return 0
    orientation, poids = compte.most_common(1)[0]
    if orientation not in ORIENTATIONS:
        return 0
    return orientation if poids / total >= PART_DOMINANTE else 0


def page_a_orienter(page: Any) -> bool:
    """Vrai si la page exige une reconstruction dans l'ordre de lecture.

    Le critère est l'orientation DOMINANTE des glyphes telle que la voit
    l'extracteur, pas le ``/Rotate`` déclaré : sur un scan paysage correctement
    étiqueté, pdfminer applique déjà le ``/Rotate`` et les glyphes ressortent
    verticaux — la page suit alors le chemin historique, tableaux compris. À
    l'inverse, une page dont le texte est couché dans le repère d'affichage
    (notre cas bloquant) est reconstruite.
    """
    return orientation_dominante(page.chars) != 0


def _origine_affichage(caractere: Any, hauteur_page: float) -> tuple[float, float]:
    """Origine du glyphe (x, y) dans le repère d'affichage."""
    matrice = _matrice(caractere)
    if matrice is None:
        return (float(caractere.get("x0", 0.0)), float(caractere.get("top", 0.0)))
    return (matrice[4], hauteur_page - matrice[5])


def _coordonnees_texte(
    caractere: Any, orientation: int, hauteur_page: float
) -> tuple[float, float, float]:
    """(u0, u1, v) : étendue le long du texte et position en travers du texte.

    * ``u0``/``u1`` encadrent le glyphe le long de la direction de lecture ;
    * ``v`` est la ligne (constante pour tous les glyphes d'une même ligne).
    """
    x0 = float(caractere.get("x0", 0.0))
    x1 = float(caractere.get("x1", x0))
    haut = float(caractere.get("top", 0.0))
    bas = float(caractere.get("bottom", haut))
    origine_x, origine_y = _origine_affichage(caractere, hauteur_page)
    if orientation == 90:  # texte qui descend : la lecture suit « top -> bottom »
        return (haut, bas, -origine_x)
    if orientation == 180:  # texte à l'envers : la lecture suit « droite -> gauche »
        return (-x1, -x0, -origine_y)
    if orientation == 270:  # texte qui monte
        return (-bas, -haut, origine_x)
    return (x0, x1, origine_y)


def _lignes_pour_orientation(
    caracteres: list[Any], hauteur_page: float, orientation: int
) -> list[tuple[float, float, float, str]]:
    """Lignes reconstruites avec leurs coordonnées : ``(v, u0, u1, texte)``.

    ``v`` est la position en travers du texte (ordonnée dans le repère de
    lecture), ``u0``/``u1`` l'étendue le long du texte. Les lignes sont
    rendues dans l'ordre de lecture (``v`` croissant), les caractères dans
    l'ordre de lecture (``u`` croissant). Ces coordonnées sont ce qui permet
    de vérifier qu'un tableau pivoté reste lu de gauche à droite, ligne par
    ligne, et non dans un ordre quelconque.
    """
    if not caracteres:
        return []
    glyphes: list[tuple[float, float, float, str, float]] = []
    for caractere in caracteres:
        texte = (caractere.get("text") if hasattr(caractere, "get") else None) or ""
        try:
            taille = float(caractere.get("size") or 0.0)
        except (TypeError, ValueError):
            taille = 0.0
        if taille <= 0:
            taille = max(4.0, float(caractere.get("bottom", 0.0)) - float(caractere.get("top", 0.0)) or 10.0)
        u0, u1, v = _coordonnees_texte(caractere, orientation, hauteur_page)
        glyphes.append((v, u0, u1, texte, taille))
    # Coordonnées positives : sur une page à 180°/270°, « le long du texte »
    # est un axe miroir (u négatif). On translate l'axe entier plutôt que
    # chaque ligne, pour que l'ordre relatif entre lignes reste comparable.
    decalage_u = min((glyphe[1] for glyphe in glyphes), default=0.0)
    if decalage_u:
        glyphes = [(v, u0 - decalage_u, u1 - decalage_u, texte, taille) for v, u0, u1, texte, taille in glyphes]
    glyphes.sort(key=lambda glyphe: (glyphe[0], glyphe[1]))

    groupes: list[list[tuple[float, float, float, str, float]]] = []
    courant: list[tuple[float, float, float, str, float]] = []
    reference: float | None = None
    for glyphe in glyphes:
        tolerance = max(TOLERANCE_LIGNE_MIN_PT, TOLERANCE_LIGNE_CADRATINS * glyphe[4])
        if reference is None or abs(glyphe[0] - reference) <= tolerance:
            courant.append(glyphe)
            # Moyenne glissante DANS la ligne : sur une page de texte serré, les
            # ordonnées varient de quelques dixièmes de point d'un glyphe à
            # l'autre (accent, jambage) et une comparaison au seul premier
            # glyphe finirait par couper la ligne en deux.
            reference = glyphe[0] if reference is None else (reference * 0.5 + glyphe[0] * 0.5)
        else:
            groupes.append(courant)
            courant = [glyphe]
            # Nouvelle ligne : la référence repart de SON premier glyphe. La
            # conserver ferait dériver la moyenne vers la ligne précédente et
            # détacherait le deuxième caractère de chaque ligne.
            reference = glyphe[0]
    if courant:
        groupes.append(courant)

    lignes: list[tuple[float, float, float, str]] = []
    for groupe in groupes:
        groupe.sort(key=lambda glyphe: glyphe[1])
        morceau = ""
        fin_precedente: float | None = None
        debut_ligne = groupe[0][1]
        fin_ligne = groupe[0][2]
        for v, u0, u1, texte, taille in groupe:
            if (
                fin_precedente is not None
                and u0 - fin_precedente > ECART_ESPACE_CADRATINS * taille
                and morceau
                and not morceau.endswith(" ")
                and texte != " "
            ):
                morceau += " "
            morceau += texte
            fin_precedente = u1 if fin_precedente is None else max(u1, fin_precedente)
            fin_ligne = max(fin_ligne, u1)
        ligne = morceau.rstrip()
        if ligne.strip():
            lignes.append((groupe[0][0], debut_ligne, fin_ligne, ligne))
    lignes.sort(key=lambda ligne: ligne[0])
    return lignes


def lignes_orientees(caracteres: Iterable[Any], hauteur_page: float) -> list[str]:
    """Lignes de texte dans l'ordre de lecture, quelle que soit l'orientation."""
    return [ligne[3] for ligne in lignes_avec_coordonnees(caracteres, hauteur_page)]


def texte_page(caracteres: Iterable[Any], hauteur_page: float, max_chars: int | None = None) -> str:
    """Texte d'une page reconstruit dans l'ordre de lecture (borné si demandé)."""
    texte = "\n".join(lignes_orientees(caracteres, hauteur_page))
    if max_chars is not None:
        return texte[:max_chars]
    return texte


def lignes_avec_coordonnees(
    caracteres: Iterable[Any], hauteur_page: float
) -> list[tuple[float, float, float, str]]:
    """Lignes reconstruites avec leurs coordonnées : ``(v, u0, u1, texte)``.

    ``v`` est la position en travers du texte (ordonnée dans le repère de
    lecture), ``u0``/``u1`` l'étendue le long du texte. Les lignes sont
    rendues dans l'ordre de lecture (``v`` croissant), les caractères dans
    l'ordre de lecture (``u`` croissant). Ces coordonnées sont ce qui permet
    de vérifier qu'un tableau pivoté reste lu de gauche à droite, ligne par
    ligne, et non dans un ordre quelconque.
    """
    caracteres = list(caracteres)
    return _lignes_pour_orientation(caracteres, hauteur_page, orientation_dominante(caracteres))


def lignes_minoritaires(caracteres: Iterable[Any], hauteur_page: float) -> list[str]:
    """Lignes dont l'orientation diffère de celle du corps de la page.

    Cas réel : un tampon vertical, une mention marginale, une cote écrite le
    long d'un bord. pdfplumber ne les rend **pas** dans ``extract_text`` (ils
    n'appartiennent à aucune ligne horizontale) : sans ce complément, leur
    contenu — parfois une référence client — disparaît de l'index.

    Le corps de page n'est pas répété : seules les lignes des orientations
    minoritaires sont rendues.
    """
    caracteres = list(caracteres)
    if not caracteres:
        return []
    par_orientation: dict[int, list[Any]] = {}
    for caractere in caracteres_minoritaires(caracteres):
        orientation = _quart_de_tour(_direction_affichage(caractere))
        par_orientation.setdefault(orientation, []).append(caractere)
    lignes: list[str] = []
    for orientation in sorted(par_orientation):
        groupe = par_orientation[orientation]
        if len(groupe) < 2:
            continue
        lignes.extend(
            ligne[3] for ligne in _lignes_pour_orientation(groupe, hauteur_page, orientation)
        )
    return lignes


def caracteres_minoritaires(caracteres: Iterable[Any]) -> list[Any]:
    """Caractères dont l'orientation diffère de celle du corps de la page.

    Sert à les retirer de la page avant ``extract_text`` : livrés tels quels
    par pdfplumber, ils ressortent inversés ou entrelacés (« NOPMAT » pour
    « TAMPON »), ce qui pollue l'index avec des mots qui n'existent pas.
    """
    caracteres = list(caracteres)
    if not caracteres:
        return []
    dominante = orientation_dominante(caracteres)
    return [
        caractere
        for caractere in caracteres
        if _quart_de_tour(_direction_affichage(caractere)) != dominante
    ]
