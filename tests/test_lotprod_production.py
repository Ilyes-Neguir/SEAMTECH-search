"""Lot PROD — mise en production (audit ECC `production-audit`, 2026-10-26).

Contrat RED des quatre correctifs de l'audit :

1. **Secrets d'exemple refusés au démarrage de production** : les points
   d'entrée déployés (`serve`, worker) rejettent les secrets d'exemple
   (`change-me`…) et les secrets trop courts — copier `.env.example` ne doit
   jamais démarrer un service ouvert avec des secrets publics. La validation
   vit aux points d'entrée, PAS dans `create_app` (les tests y passent des
   jetons courts, ex. ``"secret"``).
2. **Sauvegarde planifiée** : la machinerie `sauvegarde` (sauver/restaurer/
   vérifier) existe mais rien ne la déclenche — un service compose
   `sauvegarde` la lance quand la dernière dépasse l'intervalle, sans mourir
   sur un échec.
3. **Version identifiable** : `/health` expose la version de l'application.
4. **En-têtes de sécurité HTTP** globaux côté frontend (nosniff, referrer,
   cadrage, CSP) et refus des secrets d'exemple côté Next (`lib/auth.ts`).
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import seamtech_search
from seamtech_search.api import create_app
from seamtech_search.config import AppConfig

RACINE = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1. Secrets de production
# ---------------------------------------------------------------------------


def _config(tmp_path: Path, **surcharges) -> AppConfig:
    base = {
        "root_paths": [tmp_path],
        "database_path": tmp_path / "search.db",
        "auth_token": "jeton-de-test-suffisamment-long",
    }
    base.update(surcharges)
    return AppConfig(**base)


class TestExigerSecretsProduction:
    def test_jeton_exemple_refuse(self, tmp_path: Path) -> None:
        from seamtech_search.config import exiger_secrets_production

        for valeur in ("change-me", "CHANGE-ME", " changeme ", "change_me", "exemple"):
            with pytest.raises(ValueError):
                exiger_secrets_production(_config(tmp_path, auth_token=valeur))

    def test_jeton_trop_court_refuse(self, tmp_path: Path) -> None:
        from seamtech_search.config import exiger_secrets_production

        with pytest.raises(ValueError):
            exiger_secrets_production(_config(tmp_path, auth_token="court"))

    def test_jeton_ci_et_jeton_honnete_acceptes(self, tmp_path: Path) -> None:
        from seamtech_search.config import exiger_secrets_production

        # « test-token » est le jeton de la CI (boot de l'image réelle) : il
        # doit continuer de passer, sinon la recette casse pour rien.
        exiger_secrets_production(_config(tmp_path, auth_token="test-token"))
        exiger_secrets_production(_config(tmp_path, auth_token="x" * 40))

    def test_sans_jeton_hors_loopback_refuse(self, tmp_path: Path) -> None:
        from seamtech_search.config import exiger_secrets_production

        with pytest.raises(ValueError):
            exiger_secrets_production(_config(tmp_path, auth_token=None, host="0.0.0.0"))
        # En loopback strict, l'absence de jeton reste un choix local licite.
        exiger_secrets_production(_config(tmp_path, auth_token=None, host="127.0.0.1"))

    def test_secret_s3_exemple_refuse(self, tmp_path: Path) -> None:
        from seamtech_search.config import exiger_secrets_production

        with pytest.raises(ValueError):
            exiger_secrets_production(_config(tmp_path, s3_secret_key="change-me"))

    def test_mot_de_passe_base_exemple_refuse(self, tmp_path: Path) -> None:
        from seamtech_search.config import exiger_secrets_production

        with pytest.raises(ValueError):
            exiger_secrets_production(
                _config(
                    tmp_path,
                    database_url="postgresql://seamtech:change-me@postgres:5432/seamtech_search",
                )
            )

    def test_le_message_ne_fuite_jamais_la_valeur(self, tmp_path: Path) -> None:
        from seamtech_search.config import exiger_secrets_production

        with pytest.raises(ValueError) as erreur:
            exiger_secrets_production(_config(tmp_path, auth_token="change-me"))
        assert "change-me" not in str(erreur.value)
        assert "auth_token" in str(erreur.value)


class TestPointsEntreeValidentLesSecrets:
    def test_run_server_refuse_avant_de_servir(self, tmp_path: Path, monkeypatch) -> None:
        import seamtech_search.cli as cli

        servi: list[bool] = []
        monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: servi.append(True))
        fichier = tmp_path / "config.json"
        fichier.write_text(
            json.dumps(
                {
                    "root_paths": [str(tmp_path)],
                    "database_path": str(tmp_path / "search.db"),
                    "auth_token": "change-me",
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            cli.run_server(str(fichier))
        assert servi == [], "le serveur ne doit pas démarrer avec un secret d'exemple"

    def test_le_worker_valide_aussi(self) -> None:
        import seamtech_search.worker_service as worker_service

        source = inspect.getsource(worker_service._construire)
        assert "exiger_secrets_production" in source
        # La validation suit immédiatement le chargement de la config, avant
        # toute construction d'index ou de file.
        assert source.index("AppConfig.load") < source.index("exiger_secrets_production")
        assert source.index("exiger_secrets_production") < source.index("SearchIndex(")


# ---------------------------------------------------------------------------
# 2. Sauvegarde planifiée
# ---------------------------------------------------------------------------


def _ecrire_manifeste(dossier: Path, age_heures: float) -> None:
    instant = datetime.now(timezone.utc) - timedelta(hours=age_heures)
    (dossier / "2026.manifest.json").write_text(
        json.dumps({"horodatage": instant.isoformat()}), encoding="utf-8"
    )


class TestSauvegardePlanifiee:
    def _config_sauvegarde(self, tmp_path: Path, monkeypatch) -> AppConfig:
        dossier = tmp_path / "backups"
        dossier.mkdir()
        monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(dossier))
        return _config(
            tmp_path,
            database_url="postgresql://seamtech:mot-de-passe-long@postgres:5432/seamtech_search",
        )

    def test_due_sans_aucune_sauvegarde(self, tmp_path: Path, monkeypatch) -> None:
        from seamtech_search.sauvegarde_planifiee import sauvegarde_due

        config = self._config_sauvegarde(tmp_path, monkeypatch)
        assert sauvegarde_due(config, 24) is True

    def test_non_due_quand_recente(self, tmp_path: Path, monkeypatch) -> None:
        from seamtech_search.sauvegarde_planifiee import sauvegarde_due

        config = self._config_sauvegarde(tmp_path, monkeypatch)
        _ecrire_manifeste(tmp_path / "backups", age_heures=2)
        assert sauvegarde_due(config, 24) is False

    def test_due_quand_trop_ancienne(self, tmp_path: Path, monkeypatch) -> None:
        from seamtech_search.sauvegarde_planifiee import sauvegarde_due

        config = self._config_sauvegarde(tmp_path, monkeypatch)
        _ecrire_manifeste(tmp_path / "backups", age_heures=49)
        assert sauvegarde_due(config, 24) is True

    def test_executer_si_du_appelle_la_sauvegarde(self, tmp_path: Path, monkeypatch) -> None:
        from seamtech_search.sauvegarde_planifiee import executer_si_du

        config = self._config_sauvegarde(tmp_path, monkeypatch)
        appels: list[bool] = []
        assert executer_si_du(config, intervalle_heures=24, fonction_sauver=lambda: appels.append(True)) is True
        assert appels == [True]

    def test_executer_si_non_du_ne_fait_rien(self, tmp_path: Path, monkeypatch) -> None:
        from seamtech_search.sauvegarde_planifiee import executer_si_du

        config = self._config_sauvegarde(tmp_path, monkeypatch)
        _ecrire_manifeste(tmp_path / "backups", age_heures=1)
        appels: list[bool] = []
        assert executer_si_du(config, intervalle_heures=24, fonction_sauver=lambda: appels.append(True)) is False
        assert appels == []

    def test_un_echec_n_avale_pas_le_planificateur(self, tmp_path: Path, monkeypatch) -> None:
        from seamtech_search.sauvegarde_planifiee import executer_si_du

        config = self._config_sauvegarde(tmp_path, monkeypatch)

        def en_panne() -> None:
            raise RuntimeError("pg_dump absent")

        # La boucle de planification doit survivre : False, pas d'exception.
        assert executer_si_du(config, intervalle_heures=24, fonction_sauver=en_panne) is False

    def test_sans_base_postgres_rien_a_planifier(self, tmp_path: Path, monkeypatch) -> None:
        from seamtech_search.sauvegarde_planifiee import executer_si_du

        config = _config(tmp_path)  # SQLite : pg_dump sans objet
        appels: list[bool] = []
        assert executer_si_du(config, intervalle_heures=24, fonction_sauver=lambda: appels.append(True)) is False
        assert appels == []


class TestComposeSauvegarde:
    @pytest.fixture(scope="class")
    def services(self) -> dict:
        with (RACINE / "docker-compose.yml").open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)["services"]

    def test_service_sauvegarde_planifiee(self, services: dict) -> None:
        assert "sauvegarde" in services, "aucun service ne planifie les sauvegardes"
        service = services["sauvegarde"]
        commande = " ".join(service.get("command") or [])
        assert "sauvegarde_planifiee" in commande
        assert "ports" not in service, "le planificateur n'expose aucun port"
        assert service["depends_on"]["postgres"]["condition"] == "service_healthy"
        env = service.get("environment") or {}
        if isinstance(env, list):
            env = dict(e.split("=", 1) for e in env)
        assert "SEAMTECH_BACKUP_DIR" in env
        volumes = [str(v) for v in service.get("volumes") or []]
        assert any(env["SEAMTECH_BACKUP_DIR"] in v for v in volumes), (
            "le dossier des sauvegardes doit être un volume persistant"
        )

    def test_healthcheck_sauvegarde_ne_sonde_pas_un_serveur_http_absent(self, services: dict) -> None:
        """OBS-03 : l'image porte un HEALTHCHECK HTTP (/live:8000) pour `web`.
        Le planificateur ne sert pas HTTP : sans surcharge, Docker le déclare
        « unhealthy » à vie alors qu'il tourne — la sonde doit vérifier le
        processus du planificateur lui-même."""
        healthcheck = services["sauvegarde"].get("healthcheck") or {}
        sonde = " ".join(str(morceau) for morceau in healthcheck.get("test") or [])
        assert sonde, "sauvegarde doit surcharger le HEALTHCHECK HTTP hérité de l'image"
        assert "/live" not in sonde and "8000" not in sonde
        # Sonde faillible et métier : base joignable + outils de dump présents
        # (OBS-04) + dossier inscriptible — jamais un simple « PID 1 existe ».
        assert "pg_isready" in sonde and "pg_dump" in sonde and "pg_restore" in sonde
        assert "/proc/1" not in sonde


# ---------------------------------------------------------------------------
# 3. Version identifiable
# ---------------------------------------------------------------------------


def test_health_expose_la_version(tmp_path: Path) -> None:
    config = _config(tmp_path)
    client = TestClient(create_app(config))
    reponse = client.get("/health", headers={"X-SEAMTECH-TOKEN": config.auth_token})
    assert reponse.status_code == 200
    assert reponse.json()["version"] == seamtech_search.__version__


# ---------------------------------------------------------------------------
# 4. Frontend : en-têtes de sécurité + secrets d'exemple
# ---------------------------------------------------------------------------


def test_en_tetes_de_securite_frontend() -> None:
    texte = (RACINE / "frontend" / "next.config.mjs").read_text(encoding="utf-8")
    assert "X-Content-Type-Options" in texte
    assert "Referrer-Policy" in texte
    assert "X-Frame-Options" in texte or "frame-ancestors" in texte
    assert "Content-Security-Policy" in texte


def test_frontend_refuse_le_mot_de_passe_exemple() -> None:
    texte = (RACINE / "frontend" / "lib" / "auth.ts").read_text(encoding="utf-8")
    assert "change-me" in texte, "le mot de passe d'exemple doit être traité comme non configuré"
