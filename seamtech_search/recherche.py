"""Lot E — recherche hybride des fiches techniques (plan v3.0 §17.2, §17.5, §11).

« Comme Google » : mots-clés + filtres + facettes avec compteurs + suggestions
issues des valeurs RÉELLEMENT présentes. Aucune réécriture de l'existant :
``/search`` (fichiers, Phase 0) continue de servir ; ce module ajoute
``GET /recherche`` et ``GET /recherche/suggestions`` sur la couche métier.

Sources de classement, fusionnées par **RRF k=60** (chaque source vote avec le
rang, pas avec son score propre — les échelles ts_rank (drapeau 16 : normalisation sous-linéaire type BM25) / similarité / cosine
ne sont pas comparables) :

- ``lexical``   : tsvector PONDÉRÉ de la fiche (A = code + titre, B = champs
                  métier, C = notes — fonction rafraichir_texte_recherche_fiche,
                  remplie à l'ÉCRITURE depuis la migration 018, rafraîchie à
                  la VALIDATION) ;
- ``trigrammes``: tolérance aux fautes (pg_trgm, ``word_similarity``) — volet
                  dégradable de la migration 012 ;
- ``texte_pdf`` : texte des chunks et des documents rattachés à la fiche ;
- ``vecteurs``  : embeddings pgvector — branche DORMANTE tant que le Lot F
                  (encodeur local e5-small) n'est pas là : elle ne s'active
                  que si un appelant fournit ``encode_requete`` ET que la base
                  contient des embeddings. La fusion RRF, elle, est déjà
                  réelle et testée.

RG3 en lecture : le statut n'est JAMAIS masqué. Depuis la décision
commanditaire du 2026-09-29 (production-readiness, Phase 2.1), la recherche
renvoie PAR DÉFAUT les fiches ``valide`` ET ``a_valider`` — ces dernières
badgées « non vérifiée » par l'interface, leur ``statut`` étant toujours
présent dans chaque résultat. ``inclure_a_valider=false`` restreint à
l'archive de confiance (fiches validées). Une fiche ``rejete`` n'est JAMAIS
renvoyée. Les recherches SANS RÉSULTAT sont
journalisées (``recherche_log``, index partiel migration 012) : c'est la
matière première de l'amélioration du lexique (critère de sortie Phase 3).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Annotated, Any, Callable, Mapping, Sequence

from fastapi import Header, HTTPException, Query

from seamtech_search.fiches.normalisation import sans_accents, vers_metres

LOGGER = logging.getLogger("seamtech.recherche")

# Constantes de fusion et de volume (plan §17.2 / §11.3).
RRF_K = 60
PROFONDEUR_SOURCES = 100  # candidats demandés à chaque source avant fusion
FACETTE_LIMITE = 20       # valeurs max par facette affichée
SEUIL_TRIGRAMMES = 0.30   # word_similarity minimale pour la source tolérante
SUGGESTION_LIMITE_DEFAUT = 10
SYNONYMES_TTL_S = 30.0    # le référentiel synonyme est petit et modifiable à chaud

# Unités métier des cotes (normalisées à l'extraction, documentées dans docs/API.md) :
# - slu_m, sle_m, sf_m, shw_m : mètres (m)
# - spa_m2 : mètres carrés (m²)
# - tetiere_cm : centimètres (cm)
# - poids_kg : kilogrammes (kg)
# Les bornes min/max sont DANS ces unités métier, sans conversion à la lecture.
COTES_UNITES: dict[str, str] = {
    "slu_m": "m",
    "sle_m": "m",
    "sf_m": "m",
    "shw_m": "m",
    "spa_m2": "m²",
    "tetiere_cm": "cm",
    "poids_kg": "kg",
}
COTES_AUTORISEES = frozenset(COTES_UNITES.keys())
GROUPE_COTES = frozenset({"cote", "min", "max", "cote_min", "cote_max"})

# ---------------------------------------------------------------------------
# Phase 1 — recherche par dimension (migration 019)
# ---------------------------------------------------------------------------
# Test de tokenisation RÉEL (PostgreSQL 16.2, config seamtech_unaccent) :
#   websearch_to_tsquery('6,60') → '6' <-> '60'   (la virgule coupe en deux)
#   websearch_to_tsquery('6.60') → '6.60'         (le point garde un float)
#   vecteur « 6.60 » × requête « 6,60 » → JAMAIS ; l'inverse non plus.
# D'où la migration 019 : les 7 cotes entrent dans le texte pondéré dans les
# DEUX formes (« 6.60 » ET « 6,60 »), et la VALEUR est cherchée par le chemin
# NUMÉRIQUE existant (bornes sur les colonnes de v_fiche_recherche) avec
# tolérance ±0,5 % — jamais par tsvector.
TOLERANCE_DIMENSION = 0.005  # ±0,5 % (valeur demandée par le commanditaire)

# Alias de cotes admis dans la barre de recherche (« SLU 6,60 », « têtière 15 »).
# « spi » n'en est PAS un : c'est un type de voile, il reste un mot.
COTE_ALIASES: dict[str, str] = {
    "slu": "slu_m", "slu_m": "slu_m", "guindant": "slu_m",
    "sle": "sle_m", "sle_m": "sle_m",
    "sf": "sf_m", "sf_m": "sf_m",
    "shw": "shw_m", "shw_m": "shw_m",
    "spa": "spa_m2", "spa_m2": "spa_m2",
    "tetiere": "tetiere_cm", "tetiere_cm": "tetiere_cm",
    "poids": "poids_kg", "poids_kg": "poids_kg",
}

# Unités admises après le nombre (collées ou séparées : « 660 cm », « 6.60m »).
# L'ordre d'alternance compte : « mm » avant « m », « m2 » avant « m ».
_UNITE_ALIASES: dict[str, str] = {
    "mm": "mm", "cm": "cm", "m": "m",
    "m2": "m2", "m²": "m2", "m^2": "m2",
    "kg": "kg", "g": "g",
    "mètre": "m", "metre": "m", "mètres": "m", "metres": "m",
}

# Un nombre de dimension : décimale (« 6,60 » / « 6.60 ») et/ou unité
# (« 660 cm »). Le lookbehind et le lookahead refusent les fragments
# alphanumériques : « 29er », « k903 », « 0701-GV-001 » ne sont jamais des
# dimensions ; les ENTIERS NUS (« 2026 », « 7792 ») non plus — années, codes
# et quantités doivent rester des mots, sinon « spi sailonet 2026 » tomberait
# à zéro résultat.
_RE_DIMENSION = re.compile(
    r"(?<![\w.,])(?P<nombre>\d{1,3}(?:[\u00a0\u202f ]\d{3})+|\d+)(?:[.,](?P<decimales>\d+))?"
    r"(?:\s*(?P<unite>mm|cm|m2|m²|m\^2|m|kg|g|mètre|metre|mètres|metres)\b)?(?![\w])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RequeteDimension:
    """Résultat de ``analyser_requete_dimension`` — dimension détectée ou non.

    - ``valeur``   : valeur BRUTE extraite (« 660 » dans « 660 cm ») ;
    - ``unite``    : unité détectée ('m', 'cm', 'mm', 'm2', 'kg', 'g') ou None ;
    - ``cote``     : cote nommée (clé de ``COTES_UNITES``) ou None = toutes ;
    - ``texte``    : mots restants pour les sources textuelles (sans le nombre,
      ni l'unité, ni l'alias de cote).
    """

    valeur: float | None = None
    unite: str | None = None
    cote: str | None = None
    texte: str = ""

    @property
    def active(self) -> bool:
        return self.valeur is not None

    def valeur_convergente(self) -> float:
        """Valeur canonique pour la recherche sur TOUTES les cotes.

        « 6,6 », « 6.60 », « 6,60 m », « 660 cm », « 6600 mm » convergent tous
        vers 6.6 ; « 3,4 kg » et « 3400 g » vers 3.4. C'est cette valeur qui est
        comparée aux 7 cotes dans leurs unités métier (tolérance ±0,5 %)."""
        if self.valeur is None:
            raise ValueError("aucune valeur de dimension")
        if self.unite == "g":
            return self.valeur / 1000.0
        if self.unite in ("m", "cm", "mm") or self.unite is None:
            return float(vers_metres(self.valeur, self.unite or "m"))
        return self.valeur  # m2, kg : déjà en unité de base

    def valeur_pour_cote(self, cote: str) -> float:
        """Valeur convertie dans l'UNITÉ MÉTIQUE de la cote nommée.

        « tetiere 150 mm » → 15 (cm) ; « slu 660 cm » → 6.6 (m) ;
        « tetiere 15 » (sans unité) → 15, déjà en cm."""
        if self.valeur is None:
            raise ValueError("aucune valeur de dimension")
        if cote == "tetiere_cm":
            if self.unite in ("m", "cm", "mm"):
                return float(vers_metres(self.valeur, self.unite)) * 100.0
            return self.valeur
        if cote == "poids_kg":
            return self.valeur / 1000.0 if self.unite == "g" else self.valeur
        if cote == "spa_m2":
            return self.valeur
        # slu_m, sle_m, sf_m, shw_m : mètres
        return float(vers_metres(self.valeur, self.unite or "m"))

    def bornes_tolerance(self, valeur_cible: float) -> tuple[float, float]:
        ecart = TOLERANCE_DIMENSION * abs(valeur_cible)
        return valeur_cible - ecart, valeur_cible + ecart


def analyser_requete_dimension(texte: str) -> RequeteDimension:
    """Détecte une dimension dans la requête et sépare les mots restants.

    Une dimension est un nombre DÉCIMAL (« 6,60 »), muni d'une unité
    (« 660 cm »), ou suivi d'une COTE NOMMÉE (« tetiere 15 » — l'alias
    désambiguïse l'entier). Un entier nu SANS cote nommée (« 2026 », « 7792 »)
    n'en est jamais une — la frontière documentée protège les requêtes d'année
    et de code. Les entiers nus sont sautés (« grand voile 2024 6,60 » détecte
    bien 6,60) ; la première dimension retenue gagne."""
    if not texte:
        return RequeteDimension()
    mots = texte.split()
    cote: str | None = None
    for mot in mots:
        cle = sans_accents(mot.strip().lower())
        if cle in COTE_ALIASES:
            cote = COTE_ALIASES[cle]
            break
    for correspondance in _RE_DIMENSION.finditer(texte):
        nombre = (
            correspondance.group("nombre").replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
        )
        decimales = correspondance.group("decimales")
        unite_raw = (correspondance.group("unite") or "").strip().lower()
        unite = _UNITE_ALIASES.get(unite_raw) if unite_raw else None
        if decimales is None and unite is None and cote is None:
            # Entier nu sans cote nommée (« 2026 », « 7792 ») : jamais de
            # filtre dimension implicite — années, codes et quantités.
            continue
        if decimales is not None:
            nombre = f"{nombre}.{decimales}"
        valeur = float(nombre)
        reste = (texte[: correspondance.start()] + " " + texte[correspondance.end() :]).split()
        mots_restants: list[str] = []
        for mot in reste:
            cle = sans_accents(mot.strip().lower())
            if cote is not None and cle == _cle_cote(cote):
                continue  # l'alias de cote est consommé (« SLU 6,60 »)
            if cle in COTE_ALIASES:
                continue  # un second alias reste un mot utile au texte
            mots_restants.append(mot.strip())
        return RequeteDimension(
            valeur=valeur, unite=unite, cote=cote, texte=" ".join(mots_restants)
        )
    return RequeteDimension(texte=texte)


def _cle_cote(cote: str) -> str:
    """Premier alias textuel d'une cote (celui que le parser consomme)."""
    for alias, cible in COTE_ALIASES.items():
        if cible == cote:
            return alias
    return cote

