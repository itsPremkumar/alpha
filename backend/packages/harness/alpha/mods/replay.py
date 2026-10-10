"""ReplayMod — step through what a turn actually changed.

Claude Code's Replay Theater records every Edit and Write during a turn and then
walks the operator through them one diff at a time. The interesting part is how
it gets a real diff: it reads the file's old contents *just before the write
lands*, because after the write the old contents are gone.

Alpha's equivalent gets that the same way. A mod sees ``tool.requested`` before
the tool node runs the handler, so the pre-state is still on disk at that moment.
This mod snapshots it, keyed by tool call, and assembles the step when
``tool.completed`` arrives.

The honesty rule is the one that makes it usable: when the pre-state could not be
read, the step carries ``before: null`` and ``pre_state_available: false``. An
empty string would read as "the file was empty", which is a fabricated
before-state for a diff — the exact class of claim this repo refuses everywhere
else.
"""

from __future__ import annotations

import difflib
import logging
import threading
import time
from typing import Any

from alpha.mods.context import CapabilityContext
from alpha.mods.manifest import ModManifest
from alpha.mods.types import (
    AlphaEvent,
    EventResult,
    ModPriority,
    NextHandler,
)

logger = logging.getLogger(__name__)

#: Tools whose effect is a file mutation worth replaying.
_FILE_TOOLS = frozenset(
    {
        "write_to_file",
        "replace_file_content",
        "edit_file",
        "apply_patch",
        "str_replace_editor",
        "multi_edit",
    }
)

#: How many steps one turn keeps.
MAX_STEPS_PER_TURN = 50

#: How many turns are retained.
MAX_TURNS = 20

#: Largest pre-state that will be captured, per file.
MAX_CAPTURE_BYTES = 128 * 1024


def _turn_key(event: AlphaEvent) -> str:
    """Group steps by the turn they belong to.

    ``turn_id`` is preferred because a run may produce several turns, and a
    replay of "the last turn" means the turn, not the run. The fallback to
    ``run_id`` keeps single-turn runs usable when no turn id was stamped.
    """
    corr = event.correlation
    return str(corr.turn_id or corr.run_id or corr.task_id or "unkeyed")


def _diff(before: str | None, after: str) -> str:
    """A unified diff, or an empty string when there is nothing to compare.

    An empty string here means "no before-state was captured", never "no
    difference": callers read ``pre_state_available`` for that.
    """
    if before is None:
        return ""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
            n=2,
        )
    )


