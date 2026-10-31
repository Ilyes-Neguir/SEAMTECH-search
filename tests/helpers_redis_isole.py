"""Instance Redis de test privée, jamais une base d'un serveur existant."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import redis

from seamtech_search.redis_store import RedisStore


def binaire_redis() -> str | None:
    explicite = os.environ.get("SEAMTECH_TEST_REDIS_SERVER")
    if explicite:
        return explicite if Path(explicite).is_file() else None
    disponible = shutil.which("redis-server")
    if disponible:
        return disponible
    # Conserve le repli précédemment utilisé par le test de quarantaine.
    spec = importlib.util.find_spec("redislite")
    if spec and spec.origin:
        embarque = Path(spec.origin).parent / "bin" / "redis-server"
        if embarque.is_file() and os.access(embarque, os.X_OK):
            return str(embarque)
    return None


def attendre_redis(url: str, processus: subprocess.Popen, delai: float = 15.0) -> bool:
    limite = time.monotonic() + delai
    store = RedisStore(redis_url=url)
    while time.monotonic() < limite:
        if processus.poll() is not None:
            return False
        if store.ping():
            return True
        time.sleep(0.1)
    return False


@contextmanager
def serveur_redis_isole(*, version_minimale: int | None = None) -> Iterator[str]:
    """Lance un serveur privé sur socket Unix, détruit seulement ses fichiers."""
    binaire = binaire_redis()
    if not binaire:
        raise RuntimeError("redis-server introuvable ; définir SEAMTECH_TEST_REDIS_SERVER")
    if os.name == "nt":
        raise RuntimeError("Isolation Redis par socket Unix non qualifiée sous Windows")
    with tempfile.TemporaryDirectory(prefix="seamtech-redis-test-") as repertoire:
        socket = str(Path(repertoire) / "redis.sock")
        url = f"unix://{socket}?db=0"
        processus = subprocess.Popen(
            [binaire, "--port", "0", "--unixsocket", socket, "--unixsocketperm", "700",
             "--save", "", "--appendonly", "no"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            if not attendre_redis(url, processus):
                raise RuntimeError("Redis dédié du test n'a pas démarré")
            if version_minimale is not None:
                client = redis.Redis.from_url(url)
                try:
                    version = str(client.info("server").get("redis_version", ""))
                    majeur = int(version.split(".", 1)[0])
                except (ValueError, TypeError, redis.RedisError) as erreur:
                    raise RuntimeError("Version du Redis de test non vérifiable") from erreur
                finally:
                    client.close()
                if majeur < version_minimale:
                    raise RuntimeError(f"Redis {version_minimale} ou supérieur requis pour cette qualification")
            yield url
        finally:
            if processus.poll() is None:
                processus.terminate()
                try:
                    processus.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    processus.kill()
                    processus.wait(timeout=5)
