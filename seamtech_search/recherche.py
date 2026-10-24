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

from fastapi import Body, Header, HTTPException, Query

from seamtech_search.fiches.normalisation import normaliser_cle_recherche, sans_accents, vers_metres
from seamtech_search.referentiel_bateaux import (
    ORDRE_CORRESPONDANCES,
    TYPE_ALIAS,
    TYPE_EXACT,
    TYPE_FLOU,
    TYPE_PREFIXE,
    AliasBateauEnConflit,
    assurer_alias,
    charger_cles_modeles,
    classer_correspondances_modeles,
    correspondances_dans_requete,
    defusionner_modele,
    fusionner_modeles,
    lister_modeles,
    normaliser_modele_bateau,
    rechercher_modeles_flous,
    resoudre_modele_depuis_texte,
)
from seamtech_search.securite_requetes import exiger_administrateur_requete

LOGGER = logging.getLogger("seamtech.recherche")

# Constantes de fusion et de volume (plan §17.2 / §11.3).
RRF_K = 60
PROFONDEUR_SOURCES = 100  # candidats demandés à chaque source avant fusion
FACETTE_LIMITE = 20       # valeurs max par facette affichée
SEUIL_TRIGRAMMES = 0.30   # word_similarity minimale pour la source tolérante
SUGGESTION_LIMITE_DEFAUT = 10
SYNONYMES_TTL_S = 30.0    # le référentiel synonyme est petit et modifiable à chaud

# Lot 4a — aperçu « Extrait du document » des résultats. L'extrait est borné
# côté API (jamais un document entier), et ses correspondances sont rendues en
# segments textuels structurés : le contenu source ne devient jamais du HTML.
EXTRAIT_DOCUMENT_MAX_CARACTERES = 360
_MARQUEUR_EXTRAIT_DEBUT = "[[SEAMTECH_CORRESPONDANCE]]"
_MARQUEUR_EXTRAIT_FIN = "[[/SEAMTECH_CORRESPONDANCE]]"
_RE_MARQUEURS_EXTRAIT = re.compile(
    r"(\[\[SEAMTECH_CORRESPONDANCE\]\]|\[\[/SEAMTECH_CORRESPONDANCE\]\])"
)
_RE_ESPACES_EXTRAIT = re.compile(r"\s+")

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
        "annee", "annee_min", "annee_max", "boat_model_id",
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
_MODELES_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


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


def _nettoyer_segment_extrait(texte: str) -> str:
    """Texte affichable d'un segment : contrôles neutralisés, espaces unifiés."""
    sans_controle = "".join(
        " " if ord(caractere) < 32 or ord(caractere) == 127 else caractere
        for caractere in texte
    )
    return _RE_ESPACES_EXTRAIT.sub(" ", sans_controle)


def _fusionner_segments_extrait(
    segments: Sequence[tuple[str, bool]],
) -> list[dict[str, Any]]:
    fusionnes: list[list[Any]] = []
    for texte, correspondance in segments:
        if not texte:
            continue
        if fusionnes and fusionnes[-1][1] is correspondance:
            fusionnes[-1][0] += texte
        else:
            fusionnes.append([texte, correspondance])
    if fusionnes:
        fusionnes[0][0] = str(fusionnes[0][0]).lstrip()
        fusionnes[-1][0] = str(fusionnes[-1][0]).rstrip()
    return [
        {"texte": str(texte), "correspondance": bool(correspondance)}
        for texte, correspondance in fusionnes
        if str(texte)
    ]


def _borner_segments_extrait(
    segments: Sequence[dict[str, Any]],
    limite: int,
) -> list[dict[str, Any]]:
    """Fenêtre bornée centrée sur la première correspondance quand elle existe."""
    if limite <= 0:
        return []
    positions: list[tuple[int, int, dict[str, Any]]] = []
    position = 0
    for segment in segments:
        texte = str(segment["texte"])
        positions.append((position, position + len(texte), segment))
        position += len(texte)
    if position <= limite:
        return list(segments)

    texte_complet = "".join(str(segment["texte"]) for segment in segments)
    correspondance_debut: int | None = None
    correspondance_fin: int | None = None
    for debut, fin, segment in positions:
        if segment["correspondance"] is True:
            correspondance_debut, correspondance_fin = debut, fin
            break

    if correspondance_debut is None or correspondance_fin is None:
        debut_fenetre, fin_fenetre = 0, limite
    else:
        longueur_correspondance = correspondance_fin - correspondance_debut
        if longueur_correspondance >= limite:
            debut_fenetre = correspondance_debut
            fin_fenetre = correspondance_debut + limite
        else:
            debut_fenetre = correspondance_debut - (limite - longueur_correspondance) // 2
            debut_fenetre = max(0, min(debut_fenetre, position - limite))
            fin_fenetre = debut_fenetre + limite

    if debut_fenetre > 0:
        espace_suivant = texte_complet.find(" ", debut_fenetre)
        if espace_suivant != -1 and (
            correspondance_debut is None or espace_suivant < correspondance_debut
        ):
            debut_fenetre = espace_suivant + 1
    if fin_fenetre < position:
        espace_precedent = texte_complet.rfind(" ", debut_fenetre, fin_fenetre)
        if espace_precedent > debut_fenetre and (
            correspondance_fin is None or espace_precedent >= correspondance_fin
        ):
            fin_fenetre = espace_precedent

    extraits: list[tuple[str, bool]] = []
    for debut, fin, segment in positions:
        if fin <= debut_fenetre or debut >= fin_fenetre:
            continue
        texte = str(segment["texte"])
        extraits.append(
            (
                texte[max(0, debut_fenetre - debut) : len(texte) - max(0, fin - fin_fenetre)],
                segment["correspondance"] is True,
            )
        )
    return _fusionner_segments_extrait(extraits)


