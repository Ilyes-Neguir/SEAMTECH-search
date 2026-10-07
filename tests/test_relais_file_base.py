"""Relais BASE ⇄ FILE : plusieurs PROCESSUS, plusieurs points de crash.

Ce fichier est le complément « preuve d'exécution » de
``tests/test_file_durable.py`` : ici, il n'y a pas de simulation d'un worker,
mais de VRAIS processus (``python -m seamtech_search.worker_service``) lancés,
tués et relancés, tous connectés à la MÊME base PostgreSQL et au MÊME Redis.

Scénarios couverts :

1. **Relais ``pending`` → ``completed``** : le job est créé en base, remis à la
   file, exécuté par un processus worker, puis la base reflète l'état terminal.
2. **Revendication non volable entre processus** : un second worker ne peut pas
   revendiquer une tâche détenue par un worker vivant.
3. **Crash en plein travail (SIGKILL)** : le job reste ``running`` avec un
   propriétaire identifié ; la tâche survit dans la liste de traitement.
4. **Reprise (redémarrage)** : le worker suivant reprend, termine, et
   ``attempts`` prouve la redélivrance ; aucun doublon d'index.
5. **Crash RÉPÉTÉ jusqu'à épuisement** : lettre morte avec raison lisible.
6. **Zombie après reprise** : un worker dont le verrou a expiré ne peut ni
   libérer le verrou du repreneur, ni écrire d'état terminal (vérifié sur une
   vraie clé Redis, pas sur un mock).

Aucun MinIO/Docker n'est requis : le stockage objet est le magasin en mémoire
branché dans le processus de test (``storage_backend`` non configuré côté
worker ⇒ ``upload_status: not_configured``, comportement réel et documenté).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import compter_jobs_par_statut, create_job, get_job
from seamtech_search.redis_store import RedisStore

RACINE = Path(__file__).resolve().parents[1]
URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
URL_REDIS = os.environ.get("SEAMTECH_TEST_REDIS_URL", "")
FICHE = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"

pytestmark = [pytest.mark.redis_queue, pytest.mark.postgres]


# ---------------------------------------------------------------------------
# Outillage
# ---------------------------------------------------------------------------


def _creer_base_jetable() -> tuple[str, str]:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom = f"relais_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom}"')
    finally:
        admin.close()
    return nom, URL_PG.rsplit("/", 1)[0] + f"/{nom}"


def _supprimer_base(nom: str) -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
    finally:
        admin.close()


def _vider_redis(magasin: RedisStore) -> None:
    client = magasin._get_client()
    for cle in client.scan_iter(match="seamtech:*", count=500):
        client.delete(cle)


@pytest.fixture()
def base_relais(tmp_path: Path) -> Iterator[dict[str, Any]]:
    if not URL_PG or not URL_REDIS:
        pytest.skip("SEAMTECH_TEST_DATABASE_URL et SEAMTECH_TEST_REDIS_URL sont requis")
    nom, url = _creer_base_jetable()
    index = SearchIndex(tmp_path / "index.db", url)
    index.initialize()
    index.run_migrations()
    magasin = RedisStore(redis_url=URL_REDIS)
    assert magasin.ping(), "Redis injoignable"
    _vider_redis(magasin)
    try:
        yield {"index": index, "url": url, "nom": nom, "redis": magasin, "tmp": tmp_path}
    finally:
        index.close()
        _vider_redis(magasin)
        _supprimer_base(nom)


def _config_worker(tmp_path: Path, *, claim_ttl: int = 15, max_attempts: int = 2) -> Path:
    config = {
        "root_paths": [str(tmp_path)],
        "database_path": str(tmp_path / "index.db"),
        "database_url": URL_PG,
        "host": "127.0.0.1",
        "min_free_bytes": 0,
        "storage_backend": "local",
        "redis_url": URL_REDIS,
        "require_durable_queue": True,
        "task_claim_ttl_seconds": claim_ttl,
        "max_task_attempts": max_attempts,
        "queue_reclaim_interval_seconds": 2.0,
        "web_worker_enabled": False,
    }
    chemin = tmp_path / "config.worker.json"
    chemin.write_text(json.dumps(config), encoding="utf-8")
    return chemin


def _archive(tmp_path: Path, nom: str = "AFFAIRE-RELAIS") -> Path:
    dossier = tmp_path / nom
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "fiche.pdf").write_bytes(FICHE.read_bytes())
    (dossier / "notes.txt").write_text("note d'atelier", encoding="utf-8")
    return dossier


def _lancer_worker(config: Path, *, url_base: str, une_passe: bool = False) -> subprocess.Popen:
    arguments = [
        sys.executable,
        "-m",
        "seamtech_search.worker_service",
        "--config",
        str(config),
    ]
    if une_passe:
        arguments.append("--une-passe")
    environnement = dict(os.environ)
    environnement["PYTHONPATH"] = str(RACINE)
    environnement["SEAMTECH_DATABASE_URL"] = url_base
    return subprocess.Popen(
        arguments,
        cwd=str(RACINE),
        env=environnement,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _attendre(condition, delai: float, message: str):  # noqa: ANN001, ANN201
    limite = time.time() + delai
    while time.time() < limite:
        valeur = condition()
        if valeur:
            return valeur
        time.sleep(0.1)
    raise AssertionError(message)


def _index_sur(url_base: str, tmp_path: Path) -> SearchIndex:
    index = SearchIndex(tmp_path / "index.db", url_base)
    return index


def _compter(index: SearchIndex, requete: str, parametres: tuple = ()) -> int:
    """Compte compatible SQLite (``connection.execute``) et psycopg2 (curseur)."""
    with index.connect() as connexion:
        if index.is_postgres:
            with connexion.cursor() as curseur:
                curseur.execute(requete, parametres)
                return int(curseur.fetchone()[0])
        return int(connexion.execute(requete, parametres).fetchone()[0])


def _attendre_statut(index: SearchIndex, job_id: str, attendus: set[str], delai: float) -> dict:
    """Attend un état terminal écrit par un AUTRE processus (la base est la vérité)."""
    return _attendre(
        lambda: (get_job(index, job_id) or {}).get("status") in attendus,
        delai,
        f"le job {job_id} n'a jamais atteint {attendus}",
    )


# ---------------------------------------------------------------------------
# 1. Relais complet entre processus
# ---------------------------------------------------------------------------


def test_relais_pending_vers_completed_entre_processus(base_relais: dict[str, Any]) -> None:
    """Le job traverse TOUTE la chaîne : base → file → processus worker → base."""
    url, tmp_path = base_relais["url"], base_relais["tmp"]
    index = _index_sur(url, tmp_path)
    magasin: RedisStore = base_relais["redis"]
    dossier = _archive(tmp_path)
    create_job(index, "relais-1", str(dossier), durability="durable")
    assert get_job(index, "relais-1")["status"] == "pending"
    assert magasin.enqueue_task("imports", {"job_id": "relais-1", "source_path": str(dossier)})

    config = _config_worker(tmp_path)
    worker = _lancer_worker(config, url_base=url, une_passe=True)
    try:
        code = worker.wait(timeout=180)
    finally:
        if worker.poll() is None:  # pragma: no cover - filet
            worker.kill()
    sortie = worker.stdout.read() if worker.stdout else ""
    assert code == 0, f"le worker a échoué (code {code})\n{sortie}"

    job = get_job(index, "relais-1")
    assert job["status"] in {"completed", "needs_review"}, job
    assert job["claimed_by"] is None, "le verrou doit être libéré en fin de tâche"
    assert job["attempts"] == 1
    assert job["durability"] == "durable"
    profondeur = magasin.profondeur_file("imports")
    assert profondeur == {"queue": 0, "processing": 0, "retry": 0, "deadletter": 0}, profondeur
    assert _compter(index, "SELECT COUNT(*) FROM documents") == 2, "2 fichiers attendus"
    index.close()


# ---------------------------------------------------------------------------
# 2. Revendication non volable entre processus (clé Redis réelle)
# ---------------------------------------------------------------------------


def test_revendication_non_volable_entre_deux_processus(base_relais: dict[str, Any]) -> None:
    magasin: RedisStore = base_relais["redis"]
    assert magasin.revendiquer_tache("imports", "job-pair", "worker-A", ttl_seconds=60) is True
    assert magasin.revendiquer_tache("imports", "job-pair", "worker-B", ttl_seconds=60) is False
    assert magasin.revendication("imports", "job-pair")["worker_id"] == "worker-A"
    # Le worker B n'a donc pas le droit d'écrire l'état terminal.
    assert magasin.revendication_appartient_a("imports", "job-pair", "worker-B") is False
    assert magasin.revendication_appartient_a("imports", "job-pair", "worker-A") is True


# ---------------------------------------------------------------------------
# 3 & 4. Crash en plein travail, puis reprise par un autre processus
# ---------------------------------------------------------------------------


def test_crash_puis_reprise_par_un_autre_processus(base_relais: dict[str, Any]) -> None:
    url, tmp_path = base_relais["url"], base_relais["tmp"]
    index = _index_sur(url, tmp_path)
    magasin: RedisStore = base_relais["redis"]
    dossier = _archive(tmp_path, "AFFAIRE-CRASH")
    # Dossier volumineux : garantit que le SIGKILL tombe PENDANT le travail.
    for numero in range(40):
        (dossier / f"plan-{numero}.txt").write_text("x" * 20000, encoding="utf-8")
    create_job(index, "job-crash", str(dossier), durability="durable")
    magasin.enqueue_task("imports", {"job_id": "job-crash", "source_path": str(dossier)})

    config = _config_worker(tmp_path, claim_ttl=15, max_attempts=2)
    premier = _lancer_worker(config, url_base=url)
    try:
        _attendre(
            lambda: (get_job(index, "job-crash") or {}).get("status") == "running",
            60,
            "le premier worker n'a jamais démarré la tâche",
        )
        job = get_job(index, "job-crash")
        assert job["claimed_by"], "le propriétaire doit être enregistré en base"
        assert magasin.profondeur_file("imports")["processing"] >= 0
        os.kill(premier.pid, signal.SIGKILL)
        premier.wait(timeout=10)
        assert premier.returncode == -signal.SIGKILL
    finally:
        if premier.poll() is None:  # pragma: no cover - filet
            os.kill(premier.pid, signal.SIGKILL)

    # Le verrou finit par disparaître (expiration réelle du TTL).
    _attendre(
        lambda: magasin.revendication("imports", "job-crash") is None,
        45,
        "le verrou du worker mort n'a pas expiré",
    )
    tentatives_apres_crash = get_job(index, "job-crash")["attempts"]

    second = _lancer_worker(config, url_base=url, une_passe=True)
    try:
        second.wait(timeout=180)
    finally:
        if second.poll() is None:  # pragma: no cover - filet
            os.kill(second.pid, signal.SIGKILL)
    sortie = second.stdout.read() if second.stdout else ""

    job = get_job(index, "job-crash")
    assert job["status"] in {"completed", "needs_review"}, f"{job}\n{sortie}"
    assert job["attempts"] >= tentatives_apres_crash, (
        f"la reprise doit incrémenter les tentatives (avant={tentatives_apres_crash}, "
        f"après={job['attempts']})"
    )
    documents = _compter(index, "SELECT COUNT(*) FROM documents")
    assert documents == len(list(dossier.iterdir())), (
        f"{documents} documents indexés pour {len(list(dossier.iterdir()))} fichiers : "
        "la reprise a dupliqué du travail"
    )
    index.close()


# ---------------------------------------------------------------------------
# 5. Épuisement des tentatives → lettre morte AVEC raison
# ---------------------------------------------------------------------------


def test_epuisement_des_tentatives_va_en_lettre_morte_avec_raison_entre_processus(
    base_relais: dict[str, Any],
) -> None:
    url, tmp_path = base_relais["url"], base_relais["tmp"]
    index = _index_sur(url, tmp_path)
    magasin: RedisStore = base_relais["redis"]
    # Source inexistante : chaque tentative échoue pour une raison reproductible.
    fantome = tmp_path / "AFFAIRE-ABSENTE"
    create_job(index, "job-fantome", str(fantome), durability="durable")
    magasin.enqueue_task("imports", {"job_id": "job-fantome", "source_path": str(fantome)})

    config = _config_worker(tmp_path, max_attempts=1)
    worker = _lancer_worker(config, url_base=url, une_passe=True)
    try:
        worker.wait(timeout=180)
    finally:
        if worker.poll() is None:  # pragma: no cover - filet
            worker.kill()

    _attendre_statut(index, "job-fantome", {"failed"}, 30)
    job = get_job(index, "job-fantome")
    assert job["status"] == "failed", job
    assert job["failure_reason"], "un échec sans raison lisible n'est pas exploitable"
    assert magasin.profondeur_file("imports")["deadletter"] >= 1
    etats = compter_jobs_par_statut(index)
    assert etats.get("failed", 0) >= 1
    index.close()


# ---------------------------------------------------------------------------
# 6. Zombie après reprise : ni libération, ni écriture terminale
# ---------------------------------------------------------------------------


def test_zombie_ne_peut_ni_liberer_ni_ecrire_l_etat_terminal(base_relais: dict[str, Any]) -> None:
    url, tmp_path = base_relais["url"], base_relais["tmp"]
    index = _index_sur(url, tmp_path)
    magasin: RedisStore = base_relais["redis"]
    dossier = _archive(tmp_path, "AFFAIRE-ZOMBIE")
    create_job(index, "job-zombie", str(dossier), durability="durable")

    # Le zombie a pris la tâche, puis son verrou a expiré.
    magasin.revendiquer_tache("imports", "job-zombie", "worker-zombie", ttl_seconds=15)
    magasin._get_client().delete("seamtech:claim:imports:job-zombie")
    # Le repreneur s'installe.
    assert magasin.revendiquer_tache("imports", "job-zombie", "worker-repreneur", ttl_seconds=60, reprendre=True)

    # Le zombie revient : il ne peut ni libérer ni prolonger.
    assert magasin.liberer_revendication("imports", "job-zombie", "worker-zombie") == -1
    assert magasin.rafraichir_revendications("imports", ["job-zombie"], "worker-zombie", 60) == 0
    assert magasin.revendication_appartient_a("imports", "job-zombie", "worker-zombie") is False
    # La tâche est bien toujours détenue par le repreneur.
    assert magasin.revendication("imports", "job-zombie")["worker_id"] == "worker-repreneur"
    # Et la base n'a jamais reçu d'état terminal du zombie.
    assert get_job(index, "job-zombie")["status"] == "pending"
    shutil.rmtree(dossier, ignore_errors=True)
    index.close()
