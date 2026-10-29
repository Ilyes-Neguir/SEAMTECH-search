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
from seamtech_search.fiches.extraction import analyser_candidat_fiche, extraire_fiche
from seamtech_search.fiches.gabarits import GabaritDef, charger_gabarits
from seamtech_search.fiches.modeles import FicheExtraite
from seamtech_search.fiches.persistance import ecrire_fiche_resultat
from seamtech_search.fiches.promotion import fichiers_pour_promotion, promouvoir_fichiers

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
    "size = EXCLUDED.size, modified_at = EXCLUDED.modified_at, content = EXCLUDED.content "
    "RETURNING id"
)
_SQL_DOCUMENT_FICHE_UPSERT = (
    "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, "
    "is_dir, content, content_hash, extraction_status, category, role) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s, 'metadata', 'analyzed', 'fiche_pdf') "
    "ON CONFLICT (path_key) DO UPDATE SET name = EXCLUDED.name, path = EXCLUDED.path, "
    "parent_path = EXCLUDED.parent_path, extension = EXCLUDED.extension, size = EXCLUDED.size, "
    "modified_at = EXCLUDED.modified_at, "
    "content = CASE WHEN COALESCE(documents.content, '') = '' THEN EXCLUDED.content ELSE documents.content END, "
    "content_hash = EXCLUDED.content_hash, category = 'analyzed', role = 'fiche_pdf' RETURNING id"
)


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
    if not pdfs:
        plan.raison_refus = "aucun PDF dans le dossier"
        return plan

    candidats: list[tuple[Path, Any]] = []
    scores: dict[str, int] = {}
    illisibles: list[str] = []
    for pdf in pdfs:
        try:
            candidat = analyser_candidat_fiche(pdf, gabarits)
        except Exception as erreur:  # PDF illisible : raison explicite, pas de crash
            scores[pdf.name] = -1
            illisibles.append(pdf.name)
            LOGGER.warning("PDF illisible (%s) : %s — conséquence : exclu de la détection.", pdf, erreur)
            continue
        scores[pdf.name] = 0 if candidat is None else candidat.score_detection
        if candidat is not None:
            candidats.append((pdf, candidat))

    if not candidats:
        detail = ", ".join(f"{nom}={score}" for nom, score in scores.items())
        if illisibles:
            # MAN-04 : dire la cause lisible par l'opérateur d'abord, les
            # scores ensuite (diagnostic) — « gabarit inconnu » seul laissait
            # croire à un PDF sain d'un format nouveau.
            plan.raison_refus = (
                f"PDF illisible : {', '.join(illisibles)} (lecture impossible) "
                f"(scores de détection : {detail})"
            )
        else:
            plan.raison_refus = f"gabarit inconnu (scores de détection : {detail})"
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
        meilleur_pdf, meilleur_candidat = candidats_tries[0]
        plan.gabarit_code = meilleur_candidat.gabarit.code
        plan.score = meilleur_candidat.score_detection
        detail = ", ".join(f"{nom}={score}" for nom, score in scores.items())
        plan.raison_refus = (
            "aucun PDF candidat n'a produit une fiche sélectionnable (code + structure, "
            f"ou structure riche sans code ; meilleur candidat : {meilleur_pdf.name} ; scores : {detail})"
        )
        return plan

    pdf_choisi, candidat_choisi = choisi
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


