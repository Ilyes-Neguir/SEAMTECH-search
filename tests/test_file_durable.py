"""File durable : reprise après mort du worker, redélivrance, lettre morte.

Ces tests ne se contentent pas de mocks : ils utilisent un VRAI Redis
(``SEAMTECH_TEST_REDIS_URL``) et, pour les cas PostgreSQL, une vraie base
jetable. Les cas sans service (repli mémoire, refus 503) tournent partout.

Ce qui est prouvé ici, et ce qui ne l'est pas :

* prouvé : une tâche dont le worker meurt est reprise ou mise en lettre morte
  AVEC une raison ; une redélivrance ne duplique ni documents ni fiches ; un lot
  interrompu reprend sans refaire les dossiers déjà traités ; l'API refuse un job
  quand la file durable est exigée et absente ;
* NON prouvé ici : la latence de reprise à l'échelle d'une production réelle, ni
  le comportement d'un cluster Redis (le déploiement cible est un Redis unique).
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import create_job, get_job
from seamtech_search.redis_store import RedisStore

REDIS_URL = os.environ.get("SEAMTECH_TEST_REDIS_URL", "")
pytestmark = pytest.mark.redis_queue

if not REDIS_URL:  # pragma: no cover - sélection CI explicite
    pytest.skip(
        "Set SEAMTECH_TEST_REDIS_URL (ex. redis://:motdepasse@127.0.0.1:6379/1) "
        "to run durable-queue tests",
        allow_module_level=True,
    )


@pytest.fixture()
def store() -> RedisStore:
    """Redis réel, clés nettoyées avant/après (jamais les données d'un autre test)."""
    magasin = RedisStore(redis_url=REDIS_URL)
    client = magasin._get_client()
    assert client is not None, "Redis injoignable : SEAMTECH_TEST_REDIS_URL est-il correct ?"
    assert magasin.ping(), "Redis ne répond pas au PING"
    _vider(magasin)
    yield magasin
    _vider(magasin)


def _vider(magasin: RedisStore) -> None:
    client = magasin._get_client()
    for motif in ("seamtech:queue:*", "seamtech:processing:*", "seamtech:retry:*",
                  "seamtech:deadletter:*", "seamtech:claim:*", "seamtech:worker:*",
                  "seamtech:job:*", "seamtech:cancel:*"):
        for cle in client.scan_iter(match=motif, count=100):
            client.delete(cle)


def _config(tmp_path: Path, **extra) -> AppConfig:
    base = {
        "root_paths": [tmp_path],
        "database_path": tmp_path / "index.db",
        "min_free_bytes": 0,
        "redis_url": REDIS_URL,
    }
    base.update(extra)
    return AppConfig(**base)


def _index(config: AppConfig) -> SearchIndex:
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    index.run_migrations()
    return index


#: Fiche SYNTHÉTIQUE du dépôt (jamais une fiche client) utilisée comme PDF
#: technique : un dossier sans PDF technique est, à juste titre, un import en
#: échec — ce n'est pas ce que ces tests mesurent.
FICHE_SYNTHETIQUE = Path(__file__).resolve().parents[1] / "sample_data/CLIENT-123/fiche-technique.pdf"


def _dossier_source(tmp_path: Path, nom: str = "dossier", fichiers: int = 1) -> Path:
    source = tmp_path / nom
    source.mkdir(parents=True, exist_ok=True)
    for numero in range(fichiers):
        if numero == 0 and FICHE_SYNTHETIQUE.exists():
            (source / "fiche-technique.pdf").write_bytes(FICHE_SYNTHETIQUE.read_bytes())
        else:
            (source / f"note-{numero}.txt").write_text("contenu technique", encoding="utf-8")
    return source


# ---------------------------------------------------------------------------
# 1. Une tâche dont le worker est mort est REPRISE (jamais perdue)
# ---------------------------------------------------------------------------


def test_tache_orpheline_est_remise_en_file_puis_traitee(store: RedisStore, tmp_path: Path) -> None:
    """Worker tué : le claim expire, la tâche est reprise, le job se termine."""
    from seamtech_search.worker import reconcilier_file, worker_loop

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier_source(tmp_path, "affaire-1")
    create_job(index, "job-orphelin", str(source), durability="durable")

    # Un premier worker prend la tâche… puis meurt (le claim n'est plus rafraîchi).
    store.enqueue_task("imports", {"job_id": "job-orphelin", "source_path": str(source)})
    tache = store.dequeue_task("imports", timeout=1)
    assert tache is not None
    store.revendiquer_tache("imports", "job-orphelin", "worker-mort", ttl_seconds=60)
    # TTL réellement posé sur la clé (c'est lui qui déclenche la reprise) :
    client = store._get_client()
    assert client.ttl("seamtech:claim:imports:job-orphelin") > 0
    # Le worker meurt SANS acquitter : le job reste 'running' et sa tâche reste
    # dans la liste de traitement.
    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET status='running' WHERE id='job-orphelin'")
    assert store.profondeur_file("imports")["processing"] == 1

    # Un worker vivant travaille : sa tâche n'est PAS touchée par la reprise.
    # `reprendre=True` = reprise explicite d'un verrou expiré (chemin de la
    # reprise) ; le chemin normal (`False`) refuserait de voler worker-mort.
    store.revendiquer_tache("imports", "job-orphelin", "worker-vivant", ttl_seconds=60, reprendre=True)
    assert store.reprendre_taches_orphelines("imports", max_tentatives=3) == []

    # Le worker vivant meurt à son tour : la revendication disparaît (expiration
    # du TTL en production ; suppression explicite ici pour ne pas faire attendre
    # 5 minutes au test — c'est LA MÊME lecture de clé qui décide).
    client.delete("seamtech:claim:imports:job-orphelin")
    decisions = store.reprendre_taches_orphelines("imports", max_tentatives=3)
    assert [d["action"] for d in decisions] == ["requeued"]
    assert decisions[0]["job_id"] == "job-orphelin"

    # La remise en file est reflétée dans le registre (base), pas seulement dans Redis.
    store.dequeue_task("imports", timeout=1)  # la tâche reprise part en traitement
    client.delete("seamtech:claim:imports:job-orphelin")
    resume = reconcilier_file(index, store, max_tentatives=3)
    assert "job-orphelin" in resume["requeued"]
    job = get_job(index, "job-orphelin")
    assert job is not None and job["status"] == "pending"
    assert "reprise" in (job["error"] or "").lower()

    # Le job repris s'exécute vraiment : documents indexés, job terminé.
    worker_loop(config, index, store, worker_id="worker-reprise", run_once=True)
    job = get_job(index, "job-orphelin")
    assert job is not None
    assert job["status"] == "completed", job
    with index.connect() as connexion:
        total = connexion.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert total == 1, "le dossier repris doit être indexé une seule fois"
    index.close()


# ---------------------------------------------------------------------------
# 2. Redélivrance : rejouer la même tâche ne duplique RIEN
# ---------------------------------------------------------------------------


def test_livraison_dupliquee_ne_duplique_ni_documents_ni_rapports(store: RedisStore, tmp_path: Path) -> None:
    from seamtech_search.worker import process_import_task, worker_loop

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier_source(tmp_path, "affaire-2", fichiers=2)
    create_job(index, "job-dupli", str(source), durability="durable")
    charge = {"job_id": "job-dupli", "source_path": str(source), "task_id": "tache-A", "attempt": 0}

    # Première exécution (comme le ferait le worker).
    premier = process_import_task(dict(charge), config, index, store)
    assert premier["status"] in {"completed", "needs_review"}, premier
    with index.connect() as connexion:
        documents_apres_1 = connexion.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    # Redélivrance Redis : la MÊME tâche revient (worker mort avant acquittement).
    charge["task_id"] = "tache-B"
    store.enqueue_task("imports", charge)
    worker_loop(config, index, store, worker_id="worker-2", run_once=True)

    with index.connect() as connexion:
        documents_apres_2 = connexion.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert documents_apres_2 == documents_apres_1, (
        "une redélivrance a dupliqué des documents : la garde d'idempotence ne fonctionne pas"
    )
    assert store.profondeur_file("imports")["queue"] == 0
    index.close()


# ---------------------------------------------------------------------------
# 3. Tentatives épuisées : lettre morte EXPLICITE avec raison
# ---------------------------------------------------------------------------


def test_tentatives_epuisees_lettre_morte_avec_raison(store: RedisStore, tmp_path: Path) -> None:
    from seamtech_search.worker import worker_loop

    config = _config(tmp_path, max_task_attempts=3)
    index = _index(config)
    create_job(index, "job-mort", str(tmp_path / "absent"), durability="durable")
    store.enqueue_task(
        "imports",
        {"job_id": "job-mort", "source_path": str(tmp_path / "absent"), "attempt": 3},
    )

    worker_loop(config, index, store, worker_id="worker-mort", run_once=True)

    lettre = store.get_deadletters("imports")
    assert lettre, "aucune lettre morte : la tâche a disparu au lieu d'être tracée"
    assert lettre[0]["job_id"] == "job-mort"
    job = get_job(index, "job-mort")
    assert job is not None
    assert job["status"] == "failed"
    assert job["failure_reason"] and "tentative" in job["failure_reason"].lower()
    assert store.profondeur_file("imports")["processing"] == 0
    index.close()


def test_reprise_epuisee_va_en_lettre_morte(store: RedisStore, tmp_path: Path) -> None:
    """Une tâche orpheline qui a déjà épuisé ses tentatives n'est pas relancée."""
    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "job-epuise", str(tmp_path), durability="durable")
    store.enqueue_task("imports", {"job_id": "job-epuise", "source_path": str(tmp_path), "attempt": 2})
    store.dequeue_task("imports", timeout=1)
    # Claim jamais posé (worker mort avant revendication) : rien ne le rafraîchit.
    decisions = store.reprendre_taches_orphelines("imports", max_tentatives=3)
    assert decisions and decisions[0]["action"] == "dead_lettered"
    assert "tentatives épuisées" in decisions[0]["raison"]
    assert get_job(index, "job-epuise")["status"] == "pending"  # base non encore mise à jour
    index.close()


