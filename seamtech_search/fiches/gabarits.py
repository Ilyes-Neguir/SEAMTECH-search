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

# ---------------------------------------------------------------------------
# Gabarits du corpus réel (2026-09-28) — deux familles de mise en page couvrent
# les 7 dossiers fournis. Les ancres sont des LIBELLÉS génériques de voilerie
# observés sur les fiches réelles ; AUCUNE valeur métier (nom de client, nom de
# bateau, référence de commande) n'apparaît ici.
# ---------------------------------------------------------------------------

CODE_GABARIT_JADE = "FICHE_JADE_V1"
CODE_GABARIT_GV_FULLBATTEN = "FICHE_GV_FULLBATTEN_V1"

GABARIT_JADE = GabaritDef(
    code=CODE_GABARIT_JADE,
    version=VERSION_COURANTE,
    description=(
        "Fiche de fabrication atelier, format tableau mono-page : en-tête "
        "CLIENT/BATEAU/TYPE DE VOILE en colonnes, ligne N° Commande, ligne DATE "
        "avec Surface, cotes GUINDANT/CHUTE/BORDURE en décimal français, BDF "
        "par bord, points d'ancrage (amure/écoute/drisse/ris), coutures zigzag."
    ),
    ancres_detection=[
        # « fiche de fabrication » est volontairement ABSENT : ce libellé
        # figure sur toutes les fiches (portant, génois, échantillons) et ne
        # discrimine pas le format atelier — une seule occurrence suffirait
        # à capturer un document hors gabarit (constaté sur le corpus de
        # démonstration CLIENT-123). Les 4 ancres ci-dessous sont propres à
        # la mise en page atelier.
        "type de voile",
        "point d'ancrage",
        "bande de visu",
        "expédition",
    ],
    champs=[
        # En-tête complet (titre, commande, client, bateau, type, date,
        # surface) : un seul traitement lit la page en colonnes.
        RegleChamp(cible="traitement:entete_jade", ancres=["client"], type="texte"),
        # Cotes des trois bords (le libellé et la valeur peuvent être collés :
        # « GUINDANT9,53 »).
        RegleChamp(cible="traitement:bords_jade", ancres=["chute"], type="texte"),
        # Matière : zone sous la ligne DATE, colonne centrale.
        RegleChamp(cible="traitement:tissu_jade", ancres=["date"], type="texte"),
        # Bandes de force (BDF a plat / pliée / décalée) par bord.
        RegleChamp(cible="traitement:galons_bdf", ancres=["bdf"], type="texte"),
        # Points d'ancrage (amure, écoute, drisse, ris…). L'ancre « amure »
        # porte une valeur sur sa ligne (la ligne « POINT D'ANCRAGE » seule
        # n'aurait aucune valeur à lire) ; le traitement relit toute la page.
        RegleChamp(cible="traitement:finitions_jade", ancres=["amure", "ecoute", "drisse"], type="texte"),
        # Coutures (zigzag, fil) → montage.
        RegleChamp(cible="fiche.montage_type", ancres=["zigzag", "zig"], type="texte", traitement="montage_jade"),
        # Remarques : ligne DIVERS (absente sinon — jamais inventée).
        RegleChamp(cible="fiche.notes", ancres=["divers"], type="texte"),
        # Renforts : ligne RENFORTS au-dessus du bloc planning atelier.
        RegleChamp(cible="traitement:renforts_jade", ancres=["renforts"], type="texte"),
    ],
)

