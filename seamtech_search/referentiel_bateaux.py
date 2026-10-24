"""Référentiel canonique des modèles de bateaux — Lot 2 du plan directeur v2.

Le référentiel historique ``bateau`` (migration 006) conserve les libellés
tels qu'ils ont été lus ou saisis. Il ne suffit pas à dire que
``BAVARIA 45``, ``Bavaria-45`` et ``Bav. 45`` désignent le même modèle, ni à
prouver qu'une fusion a été décidée puis annulée. Ce module ajoute cette
couche d'identité, sans réécrire les libellés sources :

- ``boat_models`` porte le modèle canonique ;
- ``boat_model_aliases`` porte chaque forme rencontrée ou apprise ;
- ``bateau.boat_model_id`` relie chaque libellé historique au modèle final ;
- ``boat_model_merge_history`` conserve l'instantané nécessaire pour
  défaire une fusion.

Règle de prudence du plan : la normalisation plie la casse, les accents, la
ponctuation et quelques abréviations ponctuées non ambiguës, mais elle ne
transforme jamais ``45`` en ``45.1`` ou ``45 CC``. Ces trois clés restent
distinctes tant qu'un humain n'a pas explicitement fusionné les modèles.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from seamtech_search.fiches.normalisation import normaliser_cle_recherche

TYPE_EXACT = "exact"
TYPE_ALIAS = "alias"
TYPE_PREFIXE = "prefixe"
TYPE_FLOU = "flou"
ORDRE_CORRESPONDANCES = {TYPE_EXACT: 0, TYPE_ALIAS: 1, TYPE_PREFIXE: 2, TYPE_FLOU: 3}
SEUIL_FLOU_MODELE = 0.30

# Abréviations acceptées uniquement sous leur forme ponctuée (« Bav. »).
# Sans le point, un fragment peut être un début de mot ou un autre nom
# propre : l'alias doit alors être appris explicitement, pas deviné.
_ABREVIATIONS_PONCTUEES: tuple[tuple[str, str, str], ...] = (
    # (forme ponctuée, clé canonique minuscule, libellé d'affichage)
    ("Bav", "bavaria", "Bavaria"),
    ("ben", "Beneteau", "Beneteau"),
    ("duf", "Dufour", "Dufour"),
    ("jan", "Jeanneau", "Jeanneau"),
)
_PLACEHOLDER_DECIMAL = "\u0001"
_RE_DECIMAL = re.compile(r"(?<=\d)[.,](?=\d)")
_RE_FRONTIERE_ALPHANUM = re.compile(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])")


class AliasBateauEnConflit(ValueError):
    """Un alias normalisé désigne déjà un autre modèle final."""

    def __init__(self, alias: str, id_modele_existant: int, id_modele_demande: int) -> None:
        super().__init__(
            f"Alias bateau en conflit : {alias!r} désigne déjà le modèle "
            f"{id_modele_existant}, pas le modèle {id_modele_demande}."
        )
        self.alias = alias
        self.id_modele_existant = id_modele_existant
        self.id_modele_demande = id_modele_demande


@dataclass(frozen=True)
class ModeleBateau:
    id_modele: int
    nom_canonique: str
    cle_normalisee: str
    source: str
    actif: bool
    id_modele_fusion: int | None

    def vers_dict(self) -> dict[str, Any]:
        return {
            "id_modele": self.id_modele,
            "nom_canonique": self.nom_canonique,
            "cle_normalisee": self.cle_normalisee,
            "source": self.source,
            "actif": self.actif,
            "id_modele_fusion": self.id_modele_fusion,
        }


def _affichage_modele(valeur: str | None) -> str:
    """Libellé d'affichage nettoyé, sans changer l'identité du modèle."""
    texte = " ".join(str(valeur or "").replace("\u00a0", " ").split()).strip(" -")
    for court, _cle, affichage in _ABREVIATIONS_PONCTUEES:
        texte = re.sub(
            rf"(?i)(?<![\w]){re.escape(court)}\.(?=\s|$)",
            affichage,
            texte,
        )
    texte = _RE_DECIMAL.sub(_PLACEHOLDER_DECIMAL, texte)
    texte = re.sub(r"[-_/]+", " ", texte)
    texte = texte.replace(_PLACEHOLDER_DECIMAL, ".")
    # Un libellé source entièrement en majuscules (« BAVARIA-45 ») ne doit pas
    # imposer sa casse au nom canonique affiché. On ne replie que les mots
    # alphabétiques longs : les sigles courts métier (« CC », « SO ») restent.
    return " ".join(
        mot.capitalize() if mot.isalpha() and mot.isupper() and len(mot) > 2 else mot
        for mot in texte.split()
    )


def normaliser_modele_bateau(valeur: str | None) -> str:
    """Clé canonique d'un modèle de bateau.

    Exemples verrouillés par tests : ``Bavaria-45`` et ``BAVARIA 45`` ont la
    même clé ; ``Bavaria 45``, ``Bavaria 45.1`` et ``Bavaria 45 CC`` en ont
    trois différentes. Le point décimal entre chiffres est donc protégé,
    alors que les autres ponctuations deviennent des séparateurs.
    """
    if not valeur:
        return ""
    texte = normaliser_cle_recherche(str(valeur).replace("\u00a0", " "))
    for court, complet, _affichage in _ABREVIATIONS_PONCTUEES:
        texte = re.sub(
            rf"(?<![\w]){re.escape(court)}\.(?=\s|$)",
            complet,
            texte,
            flags=re.IGNORECASE,
        )
    texte = _RE_DECIMAL.sub(_PLACEHOLDER_DECIMAL, texte)
    texte = re.sub(rf"[^\w{_PLACEHOLDER_DECIMAL}]+", " ", texte, flags=re.UNICODE)
    texte = texte.replace("_", " ").replace(_PLACEHOLDER_DECIMAL, ".")
    texte = _RE_FRONTIERE_ALPHANUM.sub(" ", texte)
    return " ".join(texte.split())


def _modele_depuis_ligne(ligne: Sequence[Any]) -> ModeleBateau:
    return ModeleBateau(
        id_modele=int(ligne[0]),
        nom_canonique=str(ligne[1]),
        cle_normalisee=str(ligne[2]),
        source=str(ligne[3] or ""),
        actif=bool(ligne[4]),
        id_modele_fusion=int(ligne[5]) if ligne[5] is not None else None,
    )


def _lire_modele(cursor: Any, id_modele: int) -> ModeleBateau | None:  # noqa: ANN401
    cursor.execute(
        "SELECT id_modele, nom_canonique, cle_normalisee, source, actif, id_modele_fusion "
        "FROM boat_models WHERE id_modele = %s",
        (int(id_modele),),
    )
    ligne = cursor.fetchone()
    return _modele_depuis_ligne(ligne) if ligne is not None else None


def _lire_tous_modeles(cursor: Any) -> dict[int, ModeleBateau]:  # noqa: ANN401
    cursor.execute(
        "SELECT id_modele, nom_canonique, cle_normalisee, source, actif, id_modele_fusion "
        "FROM boat_models ORDER BY id_modele"
    )
    return {
        modele.id_modele: modele
        for modele in (_modele_depuis_ligne(ligne) for ligne in cursor.fetchall())
    }


def _id_final_depuis_map(modeles: Mapping[int, ModeleBateau], id_modele: int) -> int:
    courant = int(id_modele)
    vus: set[int] = set()
    while True:
        if courant in vus:
            raise ValueError(f"Cycle de fusion détecté dans le référentiel bateau (modèle {courant}).")
        vus.add(courant)
        modele = modeles.get(courant)
        if modele is None:
            raise ValueError(f"Modèle de bateau introuvable : {courant}.")
        if modele.id_modele_fusion is None:
            return courant
        courant = modele.id_modele_fusion


def resoudre_modele_final(cursor: Any, id_modele: int) -> ModeleBateau:  # noqa: ANN401
    """Suit la chaîne de fusion jusqu'au modèle canonique vivant."""
    courant = int(id_modele)
    vus: set[int] = set()
    while True:
        if courant in vus:
            raise ValueError(f"Cycle de fusion détecté dans le référentiel bateau (modèle {courant}).")
        vus.add(courant)
        modele = _lire_modele(cursor, courant)
        if modele is None:
            raise ValueError(f"Modèle de bateau introuvable : {courant}.")
        if modele.id_modele_fusion is None:
            return modele
        courant = modele.id_modele_fusion