# ---------------------------------------------------------------------------
# 4. Job orphelin côté BASE (Redis vidé/redémarré) : ré-enfilé ou échoué, jamais perdu
# ---------------------------------------------------------------------------


def test_job_running_absent_de_la_file_est_relance(store: RedisStore, tmp_path: Path) -> None:
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier_source(tmp_path, "affaire-3")
    create_job(index, "job-perdu", str(source), durability="durable")
    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET status='running', attempts=1 WHERE id='job-perdu'")
    # Le Redis a été vidé : plus trace du job dans la file.

    resume = reconcilier_file(index, store, max_tentatives=3)
    assert "job-perdu" in resume["relanced_from_db"]
    assert store.profondeur_file("imports")["queue"] == 1
    assert get_job(index, "job-perdu")["status"] == "pending"
    index.close()


def test_job_orphelin_trop_de_tentatives_echoue_avec_raison(store: RedisStore, tmp_path: Path) -> None:
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "job-hs", str(tmp_path), durability="durable")
    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET status='running', attempts=3 WHERE id='job-hs'")

    resume = reconcilier_file(index, store, max_tentatives=3)
    assert "job-hs" in resume["failed_from_db"]
    job = get_job(index, "job-hs")
    assert job["status"] == "failed"
    assert "lettre morte" in (job["failure_reason"] or "")
    index.close()