GABARIT_GV_FULLBATTEN = GabaritDef(
    code=CODE_GABARIT_GV_FULLBATTEN,
    version=VERSION_COURANTE,
    description=(
        "Fiche de fabrication grand-voile fullbatten : même en-tête atelier que "
        "FICHE_JADE_V1, plus les sections propres à une GV lattée (Ris 1-3, "
        "LATTES, goussets L1-L7, boîtiers/coulisseaux, numéro de voile, bôme)."
    ),
    ancres_detection=[
        "fullbatten",
        "lattes",
        "ris 1",
        "ragage",
        "numéro voile",
        "coulisseaux",
        "goussets",
    ],
    champs=[
        RegleChamp(cible="traitement:entete_jade", ancres=["client"], type="texte"),
        RegleChamp(cible="traitement:bords_jade", ancres=["chute"], type="texte"),
        RegleChamp(cible="traitement:tissu_jade", ancres=["date"], type="texte"),
        RegleChamp(cible="traitement:galons_bdf", ancres=["bdf"], type="texte"),
        RegleChamp(cible="traitement:finitions_jade", ancres=["point d'ancrage"], type="texte"),
        RegleChamp(cible="fiche.montage_type", ancres=["zigzag", "zig"], type="texte", traitement="montage_jade"),
        RegleChamp(cible="fiche.notes", ancres=["divers"], type="texte"),
        RegleChamp(cible="traitement:renforts_jade", ancres=["renforts"], type="texte"),
        # Sections GV lattée : conservées en mesures libres (aucun champ
        # dédié dans le schéma — jamais forcées dans une table).
        RegleChamp(cible="traitement:libres_gv", ancres=["lattes"], type="texte"),
    ],
)

# Lot 10 — famille « FFab » des grand-voiles : ligne de titre complète
# (parenté Portant), ligne Fichier corroborante et grille « Cotes (tissu) »
# à neuf colonnes. Calibration STRUCTURES uniquement : seuls des libellés
# génériques de voilerie figurent ici, jamais une valeur d'un document.
CODE_GABARIT_GV_FFAB = "FICHE_GV_FFAB_V1"

GABARIT_GV_FFAB = GabaritDef(
    code=CODE_GABARIT_GV_FFAB,
    version=VERSION_COURANTE,
    description=(
        "Fiche de fabrication grand-voile au format FFab : ligne de titre "
        "complète (désignation, bateau, client, commande, code), grille "
        "Cotes (tissu) Guindant/Bordure/Chute/Têtière/Surface/Poids et "
        "largeurs MHW/MTW/MUW conservées en mesures libres."
    ),
    ancres_detection=[
        "cotes (tissu)",
        "mesures voile finie",
        "boitier guindant",
        "boitier chute",
        "coulisseaux de guindant",
        "montage fil",
        "grand-voile",
        "ris 1",
    ],
    champs=[
        RegleChamp(cible="traitement:ligne_titre_ffab", ancres=["grand-voile"], type="texte"),
        RegleChamp(cible="fiche.atelier", ancres=["découpe et fabrication"], type="texte", stop=["fichier"]),
        RegleChamp(cible="fiche.dessinateur", ancres=["dessiné par"], type="texte", traitement="dessinateur", stop=["fichier"]),
        RegleChamp(cible="fiche.quantite", ancres=["quantité"], type="entier"),
        RegleChamp(cible="traitement:fichier_edite", ancres=["fichier"], type="texte"),
        RegleChamp(cible="traitement:cotes_ffab", ancres=["cotes (tissu)", "guindant"], type="texte"),
        RegleChamp(
            cible="materiau.tissu_principal",
            ancres=["tissu(s)", "tissu"],
            type="texte",
            traitement="tissu_principal",
            stop=["cotes", "mesures voile finie"],
        ),
        RegleChamp(cible="fiche.montage_type", ancres=["zigzag", "zig"], type="texte", traitement="montage_jade"),
        RegleChamp(cible="traitement:lattes_ffab", ancres=["lattes"], type="texte"),
        RegleChamp(cible="fiche.notes", ancres=["notes"], type="texte", traitement="notes", stop=["fichier"]),
    ],
)

# Lot 7 — famille accessoires. Gabarit GÉNÉRIQUE par libellés : à la
# rédaction, aucun échantillon accessoire réel n'est encore disponible dans
# le dépôt (téléversement Dropbox en cours) — les ancres sont les libellés
# métier ordinaires, à calibrer sur les vrais PDFs dès leur arrivée, sans
# toucher au moteur (les règles sont des données, versionnées en base).
# Aucune ancre ne doit exister sur une fiche de voile (jamais « coulisseaux »,
# qui est une ancre GV fullbatten).
CODE_GABARIT_ACCESSOIRE = "FICHE_ACCESSOIRE_V1"
# Famille DISTINCTE (pas une version de plus) : le formulaire imprimé et la
# fiche de prise de cotes sont deux documents différents, tous deux réels et
# tous deux actifs. Les fusionner en v3 aurait désactivé la v2 en base et fait
# perdre à la prise de cotes ses lectures de matière/quantité (mesuré).
CODE_GABARIT_ACCESSOIRE_IMPRIME = "FICHE_ACCESSOIRE_IMPRIME_V1"

