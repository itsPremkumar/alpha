"""Compose wiring for the free peer-network discovery beacon."""

from pathlib import Path

import pytest

COMPOSE_FILES = (
    Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yaml",
    Path(__file__).resolve().parents[2] / "docker" / "docker-compose-dev.yaml",
)


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_gateway_publishes_and_configures_peer_discovery(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "ALPHA_PEER_NETWORK_BIND_HOST" in text
    assert "ALPHA_PEER_NETWORK_DISCOVERY_PORT" in text
    assert "ALPHA_PEER_NETWORK_ADVERTISED_BASE_URL" in text
    assert "ALPHA_PEER_NETWORK_GITHUB_REPO" in text
    assert "ALPHA_PEER_NETWORK_GITHUB_TOKEN" in text
    assert "/udp" in text
    assert "${BIND_HOST:-127.0.0.1}" in text