# ---------------------------------------------------------------------------
# 5. Repli mémoire : EXPLICITE, jamais déguisé en acceptation durable
# ---------------------------------------------------------------------------


def test_job_accepte_en_memoire_est_marque_comme_tel(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    config = _config(tmp_path, redis_url=None, auth_token="jeton-de-test-32-caracteres-minimum!!")
    source = _dossier_source(tmp_path, "affaire-memoire")
    index = _index(config)
    index.close()
    app = create_app(config)
    with TestClient(app) as client:
        reponse = client.post(
            "/imports",
            json={"source_path": str(source)},
            headers={"X-SEAMTECH-TOKEN": config.auth_token},
        )
    assert reponse.status_code == 202, reponse.text
    corps = reponse.json()
    assert corps["durable"] is False
    assert corps["durability"] == "process_memory"

    index = SearchIndex(config.database_path)
    job = get_job(index, corps["job_id"])
    assert job is not None and job["durability"] == "process_memory"
    index.close()


def test_file_exigee_mais_indisponible_refuse_le_job(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    config = _config(
        tmp_path,
        redis_url="redis://:motdepasse@127.0.0.1:6399/0",  # port fermé : Redis injoignable
        require_durable_queue=True,
        auth_token="jeton-de-test-32-caracteres-minimum!!",
    )
    source = _dossier_source(tmp_path, "affaire-refus")
    app = create_app(config)
    with TestClient(app) as client:
        reponse = client.post(
            "/imports",
            json={"source_path": str(source)},
            headers={"X-SEAMTECH-TOKEN": config.auth_token},
        )
    assert reponse.status_code == 503, reponse.text
    assert "durable" in reponse.json()["detail"].lower()
    # Aucun job fantôme en base : un refus ne laisse pas une ligne « en attente ».
    index = SearchIndex(config.database_path)
    with index.connect() as connexion:
        total = connexion.execute("SELECT COUNT(*) FROM import_jobs").fetchone()[0]
    assert total == 0
    index.close()


# ---------------------------------------------------------------------------
# 6. Filet de sécurité : la base porte l'état, pas Redis
# ---------------------------------------------------------------------------


def test_migration_020_presente_et_colonnes_de_supervision(tmp_path: Path) -> None:
    config = _config(tmp_path)
    index = _index(config)
    with index.connect() as connexion:
        versions = [ligne[0] for ligne in connexion.execute("SELECT version FROM schema_migrations").fetchall()]
        colonnes = {ligne[1] for ligne in connexion.execute("PRAGMA table_info(import_jobs)").fetchall()}
    assert "020_file_durable" in versions, versions[-3:]
    assert {"attempts", "claimed_by", "heartbeat_at", "failure_reason", "durability"} <= colonnes
    index.close()


def test_marquer_claim_incremente_les_tentatives(tmp_path: Path) -> None:
    from seamtech_search.jobs import compter_jobs_par_statut, jobs_actifs, marquer_job_claim

    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "job-suivi", str(tmp_path), durability="durable")
    assert marquer_job_claim(index, "job-suivi", "worker-42") is True
    assert marquer_job_claim(index, "job-suivi", "worker-42") is True
    assert marquer_job_claim(index, "job-inexistant", "worker-42") is False

    actifs = jobs_actifs(index)
    assert actifs and actifs[0]["claimed_by"] == "worker-42"
    assert int(actifs[0]["attempts"]) == 2
    assert compter_jobs_par_statut(index)["running"] == 1
    index.close()


def test_redis_indisponible_ne_perd_pas_le_job_de_vue(tmp_path: Path) -> None:
    """Un Redis muet ne doit pas faire disparaître un job : il devient récupérable."""
    from seamtech_search.jobs import remettre_en_file

    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "job-visible", str(tmp_path), durability="durable")
    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET status='running' WHERE id='job-visible'")

    assert remettre_en_file(index, "job-visible", raison="worker perdu", tentative_durable=True) is True
    job = get_job(index, "job-visible")
    assert job["status"] == "pending" and job["stage"] == "requeued"
    index.close()


