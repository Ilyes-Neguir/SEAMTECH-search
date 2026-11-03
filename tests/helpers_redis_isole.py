"""Instance Redis de test privée, jamais une base d'un serveur existant."""
from __future__ import annotations

import contextlib
import importlib.util
import os
import shutil
import socket
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


def port_libre() -> int:
    """Un port TCP inoccupé, réservé le temps de le rendre."""
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sonde:
        sonde.bind(("127.0.0.1", 0))
        return int(sonde.getsockname()[1])


def plan_isole(repertoire: Path | None, *, nom_os: str | None = None) -> tuple[str, list[str]]:
    """``(url, arguments)`` d'un serveur privé, selon la plateforme.

    POSIX : socket Unix dans un dossier temporaire — l'isolement est
    STRUCTUREL, aucun port n'est partagé avec quoi que ce soit.

    Ailleurs (Windows) : il n'existe pas de socket Unix. On lance alors le
    serveur sur un port TCP local inoccupé, lié à la boucle locale, dans un
    processus qui nous appartient et qui est détruit à la sortie. Ce n'est
    pas l'isolement par le système de fichiers du socket Unix — concédé
    franchement — mais c'est le même degré de garantie du point de vue du
    test : une instance dédiée, éphémère, qu'on détruit nous-mêmes, au lieu
    d'un refus sec qui privait Windows de toute preuve.
    """
    nom = os.name if nom_os is None else nom_os
    if nom != "nt" and repertoire is not None:
        chemin = str(Path(repertoire) / "redis.sock")
        return (
            f"unix://{chemin}?db=0",
            ["--port", "0", "--unixsocket", chemin, "--unixsocketperm", "700",
             "--save", "", "--appendonly", "no"],
        )
    port = port_libre()
    return (
        f"redis://127.0.0.1:{port}/0",
        ["--port", str(port), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no"],
    )


@contextmanager
def serveur_redis_isole(
    *, version_minimale: int | None = None, nom_os: str | None = None
) -> Iterator[str]:
    """Lance un serveur privé — socket Unix sur POSIX, port TCP dédié ailleurs.

    ``nom_os`` n'existe que pour les tests : il permet d'exercer le chemin
    Windows SUR un banc Linux, plutôt que de le découvrir au premier run
    Windows. Forcer un nom d'OS n'est pas Windows — c'est la seule façon
    honnête d'exécuter cette branche avant d'avoir un poste Windows.
    """
    binaire = binaire_redis()
    if not binaire:
        raise RuntimeError("redis-server introuvable ; définir SEAMTECH_TEST_REDIS_SERVER")
    with tempfile.TemporaryDirectory(prefix="seamtech-redis-test-") as repertoire:
        url, arguments = plan_isole(Path(repertoire), nom_os=nom_os)
        processus = subprocess.Popen(
            [binaire, *arguments],
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
