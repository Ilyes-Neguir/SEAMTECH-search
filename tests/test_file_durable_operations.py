"""Surface opérationnelle de la file durable : supervision, cycle de vie, pannes.

Ce fichier complète ``tests/test_file_durable.py`` (qui prouve la reprise après
mort d'un worker, la redélivrance, la lettre morte et les points de crash) en
couvrant ce qu'un EXPLOITANT regarde et ce qu'un incident provoque :

* registre des workers vivants, revendications (qui travaille sur quoi),
  tâches en traitement, profondeur de file, lettres mortes et rejeu ;
* cycle de vie complet d'une tâche : acquisition, acquittement, nouvelle
  tentative différée, lettre morte, rejeu ;
* **Redis injoignable** : chaque opération de la file rend une réponse SÛRE et
  documentée (jamais une exception, jamais une perte silencieuse) ;
* **boucle du worker** sur ses chemins d'échec : reprise impossible, tâche
  d'un pair vivant (non volée), verrou PERDU (aucun état terminal écrit),
  échec répété → lettre morte AVEC raison, exception dans la boucle.

Il faut un VRAI Redis (``SEAMTECH_TEST_REDIS_URL``). Les cas « Redis
injoignable » se contentent d'un port fermé — mais le module reste marqué
``redis_queue`` : ces tests font partie de la même épreuve d'exploitation.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import create_job, get_job
from seamtech_search.redis_store import WORKER_PREFIX, RedisStore

REDIS_URL = os.environ.get("SEAMTECH_TEST_REDIS_URL", "")
#: Port fermé : rien n'écoute, la connexion est refusée tout de suite (pas de
#: temporisation) — c'est le cas « Redis injoignable » d'un incident réel.
URL_INJOIGNABLE = "redis://127.0.0.1:6399/0"

pytestmark = pytest.mark.redis_queue


@pytest.fixture(autouse=True)
def _redis_requis() -> None:
    """Saute au niveau du TEST (jamais du module) : un module sauté apparaît
    comme « skipped » dans le rapport JUnit même quand ``-m`` l'a désélectionné,
    ce qui ferait échouer les garde-fous CI « 0 test sauté »."""
    if not REDIS_URL:
        pytest.skip("Set SEAMTECH_TEST_REDIS_URL (ex. redis://:motdepasse@127.0.0.1:6379/1)")


def _vider(magasin: RedisStore) -> None:
    """Nettoie les clés de la file, jamais les données d'un autre test."""
    client = magasin._get_client()
    if client is None:
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
def store() -> object:
    magasin = RedisStore(redis_url=REDIS_URL)
    assert magasin.ping(), "Redis injoignable : SEAMTECH_TEST_REDIS_URL est-il correct ?"
    _vider(magasin)
    yield magasin
    _vider(magasin)


def _config(tmp_path: Path, **extra: object) -> AppConfig:
    base: dict[str, object] = {
        "root_paths": [tmp_path],
        "database_path": tmp_path / "index.db",
        "min_free_bytes": 0,
        "redis_url": REDIS_URL,
    }
    base.update(extra)
    return AppConfig(**base)  # type: ignore[arg-type]


def _index(config: AppConfig) -> SearchIndex:
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    index.run_migrations()
    return index


FICHE_SYNTHETIQUE = Path(__file__).resolve().parents[1] / "sample_data/CLIENT-123/fiche-technique.pdf"


def _dossier_source(tmp_path: Path, nom: str = "dossier") -> Path:
    """Dossier d'import valide (PDF technique = fiche SYNTHÉTIQUE du dépôt)."""
    source = tmp_path / nom
    source.mkdir(parents=True, exist_ok=True)
    if FICHE_SYNTHETIQUE.exists():
        (source / "fiche-technique.pdf").write_bytes(FICHE_SYNTHETIQUE.read_bytes())
    else:  # pragma: no cover - le dépôt livre la fiche synthétique
        (source / "fiche-technique.pdf").write_bytes(b"%PDF-1.4\ntraite technique\n")
    return source


# ---------------------------------------------------------------------------
# 1. Supervision : registre des workers, revendications, tâches en traitement
# ---------------------------------------------------------------------------


def test_registre_des_workers_vivants_et_retrait(store: RedisStore) -> None:
    """Le registre dit QUI travaille : enregistrement, lecture, retrait propre.

    C'est ce que lit l'écran d'exploitation pour répondre à « un worker
    tourne-t-il ? ». Une entrée illisible ne doit pas faire disparaître le
    worker de la supervision : elle est exposée telle quelle.
    """
    assert store.enregistrer_worker("worker-a", ttl_seconds=60) is True
    assert store.enregistrer_worker("worker-b", ttl_seconds=60) is True

    vivants = store.workers_vivants()
    assert [v["worker_id"] for v in vivants] == ["worker-a", "worker-b"], vivants
    assert all(isinstance(v.get("vu_le"), (int, float)) for v in vivants), vivants

    # Entrée corrompue (écrite par une version antérieure, ou tronquée) :
    # le worker reste VISIBLE, sans faire échouer la supervision.
    client = store._get_client()
    client.set(f"{WORKER_PREFIX}:worker-casse", "pas du json", ex=60)
    identifiants = [v["worker_id"] for v in store.workers_vivants()]
    assert "worker-casse" in identifiants, identifiants

    # Arrêt propre : le worker se retire (ce n'est pas une mort brutale).
    assert store.desenregistrer_worker("worker-a") is True
    assert [v["worker_id"] for v in store.workers_vivants()] == ["worker-b", "worker-casse"]


