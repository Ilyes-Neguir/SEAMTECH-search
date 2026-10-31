"""Le seed navigateur ne doit jamais détruire les bases d'autres utilisateurs."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def seed():
    chemin = Path(__file__).resolve().parents[1] / "frontend" / "e2e" / "seed-live-pg.py"
    spec = importlib.util.spec_from_file_location("seed_e2e_synthetique", chemin)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_seed_ne_termine_pas_les_connexions_et_ne_supprime_aucune_autre_base(seed, tmp_path, monkeypatch):
    sql_executes = []
    class FinSonde(Exception):
        pass
    class Curseur:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def execute(self, requete, *args):
            sql_executes.append(str(requete))
            assert "pg_terminate_backend" not in str(requete)
            assert "DROP DATABASE" not in str(requete).upper()
            if str(requete).startswith("CREATE DATABASE"):
                raise FinSonde
        def fetchall(self): return [("e2e_une_base_tierce",)]
    class Connexion:
        def set_isolation_level(self, *_): pass
        def cursor(self): return Curseur()
        def close(self): pass
    monkeypatch.setenv("SEAMTECH_E2E_DATABASE_URL", "postgresql://test-inutilise/postgres")
    monkeypatch.setenv("SEAMTECH_E2E_RUN_ID", "test-synthetique")
    monkeypatch.setattr(seed, "FICHIER_RUN", tmp_path / "run.json")
    monkeypatch.setattr(seed, "VERROU", tmp_path / "lock")
    monkeypatch.setattr(seed.psycopg2, "connect", lambda *_args, **_kwargs: Connexion())
    with pytest.raises(FinSonde):
        seed.main()
    assert sum(requete.startswith("CREATE DATABASE") for requete in sql_executes) == 1


def test_erreur_de_connexion_seed_ne_divulgue_ni_url_ni_message_sensible(seed, monkeypatch, capsys):
    def echec(*_args, **_kwargs):
        raise RuntimeError("secret-synthetique en erreur")
    monkeypatch.setattr(seed.psycopg2, "connect", echec)
    assert not seed.base_vivante("postgresql://operateur:secret-synthetique@db-test/base")
    sortie = capsys.readouterr().err
    assert "secret-synthetique" not in sortie
    assert "operateur" not in sortie
    assert "RuntimeError" in sortie


def test_reutilisation_necrit_pas_les_credentials_sur_disque_ou_stdout(seed, tmp_path, monkeypatch, capsys):
    import json
    from uuid import uuid4

    nom = "e2e_" + uuid4().hex
    chemin = tmp_path / "run.json"
    chemin.write_text(json.dumps({"run_id": "run-synthetique", "nom": nom}), encoding="utf-8")
    class Connexion:
        def set_isolation_level(self, *_): pass
        def close(self): pass
    monkeypatch.setenv("SEAMTECH_E2E_DATABASE_URL", "postgresql://operateur:secret-synthetique@db-test/postgres")
    monkeypatch.setenv("SEAMTECH_E2E_RUN_ID", "run-synthetique")
    monkeypatch.setattr(seed, "FICHIER_RUN", chemin)
    monkeypatch.setattr(seed, "VERROU", tmp_path / "lock")
    monkeypatch.setattr(seed.psycopg2, "connect", lambda *_args, **_kwargs: Connexion())
    urls = []
    monkeypatch.setattr(seed, "base_vivante", lambda url: urls.append(url) or True)
    seed.main()
    sortie = capsys.readouterr()
    assert "secret-synthetique" not in sortie.out + sortie.err
    assert "secret-synthetique" not in chemin.read_text(encoding="utf-8")
    assert urls == [f"postgresql://operateur:secret-synthetique@db-test/{nom}"]
    assert json.loads(sortie.out)["nom"] == nom


@pytest.mark.postgres
def test_seed_pg_preserve_base_et_connexion_temoins(seed, tmp_path, monkeypatch, capsys):
    import json
    import os
    from types import SimpleNamespace
    from uuid import uuid4

    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin_url = os.environ.get("SEAMTECH_TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("PostgreSQL de test non configuré")
    identifiant = uuid4()
    nom_cree = "e2e_" + identifiant.hex
    temoin = "e2e_" + uuid4().hex
    admin = psycopg2.connect(admin_url)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    temoin_cree = False
    seed_termine = False
    connexion_temoin = None
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(temoin)))
        temoin_cree = True
        connexion_temoin = psycopg2.connect(seed.url_base(admin_url, temoin))
        monkeypatch.setenv("SEAMTECH_E2E_DATABASE_URL", admin_url)
        monkeypatch.setenv("SEAMTECH_E2E_RUN_ID", "run-synthetique")
        monkeypatch.setattr(seed, "FICHIER_RUN", tmp_path / "run.json")
        monkeypatch.setattr(seed, "VERROU", tmp_path / "lock")
        monkeypatch.setattr(seed, "uuid", SimpleNamespace(uuid4=lambda: identifiant))
        seed.main()
        seed_termine = True
        with connexion_temoin.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone() == (1,)
        sortie = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert sortie == {"nom": nom_cree}
        assert json.loads(seed.FICHIER_RUN.read_text(encoding="utf-8")) == {
            "run_id": "run-synthetique", "nom": nom_cree,
        }
    finally:
        if connexion_temoin is not None:
            connexion_temoin.close()
        with admin.cursor() as cur:
            if seed_termine:
                cur.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(nom_cree)))
            if temoin_cree:
                cur.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(temoin)))
        admin.close()


def test_url_seed_conserve_options_et_refuse_nom_arbitraire(seed):
    nom = "e2e_" + "a" * 32
    assert seed.url_base("postgresql://u:p@serveur/postgres?sslmode=require", nom) == (
        f"postgresql://u:p@serveur/{nom}?sslmode=require"
    )
    with pytest.raises(ValueError, match="Nom de base"):
        seed.url_base("postgresql://u:p@serveur/postgres", 'e2e_";DROP DATABASE autre;--')
    with pytest.raises(ValueError, match="URL PostgreSQL"):
        seed.url_base("postgresql:user=postgres", nom)


def test_workflow_hors_ligne_consomme_seulement_le_nom_du_seed():
    chemin = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
    workflow = chemin.read_text(encoding="utf-8")
    etape = workflow.split("- name: Base d'essai + jeu de données", 1)[1].split(
        "- name: Configuration de la pile", 1
    )[0]
    assert 'splitlines()[-1])["nom"]' in etape
    assert 'splitlines()[-1])["url"]' not in etape
    assert 'print("base d\'essai :", url)' not in etape


def test_verrou_seed_refuse_lien_sans_modifier_la_cible(seed, tmp_path, monkeypatch):
    cible = tmp_path / "tiers"
    cible.write_text("contenu-intact", encoding="utf-8")
    lien = tmp_path / "lock"
    lien.symlink_to(cible)
    monkeypatch.setattr(seed, "VERROU", lien)
    with pytest.raises(OSError):
        with seed.verrou_exclusif():
            pass
    assert cible.read_text(encoding="utf-8") == "contenu-intact"


def test_ecriture_run_ne_suit_pas_un_lien_et_ne_contient_pas_secret(seed, tmp_path, monkeypatch):
    import json

    cible = tmp_path / "tiers"
    cible.write_text("contenu-intact", encoding="utf-8")
    lien = tmp_path / "run.json"
    lien.symlink_to(cible)
    monkeypatch.setattr(seed, "FICHIER_RUN", lien)
    seed.ecrire_run("run-test", "e2e_" + "a" * 32)
    assert cible.read_text(encoding="utf-8") == "contenu-intact"
    assert not lien.is_symlink()
    assert json.loads(lien.read_text(encoding="utf-8")) == {"run_id": "run-test", "nom": "e2e_" + "a" * 32}


def test_lecture_run_refuse_lien_et_fichier_non_ordinaire(seed, tmp_path, monkeypatch):
    cible = tmp_path / "tiers"
    cible.write_text('{"run_id":"donnee-privee","nom":"x"}', encoding="utf-8")
    lien = tmp_path / "run.json"
    lien.symlink_to(cible)
    monkeypatch.setattr(seed, "FICHIER_RUN", lien)
    with pytest.raises(OSError):
        seed.lire_run()
    lien.unlink()
    import os
    os.mkfifo(lien)
    with pytest.raises(RuntimeError, match="ordinaire"):
        seed.lire_run()


def test_base_vivante_ferme_connexion_apres_echec_requete(seed, monkeypatch):
    class Connexion:
        ferme = False
        def cursor(self):
            raise RuntimeError("panne synthétique")
        def close(self):
            self.ferme = True

    connexion = Connexion()
    monkeypatch.setattr(seed.psycopg2, "connect", lambda *_args, **_kwargs: connexion)
    assert not seed.base_vivante("postgresql://exemple/base")
    assert connexion.ferme


def test_lecture_run_refuse_fichier_excessif(seed, tmp_path, monkeypatch):
    fichier = tmp_path / "run.json"
    fichier.write_bytes(b" " * 5000)
    monkeypatch.setattr(seed, "FICHIER_RUN", fichier)
    with pytest.raises(RuntimeError, match="volumineux"):
        seed.lire_run()


def test_run_id_non_valide_refuse_avant_toute_connexion(seed, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SEAMTECH_E2E_DATABASE_URL", "postgresql://test/base")
    monkeypatch.setenv("SEAMTECH_E2E_RUN_ID", "run\nsecret-synthetique")
    monkeypatch.setattr(seed, "VERROU", tmp_path / "lock")
    def interdiction(*_args, **_kwargs):
        raise AssertionError("ne pas se connecter")
    monkeypatch.setattr(seed.psycopg2, "connect", interdiction)
    with pytest.raises(SystemExit):
        seed.main()
    sortie = capsys.readouterr()
    assert "secret-synthetique" not in sortie.out + sortie.err


def test_echec_connexion_admin_seed_ne_divulgue_pas_secret(seed, monkeypatch, tmp_path):
    monkeypatch.setenv("SEAMTECH_E2E_DATABASE_URL", "postgresql://u:secret-synthetique@db/postgres")
    monkeypatch.setattr(seed, "VERROU", tmp_path / "lock")
    def echec(*_args, **_kwargs):
        raise RuntimeError("secret-synthetique")
    monkeypatch.setattr(seed.psycopg2, "connect", echec)
    with pytest.raises(SystemExit) as erreur:
        seed.main()
    assert "secret-synthetique" not in str(erreur.value)
    assert "RuntimeError" in str(erreur.value)


def test_url_seed_refuse_option_query_qui_detourne_la_base(seed):
    nom = "e2e_" + "a" * 32
    with pytest.raises(ValueError, match="base"):
        seed.url_base("postgresql://u:p@serveur/postgres?dbname=production", nom)


def test_seed_refuse_dbname_query_avant_connexion(seed, tmp_path, monkeypatch):
    monkeypatch.setenv("SEAMTECH_E2E_DATABASE_URL", "postgresql://u:p@serveur/postgres?dbname=production")
    monkeypatch.setattr(seed, "VERROU", tmp_path / "lock")
    def interdiction(*_args, **_kwargs):
        raise AssertionError("connexion interdite")
    monkeypatch.setattr(seed.psycopg2, "connect", interdiction)
    with pytest.raises(SystemExit, match="URL"):
        seed.main()


def test_seed_url_socket_unix_est_conservee(seed):
    from psycopg2.extensions import parse_dsn

    nom = "e2e_" + "a" * 32
    cible = parse_dsn(seed.url_base("postgresql:///postgres?host=/tmp&port=55432", nom))
    assert cible["dbname"] == nom
    assert cible["host"] == "/tmp"
    assert cible["port"] == "55432"
