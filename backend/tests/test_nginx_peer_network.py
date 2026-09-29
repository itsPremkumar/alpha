"""Nginx routing contract for the separate Alpha peer network.

Alpha-to-Alpha has three public nginx locations:

* ``= /.well-known/agent-card.json`` and ``= /.well-known/agent.json`` -- the
  Agent Card a remote peer fetches to decide whether this installation exists
  and what it can do;
* ``= /api/peer-network/ws`` -- the direct transport, a WebSocket upgrade.

Both failure modes are silent. A card that falls through to ``location /`` is
answered by Next.js, which has no such route, so a peer concludes the node does
not exist. A WebSocket that falls into the generic ``/api/`` block is proxied
as plain HTTP, because that block forwards no ``Upgrade``/``Connection``.

This test originally parametrised over the two ``docker/nginx`` files only,
while ``test_nginx_voice_websocket.py`` correctly included the Helm ConfigMap.
That asymmetry is precisely how the Helm ConfigMap shipped with none of these
three locations: the peer plane was dead in every Kubernetes install and the
suite was green, because a guard that enumerates *some* of the surfaces reads
as coverage. All three surfaces are therefore listed here, and
``test_deploy_surface_parity.py`` independently fails if a fourth nginx surface
is added without a decision about its routing.

``test_deploy_surface_parity.py`` owns the general case (every Gateway
WebSocket, every shared route). The assertions here stay focused on the
peer-specific contract so a regression names peer network rather than "a
routing-table entry".
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every nginx surface that fronts the Gateway. Keep in step with
#: ``NGINX_SURFACES`` in ``test_deploy_surface_parity.py``; that file asserts
#: no nginx config is missing from this enumeration.
NGINX_FILES = (
    REPO_ROOT / "docker" / "nginx" / "nginx.conf",
    REPO_ROOT / "docker" / "nginx" / "nginx.local.conf",
    REPO_ROOT / "deploy" / "helm" / "alpha" / "templates" / "configmap-nginx.yaml",
)


def _location_block(config: str, marker: str) -> str:
    """Return the brace-balanced block that starts at ``marker``."""
    start = config.index(marker)
    opening = config.index("{", start)
    depth = 0
    for index in range(opening, len(config)):
        if config[index] == "{":
            depth += 1
        elif config[index] == "}":
            depth -= 1
            if depth == 0:
                return config[opening : index + 1]
    raise AssertionError(f"unterminated nginx location block: {marker}")


@pytest.mark.parametrize("path", NGINX_FILES, ids=lambda path: path.name)
def test_agent_cards_and_peer_websocket_reach_gateway(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "location = /.well-known/agent-card.json" in text
    assert "location = /.well-known/agent.json" in text
    assert "location = /api/peer-network/ws" in text
    assert "proxy_set_header Upgrade $http_upgrade" in text
    assert "proxy_set_header Connection 'upgrade'" in text
    generic_api = re.search(r"\n\s+location /api/ \{", text)
    assert generic_api is None or text.index("location = /api/peer-network/ws") < generic_api.start()


@pytest.mark.parametrize("path", NGINX_FILES, ids=lambda path: path.name)
def test_the_agent_cards_are_exact_matches_pointing_at_the_gateway(path: Path) -> None:
    """A prefix or regex card location would also swallow unrelated paths.

    The two card paths are exact (``= ``) matches in every surface, and both
    proxy to the Gateway: the Gateway serves them from ``routers/peer_network``
    on its ``public_router`` and nothing in Next.js does, so a card answered by
    the frontend is a 404 the peer reads as "no such node".
    """
    text = path.read_text(encoding="utf-8")

    for marker in ("location = /.well-known/agent-card.json", "location = /.well-known/agent.json"):
        assert marker in text, f"{path.name} is missing `{marker}`; an installing peer cannot discover this node"
        block = _location_block(text, marker)
        assert "gateway" in block, f"{path.name}: `{marker}` must proxy to the Gateway upstream, not the frontend"


@pytest.mark.parametrize("path", NGINX_FILES, ids=lambda path: path.name)
def test_the_peer_websocket_forwards_the_upgrade_and_keeps_the_socket_open(path: Path) -> None:
    """The transport is a WebSocket, so the handshake must survive the proxy.

    A missing ``Upgrade``/``Connection`` pair downgrades the socket to plain
    HTTP, and a missing read timeout closes an idle peer connection on nginx's
    60-second default long before the peer protocol's own cadence expects.
    """
    text = path.read_text(encoding="utf-8")
    block = _location_block(text, "location = /api/peer-network/ws")

    for directive in (
        "proxy_http_version 1.1;",
        "proxy_set_header Upgrade $http_upgrade;",
        "proxy_set_header Connection 'upgrade';",
        "proxy_read_timeout 600s;",
        "proxy_buffering off;",
    ):
        assert directive in block, f"{path.name}: `location = /api/peer-network/ws` is missing `{directive}`; copy the block from docker/nginx/nginx.conf"
