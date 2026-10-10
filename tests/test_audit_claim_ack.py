"""Propriété du claim et ordre de l'acquittement — investigation complémentaire.

Défaut relevé en revue de code (audit du 2026-10-08, investigation « claim /
lease / ack sous fautes multi-workers ») : ``ack_task`` commençait par retirer la
charge EXACTE de la liste de traitement ; quand elle ne la trouvait pas — cas
normal après une reprise, puisque le repreneur a poussé SA copie — elle se
rabattait sur « le premier élément portant le même ``job_id`` ». Ce faisant, le
worker déchu retirait la tâche QUE LE REPRENEUR ÉTAIT EN TRAIN DE TRAITER : si
le repreneur mourait ensuite, plus rien dans la liste de traitement ne le
signalait au balayage.

Correctif éprouvé ici, sur Redis réel :

* ``ack_task(..., worker_id=…)`` est REFUSÉ quand le verrou n'appartient plus à
  ce worker, et le repli aveugle « par job_id » est désactivé ;
* ``worker_loop`` vérifie la propriété AVANT d'acquitter : un worker déchu
  n'acquitte rien, n'écrit aucun état terminal, et ne touche pas la tâche du
  repreneur ;
* le worker légitime, lui, acquitte normalement (aucune régression du chemin
  nominal) ;
* après reprise, la tâche du repreneur reste REPRÉSENTÉE dans la liste de
  traitement — une mort du repreneur sera donc bien vue par le balayage.

Ces tests exigent un Redis réel (marqueur ``redis_queue``) : l'atomicité d'un
claim ne se prouve pas sur un double en mémoire.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import create_job, get_job, marquer_job_claim, terminer_job, update_job
from seamtech_search.redis_store import RedisStore

pytestmark = pytest.mark.redis_queue

PROCESSING = "seamtech:processing:imports"
QUEUE = "seamtech:queue:imports"


def _vider(magasin: RedisStore) -> None:
    client = magasin._get_client()
    if client is None:  # pragma: no cover - le fixture garantit Redis
        return
    for motif in (
        "seamtech:queue:*",
        "seamtech:processing:*",
        "seamtech:retry:*",
        "seamtech:deadletter:*",
        "seamtech:claim:*",
        "seamtech:worker:*",
        "seamtech:job:*",
        "seamtech:cancel:*",
        "seamtech:heartbeat:*",
    ):
        for cle in client.scan_iter(match=motif, count=100):
            client.delete(cle)


@pytest.fixture()
def store() -> Any:
    import os

    url = os.environ.get("SEAMTECH_TEST_REDIS_URL", "")
    if not url:
        pytest.skip("Set SEAMTECH_TEST_REDIS_URL to run Redis integration tests")
    magasin = RedisStore(redis_url=url)
    assert magasin.ping(), "Redis injoignable : SEAMTECH_TEST_REDIS_URL est-il correct ?"
    _vider(magasin)
    yield magasin
    _vider(magasin)


def _config(tmp_path: Path, **extra: Any) -> AppConfig:
    base: dict[str, Any] = {
        "root_paths": [tmp_path],
        "database_path": tmp_path / "index.db",
        "min_free_bytes": 0,
        "redis_url": None,
        **extra,
    }
    return AppConfig(**base)


def _index(config: AppConfig) -> SearchIndex:
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    index.run_migrations()
    return index


def _dossier(tmp_path: Path, nom: str = "affaire") -> Path:
    dossier = tmp_path / nom
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "fiche.pdf").write_bytes(b"%PDF-1.4 technique")
    return dossier


def test_ack_refuse_quand_le_verrou_n_est_plus_le_notre(store: RedisStore) -> None:
    """Un worker déchu ne peut plus retirer la tâche du repreneur."""
    store.enqueue_task("imports", {"job_id": "job-ack", "source_path": "/dossier"})
    prise = store.dequeue_task("imports", timeout=1)
    assert prise is not None
    # C'est la charge RÉELLEMENT sortie de la file (l'enfilage y a ajouté
    # ``attempt``/``task_id``/``enqueued_at``) : c'est elle que le worker acquitte.
    tache = prise
    assert store.revendiquer_tache("imports", "job-ack", "worker-A", ttl_seconds=60)

    # Le verrou expire et le redémarrage/reprise… : worker-B le reprend.
    assert store.revendiquer_tache("imports", "job-ack", "worker-B", ttl_seconds=60, reprendre=True)
    # B traite la même tâche : sa copie est dans la liste de traitement (ici la
    # même charge, mais c'est le VERROU qui décide de la propriété).
    avant = store._get_client().llen(PROCESSING)

    # A tente d'acquitter : refusé, la tâche de B reste là.
    assert store.ack_task("imports", tache, worker_id="worker-A") is False
    assert store._get_client().llen(PROCESSING) == avant, (
        "l'acquittement du worker déchu a retiré une tâche de la liste de traitement"
    )

    # B, propriétaire légitime, acquitte normalement.
    assert store.ack_task("imports", tache, worker_id="worker-B") is True
    assert store._get_client().llen(PROCESSING) == avant - 1


def test_ack_sans_worker_id_garde_le_comportement_historique(store: RedisStore) -> None:
    """Sans identité de worker (appelants anciens), le repli reste possible — et dit.

    Le produit, lui, fournit TOUJOURS ``worker_id`` (vérifié dans le test de
    boucle ci-dessous) : c'est ce repli aveugle qui escamotait la tâche d'un
    repreneur.
    """
    tache = {"job_id": "job-legacy", "source_path": "/dossier"}
    store.enqueue_task("imports", tache)
    prise = store.dequeue_task("imports", timeout=1)
    assert prise is not None
    client = store._get_client()
    assert client.llen(PROCESSING) == 1
    # La charge acquittée n'est PAS identique à celle de la file (variante
    # d'``attempt``) : seul le repli « par job_id » peut la retrouver.
    assert store.ack_task("imports", {"job_id": "job-legacy", "attempt": 1}) is True
    assert client.llen(PROCESSING) == 0


def test_boucle_worker_dechu_n_acquitte_pas_et_n_efface_pas_la_tache_reprisee(
    store: RedisStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le chemin complet : verrou perdu pendant l'import → ni ack, ni état terminal."""
    from seamtech_search import worker as module_worker

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier(tmp_path, "affaire-reprise")
    create_job(index, "job-repris", str(source), durability="durable")
    store.enqueue_task("imports", {"job_id": "job-repris", "source_path": str(source)})

    def faux_traitement(tache: dict, *args: object, **kwargs: object) -> dict:
        # Un autre worker reprend la tâche (verrou écrasé) PENDANT le traitement.
        assert store.revendiquer_tache("imports", "job-repris", "worker-B", ttl_seconds=60, reprendre=True)
        return {"job_id": "job-repris", "status": "completed"}

    monkeypatch.setattr(module_worker, "process_import_task", faux_traitement)
    module_worker.worker_loop(config, index, store, worker_id="worker-A", run_once=True)

    client = store._get_client()
    # 1. La tâche reste représentée dans la liste de traitement : si worker-B
    #    meurt à son tour, le balayage la retrouvera.
    assert client.llen(PROCESSING) == 1, (
        "la tâche du repreneur a été retirée de la liste de traitement par le worker déchu"
    )
    # 2. Aucun état terminal n'a été écrit par le worker déchu.
    job = get_job(index, "job-repris")
    assert job is not None and job["status"] != "completed", job
    # 3. Le verrou appartient toujours au repreneur.
    revendication = store.revendication("imports", "job-repris")
    assert revendication is not None and revendication["worker_id"] == "worker-B", revendication
    index.close()


