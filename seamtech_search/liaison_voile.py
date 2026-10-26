"""Résolution du modèle de bateau des extractions de voiles — Lot 3b.

Le Lot 3a extrait un libellé de bateau BRUT (`modele_bateau_brut`). Ce module
le résout vers le référentiel canonique unique du Lot 2 (`boat_models`), avec
des règles qui ne devinent jamais :

* liaison automatique UNIQUEMENT sur preuve déterministe — clé normalisée
  exacte d'un modèle, alias exact déjà appris, ou bateau de la fiche déjà lié
  dont le libellé (nom, nom + taille) correspond exactement ;
* tout candidat seulement préfixe ou flou (pg_trgm) rend l'extraction
  ``ambigu`` : un approbateur tranche (rattacher à un candidat, créer un
  nouveau modèle) — la machine ne fusionne jamais « 45 » dans « 45.1 » ;
* la décision humaine APPREND : le libellé brut devient un alias du modèle
  choisi (source ``apprentissage_lot3b``), les corrections récurrentes
  deviennent des motifs suggérés. Ni les alias ni les motifs n'écrivent une
  donnée métier sans qu'un approbateur ait d'abord décidé ;
* une liaison humaine n'est jamais écrasée par une ré-extraction (esprit
  RG11) ; corriger le libellé brut rouvre explicitement la résolution.

Les corrections d'approbateur vivent dans `extraction_corrections` (avant /
après, auteur, motif) et mettent à jour les colonnes canoniques de
l'extraction ; la provenance machine (`champs`) n'est jamais réécrite. Une
cote qui a une trace dans la fiche est propagée dans la même transaction par
le noyau de correction existant (révision obligatoire, règle RG11).

Tout est local : PostgreSQL et le référentiel du Lot 2, aucune API externe.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from seamtech_search.extractions_voile import (
    _COLONNES_EXTRACTION_VOILE,
    AnomalieExtractionVoile,
    ExtractionVoile,
    _extraction_depuis_ligne,
)
from seamtech_search.fiches.anomalies import evaluer_anomalies
from seamtech_search.fiches.modeles import Cotes, FicheExtraite
from seamtech_search.referentiel_bateaux import (
    ModeleBateau,
    assurer_alias,
    assurer_modele,
    normaliser_modele_bateau,
    rechercher_modeles,
    resoudre_modele_depuis_texte,
    resoudre_modele_final,
)

LOGGER = logging.getLogger("seamtech_search.liaison_voile")

STATUTS_LIAISON = ("non_resolu", "lie_automatique", "lie_humain", "ambigu", "sans_modele")
METHODES_LIAISON = (
    "cle_exacte",
    "alias_exact",
    "fiche_bateau",
    "humain",
    "humain_nouveau_modele",
)
STATUTS_EXTRACTION = ("complete", "partielle", "a_reprendre")

CHAMPS_CORRIGEABLES = (
    "type_voile",
    "gamme",
    "modele_bateau_brut",
    "taille_bateau_brute",
    "slu_m",
    "sle_m",
    "sf_m",
    "shw_m",
    "spa_m2",
    "tetiere_m",
    "poids_kg",
)

#: Champs numériques canoniques (mètres, m², kg) — toujours strictement > 0.
_CHAMPS_NUMERIQUES = ("slu_m", "sle_m", "sf_m", "shw_m", "spa_m2", "tetiere_m", "poids_kg")
#: Textes qu'un approbateur peut vider explicitement (None accepté).
_CHAMPS_TEXTES_EFFACABLES = ("gamme", "taille_bateau_brute")
#: Colonne historique de la fiche pour chaque cote (la têtière y vit en cm).
_COLONNES_FICHE: dict[str, str] = {
    "slu_m": "slu_m",
    "sle_m": "sle_m",
    "sf_m": "sf_m",
    "shw_m": "shw_m",
    "spa_m2": "spa_m2",
    "tetiere_m": "tetiere_cm",
    "poids_kg": "poids_kg",
}
#: Codes produits par RG16 (`fiches.anomalies`) : recalculés après correction,
#: les autres anomalies (provenance, unités) restent l'évidence machine.
_CODES_RG16 = {
    "cote_hors_plage",
    "cotes_incoherentes",
    "surface_incoherente",
    "champ_manquant",
    "unite_suspecte",
}
_CODES_IMPLAUSIBLES = {"cote_hors_plage", "cotes_incoherentes", "surface_incoherente", "unite_suspecte"}

_LONGUEUR_TEXTE_MAX = 300
_LONGUEUR_MOTIF_MAX = 1000
_RE_NOMBRE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")

_COLONNES_SQL = ", ".join(_COLONNES_EXTRACTION_VOILE)


class LiaisonVoileError(ValueError):
    """Base des refus métier du Lot 3b (déjà traduits en messages opérateur)."""


class ExtractionIntrouvable(LiaisonVoileError):
    """Aucune extraction ne porte cet identifiant."""


class ModeleBateauIntrouvable(LiaisonVoileError):
    """Le modèle de bateau demandé n'existe pas dans le référentiel."""


