"""Création idempotente : concurrence, pannes, annulation et entrée non fiable."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from seamtech_search.fiches import televersement_lot as t


def _config(tmp_path: Path):
    return SimpleNamespace(database_path=tmp_path / "data" / "search.db", max_file_size_bytes=1024, min_free_bytes=0)


def _creer(config, cle, **changements):
    args = dict(nom_racine="DOSSIER-SYNTHETIQUE", nombre_fichiers=1, taille_totale_octets=3)
    args.update(changements)
    return t.creer_session_televersement_lot(config, cle_idempotence=cle, **args)


def test_reponse_perdue_et_concurrence_renvoient_la_meme_session(tmp_path):
    config = _config(tmp_path)
    cle = "ab" * 32
    with ThreadPoolExecutor(max_workers=8) as pool:
        resultats = list(pool.map(lambda _: _creer(config, cle), range(16)))
    assert len({s["token"] for s in resultats}) == 1
    assert len(list((tmp_path / "data" / "uploads" / "lots-televerses").glob("*/session.json"))) == 1


def test_meme_cle_autre_demande_est_refusee(tmp_path):
    config = _config(tmp_path)
    cle = "cd" * 32
    session = _creer(config, cle)
    with pytest.raises(t.TeleversementLotConflit):
        _creer(config, cle, taille_totale_octets=4)
    assert _creer(config, cle)["token"] == session["token"]


@pytest.mark.parametrize("cle", ["", "abc", "a" * 63, "z" * 64, "A" * 64, 123])
def test_cle_invalide_est_refusee(tmp_path, cle):
    with pytest.raises(t.TeleversementLotErreur):
        _creer(_config(tmp_path), cle)


def test_apres_annulation_une_cle_ne_ressuscite_pas_la_session(tmp_path):
    config = _config(tmp_path)
    cle = "ef" * 32
    token = _creer(config, cle)["token"]
    t.annuler_session_televersement_lot(config, token)
    with pytest.raises(t.TeleversementLotConflit):
        _creer(config, cle)
    assert not (tmp_path / "data" / "uploads" / "lots-televerses" / token).exists()


@pytest.mark.parametrize("sauver_dabord", [False, True])
def test_reprise_apres_echec_pendant_creation(tmp_path, monkeypatch, sauver_dabord):
    config = _config(tmp_path)
    cle = "01" * 32
    sauver = t._sauver_manifeste
    def panne(dossier, manifeste):
        if sauver_dabord:
            sauver(dossier, manifeste)
        raise OSError("interruption synthétique")
    with monkeypatch.context() as contexte:
        contexte.setattr(t, "_sauver_manifeste", panne)
        with pytest.raises(OSError):
            _creer(config, cle)
    resultat = _creer(config, cle)
    assert _creer(config, cle)["token"] == resultat["token"]
    assert len(list((tmp_path / "data" / "uploads" / "lots-televerses").glob("*/session.json"))) == 1


def _creation_processus(tmp_path, cle, file):
    try:
        file.put(_creer(_config(Path(tmp_path)), cle)["token"])
    except Exception as erreur:
        file.put(type(erreur).__name__)


def _creation_tuee(tmp_path, cle, apres_sauvegarde):
    import os
    import signal

    sauver = t._sauver_manifeste
    def tuer(dossier, manifeste):
        if apres_sauvegarde:
            sauver(dossier, manifeste)
        os.kill(os.getpid(), signal.SIGKILL)
    t._sauver_manifeste = tuer
    _creer(_config(Path(tmp_path)), cle)


def test_reservation_concurrente_interprocessus(tmp_path):
    import multiprocessing

    contexte = multiprocessing.get_context("spawn")
    file = contexte.Queue()
    processus = [contexte.Process(target=_creation_processus, args=(str(tmp_path), "34" * 32, file)) for _ in range(4)]
    for p in processus:
        p.start()
    resultats = [file.get(timeout=30) for _ in processus]
    for p in processus:
        p.join(timeout=30)
        assert p.exitcode == 0
    assert len(set(resultats)) == 1
    assert len(resultats[0]) >= 20


@pytest.mark.skipif(not hasattr(__import__("signal"), "SIGKILL"), reason="SIGKILL indisponible")
@pytest.mark.parametrize("apres_sauvegarde", [False, True])
def test_reprise_apres_sigkill_du_createur(tmp_path, apres_sauvegarde):
    import multiprocessing
    import signal

    contexte = multiprocessing.get_context("spawn")
    processus = contexte.Process(target=_creation_tuee, args=(str(tmp_path), "56" * 32, apres_sauvegarde))
    processus.start()
    processus.join(timeout=30)
    assert processus.exitcode == -signal.SIGKILL
    token = _creer(_config(tmp_path), "56" * 32)["token"]
    assert _creer(_config(tmp_path), "56" * 32)["token"] == token
    assert len(list((tmp_path / "data" / "uploads" / "lots-televerses").glob("*/session.json"))) == 1


def test_registre_illisible_refuse_la_reprise_sans_nouvelle_session(tmp_path):
    config = _config(tmp_path)
    token = _creer(config, "78" * 32)["token"]
    racine = tmp_path / "data" / "uploads" / "lots-televerses"
    (racine / ".creations.sqlite").write_bytes(b"registre volontairement invalide")
    with pytest.raises(t.TeleversementLotConflit):
        _creer(config, "78" * 32)
    assert len(list(racine.glob("*/session.json"))) == 1
    assert (racine / token / "session.json").is_file()


@pytest.mark.skipif(__import__("os").name == "nt", reason="permissions POSIX")
def test_registre_est_cree_sans_acces_aux_autres_utilisateurs(tmp_path):
    config = _config(tmp_path)
    _creer(config, "9a" * 32)
    fichier = tmp_path / "data" / "uploads" / "lots-televerses" / ".creations.sqlite"
    assert fichier.stat().st_mode & 0o077 == 0


@pytest.mark.skipif(__import__("os").name == "nt", reason="permissions POSIX")
def test_registre_devenu_lisible_par_tous_interdit_toute_reprise(tmp_path):
    config = _config(tmp_path)
    _creer(config, "bc" * 32)
    fichier = tmp_path / "data" / "uploads" / "lots-televerses" / ".creations.sqlite"
    fichier.chmod(0o644)
    with pytest.raises(t.TeleversementLotConflit):
        _creer(config, "bc" * 32)


def test_registre_plein_refuse_nouvelles_cles_mais_permet_rejeu(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(t, "MAX_RESERVATIONS_CREATION", 1)
    initiale = _creer(config, "de" * 32)
    with pytest.raises(t.TeleversementLotEspaceInsuffisant, match="[Rr]egistre"):
        _creer(config, "f0" * 32)
    assert _creer(config, "de" * 32)["token"] == initiale["token"]
