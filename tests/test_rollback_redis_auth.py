"""La consigne de rollback Redis, exécutée pour de vrai (revue Lot 0a, blocage 3).

Le document dit : « pour repartir propre, supprimez les deux namespaces de
session avec cette commande ». Cette commande doit donc :

1. **s'authentifier** — Compose lance Redis avec ``--requirepass`` ; sans
   authentification, ``redis-cli`` répond ``NOAUTH`` et ne supprime rien ;
2. **ne pas exposer le mot de passe en argument** — ``-a`` est visible dans
   ``ps`` et dans l'historique ; ``REDISCLI_AUTH`` dans l'environnement, non ;
3. **ne supprimer QUE** ``seamtech:session:*`` et ``seamtech:user_sessions:*``,
   en laissant intacte la file durable (queue/processing/retry/deadletter/
   claim/cancel), les jobs, la supervision des workers et les compteurs de débit.

Le test ne relit pas le document « en espérant » : il en extrait le script et
l'exécute avec un **vrai** ``redis-server`` protégé par mot de passe, un vrai
``redis-cli``, et un shim qui enregistre chaque argument reçu (pour prouver
qu'aucun ne contient le secret). Les clés sont réelles, la suppression est
réelle.

Marqueur ``session_redis`` : le test démarre son propre serveur, mais il rejoint
l'étape CI qui garantit la présence des binaires ``redis-server``/``redis-cli``.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.session_redis]

DOC = Path(__file__).resolve().parent.parent / "docs" / "DEPLOYMENT.md"

SESSIONS = ("seamtech:session:abc", "seamtech:user_sessions:1")
PROTEGEES = (
    "seamtech:queue:lot-42",
    "seamtech:processing:lot-43",
    "seamtech:retry:lot-44",
    "seamtech:deadletter:lot-45",
    "seamtech:claim:lot-46",
    "seamtech:cancel:lot-47",
    "seamtech:job:lot-48",
    "seamtech:heartbeat:worker-1",
    "seamtech:worker:worker-1",
    "seamtech:ratelimit:10.0.0.7",
)


def _binaire(nom: str) -> str:
    """Chemin d'un binaire Redis : PATH d'abord, sinon celui embarqué par redislite.

    La CI installe le paquet ``redis-server`` (donc ``/usr/bin/redis-server`` et
    ``/usr/bin/redis-cli``). En local, le dépôt utilise le binaire fourni par
    ``redislite``, qui n'est pas sur le PATH. Aucun des deux n'est un saut : si
    ni l'un ni l'autre n'existe, la preuve ne peut pas être faite et le test
    DOIT échouer.
    """
    trouve = shutil.which(nom)
    if trouve:
        return trouve
    try:
        import redislite  # noqa: PLC0415 - dépendance de développement optionnelle

        embarque = Path(redislite.__file__).resolve().parent / "bin" / nom
        if embarque.is_file() and os.access(embarque, os.X_OK):
            return str(embarque)
    except ImportError:
        pass
    pytest.fail(
        f"{nom} introuvable (PATH et redislite) : la preuve « la consigne s'authentifie » "
        "ne peut pas être exécutée. La CI installe le paquet redis-server avant cette étape."
    )


def _port_libre() -> int:
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        return int(prise.getsockname()[1])


@pytest.fixture()
def redis_protege(tmp_path: Path) -> tuple[int, str]:
    """Un ``redis-server`` RÉEL, avec ``--requirepass``, sur un port libre."""
    binaire = _binaire("redis-server")
    port = _port_libre()
    secret = "MotDePasse-Rollback-" + os.urandom(6).hex()
    processus = subprocess.Popen(  # noqa: S603 - binaire résolu, arguments fixes
        [
            binaire,
            "--port",
            str(port),
            "--requirepass",
            secret,
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(tmp_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    import redis

    client = redis.Redis(host="127.0.0.1", port=port, password=secret, socket_timeout=1.0)
    limite = time.time() + 20
    while time.time() < limite:
        try:
            if client.ping():
                break
        except Exception:  # noqa: BLE001 - le serveur démarre
            time.sleep(0.05)
    else:  # pragma: no cover - seulement si le serveur ne démarre pas
        processus.kill()
        pytest.fail("redis-server protégé par mot de passe n'a pas démarré")
    try:
        yield port, secret
    finally:
        client.close()
        processus.terminate()
        try:
            processus.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            processus.kill()


def _script_documente() -> str:
    """Le script ``sh -c`` du bloc de rollback, extrait tel quel du document."""
    texte = DOC.read_text(encoding="utf-8")
    debut = texte.find("**Rolling back Lot 0a.**")
    assert debut != -1
    fin = texte.find("\n## ", debut + 10)
    section = texte[debut : fin if fin != -1 else len(texte)]
    bloc = re.search(r"```bash\n(.*?)```", section, re.DOTALL)
    assert bloc is not None, "la consigne doit contenir un bloc ```bash"
    script = re.search(
        r"docker compose exec -T redis sh -c '\n(.*?)\n'", bloc.group(1), re.DOTALL
    )
    assert script is not None, "la consigne doit exécuter un script dans le conteneur redis"
    return script.group(1)


def _shim_redis_cli(tmp_path: Path, port: int, journal: Path) -> Path:
    """``redis-cli`` instrumenté : enregistre argv et l'état de l'authentification.

    Le shim n'écrit JAMAIS la valeur du secret dans son journal : seulement
    ``auth_set``/``auth_matches``. Il relaie ensuite vers le vrai binaire sur le
    port du serveur jetable.
    """
    reel = _binaire("redis-cli")
    dossier = tmp_path / "shim"
    dossier.mkdir()
    shim = dossier / "redis-cli"
    shim.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" >> "$SHIM_LOG"\n'
        'if [ -n "${REDISCLI_AUTH:-}" ]; then echo "auth_set=1" >> "$SHIM_LOG"; '
        'else echo "auth_set=0" >> "$SHIM_LOG"; fi\n'
        'if [ "${REDISCLI_AUTH:-}" = "${REDIS_PASSWORD:-}" ]; then echo "auth_matches=1" '
        '>> "$SHIM_LOG"; else echo "auth_matches=0" >> "$SHIM_LOG"; fi\n'
        f'exec "{reel}" -p {port} "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return dossier


def _executer(script: str, *, port: int, secret: str, tmp_path: Path, journal: Path) -> subprocess.CompletedProcess[str]:
    dossier = _shim_redis_cli(tmp_path, port, journal)
    environnement = {
        **os.environ,
        "PATH": f"{dossier}{os.pathsep}{os.environ.get('PATH', '')}",
        "REDIS_PASSWORD": secret,
        "SHIM_LOG": str(journal),
    }
    environnement.pop("REDISCLI_AUTH", None)
    return subprocess.run(  # noqa: S603 - script extrait du dépôt, shell explicite
        ["sh", "-c", script],
        env=environnement,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _semer(port: int, secret: str) -> None:
    import redis

    client = redis.Redis(host="127.0.0.1", port=port, password=secret, decode_responses=True)
    try:
        for cle in (*SESSIONS, *PROTEGEES):
            client.set(cle, "x")
    finally:
        client.close()


def _cles(port: int, secret: str) -> set[str]:
    import redis

    client = redis.Redis(host="127.0.0.1", port=port, password=secret, decode_responses=True)
    try:
        return {str(cle) for cle in client.scan_iter(count=100)}
    finally:
        client.close()


def test_la_consigne_sans_authentification_echoue_en_noauth(
    redis_protege: tuple[int, str], tmp_path: Path
) -> None:
    """La version d'AVANT (scan nu) ne supprime rien : Redis répond NOAUTH."""
    port, secret = redis_protege
    _semer(port, secret)
    # Reproduction de l'ancienne consigne : le scan, sans authentification.
    ancienne = (
        'redis-cli --scan --pattern "seamtech:session:*" | xargs -r redis-cli del\n'
        'redis-cli --scan --pattern "seamtech:user_sessions:*" | xargs -r redis-cli del'
    )
    resultat = _executer(
        ancienne, port=port, secret=secret, tmp_path=tmp_path, journal=tmp_path / "journal-vide.log"
    )
    assert "NOAUTH" in (resultat.stderr + resultat.stdout), (
        f"Redis protégé par mot de passe devait refuser le scan nu : {resultat.stderr!r}"
    )
    assert set(SESSIONS) <= _cles(port, secret), (
        "sans authentification, AUCUNE clé de session ne doit disparaître"
    )


