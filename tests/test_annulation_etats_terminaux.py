"""L'annulation ne doit ni mentir sur un job clos ni le ressusciter."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import (
    cancel_job,
    clear_job_cancel,
    create_job,
    get_job,
    is_job_cancelled,
    marquer_job_claim,
    terminer_job,
    update_job,
)


@pytest.fixture
def index(tmp_path: Path):
    index = SearchIndex(tmp_path / "jobs.sqlite")
    index.initialize(rebuild=True)
    return index


@pytest.mark.parametrize("status", ["completed", "needs_review", "failed", "upload_incomplete"])
def test_annuler_job_terminal_ne_modifie_rien(index, status):
    job_id = uuid.uuid4().hex
    create_job(index, job_id, "/synthetic", status=status)
    avant = get_job(index, job_id)
    try:
        resultat = cancel_job(index, job_id)
        assert resultat == avant
        assert get_job(index, job_id) == avant
        assert not is_job_cancelled(job_id)
        assert update_job(index, job_id, status="cancelled") == avant
        assert not terminer_job(index, job_id, status="cancelled")
        assert get_job(index, job_id) == avant
    finally:
        clear_job_cancel(job_id)


def test_annuler_absent_ne_cree_pas_de_drapeau(index):
    job_id = uuid.uuid4().hex
    try:
        assert cancel_job(index, job_id) is None
        assert not is_job_cancelled(job_id)
    finally:
        clear_job_cancel(job_id)


def test_annulation_ne_peut_pas_etre_ecrasee_par_cloture_worker(index):
    job_id = uuid.uuid4().hex
    create_job(index, job_id, "/synthetic")
    assert marquer_job_claim(index, job_id, "worker")
    try:
        assert cancel_job(index, job_id)["status"] == "cancelled"
        assert not is_job_cancelled(job_id), "le registre SQL suffit ; pas de drapeau local tardif"
        assert not terminer_job(index, job_id, status="completed", expected_worker="worker")
        assert get_job(index, job_id)["status"] == "cancelled"
        assert terminer_job(index, job_id, status="cancelled", expected_worker="worker")
        assert get_job(index, job_id)["claimed_by"] is None
    finally:
        clear_job_cancel(job_id)


def test_lot_observe_annulation_en_base_sans_drapeau_local(index, tmp_path, monkeypatch):
    from seamtech_search.config import AppConfig
    from seamtech_search.worker import _process_lot_task

    job_id = uuid.uuid4().hex
    create_job(index, job_id, "/synthetic")
    assert marquer_job_claim(index, job_id, "worker")

    def executer(_index, _id_lot, *, doit_continuer, **_kwargs):
        assert doit_continuer()
        # Simule une API d'un autre processus : aucun flag de ce processus.
        with index.connect() as conn:
            conn.execute("UPDATE import_jobs SET status = 'cancelled' WHERE id = ?", (job_id,))
        assert not is_job_cancelled(job_id)
        assert not doit_continuer()
        return {"annule": True}

    monkeypatch.setattr("seamtech_search.fiches.depot.executer_lot", executer)
    monkeypatch.setattr("seamtech_search.fiches.depot.annuler_lot", lambda *_args, **_kw: None)
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "unused", min_free_bytes=0)
    retour = _process_lot_task({"job_id": job_id, "id_lot": 1}, config, index, None,
                               worker_id="worker", heartbeat=None)
    assert retour["status"] == "cancelled"
    assert get_job(index, job_id)["status"] == "cancelled"


def test_echec_transaction_annulation_ne_cree_pas_de_drapeau(index, monkeypatch):
    from contextlib import contextmanager

    job_id = uuid.uuid4().hex
    create_job(index, job_id, "/synthetic")
    connexion_reelle = index.connect

    @contextmanager
    def connexion_defaillante():
        with connexion_reelle() as conn:
            yield conn
            raise RuntimeError("échec avant commit")

    with monkeypatch.context() as contexte:
        contexte.setattr(index, "connect", connexion_defaillante)
        with pytest.raises(RuntimeError, match="échec avant commit"):
            cancel_job(index, job_id)
    assert get_job(index, job_id)["status"] == "pending"
    assert not is_job_cancelled(job_id)


def test_lot_annule_avec_claim_redis_perdu_ne_clot_pas(index, tmp_path, monkeypatch):
    from seamtech_search.config import AppConfig
    from seamtech_search.worker import _process_lot_task

    job_id = uuid.uuid4().hex
    create_job(index, job_id, "/synthetic")
    assert marquer_job_claim(index, job_id, "worker")
    assert cancel_job(index, job_id)["status"] == "cancelled"

    class RegistrePerdu:
        def is_configured(self):
            return True

        def revendication_appartient_a(self, *_args):
            return False

    def executer(_index, _id_lot, *, doit_continuer, **_kwargs):
        doit_continuer()  # doit lever ClaimLotPerdu, pas accepter l'annulation
        pytest.fail("le claim Redis perdu ne doit jamais autoriser la suite")

    def annuler_interdit(*_args, **_kwargs):
        pytest.fail("un worker déchu ne doit pas annuler le lot")

    monkeypatch.setattr("seamtech_search.fiches.depot.executer_lot", executer)
    monkeypatch.setattr("seamtech_search.fiches.depot.annuler_lot", annuler_interdit)
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "unused", min_free_bytes=0)
    retour = _process_lot_task({"job_id": job_id, "id_lot": 1}, config, index, RegistrePerdu(),
                               worker_id="worker", heartbeat=None)
    assert retour["status"] == "stale_worker"
    assert get_job(index, job_id)["claimed_by"] == "worker"
