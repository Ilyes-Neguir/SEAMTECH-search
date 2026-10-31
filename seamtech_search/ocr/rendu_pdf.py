"""Sous-processus local de rendu PDF borné pour l'OCR.

Les chemins sont transmis sur stdin, jamais dans la ligne de commande. Aucun
message d'exception ou contenu du fichier n'est imprimé. Le parent traite tout
échec comme indisponible et ne doit pas retomber sur les images brutes.
"""
from __future__ import annotations

import io
import json
import math
import os
import sys

_MAX_PIXELS = 25_000_000
_MAX_DIMENSION = 8192
_MAX_MEMOIRE = 1024 * 1024 * 1024


def _limiter_processus(timeout_s: int) -> None:
    if os.name != "posix":
        raise RuntimeError("Rendu isolé non pris en charge")
    import resource

    # Ne jamais relever un plafond déjà imposé par l'environnement.
    for ressource, limite in ((resource.RLIMIT_AS, _MAX_MEMOIRE), (resource.RLIMIT_CPU, timeout_s + 1)):
        _courant, maximum = resource.getrlimit(ressource)
        nouveau = limite if maximum == resource.RLIM_INFINITY else min(limite, maximum)
        resource.setrlimit(ressource, (nouveau, nouveau))


def _rotation_image_simple(page) -> int:
    """Annule /Rotate seulement si une image simple n'a pas été compensée.

    Les compositions ambiguës restent dans leur orientation d'affichage : on
    ne prétend pas avoir inféré l'orientation intrinsèque des pixels du scan.
    """
    from pypdf.generic import ContentStream

    try:
        objets = page["/Resources"].get_object()["/XObject"].get_object()
        matrice = (1., 0., 0., 1., 0., 0.)
        pile = []
        matrices = []
        for args, op in ContentStream(page.get_contents(), page.pdf).operations:
            if op == b"q":
                pile.append(matrice)
            elif op == b"Q":
                matrice = pile.pop()
            elif op == b"cm":
                a, b, c, d, e, f = (float(v) for v in args)
                A, B, C, D, E, F = matrice
                matrice = (A*a+C*b, B*a+D*b, A*c+C*d, B*c+D*d, A*e+C*f+E, B*e+D*f+F)
            elif op == b"Do":
                objet = objets[args[0]].get_object()
                if objet.get("/Subtype") != "/Image":
                    return 0
                matrices.append(matrice)
        if pile or len(matrices) != 1:
            return 0
        a, b, c, d, e, f = matrices[0]
        if not all(math.isfinite(v) for v in (a, b, c, d, e, f)) or b != 0 or c != 0 or a <= 0 or d <= 0:
            return 0
        angle = int(page.get("/Rotate", 0)) % 360
        return (360 - angle) % 360 if angle in (90, 180, 270) else 0
    except Exception:
        return 0


def _rendre(requete: dict) -> None:
    import pypdfium2
    from pypdf import PdfReader

    source = requete["source"]
    numero = int(requete["page"])
    dpi = int(requete["dpi"])
    sortie = requete["sortie"]
    timeout = int(requete["timeout"])
    if numero < 0 or not 36 <= dpi <= 400 or not 1 <= timeout <= 120:
        raise ValueError("Paramètres de rendu invalides")
    _limiter_processus(timeout)

    # Les annotations/calques dynamiques et les formulaires ne sont pas
    # qualifiés par rapport au visualiseur métier : refuser au lieu de
    # présumer que les deux moteurs montrent exactement la même chose.
    lecteur = PdfReader(source, strict=False)
    catalogue = lecteur.trailer["/Root"]
    if catalogue.get("/OCProperties") is not None or catalogue.get("/AcroForm") is not None:
        raise ValueError("Visibilité dynamique non qualifiée")
    page_source = lecteur.pages[numero]
    if page_source.get("/Annots") is not None or page_source.get("/AA") is not None:
        raise ValueError("Annotations non qualifiées")

    document = pypdfium2.PdfDocument(source)
    try:
        page = document[numero]
        try:
            largeur, hauteur = page.get_size()
            echelle = dpi / 72.0
            if not all(math.isfinite(v) and v > 0 for v in (largeur, hauteur)):
                raise ValueError("Dimensions PDF invalides")
            pixels_x = math.ceil(largeur * echelle)
            pixels_y = math.ceil(hauteur * echelle)
            if max(pixels_x, pixels_y) > _MAX_DIMENSION or pixels_x * pixels_y > _MAX_PIXELS:
                raise ValueError("Page trop grande pour le rendu OCR")
            rotation = _rotation_image_simple(page_source)
            bitmap = page.render(
                scale=echelle, rotation=rotation, draw_annots=False, may_draw_forms=False, limit_image_cache=True
            )
            try:
                with open(sortie, "xb") as fichier:
                    bitmap.to_pil().save(fichier, format="PNG")
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()


def _extraire_image(requete: dict) -> None:
    """Décode une image unique depuis les mêmes octets que la vérification."""
    from pypdf import PdfReader

    from seamtech_search.ocr.pipeline import _page_pdf_vue_integrale

    numero, timeout = int(requete["page"]), int(requete["timeout"])
    if numero < 0 or not 1 <= timeout <= 120:
        raise ValueError("Paramètres d'extraction invalides")
    _limiter_processus(timeout)
    with open(requete["source"], "rb") as entree:
        contenu = entree.read(512 * 1024 * 1024 + 1)
    if len(contenu) > 512 * 1024 * 1024:
        raise ValueError("PDF trop volumineux")
    flux = io.BytesIO(contenu)
    if not _page_pdf_vue_integrale(flux, numero):
        raise ValueError("Page non conforme à l'extraction d'image brute")
    flux.seek(0)
    lecteur = PdfReader(flux, strict=False)
    images = list(lecteur.pages[numero].images)
    if len(images) != 1 or images[0].image is None:
        raise ValueError("Image unique non disponible")
    image = images[0].image
    try:
        largeur, hauteur = image.size
        if min(largeur, hauteur) < 1 or max(largeur, hauteur) > _MAX_DIMENSION or largeur * hauteur > _MAX_PIXELS:
            raise ValueError("Image trop grande")
        with open(requete["sortie"], "xb") as fichier:
            image.save(fichier, format="PNG")
    finally:
        image.close()


def _verifier_image(requete: dict) -> bool:
    from seamtech_search.ocr.pipeline import _page_pdf_vue_integrale

    numero, timeout = int(requete["page"]), int(requete["timeout"])
    if numero < 0 or not 1 <= timeout <= 120:
        raise ValueError("Paramètres de vérification invalides")
    _limiter_processus(timeout)
    return _page_pdf_vue_integrale(requete["source"], numero)


def main() -> int:
    try:
        donnees = sys.stdin.read(16385)
        if len(donnees) > 16384:
            return 1
        requete = json.loads(donnees)
        if requete.get("operation") == "extraire_image":
            _extraire_image(requete)
        elif requete.get("operation") == "verifier_image":
            return 0 if _verifier_image(requete) else 1
        else:
            _rendre(requete)
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
