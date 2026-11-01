"""Dépôt d'un dossier complet, lots suivis et reprenables (Lot C — §17.2, §11, §12.2).

Flux : on désigne un dossier de l'archive ; le service y repère LE PDF de fiche
( meilleure détection de gabarit), l'extrait avec le moteur du Lot B, rattache
les autres fichiers du dossier comme pièces jointes (``fiche_piece_jointe``,
migration 010 — ``fiche_lien`` relie des FICHES entre elles), et enregistre
l'opération comme lot suivi en base.

Contraintes appliquées, toutes testées :
- **Archive en lecture seule (RG13)** : ce module n'appelle que ``iterdir`` /
  ``read_bytes`` / ``stat`` ; aucune écriture, copie, renommage ni suppression ;
  les sorties (état des lots, échecs) vivent en base, hors archive.
- **Idempotence** : clé = ``os.path.normcase(chemin) + SHA-256`` du PDF de fiche
  (même normalisation que le crawler — c'est le sujet du correctif normcase) ;
  un dossier déjà TRAITÉ n'est jamais refait (index unique partiel en base).
- **Reprise** : un lot interrompu conserve ses dossiers ``en_attente`` ; on
  relance ``executer_lot`` — traités et échecs ne bougent pas.
- **Transaction unique par dossier** : fiche + pièces jointes + ligne de lot
  sont écrites ensemble, ou rien (test_depot_transactionnel).
- **Un dossier refusé est un RÉSULTAT** : raison explicite (« gabarit inconnu »,
  « aucun PDF », « PDF illisible »), jamais une exception qui tuerait le lot.
- Statut d'arrivée : ``a_valider`` — jamais valide (RG3, délégué à ecrire_fiche).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from seamtech_search.extractions_voile import lier_extraction_voile_document_curseur
from seamtech_search.fiches.extraction import (
    DetectionMixteIndeterminee,
    analyser_candidat_fiche,
    analyser_pdf,
    extraire_fiche,
    texte_normalise,
)
from seamtech_search.fiches.gabarits import GabaritDef, charger_gabarits
from seamtech_search.fiches.modeles import ChampExtrait, FicheExtraite
from seamtech_search.fiches.persistance import ecrire_fiche_resultat
from seamtech_search.fiches.promotion import fichiers_pour_promotion, promouvoir_fichiers
from seamtech_search.fiches.reprise import construire_fiche_de_reprise

LOGGER = logging.getLogger("seamtech_search.fiches.depot")

SEPARATEUR_CLE = "|"
# Agencement du staging Lot 10 (miroir de televersement_lot.DOSSIER_*) :
# uploads/lots-televerses/<jeton>/fichiers/<arborescence envoyée>. Le jeton
# est propre à chaque envoi — il ne fait pas partie de l'identité du dossier.
_SEGMENT_LOTS_TELEVERSES = "lots-televerses"
_SEGMENT_FICHIERS_STAGING = "fichiers"
# Bornes explicites de la découverte récursive : l'archive complète mesurée
# reste très en dessous, et un dépassement est un refus nommé — jamais une
# troncature silencieuse d'un lot de plusieurs milliers de dossiers.
LOT_MAX_DOSSIERS = 5000
LOT_MAX_PROFONDEUR = 32
# Rôles de pièces jointes (suffixe → rôle) : valeurs fermées, documentées dans
# docs/API.md ; tout suffixe inconnu part en « piece_jointe ».
ROLES_PIECES: tuple[tuple[str, str], ...] = (
    (".dwg", "plan"),
    (".dxf", "plan"),
    (".xlsm", "production"),
    (".xlsx", "production"),
    (".jpg", "croquis"),
    (".jpeg", "croquis"),
    (".png", "croquis"),
)

_SQL_LOT_INS = "INSERT INTO lot_import (dossier_racine, notes) VALUES (%s, %s) RETURNING id_lot"
_SQL_LOT_DOSSIERS_INS = (
    "INSERT INTO lot_dossier (id_lot, chemin_dossier) VALUES (%s, %s) ON CONFLICT DO NOTHING"
)
_SQL_LOT_COMPTE = "SELECT COUNT(*) FROM lot_dossier WHERE id_lot = %s"
_SQL_LOT_MAJ = (
    "UPDATE lot_import SET nb_dossiers = %s, statut = %s, termine_le = CASE WHEN %s = 'termine' THEN now() ELSE termine_le END "
    "WHERE id_lot = %s"
)
_SQL_LOT_LIRE = "SELECT id_lot, dossier_racine, statut, nb_dossiers, nb_traites, nb_echecs, cree_le, termine_le, notes FROM lot_import WHERE id_lot = %s"
_SQL_LOTS_LISTE = (
    "SELECT id_lot, dossier_racine, statut, nb_dossiers, nb_traites, nb_echecs, cree_le, termine_le "
    "FROM lot_import ORDER BY id_lot DESC LIMIT 200"
)
_SQL_DOSSIERS_LOT = (
    "SELECT id_lot_dossier, chemin_dossier, statut, raison, id_fiche, nb_pieces, traite_le "
    "FROM lot_dossier WHERE id_lot = %s ORDER BY id_lot_dossier"
)
_SQL_RESTANTS = "SELECT chemin_dossier FROM lot_dossier WHERE id_lot = %s AND statut = 'en_attente' ORDER BY id_lot_dossier"
_SQL_DOSSIER_PAR_ID = "SELECT id_lot, chemin_dossier, statut FROM lot_dossier WHERE id_lot_dossier = %s"
_SQL_LIGNE_MAJ = (
    "UPDATE lot_dossier SET statut = %s, raison = %s, id_fiche = %s, cle_idempotence = %s, "
    "nb_pieces = %s, traite_le = now() WHERE id_lot_dossier = %s"
)
_SQL_DEJA_TRAITE = (
    "SELECT id_lot_dossier, id_lot FROM lot_dossier WHERE cle_idempotence = %s "
    "AND statut = 'traite' ORDER BY id_lot_dossier LIMIT 1"
)
# Reconnaissance d'un rejeu téléversé (MAN-02) : les clés stockées portent
# le chemin absolu stagé (jeton de session inclus) suivi de l'empreinte ; on
# compare donc, à empreinte égale, le chemin relatif envoyé.
_SQL_CANDIDATS_MEME_EMPREINTE = (
    "SELECT id_lot_dossier, id_lot, cle_idempotence FROM lot_dossier "
    "WHERE statut = 'traite' AND cle_idempotence LIKE %s ORDER BY id_lot_dossier"
)
_SQL_DOCUMENT_UPSERT = (
    "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, "
    "is_dir, content, content_hash, extraction_status, role) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s, 'metadata', %s) "
    "ON CONFLICT (path_key) DO UPDATE SET role = EXCLUDED.role, content_hash = EXCLUDED.content_hash, "
    "size = EXCLUDED.size, modified_at = EXCLUDED.modified_at, content = EXCLUDED.content, "
    "source_sha256 = NULL "
    # Même règle que pour le PDF de fiche, et pour la même raison : une pièce
    # déjà passée par le flux d'import porte un `content_hash` différent
    # (l'empreinte du TEXTE qui en a été extrait) alors que ses octets n'ont
    # pas changé. Sans la comparaison sur l'IDENTITÉ DES OCTETS, le dépôt Lot C
    # était refusé à tort (« Une pièce existante au même chemin a changé ») —
    # vu en CI sur `recette-corpus-reel`, REF-001..REF-007.
    "WHERE documents.content_hash = EXCLUDED.content_hash OR documents.source_sha256 = %s OR ("
    "documents.role IS NULL AND documents.id_fiche IS NULL AND documents.object_key IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM fiche_piece_jointe p WHERE p.id_document = documents.id) "
    "AND NOT EXISTS (SELECT 1 FROM approval_requests a WHERE a.id_piece = documents.id) "
    "AND NOT EXISTS (SELECT 1 FROM sail_extractions s WHERE s.id_document = documents.id) "
    "AND NOT EXISTS (SELECT 1 FROM accessory_extractions a WHERE a.id_document = documents.id) "
    "AND NOT EXISTS (SELECT 1 FROM lot_dossier l WHERE l.statut = 'traite' "
    "AND split_part(l.cle_idempotence, '|', 1) = documents.path)) RETURNING id"
)
_SQL_DOCUMENT_FICHE_UPSERT = (
    "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, "
    "is_dir, content, content_hash, source_sha256, extraction_status, category, role) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s, %s, 'metadata', 'analyzed', 'fiche_pdf') "
    "ON CONFLICT (path_key) DO UPDATE SET name = EXCLUDED.name, path = EXCLUDED.path, "
    "parent_path = EXCLUDED.parent_path, extension = EXCLUDED.extension, size = EXCLUDED.size, "
    "modified_at = EXCLUDED.modified_at, "
    "content = CASE WHEN COALESCE(documents.content, '') = '' THEN EXCLUDED.content ELSE documents.content END, "
    "content_hash = EXCLUDED.content_hash, source_sha256 = NULL, category = 'analyzed', role = 'fiche_pdf' "
    # Reprise refusée SAUF preuve que c'est bien le même fichier : (a) le texte
    # indexé est identique, (b) l'IDENTITÉ DES OCTETS déjà enregistrée est
    # identique, ou (c) la ligne est vierge (aucune liaison, aucune pièce,
    # aucun objet).
    #
    # Le cas (b) manquait : un même PDF déjà passé par le flux d'import porte
    # un `content_hash` différent (texte extrait d'un côté, métadonnées de
    # l'autre) alors que les octets n'ont pas changé — le dépôt était refusé à
    # tort (« Un PDF existant au même chemin a changé »), vu en CI sur
    # `recette-corpus-reel` (REF-001..007, lot C). La comparaison porte donc
    # aussi sur l'identité des octets, jamais sur le seul texte indexé.
    #
    # Le déclencheur `documents_invalider_source_sha256` met `source_sha256` à
    # NULL sur toute mise à jour de taille/mtime/contenu : l'identité est donc
    # RéTABLIE juste après, par un UPDATE qui ne touche aucune des colonnes
    # surveillées (voir `_etablir_identite_octets`).
    "WHERE documents.content_hash = EXCLUDED.content_hash OR documents.source_sha256 = %s OR ("
    "documents.role IS NULL AND documents.id_fiche IS NULL AND documents.object_key IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM fiche_piece_jointe p WHERE p.id_document = documents.id) "
    "AND NOT EXISTS (SELECT 1 FROM approval_requests a WHERE a.id_piece = documents.id) "
    "AND NOT EXISTS (SELECT 1 FROM sail_extractions s WHERE s.id_document = documents.id) "
    "AND NOT EXISTS (SELECT 1 FROM accessory_extractions a WHERE a.id_document = documents.id) "
    "AND NOT EXISTS (SELECT 1 FROM lot_dossier l WHERE l.statut = 'traite' "
    "AND split_part(l.cle_idempotence, '|', 1) = documents.path)) RETURNING id"
)


def _etablir_identite_octets(cursor: Any, id_document: int, empreinte: str) -> None:
    """Rétablit l'identité des octets après l'upsert.

    Le déclencheur ``documents_invalider_source_sha256`` met ``source_sha256``
    à NULL dès qu'une colonne surveillée bouge — y compris pendant notre propre
    upsert. Sans ce rétablissement, l'identité serait perdue à chaque dépôt et
    le prochain dépôt ne pourrait plus prouver que le fichier n'a pas changé.
    Cet UPDATE ne touche AUCUNE colonne surveillée : le déclencheur ne se
    déclenche donc pas.
    """
    cursor.execute("UPDATE documents SET source_sha256 = %s WHERE id = %s", (empreinte, id_document))


def _texte_indexable_fiche(fiche: Any) -> str:
    """Index searchable fiche metadata without persisting source paths.

    The primary PDF can be new to the crawler (for example, the original file
    behind an uploaded staging copy). In that case, its catalogue row still
    needs searchable metadata. When the crawler already extracted document
    text, the upsert preserves that content instead of replacing it with this
    metadata fallback.
    """
    valeurs: list[str] = []
    for nom in (
        "code",
        "titre",
        "gamme",
        "type_voile_libelle",
        "client_nom",
        "client_chantier",
        "bateau_nom",
        "bateau_taille",
        "commande_numero",
        "atelier",
        "tissu_texte",
        "montage_type",
        "montage_fil",
        "logo",
        "notes",
        "dessinateur",
        "date_dessin",
        "date_edition",
    ):
        valeur = getattr(fiche, nom, None)
        if valeur is not None and str(valeur).strip():
            valeurs.append(str(valeur).strip())

    tous_les_champs = getattr(fiche, "tous_les_champs", None)
    if callable(tous_les_champs):
        for champ in tous_les_champs():
            nom_champ = str(getattr(champ, "champ", "")).lower()
            if any(fragment in nom_champ for fragment in ("fichier_source", "path", "chemin", "object_key")):
                continue
            for attribut in ("valeur_normalisee", "valeur_brute"):
                valeur = getattr(champ, attribut, None)
                if valeur is not None and str(valeur).strip():
                    valeurs.append(str(valeur).strip())

    return " ".join(dict.fromkeys(valeurs))
# Même expression que l'indexeur (RG12 : contenu indexé = métadonnées seulement).
# La configuration est passée en PARAMÈTRE (avec cast explicite) : elle est
# résolue par l'indexeur (``config_recherche_texte``), donc identique à celle
# qui a servi à créer les index, et le repli ``simple`` reste utilisable.
_SQL_DOCUMENT_VECTOR = (
    "UPDATE documents SET search_vector = to_tsvector(%s::regconfig, "
    "concat_ws(E'\\n', name, path, extension, content)) WHERE id = %s"
)
_SQL_PIECE_INS = (
    "INSERT INTO fiche_piece_jointe (id_fiche, id_document, chemin, role, empreinte_sha256, taille_octets) "
    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id_fiche, chemin, empreinte_sha256) DO NOTHING"
)
_SQL_COMPTE_FICHES = "SELECT COUNT(*) FROM fiche"
_SQL_COMPTE_PIECES = "SELECT COUNT(*) FROM fiche_piece_jointe"


class DepotImpossible(RuntimeError):
    """Le dossier ne peut même pas être lu (absent, pas un dossier)."""


# Seules ces raisons internes constantes peuvent être exposées sans risquer
# d'y inclure un chemin, une valeur métier ou le texte d'une exception tierce.
_RAISON_DEPOT_SURE = frozenset({
    "Rejeu refusé : des pièces précédentes sont absentes ; revue nécessaire.",
    "Rejeu impossible : fiche précédente non identifiée.",
    "Les pièces ont changé pendant la préparation du dépôt.",
    "RG11 : fiche validée ; dépôt différent refusé sans modification des pièces.",
    "Un PDF existant au même chemin a changé ; revue nécessaire.",
    "Une pièce existante au même chemin a changé ; revue nécessaire.",
    "Une copie objet vérifiée manque pour ce dossier.",
    "L'intégrité d'un fichier a changé après sa promotion.",
    "Le document ou son objet a changé pendant le dépôt.",
    "Rejeu sans PDF source.",
    "Rejeu historique : la fiche courante a changé ; revue nécessaire.",
    "Rejeu impossible : document précédent non identifié.",
    "Rejeu impossible : fiche précédente absente.",
    "Rejeu impossible : pièces modifiées pendant la vérification.",
    "Rejeu impossible : PDF précédent non identifié.",
    "Rejeu impossible : pièces jointes différentes ou ambiguës.",
    "Rejeu impossible : nombre de pièces jointes différent.",
})


def _raison_ecriture_sure(erreur: Exception) -> str:
    prefixe = f"écriture annulée : {type(erreur).__name__}"
    if type(erreur) is DepotImpossible and str(erreur) in _RAISON_DEPOT_SURE:
        return f"{prefixe}: {erreur}"
    return prefixe


class DepotAnnule(DepotImpossible):
    """Le traitement a été interrompu à la demande de l'opérateur."""