class CorrectionRefusee(LiaisonVoileError):
    """Champ ou valeur de correction invalide."""


class LiaisonRefusee(LiaisonVoileError):
    """Demande de liaison incohérente (cible absente ou double)."""


class DemandeInvalide(LiaisonVoileError):
    """Paramètre de lecture hors vocabulaire fermé."""


# ---------------------------------------------------------------------------
# Validation des valeurs corrigées — pure
# ---------------------------------------------------------------------------


def valider_valeur_correction(champ: str, valeur: Any) -> float | str | None:  # noqa: ANN401
    """Valide et canonicalise une valeur corrigée par un approbateur.

    Les dimensions sont saisies dans l'unité canonique de l'extraction
    (m, m², kg) ; aucune conversion devinée n'est appliquée à une saisie
    humaine — un texte qui n'est pas un nombre simple est refusé.
    """
    if champ not in CHAMPS_CORRIGEABLES:
        raise CorrectionRefusee(
            f"Champ « {champ} » non corrigeable (corrigables : {', '.join(CHAMPS_CORRIGEABLES)})."
        )
    if champ in _CHAMPS_NUMERIQUES:
        if valeur is None or isinstance(valeur, bool):
            raise CorrectionRefusee(f"« {champ} » exige un nombre strictement positif.")
        if isinstance(valeur, str):
            texte = valeur.strip()
            if not _RE_NOMBRE.match(texte):
                raise CorrectionRefusee(
                    f"« {champ} » : « {valeur} » n'est pas un nombre simple "
                    "(unité canonique attendue, sans texte autour)."
                )
            nombre = float(texte.replace(",", "."))
        elif isinstance(valeur, (int, float)):
            nombre = float(valeur)
        else:
            raise CorrectionRefusee(f"« {champ} » exige un nombre strictement positif.")
        if not nombre > 0:
            raise CorrectionRefusee(f"« {champ} » doit être strictement positif (reçu : {nombre}).")
        return nombre

    if valeur is None:
        if champ in _CHAMPS_TEXTES_EFFACABLES:
            return None
        raise CorrectionRefusee(f"« {champ} » ne peut pas être vidé par une correction.")
    texte = " ".join(str(valeur).split())
    if not texte:
        if champ in _CHAMPS_TEXTES_EFFACABLES:
            return None
        raise CorrectionRefusee(f"« {champ} » ne peut pas être vidé par une correction.")
    if len(texte) > _LONGUEUR_TEXTE_MAX:
        raise CorrectionRefusee(f"« {champ} » dépasse {_LONGUEUR_TEXTE_MAX} caractères.")
    return texte


def _formater_nombre(valeur: float) -> str:
    """Forme texte stable d'un nombre canonique (« 3.08 », « 9 »)."""
    return ("%f" % float(valeur)).rstrip("0").rstrip(".") or "0"


def _cle_motif(valeur: Any) -> str:  # noqa: ANN401
    """Clé de comparaison d'une valeur pour les motifs appris."""
    if isinstance(valeur, bool) or valeur is None:
        return ""
    if isinstance(valeur, (int, float)):
        return _formater_nombre(float(valeur))
    return " ".join(str(valeur).split()).lower()


def _exiger_postgres(index: Any, action: str) -> None:  # noqa: ANN401
    if not getattr(index, "is_postgres", False):
        raise LiaisonVoileError(
            f"{action} exige PostgreSQL (couche métier, migrations 026/028/029)."
        )


def _iso(valeur: Any) -> str | None:  # noqa: ANN401
    if valeur is None:
        return None
    return valeur.isoformat() if hasattr(valeur, "isoformat") else str(valeur)


# ---------------------------------------------------------------------------
# Décision de résolution — déterministe, jamais devinée
# ---------------------------------------------------------------------------


def _candidat_depuis_modele(modele: ModeleBateau, type_correspondance: str) -> dict[str, Any]:
    return {
        "id_modele": modele.id_modele,
        "nom_canonique": modele.nom_canonique,
        "cle_normalisee": modele.cle_normalisee,
        "type_correspondance": type_correspondance,
        "rang": 0,
        "score": 1.0,
        "alias": None,
    }


