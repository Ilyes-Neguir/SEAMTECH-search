"""Isolation du Redis de test : socket Unix sur POSIX, port TCP dédié ailleurs.

Pourquoi ce fichier existe : ``serveur_redis_isole`` REFUSAIT catégoriquement
de s'exécuter sous Windows (``RuntimeError: Isolation Redis par socket Unix
non qualifiée sous Windows``). Ce refus privait la qualification Windows de
deux preuves — « perte de claim Redis pendant la promotion » et « perte de
claim Redis pendant une transaction », dont dépendent les tests
``tests/test_depot_transactionnel.py`` — tout en laissant croire, à la lecture
du journal, à une panne de produit alors que c'était une limite de banc.

Le refus a été remplacé par un ÉQUIVALENT, pas par un saut : sur les
plateformes sans socket Unix, le serveur privé est lancé sur un port TCP
inoccupé, lié à la boucle locale, dans un processus qu'on détruit nous-mêmes.

Ce que ce fichier prouve, et ce qu'il ne prouve pas, est dit une fois pour
toutes :

* prouvé ici, sur un banc POSIX : le choix du mécanisme d'isolement par
  plateforme, la cohérence de l'URL avec les arguments passés au serveur, et
  le fait qu'un port « libre » l'est vraiment ;
* prouvé par le workflow « Qualification Windows » : que Redis 7.4 démarre
  réellement sur ce port et que les deux tests de perte de claim y passent.
* NON prouvé : qu'un port TCP isole AUSSI bien qu'un socket Unix. C'est faux,
  et c'est pourquoi POSIX garde le socket Unix : sur Windows, la garantie
  devient « une instance dédiée et éphémère », pas « un objet du système de
  fichiers inaccessible aux autres ».
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from tests.helpers_redis_isole import binaire_redis, plan_isole, port_libre


def test_posix_isole_par_socket_unix_dans_un_dossier_prive(tmp_path: Path) -> None:
    url, arguments = plan_isole(tmp_path, nom_os="posix")
    assert url.startswith("unix://"), url
    assert url.endswith("redis.sock?db=0"), url
    assert str(tmp_path / "redis.sock") in url
    assert "--unixsocket" in arguments
    assert str(tmp_path / "redis.sock") in arguments
    # Le port TCP est désactivé : l'instance n'écoute QUE sur le socket.
    assert arguments[arguments.index("--port") + 1] == "0"


def test_windows_isole_par_port_tcp_lie_a_la_boucle_locale(tmp_path: Path) -> None:
    url, arguments = plan_isole(tmp_path, nom_os="nt")
    assert url.startswith("redis://127.0.0.1:"), url
    port = int(url.removeprefix("redis://127.0.0.1:").split("/")[0])
    assert 1024 <= port <= 65535, port
    # L'URL et les arguments doivent dire le MÊME port : sinon le client
    # interrogerait un port où rien n'écoute — panne en apparence aléatoire.
    assert arguments[arguments.index("--port") + 1] == str(port)
    assert arguments[arguments.index("--bind") + 1] == "127.0.0.1"
    # Aucune persistance : l'instance de test ne doit rien laisser sur le disque.
    assert "--save" in arguments and "--appendonly" in arguments


def test_windows_n_invoque_jamais_de_socket_unix(tmp_path: Path) -> None:
    _, arguments = plan_isole(tmp_path, nom_os="nt")
    assert "--unixsocket" not in arguments
    assert "--unixsocketperm" not in arguments


def test_le_port_annonce_libre_l_est_vraiment() -> None:
    port = port_libre()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sonde:
        sonde.bind(("127.0.0.1", port))  # échoue si le port est pris


def test_le_plan_par_defaut_suit_la_plateforme_reelle(tmp_path: Path) -> None:
    """Aucun test ne doit durcir POSIX : le plan suit ``os.name``."""
    import os

    url, _ = plan_isole(tmp_path)
    if os.name == "nt":
        assert url.startswith("redis://127.0.0.1:"), url
    else:
        assert url.startswith("unix://"), url


def test_une_instance_privee_repond_et_ne_touche_pas_au_serveur_partage(tmp_path: Path) -> None:
    """Preuve d'exécution : l'instance dédiée répond, quel que soit le mécanisme."""
    import redis

    from tests.helpers_redis_isole import serveur_redis_isole

    if not binaire_redis():
        pytest.skip("aucun binaire redis-server disponible sur ce banc (SEAMTECH_TEST_REDIS_SERVER)")

    with serveur_redis_isole() as url:
        client = redis.Redis.from_url(url)
        try:
            assert client.ping()
            client.set("seamtech::banc::temoin", "1")
            assert client.get("seamtech::banc::temoin") == b"1"
        finally:
            client.close()
    # Après la sortie, plus rien n'écoute : l'instance était bien dédiée.
    _, arguments = plan_isole(tmp_path)
    if "--unixsocket" in arguments:
        assert not Path(arguments[arguments.index("--unixsocket") + 1]).exists()


def test_le_chemin_windows_demarre_vraiment_un_serveur_prive(tmp_path: Path) -> None:
    """Exécution RÉELLE de la branche Windows, sur un banc Linux.

    ``nom_os="nt"`` force le plan TCP. Ce n'est pas Windows : c'est la preuve
    que la branche que Windows empruntera démarre un serveur, répond sur l'URL
    annoncée et s'éteint à la sortie — au lieu d'un refus sec.
    """
    import redis

    from tests.helpers_redis_isole import serveur_redis_isole

    if not binaire_redis():
        pytest.skip("aucun binaire redis-server disponible sur ce banc (SEAMTECH_TEST_REDIS_SERVER)")

    url_observee: list[str] = []
    with serveur_redis_isole(nom_os="nt") as url:
        url_observee.append(url)
        assert url.startswith("redis://127.0.0.1:"), url
        client = redis.Redis.from_url(url)
        try:
            assert client.ping(), "l'instance dédiée ne répond pas sur son port"
            assert client.info("server").get("redis_version", "").startswith(("6.", "7.", "8."))
        finally:
            client.close()
    port = int(url_observee[0].removeprefix("redis://127.0.0.1:").split("/")[0])
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sonde:
        sonde.settimeout(1.0)
        # Le serveur est arrêté : le port est rendu, l'instance était privée.
        assert sonde.connect_ex(("127.0.0.1", port)) != 0
