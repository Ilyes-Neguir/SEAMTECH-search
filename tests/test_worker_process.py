"""Preuve de bout en bout : worker SÉPARÉ tué en plein import, puis reprise.

Ce test démarre un VRAI processus worker (``python -m seamtech_search.worker_service``),
lui confie un job via un VRAI Redis, le tue par SIGKILL en plein traitement
(aucun nettoyage ne peut s'exécuter), puis vérifie :

1. le job n'est pas perdu : il reste ``running`` en base avec un worker
   propriétaire identifié, et sa tâche est toujours dans la liste de traitement ;
2. après expiration du claim (durée réelle, pas simulée), un SECOND processus
   worker reprend la tâche ;
3. le job se termine, une seule fois : les documents ne sont pas dupliqués.

C'est le scénario que la documentation ne prouvait pas (« worker is a thread
inside web ») : ici le worker est un service à part, et sa mort est un incident
ordinaire, pas une perte de données.

Marqueur ``redis_queue`` : exige ``SEAMTECH_TEST_REDIS_URL``. Ce test n'est pas
exécuté par la sélection par défaut ; il est lancé explicitement en CI et en
recette locale.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import create_job, get_job
from seamtech_search.redis_store import RedisStore

REDIS_URL = os.environ.get("SEAMTECH_TEST_REDIS_URL", "")
RACINE_DEPOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.redis_queue

@pytest.fixture(autouse=True)
def _redis_requis() -> None:
    """Même règle que ``test_file_durable`` : saut par TEST, jamais par MODULE.

    Un saut au niveau du module est compté comme « sauté » dans le JUnit même
    lorsque le marqueur ``redis_queue`` a fait désélectionner le fichier, ce qui
    faisait échouer les garde-fous « aucun test sauté » des jobs CI voisins.
    """
    if not REDIS_URL:  # pragma: no cover - sélection explicite
        pytest.skip("Set SEAMTECH_TEST_REDIS_URL to run the killed-worker scenario")


def _rediger_config(tmp_path: Path, racine_donnees: Path, claim_ttl: int) -> Path:
    """Config JSON d'un worker réel : file durable exigée, claim court."""
    config = {
        "root_paths": [str(racine_donnees)],
        "database_path": str(tmp_path / "index.db"),
        "database_url": None,
        "host": "127.0.0.1",
        "min_free_bytes": 0,
        "storage_backend": "local",
        "redis_url": REDIS_URL,
        "require_durable_queue": True,
        "task_claim_ttl_seconds": claim_ttl,
        "max_task_attempts": 3,
        "queue_reclaim_interval_seconds": 2.0,
        "web_worker_enabled": False,
    }
    chemin = tmp_path / "config.worker.json"
    chemin.write_text(json.dumps(config), encoding="utf-8")
    return chemin