def _proprietaire_alias(cursor: Any, cle_alias: str) -> int | None:  # noqa: ANN401
    cursor.execute(
        "SELECT id_modele FROM boat_model_aliases WHERE cle_normalisee = %s",
        (cle_alias,),
    )
    ligne = cursor.fetchone()
    return int(ligne[0]) if ligne is not None else None


def assurer_alias(
    cursor: Any,  # noqa: ANN401
    id_modele: int,
    alias_brut: str,
    *,
    source: str = "manuel",
    cree_par: int | None = None,
) -> dict[str, Any]:
    """Ajoute un alias au modèle final, ou signale un conflit explicite."""
    modele = resoudre_modele_final(cursor, int(id_modele))
    alias = _affichage_modele(alias_brut)
    cle = normaliser_modele_bateau(alias_brut)
    if not alias or not cle:
        raise ValueError("Alias de modèle de bateau vide.")
    proprietaire = _proprietaire_alias(cursor, cle)
    if proprietaire is not None:
        final = resoudre_modele_final(cursor, proprietaire)
        if final.id_modele != modele.id_modele:
            raise AliasBateauEnConflit(alias, final.id_modele, modele.id_modele)
        cursor.execute(
            "SELECT id_alias, alias_brut FROM boat_model_aliases WHERE cle_normalisee = %s",
            (cle,),
        )
        ligne = cursor.fetchone()
        return {
            "id_alias": int(ligne[0]),
            "id_modele": proprietaire,
            "id_modele_final": final.id_modele,
            "alias": str(ligne[1]),
            "cle_normalisee": cle,
            "cree": False,
        }
    cursor.execute(
        "INSERT INTO boat_model_aliases (id_modele, alias_brut, cle_normalisee, source, cree_par) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (cle_normalisee) DO NOTHING RETURNING id_alias",
        (modele.id_modele, alias, cle, source, cree_par),
    )
    ligne = cursor.fetchone()
    if ligne is None:  # course concurrente : relire et vérifier le propriétaire
        return assurer_alias(cursor, modele.id_modele, alias_brut, source=source, cree_par=cree_par)
    return {
        "id_alias": int(ligne[0]),
        "id_modele": modele.id_modele,
        "id_modele_final": modele.id_modele,
        "alias": alias,
        "cle_normalisee": cle,
        "cree": True,
    }


