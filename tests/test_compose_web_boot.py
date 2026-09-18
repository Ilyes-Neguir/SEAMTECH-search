"""`docker compose up web` must be able to boot.

Commit 66385a9 (UI sign-in) flipped the web service's compose default for
``SEAMTECH_BEHIND_TLS_PROXY`` from ``true`` to ``false``. AppConfig rejects that
combination outright -- host ``0.0.0.0`` (which the container needs so
``frontend`` can reach it over the compose network) plus an auth token plus
``behind_tls_proxy=false`` -- and ``create_app`` repeats the check. Either way
the process raised *before* uvicorn bound a port, so the container exited, never
became healthy, and

    docker compose up -d --build web frontend

failed with "dependency failed to start: container seamtech-search-web-1 is
unhealthy". That is the documented one-command office deployment, and it was
broken for every fresh checkout.

Nothing caught it locally: Docker is unavailable in this sandbox, so
``tests/test_integration_docker.py`` skips, and the CI job that does run it can
only report that the container was unhealthy -- its logs live on a blob host
that is not always reachable.

So these tests rebuild the web service's environment straight out of
docker-compose.yml and call the real ``create_app()``. No containers, no
network: just the compose file and the guard it has to satisfy.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"
CI_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
EXAMPLE_CONFIG = Path(__file__).resolve().parents[1] / "config" / "config.example.json"

# The values the CI integration job supplies. `${VAR:?...}` entries in
# docker-compose.yml make `docker compose config` fail without them, so these
# have to exist for the file to be usable at all.
CI_PROVIDED_ENV = {
    "POSTGRES_PASSWORD": "test_password",
    "MINIO_ROOT_USER": "minioadmin",
    "MINIO_ROOT_PASSWORD": "minioadmin123",
    "REDIS_PASSWORD": "redis_test_password",
    "SEAMTECH_AUTH_TOKEN": "test-token-123",
    "SEAMTECH_UI_PASSWORD": "ci-ui-password",
    "SEAMTECH_SESSION_SECRET": "ci-session-secret-not-for-production",
    "SEAMTECH_S3_BUCKET": "seamtech-documents",
    # SEAMTECH_BEHIND_TLS_PROXY is deliberately absent: the tests below assert
    # the compose *defaults*, which is what a fresh checkout actually gets.
}

# Matches ${VAR}, ${VAR:-default} and ${VAR:?message}, including when embedded
# inside a longer string such as the Postgres DSN.
_INTERPOLATION = re.compile(r"\$\{([A-Za-z0-9_]+)(?::([-?])([^}]*))?\}")


class _MissingRequiredVar(Exception):
    """A `${VAR:?message}` variable that the environment does not provide."""


def _resolve(value: Any, env: dict[str, str]) -> str:
    """Apply docker compose's interpolation rules to one environment value."""
    text = str(value)

    def substitute(match: re.Match[str]) -> str:
        name, operator, argument = match.group(1), match.group(2), match.group(3) or ""
        provided = env.get(name)
        if provided:
            return provided
        if operator == "-":
            return argument
        if operator == "?":
            raise _MissingRequiredVar(f"{name}: {argument}")
        return ""

    return _INTERPOLATION.sub(substitute, text)


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    with COMPOSE_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _service_environment(compose: dict[str, Any], service: str, env: dict[str, str]) -> dict[str, str]:
    raw = compose["services"][service]["environment"]
    # Compose accepts either a mapping or a list of KEY=VALUE; this file uses a
    # mapping, but handle both so the test does not break on a refactor.
    if isinstance(raw, list):
        raw = dict(item.split("=", 1) for item in raw)
    return {key: _resolve(value, env) for key, value in raw.items()}