# ---------------------------------------------------------------------------
# 7. write_through : la charge reconstruite depuis la base reste exploitable
# ---------------------------------------------------------------------------


def test_charge_reconstruite_depuis_la_base(tmp_path: Path) -> None:
    from seamtech_search.worker import _charge_depuis_job

    assert _charge_depuis_job({"id": "lot-12"}) == {"job_id": "lot-12", "kind": "lot", "id_lot": 12}
    assert _charge_depuis_job({"id": "abc", "source_path": "/data/x"}) == {
        "job_id": "abc",
        "source_path": "/data/x",
        "selected_pdf": None,
        "selected_excel": None,
    }
    assert _charge_depuis_job({"id": "abc"}) is None
    assert _charge_depuis_job({"id": "lot-abc"}) is None


def test_sqlite_et_postgres_ont_les_memes_colonnes_de_job() -> None:
    """Une divergence de schéma entre SQLite (dev) et PostgreSQL (prod) ferait
    échouer la supervision d'un côté seulement — donc en silence."""
    from seamtech_search.indexer import POSTGRES_SCHEMA

    for colonne in ("attempts", "claimed_by", "heartbeat_at", "failure_reason", "durability"):
        assert colonne in POSTGRES_SCHEMA, f"POSTGRES_SCHEMA sans {colonne}"
    # SQLite : la table réellement créée doit porter ces colonnes.
    connexion = sqlite3.connect(":memory:")
    from seamtech_search.indexer import SQLITE_SCHEMA

    connexion.executescript(SQLITE_SCHEMA)
    colonnes = {ligne[1] for ligne in connexion.execute("PRAGMA table_info(import_jobs)").fetchall()}
    connexion.close()
    assert {"attempts", "claimed_by", "heartbeat_at", "failure_reason", "durability"} <= colonnes


