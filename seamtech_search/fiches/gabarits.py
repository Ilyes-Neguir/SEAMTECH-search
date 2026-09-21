"""Gabarits d'extraction — registre, détection de variante, règles (Lot B).

Chaque variante de fiche = un gabarit enregistré en base (table ``gabarit``
du Lot A) avec :
- des ancres de détection (normalisées) qui identifient la variante ;
- des règles (JSONB) : pour chaque champ cible, son ancre, son type et son
  « traitement » (fonction de décomposition connue du moteur) ;
- une version — modifier un gabarit ne ré-analyse pas les fiches validées
  (RG11) mais permet de ré-extraire les fiches ``a_valider``.

Détection : le gabarit dont le score d'ancres présentes dans le texte est le
plus élevé (et non nul) gagne ; aucun score nul → :class:`GabaritInconnu`,
et l'appelant passe en mode dégradé (valeurs libres, RG6).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, model_validator

from seamtech_search.lexique import normaliser_terme

LOGGER = logging.getLogger("seamtech_search.fiches.gabarits")

CODE_GABARIT_PORTANT = "FICHE_PORTANT_V1"
CODE_GABARIT_GENOIS = "FICHE_GENOIS_V1"
VERSION_COURANTE = 1


class GabaritInconnu(RuntimeError):
    """Aucun gabarit du registre ne reconnaît le PDF (voie « reprise complète » §10.3)."""


class RegleChamp(BaseModel):
    """Une règle de lecture : ancre(s) → cible, avec type et traitement."""

    cible: str  # « fiche.code », « cotes.finie.slu_m », « traitement:galons »…
    ancres: list[str] = Field(min_length=1)
    type: str = "texte"  # texte | entier | decimal_m | decimal_mm | grammage | date_fr | booleen | decimal
    traitement: str | None = None  # décomposition structurée (voir extraction.TRAITEMENTS)
    stop: list[str] = Field(default_factory=list)  # libellés suivants bornant la valeur
    obligatoire: bool = False

    @model_validator(mode="after")
    def _traitement_deduit_de_cible(self) -> "RegleChamp":
        """Cible « traitement:nom » ⇒ traitement=nom — impossible de l'oublier."""
        if self.cible.startswith("traitement:") and self.traitement is None:
            self.traitement = self.cible.split(":", 1)[1]
        return self


class GabaritDef(BaseModel):
    """Définition complète d'un gabarit (telle que stockée en JSONB)."""

    code: str
    version: int = 1
    description: str = ""
    ancres_detection: list[str] = Field(min_length=1)
    champs: list[RegleChamp] = Field(default_factory=list)

    def score_detection(self, texte_normalise: str) -> int:
        """Nombre d'ancres de détection présentes dans le texte."""
        return sum(1 for ancre in self.ancres_detection if normaliser_terme(ancre) in texte_normalise)


def charger_gabarits(index: Any) -> list[GabaritDef]:
    """Charge les gabarits actifs depuis la table ``gabarit`` (Lot A)."""
    gabarits: list[GabaritDef] = []
    with index.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT code, version, description, ancres_detection, regles FROM gabarit "
                "WHERE actif ORDER BY code, version DESC"
            )
            for code, version, description, ancres, regles in cursor.fetchall():
                donnees = regles if isinstance(regles, dict) else json.loads(regles)
                donnees.setdefault("code", code)
                donnees.setdefault("version", version)
                donnees.setdefault("description", description or "")
                donnees["ancres_detection"] = list(ancres or [])
                gabarits.append(GabaritDef.model_validate(donnees))
    return gabarits


def detecter_gabarit(texte_normalise: str, gabarits: list[GabaritDef]) -> GabaritDef:
    """Choisit le gabarit au meilleur score d'ancres ; lève GabaritInconnu sinon.

    Le score et le vainqueur sont journalisés (debug) : la détection doit
    rester expliquable, comme le reste du projet.
    """
    scores = [(gabarit.score_detection(texte_normalise), gabarit) for gabarit in gabarits]
    scores.sort(key=lambda paire: -paire[0])
    if not scores or scores[0][0] == 0:
        detail = ", ".join(gabarit.code for gabarit in gabarits) or "aucun gabarit enregistré"
        raise GabaritInconnu(
            f"Aucun gabarit ne reconnaît ce PDF (ancres disponibles : {detail}). "
            "La fiche part en reprise complète (§10.3) ; ses libellés connus seront "
            "conservés en mesures libres (RG6)."
        )
    meilleur_score, meilleur = scores[0]
    LOGGER.debug("Gabarit détecté : %s (score %d) ; scores : %s", meilleur.code, meilleur_score, [(g.code, s) for s, g in scores])
    return meilleur


