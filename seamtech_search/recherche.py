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
                  remplie à la VALIDATION) ;
- ``trigrammes``: tolérance aux fautes (pg_trgm, ``word_similarity``) — volet
                  dégradable de la migration 012 ;
- ``texte_pdf`` : texte des chunks et des documents rattachés à la fiche ;
- ``vecteurs``  : embeddings pgvector — branche DORMANTE tant que le Lot F
                  (encodeur local e5-small) n'est pas là : elle ne s'active
                  que si un appelant fournit ``encode_requete`` ET que la base
                  contient des embeddings. La fusion RRF, elle, est déjà
                  réelle et testée.

RG3 en lecture : par défaut, la recherche ne renvoie que des fiches
``valide`` (l'archive de confiance) ; ``inclure_a_valider`` élargit
explicitement à la file de validation. Les recherches SANS RÉSULTAT sont
journalisées (``recherche_log``, index partiel migration 012) : c'est la
matière première de l'amélioration du lexique (critère de sortie Phase 3).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Annotated, Any, Callable, Mapping, Sequence

from fastapi import Header, HTTPException, Query

LOGGER = logging.getLogger("seamtech.recherche")

# Constantes de fusion et de volume (plan §17.2 / §11.3).
RRF_K = 60
PROFONDEUR_SOURCES = 100  # candidats demandés à chaque source avant fusion
FACETTE_LIMITE = 20       # valeurs max par facette affichée
SEUIL_TRIGRAMMES = 0.30   # word_similarity minimale pour la source tolérante
SUGGESTION_LIMITE_DEFAUT = 10
SYNONYMES_TTL_S = 30.0    # le référentiel synonyme est petit et modifiable à chaud

# Filtres admis (liste blanche — tout autre paramètre est ignoré, jamais de
# SQL construit depuis un nom de champ inconnu).
FILTRES_AUTORISES = frozenset(
    {"type_voile", "client", "bateau", "matiere", "gamme", "annee", "annee_min", "annee_max"}
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
) -> tuple[str, list[Any]]:
    """Fragment WHERE (alias imposés : v = vue, f = fiche). ``exclure`` sert
    aux facettes : chaque facette compte sans son PROPRE filtre, comme un
    moteur généraliste (le filtre type_voile ne réduit pas la facette
    type_voile)."""
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


def _facettes(
    cursor: Any,  # noqa: ANN401 - curseur psycopg2 réel
    ts_config: str,
    texte: str,
    filtres: Mapping[str, object],
    inclure_a_valider: bool,
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
        fragment, params_fragment = _fragment_filtres(filtres, inclure_a_valider, exclure)
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

def rechercher_fiches(
    index: Any,  # noqa: ANN401 - SearchIndex réel
    requete: str = "",
    filtres: Mapping[str, object] | None = None,
    limit: int = 20,
    offset: int = 0,
    inclure_a_valider: bool = False,
    encode_requete: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Recherche hybride : sources → RRF k=60 → page + facettes + journal."""
    filtres_purs = {
        cle: valeur
        for cle, valeur in (filtres or {}).items()
        if cle in FILTRES_AUTORISES and valeur not in (None, "")
    }
    debut = time.perf_counter()
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            ts_config = index._postgres_ts_config(connexion)
            capacites = _capacites(cursor)
            nom_base = getattr(getattr(connexion, "info", None), "dbname", "") or ""
            texte = _appliquer_synonymes(
                _normaliser_separateurs(requete.strip()), _charger_synonymes(cursor, nom_base)
            )

            fragment, params = _fragment_filtres(filtres_purs, inclure_a_valider)
            classements: dict[str, list[int]] = {}
            if texte:
                classements["lexical"] = _source_lexicale(
                    cursor, ts_config, texte, fragment, params, PROFONDEUR_SOURCES
                )
                # La tolérance aux fautes est un FILET, réservé aux requêtes
                # d'UN SEUL mot — c'est la forme dominante des fautes de
                # frappe (« monofime », « dacronn ») et c'est là que le
                # classement word_similarity a du sens. Mesuré à 10 000
                # fiches : multi-mots, l'extraction trigrammes coûte 260 à
                # 340 ms pour un apport nul (les mots corrects sont déjà
                # trouvés par le lexical) ; un seul mot passe en 20 à 40 ms
                # grâce aux index GIN. Le filet ne se déploie en outre que si
                # le lexical ne sature pas déjà la profondeur de fusion :
                # 100 candidats lexicaux rendent la source trigrammes
                # redondante pour le classement.
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
            else:
                classements["parcours"] = _liste_par_defaut(
                    cursor, fragment, params, PROFONDEUR_SOURCES
                )
                fusion = [
                    {"id_fiche": id_fiche, "score": 1.0, "sources": ["parcours"]}
                    for id_fiche in classements["parcours"]
                ]

            total = len(fusion)
            page = fusion[offset : offset + limit]
            resultats = _details_fiches(cursor, [ligne["id_fiche"] for ligne in page])
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

            facettes = _facettes(cursor, ts_config, texte, filtres_purs, inclure_a_valider)

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
        "sources_actives": sorted(classements.keys()),
        "sans_resultat": total == 0,
        "duree_ms": round(duree_ms, 2),
    }


def _details_fiches(cursor: Any, ids: Sequence[int]) -> dict[int, dict[str, Any]]:  # noqa: ANN401
    if not ids:
        return {}
    cursor.execute(
        """
        SELECT v.id_fiche, v.code, v.titre, v.type_voile, v.client, v.bateau,
               v.gamme, v.statut,
               CASE WHEN v.date_edition IS NULL THEN NULL
                    ELSE extract(year FROM v.date_edition)::int END,
               left(f.champs_texte, 400)
        FROM v_fiche_recherche v
        JOIN fiche f ON f.id_fiche = v.id_fiche
        WHERE v.id_fiche = ANY(%s)
        """,
        (list(ids),),
    )
    return {
        int(ligne[0]): {
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
        for ligne in cursor.fetchall()
    }


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
        type_voile: str | None = Query(None, max_length=200),
        client: str | None = Query(None, max_length=200),
        bateau: str | None = Query(None, max_length=200),
        matiere: str | None = Query(None, max_length=200),
        gamme: str | None = Query(None, max_length=200),
        annee: int | None = Query(None, ge=1900, le=2100),
        annee_min: int | None = Query(None, ge=1900, le=2100),
        annee_max: int | None = Query(None, ge=1900, le=2100),
        inclure_a_valider: bool = Query(False),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        filtres = {
            "type_voile": type_voile,
            "client": client,
            "bateau": bateau,
            "matiere": matiere,
            "gamme": gamme,
            "annee": annee,
            "annee_min": annee_min,
            "annee_max": annee_max,
        }
        try:
            reponse = rechercher_fiches(
                index,
                requete=q,
                filtres=filtres,
                limit=limit,
                offset=offset,
                inclure_a_valider=inclure_a_valider,
                encode_requete=encode_requete,
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