def _modele_fiche(curseur: Any, id_fiche: int | None, brut: str) -> ModeleBateau | None:  # noqa: ANN401
    """Modèle final du bateau de la fiche si son libellé correspond exactement."""
    if not id_fiche:
        return None
    curseur.execute(
        "SELECT b.nom, b.taille, b.boat_model_id "
        "FROM fiche f JOIN bateau b ON b.id_bateau = f.id_bateau WHERE f.id_fiche = %s",
        (int(id_fiche),),
    )
    ligne = curseur.fetchone()
    if ligne is None or ligne[2] is None:
        return None
    cles = {normaliser_modele_bateau(str(ligne[0]))}
    if ligne[1]:
        cles.add(normaliser_modele_bateau(f"{ligne[0]} {ligne[1]}"))
    if normaliser_modele_bateau(brut) not in cles:
        return None
    try:
        return resoudre_modele_final(curseur, int(ligne[2]))
    except ValueError:
        LOGGER.warning(
            "Bateau de la fiche %s lié à un modèle introuvable (%s) — voie fiche ignorée.",
            id_fiche, ligne[2],
        )
        return None


def decider_resolution(
    curseur: Any,  # noqa: ANN401
    *,
    modele_bateau_brut: str | None,
    id_fiche: int | None,
) -> dict[str, Any]:
    """Tranche le statut de liaison d'un libellé brut — sans jamais deviner.

    Ordre des preuves : clé/alias exact du référentiel, puis bateau de la
    fiche déjà lié. Un désaccord entre les deux, ou de simples candidats
    préfixe/flous, donnent ``ambigu`` avec les candidats à présenter ; aucun
    candidat donne ``non_resolu`` ; aucun libellé donne ``sans_modele``.
    """
    brut = (modele_bateau_brut or "").strip()
    if not brut:
        return {
            "statut_liaison": "sans_modele",
            "boat_model_id": None,
            "methode_liaison": None,
            "candidats": [],
        }
    exact = resoudre_modele_depuis_texte(curseur, brut)
    fiche = _modele_fiche(curseur, id_fiche, brut)
    if exact is not None and fiche is not None and exact.id_modele != fiche.id_modele:
        candidats = [
            _candidat_depuis_modele(exact, "exact"),
            _candidat_depuis_modele(fiche, "fiche_bateau"),
        ]
        return {
            "statut_liaison": "ambigu",
            "boat_model_id": None,
            "methode_liaison": None,
            "candidats": candidats,
        }
    if exact is not None:
        methode = (
            "cle_exacte"
            if normaliser_modele_bateau(brut) == exact.cle_normalisee
            else "alias_exact"
        )
        return {
            "statut_liaison": "lie_automatique",
            "boat_model_id": exact.id_modele,
            "methode_liaison": methode,
            "candidats": [],
        }
    if fiche is not None:
        return {
            "statut_liaison": "lie_automatique",
            "boat_model_id": fiche.id_modele,
            "methode_liaison": "fiche_bateau",
            "candidats": [],
        }
    candidats = rechercher_modeles(curseur, brut, limite=5)
    return {
        "statut_liaison": "ambigu" if candidats else "non_resolu",
        "boat_model_id": None,
        "methode_liaison": None,
        "candidats": candidats,
    }


_SQL_LIAISON_EXTRACTION = (
    "SELECT id_extraction, modele_bateau_brut, id_fiche, statut_liaison, boat_model_id, methode_liaison "
    "FROM sail_extractions WHERE id_extraction = %s"
)


def resoudre_extraction_curseur(curseur: Any, id_extraction: int) -> dict[str, Any]:  # noqa: ANN401
    """Applique la décision automatique à une extraction (transaction appelante).

    Une liaison décidée par un approbateur (`lie_humain`) n'est jamais
    revisitée ici : seule une nouvelle décision humaine peut la changer.
    """
    curseur.execute(_SQL_LIAISON_EXTRACTION + " FOR UPDATE", (int(id_extraction),))
    ligne = curseur.fetchone()
    if ligne is None:
        raise ExtractionIntrouvable(f"Extraction {id_extraction} inconnue.")
    _id, brut, id_fiche, statut_actuel, id_modele_actuel, methode_actuelle = ligne
    if statut_actuel == "lie_humain":
        return {
            "id_extraction": int(id_extraction),
            "statut_liaison": "lie_humain",
            "boat_model_id": int(id_modele_actuel) if id_modele_actuel is not None else None,
            "methode_liaison": methode_actuelle,
            "candidats": [],
            "inchange": True,
        }
    decision = decider_resolution(
        curseur,
        modele_bateau_brut=str(brut) if brut is not None else None,
        id_fiche=int(id_fiche) if id_fiche is not None else None,
    )
    lie = decision["boat_model_id"] is not None
    curseur.execute(
        "UPDATE sail_extractions SET boat_model_id = %s, statut_liaison = %s, "
        "methode_liaison = %s, lie_le = CASE WHEN %s THEN now() ELSE NULL END, "
        "lie_par = NULL, maj_le = now() WHERE id_extraction = %s",
        (
            decision["boat_model_id"],
            decision["statut_liaison"],
            decision["methode_liaison"],
            lie,
            int(id_extraction),
        ),
    )
    return {"id_extraction": int(id_extraction), **decision, "inchange": False}


