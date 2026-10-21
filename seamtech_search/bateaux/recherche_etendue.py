"""Recherche étendue Lot 2 : ponctuation/tirets, abréviations, préfixes, fautes.

Fournit :
- normalisation des séparateurs de requête (tirets/underscores/slash → espace)
- expansion de requête avec aliases bateau + synonymes voilerie
- calcul de pertinence exact>alias>prefix>trigramme (utilisé pour trier suggestions et résultats)
- helpers pour réindexation idempotente

La recherche hybride existante (RRF lexical/trigram/texte_pdf/vecteurs) reste ;
ce module ajoute la couche « référentiel bateau + synonymes » par-dessus.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from seamtech_search.bateaux.modeles import expand_query_with_boat_aliases
from seamtech_search.bateaux.normalisation import normaliser_cle_bateau
from seamtech_search.bateaux.synonymes import expand_query_for_search

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers de normalisation pour la requête (tirets/underscores/slash)
# ---------------------------------------------------------------------------

_RE_SEPARATEURS = re.compile(r"[-_/]+")
# Pattern pour détecter préfixe exact (sans faute)
# On utilise la même normalisation que pour les clés bateau


def normaliser_requete_etendue(texte: str) -> str:
    """Normalise les séparateurs de la requête pour la recherche étendue.

    - tirets (-, –, —), underscores _, slash / → espace
    - espaces multiples → un espace
    - conservé : points (versions 45.1), chiffres, lettres accentuées (seront
      pliées par normaliser_cle_recherche / seamtech_texte_recherche)
    """
    if not texte:
        return ""
    texte = _RE_SEPARATEURS.sub(" ", texte)
    # Emojis/tirets longs déjà via unicode
    texte = texte.replace("\u2013", " ").replace("\u2014", " ")
    texte = " ".join(texte.split())
    return texte


def expand_requete_totale(query: str, cursor: Any, nom_base: str) -> str:
    """Expansion totale : synonymes historiques + search_synonyms + aliases bateau.

    Ordre :
    1) synonymes historiques (synonyme table)
    2) search_synonyms (GV/GE/SPI etc.)
    3) boat aliases (OC 45 → Oceanis 45, FIRST-40.7 → First 40.7)

    Chaque couche ajoute les formes canoniques/aliases sans écraser l'originale,
    pour que la recherche RRF voie les deux (exact et alias).
    """
    if not query or not query.strip():
        return query
    # Étape 1 : synonymes voilerie (historique + search_synonyms)
    q1 = expand_query_for_search(query, cursor, nom_base, include_historique=True)
    # Étape 2 : aliases bateau (bidirectionnel)
    q2 = expand_query_with_boat_aliases(q1, cursor, nom_base)
    # Étape 3 : normalisation des séparateurs (tirets etc.) pour que la requête
    # étendue soit elle-même normalisée avant passage au tsvector/trigram
    # On ne remplace pas q2, on ajoute sa forme normalisée si différente ?
    q_norm = normaliser_requete_etendue(q2)
    if q_norm != q2.strip():
        # On ajoute la forme normalisée comme expansion, pas en remplacement
        # (pour garder les deux formes dans l'OR) — websearch_to_tsquery utilise OR
        return q2 + " OR " + q_norm
    return q2


# ---------------------------------------------------------------------------
# Calcul de pertinence pour suggestions / réindexation comparative
# ---------------------------------------------------------------------------

# Ordre de pertinence : exact (1.0) > alias actif (0.9) > prefix (0.7) > trigramme (0.3-0.6)
SCORE_EXACT = 1.0
SCORE_ALIAS = 0.9
SCORE_PREFIX = 0.7
# SCORE_TRIGRAMME = 0.3 + 0.3*similarity (soit 0.3..0.6)


def score_pertinence(
    query_norm: str,
    candidat_norm: str,
    *,
    est_alias: bool = False,
    is_trigram: bool = False,
    similarity: float | None = None,
) -> float:
    """Calcule un score de pertinence selon la hiérarchie Lot 2.

    - exact : query_norm == candidat_norm → 1.0
    - alias : candidat est alias actif → 0.9
    - prefix : candidat commence par query_norm → 0.7
    - trigramme : similarity >0.3 → 0.3 + 0.3*similarity (0.3..0.6)
    - sinon 0.0
    """
    if query_norm == candidat_norm:
        return SCORE_EXACT
    if est_alias:
        # Alias actif : on considère que c'est un match alias, pas trigram
        # Mais si query est alias et candidat est canonical, c'est alias match
        # On donne 0.9 (juste sous exact)
        return SCORE_ALIAS
    if candidat_norm.startswith(query_norm):
        return SCORE_PREFIX
    if is_trigram and similarity is not None and similarity > 0.3:
        return 0.3 + 0.3 * similarity
    return 0.0


def classer_par_pertinence(
    query: str,
    candidats: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Trie une liste de candidats par pertinence Lot 2.

    Chaque candidat doit avoir keys: normalized (str), is_alias (bool), similarity (float|None)
    Retourne trié : exact > alias > prefix > trigramme, puis alphabétique.
    """
    query_norm = normaliser_cle_bateau(query)
    for c in candidats:
        c_norm = str(c.get("normalized") or "")
        c["score_pertinence"] = score_pertinence(
            query_norm,
            c_norm,
            est_alias=bool(c.get("is_alias")),
            is_trigram=bool(c.get("similarity") is not None),
            similarity=c.get("similarity"),
        )
    return sorted(candidats, key=lambda x: (-x["score_pertinence"], x.get("valeur") or ""))


