"""Server-owned runtime/config probes for autonomy readiness.

The model cannot supply these facts. The collector reads only the injected
runtime context and non-secret server configuration; it never reads credential
values, calls a provider, or accesses the network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alpha.config import get_app_config
from alpha.tools.types import Runtime


def _provider_name(use: str) -> str:
    value = str(use or "").casefold()
    known = {
        "LocalSandboxProvider": "local",
        "AioSandboxProvider": "a2a",
        "BoxliteProvider": "boxlite",
        "E2BSandboxProvider": "e2b",
        "OpenSandboxProvider": "open_sandbox",
        "DockerSandboxProvider": "docker",
    }
    for marker, name in known.items():
        if marker.casefold() in value:
            return name
    return value.rsplit(":", 1)[-1] or "unknown"


def _public_skill_count(skills_path: Path) -> int:
    public_root = skills_path / "public"
    if not public_root.is_dir():
        return 0
    return sum(1 for path in public_root.glob("*/SKILL.md") if path.is_file())


def collect_server_readiness(runtime: Runtime) -> tuple[dict[str, Any], list[str]]:
    """Collect non-secret readiness facts from runtime and server config."""

    context = getattr(runtime, "context", None)
    runtime_context = dict(context) if isinstance(context, dict) else {}
    facts: dict[str, Any] = {
        "current_model_observed": True,
        "tool_execution_observed": True,
    }
    diagnostics: list[str] = []
    model_name = runtime_context.get("model_name")
    if isinstance(model_name, str) and model_name:
        facts["model_name"] = model_name

    try:
        config = get_app_config()
    except Exception as exc:
        diagnostics.append(f"config_unavailable:{type(exc).__name__}")
        return facts, diagnostics

    if not facts.get("model_name") and getattr(config, "models", None):
        facts["model_name"] = config.models[0].name
    model_config = None
    if facts.get("model_name"):
        try:
            model_config = config.get_model_config(facts["model_name"])
        except Exception as exc:
            diagnostics.append(f"model_probe_failed:{type(exc).__name__}")
    facts["model_available"] = model_config is not None
    if model_config is not None:
        facts["model_supports_vision"] = model_config.supports_vision is True

    sandbox = getattr(config, "sandbox", None)
    if sandbox is not None:
        facts["sandbox_provider"] = _provider_name(getattr(sandbox, "use", ""))
        network = getattr(sandbox, "network", None)
        if network is not None and getattr(network, "mode", None):
            facts["network_policy"] = str(network.mode)

    extensions = getattr(config, "extensions", None)
    if extensions is not None:
        try:
            facts["enabled_mcp_servers"] = sorted(extensions.get_enabled_mcp_servers())
        except Exception as exc:
            diagnostics.append(f"mcp_probe_failed:{type(exc).__name__}")

    skills = getattr(config, "skills", None)
    if skills is not None:
        try:
            facts["public_skill_count"] = _public_skill_count(skills.get_skills_path())
        except Exception as exc:
            diagnostics.append(f"skill_probe_failed:{type(exc).__name__}")
    return facts, diagnostics


__all__ = ["collect_server_readiness"]
