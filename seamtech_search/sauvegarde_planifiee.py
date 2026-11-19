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

CORPUS (Lot 21, priorité 0) : le même passage sauvegarde aussi le corpus
mesuré (`seamtech_search.sauvegarde_corpus`), au même rythme et avec la même
identité hors-site. Il n'est couvert par AUCUNE des deux protections
précédentes — il n'est ni dans la base (c'est un dépôt de fichiers) ni dans
l'archive indexée (`root_paths`) — et il n'existe qu'en un seul exemplaire.
Sa fraîcheur est lue sur le stockage distant lui-même, pas sur le disque :
c'est la seule lecture qui prouve quelque chose.

Mode SQLite (pas de ``database_url``) : rien à planifier — ``pg_dump`` n'a
pas d'objet et la sauvegarde du fichier reste le geste documenté du poste.
Le processus sort alors proprement (code 0) au lieu de boucler en erreur.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from . import sauvegarde_corpus
from .config import AppConfig, default_config_path, exiger_secrets_production
from .etat_exploitation import dossier_sauvegardes, etat_sauvegarde
from .sauvegarde import _client_s3_depuis_env

logger = logging.getLogger(__name__)

INTERVALLE_HEURES_DEFAUT = 24.0
VERIFICATION_MINUTES_DEFAUT = 60.0
RETENTION_DEFAUT = 5


def sauvegarde_due(config: AppConfig, intervalle_heures: float) -> bool:
    """Vrai si la dernière sauvegarde dépasse l'intervalle — ou n'existe pas."""
    if not math.isfinite(intervalle_heures) or intervalle_heures <= 0:
        # Un appel direct invalide ne doit jamais masquer une sauvegarde due.
        return True
    etat = etat_sauvegarde(config)
    age = etat.get("age_heures")
    if age is None:
        # « jamais », manifeste illisible ou âge inconnu : dans le doute, on
        # sauvegarde — une sauvegarde de trop ne coûte qu'un dump.
        return True
    return bool(age >= intervalle_heures)


def _sauver_reel(config: AppConfig) -> dict:
    from .sauvegarde import _client_s3_depuis_env, sauver

    retention = int(os.environ.get("SEAMTECH_BACKUP_RETENTION", str(RETENTION_DEFAUT)))
    client = _client_s3_depuis_env()
    if client is None:
        logger.warning(
            "S3 hors-site non configuré : sauvegarde LOCALE seule "
            "(une copie sur le même serveur n'est pas une reprise)."
        )
    return sauver(
        config.database_url or "",
        list(config.root_paths),
        dossier_sauvegardes(config),
        client_s3=client,
        retention=retention,
    )


def sauvegarde_corpus_due(client: Any | None, intervalle_heures: float) -> bool:
    """Vrai si le corpus n'a pas de sauvegarde hors-site plus récente que l'intervalle.

    La fraîcheur est lue SUR LE STOCKAGE DISTANT (`sauvegarde_corpus.verifier`),
    jamais sur le disque local : un âge calculé depuis le disque ne prouverait
    pas qu'une copie existe ailleurs — c'est précisément l'angle mort que le
    Lot 21 corrige.

    Sans client configuré, c'est dû : il n'y a rien à attendre, et l'échec
    doit remonter à chaque passage plutôt que d'être avalé une fois.
    """
    if not math.isfinite(intervalle_heures) or intervalle_heures <= 0:
        return True
    etat = sauvegarde_corpus.verifier(client, fraicheur_heures=intervalle_heures)
    return not etat.get("protege", False)


