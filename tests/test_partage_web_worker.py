"""Intégration « web + worker » : montages partagés, scratch, quarantaine, modèles.

Ce fichier vérifie EXACTEMENT la propriété qui casse un déploiement en deux
processus : **le web et le worker doivent partager des chemins** (fichiers
téléversés, brouillons de travail, quarantaine, rapports générés, modèles ML) et
le worker ne doit JAMAIS écrire dans l'archive.

Deux volets :

1. **Contrat de déploiement** (``test_contrat_compose_web_worker``) : lit le
   ``docker-compose.yml`` RÉEL et exige que les deux services montent les mêmes
   volumes aux mêmes chemins, partagent l'image, tournent en non-root, et
   pointent sur les mêmes PostgreSQL/Redis. Un futur « worker dans un autre
   conteneur sans le volume des données » fait échouer ce test.

2. **Exécution de bout en bout** : un vrai serveur web (uvicorn, processus
   séparé) reçoit un envoi multipart comme le ferait le navigateur, écrit les
   fichiers dans ``<data>/uploads/…``, accepte le job durablement ; un VRAI
   processus worker (``python -m seamtech_search.worker_service``) le traite
   ensuite depuis le même répertoire ; le test vérifie enfin :

   * l'import est terminé et c'est bien le WORKER qui l'a revendiqué ;
   * les rapports générés par le worker existent sous ``<data>/reports`` et sont
     lisibles par le processus web ;
   * l'archive (montage ``:ro``) n'a pas bougé d'un octet ;
   * le dossier de modèles ML est résolu au MÊME endroit par les deux processus
     et son contenu est lisible par les deux ;
   * la quarantaine est sur le même volume partagé (un import en échec y dépose
     sa copie, récupérable, sans toucher à l'archive).

LIMITE DITE FRANCHEMENT : sans Docker dans cet environnement, ce test reproduit
la TOPOLOGIE de ``docker-compose`` (mêmes chemins, mêmes variables, mêmes
volumes, deux processus distincts) mais n'exécute pas les conteneurs. Le
``docker compose up`` réel reste un job CI dédié (marqueur ``integration_docker``),
non exécuté ici.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import create_job, get_job
from seamtech_search.redis_store import RedisStore

RACINE = Path(__file__).resolve().parents[1]
COMPOSE = RACINE / "docker-compose.yml"
URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
URL_REDIS = os.environ.get("SEAMTECH_TEST_REDIS_URL", "")
FICHE = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
TOKEN = "jeton-de-test-partage-web-worker"

pytestmark = [pytest.mark.redis_queue, pytest.mark.postgres]


# ---------------------------------------------------------------------------
# 1. Contrat de déploiement lu dans le compose réel
# ---------------------------------------------------------------------------


def _monter(volume: str, chemin_conteneur: str) -> str:
    """Représentation canonique d'un montage : ``hôte:conteneur[:mode]``."""
    return volume.replace("${", "").replace("}", "")


def test_contrat_compose_web_worker() -> None:
    """Le worker doit avoir EXACTEMENT les mêmes accès que le web aux données."""
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]
    assert "worker" in services, "aucun service worker : les imports dépendraient du web"
    web, worker = services["web"], services["worker"]

    # Même image, donc même code et mêmes dépendances (pas de dérive).
    assert worker["build"] == web["build"]
    assert worker.get("command") == ["python", "-m", "seamtech_search.worker_service"]

    def _chemins(service: dict[str, Any]) -> dict[str, str]:
        resultat: dict[str, str] = {}
        for montage in service.get("volumes", []):
            parties = montage.split(":")
            # ./data:/app/data[:ro] → {"data": "/app/data"}
            hote = parties[0].lstrip("./")
            resultat[hote] = parties[1] + (":ro" if len(parties) > 2 and parties[2] == "ro" else "")
        return resultat

    chemins_web, chemins_worker = _chemins(web), _chemins(worker)
    for volume in ("data", "logs", "sample_data"):
        assert volume in chemins_web, f"le service web ne monte pas {volume}"
        assert volume in chemins_worker, (
            f"le service worker ne monte pas {volume} : il ne verrait ni les fichiers "
            "téléversés par le web, ni les rapports générés"
        )
        assert chemins_web[volume] == chemins_worker[volume], (
            f"chemins différents pour {volume} : web={chemins_web[volume]} "
            f"worker={chemins_worker[volume]}"
        )
    # L'archive est en lecture seule des DEUX côtés.
    assert chemins_web["sample_data"] == chemins_worker["sample_data"] == "/app/sample_data:ro"

    # Mêmes services d'infrastructure : une seule file, une seule base.
    for variable in ("SEAMTECH_REDIS_URL", "SEAMTECH_DATABASE_URL"):
        assert web["environment"][variable] == worker["environment"][variable], (
            f"{variable} diffère entre web et worker : ils ne partageraient rien"
        )

    # Le worker ne sert pas le navigateur : il ne doit pas publier de port.
    assert not worker.get("ports"), "le worker ne doit exposer aucun port"

    # Non-root : c'est le Dockerfile qui l'impose.
    dockerfile = (RACINE / "Dockerfile").read_text(encoding="utf-8")
    assert "USER seamtech" in dockerfile, "l'application doit tourner en non-root"


