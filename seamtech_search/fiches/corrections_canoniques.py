"""Propagation d'une correction humaine vers les données métier CANONIQUES.

Défaut corrigé (A01 de l'audit de préparation du 2026-10-08) : ``corriger_champ``
n'écrivait que la TRACE (``fiche_champ_extrait.valeur_normalisee`` + ``corrige``).
Les tables typées — ``fiche_cotes`` en tête, mais aussi ``fiche_materiau``,
``fiche_finition``… — gardaient l'ancienne valeur, et comme le texte de recherche
pondéré (migrations 013/018/019) et les filtres numériques de dimension
(migration 014/019) sont calculés À PARTIR de ces tables, une correction de
« 6,6 » en « 7,7 » changeait ce que l'opérateur VOIT dans la revue sans changer
ce que l'atelier CHERCHE. Un « corrigé » qui ne corrige pas la donnée qui sert
est un faux succès, et c'est exactement ce que la revue a reproduit.

Ce module dit, pour chaque champ d'extraction, quelle donnée canonique il
représente — et refuse tout le reste EXPLICITEMENT.

Invariant de sécurité, non négociable :
    Les noms de tables et de colonnes écrits ici sont des **littéraux du code**.
    ``table_cible`` / ``colonne_cible`` viennent de la base (donc, en dernier
    ressort, du document et des outils d'extraction) et ne sont JAMAIS concaténés
    dans du SQL : ils ne servent qu'à CHOISIR une entrée de ce registre par
    égalité stricte. Toute valeur part en paramètre lié.

Trois issues possibles, jamais confondues :

* ``appliquee``   — la cible est connue ET la valeur est valide : la donnée
                    canonique est écrite, la trace reste la référence de revue ;
* ``aucune``      — le champ ne désigne aucune donnée typée (trace seule :
                    c'est le cas normal d'un champ « libre ») ;
* ``non_supportee`` — le champ DÉSIGNE une donnée typée que ce registre ne sait
                    pas écrire (identité de fiche, clé étrangère, champ
                    composite). La correction est CONSERVÉE comme trace de revue
                    mais la réponse le DIT, avec la raison : plus jamais
                    « corrigé » sans dire ce qui, en base, a réellement changé.

Une valeur INVALIDE pour une cible connue lève : rien n'est écrit (transaction
annulée), et l'opérateur reçoit 422 plutôt qu'un succès trompeur.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

LOGGER = logging.getLogger("seamtech_search.fiches.corrections_canoniques")

# --------------------------------------------------------------------------- #
# Vocabulaires contrôlés (littéraux du code — pas des données de la base)
# --------------------------------------------------------------------------- #

#: Les 7 cotes de ``fiche_cotes`` (miroir exact de la migration 006).
COLONNES_COTES: tuple[str, ...] = (
    "slu_m",
    "sle_m",
    "sf_m",
    "shw_m",
    "spa_m2",
    "tetiere_cm",
    "poids_kg",
)
JEUX_COTES: tuple[str, ...] = ("dessin", "finie")

#: Les 3 bandes de ``fiche_galon`` (CHECK de la migration 006).
BANDES_GALON: tuple[str, ...] = ("guindant", "chute", "bordure")

#: Champs d'extraction ``fiche.*`` alimentables par correction, et la colonne
#: PHYSIQUE qu'ils désignent. Indexé par CHAMP et non par ``colonne_cible`` :
#: le dernier segment du nom de champ est ambigu (« fiche.montage » désigne la
#: colonne ``montage_type``) et un registre bâti sur lui écrirait à côté.
#:
#: Volontairement ABSENTS (donc ``non_supportee``, jamais un succès muet) :
#: ``fiche.code`` (identité de la fiche), ``fiche.client`` / ``fiche.bateau`` /
#: ``fiche.commande`` (clés étrangères à RÉSOUDRE, pas du texte),
#: ``fiche.fichier_source`` / ``fiche.designation`` (métadonnée technique et
#: champ composite du titre — la cible est un ``traitement:``, pas une colonne).
COLONNES_FICHE_PAR_CHAMP: dict[str, str] = {
    "fiche.titre": "titre",
    "fiche.gamme": "gamme",
    "fiche.segment": "segment",
    "fiche.atelier": "atelier",
    "fiche.tissu_texte": "tissu_texte",
    "fiche.montage": "montage_type",
    "fiche.montage_type": "montage_type",
    "fiche.montage_fil": "montage_fil",
    "fiche.notes": "notes",
    "fiche.dessinateur": "dessinateur",
}

#: Champs ``fiche.*`` NUMÉRIQUES : la valeur corrigée doit être un entier
#: (une quantité « 3,5 » est une faute de frappe, pas une quantité).
COLONNES_FICHE_ENTIER: dict[str, str] = {
    "fiche.quantite": "quantite",
}

#: Unité attendue par colonne canonique — sert à la VALIDATION D'UNITÉ : une
#: valeur « 50 mm » corrigée dans une colonne en mètres est refusée, pas
#: convertie en silence (l'opérateur a écrit ce qu'il voulait dire).
UNITES_COLONNES: dict[str, tuple[str, ...]] = {
    "slu_m": ("m",),
    "sle_m": ("m",),
    "sf_m": ("m",),
    "shw_m": ("m",),
    "spa_m2": ("m2", "m²"),
    "tetiere_cm": ("cm",),
    "poids_kg": ("kg",),
}

#: Bornes de plausibilité par colonne canonique : une cote de voilier dépasse
#: rarement ces ordres de grandeur, et une valeur absurde (négative, 1e12)
#: signalerait une faute de frappe que la revue doit voir AVANT la base.
BORNES_COLONNES: dict[str, tuple[float, float]] = {
    "slu_m": (0.0, 100.0),
    "sle_m": (0.0, 100.0),
    "sf_m": (0.0, 100.0),
    "shw_m": (0.0, 100.0),
    "spa_m2": (0.0, 1000.0),
    "tetiere_cm": (0.0, 1000.0),
    "poids_kg": (0.0, 10000.0),
}

_NOMBRE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
_JONCTION = re.compile(r"^(?P<nature>[A-Za-zÀ-ÿ0-9_ \-]{1,60})\[(?P<ordre>\d{1,3})\]$")
_EPAISSEUR = re.compile(r"^epaisseur_(\d{2})$")
_RENFORT = re.compile(r"^renfort_(\d{1,2})$")
#: Code d'option : les codes réels sont produits par l'extraction à partir du
#: libellé du document (« emmagasineur », « v_trim »…). Le motif est celui de
#: cette génération ; il est validé pour qu'un libellé quelconque ne devienne
#: jamais la CLÉ d'une écriture (et la ligne doit exister, sinon refus).
_CODE_OPTION = re.compile(r"^[a-z0-9][a-z0-9_]{0,59}$")
_LONGUEUR_TEXTE_MAX = 500


class CorrectionCanoniqueRefusee(ValueError):
    """Valeur invalide pour une cible canonique connue — rien ne doit être écrit."""

    def __init__(self, message: str, *, cible: str, valeur: Any) -> None:
        super().__init__(message)
        self.cible = cible
        self.valeur = valeur


@dataclass(frozen=True)
class CibleCanonique:
    """Description d'une donnée métier canonique alimentable par correction.

    ``where`` contient les fragments SQL LITTÉRAUX de localisation de la ligne ;
    ``parametres`` porte leurs VALEURS (toujours liées, jamais concaténées).
    """

    table: str
    colonne: str
    where: str
    parametres: tuple[Any, ...]
    cle: str
    #: Recalcule la valeur écrite dans la base (normalisation + validation).
    convertir: Callable[[str], Any]
    #: Colonne de ``fiche`` mise en avant dans le rapport (lecture de contrôle).
    relecture: str = "valeur"
    #: Colonne booléenne dérivée de la même valeur, quand elle existe
    #: (``fiche_option.valeur_bool``) : les deux colonnes restent cohérentes.
    colonne_bool: str | None = None

    @property
    def etiquette(self) -> str:
        return f"{self.table}.{self.colonne}"


@dataclass(frozen=True)
class ResultatPropagation:
    """Ce qui a RÉELLEMENT été fait — jamais une intention.

    ``statut`` : ``appliquee`` / ``aucune`` / ``non_supportee``.
    """

    statut: str
    cible: CibleCanonique | None = None
    raison: str | None = None
    valeur_ecrite: Any = None
    champs: dict[str, Any] = field(default_factory=dict)

    @property
    def appliquee(self) -> bool:
        return self.statut == "appliquee"

    def to_dict(self) -> dict[str, Any]:
        charge: dict[str, Any] = {"statut": self.statut}
        if self.cible is not None:
            charge["cible"] = self.cible.etiquette
            charge["cle"] = self.cible.cle
        if self.valeur_ecrite is not None:
            charge["valeur"] = (
                float(self.valeur_ecrite)
                if isinstance(self.valeur_ecrite, (int, float))
                else self.valeur_ecrite
            )
        if self.raison:
            charge["raison"] = self.raison
        if self.champs:
            charge.update(self.champs)
        return charge


# --------------------------------------------------------------------------- #
# Convertisseurs (validation de type ET d'unité)
# --------------------------------------------------------------------------- #


def _texte(valeur: str) -> str:
    texte = str(valeur).strip()
    if not texte:
        raise ValueError("valeur vide")
    if len(texte) > _LONGUEUR_TEXTE_MAX:
        raise ValueError(f"valeur trop longue ({len(texte)} caractères, max {_LONGUEUR_TEXTE_MAX})")
    if "\x00" in texte:
        raise ValueError("valeur contient un octet NUL")
    return texte


def _texte_jamais_vide(valeur: str) -> str:
    return _texte(valeur)


def _nombre_unite(colonne: str) -> Callable[[str], float]:
    """Convertisseur numérique d'une colonne de cote : nombre + unité vérifiée."""

    def convertir(valeur: str) -> float:
        brut = str(valeur).strip().replace("\u00a0", " ")
        if not brut:
            raise ValueError("valeur vide")
        morceaux = brut.split()
        # Le séparateur décimal français est accepté : « 7,7 » == « 7.7 ».
        texte_nombre = morceaux[0]
        if not _NOMBRE.match(texte_nombre):
            raise ValueError(f"« {valeur} » n'est pas un nombre")
        nombre = float(texte_nombre.replace(",", "."))
        if len(morceaux) > 1:
            unite = " ".join(morceaux[1:]).strip().lower().replace("²", "2")
            attendues = UNITES_COLONNES.get(colonne, ())
            if unite not in {u.lower().replace("²", "2") for u in attendues}:
                raise ValueError(
                    f"unité « {morceaux[1]} » inattendue pour {colonne} "
                    f"(attendu : {' ou '.join(attendues) or 'sans unité'})"
                )
        bornes = BORNES_COLONNES.get(colonne)
        if bornes is not None and not (bornes[0] <= nombre <= bornes[1]):
            raise ValueError(
                f"valeur {nombre} hors bornes plausibles pour {colonne} "
                f"[{bornes[0]} ; {bornes[1]}]"
            )
        return nombre

    return convertir


