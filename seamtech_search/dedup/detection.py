"""Détection de doublons — Lot L.1 (plan v3.0 §17.4).

RÈGLE ABSOLUE : la détection est **PROPOSITIVE**. Ce module
- n'exécute AUCUN ``DELETE`` / ``DROP`` / ``TRUNCATE`` ;
- ne fusionne JAMAIS deux fiches ;
- ne modifie JAMAIS ``fiche.statut`` (une fiche ``a_valider`` le reste) ;
- n'écrit JAMAIS dans l'archive indexée (RG13) ;
- n'effectue AUCUN appel réseau (RG14 : ni ``requests``, ni ``httpx``, ni
  ``urllib`` — test dédié ``test_dedup_sans_appel_reseau``).

La seule écriture possible, et seulement si ``dry_run=False`` est demandé
explicitement, est une LIGNE DE LIEN dans ``fiche_lien`` (table créée au schéma
initial 006 et restée inutilisée jusqu'ici) : ``type='doublon_exact'`` ou
``type='doublon_probable'``, avec un ``score``. La décision de fusionner (ou
non) reste entièrement humaine : aucun bouton « fusionner » n'est exposé.

Deux natures de doublon
-----------------------

**Exact** (:func:`doublons_exacts`) — deux fiches qui partagent une pièce
jointe de même empreinte SHA-256. ``fiche_piece_jointe.empreinte_sha256`` est
remplie à l'import par :func:`seamtech_search.fiches.depot.empreinte_fichier` :
aucune ambiguïté possible, c'est le même fichier octet pour octet. Cas réel du
fonds : ``fiche-7792-SO_ffab.pdf``, sha256 ``43afc51e…``.

**Probable** (:func:`doublons_probables`) — titres normalisés proches, avec
``similarity()`` de ``pg_trgm``. ``pg_trgm`` est **DÉGRADABLE** dans ce dépôt
(l'extension est posée sous ``SAVEPOINT`` dans
:meth:`seamtech_search.indexer.SearchIndex._migration_007_recherche_index` :
sans le privilège ``CREATE``, les index trigrammes sont omis et la recherche
dégrade). La détection dégrade **pareil** — elle ne lève jamais : faute de
``pg_trgm``, elle change de critère et bascule sur un repli déterministe
documenté (même ``client`` + ``bateau`` + ``gamme`` + année, ET titre
identique à la casse et aux accents près) au lieu de ne rien voir.

La similarité porte sur un **titre normalisé** calculé par la fonction SQL
``seamtech_titre_normalise`` (migration 016) : casse pliée, espaces compactés,
accents français dépliés via ``translate()``. ``translate()`` est IMMUTABLE
(contrairement à ``unaccent()``, qui est seulement STABLE et ne peut donc pas
porter d'index) : la normalisation peut donc être indexée en GIN trigrammes.
Côté Python, :func:`normaliser_titre` reproduit EXACTEMENT la même
normalisation (mêmes pliages) pour que les deux voies ne divergent pas.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, NamedTuple, Sequence

from seamtech_search.fiches.normalisation import sans_accents

# Types de lien écrits par ce module (contrainte UNIQUE de fiche_lien :
# id_fiche_source, id_fiche_cible, type).
TYPE_DOUBLON_EXACT = "doublon_exact"
TYPE_DOUBLON_PROBABLE = "doublon_probable"
TYPES_DOUBLON = (TYPE_DOUBLON_EXACT, TYPE_DOUBLON_PROBABLE)

# Score d'un doublon exact : certitude (même fichier, même empreinte).
SCORE_EXACT = 1.0

# Score d'un doublon probable trouvé par le repli déterministe : le critère est
# une ÉGALITÉ (titre identique + même contexte), donc certitude de critère —
# mais pas de fichier. 1.0 est donc justifié, la NATURE du lien diffère.
SCORE_REPLI_DETERMINISTE = 1.0

# ``fiche_lien.score`` est NUMERIC(4,3) : 4 chiffres, dont 3 après la virgule
# → maximum 9.999. Un score hors bornes ferait échouer l'INSERT (erreur 22003)
# et ferait tomber tout le scan : on borne, on ne devine pas.
SCORE_MAX = 9.999

SEUIL_PROBABLE_DEFAUT = 0.55


def normaliser_titre(titre: str | None) -> str:
    """Titre normalisé — miroir Python EXACT de ``seamtech_titre_normalise`` (SQL).

    Casse pliée, accents français dépliés, espaces compactés, bords rognés.
    Utilisé pour le repli déterministe et pour les tests de parité SQL/Python.
    """
    return re.sub(r"\s+", " ", sans_accents(str(titre or "")).lower()).strip()


class CandidatDoublon(NamedTuple):
    """Un candidat ``(id_a, id_b, score, motifs)`` — tuple, donc dépaquetable.

    ``motifs`` est destiné à l'humain qui décide : il porte les RAISONS du
    rapprochement (similarité de titre, même client, même bateau…), jamais une
    conclusion. Aucun motif ne dit « fusionner ».
    """

    id_a: int
    id_b: int
    score: float
    motifs: tuple[str, ...]


class ResultatProbables(list):
    """Liste de :class:`CandidatDoublon` qui PORTE aussi l'état du volet trigrammes.

    C'est littéralement ``[(id_a, id_b, score, motifs)]`` (chaque élément est un
    tuple de 4 champs), avec ``trigrammes_disponibles`` en attribut : l'appelant
    sait toujours PAR QUEL critère la liste a été produite — sans quoi un repli
    silencieux se lirait comme une absence de doublons.
    """

    def __init__(self, iterable: Iterable[Any] = (), *, trigrammes_disponibles: bool, seuil: float) -> None:
        super().__init__(iterable)
        self.trigrammes_disponibles = bool(trigrammes_disponibles)
        self.seuil = float(seuil)

    def critere(self) -> str:
        """Nom lisible du critère réellement appliqué (pour l'affichage/le journal)."""
        return "similarite_trigrammes" if self.trigrammes_disponibles else "repli_deterministe"

    def en_dicts(self) -> list[dict[str, Any]]:
        """Forme sérialisable JSON (CLI ``--json``, routes HTTP)."""
        return [
            {
                "id_fiche_source": int(c.id_a),
                "id_fiche_cible": int(c.id_b),
                "type": TYPE_DOUBLON_PROBABLE,
                "score": round(float(c.score), 3),
                "motifs": list(c.motifs),
            }
            for c in self
        ]


def _exiger_postgres(index: Any) -> None:  # noqa: ANN401 - SearchIndex réel
    if not getattr(index, "is_postgres", False):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail=(
                "Détection de doublons disponible sur PostgreSQL uniquement (décision de couche "
                "§17.1 du plan v3.0) : les tables fiche*/fiche_lien n'existent pas côté SQLite."
            ),
        )