def _segments_extrait_document(
    texte: str,
    limite: int = EXTRAIT_DOCUMENT_MAX_CARACTERES,
) -> list[dict[str, Any]]:
    """Convertit les marqueurs PostgreSQL en segments JSON sûrs à afficher."""
    brut: list[tuple[str, bool]] = []
    correspondance = False
    for morceau in _RE_MARQUEURS_EXTRAIT.split(texte or ""):
        if morceau == _MARQUEUR_EXTRAIT_DEBUT:
            correspondance = True
            continue
        if morceau == _MARQUEUR_EXTRAIT_FIN:
            correspondance = False
            continue
        nettoye = _nettoyer_segment_extrait(morceau)
        if nettoye:
            brut.append((nettoye, correspondance))
    return _borner_segments_extrait(_fusionner_segments_extrait(brut), limite)


def _libelle_source_extrait(source: str, libelle_source: str | None) -> str:
    if source == "fiche":
        return "Champs de la fiche"
    brut = _nettoyer_segment_extrait(str(libelle_source or "")).strip()
    nom = re.split(r"[\\/]", brut)[-1].strip() if brut else ""
    nom = nom[:120].rstrip()
    if source == "chunk":
        return f"Texte du document · {nom}" if nom else "Texte du document"
    return nom or "Document rattaché"


def _construire_extrait_document(
    texte: str,
    *,
    source: str,
    libelle_source: str | None,
) -> dict[str, Any] | None:
    """Contrat Lot 4a : texte intégralement reconstructible depuis ses segments."""
    source_retenue = source if source in {"document", "chunk", "fiche"} else "fiche"
    segments = _segments_extrait_document(texte)
    if not segments:
        return None
    return {
        "texte": "".join(str(segment["texte"]) for segment in segments),
        "segments": segments,
        "source": source_retenue,
        "libelle_source": _libelle_source_extrait(source_retenue, libelle_source),
        "correspondance_requete": any(
            segment["correspondance"] is True for segment in segments
        ),
    }


def _extraits_documents(
    cursor: Any,  # noqa: ANN401 - curseur psycopg2 réel
    ids: Sequence[int],
    ts_config: str,
    requetes: Sequence[str],
) -> dict[int, dict[str, Any]]:
    """Meilleur aperçu documentaire pour les fiches d'une page de résultats.

    Ordre de provenance, après pertinence plein texte : document réellement
    extrait (lien direct, PDF source ou pièce jointe), chunk documentaire,
    puis champs de la fiche. Les chunks ``resume_champs`` sont exclus : ce
    sont des résumés générés pour les vecteurs, pas le texte du document.
    ``requetes`` contient au plus deux variantes alternatives (texte étendu
    par synonymes et texte saisi), réunies en OU pour le surlignage.
    """
    if not ids:
        return {}

    variantes: list[str] = []
    for requete in requetes:
        texte = str(requete or "").strip()
        if texte and texte not in variantes:
            variantes.append(texte)
        if len(variantes) == 2:
            break
    requete_1 = variantes[0] if variantes else ""
    requete_2 = variantes[1] if len(variantes) > 1 else ""

    cursor.execute(
        "SELECT to_regclass('public.documents') IS NOT NULL, "
        "to_regclass('public.chunk') IS NOT NULL, "
        "to_regclass('public.fiche_piece_jointe') IS NOT NULL AND EXISTS ("
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'fiche_piece_jointe' "
        "AND column_name = 'id_document')"
    )
    documents_disponibles, chunks_disponibles, pieces_disponibles = (
        bool(valeur) for valeur in cursor.fetchone()
    )

    branches: list[str] = []
    if documents_disponibles:
        branches.extend(
            [
                """
                SELECT p.id_fiche, 'document'::text AS source,
                       d.name AS libelle_source, d.content AS texte_source,
                       d.search_vector AS vecteur, 0 AS priorite, d.id AS id_source
                FROM page p
                JOIN documents d ON d.id_fiche = p.id_fiche
                WHERE NOT d.is_dir AND d.extraction_status = 'extracted'
                  AND btrim(coalesce(d.content, '')) <> ''
                """,
                """
                SELECT p.id_fiche, 'document'::text AS source,
                       d.name AS libelle_source, d.content AS texte_source,
                       d.search_vector AS vecteur, 0 AS priorite, d.id AS id_source
                FROM page p
                JOIN fiche f ON f.id_fiche = p.id_fiche
                JOIN documents d
                  ON d.path = f.fichier_source
                  OR d.path_key = lower(f.fichier_source)
                WHERE btrim(coalesce(f.fichier_source, '')) <> ''
                  AND NOT d.is_dir AND d.extraction_status = 'extracted'
                  AND btrim(coalesce(d.content, '')) <> ''
                """,
            ]
        )
        if pieces_disponibles:
            branches.append(
                """
                SELECT p.id_fiche, 'document'::text AS source,
                       d.name AS libelle_source, d.content AS texte_source,
                       d.search_vector AS vecteur, 10 AS priorite, d.id AS id_source
                FROM page p
                JOIN fiche_piece_jointe pj ON pj.id_fiche = p.id_fiche
                JOIN documents d ON d.id = pj.id_document
                WHERE NOT d.is_dir AND d.extraction_status = 'extracted'
                  AND btrim(coalesce(d.content, '')) <> ''
                """
            )
    if chunks_disponibles:
        branches.append(
            """
            SELECT p.id_fiche, 'chunk'::text AS source,
                   c.nature AS libelle_source, c.contenu AS texte_source,
                   c.tsv AS vecteur, 20 AS priorite, c.id_chunk AS id_source
            FROM page p
            JOIN chunk c ON c.id_fiche = p.id_fiche
            WHERE c.nature <> 'resume_champs'
              AND btrim(coalesce(c.contenu, '')) <> ''
            """
        )
    branches.append(
        """
        SELECT p.id_fiche, 'fiche'::text AS source,
               'Champs de la fiche'::text AS libelle_source,
               f.champs_texte AS texte_source, f.search_vector AS vecteur,
               40 AS priorite, f.id_fiche AS id_source
        FROM page p
        JOIN fiche f ON f.id_fiche = p.id_fiche
        WHERE btrim(coalesce(f.champs_texte, '')) <> ''
        """
    )

    options_headline = (
        f"StartSel={_MARQUEUR_EXTRAIT_DEBUT}, StopSel={_MARQUEUR_EXTRAIT_FIN}, "
        "MaxFragments=1, MaxWords=42, MinWords=12, ShortWord=2, HighlightAll=false"
    )
    cursor.execute(
        f"""
        WITH page AS (
            SELECT unnest(%s::bigint[]) AS id_fiche
        ), requetes AS (
            SELECT
                CASE WHEN btrim(%s) = '' THEN NULL::tsquery
                     ELSE websearch_to_tsquery(%s, %s) END AS q1,
                CASE WHEN btrim(%s) = '' THEN NULL::tsquery
                     ELSE websearch_to_tsquery(%s, %s) END AS q2
        ), requete_unifiee AS (
            SELECT CASE
                       WHEN q1 IS NULL THEN q2
                       WHEN q2 IS NULL THEN q1
                       ELSE q1 || q2
                   END AS q
            FROM requetes
        ), candidats AS (
            {' UNION ALL '.join(branches)}
        ), notes AS (
            SELECT c.*,
                   COALESCE(c.vecteur @@ r.q1, false)
                       OR COALESCE(c.vecteur @@ r.q2, false) AS correspondance,
                   COALESCE(ts_rank_cd(c.vecteur, r.q1, 16), 0)
                       + COALESCE(ts_rank_cd(c.vecteur, r.q2, 16), 0) AS score
            FROM candidats c
            CROSS JOIN requetes r
        ), choisis AS MATERIALIZED (
            SELECT classes.*
            FROM (
                SELECT n.*,
                       row_number() OVER (
                           PARTITION BY n.id_fiche
                           ORDER BY n.correspondance DESC, n.score DESC,
                                    n.priorite ASC, n.id_source ASC
                       ) AS rang
                FROM notes n
            ) classes
            WHERE classes.rang = 1
        )
        SELECT ch.id_fiche, ch.source, ch.libelle_source,
               CASE
                   WHEN ru.q IS NULL THEN left(
                       regexp_replace(ch.texte_source, '\\s+', ' ', 'g'), 600
                   )
                   ELSE ts_headline(
                       %s::regconfig, ch.texte_source, ru.q, %s
                   )
               END AS extrait_brut
        FROM choisis ch
        CROSS JOIN requete_unifiee ru
        ORDER BY ch.id_fiche
        """,
        (
            list(ids),
            requete_1,
            ts_config,
            requete_1,
            requete_2,
            ts_config,
            requete_2,
            ts_config,
            options_headline,
        ),
    )

    extraits: dict[int, dict[str, Any]] = {}
    for id_fiche, source, libelle_source, extrait_brut in cursor.fetchall():
        extrait = _construire_extrait_document(
            str(extrait_brut or ""),
            source=str(source),
            libelle_source=None if libelle_source is None else str(libelle_source),
        )
        if extrait is not None:
            extraits[int(id_fiche)] = extrait
    return extraits