def _entier(valeur: str) -> int:
    texte = str(valeur).strip()
    if not _NOMBRE.match(texte):
        raise ValueError(f"« {valeur} » n'est pas un entier")
    nombre = float(texte.replace(",", "."))
    if nombre != int(nombre):
        raise ValueError(f"« {valeur} » n'est pas un entier")
    return int(nombre)


# --------------------------------------------------------------------------- #
# Résolution : (champ, table_cible, colonne_cible) -> CibleCanonique | refus
# --------------------------------------------------------------------------- #


class _NonSupporte(Exception):
    """Le champ désigne une donnée typée que le registre ne sait pas écrire."""

    def __init__(self, raison: str) -> None:
        super().__init__(raison)
        self.raison = raison


def _cible_fiche(champ: str, colonne_cible: str) -> CibleCanonique:
    """Cible d'un champ ``fiche.*`` — résolue par CHAMP, pas par colonne cible."""
    if champ in COLONNES_FICHE_PAR_CHAMP:
        return CibleCanonique(
            table="fiche",
            colonne=COLONNES_FICHE_PAR_CHAMP[champ],
            where="id_fiche = %s",
            parametres=(),
            cle="id_fiche",
            convertir=_texte,
        )
    if champ in COLONNES_FICHE_ENTIER:
        return CibleCanonique(
            table="fiche",
            colonne=COLONNES_FICHE_ENTIER[champ],
            where="id_fiche = %s",
            parametres=(),
            cle="id_fiche",
            convertir=_entier,
        )
    raise _NonSupporte(
        f"champ « {champ} » (colonne annoncée « {colonne_cible} ») non supporté : "
        "identité de fiche, clé étrangère à résoudre ou champ composite — "
        "correction conservée en trace, donnée canonique inchangée"
    )