def _trigrammes_disponibles(cursor: Any) -> bool:  # noqa: ANN401 - curseur psycopg2 réel
    """``pg_trgm`` est-il réellement installé ? Interroge ``pg_extension`` (le
    FAIT), jamais la configuration. Dégrade en ``False`` si la requête échoue —
    on ne sait pas, donc on n'utilise pas la voie rapide."""
    try:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
        return cursor.fetchone() is not None
    except Exception:  # noqa: BLE001 - « on ne sait pas » ne doit jamais faire échouer le scan
        return False


# ---------------------------------------------------------------------------
# 1) Doublons EXACTS — empreinte SHA-256 de pièce jointe partagée
# ---------------------------------------------------------------------------

# L'ORDER BY vise la POSITION 1 (l'empreinte, déjà première colonne) et non une
# seconde référence à `p.empreinte_sha256` : la clé de rapprochement n'apparaît
# ainsi que DEUX fois (SELECT et GROUP BY), ce qui rend le sabotage du garde-fou
# CI chirurgical — remplacer la colonne par une expression constante fait
# retomber TOUTES les fiches dans un seul groupe FAUX, sans casser la syntaxe,
# si bien que le test doit échouer sur son ASSERTION (le vrai comportement), pas
# sur une erreur SQL (qui masquerait ce que l'on veut prouver).
_SQL_DOUBLONS_EXACTS = """
SELECT p.empreinte_sha256,
       array_agg(DISTINCT p.id_fiche ORDER BY p.id_fiche) AS id_fiches,
       array_agg(DISTINCT p.chemin ORDER BY p.chemin) AS chemins,
       COUNT(DISTINCT p.id_fiche) AS nb_fiches
FROM fiche_piece_jointe p
WHERE p.empreinte_sha256 IS NOT NULL
  AND p.empreinte_sha256 <> ''
GROUP BY p.empreinte_sha256
HAVING COUNT(DISTINCT p.id_fiche) > 1
ORDER BY COUNT(DISTINCT p.id_fiche) DESC, 1
"""