def _maj_ligne(index: Any, id_lot_dossier: int, statut: str, raison: str | None, id_fiche: int | None, cle: str | None, nb_pieces: int) -> None:
    """Met à jour la ligne de lot (hors transaction fiche : simple suivi)."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_LIGNE_MAJ, (statut, raison, id_fiche, cle, nb_pieces, id_lot_dossier))


def deposer_dossier(
    index: Any,
    dossier: Path,
    gabarits: list[GabaritDef] | None = None,
    id_lot: int | None = None,
    id_lot_dossier: int | None = None,
    *,
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
        LOGGER.warning(
            "Dossier %s refusé : %s — conséquence : dossier listé en échec, le lot continue.",
            dossier,
            raison,
        )
        lot_isole_cree = False
        if id_lot_dossier is None:
            id_lot_local, id_lot_dossier = _creer_lot_isole(index, Path(dossier))
            lot_isole_cree = True
        _maj_ligne(index, id_lot_dossier, "echec", raison, None, None, 0)
        _rafraichir_compteurs(index, id_lot_local)
        if isolé or lot_isole_cree:
            _finaliser_lot(index, id_lot_local)
        return {"statut": "echec", "fiche": None, "raison": raison, "pieces": 0, "id_lot": id_lot_local}

    try:
        plan = scanner_dossier(Path(dossier), gabarits)
    except DepotImpossible as erreur:
        LOGGER.error("Dépôt refusé (%s) : %s — conséquence : dossier compté en échec.", dossier, erreur)
        return _enregistrer_echec(str(erreur))
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
        if existant is not None:
            LOGGER.info(
                "Dossier %s déjà traité (lot #%d, ligne #%d) : rejeu sans effet (idempotence).",
                dossier,
                existant[1],
                existant[0],
            )
            # NB : cle_idempotence reste NULL ici — l'index unique partiel
            # interdit DEUX lignes traite avec la même clé, et la ligne
            # originale (lot #{existant[1]}) est la seule qui la porte.
            _maj_ligne(index, id_lot_dossier, "traite", f"deja_traite (lot #{existant[1]})", None, None, len(plan.pieces))
            if id_lot_local is not None:
                _rafraichir_compteurs(index, id_lot_local)
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
        # L'EXCEPTION doit franchir le « with » pour déclencher le rollback de
        # la connexion : le except est donc VOLONTAIREMENT à l'extérieur —
        # écriture entière ou pas du tout (fiche + pièces jointes).
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                fiche = plan.fiche_extraite or extraire_fiche(
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

                # Le PDF principal a lui aussi un identifiant de catalogue : toutes
                # les vues l'ouvrent par /pieces/{id}, sans transmettre son chemin.
                pdf_fiche = Path(plan.pdf_fiche)
                pdf_stat = pdf_fiche.stat()
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
                    ),
                )
                id_document_pdf = cursor.fetchone()[0]
                cursor.execute(
                    _SQL_DOCUMENT_VECTOR,
                    (index.config_recherche_texte(connexion), id_document_pdf),
                )
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

                for piece in plan.pieces:
                    chemin_piece = Path(piece.chemin)
                    # RG12 — décision revue 21/09 : le fichier est décrit UNE FOIS,
                    # dans `documents` (métadonnées indexées), path_key = normcase
                    # (même clé que le crawler ⇒ réconciliation, jamais duplication) ;
                    # fiche_piece_jointe est le LIEN fiche ↔ document.
                    contenu = (
                        f"pièce jointe ({piece.role}) de la fiche {fiche.code} — "
                        "non analysée (RG12 : métadonnées seulement)"
                    )
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
                        ),
                    )
                    id_document = cursor.fetchone()[0]
                    cursor.execute(
                        _SQL_DOCUMENT_VECTOR,
                        (index.config_recherche_texte(connexion), id_document),
                    )
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
    except Exception as erreur:
        LOGGER.exception(
            "Échec d'écriture du dossier %s : %s: %s — conséquence : RIEN n'est persisté (transaction annulée), dossier listé en échec.",
            dossier,
            type(erreur).__name__,
            erreur,
        )
        _maj_ligne(index, id_lot_dossier, "echec", f"écriture annulée : {type(erreur).__name__}: {erreur}", None, cle, 0)
        _rafraichir_compteurs(index, id_lot_local)
        if isolé:
            _finaliser_lot(index, id_lot_local)
        return {
            "statut": "echec",
            "fiche": None,
            "raison": f"écriture annulée : {type(erreur).__name__}: {erreur}",
            "pieces": 0,
            "id_lot": id_lot_local,
        }
    _maj_ligne(index, id_lot_dossier, "traite", None if action == "creee" else action, id_fiche, cle, len(plan.pieces))
    if id_lot_local is not None:
        _rafraichir_compteurs(index, id_lot_local)
        if isolé:
            _finaliser_lot(index, id_lot_local)  # un dossier isolé = un lot qui se termine
    # P0.2 / OBS-05 : les OCTETS du dossier remontent vers le stockage objet
    # APRÈS le commit (un objet est externe : le tenir dans la transaction
    # reviendrait à bloquer PostgreSQL sur un réseau). Un échec de stockage ne
    # défait pas la fiche : il publie upload_status='failed' et laisse la
    # reprise à `promouvoir_en_attente`.
    promotion = promouvoir_fichiers(
        index,
        config,
        fichiers_pour_promotion(plan.pdf_fiche, plan.pieces),
        nom_espace=_espace_promotion(dossier, id_lot_local),
    )
    LOGGER.info(
        "Dossier %s déposé : fiche %s (%s), %d pièce(s) jointe(s), lot #%d.",
        dossier,
        fiche.code,
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
    LOGGER.info("Lot #%d créé : %d dossier(s) en attente sous %s.", id_lot, nb, racine)
    return id_lot


def executer_lot(
    index: Any,
    id_lot: int,
    interrompre_apres: int | None = None,
    *,
    doit_continuer: Callable[[], bool] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    worker_id: str | None = None,
    config: Any | None = None,
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
    LOGGER.info("Lot #%s : chargement des gabarits actifs…", id_lot)
    gabarits = charger_gabarits(index)
    LOGGER.info("Lot #%s : %d gabarit(s) actif(s) chargé(s).", id_lot, len(gabarits))

    # Reprise : un lot laissé « en_cours » par un worker mort est marqué
    # « relance » AVANT de continuer — l'exploitant voit que ce n'est pas la
    # passe initiale, et le worker propriétaire est tracé.
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
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
            with index.connect() as connexion:
                with connexion.cursor() as cursor:
                    cursor.execute(
                        "UPDATE lot_import SET statut = 'annule', annule_le = now() WHERE id_lot = %s",
                        (id_lot,),
                    )
            etat = etat_lot(index, id_lot)
            etat["annule"] = True
            return etat
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
                cursor.execute(
                    "UPDATE lot_import SET heartbeat_at = now() WHERE id_lot = %s",
                    (id_lot,),
                )
        LOGGER.info("Lot #%s : traitement du dossier %s (%s)…", id_lot, id_lot_dossier, chemin)
        try:
            resultat = deposer_dossier(
                index,
                chemin,
                gabarits=gabarits,
                id_lot=id_lot,
                id_lot_dossier=id_lot_dossier,
                config=config,
            )
        except Exception as erreur:  # filet : « le lot continue » quoi qu'il arrive
            LOGGER.exception("Lot #%s : erreur non gérée sur le dossier %s", id_lot, chemin)
            _maj_ligne(index, id_lot_dossier, "echec", f"{type(erreur).__name__}: {erreur}", None, None, 0)
            _rafraichir_compteurs(index, id_lot)
            continue
        if resultat["statut"] == "echec":
            LOGGER.warning(
                "Dossier %s en échec dans le lot #%d : %s — conséquence : compté en échec, le lot continue.",
                chemin,
                id_lot,
                resultat["raison"],
            )
        traites_cette_passe += 1
        _rafraichir_compteurs(index, id_lot)
        if progress_cb is not None:
            try:
                progress_cb(traites_cette_passe, total)
            except Exception:  # pragma: no cover - le suivi ne doit jamais casser le lot
                LOGGER.warning("Lot #%s : le callback de progression a échoué (lot poursuivi).", id_lot)
        if interrompre_apres is not None and traites_cette_passe >= interrompre_apres:
            with index.connect() as connexion:
                with connexion.cursor() as cursor:
                    cursor.execute("UPDATE lot_import SET statut = 'interrompu' WHERE id_lot = %s", (id_lot,))
            LOGGER.warning(
                "Lot #%d interrompu volontairement après %d traitement(s) — conséquence : les dossiers restants sont en attente, la reprise se fait en rappelant executer_lot.",
                id_lot,
                traites_cette_passe,
            )
            return etat_lot(index, id_lot)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
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


def annuler_lot(index: Any, id_lot: int) -> dict[str, Any]:
    """Marque un lot annulé (état terminal explicite, distinct d'un échec).

    Ne supprime ni dossier ni fiche : un lot annulé puis relancé reprend les
    dossiers ``en_attente``, et les dossiers ``traite`` ne sont jamais refaits
    (clé d'idempotence).
    """
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
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


def _rafraichir_compteurs(index: Any, id_lot: int) -> None:
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
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
