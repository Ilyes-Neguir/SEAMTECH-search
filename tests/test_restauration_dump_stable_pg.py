"""Démonstration sur PostgreSQL jetable : pg_restore reçoit le dump vérifié."""
from __future__ import annotations

import hashlib
import os
import subprocess
from contextlib import closing

import psycopg2
import pytest

from seamtech_search import sauvegarde
from tests.conftest import _creer_base_jetable, _supprimer_base_jetable

pytestmark = [pytest.mark.postgres, pytest.mark.sauvegarde]


def test_restaure_dump_verifie_si_source_substituee(tmp_path, monkeypatch):
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("serveur PG de test dédié absent")
    nom_source, url_source = _creer_base_jetable()
    nom_cible = None
    try:
        with closing(psycopg2.connect(url_source)) as connexion:
            with connexion:
                with connexion.cursor() as curseur:
                    curseur.execute("CREATE TABLE preuve (valeur text NOT NULL)")
                    curseur.execute("INSERT INTO preuve VALUES ('synthetique-original')")
        dump = tmp_path / "original.dump"
        subprocess.run([sauvegarde._trouver_binaire("pg_dump"), "-Fc", "-f", str(dump), url_source], check=True)
        original = dump.read_bytes()
        manifeste = {"dump": {"sha256": hashlib.sha256(original).hexdigest(), "octets": len(original)}}
        nom_cible, url_cible = _creer_base_jetable()
        _supprimer_base_jetable(nom_cible)
        connecter = sauvegarde._connecter
        def substituer(*args, **kwargs):
            dump.write_bytes(b"substitution-synthetique")
            return connecter(*args, **kwargs)
        monkeypatch.setattr(sauvegarde, "_connecter", substituer)
        monkeypatch.setattr(sauvegarde, "_etat_base", lambda *_: {"tables": {}, "fiches_par_statut": {}})
        monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(tmp_path))
        sauvegarde.restaurer(url_cible, manifeste, dump)
        with closing(psycopg2.connect(url_cible)) as connexion:
            with connexion.cursor() as curseur:
                curseur.execute("SELECT valeur FROM preuve")
                assert curseur.fetchall() == [("synthetique-original",)]
        assert dump.read_bytes() == b"substitution-synthetique"
        assert not list(tmp_path.glob("seamtech-restore-copy-*"))
    finally:
        if nom_cible:
            _supprimer_base_jetable(nom_cible)
        _supprimer_base_jetable(nom_source)


def test_cli_fige_fichiers_avant_connexion_pg_reelle(tmp_path, monkeypatch):
    import json

    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("serveur PG de test dédié absent")
    nom_source, url_source = _creer_base_jetable()
    nom_cible = None
    try:
        with closing(psycopg2.connect(url_source)) as connexion:
            with connexion:
                with connexion.cursor() as curseur:
                    curseur.execute("CREATE TABLE preuve (valeur text NOT NULL)")
                    curseur.execute("INSERT INTO preuve VALUES ('synthetique-original')")
        dump = tmp_path / "original.dump"
        subprocess.run([sauvegarde._trouver_binaire("pg_dump"), "-Fc", "-f", str(dump), url_source], check=True)
        source = tmp_path / "piece"
        source.write_bytes(b"original")
        manifeste = {
            "dump": {"fichier": dump.name, "sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
                     "octets": dump.stat().st_size},
            "fichiers_locaux": {"copies": [{"id": 1, "path_relatif": "piece",
                                            "sha256": hashlib.sha256(b"original").hexdigest()}]},
        }
        chemin = tmp_path / "manifest.json"
        chemin.write_text(json.dumps(manifeste))
        nom_cible, url_cible = _creer_base_jetable()
        _supprimer_base_jetable(nom_cible)
        connecter = sauvegarde._connecter
        def substituer(*args, **kwargs):
            source.write_bytes(b"substitution-synthetique")
            return connecter(*args, **kwargs)
        monkeypatch.setattr(sauvegarde, "_connecter", substituer)
        monkeypatch.setattr(sauvegarde, "_client_s3_depuis_env", lambda: None)
        monkeypatch.setattr(sauvegarde, "_etat_base", lambda *_: {"tables": {}, "fiches_par_statut": {}})
        monkeypatch.setenv("SEAMTECH_SAUVEGARDE_TMP", str(tmp_path))
        assert sauvegarde.main(["restaurer", "--manifeste", str(chemin), "--base-cible", url_cible,
                                "--fichiers-cible", str(tmp_path / "out")]) == 0
        assert (tmp_path / "out" / "1" / "piece").read_bytes() == b"original"
        with closing(psycopg2.connect(url_cible)) as connexion:
            with connexion.cursor() as curseur:
                curseur.execute("SELECT valeur FROM preuve")
                assert curseur.fetchall() == [("synthetique-original",)]
        assert source.read_bytes() == b"substitution-synthetique"
        assert not list(tmp_path.glob("seamtech-restore-copy-*"))
    finally:
        if nom_cible:
            _supprimer_base_jetable(nom_cible)
        _supprimer_base_jetable(nom_source)


