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
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .import_pipeline import (
    ImportCancelledError,
    get_import,
    import_folder,
    quarantine_root,
    resoudre_chemin_confine,
    retry_upload,
    staging_root,
)
from .jobs import (
    acquerir_claim_job,
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
from .purge_locale import BilanPurge, purger_copie_prouvee
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
    if existant is not None and existant.get("status") == "needs_review":
        resultat_existant = existant.get("result")
        if isinstance(resultat_existant, dict) and resultat_existant.get("publication_pending") is True:
            return {"job_id": job_id, "status": "needs_review", "duplicate_delivery": True,
                    "publication_pending": True}
        if (isinstance(resultat_existant, dict)
                and isinstance(resultat_existant.get("preservation"), dict)
                and resultat_existant["preservation"].get("transport_verifie") is True
                and resultat_existant.get("all_verified") is False):
            # Le transport est déjà réalisé ; la revue ne peut pas être
            # résolue par une nouvelle livraison du même message.
            return {"job_id": job_id, "status": "needs_review", "duplicate_delivery": True}

    if payload.get("kind") == "lot":
        return _process_lot_task(payload, config, index, redis_store, worker_id=worker_id, heartbeat=heartbeat)

    source_path = Path(payload["source_path"])
    selected_pdf = Path(payload["selected_pdf"]) if payload.get("selected_pdf") else None
    selected_excel = Path(payload["selected_excel"]) if payload.get("selected_excel") else None

    # Fencing R1 : vérifier si le worker est toujours propriétaire avant toute mutation de fichiers/quarantaine/purge
    def est_proprietaire_actif() -> bool:
        if worker_id is None:
            return True
        if redis_store and redis_store.is_configured():
            if not redis_store.revendication_appartient_a("imports", str(job_id), worker_id):
                return False
        j = get_job(index, str(job_id))
        return bool(j and j.get("claimed_by") == worker_id)

    # Le claim du registre ET celui de Redis doivent être présents avant
    # l'extraction, l'envoi ou l'indexation ; une simple absence de propriétaire
    # n'autorise pas un ancien worker à continuer.
    if not est_proprietaire_actif():
        return {"job_id": job_id, "status": "stale_worker", "lost_claim": True}

    cancel_check_local = make_cancel_checker(job_id, redis_store)

    def cancel_check() -> bool:
        # L'API et le worker peuvent être des processus distincts. L'API écrit
        # l'état d'annulation en base ; son drapeau mémoire n'est pas partagé.
        if cancel_check_local():
            return True
        job = get_job(index, job_id)
        return bool(job and job.get("status") == "cancelled")

    def progress_cb(stage: str, percent: int) -> None:
        if cancel_check():
            raise ImportCancelledError("Job was cancelled by user")
        if not est_proprietaire_actif():
            raise RuntimeError("Claim perdu pendant l'import : traitement interrompu")
        update_job(index, job_id, status="running", progress=percent, stage=stage, expected_worker=worker_id)
        heartbeat_job(index, job_id, expected_worker=worker_id)
        if heartbeat is not None:
            heartbeat()
        if redis_store and redis_store.is_configured():
            if worker_id is None or redis_store.revendication_appartient_a("imports", str(job_id), worker_id):
                redis_store.set_heartbeat(job_id)
                redis_store.update_job(job_id, {"status": "running", "progress": percent, "stage": stage})

    @contextmanager
    def publication_protegee():
        # Le verrou PG empêche le transfert du claim de base pendant la
        # publication. SQLite réserve le verrou d'écriture dès BEGIN IMMEDIATE.
        with index.scan_transaction():
            with index.connect() as connexion:
                if index.is_postgres:
                    with connexion.cursor() as cursor:
                        cursor.execute("SELECT claimed_by, status FROM import_jobs WHERE id = %s FOR UPDATE", (job_id,))
                        ligne = cursor.fetchone()
                else:
                    ligne = connexion.execute("SELECT claimed_by, status FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
                if not ligne or ligne[0] != worker_id or ligne[1] != "running":
                    raise RuntimeError("Claim de base perdu avant publication")
            if redis_store and redis_store.is_configured():
                if not redis_store.revendication_appartient_a("imports", str(job_id), str(worker_id)):
                    raise RuntimeError("Claim Redis perdu avant publication")
            if cancel_check():
                raise ImportCancelledError("Job was cancelled by user")
            yield
            if redis_store and redis_store.is_configured():
                if not redis_store.revendication_appartient_a("imports", str(job_id), str(worker_id)):
                    raise RuntimeError("Claim Redis perdu pendant publication")
            if cancel_check():
                raise ImportCancelledError("Job was cancelled by user")

    nettoyage_terminal = False
    try:
        update_job(index, job_id, status="running", progress=5, stage="starting", expected_worker=worker_id)
        if redis_store and redis_store.is_configured():
            redis_store.update_job(job_id, {"status": "running", "progress": 5, "stage": "starting"})

        import_existant = get_import(index, job_id)
        preuves_existantes = bool(
            import_existant and not import_existant.get("publication_pending")
            and any(isinstance(a, dict) and a.get("status") == "uploaded" and a.get("verified")
                    and a.get("key") and a.get("bucket") for a in (import_existant.get("artifacts") or []))
        )
        if preuves_existantes:
            # Ne jamais recommencer l'import complet lorsqu'une partie des
            # originaux est déjà préservée : seule la reprise du manifeste peut
            # conserver ces objets et envoyer les pièces restantes.
            if os.path.abspath(str(import_existant.get("source_path") or "")) != os.path.abspath(source_path):
                raise RuntimeError("Source du job différente de l'import enregistré")
            progress_cb("uploading", 75)
            result = retry_upload(
                index, config, job_id,
                **({"publication_context": publication_protegee} if worker_id is not None else {}),
            )
        else:
            result = import_folder(
                source=source_path,
                config=config,
                index=index,
                selected_pdf=selected_pdf,
                import_id=job_id,
                progress_callback=progress_cb,
                cancel_check=cancel_check,
                selected_excel=selected_excel,
                **({"publication_context": publication_protegee} if worker_id is not None else {}),
            )

        if cancel_check():
            raise ImportCancelledError("Job was cancelled by user")

        from dataclasses import asdict

        final_payload = result if isinstance(result, dict) else asdict(result)

        # 1. PRÉSERVATION — la preuve, pas l'absence d'exception (A03/A07).
        preservation = evaluer_preservation(result)
        deja_preservation = final_payload.get("preservation")
        final_payload["preservation"] = {
            **(deja_preservation if isinstance(deja_preservation, dict) else {}), **preservation.to_dict(),
        }
        statut_resultat = str(final_payload.get("status") or "needs_review")

        if final_payload.get("publication_pending", False):
            final_status = "needs_review"
            final_payload["status"] = "needs_review"
            final_payload["preservation"]["raison_integrite"] = (
                "objet non prouvé ou réponse perdue : réconciliation requise avant nouvel envoi"
            )
        elif preservation.integrite_prouvee:
            final_status = statut_resultat
        elif (final_payload.get("transport_verifie", False) and preservation.artefacts_total
              and not preservation.artefacts_non_verifies and not preservation.artefacts_sans_cle):
            # Un renvoi automatique ne peut pas fabriquer un snapshot ni une
            # liaison catalogue. Conserver les objets et placer en revue sans
            # prétendre à une intégrité globale ni réexpédier en boucle.
            final_status = "needs_review"
            final_payload["status"] = "needs_review"
            final_payload["preservation"]["transport_verifie"] = True
            final_payload["preservation"]["raison_integrite"] = (
                "succès global suspendu : snapshot et liaison catalogue non prouvés"
            )
        elif not preservation.destination_configuree:
            # Le stockage n'est pas configuré : l'import reste exploitable en
            # revue, mais AUCUNE copie durable n'existe — on le dit, et rien
            # n'autorise plus la suppression du seul exemplaire.
            final_status = statut_resultat
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

        job_apres_resultat = update_job(
            index,
            job_id,
            status="running" if worker_id is not None and final_status in {"failed", "upload_incomplete"} else final_status,
            progress=100,
            stage="done" if final_status != "upload_incomplete" else "upload_incomplete",
            result=final_payload,
            expected_worker=worker_id,
        )
        if worker_id is not None:
            if job_apres_resultat and job_apres_resultat.get("status") == "cancelled":
                raise ImportCancelledError("Job was cancelled by user")
            if not job_apres_resultat or job_apres_resultat.get("claimed_by") != worker_id:
                return {"job_id": job_id, "status": "stale_worker", "lost_claim": True}
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

        # 3. PURGE — fichier par fichier, preuve relue À L'INSTANT DE LA
        #    SUPPRESSION. Une preuve d'envoi est un souvenir : elle ne couvre
        #    ni les fichiers ajoutés ni les modifications survenues après
        #    l'inventaire, et elle ne dit rien de l'état présent de l'objet.
        #    Aucune suppression récursive n'est sûre ; seule l'est la
        #    suppression de ce qui vient d'être prouvé (``purge_locale``).
        chemin_purge = Path(final_payload.get("source_path") or source_path)
        accord_purge, raison_purge = decider_purge(preservation, config, chemin_purge)
        # Ordre volontaire : annulation ET propriété sont revérifiées juste
        # avant toute suppression. Un worker déchu n'efface rien.
        if cancel_check():
            raise ImportCancelledError("Job was cancelled by user")
        if accord_purge and not est_proprietaire_actif():
            logger.warning(
                "Job %s : worker %s déchu avant purge — aucune suppression.", job_id, worker_id
            )
            return {"job_id": job_id, "status": "stale_worker", "lost_claim": True}
        if accord_purge:
            bilan_purge = purger_copie_prouvee(
                racine=chemin_purge,
                entrees=list(final_payload.get("files") or []),
                sous_staging=True,  # décidé par decider_purge, qui détient config
                verifier_objet=_verificateur_objet_purge(config),
            )
        else:
            bilan_purge = BilanPurge(False, raison_purge)
        final_payload["cleanup"] = bilan_purge.to_dict()
        if bilan_purge.purge:
            logger.info(
                "Import %s : %d fichier(s) de la copie locale purgé(s) (%d octets) — %s",
                job_id, len(bilan_purge.supprimes), bilan_purge.octets_liberes, bilan_purge.raison,
            )
        else:
            logger.info("Import %s : copie locale conservée — %s", job_id, bilan_purge.raison)
        if est_proprietaire_actif():
            update_job(index, job_id, result=final_payload, expected_worker=worker_id)

        nettoyage_terminal = final_status not in {"failed", "upload_incomplete"}
        return final_payload

    except ImportCancelledError:
        if not est_proprietaire_actif():
            return {"job_id": job_id, "status": "stale_worker", "lost_claim": True}
        intention = get_import(index, job_id)
        resultat_annule: dict[str, Any] = {"job_id": job_id, "status": "cancelled"}
        message_annulation = "Job was cancelled by user"
        if intention and intention.get("publication_pending"):
            resultat_annule = {**intention, **resultat_annule, "publication_pending": True,
                               "error": "Import annulé ; publication incertaine à réconcilier."}
            message_annulation = resultat_annule["error"]
        update_job(index, job_id, status="cancelled", stage="cancelled", error=message_annulation,
                   result=resultat_annule if intention and intention.get("publication_pending") else None,
                   expected_worker=worker_id)
        if redis_store and redis_store.is_configured():
            redis_store.update_job(job_id, {"status": "cancelled", "stage": "cancelled", "error": message_annulation,
                                            "result": resultat_annule})
        nettoyage_terminal = True
        return resultat_annule
    except Exception as exc:
        message = f"Import impossible ({type(exc).__name__})."
        logger.error("Import job %s failed (%s)", job_id, type(exc).__name__)
        if not est_proprietaire_actif():
            return {"job_id": job_id, "status": "stale_worker", "lost_claim": True}
        intention = get_import(index, job_id)
        if intention and intention.get("publication_pending"):
            # Une panne après la première écriture peut avoir laissé des objets.
            # Ne pas programmer une nouvelle livraison qui les dupliquerait.
            resultat = {**intention, "job_id": job_id, "status": "needs_review",
                        "publication_pending": True, "error": "Publication incertaine : réconciliation requise."}
            etat = update_job(index, job_id, status="needs_review", stage="reconciliation",
                              result=resultat, error=resultat["error"], expected_worker=worker_id)
            if not etat or etat.get("status") != "needs_review":
                if etat and etat.get("status") == "cancelled":
                    return {"job_id": job_id, "status": "cancelled"}
                return {"job_id": job_id, "status": "stale_worker", "lost_claim": True}
            if redis_store and redis_store.is_configured():
                redis_store.update_job(job_id, {"status": "needs_review", "stage": "reconciliation", "result": resultat})
            nettoyage_terminal = True
            return resultat
        update_job(index, job_id, status="running" if worker_id is not None else "failed", stage="failed", error=message, expected_worker=worker_id)
        if redis_store and redis_store.is_configured():
            redis_store.update_job(job_id, {"status": "failed", "stage": "failed", "error": message})
        return {"job_id": job_id, "status": "failed", "error": message}
    finally:
        try:
            if (nettoyage_terminal or worker_id is None) and est_proprietaire_actif():
                clear_job_cancel(job_id, redis_store, expected_worker=worker_id)
        except Exception:
            # Une panne du registre ne doit pas effacer une annulation destinée
            # au repreneur ni masquer l'erreur ou le résultat du traitement.
            logger.warning("Impossible de vérifier le claim avant nettoyage du job %s", job_id, exc_info=True)


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
    from .fiches.depot import DepotAnnule, DepotClaimPerdu, DepotImpossible, annuler_lot, executer_lot

    job_id = payload["job_id"]
    id_lot = int(payload["id_lot"])

    # Fencing R1 : vérifier / acquérir légitimement et atomiquement la possession du job.
    # Distingue l'acquisition initiale (job non possédé en BDD) d'une vérification
    # de propriété. Si le job a été transféré ou repris par un autre worker (B),
    # le worker déchu A s'interrompt immédiatement SANS réattribuer la tâche ni
    # écraser le travail ou le résultat de B.
    # L'acquisition est atomique (UPDATE conditionnel) afin d'éliminer toute course TOCTOU.
    if worker_id is not None:
        j = get_job(index, str(job_id))
        if j is not None and j.get("claimed_by") and j.get("claimed_by") != worker_id:
            logger.warning(
                "Lot #%s (job %s) : possession perdue au profit de %s — traitement interrompu sans réattribution.",
                id_lot,
                job_id,
                j.get("claimed_by"),
            )
            return {"job_id": job_id, "status": "stale_worker", "id_lot": id_lot}
        if j is not None and j.get("claimed_by") is None:
            if not acquerir_claim_job(index, str(job_id), worker_id):
                logger.warning(
                    "Lot #%s (job %s) : acquisition atomique du verrou refusée (pris par un tiers) — traitement interrompu.",
                    id_lot,
                    job_id,
                )
                return {"job_id": job_id, "status": "stale_worker", "id_lot": id_lot}
    class ClaimLotPerdu(RuntimeError):
        pass

    # Même sans Redis, l'API peut annuler le job dans un autre processus ;
    # le registre SQL doit donc être consulté aussi pour le repli local.
    from .jobs import is_job_cancelled

    dernier_battement = time.monotonic()

    def doit_continuer() -> bool:  # type: ignore[misc]
        nonlocal dernier_battement
        job_courant = get_job(index, str(job_id))
        if not job_courant or (worker_id is not None and job_courant.get("claimed_by") != worker_id):
            raise ClaimLotPerdu()
        if worker_id is not None and redis_store is not None and redis_store.is_configured():
            if not redis_store.revendication_appartient_a("imports", str(job_id), worker_id):
                raise ClaimLotPerdu()
        if job_courant.get("status") == "cancelled":
            return False
        maintenant = time.monotonic()
        if maintenant - dernier_battement >= _HEARTBEAT_INTERVAL_SECONDS:
            if not heartbeat_job(index, job_id, expected_worker=worker_id):
                raise ClaimLotPerdu()
            if heartbeat is not None:
                heartbeat()
            dernier_battement = maintenant
        return not is_job_cancelled(job_id, redis_store)
    def verifier_claim_externe() -> bool:
        if worker_id is None or redis_store is None or not redis_store.is_configured():
            return True
        return redis_store.revendication_appartient_a("imports", str(job_id), worker_id)

    def progress_cb(dossiers_traites: int, total: int) -> None:
        pourcentage = int(100 * dossiers_traites / total) if total else 0
        update_job(index, job_id, status="running", progress=pourcentage, stage="lot", expected_worker=worker_id)
        heartbeat_job(index, job_id, expected_worker=worker_id)
        if heartbeat is not None:
            heartbeat()
        if redis_store and redis_store.is_configured():
            if worker_id is None or redis_store.revendication_appartient_a("imports", str(job_id), worker_id):
                redis_store.update_job(job_id, {"status": "running", "progress": pourcentage, "stage": "lot"})

    def signaler_echec(message: str) -> bool:
        if worker_id is None:
            return terminer_job(index, job_id, status="failed", failure_reason=message, stage="failed", error=message)
        # La boucle doit encore transférer la livraison vers retry/deadletter.
        # Garder le claim et l'état actif jusqu'à cette transition, afin qu'un
        # crash intermédiaire laisse un job récupérable par la réconciliation.
        job = update_job(index, job_id, status="running", stage="failed", error=message, expected_worker=worker_id)
        return bool(job and job.get("claimed_by") == worker_id and job.get("status") == "running")

    try:
        etat = executer_lot(
            index,
            id_lot,
            doit_continuer=doit_continuer,
            progress_cb=progress_cb,
            worker_id=worker_id,
            claim_job_id=str(job_id) if worker_id is not None else None,
            verifier_claim_externe=verifier_claim_externe,
            # P0.2 : le worker est le seul acteur des lots en production — il
            # doit donc promouvoir les fichiers vers le stockage objet.
            config=config,
        )
        if doit_continuer is not None and not doit_continuer():
            etat["annule"] = True
    except (ClaimLotPerdu, DepotClaimPerdu):
        return {"job_id": job_id, "status": "stale_worker", "id_lot": id_lot}
    except DepotAnnule:
        try:
            if doit_continuer is not None:
                doit_continuer()
        except ClaimLotPerdu:
            return {"job_id": job_id, "status": "stale_worker", "id_lot": id_lot}
        etat = {"annule": True}
    except DepotImpossible as erreur:
        try:
            if doit_continuer is not None:
                doit_continuer()
        except ClaimLotPerdu:
            return {"job_id": job_id, "status": "stale_worker", "id_lot": id_lot}
        message = f"Lot #{id_lot} impossible : {erreur}"
        if not signaler_echec(message):
            return {"job_id": job_id, "status": "stale_worker", "id_lot": id_lot}
        return {"job_id": job_id, "status": "failed", "error": message, "id_lot": id_lot}

    if etat.get("annule"):
        message = f"Lot #{id_lot} annulé à la demande de l'opérateur."
        try:
            annuler_lot(index, id_lot, claim=(str(job_id), worker_id) if worker_id is not None else None)
        except DepotClaimPerdu:
            return {"job_id": job_id, "status": "stale_worker", "id_lot": id_lot}
        except Exception as exc:
            logger.warning("Lot #%s : impossible de marquer l'annulation : %s", id_lot, exc)
            try:
                if doit_continuer is not None:
                    doit_continuer()
            except ClaimLotPerdu:
                return {"job_id": job_id, "status": "stale_worker", "id_lot": id_lot}
            raison = f"Lot #{id_lot} : enregistrement de l'annulation impossible."
            if not signaler_echec(raison):
                return {"job_id": job_id, "status": "stale_worker", "id_lot": id_lot}
            return {"job_id": job_id, "status": "failed", "error": raison, "id_lot": id_lot}
        if not terminer_job(index, job_id, status="cancelled", failure_reason=message, expected_worker=worker_id, stage="cancelled", error=message, result=etat):
            return {"job_id": job_id, "status": "stale_worker", "id_lot": id_lot}
        return {"job_id": job_id, "status": "cancelled", "id_lot": id_lot}

    statut = "completed" if int(etat.get("nb_echecs", 0)) == 0 else "needs_review"
    raison_echec = None if statut == "completed" else f"{etat.get('nb_echecs')} dossier(s) en échec"
    ferme = terminer_job(
        index,
        job_id,
        status=statut,
        failure_reason=raison_echec,
        expected_worker=worker_id,
        stage="done", progress=100, result=etat,
    )
    if not ferme:
        return {"job_id": job_id, "status": "stale_worker", "id_lot": id_lot}
    if redis_store and redis_store.is_configured():
        if worker_id is None or redis_store.revendication_appartient_a("imports", str(job_id), worker_id):
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

    # Le compteur de prises est une version : un worker qui prend la
    # livraison après son transfert Redis l'incrémente avant de traiter. Un
    # balayeur en retard ne doit pas effacer ce nouveau claim dans la base.
    versions = {str(job['id']): int(job.get('attempts') or 0) for job in _jobs_actifs_pagines(index, par_page=200)}
    for decision in _decisions_reprise(redis_store, queue_name, max_tentatives):
        job_id = decision.get("job_id")
        if not job_id or str(job_id) not in versions:
            continue
        version = versions[str(job_id)]
        if decision.get("action") == "requeued":
            actualise = remettre_en_file(
                index,
                str(job_id),
                raison=f"reprise après worker interrompu (tentative {decision.get('attempt')})",
                tentative_durable=True,
                expected_attempts=version,
            )
            if actualise:
                resume["requeued"].append(job_id)
        else:
            actualise = remettre_en_file(
                index,
                str(job_id),
                raison=str(decision.get("raison") or "tentatives épuisées"),
                tentative_durable=False,
                expected_attempts=version,
            )
            if actualise:
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
            if remettre_en_file(index, job_id, raison=raison, tentative_durable=False, expected_attempts=attempts):
                resume["failed_from_db"].append(job_id)
            continue
        charge = _charge_depuis_job(job)
        if charge is None:
            raison = (
                f"job {statut_job} orphelin non ré-enfilable (charge absente ou sélection "
                "manuelle introuvable) — échec explicite, jamais une relance approximative"
            )
            if remettre_en_file(index, job_id, raison=raison, tentative_durable=False, expected_attempts=attempts):
                resume["failed_from_db"].append(job_id)
            continue
        charge["reprise"] = f"job {statut_job} orphelin ré-enfilé"
        if redis_store.enqueue_task_si_absent(queue_name, charge):
            remettre_en_file(
                index, job_id, raison=f"job {statut_job} orphelin ré-enfilé", tentative_durable=True,
                expected_attempts=attempts
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
            logger.warning("Job %s : remise en file non effectuée (déjà présent, claim actif ou erreur Redis).", job_id)
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
    def valeur(objet: Any, nom: str, defaut: Any = None) -> Any:
        return objet.get(nom, defaut) if isinstance(objet, dict) else getattr(objet, nom, defaut)

    fichiers = list(valeur(result, "files", []) or [])
    non_verifies: list[str] = []
    sans_cle: list[str] = []
    verifies = 0
    for fichier in fichiers:
        statut = str(valeur(fichier, "upload_status", "") or "")
        cle = valeur(fichier, "object_key")
        if not cle:
            sans_cle.append(str(valeur(fichier, "path", "") or valeur(fichier, "name", "?")))
            continue
        if statut == "uploaded" and statut:
            # « uploaded » ici vient de la vérification d'octets du lot
            # d'envoi ; l'absence de clé d'objet est traitée juste au-dessus.
            verifies += 1
        else:
            non_verifies.append(
                f"{valeur(fichier, 'path', '') or valeur(fichier, 'name', '?')} ({statut or 'statut inconnu'})"
            )

    upload_status = str(valeur(result, "upload_status", "not_configured") or "not_configured")
    integrite = (
        bool(valeur(result, "all_verified", False))
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
    """Conserver la source à son chemin de reprise lors d'un envoi incomplet.

    Le déplacement et la mise à jour du registre ne sont pas atomiques : une
    interruption entre les deux rend la source introuvable pour la reprise.
    Tant qu'un protocole durable n'est pas qualifié, il n'y a pas de déplacement
    physique ; l'état du travail indique l'échec et la rétention est suspendue.
    """
    try:
        # Python ≥ 3.13 rend la résolution muette sur une boucle de liens :
        # le garde-fou explicite conserve le refus, quelle que soit la version.
        resolved_source = resoudre_chemin_confine(Path(source_path))
    except (OSError, RuntimeError, ValueError):
        return DecisionQuarantaine(False, None, "chemin source illisible", [], "chemin source illisible")
    if not est_sous_staging(resolved_source, config):
        logger.warning("Import %s en échec : source externe conservée sur place.", job_id)
        return DecisionQuarantaine(False, None, "source externe — jamais déplacée", [])
    if not resolved_source.exists():
        return DecisionQuarantaine(False, None, "source absente", [], "source absente")
    logger.warning("Import %s en échec : source stagée conservée sur place pour reprise.", job_id)
    return DecisionQuarantaine(False, None, "déplacement suspendu : source conservée pour reprise", [])


def _verificateur_objet_purge(config: AppConfig) -> Callable[[str, Path], Any] | None:
    """Relit l'objet distant au moment de la purge ; ``None`` si impossible.

    « Je n'ai pas pu vérifier » n'autorise aucune suppression : sans
    vérificateur, la copie locale est conservée et la raison est dite.
    """
    try:
        from .storage import S3StorageClient

        # ``config=`` est impératif : le premier paramètre positional est
        # ``endpoint_url``. Passer la configuration à sa place fabriquerait un
        # client qui se déclare configuré sans l'être.
        client = S3StorageClient(config=config)
    except Exception as exc:  # pragma: no cover - dépend de la configuration
        logger.warning("Purge : client de stockage indisponible (%s) — aucune suppression.", type(exc).__name__)
        return None
    try:
        if not client.is_configured():
            return None
    except Exception:  # pragma: no cover
        return None

    def verifier(cle: str, chemin: Path) -> Any:
        return client.verifier_integrite(cle, chemin, relire=True)

    return verifier


def decider_purge(
    preservation: Preservation, config: AppConfig, source_path: Path
) -> tuple[bool, str]:
    """ACCORDER la purge — la suppression, elle, reste prouvée fichier par fichier.

    Longtemps la réponse a été « jamais » et c'était juste au vu de ce qui
    existait : une preuve d'envoi ne couvre ni les ajouts ni les modifications
    postérieurs à l'inventaire, et rien ne garantissait l'état présent de
    l'objet. La conséquence était une autre panne, silencieuse celle-là : le
    serveur se remplissait sans jamais rien libérer.

    La décision est donc déplacée du souvenir vers l'instant : cette fonction
    n'accorde qu'un DROIT D'EXAMEN (intégrité prouvée, dossier de staging
    appartenant à l'application) ; chaque suppression est ensuite conditionnée
    à une relecture des octets distants et à une empreinte locale revérifiée
    (``purge_locale.purger_copie_prouvee``). Ce qui n'est pas prouvé à cet
    instant est conservé et nommé.
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
    try:
        sous_staging = est_sous_staging(Path(source_path), config)
    except Exception:  # pragma: no cover - chemin exotique
        sous_staging = False
    if not sous_staging:
        return False, "source hors staging : l'application n'a aucun mandat pour la supprimer"
    return True, "intégrité prouvée sur le staging : examen fichier par fichier"


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
                    "imports", job_id, identifiant, ttl_seconds=claim_ttl, attempt=attempt, livraison=task
                )
                if not revendique:
                    logger.warning(
                        "Tâche %s ignorée : elle appartient à un autre worker vivant. "
                        "Conséquence : aucun double traitement, le propriétaire l'acquittera.",
                        job_id,
                    )
                    continue
                if not marquer_job_claim(index, job_id, identifiant):
                    try:
                        existant = get_job(index, job_id)
                        if existant and existant.get("status") in {"completed", "cancelled", "needs_review"}:
                            redis_store.ack_task("imports", task, worker_id=identifiant)
                        else:
                            logger.warning("Job %s impossible à revendiquer dans le registre ; livraison conservée.", job_id)
                    finally:
                        redis_store.liberer_revendication("imports", job_id, identifiant)
                    continue
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
                            if not redis_store.retry_task(
                                "imports", task_retry, delay_seconds=delay, worker_id=identifiant, original_payload=task
                            ):
                                logger.warning("Job %s : reprogrammation non confirmée ; traitement conservé si possible.", job_id)
                                continue
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
                            if redis_store.deadletter_task("imports", task, worker_id=identifiant):
                                terminer_job(index, job_id, status="failed", failure_reason=raison, expected_worker=identifiant)
                    elif statut == "stale_worker":
                        logger.warning("Job %s : worker %s déchu, aucune clôture de tâche.", job_id, identifiant)
                    elif redis_store.ack_task("imports", task, worker_id=identifiant):
                        if statut == "cancelled":
                            terminer_job(index, job_id, status=statut, failure_reason="annulé par l'opérateur", expected_worker=identifiant)
                        else:
                            terminer_job(index, job_id, status=statut, expected_worker=identifiant)
                except Exception as task_exc:
                    erreur_tache = f"traitement impossible ({type(task_exc).__name__})"
                    logger.error("Task %s failed (%s)", job_id, type(task_exc).__name__)
                    if attempt + 1 < max_attempts:
                        delay = 2**attempt
                        job_actuel = get_job(index, job_id)
                        task_retry = (_charge_depuis_job(job_actuel) if job_actuel else None) or dict(task)
                        task_retry["attempt"] = attempt
                        if not redis_store.retry_task(
                            "imports", task_retry, delay_seconds=delay, worker_id=identifiant, original_payload=task
                        ):
                            logger.warning("Job %s : reprogrammation après exception non confirmée.", job_id)
                            continue
                        # Même règle que ci-dessus : une tâche reprogrammée est un
                        # job RÉCUPÉRABLE, jamais un « échec » déjà écrit.
                        update_job(
                            index,
                            job_id,
                            status="pending",
                            stage="requeued",
                            error=(
                                f"nouvelle tentative programmée dans {delay}s "
                                f"(tentative {attempt + 2}/{max_attempts}) : {erreur_tache}"
                            ),
                            expected_worker=identifiant,
                        )
                    else:
                        raison = f"lettre morte après exception ({attempt + 1} tentative(s)) : {erreur_tache}"
                        if redis_store.deadletter_task("imports", task, worker_id=identifiant):
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
                logger.error("Error in Redis worker loop (%s)", type(exc).__name__)
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