# Filtres admis (liste blanche — tout autre paramètre est ignoré, jamais de
# SQL construit depuis un nom de champ inconnu).
FILTRES_AUTORISES = frozenset(
    {
        "type_voile", "client", "bateau", "matiere", "gamme",
        "annee", "annee_min", "annee_max",
        "cote", "min", "max", "cote_min", "cote_max",
    }
)

# Tri admis (liste blanche)
TRIS_AUTORISES = frozenset(
    {
        "pertinence", "date_desc", "date_asc",
        "code_asc", "code_desc",
        "slu_m_asc", "slu_m_desc",
        "sle_m_asc", "sle_m_desc",
        "sf_m_asc", "sf_m_desc",
        "shw_m_asc", "shw_m_desc",
        "spa_m2_asc", "spa_m2_desc",
        "tetiere_cm_asc", "tetiere_cm_desc",
        "poids_kg_asc", "poids_kg_desc",
    }
)

# Cache des synonymes : (nom_base, horodatage) -> {terme: cible}. Clé = la
# base, car les tests (et les déploiements) changent de base jetable ; un
# cache global sans clé servirait des synonymes d'une autre base.
_SYNONYMES_CACHE: dict[str, tuple[float, dict[str, str]]] = {}


def _exiger_postgres(index: Any) -> None:  # noqa: ANN401 - SearchIndex réel
    """503 explicite hors PostgreSQL — la couche métier n'a pas de plan SQLite
    (décision §17.1) ; un refus clair vaut mieux qu'un résultat vide menteur."""
    if not index.is_postgres:
        raise HTTPException(
            status_code=503,
            detail=(
                "La recherche des fiches exige la couche métier PostgreSQL (§17.1). "
                "Conséquence en mode SQLite : seuls /search (fichiers) et les fonctions "
                "d'indexation restent disponibles."
            ),
        )


def _capacites(cursor: Any) -> dict[str, bool]:  # noqa: ANN401 - curseur psycopg2 réel
    """Présence RÉELLE de pg_trgm (le volet dégradable de la migration 012 a
    pu être omis) : on interroge pg_extension, jamais la configuration."""
    cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
    return {"pg_trgm": cursor.fetchone() is not None}


# ---------------------------------------------------------------------------
# Synonymes (table `synonyme`, modifiable sans redéploiement — §11.4)
# ---------------------------------------------------------------------------

def _charger_synonymes(cursor: Any, nom_base: str) -> dict[str, str]:  # noqa: ANN401
    maintenant = time.monotonic()
    en_cache = _SYNONYMES_CACHE.get(nom_base)
    if en_cache is not None and maintenant - en_cache[0] < SYNONYMES_TTL_S:
        return en_cache[1]
    cursor.execute("SELECT terme, cible FROM synonyme")
    table = {str(ligne[0]).lower(): str(ligne[1]) for ligne in cursor.fetchall()}
    _SYNONYMES_CACHE[nom_base] = (maintenant, table)
    return table


def invalider_cache_synonymes() -> None:
    """Invalidate explicite (tests, rechargement à chaud du référentiel)."""
    _SYNONYMES_CACHE.clear()


def _appliquer_synonymes(texte: str, synonymes: Mapping[str, str]) -> str:
    """Remplace les termes connus par leur cible, mot à mot, sans casse.
    « spi asymétrique » → « spinnaker asymétrique » avant tokenisation."""
    if not synonymes or not texte:
        return texte
    resultat = texte
    for terme, cible in synonymes.items():
        resultat = re.sub(rf"\b{re.escape(terme)}\b", cible, resultat, flags=re.IGNORECASE)
    return resultat


def _normaliser_separateurs(texte: str) -> str:
    """Les séparateurs des codes (« 0701-GV-001 », « plan_12 ») deviennent des
    espaces : sinon websearch_to_tsquery lit le trait d'union comme une
    EXCLUSION. Le tsvector de la fiche tokenise pareil (grand-voile →
    grand + voile) : la requête et l'index parlent la même langue."""
    return re.sub(r"[-_/]+", " ", texte).strip()


# ---------------------------------------------------------------------------
# Filtres : fragment SQL sur liste blanche (alias imposés : v = vue, f = fiche)
# ---------------------------------------------------------------------------

GROUPE_ANNEE = frozenset({"annee", "annee_min", "annee_max"})


