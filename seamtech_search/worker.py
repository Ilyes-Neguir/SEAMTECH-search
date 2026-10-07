"""Redis background worker for asynchronous reference imports and S3 archival.

Handles decoupled task execution:
- Dequeues import jobs from Redis (RPUSH / BLPOP)
- Runs extraction, analysis, and report generation
- Uploads all raw documents and reports to S3/MinIO/Cloudflare R2
- Updates job state in Redis cache and PostgreSQL
- Purges temporary scratch/staging directories only when every artifact is verified
- Revendique chaque tâche (claim à TTL + battement de cœur) et reprend les
  tâches dont le worker est mort — voir docs/FILE_DURABLE.md

Garanties honnêtes : Redis ne fournit PAS « exactement une fois ». Ce module
garantit qu'une tâche n'est jamais perdue silencieusement (reprise ou lettre
morte explicite) et que la redélivrance est SÛRE (garde d'idempotence sur les
jobs déjà terminés, clé d'idempotence en base pour les dossiers).
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .import_pipeline import ImportCancelledError, import_folder, quarantine_root, staging_root
from .jobs import (
    clear_job_cancel,
    get_job,
    heartbeat_job,
    jobs_actifs,
    make_cancel_checker,
    marquer_job_claim,
    remettre_en_file,
    terminer_job,
    update_job,
)
from .redis_store import RedisStore

if TYPE_CHECKING:
    from .config import AppConfig
    from .indexer import SearchIndex

logger = logging.getLogger("seamtech_search.worker")

_worker_thread: threading.Thread | None = None
_worker_running: bool = False
#: Intervalle de rafraîchissement du battement de cœur (worker + claims).
_HEARTBEAT_INTERVAL_SECONDS = 10.0


def identifiant_worker() -> str:
    """Identité stable d'un processus worker : hôte + PID.

    Elle est écrite en base (``import_jobs.claimed_by``, ``lot_import.worker_id``)
    pour répondre à « qui traitait ce job, et est-il encore vivant ? ».
    """
    # platform.node() plutôt que socket.gethostname() : RG14 interdit toute
    # dépendance réseau dans seamtech_search/ (un import de socket fait échouer
    # le garde-fou automatisé, et « il ne s'en sert pas » n'est pas vérifiable).
    return f"{platform.node() or 'worker'}-{os.getpid()}"


def process_import_task(
    payload: dict[str, Any],
    config: AppConfig,
    index: SearchIndex,
    redis_store: RedisStore | None = None,
    *,
    worker_id: str | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Execute a single import job payload, upload to S3, and clean up temporary files.

    IDEMPOTENCE : un job déjà ``completed`` en base n'est PAS réexécuté — c'est
    ce qui rend une redélivrance Redis (worker mort après écriture, acquittement
    perdu) sans effet de bord, plutôt que de dupliquer rapports et index.

    ``heartbeat`` : callback appelé à chaque étape pour rafraîchir le claim
    Redis et ``import_jobs.heartbeat_at``. Un import long (gros PDF, upload
    lent) ne doit pas paraître « mort » au superviseur.
    """
    job_id = payload["job_id"]

    # --- Garde d'idempotence (redélivrance) -------------------------------
    existant = get_job(index, job_id)
    if existant is not None and existant.get("status") == "completed" and existant.get("result"):
        logger.info(
            "Livraison dupliquée du job %s ignorée : il est déjà terminé en base "
            "(conséquence : aucun doublon d'objets, de rapports ni d'index).",
            job_id,
        )
        return {"job_id": job_id, "status": "completed", "duplicate_delivery": True}

    if payload.get("kind") == "lot":
        return _process_lot_task(payload, config, index, redis_store, worker_id=worker_id, heartbeat=heartbeat)

    source_path = Path(payload["source_path"])
    selected_pdf = Path(payload["selected_pdf"]) if payload.get("selected_pdf") else None
    selected_excel = Path(payload["selected_excel"]) if payload.get("selected_excel") else None

    cancel_check = make_cancel_checker(job_id, redis_store)

    def progress_cb(stage: str, percent: int) -> None:
        update_job(index, job_id, status="running", progress=percent, stage=stage)
        heartbeat_job(index, job_id)
        if heartbeat is not None:
            heartbeat()
        if redis_store and redis_store.is_configured():
            redis_store.set_heartbeat(job_id)
            redis_store.update_job(job_id, {"status": "running", "progress": percent, "stage": stage})

    try:
        update_job(index, job_id, status="running", progress=5, stage="starting")
        if redis_store and redis_store.is_configured():
            redis_store.update_job(job_id, {"status": "running", "progress": 5, "stage": "starting"})

        result = import_folder(
            source=source_path,
            config=config,
            index=index,
            selected_pdf=selected_pdf,
            import_id=job_id,
            progress_callback=progress_cb,
            cancel_check=cancel_check,
            selected_excel=selected_excel,
        )

        if cancel_check():
            raise ImportCancelledError("Job was cancelled by user")

        from dataclasses import asdict

        final_payload = asdict(result)

        # Determine if upload is truly complete: status == uploaded AND every file has verified key
        all_verified = getattr(result, "all_verified", False)
        upload_status = getattr(result, "upload_status", "not_configured")
        files_have_keys = True
        if result.files:
            for f in result.files:
                # ImportFile dataclass
                key = getattr(f, "object_key", None)
                if not key:
                    files_have_keys = False
                    break

        truly_uploaded = upload_status == "uploaded" and all_verified and files_have_keys

        if truly_uploaded:
            final_status = "completed" if result.status == "completed" else result.status
        elif upload_status in ("not_configured", "not_applicable"):
            final_status = "completed" if result.status == "completed" else result.status
        else:
            # Upload failed or partial — mark upload_incomplete, keep files, move to quarantine
            final_status = "upload_incomplete"
            final_payload["status"] = "upload_incomplete"
            final_payload["upload_status"] = "upload_incomplete"
            logger.warning(
                "Import %s upload incomplete (status=%s all_verified=%s) — moving to quarantine",
                job_id,
                upload_status,
                all_verified,
            )
            try:
                q_root = quarantine_root(config)
                q_root.mkdir(parents=True, exist_ok=True)
                resolved_source = source_path.expanduser().resolve()
                if resolved_source.exists():
                    dest = q_root / f"{job_id}_{resolved_source.name}"
                    counter = 1
                    original_dest = dest
                    while dest.exists():
                        counter += 1
                        dest = original_dest.parent / f"{original_dest.name}-{counter}"
                    shutil.move(str(resolved_source), str(dest))
                    logger.info("Moved failed import %s to quarantine %s", job_id, dest)
                    final_payload["quarantine_path"] = str(dest)
            except Exception as q_err:
                logger.warning("Failed to quarantine %s: %s", source_path, q_err)

        update_job(
            index,
            job_id,
            status=final_status,
            progress=100,
            stage="done" if final_status != "upload_incomplete" else "upload_incomplete",
            result=final_payload,
        )
        if redis_store and redis_store.is_configured():
            redis_store.update_job(
                job_id,
                {
                    "status": final_status,
                    "progress": 100,
                    "stage": "done" if final_status != "upload_incomplete" else "upload_incomplete",
                    "result": final_payload,
                },
            )

        # Ephemeral scratch cleanup: only when truly uploaded and verified
        if truly_uploaded or upload_status in ("not_configured", "not_applicable"):
            staging_dir = staging_root(config)
            try:
                if final_status == "upload_incomplete":
                    # Already quarantined, skip purge
                    pass
                else:
                    resolved_source = source_path.expanduser().resolve()
                    # Source may have been moved to quarantine already if incomplete, but we are in complete branch
                    if not resolved_source.exists():
                        # Already purged or moved, nothing to do
                        pass
                    else:
                        try:
                            resolved_staging = staging_dir.resolve()
                            is_staged = resolved_staging in resolved_source.parents or resolved_source.parent == resolved_staging
                        except Exception:
                            is_staged = False
                        should_purge = config.delete_local_after_upload or is_staged
                        if should_purge:
                            logger.info(
                                "Purging local staged scratch directory %s to keep VPS disk stateless",
                                resolved_source,
                            )
                            shutil.rmtree(resolved_source, ignore_errors=True)
            except Exception as cleanup_err:
                logger.warning("Failed to purge scratch directory %s: %s", source_path, cleanup_err)
        else:
            logger.warning(
                "Skipping purge of %s: upload_status=%s all_verified=%s — keeping for retry/quarantine",
                source_path,
                upload_status,
                all_verified,
            )

        return final_payload

    except ImportCancelledError:
        update_job(index, job_id, status="cancelled", stage="cancelled", error="Job was cancelled by user")
        if redis_store and redis_store.is_configured():
            redis_store.update_job(job_id, {"status": "cancelled", "stage": "cancelled", "error": "Job was cancelled by user"})
        return {"job_id": job_id, "status": "cancelled"}
    except Exception as exc:
        logger.exception("Import job %s failed: %s", job_id, exc)
        update_job(index, job_id, status="failed", stage="failed", error=str(exc))
        if redis_store and redis_store.is_configured():
            redis_store.update_job(job_id, {"status": "failed", "stage": "failed", "error": str(exc)})
        return {"job_id": job_id, "status": "failed", "error": str(exc)}
    finally:
        clear_job_cancel(job_id, redis_store)


