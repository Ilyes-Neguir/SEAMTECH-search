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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from seamtech_search.fiches.extraction import extraire_fiche
from seamtech_search.fiches.gabarits import GabaritDef, charger_gabarits
from seamtech_search.fiches.persistance import ecrire_fiche
from seamtech_search.indexer import PG_UNACCENT_CONFIG

LOGGER = logging.getLogger("seamtech_search.fiches.depot")

SEPARATEUR_CLE = "|"
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
_SQL_DOCUMENT_UPSERT = (
    "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, "
    "is_dir, content, content_hash, extraction_status, role) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s, 'metadata', %s) "
    "ON CONFLICT (path_key) DO UPDATE SET role = EXCLUDED.role, content_hash = EXCLUDED.content_hash, "
    "size = EXCLUDED.size, modified_at = EXCLUDED.modified_at, content = EXCLUDED.content "
    "RETURNING id"
)
# Même expression que l'indexeur (RG12 : contenu indexé = métadonnées seulement).
_SQL_DOCUMENT_VECTOR = (
    f"UPDATE documents SET search_vector = to_tsvector('{PG_UNACCENT_CONFIG}', "
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
    """Un fichier du dossier, lu en lecture seule (chemin, rôle, empreinte)."""

    chemin: Path
    role: str
    taille_octets: int
    empreinte_sha256: str


@dataclass
class PlanDossier:
    """Ce que le scanner a compris d'un dossier, SANS rien écrire."""

    dossier: Path
    pdf_fiche: Path | None = None
    gabarit_code: str | None = None
    score: int = 0
    pieces: list[FichierDossier] = field(default_factory=list)
    raison_refus: str | None = None

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


def cle_idempotence(chemin_pdf: Path, empreinte_pdf: str) -> str:
    """Chemin normalisé (os.path.normcase — même fonction que crawler.py:154) + empreinte."""
    return os.path.normcase(str(chemin_pdf.resolve())) + SEPARATEUR_CLE + empreinte_pdf


def role_piece(chemin: Path) -> str:
    """Rôle d'une pièce jointe d'après son suffixe (valeurs fermées)."""
    suffixe = chemin.suffix.lower()
    return next((role for motif, role in ROLES_PIECES if suffixe == motif), "piece_jointe")


def scanner_dossier(dossier: Path, gabarits: list[GabaritDef]) -> PlanDossier:
    """Repère le PDF de fiche et les pièces jointes d'un dossier — SANS écriture.

    Le PDF de fiche est celui dont le score d'ancres de détection est le plus
    élevé (strictement positif) ; les autres fichiers deviennent des pièces
    jointes. Un dossier sans PDF détectable est un REFUS documenté (raison).
    """
    dossier = Path(dossier)
    if not dossier.is_dir():
        raise DepotImpossible(f"Dossier introuvable ou non-directory : {dossier}")
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
                empreinte_sha256=empreinte_fichier(element),
            )
        )
    if not pdfs:
        plan.raison_refus = "aucun PDF dans le dossier"
        return plan
    from seamtech_search.fiches.extraction import analyser_pdf, texte_normalise

    meilleur: tuple[int, GabaritDef | None, Path | None] = (0, None, None)
    scores: dict[str, int] = {}
    for pdf in pdfs:
        try:
            texte = texte_normalise(analyser_pdf(pdf))
        except Exception as erreur:  # PDF illisible : raison explicite, pas de crash
            scores[pdf.name] = -1
            LOGGER.warning("PDF illisible (%s) : %s — conséquence : exclu de la détection.", pdf, erreur)
            continue
        scores_pdf = {gabarit.code: gabarit.score_detection(texte) for gabarit in gabarits}
        code, score = max(scores_pdf.items(), key=lambda paire: paire[1])
        scores[pdf.name] = score
        if score > meilleur[0]:
            meilleur = (score, next(g for g in gabarits if g.code == code), pdf)
    if meilleur[1] is None or meilleur[2] is None:
        detail = ", ".join(f"{nom}={score}" for nom, score in scores.items())
        plan.raison_refus = f"gabarit inconnu (scores de détection : {detail})"
        return plan
    plan.pdf_fiche = meilleur[2]
    plan.gabarit_code = meilleur[1].code
    plan.score = meilleur[0]
    # les autres PDF (non reconnus ou seconds) restent des pièces jointes
    plan.pieces.extend(
        FichierDossier(chemin=pdf, role="piece_jointe", taille_octets=pdf.stat().st_size, empreinte_sha256=empreinte_fichier(pdf))
        for pdf in pdfs
        if pdf != meilleur[2]
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


def deposer_dossier(index: Any, dossier: Path, gabarits: list[GabaritDef] | None = None, id_lot: int | None = None, id_lot_dossier: int | None = None) -> dict[str, Any]:
    """Dépose UN dossier : détecte, extrait, écrit fiche + pièces jointes en UNE
    transaction, puis met à jour la ligne de lot. Retourne un RÉSULTAT (jamais
    d'exception métier) :
    ``{"statut": "traite"|"echec"|"deja_traite", "fiche": code|None, "raison": …, "id_lot": …}``.

    ``gabarits`` : registre (chargé de base si absent). Sans ``id_lot_dossier``,
    l'opération devient son propre lot suivi (refus compris).
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
        if id_lot_dossier is None:
            id_lot_local, id_lot_dossier = _creer_lot_isole(index, Path(dossier))
        _maj_ligne(index, id_lot_dossier, "echec", raison, None, None, 0)
        _rafraichir_compteurs(index, id_lot_local)
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
            return {
                "statut": "deja_traite",
                "fiche": None,
                "raison": f"deja_traite (lot #{existant[1]})",
                "pieces": len(plan.pieces),
                "id_lot": id_lot_local,
            }
        # L'EXCEPTION doit franchir le « with » pour déclencher le rollback de
        # la connexion : le except est donc VOLONTAIREMENT à l'extérieur —
        # écriture entière ou pas du tout (fiche + pièces jointes).
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                fiche = extraire_fiche(plan.pdf_fiche, gabarits=gabarits, gabarit_code=plan.gabarit_code)
                id_fiche, action = ecrire_fiche(index, fiche, connexion=connexion)
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
                    cursor.execute(_SQL_DOCUMENT_VECTOR, (id_document,))
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
    }


def creer_lot(index: Any, racine: Path, notes: str | None = None) -> int:
    """Crée un lot : chaque sous-dossier direct de la racine devient une ligne
    en attente. Lecture seule sur l'archive (un ``iterdir`` + un ``is_dir``)."""
    racine = Path(racine)
    if not racine.is_dir():
        raise DepotImpossible(f"Racine de lot introuvable : {racine}")
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


def executer_lot(index: Any, id_lot: int, interrompre_apres: int | None = None) -> dict[str, Any]:
    """Traite séquentiellement les dossiers en attente d'un lot.

    ``interrompre_apres`` : nombre de TRAITEMENTS (succès ou échec) après
    lequel le lot passe « interrompu » — c'est l'interruption volontaire des
    tests de reprise. Rejouer la fonction reprend là où c'était resté.
    """
    gabarits = charger_gabarits(index)
    traites_cette_passe = 0
    while True:
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
        resultat = deposer_dossier(index, chemin, gabarits=gabarits, id_lot=id_lot, id_lot_dossier=id_lot_dossier)
        if resultat["statut"] == "echec":
            LOGGER.warning(
                "Dossier %s en échec dans le lot #%d : %s — conséquence : compté en échec, le lot continue.",
                chemin,
                id_lot,
                resultat["raison"],
            )
        traites_cette_passe += 1
        _rafraichir_compteurs(index, id_lot)
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