class DepotClaimPerdu(RuntimeError):
    """Le worker n'est plus propriétaire du job PostgreSQL."""


def _verrouiller_claim(cursor: Any, claim: tuple[str, str] | None) -> None:
    """Sérialise les mutations métier avec le transfert du propriétaire PG."""
    if claim is None:
        return
    cursor.execute("SELECT claimed_by FROM import_jobs WHERE id = %s FOR UPDATE", (claim[0],))
    ligne = cursor.fetchone()
    if not ligne or ligne[0] != claim[1]:
        raise DepotClaimPerdu("Le worker ne détient plus le job.")


@dataclass
class FichierDossier:
    """Un fichier du dossier, lu en lecture seule (chemin, rôle, empreinte).

    L'empreinte est PARESSEUSE : scanner un dossier volumineux ne relit pas
    toutes ses pièces avant même de savoir si son PDF produit une fiche
    viable. Elle est calculée au premier accès (écriture des pièces d'un
    dépôt accepté) puis mise en cache.
    """

    chemin: Path
    role: str
    taille_octets: int
    _empreinte_sha256: str | None = field(default=None, repr=False)

    @property
    def empreinte_sha256(self) -> str:
        if self._empreinte_sha256 is None:
            self._empreinte_sha256 = empreinte_fichier(self.chemin)
        return self._empreinte_sha256

    @empreinte_sha256.setter
    def empreinte_sha256(self, valeur: str | None) -> None:
        self._empreinte_sha256 = valeur


@dataclass
class PlanDossier:
    """Ce que le scanner a compris d'un dossier, SANS rien écrire."""

    dossier: Path
    pdf_fiche: Path | None = None
    gabarit_code: str | None = None
    score: int = 0
    pieces: list[FichierDossier] = field(default_factory=list)
    raison_refus: str | None = None
    #: Autres PDF du dossier qui portent EUX AUSSI une fiche exploitable.
    #: Mesuré sur `/YACHET CLASSIC ST508` : la même voile y est archivée en
    #: deux révisions (`ST 508_ffab.pdf` et `ST 508V2_ffab.pdf`, toutes deux
    #: viables, score 6). Le scanner doit trancher — il le fait de façon
    #: déterministe (score, puis nom « …ffab », puis ordre alphabétique) — mais
    #: il ne doit pas le faire EN SILENCE : ces PDF sont conservés en pièces
    #: jointes et listés ici pour l'opérateur.
    autres_fiches: list[Path] = field(default_factory=list)
    fiche_extraite: FicheExtraite | None = field(default=None, repr=False)

    @property
    def accepte(self) -> bool:
        return self.pdf_fiche is not None


def empreinte_fichier(chemin: Path) -> str:
    """SHA-256 d'un fichier (lecture seule — preuve RG13, clé d'idempotence)."""
    empreinte = hashlib.sha256()
    with chemin.open("rb") as fichier:
        for bloc in iter(lambda: fichier.read(65536), b""):
            empreinte.update(bloc)
    return empreinte.hexdigest()


def _chemin_logique_televersement(chemin: Path | str) -> str | None:
    """Chemin RELATIF envoyé par le navigateur, si ``chemin`` est stagé (Lot 10).

    ``…/lots-televerses/<jeton>/fichiers/ANNEE/AFFAIRE/fiche.pdf`` →
    ``ANNEE/AFFAIRE/fiche.pdf``. Hors staging : ``None`` (l'identité reste le
    chemin d'archive). Les séparateurs ``/`` et ``\\`` sont acceptés : une clé
    écrite sous Windows doit rester reconnaissable sous Linux et inversement.
    """
    segments = [s for s in str(chemin).replace("\\", "/").split("/") if s not in ("", ".")]
    for indice, segment in enumerate(segments):
        if segment != _SEGMENT_LOTS_TELEVERSES:
            continue
        if indice + 2 >= len(segments) or segments[indice + 2] != _SEGMENT_FICHIERS_STAGING:
            return None
        relatif = segments[indice + 3 :]
        return "/".join(relatif) if relatif else None
    return None


def cle_idempotence(chemin_pdf: Path, empreinte_pdf: str) -> str:
    """Chemin normalisé (os.path.normcase — même fonction que crawler.py:154) + empreinte.

    Le format stocké ne change pas : d'autres lecteurs (pièces d'une fiche,
    approbations) retrouvent le PDF principal par la partie chemin de cette
    clé. La reconnaissance d'un rejeu TÉLÉVERSÉ dans une nouvelle session
    (MAN-02) se fait en plus, à la lecture, par chemin relatif envoyé +
    empreinte — voir deposer_dossier.
    """
    return os.path.normcase(str(chemin_pdf.resolve())) + SEPARATEUR_CLE + empreinte_pdf


def role_piece(chemin: Path) -> str:
    """Rôle d'une pièce jointe d'après son suffixe (valeurs fermées)."""
    suffixe = chemin.suffix.lower()
    return next((role for motif, role in ROLES_PIECES if suffixe == motif), "piece_jointe")


def decouvrir_dossiers_candidats(racine: Path) -> list[Path]:
    """Dossiers d'une racine contenant au moins un PDF DIRECT.

    La racine peut être une année, une catégorie ou un dossier d'affaire :
    chaque niveau qui porte directement un PDF devient une ligne de lot.
    Les dossiers cachés et les liens symboliques de dossiers ne sont pas
    suivis (cycles et artefacts d'outils exclus, explicitement). La lecture
    est déterministe et ne modifie rien.
    """
    racine = Path(racine)
    if not racine.is_dir():
        raise DepotImpossible(f"Racine de lot introuvable : {racine}")
    racine_resolue = racine
    candidats: list[Path] = []
    for courant, sous_dossiers, fichiers in os.walk(racine_resolue, topdown=True, followlinks=False):
        dossier = Path(courant)
        relatif = dossier.relative_to(racine_resolue)
        profondeur = len(relatif.parts)
        if profondeur > LOT_MAX_PROFONDEUR:
            raise DepotImpossible(
                f"Profondeur de lot supérieure à {LOT_MAX_PROFONDEUR} niveaux sous {racine} — "
                "scindez la racine ; conséquence : aucun lot n'a été créé."
            )
        sous_dossiers[:] = sorted(
            (
                nom
                for nom in sous_dossiers
                if not nom.startswith(".") and not (dossier / nom).is_symlink()
            ),
            key=str.casefold,
        )
        if any(not nom.startswith(".") and nom.lower().endswith(".pdf") for nom in fichiers):
            candidats.append(dossier)
            if len(candidats) > LOT_MAX_DOSSIERS:
                raise DepotImpossible(
                    f"Plus de {LOT_MAX_DOSSIERS} dossiers avec PDF sous {racine} — "
                    "scindez le lot par année ou catégorie ; conséquence : aucun lot n'a été créé."
                )
    return sorted(candidats, key=lambda chemin: str(chemin).casefold())


# Signature MESURÉE des formulaires de prise de cotes du client (trois
# exemplaires réels : `/VOILES 2024/CAVAILLE ST76/CavailleGV.pdf`,
# `/VOILES 2024/FIRST 210 ST106/GSE First210.pdf`,
# `/VOILES 2024/FIRST 325 ST74A/LF DeveloppementSolentFirst325.pdf`).
# Ils portent « Fiche de prise de cotes » + « Compléter et retourner à
# contact@seamtech.tn » et des champs vides (« Nom : », « Tel : »,
# « N° de commande : » sans valeur). Ce ne sont PAS des fiches de fabrication :
# aucun code commande, aucune cote produite. Les confondre avec un « gabarit
# inconnu » enverrait l'opérateur chercher un bug de reconnaissance au lieu du
# document manquant.
_MOTIF_FORMULAIRE_PRISE_DE_COTES = re.compile(r"fiche\s+de\s+prise\s+de\s+cotes")
_MOTIF_RETOUR_FORMULAIRE = re.compile(r"compl[ée]ter\s+et\s+retourner")
_MOTIF_CODE_DANS_FORMULAIRE = re.compile(r"\b[A-Z]{2}\s?\d{2,5}[A-Za-z]?\b")


def _diagnostic_formulaire_prise_de_cotes(chemin: Path) -> str | None:
    """Décrit le formulaire de prise de cotes, ou ``None`` si ce n'en est pas un.

    Lecture seule, jamais d'exception propagée : un PDF illisible n'est pas un
    formulaire, l'appelant garde alors sa raison de refus habituelle.
    """
    try:
        texte = texte_normalise(analyser_pdf(chemin))
    except Exception:  # PDF illisible : ce n'est pas à cette fonction de le dire
        return None
    if not _MOTIF_FORMULAIRE_PRISE_DE_COTES.search(texte):
        return None
    if not _MOTIF_RETOUR_FORMULAIRE.search(texte):
        return None
    if _MOTIF_CODE_DANS_FORMULAIRE.search(texte.replace("ST ", "ST ")):
        return None
    return "formulaire de prise de cotes (document à remplir, sans code commande ni cote de fabrication)"


# Signature MESURÉE des planches de tracé SailPack (BSG Développements) :
# `/VOILES 2026/ATHENA 44 ST761/JW26050B.pdf`, `/VOILES 2026/AURA 51 ST795/
# 20260410 Gennak Aura51.pdf`, `/WESTERLY PENTLAND ST660B/ST 660b.pdf`. Le
# texte porte « Panel layout » et le pied de page « S a i l P a c k V3.0 »
# (lettres espacées). Ce sont des PLANS de panneaux/patches : ni code de
# fiche, ni cotes de fiche. Mesuré : score 0 sur tous les gabarits.
_MOTIF_PLANCHE_SAILPACK = re.compile(r"s\s*a\s*i\s*l\s*p\s*a\s*c\s*k", re.IGNORECASE)
_MOTIF_PANEL_LAYOUT = re.compile(r"panel\s+layout", re.IGNORECASE)


def _est_planche_sailpack(chemin: Path) -> bool:
    """Vrai si le PDF est une planche de tracé SailPack (jamais une fiche)."""
    try:
        texte = texte_normalise(analyser_pdf(chemin))
    except Exception:  # sans couche texte : classé ailleurs, pas ici
        return False
    return bool(_MOTIF_PANEL_LAYOUT.search(texte) and _MOTIF_PLANCHE_SAILPACK.search(texte))


