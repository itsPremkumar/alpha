"""Bot Mode Autonomous Controller Mod (BotModeMod).

Governs autonomous, goal-driven bot execution loops across Alpha.
Ensures bot missions make continuous forward progress, detects execution stalls
and repetition loops, and automatically triggers self-healing / remediation paths.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
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


@dataclass
class BotMission:
    """State tracking for an active, autonomous bot mission."""

    mission_id: str
    bot_name: str
    goal: str
    current_step: int = 0
    max_steps: int = 50
    status: str = "active"  # "active", "stalled", "remediating", "completed", "failed", "cancelled"
    created_at: float = field(default_factory=time.time)
    last_progress_at: float = field(default_factory=time.time)
    last_heartbeat_at: float = field(default_factory=time.time)
    step_fingerprints: list[str] = field(default_factory=list)
    stall_count: int = 0
    remediation_attempts: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BotModeMod:
    """Autonomous goal-driven execution controller mod.

    Intercepts bot turns, workflow admissions, and step completions:
    1. Tracks active bot missions and per-step progress.
    2. Detects cognitive stalls, deadlocks, and repetitive tool invocations.
    3. Injects autonomous self-healing prompts or escalates when stalled.
    4. Records execution evidence receipts for durable auditing.
    """

    name: str = "bot_mode_controller"
    version: str = "1.0.0"
    priority: int = int(ModPriority.AUTONOMY)
    required_capabilities: set[str] = {"evidence:record", "estop:control", "clock:schedule"}
    subscribed_events: set[str] = {
        "bot.mission_started",
        "bot.workflow_admit",
        "bot.turn_started",
        "bot.turn_completed",
        "bot.heartbeat",
        "turn.step",
        "autonomy.tick",
        "bot.task_claimed",
    }
    manifest = ModManifest.create(
        name="bot_mode_controller",
        version="1.0.0",
        description="Autonomous goal-driven execution controller: tracks missions, detects stalls, injects self-healing.",
        hooks=tuple(sorted(subscribed_events)),
        calls=("evidence:record", "estop:control", "clock:schedule"),
        gating=True,
    )

    def __init__(
        self,
        *,
        stall_threshold_steps: int = 3,
        stall_timeout_seconds: float = 300.0,
        heartbeat_timeout_seconds: float = 600.0,
        max_remediation_attempts: int = 3,
        default_max_steps: int = 50,
    ):
        self.stall_threshold_steps = max(1, stall_threshold_steps)
        self.stall_timeout_seconds = stall_timeout_seconds
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.max_remediation_attempts = max_remediation_attempts
        self.default_max_steps = default_max_steps

        self._missions: dict[str, BotMission] = {}
        self._lock = threading.RLock()

    # -------------------------------------------------------------------------
    # Mission Query & Management API
    # -------------------------------------------------------------------------

    def get_mission(self, mission_id: str) -> BotMission | None:
        with self._lock:
            return self._missions.get(mission_id)

    def list_active_missions(self) -> list[BotMission]:
        with self._lock:
            return [m for m in self._missions.values() if m.status in ("active", "stalled", "remediating")]

    def cancel_mission(self, mission_id: str, reason: str = "Operator cancelled") -> bool:
        with self._lock:
            mission = self._missions.get(mission_id)
            if mission and mission.status not in ("completed", "failed", "cancelled"):
                mission.status = "cancelled"
                mission.history.append({"timestamp": time.time(), "action": "cancelled", "reason": reason})
                return True
            return False

    def record_progress(self, mission_id: str, summary: str) -> None:
        with self._lock:
            mission = self._missions.get(mission_id)
            if mission:
                now = time.time()
                mission.last_progress_at = now
                mission.last_heartbeat_at = now
                mission.stall_count = 0
                if mission.status in ("stalled", "remediating"):
                    mission.status = "active"
                mission.history.append({"timestamp": now, "action": "progress", "summary": summary})

    # -------------------------------------------------------------------------
    # Fingerprinting & Stall Detection
    # -------------------------------------------------------------------------

    @staticmethod
    def _compute_step_fingerprint(payload: dict[str, Any]) -> str:
        """Hash key action traits to detect repetitive behavior."""
        tool_name = str(payload.get("tool_name") or payload.get("action") or "")
        tool_args = payload.get("tool_args") or {}
        output_snippet = str(payload.get("output") or payload.get("content") or "")[:200]

        args_str = json.dumps(tool_args, sort_keys=True, default=str) if isinstance(tool_args, dict) else str(tool_args)
        raw = f"{tool_name}|{args_str}|{output_snippet.strip().lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _resolve_mission(self, event: AlphaEvent) -> BotMission | None:
        """Find the matching mission from correlation IDs or payload."""
        corr = event.correlation
        candidates = [corr.mission_id, corr.run_id, corr.task_id, event.payload.get("mission_id"), event.payload.get("run_id")]
        with self._lock:
            for c in candidates:
                if c and c in self._missions:
                    return self._missions[c]
        return None

    # -------------------------------------------------------------------------
    # Mod Handler Pipeline
    # -------------------------------------------------------------------------

    async def handle(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        ev_name = event.name

        # 1. Fleet ESTOP check (read off-loop: this handler runs on the event loop)
        engaged, status = await ctx.estop.read()
        if engaged:
            return EventResult.deny(
                event,
                reason=f"FLEET_ESTOP_ACTIVE: Bot mode execution loop halted -- {ctx.estop.reason(status)}",
            )

        # 2. Mission Admission / Initialization
        if ev_name in ("bot.mission_started", "bot.workflow_admit", "bot.task_claimed"):
            return await self._handle_mission_admission(ctx, event, next_fn)

        # 3. Turn Started / Step Increment
        if ev_name in ("bot.turn_started", "turn.step"):
            return await self._handle_turn_started(ctx, event, next_fn)

        # 4. Turn Completed / Stall Detection & Self-Healing
        if ev_name == "bot.turn_completed":
            return await self._handle_turn_completed(ctx, event, next_fn)

        # 5. Heartbeat
        if ev_name == "bot.heartbeat":
            return await self._handle_heartbeat(ctx, event, next_fn)

        # 6. Periodic Maintenance Sweep
        if ev_name == "autonomy.tick":
            return await self._handle_autonomy_tick(ctx, event, next_fn)

        return await next_fn(event)

    # -------------------------------------------------------------------------
    # Internal Event Handlers
    # -------------------------------------------------------------------------

    async def _handle_mission_admission(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        payload = event.payload
        corr = event.correlation

        mission_id = str(payload.get("mission_id") or "") or corr.mission_id or corr.run_id or f"msn_{int(time.time() * 1000)}"
        bot_name = str(payload.get("bot_name") or corr.agent_id or "unknown_bot").lower()
        goal = str(payload.get("prompt") or payload.get("goal") or payload.get("objective") or "Autonomous Mission")
        max_steps = int(payload.get("max_steps") or self.default_max_steps)

        with self._lock:
            mission = BotMission(
                mission_id=mission_id,
                bot_name=bot_name,
                goal=goal,
                max_steps=max_steps,
            )
            self._missions[mission_id] = mission
            # Also register under run_id if distinct
            if corr.run_id and corr.run_id != mission_id:
                self._missions[corr.run_id] = mission

        # Record admission evidence
        ctx.evidence.record(
            {
                "kind": "bot_mission_admitted",
                "mission_id": mission_id,
                "bot": bot_name,
                "goal": goal,
                "max_steps": max_steps,
            },
            correlation=corr,
        )

        logger.info("BotModeMod admitted autonomous mission '%s' for bot '@%s'", mission_id, bot_name)
        return await next_fn(event)

    async def _handle_turn_started(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        mission = self._resolve_mission(event)
        if not mission:
            return await next_fn(event)

        with self._lock:
            # Check terminal state
            if mission.status in ("failed", "cancelled"):
                return EventResult.deny(
                    event,
                    reason=f"MISSION_{mission.status.upper()}: Bot mission '{mission.mission_id}' is no longer active",
                )

            now = time.time()
            mission.last_heartbeat_at = now
            mission.current_step += 1

            # Check step budget
            if mission.current_step > mission.max_steps:
                mission.status = "failed"
                logger.warning(
                    "BotModeMod step limit exceeded for mission '%s' (step %d/%d)",
                    mission.mission_id,
                    mission.current_step,
                    mission.max_steps,
                )
                return EventResult.deny(
                    event,
                    reason=f"MISSION_STEP_LIMIT_EXCEEDED: Bot '@{mission.bot_name}' exceeded max steps ({mission.max_steps})",
                )

        return await next_fn(event)

    async def _handle_turn_completed(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        mission = self._resolve_mission(event)
        if not mission:
            return await next_fn(event)

        payload = event.payload
        fp = self._compute_step_fingerprint(payload)
        now = time.time()

        with self._lock:
            mission.last_heartbeat_at = now
            mission.step_fingerprints.append(fp)
            if len(mission.step_fingerprints) > 20:
                mission.step_fingerprints.pop(0)

            # Check repetition
            is_repeating = False
            if len(mission.step_fingerprints) >= 2 and mission.step_fingerprints[-1] == mission.step_fingerprints[-2]:
                is_repeating = True
                mission.stall_count += 1
            else:
                mission.stall_count = max(0, mission.stall_count - 1)
                mission.last_progress_at = now

            # Check timeout stall
            is_timed_out = (now - mission.last_progress_at) > self.stall_timeout_seconds
            # stall_count records repeated transitions after the first matching
            # action. Thus a threshold of 3 means three identical actions, not
            # four observations (which previously delayed remediation by one turn).
            repeat_threshold = max(1, self.stall_threshold_steps - 1)
            is_stalled = (mission.stall_count >= repeat_threshold) or is_timed_out

            if is_stalled:
                mission.status = "stalled"
                logger.warning(
                    "BotModeMod detected execution stall on mission '%s' (stall_count=%d, repeating=%s, timed_out=%s)",
                    mission.mission_id,
                    mission.stall_count,
                    is_repeating,
                    is_timed_out,
                )

                # Attempt self-healing remediation
                if mission.remediation_attempts < self.max_remediation_attempts:
                    mission.remediation_attempts += 1
                    mission.status = "remediating"

                    remediation_prompt = (
                        f"Autonomous Controller Notice [STALL_DETECTED]: "
                        f"Bot '@{mission.bot_name}' has repeated actions without measurable progress on goal '{mission.goal}'. "
                        f"Remediation guidelines: "
                        f"1) Do NOT repeat the previous action '{payload.get('tool_name', 'step')}'. "
                        f"2) Re-read requirements and evaluate alternative tools or reasoning steps. "
                        f"3) If external assistance is needed, use `message_agent` or escalate to supervisor."
                    )

                    # Durably record stall receipt
                    ctx.evidence.record(
                        {
                            "kind": "bot_stall_remediation",
                            "mission_id": mission.mission_id,
                            "bot": mission.bot_name,
                            "stall_count": mission.stall_count,
                            "remediation_attempt": mission.remediation_attempts,
                            "prompt": remediation_prompt,
                        },
                        correlation=event.correlation,
                    )

                    # Rewrite event to inject remediation directive downstream
                    mutated = event.with_payload(
                        remediation_required=True,
                        remediation_prompt=remediation_prompt,
                        stall_count=mission.stall_count,
                    )
                    return EventResult.rewrite(
                        mutated,
                        reason="BOT_STALL_SELF_HEAL: Injected remediation prompt to break execution loop",
                        metadata={"mission_id": mission.mission_id, "remediation_attempt": mission.remediation_attempts},
                    )

                # Remediation exhausted: Escalate to supervisor
                mission.status = "failed"
                logger.error(
                    "BotModeMod exhausted max remediation attempts (%d) on mission '%s'; escalating",
                    self.max_remediation_attempts,
                    mission.mission_id,
                )
                return EventResult.escalate(
                    event,
                    reason=(f"UNRECOVERABLE_STALL: Bot '@{mission.bot_name}' failed to break loop after {self.max_remediation_attempts} remediation attempts. Escalating for reassignment."),
                    metadata={"mission_id": mission.mission_id, "status": "failed"},
                )

        return await next_fn(event)

    async def _handle_heartbeat(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        mission = self._resolve_mission(event)
        if mission:
            with self._lock:
                mission.last_heartbeat_at = time.time()
        return await next_fn(event)

    async def _handle_autonomy_tick(
        self,
        ctx: CapabilityContext,
        event: AlphaEvent,
        next_fn: NextHandler,
    ) -> EventResult:
        """Periodic maintenance pass to detect abandoned or deadlocked missions."""
        now = time.time()
        stalled_missions: list[str] = []

        with self._lock:
            for m in self._missions.values():
                if m.status in ("active", "remediating"):
                    if (now - m.last_heartbeat_at) > self.heartbeat_timeout_seconds:
                        m.status = "stalled"
                        stalled_missions.append(m.mission_id)

        if stalled_missions:
            logger.info("BotModeMod autonomy tick marked %d missions stalled: %s", len(stalled_missions), stalled_missions)

        return await next_fn(event)
