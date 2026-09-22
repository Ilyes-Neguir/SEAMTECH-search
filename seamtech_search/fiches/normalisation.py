"""Normalisation des valeurs lues (Lot B — plan v3.0 §13, RG5, RG16).

Rôle : transformer la chaîne brute du PDF en valeur typée — « 6,60 m » →
6.600 mètres, « 270 g/m² » → 270.0, « 6 mars 2026 » → 2026-03-06, « Non » →
faux. Toute conversion renvoie ``None`` quand elle ne sait pas (jamais
d'invention) : la valeur brute reste conservée dans le ChampExtrait.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

MOIS_FR = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}

_RE_DECIMAL = re.compile(r"-?\d+(?:[.,]\d+)?")
_RE_NOMBRE = re.compile(r"-?\d+")


def sans_accents(texte: str) -> str:
    """Minuscules sans accents, espaces comprimés — forme de comparaison."""
    decompose = unicodedata.normalize("NFD", texte)
    sans_signes = "".join(caractere for caractere in decompose if not unicodedata.combining(caractere))
    return " ".join(sans_signes.lower().split())


def extraire_decimal(texte: str | None) -> float | None:
    """Premier nombre décimal d'une chaîne (« 6,60 m » → 6.6). ``None`` sinon."""
    if not texte:
        return None
    trouve = _RE_DECIMAL.search(texte.replace(" ", ""))
    if trouve is None:
        return None
    try:
        return float(trouve.group(0).replace(",", "."))
    except ValueError:
        return None


def extraire_entier(texte: str | None) -> int | None:
    """Premier entier d'une chaîne (« Quantité : 2 » → 2). ``None`` sinon."""
    if not texte:
        return None
    trouve = _RE_NOMBRE.search(texte)
    if trouve is None:
        return None
    return int(trouve.group(0))


def vers_metres(valeur: float, unite: str | None) -> float | None:
    """Convertit une cote vers des mètres (m, cm, mm ; m par défaut)."""
    if valeur is None:
        return None
    facteur = {"m": 1.0, "cm": 0.01, "mm": 0.001}.get((unite or "m").strip().lower())
    if facteur is None:
        return None
    return round(valeur * facteur, 3)


def vers_mm(valeur: float, unite: str | None) -> float | None:
    """Convertit vers des millimètres (mm par défaut ; m et cm acceptés)."""
    if valeur is None:
        return None
    facteur = {"mm": 1.0, "cm": 10.0, "m": 1000.0}.get((unite or "mm").strip().lower())
    if facteur is None:
        return None
    return round(valeur * facteur, 1)


def cote_en_metres(texte: str | None) -> float | None:
    """« 6,60 m » / « 6.60 » / « 660 cm » → mètres (2 jeux de cotes du §13)."""
    if not texte:
        return None
    valeur = extraire_decimal(texte)
    if valeur is None:
        return None
    unite = "mm" if re.search(r"\bmm\b", texte, re.I) else "cm" if re.search(r"\bcm\b", texte, re.I) else "m"
    return vers_metres(valeur, unite)


def mesure_en_mm(texte: str | None) -> float | None:
    """« 190 mm » / « 19 cm » → millimètres (colonne de droite des épaisseurs)."""
    if not texte:
        return None
    valeur = extraire_decimal(texte)
    if valeur is None:
        return None
    unite = "m" if re.search(r"\bm\b", texte, re.I) and not re.search(r"\bmm\b", texte, re.I) else "cm" if re.search(r"\bcm\b", texte, re.I) else "mm"
    return vers_mm(valeur, unite)


def grammage_g_m2(texte: str | None) -> float | None:
    """« 270 g/m² », « 65 gr/m2 », « 270g/m2 » → 270.0. ``None`` sinon."""
    if not texte:
        return None
    trouve = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:g|gr)\s*/\s*m\s*[²2^]?", texte, re.I)
    if trouve is None:
        return None
    return float(trouve.group(1).replace(",", "."))


def date_fr_vers_iso(texte: str | None) -> str | None:
    """« 6 mars 2026 » / « 06/03/2026 » → « 2026-03-06 ». ``None`` sinon.

    Format français assumé (jour/mois/année) — le dépôt est français ; une
    date illisible renvoie None, jamais une date inventée.
    """
    if not texte:
        return None
    texte = sans_accents(texte)
    trouve = re.search(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", texte)
    if trouve:
        jour, mois_litteral, annee = trouve.groups()
        mois = MOIS_FR.get(mois_litteral)
        if mois is not None:
            try:
                return date(int(annee), mois, int(jour)).isoformat()
            except ValueError:
                return None
    trouve = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", texte)
    if trouve:
        jour, mois, annee = (int(groupe) for groupe in trouve.groups())
        try:
            return date(annee, mois, jour).isoformat()
        except ValueError:
            return None
    return None


def booleen_fr(texte: str | None) -> bool | None:
    """« Non » → faux, « Oui » → vrai, « ~ »/« - » → None (RG5 : vide = sans objet)."""
    if not texte:
        return None
    normalise = sans_accents(texte)
    if normalise.startswith("non"):
        return False
    if normalise.startswith("oui"):
        return True
    return None


def separer_nom_et_detail(texte: str | None, motif_detail: str = r"\(([^)]*)\)") -> tuple[str | None, str | None]:
    """« 29er (15') » → (« 29er », « 15' ») ; « Sailonet (Cruette) » → idem."""
    if not texte:
        return None, None
    trouve = re.search(motif_detail, texte)
    if trouve is None:
        return texte.strip() or None, None
    nom = texte[: trouve.start()].strip()
    return (nom or None), (trouve.group(1).strip() or None)


def scinder_sur_tirets(texte: str | None) -> list[str]:
    """« Bleu · 50 mm · Nylon 65 g/m2 » → morceaux (séparateurs · ; / ; -)."""
    if not texte:
        return []
    morceaux = re.split(r"\s*[·;]\s*|\s+\|\s+", texte)
    return [morceau.strip() for morceau in morceaux if morceau.strip()]


def decomposer_jonction(texte: str | None) -> tuple[int | None, int | None, float | None, str | None]:
    """« 2 zigzag 6 tps 30 mm » → (2, 6, 30.0, description d'origine).

    Retourne (nb_zigzag, nb_points, espacement_mm, description).
    """
    if not texte:
        return None, None, None, None
    normalise = sans_accents(texte)
    nb_zigzag = None
    nb_points = None
    espacement = None
    trouve = re.search(r"(\d+)\s*zigzags?\b", normalise)
    if trouve:
        nb_zigzag = int(trouve.group(1))
    trouve = re.search(r"(\d+)\s*(?:tps|points)\b", normalise)
    if trouve:
        nb_points = int(trouve.group(1))
    trouve = re.search(r"(\d+(?:[.,]\d+)?)\s*mm\b", normalise)
    if trouve:
        espacement = float(trouve.group(1).replace(",", "."))
    return nb_zigzag, nb_points, espacement, texte.strip()
