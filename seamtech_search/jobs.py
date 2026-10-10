"""Background job management and cooperative cancellation.

Manages the import_jobs table:
- Async job registration & polling (POST /imports -> 202 -> GET /imports/{id})
- Cooperative cancellation flags
- Progress reporting & stage transitions
- Stale job recovery on server startup
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .indexer import SearchIndex

logger = logging.getLogger("seamtech_search.jobs")

_cancellation_lock = threading.Lock()
_cancelled_job_ids: set[str] = set()


class ImportCancelledError(Exception):
    """Raised when an import job is cooperatively cancelled during execution."""


def register_job_cancel(job_id: str, redis_store: Any | None = None) -> None:
    """Flag a job ID as cancelled — Redis first, memory fallback (4.5)."""
    if redis_store is not None:
        try:
            from .redis_store import RedisStore

            if isinstance(redis_store, RedisStore) and redis_store.is_configured():
                redis_store.set_cancel_flag(job_id)
        except Exception as exc:
            # In-memory cancellation still works in THIS process; other worker
            # processes would miss the request, so log it.
            logger.warning("Could not set Redis cancel flag for job %s (memory fallback only): %s", job_id, exc)
    with _cancellation_lock:
        _cancelled_job_ids.add(job_id)


def is_job_cancelled(job_id: str, redis_store: Any | None = None) -> bool:
    """Check whether a job has received a cancellation request — Redis first."""
    if redis_store is not None:
        try:
            from .redis_store import RedisStore

            if isinstance(redis_store, RedisStore) and redis_store.is_configured():
                if redis_store.is_cancelled(job_id):
                    return True
        except Exception as exc:
            # Hot path (checked per poll): the in-memory set is still
            # consulted, so log at debug to avoid spamming during an outage.
            logger.debug("Could not check Redis cancel flag for job %s: %s", job_id, exc)
    with _cancellation_lock:
        return job_id in _cancelled_job_ids


def clear_job_cancel(job_id: str, redis_store: Any | None = None) -> None:
    """Clean up cancellation flag once job finishes or exits."""
    if redis_store is not None:
        try:
            from .redis_store import RedisStore

            if isinstance(redis_store, RedisStore) and redis_store.is_configured():
                redis_store.clear_cancel_flag(job_id)
                redis_store.set_heartbeat(job_id, ttl_seconds=1)  # clear heartbeat by short TTL
        except Exception as exc:
            # Housekeeping: a leftover flag expires via Redis TTL and job ids
            # are unique, so a failure here is non-fatal — but visible.
            logger.debug("Could not clear Redis cancel flag for job %s: %s", job_id, exc)
    with _cancellation_lock:
        _cancelled_job_ids.discard(job_id)


def make_cancel_checker(job_id: str, redis_store: Any | None = None) -> Callable[[], bool]:
    """Return a zero-argument callable that returns True if job_id was cancelled."""
    # Capture redis_store if provided, else check memory only
    return lambda: is_job_cancelled(job_id, redis_store)


def create_job(
    index: SearchIndex,
    job_id: str,
    source_path: str,
    status: str = "pending",
    stage: str = "queued",
    durability: str = "durable",
    *,
    selected_pdf: str | None = None,
    selected_excel: str | None = None,
) -> dict[str, Any]:
    """Create a new job record in import_jobs.

    ``durability`` dit la VÉRITÉ de l'acceptation : 'durable' (le job est dans
    la file Redis, il survivra à ce processus), 'process_memory' (repli de
    développement : le job meurt avec le processus) ou 'sync' (traité dans la
    requête). Le champ est écrit à la création, jamais deviné plus tard.

    ``selected_pdf`` / ``selected_excel`` : les choix MANUELS de l'opérateur,
    persistés en base (défaut A06 de l'audit du 2026-10-08). Ils ne vivaient que
    dans la charge Redis : une fois cette charge perdue, la reprise ré-enfilait
    le job avec « premier PDF trouvé » — c'est-à-dire un AUTRE document que
    celui que l'opérateur avait désigné, sans que rien ne le signale. Le
    registre de vérité doit porter ce qui définit le travail à refaire.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with index.connect() as conn:
        if index.is_postgres:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO import_jobs (
                        id, status, progress, stage, source_path, error, result,
                        created_at, updated_at, durability, selected_pdf, selected_excel
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now(), %s, %s, %s)
                    """,
                    (job_id, status, 0, stage, source_path, None, None, durability,
                     selected_pdf, selected_excel),
                )
        else:
            conn.execute(
                """
                INSERT INTO import_jobs (
                    id, status, progress, stage, source_path, error, result,
                    created_at, updated_at, durability, selected_pdf, selected_excel
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, status, 0, stage, source_path, None, None, now_iso, now_iso,
                 durability, selected_pdf, selected_excel),
            )

    return {
        "id": job_id,
        "status": status,
        "progress": 0,
        "stage": stage,
        "source_path": source_path,
        "error": None,
        "result": None,
        "durability": durability,
        "selected_pdf": selected_pdf,
        "selected_excel": selected_excel,
        "created_at": now_iso,
        "updated_at": now_iso,
    }


