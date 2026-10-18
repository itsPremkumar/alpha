"""The single ``bot_mode`` contract: what a bot may do right now (item 2.1).

"Bot Mode" previously meant three unrelated things: a roster/persona engine, a
bot-scoped chat convention, and a mod controller. This module is the ONE
resolver for the run-scoping contract:

``BotModeContext``
    Capabilities (ceiling-intersected), toolsets, skills, MCP servers, model,
    memory scope, approval mode and inbox policy — plus per-field provenance
    and an ``enforcement`` disclosure.

Resolution is **server-side only**. A client may request an ``assistant_id``
when it creates a thread or a run; every scoping field is then read from the
roster profile and the server-owned authority ceiling — never from a request
payload. A thread/run that does not resolve to a roster bot has NO bot mode
(``resolve_bot_mode`` returns ``None``).

Honesty rules baked into the shape:

* ``capabilities`` is the ceiling-intersected grant a dispatcher may act on;
  whatever the ceiling removed is reported in ``denied_capabilities`` instead
  of silently vanishing.
* ``enforcement`` names, per domain, where the contract bites today. A field
  that is declared but not yet enforced at run time says so — the endpoint
  must not imply a restriction that does not exist.
* Every scoping field carries its layer in ``provenance``, so "why is this
  value here" is a lookup, not an inference.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Aliases a server-owned carrier may name the active bot under. These match
#: ``resolve_runtime_bot_name`` (``alpha.bots.dm``) so this resolver never
#: disagrees with the middleware about who a run belongs to.
BOT_NAME_KEYS: tuple[str, ...] = ("bot_name", "botName", "bot")

#: The key a client may legitimately set when it creates a thread or a run;
#: the server re-homes a roster name onto the bot-name carriers during
#: assembly (``_clear_dangling_agent_identity``).
ASSISTANT_ID_KEYS: tuple[str, ...] = ("assistant_id", "assistantId")

#: Documented defaults for the two policy fields that have no per-profile
#: column yet. A profile may override them through ``metadata``.
DEFAULT_APPROVAL_MODE = "default"
DEFAULT_INBOX_POLICY = "async"

#: Where each scoping domain actually bites today. A declared-but-unenforced
#: field says so here rather than implying a run-time filter that does not
#: exist — the honesty rule this repo applies to every status surface.
BOT_MODE_ENFORCEMENT: Mapping[str, str] = {
    "capabilities": "dispatch admission re-validates the live ceiling (authorized_bot); the grant gates work assignment",
    "toolsets": "declared only — routing/selection read it; no run-time tool filter is wired to it yet",
    "skills": "declared only — routing/selection read it; no run-time skill filter is wired to it yet",
    "mcp_servers": "declared only — per-bot MCP process isolation is an explicit non-goal",
    "model": "enforced — the lead-agent assembly resolves the same model plan",
    "memory_scope": "declared only — memory_namespace() is the intended namespace; no memory consumer keys by it yet",
    "approval_mode": "declared only — per-bot approval overrides are not consumed yet",
    "inbox_policy": "declared only — delivery is async today; the field names the contract, it is not yet a switch",
}


@dataclass(frozen=True)
class BotModeContext:
    """One bot's effective contract, resolved server-side.

    ``capabilities`` is the effective (ceiling-intersected) grant;
    ``requested_capabilities`` is what the profile stores. They differ exactly
    by ``denied_capabilities`` when an operator has tightened the ceiling after
    the profile was created — the demotion path is disclosed, not silent.
    """

    bot_name: str
    status: str
    thread_id: str | None
    run_id: str | None
    requested_capabilities: tuple[str, ...]
    capabilities: tuple[str, ...]
    denied_capabilities: tuple[str, ...]
    toolsets: tuple[str, ...]
    skills: tuple[str, ...]
    mcp_servers: tuple[str, ...]
    model: str | None
    memory_scope: str
    approval_mode: str
    inbox_policy: str
    epoch: str = ""
    provenance: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bot_name": self.bot_name,
            "status": self.status,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "requested_capabilities": list(self.requested_capabilities),
            "capabilities": list(self.capabilities),
            "denied_capabilities": list(self.denied_capabilities),
            "toolsets": list(self.toolsets),
            "skills": list(self.skills),
            "mcp_servers": list(self.mcp_servers),
            "model": self.model,
            "memory_scope": self.memory_scope,
            "approval_mode": self.approval_mode,
            "inbox_policy": self.inbox_policy,
            "epoch": self.epoch,
            "provenance": dict(self.provenance),
            "enforcement": dict(BOT_MODE_ENFORCEMENT),
        }


def _registry_or_default(registry: Any | None) -> Any:
    if registry is not None:
        return registry
    from alpha.bots.registry import get_bot_registry

    return get_bot_registry()


def resolve_roster_bot(name: str | None, *, registry: Any | None = None) -> Any | None:
    """Return the roster profile *name* refers to, or ``None``.

    Advisory on every run path: an empty name, an unreadable roster or a
    failed lookup degrade to ``None`` (logged) — never an exception. This is
    the ONE roster lookup for run-time identity resolution: the lead agent's
    ``_roster_bot_name`` / ``_resolve_bot_profile`` delegate here so a bot
    either resolves for every consumer or for none.
    """
    if not name or not str(name).strip():
        return None
    try:
        return _registry_or_default(registry).get_bot(str(name))
    except Exception:  # noqa: BLE001 — roster is advisory on this path
        logger.warning("Roster lookup for %r failed; bot context is unavailable on this path.", name, exc_info=True)
        return None


def _mapping_str(mapping: Mapping[str, Any] | None, keys: tuple[str, ...]) -> str | None:
    if not isinstance(mapping, Mapping):
        return None
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _metadata_policy(metadata: Mapping[str, Any], key: str, default: str) -> tuple[str, bool]:
    value = metadata.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip().lower(), True
    return default, False


def _profile_model(profile: Any) -> tuple[str | None, str]:
    """The model this bot's runs actually resolve to, with its source label.

    Uses the same plan resolver the lead-agent assembly uses, so the endpoint
    and the run cannot disagree; degrades to the raw ``model`` field when the
    plan read fails.
    """
    try:
        from alpha.bots.model_config import resolve_model_plan

        plan = resolve_model_plan(bot=profile)
        return plan.primary, plan.primary_source
    except Exception:  # noqa: BLE001 — advisory: report the raw field instead
        logger.debug("Bot mode: model plan resolution failed; reporting the raw profile model.", exc_info=True)
        value = getattr(profile, "model", None)
        return (str(value) if value else None), "bot.model"


def resolve_bot_mode(
    thread_metadata: Mapping[str, Any] | None = None,
    runtime_context: Mapping[str, Any] | None = None,
    registry: Any | None = None,
    *,
    bot_name: str | None = None,
) -> BotModeContext | None:
    """Resolve the effective bot-mode contract, or ``None`` for a non-bot thread.

    Identity precedence: explicit *bot_name* (a server-validated path
    parameter) > ``runtime_context`` (the live run's carriers) >
    ``thread_metadata`` (``bot_name``, then the ``assistant_id`` a client may
    legitimately set). Server-side only: no scoping field is ever read from a
    request payload; capabilities come from the roster profile intersected
    with the server-owned ceiling.

    Never raises: a run/thread without a roster bot has no bot mode, and an
    unreadable ceiling narrows (``narrow_to_ceiling``'s tolerant path) rather
    than failing the resolution.
    """
    candidate = (str(bot_name).strip() if bot_name else "") or _mapping_str(runtime_context, BOT_NAME_KEYS) or _mapping_str(thread_metadata, BOT_NAME_KEYS) or _mapping_str(thread_metadata, ASSISTANT_ID_KEYS)
    profile = resolve_roster_bot(candidate, registry=registry)
    if profile is None:
        return None

    from alpha.bots.authority_ceiling import narrow_to_ceiling

    try:
        ceiling = _registry_or_default(registry).ceiling()
    except Exception:  # noqa: BLE001 — an unreadable ceiling must not fail the read
        ceiling = None

    requested = tuple(sorted(str(c) for c in (getattr(profile, "capabilities", None) or [])))
    kept, denied = narrow_to_ceiling(requested, subject=f"bot:{profile.name}", ceiling=ceiling)

    model, model_source = _profile_model(profile)

    declared_scope = getattr(profile, "memory_scope", None)
    memory_scope = str(declared_scope).strip().lower() if declared_scope else str(profile.name).strip().lower()

    metadata = getattr(profile, "metadata", None)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    approval_mode, approval_from_metadata = _metadata_policy(metadata, "approval_mode", DEFAULT_APPROVAL_MODE)
    inbox_policy, inbox_from_metadata = _metadata_policy(metadata, "inbox_policy", DEFAULT_INBOX_POLICY)

    try:
        epoch = profile.capability_fingerprint()
    except Exception:  # noqa: BLE001 — the epoch is advisory on the read path
        logger.debug("Bot mode: capability fingerprint failed for %r.", profile.name, exc_info=True)
        epoch = ""

    provenance = {
        "capabilities": "bot_profile ∩ authority_ceiling",
        "toolsets": "bot_profile",
        "skills": "bot_profile",
        "mcp_servers": "bot_profile",
        "model": model_source if model else "default — the global default model applies at assembly",
        "memory_scope": "bot_profile" if declared_scope else "default — the bot's own name",
        "approval_mode": "profile_metadata" if approval_from_metadata else "default — no per-bot override yet",
        "inbox_policy": "profile_metadata" if inbox_from_metadata else "default — async delivery",
    }

    return BotModeContext(
        bot_name=str(profile.name),
        status=str(getattr(profile, "status", "active") or "active"),
        thread_id=_mapping_str(runtime_context, ("thread_id",)),
        run_id=_mapping_str(runtime_context, ("run_id",)),
        requested_capabilities=requested,
        capabilities=tuple(sorted(kept)),
        denied_capabilities=tuple(sorted(denied)),
        toolsets=tuple(str(t) for t in (getattr(profile, "toolsets", None) or [])),
        skills=tuple(str(s) for s in (getattr(profile, "skills", None) or [])),
        mcp_servers=tuple(str(m) for m in (getattr(profile, "mcp_servers", None) or [])),
        model=model,
        memory_scope=memory_scope,
        approval_mode=approval_mode,
        inbox_policy=inbox_policy,
        epoch=epoch,
        provenance=provenance,
    )
