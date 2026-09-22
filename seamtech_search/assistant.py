"""Lot I — assistant sourcé des fiches (plan v3.0 §11, Phase 4).

Un assistant **EXTRACTIF** : il construit ses réponses à partir des données
déjà en base (tables ``fiche*``, référentiels) et de la recherche existante.
AUCUN LLM génératif, AUCUN torch, AUCUN modèle téléchargé — décision
commanditaire actée (postes 8 Go, CPU ; consignée au CHANGELOG et dans
``docs/verite_terrain/DECISION_MATERIEL.md``). Si une information n'est pas
dans la base, l'assistant REFUSE explicitement (« je ne trouve pas dans les
fiches ») et propose au besoin des pistes de requête : jamais une valeur
inventée, jamais une réponse plausible non sourcée.

Contrat de réponse (``poser``) — toujours le même JSON :

- ``etat`` :
    ``ok``           réponse courte + ≥ 1 citation (chaque valeur affirmée
                     est sourcée) ;
    ``ambigu``       question interprétable de plusieurs façons — la réponse
                     liste les lectures possibles, chacune SOURCÉE par ses
                     propres citations ;
    ``sans_source``  refus explicite : la base ne contient pas l'information.
                     ``citations`` est alors vide (aucune valeur n'est
                     affirmée) ;
    ``occupe``       une autre question est déjà en cours d'analyse (le poste
                     cible est mono-cœur, l'analyse est sérialisée par un
                     verrou non bloquant) — redemander dans un instant ;
    ``indisponible`` la couche métier PostgreSQL manque (§17.1).
- ``reponse`` : texte court, extractif uniquement.
- ``citations`` : liste de citations VÉRIFIABLES — code de la fiche, champ
  (clé de ``fiche_champ_extrait``), table/colonne cibles, valeur, et quand
  c'est disponible la page + la zone PDF (mêmes coordonnées que le lot B :
  le clic ouvre ``/dossier/<code>?champ=<champ>`` qui surligne la zone dans
  la visionneuse).
- ``interpretations`` : présentes quand ``etat == ambigu`` (chacune avec ses
  citations).
- ``pistes`` : quand aucune source — des façons de reformuler, PAS des
  réponses.
- ``duree_ms`` : durée mesurée côté serveur.

Journalisation « comme les recherches » (§11.3) : chaque question est écrite
dans ``recherche_log`` — ``requete`` = question, ``filtres`` =
``{"canal": "assistant", "etat": …, "duree_ms": …}``, ``nb_resultats`` =
nombre de citations. Aucune nouvelle table, aucun fichier d'archive touché
(RG13), aucun appel réseau (RG14).

Compréhension de la question : déterministe (regex + dictionnaire de champs),
sans modèle. Les critères d'interprétation (par ex. « type portant » →
famille/libellé/titre contenant « portant » ou famille « voile_porteuse »)
sont écrits dans le code, affichés dans la réponse pour rester vérifiables,
et corrigeables en un seul endroit (:data:`MOTIFS_TYPE`).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

LOGGER = logging.getLogger("seamtech_search.assistant")

# États du contrat (documentés en tête de module).
ETAT_OK = "ok"
ETAT_AMBIGU = "ambigu"
ETAT_SANS_SOURCE = "sans_source"
ETAT_OCCUPE = "occupe"
ETAT_INDISPONIBLE = "indisponible"

# Une seule analyse à la fois : le poste cible est un CPU 8 Go (décision
# matérielle actée) et l'analyse est purement CPU/base — la sérialiser évite
# la contention tout en rendant l'état « occupé » observable et testable.
_VERROU_ANALYSE = threading.BoundedSemaphore(1)

LONGUEUR_QUESTION_MAX = 500
CITATIONS_MAX = 20  # borne d'affichage : au-delà, la réponse le dit

# ---------------------------------------------------------------------------
# Compréhension déterministe de la question (aucun modèle)
# ---------------------------------------------------------------------------

# Codes de fiche dans la question : « 7792-SO », « 0701-GV-001 ».
REGEX_CODE = re.compile(r"\b(\d{2,6}-[A-Za-z]{2,6}(?:-\d{1,4})?)\b")
REGEX_ANNEE = re.compile(r"\b(20\d{2})\b")
# Nombres à la française (« 6,5 ») — la virgule est un séparateur décimal ici.
REGEX_NOMBRE = re.compile(r"(\d+(?:[.,]\d+)?)")

# Champs de cotes : terme normalisé → (colonne fiche_cotes, libellé, unité,
# décimales d'affichage).
CHAMPS_COTES: dict[str, tuple[str, str, str, int]] = {
    "slu": ("slu_m", "SLU", "m", 2),
    "sle": ("sle_m", "SLE", "m", 2),
    "sf": ("sf_m", "SF", "m", 2),
    "shw": ("shw_m", "SHW", "m", 2),
    "spa": ("spa_m2", "SPA (surface)", "m²", 2),
    "surface": ("spa_m2", "SPA (surface)", "m²", 2),
    "tetiere": ("tetiere_cm", "Têtière", "cm", 1),
    "poids": ("poids_kg", "Poids", "kg", 2),
}

# Attributs de galon : terme → (colonne fiche_galon, libellé, unité, décimales).
ATTRIBUTS_GALON: dict[str, tuple[str, str, str, int]] = {
    "matiere": ("matiere", "Matière", "", 0),
    "couleur": ("couleur", "Couleur", "", 0),
    "largeur": ("largeur_mm", "Largeur", "mm", 1),
    "grammage": ("grammage_g_m2", "Grammage", "g/m²", 1),
}

BANDES_GALON = ("guindant", "chute", "bordure")

# Jeux de cotes : termes normalisés → valeur de fiche_cotes.jeu.
JEUX_COTES: dict[str, str] = {"finie": "finie", "finies": "finie", "dessin": "dessin", "dessins": "dessin"}

# Interprétation « type de voile » : un motif de la question est traduit en
# condition SQL affichée telle quelle dans la réponse (vérifiable, corrigeable
# en un seul endroit). Terme normalisé (sans accent) → (description, SQL).
# NB : %% (doublé) car les conditions sont injectées dans des énoncés
# exécutés avec paramètres psycopg2 — un % seul serait lu comme un joker de
# substitution.
MOTIFS_TYPE: tuple[tuple[str, str, str], ...] = (
    ("portant", "famille voile_porteuse, libellé ou titre contenant « portant »",
     "(tv.famille ILIKE '%%port%%' OR tv.libelle ILIKE '%%portant%%' OR f.titre ILIKE '%%portant%%')"),
    ("spi", "libellé de type contenant « spi »", "tv.libelle ILIKE '%%spi%%'"),
    ("genois", "libellé de type contenant « génois »", "tv.libelle ILIKE '%%gnois%%'"),
    ("grand-voile", "libellé de type contenant « grand »", "tv.libelle ILIKE '%%grand%%'"),
    ("tourmentin", "libellé de type contenant « tourmentin »", "tv.libelle ILIKE '%%tourmentin%%'"),
)

CONDITIONS_TYPE: dict[str, str] = {terme: condition for terme, _description, condition in MOTIFS_TYPE}
DESCRIPTIONS_TYPE: dict[str, str] = {terme: description for terme, description, _condition in MOTIFS_TYPE}

# Champs de tête de fiche : terme → (colonne fiche, libellé) — interrogés
# directement sur la ligne fiche (jamais devinés).
CHAMPS_FICHE: dict[str, tuple[str, str]] = {
    "client": ("client", "Client"),
    "bateau": ("bateau", "Bateau"),
    "titre": ("titre", "Titre"),
    "gamme": ("gamme", "Gamme"),
    "quantite": ("quantite", "Quantité"),
    "dessinateur": ("dessinateur", "Dessinateur"),
    "atelier": ("atelier", "Atelier"),
}

# Valeurs qui signifient « non applicable / sans objet » (RG5 : la fiche
# consigne « ~ » ; on reconnaît aussi les équivalents littéraux).
VALEURS_SANS_OBJET = frozenset({"~", "-", "non applicable", "sans objet", "n/a", "s.o.", "s/o"})


def normaliser_question(texte: str) -> str:
    """Minuscules, sans accents, espaces comprimés — forme de comparaison."""
    decompose = unicodedata.normalize("NFD", texte or "")
    sans_signes = "".join(c for c in decompose if not unicodedata.combining(c))
    return " ".join(sans_signes.lower().split())


def formater_valeur(valeur: Any, unite: str = "", decimales: int = 2) -> str:
    """Format français : 6.6 → « 6,60 m ». Les textes passent tels quels."""
    if valeur is None:
        return "—"
    if isinstance(valeur, (int, float)):
        texte = f"{float(valeur):,.{decimales}f}".replace(",", " ").replace(".", ",")
        return f"{texte}{(' ' + unite) if unite else ''}"
    return str(valeur)


@dataclass
class Contexte:
    """Question décortiquée (pur, testable sans base)."""

    brute: str
    normalisee: str
    code: str | None = None
    jeu: str | None = None          # finie | dessin | None (non précisé)
    cote: str | None = None         # clé de CHAMPS_COTES
    borne_min: float | None = None
    borne_max: float | None = None
    annee: int | None = None
    combien: bool = False
    entre: bool = False
    bateau_terme: str | None = None
    bande_galon: str | None = None
    attribut_galon: str | None = None
    terme_type: str | None = None   # clé de MOTIFS_TYPE
    champ_fiche: str | None = None  # clé de CHAMPS_FICHE
    veut_voiles: bool = False
    demande_matiere: bool = False
    demande_surplus: bool = False


def analyser_question(question: str) -> Contexte:
    """Décortique la question SANS toucher à la base (regex, dictionnaires)."""
    n = normaliser_question(question)
    ctx = Contexte(brute=question.strip(), normalisee=n)

    code = REGEX_CODE.search(question.strip())
    if code:
        ctx.code = code.group(1).upper()

    for terme, jeu in JEUX_COTES.items():
        if re.search(rf"\b{terme}\b", n):
            ctx.jeu = jeu
            break

    for terme in CHAMPS_COTES:
        if re.search(rf"\b{terme}\b", n):
            ctx.cote = terme
            break

    m = re.search(r"entre\s+(\d+(?:[.,]\d+)?)\s+et\s+(\d+(?:[.,]\d+)?)", n)
    if m:
        ctx.entre = True
        ctx.borne_min = float(m.group(1).replace(",", "."))
        ctx.borne_max = float(m.group(2).replace(",", "."))

    m = REGEX_ANNEE.search(n)
    if m:
        ctx.annee = int(m.group(1))

    ctx.combien = bool(re.search(r"\bcombien\b", n))
    ctx.veut_voiles = bool(re.search(r"\bvoile?s?\b", n)) and not ctx.combien
    ctx.demande_matiere = bool(re.search(r"\bmatiere\b", n))
    ctx.demande_surplus = bool(re.search(r"\bsurplus\b", n)) or bool(re.search(r"\bjonction\b", n) and re.search(r"\bsurplus\b", n))

    m = re.search(r"bateau\s+(?:\w+\s+)?([\w'’-]+(?:\s+[\w'’-]+)*?)\s*(?:\?|$|,|;| en | de )", n)
    if m:
        brut = m.group(1).strip()
        # Ne pas prendre un code de fiche pour un nom de bateau.
        ctx.bateau_terme = brut if not REGEX_CODE.fullmatch(brut) else None

    for bande in BANDES_GALON:
        if re.search(rf"\bbande?\s+de\s+{bande}\b|\bgalon.{0,12}\b{bande}\b|\b{bande}\b", n):
            ctx.bande_galon = bande
            break

    for attribut in ATTRIBUTS_GALON:
        if re.search(rf"\b{attribut}\b", n):
            ctx.attribut_galon = attribut
            break

    for motif, _description, _condition in MOTIFS_TYPE:
        if motif in n:
            ctx.terme_type = motif
            break

    for terme in CHAMPS_FICHE:
        if re.search(rf"\b{terme}\b", n):
            ctx.champ_fiche = terme
            break

    return ctx


# ---------------------------------------------------------------------------
# Réponses
# ---------------------------------------------------------------------------

@dataclass
class Reponse:
    etat: str
    reponse: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    interpretations: list[dict[str, Any]] = field(default_factory=list)
    pistes: list[str] = field(default_factory=list)


def _citation(
    code_fiche: str | None,
    champ: str,
    libelle: str,
    table_cible: str,
    colonne_cible: str,
    valeur: Any,
    valeur_affichee: str,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    citation = {
        "code_fiche": code_fiche,
        "champ": champ,
        "libelle": libelle,
        "table_cible": table_cible,
        "colonne_cible": colonne_cible,
        "valeur": valeur_affichee,
        "valeur_normalisee": None if valeur is None else str(valeur),
        "page": None,
        "zone": None,
    }
    if trace is not None:
        citation["page"] = trace.get("page")
        citation["zone"] = trace.get("zone")
        if trace.get("rang") is not None:
            citation["rang"] = trace["rang"]
    if code_fiche:
        parametres = f"champ={champ}"
        if trace and trace.get("rang") is not None:
            parametres += f"&rang={trace['rang']}"
        citation["lien"] = f"/dossier/{code_fiche}?{parametres}"
    return citation


def _est_sans_objet(valeur_texte: str | None) -> bool:
    if valeur_texte is None:
        return False
    return normaliser_question(valeur_texte) in VALEURS_SANS_OBJET


def _statut_sql(inclure_a_valider: bool) -> str:
    return "f.statut IN ('valide', 'a_valider')" if inclure_a_valider else "f.statut = 'valide'"


def _completer_zones(cursor: Any, citations: list[dict[str, Any]], fiches_ids: dict[str, int]) -> None:
    """Attache page/zone depuis fiche_champ_extrait quand une trace existe.

    Une citation reste vérifiable SANS trace (fiche + champ + valeur) ; la
    zone est un PLUS quand la fiche vient de l'extraction réelle.
    """
    if not citations or not fiches_ids:
        return
    cle_par_citation = [
        (citation, citation["champ"], citation.get("rang"))
        for citation in citations
        if citation.get("code_fiche") in fiches_ids
    ]
    if not cle_par_citation:
        return
    cursor.execute(
        "SELECT id_fiche, champ, rang, page, zone FROM fiche_champ_extrait WHERE id_fiche = ANY(%s) AND champ = ANY(%s)",
        (
            [fiches_ids[c["code_fiche"]] for c, _ch, _r in cle_par_citation],
            sorted({ch for _c, ch, _r in cle_par_citation}),
        ),
    )
    traces: dict[tuple[int, str, int | None], dict[str, Any]] = {}
    for id_fiche, champ, rang, page, zone in cursor.fetchall():
        traces[(int(id_fiche), str(champ), None if rang is None else int(rang))] = {"page": page, "zone": zone}
    for citation, champ, rang in cle_par_citation:
        id_fiche = fiches_ids.get(citation.get("code_fiche"))
        trace = traces.get((id_fiche, champ, rang)) if id_fiche is not None else None
        if trace is None and id_fiche is not None:
            trace = traces.get((id_fiche, champ, None))
        if trace is not None:
            citation["page"] = trace["page"]
            citation["zone"] = trace["zone"]
            if citation.get("rang") is None and trace.get("rang") is not None:
                citation["rang"] = trace["rang"]


def _ids_fiches(cursor: Any, codes: list[str]) -> dict[str, int]:
    if not codes:
        return {}
    cursor.execute(
        "SELECT code, id_fiche FROM fiche WHERE code = ANY(%s)",
        (codes,),
    )
    return {str(ligne[0]): int(ligne[1]) for ligne in cursor.fetchall()}


# ---------------------------------------------------------------------------
# Intents — chaque résolveur rend une Reponse ou None (non concerné)
# ---------------------------------------------------------------------------

def _resoudre_cote_fiche(cursor: Any, ctx: Contexte, inclure_a_valider: bool) -> Reponse | None:
    """« quelle est la SLU de la fiche 7792-SO ? » — lecture directe en
    fiche_cotes, les deux jeux comparés (jamais d'un seul deviné)."""
    if not (ctx.code and ctx.cote and not ctx.entre):
        return None
    colonne, libelle, unite, decimales = CHAMPS_COTES[ctx.cote]
    cursor.execute(
        f"""
        SELECT cd.jeu, cd.{colonne}
        FROM fiche_cotes cd JOIN fiche f ON f.id_fiche = cd.id_fiche
        WHERE f.code ILIKE %s AND {_statut_sql(inclure_a_valider)} AND cd.{colonne} IS NOT NULL
        ORDER BY (cd.jeu = 'finie') DESC, cd.jeu
        """,
        (ctx.code,),
    )
    lignes = cursor.fetchall()
    if lignes:
        valeurs = [(str(jeu), float(valeur)) for jeu, valeur in lignes]
        ids = _ids_fiches(cursor, [ctx.code])
        citations = [
            _citation(
                ctx.code,
                f"cotes.{jeu}.{colonne}",
                f"{libelle} (mesures {'finies' if jeu == 'finie' else 'dessin'})",
                "fiche_cotes",
                colonne,
                valeur,
                formater_valeur(valeur, unite, decimales),
            )
            for jeu, valeur in valeurs
        ]
        _completer_zones(cursor, citations, ids)
        distinctes = sorted({v for _jeu, v in valeurs})
        if len(distinctes) == 1:
            detail = (
                "identiques en mesures dessin et finies"
                if len(valeurs) > 1
                else f"jeu « {valeurs[0][0]} »"
            )
            return Reponse(
                ETAT_OK,
                f"{libelle} de la fiche {ctx.code} : {formater_valeur(distinctes[0], unite, decimales)} ({detail}).",
                citations,
            )
        interpretations = [
            {
                "lecture": f"{libelle} en mesures {'finies' if jeu == 'finie' else 'dessin'}",
                "reponse": f"{libelle} (mesures {'finies' if jeu == 'finie' else 'dessin'}) : {formater_valeur(valeur, unite, decimales)}",
                "citations": [citation],
            }
            for citation, (jeu, valeur) in zip(citations, valeurs)
        ]
        return Reponse(
            ETAT_AMBIGU,
            f"La fiche {ctx.code} porte {libelle} pour deux jeux de mesures différents — précisez « mesures finies » ou « mesures dessin ».",
            interpretations=interpretations,
        )
    # Aucune valeur : la fiche existe-t-elle au moins ?
    cursor.execute(
        f"SELECT id_fiche, code FROM fiche f WHERE f.code ILIKE %s AND {_statut_sql(inclure_a_valider)}",
        (ctx.code,),
    )
    fiche = cursor.fetchone()
    if fiche is None:
        return Reponse(
            ETAT_SANS_SOURCE,
            f"Je ne trouve pas dans les fiches la cote {libelle} pour « {ctx.code} » (fiche absente ou non validée — RG3).",
            pistes=["Vérifiez le code exact de la fiche.", "La fiche est peut-être encore dans la file de validation."],
        )
    return Reponse(
        ETAT_SANS_SOURCE,
        f"La fiche {ctx.code} ne porte aucune valeur {libelle} : non renseigné dans la base (aucune valeur inventée).",
        pistes=[f"Champs de cotes disponibles : {', '.join(lib for _c, lib, _u, _d in CHAMPS_COTES.values())}."],
    )


def _resoudre_plage_cote(cursor: Any, ctx: Contexte, inclure_a_valider: bool) -> Reponse | None:
    """« quelles fiches ont une SLU entre 6,5 et 6,7 m ? » — intervalle sur
    fiche_cotes. Sans précision de jeu, la lecture se fait en MESURES FINIES
    (jeu de production — défaut documenté, étiqueté dans la réponse) ;
    « en mesures dessin » interroge l'autre jeu explicitement."""
    if not (ctx.entre and ctx.cote):
        return None
    colonne, libelle, unite, decimales = CHAMPS_COTES[ctx.cote]
    jeu = ctx.jeu or "finie"
    cursor.execute(
        f"""
        SELECT f.code, cd.{colonne}
        FROM fiche_cotes cd JOIN fiche f ON f.id_fiche = cd.id_fiche
        WHERE cd.jeu = %s AND cd.{colonne} BETWEEN %s AND %s
          AND {_statut_sql(inclure_a_valider)}
        ORDER BY f.code
        """,
        (jeu, ctx.borne_min, ctx.borne_max),
    )
    lignes = [(str(code), float(valeur)) for code, valeur in cursor.fetchall()]

    jeu_affiche = jeu
    suffixe_jeu = f"mesures {'finies' if jeu == 'finie' else 'dessin'}"
    if ctx.jeu is None:
        suffixe_jeu += " — jeu par défaut, précisez « en mesures dessin » pour l'autre jeu"
    if not lignes:
        return Reponse(
            ETAT_OK,
            f"Aucune fiche avec {libelle} ({suffixe_jeu}) entre "
            f"{formater_valeur(ctx.borne_min, unite, decimales)} et {formater_valeur(ctx.borne_max, unite, decimales)} "
            "dans la base — comptage à zéro, aucune fiche citée.",
        )
    ids = _ids_fiches(cursor, [code for code, _v in lignes])
    citations = [
        _citation(code, f"cotes.{jeu_affiche}.{colonne}", f"{libelle} ({suffixe_jeu})",
                  "fiche_cotes", colonne, valeur, formater_valeur(valeur, unite, decimales))
        for code, valeur in lignes[:CITATIONS_MAX]
    ]
    _completer_zones(cursor, citations, ids)
    liste = ", ".join(f"{code} ({formater_valeur(valeur, unite, decimales)})" for code, valeur in lignes[:CITATIONS_MAX])
    if len(lignes) > CITATIONS_MAX:
        liste += f"… ({len(lignes) - CITATIONS_MAX} autres)"
    return Reponse(
        ETAT_OK,
        f"{len(lignes)} fiche(s) avec {libelle} ({suffixe_jeu}) entre "
        f"{formater_valeur(ctx.borne_min, unite, decimales)} et {formater_valeur(ctx.borne_max, unite, decimales)} : {liste}.",
        citations,
    )


def _resoudre_voiles_bateau(cursor: Any, ctx: Contexte, inclure_a_valider: bool) -> Reponse | None:
    """« quelles voiles pour le bateau 29er ? » — référentiel bateau puis
    fiches rattachées (types réellement enregistrés, jamais déduits)."""
    if not (ctx.veut_voiles and ctx.bateau_terme and not ctx.code):
        return None
    motif = f"%{ctx.bateau_terme}%"
    cursor.execute("SELECT id_bateau, nom, taille FROM bateau WHERE nom ILIKE %s OR coalesce(taille,'') ILIKE %s", (motif, motif))
    bateaux = cursor.fetchall()
    if not bateaux:
        cursor.execute("SELECT nom FROM bateau ORDER BY nom LIMIT 5")
        pistes = [f"Bateaux connus : {', '.join(str(ligne[0]) for ligne in cursor.fetchall())}."]
        return Reponse(
            ETAT_SANS_SOURCE,
            f"Je ne trouve pas le bateau « {ctx.bateau_terme} » dans les référentiels des fiches.",
            pistes=pistes,
        )
    ids_bateaux = [int(ligne[0]) for ligne in bateaux]
    noms = " / ".join(f"{ligne[1]}{(' ' + str(ligne[2])) if ligne[2] else ''}" for ligne in bateaux)
    cursor.execute(
        f"""
        SELECT v.code, v.titre, tv.libelle
        FROM fiche f
        JOIN v_fiche_recherche v ON v.id_fiche = f.id_fiche
        LEFT JOIN type_voile tv ON tv.id_type_voile = f.id_type_voile
        WHERE f.id_bateau = ANY(%s) AND {_statut_sql(inclure_a_valider)}
        ORDER BY v.code
        """,
        (ids_bateaux,),
    )
    fiches = cursor.fetchall()
    if not fiches:
        return Reponse(
            ETAT_OK,
            f"Le bateau {noms} existe dans les référentiels mais n'a aucune fiche validée dans la base.",
        )
    ids = _ids_fiches(cursor, [str(ligne[0]) for ligne in fiches])
    citations = [
        _citation(str(ligne[0]), "fiche.type_voile", "Type de voile", "fiche", "id_type_voile", ligne[2], str(ligne[2] or "—"))
        for ligne in fiches[:CITATIONS_MAX]
    ]
    _completer_zones(cursor, citations, ids)
    liste = ", ".join(f"{ligne[2] or 'type inconnu'} ({ligne[0]})" for ligne in fiches[:CITATIONS_MAX])
    return Reponse(ETAT_OK, f"Voiles enregistrées pour le bateau {noms} : {liste}.", citations)


def _resoudre_comptage(cursor: Any, ctx: Contexte, inclure_a_valider: bool) -> Reponse | None:
    """« combien de fiches de type portant en 2024 ? » — comptage par filtres
    explicites, chaque fiche comptée est citée (vérifiable un à un)."""
    if not ctx.combien:
        return None
    conditions = [_statut_sql(inclure_a_valider)]
    parametres: list[Any] = []
    critere_lu = []
    if ctx.terme_type:
        conditions.append(CONDITIONS_TYPE[ctx.terme_type])
        critere_lu.append(f"type « {ctx.terme_type} » ({DESCRIPTIONS_TYPE[ctx.terme_type]})")
    if ctx.annee:
        conditions.append("extract(year FROM v.date_edition)::int = %s")
        parametres.append(ctx.annee)
        critere_lu.append(f"année {ctx.annee}")
    if not critere_lu:
        critere_lu.append("toutes, sans filtre")
    cursor.execute(
        f"""
        SELECT v.code, tv.libelle, extract(year FROM v.date_edition)::int
        FROM fiche f
        JOIN v_fiche_recherche v ON v.id_fiche = f.id_fiche
        LEFT JOIN type_voile tv ON tv.id_type_voile = f.id_type_voile
        WHERE {" AND ".join(conditions)}
        ORDER BY v.code
        """,
        parametres,
    )
    lignes = cursor.fetchall()
    if not lignes:
        return Reponse(
            ETAT_OK,
            f"0 fiche pour ce comptage ({' ; '.join(critere_lu)}) dans la base — comptage à zéro, aucune fiche citée.",
        )
    ids = _ids_fiches(cursor, [str(ligne[0]) for ligne in lignes])
    citations = [
        _citation(str(ligne[0]), "fiche.code", "Fiche comptée", "fiche", "code", ligne[0], str(ligne[0]))
        for ligne in lignes[:CITATIONS_MAX]
    ]
    _completer_zones(cursor, citations, ids)
    liste = ", ".join(str(ligne[0]) for ligne in lignes[:CITATIONS_MAX]) + ("…" if len(lignes) > CITATIONS_MAX else "")
    return Reponse(
        ETAT_OK,
        f"{len(lignes)} fiche(s) pour ce comptage ({' ; '.join(critere_lu)}) : {liste}.",
        citations,
    )


def _lignes_galon(cursor: Any, code: str, bande: str | None, inclure_a_valider: bool) -> list[tuple[Any, ...]]:
    where_bande = "AND g.bande = %s" if bande else ""
    parametres: list[Any] = [code]
    if bande:
        parametres.append(bande)
    cursor.execute(
        f"""
        SELECT g.bande, g.matiere, g.couleur, g.largeur_mm, g.grammage_g_m2
        FROM fiche_galon g JOIN fiche f ON f.id_fiche = g.id_fiche
        WHERE f.code ILIKE %s AND {_statut_sql(inclure_a_valider)} {where_bande}
        ORDER BY g.bande
        """,
        parametres,
    )
    return cursor.fetchall()


def _resoudre_galon(cursor: Any, ctx: Contexte, inclure_a_valider: bool) -> Reponse | None:
    """« quelle matière pour le galon de bordure ? » — lecture directe en
    fiche_galon, avec ou sans code de fiche."""
    demande_galon = bool(re.search(r"\bgalon\b", ctx.normalisee))
    if not demande_galon:
        return None
    if ctx.code:
        lignes = _lignes_galon(cursor, ctx.code, ctx.bande_galon, inclure_a_valider)
        if not lignes:
            return Reponse(
                ETAT_SANS_SOURCE,
                f"Je ne trouve pas dans les fiches de galon{f' de {ctx.bande_galon}' if ctx.bande_galon else ''} pour « {ctx.code} ».",
            )
        attribut = ctx.attribut_galon or "matiere"
        colonne, libelle, unite, decimales = ATTRIBUTS_GALON[attribut]
        ids = _ids_fiches(cursor, [ctx.code])
        citations = []
        valeurs: list[str] = []
        for bande, matiere, couleur, largeur, grammage in lignes:
            valeur = {"matiere": matiere, "couleur": couleur, "largeur": largeur, "grammage": grammage}[attribut]
            if valeur is None:
                continue
            citations.append(
                _citation(ctx.code, f"galon.{bande}", f"{libelle} (bande {bande})", "fiche_galon", colonne, valeur,
                          formater_valeur(valeur, unite, decimales))
            )
            valeurs.append(f"{bande} : {formater_valeur(valeur, unite, decimales)}")
        if not citations:
            return Reponse(
                ETAT_SANS_SOURCE,
                f"Le galon de la fiche {ctx.code} ne porte pas de {libelle.lower()} renseignée (non renseigné, rien d'inventé).",
            )
        _completer_zones(cursor, citations, ids)
        return Reponse(
            ETAT_OK,
            f"{libelle} des galons de la fiche {ctx.code} : " + " ; ".join(valeurs) + ".",
            citations,
        )
    # Sans code : on balaie les fiches (galon de bordure ⇢ question générique).
    bande = ctx.bande_galon or "bordure"
    attribut = ctx.attribut_galon or "matiere"
    colonne, libelle, unite, decimales = ATTRIBUTS_GALON[attribut]
    cursor.execute(
        f"""
        SELECT f.code, g.{colonne}
        FROM fiche_galon g JOIN fiche f ON f.id_fiche = g.id_fiche
        WHERE g.bande = %s AND g.{colonne} IS NOT NULL AND {_statut_sql(inclure_a_valider)}
        ORDER BY f.code
        LIMIT %s
        """,
        (bande, CITATIONS_MAX + 1),
    )
    lignes = cursor.fetchall()
    if not lignes:
        return Reponse(
            ETAT_SANS_SOURCE,
            f"Je ne trouve pas dans les fiches de galon de {bande} avec {libelle.lower()} renseignée.",
        )
    if len(lignes) == 1:
        code, valeur = str(lignes[0][0]), lignes[0][1]
        ids = _ids_fiches(cursor, [code])
        citations = [_citation(code, f"galon.{bande}", f"{libelle} (bande {bande})", "fiche_galon", colonne, valeur,
                               formater_valeur(valeur, unite, decimales))]
        _completer_zones(cursor, citations, ids)
        return Reponse(
            ETAT_OK,
            f"{libelle} du galon de {bande} (seule fiche concernée, {code}) : {formater_valeur(valeur, unite, decimales)}.",
            citations,
        )
    ids = _ids_fiches(cursor, [str(ligne[0]) for ligne in lignes[:CITATIONS_MAX]])
    citations = [
        _citation(str(code), f"galon.{bande}", f"{libelle} (bande {bande})", "fiche_galon", colonne, valeur,
                  formater_valeur(valeur, unite, decimales))
        for code, valeur in lignes[:CITATIONS_MAX]
    ]
    _completer_zones(cursor, citations, ids)
    liste = ", ".join(f"{code} : {formater_valeur(valeur, unite, decimales)}" for code, valeur in lignes[:CITATIONS_MAX])
    return Reponse(
        ETAT_OK,
        f"{libelle} du galon de {bande}, par fiche ({len(lignes)} au total) : {liste}.",
        citations,
    )


def _resoudre_matiere_ambigue(cursor: Any, ctx: Contexte, inclure_a_valider: bool) -> Reponse | None:
    """« quelle est la matière de la fiche 7792-SO ? » — « matière » sans
    préciser tissu, galon ou renfort : on LISTE les lectures possibles, chacune
    sourcée (jamais un choix silencieux)."""
    if not (ctx.code and ctx.demande_matiere and not ctx.bande_galon and not re.search(r"\bgalon\b|\btissu\b|\brenfort\b|\bepaisseur\b", ctx.normalisee)):
        return None
    interpretations: list[dict[str, Any]] = []

    cursor.execute(
        f"""
        SELECT fm.designation_texte, fm.role, fm.niveau
        FROM fiche_materiau fm JOIN fiche f ON f.id_fiche = fm.id_fiche
        WHERE f.code ILIKE %s AND {_statut_sql(inclure_a_valider)} AND fm.designation_texte IS NOT NULL
        ORDER BY fm.niveau NULLS LAST
        """,
        (ctx.code,),
    )
    materiaux = cursor.fetchall()
    if materiaux:
        citations = [
            _citation(ctx.code, f"materiau.epaisseur_{int(niveau):02d}" if niveau else "materiau.tissu_principal",
                      f"Matière ({role})", "fiche_materiau", f"epaisseur_{int(niveau):02d}" if niveau else "tissu_principal",
                      designation, str(designation), {"rang": niveau, "page": None, "zone": None})
            for designation, role, niveau in materiaux
        ]
        interpretations.append(
            {
                "lecture": "matière du tissu (épaisseurs de panneau)",
                "reponse": "Matières du tissu : " + ", ".join(f"{str(designation)} ({role})" for designation, role, _n in materiaux),
                "citations": citations,
            }
        )

    lignes = _lignes_galon(cursor, ctx.code, None, inclure_a_valider)
    galons_matiere = [(str(bande), matiere) for bande, matiere, _c, _l, _g in lignes if matiere]
    if galons_matiere:
        citations = [
            _citation(ctx.code, f"galon.{bande}", f"Matière du galon (bande {bande})", "fiche_galon", "matiere", matiere, str(matiere))
            for bande, matiere in galons_matiere
        ]
        interpretations.append(
            {
                "lecture": "matière des galons",
                "reponse": "Matières des galons : " + ", ".join(f"{matiere} ({bande})" for bande, matiere in galons_matiere),
                "citations": citations,
            }
        )

    cursor.execute(
        f"""
        SELECT r.repere, r.matiere
        FROM fiche_renfort r JOIN fiche f ON f.id_fiche = r.id_fiche
        WHERE f.code ILIKE %s AND {_statut_sql(inclure_a_valider)} AND r.matiere IS NOT NULL
        ORDER BY r.repere
        """,
        (ctx.code,),
    )
    renforts = cursor.fetchall()
    if renforts:
        citations = [
            _citation(ctx.code, "renfort.note", f"Matière du renfort ({repere or 'n° non précisé'})", "fiche_renfort", "matiere",
                      matiere, str(matiere))
            for repere, matiere in renforts
        ]
        interpretations.append(
            {
                "lecture": "matière des renforts",
                "reponse": "Matières des renforts : " + ", ".join(f"{matiere} ({repere or 'sans repère'})" for repere, matiere in renforts),
                "citations": citations,
            }
        )

    if not interpretations:
        return Reponse(
            ETAT_SANS_SOURCE,
            f"Je ne trouve pas dans les fiches de matière renseignée pour « {ctx.code} ».",
        )
    return Reponse(
        ETAT_AMBIGU,
        f"« Matière » est ambigu pour la fiche {ctx.code} : précisez tissu, galon ou renfort — voici les valeurs consignées pour chaque lecture.",
        interpretations=interpretations,
    )


def _resoudre_surplus(cursor: Any, ctx: Contexte, inclure_a_valider: bool) -> Reponse | None:
    """« quel est le surplus de jonction ? » — RG5 : « ~ » est consigné tel
    quel (sans objet) ; l'assistant le DIT, il n'invente pas de valeur."""
    if not (ctx.code and ctx.demande_surplus):
        return None
    cursor.execute(
        f"""
        SELECT j.nature, j.surplus
        FROM fiche_jonction j JOIN fiche f ON f.id_fiche = j.id_fiche
        WHERE f.code ILIKE %s AND {_statut_sql(inclure_a_valider)} AND j.nature = 'surplus'
        """,
        (ctx.code,),
    )
    ligne = cursor.fetchone()
    if ligne is None:
        return Reponse(
            ETAT_SANS_SOURCE,
            f"Je ne trouve pas dans les fiches de surplus de jonction pour « {ctx.code} » (non renseigné).",
        )
    surplus = ligne[1]
    if _est_sans_objet(None if surplus is None else str(surplus)):
        citations = [
            _citation(ctx.code, "jonction.surplus", "Surplus de jonction (consigné « sans objet »)", "fiche_jonction",
                      "surplus", surplus, f"« {surplus} » (sans objet — RG5)")
        ]
        _completer_zones(cursor, citations, _ids_fiches(cursor, [ctx.code]))
        return Reponse(
            ETAT_OK,
            f"Surplus de jonction de la fiche {ctx.code} : consigné « {surplus} » dans la fiche — sans objet (non applicable), aucune valeur chiffrée.",
            citations,
        )
    if surplus is None:
        return Reponse(
            ETAT_SANS_SOURCE,
            f"Le surplus de jonction de la fiche {ctx.code} n'est pas renseigné dans la base (aucune valeur inventée).",
        )
    citations = [
        _citation(ctx.code, "jonction.surplus", "Surplus de jonction", "fiche_jonction", "surplus", surplus, str(surplus))
    ]
    _completer_zones(cursor, citations, _ids_fiches(cursor, [ctx.code]))
    return Reponse(ETAT_OK, f"Surplus de jonction de la fiche {ctx.code} : {surplus}.", citations)


def _resoudre_option(cursor: Any, ctx: Contexte, inclure_a_valider: bool) -> Reponse | None:
    """« la fiche X a-t-elle une protection anti-uv ? » — options consignées
    (valeur booléenne ou texte), jamais déduites."""
    if not ctx.code or not re.search(r"\boption\b|\banti[- ]?uv\b|\bvelcro\b|\bchaussette\b|\bsac\b|\bemmagasineur\b", ctx.normalisee):
        return None
    m = re.search(r"(protection[-_ ]?anti[-_ ]?uv|velcro[-_ ]?anti[-_ ]?deroulement|retenue[-_ ]?contre[-_ ]?ecoute|chaussette|sac|emmagasineur|bout[-_ ]?de[-_ ]?manoeuvre|v[-_ ]?trim)", ctx.normalisee)
    if m is None:
        return None
    codes_options = {
        "protection_anti_uv": "protection anti-UV",
        "velcro_anti_deroulement": "velcro anti-déroulement",
        "retenue_contre_ecoute": "retenue contre écoute",
        "chaussette": "chaussette",
        "sac": "sac",
        "emmagasineur": "emmagasineur",
        "bout_de_manoeuvre": "bout-de-manœuvre",
        "v_trim": "v-trim",
    }
    code_option = m.group(1).replace(" ", "_").replace("-", "_")
    cursor.execute(
        f"""
        SELECT o.code, o.valeur_bool, o.valeur_texte
        FROM fiche_option o JOIN fiche f ON f.id_fiche = o.id_fiche
        WHERE f.code ILIKE %s AND {_statut_sql(inclure_a_valider)} AND o.code = %s
        """,
        (ctx.code, code_option),
    )
    ligne = cursor.fetchone()
    if ligne is None:
        return Reponse(
            ETAT_SANS_SOURCE,
            f"Je ne trouve pas dans les fiches l'option « {codes_options.get(code_option, code_option)} » pour « {ctx.code} ».",
        )
    _code, valeur_bool, valeur_texte = ligne
    if valeur_bool is not None:
        lu = "Oui" if valeur_bool else "Non"
        citations = [_citation(ctx.code, f"option.{code_option}", f"Option {codes_options.get(code_option, code_option)}",
                               "fiche_option", "valeur_bool", valeur_bool, lu)]
        _completer_zones(cursor, citations, _ids_fiches(cursor, [ctx.code]))
        return Reponse(
            ETAT_OK,
            f"Option « {codes_options.get(code_option, code_option)} » de la fiche {ctx.code} : {lu} (consigné tel quel dans la fiche).",
            citations,
        )
    if valeur_texte is not None:
        if _est_sans_objet(str(valeur_texte)):
            citations = [_citation(ctx.code, f"option.{code_option}", f"Option {codes_options.get(code_option, code_option)}",
                                   "fiche_option", "valeur_texte", valeur_texte, f"« {valeur_texte} » (sans objet — RG5)")]
            _completer_zones(cursor, citations, _ids_fiches(cursor, [ctx.code]))
            return Reponse(
                ETAT_OK,
                f"Option « {codes_options.get(code_option, code_option)} » de la fiche {ctx.code} : consignée « {valeur_texte} » — sans objet (non applicable).",
                citations,
            )
        citations = [_citation(ctx.code, f"option.{code_option}", f"Option {codes_options.get(code_option, code_option)}",
                               "fiche_option", "valeur_texte", valeur_texte, str(valeur_texte))]
        _completer_zones(cursor, citations, _ids_fiches(cursor, [ctx.code]))
        return Reponse(
            ETAT_OK,
            f"Option « {codes_options.get(code_option, code_option)} » de la fiche {ctx.code} : « {valeur_texte} » (consigné tel quel).",
            citations,
        )
    return Reponse(
        ETAT_SANS_SOURCE,
        f"L'option « {codes_options.get(code_option, code_option)} » de la fiche {ctx.code} n'a aucune valeur renseignée.",
    )


def _resoudre_champ_fiche(cursor: Any, ctx: Contexte, inclure_a_valider: bool) -> Reponse | None:
    """Champ de tête de fiche (client, bateau, titre, quantité…) par code."""
    if not (ctx.code and ctx.champ_fiche):
        return None
    colonne, libelle = CHAMPS_FICHE[ctx.champ_fiche]
    cursor.execute(
        f"""
        SELECT v.{colonne}
        FROM v_fiche_recherche v JOIN fiche f ON f.id_fiche = v.id_fiche
        WHERE v.code ILIKE %s AND {_statut_sql(inclure_a_valider)}
        """,
        (ctx.code,),
    )
    ligne = cursor.fetchone()
    if ligne is None or ligne[0] in (None, ""):
        return Reponse(
            ETAT_SANS_SOURCE,
            f"Je ne trouve pas dans les fiches le {libelle.lower()} pour « {ctx.code} » (fiche absente ou non validée — RG3).",
        )
    cle_trace = {"client": "fiche.client", "bateau": "fiche.bateau", "titre": "fiche.titre"}.get(ctx.champ_fiche, f"fiche.{colonne}")
    citations = [_citation(ctx.code, cle_trace, libelle, "fiche", colonne, ligne[0], str(ligne[0]))]
    _completer_zones(cursor, citations, _ids_fiches(cursor, [ctx.code]))
    return Reponse(ETAT_OK, f"{libelle} de la fiche {ctx.code} : {ligne[0]}.", citations)


# Mots-outils français : retirés des pistes (pas des critères métier).
MOTS_OUTILS = frozenset(
    {"quelle", "quelles", "quel", "quels", "est", "sont", "les", "des", "une", "pour", "avec", "dans", "sur", "par",
     "que", "qui", "quoi", "comment", "pourquoi", "fiche", "donc", "ainsi", "alors", "peut", "etre", "avoir"}
)


def _resoudre_defaut(cursor: Any, ctx: Contexte, inclure_a_valider: bool, ts_config: str) -> Reponse:
    """Aucune intention reconnue : REFUS explicite. La recherche hybride
    existante sert uniquement à proposer des PISTES (jamais des valeurs)."""
    termes = [t for t in re.split(r"[^\w']+", ctx.normalisee) if len(t) >= 3 and t not in MOTS_OUTILS][:6]
    pistes: list[str] = []
    if termes:
        modele = " ".join(termes)
        cursor.execute(
            f"""
            SELECT v.code, v.titre
            FROM fiche f JOIN v_fiche_recherche v ON v.id_fiche = f.id_fiche
            WHERE f.search_vector @@ websearch_to_tsquery(%s, %s)
              AND {_statut_sql(inclure_a_valider)}
            ORDER BY ts_rank_cd(f.search_vector, websearch_to_tsquery(%s, %s), 16) DESC, v.code
            LIMIT 3
            """,
            (ts_config, modele, ts_config, modele),
        )
        pistes = [
            f"Rechercher « {str(ligne[0])} » ({ligne[1]}) dans l'écran Recherche pour examiner la fiche vous-même."
            for ligne in cursor.fetchall()
        ]
        pistes.append("Reformulez en nommant la fiche (ex. « quelle est la SLU de la fiche 7792-SO ? ») ou le champ demandé.")
    return Reponse(
        ETAT_SANS_SOURCE,
        "Je ne trouve pas cette information dans les fiches : la base ne contient pas ce champ, je n'invente aucune valeur.",
        pistes=pistes,
    )


# Ordre des intentions : la première qui se déclare l'emporte. Les résolveurs
# fiche-étroit passent avant les lectures larges ; _resoudre_defaut répond
# toujours (refus) — il clôt la chaîne.
_INTENTS: tuple[Callable[[Any, Contexte, bool], Reponse | None], ...] = (
    _resoudre_plage_cote,
    _resoudre_cote_fiche,
    _resoudre_surplus,
    _resoudre_option,
    _resoudre_galon,
    _resoudre_matiere_ambigue,
    _resoudre_voiles_bateau,
    _resoudre_comptage,
    _resoudre_champ_fiche,
)


def analyser(index: Any, question: str, inclure_a_valider: bool = False) -> dict[str, Any]:  # noqa: ANN401
    """Chaîne complète : contexte → première intention qui se déclare →
    réponse + citations + journal. N'ouvre qu'UNE connexion."""
    debut = time.perf_counter()
    ctx = analyser_question(question)
    if not getattr(index, "is_postgres", False):
        raise HTTPException(
            status_code=503,
            detail=(
                "L'assistant sourcé exige la couche métier PostgreSQL (§17.1) : "
                "configurer database_url (migrations 006-009)."
            ),
        )
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            ts_config = index._postgres_ts_config(connexion)
            reponse: Reponse = Reponse(ETAT_SANS_SOURCE, "")
            for intent in _INTENTS:
                resolu = intent(cursor, ctx, inclure_a_valider)
                if resolu is not None:
                    reponse = resolu
                    break
            else:
                reponse = _resoudre_defaut(cursor, ctx, inclure_a_valider, ts_config)
            duree_ms = round((time.perf_counter() - debut) * 1000.0, 2)
            # Journal « comme les recherches » (§11.3) : même table, canal
            # distinct dans filtres JSONB — nb_resultats = nombre de citations
            # (0 pour un refus), matière première de la mesure d'usage réel.
            cursor.execute(
                "INSERT INTO recherche_log (requete, filtres, nb_resultats) VALUES (%s, %s::jsonb, %s)",
                (
                    ctx.brute,
                    json.dumps(
                        {
                            "canal": "assistant",
                            "etat": reponse.etat,
                            "duree_ms": duree_ms,
                            "nb_citations": len(reponse.citations),
                            "inclure_a_valider": inclure_a_valider,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    len(reponse.citations),
                ),
            )
    return {
        "question": ctx.brute,
        "etat": reponse.etat,
        "reponse": reponse.reponse,
        "citations": reponse.citations,
        "interpretations": reponse.interpretations,
        "pistes": reponse.pistes,
        "duree_ms": duree_ms,
    }


def poser(index: Any, question: str, inclure_a_valider: bool = False) -> dict[str, Any]:  # noqa: ANN401
    """Point d'entrée public : sérialise les analyses (un poste = un CPU) et
    rend TOUJOURS le contrat JSON (occupe / indisponible inclus)."""
    question = (question or "").strip()
    if not question:
        return {
            "question": "",
            "etat": ETAT_SANS_SOURCE,
            "reponse": "Posez une question sur les fiches (ex. « quelle est la SLU de la fiche 7792-SO ? »).",
            "citations": [],
            "interpretations": [],
            "pistes": [],
            "duree_ms": 0.0,
        }
    if not _VERROU_ANALYSE.acquire(blocking=False):
        return {
            "question": question,
            "etat": ETAT_OCCUPE,
            "reponse": "Une autre question est en cours d'analyse — redemandez dans un instant.",
            "citations": [],
            "interpretations": [],
            "pistes": [],
            "duree_ms": 0.0,
        }
    try:
        return analyser(index, question, inclure_a_valider)
    finally:
        _VERROU_ANALYSE.release()


# ---------------------------------------------------------------------------
# Route FastAPI — POST /assistant
# ---------------------------------------------------------------------------

class QuestionAssistant(BaseModel):
    question: str = Field(min_length=1, max_length=LONGUEUR_QUESTION_MAX)
    inclure_a_valider: bool = False


def enregistrer_routes_assistant(
    app: Any,  # noqa: ANN401 - FastAPI
    index: Any,  # noqa: ANN401 - SearchIndex
    config: Any,  # noqa: ANN401 - AppConfig
    verifier_auth: Callable[[Any, str | None], None],
    metriques: dict[str, Any] | None = None,
) -> None:
    """Enregistre ``POST /assistant`` (nom documenté dans docs/API.md)."""

    @app.post("/assistant")
    def route_assistant(
        corps: QuestionAssistant,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        if not getattr(index, "is_postgres", False):
            raise HTTPException(
                status_code=503,
                detail={
                    "etat": ETAT_INDISPONIBLE,
                    "message": (
                        "L'assistant sourcé exige la couche métier PostgreSQL (§17.1). "
                        "Mode SQLite : seuls /search (fichiers) et les fonctions d'indexation restent disponibles."
                    ),
                },
            )
        try:
            reponse = poser(index, corps.question, corps.inclure_a_valider)
        except HTTPException:
            raise
        except Exception as exc:
            LOGGER.exception("Échec de l'assistant sur %r — conséquence : 503 indisponible.", corps.question)
            raise HTTPException(
                status_code=503,
                detail={"etat": ETAT_INDISPONIBLE, "message": "Analyse impossible pour le moment (couche métier)."},
            ) from exc
        if metriques is not None:
            metriques["assistant_requests"] = int(metriques.get("assistant_requests", 0)) + 1
            if reponse["etat"] == ETAT_SANS_SOURCE:
                metriques["assistant_sans_source"] = int(metriques.get("assistant_sans_source", 0)) + 1
        return reponse

    @app.get("/assistant/etat")
    def route_etat_assistant(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        verifier_auth(config, token)
        disponible = bool(getattr(index, "is_postgres", False)) and _VERROU_ANALYSE.acquire(blocking=False)
        if disponible:
            _VERROU_ANALYSE.release()
        return {"etat": ETAT_OK if disponible else ETAT_OCCUPE, "postgres": bool(getattr(index, "is_postgres", False))}