def resoudre_extractions(index: Any, *, limite: int = 200) -> dict[str, Any]:  # noqa: ANN401
    """Résout en lot les extractions en attente (non résolues ou ambiguës)."""
    _exiger_postgres(index, "La résolution en lot des extractions")
    limite_effective = max(1, min(int(limite), 500))
    bilan = {
        "traitees": 0,
        "liees": 0,
        "ambigues": 0,
        "sans_modele": 0,
        "non_resolues": 0,
        "limite": limite_effective,
    }
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT id_extraction FROM sail_extractions "
                "WHERE statut_liaison IN ('non_resolu', 'ambigu') "
                "ORDER BY id_extraction LIMIT %s",
                (limite_effective,),
            )
            ids = [int(ligne[0]) for ligne in curseur.fetchall()]
            for id_extraction in ids:
                decision = resoudre_extraction_curseur(curseur, id_extraction)
                bilan["traitees"] += 1
                if decision["statut_liaison"] in ("lie_automatique", "lie_humain"):
                    bilan["liees"] += 1
                elif decision["statut_liaison"] == "ambigu":
                    bilan["ambigues"] += 1
                elif decision["statut_liaison"] == "sans_modele":
                    bilan["sans_modele"] += 1
                else:
                    bilan["non_resolues"] += 1
    LOGGER.info(
        "Résolution Lot 3b en lot : %s — conséquence : seules les preuves exactes lient, "
        "le reste attend un approbateur.",
        bilan,
    )
    return bilan


# ---------------------------------------------------------------------------
# Décision humaine de liaison + apprentissage d'alias
# ---------------------------------------------------------------------------


def lier_extraction(
    index: Any,  # noqa: ANN401
    id_extraction: int,
    *,
    id_utilisateur: int,
    id_modele: int | None = None,
    nouveau_modele: str | None = None,
    apprendre_alias: bool = True,
) -> dict[str, Any]:
    """Décision d'approbateur : rattacher l'extraction à un modèle (ou le créer).

    Si le libellé brut n'est pas déjà la clé du modèle choisi, il devient un
    alias appris (`apprentissage_lot3b`) : les prochaines extractions portant
    le même libellé se lieront toutes seules. Un alias déjà possédé par un
    autre modèle lève `AliasBateauEnConflit` — jamais de vol d'étiquette.
    """
    _exiger_postgres(index, "La liaison d'une extraction")
    nom_nouveau = " ".join(str(nouveau_modele).split()) if nouveau_modele else ""
    if bool(id_modele) == bool(nom_nouveau):
        raise LiaisonRefusee(
            "Préciser soit « id_modele » (modèle existant), soit « nouveau_modele » "
            "(création) — jamais les deux, jamais aucun."
        )
    if nom_nouveau and len(nom_nouveau) > _LONGUEUR_TEXTE_MAX:
        raise LiaisonRefusee(f"Le nom du nouveau modèle dépasse {_LONGUEUR_TEXTE_MAX} caractères.")

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(_SQL_LIAISON_EXTRACTION + " FOR UPDATE", (int(id_extraction),))
            ligne = curseur.fetchone()
            if ligne is None:
                raise ExtractionIntrouvable(f"Extraction {id_extraction} inconnue.")
            brut = str(ligne[1]) if ligne[1] is not None else None

            if nom_nouveau:
                existant = resoudre_modele_depuis_texte(curseur, nom_nouveau)
                modele = assurer_modele(
                    curseur, nom_nouveau, source="creation_lot3b", cree_par=int(id_utilisateur)
                )
                if modele is None:
                    raise LiaisonRefusee(f"Nom de modèle inexploitable : « {nom_nouveau} ».")
                methode = "humain" if existant is not None else "humain_nouveau_modele"
            else:
                assert id_modele is not None  # garanti par le contrôle d'exclusivité
                try:
                    modele = resoudre_modele_final(curseur, int(id_modele))
                except ValueError as erreur:
                    raise ModeleBateauIntrouvable(
                        f"Modèle de bateau {id_modele} inconnu du référentiel."
                    ) from erreur
                methode = "humain"

            alias_appris: dict[str, Any] | None = None
            if apprendre_alias and brut and normaliser_modele_bateau(brut) != modele.cle_normalisee:
                # Peut lever AliasBateauEnConflit : la transaction est annulée
                # en entier — ni liaison ni alias à moitié écrits.
                alias_appris = assurer_alias(
                    curseur,
                    modele.id_modele,
                    brut,
                    source="apprentissage_lot3b",
                    cree_par=int(id_utilisateur),
                )

            curseur.execute(
                "UPDATE sail_extractions SET boat_model_id = %s, statut_liaison = 'lie_humain', "
                "methode_liaison = %s, lie_le = now(), lie_par = %s, maj_le = now() "
                "WHERE id_extraction = %s",
                (modele.id_modele, methode, int(id_utilisateur), int(id_extraction)),
            )
    LOGGER.info(
        "Extraction %s liée par l'utilisateur %s au modèle « %s » (%s)%s.",
        id_extraction,
        id_utilisateur,
        modele.nom_canonique,
        methode,
        " — alias appris pour les suivantes" if alias_appris else "",
    )
    return {
        "id_extraction": int(id_extraction),
        "statut_liaison": "lie_humain",
        "methode_liaison": methode,
        "modele": {
            "id_modele": modele.id_modele,
            "nom_canonique": modele.nom_canonique,
            "cle_normalisee": modele.cle_normalisee,
        },
        "alias_appris": alias_appris,
    }