GABARIT_ACCESSOIRE_V1 = GabaritDef(
    code=CODE_GABARIT_ACCESSOIRE,
    version=1,
    description=(
        "Fiche de fabrication d'accessoire (Lazy Bag, housse, taud, capote…) : "
        "lecture par libellés (type, matière, couleur, dimensions, quantité, "
        "bateau). Gabarit générique v1, à calibrer sur les échantillons réels."
    ),
    ancres_detection=[
        "accessoire",
        "accessoires",
        "housse",
        "taud",
        "capote",
        "Lazy Bag",
        "bimini",
        "sellerie",
    ],
    champs=[
        # Le dépôt exige un code de fiche (règle produit : pas de référence,
        # pas de stockage) — l'ancre « référence/commande » donne aux fiches
        # accessoires autonomes leur code quand la ligne existe ; sinon le
        # dossier part en reprise complète, comme une voile sans code.
        RegleChamp(cible="fiche.code", ancres=["reference", "référence", "commande", "code"], type="texte"),
        RegleChamp(cible="accessoire.type", ancres=["type", "accessoire", "article"], type="texte"),
        RegleChamp(cible="accessoire.matiere", ancres=["matiere", "matière", "tissu", "materiau"], type="texte"),
        RegleChamp(cible="accessoire.couleur", ancres=["couleur", "coloris"], type="texte"),
        RegleChamp(cible="accessoire.dimensions", ancres=["dimensions", "dimension", "longueur"], type="texte"),
        RegleChamp(cible="accessoire.quantite", ancres=["quantite", "quantité", "qte"], type="entier"),
        RegleChamp(cible="accessoire.modele_bateau", ancres=["bateau", "modele", "modèle"], type="texte"),
    ],
)