class ReplayMod:
    """Records the file changes one turn made and replays them on request."""

    name = "replay_theater"
    version = "1.0.0"
    priority = int(ModPriority.EXECUTION)
    required_capabilities = {"fs:read", "storage:write"}
    subscribed_events = {"tool.requested", "tool.completed", "replay.requested", "bot.turn_completed", "turn.complete"}
    manifest = ModManifest.create(
        name="replay_theater",
        version="1.0.0",
        description="Records a turn's file edits with real before/after diffs and replays them on request.",
        hooks=["tool.requested", "tool.completed", "replay.requested", "bot.turn_completed", "turn.complete"],
        calls=["fs:read", "storage:write"],
        state_reads=["steps"],
        state_writes=["steps", "pending"],
        gating=False,
    )

    def __init__(self) -> None:
        self._lock = threading.RLock()

    # -- capture -----------------------------------------------------------

    def _capture_pre_state(self, ctx: CapabilityContext, event: AlphaEvent) -> None:
        """Snapshot a file's contents before a write tool overwrites it."""
        payload = event.payload
        tool_name = str(payload.get("tool_name") or "")
        if tool_name.lower() not in _FILE_TOOLS:
            return
        tool_args = payload.get("tool_args") or {}
        if not isinstance(tool_args, dict):
            return
        target = str(tool_args.get("TargetFile") or tool_args.get("path") or tool_args.get("file_path") or tool_args.get("absolute_path") or "").strip()
        if not target:
            return
        snapshot = ctx.fs.read_text(target, max_bytes=MAX_CAPTURE_BYTES)
        # The snapshot is only useful while its write is still pending, so it is
        # keyed by the tool call that will consume it.
        pending = ctx.storage.get("pending") or {}
        if not isinstance(pending, dict):
            pending = {}
        pending[str(event.correlation.tool_call_id or event.event_id)] = {
            "path": target,
            "before": snapshot.text if snapshot.available else None,
            "pre_state_available": snapshot.available,
            "pre_state_reason": snapshot.reason,
            "pre_state_size": snapshot.size,
            "pre_state_truncated": snapshot.truncated,
        }
        ctx.storage.set("pending", pending)

    def _assemble_step(self, ctx: CapabilityContext, event: AlphaEvent) -> None:
        payload = event.payload
        tool_name = str(payload.get("tool_name") or "")
        if tool_name.lower() not in _FILE_TOOLS:
            return
        call_id = str(payload.get("tool_call_id") or event.correlation.tool_call_id or "")
        pending = ctx.storage.get("pending") or {}
        record = pending.pop(call_id, None) if isinstance(pending, dict) else None
        if isinstance(pending, dict):
            ctx.storage.set("pending", pending)

        tool_args = payload.get("tool_args") or {}
        target = ""
        if isinstance(tool_args, dict):
            target = str(tool_args.get("TargetFile") or tool_args.get("path") or tool_args.get("file_path") or tool_args.get("absolute_path") or "")
        after = str(payload.get("content") or (tool_args.get("ReplacementContent") if isinstance(tool_args, dict) else "") or "")
        pre = record if isinstance(record, dict) else {}

        before = pre.get("before")
        available = bool(pre.get("pre_state_available"))
        if before is None and available:
            # A pre-state that read as empty is a real empty file; keep the
            # distinction from "no pre-state captured".
            before = ""
        if not available and not target:
            # Without either a captured path or a declared one there is nothing
            # to replay; do not invent a step.
            return

        step = {
            "index": 0,
            "tool": tool_name,
            "path": target or str(pre.get("path") or ""),
            "before": before,
            "pre_state_available": available,
            "pre_state_reason": str(pre.get("pre_state_reason") or ""),
            "after": after[:MAX_CAPTURE_BYTES],
            "status": str(payload.get("status") or "success"),
            "tool_call_id": call_id,
            "timestamp": time.time(),
        }
        step["diff"] = _diff(before, step["after"])

        turn = _turn_key(event)
        steps = ctx.storage.get("steps") or {}
        if not isinstance(steps, dict):
            steps = {}
        order = ctx.storage.get("turn_order") or []
        if not isinstance(order, list):
            order = []
        turn_steps = steps.get(turn) or []
        step["index"] = len(turn_steps) + 1
        turn_steps.append(step)
        # Bounded: the newest turn wins, and a turn keeps only its first N steps.
        steps[turn] = turn_steps[-MAX_STEPS_PER_TURN:]
        if turn not in order:
            order.append(turn)
        if len(steps) > MAX_TURNS:
            # Drop the oldest turns by write order, which `turn_order` records,
            # so an eviction never depends on how the ids happen to sort.
            doomed = [key for key in order if key in steps][: len(steps) - MAX_TURNS]
            for stale in doomed:
                steps.pop(stale, None)
                if stale in order:
                    order.remove(stale)
        ctx.storage.set("steps", steps)
        ctx.storage.set("turn_order", order)

    # -- queries -----------------------------------------------------------

    def replay(self, ctx: CapabilityContext, turn_id: str | None = None) -> dict[str, Any]:
        """Return the recorded steps for one turn, defaulting to the newest.

        "Newest" means most recently *written*, not lexicographically last:
        turn ids are opaque strings, and ``sorted(...)[-1]`` would happily pick
        ``turn_a`` over ``turn_z`` and then claim it was the latest.
        """
        steps = ctx.storage.get("steps") or {}
        if not isinstance(steps, dict) or not steps:
            return {"turns": [], "steps": [], "turn_id": turn_id, "recorded": False, "reason": "NO_RECORDED_STEPS"}
        order = ctx.storage.get("turn_order") or []
        if not isinstance(order, list):
            order = []
        key = turn_id or (order[-1] if order else sorted(steps.keys())[-1])
        turn_steps = steps.get(key) or []
        return {
            "turn_id": key,
            "recorded": True,
            "steps": list(turn_steps),
            "turns": list(order),
        }

    # -- handler -----------------------------------------------------------

    async def handle(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        try:
            if event.name == "tool.requested":
                self._capture_pre_state(ctx, event)
                return await next_fn(event)

            if event.name == "tool.completed":
                self._assemble_step(ctx, event)
                return await next_fn(event)

            if event.name == "replay.requested":
                requested = str(event.payload.get("turn_id") or event.correlation.turn_id or "") or None
                report = self.replay(ctx, requested)
                return EventResult.answer(
                    event,
                    response_payload=report,
                    reason=f"REPLAY: {len(report.get('steps', []))} step(s) for turn {report.get('turn_id')}",
                )

            if event.name in ("bot.turn_completed", "turn.complete"):
                # Drop pending captures whose write never completed: a capture
                # with no completer is a stale entry that would attach to the
                # next write with the same call id.
                pending = ctx.storage.get("pending") or {}
                if isinstance(pending, dict) and pending:
                    with self._lock:
                        ctx.storage.set("pending", {})
                return await next_fn(event)
        except Exception as exc:
            logger.debug("ReplayMod failed on '%s': %s", event.name, exc)

        return await next_fn(event)