def _sauver_corpus(client: Any | None) -> bool:
    """Une tentative de sauvegarde du corpus. Faux en cas d'échec, sans jamais lever.

    Un corpus absent (déploiement qui ne l'embarque pas) est signalé une fois
    au niveau INFO, sans échec : ce n'est pas une panne, mais l'opérateur doit
    pouvoir le lire dans les journaux au lieu de croire la protection acquise.
    """
    dossier = sauvegarde_corpus.dossier_corpus()
    if not dossier.is_dir():
        logger.info("Corpus absent (%s) : rien à sauvegarder hors-site.", dossier.name)
        return True
    if client is None:
        logger.error(
            "Corpus NON sauvegardé : aucun stockage distant configuré. "
            "Provisionner l'identité de sauvegarde (scripts/provisionner_stockage.sh)."
        )
        return False
    try:
        manifeste = sauvegarde_corpus.sauvegarder(client, dossier=dossier)
    except Exception as exc:
        # Type seul au journal : la trace peut contenir une clé ou un endpoint.
        logger.error("Sauvegarde du corpus en échec (%s) ; nouvel essai au prochain passage.", type(exc).__name__)
        return False
    logger.info(
        "Corpus sauvegardé hors-site : %s fichier(s), instant %s.",
        manifeste.get("nb_fichiers"),
        manifeste.get("horodatage"),
    )
    return True


def executer_si_du(
    config: AppConfig,
    *,
    intervalle_heures: float,
    fonction_sauver: Callable[[], object] | None = None,
    client_corpus: Any | None = None,
) -> bool:
    """Un passage du planificateur. Retourne Faux pour une copie non qualifiée.

    Ne lève jamais : un échec de sauvegarde est journalisé et retourne Faux —
    la boucle doit survivre pour retenter au passage suivant.

    Le corpus est sauvegardé APRÈS la base : une panne ne doit pas laisser
    croire que tout est protégé, et l'échec du corpus est compté dans le
    résultat du passage (Faux) au lieu d'être avalé.
    """
    if not config.database_url:
        logger.info("Pas de base PostgreSQL configurée : sauvegarde planifiée sans objet.")
        return False
    if not sauvegarde_due(config, intervalle_heures):
        return False
    try:
        resultat = (fonction_sauver or (lambda: _sauver_reel(config)))()
    except Exception as exc:
        # L'exception ou la trace peuvent contenir une URI, des identifiants,
        # ou des chemins privés. Seul le type est conservé au journal.
        logger.error("Sauvegarde planifiée en échec (%s) ; nouvel essai au prochain passage.", type(exc).__name__)
        return False
    if not isinstance(resultat, dict) or (
        resultat.get("protection_globale_prouvee") is not True or resultat.get("complet") is not True
    ):
        logger.error("Sauvegarde planifiée non qualifiée comme protection globale ; rétention suspendue.")
        return False
    logger.info("Sauvegarde planifiée terminée.")

    # Lot 21 : le corpus, sur le même passage — donc au même rythme (24 h).
    if not sauvegarde_corpus_due(client_corpus, intervalle_heures):
        return True
    return _sauver_corpus(client_corpus)


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

    try:
        intervalle = float(os.environ.get("SEAMTECH_BACKUP_INTERVAL_HEURES", str(INTERVALLE_HEURES_DEFAUT)))
        verification = float(
            os.environ.get("SEAMTECH_BACKUP_VERIFICATION_MINUTES", str(VERIFICATION_MINUTES_DEFAUT))
        )
        if (not math.isfinite(intervalle) or intervalle <= 0 or
                not math.isfinite(verification) or verification <= 0 or
                verification * 60 > threading.TIMEOUT_MAX):
            raise ValueError
    except ValueError:
        logger.error("Intervalles de sauvegarde invalides : valeurs finies positives attendues.")
        return 2
    logger.info(
        "Planificateur de sauvegardes : intervalle %.1f h, vérification toutes les %.0f min, dossier %s.",
        intervalle,
        verification,
        dossier_sauvegardes(config),
    )
    logger.info("Corpus pris en charge : %s", sauvegarde_corpus.dossier_corpus())
    while True:
        executer_si_du(config, intervalle_heures=intervalle, client_corpus=_client_s3_depuis_env())
        time.sleep(verification * 60)


if __name__ == "__main__":
    raise SystemExit(main())