def test_revendication_cycle_de_vie_et_protection_du_proprietaire(store: RedisStore) -> None:
    """Une revendication appartient à UN worker : lui seul la prolonge, lui seul
    la libère. C'est la garantie « pas de double traitement » côté supervision.
    """
    store.enqueue_task("imports", {"job_id": "job-1"})
    assert store.dequeue_task("imports", timeout=1) is not None
    assert store.revendiquer_tache("imports", "job-1", "worker-1", ttl_seconds=60) is True

    detail = store.taches_en_traitement("imports")
    assert len(detail) == 1, detail
    assert detail[0]["job_id"] == "job-1"
    assert detail[0]["claim"]["worker_id"] == "worker-1", detail
    assert json.loads(detail[0]["brute"])["job_id"] == "job-1"

    assert store.revendication_appartient_a("imports", "job-1", "worker-1") is True
    assert store.revendication_appartient_a("imports", "job-1", "worker-2") is False

    # Prolongation : le propriétaire oui, un autre worker non (aucune
    # résurrection d'un verrou qui ne lui appartient pas).
    assert store.rafraichir_revendications("imports", ["job-1"], "worker-2", 60) == 0
    assert store.rafraichir_revendications("imports", ["job-1"], "worker-1", 60) == 1
    assert store.rafraichir_revendications("imports", [], "worker-1", 60) == 0

    # Libération : refusée à un autre worker (le repreneur resterait protégé),
    # acceptée au propriétaire, neutre si la clé a déjà expiré.
    assert store.liberer_revendication("imports", "job-1", "worker-2") == -1
    assert store.liberer_revendication("imports", "job-1", "worker-1") == 1
    assert store.liberer_revendication("imports", "job-1") == 0

    # Charge illisible en traitement : elle reste VISIBLE (elle ne peut pas
    # s'exécuter, mais rien ne disparaît sans trace).
    client = store._get_client()
    client.rpush("seamtech:processing:imports", "pas du json")
    detail = store.taches_en_traitement("imports")
    assert detail[-1] == {"job_id": None, "charge": None, "brute": "pas du json"}, detail


def test_profondeur_presence_en_file_sans_recherche_par_sous_chaine(store: RedisStore) -> None:
    """La supervision lit la file par état, et ne confond jamais ``job-1`` avec
    ``job-10`` (l'ancienne recherche par sous-chaîne marquait des jobs sains
    comme perdus)."""
    assert store.enqueue_task("imports", {"job_id": "job-10"}) is True
    profondeur = store.profondeur_file("imports")
    assert profondeur == {"queue": 1, "processing": 0, "retry": 0, "deadletter": 0}, profondeur
    assert store.job_est_dans_file("job-10") is True
    assert store.job_est_dans_file("job-1") is False, (
        "« job-1 » ne doit pas être trouvé par sous-chaîne dans « job-10 »"
    )

    tache = store.dequeue_task("imports", timeout=1)
    assert tache is not None and tache["job_id"] == "job-10"
    assert store.job_est_dans_file("job-10") is True, "une tâche en traitement est ENCORE en file"
    assert store.profondeur_file("imports")["processing"] == 1

    # Nouvelle tentative différée : le job est toujours suivi (ni perdu ni échoué).
    assert store.retry_task("imports", tache, delay_seconds=60) is True
    assert store.job_est_dans_file("job-10") is True
    assert store.profondeur_file("imports") == {"queue": 0, "processing": 0, "retry": 1, "deadletter": 0}
    assert store.job_est_dans_file("job-inexistant") is False


def test_cycle_de_vie_complet_acquittement_lettre_morte_rejeu(store: RedisStore) -> None:
    """Acquittement, nouvelle tentative immédiate, échéance de la file de
    nouvelle tentative, lettre morte, rejeu par l'opérateur, file vide."""
    # Acquittement : la tâche sort de « en traitement ».
    assert store.enqueue_task("imports", {"job_id": "job-2"}) is True
    tache = store.dequeue_task("imports", timeout=1)
    assert tache is not None
    assert store.ack_task("imports", tache) is True
    assert store.profondeur_file("imports")["processing"] == 0

    # Nouvelle tentative IMMÉDIATE : retour en file, tentative incrémentée.
    assert store.retry_task("imports", tache, delay_seconds=0) is True
    assert store.profondeur_file("imports")["queue"] == 1
    reprise = store.dequeue_task("imports", timeout=1)
    assert reprise is not None and reprise["attempt"] == 1, reprise

    # Nouvelle tentative DIFFÉRÉE : pas encore due → la file n'est pas touchée.
    assert store.retry_task("imports", reprise, delay_seconds=3600) is True
    assert store.process_retry_queue("imports") == 0
    client = store._get_client()
    retardataire = client.zrange("seamtech:retry:imports", 0, -1)[0]
    client.zadd("seamtech:retry:imports", {retardataire: 0})  # échéance atteinte
    assert store.process_retry_queue("imports") == 1
    assert store.profondeur_file("imports")["queue"] == 1

    # Lettre morte : visible, avec sa charge, puis rejouable.
    assert store.deadletter_task("imports", {"job_id": "job-mort", "attempt": 3}) is True
    assert store.get_deadletter_count("imports") == 1
    lettres = store.get_deadletters("imports")
    assert lettres and lettres[0]["job_id"] == "job-mort", lettres
    assert store.replay_deadletters("imports") == 1
    assert store.get_deadletter_count("imports") == 0
    assert store.profondeur_file("imports")["queue"] == 2

    # File vide : la boucle de consommation rend la main (pas de blocage).
    while store.dequeue_task("imports", timeout=1) is not None:
        pass
    assert store.dequeue_task("imports", timeout=1) is None