_SQL_CODES_DES_FICHES = "SELECT id_fiche, code, titre, statut FROM fiche WHERE id_fiche = ANY(%s)"


def doublons_exacts(index: Any) -> list[dict[str, Any]]:  # noqa: ANN401 - SearchIndex réel
    """Groupes de fiches partageant une pièce jointe de même empreinte SHA-256.

    Critère imposé : ``GROUP BY empreinte_sha256 HAVING count(DISTINCT id_fiche) > 1``
    sur ``fiche_piece_jointe`` — la clé de rapprochement est l'EMPREINTE, jamais
    un titre ni une ressemblance : deux fiches qui partagent le même PDF sont
    indiscutablement le même document.

    Rend une liste de ``{empreinte_sha256, id_fiches, codes, chemins, nb_fiches}``
    (``codes``/``titres``/``statuts`` sont ajoutés pour l'affichage : ce sont des
    commodités de lecture, le critère reste l'empreinte seule).
    """
    _exiger_postgres(index)
    groupes: list[dict[str, Any]] = []
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_DOUBLONS_EXACTS)
            lignes = cursor.fetchall()
            if lignes:
                ids = sorted({int(i) for ligne in lignes for i in ligne[1]})
                cursor.execute(_SQL_CODES_DES_FICHES, (ids,))
                infos = {
                    int(id_fiche): (str(code), titre, str(statut))
                    for id_fiche, code, titre, statut in cursor.fetchall()
                }
            else:
                infos = {}
    for empreinte, id_fiches, chemins, nb_fiches in lignes:
        ids = [int(i) for i in id_fiches]
        groupes.append(
            {
                "empreinte_sha256": str(empreinte),
                "id_fiches": ids,
                "codes": [infos.get(i, ("", None, ""))[0] for i in ids],
                "titres": [infos.get(i, ("", None, ""))[1] for i in ids],
                "statuts": [infos.get(i, ("", None, ""))[2] for i in ids],
                "chemins": [str(c) for c in chemins],
                "nb_fiches": int(nb_fiches),
            }
        )
    return groupes


# ---------------------------------------------------------------------------
# 2) Doublons PROBABLES — similarité de titre normalisé
# ---------------------------------------------------------------------------