def update_job_source_path(
    index: SearchIndex,
    job_id: str,
    source_path: str,
    *,
    selected_pdf: str | None = None,
    selected_excel: str | None = None,
) -> bool:
    """Fait suivre le chemin de reprise d'un job et ses sélections après relocalisation (A04, R3).

    Appelé quand le staging applicatif est déplacé en quarantaine : la base est
    le registre de vérité, donc la reprise doit retrouver l'archive ET les sélections
    manuelles à leur NOUVELLE place sans édition manuelle.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with index.connect() as conn:
        if index.is_postgres:
            with conn.cursor() as cursor:
                if selected_pdf is not None or selected_excel is not None:
                    clauses = ["source_path = %s"]
                    params = [source_path]
                    if selected_pdf is not None:
                        clauses.append("selected_pdf = %s")
                        params.append(selected_pdf)
                    if selected_excel is not None:
                        clauses.append("selected_excel = %s")
                        params.append(selected_excel)
                    clauses.append("updated_at = now()")
                    params.append(job_id)
                    cursor.execute(f"UPDATE import_jobs SET {', '.join(clauses)} WHERE id = %s", params)
                else:
                    cursor.execute(
                        "UPDATE import_jobs SET source_path = %s, updated_at = now() WHERE id = %s",
                        (source_path, job_id),
                    )
                return cursor.rowcount > 0
        if selected_pdf is not None or selected_excel is not None:
            clauses = ["source_path = ?"]
            params = [source_path]
            if selected_pdf is not None:
                clauses.append("selected_pdf = ?")
                params.append(selected_pdf)
            if selected_excel is not None:
                clauses.append("selected_excel = ?")
                params.append(selected_excel)
            clauses.append("updated_at = ?")
            params.extend([now_iso, job_id])
            cursor = conn.execute(f"UPDATE import_jobs SET {', '.join(clauses)} WHERE id = ?", params)
        else:
            cursor = conn.execute(
                "UPDATE import_jobs SET source_path = ?, updated_at = ? WHERE id = ?",
                (source_path, now_iso, job_id),
            )
        return cursor.rowcount > 0


def update_job(
    index: SearchIndex,
    job_id: str,
    status: str | None = None,
    progress: int | None = None,
    stage: str | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    failure_reason: str | None = None,
    *,
    expected_worker: str | None = None,
) -> dict[str, Any] | None:
    """Update fields of an active job."""
    if is_job_cancelled(job_id) and status != "cancelled":
        return get_job(index, job_id)

    now_iso = datetime.now(timezone.utc).isoformat()
    fields: list[str] = []
    values: list[Any] = []

    if status is not None:
        fields.append("status")
        values.append(status)
    if progress is not None:
        fields.append("progress")
        values.append(progress)
    if stage is not None:
        fields.append("stage")
        values.append(stage)
    if error is not None:
        fields.append("error")
        values.append(error)
    if failure_reason is not None:
        fields.append("failure_reason")
        values.append(failure_reason)
    if result is not None:
        fields.append("result")
        values.append(json.dumps(result, ensure_ascii=False))

    if not fields:
        return get_job(index, job_id)

    rowcount = 0
    with index.connect() as conn:
        if index.is_postgres:
            import psycopg2.extras

            set_clauses = [f"{f} = %s" for f in fields]
            set_clauses.append("updated_at = now()")
            extra_where = "" if status == "cancelled" else " AND status != 'cancelled'"
            if expected_worker is not None:
                extra_where += " AND (claimed_by = %s OR (claimed_by IS NULL AND status NOT IN ('completed', 'failed', 'cancelled', 'upload_incomplete', 'needs_review')))"
            sql = f"UPDATE import_jobs SET {', '.join(set_clauses)} WHERE id = %s{extra_where}"

            pg_values = []
            for f, v in zip(fields, values):
                if f == "result" and result is not None:
                    pg_values.append(psycopg2.extras.Json(result))
                else:
                    pg_values.append(v)
            pg_values.append(job_id)
            if expected_worker is not None:
                pg_values.append(expected_worker)

            with conn.cursor() as cursor:
                cursor.execute(sql, pg_values)
                rowcount = cursor.rowcount
        else:
            set_clauses = [f"{f} = ?" for f in fields]
            set_clauses.append("updated_at = ?")
            extra_where = "" if status == "cancelled" else " AND status != 'cancelled'"
            if expected_worker is not None:
                extra_where += " AND (claimed_by = ? OR (claimed_by IS NULL AND status NOT IN ('completed', 'failed', 'cancelled', 'upload_incomplete', 'needs_review')))"
            sql = f"UPDATE import_jobs SET {', '.join(set_clauses)} WHERE id = ?{extra_where}"
            sqlite_values = list(values) + [now_iso, job_id]
            if expected_worker is not None:
                sqlite_values.append(expected_worker)
            cur = conn.execute(sql, sqlite_values)
            rowcount = cur.rowcount

    # 4.10: check rowcount — if 0 and job does not exist, return None (do not silently succeed)
    if rowcount == 0:
        existing = get_job(index, job_id)
        if existing is None:
            logger.warning("update_job: job %s not found", job_id)
            return None
        # If job exists but was not updated due to cancelled guard, return existing
        return existing

    return get_job(index, job_id)


def get_job(index: SearchIndex, job_id: str) -> dict[str, Any] | None:
    """Retrieve job details by job_id."""
    with index.connect() as conn:
        if index.is_postgres:
            import psycopg2.extras

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, status, progress, stage, source_path, error, result,
                           created_at, updated_at, attempts, claimed_by,
                           extract(epoch FROM heartbeat_at) AS heartbeat_epoch,
                           failure_reason, durability, selected_pdf, selected_excel
                    FROM import_jobs WHERE id = %s
                    """,
                    (job_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                if isinstance(data.get("created_at"), datetime):
                    data["created_at"] = data["created_at"].isoformat()
                if isinstance(data.get("updated_at"), datetime):
                    data["updated_at"] = data["updated_at"].isoformat()
                return data

        cursor = conn.execute(
            """
            SELECT id, status, progress, stage, source_path, error, result, created_at, updated_at,
                   attempts, claimed_by, heartbeat_at, failure_reason, durability,
                   selected_pdf, selected_excel
            FROM import_jobs WHERE id = ?
            """,
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        res_val = row["result"]
        parsed_result = None
        if res_val:
            try:
                parsed_result = json.loads(res_val) if isinstance(res_val, str) else res_val
            except Exception:
                parsed_result = None
        return {
            "id": row["id"],
            "status": row["status"],
            "progress": row["progress"],
            "stage": row["stage"],
            "source_path": row["source_path"],
            "error": row["error"],
            "result": parsed_result,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "attempts": row["attempts"] if "attempts" in row.keys() else 0,
            "claimed_by": row["claimed_by"] if "claimed_by" in row.keys() else None,
            "heartbeat_at": row["heartbeat_at"] if "heartbeat_at" in row.keys() else None,
            "failure_reason": row["failure_reason"] if "failure_reason" in row.keys() else None,
            "durability": row["durability"] if "durability" in row.keys() else None,
            "selected_pdf": row["selected_pdf"] if "selected_pdf" in row.keys() else None,
            "selected_excel": row["selected_excel"] if "selected_excel" in row.keys() else None,
        }


def cancel_job(index: SearchIndex, job_id: str) -> dict[str, Any] | None:
    """Request cooperative cancellation of a job."""
    register_job_cancel(job_id)
    return update_job(
        index,
        job_id,
        status="cancelled",
        stage="cancelled",
        error="Job was cancelled by user",
    )


def recover_stale_jobs(
    index: SearchIndex,
    heartbeat_threshold_seconds: int = 300,
    redis_store: Any | None = None,
) -> int:
    """Marque en échec les jobs abandonnés — mais PAS ceux qui attendent en file.

    Défaut réel corrigé (2026-10-06) : la version précédente marquait en échec
    tout job ``pending``/``running`` plus vieux que le seuil, y compris ceux qui
    attendaient sagement dans la file Redis. Un backlog d'imports ralentissait le
    traitement, et au bout de 5 minutes les jobs en attente s'auto-détruisaient
    (« Server restarted while job was running ») — l'exploitant devait alors les
    relancer à la main.

    Désormais : on LISTE d'abord les candidats, on écarte ceux qui sont encore
    dans la file (quand Redis est joignable), puis on ne marque en échec que les
    vrais orphelins. La supervision (``jobs_actifs``) reste la même.
    """
    from datetime import timedelta

    cutoff_iso = (datetime.now(timezone.utc) - timedelta(seconds=heartbeat_threshold_seconds)).isoformat()
    with index.connect() as conn:
        if index.is_postgres:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id FROM import_jobs
                    WHERE status IN ('pending', 'running')
                      AND updated_at < now() - (%s || ' seconds')::interval
                    """,
                    (str(heartbeat_threshold_seconds),),
                )
                candidats = [str(ligne[0]) for ligne in cursor.fetchall()]
        else:
            candidats = [
                str(ligne[0])
                for ligne in conn.execute(
                    """
                    SELECT id FROM import_jobs
                    WHERE status IN ('pending', 'running') AND updated_at < ?
                    """,
                    (cutoff_iso,),
                ).fetchall()
            ]

        orphelins = candidats
        if redis_store is not None and candidats:
            restants: list[str] = []
            for job_id in candidats:
                try:
                    if redis_store.job_est_dans_file(job_id):
                        # Toujours en file : ce n'est pas un job mort, il attend.
                        continue
                except Exception as exc:
                    # Dans le doute on ne marque PAS en échec : « attendre » est
                    # réversible, « échoué » ne l'est pas.
                    logger.debug("Présence en file indéterminée pour %s : %s", job_id, exc)
                    continue
                restants.append(job_id)
            orphelins = restants

        if not orphelins:
            return 0

        if index.is_postgres:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE import_jobs
                    SET status = 'failed',
                        stage = 'failed',
                        error = 'Server restarted while job was running',
                        failure_reason = 'job orphelin au redémarrage (aucune trace en file)',
                        claimed_by = NULL,
                        heartbeat_at = NULL,
                        updated_at = now()
                    WHERE id = ANY(%s)
                    """,
                    (orphelins,),
                )
                count = cursor.rowcount
        else:
            now_iso = datetime.now(timezone.utc).isoformat()
            count = 0
            for job_id in orphelins:
                cursor = conn.execute(
                    """
                    UPDATE import_jobs
                    SET status = 'failed',
                        stage = 'failed',
                        error = 'Server restarted while job was running',
                        failure_reason = 'job orphelin au redémarrage (aucune trace en file)',
                        claimed_by = NULL,
                        heartbeat_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now_iso, job_id),
                )
                count += cursor.rowcount

    if count > 0:
        logger.warning("Recovered %d stale import job(s) left in running/pending state.", count)
    return max(0, count)


# ---------------------------------------------------------------------------
# Durable job lifecycle (migration 020) — la base est le registre de vérité
# ---------------------------------------------------------------------------
#
# États d'un job d'import et transitions autorisées :
#
#   pending   (queued)      → running (processing) → completed
#                                                 → failed
#                                                 → upload_incomplete / needs_review
#                                                 → cancelled
#   running   (processing)  → pending  (RECOVERABLE : worker mort, job remis
#                                       en file — c'est l'état « récupérable »
#                                       exposé par /health et /lots)
#   pending   (queued)      → failed  avec failure_reason='dead_letter: …'
#                                       quand les tentatives sont épuisées
#
# Aucun état « accepté durablement » n'est écrit pour un job resté en mémoire :
# c'est la colonne ``durability`` qui le dit ('durable', 'process_memory',
# 'sync'). Un job 'process_memory' disparaît avec son processus, c'est assumé
# et visible.

STATUTS_ACTIFS = ("pending", "running")
STATUTS_TERMINAUX = ("completed", "failed", "cancelled", "upload_incomplete", "needs_review")


def marquer_job_claim(index: SearchIndex, job_id: str, worker_id: str) -> bool:
    """Enregistre le worker qui prend le job et incrémente le compteur de tentatives.

    Retourne False si le job n'existe pas (jamais d'échec silencieux) : un
    worker qui ne trouve pas sa ligne en base ne doit pas croire qu'il travaille
    sur un job suivi.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with index.connect() as conn:
        if index.is_postgres:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE import_jobs
                    SET status = 'running', claimed_by = %s, heartbeat_at = now(),
                        attempts = attempts + 1, stage = 'starting', updated_at = now()
                    WHERE id = %s
                    """,
                    (worker_id, job_id),
                )
                return cursor.rowcount > 0
        cursor = conn.execute(
            """
            UPDATE import_jobs
            SET status = 'running', claimed_by = ?, heartbeat_at = ?, attempts = attempts + 1,
                stage = 'starting', updated_at = ?
            WHERE id = ?
            """,
            (worker_id, now_iso, now_iso, job_id),
        )
        return cursor.rowcount > 0


def heartbeat_job(index: SearchIndex, job_id: str, expected_worker: str | None = None) -> bool:
    """Met à jour le battement de cœur du job (supervision + reprise)."""
    with index.connect() as conn:
        if index.is_postgres:
            with conn.cursor() as cursor:
                if expected_worker is not None:
                    cursor.execute("UPDATE import_jobs SET heartbeat_at = now() WHERE id = %s AND claimed_by = %s", (job_id, expected_worker))
                else:
                    cursor.execute("UPDATE import_jobs SET heartbeat_at = now() WHERE id = %s", (job_id,))
                return cursor.rowcount > 0
        now_iso = datetime.now(timezone.utc).isoformat()
        if expected_worker is not None:
            cursor = conn.execute("UPDATE import_jobs SET heartbeat_at = ? WHERE id = ? AND claimed_by = ?", (now_iso, job_id, expected_worker))
        else:
            cursor = conn.execute("UPDATE import_jobs SET heartbeat_at = ? WHERE id = ?", (now_iso, job_id))
        return cursor.rowcount > 0


def remettre_en_file(
    index: SearchIndex,
    job_id: str,
    *,
    raison: str,
    tentative_durable: bool = True,
) -> bool:
    """Remet un job en attente (état RÉCUPÉRABLE) après la mort de son worker.

    ``tentative_durable=False`` (redélivrance déjà épuisée) marque le job en
    échec définitif AVEC la raison : c'est le seul chemin acceptable pour
    « il ne sera plus jamais repris », jamais une disparition silencieuse.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with index.connect() as conn:
        if index.is_postgres:
            with conn.cursor() as cursor:
                if tentative_durable:
                    cursor.execute(
                        """
                        UPDATE import_jobs
                        SET status = 'pending', stage = 'requeued', claimed_by = NULL,
                            heartbeat_at = NULL, error = %s, failure_reason = %s, updated_at = now()
                        WHERE id = %s AND status IN ('pending', 'running')
                        """,
                        (raison, raison, job_id),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE import_jobs
                        SET status = 'failed', stage = 'dead_letter', claimed_by = NULL,
                            heartbeat_at = NULL, error = %s, failure_reason = %s, updated_at = now()
                        WHERE id = %s AND status IN ('pending', 'running')
                        """,
                        (raison, raison, job_id),
                    )
                return cursor.rowcount > 0
        if tentative_durable:
            cursor = conn.execute(
                """
                UPDATE import_jobs
                SET status = 'pending', stage = 'requeued', claimed_by = NULL,
                    heartbeat_at = NULL, error = ?, failure_reason = ?, updated_at = ?
                WHERE id = ? AND status IN ('pending', 'running')
                """,
                (raison, raison, now_iso, job_id),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE import_jobs
                SET status = 'failed', stage = 'dead_letter', claimed_by = NULL,
                    heartbeat_at = NULL, error = ?, failure_reason = ?, updated_at = ?
                WHERE id = ? AND status IN ('pending', 'running')
                """,
                (raison, raison, now_iso, job_id),
            )
        return cursor.rowcount > 0


def terminer_job(
    index: SearchIndex,
    job_id: str,
    *,
    status: str,
    failure_reason: str | None = None,
    expected_worker: str | None = None,
) -> bool:
    """Ferme un job : libère le worker propriétaire et trace la raison (avec contrôle optionnel de fencing)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with index.connect() as conn:
        if index.is_postgres:
            with conn.cursor() as cursor:
                if expected_worker is not None:
                    cursor.execute(
                        """
                        UPDATE import_jobs
                        SET status = %s, claimed_by = NULL, heartbeat_at = NULL,
                            failure_reason = %s, updated_at = now()
                        WHERE id = %s AND (claimed_by = %s OR (claimed_by IS NULL AND status NOT IN ('completed', 'failed', 'cancelled', 'upload_incomplete', 'needs_review')))
                        """,
                        (status, failure_reason, job_id, expected_worker),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE import_jobs
                        SET status = %s, claimed_by = NULL, heartbeat_at = NULL,
                            failure_reason = %s, updated_at = now()
                        WHERE id = %s
                        """,
                        (status, failure_reason, job_id),
                    )
                return cursor.rowcount > 0
        if expected_worker is not None:
            cursor = conn.execute(
                """
                UPDATE import_jobs
                SET status = ?, claimed_by = NULL, heartbeat_at = NULL,
                    failure_reason = ?, updated_at = ?
                WHERE id = ? AND (claimed_by = ? OR (claimed_by IS NULL AND status NOT IN ('completed', 'failed', 'cancelled', 'upload_incomplete', 'needs_review')))
                """,
                (status, failure_reason, now_iso, job_id, expected_worker),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE import_jobs
                SET status = ?, claimed_by = NULL, heartbeat_at = NULL,
                    failure_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, failure_reason, now_iso, job_id),
            )
        return cursor.rowcount > 0


