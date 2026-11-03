"""Course réelle PostgreSQL entre annulation et clôture d'un job."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from seamtech_search.jobs import cancel_job, clear_job_cancel, create_job, get_job, marquer_job_claim, terminer_job
from tests.test_file_durable_postgres import base_durable  # noqa: F401

pytestmark = [pytest.mark.postgres, pytest.mark.redis_queue]


def test_annulation_et_cloture_ne_peuvent_pas_gagner_toutes_les_deux(base_durable):  # noqa: F811
    index = base_durable["index"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(40):
            job_id = uuid.uuid4().hex
            create_job(index, job_id, "/synthetic")
            assert marquer_job_claim(index, job_id, "worker")
            depart = threading.Barrier(2)

            def annuler():
                depart.wait(timeout=10)
                return cancel_job(index, job_id)

            def cloturer():
                depart.wait(timeout=10)
                return terminer_job(index, job_id, status="completed", expected_worker="worker")

            try:
                a, b = pool.submit(annuler), pool.submit(cloturer)
                annule, clos = a.result(timeout=10), b.result(timeout=10)
                final = get_job(index, job_id)
                assert annule["status"] == final["status"]
                assert final["status"] == ("completed" if clos else "cancelled")
                if not clos:
                    assert terminer_job(index, job_id, status="cancelled", expected_worker="worker")
            finally:
                clear_job_cancel(job_id)


def test_lot_observe_annulation_d_un_autre_processus(base_durable, tmp_path, monkeypatch):  # noqa: F811
    import subprocess
    import sys

    from seamtech_search.config import AppConfig
    from seamtech_search.jobs import is_job_cancelled
    from seamtech_search.worker import _process_lot_task

    index = base_durable["index"]
    job_id = uuid.uuid4().hex
    create_job(index, job_id, "/synthetic")
    assert marquer_job_claim(index, job_id, "worker")

    def executer(_index, _id_lot, *, doit_continuer, **_kwargs):
        assert doit_continuer()
        subprocess.run(
            [sys.executable, "-c", (
                "import sys; from seamtech_search.indexer import SearchIndex; "
                "from seamtech_search.jobs import cancel_job; "
                "idx=SearchIndex(sys.argv[1],sys.argv[2]); "
                "assert cancel_job(idx,sys.argv[3])['status']=='cancelled'; idx.close()"
            ), str(tmp_path / "subprocess.db"), base_durable["url"], job_id],
            check=True, timeout=20,
        )
        assert not is_job_cancelled(job_id)
        assert not doit_continuer()
        return {"annule": True}

    monkeypatch.setattr("seamtech_search.fiches.depot.executer_lot", executer)
    monkeypatch.setattr("seamtech_search.fiches.depot.annuler_lot", lambda *_args, **_kwargs: None)
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "unused", min_free_bytes=0)
    resultat = _process_lot_task({"job_id": job_id, "id_lot": 1}, config, index, None,
                                 worker_id="worker", heartbeat=None)
    assert resultat["status"] == "cancelled"
    assert get_job(index, job_id)["status"] == "cancelled"
