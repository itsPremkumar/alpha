"""D1 dynamic MCP lifecycle: skill-embedded mount/unmount + no-restart gateway config.

Everything is mocked end to end: no MCP server process is spawned, no network
call is made. Two seams are proven:

1. ``SkillMcpLifecycleManager``: a SKILL.md declaring four servers
   (GitHub / PostgreSQL / Docker / Slack) mounts them on acquire and unmounts
   them on release; parsing alone mounts nothing; release is idempotent.
2. The gateway config handlers (``app.gateway.routers.mcp``) add / disable /
   delete servers by rewriting ``extensions_config.json`` IN-PROCESS and
   calling ``reset_mcp_tools_cache`` after each mutation -- the reload path
   that avoids a Gateway restart. The cache reset is spied (record-only), the
   admin check is a no-op, and the stdio command allowlist rejection is pinned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from alpha.skills.mcp_lifecycle import SkillMcpLifecycleManager
from app.gateway.routers import mcp as mcp_router
from app.gateway.routers.mcp import (
    McpConfigUpdateRequest,
    McpServerConfigResponse,
    McpServerStateUpdateRequest,
    create_mcp_servers,
    delete_mcp_server,
    get_mcp_configuration,
    update_mcp_server_state,
)

# Fixture specs only: these commands/packages are NEVER executed by these tests
# (no spawn, no network). They exist to satisfy the API boundary's stdio
# command allowlist (npx/uvx) and the exec-argument screen.
SKILL_WITH_FOUR_SERVERS = """---
name: infra-ops
description: Mounts the four infrastructure MCP servers on demand.
mcp-servers:
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
  postgres:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-postgres"]
  docker:
    command: uvx
    args: ["mcp-server-docker"]
  slack:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-slack"]
---

# Infra Ops
Uses the embedded MCP servers while this skill is active.
"""

_FOUR = ("docker", "github", "postgres", "slack")


def _body() -> McpConfigUpdateRequest:
    """Fresh request body with the four mocked servers (stdio, allowlisted)."""
    return McpConfigUpdateRequest(
        mcp_servers={
            "github": McpServerConfigResponse(command="npx", args=["-y", "@modelcontextprotocol/server-github"], description="GitHub MCP server (mocked fixture)."),
            "postgres": McpServerConfigResponse(command="npx", args=["-y", "@modelcontextprotocol/server-postgres"], description="PostgreSQL MCP server (mocked fixture)."),
            "docker": McpServerConfigResponse(command="uvx", args=["mcp-server-docker"], description="Docker MCP server (mocked fixture)."),
            "slack": McpServerConfigResponse(command="npx", args=["-y", "@modelcontextprotocol/server-slack"], description="Slack MCP server (mocked fixture)."),
        }
    )


@pytest.fixture()
def gateway_config(tmp_path, monkeypatch) -> tuple[Path, list[str]]:
    """Point the gateway at a seeded temp extensions config; mock admin + cache reset."""
    config_path = tmp_path / "extensions_config.json"
    config_path.write_text(json.dumps({"mcpServers": {}, "skills": {}}), encoding="utf-8")
    monkeypatch.setenv("AGENT_WORKSPACE_EXTENSIONS_CONFIG_PATH", str(config_path))

    async def _noop_admin(_request, **_kwargs) -> None:
        return None

    monkeypatch.setattr(mcp_router, "require_admin_user", _noop_admin)

    # Record-only spy: proves each mutation schedules an in-process tool
    # reload (the no-restart path) without touching the real cache machinery.
    resets: list[str] = []
    monkeypatch.setattr(mcp_router, "reset_mcp_tools_cache", lambda: resets.append("reset"))
    return config_path, resets


def _read_disk(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Seam 1 -- skill-embedded lifecycle manager (pure bookkeeping, no spawn)
# ---------------------------------------------------------------------------


def test_skill_embedded_servers_mount_and_unmount() -> None:
    manager = SkillMcpLifecycleManager()

    specs = manager.parse_skill_mcp_specs(SKILL_WITH_FOUR_SERVERS)
    assert {spec.server_name for spec in specs} == set(_FOUR)
    # Parsing alone must mount nothing.
    assert manager.get_active_servers() == []

    acquired = manager.acquire_for_skill("infra-ops", SKILL_WITH_FOUR_SERVERS)
    assert sorted(acquired) == list(_FOUR)
    assert sorted(manager.get_active_servers()) == list(_FOUR)

    released = manager.release_for_skill("infra-ops")
    assert sorted(released) == list(_FOUR)
    assert manager.get_active_servers() == []
    # Unmount is idempotent: releasing again reports nothing.
    assert manager.release_for_skill("infra-ops") == []


# ---------------------------------------------------------------------------
# Seam 2 -- gateway config mount / disable / delete without a restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_mount_unmount_reloads_cache_in_process(gateway_config) -> None:
    config_path, resets = gateway_config

    # Mount all four servers.
    created = await create_mcp_servers(request=None, body=_body())
    assert sorted(created.mcp_servers) == list(_FOUR)
    assert resets == ["reset"]  # cache invalidated in THIS process

    on_disk = _read_disk(config_path)
    assert sorted(on_disk["mcpServers"]) == list(_FOUR)
    assert on_disk["skills"] == {}

    # Same process, no restart: the live config view reflects the mount.
    live = await get_mcp_configuration(request=None)
    assert sorted(live.mcp_servers) == list(_FOUR)

    # Disable (soft unmount) one server.
    patched = await update_mcp_server_state(request=None, body=McpServerStateUpdateRequest(server_name="slack", enabled=False))
    assert patched.mcp_servers["slack"].enabled is False
    assert resets == ["reset", "reset"]
    assert _read_disk(config_path)["mcpServers"]["slack"]["enabled"] is False

    # Re-enabling runs the execution-policy validation again and stays mocked-safe.
    reenabled = await update_mcp_server_state(request=None, body=McpServerStateUpdateRequest(server_name="slack", enabled=True))
    assert reenabled.mcp_servers["slack"].enabled is True
    assert resets == ["reset", "reset", "reset"]

    # Delete (hard unmount) one server.
    remaining = await delete_mcp_server(request=None, server_name="github")
    assert "github" not in remaining.mcp_servers
    assert sorted(remaining.mcp_servers) == ["docker", "postgres", "slack"]
    assert resets == ["reset", "reset", "reset", "reset"]
    assert sorted(_read_disk(config_path)["mcpServers"]) == ["docker", "postgres", "slack"]

    # Duplicate mount is rejected atomically: 409, no write, no extra reset.
    before = config_path.read_text(encoding="utf-8")
    with pytest.raises(HTTPException) as exc_info:
        await create_mcp_servers(request=None, body=_body())
    assert exc_info.value.status_code == 409
    assert "already exists" in str(exc_info.value.detail)
    assert resets == ["reset", "reset", "reset", "reset"]
    assert config_path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_gateway_rejects_disallowed_stdio_command_without_writing(gateway_config) -> None:
    config_path, resets = gateway_config
    before = config_path.read_text(encoding="utf-8")

    rogue = McpConfigUpdateRequest(
        mcp_servers={
            "rogue": McpServerConfigResponse(
                command="python",
                args=["-c", "print(1)"],
                description="Must be rejected by the stdio command allowlist.",
            )
        }
    )
    with pytest.raises(HTTPException) as exc_info:
        await create_mcp_servers(request=None, body=rogue)

    assert exc_info.value.status_code == 400
    assert "disallowed stdio command" in str(exc_info.value.detail)
    # Rejected before any persistence or cache reset: file untouched, no reload.
    assert resets == []
    assert config_path.read_text(encoding="utf-8") == before
