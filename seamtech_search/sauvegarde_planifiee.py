"""Sauvegarde planifiée (Lot PROD) — le service compose ``sauvegarde``.

La machinerie de sauvegarde existe depuis le Lot H.1 (`sauvegarde.py` :
``sauver``/``restaurer``/``verifier``, manifestes, rétention, envoi hors-site
prouvé en CI) mais rien ne la déclenchait : une sauvegarde manuelle est une
sauvegarde qu'on oubliera le jour où le disque meurt. Ce module est la boucle
qui manquait, et rien de plus :

- toutes les ``SEAMTECH_BACKUP_VERIFICATION_MINUTES`` (60 par défaut), il
  demande l'âge de la dernière sauvegarde à `etat_exploitation` — la MÊME
  lecture que `/health` publie, donc l'opérateur voit exactement ce que le
  planificateur décide ;
- si l'âge dépasse ``SEAMTECH_BACKUP_INTERVAL_HEURES`` (24 par défaut) — ou
  s'il n'y a jamais eu de sauvegarde — il appelle ``sauvegarde.sauver`` avec
  la rétention ``SEAMTECH_BACKUP_RETENTION`` (5 par défaut) et le client S3
  hors-site de l'environnement quand il est configuré ;
- un échec est journalisé et la boucle CONTINUE : un planificateur mort est
  pire qu'une sauvegarde manquée, et `/health` (``statut: "ancienne"``)
  rend l'échec visible au lieu de le cacher.

Mode SQLite (pas de ``database_url``) : rien à planifier — ``pg_dump`` n'a
pas d'objet et la sauvegarde du fichier reste le geste documenté du poste.
Le processus sort alors proprement (code 0) au lieu de boucler en erreur.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from collections.abc import Callable

from .config import AppConfig, default_config_path, exiger_secrets_production
from .etat_exploitation import dossier_sauvegardes, etat_sauvegarde

logger = logging.getLogger(__name__)

INTERVALLE_HEURES_DEFAUT = 24.0
VERIFICATION_MINUTES_DEFAUT = 60.0
RETENTION_DEFAUT = 5


def sauvegarde_due(config: AppConfig, intervalle_heures: float) -> bool:
    """Vrai si la dernière sauvegarde dépasse l'intervalle — ou n'existe pas."""
    etat = etat_sauvegarde(config)
    age = etat.get("age_heures")
    if age is None:
        # « jamais », manifeste illisible ou âge inconnu : dans le doute, on
        # sauvegarde — une sauvegarde de trop ne coûte qu'un dump.
        return True
    return bool(age >= intervalle_heures)


def _sauver_reel(config: AppConfig) -> None:
    from .sauvegarde import _client_s3_depuis_env, sauver

    retention = int(os.environ.get("SEAMTECH_BACKUP_RETENTION", str(RETENTION_DEFAUT)))
    client = _client_s3_depuis_env()
    if client is None:
        logger.warning(
            "S3 hors-site non configuré : sauvegarde LOCALE seule "
            "(une copie sur le même serveur n'est pas une reprise)."
        )
    sauver(
        config.database_url or "",
        list(config.root_paths),
        dossier_sauvegardes(config),
        client_s3=client,
        retention=retention,
    )


def executer_si_du(
    config: AppConfig,
    *,
    intervalle_heures: float,
    fonction_sauver: Callable[[], object] | None = None,
) -> bool:
    """Un passage du planificateur. Retourne Vrai si une sauvegarde a eu lieu.

    Ne lève jamais : un échec de sauvegarde est journalisé et retourne Faux —
    la boucle doit survivre pour retenter au passage suivant.
    """
    if not config.database_url:
        logger.info("Pas de base PostgreSQL configurée : sauvegarde planifiée sans objet.")
        return False
    if not sauvegarde_due(config, intervalle_heures):
        return False
    try:
        (fonction_sauver or (lambda: _sauver_reel(config)))()
    except Exception:
        logger.exception("Sauvegarde planifiée en échec ; nouvel essai au prochain passage.")
        return False
    logger.info("Sauvegarde planifiée terminée.")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seamtech_search.sauvegarde_planifiee",
        description="Planificateur de sauvegardes (service compose « sauvegarde »).",
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = AppConfig.load(args.config or default_config_path())
    exiger_secrets_production(config)
    if not config.database_url:
        logger.info("Mode SQLite : le planificateur n'a rien à faire, arrêt propre.")
        return 0

    intervalle = float(os.environ.get("SEAMTECH_BACKUP_INTERVAL_HEURES", str(INTERVALLE_HEURES_DEFAUT)))
    verification = float(
        os.environ.get("SEAMTECH_BACKUP_VERIFICATION_MINUTES", str(VERIFICATION_MINUTES_DEFAUT))
    )
    logger.info(
        "Planificateur de sauvegardes : intervalle %.1f h, vérification toutes les %.0f min, dossier %s.",
        intervalle,
        verification,
        dossier_sauvegardes(config),
    )
    while True:
        executer_si_du(config, intervalle_heures=intervalle)
        time.sleep(verification * 60)


if __name__ == "__main__":
    raise SystemExit(main())
