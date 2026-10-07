"""Résilience de la file durable SANS Redis : replis en mémoire et garde-fous.

Ces tests ne demandent AUCUN service : ils vérifient le comportement de repli
quand Redis est absent ou en panne, et les garde-fous qui empêchent un job
« en attente » de passer pour un job « échoué » :

* annulation coopérative : si Redis tombe, le drapeau en mémoire prend le
  relais DANS CE PROCESSUS (et l'incident est journalisé — un autre worker ne
  verrait pas l'annulation, c'est dit) ;
* ``get_job`` tolère un ``result`` illisible au lieu d'exploser ;
* ``recover_stale_jobs`` ne marque JAMAIS un job en échec quand il ne peut pas
  savoir s'il attend encore en file : « attendre » est réversible, « échoué »
  ne l'est pas ;
* ``file_durable_disponible`` ne dit « durable » que sur un PING réellement
  réussi (c'est la porte qui évite d'annoncer une acceptation durable alors que
  le job ne vit qu'en mémoire) ;
* la reprise tolère une file qui échoue ou un double non conforme, sans jamais
  transformer un incident en échec définitif.
"""

from __future__ import annotations

import json
from pathlib import Path

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import (
    clear_job_cancel,
    create_job,
    get_job,
    is_job_cancelled,
    recover_stale_jobs,
    register_job_cancel,
)
from seamtech_search.redis_store import RedisStore

#: Port fermé : rien n'écoute, la connexion est refusée immédiatement.
URL_FERMEE = "redis://127.0.0.1:6399/0"


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "index.db",
        min_free_bytes=0,
        redis_url=None,
    )


def _index(config: AppConfig) -> SearchIndex:
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    index.run_migrations()
    return index


class _RedisEnPanne(RedisStore):
    """Redis « configuré » mais dont les opérations de drapeau échouent.

    C'est exactement l'incident réel : la configuration est là, la connexion
    non. Le repli en mémoire doit prendre le relais sans lever d'exception.
    """

    def __init__(self) -> None:
        super().__init__(redis_url=URL_FERMEE)

    def set_cancel_flag(self, job_id: str, ttl_seconds: int = 86400) -> bool:  # noqa: ARG002
        raise RuntimeError("connexion refusée")

    def is_cancelled(self, job_id: str) -> bool:  # noqa: ARG002
        raise RuntimeError("connexion refusée")

    def clear_cancel_flag(self, job_id: str) -> bool:  # noqa: ARG002
        raise RuntimeError("connexion refusée")


def test_annulation_cooperative_repliee_en_memoire_quand_redis_tombe() -> None:
    """Redis en panne : l'annulation demandée reste effective dans ce processus
    (repli mémoire), l'incident est journalisé, et aucune exception ne remonte.
    """
    magasin = _RedisEnPanne()
    assert magasin.is_configured() is True, "le repli n'a de sens que si Redis est configuré"
    identifiant = "job-annulation-repli"

    register_job_cancel(identifiant, magasin)  # Redis échoue → mémoire
    assert is_job_cancelled(identifiant, magasin) is True, (
        "l'annulation doit rester effective malgré la panne Redis"
    )
    assert is_job_cancelled(identifiant, None) is True

    clear_job_cancel(identifiant, magasin)
    assert is_job_cancelled(identifiant, None) is False


def test_get_job_tolere_un_resultat_illisible(tmp_path: Path) -> None:
    """Un ``result`` corrompu en base ne fait pas échouer la lecture du job :
    le reste de la fiche reste exploitable par l'opérateur."""
    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "job-result-ko", str(tmp_path / "dossier"))
    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET result = ? WHERE id = ?", ("{pas du json", "job-result-ko"))

    job = get_job(index, "job-result-ko")
    assert job is not None, "un result illisible ne doit pas rendre le job introuvable"
    assert job["result"] is None
    assert job["status"] == "pending"
    index.close()


def test_recover_stale_jobs_ne_condamne_pas_un_job_sans_certitude(tmp_path: Path) -> None:
    """File illisible : AUCUN job n'est marqué en échec — dans le doute, il
    attend (l'ancien comportement détruisait des jobs légitimement en file)."""
    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "job-en-attente", str(tmp_path / "dossier"))

    class _FileIllisible:
        def job_est_dans_file(self, *args: object, **kwargs: object) -> bool:
            raise RuntimeError("Redis injoignable")

    recuperes = recover_stale_jobs(index, 0, _FileIllisible())
    assert recuperes == 0, "un job ne doit pas être déclaré mort sur une file illisible"
    job = get_job(index, "job-en-attente")
    assert job is not None and job["status"] == "pending", job

    # Sans file joignable du tout (redis_store=None), le job est bien repris en
    # échec AVEC la raison — c'est le seul chemin « plus jamais repris ».
    recuperes = recover_stale_jobs(index, 0, None)
    assert recuperes == 1
    job = get_job(index, "job-en-attente")
    assert job is not None and job["status"] == "failed", job
    assert "orphelin" in (job["failure_reason"] or ""), job
    index.close()