def _assurer_modele_interne(
    cursor: Any,  # noqa: ANN401
    nom_brut: str,
    *,
    source: str,
    cree_par: int | None,
) -> tuple[ModeleBateau | None, bool]:
    cle = normaliser_modele_bateau(nom_brut)
    if not cle:
        return None, False
    proprietaire = _proprietaire_alias(cursor, cle)
    if proprietaire is not None:
        return resoudre_modele_final(cursor, proprietaire), False
    cursor.execute(
        "SELECT id_modele FROM boat_models WHERE cle_normalisee = %s",
        (cle,),
    )
    ligne = cursor.fetchone()
    if ligne is not None:
        modele = resoudre_modele_final(cursor, int(ligne[0]))
        assurer_alias(cursor, modele.id_modele, nom_brut, source=source, cree_par=cree_par)
        return modele, False
    affichage = _affichage_modele(nom_brut)
    cursor.execute(
        "INSERT INTO boat_models (nom_canonique, cle_normalisee, source, cree_par) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT (cle_normalisee) DO NOTHING RETURNING id_modele",
        (affichage, cle, source, cree_par),
    )
    ligne = cursor.fetchone()
    if ligne is None:
        cursor.execute("SELECT id_modele FROM boat_models WHERE cle_normalisee = %s", (cle,))
        ligne = cursor.fetchone()
        if ligne is None:
            raise RuntimeError("Création du modèle de bateau sans identifiant retourné.")
        modele = resoudre_modele_final(cursor, int(ligne[0]))
        assurer_alias(cursor, modele.id_modele, nom_brut, source=source, cree_par=cree_par)
        return modele, False
    modele = _lire_modele(cursor, int(ligne[0]))
    if modele is None:
        raise RuntimeError("Modèle de bateau créé puis introuvable.")
    assurer_alias(cursor, modele.id_modele, affichage, source=source, cree_par=cree_par)
    if _affichage_modele(nom_brut) != affichage:
        assurer_alias(cursor, modele.id_modele, nom_brut, source=source, cree_par=cree_par)
    return modele, True


def assurer_modele(
    cursor: Any,  # noqa: ANN401
    nom_brut: str,
    *,
    source: str = "manuel",
    cree_par: int | None = None,
) -> ModeleBateau | None:
    """Résout ou crée le modèle final désigné par un libellé brut."""
    modele, _cree = _assurer_modele_interne(
        cursor, nom_brut, source=source, cree_par=cree_par
    )
    return modele


def resoudre_modele_depuis_texte(cursor: Any, texte: str) -> ModeleBateau | None:  # noqa: ANN401
    """Résolution exacte/alias d'un libellé complet — jamais de préfixe ici."""
    cle = normaliser_modele_bateau(texte)
    if not cle:
        return None
    proprietaire = _proprietaire_alias(cursor, cle)
    if proprietaire is not None:
        return resoudre_modele_final(cursor, proprietaire)
    cursor.execute("SELECT id_modele FROM boat_models WHERE cle_normalisee = %s", (cle,))
    ligne = cursor.fetchone()
    return resoudre_modele_final(cursor, int(ligne[0])) if ligne is not None else None


def lier_bateau_au_modele(
    cursor: Any,  # noqa: ANN401
    id_bateau: int,
    *,
    source: str = "import",
    cree_par: int | None = None,
) -> ModeleBateau | None:
    """Relie une ligne ``bateau`` existante à son modèle canonique final."""
    cursor.execute("SELECT nom, taille FROM bateau WHERE id_bateau = %s", (int(id_bateau),))
    ligne = cursor.fetchone()
    if ligne is None:
        raise ValueError(f"Bateau introuvable : {id_bateau}.")
    modele = assurer_modele(cursor, str(ligne[0]), source=source, cree_par=cree_par)
    if modele is None:
        return None
    if ligne[1]:
        try:
            assurer_alias(
                cursor,
                modele.id_modele,
                f"{ligne[0]} {ligne[1]}",
                source=source,
                cree_par=cree_par,
            )
        except AliasBateauEnConflit:
            # L'étiquette complète historique peut déjà être l'alias d'un
            # autre modèle (détail entre parenthèses ambigu) : le modèle du
            # nom reste résolu, mais on ne vole pas l'alias en silence.
            pass
    cursor.execute(
        "UPDATE bateau SET boat_model_id = %s WHERE id_bateau = %s",
        (modele.id_modele, int(id_bateau)),
    )
    return modele