CLASSEMENT_FORMULAIRE = "formulaire de prise de cotes"
CLASSEMENT_PLANCHE = "planche de tracé SailPack (plan de panneaux BSG)"
CLASSEMENT_SANS_TEXTE = "PDF sans couche texte (image ou plan exporté)"


def _raison_documents_non_fiche(classements: dict[str, str]) -> str:
    """Raison de refus qui NOMME ce que contient réellement le dossier.

    Un refus « gabarit inconnu » se lit comme une panne de reconnaissance.
    Sur l'archive réelle, la cause est presque toujours documentaire : le
    dossier ne contient qu'un formulaire de prise de cotes, une planche de
    tracé SailPack ou un PDF sans couche texte. On le dit, fichier par
    fichier, sans jamais inventer de fiche ni modifier un octet (RG13).
    """
    par_classe: dict[str, list[str]] = {}
    for nom, classe in classements.items():
        par_classe.setdefault(classe, []).append(nom)
    phrases = [f"{classe} : {', '.join(sorted(noms))}" for classe, noms in sorted(par_classe.items())]
    suite = ""
    if CLASSEMENT_SANS_TEXTE in par_classe:
        suite = (
            " L'OCR n'est pas appliqué au dépôt de fiches : ces PDF relèvent d'une "
            "REVUE MANUELLE."
        )
    if CLASSEMENT_FORMULAIRE in par_classe:
        suite += (
            " Un formulaire de prise de cotes sert à recueillir les mesures du client : "
            "il ne porte ni code commande ni cote de fabrication."
        )
    return (
        "aucune fiche de fabrication dans ce dossier — contenu réellement reconnu : "
        + " ; ".join(phrases)
        + "."
        + suite
        + " Aucun fichier n'a été modifié ; joindre la fiche de fabrication "
        "(document « …ffab.pdf ») du dossier pour le déposer."
    )


def _tenter_fiche_de_reprise(
    plan: PlanDossier,
    dossier: Path,
    pdfs: list[Path],
    classements: dict[str, str],
    illisibles: list[str] | None = None,
) -> bool:
    """Dernier recours : ne PERDRE aucun dossier réel (voir `reprise.py`).

    Mesuré sur `VOILES 2024` : 21 dossiers réels sur 24 étaient refusés parce
    qu'ils ne contiennent pas de fiche de fabrication (plan SailPack, formulaire
    de prise de cotes, PDF image). Le document existe pourtant, et l'atelier a
    besoin de le retrouver dans l'application. On crée donc une fiche de
    reprise — titre qui dit ce qu'est le document, code tracé avec sa
    provenance, anomalie « fiche de fabrication absente » qui exige la saisie
    manuelle — au lieu de jeter le dossier.

    Un dossier dont AUCUN PDF n'est lisible (fichier corrompu, tronqué) n'entre
    pas : ce n'est pas un document « sans fiche », c'est un fichier abîmé — le
    refus reste la bonne réponse, avec ses scores.
    """
    if illisibles and not classements:
        return False
    code_dossier = code_depuis_nom_dossier(dossier.name)
    code_fichier = None
    for pdf in pdfs:
        code_fichier = code_depuis_nom_fichier(pdf.name)
        if code_fichier:
            break
    fiche = construire_fiche_de_reprise(
        dossier, pdfs, classements, code_dossier=code_dossier, code_fichier=code_fichier
    )
    if fiche is None:
        return False
    reference = next(
        (pdf for pdf in pdfs if classements.get(pdf.name) == CLASSEMENT_PLANCHE),
        pdfs[0],
    )
    plan.pdf_fiche = reference
    # ``fichier_source`` est la colonne que l'écran de validation lit pour
    # afficher/ouvrir le PDF de la fiche (`nb_fichiers`, `a_pdf`). Sans elle,
    # une fiche de reprise apparaissait SANS document — vérifié sur `ST63`
    # (`nb_fichiers: 0`) alors que le PDF était bien promu dans le stockage.
    if not fiche.fichier_source:
        fiche.fichier_source = reference.name
    fiche.fichier_pdf = str(reference)
    plan.gabarit_code = fiche.gabarit_code
    plan.score = 0
    plan.fiche_extraite = fiche
    plan.raison_refus = None
    plan.pieces.extend(
        FichierDossier(chemin=pdf, role="piece_jointe", taille_octets=pdf.stat().st_size)
        for pdf in pdfs
        if pdf != reference
    )
    LOGGER.info(
        "Dossier %s : aucune fiche de fabrication — FICHE DE REPRISE créée depuis %s "
        "(code %s, titre « %s ») ; %d PDF attaché(s) en pièces.",
        dossier,
        reference.name,
        fiche.code,
        fiche.titre,
        len(pdfs) - 1,
    )
    return True


def _nom_evoque_ffab(chemin: Path) -> bool:
    """Indice de nom de fichier seulement : jamais une preuve de contenu."""
    tige = chemin.stem.casefold()
    return tige.endswith("ffab") or "ffab" in re.split(r"[^a-z0-9]+", tige)


def _groupes_preuve_fiche(fiche: FicheExtraite) -> set[str]:
    """Groupes de structure réellement lus, hors code de fiche."""
    champs = {
        champ.champ
        for champ in fiche.tous_les_champs()
        if champ.valeur_normalisee not in (None, "")
    }

    def _a_prefixe(*prefixes: str) -> bool:
        return any(champ.startswith(prefixe) for champ in champs for prefixe in prefixes)

    groupes: set[str] = set()
    if (
        fiche.client_nom or fiche.bateau_nom or fiche.commande_numero
        or _a_prefixe("fiche.client", "fiche.bateau", "fiche.commande", "accessoire.modele_bateau")
    ):
        groupes.add("identite")
    if (
        fiche.type_voile_libelle or fiche.gamme
        or _a_prefixe("fiche.designation", "fiche.titre", "accessoire.type")
    ):
        groupes.add("type")
    if fiche.cotes or fiche.mesures_libres or _a_prefixe("cotes.", "libre.", "accessoire.dimensions"):
        groupes.add("mesures")
    if (
        fiche.materiaux or fiche.tissu_texte
        or _a_prefixe("materiau.", "fiche.tissu_texte", "accessoire.matiere", "accessoire.couleur")
    ):
        groupes.add("matieres")
    if (
        fiche.galons or fiche.jonctions or fiche.finitions or fiche.options or fiche.renforts
        or fiche.montage_type or fiche.montage_fil
        or _a_prefixe("galon.", "jonction.", "finition.", "option", "renfort", "fiche.montage", "accessoire.quantite")
    ):
        groupes.add("fabrication")
    return groupes


#: Référence de fiche dans un NOM DE FICHIER. Deux formes observées dans les
#: archives de l'atelier : la référence en tête (« ZZ 075a_ffab.pdf ») et la
#: référence de commande en fin de nom (« fiche GV 250329 AJA.pdf »). Motif
#: volontairement étroit : quelques lettres puis 2 à 5 chiffres, avec une
#: lettre finale facultative. Aucune valeur n'est inventée : sans motif, il
#: n'y a pas de repli.
#: ``(?![A-Za-z0-9])`` et non ``\b`` : dans « ZZ 075a_ffab », le souligné
#: est un caractère de mot, donc ``\b`` ne se déclenche PAS après le « a ».
_RE_CODE_NOM_FICHIER_TETE = re.compile(
    r"^\s*(?P<code>[A-Za-z]{1,4}\s?\d{2,5}[A-Za-z]?)(?![A-Za-z0-9])"
)
_RE_CODE_NOM_FICHIER_QUEUE = re.compile(
    r"(?<![A-Za-z0-9])(?P<code>\d{3,6}[\s-][A-Za-z]{2,4})(?![A-Za-z0-9])"
)


def code_depuis_nom_fichier(nom: str) -> str | None:
    """Référence de fiche déductible d'un nom de fichier, ou ``None``.

    Le code d'une fiche est une DONNÉE LUE dans le document ; le nom du
    fichier n'est qu'un repli explicite, utilisé quand le PDF n'en porte
    aucun (mise en page sans ligne « Fichier », formulaire sans référence).
    La provenance est conservée dans la trace du champ, jamais maquillée.
    """
    tige = Path(nom).stem
    tete = _RE_CODE_NOM_FICHIER_TETE.match(tige)
    if tete:
        return re.sub(r"\s+", " ", tete.group("code").strip().upper())
    queue = _RE_CODE_NOM_FICHIER_QUEUE.search(tige)
    if queue:
        return re.sub(r"\s+", " ", queue.group("code").strip().upper())
    return None


#: Référence dans un NOM DE DOSSIER d'affaire. Les archives de l'atelier
#: nomment le dossier d'une affaire « BATEAU TAILLE RÉFÉRENCE » (« … ST103 »,
#: « … ST67A ») ou « BATEAU - jjmmaannée initiales » (« … 250821JA »). Ces
#: dossiers contiennent souvent une fiche dont NI le PDF ni le nom de fichier
#: ne portent la référence : sans cette source, l'écriture était refusée et le
#: dossier « ne rentrait pas » alors que sa référence était sous les yeux.
_RE_CODE_NOM_DOSSIER = re.compile(
    r"(?<![A-Za-z0-9])(?P<code>[A-Za-z]{1,4}\s?\d{2,5}[A-Za-z]?)(?![A-Za-z0-9])"
)
_RE_CODE_NOM_DOSSIER_DATE = re.compile(
    r"(?<![A-Za-z0-9])(?P<code>\d{6}\s?[A-Za-z]{2,4})(?![A-Za-z0-9])"
)


def code_depuis_nom_dossier(nom: str) -> str | None:
    """Référence d'affaire déductible d'un NOM DE DOSSIER, ou ``None``.

    Dernière source de la chaîne (document → fichier → dossier). Motif étroit :
    des lettres puis 2 à 5 chiffres (« ST103 », « ST67A ») ou une date de
    commande suivie d'initiales (« 250821JA »). Le dernier motif du nom gagne,
    c'est celui qui porte la référence dans la convention de l'atelier.
    """
    candidats = [m.group("code") for m in _RE_CODE_NOM_DOSSIER.finditer(nom)]
    candidats += [m.group("code") for m in _RE_CODE_NOM_DOSSIER_DATE.finditer(nom)]
    if not candidats:
        return None
    return re.sub(r"\s+", " ", candidats[-1].strip().upper())


def reprendre_code_depuis_nom(
    fiche: FicheExtraite, pdf: Path, dossier: Path | str | None = None
) -> str | None:
    """Complète le code d'une fiche : document, puis fichier, puis dossier.

    Ne touche jamais une fiche qui a déjà un code (le document fait foi). La
    provenance de la valeur retenue est TOUJOURS tracée
    (``methode="nom_fichier"`` ou ``"nom_dossier"``) avec une confiance
    inférieure à une lecture — l'approbateur voit d'où vient la référence et
    peut la corriger. Sans aucune des trois sources, rend ``None`` : rien
    n'est inventé.
    """
    if (fiche.code or "").strip():
        return fiche.code
    sources: list[tuple[str, str, float]] = []
    nom_fichier = Path(pdf).name
    code_fichier = code_depuis_nom_fichier(nom_fichier)
    if code_fichier:
        sources.append((code_fichier, nom_fichier, 0.75))
    if dossier is not None:
        nom_dossier = Path(dossier).name
        code_dossier = code_depuis_nom_dossier(nom_dossier)
        if code_dossier:
            sources.append((code_dossier, nom_dossier, 0.60))
    if not sources:
        return None
    code, brut, confiance = sources[0]
    fiche.code = code
    fiche.champs.append(
        ChampExtrait(
            champ="fiche.code",
            valeur_brute=brut,
            valeur_normalisee=code,
            methode="nom_fichier" if brut == nom_fichier else "nom_dossier",
            confiance=confiance,
            page=0,
            zone=None,
            table_cible="fiche",
            colonne_cible="code",
        )
    )
    return code


def _extraction_viable(fiche: FicheExtraite) -> bool:
    """Une fiche persistable : code + gabarit + au moins deux groupes lus.

    Un code isolé (mauvaise famille, imprimé de commande…) ne suffit donc
    jamais à voler le rôle de PDF principal à une vraie fiche du dossier.
    """
    return bool(fiche.code and fiche.code.strip() and fiche.gabarit_code and len(_groupes_preuve_fiche(fiche)) >= 2)


def _extraction_selectionnable(fiche: FicheExtraite) -> bool:
    """Repli historique du scanner quand aucune fiche n'est pleinement viable.

    Un formulaire riche sans code lisible reste le PDF principal (le dépôt
    refusera ensuite explicitement « fiche sans code »), et un code assorti
    d'au moins un groupe de structure reste persistable. En revanche, un
    code seul — ou une seule trace de fabrication sans code — ne désigne
    pas une fiche : le dossier est refusé dès le scan, avec sa raison.
    """
    groupes = len(_groupes_preuve_fiche(fiche))
    if not fiche.gabarit_code:
        return False
    if fiche.code and fiche.code.strip():
        return groupes >= 1
    return groupes >= 2