def test_annulation_battements_et_etat_de_job_en_cache(store: RedisStore) -> None:
    """Annulation coopérative, battement de cœur, état de job en cache : les
    trois informations que l'API et le worker échangent par Redis."""
    assert store.set_cancel_flag("job-3") is True
    assert store.is_cancelled("job-3") is True
    assert store.clear_cancel_flag("job-3") is True
    assert store.is_cancelled("job-3") is False

    assert store.set_heartbeat("job-3") is True
    battement = store.get_heartbeat("job-3")
    assert isinstance(battement, float) and battement > 0, battement
    assert store.get_heartbeat("job-jamais-vu") is None

    assert store.set_job("job-3", {"status": "running", "progress": 5}) is True
    maj = store.update_job("job-3", {"progress": 60})
    assert maj is not None and maj["progress"] == 60 and maj["status"] == "running", maj
    assert store.get_job("job-3")["progress"] == 60

    # Job inconnu : update_job crée l'entrée (id conservé) au lieu d'échouer.
    cree = store.update_job("job-4", {"progress": 10})
    assert cree is not None and cree["id"] == "job-4" and cree["progress"] == 10, cree


# ---------------------------------------------------------------------------
# 2. Redis injoignable : une réponse sûre et documentée, jamais une exception
# ---------------------------------------------------------------------------


def test_redis_injoignable_repond_de_facon_sure_et_documentee() -> None:
    """Incident Redis : chaque opération rend sa valeur de repli, aucune
    exception ne remonte, et « dans le doute » ne fait jamais marquer un job
    en échec (``job_est_dans_file`` répond VRAI)."""
    magasin = RedisStore(redis_url=URL_INJOIGNABLE)
    assert magasin.is_configured() is True
    assert magasin.ping() is False

    assert magasin.enregistrer_worker("worker-x") is False
    assert magasin.desenregistrer_worker("worker-x") is False
    assert magasin.workers_vivants() == []

    assert magasin.revendiquer_tache("imports", "job-x", "worker-x") is False
    assert magasin.revendication("imports", "job-x") is None
    assert magasin.revendication_appartient_a("imports", "job-x", "worker-x") is False
    assert magasin.rafraichir_revendications("imports", ["job-x"], "worker-x", 60) == 0
    assert magasin.liberer_revendication("imports", "job-x", "worker-x") == 0

    assert magasin.profondeur_file("imports") == {"queue": 0, "processing": 0, "retry": 0, "deadletter": 0}
    assert magasin.taches_en_traitement("imports") == []
    assert magasin.job_est_dans_file("job-x") is True, (
        "prudence documentée : dans le doute, le job est considéré ENCORE en file "
        "(« attendre » est réversible, « échoué » ne l'est pas)"
    )
    assert magasin.reprendre_taches_orphelines("imports") == []

    assert magasin.enqueue_task("imports", {"job_id": "job-x"}) is False
    assert magasin.dequeue_task("imports", timeout=1) is None
    assert magasin.ack_task("imports", {"job_id": "job-x"}) is False
    assert magasin.retry_task("imports", {"job_id": "job-x"}) is False
    assert magasin.deadletter_task("imports", {"job_id": "job-x"}) is False
    assert magasin.get_deadletter_count("imports") == 0
    assert magasin.get_deadletters("imports") == []
    assert magasin.replay_deadletters("imports") == 0
    assert magasin.process_retry_queue("imports") == 0

    assert magasin.set_job("job-x", {"status": "running"}) is False
    assert magasin.get_job("job-x") is None
    assert magasin.update_job("job-x", {"progress": 1}) is None
    assert magasin.set_cancel_flag("job-x") is False
    assert magasin.is_cancelled("job-x") is False
    assert magasin.clear_cancel_flag("job-x") is False
    assert magasin.set_heartbeat("job-x") is False
    assert magasin.get_heartbeat("job-x") is None