# ---------------------------------------------------------------------------
# Corrections d'approbateur
# ---------------------------------------------------------------------------


def _recalculer_plausibilite(extraction: ExtractionVoile) -> tuple[str, list[AnomalieExtractionVoile]]:
    """Rejoue RG16 sur les cotes canoniques courantes (après correction)."""
    dimensions = extraction.dimensions
    conservees = [a for a in extraction.anomalies if a.code not in _CODES_RG16]
    if dimensions.get("slu_m") is None and dimensions.get("sf_m") is None:
        return "indeterminee", conservees
    fiche_controle = FicheExtraite(
        code=extraction.code_fiche or "",
        type_voile_libelle=extraction.type_voile,
        cotes=[
            Cotes(
                jeu="finie",
                slu_m=dimensions.get("slu_m"),
                sle_m=dimensions.get("sle_m"),
                sf_m=dimensions.get("sf_m"),
                shw_m=dimensions.get("shw_m"),
                spa_m2=dimensions.get("spa_m2"),
                tetiere_cm=(
                    round(dimensions["tetiere_m"] * 100.0, 3)
                    if dimensions.get("tetiere_m") is not None
                    else None
                ),
                poids_kg=dimensions.get("poids_kg"),
            )
        ],
    )
    rg16 = [
        AnomalieExtractionVoile(code=a.code, gravite=a.gravite, message=a.message)
        for a in evaluer_anomalies(fiche_controle)
    ]
    toutes = conservees + rg16
    if any(a.code in _CODES_IMPLAUSIBLES or a.gravite == "forte" for a in toutes):
        return "implausible", toutes
    if any(a.code != "unite_corrigee" for a in toutes):
        return "a_verifier", toutes
    return "plausible", toutes


def _champ_trace_fiche(extraction: ExtractionVoile, champ: str) -> str | None:
    """Nom du champ de trace fiche portant cette cote, s'il existe."""
    colonne = _COLONNES_FICHE.get(champ)
    if colonne is None:
        return None
    for trace in extraction.champs:
        if trace.colonne_cible == colonne and trace.champ.startswith("cotes."):
            return trace.champ
    return None