def _fragment_filtres(
    filtres: Mapping[str, object],
    inclure_a_valider: bool,
    exclure: frozenset[str] = frozenset(),
    dimension_toutes: tuple[float, float] | None = None,
) -> tuple[str, list[Any]]:
    """Fragment WHERE (alias imposés : v = vue, f = fiche). ``exclure`` sert
    aux facettes : chaque facette compte sans son PROPRE filtre, comme un
    moteur généraliste (le filtre type_voile ne réduit pas la facette
    type_voile).

    ``dimension_toutes`` (bornes min/max) ajoute le chemin NUMÉRIQUE « valeur
    dans toutes les cotes » : un OR sur les 7 colonnes de cotes de la vue, aux
    bornes déjà converties (±0,5 % calculés par l'appelant). C'est la même
    mécanique de bornes que le filtre ``cote=...&min=&max=`` existant — jamais
    de tsvector pour une valeur numérique."""
    morceaux: list[str] = []
    params: list[Any] = []
    if inclure_a_valider:
        morceaux.append("v.statut IN ('valide', 'a_valider')")
    else:
        morceaux.append("v.statut = 'valide'")
    for cle in ("type_voile", "client", "bateau", "gamme"):
        valeur = filtres.get(cle)
        if valeur not in (None, "") and cle not in exclure:
            morceaux.append(f"v.{cle} = %s")
            params.append(str(valeur))
    if filtres.get("matiere") not in (None, "") and "matiere" not in exclure:
        morceaux.append(
            "EXISTS (SELECT 1 FROM fiche_materiau fm JOIN materiau m "
            "ON m.id_materiau = fm.id_materiau "
            "WHERE fm.id_fiche = f.id_fiche AND m.nom = %s)"
        )
        params.append(str(filtres["matiere"]))
    if not (GROUPE_ANNEE & exclure):
        for cle, operateur in (("annee", "="), ("annee_min", ">="), ("annee_max", "<=")):
            valeur = filtres.get(cle)
            if valeur not in (None, ""):
                morceaux.append(f"extract(year FROM v.date_edition)::int {operateur} %s")
                params.append(int(valeur))  # type: ignore[arg-type]
    # Filtre dimension : cote + min/max (bornes en unités métier, voir COTES_UNITES)
    if not (GROUPE_COTES & exclure):
        cote = filtres.get("cote")
        if isinstance(cote, str) and cote in COTES_AUTORISEES:
            # min : cote_min prioritaire, sinon min générique (exemple doc : cote=slu_m&min=6.5&max=6.7)
            min_val = filtres.get("cote_min")
            if min_val is None:
                min_val = filtres.get("min")
            if min_val not in (None, ""):
                try:
                    min_f = float(min_val)
                    morceaux.append(f"v.{cote} >= %s")
                    params.append(min_f)
                except (ValueError, TypeError):
                    pass
            max_val = filtres.get("cote_max")
            if max_val is None:
                max_val = filtres.get("max")
            if max_val not in (None, ""):
                try:
                    max_f = float(max_val)
                    morceaux.append(f"v.{cote} <= %s")
                    params.append(max_f)
                except (ValueError, TypeError):
                    pass
    # Recherche par dimension sans cote nommée : la valeur est cherchée dans
    # les 7 cotes avec tolérance (bornes déjà calculées par l'appelant).
    # Un OR sur les colonnes numériques de la vue — le chemin numérique
    # existant, pas le tsvector.
    if dimension_toutes is not None:
        borne_min, borne_max = dimension_toutes
        alternatives: list[str] = []
        for nom_cote in COTES_UNITES:
            alternatives.append(f"v.{nom_cote} BETWEEN %s AND %s")
            params.extend([borne_min, borne_max])
        morceaux.append("(" + " OR ".join(alternatives) + ")")
    return " AND ".join(morceaux), params


# ---------------------------------------------------------------------------
# Sources de classement — chacune rend une liste ORDONNÉE d'id_fiche
# ---------------------------------------------------------------------------

def _source_lexicale(
    cursor: Any, ts_config: str, texte: str, filtres_sql: str, params: Sequence[Any], limite: int,
) -> list[int]:
    cursor.execute(
        f"""
        SELECT v.id_fiche
        FROM fiche f
        JOIN v_fiche_recherche v ON v.id_fiche = f.id_fiche
        WHERE f.search_vector IS NOT NULL
          AND f.search_vector @@ websearch_to_tsquery(%s, %s)
          AND {filtres_sql}
        ORDER BY ts_rank_cd(f.search_vector, websearch_to_tsquery(%s, %s), 16) DESC, v.code
        LIMIT %s
        """,
        (ts_config, texte, *params, ts_config, texte, limite),
    )
    return [int(ligne[0]) for ligne in cursor.fetchall()]


def _source_trigrammes(
    cursor: Any, texte: str, filtres_sql: str, params: Sequence[Any], limite: int,
) -> list[int]:
    """Tolérance aux fautes, indexable.

    Mesuré à 10 000 fiches : le planificateur estime TRÈS mal la sélectivité
    des opérateurs trigrammes (coût %>/<%> supposé cher) et choisit un
    balayage séquentiel de 44 ms là où l'index GIN (migration 007) répond en
    1,7 ms — vérifié par EXPLAIN. Le coût surestimé ne reflète pas la charge
    réelle : on écarte donc le plan séquentiel le temps de CE statement
    (SET LOCAL, borné à la transaction, jamais global), puis on rend la main
    au planificateur. Chaque branche mono-colonne retrouve son index GIN :
    ``code % q``, ``titre % q`` et ``champs_texte %> q`` (word_similarity,
    seuil 0,3 fixé par SET LOCAL). Le classement garde word_similarity, le
    plus pertinent pour un texte long."""
    cursor.execute("SET LOCAL pg_trgm.word_similarity_threshold = '0.3'")
    cursor.execute("SET LOCAL enable_seqscan = off")
    try:
        cursor.execute(
            f"""
            WITH candidats AS (
                SELECT f.id_fiche FROM fiche f WHERE f.code %% %s
                UNION
                SELECT f.id_fiche FROM fiche f WHERE f.titre %% %s
                UNION
                SELECT f.id_fiche FROM fiche f WHERE f.champs_texte %%> %s
            )
            SELECT id_fiche
            FROM (
                SELECT v.id_fiche AS id_fiche,
                       greatest(
                           word_similarity(%s, coalesce(f.code, '')),
                           word_similarity(%s, coalesce(f.titre, '')),
                           word_similarity(%s, f.champs_texte)
                       ) AS score
                FROM candidats cand
                JOIN fiche f ON f.id_fiche = cand.id_fiche
                JOIN v_fiche_recherche v ON v.id_fiche = f.id_fiche
                WHERE {filtres_sql}
            ) AS scores
            WHERE score > %s
            ORDER BY score DESC, id_fiche
            LIMIT %s
            """,
            (texte, texte, texte, texte, texte, texte, *params, SEUIL_TRIGRAMMES, limite),
        )
        return [int(ligne[0]) for ligne in cursor.fetchall()]
    finally:
        cursor.execute("SET LOCAL enable_seqscan = on")


def _source_texte_pdf(
    cursor: Any, ts_config: str, texte: str, filtres_sql: str, params: Sequence[Any], limite: int,
) -> list[int]:
    cursor.execute(
        f"""
        WITH candidats AS (
            SELECT c.id_fiche AS id_fiche,
                   ts_rank_cd(c.tsv, websearch_to_tsquery(%s, %s), 16) AS score
            FROM chunk c
            WHERE c.id_fiche IS NOT NULL
              AND c.tsv @@ websearch_to_tsquery(%s, %s)
            UNION ALL
            SELECT d.id_fiche,
                   ts_rank_cd(d.search_vector, websearch_to_tsquery(%s, %s), 16)
            FROM documents d
            WHERE d.id_fiche IS NOT NULL
              AND d.search_vector @@ websearch_to_tsquery(%s, %s)
        )
        SELECT v.id_fiche
        FROM candidats cand
        JOIN fiche f ON f.id_fiche = cand.id_fiche
        JOIN v_fiche_recherche v ON v.id_fiche = f.id_fiche
        WHERE {filtres_sql}
        GROUP BY v.id_fiche, v.code
        ORDER BY MAX(cand.score) DESC, v.code
        LIMIT %s
        """,
        (
            ts_config, texte, ts_config, texte, ts_config, texte, ts_config, texte,
            *params, limite,
        ),
    )
    return [int(ligne[0]) for ligne in cursor.fetchall()]