def _cle_suggestion(texte: str) -> str:
    """Clé de comparaison des suggestions et synonymes (Lot 2).

    Même normalisation conservative que les modèles de bateaux : casse,
    accents et ponctuation disparaissent, les abréviations ponctuées connues
    sont développées, mais aucun suffixe métier n'est tronqué.
    """
    return normaliser_modele_bateau(texte)


def _charger_cles_modeles_bateaux(cursor: Any, nom_base: str) -> list[dict[str, Any]]:  # noqa: ANN401
    """Clés canoniques + alias du référentiel, avec TTL court par base."""
    maintenant = time.monotonic()
    en_cache = _MODELES_CACHE.get(nom_base)
    if en_cache is not None and maintenant - en_cache[0] < SYNONYMES_TTL_S:
        return en_cache[1]
    cursor.execute("SELECT to_regclass('public.boat_models')")
    ligne = cursor.fetchone()
    if ligne is None or ligne[0] is None:
        cles: list[dict[str, Any]] = []
    else:
        cles = charger_cles_modeles(cursor)
    _MODELES_CACHE[nom_base] = (maintenant, cles)
    return cles


def invalider_cache_modeles_bateaux() -> None:
    """Invalidate explicite après alias/fusion/défusion ou réindexation."""
    _MODELES_CACHE.clear()


# ---------------------------------------------------------------------------
# Synonymes (`search_synonyms` Lot 2 + table historique `synonyme`)
# ---------------------------------------------------------------------------

def _charger_synonymes(cursor: Any, nom_base: str) -> dict[str, str]:  # noqa: ANN401
    maintenant = time.monotonic()
    en_cache = _SYNONYMES_CACHE.get(nom_base)
    if en_cache is not None and maintenant - en_cache[0] < SYNONYMES_TTL_S:
        return en_cache[1]
    table: dict[str, str] = {}
    cursor.execute("SELECT to_regclass('public.search_synonyms')")
    ligne = cursor.fetchone()
    if ligne is not None and ligne[0] is not None:
        cursor.execute(
            "SELECT terme, cible FROM search_synonyms WHERE actif ORDER BY id_synonyme"
        )
        for terme, cible in cursor.fetchall():
            cle = _cle_suggestion(str(terme or ""))
            if cle:
                table[cle] = str(cible)
    # La table historique reste l'autorité de compatibilité : ses entrées,
    # modifiées par les outils existants, écrasent une copie Lot 2 du même
    # terme au lieu de créer deux comportements selon le chemin de lecture.
    cursor.execute("SELECT terme, cible FROM synonyme ORDER BY terme")
    for terme, cible in cursor.fetchall():
        cle = _cle_suggestion(str(terme or ""))
        if cle:
            table[cle] = str(cible)
    _SYNONYMES_CACHE[nom_base] = (maintenant, table)
    return table


def invalider_cache_synonymes() -> None:
    """Invalidate explicite (tests, rechargement à chaud du référentiel)."""
    _SYNONYMES_CACHE.clear()


