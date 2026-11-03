"""Banc E2E isolé : vraie API, Redis privé, base jetable, aucun worker initial.

Les deux routes __suivi_test__ ne sont présentes que dans ce processus d'essai.
La source et les identifiants sont synthétiques. Aucune ressource existante n'est
vidée : Redis est privé et toute base PostgreSQL est créée pour ce seul run.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import uvicorn
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from seamtech_search.api import create_app
from seamtech_search.comptes.comptes import creer_utilisateur
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.redis_store import RedisStore, queue_keys
from tests.helpers_postgres_isole import url_base_jetable
from tests.helpers_redis_isole import serveur_redis_isole

JETON = "e2e-suivi-jeton-service"
RACINE_DEPOT = Path(__file__).resolve().parents[2]


@contextmanager
def base_postgres_jetable() -> Iterator[str]:
    admin_url = os.environ.get("SEAMTECH_E2E_SUIVI_DATABASE_URL")
    if not admin_url:
        raise RuntimeError("Une URL PostgreSQL de test isolée est requise pour ce banc")

    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom = f"e2e_suivi_{uuid.uuid4().hex}"
    url = url_base_jetable(admin_url, nom)
    connexion = psycopg2.connect(admin_url)
    connexion.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with connexion.cursor() as curseur:
            curseur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom)))
    finally:
        connexion.close()
    try:
        yield url
    finally:
        connexion = psycopg2.connect(admin_url)
        connexion.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with connexion.cursor() as curseur:
                curseur.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(nom)))
        finally:
            connexion.close()


def ecrire_prive_exclusif(chemin: Path, contenu: str) -> os.stat_result:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(chemin, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fichier:
        fichier.write(contenu)
        fichier.flush()
        return os.fstat(fichier.fileno())


def main() -> None:
    # Uvicorn restitue le SIGTERM après l'arrêt ASGI ; le comportement par
    # défaut termine immédiatement le processus sans exécuter nos finally.
    # Le convertir en SystemExit permet de fermer la base et les répertoires.
    def terminer_proprement(_signal: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminer_proprement)
    info = Path(os.environ["SEAMTECH_E2E_SUIVI_INFO"])
    if info.parent != Path(tempfile.gettempdir()) or not re.fullmatch(r"seamtech-suivi-[a-f0-9]{32}\.json", info.name):
        raise ValueError("Chemin de manifeste du banc invalide")

    with tempfile.TemporaryDirectory(prefix="seamtech-banc-suivi-") as temporaire, \
            serveur_redis_isole(version_minimale=7) as redis_url, base_postgres_jetable() as database_url:
        racine = Path(temporaire)
        source = racine / "source"
        source.mkdir()
        fichier = source / "piece-synthetique.txt"
        fichier.write_bytes(b"Source synthetique, a conserver.\n")
        config = AppConfig(
            root_paths=[source], database_path=racine / "data" / "search.db", database_url=database_url,
            host="0.0.0.0", port=18135, behind_tls_proxy=True, allow_network_access=True, auth_token=JETON,
            redis_url=redis_url, require_durable_queue=True, web_worker_enabled=False,
            min_free_bytes=0, rate_limit_per_minute=1000,
        )
        config_file = racine / "config-worker.json"
        ecrire_prive_exclusif(config_file, config.model_dump_json())
        app = create_app(config)
        index = SearchIndex(config.database_path, database_url)
        try:
            creer_utilisateur(index, "e2e-utilisateur-suivi", "Utilisateur synthétique",
                              "e2e-suivi-mot-de-passe", role="utilisateur", doit_changer_mot_de_passe=False)
        finally:
            index.close()
        observations = {"ouvertes": 0, "deconnectees": 0, "bloquer_auth": False, "panne_auth": False}

        @app.middleware("http")
        async def bloquer_suivi_pour_test(request: Request, call_next):
            if request.method == "GET" and request.url.path == "/auth/session" and observations["panne_auth"]:
                return JSONResponse({"detail": "Panne synthétique"}, status_code=503)
            if request.method == "GET" and (request.url.path == "/imports/suivi-bloque" or
                    (request.url.path == "/auth/session" and observations["bloquer_auth"])):
                observations["ouvertes"] += 1
                try:
                    for _ in range(250):
                        if await request.is_disconnected():
                            observations["deconnectees"] += 1
                            return JSONResponse({"detail": "Client déconnecté"}, status_code=499)
                        await asyncio.sleep(0.1)
                    return JSONResponse({"detail": "Délai du banc dépassé"}, status_code=503)
                finally:
                    observations["ouvertes"] -= 1
            return await call_next(request)

        def autoriser(request: Request) -> None:
            if request.headers.get("X-Test-Token") != JETON:
                raise HTTPException(status_code=403)

        @app.get("/__suivi_test__/etat")
        async def etat_test(request: Request):
            autoriser(request)
            return observations.copy()

        @app.post("/__suivi_test__/auth-bloquee")
        async def regler_auth_test(request: Request):
            autoriser(request)
            charge = await request.json()
            observations["bloquer_auth"] = charge.get("bloquer") is True
            observations["panne_auth"] = charge.get("panne") is True
            return {"bloquer": observations["bloquer_auth"], "panne": observations["panne_auth"]}

        @app.post("/__suivi_test__/worker")
        async def executer_worker_test(request: Request):
            autoriser(request)
            magasin = RedisStore(config=config)
            client = magasin._get_client()
            if client is None or not client.ping():
                raise HTTPException(status_code=503, detail="Redis de test indisponible")
            cles = queue_keys("imports")

            def profondeur() -> dict[str, int]:
                return {"queue": int(client.llen(cles["queue"])),
                        "processing": int(client.llen(cles["processing"])),
                        "retry": int(client.zcard(cles["retry"])),
                        "deadletter": int(client.llen(cles["deadletter"]))}

            avant = profondeur()
            resultat = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, str(RACINE_DEPOT / "frontend/e2e-import/worker-une-fois.py"), str(config_file)],
                cwd=RACINE_DEPOT, capture_output=True, text=True, timeout=35, check=False,
            )
            if resultat.returncode:
                raise HTTPException(status_code=500, detail="Worker de test en échec")
            return {"avant": avant, "apres": profondeur(), "processus_separe": True}

        empreinte = hashlib.sha256(fichier.read_bytes()).hexdigest()
        info_stat = ecrire_prive_exclusif(info, json.dumps({
            "source": str(source), "fichier": str(fichier), "sha256": empreinte,
            "base": "postgresql" if database_url else "sqlite",
        }))
        try:
            uvicorn.run(app, host="0.0.0.0", port=18135, log_level="warning")
        finally:
            try:
                actuelle = info.lstat()
                if (actuelle.st_dev, actuelle.st_ino) == (info_stat.st_dev, info_stat.st_ino):
                    info.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