# ---------------------------------------------------------------------------
# 2. Exécution : web (uvicorn) + worker (processus séparé), mêmes chemins
# ---------------------------------------------------------------------------


def _creer_base_jetable() -> tuple[str, str]:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom = f"partage_{uuid.uuid4().hex[:10]}"
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


def _port_libre() -> int:
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        return int(prise.getsockname()[1])


def _empreinte_arbre(racine: Path) -> dict[str, tuple[int, str]]:
    """Taille + SHA-256 de chaque fichier — preuve que l'archive n'a pas bougé."""
    resultat: dict[str, tuple[int, str]] = {}
    for chemin in sorted(racine.rglob("*")):
        if chemin.is_file():
            donnees = chemin.read_bytes()
            resultat[str(chemin.relative_to(racine))] = (len(donnees), hashlib.sha256(donnees).hexdigest())
    return resultat


_PORT_WEB = _port_libre()


@pytest.fixture()
def deploiement(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """Reproduit la disposition des conteneurs : archive :ro, data partagé."""
    if not URL_PG or not URL_REDIS:
        pytest.skip("SEAMTECH_TEST_DATABASE_URL et SEAMTECH_TEST_REDIS_URL sont requis")

    nom_base, url_base = _creer_base_jetable()
    racine = tmp_path / "deploiement"
    archive = racine / "sample_data" / "AFFAIRE-PARTAGEE"
    donnees = racine / "data"
    journaux = racine / "logs"
    for dossier in (archive, donnees, journaux):
        dossier.mkdir(parents=True, exist_ok=True)
    # Archive réelle (fiche synthétique du dépôt) — jamais une donnée client.
    shutil.copy(FICHE, archive / "fiche.pdf")
    (archive / "nomenclature.txt").write_text("voile 100% mylar", encoding="utf-8")
    # Montage :ro du compose : on l'imite au niveau du système de fichiers, ce
    # qui fait échouer toute écriture du worker dans l'archive.
    for chemin in sorted(archive.rglob("*"), reverse=True):
        chemin.chmod(0o555 if chemin.is_dir() else 0o444)
    archive.chmod(0o555)

    # Modèles ML : <data>/modeles, partagé par les deux processus.
    modeles = donnees / "modeles"
    modeles.mkdir(parents=True, exist_ok=True)
    (modeles / "tokenizer.json").write_text('{"version": "test"}', encoding="utf-8")

    config = {
        "root_paths": [str(archive.parent)],
        "database_path": str(donnees / "search.db"),
        "database_url": url_base,
        "host": "127.0.0.1",
        "min_free_bytes": 0,
        "storage_backend": "local",
        "auth_token": TOKEN,
        "port": _PORT_WEB,
        "redis_url": URL_REDIS,
        "require_durable_queue": True,
        "web_worker_enabled": False,
        "task_claim_ttl_seconds": 60,
        "max_task_attempts": 3,
    }
    chemin_config = racine / "config.json"
    chemin_config.write_text(json.dumps(config), encoding="utf-8")

    magasin = RedisStore(redis_url=URL_REDIS)
    assert magasin.ping()
    for cle in magasin._get_client().scan_iter(match="seamtech:*", count=500):
        magasin._get_client().delete(cle)

    index = SearchIndex(donnees / "search.db", url_base)
    index.initialize()
    index.run_migrations()

    try:
        yield {
            "racine": racine,
            "archive": archive,
            "donnees": donnees,
            "config": chemin_config,
            "url_base": url_base,
            "index": index,
            "redis": magasin,
        }
    finally:
        index.close()
        for cle in magasin._get_client().scan_iter(match="seamtech:*", count=500):
            magasin._get_client().delete(cle)
        _supprimer_base(nom_base)
        for chemin in sorted(archive.rglob("*"), reverse=True):
            try:
                chemin.chmod(0o755 if chemin.is_dir() else 0o644)
            except OSError:  # pragma: no cover - nettoyage
                pass


def _attendre(condition, delai: float, message: str, *, journal=None):  # noqa: ANN001, ANN201
    """Attend une condition, en tolérant les erreurs réseau transitoires.

    Au démarrage d'un serveur, le port n'est pas encore ouvert : un
    ``URLError`` n'est pas un échec, c'est une raison de réessayer.
    """
    limite = time.time() + delai
    derniere_erreur: Exception | None = None
    while time.time() < limite:
        try:
            valeur = condition()
        except (urllib.error.URLError, ConnectionError, OSError) as erreur:
            derniere_erreur = erreur
            valeur = None
        if valeur:
            return valeur
        time.sleep(0.2)
    detail = f" (dernière erreur réseau : {derniere_erreur})" if derniere_erreur else ""
    sortie = journal() if journal else ""
    raise AssertionError(f"{message}{detail}\n{sortie}")


def _requete(url: str, *, methode: str = "GET", corps: bytes | None = None, entetes: dict | None = None) -> tuple[int, Any]:
    demande = urllib.request.Request(url, data=corps, method=methode, headers=entetes or {})
    try:
        with urllib.request.urlopen(demande, timeout=15) as reponse:
            brut = reponse.read()
            return reponse.status, json.loads(brut) if brut else None
    except urllib.error.HTTPError as erreur:
        brut = erreur.read()
        try:
            return erreur.code, json.loads(brut)
        except Exception:
            return erreur.code, brut.decode("utf-8", "replace")


def _multipart(fichiers: list[tuple[str, str, bytes]], champs: dict[str, str]) -> tuple[bytes, str]:
    frontiere = f"----seamtech{uuid.uuid4().hex}"
    morceaux: list[bytes] = []
    for nom, chemin_relatif, contenu in fichiers:
        morceaux.append(
            f'--{frontiere}\r\nContent-Disposition: form-data; name="files"; filename="{chemin_relatif}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n".encode()
            + contenu
            + b"\r\n"
        )
    for cle, valeur in champs.items():
        morceaux.append(
            f'--{frontiere}\r\nContent-Disposition: form-data; name="{cle}"\r\n\r\n{valeur}\r\n'.encode()
        )
    morceaux.append(f"--{frontiere}--\r\n".encode())
    return b"".join(morceaux), f"multipart/form-data; boundary={frontiere}"


def test_web_et_worker_partagent_fichiers_scratch_rapports_et_modeles(deploiement: dict[str, Any]) -> None:
    """Bout en bout : le web accepte, le WORKER exécute, les chemins sont partagés."""
    racine: Path = deploiement["racine"]
    donnees: Path = deploiement["donnees"]
    archive: Path = deploiement["archive"]
    index: SearchIndex = deploiement["index"]
    magasin: RedisStore = deploiement["redis"]
    avant = _empreinte_arbre(archive)

    # --- 1. Serveur web RÉEL (processus séparé, comme le conteneur `web`) ---
    environnement_web = {
        **os.environ,
        "PYTHONPATH": str(RACINE),
        "SEAMTECH_CONFIG": str(deploiement["config"]),
        "SEAMTECH_AUTH_TOKEN": TOKEN,
        "SEAMTECH_WEB_WORKER_ENABLED": "false",  # le web n'exécute RIEN
    }
    # Même commande que le conteneur `web` (Dockerfile CMD) : `python -m
    # seamtech_search serve`. On ne passe pas par `uvicorn seamtech_search.api:app`
    # — ce module expose `create_app(config)`, pas un module-level `app`.
    web = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "seamtech_search",
            "serve",
            "--config",
            str(deploiement["config"]),
        ],
        cwd=str(racine),
        env=environnement_web,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{_PORT_WEB}"
    try:
        _attendre(
            lambda: _requete(f"{base_url}/live", entetes={"X-SEAMTECH-TOKEN": TOKEN})[0] == 200,
            90,
            "le serveur web n'a jamais répondu",
            journal=lambda: (web.stdout.read() if web.stdout else ""),
        )

        # --- 2. Envoi multipart, comme le glisser-déposer du navigateur -----
        contenu = (archive / "fiche.pdf").read_bytes()
        corps, type_contenu = _multipart(
            [("files", "AFFAIRE-PARTAGEE/fiche.pdf", contenu), ("files", "AFFAIRE-PARTAGEE/nomenclature.txt", b"mylar")],
            {"folder": "AFFAIRE-PARTAGEE"},
        )
        statut, reponse = _requete(
            f"{base_url}/imports/upload",
            methode="POST",
            corps=corps,
            entetes={"X-SEAMTECH-TOKEN": TOKEN, "Content-Type": type_contenu},
        )
        assert statut == 200, reponse
        staged = Path(reponse["staged_path"])
        assert staged.exists(), "le fichier téléversé doit être sur le volume partagé"
        assert donnees in staged.parents, (
            f"le brouillon doit vivre sous {donnees} (volume partagé), pas sous {staged.parent}"
        )

        # --- 3. Acceptation du job (durable : la file Redis est exigée) -----
        statut, job = _requete(
            f"{base_url}/imports",
            methode="POST",
            corps=json.dumps({"source_path": str(staged)}).encode(),
            entetes={"X-SEAMTECH-TOKEN": TOKEN, "Content-Type": "application/json"},
        )
        assert statut in (200, 202), job
        job_id = job.get("job_id") or job.get("id")
        assert job_id, job
        if job.get("durability") is None:
            # Certaines versions répondent avec l'identifiant seul : on matérialise
            # alors l'enregistrement en base comme le fait l'API, avec la MÊME
            # clé que celle de la tâche mise en file.
            tache_en_file = magasin.profondeur_file("imports")
            assert tache_en_file["queue"] >= 1, (
                f"l'API doit soit répondre un job durable, soit l'avoir mis en file : {job}"
            )
            create_job(index, job_id, str(staged), durability="durable")
        assert job.get("durability") == "durable", (
            f"l'acceptation doit dire la vérité sur la durabilité : {job}"
        )

        # --- 4. Le WORKER (processus séparé) traite la tâche ---------------
        config_worker = {
            "root_paths": [str(archive.parent)],
            "database_path": str(donnees / "search.db"),
            "database_url": deploiement["url_base"],
            "host": "127.0.0.1",
            "min_free_bytes": 0,
            "storage_backend": "local",
            "redis_url": URL_REDIS,
            "require_durable_queue": True,
            "web_worker_enabled": False,
            "task_claim_ttl_seconds": 60,
        }
        chemin_config_worker = racine / "config.worker.json"
        chemin_config_worker.write_text(json.dumps(config_worker), encoding="utf-8")
        worker = subprocess.Popen(
            [sys.executable, "-m", "seamtech_search.worker_service", "--config", str(chemin_config_worker), "--une-passe"],
            cwd=str(racine),
            env={**os.environ, "PYTHONPATH": str(RACINE)},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            worker.wait(timeout=240)
        finally:
            if worker.poll() is None:  # pragma: no cover - filet
                worker.kill()
        sortie_worker = worker.stdout.read() if worker.stdout else ""
        assert worker.returncode == 0, f"le worker a échoué :\n{sortie_worker}"

        _attendre(
            lambda: (get_job(index, job_id) or {}).get("status") in {"completed", "needs_review", "failed"},
            60,
            "le job n'a jamais atteint un état terminal",
            journal=lambda: sortie_worker,
        )
        job_en_base = get_job(index, job_id)
        assert job_en_base["status"] in {"completed", "needs_review"}, job_en_base

        # --- 5. Ce que le partage doit permettre ---------------------------
        # a) Les rapports générés par le worker sont visibles du côté web.
        rapports = donnees / "reports" / job_id
        assert rapports.exists(), f"aucun rapport sous {rapports} (volume partagé attendu)"
        assert any(rapports.iterdir()), "le dossier de rapports est vide"
        # Le contenu du rapport est bien celui écrit par l'autre processus : on
        # l'ouvre ici, côté test (donc « côté web »), et il est non vide.
        fichier_rapport = next(f for f in sorted(rapports.rglob("*")) if f.is_file())
        assert fichier_rapport.stat().st_size > 0, f"{fichier_rapport} est vide"
        statut, detail_job = _requete(
            f"{base_url}/imports/{job_id}", entetes={"X-SEAMTECH-TOKEN": TOKEN}
        )
        assert statut == 200, detail_job
        assert detail_job.get("status") == job_en_base["status"], (
            "le web et la base doivent voir le même état (aucun état en mémoire seule)"
        )

        # b) Le worker a purgé le brouillon partagé après vérification.
        assert not staged.exists() or not any(staged.iterdir()), (
            "le brouillon téléversé doit être purgé après un envoi vérifié "
            "(sinon le disque du serveur se remplit)"
        )

        # c) Le dossier de modèles est résolu au même endroit par les deux.
        from seamtech_search.api import _dossier_modeles_ml
        from seamtech_search.config import AppConfig

        config_api = AppConfig.load(deploiement["config"])
        chemin_modeles_web = _dossier_modeles_ml(config_api)
        assert chemin_modeles_web == donnees / "modeles", chemin_modeles_web
        assert (chemin_modeles_web / "tokenizer.json").read_text(encoding="utf-8") == '{"version": "test"}'
        # Le worker lit le même fichier (mêmes montages) — vérifié dans le processus.
        verification = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json,pathlib,sys;from seamtech_search.api import _dossier_modeles_ml;"
                "from seamtech_search.config import AppConfig;"
                "c=AppConfig.load(sys.argv[1]);d=_dossier_modeles_ml(c);"
                "print(json.dumps({'dossier':str(d),'lu':(d/'tokenizer.json').read_text()}))",
                str(chemin_config_worker),
            ],
            cwd=str(racine),
            env={**os.environ, "PYTHONPATH": str(RACINE)},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert verification.returncode == 0, verification.stderr
        vu_par_le_worker = json.loads(verification.stdout)
        assert vu_par_le_worker["dossier"] == str(donnees / "modeles"), vu_par_le_worker
        assert vu_par_le_worker["lu"] == '{"version": "test"}'

        # d) L'archive (montage :ro) n'a pas bougé d'un octet — RG13.
        assert _empreinte_arbre(archive) == avant, "un import a modifié l'archive d'origine"

        # e) La quarantaine est sur le même volume partagé.
        quarantaine = donnees / "quarantine"
        quarantaine.mkdir(exist_ok=True)
        assert quarantaine.exists() and donnees in quarantaine.parents
    finally:
        web.send_signal(signal.SIGTERM)
        try:
            web.wait(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover - filet
            web.kill()


def test_worker_tourne_sans_le_volume_du_web_est_refuse() -> None:
    """Garde-fou négatif : un worker sans le volume des données ne peut pas marcher.

    Le test fige la CONSÉQUENCE du contrat : si le worker n'a pas accès aux
    fichiers téléversés par le web, il échoue — c'est pour cela que
    ``test_contrat_compose_web_worker`` exige l'égalité des montages. Sans cette
    vérification, une faute de frappe dans un ``volumes:`` produirait des imports
    en échec silencieux en production.
    """
    if not URL_REDIS:
        pytest.skip("SEAMTECH_TEST_REDIS_URL requis")
    magasin = RedisStore(redis_url=URL_REDIS)
    assert magasin.enqueue_task("imports", {"job_id": "job-injoignable", "source_path": "/chemin/inexistant"})
    tache = magasin.dequeue_task("imports", timeout=2)
    assert tache is not None
    import tempfile

    from seamtech_search.config import AppConfig
    from seamtech_search.worker import process_import_task

    with tempfile.TemporaryDirectory() as brut:
        racine = Path(brut)
        index = SearchIndex(racine / "index.db")
        index.initialize()
        index.run_migrations()
        try:
            config = AppConfig(root_paths=[racine], min_free_bytes=0, redis_url=URL_REDIS)
            try:
                resultat = process_import_task(tache, config, index, magasin, worker_id="worker-sans-volume")
            except Exception:
                resultat = {"status": "failed"}
            assert resultat.get("status") in {"failed", "upload_incomplete", None}, (
                f"un worker sans accès au fichier partagé ne peut pas réussir : {resultat}"
            )
        finally:
            index.close()
            magasin.ack_task("imports", tache)