def _appliquer_synonymes(texte: str, synonymes: Mapping[str, str]) -> str:
    """Remplace un terme connu par sa cible, y compris les locutions.

    La comparaison se fait sur la clé normalisée (casse/accents/ponctuation),
    jamais par sous-chaîne : « spi » ne réécrit pas « spinnaker », et une
    locution comme « voile de portant » est consommée en une seule fois.
    """
    if not synonymes or not texte:
        return texte
    table: dict[tuple[str, ...], str] = {}
    for terme, cible in synonymes.items():
        morceaux = tuple(_cle_suggestion(str(terme)).split())
        if morceaux:
            table[morceaux] = str(cible)
    if not table:
        return texte
    mots = texte.split()
    normes: list[str] = []
    origines: list[int] = []
    for indice, mot in enumerate(mots):
        parties = _cle_suggestion(mot).split() or [mot.lower()]
        for partie in parties:
            normes.append(partie)
            origines.append(indice)
    longueur_max = max(len(cle) for cle in table)
    resultat: list[str] = []
    consommes: set[int] = set()
    position = 0
    while position < len(normes):
        trouve: tuple[int, str] | None = None
        for longueur in range(min(longueur_max, len(normes) - position), 0, -1):
            cle = tuple(normes[position:position + longueur])
            if cle in table:
                trouve = (longueur, table[cle])
                break
        if trouve is not None:
            longueur, cible = trouve
            resultat.append(cible)
            consommes.update(origines[position:position + longueur])
            position += longueur
            continue
        origine = origines[position]
        if origine not in consommes:
            resultat.append(mots[origine])
            consommes.add(origine)
        position += 1
    return " ".join(resultat)


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
    if filtres.get("boat_model_id") not in (None, "") and "bateau" not in exclure:
        morceaux.append(
            "EXISTS (SELECT 1 FROM bateau bf WHERE bf.id_bateau = f.id_bateau "
            "AND bf.boat_model_id = %s)"
        )
        params.append(int(filtres["boat_model_id"]))
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

