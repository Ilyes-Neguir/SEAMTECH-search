"""Cartographie du corpus — Lot 0 du plan « comparaison et réutilisation ».

Pourquoi ce module existe.

Le projet veut comparer des voiles entre elles pour décider si une voile
existante peut resservir. Avant d'écrire la moindre règle, il faut mesurer à
quoi ressemble réellement le corpus : combien de fiches sont comparables entre
elles, combien de formes différentes prennent les mêmes valeurs, combien de
fiches franchiraient un seuil de validation automatique.

Ces mesures ne sont pas décoratives : elles ont déjà tranché une décision de
conception. Sur le corpus réel, les groupes « même bateau + même type de voile »
plafonnent à trois fiches, alors que le module de confiance exige cinq
échantillons pour produire un score statistique. La comparaison doit donc
reposer sur des **écarts mesurés** (des faits, valables à deux fiches) et non
sur une statistique qui ne serait presque jamais calculable.

Deux principes tenus ici :

* **La logique est séparée du SQL.** Les fonctions pures transforment des lignes
  déjà agrégées ; elles sont testables sans base et donc réellement rejouées.
  Les fonctions qui parlent à PostgreSQL sont minces. Aucune agrégation n'est
  faite en Python quand ``GROUP BY`` suffit — règle déjà posée par
  ``qualite/tableau.py``, reconduite ici.
* **On ne fusionne pas ce qu'on n'a pas mesuré.** Une différence de casse,
  d'espace ou d'accent est une variante **certaine**. Le fait qu'une valeur soit
  contenue dans une autre (« ocean » dans « medium ocean ») n'est qu'une
  variante **probable** : elle est signalée à l'exploitante avec ses comptes,
  jamais fusionnée par la machine.

Lecture seule, comme tout le tableau de bord : aucune de ces fonctions n'écrit.
Aucune donnée nominative n'en sort : uniquement des comptes, des distributions
et des clés normalisées génériques.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Iterable, Sequence

LOGGER = logging.getLogger("seamtech_search.qualite.cartographie")

#: Seuil à partir duquel un groupe peut porter un score statistique.
#: Volontairement égal à ``confiance_reutilisation.CONFIANCE_MIN_ECHANTILLONS`` :
#: deux seuils différents pour la même promesse d'honnêteté produiraient deux
#: réponses différentes à la même question. Un test verrouille l'alignement.
SEUIL_STATISTIQUE = 5

#: Au-delà de 9 fiches dans un groupe, les tailles sont regroupées : le rapport
#: reste lisible même sur un corpus très fourni.
_TRANCHE_OUVERTE = "10+"

#: Longueur minimale d'une clé pour être proposée comme variante probable.
#: Sans ce garde-fou, une valeur d'une ou deux lettres serait proposée comme
#: variante de tout ce qui la contient — un bruit inutile.
_LONGUEUR_MIN_CLE_PROBABLE = 4

#: Colonnes interrogeables par :func:`variantes_valeurs`. Liste fermée : une
#: colonne arbitraire est refusée, donc aucune construction SQL dynamique n'est
#: possible depuis l'extérieur (un test verrouille ce refus).
_COLONNES_AUTORISEES = frozenset(
    {
        "gamme",
        "type_voile",
        "modele_bateau_brut",
        "designation_brute",
        "methode_principale",
    }
)

#: Dimensions et attributs dont on mesure la complétude dans ``sail_extractions``.
_COLONNES_COMPLETUDE = (
    "slu_m",
    "sle_m",
    "sf_m",
    "shw_m",
    "spa_m2",
    "tetiere_m",
    "poids_kg",
    "type_voile",
    "gamme",
    "modele_bateau_brut",
    "boat_model_id",
)

_NON_ALPHANUMERIQUE = re.compile(r"[^0-9a-z]+")


# ---------------------------------------------------------------------------
# Fonctions pures — aucune base requise
# ---------------------------------------------------------------------------


def cle_normalisation(valeur: object) -> str:
    """Forme comparable d'une valeur : sans casse, sans accent, sans ponctuation.

    « medium Ocean », « Medium Ocean » et « MEDIUM OCEAN » donnent la même clé.
    Les accents sont repliés (« Océan » → « ocean ») et la ponctuation devient
    une espace (« semi-lattée » → « semi lattee »), parce que les documents
    réels écrivent la même notion des deux façons.

    Une chaîne vide pour une valeur absente : ce n'est pas une variante, c'est
    une donnée manquante.
    """
    if valeur is None:
        return ""
    texte = unicodedata.normalize("NFKD", str(valeur))
    texte = "".join(caractere for caractere in texte if not unicodedata.combining(caractere))
    texte = _NON_ALPHANUMERIQUE.sub(" ", texte.casefold())
    return " ".join(texte.split())


def histogramme_groupes(tailles: Iterable[int]) -> dict[str, Any]:
    """Distribution des tailles de groupes comparables.

    Entrée : une taille par groupe (un groupe = un couple bateau + type de
    voile). Sortie : le nombre de groupes, le nombre de fiches couvertes, la
    distribution des tailles, et surtout **la part des fiches qui peuvent
    porter un score statistique** — c'est-à-dire celles appartenant à un
    groupe d'au moins :data:`SEUIL_STATISTIQUE` fiches.

    Un corpus vide rend des zéros et un ``None`` pour la part, jamais une
    division par zéro.
    """
    tailles_entieres = [int(taille) for taille in tailles if taille]
    distribution = {str(taille): 0 for taille in range(1, 10)}
    distribution[_TRANCHE_OUVERTE] = 0
    for taille in tailles_entieres:
        cle = str(taille) if 1 <= taille <= 9 else _TRANCHE_OUVERTE
        distribution[cle] += 1

    total_fiches = sum(tailles_entieres)
    groupes_statistiques = [taille for taille in tailles_entieres if taille >= SEUIL_STATISTIQUE]
    fiches_statistiques = sum(groupes_statistiques)

    return {
        "definition": (
            "Répartition des fiches par groupe comparable (même bateau, même type de voile). "
            "« part_couverte_statistique » = part des fiches appartenant à un groupe d'au moins "
            f"{SEUIL_STATISTIQUE} fiches, donc les seules à pouvoir porter un score statistique."
        ),
        "unite": "groupe + fiche",
        "periode": "instantané",
        "seuil_statistique": SEUIL_STATISTIQUE,
        "nb_groupes": len(tailles_entieres),
        "nb_fiches_couvertes": total_fiches,
        "distribution": distribution,
        "groupes_statistiques": len(groupes_statistiques),
        "fiches_en_groupe_statistique": fiches_statistiques,
        "part_couverte_statistique": (fiches_statistiques / total_fiches) if total_fiches else None,
    }


def regrouper_variantes(lignes: Sequence[tuple[object, int]]) -> list[dict[str, Any]]:
    """Variantes **certaines** : une même clé normalisée écrite de plusieurs façons.

    Entrée : ``(valeur brute, nombre d'occurrences)``. Ne sont retournées que
    les clés ayant **plus d'une** forme distincte — une valeur écrite d'une
    seule façon n'a rien à corriger. Triées par impact décroissant, pour qu'on
    répare d'abord ce qui concerne le plus de fiches.
    """
    par_cle: dict[str, dict[str, Any]] = {}
    for valeur, nombre in lignes:
        cle = cle_normalisation(valeur)
        if not cle:
            continue
        entree = par_cle.setdefault(cle, {"formes": {}, "total": 0})
        forme = str(valeur)
        entree["formes"][forme] = entree["formes"].get(forme, 0) + int(nombre or 0)
        entree["total"] += int(nombre or 0)

    groupes = [
        {
            "cle": cle,
            "nb_formes": len(entree["formes"]),
            "total": entree["total"],
            "formes": [
                {"valeur": forme, "nb": nb}
                for forme, nb in sorted(entree["formes"].items(), key=lambda couple: (-couple[1], couple[0]))
            ],
        }
        for cle, entree in par_cle.items()
        if len(entree["formes"]) > 1
    ]
    groupes.sort(key=lambda groupe: (-groupe["total"], groupe["cle"]))
    return groupes


def variantes_probables(lignes: Sequence[tuple[object, int]]) -> list[dict[str, Any]]:
    """Variantes **probables** : une clé entièrement contenue dans une autre.

    « ocean » est contenu dans « medium ocean ». Est-ce la même gamme, ou deux
    gammes distinctes ? La machine ne le sait pas, et deviner reviendrait à
    fusionner des notions différentes. On signale donc le candidat **avec ses
    comptes**, en laissant ``confirmee_par`` à ``None`` : seule l'exploitante
    tranche.

    Les clés trop courtes (moins de :data:`_LONGUEUR_MIN_CLE_PROBABLE`
    caractères) sont écartées : sinon toute lettre serait une variante de tout.
    """
    totaux: dict[str, int] = {}
    for valeur, nombre in lignes:
        cle = cle_normalisation(valeur)
        if not cle:
            continue
        totaux[cle] = totaux.get(cle, 0) + int(nombre or 0)

    cles = sorted(totaux)
    candidats: list[dict[str, Any]] = []
    for cle_courte in cles:
        if len(cle_courte) < _LONGUEUR_MIN_CLE_PROBABLE:
            continue
        for cle_longue in cles:
            if cle_longue == cle_courte or len(cle_longue) <= len(cle_courte):
                continue
            if f" {cle_courte} " in f" {cle_longue} ":
                candidats.append(
                    {
                        "cle_courte": cle_courte,
                        "cle_longue": cle_longue,
                        "nb_court": totaux[cle_courte],
                        "nb_long": totaux[cle_longue],
                        "confirmee_par": None,
                        "motif": "la valeur courte est entièrement contenue dans la valeur longue",
                    }
                )
    candidats.sort(key=lambda candidat: (-candidat["nb_long"], -candidat["nb_court"]))
    return candidats


def distribution_scores(
    scores: Sequence[float | None],
    seuils: Sequence[float] = (0.80, 0.85, 0.90, 0.95),
) -> dict[str, Any]:
    """Répartition des scores de qualité, et effet de chaque seuil de validation.

    C'est la réponse chiffrée à la question du lot 3 : « à 0,90, combien de
    fiches seraient validées automatiquement, et combien partiraient en revue
    humaine ? » Un seuil est **inclusif** : un score exactement égal passe.

    Les scores inconnus (``None``) sont comptés à part et exclus des parts : une
    fiche sans score n'est ni au-dessus ni en dessous du seuil.
    """
    for seuil in seuils:
        if not 0.0 <= float(seuil) <= 1.0:
            raise ValueError(f"Un seuil doit être compris entre 0 et 1 (reçu : {seuil!r}).")

    connus = [float(score) for score in scores if score is not None]
    inconnus = len(scores) - len(connus)

    paliers = []
    for seuil in sorted(seuils):
        nb = sum(1 for score in connus if score >= seuil)
        part = (nb / len(connus)) if connus else None
        paliers.append(
            {
                "seuil": float(seuil),
                "nb_fiches": nb,
                "part": part,
                "part_pct": round(part * 100, 2) if part is not None else None,
            }
        )

    return {
        "definition": (
            "Répartition des scores de qualité de lecture, et nombre de fiches qui franchiraient "
            "chaque seuil de validation automatique (seuil inclusif)."
        ),
        "unite": "fiche + ratio",
        "periode": "instantané",
        "nb_scores_connus": len(connus),
        "nb_scores_inconnus": inconnus,
        "paliers": paliers,
    }


def completude_champs(lignes: Sequence[tuple[str, int, int]]) -> list[dict[str, Any]]:
    """Complétude de chaque champ, du moins renseigné au mieux renseigné.

    Entrée : ``(champ, nombre renseigné, nombre total)``. Le champ le plus
    souvent absent apparaît en premier : c'est lui qu'il faut réparer d'abord
    dans le lot 1.
    """
    resultat = []
    for champ, renseignes, total in lignes:
        total_entier = int(total or 0)
        renseignes_entier = int(renseignes or 0)
        taux = (renseignes_entier / total_entier) if total_entier else None
        resultat.append(
            {
                "champ": str(champ),
                "nb_renseignes": renseignes_entier,
                "nb_total": total_entier,
                "taux": taux,
                "taux_pct": round(taux * 100, 2) if taux is not None else None,
            }
        )
    resultat.sort(key=lambda entree: (entree["taux"] if entree["taux"] is not None else -1.0, entree["champ"]))
    return resultat


# ---------------------------------------------------------------------------
# Accès PostgreSQL — fonctions minces, requêtes GROUP BY
# ---------------------------------------------------------------------------


def _table_absente(nom: str, definition: str) -> dict[str, Any]:
    """Réponse neutre quand une table n'existe pas encore sur la base sondée.

    Le drapeau ``table_absente`` rend l'état explicite : des zéros silencieux
    cacheraient un schéma non migré, ce qui est exactement le genre de chose
    qu'un diagnostic doit dire.
    """
    return {
        "definition": definition,
        "unite": "compte",
        "periode": "instantané",
        "table_absente": nom,
        "nb_groupes": 0,
        "nb_fiches_couvertes": 0,
        "distribution": {},
        "groupes_statistiques": 0,
        "fiches_en_groupe_statistique": 0,
        "part_couverte_statistique": None,
    }


def groupes_comparables(cursor: Any) -> dict[str, Any]:  # noqa: ANN401 — curseur psycopg2 réel
    """Groupes « même bateau + même type de voile », et leur distribution.

    C'est la mesure qui dit si la comparaison statistique est seulement
    possible. Elle distingue quatre populations : sans bateau, sans type, sans
    les deux, et rattachées.
    """
    try:
        cursor.execute(
            """
            SELECT COUNT(*) AS taille
            FROM sail_extractions
            WHERE boat_model_id IS NOT NULL
              AND COALESCE(btrim(type_voile), '') <> ''
            GROUP BY boat_model_id, lower(btrim(type_voile))
            """
        )
        tailles = [int(ligne[0]) for ligne in cursor.fetchall()]

        cursor.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE boat_model_id IS NULL) AS sans_bateau,
                COUNT(*) FILTER (WHERE COALESCE(btrim(type_voile), '') = '') AS sans_type,
                COUNT(*) FILTER (
                    WHERE boat_model_id IS NULL AND COALESCE(btrim(type_voile), '') = ''
                ) AS sans_les_deux,
                COUNT(*) AS total
            FROM sail_extractions
            """
        )
        sans_bateau, sans_type, sans_les_deux, total = cursor.fetchone()
    except Exception:
        return _table_absente("sail_extractions", "Groupes comparables (même bateau, même type de voile).")

    rapport = histogramme_groupes(tailles)
    rapport["extractions_total"] = int(total or 0)
    rapport["extractions_sans_bateau"] = int(sans_bateau or 0)
    rapport["extractions_sans_type"] = int(sans_type or 0)
    rapport["extractions_sans_les_deux"] = int(sans_les_deux or 0)
    return rapport


