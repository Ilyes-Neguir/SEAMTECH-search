from pathlib import Path

import pytest

from seamtech_search.config import AppConfig, default_config_path


def test_default_config_path_prefers_project_config_directory(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample-project"
    config_dir = project_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text('{"root_paths": []}', encoding="utf-8")

    assert default_config_path(project_dir) == config_dir / "config.json"


def test_network_host_requires_explicit_policy(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AppConfig(root_paths=[tmp_path], host="0.0.0.0")

    # Token auth over non-local host requires behind_tls_proxy=True
    with pytest.raises(ValueError, match="behind_tls_proxy"):
        AppConfig(root_paths=[tmp_path], host="0.0.0.0", allow_network_access=True, auth_token="secret")

    config = AppConfig(
        root_paths=[tmp_path], host="0.0.0.0", allow_network_access=True, auth_token="secret", behind_tls_proxy=True
    )
    assert config.allow_network_access is True
    assert config.behind_tls_proxy is True


def test_config_in_config_directory_resolves_paths_from_project_root(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample-project"
    config_dir = project_dir / "config"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.json"
    config_file.write_text(
        """
        {
          "root_paths": ["sample_data"],
          "database_path": "data/search.db"
        }
        """,
        encoding="utf-8",
    )

    config = AppConfig.load(config_file)

    assert config.root_paths == [project_dir / "sample_data"]
    assert config.database_path == project_dir / "data" / "search.db"


def test_env_overrides_host_port_and_network_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text('{"root_paths": ["sample_data"]}', encoding="utf-8")

    monkeypatch.setenv("SEAMTECH_HOST", "0.0.0.0")
    monkeypatch.setenv("SEAMTECH_PORT", "9000")
    monkeypatch.setenv("SEAMTECH_ALLOW_NETWORK_ACCESS", "true")
    monkeypatch.setenv("SEAMTECH_AUTH_TOKEN", "secret")
    monkeypatch.setenv("SEAMTECH_BEHIND_TLS_PROXY", "true")
    monkeypatch.setenv("SEAMTECH_RATE_LIMIT_PER_MINUTE", "1200")
    monkeypatch.setenv("SEAMTECH_MIN_FREE_BYTES", "0")

    config = AppConfig.load(config_file)

    assert config.host == "0.0.0.0"
    assert config.port == 9000
    assert config.allow_network_access is True
    assert config.auth_token == "secret"
    assert config.behind_tls_proxy is True
    assert config.rate_limit_per_minute == 1200
    assert config.min_free_bytes == 0


def test_optional_extraction_tools_default_to_disabled(tmp_path: Path) -> None:
    config = AppConfig(root_paths=[tmp_path])

    assert config.enable_legacy_office is False
    assert config.enable_ocr is False
    assert config.external_extraction_timeout_seconds == 120


def test_missing_config_file_fails_fast(tmp_path: Path) -> None:
    non_existent = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError, match="Configuration file not found"):
        AppConfig.load(non_existent)


def test_variables_durables_et_relecture_ne_dependent_pas_l_une_de_l_autre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Régression : exiger la durabilité ne doit pas exiger la relecture.

    Le bug d'origine testait ``SEAMTECH_REQUIRE_DURABLE_QUEUE`` puis indexait
    ``SEAMTECH_STORAGE_VERIFY_REREAD`` : avec la seule variable de durabilité
    posée (exactement l'environnement du conteneur ``web`` documenté), AppConfig
    levait ``KeyError`` et le serveur ne démarrait plus. Ce test reproduit les
    deux variables indépendamment.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text('{"root_paths": ["sample_data"]}', encoding="utf-8")

    # 1. Durabilité exigée, variable de relecture ABSENTE → aucun KeyError.
    monkeypatch.setenv("SEAMTECH_REQUIRE_DURABLE_QUEUE", "true")
    monkeypatch.delenv("SEAMTECH_STORAGE_VERIFY_REREAD", raising=False)
    config = AppConfig.load(config_file)
    assert config.require_durable_queue is True
    assert config.storage_verify_reread is True, "défaut sûr : on relit les octets stockés"

    # 2. Relecture explicitement désactivée → la valeur est bien lue.
    monkeypatch.setenv("SEAMTECH_STORAGE_VERIFY_REREAD", "false")
    assert AppConfig.load(config_file).storage_verify_reread is False

    # 3. Les deux posées → chacune garde sa valeur.
    monkeypatch.setenv("SEAMTECH_STORAGE_VERIFY_REREAD", "true")
    config = AppConfig.load(config_file)
    assert (config.require_durable_queue, config.storage_verify_reread) == (True, True)
