"""Retention and disk space guard utilities.

Provides:
- Disk space check guard (throws InsufficientStorageError -> HTTP 507)
- Report and staged-upload pruning suspended until safe identity verification
- Pruning of audit log entries (default 365 days)
- Orchestration via run_retention_cleanup
"""

from __future__ import annotations

import logging
import shutil
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
    """Conserver les rapports jusqu'à une rétention à identité vérifiable.

    Une racine ou un sous-arbre substitué peut désigner des originaux et une
    preuve passée ne couvre pas un rapport modifié depuis sa publication.
    """
    return 0


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
    """Conserver les dépôts : l'âge ne prouve pas que leurs octets sont sauvegardés.

    Le registre peut omettre un dépôt, et une preuve ancienne ne couvre pas
    les ajouts ultérieurs. Rétention suspendue jusqu'à un protocole sûr.
    """
    return 0


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
