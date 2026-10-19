"""Normalisation canonique des modèles de bateaux — Lot 2.

Ne réécrit pas la normalisation casse/accents/ligatures existante :
étend ``normaliser_cle_recherche`` pour les séparateurs (tirets,
underscores, espaces multiples) qui définissent le « même bateau ».

Règle de canonisation :
- casse, accents, ligatures œ/æ/ß déjà gérées par normaliser_cle_recherche
- tirets (-, –, —), underscores (_) et slashs (/) deviennent des espaces
- espaces multiples (incl. insécables) comprimés
- points et chiffres conservés (garde-fou 45 vs 45.1 / 45 CC)

Miroir SQL : la fonction PostgreSQL ``seamtech_texte_recherche`` fait
le même travail côté trigrammes/index, mais cette version Python est
utilisée pour la clé normalisée ``boat_models.normalized_key`` et
``boat_model_aliases.alias_normalized``.
"""

from __future__ import annotations

import re
import unicodedata

# Réutilise la table de ligatures du module historique pour parité exacte
from seamtech_search.fiches.normalisation import _LIGATURES_RECHERCHE  # noqa: F401 - réutilisation intentionnelle

# Tirets et séparateurs : tiret simple, tiret long, underscore, slash
_RE_SEPARATEURS_BATEAU = re.compile(r"[-_–—/]+")

# Séparateurs pour la tokenisation de la requête (comme _normaliser_separateurs)
_RE_SEPARATEURS_REQUETE = re.compile(r"[-_/]+")


def _plier_diacritiques(texte: str, *, casefold: bool) -> str:
    decompose = unicodedata.normalize("NFD", texte)
    sans_signes = "".join(c for c in decompose if not unicodedata.combining(c))
    plie = sans_signes.casefold() if casefold else sans_signes.lower()
    return " ".join(plie.split())


def normaliser_cle_bateau(texte: str) -> str:
    """Clé normalisée d'un modèle de bateau (canonisation Lot 2).

    - ligatures œ/æ → oe/ae (comme normaliser_cle_recherche)
    - suppression des diacritiques latin (NFD + retrait combinants)
    - casefold Unicode (casse pliée)
    - tirets (-, –, —), underscores _, slash / → espace
    - espaces (incl. insécables) comprimés

    Exemples :
    - "Oceanis 45" == "OCEANIS-45" == "Oceanis  45" → "oceanis 45"
    - "GÉNOIS" == "GENOIS" == "genois" → "genois"
    - "CŒUR" == "coeur" → "coeur"
    - "Oceanis 45.1" → "oceanis 45.1" (point conservé, garde-fou)
    - "Oceanis 45 CC" → "oceanis 45 cc" (suffixe conservé)
    - "FIRST-40.7" → "first 40.7"
    """
    if not texte:
        return ""
    # Ligatures (comme normaliser_cle_recherche)
    for source, cible in _LIGATURES_RECHERCHE:
        if source in texte:
            texte = texte.replace(source, cible)
    # Diacritiques + casefold + espaces
    decompose = unicodedata.normalize("NFD", texte)
    sans_signes = "".join(c for c in decompose if not unicodedata.combining(c))
    plie = sans_signes.casefold()
    # Tirets, underscores, slash → espace (après casefold pour couvrir tous les tirets)
    plie = _RE_SEPARATEURS_BATEAU.sub(" ", plie)
    # Espaces comprimés (inclut insécables déjà normalisés par NFD)
    # On remplace aussi les espaces insécables explicites par espace normal
    plie = plie.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2007", " ").replace("\u2009", " ")
    plie = " ".join(plie.split())
    return plie


def normaliser_separateurs_requete(texte: str) -> str:
    """Normalise les séparateurs de la requête (tirets, underscores, slashs).

    Identique à recherche._normaliser_separateurs mais étendu au slash.
    """
    return _RE_SEPARATEURS_REQUETE.sub(" ", texte).strip()


def est_variante_distincte(cle1: str, cle2: str) -> bool:
    """Garde-fou : deux clés normalisées sont-elles des variantes distinctes ?

    Deux modèles ayant des numéros de version/variante distincts
    (ex: "oceanis 45" vs "oceanis 45.1" ou "oceanis 45 cc") NE DOIVENT JAMAIS
    être fusionnés automatiquement.

    Cette fonction détecte les variantes : si l'une est préfixe de l'autre
    plus un suffixe de version/variante (ex: ".1", " cc", " mk2"), elles sont
    distinctes et ne doivent pas être auto-fusionnées.

    Utilisée pour refuser l'auto_exact quand les clés sont proches mais
    distinctes par suffixe.
    """
    if cle1 == cle2:
        return False
    # Si l'une est préfixe de l'autre + séparateur
    for prefix, suffix in [(cle1, cle2), (cle2, cle1)]:
        if suffix.startswith(prefix + " ") or suffix.startswith(prefix + "."):
            reste = suffix[len(prefix):].lstrip(" .")
            if reste:
                # Suffixe de variante : ".1", "1", "cc", "mk2", "v2", etc.
                # On considère que tout suffixe après préfixe = variante distincte
                return True
    return False
