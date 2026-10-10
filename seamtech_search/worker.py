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
from dataclasses import dataclass
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
        update_job(index, job_id, status="running", progress=percent, stage=stage, expected_worker=worker_id)
        heartbeat_job(index, job_id, expected_worker=worker_id)
        if heartbeat is not None:
            heartbeat()
        if redis_store and redis_store.is_configured():
            if worker_id is None or redis_store.revendication_appartient_a("imports", str(job_id), worker_id):
                redis_store.set_heartbeat(job_id)
                redis_store.update_job(job_id, {"status": "running", "progress": percent, "stage": stage})

    try:
        update_job(index, job_id, status="running", progress=5, stage="starting", expected_worker=worker_id)
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

        # 1. PRÉSERVATION — la preuve, pas l'absence d'exception (A03/A07).
        preservation = evaluer_preservation(result)
        final_payload["preservation"] = preservation.to_dict()

        if preservation.integrite_prouvee:
            final_status = "completed" if result.status == "completed" else result.status
        elif not preservation.destination_configuree:
            # Le stockage n'est pas configuré : l'import reste exploitable en
            # revue, mais AUCUNE copie durable n'existe — on le dit, et rien
            # n'autorise plus la suppression du seul exemplaire.
            final_status = "completed" if result.status == "completed" else result.status
            final_payload["preservation"]["raison_sans_destination"] = (
                "stockage objet non configuré : la copie locale est la seule copie"
            )
        else:
            final_status = "upload_incomplete"
            final_payload["status"] = "upload_incomplete"
            final_payload["upload_status"] = "upload_incomplete"
            logger.warning(
                "Import %s : envoi incomplet (upload_status=%s, %d/%d artefact(s) vérifié(s)) — "
                "traitement de la copie locale selon la politique de quarantaine.",
                job_id, preservation.upload_status,
                preservation.artefacts_verifies, preservation.artefacts_total,
            )

        # Fencing R1 : vérifier si le worker est toujours propriétaire avant toute mutation de fichiers/quarantaine/purge
        def est_proprietaire_actif() -> bool:
            if worker_id is None:
                return True
            if redis_store and redis_store.is_configured():
                if not redis_store.revendication_appartient_a("imports", str(job_id), worker_id):
                    return False
            j = get_job(index, str(job_id))
            if j and j.get("claimed_by") and j.get("claimed_by") != worker_id:
                return False
            return True

        if not est_proprietaire_actif():
            logger.warning(
                "Job %s : worker %s déchu (propriété perdue) — aucune mutation, ni quarantaine ni purge ni résultat.",
                job_id, worker_id,
            )
            return {"job_id": job_id, "status": "stale_worker", "lost_claim": True}

        # 2. QUARANTAINE — uniquement ce qui nous appartient (A04). Une archive
        # externe n'est JAMAIS déplacée par un échec d'envoi, et une
        # relocalisation de staging met à jour toutes les références
        # persistées : la reprise retrouve ses fichiers, même après
        # redémarrage du processus.
        if final_status == "upload_incomplete":
            decision = mettre_en_quarantaine(index, job_id, source_path, config, final_payload)
            final_payload["quarantine"] = decision.to_dict()
        else:
            final_payload["quarantine"] = DecisionQuarantaine(
                deplace=False, destination=None, raison="aucun échec d'envoi",
                references_mises_a_jour=[],
            ).to_dict()

        if not est_proprietaire_actif():
            logger.warning("Job %s : worker %s déchu après quarantaine — écriture du résultat annulée.", job_id, worker_id)
            return {"job_id": job_id, "status": "stale_worker", "lost_claim": True}

        update_job(
            index,
            job_id,
            status=final_status,
            progress=100,
            stage="done" if final_status != "upload_incomplete" else "upload_incomplete",
            result=final_payload,
            expected_worker=worker_id,
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

        # 3. PURGE — preuve de copie durable ET accord explicite (A03). Ni
        # « c'est dans le staging », ni « le stockage n'est pas configuré » ne
        # suppléent l'une ou l'autre de ces deux conditions.
        # Le chemin peut avoir été relocalisé (quarantaine) : la décision porte
        # sur la copie RÉELLE, pas sur l'ancien emplacement.
        purge, raison_purge = decider_purge(
            preservation, config, Path(final_payload.get("source_path") or source_path)
        )
        final_payload["cleanup"] = {"purge": purge, "raison": raison_purge}
        if final_status == "upload_incomplete":
            final_payload["cleanup"] = {
                "purge": False,
                "raison": "import en échec : la copie locale reste la source de la reprise",
            }
        elif purge and est_proprietaire_actif():
            try:
                resolved_source = Path(source_path).expanduser().resolve()
                if resolved_source.exists():
                    logger.warning(
                        "PURGE de %s : copie durable vérifiée (%d artefact(s)) et "
                        "SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD activé.",
                        resolved_source, preservation.artefacts_verifies,
                    )
                    shutil.rmtree(resolved_source, ignore_errors=True)
                    final_payload["cleanup"]["effectuee"] = True
                else:
                    final_payload["cleanup"]["effectuee"] = False
                    final_payload["cleanup"]["raison"] = "source locale déjà absente"
            except Exception as cleanup_err:
                final_payload["cleanup"]["effectuee"] = False
                final_payload["cleanup"]["erreur"] = str(cleanup_err)
                logger.warning("PURGE de %s impossible : %s", source_path, cleanup_err)
        else:
            logger.warning(
                "Copie locale de %s CONSERVÉE : %s", source_path, raison_purge,
            )
        if est_proprietaire_actif():
            update_job(index, job_id, result=final_payload, expected_worker=worker_id)

        return final_payload

    except ImportCancelledError:
        if est_proprietaire_actif():
            update_job(index, job_id, status="cancelled", stage="cancelled", error="Job was cancelled by user", expected_worker=worker_id)
            if redis_store and redis_store.is_configured():
                redis_store.update_job(job_id, {"status": "cancelled", "stage": "cancelled", "error": "Job was cancelled by user"})
        return {"job_id": job_id, "status": "cancelled"}
    except Exception as exc:
        logger.exception("Import job %s failed: %s", job_id, exc)
        if est_proprietaire_actif():
            update_job(index, job_id, status="failed", stage="failed", error=str(exc), expected_worker=worker_id)
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
        update_job(index, job_id, status="running", progress=pourcentage, stage="lot", expected_worker=worker_id)
        heartbeat_job(index, job_id, expected_worker=worker_id)
        if heartbeat is not None:
            heartbeat()
        if redis_store and redis_store.is_configured():
            if worker_id is None or redis_store.revendication_appartient_a("lots", str(job_id), worker_id):
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
        update_job(index, job_id, status="failed", stage="failed", error=message, expected_worker=worker_id)
        terminer_job(index, job_id, status="failed", failure_reason=message, expected_worker=worker_id)
        return {"job_id": job_id, "status": "failed", "error": message, "id_lot": id_lot}

    if etat.get("annule"):
        message = f"Lot #{id_lot} annulé à la demande de l'opérateur."
        try:
            annuler_lot(index, id_lot)
        except Exception as exc:  # pragma: no cover - défensif
            logger.warning("Lot #%s : impossible de marquer l'annulation : %s", id_lot, exc)
        update_job(index, job_id, status="cancelled", stage="cancelled", error=message, result=etat, expected_worker=worker_id)
        terminer_job(index, job_id, status="cancelled", failure_reason=message, expected_worker=worker_id)
        return {"job_id": job_id, "status": "cancelled", "id_lot": id_lot}

    statut = "completed" if int(etat.get("nb_echecs", 0)) == 0 else "needs_review"
    update_job(index, job_id, status=statut, progress=100, stage="done", result=etat, expected_worker=worker_id)
    terminer_job(
        index,
        job_id,
        status=statut,
        failure_reason=None if statut == "completed" else f"{etat.get('nb_echecs')} dossier(s) en échec",
        expected_worker=worker_id,
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
    resume: dict[str, Any] = {
        "requeued": [],
        "dead_lettered": [],
        "relanced_from_db": [],
        "failed_from_db": [],
        "redis_indisponible": [],
    }

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

    # Cas 2 : jobs ACCEPTÉS en base qui ne sont plus dans la file.
    #
    # Défaut corrigé (A06 de l'audit du 2026-10-08) : cette boucle ne regardait
    # que les jobs ``running``. Un job ``pending`` (accepté, en attente de
    # worker) dont l'entrée Redis disparaissait — Redis redémarré sans
    # persistance, entrée évincée — n'était jamais repris : il restait
    # « en attente » POUR TOUJOURS, sans que rien ne le signale. Les deux états
    # actifs sont désormais réconciliés, chacun avec sa raison.
    #
    # Le balayage est PAGINÉ (keyset sur ``updated_at``) : une file de plus de
    # 200 jobs n'est plus ignorée au-delà de la première page — c'était la
    # seconde moitié du même défaut (« au-delà de la limite, plus jamais
    # regardé »).
    for job in _jobs_actifs_pagines(index, par_page=200):
        job_id = str(job.get("id"))
        statut_job = str(job.get("status") or "")
        if statut_job not in ("pending", "running"):
            continue
        try:
            encore_en_file = redis_store.job_est_dans_file(job_id, (queue_name,))
        except Exception:
            # Redis injoignable : on ne touche à RIEN (un job peut être en
            # cours ailleurs) et on le dit — « dans le doute, ne pas casser ».
            logger.warning(
                "Réconciliation : Redis injoignable, job %s non arbitré (laisse en place).", job_id,
            )
            resume["redis_indisponible"].append(job_id)
            continue
        if encore_en_file:
            continue
        attempts = int(job.get("attempts") or 0)
        if attempts >= max_tentatives:
            raison = (
                f"job {statut_job} introuvable dans la file après {attempts} tentative(s) — lettre morte"
            )
            remettre_en_file(index, job_id, raison=raison, tentative_durable=False)
            resume["failed_from_db"].append(job_id)
            continue
        charge = _charge_depuis_job(job)
        if charge is None:
            raison = (
                f"job {statut_job} orphelin non ré-enfilable (charge absente ou sélection "
                "manuelle introuvable) — échec explicite, jamais une relance approximative"
            )
            remettre_en_file(index, job_id, raison=raison, tentative_durable=False)
            resume["failed_from_db"].append(job_id)
            continue
        charge["reprise"] = f"job {statut_job} orphelin ré-enfilé"
        if redis_store.enqueue_task(queue_name, charge):
            remettre_en_file(
                index, job_id, raison=f"job {statut_job} orphelin ré-enfilé", tentative_durable=True
            )
            resume["relanced_from_db"].append(job_id)
            # Le balayage n'est pas muet : une reprise doit être constatable
            # dans les journaux du worker (l'exploitant voit qu'un job a été
            # récupéré, pas seulement qu'il a fini).
            logger.warning(
                "Réconciliation : job %s (%s) absent de la file — ré-enfilé (%d tentative(s) en base).",
                job_id, statut_job, attempts,
            )
        else:
            logger.warning("Job %s orphelin : la remise en file a échoué (Redis indisponible).", job_id)
    return resume


def _jobs_actifs_pagines(index: Any, *, par_page: int = 200) -> list[dict[str, Any]]:
    """Tous les jobs actifs, page après page (jamais « les 200 premiers »).

    Le tri de :func:`jobs_actifs` est ``updated_at DESC`` : les jobs les plus
    anciens — donc les plus susceptibles d'être orphelins — sortaient en DERNIER
    et tombaient hors de la limite. La pagination par décalage les couvre tous.
    """
    tous: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = jobs_actifs(index, limite=par_page, offset=offset)
        tous.extend(page)
        if len(page) < par_page:
            return tous
        offset += par_page


# --------------------------------------------------------------------------- #
# Politique de fin d'import : préservation, quarantaine, purge locale
# --------------------------------------------------------------------------- #
#
# Trois décisions distinctes, qui étaient mêlées — et c'est ce mélange qui a
# produit les défauts A03 et A04 de l'audit du 2026-10-08 :
#
#   1. PRÉSERVATION : les octets sont-ils durablement stockés ET VÉRIFIÉS ?
#      Question binaire, à laquelle on ne répond qu'avec la preuve (relecture
#      des octets), jamais avec « la fonction n'a pas levé ».
#   2. QUARANTAINE : que faire de la copie locale d'un import en échec ?
#      Réponse : RIEN, si l'original n'appartient pas à l'application. Une
#      archive client en lecture/écriture n'est pas un brouillon de staging.
#   3. PURGE : peut-on supprimer la copie locale ? Uniquement si (1) est vrai
#      ET que l'exploitant l'a demandé. « Le fichier est dans le staging » ou
#      « le stockage n'est pas configuré » n'ont JAMAIS été des preuves.
#
# Chaque décision est consignée dans le payload du job : l'exploitant lit ce
# qui a été décidé et POURQUOI, au lieu de le déduire de la disparition des
# fichiers.


@dataclass(frozen=True)
class Preservation:
    """État RÉEL de la copie durable d'un import, preuves incluses."""

    upload_status: str
    integrite_prouvee: bool
    artefacts_total: int
    artefacts_verifies: int
    artefacts_non_verifies: list[str]
    artefacts_sans_cle: list[str]

    @property
    def destination_configuree(self) -> bool:
        return self.upload_status not in ("not_configured", "not_applicable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "upload_status": self.upload_status,
            "integrite_prouvee": self.integrite_prouvee,
            "artefacts_total": self.artefacts_total,
            "artefacts_verifies": self.artefacts_verifies,
            "artefacts_non_verifies": list(self.artefacts_non_verifies),
            "artefacts_sans_cle": list(self.artefacts_sans_cle),
            "destination_configuree": self.destination_configuree,
        }


def evaluer_preservation(result: Any) -> Preservation:
    """Répond à UNE question : les octets sont-ils durablement conservés ?

    L'inventaire considéré est COMPLET (tous les fichiers de l'import), pas le
    sous-ensemble qui vient d'être envoyé : c'est précisément la confusion qui
    faisait rapporter ``all_verified=true`` après un renvoi de rapports seuls.
    """
    fichiers = list(getattr(result, "files", []) or [])
    non_verifies: list[str] = []
    sans_cle: list[str] = []
    verifies = 0
    for fichier in fichiers:
        statut = str(getattr(fichier, "upload_status", "") or "")
        cle = getattr(fichier, "object_key", None)
        if not cle:
            sans_cle.append(str(getattr(fichier, "path", "") or getattr(fichier, "name", "?")))
            continue
        if statut == "uploaded" and statut:
            # « uploaded » ici vient de la vérification d'octets du lot
            # d'envoi ; l'absence de clé d'objet est traitée juste au-dessus.
            verifies += 1
        else:
            non_verifies.append(
                f"{getattr(fichier, 'path', '') or getattr(fichier, 'name', '?')} ({statut or 'statut inconnu'})"
            )

    upload_status = str(getattr(result, "upload_status", "not_configured") or "not_configured")
    integrite = (
        bool(getattr(result, "all_verified", False))
        and upload_status == "uploaded"
        and bool(fichiers)
        and not non_verifies
        and not sans_cle
    )
    return Preservation(
        upload_status=upload_status,
        integrite_prouvee=integrite,
        artefacts_total=len(fichiers),
        artefacts_verifies=verifies,
        artefacts_non_verifies=non_verifies,
        artefacts_sans_cle=sans_cle,
    )


def est_sous_staging(chemin: Path, config: AppConfig) -> bool:
    """Le chemin appartient-il au staging QUE L'APPLICATION gère ?

    Un chemin est « à nous » s'il est sous la racine de staging — et pas déjà
    sous la quarantaine, qui en est un sous-dossier. Tout le reste (l'archive
    du commanditaire, un partage réseau, un dossier d'un autre outil) est
    EXTÉRIEUR : l'application n'a aucun mandat pour le déplacer ou le
    supprimer, même en cas d'échec d'envoi.
    """
    try:
        racine = staging_root(config).expanduser().resolve()
        quarantaine = quarantine_root(config).expanduser().resolve()
        cible = Path(chemin).expanduser().resolve()
    except Exception:
        return False
    if cible == racine:
        return False
    try:
        cible.relative_to(racine)
    except ValueError:
        return False
    try:
        cible.relative_to(quarantaine)
        return False  # déjà en quarantaine : ne pas la déplacer une seconde fois
    except ValueError:
        return True


def _chemins_du_payload(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Chemins persistés qui devront suivre une relocalisation du staging."""
    chemins: list[tuple[str, str]] = []
    if payload.get("source_path"):
        chemins.append(("source_path", str(payload["source_path"])))
    for index, entree in enumerate(payload.get("files") or []):
        if isinstance(entree, dict) and entree.get("path"):
            chemins.append((f"files[{index}].path", str(entree["path"])))
    for cle in ("technical_pdf", "report_path", "report_docx_path", "excel_file"):
        if payload.get(cle):
            chemins.append((cle, str(payload[cle])))
    return chemins


def relocaliser_references(payload: dict[str, Any], ancien: Path, nouveau: Path) -> list[str]:
    """Fait suivre TOUTES les références persistées après un déplacement.

    Sans cela, ``retry_upload`` (qui relit les chemins en base) chercherait les
    fichiers à leur ancienne place : la reprise deviendrait impossible sans
    édition manuelle de la base — exactement le défaut A04.
    """
    ancien_resolu = Path(ancien).expanduser().resolve()
    nouveau_resolu = Path(nouveau)
    modifiees: list[str] = []

    def _suivre(chemin: str) -> str | None:
        try:
            reste = Path(chemin).expanduser().resolve().relative_to(ancien_resolu)
        except (ValueError, OSError):
            return None
        return str(nouveau_resolu / reste)

    for cle, valeur in _chemins_du_payload(payload):
        remplacement = _suivre(valeur)
        if remplacement is None:
            continue
        if cle == "source_path":
            payload["source_path"] = remplacement
        elif cle.startswith("files["):
            payload["files"][int(cle[6:-6])]["path"] = remplacement
        else:
            payload[cle] = remplacement
        modifiees.append(cle)
    return modifiees


@dataclass(frozen=True)
class DecisionQuarantaine:
    """Ce qui a été fait de la copie locale d'un import en échec — et pourquoi."""

    deplace: bool
    destination: str | None
    raison: str
    references_mises_a_jour: list[str]
    erreur: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "deplace": self.deplace,
            "destination": self.destination,
            "raison": self.raison,
            "references_mises_a_jour": list(self.references_mises_a_jour),
            "erreur": self.erreur,
        }


def mettre_en_quarantaine(
    index: Any,
    job_id: str,
    source_path: Path,
    config: AppConfig,
    payload: dict[str, Any],
) -> DecisionQuarantaine:
    """Déplace la copie locale d'un import en échec — SI elle nous appartient.

    * staging applicatif → quarantaine, et TOUTES les références persistées
      suivent (payload + ``import_jobs.source_path``) : la reprise retrouve ses
      fichiers après relocalisation ET après redémarrage ;
    * archive externe → JAMAIS déplacée (le dossier du commanditaire n'est pas
      un brouillon) ; on le dit et on laisse la reprise possible sur place ;
    * échec de relocalisation → signalé explicitement, copie laissée en place
      (on ne perd jamais le fichier pour « ranger »).
    """
    try:
        resolved_source = Path(source_path).expanduser().resolve()
    except Exception as erreur:  # pragma: no cover - chemin invalide
        return DecisionQuarantaine(
            deplace=False, destination=None, raison="chemin source illisible",
            references_mises_a_jour=[], erreur=str(erreur),
        )

    if not est_sous_staging(resolved_source, config):
        logger.warning(
            "Import %s en échec : la source %s n'appartient PAS au staging applicatif — "
            "aucun déplacement (archive externe préservée, reprise possible sur place).",
            job_id, resolved_source,
        )
        return DecisionQuarantaine(
            deplace=False,
            destination=None,
            raison="source externe (hors staging applicatif) — jamais déplacée",
            references_mises_a_jour=[],
        )

    if not resolved_source.exists():
        return DecisionQuarantaine(
            deplace=False, destination=None, raison="source absente (déjà déplacée ou purgée)",
            references_mises_a_jour=[],
        )

    try:
        q_root = quarantine_root(config)
        q_root.mkdir(parents=True, exist_ok=True)
        dest = q_root / f"{job_id}_{resolved_source.name}"
        counter = 1
        original_dest = dest
        while dest.exists():
            counter += 1
            dest = original_dest.parent / f"{original_dest.name}-{counter}"
        shutil.move(str(resolved_source), str(dest))
    except Exception as erreur:
        # Déplacement impossible (droits, volume plein, chemin croisé…) : la
        # copie locale est LAISSÉE EN PLACE et l'échec est visible.
        logger.error(
            "Import %s : mise en quarantaine IMPOSSIBLE (%s) — la copie locale %s est conservée "
            "en place pour la reprise.",
            job_id, erreur, resolved_source,
        )
        return DecisionQuarantaine(
            deplace=False, destination=None,
            raison="relocalisation impossible — copie locale conservée",
            references_mises_a_jour=[], erreur=str(erreur),
        )

    references = relocaliser_references(payload, resolved_source, dest)
    payload["quarantine_path"] = str(dest)
    # La BASE porte le chemin de reprise (registry de vérité) : sans cette mise
    # à jour, `retry_upload` chercherait l'ancien emplacement.
    # Fait suivre également selected_pdf et selected_excel dans la base (R3).
    try:
        from .jobs import get_job, update_job_source_path

        job_actuel = get_job(index, job_id) or {}
        new_sel_pdf = None
        new_sel_excel = None
        if job_actuel.get("selected_pdf"):
            try:
                reste = Path(job_actuel["selected_pdf"]).expanduser().resolve().relative_to(resolved_source)
                new_sel_pdf = str(dest / reste)
            except Exception:
                new_sel_pdf = job_actuel.get("selected_pdf")
        if job_actuel.get("selected_excel"):
            try:
                reste = Path(job_actuel["selected_excel"]).expanduser().resolve().relative_to(resolved_source)
                new_sel_excel = str(dest / reste)
            except Exception:
                new_sel_excel = job_actuel.get("selected_excel")

        update_job_source_path(
            index,
            job_id,
            str(dest),
            selected_pdf=new_sel_pdf,
            selected_excel=new_sel_excel,
        )
    except Exception as erreur:  # pragma: no cover - défensif
        logger.warning("Import %s : chemin de job non mis à jour après quarantaine : %s", job_id, erreur)
    logger.warning(
        "Import %s mis en quarantaine : %s → %s (%d référence(s) persistée(s) mise(s) à jour).",
        job_id, resolved_source, dest, len(references),
    )
    return DecisionQuarantaine(
        deplace=True, destination=str(dest),
        raison="staging applicatif déplacé en quarantaine",
        references_mises_a_jour=references,
    )


def decider_purge(
    preservation: Preservation, config: AppConfig, source_path: Path
) -> tuple[bool, str]:
    """La copie locale peut-elle être supprimée ? (réponse + raison, toujours)

    Règle : **preuve d'une copie durable d'abord, et rien d'autre ne la
    remplace.** Un statut d'envoi, un stockage « non configuré » ou
    l'appartenance au staging ne sont PAS des preuves (défaut A03 : des
    originaux stagés étaient supprimés avec ``upload_status=not_configured`` et
    ``all_verified=false``).

    Une fois la preuve faite, il reste la question du DROIT de supprimer :

    * le **staging applicatif** est notre brouillon (la copie navigateur, déjà
      dans le bucket après vérification) — le cycle de vie documenté le purge
      (« local scratch staging is purged immediately after S3 upload »,
      ``docs/REPORT.md``), sans quoi il faudrait le purger par l'âge plus tard ;
    * une **archive externe** (dossier du commanditaire, partage réseau) n'est
      jamais notre fichier : sa suppression exige l'accord explicite
      ``SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD`` (RG13).
    """
    if not preservation.integrite_prouvee:
        if not preservation.destination_configuree:
            return False, (
                "aucune destination durable configurée : conservation obligatoire "
                "(une copie locale est la SEULE copie)"
            )
        if preservation.artefacts_sans_cle:
            return False, (
                "artefacts sans clé d'objet : "
                + ", ".join(preservation.artefacts_sans_cle[:5])
            )
        if preservation.artefacts_non_verifies:
            return False, (
                "artefacts non vérifiés : "
                + ", ".join(preservation.artefacts_non_verifies[:5])
            )
        return False, f"intégrité non prouvée (upload_status={preservation.upload_status})"
    if est_sous_staging(Path(source_path), config):
        return True, (
            "copie durable vérifiée (tous les artefacts sont dans le stockage objet) : "
            "purge du staging applicatif (brouillon, pas une archive)"
        )
    if not getattr(config, "delete_local_after_upload", False):
        return False, (
            "copie durable vérifiée, mais la source est une archive EXTERNE : "
            "SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD désactivé, la copie locale est conservée"
        )
    return True, "copie durable vérifiée et purge locale autorisée par l'exploitant"


def _charge_depuis_job(job: dict[str, Any]) -> dict[str, Any] | None:
    """Reconstruit la charge d'une tâche à partir de la base (registre de vérité).

    Les sélections manuelles (``selected_pdf`` / ``selected_excel``) sont
    relues EN BASE : une reprise ne doit jamais substituer « le premier PDF
    trouvé » au document que l'opérateur avait désigné (A06). Si une sélection
    est enregistrée mais que le fichier a disparu, on refuse la relance
    approximative (``None``) : mieux vaut un échec explicite qu'un import
    silencieusement différent.
    """
    job_id = str(job.get("id"))
    if job_id.startswith("lot-"):
        try:
            return {"job_id": job_id, "kind": "lot", "id_lot": int(job_id.split("-", 1)[1])}
        except (IndexError, ValueError):
            return None
    source = job.get("source_path")
    if not source:
        return None
    selected_pdf = job.get("selected_pdf")
    selected_excel = job.get("selected_excel")
    if selected_pdf and not Path(str(selected_pdf)).expanduser().exists():
        logger.warning(
            "Job %s : le PDF choisi manuellement (%s) n'existe plus — relance refusée "
            "(aucune substitution silencieuse par un autre document).",
            job_id, selected_pdf,
        )
        return None
    if selected_excel and not Path(str(selected_excel)).expanduser().exists():
        logger.warning(
            "Job %s : le classeur choisi manuellement (%s) n'existe plus — relance refusée.",
            job_id, selected_excel,
        )
        return None
    return {
        "job_id": job_id,
        "source_path": str(source),
        "selected_pdf": selected_pdf,
        "selected_excel": selected_excel,
    }


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
                    # ORDRE (investigation « propriété du claim », audit du
                    # 2026-10-08) : la propriété est vérifiée AVANT
                    # l'acquittement. Acquitter d'abord retirait la tâche de la
                    # file même quand un autre worker l'avait reprise — sa copie
                    # disparaissait, et une mort ultérieure du repreneur ne
                    # laissait plus rien à reprendre dans la liste de traitement.
                    if not redis_store.revendication_appartient_a("imports", job_id, identifiant):
                        logger.warning(
                            "Job %s : verrou perdu (repris par un autre worker) — "
                            "conséquence : la tâche n'est PAS acquittée ici et cet état terminal "
                            "n'est PAS écrit, la reprise fait foi.",
                            job_id,
                        )
                        continue
                    redis_store.ack_task("imports", task, worker_id=identifiant)
                    statut = str(result.get("status", "unknown"))
                    # `max_task_attempts` compte le nombre TOTAL de tentatives :
                    # avec 3, un job est essayé 3 fois (attempt 0, 1, 2) puis va
                    # en lettre morte. La version précédente comparait
                    # `attempt < max_attempts` et offrait donc une tentative de
                    # plus que ce que l'opérateur avait réglé — un job pouvait
                    # être relancé 4 fois pour un réglage à 3.
                    tentatives_restantes = attempt + 1 < max_attempts
                    # (La propriété du verrou a été vérifiée AVANT l'acquittement,
                    # juste au-dessus : un worker déchu n'écrit ni état terminal,
                    # ni acquittement, et ne peut pas escamoter la tâche du
                    # repreneur.)
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
                            # Contexte durable de reprise (R3) : reconstruire la charge à partir
                            # de la base pour propager la relocalisation éventuelle en quarantaine
                            job_actuel = get_job(index, job_id)
                            task_retry = (_charge_depuis_job(job_actuel) if job_actuel else None) or dict(task)
                            task_retry["attempt"] = attempt
                            redis_store.retry_task("imports", task_retry, delay_seconds=delay, worker_id=identifiant)
                            # Le registre (base) suit l'état RÉEL : la tâche est
                            # reprogrammée, donc le job est RÉCUPÉRABLE — pas
                            # « échoué ». L'échec définitif n'est écrit qu'à
                            # l'épuisement des tentatives (juste en dessous), et
                            # la raison reste visible pour l'opérateur.
                            update_job(
                                index,
                                job_id,
                                status="pending",
                                stage="requeued",
                                error=(
                                    f"nouvelle tentative programmée dans {delay}s "
                                    f"(tentative {attempt + 2}/{max_attempts}) : "
                                    f"{result.get('error') or statut}"
                                ),
                                expected_worker=identifiant,
                            )
                        else:
                            raison = (
                                f"lettre morte après {attempt + 1} tentative(s) : "
                                f"{result.get('error') or statut}"
                            )
                            logger.warning("Job %s : %s", job_id, raison)
                            redis_store.deadletter_task("imports", task, worker_id=identifiant)
                            terminer_job(index, job_id, status="failed", failure_reason=raison, expected_worker=identifiant)
                    elif statut == "completed":
                        terminer_job(index, job_id, status="completed", expected_worker=identifiant)
                    elif statut == "cancelled":
                        terminer_job(index, job_id, status="cancelled", failure_reason="annulé par l'opérateur", expected_worker=identifiant)
                    elif statut == "stale_worker":
                        logger.warning("Job %s : worker %s déchu, aucune clôture de tâche.", job_id, identifiant)
                    else:
                        terminer_job(index, job_id, status=statut, expected_worker=identifiant)
                except Exception as task_exc:
                    logger.exception("Task %s failed with exception: %s", job_id, task_exc)
                    if attempt + 1 < max_attempts:
                        delay = 2**attempt
                        job_actuel = get_job(index, job_id)
                        task_retry = (_charge_depuis_job(job_actuel) if job_actuel else None) or dict(task)
                        task_retry["attempt"] = attempt
                        redis_store.retry_task("imports", task_retry, delay_seconds=delay, worker_id=identifiant)
                        # Même règle que ci-dessus : une tâche reprogrammée est un
                        # job RÉCUPÉRABLE, jamais un « échec » déjà écrit.
                        update_job(
                            index,
                            job_id,
                            status="pending",
                            stage="requeued",
                            error=(
                                f"nouvelle tentative programmée dans {delay}s "
                                f"(tentative {attempt + 2}/{max_attempts}) : {task_exc}"
                            ),
                            expected_worker=identifiant,
                        )
                    else:
                        raison = f"lettre morte après exception ({attempt + 1} tentative(s)) : {task_exc}"
                        redis_store.deadletter_task("imports", task, worker_id=identifiant)
                        terminer_job(index, job_id, status="failed", failure_reason=raison, expected_worker=identifiant)
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