def _pdf_volumineux(chemin: Path, pages: int = 120) -> Path:
    """PDF texte de plusieurs pages : l'extraction prend assez de temps pour
    que le SIGKILL tombe PENDANT le traitement, pas avant ni après."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(chemin), pagesize=A4)
    for numero in range(pages):
        pdf.setFont("Helvetica", 11)
        pdf.drawString(50, 800, f"Fiche technique synthetique page {numero}")
        pdf.drawString(50, 780, "Guindant 12.40 m Bordure 4.10 m Chute 1.20 m")
        pdf.drawString(50, 760, "Tissu Dacron 380 g/m2 Renfort kevlar lattes")
        pdf.showPage()
    pdf.save()
    return chemin


def _lancer_worker(config_chemin: Path, *, une_passe: bool = True) -> subprocess.Popen:
    arguments = [sys.executable, "-m", "seamtech_search.worker_service", "--config", str(config_chemin)]
    if une_passe:
        arguments.append("--une-passe")
    environnement = dict(os.environ)
    environnement["PYTHONPATH"] = str(RACINE_DEPOT)
    return subprocess.Popen(
        arguments,
        cwd=str(RACINE_DEPOT),
        env=environnement,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _attendre(condition, delai: float, message: str):
    limite = time.time() + delai
    while time.time() < limite:
        valeur = condition()
        if valeur:
            return valeur
        time.sleep(0.1)
    raise AssertionError(message)


def test_worker_tue_en_plein_import_est_repris_sans_perte_ni_doublon(tmp_path: Path) -> None:
    magasin = RedisStore(redis_url=REDIS_URL)
    assert magasin.ping(), "Redis injoignable"
    client = magasin._get_client()
    for motif in ("seamtech:*",):
        for cle in client.scan_iter(match=motif, count=200):
            client.delete(cle)

    racine_donnees = tmp_path / "archive"
    dossier = racine_donnees / "AFFAIRE-LOURDE"
    dossier.mkdir(parents=True)
    # Le dossier contient la fiche SYNTHÉTIQUE du dépôt (dossier réel attendu)
    # ET un PDF volumineux, pour que le SIGKILL tombe pendant l'extraction.
    fiche = RACINE_DEPOT / "sample_data/CLIENT-123/fiche-technique.pdf"
    assert fiche.exists(), "fiche synthétique absente : le scénario ne serait pas représentatif"
    (dossier / "fiche-technique.pdf").write_bytes(fiche.read_bytes())
    _pdf_volumineux(dossier / "annexe-volumineuse.pdf", pages=120)
    for numero in range(3):
        (dossier / f"note-{numero}.txt").write_text("note d'atelier", encoding="utf-8")
    fichiers_attendus = len(list(dossier.iterdir()))

    claim_ttl = 15  # minimum accepté par la configuration
    config_chemin = _rediger_config(tmp_path, racine_donnees, claim_ttl)

    index = SearchIndex(tmp_path / "index.db")
    index.initialize(rebuild=True)
    index.run_migrations()
    create_job(index, "job-tue", str(dossier), durability="durable")
    magasin.enqueue_task("imports", {"job_id": "job-tue", "source_path": str(dossier)})
    index.close()

    # --- 1. Un premier worker démarre et prend le job ---------------------
    worker = _lancer_worker(config_chemin, une_passe=False)
    try:
        index = SearchIndex(tmp_path / "index.db")
        _attendre(
            lambda: (get_job(index, "job-tue") or {}).get("status") == "running",
            30,
            "le worker n'a jamais démarré le job",
        )
        # Le job est bien revendiqué par un processus identifié…
        job = get_job(index, "job-tue")
        assert job["claimed_by"], job
        # …et sa tâche est dans la liste de traitement (acquittement non encore fait).
        en_traitement = client.lrange("seamtech:processing:imports", 0, -1)
        assert any("job-tue" in charge for charge in en_traitement), en_traitement

        # --- 2. SIGKILL en plein traitement (aucun nettoyage possible) ----
        os.kill(worker.pid, signal.SIGKILL)
        worker.wait(timeout=10)
        assert worker.returncode == -signal.SIGKILL
        job = get_job(index, "job-tue")
        assert job["status"] == "running", f"état inattendu après la mort du worker : {job}"
        assert job["attempts"] >= 1
        assert client.llen("seamtech:processing:imports") >= 1
        index.close()
    finally:
        if worker.poll() is None:  # pragma: no cover - filet
            os.kill(worker.pid, signal.SIGKILL)

    # --- 3. Le claim expire (durée RÉELLE), puis un second worker reprend --
    _attendre(
        lambda: client.exists("seamtech:claim:imports:job-tue") == 0,
        claim_ttl + 15,
        "le claim du worker mort n'a pas expiré : la tâche resterait bloquée pour toujours",
    )

    second = _lancer_worker(config_chemin, une_passe=True)
    try:
        second.wait(timeout=180)
    finally:
        if second.poll() is None:  # pragma: no cover - filet
            os.kill(second.pid, signal.SIGKILL)

    index = SearchIndex(tmp_path / "index.db")
    job = get_job(index, "job-tue")
    assert job is not None
    assert job["status"] == "completed", f"le job repris n'est pas terminé : {job}"
    assert job["attempts"] >= 2, f"le job n'a pas été redélivré (attempts={job['attempts']})"
    assert job["claimed_by"] is None, "le worker propriétaire n'a pas été libéré en fin de job"

    # --- 4. Aucun doublon : l'import a été exécuté une seule fois ---------
    with index.connect() as connexion:
        documents = connexion.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert documents == fichiers_attendus, (
        f"{documents} documents indexés au lieu de {fichiers_attendus} : "
        "la redélivrance a dupliqué le travail"
    )
    assert magasin.profondeur_file("imports") == {"queue": 0, "processing": 0, "retry": 0, "deadletter": 0}
    index.close()


def test_worker_service_verifier_rend_un_diagnostic_exploitable(tmp_path: Path) -> None:
    """``--verifier`` doit répondre sans rien traiter (supervision systemd/docker)."""
    racine_donnees = tmp_path / "archive"
    racine_donnees.mkdir()
    config_chemin = _rediger_config(tmp_path, racine_donnees, 30)
    resultat = subprocess.run(
        [sys.executable, "-m", "seamtech_search.worker_service", "--config", str(config_chemin), "--verifier"],
        cwd=str(RACINE_DEPOT),
        env={**os.environ, "PYTHONPATH": str(RACINE_DEPOT)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    rapport = json.loads(resultat.stdout)
    assert rapport["redis_joinable"] is True
    assert rapport["durable_ready"] is True
    assert "jobs_par_statut" in rapport and "workers_vivants" in rapport


def test_worker_service_refuse_de_demarrer_sans_redis(tmp_path: Path) -> None:
    """Sans Redis, le worker s'arrête avec un code non nul et une raison claire :
    un superviseur doit le voir, jamais un service « vert » qui ne traite rien."""
    racine_donnees = tmp_path / "archive"
    racine_donnees.mkdir()
    config = json.loads(_rediger_config(tmp_path, racine_donnees, 30).read_text(encoding="utf-8"))
    config["redis_url"] = "redis://:motdepasse@127.0.0.1:6399/0"
    chemin = tmp_path / "config.sans-redis.json"
    chemin.write_text(json.dumps(config), encoding="utf-8")

    resultat = subprocess.run(
        [sys.executable, "-m", "seamtech_search.worker_service", "--config", str(chemin)],
        cwd=str(RACINE_DEPOT),
        env={**os.environ, "PYTHONPATH": str(RACINE_DEPOT)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert resultat.returncode == 2, resultat.stdout + resultat.stderr
    assert "Redis injoignable" in (resultat.stdout + resultat.stderr)