def scanner_dossier(dossier: Path, gabarits: list[GabaritDef]) -> PlanDossier:
    """Repère le PDF de fiche et les pièces jointes d'un dossier — SANS écriture.

    Les PDF sont classés par score d'ancres (le nom « …ffab.pdf » ne sert
    qu'à départager les égalités), puis le premier qui produit une fiche
    VIABLE devient le PDF principal ; les autres fichiers deviennent des
    pièces jointes. Un dossier sans fiche viable est un REFUS documenté.
    """
    dossier = Path(dossier)
    if not dossier.is_dir():
        raise DepotImpossible(f"Dossier introuvable ou non-directory : {dossier}")
    if not gabarits:
        raise DepotImpossible(
            "aucun gabarit actif en base (table gabarit vide ou tout inactif) — "
            "exécutez la CLI d'initialisation des gabarits ; conséquence : "
            "aucune détection n'est possible."
        )
    plan = PlanDossier(dossier=dossier)
    pdfs: list[Path] = []
    for element in sorted(dossier.iterdir(), key=lambda chemin: chemin.name):
        if not element.is_file():
            continue
        if element.suffix.lower() == ".pdf":
            pdfs.append(element)
            continue
        plan.pieces.append(
            FichierDossier(
                chemin=element,
                role=role_piece(element),
                taille_octets=element.stat().st_size,
            )
        )
    plan.pieces.extend(_pieces_des_sous_dossiers(dossier))
    if not pdfs:
        plan.raison_refus = "aucun PDF dans le dossier"
        return plan

    candidats: list[tuple[Path, Any]] = []
    scores: dict[str, int] = {}
    illisibles: list[str] = []
    detection_mixte = False
    sans_texte: list[str] = []
    #: nom du PDF → ce qu'il est RÉELLEMENT quand ce n'est pas une fiche.
    #: Sert à écrire un refus qui décrit le dossier au lieu de « gabarit inconnu ».
    classements: dict[str, str] = {}
    for pdf in pdfs:
        try:
            candidat = analyser_candidat_fiche(pdf, gabarits)
        except DetectionMixteIndeterminee:
            scores[pdf.name] = -1
            detection_mixte = True
            continue
        except Exception as erreur:  # PDF illisible : raison explicite, pas de crash
            scores[pdf.name] = -1
            # Deux causes très différentes derrière « lecture impossible » :
            # un fichier abîmé, ou un PDF SANS COUCHE TEXTE (plan exporté du
            # traceur, scan, photo de fiche). Mesuré sur l'archive réelle :
            # `/ACCESSOIRES 2026/CAGNARD ST756/*.pdf`, `/TOILE ST792 COUPE.pdf`,
            # `/ACCESSOIRES 2025/…/TAUD DE GV.pdf` — 0 caractère extrait.
            # Le second cas n'est pas une panne : c'est une revue manuelle
            # (le module OCR existe mais n'est pas branché sur le dépôt de
            # fiches). Les confondre ferait chercher un bug inexistant.
            if "sans texte lisible" in str(erreur):
                sans_texte.append(pdf.name)
                classements[pdf.name] = CLASSEMENT_SANS_TEXTE
                LOGGER.warning(
                    "PDF sans couche texte (%s) : exclu de la détection — conséquence : revue manuelle.",
                    type(erreur).__name__,
                )
            else:
                illisibles.append(pdf.name)
                LOGGER.warning(
                    "PDF illisible (%s) : aucun texte extrait — conséquence : exclu de la détection.",
                    type(erreur).__name__,
                )
            # Ni le chemin ni le message de l'exception ne sont journalisés :
            # l'un porte le nom du client (nommage des dossiers de l'atelier),
            # l'autre peut recopier un extrait du document. La cause est dite
            # par le type d'exception et la catégorie, jamais par son contenu.
            continue
        scores[pdf.name] = 0 if candidat is None else candidat.score_detection
        if _diagnostic_formulaire_prise_de_cotes(pdf) is not None:
            classements[pdf.name] = CLASSEMENT_FORMULAIRE
            # Un formulaire de prise de cotes n'est JAMAIS une fiche : il ne
            # doit donc pas rester candidat, même quand un gabarit le reconnaît
            # faiblement. Mesuré sur `/VOILES 2024/NS44 ST88` : le formulaire
            # « NS44 GV.pdf » était retenu par le gabarit accessoire et une
            # fiche « pour » (mot lu dans le formulaire) apparaissait en base.
            candidats = [paire for paire in candidats if paire[0] != pdf]
            scores[pdf.name] = 0
            continue
        elif _est_planche_sailpack(pdf):
            classements[pdf.name] = CLASSEMENT_PLANCHE
        if candidat is not None:
            candidats.append((pdf, candidat))

    if not candidats:
        detail = ", ".join(f"{nom}={score}" for nom, score in scores.items())
        if sans_texte and not illisibles and len(sans_texte) == len(scores):
            plan.raison_refus = (
                "PDF sans couche texte (image ou plan exporté) : "
                f"{', '.join(sans_texte)} — l'OCR n'est pas encore appliqué au dépôt de "
                "fiches : ce dossier relève d'une REVUE MANUELLE. Aucun fichier n'a été "
                "modifié ; si la fiche existe en PDF texte (« …ffab.pdf »), la joindre au dossier."
            )
        elif detection_mixte:
            plan.raison_refus = "Détection incertaine : orientations mixtes ou non vérifiées, relecture complète nécessaire."
        elif illisibles:
            # MAN-04 : dire la cause lisible par l'opérateur d'abord, les
            # scores ensuite (diagnostic) — « gabarit inconnu » seul laissait
            # croire à un PDF sain d'un format nouveau.
            detail_manque = (
                f" ; PDF sans couche texte (revue manuelle) : {', '.join(sans_texte)}"
                if sans_texte else ""
            )
            plan.raison_refus = (
                f"PDF illisible : {', '.join(illisibles)} (lecture impossible)"
                f"{detail_manque} (scores de détection : {detail})"
            )
        elif classements and len(classements) == len(scores):
            # TOUS les PDF examinés sont identifiés comme autre chose qu'une
            # fiche : ce n'est pas un défaut de reconnaissance. On nomme le
            # contenu réel (formulaire, planche SailPack, PDF sans texte).
            plan.raison_refus = _raison_documents_non_fiche(classements)
        else:
            # « gabarit inconnu » seul se lit comme une panne. La cause réelle
            # est presque toujours documentaire : le dossier ne contient qu'un
            # plan, une facture ou un PDF sans libellé de fiche. On le dit avec
            # les fichiers réellement examinés, et l'on rappelle que rien n'a
            # été modifié (RG13).
            examines = ", ".join(f"{nom} (score {score})" for nom, score in scores.items())
            plan.raison_refus = (
                "aucune fiche de fabrication reconnue dans ce dossier : aucun PDF ne "
                f"porte les libellés d'une fiche (gabarit inconnu) — PDF examinés : {examines}. "
                "Les fichiers n'ont pas été modifiés ; vérifier que le dossier contient bien "
                "la fiche technique, et non seulement un plan ou une pièce jointe."
            )
        if _tenter_fiche_de_reprise(plan, dossier, pdfs, classements, illisibles):
            return plan
        return plan

    # Preuves d'abord : meilleur score d'ancres, puis — à score égal
    # seulement — le nom évoquant une fiche de fabrication (« …ffab.pdf »),
    # puis l'ordre alphabétique stable. Le premier candidat VIABLE gagne :
    # un PDF mieux noté mais sans code/structure ne bloque plus le dossier.
    candidats_tries = sorted(
        candidats,
        key=lambda paire: (
            -paire[1].score_detection,
            not _nom_evoque_ffab(paire[0]),
            paire[0].name.casefold(),
        ),
    )
    choisi = next((paire for paire in candidats_tries if _extraction_viable(paire[1].fiche)), None)
    if choisi is None:
        choisi = next(
            (paire for paire in candidats_tries if _extraction_selectionnable(paire[1].fiche)),
            None,
        )
    if choisi is None:
        if detection_mixte or any(
            anomalie.code in {"orientations_pdf_mixtes", "orientation_pdf_non_verifiee"}
            for _, candidat in candidats_tries for anomalie in candidat.fiche.anomalies
        ):
            plan.raison_refus = "Détection incertaine : orientations mixtes ou non vérifiées, relecture complète nécessaire."
            return plan
        meilleur_pdf, meilleur_candidat = candidats_tries[0]
        plan.gabarit_code = meilleur_candidat.gabarit.code
        plan.score = meilleur_candidat.score_detection
        detail = ", ".join(f"{nom}={score}" for nom, score in scores.items())
        if classements and len(classements) == len(scores):
            plan.raison_refus = _raison_documents_non_fiche(classements)
        else:
            plan.raison_refus = (
                "aucun PDF candidat n'a produit une fiche sélectionnable (code + structure, "
                f"ou structure riche sans code ; meilleur candidat : {meilleur_pdf.name} ; scores : {detail})"
            )
        if _tenter_fiche_de_reprise(plan, dossier, pdfs, classements, illisibles):
            return plan
        return plan

    pdf_choisi, candidat_choisi = choisi
    plan.autres_fiches = [
        pdf for pdf, candidat in candidats_tries
        if pdf != pdf_choisi and _extraction_viable(candidat.fiche)
    ]
    if plan.autres_fiches:
        LOGGER.warning(
            "Dossier %s : %d autre(s) PDF portent aussi une fiche exploitable (%s) — "
            "fiche retenue : %s ; les autres sont conservés en pièces jointes et "
            "cités dans le résultat du dépôt (aucun fichier n'est supprimé).",
            dossier,
            len(plan.autres_fiches),
            ", ".join(pdf.name for pdf in plan.autres_fiches),
            pdf_choisi.name,
        )
    plan.pdf_fiche = pdf_choisi
    plan.gabarit_code = candidat_choisi.gabarit.code
    plan.score = candidat_choisi.score_detection
    plan.fiche_extraite = candidat_choisi.fiche
    # les autres PDF (non reconnus ou seconds) restent des pièces jointes
    plan.pieces.extend(
        FichierDossier(chemin=pdf, role="piece_jointe", taille_octets=pdf.stat().st_size)
        for pdf in pdfs
        if pdf != pdf_choisi
    )
    return plan


def _pieces_des_sous_dossiers(dossier: Path) -> list[FichierDossier]:
    """Fichiers des sous-dossiers SANS PDF direct, rattachés comme pièces.

    Mesuré sur l'archive réelle : `/YACHET CLASSIC ST508/TISSU` (échantillons),
    `/LAZY BAG 2 PARTIES DUFOUR 385/PLC` (fichiers du traceur),
    `/ACCESSOIRES 2026/TOILE ST792/toile`. Ces sous-dossiers ne portent AUCUN
    PDF : ils ne deviennent donc jamais un dossier de lot à eux seuls, et leurs
    fichiers n'étaient référencés par RIEN — invisibles depuis l'application
    alors qu'ils font partie de l'affaire (RG13 : on ne perd rien).

    Un sous-dossier qui porte un PDF est laissé de côté : il est déjà un
    dossier de lot à part entière, ses pièces lui appartiennent — les compter
    ici les dupliquerait. Les fichiers cachés sont ignorés, comme partout
    ailleurs dans la découverte des dossiers.
    """
    pieces: list[FichierDossier] = []
    for sous_dossier in sorted(
        (element for element in dossier.iterdir() if element.is_dir() and not element.name.startswith(".")),
        key=lambda chemin: chemin.name.casefold(),
    ):
        if any(fichier.suffix.lower() == ".pdf" for fichier in sous_dossier.rglob("*") if fichier.is_file()):
            continue
        for fichier in sorted(sous_dossier.rglob("*"), key=lambda chemin: str(chemin).casefold()):
            if not fichier.is_file() or fichier.name.startswith("."):
                continue
            pieces.append(
                FichierDossier(
                    chemin=fichier,
                    role=role_piece(fichier),
                    taille_octets=fichier.stat().st_size,
                )
            )
    return pieces


def _creer_lot_isole(index: Any, dossier: Path) -> tuple[int, int]:
    """Lot à une ligne pour un dossier déposé isolément (traçabilité de TOUTE
    tentative, y compris les refus) — sa propre petite transaction : la couche
    de SUIVI est distincte de la transaction fiche (tout-ou-rien)."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_LOT_INS, (str(dossier), "dossier isolé (POST /imports/dossier)"))
            id_lot = int(cursor.fetchone()[0])
            cursor.execute(_SQL_LOT_DOSSIERS_INS, (id_lot, str(dossier)))
            cursor.execute(_SQL_LOT_COMPTE, (id_lot,))
            cursor.execute(_SQL_LOT_MAJ, (int(cursor.fetchone()[0]), "en_cours", "en_cours", id_lot))
            cursor.execute(
                "SELECT id_lot_dossier FROM lot_dossier WHERE id_lot = %s AND chemin_dossier = %s",
                (id_lot, str(dossier)),
            )
            return id_lot, int(cursor.fetchone()[0])


def _maj_ligne(index: Any, id_lot_dossier: int, statut: str, raison: str | None, id_fiche: int | None, cle: str | None, nb_pieces: int, *, claim: tuple[str, str] | None = None) -> None:
    """Met à jour la ligne de lot (hors transaction fiche : simple suivi)."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            _verrouiller_claim(cursor, claim)
            cursor.execute(_SQL_LIGNE_MAJ, (statut, raison, id_fiche, cle, nb_pieces, id_lot_dossier))


