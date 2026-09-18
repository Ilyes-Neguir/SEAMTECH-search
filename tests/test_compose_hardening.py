"""Static hardening checks on docker-compose.yml.

These parse the compose file directly, so they run in every environment --
including ones with no Docker, where tests/test_integration_docker.py skips.
Each one pins a property that was previously violated and would otherwise be
easy to regress silently:

* a floating `:latest` image tag (audit: "Pin the MinIO image");
* a healthcheck that cannot fail -- `test -d /proc/1` (audit: "MinIO
  healthcheck"), which made `condition: service_healthy` meaningless;
* a published port bound to 0.0.0.0 instead of loopback;
* a service other services wait on with `service_healthy` but which declares no
  healthcheck;
* the UI sign-in secrets being dropped from the frontend service (audit
  issue #5, Option B).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE_PATH = Path(__file__).resolve().parent.parent / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    with COMPOSE_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def services(compose: dict) -> dict:
    return compose["services"]


def _image_tag(image: str) -> str:
    """Tag portion of an image reference, or '' when untagged."""
    # Ignore any digest form and registry port; split on the last colon that
    # appears after the final slash.
    if "@" in image:
        return image.split("@", 1)[1]
    tail = image.rsplit("/", 1)[-1]
    return tail.split(":", 1)[1] if ":" in tail else ""


def test_no_image_uses_a_floating_latest_tag(services: dict) -> None:
    offenders = {
        name: spec["image"]
        for name, spec in services.items()
        if isinstance(spec, dict) and "image" in spec and _image_tag(spec["image"]) == "latest"
    }
    assert not offenders, f"floating :latest tags are not reproducible: {offenders}"


def test_every_image_reference_is_pinned(services: dict) -> None:
    unpinned = {
        name: spec["image"]
        for name, spec in services.items()
        if isinstance(spec, dict) and "image" in spec and not _image_tag(spec["image"])
    }
    assert not unpinned, f"images with no tag resolve to :latest: {unpinned}"


def test_minio_image_is_pinned_to_a_release(services: dict) -> None:
    image = services["minio"]["image"]
    assert image.startswith("quay.io/minio/minio:RELEASE."), image
    assert "latest" not in image


def test_no_healthcheck_uses_the_unfailable_proc_probe(services: dict) -> None:
    """`test -d /proc/1` always succeeds, so it proves nothing about readiness."""
    offenders = {}
    for name, spec in services.items():
        if not isinstance(spec, dict):
            continue
        test = (spec.get("healthcheck") or {}).get("test")
        if not test:
            continue
        joined = " ".join(test) if isinstance(test, list) else str(test)
        if "/proc/1" in joined:
            offenders[name] = joined
    assert not offenders, f"healthchecks that cannot fail: {offenders}"


def test_minio_healthcheck_uses_mc_ready_local(services: dict) -> None:
    """MinIO's documented in-container probe; the image ships mc but no curl."""
    test = services["minio"]["healthcheck"]["test"]
    assert test == ["CMD", "mc", "ready", "local"], test


def test_every_healthchecked_service_has_sane_timing(services: dict) -> None:
    for name, spec in services.items():
        if not isinstance(spec, dict):
            continue
        healthcheck = spec.get("healthcheck")
        if not healthcheck:
            continue
        assert healthcheck.get("interval"), f"{name}: healthcheck has no interval"
        assert healthcheck.get("retries"), f"{name}: healthcheck has no retries"


def test_services_waited_on_for_health_declare_a_healthcheck(services: dict) -> None:
    """`condition: service_healthy` without a healthcheck never becomes healthy."""
    missing = []
    for name, spec in services.items():
        if not isinstance(spec, dict):
            continue
        for dependency, condition in (spec.get("depends_on") or {}).items():
            if isinstance(condition, dict) and condition.get("condition") == "service_healthy":
                target = services.get(dependency)
                if not target or not target.get("healthcheck"):
                    missing.append(f"{name} waits on {dependency}")
    assert not missing, f"service_healthy with no healthcheck: {missing}"


def test_published_ports_are_bound_to_loopback(services: dict) -> None:
    """Nothing in this stack should be reachable from the LAN by default."""
    exposed = []
    for name, spec in services.items():
        if not isinstance(spec, dict):
            continue
        for mapping in spec.get("ports") or []:
            text = str(mapping)
            if text.startswith("127.0.0.1:") or text.startswith("localhost:"):
                continue
            exposed.append(f"{name}: {text}")
    assert not exposed, f"ports published outside loopback: {exposed}"


