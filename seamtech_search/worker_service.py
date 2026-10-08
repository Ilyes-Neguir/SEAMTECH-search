"""Processus worker autonome — exécution des imports hors du cycle de vie du web.

Pourquoi un processus séparé (et non le fil d'arrière-plan historique) :

* un redémarrage de l'API (mise à jour, crash, `docker compose up -d web`) tuait
  le worker : les imports en cours s'arrêtaient, la tâche restait dans la liste
  de traitement Redis et plus personne ne la reprenait ;
* un import long (gros scan, upload lent) consommait le processus qui sert le
  navigateur ;
* « l'acceptation durable » d'un job ne peut pas dépendre de la survie du
  serveur web qui l'a accepté.

Ce module démarre une boucle de worker identique à celle du fil d'arrière-plan
(``seamtech_search.worker.worker_loop``), mais dans son propre processus, avec :

* reprise des tâches orphelines au démarrage (worker mort, claim expiré) et
  balayage périodique ;
* arrêt propre sur SIGTERM/SIGINT : la tâche en cours est acquittée ou remise
  en file par expiration de claim, jamais perdue ;
* code de sortie non nul si la configuration exige une file durable et que
  Redis est injoignable — un superviseur (systemd/docker) doit le voir.

Usage :

    python -m seamtech_search.worker_service            # boucle jusqu'à SIGTERM
    python -m seamtech_search.worker_service --une-passe # traite puis rend la main
    python -m seamtech_search.worker_service --verifier  # diagnostic, aucun traitement
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from typing import Any

from .config import AppConfig, default_config_path
from .indexer import SearchIndex
from .jobs import compter_jobs_par_statut
from .redis_store import RedisStore
from .worker import identifiant_worker, reconcilier_file, worker_loop

logger = logging.getLogger("seamtech_search.worker_service")


def _construire(config_path: str | None, verbose: bool) -> tuple[AppConfig, SearchIndex, RedisStore]:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = AppConfig.load(config_path or default_config_path())
    index = SearchIndex(
        config.database_path,
        config.database_url,
        pool_min=config.pool_min,
        pool_max=config.pool_max,
        pool_timeout=config.pool_timeout,
        statement_timeout_ms=config.statement_timeout_ms,
    )
    redis_store = RedisStore(config=config)
    return config, index, redis_store


def verifier(config: AppConfig, index: SearchIndex, redis_store: RedisStore) -> dict[str, Any]:
    """Diagnostic opérateur : la file est-elle durable et le schéma à jour ?

    ``durable_ready`` faux est une information ACTIONNABLE (« ce worker ne
    pourra rien recevoir »), pas un simple booléen décoratif.
    """
    redis_joinable = bool(redis_store.is_configured() and redis_store.ping())
    rapport: dict[str, Any] = {
        "worker_id": identifiant_worker(),
        "redis_configured": redis_store.is_configured(),
        "redis_joinable": redis_joinable,
        "require_durable_queue": config.require_durable_queue,
        "durable_ready": redis_joinable,
        "file": redis_store.profondeur_file("imports") if redis_joinable else None,
        "jobs_par_statut": compter_jobs_par_statut(index),
    }
    if redis_joinable:
        rapport["workers_vivants"] = redis_store.workers_vivants()
    return rapport


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        prog="python -m seamtech_search.worker_service",
        description="Worker d'imports SEAMTECH (processus séparé, file Redis durable).",
    )
    parseur.add_argument("--config", default=None, help="Chemin de config.json (défaut : SEAMTECH_CONFIG).")
    parseur.add_argument("--une-passe", action="store_true", help="Traite ce qui est disponible puis sort.")
    parseur.add_argument("--verifier", action="store_true", help="Diagnostic de la file, aucun traitement.")
    parseur.add_argument("--verbose", action="store_true")
    options = parseur.parse_args(argv)

    config, index, redis_store = _construire(options.config, options.verbose)
    try:
        index.initialize()
        if options.verifier:
            print(json.dumps(verifier(config, index, redis_store), ensure_ascii=False, indent=2))
            return 0 if (redis_store.is_configured() and redis_store.ping()) else 2

        if not redis_store.is_configured() or not redis_store.ping():
            message = (
                "Redis injoignable ou non configuré : ce worker ne peut rien recevoir. "
                "Démarrez Redis (docker compose up -d redis) ou corrigez SEAMTECH_REDIS_URL."
            )
            logger.error(message)
            return 2

        identifiant = identifiant_worker()
        arret = threading.Event()

        def _demander_arret(signeau: int, _cadre: Any) -> None:
            logger.warning(
                "Signal %s reçu : arrêt demandé. La tâche en cours se termine, "
                "les suivantes restent dans la file (claim à expiration).",
                signeau,
            )
            from . import worker as module_worker

            module_worker._worker_running = False
            arret.set()

        for signeau in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(signeau, _demander_arret)
            except ValueError:  # pragma: no cover - hors fil principal
                logger.warning("Impossible d'installer le gestionnaire de signal %s.", signeau)

        # Reprise explicite AVANT la boucle : le rapport de démarrage montre au
        # superviseur combien de tâches abandonnées ont été récupérées.
        resume = reconcilier_file(index, redis_store, max_tentatives=config.max_task_attempts)
        if any(resume.values()):
            logger.warning("Reprise au démarrage : %s", resume)

        worker_loop(config, index, redis_store, worker_id=identifiant, run_once=options.une_passe)
        arret.set()
        return 0
    finally:
        index.close()


if __name__ == "__main__":  # pragma: no cover - point d'entrée
    sys.exit(main())