def lignes_valeurs(cursor: Any, colonne: str) -> list[tuple[object, int]]:  # noqa: ANN401 — curseur psycopg2 réel
    """Les ``(valeur brute, nb d'occurrences)`` d'une colonne de ``sail_extractions``.

    Lot 1.1 : rendu public pour que la table de correspondance se construise
    sur la MÊME lecture que la cartographie — un seul comptage, deux usages.

    La colonne doit appartenir à une liste fermée : une colonne arbitraire est
    refusée, ce qui interdit toute construction SQL depuis l'extérieur. Une
    table absente rend une liste vide plutôt qu'une erreur : la mesure d'un axe
    ne doit jamais faire échouer celle des autres.
    """
    if colonne not in _COLONNES_AUTORISEES:
        raise ValueError(
            f"Colonne non autorisée : {colonne!r}. Autorisées : {sorted(_COLONNES_AUTORISEES)}."
        )
    try:
        cursor.execute(
            f"""
            SELECT {colonne} AS valeur, COUNT(*) AS nb
            FROM sail_extractions
            WHERE {colonne} IS NOT NULL AND btrim({colonne}) <> ''
            GROUP BY 1
            ORDER BY nb DESC, valeur
            """
        )
        return [(ligne[0], int(ligne[1])) for ligne in cursor.fetchall()]
    except Exception:
        LOGGER.warning("Colonne « %s » illisible : axe mesuré à zéro (table ou colonne absente).", colonne)
        return []