def test_file_durable_disponible_exige_un_ping_reel() -> None:
    """On n'annonce « acceptation durable » que si Redis répond vraiment."""
    from seamtech_search.worker import file_durable_disponible

    class _ConfigureeMaisMorte:
        def is_configured(self) -> bool:
            return True

        def ping(self) -> bool:
            return False

    class _NonConfiguree:
        def is_configured(self) -> bool:
            return False

    class _SansInterface:
        pass

    assert file_durable_disponible(None) is False
    assert file_durable_disponible(_NonConfiguree()) is False  # type: ignore[arg-type]
    assert file_durable_disponible(_ConfigureeMaisMorte()) is False  # type: ignore[arg-type]
    assert file_durable_disponible(_SansInterface()) is False  # type: ignore[arg-type]
    assert file_durable_disponible(RedisStore(redis_url=URL_FERMEE)) is False


def test_reprise_tolere_une_file_en_panne_ou_un_double_non_conforme(tmp_path: Path) -> None:
    """``reconcilier_file`` ne lève jamais : une file qui échoue ou un double
    qui rend n'importe quoi donne une reprise vide et DITE, pas un incident."""
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    vide = {"requeued": [], "dead_lettered": [], "relanced_from_db": [], "failed_from_db": []}

    class _FileEnPanne:
        def reprendre_taches_orphelines(self, *args: object, **kwargs: object) -> list:
            raise RuntimeError("Redis injoignable")

    class _FileBavarde:
        def reprendre_taches_orphelines(self, *args: object, **kwargs: object) -> str:
            return "pas une liste"

    assert reconcilier_file(index, _FileEnPanne()) == vide  # type: ignore[arg-type]
    assert reconcilier_file(index, _FileBavarde()) == vide  # type: ignore[arg-type]

    # …et une décision SANS identifiant de job (charge illisible) est ignorée
    # sans faire échouer la reprise des autres.
    class _FileAvecDecisionAnonyme:
        def reprendre_taches_orphelines(self, *args: object, **kwargs: object) -> list:
            return [{"job_id": None, "action": "dead_lettered", "raison": "charge illisible"}]

    assert reconcilier_file(index, _FileAvecDecisionAnonyme()) == vide  # type: ignore[arg-type]
    index.close()


def test_un_job_encore_en_file_n_est_pas_marque_en_echec(tmp_path: Path) -> None:
    """Le garde-fou qui a coûté un défaut réel : un backlog ne doit pas
    s'auto-détruire. Un job présent dans la file reste en attente."""
    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "job-file-ok", str(tmp_path / "dossier"))

    class _FileVivante:
        def job_est_dans_file(self, *args: object, **kwargs: object) -> bool:
            return True

    assert recover_stale_jobs(index, 0, _FileVivante()) == 0
    job = get_job(index, "job-file-ok")
    assert job is not None and job["status"] == "pending", job
    index.close()


def test_le_drapeau_d_annulation_est_pose_et_retire_sans_redis(tmp_path: Path) -> None:
    """Sans Redis du tout (poste de développement), l'annulation reste
    coopérative à l'intérieur du processus, sans erreur."""
    identifiant = "job-cancel-sans-redis"
    register_job_cancel(identifiant, None)
    assert is_job_cancelled(identifiant, None) is True
    clear_job_cancel(identifiant, None)
    assert is_job_cancelled(identifiant, None) is False
    # Un job inconnu n'est jamais « annulé » par défaut.
    assert is_job_cancelled("job-jamais-annule", None) is False


def test_etat_redis_non_ecrit_quand_redis_est_injoignable() -> None:
    """``update_job`` ne prétend JAMAIS avoir écrit dans Redis : sans Redis, il
    rend ``None`` (l'appelant sait que la copie Redis n'existe pas)."""
    magasin = RedisStore(redis_url=URL_FERMEE)
    assert magasin.update_job("job-etat", {"status": "running"}) is None


def test_charge_de_job_serialisable_sans_secret(tmp_path: Path) -> None:
    """La charge reconstruite depuis la base ne contient que des chemins et des
    identifiants — aucun secret ne transite par la file."""
    from seamtech_search.worker import _charge_depuis_job

    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "job-charge", str(tmp_path / "dossier"), durability="durable")
    job = get_job(index, "job-charge")
    assert job is not None
    charge = _charge_depuis_job(job)
    assert charge is not None and charge["job_id"] == "job-charge"
    texte = json.dumps(charge)
    for interdit in ("password", "secret", "token", "minioadmin", "redis://"):
        assert interdit not in texte.lower(), (interdit, texte)
    index.close()


def test_reprise_avec_verification_de_file_en_panne_garde_le_job_en_cours(tmp_path: Path) -> None:
    """``job_est_dans_file`` qui LÈVE (Redis qui vacille au pire moment) : le
    job n'est ni relancé ni condamné, la reprise continue pour les autres."""
    from seamtech_search.worker import reconcilier_file

    config = _config(tmp_path)
    index = _index(config)
    create_job(index, "job-vacille", str(tmp_path / "dossier"))

    class _FileQuiLeve:
        def reprendre_taches_orphelines(self, *args: object, **kwargs: object) -> list:
            return []

        def job_est_dans_file(self, *args: object, **kwargs: object) -> bool:
            raise RuntimeError("Redis injoignable")

    with index.connect() as connexion:
        connexion.execute("UPDATE import_jobs SET status='running' WHERE id='job-vacille'")

    resume = reconcilier_file(index, _FileQuiLeve())  # type: ignore[arg-type]
    assert resume["failed_from_db"] == [] and resume["relanced_from_db"] == [], resume
    job = get_job(index, "job-vacille")
    assert job is not None and job["status"] == "running", job
    index.close()