def test_store_non_configure_comme_un_redis_absent() -> None:
    """Aucune URL configurée (poste de développement) : mêmes replis sûrs, et
    ``is_configured()`` dit la vérité — c'est ce que lit l'API pour refuser un
    job quand la file durable est exigée."""
    magasin = RedisStore(redis_url=None)
    assert magasin.is_configured() is False
    assert magasin.ping() is False
    assert magasin.enqueue_task("imports", {"job_id": "job-y"}) is False
    assert magasin.dequeue_task("imports", timeout=1) is None
    assert magasin.profondeur_file("imports") == {"queue": 0, "processing": 0, "retry": 0, "deadletter": 0}
    assert magasin.workers_vivants() == []
    assert magasin.taches_en_traitement("imports") == []


# ---------------------------------------------------------------------------
# 3. Boucle du worker : chemins d'échec (reprise, vol, verrou perdu, échec)
# ---------------------------------------------------------------------------


def test_boucle_worker_reprise_au_demarrage_et_balayage_periodique(
    store: RedisStore, tmp_path: Path
) -> None:
    """Tâche abandonnée AVANT le démarrage, puis balayage périodique forcé :
    dans les deux cas la reprise joue et la tâche se termine."""
    from seamtech_search.worker import worker_loop

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier_source(tmp_path, "affaire-reprise")
    create_job(index, "job-reprise", str(source), durability="durable")

    # Un premier worker prend la tâche… puis meurt sans acquitter (pas de claim).
    store.enqueue_task("imports", {"job_id": "job-reprise", "source_path": str(source)})
    assert store.dequeue_task("imports", timeout=1) is not None
    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET status='running' WHERE id='job-reprise'")

    # Balayage périodique forcé : intervalle quasi nul → il tourne à chaque
    # passe. (0.0 ne conviendrait pas : la boucle traite 0 comme « désactivé »
    # et retombe sur 30 s — garde contre une valeur absente.)
    config.queue_reclaim_interval_seconds = 0.01  # non nul : 0 est traité comme « 30 s »
    worker_loop(config, index, store, worker_id="worker-balayage", run_once=True)

    job = get_job(index, "job-reprise")
    assert job is not None and job["status"] == "completed", job
    # Sortie propre : le worker se retire du registre (il n'est pas « mort »).
    assert store.workers_vivants() == []
    index.close()


def test_boucle_worker_ne_vole_pas_la_tache_d_un_pair_vivant(
    store: RedisStore, tmp_path: Path
) -> None:
    """Deux workers, une tâche : celui qui ne détient pas le verrou ne traite
    PAS et n'écrit aucun état — le propriétaire légitime reste propriétaire."""
    from seamtech_search.worker import worker_loop

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier_source(tmp_path, "affaire-verrou")
    create_job(index, "job-verrou", str(source), durability="durable")
    store.enqueue_task("imports", {"job_id": "job-verrou", "source_path": str(source)})
    assert store.revendiquer_tache("imports", "job-verrou", "worker-occupant", ttl_seconds=60) is True

    worker_loop(config, index, store, worker_id="worker-moi", run_once=True)

    job = get_job(index, "job-verrou")
    assert job is not None and job["status"] == "pending", f"job volé : {job}"
    revendication = store.revendication("imports", "job-verrou")
    assert revendication is not None and revendication["worker_id"] == "worker-occupant"
    with index.connect() as connexion:
        documents = connexion.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert documents == 0, "aucun document ne doit être indexé par un worker qui n'a pas le verrou"
    index.close()