def lignes_matiere(cursor: Any) -> list[tuple[object, int]]:  # noqa: ANN401 — curseur psycopg2 réel
    """Les ``(désignation de matière, nb)`` du JSONB ``materiaux``.

    Lot 1.1 : même lecture que :func:`variantes_matiere`, rendue réutilisable.
    """
    try:
        cursor.execute(
            """
            SELECT m->>'designation' AS valeur, COUNT(*) AS nb
            FROM sail_extractions s, jsonb_array_elements(s.materiaux) m
            WHERE jsonb_typeof(s.materiaux) = 'array'
              AND COALESCE(btrim(m->>'designation'), '') <> ''
            GROUP BY 1
            ORDER BY nb DESC, valeur
            """
        )
        return [(ligne[0], int(ligne[1])) for ligne in cursor.fetchall()]
    except Exception:
        LOGGER.warning("Matières illisibles : axe mesuré à zéro (colonne JSONB absente).")
        return []


def lignes_categorie(cursor: Any) -> list[tuple[object, int]]:  # noqa: ANN401 — curseur psycopg2 réel
    """Les ``(catégorie, nb)`` de la table ``fiche``.

    Lot 1.1 : quatrième axe. La colonne est fermée par une contrainte CHECK
    (``voile`` / ``accessoire``), donc aucune variante d'écriture n'y est
    attendue — mais c'est une MESURE, pas une supposition : si le contrôle
    saute un jour, cette fonction le verra.
    """
    try:
        cursor.execute(
            """
            SELECT categorie AS valeur, COUNT(*) AS nb
            FROM fiche
            WHERE categorie IS NOT NULL AND btrim(categorie) <> ''
            GROUP BY 1
            ORDER BY nb DESC, valeur
            """
        )
        return [(ligne[0], int(ligne[1])) for ligne in cursor.fetchall()]
    except Exception:
        LOGGER.warning("Catégories illisibles : axe mesuré à zéro (table ou colonne absente).")
        return []