def _cible_cotes(champ: str, colonne_cible: str) -> CibleCanonique:
    if colonne_cible not in COLONNES_COTES:
        raise _NonSupporte(f"colonne de cote « {colonne_cible} » inconnue du registre")
    morceaux = champ.split(".")
    if len(morceaux) != 3 or morceaux[0] != "cotes":
        raise _NonSupporte(
            f"champ « {champ} » ne porte pas le jeu de cotes (attendu cotes.<jeu>.<colonne>)"
        )
    jeu, colonne_champ = morceaux[1], morceaux[2]
    if jeu not in JEUX_COTES:
        raise _NonSupporte(f"jeu de cotes « {jeu} » inconnu (dessin | finie)")
    if colonne_champ != colonne_cible:
        raise _NonSupporte(
            f"champ « {champ} » et colonne cible « {colonne_cible} » se contredisent"
        )
    return CibleCanonique(
        table="fiche_cotes",
        colonne=colonne_cible,
        where="id_fiche = %s AND jeu = %s",
        parametres=(jeu,),
        cle=f"jeu={jeu}",
        convertir=_nombre_unite(colonne_cible),
    )


def _cible_materiau(colonne_cible: str) -> CibleCanonique:
    if colonne_cible == "tissu_principal":
        return CibleCanonique(
            table="fiche_materiau",
            colonne="designation_texte",
            where="id_fiche = %s AND role = 'tissu_principal'",
            parametres=(),
            cle="role=tissu_principal",
            convertir=_texte,
        )
    correspondance = _EPAISSEUR.match(colonne_cible)
    if correspondance:
        niveau = int(correspondance.group(1))
        if not 1 <= niveau <= 10:
            raise _NonSupporte(f"niveau d'épaisseur {niveau} hors plage (01→10)")
        return CibleCanonique(
            table="fiche_materiau",
            colonne="designation_texte",
            where="id_fiche = %s AND role = 'epaisseur' AND niveau = %s",
            parametres=(niveau,),
            cle=f"role=epaisseur,niveau={niveau}",
            convertir=_texte,
        )
    raise _NonSupporte(f"colonne de matériau « {colonne_cible} » inconnue du registre")


