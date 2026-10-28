"""Promotion des fichiers de lots vers le stockage objet (P0.2 / OBS-05).

Problème constaté
-----------------
Le dépôt de dossier (``fiches.depot.deposer_dossier``) écrivait les fiches, les
pièces jointes et leurs lignes de catalogue en base, mais les OCTETS des
fichiers restaient sous ``uploads/lots-televerses/<jeton>/fichiers/…``, sur le
disque local. Autrement dit : sauvegarder PostgreSQL seul ne restituait aucune
pièce, et sauvegarder MinIO/R2 seul non plus. Le rapport de synthèse du 28/10
le classe P0 (« OBS-05 »).

Ce que fait ce module
---------------------
Après le commit de la transaction de dépôt, il envoie les fichiers du dossier
(le PDF de fiche ET ses pièces jointes) vers le stockage objet configuré
(MinIO, S3 ou R2), **vérifie les octets stockés** (relecture + empreinte
recalculée, ou checksum fournisseur sur objet entier), puis écrit dans
``documents`` la clé d'objet réellement occupée :

``object_key``, ``object_bucket``, ``uploaded_at``, ``upload_status``.

Trois règles, toutes testées :

1. **Le dépôt n'échoue jamais à cause du stockage.** La fiche est déjà
   commitée ; un stockage indisponible doit laisser ``upload_status='failed'``
   et un chemin de reprise (``promouvoir_en_attente``), pas perdre le dossier.
2. **Une clé d'objet n'est écrite que si l'objet est vérifié.** ``uploaded``
   signifie « ces octets-là sont dans le bucket », pas « boto3 n'a pas levé ».
3. **Aucune purge locale n'est décidée ici.** Les copies de travail restent en
   place ; c'est la sauvegarde (P0.1) qui doit les couvrir, et une purge ne
   pourra être décidée que sur ``upload_status='uploaded'`` + intégrité
   prouvée, jamais sur la seule existence d'une clé.

Reprise
-------
``promouvoir_en_attente`` relit les documents dont ``upload_status`` n'est pas
``uploaded`` et rejoue l'envoi. C'est le point d'entrée d'une tâche de
maintenance (CLI ``fiches promouvoir``) après une coupure de MinIO/R2.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from seamtech_search.storage import S3StorageClient, StorageError, upload_artifacts_to_storage

LOGGER = logging.getLogger("seamtech_search.fiches.promotion")

#: Statuts possibles d'un document vis-à-vis du stockage objet.
STATUTS = ("pending", "uploaded", "failed", "not_configured")

#: Nombre de documents traités par passe de promotion en attente.
LIMITE_PROMOTION = 200


def stockage_configure(config: Any) -> bool:
    """Vrai si un stockage objet est réellement configuré (pas seulement déclaré)."""
    if config is None:
        return False
    try:
        return S3StorageClient(config=config).is_configured()
    except Exception:  # configuration incomplète : ce n'est pas « configuré »
        return False


def _cle_document(chemin: Path) -> str:
    """Clé de catalogue d'un fichier (même normalisation que le crawler)."""
    return os.path.normcase(str(Path(chemin).resolve()))


def _maj_document(index: Any, chemin: Path, *, cle_objet: str | None, bucket: str | None, statut: str) -> None:
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "UPDATE documents SET object_key = %s, object_bucket = %s, uploaded_at = %s, "
                "upload_status = %s WHERE path_key = %s",
                (cle_objet, bucket, time.time() if statut == "uploaded" else None, statut, _cle_document(chemin)),
            )