def _source_modele_bateau(
    cursor: Any,  # noqa: ANN401
    correspondances: list[dict[str, Any]],
    fragment: str,
    params_filtres: list[Any],
    limite: int,
) -> list[int]:
    """Source RRF du référentiel canonique des bateaux (Lot 2).

    Les correspondances sont déjà bornées par le classement conservatif :
    exact/alias d'abord, puis préfixe/floue seulement sans correspondance
    complète. La source ne réinterprète donc jamais « 45 » en « 45.1 ».
    """
    ids: list[int] = []
    for correspondance in correspondances:
        identifiant = int(correspondance["id_modele"])
        if identifiant not in ids:
            ids.append(identifiant)
    if not ids:
        return []
    cursor.execute(
        f"""
        SELECT v.id_fiche
        FROM fiche f
        JOIN v_fiche_recherche v ON v.id_fiche = f.id_fiche
        JOIN bateau bm ON bm.id_bateau = f.id_bateau
        WHERE bm.boat_model_id = ANY(%s) AND {fragment}
        ORDER BY array_position(%s::bigint[], bm.boat_model_id), v.code
        LIMIT %s
        """,
        (ids, *params_filtres, ids, limite),
    )
    return [int(ligne[0]) for ligne in cursor.fetchall()]


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
    ``seamtech_texte_recherche(code) % q_norm``, idem titre, et
    ``seamtech_texte_recherche(champs_texte) %> q_norm`` (word_similarity,
    seuil 0,3 fixé par SET LOCAL). La clé normalisée (casse, diacritiques
    latin, ligatures œ/æ/ß, NFC≡NFD) est appliquée AUX DEUX côtés : des
    requêtes ne différant que par la casse ou les accents votent identiquement.
    Le classement garde word_similarity, le plus pertinent pour un texte long.
    Les données métier affichées ne sont pas réécrites.

    L'expression de gauche DOIT matcher l'index GIN 023
    ``seamtech_texte_recherche(<colonne>)`` : un ``coalesce(colonne, '')``
    autour de la colonne empêche PostgreSQL d'utiliser l'index (l'enveloppe
    SQL coalesce déjà à l'intérieur de la fonction). La clé requête est
    évaluée UNE fois (CTE ``q``)."""
    cursor.execute("SET LOCAL pg_trgm.word_similarity_threshold = '0.3'")
    cursor.execute("SET LOCAL enable_seqscan = off")
    try:
        cursor.execute(
            f"""
            WITH q AS (
                SELECT seamtech_texte_recherche(%s) AS q_norm
            ),
            candidats AS (
                SELECT f.id_fiche FROM fiche f, q
                 WHERE seamtech_texte_recherche(f.code) %% q.q_norm
                UNION
                SELECT f.id_fiche FROM fiche f, q
                 WHERE seamtech_texte_recherche(f.titre) %% q.q_norm
                UNION
                SELECT f.id_fiche FROM fiche f, q
                 WHERE seamtech_texte_recherche(f.champs_texte) %%> q.q_norm
            )
            SELECT id_fiche
            FROM (
                SELECT v.id_fiche AS id_fiche,
                       greatest(
                           word_similarity(q.q_norm, seamtech_texte_recherche(f.code)),
                           word_similarity(q.q_norm, seamtech_texte_recherche(f.titre)),
                           word_similarity(q.q_norm, seamtech_texte_recherche(f.champs_texte))
                       ) AS score
                FROM candidats cand
                JOIN fiche f ON f.id_fiche = cand.id_fiche
                JOIN v_fiche_recherche v ON v.id_fiche = f.id_fiche
                CROSS JOIN q
                WHERE {filtres_sql}
            ) AS scores
            WHERE score > %s
            ORDER BY score DESC, id_fiche
            LIMIT %s
            """,
            (texte, *params, SEUIL_TRIGRAMMES, limite),
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

def _fusionner_sources_ids(principale: Sequence[int], secondaire: Sequence[int]) -> list[int]:
    """Union ordonnée de deux classements d'une même source.

    Sert à l'expansion par synonymes : la forme tapée par l'opérateur reste
    principale, la forme canonique ajoute ses résultats sans pouvoir faire
    disparaître un libellé réel qui contient déjà le terme d'origine
    (« Spi Asymétrique » ne doit pas perdre contre « spinnaker »).
    """
    resultat: list[int] = []
    vues: set[int] = set()
    for id_fiche in (*principale, *secondaire):
        identifiant = int(id_fiche)
        if identifiant not in vues:
            vues.add(identifiant)
            resultat.append(identifiant)
    return resultat

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
    if "boat_model_id" in filtres_purs:
        valeur_modele = filtres_purs["boat_model_id"]
        try:
            if isinstance(valeur_modele, bool):
                raise ValueError
            identifiant_modele = int(valeur_modele)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="boat_model_id doit être un entier positif.") from None
        if identifiant_modele <= 0:
            raise HTTPException(status_code=422, detail="boat_model_id doit être un entier positif.")
        filtres_purs["boat_model_id"] = identifiant_modele
    tri_pur = tri if tri in TRIS_AUTORISES else "pertinence"
    debut = time.perf_counter()
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            ts_config = index._postgres_ts_config(connexion)
            capacites = _capacites(cursor)
            nom_base = getattr(getattr(connexion, "info", None), "dbname", "") or ""
            texte_sans_synonymes = _normaliser_separateurs(requete.strip())
            texte = _appliquer_synonymes(
                texte_sans_synonymes, _charger_synonymes(cursor, nom_base)
            )
            cles_modeles = _charger_cles_modeles_bateaux(cursor, nom_base)
            valeur_bateau = filtres_purs.get("bateau")
            if isinstance(valeur_bateau, str) and cles_modeles and "boat_model_id" not in filtres_purs:
                modele_filtre = resoudre_modele_depuis_texte(cursor, valeur_bateau)
                if modele_filtre is not None:
                    filtres_purs = {
                        **filtres_purs,
                        "boat_model_id": modele_filtre.id_modele,
                    }
                    filtres_purs.pop("bateau", None)
            correspondances_modeles = (
                correspondances_dans_requete(cursor, texte, cles=cles_modeles)
                if texte
                else []
            )
            # Un suffixe décimal qui fait partie d'un modèle exact/alias
            # (« Bavaria 45.1 ») est une identité, pas une cote de voile :
            # le référentiel gagne sur l'analyseur de dimensions.
            modele_decimal_exact = any(
                correspondance["type_correspondance"] in {TYPE_EXACT, TYPE_ALIAS}
                and re.search(r"\b\d+[.,]\d+\b", str(correspondance.get("cle_normalisee") or ""))
                for correspondance in correspondances_modeles
            )

            # Phase 1 — recherche par dimension : la requête est scindée en
            # (valeur + unité + éventuelle cote nommée) et les mots restants.
            # « 6,60 » seul → chemin numérique (toutes les cotes ±0,5 %), AUCUN
            # tsvector ; « spi 6,60 » → mots + filtre numérique ; « SLU 6,60 » →
            # cote nommée (filtre de cote existant). Les entiers nus ne sont
            # jamais des dimensions (« spi sailonet 2026 » reste textuel).
            analyse = (
                RequeteDimension(texte=texte)
                if modele_decimal_exact
                else analyser_requete_dimension(texte)
            )
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

            if texte and not modele_decimal_exact:
                correspondances_modeles = correspondances_dans_requete(
                    cursor, texte, cles=cles_modeles
                )
            # Expansion par synonymes en OU : on conserve aussi le texte tapé
            # pour les sources textuelles. Le remplacement seul ferait perdre
            # les fiches dont le libellé réel contient le terme d'origine.
            texte_original_sources = ""
            if texte_sans_synonymes != texte:
                if dimension is not None:
                    analyse_originale = analyser_requete_dimension(texte_sans_synonymes)
                    texte_original_sources = (
                        analyse_originale.texte
                        if analyse_originale.active
                        else texte_sans_synonymes
                    )
                else:
                    texte_original_sources = texte_sans_synonymes
                if texte_original_sources == texte:
                    texte_original_sources = ""
            fragment, params = _fragment_filtres(
                filtres_purs, inclure_a_valider, dimension_toutes=dimension_toutes
            )
            classements: dict[str, list[int]] = {}
            if texte:
                if correspondances_modeles:
                    classements["modele_bateau"] = _source_modele_bateau(
                        cursor,
                        correspondances_modeles,
                        fragment,
                        params,
                        PROFONDEUR_SOURCES,
                    )
                lexical = _source_lexicale(
                    cursor, ts_config, texte, fragment, params, PROFONDEUR_SOURCES
                )
                if texte_original_sources:
                    lexical = _fusionner_sources_ids(
                        _source_lexicale(
                            cursor,
                            ts_config,
                            texte_original_sources,
                            fragment,
                            params,
                            PROFONDEUR_SOURCES,
                        ),
                        lexical,
                    )
                classements["lexical"] = lexical
                if capacites["pg_trgm"] and len(classements["lexical"]) < PROFONDEUR_SOURCES:
                    trigrammes: list[int] = []
                    trigrammes_applicable = False
                    if " " not in texte:
                        trigrammes_applicable = True
                        trigrammes = _source_trigrammes(
                            cursor, texte, fragment, params, PROFONDEUR_SOURCES
                        )
                    if texte_original_sources and " " not in texte_original_sources:
                        trigrammes_applicable = True
                        trigrammes = _fusionner_sources_ids(
                            _source_trigrammes(
                                cursor,
                                texte_original_sources,
                                fragment,
                                params,
                                PROFONDEUR_SOURCES,
                            ),
                            trigrammes,
                        )
                    if trigrammes_applicable:
                        classements["trigrammes"] = trigrammes
                texte_pdf = _source_texte_pdf(
                    cursor, ts_config, texte, fragment, params, PROFONDEUR_SOURCES
                )
                if texte_original_sources:
                    texte_pdf = _fusionner_sources_ids(
                        _source_texte_pdf(
                            cursor,
                            ts_config,
                            texte_original_sources,
                            fragment,
                            params,
                            PROFONDEUR_SOURCES,
                        ),
                        texte_pdf,
                    )
                classements["texte_pdf"] = texte_pdf
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

            # Lot 4a : l'aperçu documentaire n'est calculé que pour la page
            # affichée (jamais pour toute la fusion), avec le texte étendu par
            # synonymes et le texte réellement saisi comme variantes de
            # surlignage en OU.
            extraits_page = _extraits_documents(
                cursor,
                [ligne["id_fiche"] for ligne in page],
                ts_config,
                [texte, texte_original_sources],
            )
            for ligne in page:
                detail = resultats.get(ligne["id_fiche"])
                if detail is not None:
                    detail["extrait_document"] = extraits_page.get(ligne["id_fiche"])

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
        "modeles_bateaux_detectes": correspondances_modeles,
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
    # Compatibilité de lecture avec une base arrêtée avant 026 (tests de
    # migration historique) : les champs canoniques existent dans la réponse
    # mais valent NULL tant que la couche Lot 2 n'est pas migrée. En service
    # normal, les migrations ont tourné au démarrage et le JOIN est actif.
    cursor.execute(
        "SELECT to_regclass('public.boat_models') IS NOT NULL AND EXISTS ("
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'bateau' "
        "AND column_name = 'boat_model_id')"
    )
    referentiel_disponible = bool(cursor.fetchone()[0])
    colonnes_modele = (
        "b.boat_model_id, bm.nom_canonique, bm.cle_normalisee"
        if referentiel_disponible
        else "NULL::bigint, NULL::text, NULL::text"
    )
    jointure_modele = (
        "LEFT JOIN boat_models bm ON bm.id_modele = b.boat_model_id"
        if referentiel_disponible
        else ""
    )
    # Requête de base sans cotes — évite JOIN fiche_cotes, permet élimination
    # par le planificateur et réduit le coût du chemin par défaut.
    cursor.execute(
        f"""
        SELECT f.id_fiche, f.code, f.titre,
               tv.libelle AS type_voile,
               c.nom AS client,
               b.nom || ' ' || coalesce(b.taille,'') AS bateau,
               {colonnes_modele},
               f.gamme, f.statut,
               CASE WHEN f.date_edition IS NULL THEN NULL
                    ELSE extract(year FROM f.date_edition)::int END,
               left(f.champs_texte, 400)
        FROM fiche f
        LEFT JOIN type_voile tv ON tv.id_type_voile = f.id_type_voile
        LEFT JOIN client c ON c.id_client = f.id_client
        LEFT JOIN bateau b ON b.id_bateau = f.id_bateau
        {jointure_modele}
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
            "id_modele_bateau": None if ligne[6] is None else int(ligne[6]),
            "modele_bateau": ligne[7],
            "modele_bateau_cle": ligne[8],
            "gamme": ligne[9],
            "statut": ligne[10],
            "annee": None if ligne[11] is None else int(ligne[11]),
            "extrait": ligne[12] or "",
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
    ("type_voile", "SELECT libelle FROM type_voile WHERE seamtech_texte_recherche(libelle) LIKE %s ESCAPE '\\'"),
    ("bateau", "SELECT nom FROM bateau WHERE seamtech_texte_recherche(nom) LIKE %s ESCAPE '\\'"),
    ("client", "SELECT nom FROM client WHERE seamtech_texte_recherche(nom) LIKE %s ESCAPE '\\'"),
    ("matiere", "SELECT nom FROM materiau WHERE seamtech_texte_recherche(nom) LIKE %s ESCAPE '\\'"),
    ("gamme", "SELECT DISTINCT gamme FROM fiche WHERE seamtech_texte_recherche(gamme) LIKE %s ESCAPE '\\'"
              " AND gamme <> '' AND statut IN ('valide', 'a_valider')"),
    ("fiche", "SELECT code FROM fiche WHERE seamtech_texte_recherche(code) LIKE %s ESCAPE '\\'"
              " AND statut IN ('valide', 'a_valider')"),
)

