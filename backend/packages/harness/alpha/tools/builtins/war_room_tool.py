"""The ``war_room`` tool: the model-facing door onto the war room and lifecycle.

Registered in ``BUILTIN_TOOLS`` so every agent can open a war room, address
members, and drive the bot lifecycle without a second mechanism. This is the
runtime path that makes :mod:`alpha.groups.war_room`,
:mod:`alpha.groups.lifecycle_ops` and :mod:`alpha.groups.acquisition`
reachable, rather than reachable only from a test.

Every action is fail-closed: the tool returns a refusal, never a partial success
with a success-sounding payload. In particular an action whose ledger write
cannot be confirmed returns ``ok=False`` with the reason.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool("war_room", parse_docstring=True)
def war_room_tool(
    action: Literal[
        "open",
        "status",
        "hire",
        "clone",
        "rescope",
        "archive",
        "unarchive",
        "acquire",
        "revalidate",
    ],
    topic: str = "",
    room: str = "war-room",
    participants: str = "",
    objective: str = "",
    profile_name: str = "",
    source_profile: str = "",
    capabilities: str = "",
    actor_grant: str = "",
    actor: str = "alpha",
    approver: str = "",
    source_url: str = "",
    capability_name: str = "",
    quorum_policy: str = "majority",
    stage_timeout_seconds: float = 60.0,
    reason: str = "",
) -> str:
    """Open and steer a group war room, and manage the bot lifecycle behind it.

    A war room is a staged, time-boxed group deliberation: every stage is bounded
    by its own timeout plus grace, a wedged participant ends the run in a typed
    timeout rather than hanging, one member's failure never degrades a sibling,
    and an empty synthesis FAILS the run instead of being published as success.

    The lifecycle actions (hire/clone/rescope/archive) all run under a
    non-negotiable authority ceiling: a bot may create, clone, re-scope and
    archive other bots, but may not widen its own authority, edit the ceiling,
    or mint a profile exceeding it. Archiving never deletes and is reversible.

    Acquiring a capability is a fenced supply chain: untrusted by default,
    quarantined, provenance recorded, scanned, approved by somebody other than
    the requester, and never a grant of new authority.

    Args:
        action: Which operation to perform.
        topic: The deliberation topic. Required for action=open.
        room: Channel name the room lives in.
        participants: Comma-separated participant handles. Required for action=open.
        objective: The work objective, for lifecycle routing.
        profile_name: Profile name for hire/clone-target/rescope/archive/revalidate.
        source_profile: Existing profile to clone from, for action=clone.
        capabilities: Comma-separated capability names, for hire/rescope/acquire.
        actor_grant: Comma-separated capabilities the ACTOR holds. Never widens
            what a child may hold; a clone cannot exceed its creator.
        actor: Who is performing the action.
        approver: Who approved an acquisition. Must not be the requester.
        source_url: HTTPS URL on the source allowlist, for action=acquire.
        capability_name: Name of the capability to acquire.
        quorum_policy: One of all, any, majority, supermajority. Reported on the run.
        stage_timeout_seconds: Per-stage budget, plus grace.
        reason: Human-readable reason, recorded in the governance ledger.

    Returns:
        A JSON string with ``ok`` and the operation's result.
    """
    from alpha.groups.lifecycle_ops import BotLifecycle, LifecycleRefused
    from alpha.groups.war_room import build_default_config

    caps = tuple(c.strip() for c in (capabilities or "").split(",") if c.strip())
    grant = tuple(c.strip() for c in (actor_grant or "").split(",") if c.strip())
    roster = tuple(p.strip() for p in (participants or "").split(",") if p.strip())

    try:
        if action == "open":
            if not topic or len(roster) < 2:
                return json.dumps(
                    {
                        "ok": False,
                        "action": action,
                        "error": "open needs a topic and at least two comma-separated participants",
                    }
                )
            config = build_default_config(
                topic,
                roster,
                stage_timeout_seconds=float(stage_timeout_seconds),
                quorum_policy=quorum_policy,
            )

            async def _participant(ctx: Any) -> str:
                from alpha.groups.war_room import SubagentParticipant

                return await SubagentParticipant()(ctx)

            participants_impl = {name: _participant for name in roster}

            async def _moderator(ctx: Any) -> str:
                from alpha.groups.war_room import SubagentParticipant

                return await SubagentParticipant(agent_type="architect", max_turns=30)(ctx)

            from alpha.channels.ledger import read_ledger  # noqa: F401  (path sanity)
            from alpha.commands.channel_ops import runtime_root
            from alpha.groups.war_room import WarRoom

            war_room = WarRoom(
                config,
                participants=participants_impl,
                moderator=_moderator,
                room=room,
                root=runtime_root(),
            )
            run = asyncio.run(war_room.execute())
            return json.dumps({"ok": run.status == "succeeded", "action": action, "run": run.to_dict()})

        if action in {"hire", "clone", "rescope", "archive", "unarchive", "revalidate"}:
            if not profile_name:
                return json.dumps(
                    {"ok": False, "action": action, "error": f"action {action} needs profile_name"}
                )
            from alpha.bots.dynamic_profiles import DynamicProfileStore
            from alpha.bots.events import OrgEventStore
            from alpha.commands.channel_ops import runtime_root

            root = runtime_root()
            store = OrgEventStore(root / "events.jsonl")
            profiles = DynamicProfileStore(root / "dynamic_profiles.json", event_store=store)
            lifecycle = BotLifecycle(profiles, ledger_store=store, transcript_root=root / "rooms")

            if action == "hire":
                result = lifecycle.create_profile(
                    profile_name,
                    actor=actor,
                    role=topic or "specialist",
                    system_prompt=objective,
                    capabilities=caps,
                    actor_grant=grant,
                    rationale=reason,
                )
            elif action == "clone":
                result = lifecycle.clone_profile(
                    source_profile or profile_name,
                    profile_name,
                    actor=actor,
                    actor_grant=grant,
                    rationale=reason,
                )
            elif action == "rescope":
                result = lifecycle.rescope_profile(
                    profile_name, actor=actor, new_capabilities=caps, reason=reason, actor_grant=grant
                )
            elif action == "archive":
                result = lifecycle.archive_profile(profile_name, actor=actor, reason=reason)
            elif action == "unarchive":
                result = lifecycle.unarchive_profile(profile_name, actor=actor, reason=reason)
            else:
                result = lifecycle.revalidate(profile_name)
            return json.dumps({"ok": result.ok, "action": action, "result": result.to_dict()})

        if action == "acquire":
            if not source_url or not capability_name:
                return json.dumps(
                    {"ok": False, "action": action, "error": "acquire needs source_url and capability_name"}
                )
            from alpha.bots.events import OrgEventStore
            from alpha.commands.channel_ops import runtime_root
            from alpha.groups.acquisition import AcquisitionPipeline, default_fetcher

            root = runtime_root()
            store = OrgEventStore(root / "events.jsonl")
            pipeline = AcquisitionPipeline(
                root / "quarantine", ledger_store=store, fetcher=default_fetcher()
            )
            result = pipeline.acquire(
                capability_name,
                source_url,
                requested_by=actor,
                approver=approver,
                capabilities=caps,
                requester_grant=grant,
            )
            return json.dumps({"ok": result.enabled, "action": action, "result": result.to_dict()})

        return json.dumps({"ok": False, "action": action, "error": f"unknown action {action!r}"})
    except LifecycleRefused as exc:
        return json.dumps({"ok": False, "action": action, "error": str(exc), "refused": True})
    except Exception as exc:  # noqa: BLE001 - a tool must not raise into the agent loop
        logger.warning("war_room tool failed on %s: %s: %s", action, type(exc).__name__, exc)
        return json.dumps({"ok": False, "action": action, "error": f"{type(exc).__name__}: {exc}"})