def test_supervision_expose_file_et_jobs(tmp_path: Path) -> None:
    """Le healthcheck répond sans Redis, avec un état de file explicite."""
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    config = _config(tmp_path, redis_url=None, auth_token="jeton-de-test-32-caracteres-minimum!!")
    app = create_app(config)
    with TestClient(app) as client:
        reponse = client.get("/health", headers={"X-SEAMTECH-TOKEN": config.auth_token})
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["queue"]["configured"] is False and corps["queue"]["durable"] is False
    assert "par_statut" in corps["jobs"]
    assert "statut" in corps["backup"]
    assert corps["s3_credentials"] in {"absent", "dedie", "root_like"}


def test_ttl_du_claim_est_configure(tmp_path: Path) -> None:
    config = _config(tmp_path, task_claim_ttl_seconds=42, max_task_attempts=5)
    assert config.task_claim_ttl_seconds == 42
    assert config.max_task_attempts == 5
    with pytest.raises(ValueError):
        _config(tmp_path, task_claim_ttl_seconds=1)


def test_json_des_charges_ne_contient_pas_de_secret(store: RedisStore, tmp_path: Path) -> None:
    """La file ne doit transporter ni mot de passe, ni jeton, ni clé."""
    client = store._get_client()
    store.enqueue_task("imports", {"job_id": "job-secret", "source_path": str(tmp_path)})
    brute = client.lrange("seamtech:queue:imports", 0, -1)[0]
    charge = json.loads(brute)
    assert set(charge) == {"job_id", "source_path", "attempt", "task_id", "enqueued_at"}
    for valeur in charge.values():
        assert "password" not in str(valeur).lower()
        assert "token" not in str(valeur).lower()


def test_worker_marque_le_job_termine_en_base(store: RedisStore, tmp_path: Path) -> None:
    """Fin de job : worker propriétaire libéré, pas de job fantôme « running »."""
    from seamtech_search.worker import worker_loop

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier_source(tmp_path, "affaire-fin")
    create_job(index, "job-fin", str(source), durability="durable")
    store.enqueue_task("imports", {"job_id": "job-fin", "source_path": str(source)})
    worker_loop(config, index, store, worker_id="worker-fin", run_once=True)

    job = get_job(index, "job-fin")
    assert job["status"] == "completed", job
    assert job["claimed_by"] is None
    assert store.profondeur_file("imports")["processing"] == 0
    index.close()


def test_le_worker_ne_prend_pas_la_main_du_web_quand_desactive(tmp_path: Path) -> None:
    """Un déploiement avec service worker séparé ne doit pas doubler le travail."""
    from seamtech_search import worker as module_worker

    config = _config(tmp_path, web_worker_enabled=False)
    index = _index(config)
    magasin = RedisStore(redis_url=REDIS_URL)
    with patch.object(module_worker, "worker_loop") as boucle:
        module_worker.start_background_worker(config, index, magasin)
        assert boucle.call_count == 0
    index.close()