def test_boucle_worker_proprietaire_acquitte_et_libere(
    store: RedisStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chemin nominal préservé : le propriétaire acquitte ET libère son verrou."""
    from seamtech_search import worker as module_worker

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier(tmp_path, "affaire-nominale")
    create_job(index, "job-nominal", str(source), durability="durable")
    store.enqueue_task("imports", {"job_id": "job-nominal", "source_path": str(source)})

    monkeypatch.setattr(
        module_worker,
        "process_import_task",
        lambda tache, *args, **kwargs: {"job_id": "job-nominal", "status": "completed"},
    )
    module_worker.worker_loop(config, index, store, worker_id="worker-nominal", run_once=True)

    client = store._get_client()
    assert client.llen(PROCESSING) == 0, "le worker légitime doit acquitter sa tâche"
    assert store.revendication("imports", "job-nominal") is None, "le verrou doit être libéré"
    job = get_job(index, "job-nominal")
    assert job is not None and job["status"] == "completed", job
    index.close()


def test_r1_vrai_traitement_worker_dechu_n_ecrase_pas_resultat_du_repreneur(
    store: RedisStore, tmp_path: Path
) -> None:
    """R1 : avec le vrai process_import_task, un worker déchu ne peut écraser le résultat du repreneur."""
    from unittest.mock import patch

    from seamtech_search import worker as module_worker
    from seamtech_search.jobs import marquer_job_claim, update_job

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier(tmp_path, "affaire-r1-vraie")
    create_job(index, "lease-test-r1", str(source), durability="durable")
    marquer_job_claim(index, "lease-test-r1", "worker-A")
    store.enqueue_task("imports", {"job_id": "lease-test-r1", "source_path": str(source)})

    actual_import = module_worker.import_folder

    def transfer(*a: Any, **kw: Any) -> Any:
        result = actual_import(*a, **kw)
        # B prend le claim dans Redis ET dans la BDD pendant que A termine son extraction
        assert store.revendiquer_tache("imports", "lease-test-r1", "worker-B", ttl_seconds=60, reprendre=True)
        assert marquer_job_claim(index, "lease-test-r1", "worker-B")
        update_job(index, "lease-test-r1", status="running", result={"owner": "worker-B"}, expected_worker="worker-B")
        return result

    with patch.object(module_worker, "import_folder", side_effect=transfer):
        module_worker.worker_loop(config, index, store, worker_id="worker-A", run_once=True)

    job = get_job(index, "lease-test-r1")
    assert job is not None
    # Le résultat témoin de B n'a PAS été écrasé par A !
    assert job["result"].get("owner") == "worker-B", "Le worker déchu A a écrasé le résultat de B !"
    # La tâche est toujours protégée dans Redis pour B
    assert store.revendication("imports", "lease-test-r1")["worker_id"] == "worker-B"
    client = store._get_client()
    assert client.llen(PROCESSING) == 1, "La tâche pour B doit rester dans processing"

    index.close()


def test_r2_atomicite_ack_lua_avec_transfert_de_claim(store: RedisStore) -> None:
    """R2 : L'opération Lua d'acquittement est atomique et conditionnée au claim."""
    store.enqueue_task("imports", {"job_id": "job-r2-ack", "source_path": "/synthetic"})
    task = store.dequeue_task("imports", timeout=1)
    assert task is not None
    assert store.revendiquer_tache("imports", "job-r2-ack", "worker-A", ttl_seconds=60)

    # Transfert de claim à B
    assert store.revendiquer_tache("imports", "job-r2-ack", "worker-B", ttl_seconds=60, reprendre=True)

    client = store._get_client()
    assert client.llen(PROCESSING) == 1

    # Worker A tente d'acquitter -> refusé atomiquement
    ack_a = store.ack_task("imports", task, worker_id="worker-A")
    assert ack_a is False
    assert client.llen(PROCESSING) == 1, "La tâche de B ne doit pas être supprimée par A"

    # Worker B acquitte -> succès atomique
    ack_b = store.ack_task("imports", task, worker_id="worker-B")
    assert ack_b is True
    assert client.llen(PROCESSING) == 0


def test_r2_retry_et_deadletter_refuses_si_claim_perdu(store: RedisStore) -> None:
    """R2 : retry_task et deadletter_task sont également protégés atomiquement contre un worker déchu."""
    store.enqueue_task("imports", {"job_id": "job-r2-retry", "source_path": "/synthetic"})
    task = store.dequeue_task("imports", timeout=1)
    assert task is not None
    assert store.revendiquer_tache("imports", "job-r2-retry", "worker-A", ttl_seconds=60)

    # Transfert à B
    assert store.revendiquer_tache("imports", "job-r2-retry", "worker-B", ttl_seconds=60, reprendre=True)

    # Worker A tente un retry -> refusé
    assert store.retry_task("imports", task, delay_seconds=10, worker_id="worker-A") is False
    client = store._get_client()
    assert client.zcard("seamtech:retry:imports") == 0
    assert client.llen(PROCESSING) == 1

    # Worker A tente deadletter -> refusé
    assert store.deadletter_task("imports", task, worker_id="worker-A") is False
    assert client.llen("seamtech:deadletter:imports") == 0
    assert client.llen(PROCESSING) == 1

    # Worker B légitime retry -> accepté
    assert store.retry_task("imports", task, delay_seconds=10, worker_id="worker-B") is True
    assert client.zcard("seamtech:retry:imports") == 1
    assert client.llen(PROCESSING) == 0


def test_r1_worker_dechu_apres_cloture_repreneur_ne_peut_pas_reecrire(
    store: RedisStore, tmp_path: Path
) -> None:
    """R1 : Même après clôture par le repreneur B (claimed_by NULL), A ne peut plus écrire."""
    config = _config(tmp_path)
    index = _index(config)
    source = _dossier(tmp_path, "affaire-r1-closed")
    create_job(index, "job-r1-closed", str(source), durability="durable")
    marquer_job_claim(index, "job-r1-closed", "worker-B")

    # B termine le job avec succès -> status completed, claimed_by devient NULL
    ok = terminer_job(index, "job-r1-closed", status="completed", expected_worker="worker-B")
    assert ok is True
    job_after_b = get_job(index, "job-r1-closed")
    assert job_after_b["status"] == "completed"
    assert job_after_b["claimed_by"] is None

    # L'ancien worker A tente d'écrire (status failed, error) avec expected_worker="worker-A"
    upd = update_job(index, "job-r1-closed", status="failed", error="erreur de A", expected_worker="worker-A")
    assert upd is not None
    assert upd["status"] == "completed", "Le worker déchu A a écrasé l'état du job !"
    assert upd["error"] is None

    # L'ancien worker A tente de terminer le job
    term = terminer_job(index, "job-r1-closed", status="failed", failure_reason="rejet A", expected_worker="worker-A")
    assert term is False, "Le worker déchu A a pu terminer le job déjà clos par B !"

    job_final = get_job(index, "job-r1-closed")
    assert job_final["status"] == "completed"
    assert job_final["failure_reason"] is None
    index.close()


def test_r1_exception_handler_dans_process_import_task_necrase_pas_repreneur(
    store: RedisStore, tmp_path: Path
) -> None:
    """R1 : Lorsqu'une exception survient dans process_import_task après prise par B, A ne marque pas le job en échec."""
    from unittest.mock import patch

    from seamtech_search import worker as module_worker

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier(tmp_path, "affaire-r1-exc")
    create_job(index, "job-r1-exc", str(source), durability="durable")
    marquer_job_claim(index, "job-r1-exc", "worker-A")
    store.enqueue_task("imports", {"job_id": "job-r1-exc", "source_path": str(source)})

    def fail_and_transfer(*a: Any, **kw: Any) -> Any:
        # Reprise par B avant ou pendant l'exception
        assert store.revendiquer_tache("imports", "job-r1-exc", "worker-B", ttl_seconds=60, reprendre=True)
        assert marquer_job_claim(index, "job-r1-exc", "worker-B")
        update_job(index, "job-r1-exc", status="running", result={"owner": "worker-B"}, expected_worker="worker-B")
        raise RuntimeError("Crash simulé chez l'ancien worker A")

    with patch.object(module_worker, "import_folder", side_effect=fail_and_transfer):
        module_worker.worker_loop(config, index, store, worker_id="worker-A", run_once=True)

    job = get_job(index, "job-r1-exc")
    assert job is not None
    assert job["status"] == "running", f"L'exception chez A a marqué le job en {job['status']} au lieu de préserver B !"
    assert job["claimed_by"] == "worker-B"
    assert job["result"].get("owner") == "worker-B"
    index.close()