def _cible_galon(colonne_cible: str) -> CibleCanonique:
    if colonne_cible not in BANDES_GALON:
        raise _NonSupporte(f"bande de galon « {colonne_cible} » inconnue (guindant | chute | bordure)")
    return CibleCanonique(
        table="fiche_galon",
        colonne="couleur",
        where="id_fiche = %s AND bande = %s",
        parametres=(colonne_cible,),
        cle=f"bande={colonne_cible}",
        convertir=_texte,
    )


def _cible_jonction(colonne_cible: str) -> CibleCanonique:
    if colonne_cible == "surplus":
        return CibleCanonique(
            table="fiche_jonction",
            colonne="surplus",
            where="id_fiche = %s AND nature = 'surplus'",
            parametres=(),
            cle="nature=surplus",
            convertir=_texte,
        )
    correspondance = _JONCTION.match(colonne_cible)
    if not correspondance:
        raise _NonSupporte(f"colonne de jonction « {colonne_cible} » non reconnue (nature[ordre])")
    nature = correspondance.group("nature")
    ordre = int(correspondance.group("ordre"))
    return CibleCanonique(
        table="fiche_jonction",
        colonne="description",
        where="id_fiche = %s AND nature = %s AND ordre = %s",
        parametres=(nature, ordre),
        cle=f"nature={nature},ordre={ordre}",
        convertir=_texte,
    )