def synchroniser_referentiel_bateaux(
    cursor: Any,  # noqa: ANN401
    *,
    source: str = "reindex_lot2",
) -> dict[str, int]:
    """Backfill idempotent depuis ``bateau`` vers le référentiel canonique.

    Les libellés historiques ne sont pas modifiés. Pour un nouveau groupe,
    le libellé canonique affiché est choisi de façon déterministe : plus
    grand nombre de fiches, puis forme non entièrement majuscule, puis
    plus ancien identifiant.
    """
    cursor.execute(
        "SELECT b.id_bateau, b.nom, b.taille, b.boat_model_id, count(f.id_fiche)::int "
        "FROM bateau b LEFT JOIN fiche f ON f.id_bateau = b.id_bateau "
        "GROUP BY b.id_bateau, b.nom, b.taille, b.boat_model_id ORDER BY b.id_bateau"
    )
    lignes = cursor.fetchall()
    groupes: dict[str, list[Sequence[Any]]] = defaultdict(list)
    ignores = 0
    for ligne in lignes:
        cle = normaliser_modele_bateau(str(ligne[1] or ""))
        if not cle:
            ignores += 1
            continue
        groupes[cle].append(ligne)

    modeles_crees = 0
    bateaux_lies = 0
    alias_ignores = 0
    for membres in groupes.values():
        candidat = sorted(
            membres,
            key=lambda membre: (
                -int(membre[4] or 0),
                1 if str(membre[1]).isupper() else 0,
                len(_affichage_modele(str(membre[1]))),
                int(membre[0]),
            ),
        )[0]
        modele, cree = _assurer_modele_interne(
            cursor, str(candidat[1]), source=source, cree_par=None
        )
        if modele is None:
            ignores += len(membres)
            continue
        modeles_crees += int(cree)
        for ligne in membres:
            try:
                assurer_alias(cursor, modele.id_modele, str(ligne[1]), source=source)
            except AliasBateauEnConflit:
                alias_ignores += 1
            if ligne[2]:
                try:
                    assurer_alias(
                        cursor,
                        modele.id_modele,
                        f"{ligne[1]} {ligne[2]}",
                        source=source,
                    )
                except AliasBateauEnConflit:
                    alias_ignores += 1
            cursor.execute(
                "UPDATE bateau SET boat_model_id = %s WHERE id_bateau = %s",
                (modele.id_modele, int(ligne[0])),
            )
            bateaux_lies += 1
    return {
        "bateaux_lus": len(lignes),
        "bateaux_lies": bateaux_lies,
        "modeles_crees": modeles_crees,
        "groupes_modeles": len(groupes),
        "alias_ignores": alias_ignores,
        "bateaux_ignores": ignores,
    }