def test_la_consigne_authentifiee_ne_supprime_que_les_sessions(
    redis_protege: tuple[int, str], tmp_path: Path
) -> None:
    """La consigne DU DÉPÔT : authentifiée, sans secret en argv, deux préfixes."""
    port, secret = redis_protege
    _semer(port, secret)
    journal = tmp_path / "journal.log"
    script = _script_documente()

    resultat = _executer(script, port=port, secret=secret, tmp_path=tmp_path, journal=journal)
    assert resultat.returncode == 0, f"la consigne doit réussir : {resultat.stderr!r}"
    assert "NOAUTH" not in (resultat.stderr + resultat.stdout), resultat.stderr

    restantes = _cles(port, secret)
    assert not (set(SESSIONS) & restantes), f"clés de session encore présentes : {set(SESSIONS) & restantes}"
    assert set(PROTEGEES) <= restantes, (
        "le rollback a supprimé des clés hors du Lot 0a "
        f"(file durable, jobs, workers, débit) : {set(PROTEGEES) - restantes}"
    )

    lignes = journal.read_text(encoding="utf-8").splitlines()
    marqueurs = [ligne for ligne in lignes if ligne.startswith("auth_")]
    arguments = [ligne for ligne in lignes if not ligne.startswith("auth_")]
    assert "auth_set=1" in marqueurs, "la consigne doit fournir REDISCLI_AUTH à redis-cli"
    assert "auth_matches=1" in marqueurs, "REDISCLI_AUTH doit valoir le mot de passe du serveur"
    assert not any(secret in argument for argument in arguments), (
        "le mot de passe ne doit JAMAIS apparaître dans un argument (visible dans ps)"
    )
    # Et les arguments observés sont bien ceux du scan et du del, pas d'un AUTH.
    assert any(argument == "--scan" for argument in arguments)
    assert any(argument == "del" for argument in arguments)