def variantes_valeurs(cursor: Any, colonne: str) -> dict[str, Any]:  # noqa: ANN401 — curseur psycopg2 réel
    """Variantes d'une colonne de ``sail_extractions``."""
    lignes = lignes_valeurs(cursor, colonne)

    return {
        "definition": (
            f"Variantes de la colonne « {colonne} » : écritures multiples d'une même valeur "
            "(certaines) et valeurs engraînées l'une dans l'autre (probables, à confirmer)."
        ),
        "unite": "valeur + compte",
        "periode": "instantané",
        "colonne": colonne,
        "nb_valeurs_distinctes": len({cle_normalisation(valeur) for valeur, _ in lignes}),
        "certaines": regrouper_variantes(lignes),
        "probables": variantes_probables(lignes),
    }


def variantes_matiere(cursor: Any) -> dict[str, Any]:  # noqa: ANN401 — curseur psycopg2 réel
    """Variantes des désignations de matière (colonne JSONB ``materiaux``)."""
    try:
        cursor.execute(
            """
            SELECT m->>'designation' AS valeur, COUNT(*) AS nb
            FROM sail_extractions s, jsonb_array_elements(s.materiaux) m
            WHERE jsonb_typeof(s.materiaux) = 'array'
              AND COALESCE(btrim(m->>'designation'), '') <> ''
            GROUP BY 1
            ORDER BY nb DESC, valeur
            """
        )
        lignes = [(ligne[0], int(ligne[1])) for ligne in cursor.fetchall()]
    except Exception:
        lignes = []

    return {
        "definition": (
            "Variantes des désignations de matière (JSONB « materiaux ») : écritures multiples "
            "d'une même matière, et matières engraînées l'une dans l'autre (à confirmer)."
        ),
        "unite": "matière + compte",
        "periode": "instantané",
        "colonne": "materiaux->designation",
        "nb_valeurs_distinctes": len({cle_normalisation(valeur) for valeur, _ in lignes}),
        "certaines": regrouper_variantes(lignes),
        "probables": variantes_probables(lignes),
    }