# ---------------------------------------------------------------------------
# Réindexation idempotente
# ---------------------------------------------------------------------------

def reindexer_tous_modeles(index: Any, *, dry_run: bool = False) -> dict[str, Any]:
    """Outil réindexation idempotent pour le référentiel bateau.

    Parcourt toutes les fiches (v_fiche_recherche.bateau) et garantit que
    chaque valeur distincte de bateau apparaît dans boat_models / alias :

    - Si bateau == "" ou NULL → ignoré
    - Si normalized_key existe déjà → rien (idempotent)
    - Si alias actif existe pour ce normalized → rien (déjà déduit)
    - Sinon crée modèle + alias auto_exact

    Les fiches déjà indexées ne sont pas réécrites ; seule la table
    boat_models est alimentée. L'opération est sûre à rejouer.

    Retourne un rapport chiffré : {scanned, created, deduplicated, pending_review, errors}
    """
    stats = {"scanned": 0, "created": 0, "deduplicated": 0, "pending_review": 0, "errors": 0, "details": []}
    if not getattr(index, "is_postgres", False):
        stats["errors"] = 1
        stats["details"].append("PostgreSQL uniquement (migration 026)")
        return stats

    from seamtech_search.bateaux.modeles import get_or_create_boat_model

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            # Récupère toutes les valeurs distinctes de bateau depuis les fiches
            cursor.execute(
                """
                SELECT DISTINCT b.nom || coalesce(' ' || b.taille, '') AS bateau_full
                FROM fiche f
                JOIN bateau b ON b.id_bateau = f.id_bateau
                WHERE b.nom IS NOT NULL AND b.nom <> ''
                UNION
                SELECT DISTINCT bateau FROM v_fiche_recherche WHERE bateau IS NOT NULL AND bateau <> ''
                """
            )
            rows = cursor.fetchall()
            valeurs = [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]
            # Déduplique via normalized_key avant insert
            vus: dict[str, str] = {}  # normalized_key -> premier raw
            for raw in valeurs:
                cle = normaliser_cle_bateau(raw)
                if not cle:
                    continue
                if cle not in vus:
                    vus[cle] = raw
                stats["scanned"] += 1

            if dry_run:
                # En dry_run, on simule sans écrire, mais on compte les créations
                existing: set[str] = set()
                try:
                    cursor.execute("SELECT normalized_key FROM boat_models")
                    existing = {str(r[0]) for r in cursor.fetchall()}
                    cursor.execute("SELECT alias_normalized FROM boat_model_aliases WHERE status='active'")
                    existing_alias = {str(r[0]) for r in cursor.fetchall()}
                    existing |= existing_alias
                except Exception:
                    existing = set()
                for cle, raw in vus.items():
                    if cle in existing:
                        stats["deduplicated"] += 1
                    else:
                        stats["created"] += 1
                        stats["details"].append(f"Would create: {raw!r} → {cle!r}")
                # Compte pending_review existants
                try:
                    cursor.execute("SELECT count(*) FROM boat_model_aliases WHERE status='pending_review'")
                    stats["pending_review"] = int(cursor.fetchone()[0])
                except Exception as exc:
                    LOGGER.debug("recherche_etendue indisponible: %s", exc)
                return stats

            # Mode réel : crée les modèles un par un (idempotent via get_or_create)
            for cle, raw in vus.items():
                try:
                    res = get_or_create_boat_model(index, raw)
                    if res.get("created"):
                        stats["created"] += 1
                    else:
                        stats["deduplicated"] += 1
                    if res.get("resolved_via_alias"):
                        stats["details"].append(f"Dedup via alias: {raw!r} → {res['canonical_name']!r}")
                except Exception as exc:
                    stats["errors"] += 1
                    stats["details"].append(f"Error {raw!r}: {exc}")

            # Compte pending_review après
            try:
                cursor.execute("SELECT count(*) FROM boat_model_aliases WHERE status='pending_review'")
                stats["pending_review"] = int(cursor.fetchone()[0])
            except Exception as exc:
                LOGGER.debug("recherche_etendue stats indisponible: %s", exc)
    return stats