def compter_jobs_par_statut(index: SearchIndex) -> dict[str, int]:
    """Comptage par statut pour /health : les jobs actifs ne doivent jamais
    être invisibles pour l'exploitant."""
    with index.connect() as conn:
        if index.is_postgres:
            with conn.cursor() as cursor:
                cursor.execute("SELECT status, count(*) FROM import_jobs GROUP BY status")
                lignes = cursor.fetchall()
        else:
            lignes = conn.execute("SELECT status, count(*) FROM import_jobs GROUP BY status").fetchall()
    return {str(ligne[0]): int(ligne[1]) for ligne in lignes}


def _resultat_job_json(valeur: Any) -> dict[str, Any] | None:
    """``result`` d'un job, quel que soit l'encodage rendu par le moteur.

    PostgreSQL (JSONB) rend un dictionnaire ; SQLite peut rendre la chaîne JSON
    telle qu'elle a été écrite. Une charge illisible donne ``None`` — jamais une
    exception : la boucle de rétention doit continuer à protéger les AUTRES
    entrées plutôt que d'abandonner au premier job douteux.
    """
    if isinstance(valeur, dict):
        return valeur
    if isinstance(valeur, str):
        try:
            charge = json.loads(valeur)
        except ValueError:
            return None
        return charge if isinstance(charge, dict) else None
    return None