_SOURCES_SUGGESTIONS_TRGM: tuple[tuple[str, str], ...] = (
    ("type_voile", "SELECT libelle, similarity(seamtech_texte_recherche(%s), seamtech_texte_recherche(libelle)) FROM type_voile"
                   " WHERE similarity(seamtech_texte_recherche(%s), seamtech_texte_recherche(libelle)) > 0.3 ORDER BY 2 DESC LIMIT 3"),
    ("bateau", "SELECT nom, similarity(seamtech_texte_recherche(%s), seamtech_texte_recherche(nom)) FROM bateau"
               " WHERE similarity(seamtech_texte_recherche(%s), seamtech_texte_recherche(nom)) > 0.3 ORDER BY 2 DESC LIMIT 3"),
    ("client", "SELECT nom, similarity(seamtech_texte_recherche(%s), seamtech_texte_recherche(nom)) FROM client"
               " WHERE similarity(seamtech_texte_recherche(%s), seamtech_texte_recherche(nom)) > 0.3 ORDER BY 2 DESC LIMIT 3"),
    ("matiere", "SELECT nom, similarity(seamtech_texte_recherche(%s), seamtech_texte_recherche(nom)) FROM materiau"
                " WHERE similarity(seamtech_texte_recherche(%s), seamtech_texte_recherche(nom)) > 0.3 ORDER BY 2 DESC LIMIT 3"),
)


