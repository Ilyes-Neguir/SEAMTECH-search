"""File durable — volet PostgreSQL réel et interruption RÉELLE de Redis.

Ce fichier complète ``tests/test_file_durable.py`` (SQLite + Redis) par ce que
seule une vraie base métier peut prouver :

1. une base ANTÉRIEURE à la migration 020 démarre et se migre (défaut réel
   corrigé le 2026-10-06 : ``initialize()`` créait un index sur ``heartbeat_at``
   avant que la colonne n'existe, et le démarrage échouait) ;
2. un LOT multi-dossiers interrompu par la mort du worker REPREND sans refaire
   les dossiers déjà déposés (l'idempotence n'est pas une intention, elle est
   mesurée : nombre de fiches) ;
3. une livraison dupliquée d'un lot ne crée pas de doublon ;
4. un REDÉMARRAGE de Redis (instance réelle dédiée, AOF activé) ne perd pas une
   tâche déjà acceptée par l'API — c'est la promesse « accepté = durable ».

Marqueurs : ``redis_queue`` (exige ``SEAMTECH_TEST_REDIS_URL``) et ``postgres``
(exige ``SEAMTECH_TEST_DATABASE_URL``). Ces tests sont donc exclus des suites
« non-PostgreSQL » et lancés par l'étape CI dédiée, avec un garde-fou
« passed > 0, skipped == 0 ».
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import create_job
from seamtech_search.redis_store import RedisStore

RACINE = Path(__file__).resolve().parents[1]
URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
URL_REDIS = os.environ.get("SEAMTECH_TEST_REDIS_URL", "")
FICHE_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
FICHE_GENOIS = RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf"

pytestmark = [pytest.mark.redis_queue, pytest.mark.postgres]


# ---------------------------------------------------------------------------
# Outils : base jetable, Redis jetable
# ---------------------------------------------------------------------------


def _creer_base_jetable() -> tuple[str, str]:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom_base = f"durable_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        admin.close()
    url = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"
    return nom_base, url


def _supprimer_base(nom_base: str) -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}" WITH (FORCE)')
    finally:
        admin.close()


@pytest.fixture()
def base_durable() -> Iterator[dict[str, Any]]:
    """Base PostgreSQL jetable neuve (schéma complet + gabarits) + Redis vidé."""
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    if not URL_REDIS:
        pytest.skip("Set SEAMTECH_TEST_REDIS_URL to run durable-queue tests")

    nom_base, url_base = _creer_base_jetable()

    # Gabarits : sans eux, aucun dossier ne peut être déposé (le dépôt refuse
    # un dossier sans gabarit actif) et le test mesurerait autre chose.
    from seamtech_search.fiches.gabarits import initialiser_gabarits

    index = SearchIndex(Path(f"/tmp/unused-durable-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)

    magasin = RedisStore(redis_url=URL_REDIS)
    assert magasin.ping(), "Redis injoignable : SEAMTECH_TEST_REDIS_URL est-il correct ?"
    client = magasin._get_client()
    for motif in ("seamtech:*",):
        for cle in client.scan_iter(match=motif, count=200):
            client.delete(cle)

    try:
        yield {"index": index, "url": url_base, "nom": nom_base, "redis": magasin}
    finally:
        index.close()
        _supprimer_base(nom_base)


def _archive_lot(tmp_path: Path) -> Path:
    """Archive d'essai à 3 dossiers, PDF = vraies fiches du dépôt.

    Deux dossiers partagent VOLONTAIREMENT le même PDF : c'est le cas réel d'un
    remplacement (même affaire, même fiche). Le compte de fiches reste donc
    mesurable : 2 fiches attendues pour 3 dossiers, et une reprise ne doit pas
    en créer une troisième.
    """
    racine = tmp_path / "archive_lot"
    racine.mkdir(parents=True, exist_ok=True)
    contenus = {1: FICHE_7792, 2: FICHE_GENOIS, 3: FICHE_GENOIS}
    for numero, source in contenus.items():
        dossier = racine / f"AFFAIRE-{numero:04d}"
        dossier.mkdir()
        shutil.copy(source, dossier / "fiche.pdf")
    return racine


def _compter_fiches(index: SearchIndex) -> int:
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM fiche")
            return int(cursor.fetchone()[0])


def _dossiers_traites(index: SearchIndex, id_lot: int) -> int:
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM lot_dossier WHERE id_lot = %s AND statut = 'traite'", (id_lot,)
            )
            return int(cursor.fetchone()[0])


# ---------------------------------------------------------------------------
# 1. Base antérieure à la migration 020 : démarrage puis migration
# ---------------------------------------------------------------------------


def test_base_anterieure_a_la_020_demarre_puis_se_migre() -> None:
    """Défaut réel corrigé : ``initialize()`` échouait sur une base ancienne.

    ``CREATE TABLE IF NOT EXISTS import_jobs`` ne fait rien sur une base qui a
    déjà la table SANS ``heartbeat_at`` ; créer l'index partiel référençant
    cette colonne faisait alors échouer le DÉMARRAGE (« column heartbeat_at
    does not exist »). La correction : l'index n'est créé dans ``initialize()``
    que si la colonne existe — la migration 020 ajoute la colonne PUIS l'index.

    Ce que ce test prouve : une installation réelle (base créée avant la 020)
    redémarre sans intervention manuelle, et la migration termine le travail.
    """
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    import psycopg2

    nom_base, url_base = _creer_base_jetable()
    try:
        # Base « historique » : import_jobs SANS les colonnes de supervision.
        connexion = psycopg2.connect(url_base)
        connexion.autocommit = True
        with connexion.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE import_jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT '',
                    source_path TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    result JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        connexion.close()

        index = SearchIndex(Path("/tmp/unused-legacy.db"), url_base)
        index.initialize()  # ne doit PLUS lever (correction du 2026-10-06)
        index.run_migrations()

        with index.connect() as connexion_index:
            with connexion_index.cursor() as cursor:
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'import_jobs'"
                )
                colonnes = {ligne[0] for ligne in cursor.fetchall()}
                cursor.execute(
                    "SELECT indexname FROM pg_indexes WHERE tablename = 'import_jobs'"
                )
                index_noms = {ligne[0] for ligne in cursor.fetchall()}
        assert {"attempts", "claimed_by", "heartbeat_at", "failure_reason", "durability"} <= colonnes
        assert "idx_import_jobs_actifs" in index_noms, "l'index de supervision doit exister après migration"
    finally:
        _supprimer_base(nom_base)


# ---------------------------------------------------------------------------
# 2 & 3. Lot interrompu : reprise sans doublon, livraison dupliquée inoffensive
# ---------------------------------------------------------------------------


def test_lot_interrompu_reprend_sans_refaire_les_dossiers_traites(
    base_durable: dict[str, Any], tmp_path: Path
) -> None:
    """Le worker meurt en plein lot ; le suivant reprend là où c'était.

    Mesuré : (1) le lot n'est pas « terminé » après l'interruption, il est
    « interrompu » avec des dossiers restants ; (2) la reprise traite les
    dossiers restants ; (3) aucune fiche n'est créée deux fois.
    """
    from seamtech_search.fiches import depot

    index: SearchIndex = base_durable["index"]
    magasin: RedisStore = base_durable["redis"]
    racine = _archive_lot(tmp_path)

    id_lot = depot.creer_lot(index, racine)
    # Première passe volontairement interrompue après 1 dossier (mort simulée
    # du worker : c'est exactement ce que fait un SIGKILL).
    etat_apres_interruption = depot.executer_lot(index, id_lot, interrompre_apres=1)
    assert etat_apres_interruption["statut"] in {"interrompu", "en_cours", "relance"}
    assert etat_apres_interruption["restants"], "des dossiers doivent rester à traiter"

    fiches_apres_passe_1 = _compter_fiches(index)
    assert fiches_apres_passe_1 == 1, "la première passe ne doit avoir déposé qu'un dossier"

    # Reprise par la file durable : le worker reçoit la charge du lot.
    create_job(index, f"lot-{id_lot}", str(racine), status="pending", stage="queued", durability="durable")
    charge = {"job_id": f"lot-{id_lot}", "kind": "lot", "id_lot": id_lot, "source_path": str(racine)}
    assert magasin.enqueue_task("imports", charge)
    assert magasin.dequeue_task("imports", timeout=2) is not None

    from seamtech_search.worker import process_import_task

    config = AppConfig(root_paths=[tmp_path], min_free_bytes=0, redis_url=URL_REDIS)
    resultat = process_import_task(
        charge, config, index, magasin, worker_id="worker-reprise", heartbeat=lambda: None
    )
    assert resultat["status"] in {"completed", "needs_review"}, resultat

    etat_final = depot.etat_lot(index, id_lot)
    assert etat_final["statut"] == "termine"
    assert not etat_final["restants"], "tous les dossiers doivent être traités après reprise"
    assert _dossiers_traites(index, id_lot) == 3, "les 3 dossiers doivent être marqués traités"
    fiches_finales = _compter_fiches(index)
    # 3 dossiers, 2 contenus distincts ⇒ 2 fiches : la reprise a déposé les
    # dossiers 2 et 3 (contenu identique au dossier 2 ⇒ même fiche, pas de
    # doublon), et n'a pas retraité le dossier 1 (sinon on en aurait 3).
    assert fiches_finales == 2, (
        "la reprise doit traiter les dossiers restants sans refaire le premier ni "
        f"dupliquer un contenu (fiches avant reprise={fiches_apres_passe_1}, après={fiches_finales})"
    )

    # --- Livraison dupliquée : le même lot est redélivré par Redis ---------
    assert magasin.enqueue_task("imports", charge)
    rejoue = process_import_task(
        charge, config, index, magasin, worker_id="worker-reprise", heartbeat=lambda: None
    )
    assert rejoue.get("duplicate_delivery") is True, rejoue
    assert _compter_fiches(index) == fiches_finales, "une redélivrance ne doit créer aucune fiche"


# ---------------------------------------------------------------------------
# 4. Redémarrage RÉEL de Redis : la tâche acceptée survit
# ---------------------------------------------------------------------------


def _redis_server_binaire() -> str | None:
    """Binaire ``redis-server`` : explicite, sinon PATH, sinon bac à sable local."""
    candidats = [
        os.environ.get("SEAMTECH_TEST_REDIS_SERVER", ""),
        shutil.which("redis-server") or "",
        "/home/user/toolchain/redis-7.2.5/src/redis-server",
    ]
    for candidat in candidats:
        if candidat and Path(candidat).exists():
            return candidat
    return None


def _port_libre() -> int:
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        return int(prise.getsockname()[1])


def _attendre_ping(url: str, delai: float = 15.0) -> bool:
    limite = time.time() + delai
    while time.time() < limite:
        if RedisStore(redis_url=url).ping():
            return True
        time.sleep(0.2)
    return False


def test_redemarrage_de_redis_conserve_la_tache_acceptee(tmp_path: Path) -> None:
    """« Accepté » doit vouloir dire « survivra au redémarrage de Redis ».

    Instance Redis DÉDIÉE (jamais celle des autres tests), AOF activé, données
    dans un répertoire temporaire. On accepte deux tâches, on TUE le serveur
    (SIGKILL, comme un OOM-killer ou un `docker kill`), on le redémarre sur les
    mêmes données, et on exige : les deux tâches sont toujours là, la tâche en
    cours de traitement n'est pas perdue, la supervision les voit.
    """
    binaire = _redis_server_binaire()
    if not binaire:
        pytest.skip(
            "redis-server introuvable (ni PATH, ni SEAMTECH_TEST_REDIS_SERVER) : "
            "impossible de simuler un redémarrage réel de Redis"
        )

    port = _port_libre()
    donnees = tmp_path / "redis-data"
    donnees.mkdir()
    url = f"redis://127.0.0.1:{port}/0"

    def _demarrer() -> subprocess.Popen:
        processus = subprocess.Popen(
            [
                binaire,
                "--port", str(port),
                "--dir", str(donnees),
                "--appendonly", "yes",
                "--appendfsync", "always",
                "--save", "",
                "--daemonize", "no",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert _attendre_ping(url), "Redis dédié n'a pas démarré"
        return processus

    serveur = _demarrer()
    try:
        magasin = RedisStore(redis_url=url)
        assert magasin.enqueue_task("imports", {"job_id": "accepte-1", "source_path": "/archive/1"})
        assert magasin.enqueue_task("imports", {"job_id": "accepte-2", "source_path": "/archive/2"})
        # Une tâche en cours de traitement (revendiquée) au moment du crash.
        prise = magasin.dequeue_task("imports", timeout=2)
        assert prise is not None and prise.get("job_id") == "accepte-1"
        assert magasin.revendiquer_tache("imports", "accepte-1", "worker-1", ttl_seconds=300)
        assert magasin.profondeur_file("imports")["processing"] == 1

        # --- Redémarrage BRUTAL -------------------------------------------
        serveur.kill()
        serveur.wait(timeout=10)
        assert not _attendre_ping(url, delai=2), "le serveur aurait dû être arrêté"

        serveur = _demarrer()
        magasin = RedisStore(redis_url=url)

        profondeur = magasin.profondeur_file("imports")
        assert profondeur["queue"] == 1, f"la tâche en attente a été perdue : {profondeur}"
        assert profondeur["processing"] == 1, f"la tâche en cours a été perdue : {profondeur}"
        assert magasin.revendication("imports", "accepte-1") is not None, (
            "le claim du worker doit survivre au redémarrage : sinon un second worker "
            "pourrait exécuter la même tâche en parallèle"
        )
    finally:
        if serveur.poll() is None:
            serveur.kill()
            serveur.wait(timeout=10)
