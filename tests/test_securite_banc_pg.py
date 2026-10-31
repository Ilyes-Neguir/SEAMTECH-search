"""Garde-fous contre l'utilisation accidentelle de l'URL PG d'administration."""
from __future__ import annotations

import json

import pytest


def test_config_relais_ne_contient_pas_la_base_administration(tmp_path, monkeypatch):
    from tests import test_relais_file_base as relais

    secret = "secret-synthetique-test"
    monkeypatch.setattr(relais, "URL_PG", f"postgresql://compte:{secret}@hote/base-sensible")
    monkeypatch.setattr(relais, "URL_REDIS", "unix:///tmp/redis-fictif.sock")
    fichier = relais._config_worker(tmp_path)
    contenu = fichier.read_text(encoding="utf-8")
    assert secret not in contenu
    assert "base-sensible" not in contenu
    assert "database_url" not in json.loads(contenu)


def test_fichier_prive_ne_suit_pas_de_lien_et_n_ecrase_pas(tmp_path):
    import os
    import stat

    import pytest

    from scripts.fichier_prive import ecrire_fichier_prive

    cible = tmp_path / "secret.json"
    ecrire_fichier_prive(cible, '{"jeton":"synthetique"}')
    assert cible.read_text(encoding="utf-8") == '{"jeton":"synthetique"}'
    if os.name != "nt":
        assert stat.S_IMODE(cible.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        ecrire_fichier_prive(cible, "ne-pas-ecraser")
    tiers = tmp_path / "tiers"
    tiers.write_text("intact", encoding="utf-8")
    lien = tmp_path / "lien"
    lien.symlink_to(tiers)
    with pytest.raises(FileExistsError):
        ecrire_fichier_prive(lien, "ne-pas-suivre")
    assert tiers.read_text(encoding="utf-8") == "intact"


def test_etape_configuration_hors_ligne_protege_les_secrets(tmp_path):
    import os
    import stat
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    racine = Path(__file__).resolve().parents[1]
    workflow = (racine / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    etape = workflow.split("- name: Configuration de la pile (compte applicatif, stockage restreint)", 1)[1]
    script = etape.split("python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    config = tmp_path / "config-hors-ligne.json"
    fichier_env = tmp_path / "env-hors-ligne.sh"
    header = tmp_path / "header-hors-ligne"
    script = textwrap.dedent(script).replace("/tmp/config-hors-ligne.json", str(config))
    script = script.replace("/tmp/env-hors-ligne.sh", str(fichier_env)).replace("/tmp/header-hors-ligne", str(header))
    injection = tmp_path / "execution-interdite"
    env = dict(os.environ, GITHUB_WORKSPACE=str(racine), GITHUB_ENV=str(tmp_path / "env"),
               SEAMTECH_DATABASE_URL_ESSAI="postgresql://u:secret-synthetique@db-test/base",
               AUTH_TOKEN_APP="jeton-synthetique", SEAMTECH_S3_ACCESS_KEY="acces-fictif",
               SEAMTECH_S3_SECRET_KEY=f"secret'$(touch {injection})", SEAMTECH_UI_PASSWORD="mot-fictif",
               SEAMTECH_SESSION_SECRET="session-fictive")
    execution = subprocess.run([sys.executable, "-c", script], env=env, cwd=racine,
                               text=True, capture_output=True, check=True)
    assert "jeton-synthetique" not in execution.stdout + execution.stderr
    assert "secret-synthetique" not in execution.stdout + execution.stderr
    assert json.loads(config.read_text(encoding="utf-8"))["auth_token"] == "jeton-synthetique"
    if os.name != "nt":
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in (config, fichier_env, header))
    assert header.read_text(encoding="utf-8") == "X-SEAMTECH-TOKEN: jeton-synthetique\n"
    charge = subprocess.run(["bash", "-c", 'source "$1"; printf "%s" "$SEAMTECH_S3_SECRET_KEY"',
                             "bash", str(fichier_env)], text=True, capture_output=True, check=True)
    assert charge.stdout == env["SEAMTECH_S3_SECRET_KEY"]
    assert not injection.exists()
    assert "sudo install -m 0600 -o seamtech-app -g seamtech-app" in etape
    assert "rm /tmp/config-hors-ligne.json /tmp/env-hors-ligne.sh /tmp/header-hors-ligne" in etape


def test_destruction_sauvegarde_refuse_cible_non_produite_par_la_fixture(monkeypatch):
    import pytest

    from tests import test_sauvegarde_restauration as restauration

    monkeypatch.setattr(restauration, "URL_PG", "postgresql://test@localhost/postgres")
    def interdiction_connexion(*_args, **_kwargs):
        raise AssertionError("Aucune connexion ne doit être tentée pour une cible non sûre")
    monkeypatch.setattr(restauration.psycopg2, "connect", interdiction_connexion)
    with pytest.raises(ValueError):
        restauration._detruire_base("postgresql://test@localhost/production", "production")
    with pytest.raises(ValueError):
        restauration._detruire_base("postgresql://test@autre/recherche_test_1234567890", "recherche_test_1234567890")
    with pytest.raises(ValueError):
        restauration._detruire_base("postgresql://test@localhost/recherche_test_1234567890", "recherche_test_abcdefghij")
    with pytest.raises(ValueError):
        restauration._detruire_base(
            "postgresql://test@localhost/recherche_test_1234567890?dbname=production",
            "recherche_test_1234567890",
        )


def test_etape_ci_arrete_le_bon_processus_sans_toucher_un_autre(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    import pytest
    import yaml

    if not hasattr(os, "pidfd_open"):
        pytest.skip("pidfd Linux requis pour l'arrêt sûr du processus de test")
    racine = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((racine / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    script = next(s["run"] for s in workflow["jobs"]["backend"]["steps"] if s.get("name") == "Arrêter S3rver de test")
    env = dict(os.environ, RUNNER_TEMP=str(tmp_path))
    pid_file = tmp_path / "seamtech-s3rver.pid"
    commande = [sys.executable, "-c", "import time; time.sleep(30)"]
    tiers = subprocess.Popen(commande)
    try:
        pid_file.write_text(str(tiers.pid), encoding="ascii")
        refus = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True)
        assert refus.returncode != 0
        assert tiers.poll() is None
    finally:
        tiers.terminate()
        tiers.wait(timeout=5)
    simule = subprocess.Popen(commande + ["tests/fixtures/s3rver/node_modules/.bin/s3rver", "14569"])
    try:
        pid_file.write_text(str(simule.pid), encoding="ascii")
        resultat = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True)
        assert resultat.returncode == 0, resultat.stderr
        assert simule.wait(timeout=5) != 0
    finally:
        if simule.poll() is None:
            simule.terminate()
            simule.wait(timeout=5)


def test_banc_pg_refuse_dbname_de_query_avant_collecte_sans_fuite():
    import os
    import subprocess
    import sys
    from pathlib import Path

    racine = Path(__file__).resolve().parents[1]
    env = dict(os.environ, SEAMTECH_TEST_DATABASE_URL="postgresql://u:secret-fictif@db/postgres?dbname=production")
    result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                             "tests/test_e2e_seed_securite.py"], cwd=racine, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "secret-fictif" not in result.stdout + result.stderr
    assert "URL PostgreSQL de test" in result.stderr
    assert "tests collected" not in result.stdout


def test_url_pg_jetable_preserve_options_et_refuse_redirection():
    import pytest
    from psycopg2.extensions import parse_dsn

    from tests.helpers_postgres_isole import url_base_jetable

    source = "postgresql://u:p@127.0.0.1:55432/postgres?sslmode=disable&application_name=essai"
    sortie = url_base_jetable(source, "essai_1234")
    attendu = parse_dsn(source)
    attendu["dbname"] = "essai_1234"
    assert parse_dsn(sortie) == attendu
    for danger in ("?dbname=production", "?dbname=postgres", "?dbname=essai_1234"):
        with pytest.raises(ValueError):
            url_base_jetable("postgresql://u:p@127.0.0.1/postgres" + danger, "essai_1234")
    with pytest.raises(ValueError):
        url_base_jetable(source, "../production")


def test_fixture_base_jetable_verifie_url_avant_connexion(monkeypatch):
    import psycopg2
    import pytest

    from tests import conftest

    monkeypatch.setattr(conftest, "DATABASE_URL", "postgresql://u:p@exemple/postgres?dbname=production")
    def interdiction(*_args, **_kwargs):
        raise AssertionError("aucune connexion autorisée")
    monkeypatch.setattr(psycopg2, "connect", interdiction)
    with pytest.raises(ValueError):
        conftest._creer_base_jetable()


def test_demarrage_hors_ligne_ne_met_pas_les_secrets_dans_les_arguments():
    from pathlib import Path

    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = workflow.split("  hors-ligne-reel:", 1)[1].split("\n  recette-corpus-reel:", 1)[0]
    demarrage = job.split("- name: Démarrer la pile", 1)[1].split("- name:", 1)[0]
    for argument in ('SEAMTECH_DATABASE_URL="$SEAMTECH_DATABASE_URL"',
                     'SEAMTECH_AUTH_TOKEN="$AUTH_TOKEN_APP"',
                     'SEAMTECH_S3_SECRET_KEY="$SEAMTECH_S3_SECRET_KEY"',
                     'SEAMTECH_UI_PASSWORD="$SEAMTECH_UI_PASSWORD"',
                     'SEAMTECH_SESSION_SECRET="$SEAMTECH_SESSION_SECRET"',
                     '-H "X-SEAMTECH-TOKEN: $AUTH_TOKEN_APP"'):
        assert argument not in demarrage
    assert '. /srv/seamtech/env-hors-ligne.sh' in demarrage
    assert '-H @/srv/seamtech/header-hors-ligne' in job


def test_url_pg_jetable_preserve_socket_unix_et_options():
    from psycopg2.extensions import parse_dsn

    from tests.helpers_postgres_isole import url_base_jetable

    url = "postgresql:///postgres?host=/tmp&port=55432&sslmode=disable"
    sortie = parse_dsn(url_base_jetable(url, "essai_unix"))
    assert sortie["dbname"] == "essai_unix"
    assert sortie["host"] == "/tmp"
    assert sortie["port"] == "55432"
    assert sortie["sslmode"] == "disable"


def test_url_role_limite_preserve_socket_et_port_sans_detournement():
    from psycopg2.extensions import parse_dsn

    from tests.test_migrations_metier import _url_limite

    admin = 'postgresql://admin@localhost/postgres?host=%2Ftmp%2Fpg-test-isole&port=55498&sslmode=disable'
    construit = parse_dsn(_url_limite(admin, 'limite_test'))
    assert construit['dbname'] == 'limite_test'
    assert construit['host'] == '/tmp/pg-test-isole'
    assert construit['port'] == '55498'
    assert construit['sslmode'] == 'disable'
    assert construit['user'] == 'seamtech_limite'


@pytest.mark.parametrize('parametre', ['user=admin', 'password=secret', 'dbname=postgres'])
def test_url_role_limite_refuse_identite_ou_base_detournee(parametre):
    from tests.test_migrations_metier import _url_limite

    with pytest.raises(ValueError):
        _url_limite(f'postgresql://admin@localhost/postgres?{parametre}', 'limite_test')