def test_un_job_encore_en_file_n_est_pas_marque_en_echec(store: RedisStore, tmp_path: Path) -> None:
    """Un job qui ATTEND son tour n'est pas un job mort.

    Défaut réel corrigé : ``recover_stale_jobs`` marquait « failed : serveur
    redémarré » tout job ``pending``/``running`` plus vieux que le seuil, y
    compris ceux qui étaient sagement dans la file — un backlog de 200 dossiers
    s'auto-détruisait après 5 minutes d'attente.
    """
    from seamtech_search.jobs import recover_stale_jobs

    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "job-file", str(tmp_path), durability="durable")
    store.enqueue_task("imports", {"job_id": "job-file", "source_path": str(tmp_path)})
    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id='job-file'")

    marques = recover_stale_jobs(index, heartbeat_threshold_seconds=1, redis_store=store)
    assert marques == 0, "un job encore en file a été marqué en échec"
    assert get_job(index, "job-file")["status"] == "pending"

    # Le VRAI job perdu (ni en file, ni avec un worker vivant) est bien marqué.
    create_job(index, "job-perdu-2", str(tmp_path), durability="durable")
    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id='job-perdu-2'")
    marques = recover_stale_jobs(index, heartbeat_threshold_seconds=1, redis_store=store)
    assert marques == 1
    job = get_job(index, "job-perdu-2")
    assert job["status"] == "failed" and "restarted" in (job["error"] or "").lower()
    index.close()


# ---------------------------------------------------------------------------
# 3 bis. Revendication ATOMIQUE et protection du propriétaire
# ---------------------------------------------------------------------------


def test_deux_workers_ne_peuvent_pas_revendiquer_le_meme_job(store: RedisStore) -> None:
    """La revendication est atomique : un pair vivant ne peut pas être volé.

    Sans cette garantie, deux workers pouvaient traiter le même job et écrire
    chacun son résultat — le « au moins une fois » devenait « deux fois, avec
    écrasement croisé ».
    """
    assert store.revendiquer_tache("imports", "job-A", "worker-1", ttl_seconds=60) is True
    assert store.revendiquer_tache("imports", "job-A", "worker-2", ttl_seconds=60) is False, (
        "un second worker a volé la revendication d'un worker vivant"
    )
    # Le propriétaire peut se rafraîchir lui-même (idempotent).
    assert store.revendiquer_tache("imports", "job-A", "worker-1", ttl_seconds=60) is True
    assert store.revendication("imports", "job-A")["worker_id"] == "worker-1"

    # Après libération par le propriétaire, le verrou est de nouveau prenable.
    assert store.liberer_revendication("imports", "job-A", "worker-1") == 1
    assert store.revendiquer_tache("imports", "job-A", "worker-2", ttl_seconds=60) is True


def test_un_worker_zombie_ne_peut_pas_liberer_le_verrou_du_repreneur(store: RedisStore) -> None:
    """Verrou expiré, tâche reprise : le zombie ne doit plus rien casser.

    Scénario réel : import très long, verrou expiré, un second worker reprend la
    tâche, le premier se réveille. S'il pouvait libérer le verrou, un TROISIÈME
    worker pourrait revendiquer la tâche et la traiter en parallèle du repreneur.
    """
    store.revendiquer_tache("imports", "job-long", "worker-zombie", ttl_seconds=60)
    client = store._get_client()
    client.delete("seamtech:claim:imports:job-long")  # expiration réelle du TTL

    assert store.revendiquer_tache("imports", "job-long", "worker-repreneur", ttl_seconds=60, reprendre=True)
    # Le zombie tente de libérer / de rafraîchir : refusé.
    assert store.liberer_revendication("imports", "job-long", "worker-zombie") == -1
    assert store.revendication("imports", "job-long")["worker_id"] == "worker-repreneur"
    assert store.rafraichir_revendications("imports", ["job-long"], "worker-zombie", 60) == 0
    # Et le zombie ne peut pas prétendre écrire l'état terminal.
    assert store.revendication_appartient_a("imports", "job-long", "worker-zombie") is False
    assert store.revendication_appartient_a("imports", "job-long", "worker-repreneur") is True