def test_frontend_declares_the_ui_sign_in_secrets(services: dict) -> None:
    """Audit issue #5 (Option B): the login gate must be configured, not optional."""
    environment = services["frontend"]["environment"]
    for variable in ("SEAMTECH_UI_PASSWORD", "SEAMTECH_SESSION_SECRET"):
        value = environment.get(variable)
        assert value, f"frontend is missing {variable}"
        # ${VAR:?...} makes compose refuse to start when it is unset, which is
        # what keeps a deployment from coming up with sign-in disabled.
        assert ":?" in str(value), f"{variable} should be mandatory: {value}"


def test_required_secrets_are_mandatory_in_compose(services: dict) -> None:
    """Secrets must use ${VAR:?...} so a clean checkout fails loudly, not silently."""
    required = {
        "postgres": ["POSTGRES_PASSWORD"],
        "minio": ["MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"],
        "redis": ["REDIS_PASSWORD"],
        "web": ["SEAMTECH_AUTH_TOKEN"],
        "frontend": ["SEAMTECH_AUTH_TOKEN", "SEAMTECH_UI_PASSWORD", "SEAMTECH_SESSION_SECRET"],
    }
    problems = []
    for service, variables in required.items():
        environment = services[service].get("environment") or {}
        blob = " ".join(str(v) for v in environment.values())
        for variable in variables:
            if f"{variable}:?" not in blob:
                problems.append(f"{service}.{variable}")
    assert not problems, f"secrets not mandatory via ${{VAR:?...}}: {problems}"


def test_tls_proxy_defaults_let_web_boot(services: dict) -> None:
    """Web defaults to true, frontend to false -- and that split is required.

    This test used to assert web defaults to `:-false}`, citing audit issue #7
    ("secure by default for a localhost-only install"). That was wrong, and it
    codified a broken deployment: both AppConfig and create_app refuse
    host 0.0.0.0 + auth token + behind_tls_proxy=false, so the default made web
    raise *before* uvicorn bound a port. The container exited, never became
    healthy, and `docker compose up -d web frontend` failed with "dependency
    failed to start". A static assertion written to match the file as it
    happened to be cannot catch that -- tests/test_compose_web_boot.py boots the
    real app from this compose file and does.

    Issue #7's localhost-only guarantee comes from the published ports being
    bound to 127.0.0.1, asserted by test_published_ports_are_bound_to_loopback.
    This variable describes whether a TLS terminator sits in front, and the
    documented office deployment has one -- which is also what lets web serve
    token auth on a non-loopback bind.

    The frontend's opposite default is deliberate, not drift: it drives the
    session cookie's Secure flag (frontend/lib/auth.ts shouldMarkCookieSecure),
    and the default compose install serves plain HTTP on loopback, where a
    Secure cookie is dropped by the browser and sign-in silently fails. It still
    detects TLS per request via x-forwarded-proto, and SEAMTECH_SECURE_COOKIES
    forces it. Setting SEAMTECH_BEHIND_TLS_PROXY explicitly gives both services
    the same value.
    """
    web = str(services["web"]["environment"]["SEAMTECH_BEHIND_TLS_PROXY"])
    assert web.endswith(":-true}"), web

    frontend = str(services["frontend"]["environment"]["SEAMTECH_BEHIND_TLS_PROXY"])
    assert frontend.endswith(":-false}"), frontend


def test_web_and_frontend_build_from_the_repository(services: dict) -> None:
    assert services["web"]["build"] in (".", "./"), services["web"]["build"]
    assert services["frontend"]["build"] == "./frontend"


def test_compose_file_parses_and_declares_expected_services(compose: dict) -> None:
    assert set(compose["services"]) >= {"postgres", "minio", "redis", "web", "frontend"}
    # A parse-only smoke check: the volumes referenced by services must exist.
    declared = set((compose.get("volumes") or {}).keys())
    for name, spec in compose["services"].items():
        if not isinstance(spec, dict):
            continue
        for mount in spec.get("volumes") or []:
            text = str(mount)
            if text.startswith("./") or text.startswith("/") or ":" not in text:
                continue
            source = text.split(":", 1)[0]
            assert source in declared, f"{name} mounts undeclared volume {source!r}"