# Lot CAL — calibration sur les formulaires réels (structures mesurées en
# local, aucun document client dans le dépôt). Le code reste
# FICHE_ACCESSOIRE_V1 (identifiant de famille) ; les définitions sont
# versionnées : la v2 devient la seule ACTIVE, la v1 demeure au registre
# (RG11). Mêmes cibles que la v1 — la projection Lot 7 et la migration 031
# sont inchangées, aucune migration requise ; seules les ancres (détection
# et libellés) sont calibrées.
GABARIT_ACCESSOIRE_V2 = GabaritDef(
    code=CODE_GABARIT_ACCESSOIRE,
    version=2,
    description=(
        "Fiche d'accessoire (Lazy Bag, housse, taud, cagnard, sac de pont…) "
        "calibrée sur les formulaires réels de l'atelier : fiche de prise de "
        "cotes imprimée (« Réservé pour le CLIENT », N° de commande, Type de "
        "bateau, Type de LazyBag, Couleurs Sunbrella) et plans CAD. Lecture "
        "par libellés ; les cases à cocher et les cotes sans libellé ne "
        "produisent aucun champ — l'approbateur décide."
    ),
    ancres_detection=[
        # Corpus v1 conservé (ancres absentes des fiches de voiles).
        "accessoire",
        "accessoires",
        "housse",
        "taud",
        "capote",
        "Lazy Bag",
        "bimini",
        "sellerie",
        # Lot CAL — formes réelles mesurées : « lazybag » en un seul mot sur
        # le formulaire, familles sac de pont/cagnard, et libellés propres à
        # la fiche de prise de cotes. Le formulaire cite « FullBatten » (une
        # ancre GV fullbatten) : ces ancres nombreuses et spécifiques le
        # font rester accessoire, sans qu'aucune n'existe sur une voile.
        "lazybag",
        "sac de pont",
        "cagnard",
        "prise de cotes",
        "réservé pour le client",
        "couleurs sunbrella",
        "ancrages",
        "bordure de la grand-voile",
        "bordure de la grand voile",
    ],
    champs=[
        # Libellés réels d'identification, du plus spécifique au générique.
        # Sans ligne lisible, aucun code n'est inventé : le dossier part en
        # reprise complète (règle produit « pas de référence, pas de
        # stockage », inchangée).
        RegleChamp(
            cible="fiche.code",
            ancres=[
                "n° de commande", "no de commande", "numéro de commande",
                "numero commande", "n° commande", "commande n°",
                "référence commande", "ref commande", "code fiche",
                "référence", "reference", "commande", "code",
            ],
            type="texte",
            stop=[
                "client", "date", "bateau", "type de bateau", "produit",
                "tissu", "matière", "couleur", "dimensions", "quantité",
                "atelier", "livraison", "port",
            ],
        ),
        # « type » NU est volontairement absent : sur le formulaire réel, il
        # lirait la ligne « Type de bateau : … » comme le type d'accessoire.
        # L'ancre gardée « type : » exige le deux-points immédiatement après
        # le mot (séquence de jetons), ce que « Type de bateau : » ne
        # satisfait pas. « Type de LazyBag » est un en-tête de cases à
        # cocher : sans valeur sur sa propre ligne, il ne produit rien —
        # la case cochée reste à l'approbateur.
        RegleChamp(
            cible="accessoire.type",
            ancres=[
                "type de lazybag", "type de Lazy Bag", "type d'accessoire",
                "type accessoire", "type de produit", "type d'article",
                "type article", "type :", "produit", "article",
                "désignation", "designation", "accessoire",
            ],
            type="texte",
            stop=[
                "nombre d'ancrages", "nombre ancrages", "type de grand-voile",
                "type de grand voile", "couleurs sunbrella", "couleur",
                "bordure", "matière", "tissu", "dimensions", "quantité",
                "bateau", "port", "emplacement",
            ],
        ),
        RegleChamp(
            cible="accessoire.matiere",
            ancres=["matière", "matiere", "matériau", "materiau", "composition", "tissu"],
            type="texte",
            stop=["couleur", "coloris", "couleurs sunbrella", "dimensions", "quantité", "type"],
        ),
        # « teinte » est volontairement absent : le texte imprimé des
        # formulaires contient « teinté masse » (tissu Sunbrella), dont la
        # forme normalisée percuterait cette ancre et lirait « masse »
        # comme une couleur. « Couleurs Sunbrella » est l'en-tête réel du
        # nuancier ; « autres coloris » est son champ libre.
        RegleChamp(
            cible="accessoire.couleur",
            ancres=[
                "couleurs sunbrella", "couleur sunbrella", "coloris sunbrella",
                "autres coloris", "couleur", "coloris",
            ],
            type="texte",
            stop=["matière", "tissu", "dimensions", "quantité", "bateau", "bordure"],
        ),
        # Sur les documents réels, les cotes sont des nombres SANS libellé
        # (schémas, plans de coupe) : le moteur libellé→valeur ne les lie
        # pas et ne doit pas les inventer. Ancres v1 conservées.
        RegleChamp(
            cible="accessoire.dimensions",
            ancres=["dimensions", "dimension", "longueur"],
            type="texte",
            stop=["quantité", "couleur", "matière", "bateau", "type"],
        ),
        # « nombre » NU est volontairement absent : « Nombre d'ancrages »
        # du formulaire n'est pas une quantité d'accessoires.
        RegleChamp(
            cible="accessoire.quantite",
            ancres=["quantité", "quantite", "nombre d'exemplaires", "exemplaires", "qte", "qté"],
            type="entier",
            stop=["bateau", "dimensions", "couleur", "matière"],
        ),
        # « Type de bateau » est le libellé réel de l'en-tête client du
        # formulaire ; il précède les génériques. Les voisines de la même
        # ligne d'en-tête (Port, Emplacement…) bornent la valeur.
        RegleChamp(
            cible="accessoire.modele_bateau",
            ancres=[
                "type de bateau", "modèle de bateau", "modele de bateau",
                "modèle du bateau", "bateau", "modèle", "modele", "navire",
            ],
            type="texte",
            stop=[
                "port", "emplacement", "tél", "tel", "nom", "prénom",
                "prenom", "client", "date",
            ],
        ),
    ],
)