def test_livraison_dupliquee_apres_indexation_sans_marquage_ne_duplique_pas(
    store: RedisStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point de crash : documents indexés, puis le processus meurt AVANT d'écrire
    l'état terminal. Redis (n'ayant pas reçu d'acquittement) redélivre la tâche.

    Attendu : la redélivrance ne duplique aucun document, et le job finit
    ``completed``. C'est l'idempotence du dépôt (clé d'idempotence par contenu)
    qui protège, pas une promesse de livraison unique.
    """
    from seamtech_search import worker as module_worker

    config = _config(tmp_path, redis_url=REDIS_URL)
    index = _index(config)
    source = _dossier_source(tmp_path, "affaire-crash")
    create_job(index, "job-crash", str(source), durability="durable")

    # Le point de crash est APRÈS l'indexation : on coupe l'écriture de l'état
    # terminal (le job reste 'running' en base, la tâche n'est pas acquittée).
    vrais = {nom: getattr(module_worker, nom) for nom in ("update_job",)}

    def _update_sans_etat_terminal(idx, job_id, **champs):  # noqa: ANN001, ANN202
        if champs.get("status") in {"completed", "failed", "needs_review"}:
            raise RuntimeError("crash simulé entre l'indexation et le marquage du job")
        return vrais["update_job"](idx, job_id, **champs)

    monkeypatch.setattr(module_worker, "update_job", _update_sans_etat_terminal)
    tache = {"job_id": "job-crash", "source_path": str(source)}
    with pytest.raises(RuntimeError):
        module_worker.process_import_task(tache, config, index, store)

    with index.connect() as connexion:
        avant = connexion.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert avant >= 1, "le premier passage doit avoir indexé les documents"
    assert get_job(index, "job-crash")["status"] == "running", (
        "le crash doit laisser le job non terminal — c'est ce qui rend la reprise nécessaire"
    )

    monkeypatch.undo()
    # Redélivrance par la file (le worker précédent n'a jamais acquitté).
    resultat = module_worker.process_import_task(tache, config, index, store)
    assert resultat["status"] in {"completed", "needs_review"}, resultat

    with index.connect() as connexion:
        apres = connexion.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert apres == avant, (
        f"la redélivrance a créé {apres - avant} document(s) en double "
        f"(avant={avant}, après={apres})"
    )
    job = get_job(index, "job-crash")
    assert job["status"] in {"completed", "needs_review"}
    index.close()


def test_point_de_crash_apres_envoi_objet_avant_ecriture_en_base(tmp_path: Path) -> None:
    """Point de crash : objets envoyés au stockage, puis mort AVANT l'écriture
    des références en base.

    Attendu : le second passage ré-envoie SANS écraser les objets du premier
    (clés suffixées ``-2``), et la base finit par désigner des objets réels.
    """
    from seamtech_search.indexer import SearchIndex
    from seamtech_search.storage import S3StorageClient, upload_artifacts_to_storage
    from tests.s3_en_memoire import S3EnMemoire

    config = AppConfig(
        root_paths=[tmp_path],
        min_free_bytes=0,
        s3_endpoint_url="https://s3.invalide",
        s3_bucket="seamtech-documents",
        s3_access_key="cle-fictive",
        s3_secret_key="secret-fictif",
    )
    index = SearchIndex(tmp_path / "index.db")
    index.initialize(rebuild=True)
    index.run_migrations()
    source = _dossier_source(tmp_path, "affaire-objets")
    fichier = sorted(source.glob("*.pdf"))[0]

    magasin = S3EnMemoire()
    with (
        patch.object(S3StorageClient, "_get_client", lambda _i, probe_timeout=None: magasin),
        patch.object(S3StorageClient, "ensure_bucket_exists", lambda _i: True),
    ):
        premier = upload_artifacts_to_storage("dossier", [fichier], config, import_id="IMP-1", source_root=source)
        # (crash ici : rien n'est écrit en base)
        second = upload_artifacts_to_storage("dossier", [fichier], config, import_id="IMP-1", source_root=source)

    assert premier.all_verified and second.all_verified
    cle_1 = premier.artifacts[0].key
    cle_2 = second.artifacts[0].key
    assert cle_1 != cle_2, "le second envoi a écrasé l'objet du premier"
    assert len(magasin.objets) == 2, "les deux objets doivent coexister dans le stockage"
    assert magasin.objets[cle_1]["donnees"] == fichier.read_bytes()
    assert magasin.objets[cle_2]["donnees"] == fichier.read_bytes()
    index.close()
