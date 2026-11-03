"""La supervision ne confond pas une copie partielle avec une protection prouvée."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.etat_exploitation import etat_sauvegarde
from seamtech_search.sauvegarde_planifiee import sauvegarde_due


@pytest.mark.parametrize("qualification", [None, False])
def test_manifeste_reel_partiel_ne_se_dit_pas_ok(tmp_path: Path, monkeypatch, qualification):
    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db", database_url="postgresql://synthetique")
    manifeste = {"date_iso": datetime.now(timezone.utc).isoformat(), "complet": True}
    if qualification is not None:
        manifeste["protection_globale_prouvee"] = qualification
    (tmp_path / "sauvegarde.manifest.json").write_text(json.dumps(manifeste))
    etat = etat_sauvegarde(config)
    assert etat["age_heures"] is not None
    assert etat["statut"] == "non_qualifiee"
    assert etat["protection_globale_prouvee"] is False
    assert not sauvegarde_due(config, 24)  # pas de tempête de dumps tant que la copie est récente


def test_planificateur_ne_signale_pas_reussite_globale_pour_copie_partielle(tmp_path: Path, monkeypatch, caplog):
    from seamtech_search.sauvegarde_planifiee import executer_si_du

    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db", database_url="postgresql://synthetique")
    resultat = {"complet": True, "protection_globale_prouvee": False}
    assert not executer_si_du(config, intervalle_heures=24, fonction_sauver=lambda: resultat)
    assert "non qualifiée" in caplog.text


def test_planificateur_reel_transmet_la_qualification(tmp_path: Path, monkeypatch, caplog):
    from seamtech_search import sauvegarde
    from seamtech_search.sauvegarde_planifiee import executer_si_du

    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(sauvegarde, "_client_s3_depuis_env", lambda: None)
    monkeypatch.setattr(sauvegarde, "sauver", lambda *args, **kwargs: {
        "complet": True, "protection_globale_prouvee": False,
    })
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db", database_url="postgresql://synthetique")
    assert not executer_si_du(config, intervalle_heures=24)
    assert "non qualifiée" in caplog.text


def test_health_refuse_manifeste_contradictoire(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db", database_url="postgresql://synthetique")
    (tmp_path / "sauvegarde.manifest.json").write_text(json.dumps({
        "date_iso": datetime.now(timezone.utc).isoformat(), "complet": False,
        "protection_globale_prouvee": True,
    }))
    etat = etat_sauvegarde(config)
    assert etat["statut"] == "non_qualifiee"
    assert not etat["protection_globale_prouvee"]


def test_planificateur_refuse_manifeste_contradictoire(tmp_path: Path, monkeypatch):
    from seamtech_search.sauvegarde_planifiee import executer_si_du

    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db", database_url="postgresql://synthetique")
    assert not executer_si_du(config, intervalle_heures=24, fonction_sauver=lambda: {
        "complet": False, "protection_globale_prouvee": True,
    })


def test_date_future_ne_masque_pas_une_sauvegarde_due(tmp_path: Path, monkeypatch):
    from datetime import timedelta

    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db", database_url="postgresql://synthetique")
    (tmp_path / "sauvegarde.manifest.json").write_text(json.dumps({
        "date_iso": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "complet": False, "protection_globale_prouvee": False,
    }))
    etat = etat_sauvegarde(config)
    assert etat["age_heures"] is None
    assert etat["statut"] == "age_inconnu"
    assert sauvegarde_due(config, 24)


@pytest.mark.parametrize('resultat', [None, False, True, [], 'ok', {'complet': True}, {'protection_globale_prouvee': True}])
def test_planificateur_refuse_tout_resultat_sans_qualification_explicite(tmp_path, monkeypatch, resultat, caplog):
    from seamtech_search.sauvegarde_planifiee import executer_si_du

    monkeypatch.setenv('SEAMTECH_BACKUP_DIR', str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db', database_url='postgresql://synthetique')
    assert not executer_si_du(config, intervalle_heures=24, fonction_sauver=lambda: resultat)
    assert 'non qualifiée' in caplog.text


@pytest.mark.parametrize('manifeste', [None, [], 'texte', 42, {'dump': []}, {'dump': 'invalide'}, {'dump': 42}])
def test_health_et_planificateur_refusent_manifestes_json_malformes(tmp_path: Path, monkeypatch, manifeste):
    monkeypatch.setenv('SEAMTECH_BACKUP_DIR', str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db', database_url='postgresql://synthetique')
    (tmp_path / 'sauvegarde.manifest.json').write_text(json.dumps(manifeste))
    etat = etat_sauvegarde(config)
    assert etat['statut'] == 'illisible'
    assert etat['age_heures'] is None
    assert sauvegarde_due(config, 24)


def test_health_et_planificateur_refusent_manifeste_non_utf8(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('SEAMTECH_BACKUP_DIR', str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db', database_url='postgresql://synthetique')
    (tmp_path / 'sauvegarde.manifest.json').write_bytes(b'\xff\xfe')
    etat = etat_sauvegarde(config)
    assert etat['statut'] == 'illisible'
    assert etat['age_heures'] is None
    assert sauvegarde_due(config, 24)


def test_planificateur_ne_journalise_pas_secrets_des_exceptions(tmp_path: Path, monkeypatch, caplog):
    from seamtech_search.sauvegarde_planifiee import executer_si_du

    monkeypatch.setenv('SEAMTECH_BACKUP_DIR', str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db', database_url='postgresql://synthetique')

    def echec():
        raise RuntimeError('jeton-synthetique-secret')

    assert not executer_si_du(config, intervalle_heures=24, fonction_sauver=echec)
    assert 'jeton-synthetique-secret' not in caplog.text
    assert 'RuntimeError' in caplog.text


@pytest.mark.parametrize('nom', ['SEAMTECH_BACKUP_INTERVAL_HEURES', 'SEAMTECH_BACKUP_VERIFICATION_MINUTES'])
@pytest.mark.parametrize('valeur', ['nan', 'inf', '-inf', '0', '-1', 'invalide'])
def test_planificateur_refuse_intervalles_invalides_avant_boucle(tmp_path: Path, monkeypatch, nom, valeur):
    from seamtech_search import sauvegarde_planifiee

    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db', database_url='postgresql://synthetique')
    monkeypatch.setattr(sauvegarde_planifiee.AppConfig, 'load', lambda *_args: config)
    monkeypatch.setattr(sauvegarde_planifiee, 'default_config_path', lambda: tmp_path / 'config.json')
    monkeypatch.setattr(sauvegarde_planifiee, 'exiger_secrets_production', lambda *_args: None)
    monkeypatch.setattr(sauvegarde_planifiee, 'executer_si_du', lambda *_args, **_kwargs: pytest.fail('boucle démarrée'))
    monkeypatch.setenv(nom, valeur)
    assert sauvegarde_planifiee.main([]) == 2


@pytest.mark.parametrize('intervalle', [float('nan'), float('inf'), 0, -1])
def test_sauvegarde_due_ne_masque_pas_copie_avec_intervalle_invalide(tmp_path: Path, monkeypatch, intervalle):
    monkeypatch.setenv('SEAMTECH_BACKUP_DIR', str(tmp_path))
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / 'db', database_url='postgresql://synthetique')
    (tmp_path / 'sauvegarde.manifest.json').write_text(json.dumps({
        'date_iso': datetime.now(timezone.utc).isoformat(), 'dump': {},
        'complet': False, 'protection_globale_prouvee': False,
    }))
    assert sauvegarde_due(config, intervalle)