def _verrouiller_document_existant(cursor: Any, chemin: Path) -> None:
    """Réserve le chemin et sérialise l'adoption avec les nouvelles références FK.

    Une ligne absente ne peut pas être verrouillée ; l'insertion provisoire,
    annulée avec la transaction en cas d'échec, réserve aussi ce cas.
    """
    cle = os.path.normcase(str(chemin.resolve()))
    stat = chemin.stat()
    cursor.execute(
        "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir, extraction_status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, 'metadata') ON CONFLICT (path_key) DO NOTHING",
        (cle, str(chemin), chemin.name, str(chemin.parent), chemin.suffix.lower(), stat.st_size, stat.st_mtime),
    )
    cursor.execute(
        "SELECT id FROM documents WHERE path_key = %s FOR UPDATE",
        (cle,),
    )
    cursor.fetchone()


def _lier_objet_promu(cursor: Any, id_document: int, chemin: Path, empreinte: str, objets_promus: dict[str, dict[str, str]] | None, *, remplacer_cle: bool = False) -> None:
    """Lie une copie déjà vérifiée à son document dans la transaction métier."""
    if objets_promus is None:
        return
    objet = objets_promus.get(str(chemin.resolve()))
    if not objet or not objet.get("object_key") or not objet.get("object_bucket"):
        raise DepotImpossible("Une copie objet vérifiée manque pour ce dossier.")
    if objet.get("sha256") != empreinte or empreinte_fichier(chemin) != empreinte:
        raise DepotImpossible("L'intégrité d'un fichier a changé après sa promotion.")
    cursor.execute(
        "UPDATE documents SET object_key = %s, object_bucket = %s, upload_status = 'uploaded', "
        "uploaded_at = EXTRACT(EPOCH FROM now()) WHERE id = %s AND content_hash = %s "
        "AND (%s OR object_key IS NULL OR (object_key = %s AND object_bucket = %s))",
        (objet["object_key"], objet["object_bucket"], id_document, empreinte, remplacer_cle, objet["object_key"], objet["object_bucket"]),
    )
    if cursor.rowcount != 1:
        raise DepotImpossible("Le document ou son objet a changé pendant le dépôt.")


def _comparer_pieces_rejeu_curseur(cursor: Any, id_ligne_precedente: int, plan: PlanDossier, empreinte_pdf: str) -> tuple[bool, bool, tuple[tuple[str, str], ...]]:
    if plan.pdf_fiche is None:
        raise DepotImpossible("Rejeu sans PDF source.")
    parent = Path(plan.pdf_fiche).parent
    parent_logique = _chemin_logique_televersement(parent)
    cursor.execute("SELECT id_fiche FROM lot_dossier WHERE id_lot_dossier = %s", (id_ligne_precedente,))
    ligne = cursor.fetchone()
    if not ligne or not ligne[0]:
        raise DepotImpossible("Rejeu impossible : fiche précédente non identifiée.")
    cursor.execute("SELECT cle_idempotence FROM lot_dossier WHERE id_fiche = %s AND statut = 'traite' "
                   "AND cle_idempotence IS NOT NULL ORDER BY traite_le DESC, id_lot_dossier DESC LIMIT 1", (ligne[0],))
    source_courante = cursor.fetchone()
    if not source_courante or str(source_courante[0]).rsplit(SEPARATEUR_CLE, 1)[-1] != empreinte_pdf:
        raise DepotImpossible("Rejeu historique : la fiche courante a changé ; revue nécessaire.")
    cursor.execute("SELECT chemin, empreinte_sha256 FROM fiche_piece_jointe WHERE id_fiche = %s", (ligne[0],))
    anciennes = cursor.fetchall()

    def identite(chemin: Path) -> str:
        logique = _chemin_logique_televersement(chemin)
        return logique if logique is not None else str(chemin.resolve())

    precedentes = {
        identite(Path(str(chemin))): str(empreinte or "")
        for chemin, empreinte in anciennes
        if (_chemin_logique_televersement(Path(str(chemin)).parent) == parent_logique
            if parent_logique is not None else Path(str(chemin)).parent.resolve() == parent.resolve())
    }
    nouvelles = {identite(Path(p.chemin)): p.empreinte_sha256 for p in plan.pieces}
    return precedentes != nouvelles, bool(precedentes.keys() - nouvelles.keys()), tuple(sorted(precedentes.items()))


def _comparer_pieces_rejeu(index: Any, id_ligne_precedente: int, plan: PlanDossier, empreinte_pdf: str) -> tuple[bool, bool, tuple[tuple[str, str], ...]]:
    """Détecte les changements et retraits dans le dossier logique rejoué."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            return _comparer_pieces_rejeu_curseur(cursor, id_ligne_precedente, plan, empreinte_pdf)


def _lier_rejeu_objets(index: Any, id_ligne_precedente: int, id_ligne_courante: int, id_lot_precedent: int, plan: PlanDossier, empreinte_pdf: str, objets_promus: dict[str, dict[str, str]] | None, *, claim: tuple[str, str] | None = None, verifier_claim_externe: Callable[[], bool] | None = None) -> None:
    """Vérifie la fiche verrouillée et publie le rejeu dans une seule transaction."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            _verrouiller_claim(cursor, claim)
            cursor.execute("SELECT id_fiche, cle_idempotence FROM lot_dossier WHERE id_lot_dossier = %s", (id_ligne_precedente,))
            ligne = cursor.fetchone()
            if not ligne or not ligne[0] or not ligne[1] or plan.pdf_fiche is None:
                raise DepotImpossible("Rejeu impossible : document précédent non identifié.")
            cursor.execute("SELECT id_fiche FROM fiche WHERE id_fiche = %s FOR UPDATE", (ligne[0],))
            if not cursor.fetchone():
                raise DepotImpossible("Rejeu impossible : fiche précédente absente.")
            modifiees, _, _ = _comparer_pieces_rejeu_curseur(cursor, id_ligne_precedente, plan, empreinte_pdf)
            if modifiees:
                raise DepotImpossible("Rejeu impossible : pièces modifiées pendant la vérification.")
            if objets_promus is not None:
                ancien_pdf = str(ligne[1]).rsplit(SEPARATEUR_CLE, 1)[0]
                cursor.execute("SELECT id FROM documents WHERE path_key = %s AND content_hash = %s", (os.path.normcase(ancien_pdf), empreinte_pdf))
                document = cursor.fetchone()
                if not document:
                    raise DepotImpossible("Rejeu impossible : PDF précédent non identifié.")
                _lier_objet_promu(cursor, int(document[0]), Path(plan.pdf_fiche), empreinte_pdf, objets_promus, remplacer_cle=True)
                cursor.execute("SELECT id_document, chemin, empreinte_sha256 FROM fiche_piece_jointe WHERE id_fiche = %s", (ligne[0],))
                anciennes = cursor.fetchall()
                dossier_logique = _chemin_logique_televersement(Path(plan.pdf_fiche).parent)
                anciennes = [p for p in anciennes if (
                    _chemin_logique_televersement(Path(str(p[1])).parent) == dossier_logique
                    if dossier_logique is not None else Path(str(p[1])).parent.resolve() == Path(plan.pdf_fiche).parent.resolve()
                )]
                for piece in plan.pieces:
                    chemin = Path(piece.chemin)
                    logique = _chemin_logique_televersement(chemin)
                    candidats = [p for p in anciennes if p[0] is not None and str(p[2] or "") == piece.empreinte_sha256
                                 and ((_chemin_logique_televersement(str(p[1])) == logique) if logique is not None
                                      else Path(str(p[1])).resolve() == chemin.resolve())]
                    if len(candidats) != 1:
                        raise DepotImpossible("Rejeu impossible : pièces jointes différentes ou ambiguës.")
                    _lier_objet_promu(cursor, int(candidats[0][0]), chemin, piece.empreinte_sha256, objets_promus, remplacer_cle=True)
                if len(anciennes) != len(plan.pieces):
                    raise DepotImpossible("Rejeu impossible : nombre de pièces jointes différent.")
            if verifier_claim_externe is not None and not verifier_claim_externe():
                raise DepotClaimPerdu("Le worker ne détient plus le claim externe.")
            cursor.execute(_SQL_LIGNE_MAJ, ("traite", f"deja_traite (lot #{id_lot_precedent})", None, None, len(plan.pieces), id_ligne_courante))