def variantes_categorie(cursor: Any) -> dict[str, Any]:  # noqa: ANN401 — curseur psycopg2 réel
    """Variantes de la CATÉGORIE, et poids de chaque source (Lot 1.1).

    Particularité MESURÉE, pas supposée : ``fiche.categorie`` est fermée par une
    contrainte CHECK (``voile`` / ``accessoire``). C'est donc la seule des
    quatre dimensions du Lot 1.1 à être déjà à écriture unique — la mesure le
    confirme, ou l'infirme si la contrainte saute un jour.

    Ce qui mérite la table de correspondance, c'est la SOURCE : une catégorie
    héritée du nom de dossier n'a pas la même valeur probante qu'une catégorie
    lue dans le document ou décidée par un humain. On la mesure donc aussi.
    """
    lignes = lignes_categorie(cursor)
    try:
        cursor.execute("SELECT categorie_source, COUNT(*) FROM fiche GROUP BY 1 ORDER BY 2 DESC, 1")
        sources = [{"source": str(ligne[0]), "nb": int(ligne[1])} for ligne in cursor.fetchall()]
    except Exception:
        LOGGER.warning("Sources de catégorie illisibles.")
        sources = []

    return {
        "definition": (
            "Variantes de la catégorie (fiche.categorie) et répartition de sa source. "
            "Colonne fermée par une contrainte CHECK : aucune variante d'écriture n'y est "
            "attendue — la mesure le vérifie, elle ne le suppose pas."
        ),
        "unite": "valeur + compte",
        "periode": "instantané",
        "colonne": "fiche.categorie",
        "nb_valeurs_distinctes": len({cle_normalisation(valeur) for valeur, _ in lignes}),
        "certaines": regrouper_variantes(lignes),
        "probables": variantes_probables(lignes),
        "sources": sources,
    }