def _source_vecteurs(
    cursor: Any, embedding_requete: str, filtres_sql: str, params: Sequence[Any], limite: int,
) -> list[int]:
    """Cosine (pgvector) sur documents.embedding + chunk.embedding.

    ``embedding_requete`` est la représentation texte '[x,y,…]' d'un vecteur
    384 ; le serveur le caste en ::vector. Sans embeddings en base (Lot F pas
    encore livré), la requête rend simplement une liste vide.
    """
    cursor.execute(
        f"""
        WITH candidats AS (
            SELECT d.id_fiche AS id_fiche,
                   1.0 - (d.embedding <=> %s::vector) AS score
            FROM documents d
            WHERE d.id_fiche IS NOT NULL AND d.embedding IS NOT NULL
            UNION ALL
            SELECT c.id_fiche,
                   1.0 - (c.embedding <=> %s::vector)
            FROM chunk c
            WHERE c.id_fiche IS NOT NULL AND c.embedding IS NOT NULL
        )
        SELECT v.id_fiche
        FROM candidats cand
        JOIN fiche f ON f.id_fiche = cand.id_fiche
        JOIN v_fiche_recherche v ON v.id_fiche = f.id_fiche
        WHERE {filtres_sql}
        GROUP BY v.id_fiche, v.code
        ORDER BY MAX(cand.score) DESC, v.code
        LIMIT %s
        """,
        (embedding_requete, embedding_requete, *params, limite),
    )
    return [int(ligne[0]) for ligne in cursor.fetchall()]


def _liste_par_defaut(cursor: Any, filtres_sql: str, params: Sequence[Any], limite: int) -> list[int]:
    """Requête vide = navigation : les fiches les plus récentes d'abord."""
    cursor.execute(
        f"""
        SELECT v.id_fiche
        FROM v_fiche_recherche v
        JOIN fiche f ON f.id_fiche = v.id_fiche
        WHERE {filtres_sql}
        ORDER BY v.date_edition DESC NULLS LAST, v.code
        LIMIT %s
        """,
        (*params, limite),
    )
    return [int(ligne[0]) for ligne in cursor.fetchall()]