# Le `%%` est OBLIGATOIRE : psycopg2 réserve `%` pour ses paramètres, et
# l'opérateur de similarité pg_trgm s'écrit `%` — non échappé, il ferait lever
# un « IndexError: tuple index out of range » (même piège que dans
# seamtech_search/recherche.py, où l'opérateur est déjà écrit `%%`).
_SQL_PROBABLES_TRIGRAMMES = """
SELECT f1.id_fiche, f2.id_fiche, f1.code, f2.code,
       f1.id_client, f2.id_client, f1.id_bateau, f2.id_bateau,
       f1.gamme, f2.gamme,
       extract(year FROM f1.date_edition)::int, extract(year FROM f2.date_edition)::int,
       similarity(seamtech_titre_normalise(f1.titre), seamtech_titre_normalise(f2.titre)) AS score
FROM fiche f1
JOIN fiche f2
  ON f2.id_fiche > f1.id_fiche
 AND seamtech_titre_normalise(f2.titre) %% seamtech_titre_normalise(f1.titre)
WHERE coalesce(f1.titre, '') <> ''
  AND coalesce(f2.titre, '') <> ''
  -- Titres IDENTIQUES inclus : c'est le cas le plus probable d'un doublon, il
  -- serait absurde de l'exclure. (Ce `<>` a existé le temps d'un brouillon et
  -- faisait disparaître exactement les paires les plus évidentes — voir le test
  -- test_meme_titre_clients_differents_pas_de_doublon_exact, qui l'a attrapé.)
  AND similarity(seamtech_titre_normalise(f1.titre), seamtech_titre_normalise(f2.titre)) >= %s
ORDER BY score DESC, f1.id_fiche, f2.id_fiche
"""

# Repli DÉTERMINISTE (documenté dans l'en-tête) : pg_trgm absent → on ne
# renonce pas, on resserre le critère sur une ÉGALITÉ vérifiable sans extension :
# titre identique à la casse/accents près ET même contexte métier complet
# (client + bateau + gamme + année). Le repli est donc plus strict que la
# similarité de trigrammes : il ne produira jamais un faux positif de
# ressemblance, au prix de rater les titres seulement « proches ».
_SQL_PROBABLES_REPLI = """
SELECT f1.id_fiche, f2.id_fiche, f1.code, f2.code
FROM fiche f1
JOIN fiche f2
  ON f2.id_fiche > f1.id_fiche
 AND seamtech_titre_normalise(f2.titre) = seamtech_titre_normalise(f1.titre)
 AND f1.id_client IS NOT DISTINCT FROM f2.id_client
 AND f1.id_bateau IS NOT DISTINCT FROM f2.id_bateau
 AND coalesce(f1.gamme, '') = coalesce(f2.gamme, '')
 AND extract(year FROM f1.date_edition) IS NOT DISTINCT FROM extract(year FROM f2.date_edition)
WHERE coalesce(f1.titre, '') <> ''
ORDER BY f1.id_fiche, f2.id_fiche
"""

# Empreintes partagées : sert à annoter un couple probable déjà prouvé exact.
_SQL_EMPREINTES_PARTAGEES = """
SELECT DISTINCT a.id_fiche, b.id_fiche
FROM fiche_piece_jointe a
JOIN fiche_piece_jointe b
  ON b.empreinte_sha256 = a.empreinte_sha256
 AND b.id_fiche > a.id_fiche
WHERE a.empreinte_sha256 IS NOT NULL AND a.empreinte_sha256 <> ''
"""


def _motifs_contexte(ligne: Sequence[Any]) -> tuple[str, ...]:
    """Motifs lisibles pour un couple probable (lignes trigrammes à 13 colonnes).

    On dit à l'humain POURQUOI les deux fiches se ressemblent, sans jamais
    conclure à sa place : « même client » ne veut pas dire « même voile ».
    """
    motifs: list[str] = [f"similarité de titre normalisé {float(ligne[12]):.3f} (pg_trgm)"]
    if ligne[4] is not None and ligne[4] == ligne[5]:
        motifs.append("même client")
    if ligne[6] is not None and ligne[6] == ligne[7]:
        motifs.append("même bateau")
    if str(ligne[8] or "") and str(ligne[8]) == str(ligne[9]):
        motifs.append("même gamme")
    if ligne[10] is not None and ligne[10] == ligne[11]:
        motifs.append(f"même année ({int(ligne[10])})")
    return tuple(motifs)