def distribution_scores_qualite(
    cursor: Any,  # noqa: ANN401 — curseur psycopg2 réel
    seuils: Sequence[float] = (0.80, 0.85, 0.90, 0.95),
) -> dict[str, Any]:
    """Scores de qualité des fiches, et effet de chaque seuil de validation automatique."""
    try:
        cursor.execute("SELECT score_qualite FROM fiche")
        scores = [None if ligne[0] is None else float(ligne[0]) for ligne in cursor.fetchall()]
    except Exception:
        scores = []
    rapport = distribution_scores(scores, seuils=seuils)
    return rapport


def completude_dimensions(cursor: Any) -> dict[str, Any]:  # noqa: ANN401 — curseur psycopg2 réel
    """Complétude de chaque dimension et attribut, du moins au mieux renseigné."""
    try:
        colonnes = ", ".join(f"COUNT({colonne})" for colonne in _COLONNES_COMPLETUDE)
        cursor.execute(f"SELECT COUNT(*), {colonnes} FROM sail_extractions")  # noqa: S608 — colonnes fermées
        ligne = cursor.fetchone()
    except Exception:
        ligne = None

    if ligne is None:
        return {
            "definition": "Complétude des dimensions et attributs dans les extractions de voiles.",
            "unite": "champ + ratio",
            "periode": "instantané",
            "table_absente": "sail_extractions",
            "champs": [],
        }

    total = int(ligne[0] or 0)
    valeurs = [int(valeur or 0) for valeur in ligne[1:]]
    champs = completude_champs(
        [(colonne, nb, total) for colonne, nb in zip(_COLONNES_COMPLETUDE, valeurs, strict=True)]
    )
    return {
        "definition": "Complétude des dimensions et attributs dans les extractions de voiles.",
        "unite": "champ + ratio",
        "periode": "instantané",
        "nb_extractions": total,
        "champs": champs,
    }


