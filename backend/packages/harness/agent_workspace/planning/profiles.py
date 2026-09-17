"""Installer bridge: autonomous-planner profile specs -> runnable subagent definitions.

Fail-closed by design: specs install as *disabled* managed definitions unless
their name was explicitly approved. Existing definitions are never overwritten —
an install over a live name reports ``already_exists`` and leaves it untouched.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from agent_workspace.persistence.managed_subagents.base import (
    ManagedSubagentDefinition,
    ManagedSubagentExistsError,
)

_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9-]+$")


def sanitize_profile_name(name: str) -> str:
    """Normalize a planner profile name to the managed-store natural key."""
    cleaned = re.sub(r"[^A-Za-z0-9-]+", "-", (name or "").strip().lower()).strip("-")
    if not cleaned or not _PROFILE_NAME_RE.fullmatch(cleaned):
        raise ValueError(f"Cannot derive a valid profile name from {name!r}")
    return cleaned


def profile_spec_to_system_prompt(spec: Any) -> str:
    """Render a NewProfileSpec outline into a worker system prompt."""
    outline = list(getattr(spec, "system_prompt_outline", []) or [])
    acceptance = list(getattr(spec, "acceptance_criteria", []) or getattr(spec, "skills", []) or [])
    lines = [
        f"You are {getattr(spec, 'name', 'specialist')}: {getattr(spec, 'description', '')}".strip(),
        "",
        "## Operating rules",
        "- Own only the delegated scope; never touch sibling scopes or shared state.",
        "- Verify each claim against artifacts or primary evidence; cite tool receipts for action claims.",
        "- Destructive or externally visible actions need explicit approval first.",
    ]
    if outline:
        lines += ["", "## Assignment"] + [f"- {line}" for line in outline]
    if acceptance:
        lines += ["", "## Done means"] + [f"- {line}" for line in acceptance[:4]]
    return "\n".join(lines)


def profile_spec_to_managed_definition(spec: Any, *, enabled: bool = False) -> ManagedSubagentDefinition:
    """Convert a NewProfileSpec into a managed-store definition (disabled by default)."""
    return ManagedSubagentDefinition(
        name=sanitize_profile_name(str(getattr(spec, "name", ""))),
        description=str(getattr(spec, "description", "")).strip(),
        system_prompt=profile_spec_to_system_prompt(spec),
        tools=list(getattr(spec, "tools", None) or []) or None,
        disallowed_tools=list(getattr(spec, "disallowed_tools", None) or []),
        skills=list(getattr(spec, "skills", None) or []) or None,
        model=str(getattr(spec, "model", "inherit") or "inherit"),
        max_turns=int(getattr(spec, "max_turns", 50) or 50),
        timeout_seconds=int(getattr(spec, "timeout_seconds", 900) or 900),
        enabled=bool(enabled),
    )


def profile_spec_to_subagent_config(spec: Any) -> Any:
    """Convert a NewProfileSpec into an in-memory SubagentConfig (no persistence)."""
    from agent_workspace.subagents.config import SubagentConfig

    return SubagentConfig(
        name=sanitize_profile_name(str(getattr(spec, "name", ""))),
        description=str(getattr(spec, "description", "")).strip(),
        system_prompt=profile_spec_to_system_prompt(spec),
        tools=list(getattr(spec, "tools", None) or []) or None,
        disallowed_tools=list(getattr(spec, "disallowed_tools", None) or ["task"]),
        skills=list(getattr(spec, "skills", None) or []) or None,
        model=str(getattr(spec, "model", "inherit") or "inherit"),
        max_turns=int(getattr(spec, "max_turns", 50) or 50),
        timeout_seconds=int(getattr(spec, "timeout_seconds", 900) or 900),
    )


def install_profiles(
    specs: Sequence[Any],
    *,
    store: Any = None,
    enabled: bool = False,
    approve: Sequence[str] = (),
) -> dict[str, list[dict[str, Any]]]:
    """Install profile specs into a managed-subagent store (fail-closed).

    - ``store=None`` is a dry run: every spec reports ``pending_approval``.
    - Otherwise each spec is created *disabled* unless ``enabled`` is True or
      its name appears in ``approve``.
    - Names that already exist report ``already_exists``; nothing is overwritten.
    - Per-spec failures are isolated into ``errors``; one bad spec never
      blocks the rest.
    """
    approved = {sanitize_profile_name(str(n)) for n in (approve or [])}
    report: dict[str, list[dict[str, Any]]] = {
        "installed": [],
        "already_exists": [],
        "pending_approval": [],
        "errors": [],
    }
    for spec in specs or []:
        try:
            name = sanitize_profile_name(str(getattr(spec, "name", "")))
        except ValueError as exc:
            report["errors"].append({"name": str(getattr(spec, "name", "?")), "error": str(exc)})
            continue
        if store is None:
            report["pending_approval"].append({"name": name, "enabled": False})
            continue
        try:
            definition = profile_spec_to_managed_definition(spec, enabled=bool(enabled) or name in approved)
        except Exception as exc:  # noqa: BLE001 — per-spec isolation
            report["errors"].append({"name": name, "error": str(exc)})
            continue
        try:
            store.create(definition)
        except ManagedSubagentExistsError:
            report["already_exists"].append({"name": name})
            continue
        except Exception as exc:  # noqa: BLE001 — per-spec isolation
            report["errors"].append({"name": name, "error": str(exc)})
            continue
        if definition.enabled:
            report["installed"].append({"name": name, "enabled": True})
        else:
            report["pending_approval"].append({"name": name, "enabled": False})
    return report