def _cible_finition(colonne_cible: str) -> CibleCanonique:
    poste = str(colonne_cible).strip()
    if not poste or len(poste) > _LONGUEUR_TEXTE_MAX or "\x00" in poste:
        raise _NonSupporte(f"poste de finition « {colonne_cible} » inutilisable")
    return CibleCanonique(
        table="fiche_finition",
        colonne="valeur_texte",
        where="id_fiche = %s AND poste = %s",
        parametres=(poste,),
        cle=f"poste={poste}",
        convertir=_texte,
    )


def _cible_option(colonne_cible: str) -> CibleCanonique:
    """Option de fiche : la cible est la ligne ``fiche_option`` du CODE annoncé.

    ``colonne_cible`` porte le code d'option (``EMMAGASINEUR``…), pas une
    colonne : c'est l'identité de la ligne. Il est validé (forme et longueur)
    AVANT de servir de valeur liée — jamais concaténé.
    """
    code = str(colonne_cible).strip()
    if not _CODE_OPTION.match(code):
        raise _NonSupporte(f"code d'option « {colonne_cible} » inutilisable")
    return CibleCanonique(
        table="fiche_option",
        colonne="valeur_texte",
        where="id_fiche = %s AND code = %s",
        parametres=(code,),
        cle=f"code={code}",
        convertir=_texte,
        # Une option est aussi lue comme booléen (migration 013) : quand la
        # valeur corrigée EST un booléen, les deux colonnes disent la même
        # chose. Une valeur non booléenne laisse ``valeur_bool`` tel quel.
        colonne_bool="valeur_bool",
    )


def _cible_renfort(colonne_cible: str) -> CibleCanonique:
    """Renfort n°N : ``colonne_cible`` vaut ``renfort_N``.

    ``fiche_renfort`` n'a pas de colonne d'ordre : l'ordre d'insertion (donc
    ``id_renfort``) est l'ordre du document, et c'est celui de l'extraction.
    La sous-requête désigne la N-ième ligne ; si elle n'existe pas, l'écriture
    ne touche AUCUNE ligne et la correction est refusée (422), jamais écrite
    dans le vide.
    """
    correspondance = _RENFORT.match(str(colonne_cible).strip())
    if not correspondance:
        raise _NonSupporte(f"renfort « {colonne_cible} » non reconnu (attendu renfort_N)")
    rang = int(correspondance.group(1))
    if not 1 <= rang <= 20:
        raise _NonSupporte(f"rang de renfort {rang} hors plage (1→20)")
    return CibleCanonique(
        table="fiche_renfort",
        colonne="description",
        where=(
            "id_renfort = (SELECT id_renfort FROM fiche_renfort WHERE id_fiche = %s "
            "ORDER BY id_renfort OFFSET %s LIMIT 1)"
        ),
        parametres=(rang - 1,),
        cle=f"renfort_{rang}",
        convertir=_texte,
    )