def suggerer(index: Any, prefixe: str, limite: int = SUGGESTION_LIMITE_DEFAUT) -> dict[str, Any]:  # noqa: ANN401
    """Suggestions classées exact > alias/synonyme > préfixe > flou.

    Les valeurs proposées existent réellement dans les référentiels. Les
    bateaux passent d'abord par le référentiel canonique (Lot 2), avec la
    table historique en filet de compatibilité pour une ligne non encore
    resynchronisée ; les synonymes (``spi`` → ``Spinnaker``) sont classés en
    alias, jamais confondus avec une simple coïncidence de préfixe.
    """
    prefixe = prefixe.strip()
    if not prefixe or limite <= 0:
        return {"prefixe": prefixe, "suggestions": []}
    candidats: dict[str, dict[str, Any]] = {}

    def ajouter(
        nature: str,
        valeur: str | None,
        type_correspondance: str,
        score: float,
        *,
        alias: str | None = None,
        id_modele_bateau: int | None = None,
    ) -> None:
        if valeur is None or not str(valeur).strip():
            return
        valeur_propre = str(valeur).strip()
        candidat: dict[str, Any] = {
            "nature": nature,
            "valeur": valeur_propre,
            "type_correspondance": type_correspondance,
            "rang": ORDRE_CORRESPONDANCES[type_correspondance],
            "score": round(float(score), 4),
        }
        if alias:
            candidat["alias"] = alias
        if id_modele_bateau is not None:
            candidat["id_modele_bateau"] = int(id_modele_bateau)
        cle = _cle_suggestion(valeur_propre)
        existant = candidats.get(cle)
        if existant is None:
            candidats[cle] = candidat
            return
        meilleur = (candidat["rang"], -candidat["score"]) < (
            existant["rang"],
            -existant["score"],
        )
        if meilleur:
            for champ in ("alias", "id_modele_bateau"):
                if champ not in candidat and champ in existant:
                    candidat[champ] = existant[champ]
            candidats[cle] = candidat
        else:
            for champ in ("alias", "id_modele_bateau"):
                if champ not in existant and champ in candidat:
                    existant[champ] = candidat[champ]

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            capacites = _capacites(cursor)
            nom_base = getattr(getattr(connexion, "info", None), "dbname", "") or ""
            synonymes = _charger_synonymes(cursor, nom_base)
            requete_cle = _cle_suggestion(prefixe)
            cible_synonyme = synonymes.get(requete_cle)
            cible_cle = _cle_suggestion(cible_synonyme) if cible_synonyme else ""

            cles_modeles = _charger_cles_modeles_bateaux(cursor, nom_base)
            for correspondance in classer_correspondances_modeles(
                cles_modeles,
                prefixe,
                limite=max(limite * 2, 10),
            ):
                ajouter(
                    "bateau",
                    str(correspondance["nom_canonique"]),
                    str(correspondance["type_correspondance"]),
                    float(correspondance["score"]),
                    alias=(
                        str(correspondance["alias"])
                        if correspondance.get("alias")
                        else None
                    ),
                    id_modele_bateau=int(correspondance["id_modele"]),
                )

            def motif_sql(texte: str) -> str:
                motif_norm = normaliser_cle_recherche(texte)
                return (
                    motif_norm.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                    + "%"
                )

            def classer_generique(
                valeur: str, *, via_synonyme: bool = False
            ) -> tuple[str, float]:
                candidat_cle = _cle_suggestion(valeur)
                if candidat_cle == requete_cle:
                    return TYPE_EXACT, 1.0
                if cible_cle and candidat_cle == cible_cle:
                    return TYPE_ALIAS, 0.98
                if via_synonyme and cible_cle and candidat_cle.startswith(cible_cle):
                    return TYPE_ALIAS, 0.90 + 0.05 * len(cible_cle) / max(len(candidat_cle), 1)
                return TYPE_PREFIXE, 0.70 + 0.25 * len(requete_cle) / max(len(candidat_cle), 1)

            for nature, sql in _SOURCES_SUGGESTIONS_PREFIXE:
                cursor.execute(f"{sql} ORDER BY 1 LIMIT 50", (motif_sql(prefixe),))
                for (valeur,) in cursor.fetchall():
                    type_correspondance, score = classer_generique(str(valeur))
                    ajouter(
                        nature,
                        valeur,
                        type_correspondance,
                        score,
                        alias=prefixe if type_correspondance == TYPE_ALIAS else None,
                    )
                if cible_synonyme:
                    cursor.execute(f"{sql} ORDER BY 1 LIMIT 50", (motif_sql(cible_synonyme),))
                    for (valeur,) in cursor.fetchall():
                        type_correspondance, score = classer_generique(
                            str(valeur), via_synonyme=True
                        )
                        ajouter(
                            nature,
                            valeur,
                            type_correspondance,
                            score,
                            alias=prefixe if type_correspondance == TYPE_ALIAS else None,
                        )

            if capacites["pg_trgm"] and len(candidats) < limite:
                for correspondance in (
                    rechercher_modeles_flous(cursor, prefixe, limite=max(limite, 5))
                    if cles_modeles
                    else []
                ):
                    ajouter(
                        "bateau",
                        str(correspondance["nom_canonique"]),
                        TYPE_FLOU,
                        float(correspondance["score"]),
                        alias=(
                            str(correspondance["alias"])
                            if correspondance.get("alias")
                            else None
                        ),
                        id_modele_bateau=int(correspondance["id_modele"]),
                    )
                for nature, sql in _SOURCES_SUGGESTIONS_TRGM:
                    cursor.execute(sql, (prefixe, prefixe))
                    for valeur, score in cursor.fetchall():
                        if _cle_suggestion(str(valeur)) == requete_cle:
                            ajouter(nature, valeur, TYPE_EXACT, 1.0)
                        else:
                            ajouter(nature, valeur, TYPE_FLOU, float(score or 0.0))

    suggestions = sorted(
        candidats.values(),
        key=lambda candidat: (
            int(candidat["rang"]),
            -float(candidat["score"]),
            str(candidat["valeur"]).casefold(),
            str(candidat["nature"]),
        ),
    )
    return {"prefixe": prefixe, "suggestions": suggestions[:limite]}


# ---------------------------------------------------------------------------
# Routes FastAPI — recherche, suggestions et référentiel bateaux (Lot 2)
# ---------------------------------------------------------------------------

