"""Compose réel : le web et le worker SÉPARÉS partagent fichiers et volumes.

C'est la preuve demandée en revue indépendante (point 3) : « un test Compose
complet qui prouve que le web et un worker distinct partagent les fichiers
téléversés, les chemins scratch/quarantaine et les fichiers de modèles requis,
avec les bonnes permissions ».

Le test pilote la VRAIE pile ``docker compose`` (aucun mock, aucun faux
processus) :

1. ``docker compose up -d --build web worker`` → deux conteneurs distincts,
   deux processus (l'API et ``python -m seamtech_search.worker_service``) ;
2. un dossier est téléversé par l'API ``web`` (multipart, comme le navigateur)
   puis confirmé en mode non bloquant → la tâche est remise à la file Redis ;
3. le WORKER (conteneur séparé) la récupère et la termine ;
4. le test vérifie alors, depuis l'hôte ET depuis l'intérieur des conteneurs :
   rapports écrits par le worker et lisibles par le web, brouillon partagé,
   quarantaine sur le même volume, fichier témoin écrit par le worker et lu par
   le web, exécution non-root, archive d'origine intacte, et téléchargement du
   rapport généré par HTTP.

Sans Docker, le test est ignoré (``skip``), il ne « passe » pas à vide.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
FICHE = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
URL_WEB = "http://127.0.0.1:8000"


def _docker_disponible() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "compose", "version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        return True
    except Exception:
        return False


DOCKER_OK = _docker_disponible()

pytestmark = [
    pytest.mark.integration_docker,
    pytest.mark.s3,
    pytest.mark.skipif(
        not DOCKER_OK,
        reason=(
            "Docker/Compose absents : la preuve d'intégration multi-conteneurs ne peut pas "
            "être exécutée ici (le job CI `integration` l'exécute, lui)"
        ),
    ),
]


# ---------------------------------------------------------------------------
# Outillage
# ---------------------------------------------------------------------------


def _env_compose() -> dict[str, str]:
    """Mêmes variables que le job CI `integration` (défauts identiques)."""
    return {
        **os.environ,
        "POSTGRES_PASSWORD": os.environ.get("POSTGRES_PASSWORD", "test_password"),
        "MINIO_ROOT_USER": os.environ.get("MINIO_ROOT_USER", "minioadmin"),
        "MINIO_ROOT_PASSWORD": os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin123"),
        "REDIS_PASSWORD": os.environ.get("REDIS_PASSWORD", "redis_test_password"),
        "SEAMTECH_AUTH_TOKEN": os.environ.get("SEAMTECH_AUTH_TOKEN", "test-token-123"),
        "SEAMTECH_UI_PASSWORD": os.environ.get("SEAMTECH_UI_PASSWORD", "ci-ui-password"),
        "SEAMTECH_SESSION_SECRET": os.environ.get(
            "SEAMTECH_SESSION_SECRET", "ci-session-secret-not-for-production"
        ),
        "SEAMTECH_S3_BUCKET": os.environ.get("SEAMTECH_S3_BUCKET", "seamtech-documents"),
    }


def _compose(*arguments: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *arguments],
        cwd=str(RACINE),
        env=_env_compose(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _exec(service: str, *commande: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return _compose("exec", "-T", service, *commande, timeout=timeout)


def _requete(
    chemin: str,
    *,
    methode: str = "GET",
    corps: bytes | None = None,
    entetes: dict[str, str] | None = None,
    url_base: str = URL_WEB,
) -> tuple[int, bytes, dict[str, str]]:
    entetes_complets = {"X-SEAMTECH-TOKEN": _env_compose()["SEAMTECH_AUTH_TOKEN"], **(entetes or {})}
    demande = urllib.request.Request(
        f"{url_base}{chemin}", data=corps, method=methode, headers=entetes_complets
    )
    try:
        with urllib.request.urlopen(demande, timeout=30) as reponse:
            return reponse.status, reponse.read(), dict(reponse.headers)
    except urllib.error.HTTPError as erreur:
        return erreur.code, erreur.read(), dict(erreur.headers)


def _attendre(condition, delai: float, message: str):  # noqa: ANN001, ANN201
    limite = time.time() + delai
    derniere = None
    while time.time() < limite:
        try:
            valeur = condition()
        except Exception as erreur:  # noqa: BLE001 - on réessaie, on veut le motif final
            derniere = erreur
            valeur = None
        if valeur:
            return valeur
        time.sleep(2)
    raise AssertionError(f"{message} (dernière erreur : {derniere})")


def _multipart(fichiers: list[tuple[str, bytes]], champs: dict[str, str]) -> tuple[bytes, str]:
    frontiere = f"----seamtech{uuid.uuid4().hex}"
    morceaux: list[bytes] = []
    for chemin_relatif, contenu in fichiers:
        morceaux.append(
            f'--{frontiere}\r\nContent-Disposition: form-data; name="files"; '
            f'filename="{chemin_relatif}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            + contenu
            + b"\r\n"
        )
    for cle, valeur in champs.items():
        morceaux.append(f'--{frontiere}\r\nContent-Disposition: form-data; name="{cle}"\r\n\r\n{valeur}\r\n'.encode())
    morceaux.append(f"--{frontiere}--\r\n".encode())
    return b"".join(morceaux), f"multipart/form-data; boundary={frontiere}"


def _resoudre(service: str, chemin: str) -> Path:
    """Résout un chemin DANS le conteneur (l'API peut renvoyer un chemin relatif).

    Le faire résoudre par le conteneur prouve au passage que le montage est bien
    celui attendu : un chemin qui n'existerait pas des deux côtés ne résout pas
    au même endroit.
    """
    resultat = _exec("worker" if service == "worker" else service, "python", "-c",
                     "import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())", chemin)
    assert resultat.returncode == 0, f"résolution impossible de {chemin!r} : {resultat.stderr}"
    return Path(resultat.stdout.strip())


def _racine_donnees(service: str) -> Path:
    """``<data>`` vu par le conteneur : dérivé de la configuration réelle."""
    resultat = _exec(
        service,
        "python",
        "-c",
        (
            "from seamtech_search.config import AppConfig;"
            "import pathlib;"
            "c=AppConfig.load('/app/config/config.json');"
            "print(pathlib.Path(c.database_path).resolve().parent)"
        ),
    )
    assert resultat.returncode == 0, resultat.stderr
    return Path(resultat.stdout.strip())


def _sha256_arbre(racine: Path) -> dict[str, str]:
    resultat: dict[str, str] = {}
    for chemin in sorted(racine.rglob("*")):
        if chemin.is_file():
            resultat[str(chemin.relative_to(racine))] = hashlib.sha256(chemin.read_bytes()).hexdigest()
    return resultat


def _etat_conteneur(service: str) -> dict[str, str]:
    identifiant = _compose("ps", "-q", service).stdout.strip()
    if not identifiant:
        return {"service": service, "state": "absent"}
    inspect = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}|"
            "{{.Config.User}}",
            identifiant,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    etat, sante, utilisateur = (inspect.stdout.strip().split("|") + ["", "", ""])[:3]
    return {"service": service, "state": etat, "health": sante, "user": utilisateur}


# ---------------------------------------------------------------------------
# Le test
# ---------------------------------------------------------------------------


def test_web_et_worker_partagent_volumes_et_fichiers(tmp_path: Path) -> None:
    """Le worker séparé traite ce que le web a accepté, sur les MÊMES chemins."""
    archive = RACINE / "sample_data"
    avant = _sha256_arbre(archive)
    donnees_hote = RACINE / "data"

    # --- 1. La pile documentée, web ET worker séparés --------------------
    resultat = _compose("up", "-d", "--build", "web", "worker")
    assert resultat.returncode == 0, (
        f"`docker compose up -d --build web worker` a échoué :\n{resultat.stdout}\n{resultat.stderr}"
    )

    etat_worker = _attendre(
        lambda: (
            etat
            if (etat := _etat_conteneur("worker")).get("state") == "running"
            and etat.get("health") in {"healthy", "no-healthcheck"}
            else None
        ),
        300,
        "le conteneur worker n'est jamais devenu opérationnel",
    )
    # Le worker tourne en NON-root : une image qui repasse en root doit casser ici.
    utilisateur_worker = _exec("worker", "id", "-u").stdout.strip()
    assert utilisateur_worker not in {"", "0"}, (
        f"le worker tourne en root (uid={utilisateur_worker!r}) — refusé (priorité sécurité)"
    )
    assert etat_worker["user"] in {"seamtech", ""} or etat_worker["user"] != "root"

    # Le worker répond sur sa sonde : file, jobs par statut, workers vivants.
    sonde = _exec("worker", "python", "-m", "seamtech_search.worker_service", "--verifier")
    assert sonde.returncode == 0, f"sonde worker en échec :\n{sonde.stdout}\n{sonde.stderr}"

    _attendre(
        lambda: _requete("/live")[0] == 200,
        300,
        "l'API web n'a jamais répondu /live",
    )

    # --- 2. Téléversement par le WEB (comme le navigateur) ---------------
    dossier = tmp_path / "AFFAIRE-PARTAGE-CI"
    dossier.mkdir()
    (dossier / "fiche.pdf").write_bytes(FICHE.read_bytes())
    (dossier / "nomenclature.txt").write_text("voile 100% mylar\n", encoding="utf-8")
    corps, type_contenu = _multipart(
        [
            ("AFFAIRE-PARTAGE-CI/fiche.pdf", (dossier / "fiche.pdf").read_bytes()),
            ("AFFAIRE-PARTAGE-CI/nomenclature.txt", (dossier / "nomenclature.txt").read_bytes()),
        ],
        {"folder": "AFFAIRE-PARTAGE-CI"},
    )
    statut, brut, _ = _requete(
        "/imports/upload", methode="POST", corps=corps, entetes={"Content-Type": type_contenu}
    )
    assert statut == 200, brut.decode("utf-8", "replace")
    scan = json.loads(brut)
    donnees_conteneur = _racine_donnees("worker")
    assert donnees_conteneur == Path("/app/data"), (
        f"le volume de données n'est pas monté sur /app/data dans le worker : {donnees_conteneur}"
    )
    staged_conteneur = _resoudre("worker", scan["staged_path"])
    assert donnees_conteneur in staged_conteneur.parents, (
        f"le brouillon téléversé doit vivre sous {donnees_conteneur} : {staged_conteneur}"
    )

    # Le brouillon écrit par le WEB est visible depuis le WORKER (volume partagé)
    # et le worker peut le LIRE — permissions comprises.
    lecture_worker = _exec(
        "worker",
        "python",
        "-c",
        (
            "import pathlib,sys,os;"
            "p=pathlib.Path(sys.argv[1]);"
            "fichiers=sorted(str(x.relative_to(p)) for x in p.rglob('*') if x.is_file());"
            "print(os.access(p, os.R_OK|os.X_OK), fichiers)"
        ),
        str(staged_conteneur),
    )
    assert lecture_worker.returncode == 0, (
        f"le worker ne voit pas le brouillon téléversé par le web "
        f"(volumes non partagés ?) :\n{lecture_worker.stdout}\n{lecture_worker.stderr}"
    )
    assert lecture_worker.stdout.startswith("True"), lecture_worker.stdout
    assert "fiche.pdf" in lecture_worker.stdout, lecture_worker.stdout

    # --- 3. Acceptation DURABLE (non bloquante) --------------------------
    corps = json.dumps(
        {
            "source_path": str(staged_conteneur),
            "technical_pdf": str(staged_conteneur / "fiche.pdf"),
        }
    ).encode()
    statut, brut, _ = _requete(
        "/imports/confirm?wait=false",
        methode="POST",
        corps=corps,
        entetes={"Content-Type": "application/json"},
    )
    assert statut in (200, 202), brut.decode("utf-8", "replace")
    acceptation = json.loads(brut)
    job_id = acceptation.get("job_id") or acceptation.get("id")
    assert job_id, acceptation
    assert acceptation.get("durability") == "durable", (
        f"l'acceptation doit être durable (Redis persistant), reçu : {acceptation}"
    )

    # --- 4. Le WORKER (conteneur séparé) termine la tâche ---------------
    def _statut_job():  # noqa: ANN202
        code, charge, _ = _requete(f"/imports/{job_id}")
        if code != 200:
            return None
        return json.loads(charge)

    job = _attendre(
        lambda: (
            contenu
            if (contenu := _statut_job()) and contenu.get("status") in {"completed", "needs_review", "failed", "upload_incomplete"}
            else None
        ),
        600,
        "le job n'est jamais devenu terminal (le worker ne consomme-t-il pas la file ?)",
    )
    assert job["status"] in {"completed", "needs_review"}, job

    # --- 5. Ce que le partage doit garantir ------------------------------
    # a) Les rapports écrits par le WORKER existent sur le volume partagé…
    rapports_hote = donnees_hote / "reports" / job_id  # ./data est monté sur /app/data
    rapports_conteneur = donnees_conteneur / "reports" / job_id
    assert rapports_conteneur.name == job_id and str(rapports_conteneur).startswith("/app/data/")
    assert rapports_hote.exists(), f"aucun rapport pour {job_id} sous {rapports_hote}"
    fichiers_rapport = [f for f in sorted(rapports_hote.rglob("*")) if f.is_file()]
    assert fichiers_rapport, f"dossier de rapports vide : {rapports_hote}"
    assert all(f.stat().st_size > 0 for f in fichiers_rapport), "rapport vide"
    # …et le processus WEB les voit au même endroit.
    vu_par_web = _exec(
        "web",
        "python",
        "-c",
        "import pathlib,sys; p=pathlib.Path(sys.argv[1]); print(pathlib.Path(sys.argv[1]).exists(), sorted(x.name for x in p.iterdir()))",
        f"/app/data/reports/{job_id}",
    )
    assert vu_par_web.returncode == 0 and vu_par_web.stdout.startswith("True"), (
        f"le web ne voit pas les rapports du worker :\n{vu_par_web.stdout}\n{vu_par_web.stderr}"
    )

    # b) Le rapport est téléchargeable par HTTP (donc depuis un autre poste
    #    de l'atelier) — pas seulement présent sur le disque du serveur.
    code, contenu, entetes = _requete(f"/imports/{job_id}/artifacts/report_pdf")
    assert code == 200, f"téléchargement du rapport impossible (HTTP {code}) : {contenu[:400]!r}"
    assert contenu[:4] == b"%PDF", "le rapport servi n'est pas un PDF"
    assert "attachment" in entetes.get("Content-Disposition", ""), entetes

    # c) Le fichier témoin écrit par le WORKER est lisible par le WEB : preuve
    #    directe du volume partagé, dans les deux sens.
    temoin = "/app/data/.temoin-partage-ci"
    ecriture = _exec("worker", "python", "-c", f"open({temoin!r}, 'w').write('ok')")
    assert ecriture.returncode == 0, ecriture.stderr
    lecture = _exec("web", "python", "-c", f"print(open({temoin!r}).read())")
    assert lecture.stdout.strip() == "ok", (
        f"le web ne lit pas un fichier écrit par le worker : {lecture.stdout} {lecture.stderr}"
    )

    # d) La quarantaine est sur le même volume partagé (récupérable après un
    #    envoi objet incomplet), et jamais dans l'archive.
    quarantaine = _exec(
        "worker",
        "python",
        "-c",
        (
            "from seamtech_search.config import AppConfig;"
            "from seamtech_search.import_pipeline import quarantine_root, staging_root;"
            "c=AppConfig.load('/app/config/config.json');"
            "print(quarantine_root(c).resolve(), staging_root(c).resolve())"
        ),
    )
    assert quarantaine.returncode == 0, quarantaine.stderr
    racine_quarantaine, racine_brouillons = [Path(p) for p in quarantaine.stdout.strip().split()]
    assert racine_quarantaine == donnees_conteneur / "quarantine", quarantaine.stdout
    assert racine_brouillons == staged_conteneur.parent, (
        f"le brouillon doit vivre sous {racine_brouillons}, vu {staged_conteneur.parent}"
    )
    assert racine_brouillons.name == "uploads", quarantaine.stdout
    # Les deux vivent sur le volume partagé, donc récupérables par l'opérateur.
    assert donnees_conteneur in racine_quarantaine.parents
    assert donnees_conteneur in racine_brouillons.parents

    # e) Le dossier de modèles ML est résolu au même endroit par les deux
    #    processus, et le worker peut y lire (installation hors ligne).
    modeles = _exec(
        "worker",
        "python",
        "-c",
        (
            "from seamtech_search.config import AppConfig;"
            "from seamtech_search.api import _dossier_modeles_ml;"
            "import os; d=_dossier_modeles_ml(AppConfig.load('/app/config/config.json'));"
            "print(d, os.access(d, os.R_OK|os.X_OK))"
        ),
    )
    assert modeles.returncode == 0, modeles.stderr
    chemin_modeles = Path(modeles.stdout.strip().split()[0])
    assert chemin_modeles == donnees_conteneur / "modeles", modeles.stdout
    # Le worker crée le dossier des modèles et y dépose un témoin : c'est le
    # geste d'une installation hors ligne des poids. Le web doit le lire.
    depot = _exec(
        "worker",
        "python",
        "-c",
        (
            "import pathlib,sys;"
            "d=pathlib.Path(sys.argv[1]); d.mkdir(parents=True, exist_ok=True);"
            "(d/'tokenizer.json').write_text('{\"version\": \"ci\"}');"
            "print('ok')"
        ),
        str(chemin_modeles),
    )
    assert depot.returncode == 0 and depot.stdout.strip() == "ok", depot.stderr
    modeles_web = _exec(
        "web",
        "python",
        "-c",
        (
            "from seamtech_search.config import AppConfig;"
            "from seamtech_search.api import _dossier_modeles_ml;"
            "import os; d=_dossier_modeles_ml(AppConfig.load('/app/config/config.json'));"
            "print(d, os.access(d, os.R_OK|os.X_OK), (d/'tokenizer.json').read_text())"
        ),
    )
    assert modeles_web.returncode == 0, modeles_web.stderr
    assert modeles_web.stdout.strip().startswith(f"{chemin_modeles} True"), (
        f"le web ne lit pas les modèles écrits par le worker : {modeles_web.stdout} {modeles_web.stderr}"
    )
    assert '"version": "ci"' in modeles_web.stdout, modeles_web.stdout

    # f) L'archive montée en lecture seule n'a pas bougé d'un octet.
    assert _sha256_arbre(archive) == avant, "l'import a modifié l'archive d'origine (RG13)"

    # g) Le brouillon téléversé est purgé APRÈS vérification de l'envoi objet
    #    (ici MinIO est vivant) — sinon le serveur se remplit indéfiniment.
    restant = _exec("worker", "python", "-c", f"import pathlib; p=pathlib.Path({str(staged_conteneur)!r}); print(p.exists() and any(p.iterdir()))")
    assert restant.stdout.strip() in {"False", "None"}, (
        f"le brouillon n'a pas été purgé après un envoi vérifié : {restant.stdout}"
    )