def corriger_extraction(
    index: Any,  # noqa: ANN401
    id_extraction: int,
    *,
    id_utilisateur: int,
    champ: str,
    valeur: Any,  # noqa: ANN401
    motif: str | None = None,
    revision_fiche: int | None = None,
) -> dict[str, Any]:
    """Corrige UN champ canonique d'une extraction, décision d'approbateur.

    Même transaction pour : propagation éventuelle à la fiche (cotes avec
    trace, révision obligatoire — refus 428/409 du noyau existant), mise à
    jour de l'extraction, plausibilité RG16 recalculée, historique
    `extraction_corrections`, motif appris, et re-résolution si le libellé de
    bateau lui-même a été corrigé. La provenance machine n'est pas réécrite.
    """
    _exiger_postgres(index, "La correction d'une extraction")
    valeur_canonique = valider_valeur_correction(champ, valeur)
    motif_texte = " ".join(str(motif).split()) if motif else None
    if motif_texte is not None and len(motif_texte) > _LONGUEUR_MOTIF_MAX:
        raise CorrectionRefusee(f"Le motif dépasse {_LONGUEUR_MOTIF_MAX} caractères.")
    colonne_sql = {
        "type_voile": "type_voile",
        "gamme": "gamme",
        "modele_bateau_brut": "modele_bateau_brut",
        "taille_bateau_brute": "taille_bateau_brute",
        **{nom: nom for nom in _CHAMPS_NUMERIQUES},
    }[champ]

    propagation: dict[str, Any] = {"statut": "aucune"}
    liaison: dict[str, Any] | None = None
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                f"SELECT {_COLONNES_SQL} FROM sail_extractions WHERE id_extraction = %s FOR UPDATE",
                (int(id_extraction),),
            )
            ligne = curseur.fetchone()
            if ligne is None:
                raise ExtractionIntrouvable(f"Extraction {id_extraction} inconnue.")
            extraction = _extraction_depuis_ligne(tuple(ligne))
            avant: Any = getattr(extraction, champ)
            if isinstance(valeur_canonique, float) and isinstance(avant, (int, float)):
                identique = float(avant) == valeur_canonique
            else:
                identique = avant == valeur_canonique
            if identique:
                raise CorrectionRefusee(
                    f"« {champ} » vaut déjà « {valeur_canonique} » — aucune correction à enregistrer."
                )

            # Propagation à la fiche AVANT toute écriture d'extraction : un
            # refus du noyau (428 sans révision, 409 révision périmée, fiche
            # validée RG11…) annule la transaction entière.
            if champ in _CHAMPS_NUMERIQUES and extraction.id_fiche and extraction.code_fiche:
                champ_trace = _champ_trace_fiche(extraction, champ)
                if champ_trace is not None:
                    from seamtech_search.fiches.routes import corriger_champ_sous_curseur

                    assert isinstance(valeur_canonique, float)
                    valeur_fiche = valeur_canonique * 100.0 if champ == "tetiere_m" else valeur_canonique
                    detail = corriger_champ_sous_curseur(
                        curseur,
                        index,
                        extraction.code_fiche,
                        champ_trace,
                        _formater_nombre(valeur_fiche),
                        id_utilisateur_explicite=int(id_utilisateur),
                        commentaire=motif_texte
                        or f"Correction Lot 3b depuis l'écran d'extraction (extraction {id_extraction}).",
                        revision=revision_fiche,
                        exiger_revision=True,
                    )
                    propagation_detail = detail.get("propagation") or {}
                    if propagation_detail.get("statut") == "appliquee":
                        propagation = {
                            "statut": "fiche",
                            "champ_fiche": champ_trace,
                            "revision": detail.get("revision"),
                        }
                    else:
                        propagation = {
                            "statut": "aucune",
                            "raison": propagation_detail.get("raison")
                            or f"propagation fiche {propagation_detail.get('statut', 'inconnue')}",
                        }

            curseur.execute(
                f"UPDATE sail_extractions SET {colonne_sql} = %s, maj_le = now() "
                "WHERE id_extraction = %s",
                (valeur_canonique, int(id_extraction)),
            )

            statut_plausibilite = extraction.statut_plausibilite
            if champ in _CHAMPS_NUMERIQUES:
                corrigee = extraction.model_copy(update={champ: valeur_canonique})
                statut_plausibilite, anomalies = _recalculer_plausibilite(corrigee)
                curseur.execute(
                    "UPDATE sail_extractions SET statut_plausibilite = %s, anomalies = %s::jsonb, "
                    "maj_le = now() WHERE id_extraction = %s",
                    (
                        statut_plausibilite,
                        json.dumps(
                            [a.model_dump(mode="json") for a in anomalies], ensure_ascii=False
                        ),
                        int(id_extraction),
                    ),
                )

            if champ == "modele_bateau_brut":
                # Corriger le libellé rouvre la résolution : l'ancienne
                # liaison (même humaine, elle visait l'ancien libellé) est
                # explicitement remise à zéro puis redécidée.
                curseur.execute(
                    "UPDATE sail_extractions SET boat_model_id = NULL, "
                    "statut_liaison = 'non_resolu', methode_liaison = NULL, lie_le = NULL, "
                    "lie_par = NULL, maj_le = now() WHERE id_extraction = %s",
                    (int(id_extraction),),
                )
                liaison = resoudre_extraction_curseur(curseur, int(id_extraction))

            curseur.execute(
                "INSERT INTO extraction_corrections "
                "(id_extraction, champ, valeur_avant, valeur_apres, id_utilisateur, motif, propagation) "
                "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)",
                (
                    int(id_extraction),
                    champ,
                    json.dumps(avant, ensure_ascii=False),
                    json.dumps(valeur_canonique, ensure_ascii=False),
                    int(id_utilisateur),
                    motif_texte,
                    propagation["statut"],
                ),
            )

            # Motif appris : « ce champ lu X a été corrigé en Y » — suggestion
            # future pour l'approbateur, jamais une application automatique.
            cle_avant = _cle_motif(avant)
            cle_apres = (
                _formater_nombre(valeur_canonique)
                if isinstance(valeur_canonique, float)
                else _cle_motif(valeur_canonique)
            )
            if cle_avant and cle_apres and cle_avant != cle_apres and valeur_canonique is not None:
                curseur.execute(
                    "INSERT INTO extraction_motifs_appris "
                    "(gabarit_code, champ, valeur_brute_cle, valeur_corrigee, dernier_utilisateur) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (gabarit_code, champ, valeur_brute_cle, valeur_corrigee) "
                    "DO UPDATE SET occurrences = extraction_motifs_appris.occurrences + 1, "
                    "dernier_utilisateur = EXCLUDED.dernier_utilisateur, maj_le = now()",
                    (
                        extraction.gabarit_code or "",
                        champ,
                        cle_avant,
                        cle_apres,
                        int(id_utilisateur),
                    ),
                )
    LOGGER.info(
        "Extraction %s corrigée par l'utilisateur %s : %s %r → %r (propagation %s).",
        id_extraction, id_utilisateur, champ, avant, valeur_canonique, propagation["statut"],
    )
    return {
        "id_extraction": int(id_extraction),
        "champ": champ,
        "avant": avant,
        "apres": valeur_canonique,
        "propagation": propagation,
        "statut_plausibilite": statut_plausibilite,
        "liaison": liaison,
    }