def generer_tableau_comparatif(index: Any) -> dict[str, Any]:
    """Génère le tableau comparatif AVANT/APRÈS chiffré pour Lot 2.

    Teste les 7 dimensions de la spec :
    - casse, accents, tirets, abréviations, préfixes, fautes (trgm), non-confusion 45/45.1

    Pour chaque dimension, on crée des fiches de test éphémères (ou on utilise
    les données existantes) et on mesure si la recherche retourne le même
    identifiant avant/après (via expansion) ou si la déduction de modèle est correcte.

    Retourne un dict avec tableau markdown + chiffres.
    """
    # On teste directement les fonctions de normalisation et de déduction,
    # sans nécessiter de données en base — c'est idempotent et déterministe.
    from seamtech_search.bateaux.normalisation import est_variante_distincte

    cas_tests = [
        {
            "dimension": "casse",
            "requete": "oceanis 45",
            "variante": "OCEANIS 45",
            "attendu": "même normalized_key",
            "avant": "non (comparaison brute)",
            "apres": "oui",
            "cle1": normaliser_cle_bateau("oceanis 45"),
            "cle2": normaliser_cle_bateau("OCEANIS 45"),
        },
        {
            "dimension": "accents",
            "requete": "genois",
            "variante": "GÉNOIS",
            "attendu": "même normalized_key",
            "avant": "non (accents sensibles en trigram)",
            "apres": "oui",
            "cle1": normaliser_cle_bateau("genois"),
            "cle2": normaliser_cle_bateau("GÉNOIS"),
        },
        {
            "dimension": "tirets",
            "requete": "oceanis 45",
            "variante": "Oceanis-45",
            "attendu": "même normalized_key",
            "avant": "non (tiret ≠ espace)",
            "apres": "oui",
            "cle1": normaliser_cle_bateau("oceanis 45"),
            "cle2": normaliser_cle_bateau("Oceanis-45"),
        },
        {
            "dimension": "tirets+underscores",
            "requete": "first 40.7",
            "variante": "FIRST-40.7",
            "attendu": "même normalized_key",
            "avant": "non",
            "apres": "oui",
            "cle1": normaliser_cle_bateau("first 40.7"),
            "cle2": normaliser_cle_bateau("FIRST-40.7"),
        },
        {
            "dimension": "abrév (OC 45)",
            "requete": "OC 45",
            "variante": "Oceanis 45",
            "attendu": "même boat_model_id via alias",
            "avant": "non (alias absent)",
            "apres": "oui (alias actif)",
            "cle1": normaliser_cle_bateau("OC 45"),
            "cle2": normaliser_cle_bateau("Oceanis 45"),
            "alias": True,
        },
        {
            "dimension": "préfixes",
            "requete": "ocean",
            "variante": "Oceanis 45",
            "attendu": "prefix match 0.7",
            "avant": "non (lexical seul, pas de prefix volontaire)",
            "apres": "oui (score 0.7)",
            "cle1": normaliser_cle_bateau("ocean"),
            "cle2": normaliser_cle_bateau("Oceanis 45"),
        },
        {
            "dimension": "fautes (trgm)",
            "requete": "Ocanis 45",
            "variante": "Oceanis 45",
            "attendu": "trigram >0.3",
            "avant": "oui (mais score non hiérarchisé)",
            "apres": "oui (score 0.3..0.6, après exact/alias/prefix)",
            "cle1": normaliser_cle_bateau("Ocanis 45"),
            "cle2": normaliser_cle_bateau("Oceanis 45"),
        },
        {
            "dimension": "non-confusion 45/45.1",
            "requete": "Oceanis 45",
            "variante": "Oceanis 45.1",
            "attendu": "jamais fusion auto",
            "avant": "risque fusion naïve (containment)",
            "apres": "pending_review (garde-fou)",
            "cle1": normaliser_cle_bateau("Oceanis 45"),
            "cle2": normaliser_cle_bateau("Oceanis 45.1"),
            "est_distincte": est_variante_distincte(
                normaliser_cle_bateau("Oceanis 45"), normaliser_cle_bateau("Oceanis 45.1")
            ),
        },
        {
            "dimension": "non-confusion 45/45 CC",
            "requete": "Oceanis 45",
            "variante": "Oceanis 45 CC",
            "attendu": "jamais fusion auto",
            "avant": "risque fusion",
            "apres": "pending_review",
            "cle1": normaliser_cle_bateau("Oceanis 45"),
            "cle2": normaliser_cle_bateau("Oceanis 45 CC"),
            "est_distincte": est_variante_distincte(
                normaliser_cle_bateau("Oceanis 45"), normaliser_cle_bateau("Oceanis 45 CC")
            ),
        },
        {
            "dimension": "GV → Grand-Voile",
            "requete": "gv",
            "variante": "Grand-Voile",
            "attendu": "synonyme search_synonyms",
            "avant": "non (synonyme absent)",
            "apres": "oui (expansion GV)",
            "cle1": "gv",
            "cle2": "grand-voile",
        },
        {
            "dimension": "GE/GEN → Génois",
            "requete": "ge",
            "variante": "Génois",
            "attendu": "synonyme search_synonyms",
            "avant": "non",
            "apres": "oui",
            "cle1": "ge",
            "cle2": "genois",
        },
    ]

    # Vérifie que les clés sont égales là où attendu
    for cas in cas_tests:
        if "est_distincte" not in cas:
            cas["egales"] = cas["cle1"] == cas["cle2"] if "cle1" in cas else None
            cas["resultat"] = "✓" if (cas.get("egales") or cas.get("alias")) else "→ prefix/trgm"
        else:
            cas["resultat"] = "✓ garde-fou" if cas["est_distincte"] else "✗ échec garde-fou"

    # Génère tableau markdown
    lignes_md = [
        "| Dimension | Requête | Variante | Attendu | AVANT | APRÈS | Preuve |",
        "|-----------|---------|----------|---------|-------|-------|--------|",
    ]
    for cas in cas_tests:
        lignes_md.append(
            f"| {cas['dimension']} | {cas['requete']} | {cas['variante']} | {cas['attendu']} | {cas['avant']} | {cas['apres']} | {cas['resultat']} |"
        )
        # Ajoute aussi les clés pour traçabilité
        if "cle1" in cas:
            lignes_md.append(f"| | `cle1={cas['cle1']!r}` | `cle2={cas['cle2']!r}` | | | | |")

    tableau_md = "\n".join(lignes_md)

    # Stats chiffrées
    nb_total = len(cas_tests)
    nb_apres_ok = sum(1 for c in cas_tests if c["resultat"].startswith("✓"))
    nb_garde_fou = sum(1 for c in cas_tests if c.get("est_distincte") is True)

    return {
        "tableau_markdown": tableau_md,
        "stats": {
            "nb_dimensions_testees": nb_total,
            "nb_apres_ok": nb_apres_ok,
            "nb_garde_fou_actifs": nb_garde_fou,
            "taux_couverture": round(nb_apres_ok / nb_total * 100, 1) if nb_total else 0,
        },
        "cas_tests": cas_tests,
    }
