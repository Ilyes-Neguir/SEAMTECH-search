"""Gestion des synonymes de recherche (voilerie) — Lot 2.

Deux sources de synonymes :
- table historique ``synonyme`` (terme → cible) utilisée par recherche.py
- nouvelle table ``search_synonyms`` (canonical_term, normalized_alias, category)

Ce module charge et cache les deux, et fournit l'expansion de requête
bidirectionnelle : GV ↔ Grand-Voile, GE/GEN ↔ Génois, etc.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from seamtech_search.bateaux.normalisation import normaliser_cle_bateau

# Cache par base : (nom_base, horodatage) -> dict
_SYNONYMES_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_SEARCH_SYNONYMES_CACHE: dict[str, tuple[float, dict[str, dict[str, str]]]] = {}
_SYNONYMES_TTL_S = 30.0


def _charger_synonymes_historique(cursor: Any, nom_base: str) -> dict[str, str]:
    """Charge la table historique ``synonyme`` (terme → cible)."""
    maintenant = time.monotonic()
    en_cache = _SYNONYMES_CACHE.get(nom_base)
    if en_cache is not None and maintenant - en_cache[0] < _SYNONYMES_TTL_S:
        return en_cache[1]
    try:
        cursor.execute("SELECT terme, cible FROM synonyme")
        table = {str(ligne[0]).lower(): str(ligne[1]) for ligne in cursor.fetchall()}
    except Exception:
        table = {}
    _SYNONYMES_CACHE[nom_base] = (maintenant, table)
    return table


def _charger_search_synonymes(cursor: Any, nom_base: str) -> dict[str, dict[str, str]]:
    """Charge la table ``search_synonyms`` active.

    Retourne un dict avec deux sous-dicts :
    - ``alias_to_canonical`` : normalized_alias → canonical_term
    - ``canonical_to_alias`` : normalized_canonical → normalized_alias (premier alias, pour expansion inverse)

    Pour les synonymes multi-alias (GE, GEN → Génois), on garde la liste complète
    dans ``alias_to_canonical`` et la première entrée dans ``canonical_to_alias``.

    La table est petite (<100 lignes) et modifiable à chaud.
    """
    maintenant = time.monotonic()
    en_cache = _SEARCH_SYNONYMES_CACHE.get(nom_base)
    if en_cache is not None and maintenant - en_cache[0] < _SYNONYMES_TTL_S:
        return en_cache[1]
    try:
        cursor.execute(
            "SELECT canonical_term, normalized_alias, category FROM search_synonyms WHERE active = true"
        )
        rows = cursor.fetchall()
    except Exception:
        rows = []

    alias_to_canonical: dict[str, str] = {}
    canonical_to_alias: dict[str, str] = {}
    # Pour les expansions multi-mots, on garde aussi la forme normalisée de canonical
    for canonical_term, normalized_alias, _category in rows:
        alias_norm = str(normalized_alias).lower().strip()
        canonical_norm = normaliser_cle_bateau(str(canonical_term))
        alias_to_canonical[alias_norm] = canonical_norm
        # Garde le premier alias pour la direction inverse ; si plusieurs alias
        # pointent vers même canonical, on garde le premier (ex: GE et GEN → Génois)
        if canonical_norm not in canonical_to_alias:
            canonical_to_alias[canonical_norm] = alias_norm
        # Aussi stocker le mapping inverse complet pour expansions multiples
        # On stocke aussi canonical brut → alias pour recherche par n-gram

    resultat = {
        "alias_to_canonical": alias_to_canonical,
        "canonical_to_alias": canonical_to_alias,
    }
    _SEARCH_SYNONYMES_CACHE[nom_base] = (maintenant, resultat)
    return resultat


def invalider_cache_synonymes() -> None:
    """Invalidate tous les caches de synonymes (tests, rechargement à chaud)."""
    _SYNONYMES_CACHE.clear()
    _SEARCH_SYNONYMES_CACHE.clear()


def _expand_query_with_synonymes(
    texte: str,
    synonymes_historique: Mapping[str, str],
    search_synonymes: dict[str, dict[str, str]],
) -> str:
    """Étend la requête avec les synonymes historiques et search_synonyms.

    Pour chaque terme de la requête, on cherche :
    - si le terme est un alias historique (synonyme.terme) → ajoute cible
    - si le terme est un alias search_synonyms (normalized_alias) → ajoute canonical
    - si la requête entière (ou un n-gram) est un canonical → ajoute alias
    """
    if not texte:
        return texte
    # D'abord les synonymes historiques (mot à mot, sans casse)
    import re

    resultat = texte
    # Historique : remplacement mot à mot
    for terme, cible in synonymes_historique.items():
        resultat = re.sub(rf"\b{re.escape(terme)}\b", cible, resultat, flags=re.IGNORECASE)

    # Search_synonyms : n-gram detection — construit de vraies variantes liées
    # et conserve les autres termes comme contraintes AND (sans concaténation SQL).
    # Ex: "spi 29er" + spi→spinnaker ⇒ "spi 29er OR spinnaker 29er" (garde 29er)
    query_norm = normaliser_cle_bateau(resultat)
    tokens = query_norm.split()
    if not tokens:
        return resultat

    alias_to_canonical = search_synonymes.get("alias_to_canonical", {})
    canonical_to_alias = search_synonymes.get("canonical_to_alias", {})

    # Génère tous les n-grams jusqu'à 3 avec leur position
    variants: set[str] = set()
    for n in range(1, min(4, len(tokens) + 1)):
        for i in range(len(tokens) - n + 1):
            ng = " ".join(tokens[i : i + n])
            if ng in alias_to_canonical:
                canonical = alias_to_canonical[ng]
                # Variante où le n-gram est remplacé par son canonical, en gardant le contexte
                variant_tokens = tokens[:i] + canonical.split() + tokens[i + n :]
                variant = " ".join(variant_tokens)
                if variant != query_norm:
                    variants.add(variant)
            if ng in canonical_to_alias:
                alias = canonical_to_alias[ng]
                variant_tokens = tokens[:i] + alias.split() + tokens[i + n :]
                variant = " ".join(variant_tokens)
                if variant != query_norm:
                    variants.add(variant)

    if variants:
        # websearch_to_tsquery reconnaît le mot OR (pas |) comme alternative
        resultat = resultat + " OR " + " OR ".join(sorted(variants))
    return resultat


def expand_query_for_search(
    query: str,
    cursor: Any,
    nom_base: str,
    *,
    include_historique: bool = True,
) -> str:
    """Point d'entrée public : étend la requête avec tous les synonymes disponibles.

    Utilisé par recherche.py et indexer.py pour l'expansion de requête.
    """
    if not query:
        return query
    synonymes_historique = _charger_synonymes_historique(cursor, nom_base) if include_historique else {}
    search_synonymes = _charger_search_synonymes(cursor, nom_base)
    return _expand_query_with_synonymes(query, synonymes_historique, search_synonymes)