def doublons_probables(
    index: Any,  # noqa: ANN401 - SearchIndex réel
    seuil: float = SEUIL_PROBABLE_DEFAUT,
) -> ResultatProbables:
    """Couples de fiches dont les TITRES NORMALISÉS sont proches.

    Voie nominale : ``similarity()`` de ``pg_trgm`` sur le titre normalisé,
    couples restreints à ``id_a < id_b`` (chaque paire une seule fois) et filtrés
    au ``seuil`` (défaut 0,55). Voie de repli, si ``pg_trgm`` est absent :
    critère déterministe documenté (titre identique à la casse/accents près ET
    même client + bateau + gamme + année).

    Rend un :class:`ResultatProbables` — littéralement ``[(id_a, id_b, score,
    motifs)]`` — dont l'attribut ``trigrammes_disponibles`` dit lequel des deux
    critères a réellement tourné.
    """
    _exiger_postgres(index)
    seuil = float(seuil)
    candidats: list[CandidatDoublon] = []
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            dispo = _trigrammes_disponibles(cursor)
            if dispo:
                cursor.execute(_SQL_PROBABLES_TRIGRAMMES, (seuil,))
                for ligne in cursor.fetchall():
                    candidats.append(
                        CandidatDoublon(int(ligne[0]), int(ligne[1]), float(ligne[12]), _motifs_contexte(ligne))
                    )
            else:
                cursor.execute(_SQL_PROBABLES_REPLI)
                for ligne in cursor.fetchall():
                    candidats.append(
                        CandidatDoublon(
                            int(ligne[0]),
                            int(ligne[1]),
                            SCORE_REPLI_DETERMINISTE,
                            (
                                "pg_trgm indisponible — repli déterministe",
                                "titre identique (casse et accents pliés)",
                                "même client, bateau, gamme et année",
                            ),
                        )
                    )
            # Annotation orthogonale : un couple probable déjà prouvé exact est
            # signalé comme tel (le bandeau « exact » primant sur le probable).
            if candidats:
                cursor.execute(_SQL_EMPREINTES_PARTAGEES)
                exacts = {(int(a), int(b)) for a, b in cursor.fetchall()}
                if exacts:
                    annotes: list[CandidatDoublon] = []
                    for cand in candidats:
                        if (cand.id_a, cand.id_b) in exacts:
                            cand = cand._replace(
                                motifs=cand.motifs + ("déjà prouvé exact : même empreinte SHA-256",)
                            )
                        annotes.append(cand)
                    candidats = annotes
    return ResultatProbables(candidats, trigrammes_disponibles=dispo, seuil=seuil)


# ---------------------------------------------------------------------------
# 3) Écriture des liens (PROPOSITIVE — jamais destructive)
# ---------------------------------------------------------------------------


