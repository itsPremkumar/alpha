"""Nginx routing contract for the separate Alpha peer network."""

import re
from pathlib import Path

import pytest

NGINX_FILES = (
    Path(__file__).resolve().parents[2] / "docker" / "nginx" / "nginx.conf",
    Path(__file__).resolve().parents[2] / "docker" / "nginx" / "nginx.local.conf",
)


@pytest.mark.parametrize("path", NGINX_FILES)
def test_agent_cards_and_peer_websocket_reach_gateway(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "location = /.well-known/agent-card.json" in text
    assert "location = /.well-known/agent.json" in text
    assert "location = /api/peer-network/ws" in text
    assert "proxy_set_header Upgrade $http_upgrade" in text
    assert "proxy_set_header Connection 'upgrade'" in text
    generic_api = re.search(r"\n\s+location /api/ \{", text)
    assert generic_api is None or text.index("location = /api/peer-network/ws") < generic_api.start()