def _hermetic_web_environment(compose: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    """Web's real environment, minus the services this process cannot reach.

    ``postgres``, ``redis`` and ``minio`` are compose-internal hostnames. Only
    the URLs and the storage backend are swapped: everything AppConfig's
    validation and create_app's guard actually read -- host, auth token,
    behind_tls_proxy, allow_network_access -- comes from the compose file
    unchanged, which is the point of the test.
    """
    resolved = _service_environment(compose, "web", env)
    resolved.pop("SEAMTECH_DATABASE_URL", None)
    resolved.pop("SEAMTECH_REDIS_URL", None)
    resolved["SEAMTECH_STORAGE_BACKEND"] = "local"
    resolved["SEAMTECH_S3_ENDPOINT_URL"] = ""
    return resolved


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """AppConfig.load insists the file exists, exactly as the container does.

    The Dockerfile seeds it with `cp config/config.example.json
    config/config.json`, so the example is the right baseline.
    """
    path = tmp_path / "config.json"
    shutil.copy(EXAMPLE_CONFIG, path)
    return path


def _boot(environment: dict[str, str], config_file: Path, monkeypatch: pytest.MonkeyPatch):
    for key, value in environment.items():
        if value == "":
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return create_app(AppConfig.load(str(config_file)))


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------


def test_web_service_environment_boots_the_real_app(
    compose: dict[str, Any], config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented compose deployment must start. No Docker required."""
    app = _boot(_hermetic_web_environment(compose, CI_PROVIDED_ENV), config_file, monkeypatch)

    assert app is not None
    assert any(getattr(route, "path", None) == "/health" for route in app.routes)


def test_flipping_behind_tls_proxy_to_false_prevents_boot(
    compose: dict[str, Any], config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce 66385a9: the default is what keeps web alive.

    If this stops raising, the guard in AppConfig/create_app has been removed
    and the compose default no longer matters -- which would make the test above
    pass for the wrong reason.
    """
    environment = _hermetic_web_environment(compose, CI_PROVIDED_ENV)
    assert environment["SEAMTECH_HOST"] == "0.0.0.0", "web must bind 0.0.0.0 for frontend to reach it"
    assert environment["SEAMTECH_AUTH_TOKEN"], "web must have a token in the documented deployment"

    environment["SEAMTECH_BEHIND_TLS_PROXY"] = "false"

    with pytest.raises(Exception) as excinfo:
        _boot(environment, config_file, monkeypatch)

    message = str(excinfo.value).lower()
    assert "behind_tls_proxy" in message or "non-local" in message, (
        f"web failed for an unexpected reason: {excinfo.value}"
    )


def test_compose_web_defaults_behind_tls_proxy_to_true(compose: dict[str, Any]) -> None:
    """The default, not just an override, has to satisfy the guard."""
    resolved = _service_environment(compose, "web", CI_PROVIDED_ENV)

    assert resolved["SEAMTECH_BEHIND_TLS_PROXY"] == "true", (
        "web's compose default must stay true: host 0.0.0.0 + auth token + "
        "behind_tls_proxy=false makes AppConfig/create_app raise before uvicorn "
        "binds, so the container exits and compose reports it unhealthy"
    )


# ---------------------------------------------------------------------------
# The deliberate asymmetry with the frontend
# ---------------------------------------------------------------------------


def test_frontend_defaults_behind_tls_proxy_to_false(compose: dict[str, Any]) -> None:
    """Frontend's opposite default is intentional, not drift to reconcile.

    shouldMarkCookieSecure() (frontend/lib/auth.ts) returns true when
    SEAMTECH_BEHIND_TLS_PROXY === "true", which would mark the session cookie
    Secure. The default compose deployment publishes on 127.0.0.1 over plain
    HTTP, where a browser drops a Secure cookie and sign-in -- the whole point
    of audit issue #5 -- silently fails. It still detects TLS per request via
    x-forwarded-proto, and SEAMTECH_SECURE_COOKIES forces it.
    """
    resolved = _service_environment(compose, "frontend", CI_PROVIDED_ENV)

    assert resolved["SEAMTECH_BEHIND_TLS_PROXY"] == "false"


def test_explicit_override_reaches_both_services_identically(compose: dict[str, Any]) -> None:
    """One variable, two services: setting it must not leave them disagreeing."""
    for value in ("true", "false"):
        env = dict(CI_PROVIDED_ENV, SEAMTECH_BEHIND_TLS_PROXY=value)
        assert _service_environment(compose, "web", env)["SEAMTECH_BEHIND_TLS_PROXY"] == value
        assert _service_environment(compose, "frontend", env)["SEAMTECH_BEHIND_TLS_PROXY"] == value


# ---------------------------------------------------------------------------
# The compose environment is complete and CI can actually supply it
# ---------------------------------------------------------------------------


def test_every_required_variable_is_supplied_by_ci(compose: dict[str, Any]) -> None:
    """A `${VAR:?...}` without a CI value breaks `docker compose config` itself.

    Adding a mandatory variable to compose without adding it to the workflow
    fails the integration job before any container starts, and the error looks
    nothing like a missing environment variable.
    """
    required = {
        match.group(1)
        for match in _INTERPOLATION.finditer(COMPOSE_PATH.read_text(encoding="utf-8"))
        if match.group(2) == "?"
    }
    assert required, "no mandatory variables found; the pattern stopped matching"

    missing = sorted(required - set(CI_PROVIDED_ENV))
    assert not missing, f"docker-compose.yml requires variables CI does not provide: {missing}"

    # And the whole file must resolve for every service, not just web.
    for service in compose["services"]:
        environment = compose["services"][service].get("environment")
        if not environment:
            continue
        _service_environment(compose, service, CI_PROVIDED_ENV)


def test_ci_workflow_sets_every_required_variable() -> None:
    """Cross-check the compose requirements against the actual workflow file.

    CI_PROVIDED_ENV above is a hand-written copy; this asserts the real workflow
    declares the same variables somewhere, so the copy cannot drift into
    promising values that CI never sets.
    """
    ci_text = CI_PATH.read_text(encoding="utf-8")
    required = {
        match.group(1)
        for match in _INTERPOLATION.finditer(COMPOSE_PATH.read_text(encoding="utf-8"))
        if match.group(2) == "?"
    }

    declared = {
        match.group(1) for match in re.finditer(r"^\s+([A-Z][A-Z0-9_]*):\s", ci_text, re.MULTILINE)
    }
    missing = sorted(required - declared)
    assert not missing, f"ci.yml never sets these compose-required variables: {missing}"


def test_resolved_environment_has_no_leftover_placeholders(compose: dict[str, Any]) -> None:
    """An unresolved `${...}` would be handed to the app as a literal string."""
    for service in compose["services"]:
        if not compose["services"][service].get("environment"):
            continue
        for key, value in _service_environment(compose, service, CI_PROVIDED_ENV).items():
            assert "${" not in value, f"{service}.{key} did not resolve: {value!r}"


def test_web_environment_is_valid_json_config_overrides(config_file: Path) -> None:
    """AppConfig.load must accept the compose environment end to end."""
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    environment = _hermetic_web_environment(compose, CI_PROVIDED_ENV)

    data = json.loads(config_file.read_text(encoding="utf-8"))
    data["host"] = environment["SEAMTECH_HOST"]
    data["auth_token"] = environment["SEAMTECH_AUTH_TOKEN"]
    data["behind_tls_proxy"] = environment["SEAMTECH_BEHIND_TLS_PROXY"] == "true"
    data["allow_network_access"] = environment["SEAMTECH_ALLOW_NETWORK_ACCESS"] == "true"
    data["database_url"] = None
    data["storage_backend"] = "local"
    data["redis_url"] = None
    config_file.write_text(json.dumps(data), encoding="utf-8")

    config = AppConfig.load(str(config_file))

    assert config.host == "0.0.0.0"
    assert config.behind_tls_proxy is True
    assert config.allow_network_access is True