def liens_depuis_exacts(groupes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Développe les groupes exacts en liens ``(id_fiche_source, id_fiche_cible)``.

    Une clique de ``n`` fiches produit ``n*(n-1)/2`` liens ordonnés
    (``source < cible``) : la relation « même fichier » est symétrique, on ne
    stocke qu'un sens (la contrainte UNIQUE interdirait le doublon).
    """
    liens: list[dict[str, Any]] = []
    for groupe in groupes:
        ids = sorted({int(i) for i in (groupe.get("id_fiches") or [])})
        for position, id_source in enumerate(ids):
            for id_cible in ids[position + 1 :]:
                liens.append(
                    {
                        "id_fiche_source": id_source,
                        "id_fiche_cible": id_cible,
                        "type": TYPE_DOUBLON_EXACT,
                        "score": SCORE_EXACT,
                        "motifs": [f"empreinte SHA-256 identique ({groupe.get('empreinte_sha256', '')[:12]}…)"],
                    }
                )
    return liens


def liens_depuis_probables(candidats: Iterable[Any]) -> list[dict[str, Any]]:
    """Convertit des :class:`CandidatDoublon` en liens ``doublon_probable``."""
    liens: list[dict[str, Any]] = []
    for candidat in candidats:
        id_a, id_b, score, motifs = candidat
        liens.append(
            {
                "id_fiche_source": min(int(id_a), int(id_b)),
                "id_fiche_cible": max(int(id_a), int(id_b)),
                "type": TYPE_DOUBLON_PROBABLE,
                "score": float(score),
                "motifs": list(motifs),
            }
        )
    return liens


def _normaliser_liens(entrees: Iterable[Any]) -> list[dict[str, Any]]:
    """Accepte trois formes et les ramène à la forme canonique.

    - la forme canonique ``{id_fiche_source, id_fiche_cible, type, score?,
      motifs?}`` ;
    - un GROUPE EXACT (``doublons_exacts`` : présence de ``id_fiches``) ;
    - un CANDIDAT PROBABLE (``doublons_probables`` : tuple ``(id_a, id_b,
      score, motifs)``).

    Lever une erreur explicite vaut mieux qu'écrire un lien silencieusement
    faux : une entrée non reconnue fait échouer l'appel.
    """
    canoniques: list[dict[str, Any]] = []
    for entree in entrees:
        if isinstance(entree, Mapping):
            if "id_fiches" in entree:
                canoniques.extend(liens_depuis_exacts([entree]))
                continue
            if "id_fiche_source" not in entree or "id_fiche_cible" not in entree:
                raise ValueError(
                    "Lien de doublon invalide : attendu {id_fiche_source, id_fiche_cible, type} "
                    f"ou un groupe exact {{empreinte_sha256, id_fiches}} — reçu {sorted(entree)!r}."
                )
            type_lien = str(entree.get("type") or "")
            if type_lien not in TYPES_DOUBLON:
                raise ValueError(
                    f"Type de lien de doublon invalide : {type_lien!r} (attendu l'un de {list(TYPES_DOUBLON)})."
                )
            canoniques.append(
                {
                    "id_fiche_source": int(entree["id_fiche_source"]),
                    "id_fiche_cible": int(entree["id_fiche_cible"]),
                    "type": type_lien,
                    "score": entree.get("score"),
                    "motifs": list(entree.get("motifs") or []),
                }
            )
            continue
        if isinstance(entree, (tuple, list)) and len(entree) == 4:
            canoniques.extend(liens_depuis_probables([entree]))
            continue
        raise ValueError(f"Entrée de doublon non reconnue : {entree!r}.")
    return canoniques


def _borner_score(score: Any) -> float | None:  # noqa: ANN401 - score client
    """Ramène le score dans les bornes de ``NUMERIC(4,3)`` (0 … 9,999)."""
    if score is None:
        return None
    try:
        valeur = float(score)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(SCORE_MAX, valeur))


def enregistrer_liens(
    index: Any,  # noqa: ANN401 - SearchIndex réel
    groupes: Iterable[Any],
    dry_run: bool = False,
) -> dict[str, int]:
    """Écrit les LIENS de doublon dans ``fiche_lien`` — jamais rien d'autre.

    ``INSERT … ON CONFLICT (id_fiche_source, id_fiche_cible, type) DO NOTHING`` :
    rejouer un scan est idempotent, il ne crée pas de doublon de lien.

    ``dry_run=True`` n'écrit **RIEN** : les liens déjà présents sont comptés par
    un ``SELECT``, pas par un essai d'écriture. C'est ce que le CLI utilise avant
    toute décision humaine.

    Rend ``{liens_crees, liens_deja_presents, fiches_concernees}`` où
    ``fiches_concernees`` est le nombre de fiches DISTINCTES engagées dans les
    liens PROPOSÉS (créés ou déjà présents) — la mesure « combien de fiches sont
    concernées avant validation ».

    Aucune suppression, aucune fusion, aucun changement de statut : ce sont des
    liens, pas des décisions.
    """
    _exiger_postgres(index)
    liens = _normaliser_liens(groupes)
    liens_crees = 0
    liens_deja_presents = 0
    fiches: set[int] = set()
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            for lien in liens:
                id_source = int(lien["id_fiche_source"])
                id_cible = int(lien["id_fiche_cible"])
                if id_source == id_cible:
                    # Un lien d'une fiche vers elle-même n'a aucun sens ; on
                    # l'ignore sans bruit, ce n'est pas une erreur de données.
                    continue
                if id_source > id_cible:
                    id_source, id_cible = id_cible, id_source
                fiches.add(id_source)
                fiches.add(id_cible)
                type_lien = str(lien["type"])
                if dry_run:
                    cursor.execute(
                        "SELECT 1 FROM fiche_lien WHERE id_fiche_source = %s AND id_fiche_cible = %s AND type = %s",
                        (id_source, id_cible, type_lien),
                    )
                    if cursor.fetchone() is None:
                        liens_crees += 1
                    else:
                        liens_deja_presents += 1
                    continue
                cursor.execute(
                    "INSERT INTO fiche_lien (id_fiche_source, id_fiche_cible, type, score) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (id_fiche_source, id_fiche_cible, type) DO NOTHING "
                    "RETURNING id_lien",
                    (id_source, id_cible, type_lien, _borner_score(lien.get("score"))),
                )
                if cursor.fetchone() is not None:
                    liens_crees += 1
                else:
                    liens_deja_presents += 1
    return {
        "liens_crees": liens_crees,
        "liens_deja_presents": liens_deja_presents,
        "fiches_concernees": len(fiches),
    }


# ---------------------------------------------------------------------------
# 4) Lecture des liens — pour l'affichage AVANT validation
# ---------------------------------------------------------------------------

_SQL_LIENS_DUNE_FICHE = """
SELECT l.id_lien, l.type, l.score, l.created_at,
       l.id_fiche_source, l.id_fiche_cible,
       af.id_fiche AS id_autre, af.code, af.titre, af.statut
FROM fiche_lien l
JOIN fiche af
  ON af.id_fiche = CASE WHEN l.id_fiche_source = %s THEN l.id_fiche_cible ELSE l.id_fiche_source END
WHERE l.id_fiche_source = %s OR l.id_fiche_cible = %s
ORDER BY (l.type = 'doublon_exact') DESC, l.score DESC NULLS LAST, l.id_lien
"""

_SQL_LIENS_PAR_CODES = """
SELECT l.id_lien, l.type, l.score, l.created_at,
       l.id_fiche_source, l.id_fiche_cible,
       sf.code AS code_source, cf.code AS code_cible,
       sf.statut AS statut_source, cf.statut AS statut_cible
FROM fiche_lien l
JOIN fiche sf ON sf.id_fiche = l.id_fiche_source
JOIN fiche cf ON cf.id_fiche = l.id_fiche_cible
WHERE l.type = ANY(%s) AND (sf.code = ANY(%s) OR cf.code = ANY(%s))
ORDER BY (l.type = 'doublon_exact') DESC, l.score DESC NULLS LAST, l.id_lien
"""


def _ligne_lien(ligne: Sequence[Any], id_fiche: int) -> dict[str, Any]:
    id_source = int(ligne[4])
    vers_cible = id_source == id_fiche
    score = ligne[2]
    return {
        "id_lien": int(ligne[0]),
        "type": str(ligne[1]),
        "score": float(score) if score is not None else None,
        "cree_le": ligne[3].isoformat() if hasattr(ligne[3], "isoformat") else ligne[3],
        "sens": "source_vers_cible" if vers_cible else "cible_vers_source",
        "id_fiche": id_fiche,
        "id_fiche_autre": int(ligne[6]),
        "code_autre": str(ligne[7] or ""),
        "titre_autre": ligne[8],
        "statut_autre": str(ligne[9] or ""),
    }


def liens_dune_fiche(index: Any, id_fiche: int) -> list[dict[str, Any]]:  # noqa: ANN401 - SearchIndex réel
    """Tous les liens de doublon d'une fiche, dans LES DEUX SENS.

    Sert à afficher le bandeau AVANT validation : « Doublon exact de CODE
    (sha256 …) » / « Doublon probable de CODE (score 0.xx) », avec de quoi
    ouvrir l'autre fiche. Les doublons exacts sont rendus d'abord (un exact
    prime sur un probable), puis par score décroissant.
    """
    _exiger_postgres(index)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_LIENS_DUNE_FICHE, (int(id_fiche), int(id_fiche), int(id_fiche)))
            return [_ligne_lien(ligne, int(id_fiche)) for ligne in cursor.fetchall()]


def doublons_par_code(
    index: Any,  # noqa: ANN401 - SearchIndex réel
    codes: Sequence[str],
    types: Sequence[str] = TYPES_DOUBLON,
) -> dict[str, list[dict[str, Any]]]:
    """Liens de doublon indexés par CODE DE FICHE — une seule requête pour l'écran.

    L'écran ``/validation`` liste des codes, pas des identifiants : cette vue
    évite N requêtes (une par ligne affichée). Chaque code demandé est présent
    dans le résultat, même sans lien (liste vide), pour que l'appelant n'ait
    jamais à distinguer « pas de lien » de « fiche oubliée ».
    """
    _exiger_postgres(index)
    demandes = [str(code) for code in codes]
    resultat: dict[str, list[dict[str, Any]]] = {code: [] for code in demandes}
    if not demandes:
        return resultat
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_LIENS_PAR_CODES, (list(types), demandes, demandes))
            for ligne in cursor.fetchall():
                id_source, id_cible = int(ligne[4]), int(ligne[5])
                code_source, code_cible = str(ligne[6] or ""), str(ligne[7] or "")
                score = ligne[2]
                for code, id_courant, id_autre, code_autre, statut_autre in (
                    (code_source, id_source, id_cible, code_cible, str(ligne[9] or "")),
                    (code_cible, id_cible, id_source, code_source, str(ligne[8] or "")),
                ):
                    if code not in resultat:
                        continue
                    resultat[code].append(
                        {
                            "id_lien": int(ligne[0]),
                            "type": str(ligne[1]),
                            "score": float(score) if score is not None else None,
                            "cree_le": ligne[3].isoformat() if hasattr(ligne[3], "isoformat") else ligne[3],
                            "id_fiche": id_courant,
                            "id_fiche_autre": id_autre,
                            "code_autre": code_autre,
                            "statut_fiche": str(ligne[8] or "") if id_courant == id_source else str(ligne[9] or ""),
                            "statut_autre": statut_autre,
                        }
                    )
    for liens in resultat.values():
        # Les doublons exacts d'abord (un exact prime sur un probable), puis par
        # score décroissant : c'est l'ordre que le bandeau de /validation affiche.
        liens.sort(key=lambda lien: (lien["type"] != TYPE_DOUBLON_EXACT, -(lien["score"] or 0.0)))
    return resultat


def scanner(
    index: Any,  # noqa: ANN401 - SearchIndex réel
    seuil: float = SEUIL_PROBABLE_DEFAUT,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Scan complet : détecte les deux natures puis propose (ou écrit) les liens.

    C'est LA porte d'entrée du CLI et de la route ``POST /fiches/doublons/scan``.
    ``dry_run=True`` (défaut du CLI) n'écrit rien — un opérateur regarde d'abord
    ce qui serait proposé, puis décide.
    """
    groupes = doublons_exacts(index)
    probables = doublons_probables(index, seuil=seuil)
    liens = liens_depuis_exacts(groupes) + liens_depuis_probables(probables)
    resume = enregistrer_liens(index, liens, dry_run=dry_run)
    return {
        "seuil": float(seuil),
        "dry_run": bool(dry_run),
        "trigrammes_disponibles": bool(probables.trigrammes_disponibles),
        "critere_probables": probables.critere(),
        "groupes_exacts": len(groupes),
        "paires_probables": len(probables),
        "groupes": groupes,
        "probables": probables.en_dicts(),
        **resume,
    }