def enregistrer_gabarit(index: Any, gabarit: GabaritDef) -> None:
    """Enregistre (ou met à jour) un gabarit actif — idempotent.

    ``ON CONFLICT (code, version)`` : ré-enregistrer la même version remplace
    les règles ; une NOUVELLE variante de règles = une NOUVELLE version.
    """
    with index.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO gabarit (code, version, description, regles, ancres_detection, actif)
                VALUES (%s, %s, %s, %s, %s, true)
                ON CONFLICT (code, version) DO UPDATE
                SET description = EXCLUDED.description,
                    regles = EXCLUDED.regles,
                    ancres_detection = EXCLUDED.ancres_detection,
                    actif = true
                """,
                (
                    gabarit.code,
                    gabarit.version,
                    gabarit.description,
                    gabarit.model_dump_json(exclude={"code", "version", "description"}),
                    gabarit.ancres_detection,
                ),
            )


# ---------------------------------------------------------------------------
# Gabarits embarqués (Lot B) — enregistrés en base par initialiser_gabarits().
# Les ancres/règles proviennent du vocabulaire RÉEL des fiches disponibles :
# la reconstruction 7792-SO (§13) et la fiche génois de démonstration.
# ---------------------------------------------------------------------------

GABARIT_PORTANT = GabaritDef(
    code=CODE_GABARIT_PORTANT,
    version=VERSION_COURANTE,
    description="Fiche de fabrication « Voile de portant » (réf. 7792-SO, plan v3.0 §13).",
    ancres_detection=["voile de portant", "spi asymétrique", "spinnaker"],
    champs=[
        RegleChamp(cible="fiche.titre", ancres=["fiche de fabrication"], type="texte", traitement="titre"),
        RegleChamp(cible="fiche.code", ancres=["code fiche", "référence"], type="texte", stop=["atelier", "client", "support", "quantite", "navire"], obligatoire=True),
        RegleChamp(cible="fiche.atelier", ancres=["atelier"], type="texte", stop=["désignation", "support", "client"]),
        RegleChamp(cible="fiche.designation", ancres=["désignation"], type="texte", traitement="designation"),
        RegleChamp(cible="fiche.bateau", ancres=["support / bateau", "support", "navire"], type="texte", traitement="support"),
        RegleChamp(cible="fiche.client", ancres=["client"], type="texte", traitement="client", stop=["n° de commande", "quantite", "support"]),
        RegleChamp(cible="fiche.commande", ancres=["n° de commande", "no de commande"], type="texte", traitement="commande", stop=["quantite"]),
        RegleChamp(cible="fiche.quantite", ancres=["quantité"], type="entier"),
        RegleChamp(cible="fiche.tissu_texte", ancres=["tissu(s)", "tissu"], type="texte", stop=["dessinateur", "cotes"]),
        RegleChamp(cible="fiche.dessinateur", ancres=["dessinateur"], type="texte", traitement="dessinateur"),
        RegleChamp(cible="fiche.montage", ancres=["montage"], type="texte", traitement="montage"),
        RegleChamp(cible="galon.guindant", ancres=["galon guindant"], type="texte", traitement="galons"),
        RegleChamp(cible="galon.chute", ancres=["galon chute"], type="texte", traitement="galons"),
        RegleChamp(cible="galon.bordure", ancres=["galon bordure"], type="texte", traitement="galons"),
        RegleChamp(cible="jonction.laizes", ancres=["laizes"], type="texte", traitement="jonctions", stop=["jonctions horizontales", "surplus", "galon"]),
        RegleChamp(cible="jonction.horizontale", ancres=["jonctions horizontales"], type="texte", traitement="jonctions", stop=["surplus", "galon"]),
        RegleChamp(cible="jonction.verticale", ancres=["jonction verticale"], type="texte", traitement="jonctions"),
        RegleChamp(cible="jonction.surplus", ancres=["surplus"], type="texte", traitement="jonctions", stop=["galon", "finitions"]),
        RegleChamp(cible="finition.postes", ancres=["finitions", "finition"], type="texte", traitement="finitions"),
        RegleChamp(cible="options", ancres=["options"], type="texte", traitement="options"),
        RegleChamp(cible="materiau.epaisseurs", ancres=["épaisseurs", "epaisseur"], type="texte", traitement="epaisseurs"),
        RegleChamp(cible="fiche.notes", ancres=["notes"], type="texte", traitement="notes", stop=["fichier source", "logo"]),
        RegleChamp(cible="fiche.fichier_source", ancres=["fichier source"], type="texte", traitement="fichier_source"),
        # Cotes : d'abord dans les tableaux réglés, sinon lignes « label : valeur ».
        RegleChamp(cible="cotes.finie.slu_m", ancres=["guindant (slu)", "guindant"], type="decimal_m"),
        RegleChamp(cible="cotes.finie.sle_m", ancres=["chute (sle)", "chute"], type="decimal_m"),
        RegleChamp(cible="cotes.finie.sf_m", ancres=["bordure (sf)", "bordure"], type="decimal_m"),
        RegleChamp(cible="cotes.finie.shw_m", ancres=["shw"], type="decimal_m"),
        RegleChamp(cible="cotes.finie.spa_m2", ancres=["surface (spa)", "surface"], type="decimal_m"),
        RegleChamp(cible="cotes.finie.tetiere_cm", ancres=["têtière", "tetiere"], type="decimal"),
        RegleChamp(cible="cotes.finie.poids_kg", ancres=["poids"], type="decimal_m"),
    ],
)

# ---------------------------------------------------------------------------
# Version 2 du gabarit portant — réglée sur la géométrie du DOCUMENT CLIENT
# RÉEL 7792-SO (166 990 o, 842×595 paysage, grille tracée de 89 formes),
# reçu le 21/09 (SHA-256 43afc51e…). Les règles v1 (lignes « label : valeur »
# de la reconstruction) sont conservées en tête : elles continuent de lire
# les fixtures synthétiques ; les règles v2 ne déclenchent que sur la
# géométrie réelle (ancre propre, et « premier lu gagne » dans le moteur).
# Le gabarit reste un registre versionné en base — pas de coordonnées dures.
# ---------------------------------------------------------------------------

_REGLES_TETE_V1 = (
    "fiche.titre", "fiche.code", "fiche.atelier", "fiche.designation",
    "fiche.bateau", "fiche.client", "fiche.commande", "fiche.quantite",
    "fiche.tissu_texte", "fiche.dessinateur", "fiche.montage",
    "fiche.fichier_source",
)

GABARIT_PORTANT_V2 = GabaritDef(
    code=CODE_GABARIT_PORTANT,
    version=2,
    description=(
        "Fiche « Voile de portant » v2 — géométrie du document client réel "
        "7792-SO (ligne de titre unique, grille de cotes à colonnes, blocs "
        "épaisseurs/galons/finitions) ; englobe et prolonge les règles v1."
    ),
    ancres_detection=list(GABARIT_PORTANT.ancres_detection),
    champs=(
        [regle for regle in GABARIT_PORTANT.champs if regle.cible in _REGLES_TETE_V1]
        + [
            # Ligne de titre réelle : « Spi Asymétrique … pour 29er (15') de
            # Sailonet (Cruette) 7792-SO » → désignation, bateau, client, code.
            RegleChamp(cible="traitement:ligne_titre_portant", ancres=["spi asymétrique"], type="texte"),
            # Tête du document réel.
            RegleChamp(cible="fiche.atelier", ancres=["découpe et fabrication"], type="texte", stop=["fichier"]),
            RegleChamp(cible="fiche.commande", ancres=["(commande", "commande"], type="texte", traitement="commande", stop=["dessiné", "quantité"]),
            RegleChamp(cible="fiche.dessinateur", ancres=["dessiné par"], type="texte", traitement="dessinateur", stop=["fichier"]),
            RegleChamp(cible="fiche.montage_fil", ancres=["montage en fil:", "fil:"], type="texte", traitement="montage_fil"),
            RegleChamp(cible="traitement:fichier_edite", ancres=["fichier"], type="texte"),
            # Grille de cotes tracée : en-tête 7 colonnes, lignes « Mesures
            # Dessin » / « Mesures Finies » — les deux jeux de fiche_cotes.
            RegleChamp(cible="traitement:grille_cotes", ancres=["guindant (slu)"], type="texte"),
            # Blocs en zones : épaisseurs (01→10), galons par bande, finitions.
            RegleChamp(cible="traitement:epaisseurs_grille", ancres=["epaisseur 01"], type="texte"),
            RegleChamp(cible="traitement:galons_grille", ancres=["galon"], type="texte"),
            RegleChamp(cible="traitement:finitions_grille", ancres=["finition"], type="texte"),
            RegleChamp(cible="traitement:jonctions_grille", ancres=["laizes"], type="texte"),
            RegleChamp(cible="traitement:options_grille", ancres=["emmagasineur", "chaussette"], type="texte"),
            RegleChamp(cible="traitement:options_lignes", ancres=["velcro", "retenue", "protection"], type="texte"),
            RegleChamp(cible="traitement:renforts_note", ancres=["renforts", "renfort"], type="texte"),
        ]
        + [regle for regle in GABARIT_PORTANT.champs if regle.cible not in _REGLES_TETE_V1]
    ),
)

GABARIT_GENOIS = GabaritDef(
    code=CODE_GABARIT_GENOIS,
    version=VERSION_COURANTE,
    description="Fiche « Génois » (variante mono-colonne : Guindant, Bordure, Tissu, Surface, Navire).",
    ancres_detection=["génois", "genoa"],
    champs=[
        RegleChamp(cible="fiche.code", ancres=["code fiche", "référence"], type="texte", stop=["navire", "client", "quantite"], obligatoire=True),
        RegleChamp(cible="fiche.bateau", ancres=["navire", "support", "bateau"], type="texte", traitement="support"),
        RegleChamp(cible="fiche.client", ancres=["client"], type="texte", traitement="client", stop=["guindant", "quantite"]),
        RegleChamp(cible="fiche.quantite", ancres=["quantité"], type="entier"),
        RegleChamp(cible="cotes.finie.slu_m", ancres=["guindant"], type="decimal_m"),
        RegleChamp(cible="cotes.finie.sf_m", ancres=["bordure"], type="decimal_m"),
        RegleChamp(cible="cotes.finie.spa_m2", ancres=["surface"], type="decimal_m"),
        RegleChamp(cible="fiche.designation", ancres=["génois", "genoa"], type="texte", traitement="designation"),
        RegleChamp(cible="materiau.tissu_principal", ancres=["tissu"], type="texte", stop=["surface", "guindant"], traitement="tissu_principal"),
    ],
)

# Ordre significatif : à score d'ancres égal, la version la plus récente
# d'un code gagne la détection (v2 devant v1).
GABARITS_EMBARQUES: tuple[GabaritDef, ...] = (GABARIT_PORTANT_V2, GABARIT_PORTANT, GABARIT_GENOIS)


def initialiser_gabarits(index: Any) -> None:
    """Enregistre les gabarits embarqués en base (idempotent, appelé par le
    CLI). Par code, seule la version la plus récente reste ACTIVE — les
    versions antérieures demeurent au registre, consultables (RG11 : une
    fiche validée sous une ancienne version n'est jamais ré-analysée)."""
    for gabarit in GABARITS_EMBARQUES:
        enregistrer_gabarit(index, gabarit)
        LOGGER.info("Gabarit enregistré : %s v%d", gabarit.code, gabarit.version)
    codes = {gabarit.code for gabarit in GABARITS_EMBARQUES}
    with index.connect() as connection:
        with connection.cursor() as cursor:
            for code in sorted(codes):
                cursor.execute(
                    "UPDATE gabarit SET actif = false WHERE code = %s AND version < ("
                    "   SELECT max(version) FROM gabarit WHERE code = %s)",
                    (code, code),
                )