def test_absence_pg_restore_nouvre_pas_de_base_pg_reelle(tmp_path, monkeypatch):
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("serveur PG de test dédié absent")
    nom_cible, url_cible = _creer_base_jetable()
    _supprimer_base_jetable(nom_cible)
    dump = tmp_path / "dump"
    dump.write_bytes(b"archive-synthetique")
    manifeste = {"dump": {"sha256": hashlib.sha256(dump.read_bytes()).hexdigest(), "octets": dump.stat().st_size}}
    try:
        monkeypatch.setattr(sauvegarde.shutil, "which", lambda _nom: None)
        monkeypatch.setattr(sauvegarde.glob, "glob", lambda _motif: [])
        monkeypatch.setattr(sauvegarde, "_repertoire_binaires_pgserver", lambda: None)
        monkeypatch.delenv("SEAMTECH_PG_BINDIR", raising=False)
        with pytest.raises(sauvegarde.SauvegardeError, match="introuvable"):
            sauvegarde.restaurer(url_cible, manifeste, dump)
        with closing(psycopg2.connect(os.environ["SEAMTECH_TEST_DATABASE_URL"])) as connexion:
            with connexion.cursor() as curseur:
                curseur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nom_cible,))
                assert curseur.fetchone() is None
    finally:
        _supprimer_base_jetable(nom_cible)


def test_taille_dump_incoherente_ne_cree_pas_de_base_pg_reelle(tmp_path):
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("serveur PG de test dédié absent")
    nom_cible, url_cible = _creer_base_jetable()
    _supprimer_base_jetable(nom_cible)
    dump = tmp_path / "dump"
    dump.write_bytes(b"archive-synthetique")
    manifeste = {"dump": {"sha256": hashlib.sha256(dump.read_bytes()).hexdigest(), "octets": 999}}
    try:
        with pytest.raises(sauvegarde.SauvegardeError, match="taille"):
            sauvegarde.restaurer(url_cible, manifeste, dump)
        with closing(psycopg2.connect(os.environ["SEAMTECH_TEST_DATABASE_URL"])) as connexion:
            with connexion.cursor() as curseur:
                curseur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nom_cible,))
                assert curseur.fetchone() is None
    finally:
        _supprimer_base_jetable(nom_cible)


def test_archive_incompatible_ne_cree_pas_de_base_pg_reelle(tmp_path):
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("serveur PG de test dédié absent")
    nom_cible, url_cible = _creer_base_jetable()
    _supprimer_base_jetable(nom_cible)
    dump = tmp_path / "dump"
    dump.write_bytes(b"archive-postgresql-incompatible-synthetique")
    manifeste = {"dump": {"sha256": hashlib.sha256(dump.read_bytes()).hexdigest(), "octets": dump.stat().st_size}}
    try:
        with pytest.raises(sauvegarde.SauvegardeError, match="archive"):
            sauvegarde.restaurer(url_cible, manifeste, dump)
        with closing(psycopg2.connect(os.environ["SEAMTECH_TEST_DATABASE_URL"])) as connexion:
            with connexion.cursor() as curseur:
                curseur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nom_cible,))
                assert curseur.fetchone() is None
    finally:
        _supprimer_base_jetable(nom_cible)