def deposer_dossier(
    index: Any,
    dossier: Path,
    gabarits: list[GabaritDef] | None = None,
    id_lot: int | None = None,
    id_lot_dossier: int | None = None,
    *,
    objets_promus: dict[str, dict[str, str]] | None = None,
    claim: tuple[str, str] | None = None,
    verifier_claim_externe: Callable[[], bool] | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    """Dépose UN dossier : détecte, extrait, écrit fiche + pièces jointes en UNE
    transaction, puis met à jour la ligne de lot. Retourne un RÉSULTAT (jamais
    d'exception métier) :
    ``{"statut": "traite"|"echec"|"deja_traite", "fiche": code|None, "raison": …, "id_lot": …}``.

    ``gabarits`` : registre (chargé de base si absent). Sans ``id_lot_dossier``,
    l'opération devient son propre lot suivi (refus compris).

    ``config`` : configuration d'exécution. Quand elle porte un stockage objet,
    les OCTETS du PDF de fiche et de ses pièces jointes sont promus vers
    MinIO/S3/R2 après le commit (voir :mod:`seamtech_search.fiches.promotion`).
    Sans ``config`` — appel interne hors API — le dépôt reste strictement local
    et la promotion est laissée à ``promouvoir_en_attente``.
    """
    if not index.is_postgres:
        raise DepotImpossible("Le dépôt de dossier exige PostgreSQL (tables fiche_*, §17.1).")
    gabarits = gabarits if gabarits is not None else charger_gabarits(index)
    id_lot_local = id_lot
    isolé = id_lot_dossier is None and id_lot is None  # dossier déposé hors lot

    def _enregistrer_echec(raison: str) -> dict[str, Any]:
        nonlocal id_lot_local, id_lot_dossier
        LOGGER.warning("Dossier refusé — conséquence : dossier listé en échec, le lot continue.")
        lot_isole_cree = False
        if id_lot_dossier is None:
            id_lot_local, id_lot_dossier = _creer_lot_isole(index, Path(dossier))
            lot_isole_cree = True
        _maj_ligne(index, id_lot_dossier, "echec", raison, None, None, 0, claim=claim)
        _rafraichir_compteurs(index, id_lot_local, claim=claim)
        if isolé or lot_isole_cree:
            _finaliser_lot(index, id_lot_local)
        return {"statut": "echec", "fiche": None, "raison": raison, "pieces": 0, "id_lot": id_lot_local}

    try:
        plan = scanner_dossier(Path(dossier), gabarits)
    except DepotImpossible as erreur:
        LOGGER.error("Dépôt refusé (%s) — conséquence : dossier compté en échec.", type(erreur).__name__)
        raison = str(erreur)
        gabarit_absent = (
            "aucun gabarit actif en base (table gabarit vide ou tout inactif) — "
            "exécutez la CLI d'initialisation des gabarits ; conséquence : "
            "aucune détection n'est possible."
        )
        return _enregistrer_echec(raison if raison == gabarit_absent else "dossier impossible à analyser")
    if not plan.accepte or plan.pdf_fiche is None:
        return _enregistrer_echec(plan.raison_refus or "dossier non reconnu")

    empreinte = empreinte_fichier(plan.pdf_fiche)
    cle = cle_idempotence(plan.pdf_fiche, empreinte)
    if id_lot_dossier is None:
        id_lot_local, id_lot_dossier = _creer_lot_isole(index, Path(dossier))
    try:
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(_SQL_DEJA_TRAITE, (cle,))
                existant = cursor.fetchone()
                cle_deja_utilisee = existant is not None
                if existant is None:
                    # Rejeu téléversé dans une NOUVELLE session (MAN-02) :
                    # le jeton change, donc la clé absolue aussi — mais le
                    # chemin relatif envoyé et l'empreinte sont identiques.
                    # Sans cette reconnaissance, le rejeu remplaçait la fiche
                    # et dupliquait ses pièces.
                    logique = _chemin_logique_televersement(plan.pdf_fiche)
                    if logique is not None:
                        cursor.execute(
                            _SQL_CANDIDATS_MEME_EMPREINTE,
                            (f"%{SEPARATEUR_CLE}{empreinte}",),
                        )
                        for id_ligne, id_lot_candidat, cle_candidate in cursor.fetchall():
                            ancien_chemin = str(cle_candidate or "").rsplit(SEPARATEUR_CLE, 1)[0]
                            if _chemin_logique_televersement(ancien_chemin) == logique:
                                existant = (id_ligne, id_lot_candidat)
                                break
        reference_rejeu = None
        empreintes_precedentes = None
        if existant is not None:
            pieces_modifiees, pieces_retires, empreintes_precedentes = _comparer_pieces_rejeu(index, int(existant[0]), plan, empreinte)
            if pieces_retires:
                raise DepotImpossible("Rejeu refusé : des pièces précédentes sont absentes ; revue nécessaire.")
            if pieces_modifiees:
                reference_rejeu = int(existant[0])
                existant = None
        if existant is not None:
            _lier_rejeu_objets(index, int(existant[0]), int(id_lot_dossier), int(existant[1]), plan, empreinte,
                                objets_promus, claim=claim, verifier_claim_externe=verifier_claim_externe)
            LOGGER.info(
                "Dossier déjà traité (lot #%d, ligne #%d) : rejeu sans effet (idempotence).",
                existant[1],
                existant[0],
            )
            # NB : cle_idempotence reste NULL ici — l'index unique partiel
            # interdit DEUX lignes traite avec la même clé, et la ligne
            # originale (lot #{existant[1]}) est la seule qui la porte.
            if id_lot_local is not None:
                _rafraichir_compteurs(index, id_lot_local, claim=claim)
                if isolé:
                    _finaliser_lot(index, id_lot_local)  # un rejeu isolé = un lot qui se termine aussi
            # P0.2 (suite) — les OCTETS du rejeu n'ont aucune raison de rester
            # sur disque : la fiche d'origine a déjà ses fichiers, et le rejeu
            # est identique au SHA-256 près. Sans ce nettoyage, chaque nouvel
            # envoi du même dossier laissait une copie complète sous
            # ``uploads/lots-televerses/<nouveau jeton>/`` que RIEN ne
            # référençait et que la rétention ne pouvait pas élaguer (elle ne
            # touche pas aux sessions de téléversement, par conception).
            # On ne supprime QUE des copies stagées ET dont l'empreinte est
            # encore celle qui a été reconnue : jamais une archive déposée par
            # chemin, jamais un fichier modifié entre-temps.
            copies_supprimees = _supprimer_copies_rejeu(
                index, existant[0], plan.pdf_fiche, plan.pieces, empreinte
            )
            if copies_supprimees:
                LOGGER.info(
                    "Rejeu du dossier %s : %d copie(s) stagée(s) identique(s) supprimée(s) "
                    "(les octets d'origine sont déjà en stockage objet).",
                    dossier,
                    copies_supprimees,
                )
            return {
                "statut": "deja_traite",
                "fiche": None,
                "raison": f"deja_traite (lot #{existant[1]})",
                "pieces": len(plan.pieces),
                "id_lot": id_lot_local,
                "copies_rejeu_supprimees": copies_supprimees,
            }
        # Le code de fiche est l'IDENTITÉ de ce qu'on écrit : sans lui, la
        # transaction lèverait ValueError et le dossier finirait en « écriture
        # annulée : ValueError: Fiche sans code », message illisible pour un
        # opérateur et sans issue. On prépare donc la fiche AVANT d'ouvrir la
        # transaction : repli explicite sur le nom du fichier, puis refus
        # documenté s'il n'existe vraiment aucune référence.
        fiche_preparee = plan.fiche_extraite or extraire_fiche(
            plan.pdf_fiche, gabarits=gabarits, gabarit_code=plan.gabarit_code
        )
        # Le PDF retenu EST le document de la fiche : la colonne
        # ``fichier_source`` alimente l'affichage (`nb_fichiers`, `a_pdf`) de
        # l'écran de validation. Elle restait vide pour les fiches d'accessoires
        # et de reprise — la fiche s'ouvrait alors sans document, alors que le
        # fichier était bien promu. Mesuré sur `ST63`, `ST101`, `ST96`.
        if not fiche_preparee.fichier_source:
            fiche_preparee.fichier_source = Path(plan.pdf_fiche).name
        code_retenu = reprendre_code_depuis_nom(
            fiche_preparee, Path(plan.pdf_fiche), Path(dossier)
        )
        if code_retenu is None:
            return _enregistrer_echec(
                "aucun code de fiche lisible : ni le PDF, ni son nom "
                f"« {Path(plan.pdf_fiche).name} », ni le nom du dossier "
                f"« {Path(dossier).name} » ne portent de référence — la fiche "
                "n'est PAS enregistrée. Renommer le dossier ou le PDF avec sa "
                "référence (exemple : « ZZ 001 ») puis redéposer."
            )
        provenance_code = next(
            (champ.methode for champ in fiche_preparee.champs if champ.champ == "fiche.code"),
            "document",
        )
        if provenance_code != "document":
            # Ni le dossier (nom du client) ni le code de fiche n'ont à figurer
            # dans un journal : la trace destinée à l'approbateur est la
            # provenance portée par le champ lui-même (``champ.methode``),
            # persistée avec la fiche et relue dans l'écran de validation.
            LOGGER.info(
                "Code de fiche repris d'une provenance non documentaire « %s » : trace "
                "de provenance conservée dans les champs pour l'approbateur "
                "(ce n'est pas une lecture du document).",
                provenance_code,
            )
        # L'EXCEPTION doit franchir le « with » pour déclencher le rollback de
        # la connexion : le except est donc VOLONTAIREMENT à l'extérieur —
        # écriture entière ou pas du tout (fiche + pièces jointes).
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                _verrouiller_claim(cursor, claim)
                if reference_rejeu is not None:
                    cursor.execute("SELECT id_fiche FROM lot_dossier WHERE id_lot_dossier = %s", (reference_rejeu,))
                    ligne_reference = cursor.fetchone()
                    if not ligne_reference or not ligne_reference[0]:
                        raise DepotImpossible("Rejeu impossible : fiche précédente non identifiée.")
                    cursor.execute("SELECT id_fiche FROM fiche WHERE id_fiche = %s FOR UPDATE", (ligne_reference[0],))
                    if not cursor.fetchone():
                        raise DepotImpossible("Rejeu impossible : fiche précédente absente.")
                    _, _, empreintes_actuelles = _comparer_pieces_rejeu_curseur(cursor, reference_rejeu, plan, empreinte)
                    if empreintes_actuelles != empreintes_precedentes:
                        raise DepotImpossible("Les pièces ont changé pendant la préparation du dépôt.")
                fiche = fiche_preparee or plan.fiche_extraite or extraire_fiche(
                    plan.pdf_fiche, gabarits=gabarits, gabarit_code=plan.gabarit_code
                )
                # Lot 7 : la catégorie est proposée ICI, explicitement — nom
                # du dossier (« accessoire ») d'abord, famille du gabarit
                # gagnant ensuite. La persistance tranche et ne contredit
                # jamais une catégorie déjà décidée par un humain.
                from seamtech_search.accessoires import proposer_categorie

                categorie_proposee = proposer_categorie(
                    Path(dossier).name, plan.gabarit_code
                )
                # ResultatEcriture : la révision qui accompagne le contenu écrit
                # est publiée au lot (A02) — un dépôt qui REMPLACE une fiche
                # fait avancer sa révision, et le dépôt le DIT.
                ecriture = ecrire_fiche_resultat(
                    index, fiche, connexion=connexion, categorie=categorie_proposee
                )
                id_fiche, action = ecriture.id_fiche, ecriture.action
                revision_fiche = ecriture.revision
                if action == "conservee_validee":
                    raise DepotImpossible("RG11 : fiche validée ; dépôt différent refusé sans modification des pièces.")

                # Le PDF principal a lui aussi un identifiant de catalogue : toutes
                # les vues l'ouvrent par /pieces/{id}, sans transmettre son chemin.
                pdf_fiche = Path(plan.pdf_fiche)
                pdf_stat = pdf_fiche.stat()
                _verrouiller_document_existant(cursor, pdf_fiche)
                cursor.execute(
                    _SQL_DOCUMENT_FICHE_UPSERT,
                    (
                        os.path.normcase(str(pdf_fiche.resolve())),
                        str(pdf_fiche),
                        pdf_fiche.name,
                        str(pdf_fiche.parent),
                        pdf_fiche.suffix.lower(),
                        pdf_stat.st_size,
                        pdf_stat.st_mtime,
                        _texte_indexable_fiche(fiche),
                        empreinte,
                        # Identité des octets, deux fois : à l'insertion, puis
                        # dans la condition de reprise (ordre des marqueurs).
                        empreinte,
                        empreinte,
                    ),
                )
                ligne_document_pdf = cursor.fetchone()
                if ligne_document_pdf is None:
                    raise DepotImpossible("Un PDF existant au même chemin a changé ; revue nécessaire.")
                id_document_pdf = ligne_document_pdf[0]
                _lier_objet_promu(cursor, int(id_document_pdf), pdf_fiche, empreinte, objets_promus)
                cursor.execute(
                    _SQL_DOCUMENT_VECTOR,
                    (index.config_recherche_texte(connexion), id_document_pdf),
                )
                # L'upsert ET le rafraîchissement du vecteur passent par des
                # colonnes surveillées par `documents_invalider_source_sha256` :
                # l'identité est donc rétablie EN DERNIER, sinon elle serait
                # perdue dès ce dépôt et le suivant ne pourrait plus prouver
                # que le fichier n'a pas changé.
                _etablir_identite_octets(cursor, int(id_document_pdf), empreinte)
                # Lots 3a/7 : l'extraction écrite avec la fiche reçoit, dans
                # la même transaction de dépôt, l'identifiant du PDF source
                # et son empreinte SHA-256 — dans la famille RÉELLEMENT
                # écrite (relu en base : une catégorie humaine prime sur la
                # proposition). Aucun chemin d'archive n'est copié.
                cursor.execute(
                    "SELECT categorie FROM fiche WHERE id_fiche = %s", (id_fiche,)
                )
                if str(cursor.fetchone()[0]) == "accessoire":
                    from seamtech_search.accessoires import (
                        lier_extraction_accessoire_document_curseur,
                    )

                    lier_extraction_accessoire_document_curseur(
                        cursor,
                        id_fiche=id_fiche,
                        id_document=int(id_document_pdf),
                        empreinte_sha256=empreinte,
                        nom_fichier_source=pdf_fiche.name,
                    )
                else:
                    lier_extraction_voile_document_curseur(
                        cursor,
                        id_fiche=id_fiche,
                        id_document=int(id_document_pdf),
                        empreinte_sha256=empreinte,
                        nom_fichier_source=pdf_fiche.name,
                    )

                # Identité des octets des pièces jointes : rétablie EN DERNIER
                # (voir plus bas), comme pour le PDF de fiche — l'upsert et le
                # rafraîchissement du vecteur passent par des colonnes
                # surveillées par le déclencheur d'invalidation.
                identites_pieces: list[tuple[int, str]] = []
                for piece in plan.pieces:
                    chemin_piece = Path(piece.chemin)
                    # Une pièce stagée garde la même identité d'une session à
                    # l'autre : chemin relatif envoyé, hors jeton. Si le PDF
                    # de fiche a changé, le dépôt remplace la fiche ; il ne
                    # doit pas pour autant créer un second lien pour une
                    # annexe inchangée. Une annexe modifiée remplace le lien
                    # existant vers son nouveau document, sans réécrire
                    # l'ancien document (les approbations déjà accordées sur
                    # ses octets restent donc attachées à ces octets-là).
                    logique_piece = _chemin_logique_televersement(chemin_piece)
                    piece_existante: tuple[Any, ...] | None = None
                    if logique_piece is not None:
                        cursor.execute(
                            "SELECT id_piece, id_document, chemin, empreinte_sha256 "
                            "FROM fiche_piece_jointe WHERE id_fiche = %s "
                            "ORDER BY id_piece",
                            (id_fiche,),
                        )
                        for candidate in cursor.fetchall():
                            if _chemin_logique_televersement(str(candidate[2])) == logique_piece:
                                piece_existante = candidate
                                break
                    if (
                        piece_existante is not None
                        and piece_existante[1] is not None
                        and str(piece_existante[3] or "") == str(piece.empreinte_sha256)
                    ):
                        _lier_objet_promu(cursor, int(piece_existante[1]), chemin_piece, piece.empreinte_sha256, objets_promus, remplacer_cle=True)
                        continue
                    # RG12 — décision revue 21/09 : le fichier est décrit UNE FOIS,
                    # dans `documents` (métadonnées indexées), path_key = normcase
                    # (même clé que le crawler ⇒ réconciliation, jamais duplication) ;
                    # fiche_piece_jointe est le LIEN fiche ↔ document.
                    contenu = (
                        f"pièce jointe ({piece.role}) de la fiche {fiche.code} — "
                        "non analysée (RG12 : métadonnées seulement)"
                    )
                    _verrouiller_document_existant(cursor, chemin_piece)
                    cursor.execute(
                        _SQL_DOCUMENT_UPSERT,
                        (
                            os.path.normcase(str(chemin_piece.resolve())),
                            str(chemin_piece),
                            chemin_piece.name,
                            str(chemin_piece.parent),
                            chemin_piece.suffix.lower(),
                            piece.taille_octets or (chemin_piece.stat().st_size if chemin_piece.exists() else 0),
                            chemin_piece.stat().st_mtime if chemin_piece.exists() else 0.0,
                            contenu,
                            piece.empreinte_sha256,
                            piece.role,
                            # Identité des octets : deuxième marqueur, celui de
                            # la condition de reprise (ordre des %s).
                            piece.empreinte_sha256,
                        ),
                    )
                    ligne_document = cursor.fetchone()
                    if ligne_document is None:
                        raise DepotImpossible("Une pièce existante au même chemin a changé ; revue nécessaire.")
                    id_document = ligne_document[0]
                    identites_pieces.append((int(id_document), piece.empreinte_sha256))
                    _lier_objet_promu(cursor, int(id_document), chemin_piece, piece.empreinte_sha256, objets_promus)
                    cursor.execute(
                        _SQL_DOCUMENT_VECTOR,
                        (index.config_recherche_texte(connexion), id_document),
                    )
                    if piece_existante is not None:
                        cursor.execute(
                            "UPDATE fiche_piece_jointe SET id_document = %s, chemin = %s, "
                            "role = %s, empreinte_sha256 = %s, taille_octets = %s "
                            "WHERE id_piece = %s",
                            (
                                id_document,
                                str(piece.chemin),
                                piece.role,
                                piece.empreinte_sha256,
                                piece.taille_octets,
                                piece_existante[0],
                            ),
                        )
                    else:
                        cursor.execute(
                            _SQL_PIECE_INS,
                            (
                                id_fiche,
                                id_document,
                                str(piece.chemin),
                                piece.role,
                                piece.empreinte_sha256,
                                piece.taille_octets,
                            ),
                        )
                # Rien après cet endroit ne touche les colonnes surveillées des
                # pièces : leur identité est donc rétablie ici, EN DERNIER —
                # sinon elle serait perdue dès ce dépôt et le suivant ne
                # pourrait plus prouver que les pièces n'ont pas changé.
                for id_piece_document, empreinte_piece in identites_pieces:
                    _etablir_identite_octets(cursor, id_piece_document, empreinte_piece)
                # Le claim Redis est contrôlé au plus près du commit sans
                # ouvrir une seconde transaction PostgreSQL sur le verrou tenu.
                if verifier_claim_externe is not None and not verifier_claim_externe():
                    raise DepotClaimPerdu("Le worker ne détient plus le claim externe.")
                # La traçabilité et la clé d'idempotence sont publiées dans
                # la même transaction que la fiche et ses documents.
                cursor.execute(
                    _SQL_LIGNE_MAJ,
                    ("traite", None if action == "creee" else action, id_fiche, None if cle_deja_utilisee else cle, len(plan.pieces), id_lot_dossier),
                )
    except DepotClaimPerdu:
        raise
    except Exception as erreur:
        LOGGER.error(
            "Échec d'écriture (%s) — conséquence : transaction annulée, dossier listé en échec.",
            type(erreur).__name__,
        )
        raison_sure = _raison_ecriture_sure(erreur)
        _maj_ligne(index, id_lot_dossier, "echec", raison_sure, None, cle, 0, claim=claim)
        _rafraichir_compteurs(index, id_lot_local, claim=claim)
        if isolé:
            _finaliser_lot(index, id_lot_local)
        return {
            "statut": "echec",
            "fiche": None,
            "raison": raison_sure,
            "pieces": 0,
            "id_lot": id_lot_local,
        }
    if id_lot_local is not None:
        _rafraichir_compteurs(index, id_lot_local, claim=claim)
        if isolé:
            _finaliser_lot(index, id_lot_local)  # un dossier isolé = un lot qui se termine
    # P0.2 / OBS-05 : les OCTETS du dossier remontent vers le stockage objet
    # APRÈS le commit (un objet est externe : le tenir dans la transaction
    # reviendrait à bloquer PostgreSQL sur un réseau). Un échec de stockage ne
    # défait pas la fiche : il publie upload_status='failed' et laisse la
    # reprise à `promouvoir_en_attente`.
    # Les fichiers dont la copie objet a DÉJÀ été vérifiée et liée dans la
    # transaction (``objets_promus`` : envoi du lot téléversé, rejeu) ne
    # repassent pas par la promotion : elle les re-marquerait
    # « not_configured » — donc sans clé — quand le stockage n'est pas
    # configuré dans ce processus, effaçant la liaison prouvée.
    deja_lies = (
        {str(Path(chemin).resolve()) for chemin in objets_promus} if objets_promus else set()
    )
    a_promouvoir = [
        chemin
        for chemin in fichiers_pour_promotion(plan.pdf_fiche, plan.pieces)
        if str(Path(chemin).resolve()) not in deja_lies
    ]
    promotion = (
        promouvoir_fichiers(index, config, a_promouvoir, nom_espace=_espace_promotion(dossier, id_lot_local))
        if a_promouvoir
        else {"statut": "not_applicable", "objets": 0, "verifies": 0, "cles": {}, "echecs": []}
    )
    LOGGER.info(
        "Dossier déposé (%s), %d pièce(s) jointe(s), lot #%d.",
        action,
        len(plan.pieces),
        id_lot_local,
    )
    # raison : l'action d'écriture quand elle porte information (remplacee,
    # conservee_validee) ; None pour une création (rien à signaler).
    return {
        "statut": "traite",
        "fiche": fiche.code,
        "raison": None if action == "creee" else action,
        "pieces": len(plan.pieces),
        "id_lot": id_lot_local,
        # Révision PUBLIÉE avec le contenu (A02) : un remplacement sur place
        # l'a fait avancer, donc un écran resté ouvert sur l'ancienne révision
        # ne peut plus valider ce qu'il n'a pas relu.
        "revision": revision_fiche,
        "revision_avant": ecriture.revision_avant,
        "contenu_remplace": ecriture.remplacee,
        # P0.2 : ce que le stockage objet a réellement accepté et vérifié.
        "promotion": promotion,
        # Transparence : d'autres PDF du dossier portaient AUSSI une fiche
        # (révision antérieure, variante…). Ils sont conservés en pièces
        # jointes ; l'opérateur doit pouvoir le voir sans lire les journaux.
        "autres_fiches": [pdf.name for pdf in plan.autres_fiches],
    }


def _supprimer_copies_rejeu(
    index: Any,
    id_lot_dossier: int,
    pdf_fiche: Path,
    pieces: list[Any],
    empreinte_fiche: str,
) -> int:
    """Supprime les copies stagées d'un dossier REJOUÉ (idempotence).

    Le rejeu a été reconnu : la fiche existante porte DÉJÀ ces octets. Les
    copies de la nouvelle session de téléversement ne sont donc référencées par
    rien, et la rétention ne les élague pas (elle ne touche jamais aux sessions
    de téléversement, par conception). Sans ce nettoyage, chaque réenvoi d'une
    affaire laissait une copie complète sous ``uploads/lots-televerses``.

    Conditions cumulatives, vérifiées AVANT toute suppression :

    * le fichier est sous une session de téléversement
      (``…/lots-televerses/<jeton>/fichiers/…``) — une archive déposée par
      chemin n'est jamais touchée (lecture seule, RG13) ;
    * ses octets sont DÉJÀ ENREGISTRÉS pour cette fiche : le PDF de fiche a
      l'empreinte qui a servi à reconnaître le rejeu, une pièce a l'une des
      empreintes enregistrées dans ``fiche_piece_jointe`` pour cette fiche. Un
      fichier modifié entre-temps n'est donc JAMAIS supprimé : ses octets ne
      sont nulle part ailleurs ;
    * une suppression impossible (droits, fichier déjà absent) est journalisée
      et n'échoue jamais le dépôt.

    Renvoie le nombre de copies effectivement supprimées.
    """
    empreintes_pieces: set[str] = set()
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT p.empreinte_sha256 FROM fiche_piece_jointe p "
                "JOIN lot_dossier l ON l.id_fiche = p.id_fiche "
                "WHERE l.id_lot_dossier = %s",
                (id_lot_dossier,),
            )
            empreintes_pieces = {str(ligne[0]) for ligne in cursor.fetchall()}

    supprimes = 0
    cibles: list[tuple[Path, bool]] = [(Path(pdf_fiche), True)]  # True = déjà enregistré
    for piece in pieces:
        chemin = getattr(piece, "chemin", None)
        if chemin is None:
            continue
        enregistree = empreintes_pieces.__contains__(piece.empreinte_sha256)
        cibles.append((Path(chemin), enregistree))
    for chemin, enregistree in cibles:
        if _chemin_logique_televersement(chemin) is None:
            continue
        if not enregistree:
            LOGGER.info(
                "Rejeu : %s n'a pas d'équivalent enregistré pour cette fiche — copie CONSERVÉE "
                "(ses octets ne sont nulle part ailleurs).",
                chemin,
            )
            continue
        try:
            if not chemin.is_file():
                continue
            chemin.unlink()
            supprimes += 1
        except OSError as erreur:
            LOGGER.warning("Rejeu : copie stagée %s non supprimée (%s).", chemin, erreur)
    return supprimes