# Lot CAL-2 — calibration sur les FORMULAIRES ACCESSOIRES RÉELS de l'atelier
# (documents Dropbox ouverts et mesurés le 2026-10-29 ; aucun PDF client n'est
# versionné dans ce dépôt). Trois gabarits d'impression distincts ont été
# observés, tous préfixés « Fiche de … » :
#
#   1. « Fiche de Lazy Bag » (paysage 792×612), mesuré sur
#      `/ACCESSOIRES 2024/LAZY BAG 2P ST64/LB 2 parties.pdf` et
#      `/ACCESSOIRES 2025/LAZY BAG 2P ST105/LB ST105 FINI.pdf` :
#          Fiche de Lazy Bag
#          ST64                       ← ligne de code NUE, sous le titre
#          Commande                   ← libellé présent, valeur SOUVENT VIDE
#          Client (réf.) SEAMTECH PRO
#          Longueur du gousset de latte 3.22m - Latte jonc 12mm … Contrôle
#          Diametre de ralingue* 8 mm
#          Longueur de la ralingue* 1.77 m
#          Type des coulisseaux A031 (X3)
#          Longueur du lazy* 3.32 m
#          Hauteur du lazy* 0.29m
#          Hauteur du capot* 0.84m
#          Observations/Notes SANS LOGO
#   2. « Fiche de fourreau de gse » (paysage 792×612), mesuré sur
#      `/ACCESSOIRES 2024/HOUSSE DE GSE ST96/FICHE FINIE.pdf` et
#      `/ACCESSOIRES 2024/HOUSSE DE GSE ST101/ST101 FINI.pdf` :
#          Fiche de fourreau de gse
#          Commande ST96              ← ici le code est APRÈS « Commande »
#          Client (réf.) SEAMTECH PRO
#          Contrôle
#          Longueur du fourreau 10.50 m
#          Hauteur du fourreau 0.62 m
#          longueur point decoute 0.85m
#          Hauteur du point d'ecoute 0.42 m
#          Observations/Notes [SANS LOGO]
#   3. « Fiche de Sac de Pont » (A4 portrait 595×842), mesuré sur
#      `/ACCESSOIRES 2025/SAC DE PONT ST265B/ST 256b_ffab.pdf` et
#      `/ACCESSOIRES 2024/SAC DE PONT ST74B/ST 074b_ffab.pdf` :
#          Fiche de Sac de Pont
#          04/10/2024
#          Bateau First 325
#          Commande ST 074b
#          Client (réf.) Voilerie VBO (ST74 - SAOODXJBQ) Contrôle
#          Logo Pas de logo
#          Couleur Captain Navy 5057
#          Longueur du sac* 3,19 m
#          Hauteur avant* 1,14 m
#          Observations/Notes
#
# Défaut PROUVÉ sur ces documents (mesuré avec `extraire_fiche`) : la fiche
# Lazy Bag était détectée comme `FICHE_GV_FULLBATTEN_V1` — la ligne « Type des
# coulisseaux » porte l'ancre `coulisseaux` de la GV lattée, score 1 partout,
# et la GV passe avant les accessoires à score égal : titre, client, code et
# dimensions sortaient TOUS vides. La fiche « fourreau de gse » n'était
# reconnue par AUCUN gabarit (« fourreau » n'était une ancre nulle part), et la
# fiche « Sac de Pont » n'était reconnue que par la v1 (code seul, sans type,
# ni couleur, ni bateau). La v3 ci-dessous est calibrée sur ces trois
# formulaires : ses ancres de titre pèsent PLUS que l'ancre isolée de la GV.
#
# Les documents SANS COUCHE TEXTE (plans exportés du traceur : `TAUD DE GV.pdf`,
# `ST298.pdf`, `ST40 FINI.pdf` — 0 à 13 caractères extraits) ne sont PAS des
# fiches : `analyser_pdf` lève `ExtractionImpossible` et le scanner les compte
# comme pièces illisibles, jamais comme « gabarit inconnu ».
GABARIT_ACCESSOIRE_IMPRIME = GabaritDef(
    code=CODE_GABARIT_ACCESSOIRE_IMPRIME,
    version=1,
    description=(
        "Fiche d'accessoire imprimée par l'atelier, calibrée sur les trois "
        "formulaires réels mesurés (Fiche de Lazy Bag, Fiche de fourreau de "
        "gse, Fiche de Sac de Pont) : titre = type d'accessoire, code lu sur "
        "la ligne « Commande » ou sur la ligne nue sous le titre, client "
        "« (réf.) », bateau, couleur, logo, première dimension nommée. Les "
        "autres dimensions restent en mesures libres (RG6) : aucune n'est "
        "forcée dans une cote mal nommée."
    ),
    ancres_detection=[
        # Chaînes RÉELLES des trois formulaires imprimés mesurés. Aucune
        # n'existe sur une fiche de voile (vérifié le 2026-10-29 sur
        # `ST 508_ffab.pdf` et `ST 660b_ffab.pdf`, qui ne portent que
        # « coulisseaux » et « lattes ») ni sur le formulaire de prise de
        # cotes (famille FICHE_ACCESSOIRE_V1, v2 restée active pour lui).
        "fiche de lazy bag",
        "fiche de fourreau de gse",
        "fourreau",
        "fiche de sac de pont",
        "gousset de latte",
        "diametre de ralingue",
        "hauteur du capot",
    ],
    champs=[
        # 1) Le TITRE donne le type d'accessoire (« Fiche de Lazy Bag » →
        #    « Lazy Bag »). Le traitement relit la page : la valeur n'est pas
        #    sur la ligne de l'ancre au sens strict, elle EST l'ancre.
        RegleChamp(
            cible="accessoire.type",
            ancres=["fiche de lazy bag", "fiche de fourreau de gse", "fiche de sac de pont"],
            type="texte",
            traitement="titre_accessoire",
        ),
        # 2) Code : ligne « Commande ST 074b » (formulaires 2 et 3) ou ligne
        #    NUE « ST64 »/« ST105 » juste sous le titre (formulaire 1, où
        #    « Commande » est imprimé sans valeur). Un seul traitement couvre
        #    les deux formes mesurées.
        RegleChamp(
            cible="fiche.code",
            ancres=["fiche de lazy bag", "fiche de fourreau de gse", "fiche de sac de pont", "commande"],
            type="texte",
            traitement="code_accessoire",
        ),
        # 3) Client : « Client (réf.) … ». « Contrôle » est un en-tête de
        #    colonne imprimé sur la MÊME ligne (mesuré sur les trois
        #    formulaires) : il borne la valeur, il n'appartient pas au client.
        RegleChamp(
            cible="fiche.client",
            ancres=["client (réf.)", "client (ref.)", "client"],
            type="texte",
            traitement="client",
            stop=["contrôle", "controle", "logo", "couleur", "date", "bateau"],
        ),
        # 4) Bateau : présent sur le formulaire « Sac de Pont » uniquement
        #    (« Bateau First 325 »). Absent des deux autres : rien n'est lu,
        #    aucune valeur n'est inventée.
        RegleChamp(
            cible="accessoire.modele_bateau",
            ancres=["bateau"],
            type="texte",
            stop=["commande", "client", "date", "logo", "couleur", "longueur", "hauteur"],
        ),
        # 5) Couleur / coloris du tissu (« Couleur Captain Navy 5057 »).
        RegleChamp(
            cible="accessoire.couleur",
            ancres=["couleur", "coloris"],
            type="texte",
            stop=["longueur", "hauteur", "logo", "observations", "commande", "client"],
        ),
        # 6) UNE seule dimension nommée est projetée dans `dimensions` : la
        #    première longueur mesurée, dont l'unité est explicite. La projeter
        #    en « longueur_m » est exact ; y joindre la hauteur la ferait
        #    ranger en « largeur_m », ce qui serait un faux libellé. Les autres
        #    dimensions restent des mesures libres (RG6).
        #    L'astérisque est imprimé sur le formulaire (« Longueur du sac* ») et
        #    la normalisation le conserve : les deux formes sont donc listées.
        RegleChamp(
            cible="accessoire.dimensions",
            ancres=[
                "longueur du sac*",
                "longueur du sac",
                "longueur du fourreau",
                "longueur du lazy",
                "longueur du gousset de latte",
            ],
            type="texte",
            traitement="dimension_nommee",
            stop=["hauteur", "diametre", "type", "observations", "contrôle", "controle"],
        ),
        # 7) Logo : « Logo sans » / « Logo Pas de logo » (mesuré sur le
        #    formulaire Sac de Pont) et « SANS LOGO » en observations.
        RegleChamp(
            cible="fiche.logo",
            ancres=["logo"],
            type="texte",
            traitement="logo_accessoire",
            stop=["couleur", "longueur", "hauteur", "observations", "commande", "client"],
        ),
    ],
)