def atteignabilite_recherche(cursor: Any) -> dict[str, Any]:  # noqa: ANN401 — curseur psycopg2 réel
    """Combien de fiches la recherche peut-elle atteindre ?

    L'écran de recherche annonce un total qui peut être très inférieur au
    nombre de fiches en base. Avant d'accuser un filtre, on compte : fiches en
    base, fiches rattachées à une extraction structurée, fiches portant au
    moins une pièce jointe. L'écart entre le premier et le deuxième est le
    nombre à expliquer.
    """
    try:
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM fiche) AS nb_fiches,
                (SELECT COUNT(DISTINCT id_fiche) FROM sail_extractions
                  WHERE id_fiche IS NOT NULL) AS nb_rattachees,
                (SELECT COUNT(DISTINCT id_fiche) FROM fiche_piece_jointe
                  WHERE id_fiche IS NOT NULL) AS nb_avec_piece,
                (SELECT COUNT(*) FROM documents) AS nb_documents
            """
        )
        nb_fiches, nb_rattachees, nb_avec_piece, nb_documents = cursor.fetchone()
    except Exception:
        nb_fiches = nb_rattachees = nb_avec_piece = nb_documents = 0

    nb_fiches = int(nb_fiches or 0)
    nb_rattachees = int(nb_rattachees or 0)
    return {
        "definition": (
            "Fiches en base contre fiches rattachées à une extraction structurée et fiches portant "
            "au moins une pièce jointe. L'écart « nb_fiches_non_rattachees » est le nombre à "
            "expliquer quand l'écran de recherche annonce moins que le total réel."
        ),
        "unite": "fiche",
        "periode": "instantané",
        "nb_fiches": nb_fiches,
        "nb_fiches_rattachees": nb_rattachees,
        "nb_fiches_non_rattachees": nb_fiches - nb_rattachees,
        "nb_fiches_avec_piece": int(nb_avec_piece or 0),
        "nb_documents_indexes": int(nb_documents or 0),
    }


def cartographie(index: Any) -> dict[str, Any]:  # noqa: ANN401 — index réel
    """Assemble la cartographie complète — lecture seule, une seule connexion."""
    if not getattr(index, "is_postgres", False):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="Cartographie disponible sur PostgreSQL uniquement (migrations 006-031).",
        )

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            return {
                "groupes_comparables": groupes_comparables(cursor),
                "variantes_gamme": variantes_valeurs(cursor, "gamme"),
                "variantes_type_voile": variantes_valeurs(cursor, "type_voile"),
                "variantes_matiere": variantes_matiere(cursor),
                "variantes_categorie": variantes_categorie(cursor),
                "variantes_modele_bateau": variantes_valeurs(cursor, "modele_bateau_brut"),
                "distribution_scores": distribution_scores_qualite(cursor),
                "completude_dimensions": completude_dimensions(cursor),
                "atteignabilite_recherche": atteignabilite_recherche(cursor),
            }