def resoudre_cible(
    champ: str, table_cible: str | None, colonne_cible: str | None
) -> tuple[CibleCanonique | None, str | None]:
    """Cible canonique d'un champ d'extraction, ou (None, raison).

    ``raison`` non nulle signifie « ce champ désigne bien une donnée typée, mais
    ce registre ne l'écrit pas » — l'appelant DOIT le dire à l'opérateur au lieu
    de rapporter un succès muet. ``(None, None)`` = trace seule, cas normal.
    """
    if not table_cible or not colonne_cible:
        return None, None
    table = str(table_cible).strip()
    colonne = str(colonne_cible).strip()
    try:
        if table == "fiche":
            return _cible_fiche(str(champ), colonne), None
        if table == "fiche_cotes":
            return _cible_cotes(str(champ), colonne), None
        if table == "fiche_materiau":
            return _cible_materiau(colonne), None
        if table == "fiche_galon":
            return _cible_galon(colonne), None
        if table == "fiche_jonction":
            return _cible_jonction(colonne), None
        if table == "fiche_finition":
            return _cible_finition(colonne), None
        if table == "fiche_option":
            return _cible_option(colonne), None
        if table == "fiche_renfort":
            return _cible_renfort(colonne), None
    except _NonSupporte as refus:
        return None, refus.raison
    if table.startswith("fiche") or table in {"materiau", "client", "bateau", "commande"}:
        return None, (
            f"table cible « {table} » non supportée par la propagation canonique "
            "(correction conservée en trace, donnée métier inchangée)"
        )
    return None, None


# --------------------------------------------------------------------------- #
# Écriture
# --------------------------------------------------------------------------- #


def appliquer_correction_canonique(
    cursor: Any, index: Any, id_fiche: int, id_champ: int, cible: CibleCanonique, valeur: str
) -> dict[str, Any]:
    """Écrit la valeur corrigée dans la donnée canonique — ou refuse.

    Lève :class:`CorrectionCanoniqueRefusee` si la valeur est invalide ou si la
    ligne canonique attendue n'existe pas (mieux vaut un refus qu'une écriture
    dans le vide, qui laisserait trace et donnée divergentes).
    """
    try:
        convertie = cible.convertir(valeur)
    except ValueError as erreur:
        raise CorrectionCanoniqueRefusee(
            f"correction refusée pour {cible.etiquette} : {erreur}",
            cible=cible.etiquette,
            valeur=valeur,
        ) from erreur

    parametres = (convertie, id_fiche, *cible.parametres)
    sql = f"UPDATE {cible.table} SET {cible.colonne} = %s WHERE {cible.where}"
    cursor.execute(sql, parametres)
    if cursor.rowcount == 0:
        raise CorrectionCanoniqueRefusee(
            f"correction refusée : aucune ligne {cible.etiquette} ({cible.cle}) pour cette fiche "
            "— la donnée canonique n'a pas été créée par l'extraction",
            cible=cible.etiquette,
            valeur=valeur,
        )

    boolen = _booleen(convertie) if cible.colonne_bool else None
    if cible.colonne_bool and boolen is not None:
        cursor.execute(
            f"UPDATE {cible.table} SET {cible.colonne_bool} = %s WHERE {cible.where}",
            (boolen, id_fiche, *cible.parametres),
        )

    # Le texte de recherche pondéré est DÉRIVÉ des tables métier (migrations
    # 013/018/019) : sans ce rafraîchissement, la valeur corrigée serait
    # introuvable en recherche alors que l'ancienne y resterait. Il a lieu dans
    # la MÊME transaction : soit la correction et son index de recherche
    # changent ensemble, soit rien ne change.
    cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (id_fiche,))
    return {
        "cible": cible.etiquette,
        "cle": cible.cle,
        "valeur_ecrite": convertie,
        "recherche_rafraichie": True,
    }