def jobs_non_preserves(index: SearchIndex, *, limite: int = 5000) -> list[dict[str, Any]]:
    """Jobs dont la copie locale est encoré NÉCESSAIRE (retention, audit 2026-10-08).

    Deux familles, dans l'ordre du risque :

    * **travail actif** (``pending``/``running``) : la source locale est l'entrée
      du job — la supprimer condamne un import en cours sans le dire ;
    * **copie non prouvée** (``result.all_verified`` absent ou faux, y compris
      ``upload_incomplete`` et ``failed``) : la copie locale est alors la SEULE
      copie du document, et la reprise (``retry_upload``) en a besoin.

    Défaut visé (investigation « rétention vs travail actif ») : l'élagage des
    uploads et des rapports ne consultait QUE l'âge (mtime). Un dossier déposé
    7 jours plus tôt et toujours en attente (worker arrêté, file en panne) était
    donc supprimé — l'import devenait irrécupérable SANS aucun signal, alors que
    la rétention est présentée comme un rangement.

    Le résultat est volontairement limité (``limite``) : au-delà, l'appelant
    DOIT refuser d'élaguer (voir ``retention.chemins_proteges``) plutôt que de
    supprimer ce qu'il n'a pas pu examiner.
    """
    with index.connect() as conn:
        if index.is_postgres:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, status, source_path, result
                FROM import_jobs
                WHERE status IN ('pending', 'running')
                   OR result IS NULL
                   OR coalesce(result->>'all_verified', 'false') <> 'true'
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (limite,),
            )
            lignes = cursor.fetchall()
            cursor.close()
        else:
            # SQLite (mode développement) : pas de JSONB — ``result`` peut être la
            # chaîne JSON telle qu'écrite, d'où la normalisation. Le volume y est
            # celui d'un poste, pas d'un serveur.
            cursor = conn.execute(
                "SELECT id, status, source_path, result FROM import_jobs ORDER BY updated_at DESC LIMIT ?",
                (limite,),
            )
            lignes = []
            for ligne in cursor.fetchall():
                resultat = _resultat_job_json(ligne[3])
                preserve = (
                    str(ligne[1]) in ("pending", "running")
                    or not isinstance(resultat, dict)
                    or str(resultat.get("all_verified")).lower() != "true"
                )
                if preserve:
                    lignes.append((ligne[0], ligne[1], ligne[2], resultat))
    jobs: list[dict[str, Any]] = []
    for identifiant, statut, source_path, resultat in lignes:
        # Normalisation UNIQUE : les deux branches ci-dessus alimentent ``lignes``
        # avec un dictionnaire ou None (un résultat illisible devient None, donc
        # « non préservé » : la prudence va toujours dans le sens de la
        # conservation de l'entrée de travail).
        charge = _resultat_job_json(resultat)
        jobs.append(
            {
                "id": str(identifiant),
                "status": str(statut),
                "source_path": None if source_path is None else str(source_path),
                "result": charge,
            }
        )
    return jobs