# ---------------------------------------------------------------------------
# Lecture : file de traitement et détail côte à côte
# ---------------------------------------------------------------------------


def _modele_lie(curseur: Any, extraction: ExtractionVoile) -> dict[str, Any] | None:  # noqa: ANN401
    if extraction.boat_model_id is None:
        return None
    try:
        modele = resoudre_modele_final(curseur, extraction.boat_model_id)
        return {
            "id_modele": modele.id_modele,
            "nom_canonique": modele.nom_canonique,
            "cle_normalisee": modele.cle_normalisee,
        }
    except ValueError:
        curseur.execute(
            "SELECT nom_canonique, cle_normalisee FROM boat_models WHERE id_modele = %s",
            (extraction.boat_model_id,),
        )
        ligne = curseur.fetchone()
        if ligne is None:
            return None
        return {
            "id_modele": extraction.boat_model_id,
            "nom_canonique": str(ligne[0]),
            "cle_normalisee": str(ligne[1]),
        }


def lister_extractions(
    index: Any,  # noqa: ANN401
    *,
    statut_liaison: str | None = None,
    statut_extraction: str | None = None,
    recherche: str | None = None,
    limite: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """File de traitement des extractions pour l'écran approbateur."""
    _exiger_postgres(index, "La consultation des extractions")
    if statut_liaison is not None and statut_liaison not in STATUTS_LIAISON:
        raise DemandeInvalide(f"statut_liaison inconnu : « {statut_liaison} ».")
    if statut_extraction is not None and statut_extraction not in STATUTS_EXTRACTION:
        raise DemandeInvalide(f"statut_extraction inconnu : « {statut_extraction} ».")
    limite_effective = max(1, min(int(limite), 200))
    offset_effectif = max(0, int(offset))

    conditions: list[str] = []
    parametres: list[Any] = []
    if statut_liaison is not None:
        conditions.append("statut_liaison = %s")
        parametres.append(statut_liaison)
    if statut_extraction is not None:
        conditions.append("statut_extraction = %s")
        parametres.append(statut_extraction)
    if recherche and recherche.strip():
        motif = (
            "%"
            + recherche.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%"
        )
        conditions.append(
            "(code_fiche ILIKE %s ESCAPE '\\' OR modele_bateau_brut ILIKE %s ESCAPE '\\' "
            "OR type_voile ILIKE %s ESCAPE '\\')"
        )
        parametres.extend([motif, motif, motif])
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    colonnes = _COLONNES_EXTRACTION_VOILE
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                f"SELECT {_COLONNES_SQL}, COUNT(*) OVER() FROM sail_extractions{where} "
                "ORDER BY cree_le DESC, id_extraction DESC LIMIT %s OFFSET %s",
                (*parametres, limite_effective, offset_effectif),
            )
            lignes = curseur.fetchall()
            total = int(lignes[0][-1]) if lignes else 0
            if not lignes and offset_effectif:
                curseur.execute(f"SELECT COUNT(*) FROM sail_extractions{where}", (*parametres,))
                total = int(curseur.fetchone()[0])
            extractions: list[dict[str, Any]] = []
            for ligne in lignes:
                extraction = _extraction_depuis_ligne(tuple(ligne[: len(colonnes)]))
                extractions.append(
                    {
                        **extraction.model_dump(mode="json"),
                        "modele_lie": _modele_lie(curseur, extraction),
                    }
                )
    return {"extractions": extractions, "total": total}