def test_boucle_worker_verrou_perdu_n_ecrit_pas_d_etat_terminal(
    store: RedisStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verrou expiré pendant un import long, repris par un autre worker : le
    premier N'ÉCRIT PAS d'état terminal (il écraserait le résultat du second),
    et il ne libère pas non plus le verrou du repreneur."""
    from seamtech_search import worker as module_worker

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier_source(tmp_path, "affaire-perdue")
    create_job(index, "job-perdu", str(source), durability="durable")
    store.enqueue_task("imports", {"job_id": "job-perdu", "source_path": str(source)})

    def faux_traitement(tache: dict, *args: object, **kwargs: object) -> dict:
        # Simule l'expiration du verrou ET sa reprise par un autre worker
        # pendant que celui-ci travaille encore.
        assert store.revendiquer_tache(
            "imports", "job-perdu", "worker-voleur", ttl_seconds=60, reprendre=True
        )
        return {"job_id": "job-perdu", "status": "completed"}

    monkeypatch.setattr(module_worker, "process_import_task", faux_traitement)
    module_worker.worker_loop(config, index, store, worker_id="worker-moi", run_once=True)

    job = get_job(index, "job-perdu")
    assert job is not None and job["status"] != "completed", (
        f"le worker déchu a écrit un état terminal alors que la reprise fait foi : {job}"
    )
    revendication = store.revendication("imports", "job-perdu")
    assert revendication is not None and revendication["worker_id"] == "worker-voleur", (
        "le worker déchu a libéré le verrou du repreneur"
    )
    index.close()


def test_boucle_worker_echec_repete_puis_lettre_morte_avec_raison(
    store: RedisStore, tmp_path: Path
) -> None:
    """Un import qui échoue est RETENTÉ (visiblement), puis mis en lettre morte
    avec sa raison — le job n'est jamais laissé « en cours » pour toujours."""
    from seamtech_search.worker import worker_loop

    config = _config(tmp_path, max_task_attempts=2)
    index = _index(config)
    source_absente = tmp_path / "dossier-inexistant"
    create_job(index, "job-ko", str(source_absente), durability="durable")
    store.enqueue_task(
        "imports", {"job_id": "job-ko", "source_path": str(source_absente), "selected_pdf": None}
    )

    # Première tentative : échec → nouvelle tentative DIFFÉRÉE. Le registre
    # (base) dit RÉCUPÉRABLE : « échoué » ne s'écrit qu'à l'épuisement.
    worker_loop(config, index, store, worker_id="worker-1", run_once=True)
    assert store.profondeur_file("imports")["retry"] == 1, (
        "l'échec doit programmer une nouvelle tentative, pas disparaître"
    )
    job = get_job(index, "job-ko")
    assert job is not None, job
    assert job["status"] == "pending", f"un job relancé n'est pas un job échoué : {job}"
    assert job["stage"] == "requeued", job
    assert "nouvelle tentative" in (job["error"] or ""), job

    # La tentative différée arrive à échéance → seconde tentative → lettre morte.
    client = store._get_client()
    for charge in client.zrange("seamtech:retry:imports", 0, -1):
        client.zadd("seamtech:retry:imports", {charge: 0})
    worker_loop(config, index, store, worker_id="worker-2", run_once=True)

    job = get_job(index, "job-ko")
    assert job is not None and job["status"] == "failed", job
    assert "lettre morte" in (job["failure_reason"] or ""), job
    assert store.get_deadletter_count("imports") == 1
    lettres = store.get_deadletters("imports")
    assert lettres and lettres[0]["job_id"] == "job-ko", lettres
    index.close()


def test_boucle_worker_statuts_cancelled_et_needs_review_en_base(
    store: RedisStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le statut rendu par le traitement est reflété dans le registre (base) :
    annulation et revue nécessaire ne se confondent pas avec un succès."""
    from seamtech_search import worker as module_worker

    config = _config(tmp_path)
    index = _index(config)
    for job_id in ("job-annule", "job-revue"):
        create_job(index, job_id, str(tmp_path / job_id), durability="durable")
        store.enqueue_task("imports", {"job_id": job_id, "source_path": str(tmp_path / job_id)})

    def faux_traitement(tache: dict, *args: object, **kwargs: object) -> dict:
        job_id = str(tache["job_id"])
        return {"job_id": job_id, "status": "cancelled" if job_id == "job-annule" else "needs_review"}

    monkeypatch.setattr(module_worker, "process_import_task", faux_traitement)
    module_worker.worker_loop(config, index, store, worker_id="worker-statuts", run_once=True)

    annule = get_job(index, "job-annule")
    revue = get_job(index, "job-revue")
    assert annule is not None and annule["status"] == "cancelled", annule
    assert annule["failure_reason"] == "annulé par l'opérateur", annule
    assert revue is not None and revue["status"] == "needs_review", revue
    index.close()


def test_boucle_worker_reprise_impossible_est_dite_et_ne_bloque_pas(
    store: RedisStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis en panne au moment de la reprise : le worker le DIT et continue
    (au démarrage comme au balayage périodique) au lieu de mourir en silence."""
    from seamtech_search import worker as module_worker

    config = _config(tmp_path)
    config.queue_reclaim_interval_seconds = 0.01  # quasi nul : le balayage tourne à chaque passe
    index = _index(config)

    def reprise_impossible(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("Redis injoignable pendant la reprise")

    monkeypatch.setattr(module_worker, "reconcilier_file", reprise_impossible)
    with pytest.raises(RuntimeError):  # le double est bien celui qui est appelé
        module_worker.reconcilier_file(index, store)
    # …et la boucle, elle, ne lève pas : elle journalise et rend la main.
    module_worker.worker_loop(config, index, store, worker_id="worker-reprise-ko", run_once=True)
    assert store.workers_vivants() == []
    index.close()


def test_boucle_worker_exception_de_consommation_est_journalisee(
    store: RedisStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Une exception dans la consommation (Redis qui tombe en pleine boucle) est
    journalisée et la boucle s'arrête proprement en mode ``run_once`` — jamais
    un process qui tourne à vide en silence."""
    from seamtech_search.worker import worker_loop

    config = _config(tmp_path)
    index = _index(config)

    def dequeue_en_panne(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("connexion perdue")

    monkeypatch.setattr(store, "dequeue_task", dequeue_en_panne)
    with caplog.at_level("ERROR"):
        worker_loop(config, index, store, worker_id="worker-panne", run_once=True)
    assert "Error in Redis worker loop" in caplog.text, caplog.text
    index.close()


def test_worker_loop_traite_une_tache_de_lot(store: RedisStore, tmp_path: Path) -> None:
    """Une tâche de type « lot » (``lot-<id>``) est routée vers le traitement de
    lot, pas vers l'import d'un dossier — et son échec est visible en base."""
    from seamtech_search.worker import worker_loop

    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "lot-999999", "", durability="durable")
    store.enqueue_task("imports", {"job_id": "lot-999999", "kind": "lot", "id_lot": 999999})

    worker_loop(config, index, store, worker_id="worker-lot", run_once=True)

    job = get_job(index, "lot-999999")
    assert job is not None, job
    profondeur = store.profondeur_file("imports")
    assert profondeur["processing"] == 0, (
        f"un lot impossible ne doit pas rester « en traitement » : {profondeur}"
    )
    if job["status"] == "pending":
        # Relancé (tentatives restantes) : la relance est RÉELLE, programmée en
        # file, et le registre le dit — jamais un échec silencieux.
        assert profondeur["retry"] + profondeur["queue"] >= 1, (job, profondeur)
    else:
        assert job["status"] in {"failed", "needs_review", "completed"}, job
    index.close()


def test_redis_redemarre_sans_aof_le_job_running_est_relance(store: RedisStore, tmp_path: Path) -> None:
    """Redis vidé (redémarrage sans persistance) : un job « running » en base
    qui n'existe plus dans la file est RELANCÉ depuis la base — le registre
    reste la vérité, jamais un job bloqué « en cours » à vie."""
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier_source(tmp_path, "affaire-redemarrage")
    create_job(index, "job-redemarrage", str(source), durability="durable")
    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET status='running' WHERE id='job-redemarrage'")

    resume = reconcilier_file(index, store, max_tentatives=3)
    assert resume["relanced_from_db"] == ["job-redemarrage"], resume
    assert store.job_est_dans_file("job-redemarrage") is True
    job = get_job(index, "job-redemarrage")
    assert job is not None and job["status"] == "pending", job

    # …et la tâche relancée s'exécute vraiment.
    from seamtech_search.worker import worker_loop

    worker_loop(config, index, store, worker_id="worker-relance", run_once=True)
    job = get_job(index, "job-redemarrage")
    assert job is not None and job["status"] == "completed", job
    index.close()


def test_reconcilier_file_charge_absente_et_file_illisible(
    store: RedisStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deux cas limites de la reprise, tous deux SANS silence : un job orphelin
    non ré-enfilable (charge absente) échoue AVEC raison ; une remise en file
    impossible (Redis en panne) est dite, le job n'est pas marqué à tort."""
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    # Job « running » sans source_path : aucune charge ne peut être reconstruite.
    create_job(index, "job-sans-charge", "", durability="durable")
    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET status='running' WHERE id='job-sans-charge'")

    resume = reconcilier_file(index, store, max_tentatives=3)
    assert resume["failed_from_db"] == ["job-sans-charge"], resume
    job = get_job(index, "job-sans-charge")
    assert job is not None and job["status"] == "failed", job
    assert "charge absente" in (job["failure_reason"] or ""), job

    # Remise en file impossible (Redis indisponible au moment précis) : le job
    # n'est PAS marqué échoué, l'échec est journalisé.
    source = _dossier_source(tmp_path, "affaire-sans-redis")
    create_job(index, "job-file-ko", str(source), durability="durable")
    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET status='running' WHERE id='job-file-ko'")
    monkeypatch.setattr(store, "enqueue_task", lambda *a, **k: False)

    resume = reconcilier_file(index, store, max_tentatives=3)
    assert resume["relanced_from_db"] == [] and resume["failed_from_db"] == [], resume
    job = get_job(index, "job-file-ko")
    assert job is not None and job["status"] != "failed", (
        f"Redis indisponible ne doit pas transformer un job en échec : {job}"
    )
    index.close()


def test_reconcilier_file_tolere_une_charge_illisible(store: RedisStore, tmp_path: Path) -> None:
    """Charge illisible en traitement : sortie EXPLICITE de la file, mise en
    lettre morte avec la raison — visible de l'opérateur, jamais perdue."""
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    client = store._get_client()
    client.rpush("seamtech:processing:imports", "pas du json")

    resume = reconcilier_file(index, store, max_tentatives=3)
    assert resume == {
            "requeued": [], "dead_lettered": [], "relanced_from_db": [], "failed_from_db": [],
            "redis_indisponible": [],
        }, resume
    lettres = store.get_deadletters("imports")
    assert lettres and lettres[0]["raison"] == "charge illisible", lettres
    assert store.profondeur_file("imports")["processing"] == 0
    index.close()


def test_reconcilier_file_ne_marque_pas_en_echec_si_la_file_est_illisible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``job_est_dans_file`` qui échoue (Redis qui vacille) : la reprise
    CONSIDÈRE le job encore en file — « attendre » est réversible, « échoué »
    ne l'est pas."""
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier_source(tmp_path, "affaire-vacille")
    create_job(index, "job-vacille", str(source), durability="durable")
    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET status='running' WHERE id='job-vacille'")

    magasin = RedisStore(redis_url=URL_INJOIGNABLE)
    resume = reconcilier_file(index, magasin, max_tentatives=3)
    assert resume["failed_from_db"] == [] and resume["relanced_from_db"] == [], resume
    job = get_job(index, "job-vacille")
    assert job is not None and job["status"] == "running", job
    index.close()


def test_boucle_worker_retire_le_worker_du_registre_et_le_heartbeat_s_arrete(
    store: RedisStore, tmp_path: Path
) -> None:
    """Après un passage de boucle, plus aucun battement de cœur ne traîne pour
    un job terminé — la supervision ne montre pas de travail fantôme."""
    from seamtech_search.worker import worker_loop

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier_source(tmp_path, "affaire-heartbeat")
    create_job(index, "job-hb", str(source), durability="durable")
    store.enqueue_task("imports", {"job_id": "job-hb", "source_path": str(source)})

    worker_loop(config, index, store, worker_id="worker-hb", run_once=True)

    job = get_job(index, "job-hb")
    assert job is not None and job["status"] == "completed", job
    # Aucun battement de cœur ne subsiste pour un job terminé : la supervision
    # ne montre pas de travail fantôme (le TTL court de nettoyage fait foi).
    assert store.get_heartbeat("job-hb") is None
    assert store.workers_vivants() == []
    assert store.profondeur_file("imports")["processing"] == 0
    index.close()


def test_redis_indisponible_pendant_la_boucle_ne_perd_pas_la_tache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis injoignable PENDANT la boucle : le worker ne traite rien (il ne
    peut pas revendiquer), la tâche reste en file pour le worker suivant, et
    le job en base reste récupérable."""
    from seamtech_search import worker as module_worker
    from seamtech_search.jobs import create_job as creer

    config = _config(tmp_path)
    index = _index(config)
    creer(index, "job-file-perdue", "dossier", durability="durable")
    # Double de store : Redis répond au ping mais la revendication échoue
    # (c'est exactement ce que fait un incident réseau en cours de boucle).
    magasin = RedisStore(redis_url=REDIS_URL)
    assert magasin.enqueue_task("imports", {"job_id": "job-file-perdue", "source_path": "dossier"})
    monkeypatch.setattr(magasin, "revendiquer_tache", lambda *a, **k: False)
    taches = [{"job_id": "job-file-perdue", "source_path": "dossier"}]

    def dequeue_une_fois(*args: object, **kwargs: object) -> dict | None:
        return taches.pop() if taches else None

    monkeypatch.setattr(magasin, "dequeue_task", dequeue_une_fois)
    monkeypatch.setattr(magasin, "_get_client", lambda: None)

    module_worker.worker_loop(config, index, magasin, worker_id="worker-ko", run_once=True)

    job = get_job(index, "job-file-perdue")
    assert job is not None and job["status"] == "pending", (
        f"un worker qui ne peut pas revendiquer ne doit rien écrire : {job}"
    )
    index.close()
    _vider(RedisStore(redis_url=REDIS_URL))


def test_stockage_de_la_file_durable_expose_tous_les_etats() -> None:
    """Documentation exécutable des états d'une tâche : la boucle et la reprise
    ne manipulent que ces clés-là (toute nouvelle clé doit être ajoutée ici)."""
    from seamtech_search.redis_store import queue_keys

    cles = queue_keys("imports")
    assert {"queue", "processing", "retry", "deadletter", "claim_prefix"} <= set(cles), cles
    assert cles["queue"] == "seamtech:queue:imports", cles
    assert cles["processing"] == "seamtech:processing:imports", cles
    assert cles["retry"] == "seamtech:retry:imports", cles
    assert cles["deadletter"] == "seamtech:deadletter:imports", cles
    assert cles["claim_prefix"] == "seamtech:claim:imports", cles
    assert WORKER_PREFIX.startswith("seamtech:")


def test_latence_de_consommation_bornee_sur_file_vide(store: RedisStore) -> None:
    """File vide : ``dequeue_task`` rend la main en un temps borné (la boucle ne
    doit pas bloquer indéfiniment quand il n'y a rien à faire)."""
    debut = time.monotonic()
    assert store.dequeue_task("imports", timeout=1) is None
    duree = time.monotonic() - debut
    assert duree < 10.0, f"attente anormale sur file vide : {duree:.1f}s"


def test_reconcilier_file_marque_en_echec_les_tentatives_epuisees(store: RedisStore, tmp_path: Path) -> None:
    """Tentatives épuisées : le job passe en ÉCHEC DÉFINITIF avec la raison —
    jamais une reprise infinie, jamais une disparition silencieuse."""
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    source = _dossier_source(tmp_path, "affaire-epuisee")
    create_job(index, "job-epuise", str(source), durability="durable")
    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET status='running' WHERE id='job-epuise'")
    # Tâche abandonnée EN TRAITEMENT (le worker est mort après l'avoir prise),
    # déjà à la dernière tentative autorisée — c'est ce que voit la reprise.
    store.enqueue_task("imports", {"job_id": "job-epuise", "source_path": str(source), "attempt": 2})
    assert store.dequeue_task("imports", timeout=1) is not None
    assert store.profondeur_file("imports")["processing"] == 1

    resume = reconcilier_file(index, store, max_tentatives=3)
    assert resume["dead_lettered"] == ["job-epuise"], resume
    job = get_job(index, "job-epuise")
    assert job is not None and job["status"] == "failed", job
    assert "tentatives épuisées" in (job["failure_reason"] or ""), job
    assert store.get_deadletter_count("imports") == 1
    index.close()


def test_balayage_periodique_signale_une_reprise(
    store: RedisStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Le balayage périodique n'est pas muet : quand il reprend une tâche, il
    le journalise (l'exploitant doit pouvoir constater qu'une reprise a eu lieu)."""
    from seamtech_search.worker import worker_loop

    config = _config(tmp_path)
    config.queue_reclaim_interval_seconds = 0.01  # quasi nul : le balayage tourne à chaque passe
    index = _index(config)
    source = _dossier_source(tmp_path, "affaire-balayage")
    create_job(index, "job-balayage", str(source), durability="durable")

    # Une tâche « leurre » maintient la boucle en vie le temps d'un second
    # balayage : sans elle, ``run_once`` s'arrête dès la file vide et le
    # balayage périodique n'aurait pas lieu (c'est le comportement voulu).
    create_job(index, "job-leurre", str(source), durability="durable")
    store.enqueue_task("imports", {"job_id": "job-leurre", "source_path": str(source)})

    appels = {"n": 0}
    vrai = store.reprendre_taches_orphelines

    def decision_au_second_appel(*args: object, **kwargs: object) -> list:
        appels["n"] += 1
        if appels["n"] == 1:
            return vrai(*args, **kwargs)  # au démarrage : file vide
        return [{"job_id": "job-balayage", "action": "requeued", "attempt": 1, "raison": "worker interrompu"}]

    store.reprendre_taches_orphelines = decision_au_second_appel  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger="seamtech_search.worker"):
        worker_loop(config, index, store, worker_id="worker-balayage", run_once=True)
    messages = [enregistrement.getMessage() for enregistrement in caplog.records]

    assert appels["n"] >= 2, "le balayage périodique doit avoir tourné"
    leurre = get_job(index, "job-leurre")
    assert leurre is not None and leurre["status"] == "completed", leurre
    # Correctif A06 (audit du 2026-10-08) : un job ACTIF absent de la file n'est
    # plus laissé « en attente pour toujours ». Le balayage le ré-enfile, et la
    # boucle le traite dans la foulée. L'assertion historique (``pending``
    # figé) décrivait précisément l'état orphelin que l'audit a relevé ; elle
    # porte maintenant sur l'effet observable : le job est SORTI de l'état
    # orphelin, et la reprise est journalisée.
    job = get_job(index, "job-balayage")
    assert job is not None and job["status"] in {"completed", "needs_review", "running"}, job
    assert any("absent de la file" in message for message in messages), messages
    index.close()


def test_traitement_de_lot_annule_et_partiellement_en_echec(
    store: RedisStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un lot annulé par l'opérateur et un lot avec dossiers en échec produisent
    deux états DISTINCTS dans le registre (annulé ≠ échec), chacun avec sa raison."""
    from seamtech_search.fiches import depot as module_depot
    from seamtech_search.worker import _process_lot_task

    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "lot-1001", "", durability="durable")
    create_job(index, "lot-1002", "", durability="durable")

    annulations: list[int] = []

    def faux_annuler_lot(index_: object, id_lot: int) -> dict:
        annulations.append(id_lot)
        return {"id_lot": id_lot, "statut": "annule"}

    monkeypatch.setattr(module_depot, "annuler_lot", faux_annuler_lot)

    monkeypatch.setattr(module_depot, "executer_lot", lambda *a, **k: {"annule": True, "nb_echecs": 0})
    resultat = _process_lot_task(
        {"job_id": "lot-1001", "id_lot": 1001}, config, index, store, worker_id="worker-lot", heartbeat=None
    )
    assert resultat["status"] == "cancelled", resultat
    assert annulations == [1001], "un lot annulé doit être marqué comme tel en base"
    job = get_job(index, "lot-1001")
    assert job is not None and job["status"] == "cancelled", job

    monkeypatch.setattr(
        module_depot, "executer_lot", lambda *a, **k: {"annule": False, "nb_echecs": 2, "nb_traites": 5}
    )
    resultat = _process_lot_task(
        {"job_id": "lot-1002", "id_lot": 1002}, config, index, store, worker_id="worker-lot", heartbeat=None
    )
    assert resultat["status"] == "needs_review", resultat
    job = get_job(index, "lot-1002")
    assert job is not None and job["status"] == "needs_review", job
    assert "2 dossier(s) en échec" in (job["failure_reason"] or ""), job
    index.close()


def test_traitement_de_lot_impossible_est_visible_en_base(
    store: RedisStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lot impossible (dépôt refusé) : échec explicite, avec le message du
    dépôt — l'opérateur sait quoi corriger."""
    from seamtech_search.fiches import depot as module_depot
    from seamtech_search.worker import _process_lot_task

    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "lot-1003", "", durability="durable")

    def refus(*args: object, **kwargs: object) -> dict:
        raise module_depot.DepotImpossible("gabarit manquant")

    monkeypatch.setattr(module_depot, "executer_lot", refus)
    resultat = _process_lot_task(
        {"job_id": "lot-1003", "id_lot": 1003}, config, index, store, worker_id="worker-lot", heartbeat=None
    )
    assert resultat["status"] == "failed", resultat
    assert "gabarit manquant" in resultat["error"], resultat
    job = get_job(index, "lot-1003")
    assert job is not None and job["status"] == "failed", job
    index.close()
