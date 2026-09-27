"""Regression test for the Docker Compose default published bind address.

``README.md`` documents Alpha as being deployed by default "in a local
trusted environment (accessible only via the 127.0.0.1 loopback interface)",
but the shipped compose files published the nginx entry as
``"${PORT:-2026}:2026"``, which Docker binds to ``0.0.0.0`` (and ``[::]``). The
shipped artifact therefore did not match its own documented default, and an
operator running it on a LAN or cloud host got a wider surface than the docs
implied without changing anything.

The Gateway itself binds ``0.0.0.0`` inside the container on purpose (nginx has
to reach it over the compose network) and its port is deliberately not
published, so the published nginx port is the whole external surface. This test
pins the loopback default there while keeping it overridable for operators who
intentionally expose the stack behind their own TLS/auth front door.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATHS = {
    "prod": REPO_ROOT / "docker" / "docker-compose.yaml",
    "dev": REPO_ROOT / "docker" / "docker-compose-dev.yaml",
    # The bundled-installer profile. It runs with ALPHA_AUTH_DISABLED=1, so a
    # published port here is an unauthenticated surface on every interface, and
    # its 5432 mapping would expose Postgres directly. It has no nginx entry,
    # so it is asserted loopback-only by the shared rule below rather than by
    # the nginx-port test.
    "installer": REPO_ROOT / "build" / "compose.installer.yaml",
    # Overlay that publishes the OpenViking memory backend. It previously used a
    # bare "${OPENVIKING_PORT:-1933}:1933", which binds 0.0.0.0 and exposed the
    # backend's HTTP surface on every interface.
    "openviking": REPO_ROOT / "docker" / "docker-compose.openviking.yaml",
}

EXPECTED_NGINX_PORT_MAPPING = "${BIND_HOST:-127.0.0.1}:${PORT:-2026}:2026"


def _published_ports(compose_path: Path) -> dict[str, list[str]]:
    """Return {service_name: [port mapping, ...]} for every published port."""
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    published: dict[str, list[str]] = {}
    for service_name, service in (compose.get("services") or {}).items():
        ports = service.get("ports") if isinstance(service, dict) else None
        if not ports:
            continue
        published[service_name] = [str(entry) for entry in ports]
    return published


@pytest.mark.parametrize("variant", sorted(COMPOSE_PATHS))
def test_nginx_entry_defaults_to_loopback(variant: str):
    """With BIND_HOST unset, the entry port must bind 127.0.0.1, not 0.0.0.0.

    The installer profile has no nginx service, so it is covered only by the
    all-interfaces rule in the next test.
    """
    published = _published_ports(COMPOSE_PATHS[variant])
    if "nginx" not in published:
        pytest.skip(f"{variant} compose publishes no nginx entry")

    assert published.get("nginx") == [EXPECTED_NGINX_PORT_MAPPING], f"{variant} compose must publish nginx as {EXPECTED_NGINX_PORT_MAPPING!r}; got: {published.get('nginx')!r}"


@pytest.mark.parametrize("variant", sorted(COMPOSE_PATHS))
def test_no_service_publishes_on_all_interfaces(variant: str):
    """No compose service may publish a port without an explicit bind address.

    A bare ``"HOST:CONTAINER"`` mapping binds every interface. Any port added
    later must either stay internal to the compose network or opt in to the
    same ``BIND_HOST`` default.
    """
    offenders: list[str] = []
    for service_name, mappings in _published_ports(COMPOSE_PATHS[variant]).items():
        for mapping in mappings:
            # A bind address is present only when the mapping has three
            # colon-separated parts (``ADDR:HOST:CONTAINER``). Variable
            # substitutions such as ``${PORT:-2026}`` also contain colons, so
            # count separators outside ``${...}`` instead of splitting naively.
            if _bind_address(mapping) is None:
                offenders.append(f"{service_name}: {mapping}")

    assert not offenders, f"{variant} compose publishes ports on all interfaces (add a bind address): {offenders}"


@pytest.mark.parametrize("variant", sorted(COMPOSE_PATHS))
def test_bind_address_remains_overridable(variant: str):
    """Operators fronting the stack themselves must be able to widen the bind."""
    published = _published_ports(COMPOSE_PATHS[variant])
    if "nginx" not in published:
        pytest.skip(f"{variant} compose publishes no nginx entry")

    mapping = published["nginx"][0]

    assert _bind_address(mapping) == "${BIND_HOST:-127.0.0.1}", f"{variant} compose must keep the bind address overridable via BIND_HOST; got: {mapping!r}"


def test_peer_network_default_is_off_in_every_compose_profile():
    """The peer plane opens an inbound listener, so no profile may enable it implicitly.

    docker-compose.yaml already documented why this defaults to 0, but
    docker-compose-dev.yaml defaulted to 1. That made the posture a developer
    exercised locally differ from the posture that ships, and the difference is
    an inbound listener rather than a feature flag.
    """
    offenders: list[str] = []
    for variant in ("prod", "dev"):
        compose = yaml.safe_load(COMPOSE_PATHS[variant].read_text(encoding="utf-8"))
        for service_name, service in (compose.get("services") or {}).items():
            environment = service.get("environment") if isinstance(service, dict) else None
            if environment is None:
                continue
            # Compose accepts `environment` as either a mapping or a list of
            # "KEY=VALUE" strings, and these files use the list form. Handling
            # only the mapping form would make this test pass vacuously.
            if isinstance(environment, dict):
                pairs = list(environment.items())
            else:
                pairs = [
                    tuple(entry.split("=", 1))  # type: ignore[misc]
                    for entry in environment
                    if isinstance(entry, str) and "=" in entry
                ]
            for key, value in pairs:
                if key == "ALPHA_PEER_NETWORK_ENABLED" and not str(value).endswith(":-0}"):
                    offenders.append(f"{variant}/{service_name}: {value}")

    assert offenders == [], f"ALPHA_PEER_NETWORK_ENABLED must default to 0 in every profile: {offenders}"


def test_dev_frontend_allows_default_loopback_origins():
    """The Docker dev frontend must hydrate on its default published hosts."""
    compose = yaml.safe_load(COMPOSE_PATHS["dev"].read_text(encoding="utf-8"))
    environment = compose["services"]["frontend"]["environment"]

    assert "ALPHA_DEV_ALLOWED_ORIGINS=${ALPHA_DEV_ALLOWED_ORIGINS:-127.0.0.1,::1}" in environment


def _bind_address(mapping: str) -> str | None:
    """Return the bind-address segment of a compose port mapping, if any.

    Splits on ``:`` at nesting depth zero so ``${PORT:-2026}`` is treated as a
    single segment rather than two.
    """
    segments: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(mapping):
        char = mapping[index]
        if mapping.startswith("${", index):
            depth += 1
            current.append("${")
            index += 2
            continue
        if char == "}" and depth > 0:
            depth -= 1
        elif char == ":" and depth == 0:
            segments.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    segments.append("".join(current))

    # ADDR:HOST:CONTAINER -> bound; HOST:CONTAINER or CONTAINER -> unbound.
    return segments[0] if len(segments) >= 3 else None


def test_dev_compose_env_files_are_optional():
    """Missing .env files must not fail `docker compose -f docker/docker-compose-dev.yaml`."""
    compose = yaml.safe_load(COMPOSE_PATHS["dev"].read_text(encoding="utf-8"))
    expected = {
        "provisioner": "../.env",
        "frontend": "../frontend/.env",
        "gateway": "../.env",
    }
    for service_name, path in expected.items():
        entries = compose["services"][service_name]["env_file"]
        assert entries == [{"path": path, "required": False}], f"{service_name} env_file must be optional; got: {entries!r}"
