"""Le banc navigateur ne laisse pas de base ou données temporaires après SIGTERM."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from tests.helpers_postgres_isole import url_base_jetable
from tests.helpers_redis_isole import binaire_redis

RACINE = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.postgres, pytest.mark.redis_queue]


def _bases_suivi(url: str) -> set[str]:
    import psycopg2

    with psycopg2.connect(url) as connexion, connexion.cursor() as curseur:
        curseur.execute("SELECT datname FROM pg_database WHERE datname LIKE 'e2e_suivi_%'")
        return {ligne[0] for ligne in curseur.fetchall()}


def test_arret_banc_suivi_nettoie_les_seules_ressources_creees() -> None:
    if os.name != "posix":
        pytest.skip("Arrêt du groupe de processus non qualifié sur Windows")
    url = os.environ.get("SEAMTECH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Serveur PostgreSQL privé requis")
    url_base_jetable(url, f"controle_suivi_{uuid.uuid4().hex}")
    if not binaire_redis():
        pytest.skip("Binaire Redis privé requis")

    tmp = Path(tempfile.gettempdir())
    info = tmp / f"seamtech-suivi-{uuid.uuid4().hex}.json"
    avant_bases = _bases_suivi(url)
    avant_redis = set(tmp.glob("seamtech-redis-test-*"))
    env = {**os.environ, "PYTHONPATH": str(RACINE), "SEAMTECH_E2E_SUIVI_INFO": str(info),
           "SEAMTECH_E2E_SUIVI_DATABASE_URL": url}
    processus = subprocess.Popen(
        [sys.executable, "frontend/e2e-import/serveur-suivi.py"], cwd=RACINE, env=env,
        start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    source: Path | None = None
    try:
        fin = time.monotonic() + 30
        while time.monotonic() < fin:
            if processus.poll() is not None:
                pytest.fail("Le banc s'est arrêté pendant son démarrage")
            if info.exists():
                try:
                    with urllib.request.urlopen("http://127.0.0.1:18135/live", timeout=1) as reponse:
                        if reponse.status == 200:
                            source = Path(json.loads(info.read_text(encoding="utf-8"))["source"])
                            break
                except (OSError, urllib.error.URLError):
                    pass
            time.sleep(0.1)
        else:
            pytest.fail("Démarrage du banc non confirmé")
        assert source is not None and source.is_dir()
        os.killpg(processus.pid, signal.SIGTERM)
        sortie, _ = processus.communicate(timeout=20)
        assert processus.returncode == 0, sortie[-2000:]
    finally:
        if processus.poll() is None:
            os.killpg(processus.pid, signal.SIGKILL)
            processus.wait(timeout=5)

    assert not info.exists()
    assert source is not None and not source.parent.exists()
    assert set(tmp.glob("seamtech-redis-test-*")) == avant_redis
    assert _bases_suivi(url) == avant_bases