def promouvoir_fichiers(
    index: Any,
    config: Any,
    fichiers: Sequence[Path],
    *,
    nom_espace: str,
) -> dict[str, Any]:
    """Envoie des fichiers vers le stockage objet et publie les clés en base.

    Retour (jamais d'exception : c'est un compte rendu) ::

        {"statut": "uploaded"|"partial"|"failed"|"not_configured",
         "objets": 3, "verifies": 3, "clés": {...}, "echecs": [...]}

    ``statut='uploaded'`` exige que CHAQUE fichier ait été envoyé ET vérifié.
    """
    fichiers_existants = [Path(chemin) for chemin in fichiers if Path(chemin).is_file()]
    if not fichiers_existants:
        return {"statut": "not_applicable", "objets": 0, "verifies": 0, "cles": {}, "echecs": []}
    if not stockage_configure(config):
        # On le DIT (statut explicite en base) au lieu de laisser « pending »
        # éternellement, ce qui laisserait croire à un envoi en cours.
        for chemin in fichiers_existants:
            try:
                _maj_document(index, chemin, cle_objet=None, bucket=None, statut="not_configured")
            except Exception:  # pragma: no cover - document absent du catalogue
                LOGGER.debug("Document non catalogué pour %s (statut stockage non écrit).", chemin)
        LOGGER.warning(
            "Stockage objet non configuré : %d fichier(s) de lot restent locaux "
            "(statut 'not_configured' en base).",
            len(fichiers_existants),
        )
        return {
            "statut": "not_configured",
            "objets": 0,
            "verifies": 0,
            "cles": {},
            "echecs": [str(chemin) for chemin in fichiers_existants],
        }

    try:
        lot = upload_artifacts_to_storage(nom_espace, list(fichiers_existants), config)
    except StorageError as erreur:
        LOGGER.error("Envoi du lot %s vers le stockage objet impossible : %s", nom_espace, erreur)
        for chemin in fichiers_existants:
            _maj_document(index, chemin, cle_objet=None, bucket=None, statut="failed")
        return {
            "statut": "failed",
            "objets": 0,
            "verifies": 0,
            "cles": {},
            "echecs": [str(chemin) for chemin in fichiers_existants],
        }

    cles: dict[str, str] = {}
    echecs: list[str] = []
    verifies = 0
    for artefact in lot.artifacts:
        chemin = Path(artefact.path)
        if artefact.status == "uploaded" and getattr(artefact, "verified", False):
            _maj_document(index, chemin, cle_objet=artefact.key, bucket=artefact.bucket, statut="uploaded")
            cles[str(chemin)] = artefact.key
            verifies += 1
        else:
            statut = "failed" if artefact.status == "failed" else "pending"
            _maj_document(index, chemin, cle_objet=None, bucket=None, statut=statut)
            echecs.append(f"{chemin}: {artefact.error or artefact.status}")

    total = len(lot.artifacts)
    statut_global = "uploaded" if verifies == total and total > 0 else ("partial" if verifies else "failed")
    if statut_global == "uploaded":
        LOGGER.info("Lot %s : %d fichier(s) promus et vérifiés dans le stockage objet.", nom_espace, verifies)
    else:
        LOGGER.error(
            "Lot %s : %d/%d fichier(s) vérifiés dans le stockage objet — %s",
            nom_espace,
            verifies,
            total,
            "; ".join(echecs[:5]),
        )
    return {
        "statut": statut_global,
        "objets": total,
        "verifies": verifies,
        "cles": cles,
        "echecs": echecs,
    }


def promouvoir_en_attente(index: Any, config: Any, limite: int = LIMITE_PROMOTION) -> dict[str, Any]:
    """Rejoue l'envoi des documents dont l'envoi n'a pas abouti.

    Sélectionne les fichiers de ``documents`` qui ne sont pas dans
    ``upload_status='uploaded'`` ET qui existent encore localement (un fichier
    disparu ne peut pas être promu : il est signalé, pas inventé). Le lot de
    destination reprend le nom de la session d'origine quand elle est
    reconnaissable, sinon ``reprise``.
    """
    if not stockage_configure(config):
        return {"statut": "not_configured", "candidats": 0, "promus": 0, "echecs": []}
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT path, upload_status FROM documents "
                "WHERE coalesce(upload_status, 'pending') <> 'uploaded' AND is_dir = FALSE "
                "ORDER BY id LIMIT %s",
                (int(limite),),
            )
            lignes = cursor.fetchall()
    candidats = [(Path(str(chemin)), str(statut or "pending")) for chemin, statut in lignes]
    promus = 0
    absents: list[str] = []
    echecs: list[str] = []
    for chemin, _statut in candidats:
        if not chemin.is_file():
            absents.append(str(chemin))
            continue
        resultat = promouvoir_fichiers(index, config, [chemin], nom_espace=_espace_de(chemin))
        if resultat["statut"] == "uploaded":
            promus += 1
        else:
            echecs.extend(resultat["echecs"])
    return {
        "statut": "uploaded" if promus and not echecs else ("partial" if promus else "failed"),
        "candidats": len(candidats),
        "promus": promus,
        "absents": absents,
        "echecs": echecs,
    }


def _espace_de(chemin: Path) -> str:
    """Espace de nommage d'un fichier : la session de téléversement si connue."""
    parties = Path(chemin).parts
    if "lots-televerses" in parties:
        index = parties.index("lots-televerses")
        if index + 1 < len(parties):
            return f"lots/{parties[index + 1]}"
    return "lots/reprise"


def fichiers_pour_promotion(fiche_pdf: Path | None, pieces: Iterable[Any]) -> list[Path]:
    """Liste ordonnée des fichiers d'un dossier de lot à promouvoir."""
    fichiers: list[Path] = []
    if fiche_pdf is not None:
        fichiers.append(Path(fiche_pdf))
    for piece in pieces:
        chemin = getattr(piece, "chemin", piece)
        if chemin:
            fichiers.append(Path(chemin))
    vus: set[str] = set()
    uniques: list[Path] = []
    for chemin in fichiers:
        cle = str(chemin.resolve()) if chemin.exists() else str(chemin)
        if cle in vus:
            continue
        vus.add(cle)
        uniques.append(chemin)
    return uniques