# Ordre significatif : à score d'ancres égal, la version la plus récente
# d'un code gagne la détection (v3 devant v2 devant v1) ; les gabarits du
# corpus réel viennent EN DERNIER pour que les gabarits historiques (génois,
# portant) gagnent les égalités de score sur leurs propres documents.
GABARITS_EMBARQUES: tuple[GabaritDef, ...] = (
    GABARIT_PORTANT_V2,
    GABARIT_PORTANT,
    GABARIT_GENOIS,
    GABARIT_GV_FULLBATTEN,
    GABARIT_JADE,
    GABARIT_GV_FFAB,
    # Lots 7 + CAL : les gabarits accessoires viennent en dernier — leurs
    # ancres de titre (« fiche de lazy bag », « fiche de fourreau de gse »,
    # « fiche de sac de pont »…) sont absentes des fiches de voiles, vérifié
    # sur les fiches réelles. La famille « imprimé » passe AVANT la prise de
    # cotes pour gagner l'égalité de score sur « sac de pont ».
    GABARIT_ACCESSOIRE_IMPRIME,
    GABARIT_ACCESSOIRE_V2,
    GABARIT_ACCESSOIRE_V1,
)



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


def synchroniser_gabarits_embarques(index: Any) -> list[str]:
    """Ajoute au registre les seuls gabarits embarqués ABSENTS.

    Utilisée au démarrage lors d'une mise à jour : une base déjà initialisée
    reçoit les nouvelles familles/versions livrées par le code, sans jamais
    réécrire une ligne existante (un gabarit publié ou personnalisé n'est pas
    écrasé). Quand une nouvelle version d'un code est ajoutée, les anciennes
    versions de CE code sont désactivées, comme par ``initialiser_gabarits``.
    Retourne les libellés ajoutés (``"CODE vN"``), pour le journal.
    """
    with index.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT code, version FROM gabarit")
            existants = {(str(code), int(version)) for code, version in cursor.fetchall()}

    ajoutes: list[str] = []
    codes_touches: set[str] = set()
    for gabarit in GABARITS_EMBARQUES:
        if (gabarit.code, gabarit.version) in existants:
            continue
        enregistrer_gabarit(index, gabarit)
        ajoutes.append(f"{gabarit.code} v{gabarit.version}")
        codes_touches.add(gabarit.code)
        LOGGER.info("Gabarit embarqué absent ajouté au registre : %s v%d", gabarit.code, gabarit.version)

    if codes_touches:
        with index.connect() as connection:
            with connection.cursor() as cursor:
                for code in sorted(codes_touches):
                    cursor.execute(
                        "UPDATE gabarit SET actif = false WHERE code = %s AND version < ("
                        "   SELECT max(version) FROM gabarit WHERE code = %s)",
                        (code, code),
                    )
    return ajoutes

#: Gabarit « document » de la FICHE DE REPRISE (voir `reprise.py`).
#:
#: Il n'est JAMAIS proposé à la détection : son ancre est technique et
#: n'existe dans aucun document. Il n'est enregistré en base que pour que
#: l'écran de validation affiche une provenance lisible au lieu d'une colonne
#: « gabarit » vide sur les dossiers sans fiche de fabrication — 21 des 58
#: dossiers réels mesurés.
GABARIT_REPRISE = GabaritDef(
    code="FICHE_REPRISE_V1",  # doit valoir reprise.CODE_GABARIT_REPRISE (test dédié)
    version=1,
    description=(
        "Fiche de reprise : dossier réel sans fiche de fabrication (plan SailPack, "
        "formulaire de prise de cotes ou PDF image). Les valeurs sont lues dans le "
        "document ou dans le nom du dossier, et une anomalie exige la saisie manuelle."
    ),
    ancres_detection=["fiche de reprise (aucune ancre documentaire)"],
    champs=[],
)
