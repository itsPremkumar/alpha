"""The ``war_room`` tool: the model-facing door onto the war room and lifecycle.

Registered in ``BUILTIN_TOOLS`` so every agent can open a war room, inspect the
runs it has already produced, and drive the bot lifecycle without a second
mechanism. This is the runtime path that makes :mod:`alpha.groups.war_room`,
:mod:`alpha.groups.lifecycle_ops`, :mod:`alpha.groups.acquisition` and
:mod:`alpha.groups.trigger` reachable, rather than reachable only from a test.

Every action is fail-closed: the tool returns a refusal, never a partial success
with a success-sounding payload. In particular an action whose ledger write
cannot be confirmed returns ``ok=False`` with the reason.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any, Literal

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _run_off_loop(factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Run a coroutine whether or not an event loop is already running.

    ``asyncio.run`` raises ``RuntimeError`` when it is called from inside a
    running loop, and this tool is invoked from the agent loop. The swarm tool
    solves the identical problem with a daemon-thread fallback; the war room
    reuses that exact shape so the two tools cannot drift apart on it.

    The worker thread re-raises on the calling thread rather than swallowing, so
    the caller's ``except`` below still sees a real traceback.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["value"] = asyncio.run(factory())
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller
            box["error"] = exc

    thread = threading.Thread(target=_worker, name="war-room-runner", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


@tool("war_room", parse_docstring=True)
def war_room_tool(
    action: Literal[
        "open",
        "status",
        "evaluate",
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
    run_id: str = "",
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
    transcript_tail: int = 20,
    strategy: str = "",
    debate_rounds: int = 2,
    trigger_enabled: bool = False,
    reason: str = "",
) -> str:
    """Open and steer a group war room, and manage the bot lifecycle behind it.

    A war room is a staged, time-boxed group deliberation: every stage is bounded
    by its own timeout plus grace, a wedged participant ends the run in a typed
    timeout rather than hanging, one member's failure never degrades a sibling,
    and an empty synthesis FAILS the run instead of being published as success.

    Agreement is measured over the CLAIMS each member states, not over whether it
    returned text, the minority view is preserved verbatim, and a contribution
    matching an injection heuristic is redacted before it re-enters another
    participant's prompt and blocks an unqualified success.

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
        run_id: A specific persisted run. Required by action=status to read one
            run; omit it to list every run in the room.
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
        transcript_tail: How many trailing transcript messages action=status
            returns for a single run.
        strategy: Deliberation strategy shaping the room's stages. One of auto,
            single, ensemble, council, debate, peer_review, moa, judge,
            research_council, red_team, expert_panel. Leave empty for the fixed
            three-stage room, which is recorded as "legacy_fixed" rather than
            attributed to any strategy. "auto" asks the deliberation router and
            records its rationale on the run.
        debate_rounds: Adversarial rounds when strategy is debate.
        trigger_enabled: For action=evaluate, simulate the auto-trigger with
            auto-triggering switched ON, so a caller can see what it would decide
            without changing any stored policy.
        reason: Human-readable reason, recorded in the governance ledger.

    Returns:
        A JSON string with ``ok`` and the operation's result.
    """
    from alpha.groups.lifecycle_ops import LifecycleRefused
    from alpha.groups.war_room import build_default_config

    caps = tuple(c.strip() for c in (capabilities or "").split(",") if c.strip())
    grant = tuple(c.strip() for c in (actor_grant or "").split(",") if c.strip())
    roster = tuple(p.strip() for p in (participants or "").split(",") if p.strip())

    try:
        # ------------------------------------------------------------------
        # read-only: status
        # ------------------------------------------------------------------
        if action == "status":
            from alpha.bots.kill_switch import get_kill_switch_status
            from alpha.commands.channel_ops import runtime_root
            from alpha.groups.war_room import list_persisted_runs, load_run_record, read_transcript_tail

            root = runtime_root()
            if run_id.strip():
                record = load_run_record(root, room, run_id.strip())
                if record is None:
                    return json.dumps(
                        {
                            "ok": False,
                            "action": action,
                            "error": f"no persisted run {run_id.strip()!r} in room {room!r}",
                            "room": room,
                        }
                    )
                record["transcript_tail"] = read_transcript_tail(str(record.get("transcript_path") or ""), max(1, int(transcript_tail)))
                return json.dumps(
                    {
                        "ok": True,
                        "action": action,
                        "room": room,
                        "run": record,
                        "kill_switch": get_kill_switch_status(),
                    }
                )

            runs = list_persisted_runs(root, room or None)
            return json.dumps(
                {
                    "ok": True,
                    "action": action,
                    "room": room,
                    "count": len(runs),
                    "runs": runs,
                    "kill_switch": get_kill_switch_status(),
                }
            )

        # ------------------------------------------------------------------
        # read-only: should a room open here at all?
        # ------------------------------------------------------------------
        if action == "evaluate":
            from alpha.groups.trigger import TriggerPolicy, decide

            if not topic.strip():
                return json.dumps({"ok": False, "action": action, "error": "evaluate needs a topic"})
            decision = decide(topic, policy=TriggerPolicy(enabled=bool(trigger_enabled)))
            return json.dumps({"ok": True, "action": action, "decision": decision.to_dict()})

        # ------------------------------------------------------------------
        # run a deliberation
        # ------------------------------------------------------------------
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
                strategy=strategy.strip() or None,
                debate_rounds=int(debate_rounds),
            )

            async def _participant(ctx: Any) -> str:
                from alpha.groups.war_room import SubagentParticipant

                return await SubagentParticipant()(ctx)

            participants_impl = {name: _participant for name in roster}

            async def _moderator(ctx: Any) -> str:
                from alpha.groups.war_room import SubagentParticipant

                return await SubagentParticipant(agent_type="architect", max_turns=30)(ctx)

            from alpha.commands.channel_ops import runtime_root
            from alpha.groups.war_room import WarRoom

            war_room = WarRoom(
                config,
                participants=participants_impl,
                moderator=_moderator,
                room=room,
                root=runtime_root(),
            )
            run = _run_off_loop(war_room.execute)
            return json.dumps({"ok": run.status == "succeeded", "action": action, "run": run.to_dict()})

        # ------------------------------------------------------------------
        # bot lifecycle
        # ------------------------------------------------------------------
        if action in {"hire", "clone", "rescope", "archive", "unarchive", "revalidate"}:
            if not profile_name:
                return json.dumps({"ok": False, "action": action, "error": f"action {action} needs profile_name"})
            from alpha.bots.dynamic_profiles import DynamicProfileStore
            from alpha.bots.events import OrgEventStore
            from alpha.commands.channel_ops import runtime_root

            root = runtime_root()
            store = OrgEventStore(root / "events.jsonl")
            profiles = DynamicProfileStore(root / "dynamic_profiles.json", event_store=store)
            from alpha.groups.lifecycle_ops import BotLifecycle

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
                result = lifecycle.rescope_profile(profile_name, actor=actor, new_capabilities=caps, reason=reason, actor_grant=grant)
            elif action == "archive":
                result = lifecycle.archive_profile(profile_name, actor=actor, reason=reason)
            elif action == "unarchive":
                result = lifecycle.unarchive_profile(profile_name, actor=actor, reason=reason)
            else:
                result = lifecycle.revalidate(profile_name)
            return json.dumps({"ok": result.ok, "action": action, "result": result.to_dict()})

        # ------------------------------------------------------------------
        # capability acquisition
        # ------------------------------------------------------------------
        if action == "acquire":
            if not source_url or not capability_name:
                return json.dumps({"ok": False, "action": action, "error": "acquire needs source_url and capability_name"})
            from alpha.bots.events import OrgEventStore
            from alpha.commands.channel_ops import runtime_root
            from alpha.groups.acquisition import AcquisitionPipeline, default_fetcher

            root = runtime_root()
            store = OrgEventStore(root / "events.jsonl")
            pipeline = AcquisitionPipeline(root / "quarantine", ledger_store=store, fetcher=default_fetcher())
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