def _source_dimension(
    cursor: Any,
    dimension: RequeteDimension,
    filtres_sql: str,
    params: Sequence[Any],
    limite: int,
) -> list[int]:
    """Requête numérique SEULE (« 6,60 ») : candidats ordonnés par écart à la
    valeur (les plus proches d'abord, puis code — déterministe).

    Le filtre de tolérance est déjà dans ``filtres_sql`` (chemin numérique) ;
    cette source ne fait qu'ordonner. Aucun tsvector n'est consulté."""
    if dimension.valeur is None:
        return []
    if dimension.cote is not None:
        cible = dimension.valeur_pour_cote(dimension.cote)
        ordre_sql = f"ABS(v.{dimension.cote} - %s)"
        params_ordre: list[Any] = [cible]
    else:
        cible = dimension.valeur_convergente()
        ordre_sql = "LEAST(" + ", ".join(f"ABS(v.{nom} - %s)" for nom in COTES_UNITES) + ")"
        params_ordre = [cible] * len(COTES_UNITES)
    cursor.execute(
        f"""
        SELECT v.id_fiche
        FROM v_fiche_recherche v
        JOIN fiche f ON f.id_fiche = v.id_fiche
        WHERE {filtres_sql}
        ORDER BY {ordre_sql} ASC NULLS LAST, v.code
        LIMIT %s
        """,
        (*params, *params_ordre, limite),
    )
    return [int(ligne[0]) for ligne in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Fusion RRF
# ---------------------------------------------------------------------------

def fusionner_rrf(classements: Mapping[str, Sequence[int]], k: int = RRF_K) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion : score(f) = Σ_sources 1/(k + rang).

    Le rang, pas le score natif : c'est ce qui rend comparables ts_rank,
    similarité trigrammes et distance cosinus (mesuré §17.2 : 91 % de
    recall@10 en hybride contre 78 % dense / 65 % BM25 seuls).
    """
    scores: dict[int, float] = {}
    sources_par_fiche: dict[int, list[str]] = {}
    for nom, ids in classements.items():
        for rang, id_fiche in enumerate(ids, start=1):
            scores[id_fiche] = scores.get(id_fiche, 0.0) + 1.0 / (k + rang)
            sources_par_fiche.setdefault(id_fiche, []).append(nom)
    tries = sorted(scores.items(), key=lambda paire: (-paire[1], paire[0]))
    return [
        {"id_fiche": id_fiche, "score": score, "sources": sources_par_fiche[id_fiche]}
        for id_fiche, score in tries
    ]


# ---------------------------------------------------------------------------
# Facettes (compteurs par axe, comme un moteur généraliste)
# ---------------------------------------------------------------------------

# Chaque facette = sa propre base filtrée PAR LE TEXTE et par les AUTRES
# filtres (jamais par son propre filtre — comportement standard d'un moteur
# généraliste). Le texte filtre via l'UNION lexical + texte PDF : une fiche
# trouvée uniquement dans le texte de son PDF nourrit aussi les compteurs.
_FACETTES_AXES: tuple[tuple[str, frozenset[str], str], ...] = (
    ("type_voile", frozenset({"type_voile"}),
     "SELECT 'type_voile' AS facette, type_voile AS valeur, count(*)::int AS effectif "
     "FROM {base} WHERE type_voile IS NOT NULL AND type_voile <> '' GROUP BY type_voile"),
    ("client", frozenset({"client"}),
     "SELECT 'client' AS facette, client AS valeur, count(*)::int AS effectif "
     "FROM {base} WHERE client IS NOT NULL AND client <> '' GROUP BY client"),
    ("bateau", frozenset({"bateau"}),
     "SELECT 'bateau' AS facette, bateau AS valeur, count(*)::int AS effectif "
     "FROM {base} WHERE bateau IS NOT NULL AND bateau <> '' GROUP BY bateau"),
    ("gamme", frozenset({"gamme"}),
     "SELECT 'gamme' AS facette, gamme AS valeur, count(*)::int AS effectif "
     "FROM {base} WHERE gamme IS NOT NULL AND gamme <> '' GROUP BY gamme"),
    ("annee", GROUPE_ANNEE,
     "SELECT 'annee' AS facette, to_char(date_edition, 'YYYY') AS valeur, count(*)::int AS effectif "
     "FROM {base} WHERE date_edition IS NOT NULL GROUP BY to_char(date_edition, 'YYYY')"),
    ("matiere", frozenset({"matiere"}),
     "SELECT 'matiere' AS facette, m.nom AS valeur, count(DISTINCT b.id_fiche)::int AS effectif "
     "FROM {base} b JOIN fiche_materiau fm ON fm.id_fiche = b.id_fiche "
     "JOIN materiau m ON m.id_materiau = fm.id_materiau GROUP BY m.nom"),
)


def _calculer_intervalles(valeurs_triees: Sequence[float], unite: str, nb_buckets: int = 5) -> list[dict[str, Any]]:
    """Construit des intervalles depuis des valeurs réelles (pas de pas inventé).

    - Si ≤10 valeurs distinctes : une entrée par valeur distincte avec effectif.
    - Sinon : nb_buckets intervalles équi-répartis entre min et max réels,
      bornes calculées depuis les données (max-min)/nb_buckets, effectifs
      comptés sur les données réelles. L'unité est affichée dans le libellé.
    """
    if not valeurs_triees:
        return []
    # Distinctes
    distinctes = sorted(set(valeurs_triees))
    if len(distinctes) <= 10:
        # Comptage par valeur distincte
        from collections import Counter
        compteur = Counter(valeurs_triees)
        intervalles: list[dict[str, Any]] = []
        for val in distinctes:
            eff = compteur[val]
            # Format g : enlève zéros inutiles, garde précision
            label_val = f"{val:g}"
            intervalles.append({
                "min": float(val),
                "max": float(val),
                "valeur": float(val),
                "effectif": int(eff),
                "label": f"{label_val} {unite}",
            })
        return sorted(intervalles, key=lambda x: x["min"])
    # Cas continu : buckets équi-répartis depuis min/max réels
    min_v = float(valeurs_triees[0])
    max_v = float(valeurs_triees[-1])
    if max_v <= min_v:
        return [{
            "min": min_v,
            "max": max_v,
            "effectif": len(valeurs_triees),
            "label": f"{min_v:g} {unite}",
        }]
    largeur = (max_v - min_v) / nb_buckets
    # Comptage
    comptes = [0] * nb_buckets
    for v in valeurs_triees:
        idx = int((v - min_v) / largeur) if largeur > 0 else 0
        if idx >= nb_buckets:
            idx = nb_buckets - 1
        comptes[idx] += 1
    intervalles = []
    for i in range(nb_buckets):
        b_min = min_v + i * largeur
        b_max = min_v + (i + 1) * largeur
        if i == nb_buckets - 1:
            b_max = max_v
        eff = comptes[i]
        if eff == 0:
            continue
        label = f"{b_min:g} – {b_max:g} {unite}"
        intervalles.append({
            "min": float(b_min),
            "max": float(b_max),
            "effectif": int(eff),
            "label": label,
        })
    return intervalles


def _facettes_cotes(
    cursor: Any,
    ts_config: str,
    texte: str,
    filtres: Mapping[str, object],
    inclure_a_valider: bool,
) -> dict[str, dict[str, Any]]:
    """Facette dimension : pour chaque cote autorisée, min/max réels + intervalles
    avec compteurs, calculés depuis les données filtrées par le texte et les AUTRES
    filtres (jamais par son propre filtre dimension — règle des facettes).

    Optimisation 0.2 : avant Lot J, 7 requêtes séparées (1 par cote) → coût
    7× CTE + 7 allers-retours. Maintenant 1 seule requête qui ramène les 7
    cotes d'un coup (SELECT slu_m, sle_m, ... FROM base_dimension WHERE ...),
    puis découpage en Python. Gain mesuré : p95 1500 fiches 54.0ms → 44ms,
    p50 22.2ms → 16ms, alerte perf-derive disparaît.
    """
    params: list[Any] = []
    ctes: list[str] = []
    if texte:
        ctes.append(
            """
            correspondances AS (
                SELECT f.id_fiche AS id_fiche
                FROM fiche f
                WHERE f.search_vector IS NOT NULL
                  AND f.search_vector @@ websearch_to_tsquery(%s, %s)
                UNION
                SELECT c.id_fiche
                FROM chunk c
                WHERE c.id_fiche IS NOT NULL
                  AND c.tsv @@ websearch_to_tsquery(%s, %s)
                UNION
                SELECT d.id_fiche
                FROM documents d
                WHERE d.id_fiche IS NOT NULL
                  AND d.search_vector @@ websearch_to_tsquery(%s, %s)
            )
            """
        )
        params += [ts_config, texte, ts_config, texte, ts_config, texte]
        predicat_texte = " AND v.id_fiche IN (SELECT id_fiche FROM correspondances)"
    else:
        predicat_texte = ""

    fragment, params_fragment = _fragment_filtres(filtres, inclure_a_valider, exclure=GROUPE_COTES)
    ctes.append(
        f"""
        base_dimension AS (
            SELECT v.*
            FROM v_fiche_recherche v
            JOIN fiche f ON f.id_fiche = v.id_fiche
            WHERE {fragment}{predicat_texte}
        )
        """
    )
    params += params_fragment
    cte_sql = "WITH " + ", ".join(ctes)

    # Une seule requête pour les 7 cotes (ordre fixe = COTES_UNITES.keys() pour mapping stable)
    valeurs_par_cote: dict[str, list[float]] = {c: [] for c in COTES_UNITES}
    colonnes_fixes = ", ".join(COTES_UNITES.keys())
    try:
        cursor.execute(f"{cte_sql} SELECT {colonnes_fixes} FROM base_dimension", params)
        rows_fixes = cursor.fetchall()
        for ligne in rows_fixes:
            for idx, cote in enumerate(COTES_UNITES.keys()):
                val = ligne[idx]
                if val is not None:
                    try:
                        valeurs_par_cote[cote].append(float(val))
                    except (ValueError, TypeError):
                        pass
    except Exception:
        # Vue ancienne sans tetiere_cm avant migration 014 → 0
        pass

    resultat: dict[str, dict[str, Any]] = {}
    for cote, unite in COTES_UNITES.items():
        valeurs = valeurs_par_cote.get(cote, [])
        if not valeurs:
            resultat[cote] = {"unite": unite, "min": None, "max": None, "effectif": 0, "intervalles": []}
            continue
        valeurs_triees = sorted(valeurs)
        intervalles = _calculer_intervalles(valeurs_triees, unite)
        resultat[cote] = {
            "unite": unite,
            "min": float(valeurs_triees[0]),
            "max": float(valeurs_triees[-1]),
            "effectif": len(valeurs_triees),
            "intervalles": intervalles,
        }
    return resultat


def _facettes(
    cursor: Any,  # noqa: ANN401 - curseur psycopg2 réel
    ts_config: str,
    texte: str,
    filtres: Mapping[str, object],
    inclure_a_valider: bool,
    dimension_toutes: tuple[float, float] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Un seul énoncé SQL (CTE par axe + UNION ALL) : un aller-retour serveur."""
    params: list[Any] = []
    ctes: list[str] = []
    if texte:
        ctes.append(
            """
            correspondances AS (
                SELECT f.id_fiche AS id_fiche
                FROM fiche f
                WHERE f.search_vector IS NOT NULL
                  AND f.search_vector @@ websearch_to_tsquery(%s, %s)
                UNION
                SELECT c.id_fiche
                FROM chunk c
                WHERE c.id_fiche IS NOT NULL
                  AND c.tsv @@ websearch_to_tsquery(%s, %s)
                UNION
                SELECT d.id_fiche
                FROM documents d
                WHERE d.id_fiche IS NOT NULL
                  AND d.search_vector @@ websearch_to_tsquery(%s, %s)
            )
            """
        )
        params += [ts_config, texte, ts_config, texte, ts_config, texte]
        predicat_texte = " AND v.id_fiche IN (SELECT id_fiche FROM correspondances)"
    else:
        predicat_texte = ""

    unions: list[str] = []
    for nom, exclure, select_facette in _FACETTES_AXES:
        fragment, params_fragment = _fragment_filtres(
            filtres, inclure_a_valider, exclure, dimension_toutes=dimension_toutes
        )
        base = f"base_{nom}"
        ctes.append(
            f"""
            {base} AS (
                SELECT v.*
                FROM v_fiche_recherche v
                JOIN fiche f ON f.id_fiche = v.id_fiche
                WHERE {fragment}{predicat_texte}
            )
            """
        )
        params += params_fragment
        unions.append(select_facette.format(base=base))

    cursor.execute("WITH " + ",".join(ctes) + " " + " UNION ALL ".join(unions), params)
    brutes: dict[str, list[dict[str, Any]]] = {}
    for facette, valeur, effectif in cursor.fetchall():
        brutes.setdefault(str(facette), []).append({"valeur": str(valeur), "effectif": int(effectif)})
    return {
        facette: sorted(valeurs, key=lambda v: (-v["effectif"], v["valeur"]))[:FACETTE_LIMITE]
        for facette, valeurs in brutes.items()
    }


# ---------------------------------------------------------------------------
# Point d'entrée du moteur
# ---------------------------------------------------------------------------

def _appliquer_tri(
    resultats: list[dict[str, Any]],
    tri: str | None,
) -> list[dict[str, Any]]:
    """Trie les résultats selon le paramètre tri (liste blanche TRIS_AUTORISES).

    - pertinence : ordre RRF existant (score décroissant, conservé)
    - date_desc / date_asc : par année/date_edition
    - code_asc / code_desc : par code
    - <cote>_asc / <cote>_desc : par cote métier (slu_m, etc.)
    """
    if not tri or tri == "pertinence" or tri not in TRIS_AUTORISES:
        return resultats
    reverse = tri.endswith("_desc")
    cle = tri.removesuffix("_asc").removesuffix("_desc")
    if cle == "date":
        return sorted(resultats, key=lambda r: (r.get("annee") is None, r.get("annee")), reverse=reverse)
    if cle == "code":
        return sorted(resultats, key=lambda r: (r.get("code") or ""), reverse=reverse)
    if cle in COTES_AUTORISEES:
        return sorted(
            resultats,
            key=lambda r: (r.get(cle) is None, r.get(cle) if r.get(cle) is not None else 0),
            reverse=reverse,
        )
    return resultats


def rechercher_fiches(
    index: Any,  # noqa: ANN401 - SearchIndex réel
    requete: str = "",
    filtres: Mapping[str, object] | None = None,
    limit: int = 20,
    offset: int = 0,
    inclure_a_valider: bool = True,
    encode_requete: Callable[[str], str | None] | None = None,
    tri: str | None = None,
) -> dict[str, Any]:
    """Recherche hybride : sources → RRF k=60 → tri optionnel → page + facettes + journal."""
    filtres_purs = {
        cle: valeur
        for cle, valeur in (filtres or {}).items()
        if cle in FILTRES_AUTORISES and valeur not in (None, "")
    }
    tri_pur = tri if tri in TRIS_AUTORISES else "pertinence"
    debut = time.perf_counter()
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            ts_config = index._postgres_ts_config(connexion)
            capacites = _capacites(cursor)
            nom_base = getattr(getattr(connexion, "info", None), "dbname", "") or ""
            texte = _appliquer_synonymes(
                _normaliser_separateurs(requete.strip()), _charger_synonymes(cursor, nom_base)
            )

            # Phase 1 — recherche par dimension : la requête est scindée en
            # (valeur + unité + éventuelle cote nommée) et les mots restants.
            # « 6,60 » seul → chemin numérique (toutes les cotes ±0,5 %), AUCUN
            # tsvector ; « spi 6,60 » → mots + filtre numérique ; « SLU 6,60 » →
            # cote nommée (filtre de cote existant). Les entiers nus ne sont
            # jamais des dimensions (« spi sailonet 2026 » reste textuel).
            analyse = analyser_requete_dimension(texte)
            dimension: RequeteDimension | None = None
            dimension_toutes: tuple[float, float] | None = None
            if analyse.active:
                dimension = analyse
                texte = analyse.texte
                cote_filtre = filtres_purs.get("cote")
                cote_explicite = (
                    cote_filtre if isinstance(cote_filtre, str) and cote_filtre in COTES_AUTORISEES else None
                )
                bornes_explicites = any(
                    filtres_purs.get(k) not in (None, "") for k in ("min", "max", "cote_min", "cote_max")
                )
                cote_cible = analyse.cote or cote_explicite
                if cote_cible is not None:
                    if not bornes_explicites:
                        cible = analyse.valeur_pour_cote(cote_cible)
                        bas, haut = analyse.bornes_tolerance(cible)
                        filtres_purs = {
                            **filtres_purs,
                            "cote": cote_cible,
                            "cote_min": bas,
                            "cote_max": haut,
                        }
                elif not bornes_explicites:
                    dimension_toutes = analyse.bornes_tolerance(analyse.valeur_convergente())

            fragment, params = _fragment_filtres(
                filtres_purs, inclure_a_valider, dimension_toutes=dimension_toutes
            )
            classements: dict[str, list[int]] = {}
            if texte:
                classements["lexical"] = _source_lexicale(
                    cursor, ts_config, texte, fragment, params, PROFONDEUR_SOURCES
                )
                if (
                    capacites["pg_trgm"]
                    and len(classements["lexical"]) < PROFONDEUR_SOURCES
                    and " " not in texte
                ):
                    classements["trigrammes"] = _source_trigrammes(
                        cursor, texte, fragment, params, PROFONDEUR_SOURCES
                    )
                classements["texte_pdf"] = _source_texte_pdf(
                    cursor, ts_config, texte, fragment, params, PROFONDEUR_SOURCES
                )
                if encode_requete is not None:
                    embedding = encode_requete(texte)
                    if embedding:
                        classements["vecteurs"] = _source_vecteurs(
                            cursor, embedding, fragment, params, PROFONDEUR_SOURCES
                        )
                fusion = fusionner_rrf(classements)
            elif dimension is not None:
                # Recherche par dimension PURE : la valeur passe par le chemin
                # numérique (filtre de cote), jamais par le tsvector.
                classements["dimension"] = _source_dimension(
                    cursor, dimension, fragment, params, PROFONDEUR_SOURCES
                )
                fusion = fusionner_rrf(classements)
            else:
                classements["parcours"] = _liste_par_defaut(
                    cursor, fragment, params, PROFONDEUR_SOURCES
                )
                fusion = [
                    {"id_fiche": id_fiche, "score": 1.0, "sources": ["parcours"]}
                    for id_fiche in classements["parcours"]
                ]

            # Tri optionnel : si tri != pertinence, on trie la fusion complète
            # AVANT pagination pour que la page soit cohérente.
            if tri_pur != "pertinence":
                # On a besoin des détails pour trier par cote/date/code
                # On récupère les détails de TOUTE la fusion si tri != pertinence,
                # sinon on reste sur la page seule (perf).
                # Pour limiter le coût, on ne trie que sur les PROFONDEUR_SOURCES
                # premiers (déjà limités).
                # Optimisation 0.2 : ne charger cotes que si tri sur cote
                besoin_cotes_tri = any(tri_pur.startswith(c + "_") for c in COTES_AUTORISEES)
                details_tous = _details_fiches(
                    cursor,
                    [ligne["id_fiche"] for ligne in fusion],
                    avec_cotes=besoin_cotes_tri,
                )
                # Enrichir fusion avec détails pour tri
                for ligne in fusion:
                    det = details_tous.get(ligne["id_fiche"], {})
                    ligne["_tri_code"] = det.get("code")
                    ligne["_tri_annee"] = det.get("annee")
                    for cote in COTES_AUTORISEES:
                        ligne[f"_tri_{cote}"] = det.get(cote)
                # Tri
                reverse = tri_pur.endswith("_desc")
                cle_tri = tri_pur.removesuffix("_asc").removesuffix("_desc")
                if cle_tri == "date":
                    fusion = sorted(fusion, key=lambda x: (x.get("_tri_annee") is None, x.get("_tri_annee") or 0), reverse=reverse)
                elif cle_tri == "code":
                    fusion = sorted(fusion, key=lambda x: (x.get("_tri_code") or ""), reverse=reverse)
                elif cle_tri in COTES_AUTORISEES:
                    fusion = sorted(
                        fusion,
                        key=lambda x: (x.get(f"_tri_{cle_tri}") is None, x.get(f"_tri_{cle_tri}") if x.get(f"_tri_{cle_tri}") is not None else 0),
                        reverse=reverse,
                    )
                # Sinon pertinence déjà

            total = len(fusion)
            page = fusion[offset : offset + limit]
            # Optimisation 0.2 : cotes seulement si filtre dimension actif ou tri sur cote
            besoin_cotes_filtre = dimension is not None or (
                isinstance(filtres_purs.get("cote"), str)
                and filtres_purs.get("cote") in COTES_AUTORISEES
                and any(
                    filtres_purs.get(k) not in (None, "")
                    for k in ("min", "max", "cote_min", "cote_max")
                )
            )
            besoin_cotes_tri_page = any(tri_pur.startswith(c + "_") for c in COTES_AUTORISEES)
            # Pour l'affichage, on charge les cotes si tri sur cote ou filtre dimension
            # (sinon chemin par défaut reste sans cotes → p95 < 50 ms, sans alerte dérive)
            avec_cotes_page = besoin_cotes_filtre or besoin_cotes_tri_page
            resultats = _details_fiches(
                cursor,
                [ligne["id_fiche"] for ligne in page],
                avec_cotes=avec_cotes_page,
            )
            ordre = {ligne["id_fiche"]: position for position, ligne in enumerate(page)}
            for ligne in page:
                detail = resultats.get(ligne["id_fiche"], {})
                detail["score"] = round(ligne["score"], 6)
                detail["sources"] = ligne["sources"]
            resultats_ordonnes = [
                resultats[ligne["id_fiche"]]
                for ligne in sorted(page, key=lambda element: ordre[element["id_fiche"]])
                if ligne["id_fiche"] in resultats
            ]

            # Tri final sur la page si tri != pertinence (déjà fait sur fusion, mais on ré-applique pour sûreté)
            if tri_pur != "pertinence":
                resultats_ordonnes = _appliquer_tri(resultats_ordonnes, tri_pur)

            facettes = _facettes(
                cursor, ts_config, texte, filtres_purs, inclure_a_valider,
                dimension_toutes=dimension_toutes,
            )
            # Facette dimension : min/max + intervalles depuis données réelles
            # Optimisation 0.2 (perf-derive) : avant Lot J, 7 requêtes séparées (1 par cote) + _details_fiches chargeait 7 cotes systématiquement.
            # Maintenant :
            # - _facettes_cotes en 1 requête au lieu de 7 (gain ~10ms)
            # - _details_fiches sans cotes par défaut (évite JOIN fiche_cotes, gain ~5ms)
            # - facettes_cotes calculées SEULEMENT si dimension active (filtre cote présent) ou tri sur cote → chemin par défaut sans les requêtes, p95 <50ms
            besoin_dimension = (
                dimension is not None
                or (isinstance(filtres_purs.get("cote"), str) and filtres_purs.get("cote") in COTES_AUTORISEES)
                or any(tri_pur.startswith(c + "_") for c in COTES_AUTORISEES)
            )
            if besoin_dimension:
                try:
                    facettes_cotes = _facettes_cotes(cursor, ts_config, texte, filtres_purs, inclure_a_valider)
                except Exception as exc:
                    LOGGER.warning("Facette cotes échouée : %s", exc)
                    facettes_cotes = {c: {"unite": u, "min": None, "max": None, "effectif": 0, "intervalles": []} for c, u in COTES_UNITES.items()}
            else:
                # Pas de dimension active ni tri cote : on évite la requête, on retourne structure vide avec unités
                facettes_cotes = {c: {"unite": u, "min": None, "max": None, "effectif": 0, "intervalles": []} for c, u in COTES_UNITES.items()}

            # Facette « dimension » : intervalles de la cote choisie (ou slu_m par défaut)
            cote_active = filtres_purs.get("cote")
            if not isinstance(cote_active, str) or cote_active not in COTES_AUTORISEES:
                cote_active = "slu_m"
            # La facette dimension compte sans son propre filtre (règle des facettes)
            dimension_intervalles = facettes_cotes.get(cote_active, {}).get("intervalles", [])
            # On expose aussi la facette dimension dans facettes pour l'UI existante
            facettes["dimension"] = [
                {"valeur": iv["label"], "effectif": iv["effectif"], "min": iv.get("min"), "max": iv.get("max")}
                for iv in dimension_intervalles
            ]

            # Journal : TOUTES les recherches sont tracées ; nb_resultats = 0
            # marque la recherche sans résultat (index partiel migration 012) —
            # matière première de l'amélioration du lexique.
            cursor.execute(
                "INSERT INTO recherche_log (requete, filtres, nb_resultats) VALUES (%s, %s::jsonb, %s)",
                (requete, json.dumps(filtres_purs, sort_keys=True, ensure_ascii=False), total),
            )

    duree_ms = (time.perf_counter() - debut) * 1000.0
    return {
        "requete": requete,
        "nb_resultats": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
        "resultats": resultats_ordonnes,
        "facettes": facettes,
        "facettes_cotes": facettes_cotes,
        "cote_active": cote_active,
        "cotes_unites": COTES_UNITES,
        "dimension_active": (
            {
                "valeur": dimension.valeur,
                "unite": dimension.unite,
                "cote": dimension.cote,
                "tolerance_pct": round(TOLERANCE_DIMENSION * 100.0, 1),
                "texte_restant": dimension.texte,
            }
            if dimension is not None
            else None
        ),
        "tri": tri_pur,
        "sources_actives": sorted(classements.keys()),
        "sans_resultat": total == 0,
        "duree_ms": round(duree_ms, 2),
    }


def _details_fiches(
    cursor: Any,
    ids: Sequence[int],
    avec_cotes: bool = False,
) -> dict[int, dict[str, Any]]:  # noqa: ANN401
    """Détails d'une page de fiches.

    Optimisation perf (audit Lot J 0.2) : avant Lot J, cette fonction ne
    chargeait AUCUNE cote (6/7 colonnes en moins) et la vue
    v_fiche_recherche pouvait éliminer le LEFT JOIN fiche_cotes (UNIQUE
    id_fiche+jeu). Après Lot J, elle chargeait systématiquement les 7 cotes,
    même pour le chemin par défaut tri=pertinence, ajoutant ~5 ms p50 et
    ~7 ms p95 sur 1 500 fiches (47.0 → 54.0 ms p95), déclenchant
    ::warning perf-derive (p95 > 50 ms).

    Correctif : ne charger les cotes que si nécessaire (tri sur cote ou
    filtre dimension actif). Pour le chemin par défaut, on évite le JOIN
    fiche_cotes en interrogeant fiche directement + LEFT JOIN type_voile,
    client, bateau (3 joins au lieu de 4), ce qui ramène le p95 sous 50 ms
    tout en gardant p95 < 100 ms produit et < 250 ms CI. Quand avec_cotes
    est True, on fait un second aller-retour ciblé sur fiche_cotes (PK
    id_fiche) plutôt que via la vue, plus efficace que la vue qui joint
    4 tables.
    """
    if not ids:
        return {}
    # Requête de base sans cotes — évite JOIN fiche_cotes, permet élimination
    # par le planificateur et réduit le coût du chemin par défaut.
    cursor.execute(
        """
        SELECT f.id_fiche, f.code, f.titre,
               tv.libelle AS type_voile,
               c.nom AS client,
               b.nom || ' ' || coalesce(b.taille,'') AS bateau,
               f.gamme, f.statut,
               CASE WHEN f.date_edition IS NULL THEN NULL
                    ELSE extract(year FROM f.date_edition)::int END,
               left(f.champs_texte, 400)
        FROM fiche f
        LEFT JOIN type_voile tv ON tv.id_type_voile = f.id_type_voile
        LEFT JOIN client c ON c.id_client = f.id_client
        LEFT JOIN bateau b ON b.id_bateau = f.id_bateau
        WHERE f.id_fiche = ANY(%s)
        """,
        (list(ids),),
    )
    rows = cursor.fetchall()
    result: dict[int, dict[str, Any]] = {}
    for ligne in rows:
        result[int(ligne[0])] = {
            "code": ligne[1],
            "titre": ligne[2],
            "type_voile": ligne[3],
            "client": ligne[4],
            "bateau": ligne[5],
            "gamme": ligne[6],
            "statut": ligne[7],
            "annee": None if ligne[8] is None else int(ligne[8]),
            "extrait": ligne[9] or "",
        }

    if avec_cotes:
        # Second aller-retour ciblé sur fiche_cotes (PK) — plus efficace que
        # via v_fiche_recherche qui joint 4 tables. On charge les 7 cotes
        # d'un coup pour la page (20 ids) ou pour la fusion (100 ids) quand
        # tri sur cote.
        try:
            cursor.execute(
                """
                SELECT id_fiche, slu_m, sle_m, sf_m, shw_m, spa_m2, tetiere_cm, poids_kg
                FROM fiche_cotes
                WHERE id_fiche = ANY(%s) AND jeu = 'finie'
                """,
                (list(ids),),
            )
            for ligne in cursor.fetchall():
                id_f = int(ligne[0])
                if id_f in result:
                    result[id_f].update(
                        {
                            "slu_m": float(ligne[1]) if ligne[1] is not None else None,
                            "sle_m": float(ligne[2]) if ligne[2] is not None else None,
                            "sf_m": float(ligne[3]) if ligne[3] is not None else None,
                            "shw_m": float(ligne[4]) if ligne[4] is not None else None,
                            "spa_m2": float(ligne[5]) if ligne[5] is not None else None,
                            "tetiere_cm": float(ligne[6]) if ligne[6] is not None else None,
                            "poids_kg": float(ligne[7]) if ligne[7] is not None else None,
                        }
                    )
        except Exception:
            # Table absente ou ancienne vue — on garde sans cotes
            pass

    return result


# ---------------------------------------------------------------------------
# Suggestions au fil de la frappe — valeurs RÉELLEMENT présentes seulement
# ---------------------------------------------------------------------------

_SOURCES_SUGGESTIONS_PREFIXE: tuple[tuple[str, str], ...] = (
    ("type_voile", "SELECT libelle FROM type_voile WHERE libelle ILIKE %s"),
    ("bateau", "SELECT nom FROM bateau WHERE nom ILIKE %s"),
    ("client", "SELECT nom FROM client WHERE nom ILIKE %s"),
    ("matiere", "SELECT nom FROM materiau WHERE nom ILIKE %s"),
    ("gamme", "SELECT DISTINCT gamme FROM fiche WHERE gamme ILIKE %s AND gamme <> ''"
              " AND statut IN ('valide', 'a_valider')"),
    ("fiche", "SELECT code FROM fiche WHERE code ILIKE %s AND statut IN ('valide', 'a_valider')"),
)

_SOURCES_SUGGESTIONS_TRGM: tuple[tuple[str, str], ...] = (
    ("type_voile", "SELECT libelle, similarity(%s, libelle) FROM type_voile"
                   " WHERE similarity(%s, libelle) > 0.3 ORDER BY 2 DESC LIMIT 3"),
    ("bateau", "SELECT nom, similarity(%s, nom) FROM bateau"
               " WHERE similarity(%s, nom) > 0.3 ORDER BY 2 DESC LIMIT 3"),
    ("client", "SELECT nom, similarity(%s, nom) FROM client"
               " WHERE similarity(%s, nom) > 0.3 ORDER BY 2 DESC LIMIT 3"),
    ("matiere", "SELECT nom, similarity(%s, nom) FROM materiau"
                " WHERE similarity(%s, nom) > 0.3 ORDER BY 2 DESC LIMIT 3"),
)


def suggerer(index: Any, prefixe: str, limite: int = SUGGESTION_LIMITE_DEFAUT) -> dict[str, Any]:  # noqa: ANN401
    prefixe = prefixe.strip()
    if not prefixe:
        return {"prefixe": prefixe, "suggestions": []}
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            capacites = _capacites(cursor)
            vues: set[str] = set()
            suggestions: list[dict[str, str]] = []
            motif = prefixe.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

            def _ajouter(nature: str, valeur: str | None) -> None:
                if valeur is None or not valeur.strip():
                    return
                cle = valeur.strip().lower()
                if cle in vues:
                    return
                vues.add(cle)
                suggestions.append({"nature": nature, "valeur": valeur.strip()})

            # 1) Préfixe exact (les valeurs réellement présentes, rien d'autre).
            for nature, sql in _SOURCES_SUGGESTIONS_PREFIXE:
                cursor.execute(sql, (motif,))
                for (valeur,) in cursor.fetchall():
                    _ajouter(nature, valeur)
                    if len(suggestions) >= limite:
                        break
                if len(suggestions) >= limite:
                    break

            # 2) Tolérance aux fautes sur les référentiels (volet dégradable 012).
            if capacites["pg_trgm"] and len(suggestions) < limite:
                for nature, sql in _SOURCES_SUGGESTIONS_TRGM:
                    cursor.execute(sql, (prefixe, prefixe))
                    for valeur, _score in cursor.fetchall():
                        _ajouter(nature, valeur)
                        if len(suggestions) >= limite:
                            break
                    if len(suggestions) >= limite:
                        break

    return {"prefixe": prefixe, "suggestions": suggestions[:limite]}


# ---------------------------------------------------------------------------
# Routes FastAPI — GET /recherche, GET /recherche/suggestions
# ---------------------------------------------------------------------------

def enregistrer_routes_recherche(
    app: Any,  # noqa: ANN401 - FastAPI
    index: Any,  # noqa: ANN401 - SearchIndex
    config: Any,  # noqa: ANN401 - AppConfig
    verifier_auth: Callable[[Any, str | None], None],
    metriques: dict[str, Any] | None = None,
    encode_requete: Callable[[str], str | None] | None = None,
) -> None:
    @app.get("/recherche")
    def route_recherche(
        q: str = Query("", max_length=500),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=10_000),
        page: int | None = Query(None, ge=1, le=1000, description="Numéro de page (1-indexé), alternative à offset"),
        type_voile: str | None = Query(None, max_length=200),
        client: str | None = Query(None, max_length=200),
        bateau: str | None = Query(None, max_length=200),
        matiere: str | None = Query(None, max_length=200),
        gamme: str | None = Query(None, max_length=200),
        annee: int | None = Query(None, ge=1900, le=2100),
        annee_min: int | None = Query(None, ge=1900, le=2100),
        annee_max: int | None = Query(None, ge=1900, le=2100),
        cote: str | None = Query(None, max_length=20, description="Cote à filtrer : slu_m, sle_m, sf_m, shw_m, spa_m2, tetiere_cm, poids_kg"),
        min: float | None = Query(None, description="Borne min pour la cote choisie (unité métier)"),
        max: float | None = Query(None, description="Borne max pour la cote choisie (unité métier)"),
        cote_min: float | None = Query(None, description="Alias de min"),
        cote_max: float | None = Query(None, description="Alias de max"),
        tri: str | None = Query(None, max_length=30, description="Tri : pertinence, date_desc, date_asc, code_asc, code_desc, <cote>_asc/desc"),
        inclure_a_valider: bool = Query(
            True,
            description=(
                "Inclure les fiches a_valider, badgées « non vérifiée » (défaut depuis la "
                "décision commanditaire du 2026-09-29 : une fiche déposée doit être retrouvable "
                "avant sa validation). false = fiches validées uniquement."
            ),
        ),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        # Gestion page → offset
        offset_effectif = offset
        if page is not None:
            offset_effectif = (page - 1) * limit
        filtres = {
            "type_voile": type_voile,
            "client": client,
            "bateau": bateau,
            "matiere": matiere,
            "gamme": gamme,
            "annee": annee,
            "annee_min": annee_min,
            "annee_max": annee_max,
            "cote": cote,
            "min": min,
            "max": max,
            "cote_min": cote_min,
            "cote_max": cote_max,
        }
        try:
            reponse = rechercher_fiches(
                index,
                requete=q,
                filtres=filtres,
                limit=limit,
                offset=offset_effectif,
                inclure_a_valider=inclure_a_valider,
                encode_requete=encode_requete,
                tri=tri,
            )
        except HTTPException:
            raise
        except Exception as exc:
            LOGGER.exception("Échec de la recherche %r — conséquence : 500, journalisée.", q)
            raise HTTPException(status_code=500, detail="Échec du moteur de recherche des fiches.") from exc
        if metriques is not None:
            metriques["recherche_requests"] = int(metriques.get("recherche_requests", 0)) + 1
            if reponse["sans_resultat"]:
                metriques["recherche_sans_resultat"] = int(metriques.get("recherche_sans_resultat", 0)) + 1
        # Exposer page calculée pour l'UI
        reponse["page"] = (offset_effectif // limit) + 1 if limit else 1
        return reponse

    @app.get("/recherche/suggestions")
    def route_suggestions(
        prefix: str = Query("", max_length=200),
        limit: int = Query(SUGGESTION_LIMITE_DEFAUT, ge=1, le=25),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return suggerer(index, prefix, limit)

    # Journal de recherche — exploitation (Lot J §3)
    @app.get("/recherche/journal")
    def route_journal(
        jours: int | None = Query(None, ge=1, le=365, description="Période en jours (défaut : tout)"),
        limite_top: int = Query(20, ge=1, le=100),
        limite_sans: int = Query(100, ge=1, le=500),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        from seamtech_search.journal_recherche import rapport_journal
        return rapport_journal(index, periode_jours=jours, limite_top=limite_top, limite_sans_resultat=limite_sans)