def _process_lot_task(
    payload: dict[str, Any],
    config: AppConfig,
    index: SearchIndex,
    redis_store: RedisStore | None,
    *,
    worker_id: str | None,
    heartbeat: Callable[[], None] | None,
) -> dict[str, Any]:
    """Exécute un lot multi-dossiers (porte A bis) comme une tâche durable.

    Le lot est repris EXACTEMENT là où il s'était arrêté : ``executer_lot`` ne
    traite que les lignes ``en_attente`` de ``lot_dossier``. Un worker tué en
    plein lot laisse donc un lot « relance », pas une progression perdue.
    """
    from .fiches.depot import DepotImpossible, annuler_lot, executer_lot

    job_id = payload["job_id"]
    id_lot = int(payload["id_lot"])
    doit_continuer = None
    if redis_store is not None:
        from .jobs import is_job_cancelled

        def doit_continuer() -> bool:  # type: ignore[misc]
            # SENS du prédicat : « continuer ? » — True tant qu'aucune
            # annulation n'est demandée. Le renvoyer à l'envers (ce que faisait
            # la première version) annulait TOUT lot durable au premier
            # dossier : le drapeau Redis disait « pas annulé » = False =
            # « ne pas continuer ». Défaut réel corrigé le 2026-10-06, mis en
            # évidence par tests/test_file_durable_postgres.py.
            return not is_job_cancelled(job_id, redis_store)

    def progress_cb(dossiers_traites: int, total: int) -> None:
        pourcentage = int(100 * dossiers_traites / total) if total else 0
        update_job(index, job_id, status="running", progress=pourcentage, stage="lot")
        heartbeat_job(index, job_id)
        if heartbeat is not None:
            heartbeat()
        if redis_store and redis_store.is_configured():
            redis_store.update_job(job_id, {"status": "running", "progress": pourcentage, "stage": "lot"})

    try:
        etat = executer_lot(
            index,
            id_lot,
            doit_continuer=doit_continuer,
            progress_cb=progress_cb,
            worker_id=worker_id,
        )
    except DepotImpossible as erreur:
        message = f"Lot #{id_lot} impossible : {erreur}"
        update_job(index, job_id, status="failed", stage="failed", error=message)
        terminer_job(index, job_id, status="failed", failure_reason=message)
        return {"job_id": job_id, "status": "failed", "error": message, "id_lot": id_lot}

    if etat.get("annule"):
        message = f"Lot #{id_lot} annulé à la demande de l'opérateur."
        try:
            annuler_lot(index, id_lot)
        except Exception as exc:  # pragma: no cover - défensif
            logger.warning("Lot #%s : impossible de marquer l'annulation : %s", id_lot, exc)
        update_job(index, job_id, status="cancelled", stage="cancelled", error=message, result=etat)
        terminer_job(index, job_id, status="cancelled", failure_reason=message)
        return {"job_id": job_id, "status": "cancelled", "id_lot": id_lot}

    statut = "completed" if int(etat.get("nb_echecs", 0)) == 0 else "needs_review"
    update_job(index, job_id, status=statut, progress=100, stage="done", result=etat)
    terminer_job(
        index,
        job_id,
        status=statut,
        failure_reason=None if statut == "completed" else f"{etat.get('nb_echecs')} dossier(s) en échec",
    )
    if redis_store and redis_store.is_configured():
        redis_store.update_job(job_id, {"status": statut, "progress": 100, "stage": "done", "result": etat})
    return {"job_id": job_id, "status": statut, "id_lot": id_lot, "etat": etat}