def detail_extraction(index: Any, id_extraction: int) -> dict[str, Any]:  # noqa: ANN401
    """Détail complet pour l'écran côte à côte (PDF + valeurs + liaison)."""
    _exiger_postgres(index, "La consultation d'une extraction")
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                f"SELECT {_COLONNES_SQL} FROM sail_extractions WHERE id_extraction = %s",
                (int(id_extraction),),
            )
            ligne = curseur.fetchone()
            if ligne is None:
                raise ExtractionIntrouvable(f"Extraction {id_extraction} inconnue.")
            extraction = _extraction_depuis_ligne(tuple(ligne))

            if extraction.statut_liaison in ("lie_automatique", "lie_humain"):
                candidats: list[dict[str, Any]] = []
            else:
                candidats = decider_resolution(
                    curseur,
                    modele_bateau_brut=extraction.modele_bateau_brut,
                    id_fiche=extraction.id_fiche,
                )["candidats"]

            curseur.execute(
                "SELECT c.id_correction, c.champ, c.valeur_avant, c.valeur_apres, "
                "c.id_utilisateur, u.identifiant, c.motif, c.propagation, c.cree_le "
                "FROM extraction_corrections c "
                "LEFT JOIN utilisateur u ON u.id_utilisateur = c.id_utilisateur "
                "WHERE c.id_extraction = %s ORDER BY c.cree_le, c.id_correction",
                (int(id_extraction),),
            )
            corrections = [
                {
                    "id_correction": int(row[0]),
                    "champ": str(row[1]),
                    "valeur_avant": row[2],
                    "valeur_apres": row[3],
                    "id_utilisateur": int(row[4]) if row[4] is not None else None,
                    "corrige_par": str(row[5]) if row[5] is not None else None,
                    "motif": str(row[6]) if row[6] is not None else None,
                    "propagation": str(row[7]),
                    "cree_le": _iso(row[8]),
                }
                for row in curseur.fetchall()
            ]

            suggestions: list[dict[str, Any]] = []
            for champ in CHAMPS_CORRIGEABLES:
                actuelle: Any = getattr(extraction, champ)
                cle = _cle_motif(actuelle)
                if not cle:
                    continue
                curseur.execute(
                    "SELECT valeur_corrigee, occurrences FROM extraction_motifs_appris "
                    "WHERE champ = %s AND valeur_brute_cle = %s "
                    "AND gabarit_code IN (%s, '') AND valeur_corrigee <> %s "
                    "ORDER BY occurrences DESC, valeur_corrigee LIMIT 3",
                    (champ, cle, extraction.gabarit_code or "", cle),
                )
                for valeur_corrigee, occurrences in curseur.fetchall():
                    suggestions.append(
                        {
                            "champ": champ,
                            "valeur_actuelle": actuelle,
                            "valeur_corrigee": str(valeur_corrigee),
                            "occurrences": int(occurrences),
                        }
                    )

            fiche: dict[str, Any] | None = None
            if extraction.id_fiche:
                curseur.execute(
                    "SELECT code, statut, revision FROM fiche WHERE id_fiche = %s",
                    (extraction.id_fiche,),
                )
                ligne_fiche = curseur.fetchone()
                if ligne_fiche is not None:
                    fiche = {
                        "id_fiche": extraction.id_fiche,
                        "code": str(ligne_fiche[0]),
                        "statut": str(ligne_fiche[1]),
                        "revision": int(ligne_fiche[2]) if ligne_fiche[2] is not None else 1,
                    }

            return {
                "extraction": extraction.model_dump(mode="json"),
                "modele_lie": _modele_lie(curseur, extraction),
                "candidats": candidats,
                "corrections": corrections,
                "suggestions_motifs": suggestions,
                "fiche": fiche,
            }