def _espace_promotion(dossier: Path, id_lot: int | None) -> str:
    """Préfixe de clés du stockage objet pour un dossier de lot.

    La session de téléversement est conservée quand elle est reconnaissable
    (``uploads/lots-teverses/<jeton>/fichiers/…``) : deux envois du même nom de
    dossier restent ainsi séparés dans le bucket, comme ils le sont sur disque.
    """
    parties = Path(dossier).parts
    if _SEGMENT_LOTS_TELEVERSES in parties:
        index_jeton = parties.index(_SEGMENT_LOTS_TELEVERSES) + 1
        if index_jeton < len(parties):
            return f"lots/{parties[index_jeton]}"
    return f"lots/lot-{id_lot}" if id_lot is not None else "lots/depot"


def creer_lot(index: Any, racine: Path, notes: str | None = None, *, recursif: bool = True) -> int:
    """Crée un lot en attente, sans exécuter aucun dossier.

    Par défaut, la découverte est RÉCURSIVE : tout dossier portant un PDF
    direct devient une ligne, qu'il soit sous une année, une catégorie ou
    à la racine même. ``recursif=False`` conserve l'ancien comportement
    (sous-dossiers directs, y compris sans PDF). Lecture seule sur
    l'archive dans les deux cas.
    """
    racine = Path(racine)
    if not racine.is_dir():
        raise DepotImpossible(f"Racine de lot introuvable : {racine}")
    if recursif:
        sous_dossiers = decouvrir_dossiers_candidats(racine)
        if not sous_dossiers:
            raise DepotImpossible(
                f"Aucun dossier contenant un PDF direct sous {racine} — conséquence : aucun lot créé."
            )
    else:
        sous_dossiers = sorted(chemin for chemin in racine.iterdir() if chemin.is_dir())
        if not sous_dossiers:
            raise DepotImpossible(f"Aucun sous-dossier à traiter dans {racine} (un dossier par affaire attendu).")
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_LOT_INS, (str(racine), notes))
            id_lot = int(cursor.fetchone()[0])
            cursor.executemany(_SQL_LOT_DOSSIERS_INS, [(id_lot, str(chemin)) for chemin in sous_dossiers])
            cursor.execute(_SQL_LOT_COMPTE, (id_lot,))
            nb = int(cursor.fetchone()[0])
            cursor.execute(_SQL_LOT_MAJ, (nb, "en_cours", "en_cours", id_lot))
    LOGGER.info("Lot #%d créé : %d dossier(s) en attente.", id_lot, nb)
    return id_lot


def _preparer_objets_lot(
    index: Any, id_lot: int, config: Any, client_documents: Any | None,
    doit_continuer: Callable[[], bool] | None,
) -> dict[str, dict[str, str]] | None:
    """Préserve une session stagée avant son dépôt ; les archives restent en lecture seule."""
    from seamtech_search.fiches import televersement_lot as televersement
    from seamtech_search.import_pipeline import staging_root
    from seamtech_search.storage import S3StorageClient

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT dossier_racine FROM lot_import WHERE id_lot = %s", (id_lot,))
            ligne = cursor.fetchone()
    if ligne is None:
        raise DepotImpossible(f"Lot #{id_lot} inconnu.")
    racine_brute = Path(os.path.abspath(ligne[0]))
    racine_lots_brute = Path(os.path.abspath(staging_root(config) / televersement.DOSSIER_LOTS_TELEVERSES))
    racine_lots = racine_lots_brute.resolve()
    racine = racine_brute.resolve()
    if not racine_brute.is_relative_to(racine_lots_brute):
        if racine.is_relative_to(racine_lots):
            raise DepotImpossible("Racine de session téléversée indirecte interdite.")
        return None
    parties = racine_brute.relative_to(racine_lots_brute).parts
    if len(parties) != 2 or parties[1] != televersement.DOSSIER_FICHIERS:
        raise DepotImpossible("Racine de session téléversée invalide.")
    if (racine_lots_brute / parties[0]).is_symlink() or racine_brute.is_symlink() or racine != racine_lots.joinpath(*parties):
        raise DepotImpossible("Racine de session téléversée remplacée par un lien symbolique.")
    token = parties[0]
    try:
        session = televersement.etat_session_televersement_lot(config, token)
        if session.get("id_lot") != id_lot:
            raise DepotImpossible("Session non associée au lot demandé.")
        if getattr(config, "storage_backend", "s3") != "s3":
            return None
        client = client_documents if client_documents is not None else S3StorageClient(config=config)
        if not client.is_configured():
            raise DepotImpossible("Stockage objet non configuré pour le lot téléversé.")
        resultat = televersement.promouvoir_session_televersement_objets(
            config, token, client, doit_continuer=doit_continuer,
        )
    except televersement.TeleversementLotInterrompu as erreur:
        raise DepotAnnule("Promotion objet interrompue ; fichiers locaux conservés.") from erreur
    except televersement.TeleversementLotErreur as erreur:
        raise DepotImpossible("Promotion objet du lot impossible ; fichiers locaux conservés.") from erreur
    return resultat["objets_promus"]