def jobs_actifs(index: SearchIndex, limite: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    """Jobs en attente ou en cours, avec worker propriétaire et ancienneté.

    Sert la supervision opérateur (« qui traite quoi, depuis quand ») sans
    lire Redis : c'est la base qui répond.

    ``offset`` existe pour la RÉCONCILIATION (A06) : la file d'un atelier peut
    dépasser une page, et un job jamais lu est un job jamais repris. Le tri
    reste ``updated_at DESC`` — déterministe pour une pagination stable.
    """
    with index.connect() as conn:
        if index.is_postgres:
            import psycopg2.extras

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, status, stage, progress, claimed_by, attempts,
                           extract(epoch FROM heartbeat_at) AS heartbeat_epoch,
                           extract(epoch FROM updated_at) AS updated_epoch,
                           source_path, error, durability, selected_pdf, selected_excel
                    FROM import_jobs
                    WHERE status IN ('pending', 'running')
                    ORDER BY updated_at DESC, id
                    LIMIT %s OFFSET %s
                    """,
                    (limite, offset),
                )
                return [dict(ligne) for ligne in cursor.fetchall()]
        lignes = conn.execute(
            """
            SELECT id, status, stage, progress, claimed_by, attempts,
                   NULL AS heartbeat_epoch, NULL AS updated_epoch,
                   source_path, error, durability, selected_pdf, selected_excel
            FROM import_jobs
            WHERE status IN ('pending', 'running')
            ORDER BY updated_at DESC, id
            LIMIT ? OFFSET ?
            """,
            (limite, offset),
        ).fetchall()
    return [dict(ligne) for ligne in lignes]