def _booleen(valeur: Any) -> bool | None:
    """Interprétation booléenne d'une valeur d'option — ou None (pas un booléen).

    Vocabulaire FERMÉ : « oui/non », « true/false », « 1/0 », « x », « ~ ».
    Toute autre valeur n'est PAS convertie (elle reste du texte) : on ne
    devine pas ce que l'opérateur a voulu dire.
    """
    texte = str(valeur).strip().casefold()
    if texte in {"oui", "true", "1", "x", "~", "yes", "vrai"}:
        return True
    if texte in {"non", "false", "0", "no", "faux", "-"}:
        return False
    return None


def _normaliser_comparaison(valeur: Any) -> str:
    """Forme comparable d'une valeur (nombre en 3 décimales, texte replié)."""
    if valeur is None:
        return ""
    if isinstance(valeur, bool):
        return "true" if valeur else "false"
    if isinstance(value := valeur, (int, float)):
        return f"{float(value):.3f}".rstrip("0").rstrip(".")
    try:
        return f"{float(str(valeur).replace(',', '.')):.3f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return " ".join(str(valeur).split()).casefold()


def _lire_canonique(cursor: Any, cible: CibleCanonique, id_fiche: int) -> Any:
    """Valeur ACTUELLE de la donnée canonique visée (None si la ligne manque)."""
    cursor.execute(
        f"SELECT {cible.colonne} FROM {cible.table} WHERE {cible.where}",
        (id_fiche, *cible.parametres),
    )
    ligne = cursor.fetchone()
    return None if ligne is None else ligne[0]


# --------------------------------------------------------------------------- #
# Corrections HISTORIQUES divergentes — diagnostic et réconciliation explicite
# --------------------------------------------------------------------------- #
#
# Les corrections enregistrées AVANT ce correctif ont pu laisser une trace
# corrigée et une donnée canonique restée à l'ancienne valeur. La divergence est
# AMBIGUË par nature : on ne sait pas si l'opérateur voulait changer la donnée
# qui sert la fabrication, ou seulement annoter la revue. Ce module ne
# « répare » donc RIEN tout seul : il LISTE (lecture seule, pour l'exploitant)
# et n'écrit que sur une demande EXPLICITE, fiche par fiche, en journalisant.


def diagnostiquer_divergences(index: Any, *, code: str | None = None, limite: int = 500) -> list[dict[str, Any]]:
    """Corrections tracées qui ne coïncident pas avec la donnée canonique.

    Lecture seule. Retourne une ligne par divergence, avec de quoi décider :
    code de fiche, champ, rang, valeur corrigée, valeur canonique actuelle et
    la raison pour laquelle aucune propagation n'est proposée le cas échéant.
    """
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            conditions = ["e.corrige"]
            parametres: list[Any] = []
            if code is not None:
                conditions.append("f.code = %s")
                parametres.append(code)
            cursor.execute(
                "SELECT f.code, f.id_fiche, e.id_champ, e.champ, e.rang, e.table_cible, "
                "e.colonne_cible, e.valeur_normalisee "
                "FROM fiche_champ_extrait e JOIN fiche f ON f.id_fiche = e.id_fiche "
                f"WHERE {' AND '.join(conditions)} ORDER BY f.code, e.id_champ LIMIT %s",
                (*parametres, limite),
            )
            lignes = cursor.fetchall()
            divergences: list[dict[str, Any]] = []
            for ligne in lignes:
                code_fiche, id_fiche, id_champ, champ, rang, table_cible, colonne_cible, corrigee = ligne
                cible, raison = resoudre_cible(str(champ), table_cible, colonne_cible)
                entree: dict[str, Any] = {
                    "code": str(code_fiche),
                    "champ": str(champ),
                    "rang": None if rang is None else int(rang),
                    "table_cible": table_cible,
                    "colonne_cible": colonne_cible,
                    "valeur_corrigee": corrigee,
                }
                if cible is None:
                    entree.update(
                        {
                            "statut": "non_propageable",
                            "raison": raison or "champ sans cible canonique (trace seule)",
                            "valeur_canonique": None,
                        }
                    )
                    if raison:
                        divergences.append(entree)
                    continue
                actuelle = _lire_canonique(cursor, cible, int(id_fiche))
                entree["cible"] = cible.etiquette
                entree["cle"] = cible.cle
                entree["valeur_canonique"] = actuelle
                if _normaliser_comparaison(corrigee) == _normaliser_comparaison(actuelle):
                    entree["statut"] = "coherente"
                else:
                    entree["statut"] = "divergente"
                    divergences.append(entree)
            return divergences