def executer_lot(
    index: Any,
    id_lot: int,
    interrompre_apres: int | None = None,
    *,
    doit_continuer: Callable[[], bool] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    worker_id: str | None = None,
    claim_job_id: str | None = None,
    verifier_claim_externe: Callable[[], bool] | None = None,
    objets_promus: dict[str, dict[str, str]] | None = None,
    config: Any | None = None,
    client_documents: Any | None = None,
) -> dict[str, Any]:
    """Traite séquentiellement les dossiers en attente d'un lot.

    ``interrompre_apres`` : nombre de TRAITEMENTS (succès ou échec) après
    lequel le lot passe « interrompu » — c'est l'interruption volontaire des
    tests de reprise. Rejouer la fonction reprend là où c'était resté.

    ``doit_continuer`` : contrôle d'annulation inter-processus (drapeau Redis
    lu par le worker) ; quand il renvoie faux, le lot s'arrête proprement entre
    deux dossiers et ``etat_lot`` expose ``annule: True``. Aucun dossier n'est
    laissé « en cours » : ce qui reste est ``en_attente``, donc reprenable.

    ``progress_cb(traites, total)`` et ``worker_id`` servent la supervision :
    sans eux, un lot long est indistinguable d'un lot bloqué.
    """
    if not getattr(index, "is_postgres", False):
        raise DepotImpossible("Le traitement de lot exige PostgreSQL (tables fiche_*, §17.1).")
    if claim_job_id and not worker_id:
        raise DepotImpossible("Identité du claim de lot incomplète.")
    claim = (claim_job_id, worker_id) if claim_job_id and worker_id else None
    if config is not None and objets_promus is None:
        objets_promus = _preparer_objets_lot(index, id_lot, config, client_documents, doit_continuer)
    LOGGER.info("Lot #%s : chargement des gabarits actifs…", id_lot)
    gabarits = charger_gabarits(index)
    LOGGER.info("Lot #%s : %d gabarit(s) actif(s) chargé(s).", id_lot, len(gabarits))

    # Reprise : un lot laissé « en_cours » par un worker mort est marqué
    # « relance » AVANT de continuer — l'exploitant voit que ce n'est pas la
    # passe initiale, et le worker propriétaire est tracé.
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            _verrouiller_claim(cursor, claim)
            cursor.execute(
                "UPDATE lot_import SET statut = 'relance', worker_id = %s, heartbeat_at = now() "
                "WHERE id_lot = %s AND statut IN ('en_cours', 'relance')",
                (worker_id, id_lot),
            )

    total = _compter_dossiers(index, id_lot)
    traites_cette_passe = 0
    while True:
        if doit_continuer is not None and not doit_continuer():
            LOGGER.warning(
                "Lot #%s : annulation demandée — conséquence : arrêt entre deux dossiers, "
                "les dossiers restants restent « en_attente » (repris si le lot est relancé).",
                id_lot,
            )
            return annuler_lot(index, id_lot, claim=claim)
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT id_lot_dossier, chemin_dossier FROM lot_dossier "
                    "WHERE id_lot = %s AND statut = 'en_attente' ORDER BY id_lot_dossier LIMIT 1",
                    (id_lot,),
                )
                ligne = cursor.fetchone()
        if ligne is None:
            break  # plus rien en attente : le lot se termine plus bas
        id_lot_dossier, chemin = int(ligne[0]), Path(ligne[1])
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                _verrouiller_claim(cursor, claim)
                cursor.execute(
                    "UPDATE lot_import SET heartbeat_at = now() WHERE id_lot = %s",
                    (id_lot,),
                )
        LOGGER.info("Lot #%s : traitement du dossier #%s…", id_lot, id_lot_dossier)
        try:
            resultat = deposer_dossier(
                index, chemin, gabarits=gabarits, id_lot=id_lot, id_lot_dossier=id_lot_dossier,
                objets_promus=objets_promus, claim=claim, verifier_claim_externe=verifier_claim_externe,
                config=config,
            )
        except DepotClaimPerdu:
            # Perdre le claim n'est PAS un défaut du dossier : la transaction est
            # annulée (aucune fiche écrite) et le dossier reste « en_attente »
            # pour le worker propriétaire. Le marquer « echec » le ferait
            # disparaître de la reprise alors qu'il n'a jamais été traité.
            LOGGER.warning(
                "Lot #%s : claim perdu sur le dossier #%s — conséquence : aucune écriture, "
                "dossier laissé « en_attente », traitement arrêté pour laisser la place.",
                id_lot,
                id_lot_dossier,
            )
            raise
        except Exception as erreur:  # filet : « le lot continue » quoi qu'il arrive
            LOGGER.error("Lot #%s : erreur non gérée (%s) sur le dossier #%s", id_lot, type(erreur).__name__, id_lot_dossier)
            _maj_ligne(index, id_lot_dossier, "echec", f"erreur non gérée : {type(erreur).__name__}", None, None, 0, claim=claim)
            _rafraichir_compteurs(index, id_lot, claim=claim)
            continue
        if resultat["statut"] == "echec":
            LOGGER.warning(
                "Dossier #%s en échec dans le lot #%d — conséquence : compté en échec, le lot continue.",
                id_lot_dossier,
                id_lot,
            )
        traites_cette_passe += 1
        _rafraichir_compteurs(index, id_lot, claim=claim)
        if progress_cb is not None:
            try:
                progress_cb(traites_cette_passe, total)
            except Exception:  # pragma: no cover - le suivi ne doit jamais casser le lot
                LOGGER.warning("Lot #%s : le callback de progression a échoué (lot poursuivi).", id_lot)
        if interrompre_apres is not None and traites_cette_passe >= interrompre_apres:
            with index.connect() as connexion:
                with connexion.cursor() as cursor:
                    _verrouiller_claim(cursor, claim)
                    cursor.execute("UPDATE lot_import SET statut = 'interrompu' WHERE id_lot = %s", (id_lot,))
            LOGGER.warning(
                "Lot #%d interrompu volontairement après %d traitement(s) — conséquence : les dossiers restants sont en attente, la reprise se fait en rappelant executer_lot.",
                id_lot,
                traites_cette_passe,
            )
            return etat_lot(index, id_lot)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            _verrouiller_claim(cursor, claim)
            cursor.execute(
                "UPDATE lot_import SET statut = 'termine', termine_le = now() WHERE id_lot = %s",
                (id_lot,),
            )
    return etat_lot(index, id_lot)


def _compter_dossiers(index: Any, id_lot: int) -> int:
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_LOT_COMPTE, (id_lot,))
            return int(cursor.fetchone()[0])


def annuler_lot(index: Any, id_lot: int, *, claim: tuple[str, str] | None = None) -> dict[str, Any]:
    """Marque un lot annulé (état terminal explicite, distinct d'un échec).

    Ne supprime ni dossier ni fiche : un lot annulé puis relancé reprend les
    dossiers ``en_attente``, et les dossiers ``traite`` ne sont jamais refaits
    (clé d'idempotence).
    """
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            _verrouiller_claim(cursor, claim)
            cursor.execute(
                "UPDATE lot_import SET statut = 'annule', annule_le = now() WHERE id_lot = %s",
                (id_lot,),
            )
    etat = etat_lot(index, id_lot)
    etat["annule"] = True
    return etat


def _finaliser_lot(index: Any, id_lot: int) -> None:
    """Lot fini (un dossier isolé, ou la fin d'une passe complète)."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("UPDATE lot_import SET statut = 'termine', termine_le = now() WHERE id_lot = %s", (id_lot,))


def _rafraichir_compteurs(index: Any, id_lot: int, *, claim: tuple[str, str] | None = None) -> None:
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            _verrouiller_claim(cursor, claim)
            cursor.execute(
                "UPDATE lot_import SET nb_dossiers = (SELECT COUNT(*) FROM lot_dossier WHERE id_lot = %s), "
                "nb_traites = (SELECT COUNT(*) FROM lot_dossier WHERE id_lot = %s AND statut = 'traite'), "
                "nb_echecs = (SELECT COUNT(*) FROM lot_dossier WHERE id_lot = %s AND statut = 'echec') "
                "WHERE id_lot = %s",
                (id_lot, id_lot, id_lot, id_lot),
            )


def _chemin_relatif_lot(racine: str, chemin_dossier: str) -> str:
    """Chemin d'un dossier de lot relatif à sa racine, pour l'affichage.

    Le chemin absolu reste exposé dans ``chemin_dossier`` (traçabilité et
    compatibilité), mais l'écran de suivi n'a pas à montrer l'arborescence
    interne du serveur — en particulier le dossier d'une session de
    téléversement. Un chemin incohérent retombe sur son nom final, jamais
    sur une exception qui rendrait tout le lot illisible.
    """
    try:
        relatif = Path(chemin_dossier).relative_to(Path(racine))
    except (TypeError, ValueError):
        return Path(chemin_dossier).name or chemin_dossier
    return "." if str(relatif) == "." else relatif.as_posix()


def etat_lot(index: Any, id_lot: int) -> dict[str, Any]:
    """État complet d'un lot : progression, dossiers, échecs avec raisons,
    fichiers restants — tout ce que GET /lots/{id} retournera."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_LOT_LIRE, (id_lot,))
            lot = cursor.fetchone()
            if lot is None:
                raise DepotImpossible(f"Lot #{id_lot} inconnu.")
            cursor.execute(_SQL_DOSSIERS_LOT, (id_lot,))
            dossiers = cursor.fetchall()
            cursor.execute(_SQL_RESTANTS, (id_lot,))
            restants = [str(ligne[0]) for ligne in cursor.fetchall()]
    _, racine, statut, nb_dossiers, nb_traites, nb_echecs, cree_le, termine_le, notes = lot
    return {
        "id_lot": id_lot,
        "dossier_racine": racine,
        "statut": statut,
        "nb_dossiers": nb_dossiers,
        "nb_traites": nb_traites,
        "nb_echecs": nb_echecs,
        "cree_le": None if cree_le is None else cree_le.isoformat(),
        "termine_le": None if termine_le is None else termine_le.isoformat(),
        "notes": notes,
        "progression_pct": round(100.0 * (int(nb_traites) + int(nb_echecs)) / int(nb_dossiers), 1) if int(nb_dossiers) else 0.0,
        "dossiers": [
            {
                "id_lot_dossier": int(ligne[0]),
                "chemin_dossier": str(ligne[1]),
                "chemin_relatif": _chemin_relatif_lot(str(racine), str(ligne[1])),
                "statut": ligne[2],
                "raison": ligne[3],
                "id_fiche": ligne[4],
                "nb_pieces": int(ligne[5]),
                "traite_le": None if ligne[6] is None else ligne[6].isoformat(),
            }
            for ligne in dossiers
        ],
        "restants": restants,
    }


def lister_lots(index: Any) -> list[dict[str, Any]]:
    """Liste des lots (les plus récents d'abord) avec leur progression."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_LOTS_LISTE)
            lignes = cursor.fetchall()
    return [
        {
            "id_lot": int(ligne[0]),
            "dossier_racine": str(ligne[1]),
            "statut": ligne[2],
            "nb_dossiers": int(ligne[3]),
            "nb_traites": int(ligne[4]),
            "nb_echecs": int(ligne[5]),
            "cree_le": None if ligne[6] is None else ligne[6].isoformat(),
            "termine_le": None if ligne[7] is None else ligne[7].isoformat(),
        }
        for ligne in lignes
    ]


def empreintes_arbre(racine: Path) -> dict[str, str]:
    """SHA-256 de chaque fichier d'un arbre (trié) — la preuve RG13 avant/après."""
    empreintes: dict[str, str] = {}
    for chemin in sorted(Path(racine).rglob("*")):
        if chemin.is_file():
            empreintes[str(chemin.relative_to(racine))] = empreinte_fichier(chemin)
    return empreintes