def enregistrer_routes_recherche(
    app: Any,  # noqa: ANN401 - FastAPI
    index: Any,  # noqa: ANN401 - SearchIndex
    config: Any,  # noqa: ANN401 - AppConfig
    verifier_auth: Callable[[Any, str | None], None],
    metriques: dict[str, Any] | None = None,
    encode_requete: Callable[[str], str | None] | None = None,
) -> None:
    def _administrateur_referentiel(
        token: str | None,
        entete_utilisateur: str | None,
        entete_role: str | None,
        entete_session: str | None,
        entete_jeton: str | None,
    ) -> Any:  # noqa: ANN401
        return exiger_administrateur_requete(
            index,
            config,
            token=token,
            entete_utilisateur=entete_utilisateur,
            entete_role=entete_role,
            entete_session=entete_session,
            entete_jeton=entete_jeton,
        )

    def _payload_entier(payload: Mapping[str, Any], champ: str) -> int:
        valeur = payload.get(champ)
        if isinstance(valeur, bool):
            raise HTTPException(status_code=422, detail=f"{champ} doit être un entier positif.")
        try:
            entier = int(valeur)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"{champ} doit être un entier positif.") from None
        if entier <= 0:
            raise HTTPException(status_code=422, detail=f"{champ} doit être un entier positif.")
        return entier

    def _payload_texte(payload: Mapping[str, Any], champ: str) -> str:
        valeur = payload.get(champ)
        if not isinstance(valeur, str) or not valeur.strip():
            raise HTTPException(status_code=422, detail=f"{champ} est requis.")
        if len(valeur.strip()) > 200:
            raise HTTPException(status_code=422, detail=f"{champ} dépasse 200 caractères.")
        return valeur.strip()

    def _payload_motif(payload: Mapping[str, Any]) -> str | None:
        motif = payload.get("motif")
        if motif is None:
            return None
        if not isinstance(motif, str) or len(motif.strip()) > 500:
            raise HTTPException(status_code=422, detail="motif doit être un texte de 500 caractères maximum.")
        return motif.strip() or None

    def _exiger_modele(cursor: Any, id_modele: int) -> None:  # noqa: ANN401
        cursor.execute("SELECT 1 FROM boat_models WHERE id_modele = %s", (id_modele,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Modèle de bateau introuvable.")

    @app.get("/recherche")
    def route_recherche(
        q: str = Query("", max_length=500),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=10_000),
        page: int | None = Query(None, ge=1, le=1000, description="Numéro de page (1-indexé), alternative à offset"),
        type_voile: str | None = Query(None, max_length=200),
        client: str | None = Query(None, max_length=200),
        bateau: str | None = Query(None, max_length=200),
        boat_model_id: int | None = Query(None, gt=0, description="Modèle canonique Lot 2"),
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
            "boat_model_id": boat_model_id,
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

    def _reponse_suggestions(prefix: str, limit: int, token: str | None) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        return suggerer(index, prefix, limit)

    @app.get("/recherche/suggestions")
    def route_suggestions(
        prefix: str = Query("", max_length=200),
        limit: int = Query(SUGGESTION_LIMITE_DEFAUT, ge=1, le=25),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        return _reponse_suggestions(prefix, limit, token)

    @app.get("/search/suggestions")
    def route_search_suggestions(
        prefix: str = Query("", max_length=200),
        limit: int = Query(SUGGESTION_LIMITE_DEFAUT, ge=1, le=25),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        """Route canonique Lot 2 ; l'ancienne route reste compatible."""
        return _reponse_suggestions(prefix, limit, token)

    # Journal de recherche — exploitation (Lot J §3)
    @app.get("/recherche/journal")
    def route_journal(
        jours: int | None = Query(None, ge=1, le=365, description="Période en jours (défaut : tout)"),
        limite_top: int = Query(20, ge=1, le=100),
        limite_sans: int = Query(100, ge=1, le=500),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        # Les termes de recherche des utilisateurs sont une donnée
        # d'exploitation : administrateur humain uniquement (Lot 0b).
        exiger_administrateur_requete(
            index,
            config,
            token=token,
            entete_utilisateur=entete_utilisateur,
            entete_role=entete_role,
            entete_session=entete_session,
            entete_jeton=entete_jeton,
        )
        from seamtech_search.journal_recherche import rapport_journal
        return rapport_journal(index, periode_jours=jours, limite_top=limite_top, limite_sans_resultat=limite_sans)

    @app.get("/referentiel/bateaux")
    def route_referentiel_bateaux(
        inclure_fusionnes: bool = Query(False, description="Inclure les modèles fusionnés"),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                modeles = lister_modeles(cursor, inclure_fusionnes=inclure_fusionnes)
        return {"modeles": modeles}

    @app.post("/referentiel/bateaux/{id_modele}/alias")
    def route_ajouter_alias_bateau(
        id_modele: int,
        payload: Annotated[dict[str, Any], Body()],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _administrateur_referentiel(
            token, entete_utilisateur, entete_role, entete_session, entete_jeton
        )
        alias = _payload_texte(payload, "alias")
        try:
            with index.connect() as connexion:
                with connexion.cursor() as cursor:
                    _exiger_modele(cursor, id_modele)
                    resultat = assurer_alias(
                        cursor,
                        id_modele,
                        alias,
                        source="manuel",
                        cree_par=getattr(acteur, "id_utilisateur", None),
                    )
        except AliasBateauEnConflit as erreur:
            raise HTTPException(status_code=409, detail=str(erreur)) from erreur
        except ValueError as erreur:
            raise HTTPException(status_code=400, detail=str(erreur)) from erreur
        invalider_cache_modeles_bateaux()
        return resultat

    @app.post("/referentiel/bateaux/{id_modele}/fusion")
    def route_fusionner_bateau(
        id_modele: int,
        payload: Annotated[dict[str, Any], Body()],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _administrateur_referentiel(
            token, entete_utilisateur, entete_role, entete_session, entete_jeton
        )
        id_cible = _payload_entier(payload, "id_modele_cible")
        motif = _payload_motif(payload)
        try:
            with index.connect() as connexion:
                with connexion.cursor() as cursor:
                    _exiger_modele(cursor, id_modele)
                    _exiger_modele(cursor, id_cible)
                    resultat = fusionner_modeles(
                        cursor,
                        id_modele,
                        id_cible,
                        effectue_par=getattr(acteur, "id_utilisateur", None),
                        motif=motif,
                    )
        except ValueError as erreur:
            raise HTTPException(status_code=400, detail=str(erreur)) from erreur
        invalider_cache_modeles_bateaux()
        return resultat

    @app.post("/referentiel/bateaux/{id_modele}/defusion")
    def route_defusionner_bateau(
        id_modele: int,
        payload: Annotated[dict[str, Any] | None, Body()] = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        _exiger_postgres(index)
        acteur = _administrateur_referentiel(
            token, entete_utilisateur, entete_role, entete_session, entete_jeton
        )
        donnees = payload or {}
        motif = _payload_motif(donnees)
        id_historique = (
            _payload_entier(donnees, "id_historique")
            if donnees.get("id_historique") is not None
            else None
        )
        try:
            with index.connect() as connexion:
                with connexion.cursor() as cursor:
                    _exiger_modele(cursor, id_modele)
                    resultat = defusionner_modele(
                        cursor,
                        id_modele,
                        id_historique=id_historique,
                        effectue_par=getattr(acteur, "id_utilisateur", None),
                        motif=motif,
                    )
        except ValueError as erreur:
            raise HTTPException(status_code=400, detail=str(erreur)) from erreur
        invalider_cache_modeles_bateaux()
        return resultat