class _BattementDeCoeur(threading.Thread):
    """Rafraîchit l'enregistrement du worker et les claims de ses tâches en cours.

    Un worker vivant dont le claim expire est un défaut réel : un autre worker
    reprendrait la tâche et la traiterait en parallèle. Le fil est daemon et
    s'arrête avec ``_worker_running``.
    """

    def __init__(
        self,
        redis_store: RedisStore,
        worker_id: str,
        *,
        queue_name: str = "imports",
        ttl_seconds: int = 300,
        interval_seconds: float = _HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(daemon=True, name=f"seamtech-heartbeat-{worker_id}")
        self.redis_store = redis_store
        self.worker_id = worker_id
        self.queue_name = queue_name
        self.ttl_seconds = ttl_seconds
        self.interval_seconds = interval_seconds
        self._en_cours: set[str] = set()
        self._verrou = threading.Lock()
        self._arreter = threading.Event()

    def suivre(self, job_id: str) -> None:
        with self._verrou:
            self._en_cours.add(job_id)

    def oublier(self, job_id: str) -> None:
        with self._verrou:
            self._en_cours.discard(job_id)

    def arreter(self) -> None:
        self._arreter.set()

    def run(self) -> None:  # pragma: no cover - fil d'arrière-plan
        while not self._arreter.wait(self.interval_seconds):
            try:
                self.redis_store.enregistrer_worker(self.worker_id, ttl_seconds=int(self.ttl_seconds / 5) or 30)
                with self._verrou:
                    jobs = sorted(self._en_cours)
                self.redis_store.rafraichir_revendications(
                    self.queue_name, jobs, self.worker_id, ttl_seconds=self.ttl_seconds
                )
            except Exception as exc:  # pragma: no cover - défensif
                logger.warning("Battement de cœur du worker %s en échec : %s", self.worker_id, exc)


def _decisions_reprise(redis_store: RedisStore, queue_name: str, max_tentatives: int) -> list[dict[str, Any]]:
    """Lit les reprises de tâches orphelines en tolérant un double de test.

    ``redis_store`` peut être un ``MagicMock`` (tests unitaires) : on exige une
    LISTE de décisions, jamais un objet magique, pour ne pas transformer un test
    en fausse preuve.
    """
    try:
        decisions = redis_store.reprendre_taches_orphelines(queue_name, max_tentatives=max_tentatives)
    except Exception as exc:
        logger.warning("Reprise des tâches orphelines en échec : %s", exc)
        return []
    if not isinstance(decisions, list):
        return []
    return [d for d in decisions if isinstance(d, dict)]


def reconcilier_file(
    index: SearchIndex,
    redis_store: RedisStore,
    *,
    queue_name: str = "imports",
    max_tentatives: int = 3,
) -> dict[str, Any]:
    """Reprise des tâches abandonnées + mise à jour du registre en base.

    Deux cas distincts, tous deux traités :
      1. une tâche est restée dans la liste de traitement avec un claim expiré
         (worker tué) → elle est remise en file, ou mise en lettre morte si ses
         tentatives sont épuisées ;
      2. un job est ``running`` en base mais n'existe plus nulle part dans la
         file (Redis vidé/redémarré sans AOF) → il est ré-enfilé ou marqué en
         échec AVEC la raison, jamais laissé « en cours » pour toujours.
    """
    resume: dict[str, Any] = {"requeued": [], "dead_lettered": [], "relanced_from_db": [], "failed_from_db": []}

    for decision in _decisions_reprise(redis_store, queue_name, max_tentatives):
        job_id = decision.get("job_id")
        if not job_id:
            continue
        if decision.get("action") == "requeued":
            remettre_en_file(
                index,
                str(job_id),
                raison=f"reprise après worker interrompu (tentative {decision.get('attempt')})",
                tentative_durable=True,
            )
            resume["requeued"].append(job_id)
        else:
            remettre_en_file(
                index,
                str(job_id),
                raison=str(decision.get("raison") or "tentatives épuisées"),
                tentative_durable=False,
            )
            resume["dead_lettered"].append(job_id)

    # Cas 2 : jobs en base qui ne sont plus dans la file.
    for job in jobs_actifs(index, limite=200):
        job_id = str(job.get("id"))
        if job.get("status") != "running":
            continue
        try:
            encore_en_file = redis_store.job_est_dans_file(job_id, (queue_name,))
        except Exception:
            encore_en_file = True
        if encore_en_file:
            continue
        attempts = int(job.get("attempts") or 0)
        if attempts >= max_tentatives:
            raison = f"job introuvable dans la file après {attempts} tentative(s) — lettre morte"
            remettre_en_file(index, job_id, raison=raison, tentative_durable=False)
            resume["failed_from_db"].append(job_id)
            continue
        charge = _charge_depuis_job(job)
        if charge is None:
            raison = "job orphelin non ré-enfilable (charge absente) — échec explicite"
            remettre_en_file(index, job_id, raison=raison, tentative_durable=False)
            resume["failed_from_db"].append(job_id)
            continue
        charge["reprise"] = "job orphelin ré-enfilé"
        if redis_store.enqueue_task(queue_name, charge):
            remettre_en_file(index, job_id, raison="job orphelin ré-enfilé", tentative_durable=True)
            resume["relanced_from_db"].append(job_id)
        else:
            logger.warning("Job %s orphelin : la remise en file a échoué (Redis indisponible).", job_id)
    return resume


def _charge_depuis_job(job: dict[str, Any]) -> dict[str, Any] | None:
    """Reconstruit la charge d'une tâche à partir de la base (registre de vérité)."""
    job_id = str(job.get("id"))
    if job_id.startswith("lot-"):
        try:
            return {"job_id": job_id, "kind": "lot", "id_lot": int(job_id.split("-", 1)[1])}
        except (IndexError, ValueError):
            return None
    source = job.get("source_path")
    if not source:
        return None
    return {"job_id": job_id, "source_path": str(source), "selected_pdf": None, "selected_excel": None}


def worker_loop(
    config: AppConfig,
    index: SearchIndex,
    redis_store: RedisStore,
    *,
    worker_id: str | None = None,
    run_once: bool = False,
) -> None:
    """Boucle du worker : reprise, file d'attente, acquittement, lettre morte.

    ``run_once=True`` traite ce qui est disponible puis rend la main (utile pour
    les tests d'intégration et un passage planifié) ; sinon la boucle tourne
    jusqu'à ``stop_background_worker()`` / SIGTERM.
    """
    global _worker_running
    _worker_running = True
    max_attempts = int(getattr(config, "max_task_attempts", 3) or 3)
    claim_ttl = int(getattr(config, "task_claim_ttl_seconds", 300) or 300)
    reclaim_interval = float(getattr(config, "queue_reclaim_interval_seconds", 30) or 30)
    identifiant = worker_id or identifiant_worker()

    logger.info(
        "SEAMTECH worker %s démarré (queue seamtech:queue:imports, tentatives max %s, claim TTL %ss)",
        identifiant,
        max_attempts,
        claim_ttl,
    )
    redis_store.enregistrer_worker(identifiant, ttl_seconds=max(30, claim_ttl // 5))
    battement = _BattementDeCoeur(redis_store, identifiant, ttl_seconds=claim_ttl)
    battement.start()

    # 1. Reprise AVANT de consommer : un job repris ne doit pas attendre.
    try:
        resume = reconcilier_file(index, redis_store, max_tentatives=max_attempts)
        if any(resume.values()):
            logger.warning("Reprise au démarrage du worker : %s", resume)
    except Exception as exc:
        logger.warning("Reprise au démarrage impossible : %s", exc)

    dernier_balayage = time.time()
    try:
        while _worker_running:
            try:
                # 2. Balayage périodique des tâches orphelines.
                if time.time() - dernier_balayage >= reclaim_interval:
                    dernier_balayage = time.time()
                    try:
                        resume = reconcilier_file(index, redis_store, max_tentatives=max_attempts)
                        if any(resume.values()):
                            logger.warning("Reprise périodique : %s", resume)
                    except Exception as exc:
                        logger.warning("Reprise périodique impossible : %s", exc)

                try:
                    redis_store.process_retry_queue("imports")
                except Exception as exc:
                    logger.debug("Could not process retry queue this pass: %s", exc)

                task = redis_store.dequeue_task("imports", timeout=2)
                if not task:
                    if run_once:
                        break
                    continue

                job_id = str(task.get("job_id", "unknown"))
                attempt = int(task.get("attempt", 0))
                logger.info("Worker %s received import task for job %s (attempt %s)", identifiant, job_id, attempt)

                # 3. Revendication (claim) : c'est ce qui distingue « en cours
                # chez un worker vivant » de « orphelin à reprendre ».
                #
                # Atomique et NON VOLANTE : si un autre worker détient encore le
                # verrou, on ne traite pas la tâche. Sans cela, deux workers
                # pouvaient traiter le même job et écraser mutuellement leur
                # résultat (le « au moins une fois » deviendrait « deux fois,
                # écrasées »). La tâche reste dans la liste de traitement, le
                # propriétaire légitime l'acquittera.
                revendique = redis_store.revendiquer_tache(
                    "imports", job_id, identifiant, ttl_seconds=claim_ttl, attempt=attempt
                )
                if not revendique:
                    logger.warning(
                        "Tâche %s ignorée : elle appartient à un autre worker vivant. "
                        "Conséquence : aucun double traitement, le propriétaire l'acquittera.",
                        job_id,
                    )
                    continue
                marquer_job_claim(index, job_id, identifiant)
                battement.suivre(job_id)
                try:
                    result = process_import_task(
                        task,
                        config,
                        index,
                        redis_store,
                        worker_id=identifiant,
                        heartbeat=lambda: battement.suivre(job_id),
                    )
                    redis_store.ack_task("imports", task)
                    statut = str(result.get("status", "unknown"))
                    # `max_task_attempts` compte le nombre TOTAL de tentatives :
                    # avec 3, un job est essayé 3 fois (attempt 0, 1, 2) puis va
                    # en lettre morte. La version précédente comparait
                    # `attempt < max_attempts` et offrait donc une tentative de
                    # plus que ce que l'opérateur avait réglé — un job pouvait
                    # être relancé 4 fois pour un réglage à 3.
                    tentatives_restantes = attempt + 1 < max_attempts
                    # Protection de propriété : si le verrou a expiré pendant un
                    # import très long et qu'un autre worker a repris la tâche,
                    # écrire l'état terminal ici écraserait SON résultat. On
                    # s'abstient et on le dit — la reprise fait autorité.
                    if not redis_store.revendication_appartient_a("imports", job_id, identifiant):
                        logger.warning(
                            "Job %s : verrou perdu (repris par un autre worker) — "
                            "conséquence : cet état terminal n'est PAS écrit, la reprise fait foi.",
                            job_id,
                        )
                        continue
                    if statut in ("failed", "upload_incomplete"):
                        if tentatives_restantes:
                            delay = 2**attempt  # backoff exponentiel borné par le nombre de tentatives
                            logger.info(
                                "Job %s en %s — nouvelle tentative dans %ss (tentative %s/%s)",
                                job_id,
                                statut,
                                delay,
                                attempt + 1,
                                max_attempts,
                            )
                            redis_store.retry_task("imports", task, delay_seconds=delay)
                        else:
                            raison = (
                                f"lettre morte après {attempt + 1} tentative(s) : "
                                f"{result.get('error') or statut}"
                            )
                            logger.warning("Job %s : %s", job_id, raison)
                            redis_store.deadletter_task("imports", task)
                            terminer_job(index, job_id, status="failed", failure_reason=raison)
                    elif statut == "completed":
                        terminer_job(index, job_id, status="completed")
                    elif statut == "cancelled":
                        terminer_job(index, job_id, status="cancelled", failure_reason="annulé par l'opérateur")
                    else:
                        terminer_job(index, job_id, status=statut)
                except Exception as task_exc:
                    logger.exception("Task %s failed with exception: %s", job_id, task_exc)
                    if attempt + 1 < max_attempts:
                        delay = 2**attempt
                        redis_store.retry_task("imports", task, delay_seconds=delay)
                    else:
                        raison = f"lettre morte après exception ({attempt + 1} tentative(s)) : {task_exc}"
                        redis_store.deadletter_task("imports", task)
                        terminer_job(index, job_id, status="failed", failure_reason=raison)
                finally:
                    battement.oublier(job_id)
                    # Libération conditionnelle : ne supprime que SI le verrou est
                    # encore le nôtre (sinon on effacerait celui du repreneur).
                    libere = redis_store.liberer_revendication("imports", job_id, identifiant)
                    if libere == -1:
                        logger.warning(
                            "Job %s : le verrou appartient désormais à un autre worker — "
                            "non libéré (le repreneur reste protégé).",
                            job_id,
                        )
            except Exception as exc:
                logger.error("Error in Redis worker loop: %s", exc)
                time.sleep(1.0)
                if run_once:
                    break
    finally:
        battement.arreter()
        # Arrêt propre : le worker se retire du registre de supervision. Ses
        # éventuels verrous de tâches ne sont PAS supprimés ici — ils expirent,
        # ce qui laisse la reprise jouer son rôle si la tâche était en cours.
        redis_store.desenregistrer_worker(identifiant)
        logger.info("SEAMTECH worker %s arrêté.", identifiant)


def file_durable_disponible(redis_store: RedisStore | None) -> bool:
    """Vrai si un job peut être confié à la file durable (Redis joignable).

    Une seule question, une seule réponse, utilisée par toutes les portes
    d'entrée (/imports, /imports/confirm, /imports/dossier/lot) : c'est ce qui
    garantit qu'aucune route ne « croit » accepter durablement un job resté en
    mémoire. Un double de test sans ``is_configured``/``ping`` renvoie faux —
    le repli explicite est alors annoncé, jamais silencieux.
    """
    if redis_store is None:
        return False
    try:
        if not redis_store.is_configured():
            return False
        return bool(redis_store.ping())
    except Exception as exc:
        logger.warning("File durable indisponible : %s", exc)
        return False


def start_background_worker(config: AppConfig, index: SearchIndex, redis_store: RedisStore) -> None:
    """Start the Redis background worker in a daemon thread if Redis is configured."""
    global _worker_thread
    if not getattr(config, "web_worker_enabled", True):
        # Déploiement avec service `worker` séparé : le web ne doit PAS exécuter
        # les jobs, sinon un redémarrage du web les tuerait (défaut réel corrigé).
        logger.info(
            "Worker en fil désactivé (SEAMTECH_WEB_WORKER_ENABLED=false) : les jobs sont exécutés "
            "par le service worker dédié."
        )
        return
    if not redis_store.is_configured() or not redis_store.ping():
        logger.info("Redis is not configured or offline; background jobs will run in-process.")
        return

    _worker_thread = threading.Thread(
        target=worker_loop,
        args=(config, index, redis_store),
        daemon=True,
        name="seamtech-redis-worker",
    )
    _worker_thread.start()


def stop_background_worker() -> None:
    """Signal the background worker thread to terminate."""
    global _worker_running
    _worker_running = False
