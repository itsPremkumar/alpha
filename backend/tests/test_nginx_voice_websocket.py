"""The public nginx entry points must preserve the voice WebSocket upgrade."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS = (
    REPO_ROOT / "docker/nginx/nginx.conf",
    REPO_ROOT / "docker/nginx/nginx.local.conf",
    REPO_ROOT / "deploy/helm/agent-workspace/templates/configmap-nginx.yaml",
)


def _location_block(config: str, marker: str) -> str:
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


@pytest.mark.parametrize("path", CONFIGS, ids=lambda path: path.name)
def test_voice_websocket_location_forwards_upgrade_headers(path: Path):
    config = path.read_text(encoding="utf-8")
    marker = "location = /api/multimodal/voice"
    block = _location_block(config, marker)

    assert "proxy_http_version 1.1;" in block
    assert "proxy_set_header Upgrade $http_upgrade;" in block
    assert "proxy_set_header Connection 'upgrade';" in block
    assert "proxy_read_timeout 600s;" in block
    assert "proxy_buffering off;" in block


@pytest.mark.parametrize("path", CONFIGS, ids=lambda path: path.name)
def test_voice_websocket_precedes_generic_api_catch_all(path: Path):
    config = path.read_text(encoding="utf-8")
    voice = config.index("location = /api/multimodal/voice")
    generic = min(index for marker in ("location ~ ^/api/threads", "location /api/") if (index := config.find(marker, voice)) >= 0)
    assert voice < generic


def test_helm_also_preserves_live_browser_websocket_upgrade():
    config = CONFIGS[-1].read_text(encoding="utf-8")
    block = _location_block(config, "location ~ ^/api/threads/[^/]+/browser/stream")
    assert "proxy_set_header Upgrade $http_upgrade;" in block
    assert "proxy_set_header Connection 'upgrade';" in block