def test_version_archive_trop_recente_ne_cree_pas_de_base_pg_reelle(tmp_path):
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("serveur PG de test dédié absent")
    nom_source, url_source = _creer_base_jetable()
    nom_cible = None
    try:
        nom_cible, url_cible = _creer_base_jetable()
        _supprimer_base_jetable(nom_cible)
        dump = tmp_path / "dump"
        subprocess.run([sauvegarde._trouver_binaire("pg_dump"), "-Fc", "-f", str(dump), url_source], check=True)
        donnees = bytearray(dump.read_bytes())
        assert donnees[:5] == b"PGDMP"
        donnees[5] = 127  # version majeure d'archive volontairement non prise en charge
        dump.write_bytes(donnees)
        manifeste = {"dump": {"sha256": hashlib.sha256(donnees).hexdigest(), "octets": len(donnees)}}
        with pytest.raises(sauvegarde.SauvegardeError, match="archive"):
            sauvegarde.restaurer(url_cible, manifeste, dump)
        with closing(psycopg2.connect(os.environ["SEAMTECH_TEST_DATABASE_URL"])) as connexion:
            with connexion.cursor() as curseur:
                curseur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nom_cible,))
                assert curseur.fetchone() is None
    finally:
        if nom_cible:
            _supprimer_base_jetable(nom_cible)
        _supprimer_base_jetable(nom_source)


def test_archive_tronquee_ne_laisse_pas_de_base_pg_partielle(tmp_path):
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("serveur PG de test dédié absent")
    nom_source, url_source = _creer_base_jetable()
    nom_cible = None
    try:
        with closing(psycopg2.connect(url_source)) as connexion:
            with connexion:
                with connexion.cursor() as curseur:
                    curseur.execute("CREATE TABLE preuve (valeur text NOT NULL)")
                    curseur.execute("INSERT INTO preuve SELECT md5(g::text) FROM generate_series(1,10000) g")
        nom_cible, url_cible = _creer_base_jetable()
        _supprimer_base_jetable(nom_cible)
        dump = tmp_path / "dump"
        subprocess.run([sauvegarde._trouver_binaire("pg_dump"), "-Fc", "-f", str(dump), url_source], check=True)
        donnees = dump.read_bytes()[:-100]
        dump.write_bytes(donnees)
        # La liste seule ne détecte pas cette troncature des données.
        subprocess.run([sauvegarde._trouver_binaire("pg_restore"), "--list", str(dump)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        manifeste = {"dump": {"sha256": hashlib.sha256(donnees).hexdigest(), "octets": len(donnees)}}
        with pytest.raises(sauvegarde.SauvegardeError, match="pg_restore"):
            sauvegarde.restaurer(url_cible, manifeste, dump)
        with closing(psycopg2.connect(os.environ["SEAMTECH_TEST_DATABASE_URL"])) as connexion:
            with connexion.cursor() as curseur:
                curseur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nom_cible,))
                assert curseur.fetchone() is None
    finally:
        if nom_cible:
            _supprimer_base_jetable(nom_cible)
        _supprimer_base_jetable(nom_source)


def test_dump_par_repertoire_lien_ne_cree_pas_de_base_pg_reelle(tmp_path):
    if not os.environ.get("SEAMTECH_TEST_DATABASE_URL"):
        pytest.skip("serveur PG de test dédié absent")
    nom_cible, url_cible = _creer_base_jetable()
    _supprimer_base_jetable(nom_cible)
    externe = tmp_path / "externe"
    externe.mkdir()
    donnees = b"dump-synthetique"
    (externe / "dump").write_bytes(donnees)
    lien = tmp_path / "bundle"
    lien.symlink_to(externe, target_is_directory=True)
    manifeste = {"dump": {"sha256": hashlib.sha256(donnees).hexdigest(), "octets": len(donnees)}}
    try:
        with pytest.raises(sauvegarde.SauvegardeError):
            sauvegarde.restaurer(url_cible, manifeste, lien / "dump")
        with closing(psycopg2.connect(os.environ["SEAMTECH_TEST_DATABASE_URL"])) as connexion:
            with connexion.cursor() as curseur:
                curseur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nom_cible,))
                assert curseur.fetchone() is None
    finally:
        _supprimer_base_jetable(nom_cible)