def reconcilier_fiche(
    index: Any, code: str, *, utilisateur: str | None = None, confirmer: bool = False
) -> dict[str, Any]:
    """Ré-applique les corrections PROPRES d'une fiche à ses données canoniques.

    Refusé sans ``confirmer=True`` : l'exploitant doit nommer la fiche ET
    assumer l'écriture. Aucune valeur ambiguë n'est interprétée — seules les
    corrections dont la cible est connue ET la valeur revalidable sont
    appliquées ; les autres sont listées telles quelles.
    """
    if not confirmer:
        raise ValueError(
            "réconciliation refusée : passer confirmer=True après avoir examiné "
            "les divergences de CETTE fiche (jamais de réparation automatique)"
        )
    from seamtech_search.fiches.routes import _jouter_journal, _resoudre_utilisateur  # noqa: PLC0415

    rapport: dict[str, Any] = {"code": code, "appliquees": [], "ignorees": [], "refusees": []}
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT id_fiche FROM fiche WHERE code = %s", (code,))
            ligne = cursor.fetchone()
            if ligne is None:
                raise KeyError(f"fiche inconnue : {code}")
            id_fiche = int(ligne[0])
            cursor.execute(
                "SELECT id_champ, champ, rang, table_cible, colonne_cible, valeur_normalisee "
                "FROM fiche_champ_extrait WHERE id_fiche = %s AND corrige ORDER BY id_champ",
                (id_fiche,),
            )
            for id_champ, champ, rang, table_cible, colonne_cible, corrigee in cursor.fetchall():
                cible, raison = resoudre_cible(str(champ), table_cible, colonne_cible)
                if cible is None:
                    rapport["ignorees"].append(
                        {"champ": str(champ), "rang": rang, "raison": raison or "trace seule"}
                    )
                    continue
                actuelle = _lire_canonique(cursor, cible, id_fiche)
                if _normaliser_comparaison(corrigee) == _normaliser_comparaison(actuelle):
                    continue
                try:
                    detail = appliquer_correction_canonique(
                        cursor, index, id_fiche, int(id_champ), cible, str(corrigee)
                    )
                except CorrectionCanoniqueRefusee as refus:
                    rapport["refusees"].append(
                        {"champ": str(champ), "rang": rang, "cible": cible.etiquette, "raison": str(refus)}
                    )
                    continue
                rapport["appliquees"].append(
                    {"champ": str(champ), "rang": rang, "valeur_canonique_avant": actuelle, **detail}
                )
                _jouter_journal(
                    cursor,
                    id_fiche,
                    _resoudre_utilisateur(cursor, utilisateur),
                    "reconcilier",
                    None,
                    None,
                    f"réconciliation explicite : {cible.etiquette} ({cible.cle}) "
                    f"{actuelle!r} → {corrigee!r}",
                )
    LOGGER.warning(
        "Réconciliation explicite de la fiche %s : %d appliquée(s), %d refusée(s), %d ignorée(s).",
        code,
        len(rapport["appliquees"]),
        len(rapport["refusees"]),
        len(rapport["ignorees"]),
    )
    return rapport
