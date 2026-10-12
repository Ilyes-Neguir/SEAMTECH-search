"""Retention and disk space guard utilities.

Provides:
- Disk space check guard (throws InsufficientStorageError -> HTTP 507)
- Pruning of old reports (default 90 days)
- Pruning of staged uploads (default 7 days)
- Pruning of audit log entries (default 365 days)
- Orchestration via run_retention_cleanup
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import AppConfig
    from .indexer import SearchIndex

logger = logging.getLogger("seamtech_search.retention")


class InsufficientStorageError(Exception):
    """Raised when available disk space falls below the configured min_free_bytes threshold."""


def ensure_free_space(path: str | Path, min_free_bytes: int) -> None:
    """Check that the target filesystem has at least `min_free_bytes` available.

    If min_free_bytes <= 0, the check is disabled (useful for tests).
    Raises InsufficientStorageError if free space is insufficient.
    """
    if min_free_bytes <= 0:
        return
    target_path = Path(path).resolve()
    # Check parent if target does not yet exist
    check_path = target_path
    while not check_path.exists() and check_path.parent != check_path:
        check_path = check_path.parent
    try:
        usage = shutil.disk_usage(check_path)
        if usage.free < min_free_bytes:
            raise InsufficientStorageError(
                f"Insufficient disk space on {check_path}: {usage.free} bytes free, "
                f"required minimum is {min_free_bytes} bytes."
            )
    except OSError as exc:
        logger.warning("Could not determine disk usage for %s: %s", check_path, exc)


def prune_reports(base_dir: str | Path, max_age_days: int, *, proteges: set[Path] | None = None) -> int:
    """Prune generated PDF and Word report files older than max_age_days.

    ``proteges`` : depuis l'audit du 2026-10-08, les rapports font partie de
    l'inventaire de reprise (constat A07) : supprimer le rapport d'un import non
    préservé ferait échouer la reprise sur un fichier manquant. Un rapport
    encore référencé par un travail non terminé est donc conservé, quel que soit
    son âge.
    """
    reports_dir = Path(base_dir) / "reports"
    if not reports_dir.exists() or not reports_dir.is_dir():
        return 0

    cutoff_seconds = time.time() - (max_age_days * 86400)
    pruned_count = 0

    for root, dirs, files in os.walk(reports_dir, topdown=False):
        for file_name in files:
            file_path = Path(root) / file_name
            try:
                if file_path.stat().st_mtime < cutoff_seconds:
                    if _est_protege(file_path, proteges):
                        logger.warning(
                            "Rétention : rapport %s conservé — référencé par un import non préservé.",
                            file_path,
                        )
                        continue
                    file_path.unlink()
                    pruned_count += 1
            except OSError as exc:
                logger.warning("Failed to delete old report file %s: %s", file_path, exc)
        # Remove directory if empty
        if root != str(reports_dir):
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except OSError as exc:
                # Best-effort cleanup of an empty directory; harmless if it
                # stays, but not silently.
                logger.debug("Could not remove empty report directory %s: %s", root, exc)

    return pruned_count


def _est_protege(chemin: Path, proteges: set[Path] | None) -> bool:
    """Ce chemin (ou ce qu'il contient) est-il encore nécessaire ?

    Un dossier de staging protégé doit rester ENTIER : on refuse donc aussi de
    supprimer un parent ou un enfant d'un chemin protégé (sous-dossiers d'un
    dossier en cours, dossier contenant un fichier attendu).
    """
    if not proteges:
        return False
    try:
        resolu = chemin.expanduser().resolve()
    except OSError:  # pragma: no cover - chemin exotique
        return True  # dans le doute : ne pas supprimer
    for protege in proteges:
        if resolu == protege:
            return True
        try:
            if resolu in protege.parents or protege in resolu.parents:
                return True
        except (OSError, ValueError):  # pragma: no cover
            return True
    return False


def prune_staged_uploads(
    staging_dir: str | Path, max_age_days: int, *, proteges: set[Path] | None = None
) -> int:
    """Prune temporary staged upload directories older than max_age_days.

    ``proteges`` : chemins encore nécessaires (travail actif, ou copie locale
    qui est la seule copie d'un import non préservé). Défaut ``None`` = aucun
    chemin protégé — c'est la rétention HISTORIQUE, par âge seul ; les appelants
    qui ont un registre de jobs DOIVENT fournir la liste
    (:func:`run_retention_cleanup` le fait). L'élagage ne se fonde jamais sur le
    seul âge quand un travail dépend du dossier.
    """
    staging_path = Path(staging_dir)
    if not staging_path.exists() or not staging_path.is_dir():
        return 0

    cutoff_seconds = time.time() - (max_age_days * 86400)
    pruned_count = 0

    for child in staging_path.iterdir():
        # Never delete quarantine even if it lives inside staging (defensive)
        if child.name == "quarantine":
            continue
        try:
            if child.stat().st_mtime >= cutoff_seconds:
                continue
            if _est_protege(child, proteges):
                logger.warning(
                    "Rétention : %s conservé malgré son âge — travail non terminé ou copie "
                    "locale d'un import non préservé (la supprimer détruirait la seule copie).",
                    child,
                )
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
            pruned_count += 1
        except OSError as exc:
            logger.warning("Failed to remove old staged upload %s: %s", child, exc)

    return pruned_count


def prune_audit_logs(index: SearchIndex, max_age_days: int) -> int:
    """Prune audit log entries older than max_age_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    deleted_rows = 0
    with index.connect() as conn:
        if index.is_postgres:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM audit_log WHERE timestamp < %s", (cutoff,))
                deleted_rows = cursor.rowcount
        else:
            cutoff_iso = cutoff.isoformat()
            cursor = conn.execute("DELETE FROM audit_log WHERE timestamp < ?", (cutoff_iso,))
            deleted_rows = cursor.rowcount
    return max(0, deleted_rows)


def chemins_proteges(
    index: SearchIndex, *, limite_jobs: int = 5000, limite_chemins: int = 20000
) -> tuple[set[Path], int, str | None]:
    """Chemins qu'un élagage ne doit PAS emporter — et l'échec éventuel, DIT.

    Règle (investigation « rétention vs travail actif », audit du 2026-10-08) :
    tant qu'un import n'a pas de copie durable VÉRIFIÉE, sa copie locale est la
    seule copie — et tant qu'un job est actif, sa source est l'entrée du travail.
    L'élagage par âge ne peut donc pas les supprimer, même si personne ne les a
    touchés depuis plus longtemps que la rétention.

    Troisième valeur de retour : ``None`` si la liste a pu être établie, sinon la
    raison de l'échec. En cas d'échec, l'appelant NE DOIT PAS élaguer : « je
    n'ai pas pu savoir ce qui est protégé » n'autorise aucune suppression.
    """
    from .jobs import jobs_non_preserves

    try:
        lignes = jobs_non_preserves(index, limite=limite_jobs)
    except Exception as exc:  # pragma: no cover - dépend de la base
        logger.error(
            "Rétention : impossible de lire les jobs à préserver (%s) — AUCUN élagage ne sera "
            "fait cette fois (on ne supprime pas ce qu'on n'a pas pu examiner).",
            exc,
        )
        return set(), 0, str(exc)

    proteges: set[Path] = set()
    tronque = False
    for job in lignes:
        chemins: list[str] = []
        if job.get("source_path"):
            chemins.append(str(job["source_path"]))
        resultat = job.get("result") or {}
        for entree in resultat.get("files") or []:
            if isinstance(entree, dict) and entree.get("path"):
                chemins.append(str(entree["path"]))
        for cle in ("technical_pdf", "report_path", "report_docx_path", "excel_file"):
            if resultat.get(cle):
                chemins.append(str(resultat[cle]))
        for chemin in chemins:
            proteges.add(Path(chemin).expanduser())
            if len(proteges) > limite_chemins:
                tronque = True
                break
        if tronque:
            break

    if tronque:
        logger.error(
            "Rétention : plus de %d chemins protégés — élagage ANNULÉ cette fois (plutôt que "
            "de risquer la seule copie d'un import).",
            limite_chemins,
        )
        return proteges, len(lignes), f"plafond de {limite_chemins} chemins protégés atteint"
    return proteges, len(lignes), None


def run_retention_cleanup(config: AppConfig, index: SearchIndex) -> dict[str, Any]:
    """Execute all retention cleanup policies and return a summary of deleted artifacts.

    Depuis l'audit du 2026-10-08, l'élagage tient compte du TRAVAIL : les
    chemins des jobs actifs et des imports sans copie durable vérifiée sont
    protégés, et si cette liste ne peut pas être établie, aucun élagage n'a lieu
    (échec fermé) — une rétention ne doit jamais être la cause d'une perte.
    """
    base_dir = config.database_path.parent
    # Fixed: use actual staging_root (data/uploads) not data/staging_uploads
    from .import_pipeline import quarantine_root, staging_root

    staging_dir = staging_root(config)
    quarantine_dir = quarantine_root(config)

    proteges, nb_jobs_proteges, echec = chemins_proteges(index)
    if echec is not None:
        logger.error(
            "Rétention : élagage des copies locales ANNULÉ (%s). Seuls les journaux d'audit, "
            "qui ne portent aucune donnée métier, sont élagués.",
            echec,
        )
        # Les journaux d'audit ne portent aucune donnée métier : les élaguer
        # reste possible… si la base répond encore. Un second échec ici ne doit
        # pas faire remonter une exception depuis la boucle de rétention.
        erreur_audit: str | None = None
        try:
            pruned_aud = prune_audit_logs(index, config.audit_retention_days)
        except Exception as exc_audit:  # pragma: no cover - dépend de la base
            logger.error("Rétention : élagage des journaux d'audit impossible (%s).", exc_audit)
            pruned_aud = 0
            erreur_audit = str(exc_audit)
        return {
            "pruned_reports": 0,
            "pruned_staged_uploads": 0,
            "pruned_audit_logs": pruned_aud,
            "chemins_proteges": 0,
            "jobs_proteges": 0,
            "protection_indisponible": echec,
            **({"prune_audit_erreur": erreur_audit} if erreur_audit else {}),
        }

    pruned_rep = prune_reports(base_dir, config.reports_retention_days, proteges=proteges)
    pruned_stg = prune_staged_uploads(staging_dir, config.staged_retention_days, proteges=proteges)
    # Never prune quarantine — failed uploads must be preserved for manual retry
    # Ensure quarantine dir exists but is excluded from staged pruning
    pruned_aud = prune_audit_logs(index, config.audit_retention_days)

    logger.info(
        "Retention cleanup completed: %d reports, %d staged uploads, %d audit log rows removed "
        "(quarantine preserved at %s ; %d chemin(s) protégé(s) par %d job(s) non préservé(s)).",
        pruned_rep,
        pruned_stg,
        pruned_aud,
        quarantine_dir,
        len(proteges),
        nb_jobs_proteges,
    )

    return {
        "pruned_reports": pruned_rep,
        "pruned_staged_uploads": pruned_stg,
        "pruned_audit_logs": pruned_aud,
        "chemins_proteges": len(proteges),
        "jobs_proteges": nb_jobs_proteges,
        "protection_indisponible": None,
    }