def charger_cles_modeles(cursor: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """Toutes les clés cherchables, déjà résolues vers le modèle final."""
    modeles = _lire_tous_modeles(cursor)
    cles: list[dict[str, Any]] = []
    for modele in modeles.values():
        try:
            final = modeles[_id_final_depuis_map(modeles, modele.id_modele)]
        except ValueError:
            continue
        cles.append(
            {
                "id_modele": final.id_modele,
                "id_modele_origine": modele.id_modele,
                "nom_canonique": final.nom_canonique,
                "cle": modele.cle_normalisee,
                "type_cle": "canonique" if modele.id_modele == final.id_modele else "alias",
                "alias": None if modele.id_modele == final.id_modele else modele.nom_canonique,
            }
        )
    cursor.execute(
        "SELECT id_modele, alias_brut, cle_normalisee FROM boat_model_aliases ORDER BY id_alias"
    )
    for id_origine, alias_brut, cle in cursor.fetchall():
        try:
            final = modeles[_id_final_depuis_map(modeles, int(id_origine))]
        except (KeyError, ValueError):
            continue
        cles.append(
            {
                "id_modele": final.id_modele,
                "id_modele_origine": int(id_origine),
                "nom_canonique": final.nom_canonique,
                "cle": str(cle),
                "type_cle": "alias",
                "alias": str(alias_brut),
            }
        )
    return cles


def _candidate_depuis_cle(
    entree: Mapping[str, Any], type_correspondance: str, score: float
) -> dict[str, Any]:
    return {
        "id_modele": int(entree["id_modele"]),
        "nom_canonique": str(entree["nom_canonique"]),
        "cle_normalisee": str(entree["cle"]),
        "type_correspondance": type_correspondance,
        "rang": ORDRE_CORRESPONDANCES[type_correspondance],
        "score": round(float(score), 6),
        "alias": entree.get("alias"),
    }


def _garder_meilleures(candidates: Iterable[dict[str, Any]], limite: int) -> list[dict[str, Any]]:
    meilleures: dict[int, dict[str, Any]] = {}
    for candidate in candidates:
        id_modele = int(candidate["id_modele"])
        actuelle = meilleures.get(id_modele)
        if actuelle is None or (
            int(candidate["rang"]), -float(candidate["score"]), str(candidate["nom_canonique"])
        ) < (
            int(actuelle["rang"]), -float(actuelle["score"]), str(actuelle["nom_canonique"])
        ):
            meilleures[id_modele] = candidate
    return sorted(
        meilleures.values(),
        key=lambda candidate: (
            int(candidate["rang"]),
            -float(candidate["score"]),
            str(candidate["nom_canonique"]),
        ),
    )[:limite]


def classer_correspondances_modeles(
    cles: Sequence[Mapping[str, Any]], texte: str, limite: int = 10
) -> list[dict[str, Any]]:
    """Exact, puis alias, puis préfixe — classement pur et déterministe."""
    requete = normaliser_modele_bateau(texte)
    if not requete:
        return []
    candidates: list[dict[str, Any]] = []
    for entree in cles:
        cle = str(entree["cle"])
        if cle == requete:
            type_correspondance = TYPE_EXACT if entree.get("type_cle") == "canonique" else TYPE_ALIAS
            candidates.append(
                _candidate_depuis_cle(
                    entree, type_correspondance, 1.0 if type_correspondance == TYPE_EXACT else 0.98
                )
            )
        elif cle.startswith(requete):
            score = 0.70 + 0.25 * (len(requete) / max(len(cle), 1))
            candidates.append(_candidate_depuis_cle(entree, TYPE_PREFIXE, score))
    return _garder_meilleures(candidates, limite)


def _pg_trgm_disponible(cursor: Any) -> bool:  # noqa: ANN401
    cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
    return cursor.fetchone() is not None


def rechercher_modeles_flous(
    cursor: Any, texte: str, limite: int = 10, seuil: float = SEUIL_FLOU_MODELE  # noqa: ANN401
) -> list[dict[str, Any]]:
    """Correspondances pg_trgm sur clés normalisées, résolues au modèle final."""
    requete = normaliser_modele_bateau(texte)
    if not requete or not _pg_trgm_disponible(cursor):
        return []
    cursor.execute(
        "WITH cles AS ("
        "  SELECT id_modele, cle_normalisee AS cle, NULL::text AS alias FROM boat_models "
        "  UNION ALL "
        "  SELECT id_modele, cle_normalisee, alias_brut FROM boat_model_aliases"
        ") "
        "SELECT id_modele, cle, alias, similarity(cle, %s) AS score "
        "FROM cles WHERE similarity(cle, %s) >= %s ORDER BY score DESC, cle LIMIT %s",
        (requete, requete, float(seuil), max(limite * 4, limite)),
    )
    lignes = cursor.fetchall()
    if not lignes:
        return []
    modeles = _lire_tous_modeles(cursor)
    candidates: list[dict[str, Any]] = []
    for id_origine, cle, alias, score in lignes:
        try:
            final = modeles[_id_final_depuis_map(modeles, int(id_origine))]
        except (KeyError, ValueError):
            continue
        candidates.append(
            {
                "id_modele": final.id_modele,
                "nom_canonique": final.nom_canonique,
                "cle_normalisee": str(cle),
                "type_correspondance": TYPE_FLOU,
                "rang": ORDRE_CORRESPONDANCES[TYPE_FLOU],
                "score": round(float(score), 6),
                "alias": alias,
            }
        )
    return _garder_meilleures(candidates, limite)


def rechercher_modeles(
    cursor: Any,  # noqa: ANN401
    texte: str,
    limite: int = 10,
    cles: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Recherche de modèles par paliers : exact > alias > préfixe > flou."""
    cles_effectives = list(cles) if cles is not None else charger_cles_modeles(cursor)
    candidates = classer_correspondances_modeles(cles_effectives, texte, limite=limite)
    if len(candidates) < limite:
        deja = {int(candidate["id_modele"]) for candidate in candidates}
        for candidate in rechercher_modeles_flous(cursor, texte, limite=limite):
            if int(candidate["id_modele"]) not in deja:
                candidates.append(candidate)
    return _garder_meilleures(candidates, limite)


def correspondances_dans_requete(
    cursor: Any,  # noqa: ANN401
    texte: str,
    *,
    cles: Sequence[Mapping[str, Any]] | None = None,
    limite: int = 10,
) -> list[dict[str, Any]]:
    """Détecte un modèle dans une requête libre (« génois Bavaria 45 »).

    Une correspondance exacte/alias supprime les préfixes plus courts : sans
    cette règle, chercher ``Bavaria 45`` ferait aussi remonter ``45.1`` et
    ``45 CC`` au seul motif que ``Bavaria`` en est un préfixe — exactement la
    fusion silencieuse interdite par le plan.
    """
    cles_effectives = list(cles) if cles is not None else charger_cles_modeles(cursor)
    requete = normaliser_modele_bateau(texte)
    tokens = requete.split()
    if not tokens or not cles_effectives:
        return []
    par_cle: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for entree in cles_effectives:
        par_cle[str(entree["cle"])].append(entree)
    max_tokens = max((len(str(entree["cle"]).split()) for entree in cles_effectives), default=0)
    max_tokens = min(max_tokens, 8)

    exactes: list[tuple[int, dict[str, Any]]] = []
    for debut in range(len(tokens)):
        for longueur in range(1, min(max_tokens, len(tokens) - debut) + 1):
            phrase = " ".join(tokens[debut : debut + longueur])
            for entree in par_cle.get(phrase, []):
                type_correspondance = (
                    TYPE_EXACT if entree.get("type_cle") == "canonique" else TYPE_ALIAS
                )
                exactes.append(
                    (
                        longueur,
                        _candidate_depuis_cle(
                            entree,
                            type_correspondance,
                            1.0 if type_correspondance == TYPE_EXACT else 0.98,
                        ),
                    )
                )
    if exactes:
        longueur_max = max(longueur for longueur, _candidate in exactes)
        return _garder_meilleures(
            [candidate for longueur, candidate in exactes if longueur == longueur_max], limite
        )

    prefixes: list[tuple[int, dict[str, Any]]] = []
    phrases: set[str] = set()
    for debut in range(len(tokens)):
        for longueur in range(1, min(max_tokens, len(tokens) - debut) + 1):
            phrase = " ".join(tokens[debut : debut + longueur])
            if len(phrase) >= 3:
                phrases.add(phrase)
    for entree in cles_effectives:
        cle = str(entree["cle"])
        for phrase in phrases:
            if cle.startswith(phrase):
                prefixes.append(
                    (
                        len(phrase),
                        _candidate_depuis_cle(
                            entree,
                            TYPE_PREFIXE,
                            0.70 + 0.25 * (len(phrase) / max(len(cle), 1)),
                        ),
                    )
                )
    if prefixes:
        longueur_max = max(longueur for longueur, _candidate in prefixes)
        return _garder_meilleures(
            [candidate for longueur, candidate in prefixes if longueur == longueur_max], limite
        )

    floues: list[dict[str, Any]] = []
    essais = {requete}
    for debut in range(len(tokens)):
        for longueur in range(1, min(max_tokens, len(tokens) - debut) + 1):
            essais.add(" ".join(tokens[debut : debut + longueur]))
    for essai in sorted((essai for essai in essais if len(essai) >= 4), key=len, reverse=True)[:12]:
        floues.extend(rechercher_modeles_flous(cursor, essai, limite=limite))
    return _garder_meilleures(floues, limite)


def _jsonable(valeur: Any) -> Any:  # noqa: ANN401
    if isinstance(valeur, Mapping):
        return {str(cle): _jsonable(item) for cle, item in valeur.items()}
    if isinstance(valeur, (list, tuple)):
        return [_jsonable(item) for item in valeur]
    if hasattr(valeur, "isoformat"):
        return valeur.isoformat()
    return valeur


def _instantane_modele(cursor: Any, modele: ModeleBateau) -> dict[str, Any]:  # noqa: ANN401
    cursor.execute(
        "SELECT id_alias, alias_brut, cle_normalisee FROM boat_model_aliases "
        "WHERE id_modele = %s ORDER BY id_alias",
        (modele.id_modele,),
    )
    alias = [
        {"id_alias": int(ligne[0]), "alias": str(ligne[1]), "cle_normalisee": str(ligne[2])}
        for ligne in cursor.fetchall()
    ]
    cursor.execute(
        "SELECT id_bateau FROM bateau WHERE boat_model_id = %s ORDER BY id_bateau",
        (modele.id_modele,),
    )
    ids_bateaux = [int(ligne[0]) for ligne in cursor.fetchall()]
    cursor.execute(
        "SELECT id_modele FROM boat_models WHERE id_modele_fusion = %s ORDER BY id_modele",
        (modele.id_modele,),
    )
    entrants = [int(ligne[0]) for ligne in cursor.fetchall()]
    return {
        "modele": modele.vers_dict(),
        "alias": alias,
        "ids_bateaux": ids_bateaux,
        "ids_modeles_entrants": entrants,
    }


def _inserer_historique(
    cursor: Any,  # noqa: ANN401
    *,
    action: str,
    id_source: int,
    id_cible: int,
    acteur_id: int | None,
    motif: str | None,
    instantane: Mapping[str, Any],
    id_origine: int | None = None,
) -> int:
    from psycopg2.extras import Json

    cursor.execute(
        "INSERT INTO boat_model_merge_history "
        "(action, id_modele_source, id_modele_cible, id_historique_origine, effectue_par, motif, instantane) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id_historique",
        (
            action,
            int(id_source),
            int(id_cible),
            id_origine,
            acteur_id,
            motif,
            Json(_jsonable(instantane)),
        ),
    )
    return int(cursor.fetchone()[0])


def fusionner_modeles(
    cursor: Any,  # noqa: ANN401
    id_source: int,
    id_cible: int,
    *,
    effectue_par: int | None = None,
    motif: str | None = None,
) -> dict[str, Any]:
    """Fusion explicite et réversible de deux modèles distincts."""
    source_id, cible_id = int(id_source), int(id_cible)
    if source_id == cible_id:
        raise ValueError("Un modèle de bateau ne peut pas être fusionné avec lui-même.")
    cursor.execute(
        "SELECT id_modele, nom_canonique, cle_normalisee, source, actif, id_modele_fusion "
        "FROM boat_models WHERE id_modele = ANY(%s) ORDER BY id_modele FOR UPDATE",
        ([source_id, cible_id],),
    )
    modeles = {
        modele.id_modele: modele
        for modele in (_modele_depuis_ligne(ligne) for ligne in cursor.fetchall())
    }
    source, cible = modeles.get(source_id), modeles.get(cible_id)
    if source is None or cible is None:
        raise ValueError("Modèle source ou cible introuvable pour la fusion.")
    if source.id_modele_fusion is not None:
        raise ValueError("Le modèle source est déjà fusionné ; défusionner d'abord.")
    if cible.id_modele_fusion is not None:
        raise ValueError("La cible est elle-même fusionnée ; choisir le modèle final.")
    instantane = {
        "avant": {
            "source": _instantane_modele(cursor, source),
            "cible": _instantane_modele(cursor, cible),
        }
    }
    ids_bateaux = instantane["avant"]["source"]["ids_bateaux"]
    ids_entrants = instantane["avant"]["source"]["ids_modeles_entrants"]
    if ids_entrants:
        cursor.execute(
            "UPDATE boat_models SET id_modele_fusion = %s, updated_at = now() "
            "WHERE id_modele = ANY(%s)",
            (cible_id, ids_entrants),
        )
    if ids_bateaux:
        cursor.execute(
            "UPDATE bateau SET boat_model_id = %s WHERE id_bateau = ANY(%s)",
            (cible_id, ids_bateaux),
        )
    cursor.execute(
        "UPDATE boat_models SET actif = false, id_modele_fusion = %s, fusionne_le = now(), "
        "fusionne_par = %s, updated_at = now() WHERE id_modele = %s",
        (cible_id, effectue_par, source_id),
    )
    id_historique = _inserer_historique(
        cursor,
        action="fusion",
        id_source=source_id,
        id_cible=cible_id,
        acteur_id=effectue_par,
        motif=motif,
        instantane=instantane,
    )
    return {
        "id_historique": id_historique,
        "action": "fusion",
        "id_modele_source": source_id,
        "id_modele_cible": cible_id,
        "bateaux_relie": len(ids_bateaux),
        "modeles_entrants_relie": len(ids_entrants),
    }


def defusionner_modele(
    cursor: Any,  # noqa: ANN401
    id_source: int,
    *,
    effectue_par: int | None = None,
    motif: str | None = None,
    id_historique: int | None = None,
) -> dict[str, Any]:
    """Annule la fusion d'un modèle à partir de son instantané.

    Sans ``id_historique``, la dernière fusion vers la cible actuelle est
    annulée. Un identifiant fourni doit désigner cette même fusion : on ne
    restaure jamais un instantané d'une autre paire source/cible.
    """
    source_id = int(id_source)
    cursor.execute(
        "SELECT id_modele, nom_canonique, cle_normalisee, source, actif, id_modele_fusion "
        "FROM boat_models WHERE id_modele = %s FOR UPDATE",
        (source_id,),
    )
    ligne = cursor.fetchone()
    if ligne is None:
        raise ValueError(f"Modèle de bateau introuvable : {source_id}.")
    source = _modele_depuis_ligne(ligne)
    if source.id_modele_fusion is None:
        raise ValueError("Le modèle n'est pas fusionné : rien à défaire.")
    cible_id = source.id_modele_fusion
    if id_historique is not None:
        cursor.execute(
            "SELECT id_historique, instantane FROM boat_model_merge_history "
            "WHERE id_historique = %s AND action = 'fusion' "
            "AND id_modele_source = %s AND id_modele_cible = %s",
            (int(id_historique), source_id, cible_id),
        )
        historique = cursor.fetchone()
        if historique is None:
            raise ValueError("Historique de fusion introuvable pour ce modèle.")
    else:
        cursor.execute(
            "SELECT id_historique, instantane FROM boat_model_merge_history "
            "WHERE action = 'fusion' AND id_modele_source = %s AND id_modele_cible = %s "
            "ORDER BY id_historique DESC LIMIT 1",
            (source_id, cible_id),
        )
        historique = cursor.fetchone()
    id_origine = int(historique[0]) if historique is not None else None
    instantane_origine = historique[1] if historique is not None else {}
    if isinstance(instantane_origine, str):
        instantane_origine = json.loads(instantane_origine)
    avant_fusion = (instantane_origine or {}).get("avant", {}).get("source", {})
    ids_bateaux = [int(valeur) for valeur in avant_fusion.get("ids_bateaux", [])]
    ids_entrants = [int(valeur) for valeur in avant_fusion.get("ids_modeles_entrants", [])]
    if historique is None:
        # Fusion réalisée hors de ce service : on ne devine que depuis les
        # libellés dont la clé appartient encore au modèle source.
        cles_source = {source.cle_normalisee}
        cursor.execute(
            "SELECT cle_normalisee FROM boat_model_aliases WHERE id_modele = %s",
            (source_id,),
        )
        cles_source.update(str(ligne_alias[0]) for ligne_alias in cursor.fetchall())
        cursor.execute(
            "SELECT id_bateau, nom FROM bateau WHERE boat_model_id = %s",
            (cible_id,),
        )
        ids_bateaux = [
            int(id_bateau)
            for id_bateau, nom in cursor.fetchall()
            if normaliser_modele_bateau(str(nom)) in cles_source
        ]
    instantane = {
        "avant_defusion": {
            "source": _instantane_modele(cursor, source),
            "ids_bateaux_a_restaurer": ids_bateaux,
            "ids_modeles_entrants_a_restaurer": ids_entrants,
        }
    }
    if ids_bateaux:
        cursor.execute(
            "UPDATE bateau SET boat_model_id = %s "
            "WHERE id_bateau = ANY(%s) AND boat_model_id = %s",
            (source_id, ids_bateaux, cible_id),
        )
    if ids_entrants:
        cursor.execute(
            "UPDATE boat_models SET id_modele_fusion = %s, updated_at = now() "
            "WHERE id_modele = ANY(%s) AND id_modele_fusion = %s",
            (source_id, ids_entrants, cible_id),
        )
    cursor.execute(
        "UPDATE boat_models SET actif = true, id_modele_fusion = NULL, fusionne_le = NULL, "
        "fusionne_par = NULL, updated_at = now() WHERE id_modele = %s",
        (source_id,),
    )
    id_historique = _inserer_historique(
        cursor,
        action="defusion",
        id_source=source_id,
        id_cible=cible_id,
        acteur_id=effectue_par,
        motif=motif,
        instantane=instantane,
        id_origine=id_origine,
    )
    return {
        "id_historique": id_historique,
        "id_historique_origine": id_origine,
        "action": "defusion",
        "id_modele_source": source_id,
        "id_modele_cible": cible_id,
        "bateaux_restaure": len(ids_bateaux),
        "modeles_entrants_restaure": len(ids_entrants),
    }


def lister_modeles(cursor: Any, *, inclure_fusionnes: bool = False) -> list[dict[str, Any]]:  # noqa: ANN401
    """Référentiel lisible : modèles, alias et effectifs réels."""
    modeles = _lire_tous_modeles(cursor)
    cursor.execute(
        "SELECT id_modele, alias_brut, cle_normalisee, source FROM boat_model_aliases ORDER BY id_alias"
    )
    alias_par_modele: dict[int, list[dict[str, str]]] = defaultdict(list)
    for id_modele, alias, cle, source in cursor.fetchall():
        alias_par_modele[int(id_modele)].append(
            {"alias": str(alias), "cle_normalisee": str(cle), "source": str(source or "")}
        )
    cursor.execute(
        "SELECT b.boat_model_id, count(DISTINCT b.id_bateau)::int, count(f.id_fiche)::int "
        "FROM bateau b LEFT JOIN fiche f ON f.id_bateau = b.id_bateau "
        "WHERE b.boat_model_id IS NOT NULL GROUP BY b.boat_model_id"
    )
    compteurs = {int(ligne[0]): (int(ligne[1]), int(ligne[2])) for ligne in cursor.fetchall()}
    resultat: list[dict[str, Any]] = []
    for modele in sorted(modeles.values(), key=lambda modele: (modele.nom_canonique.casefold(), modele.id_modele)):
        if modele.id_modele_fusion is not None and not inclure_fusionnes:
            continue
        nb_bateaux, nb_fiches = compteurs.get(modele.id_modele, (0, 0))
        resultat.append(
            {
                **modele.vers_dict(),
                "alias": alias_par_modele.get(modele.id_modele, []),
                "nb_bateaux": nb_bateaux,
                "nb_fiches": nb_fiches,
            }
        )
    return resultat
