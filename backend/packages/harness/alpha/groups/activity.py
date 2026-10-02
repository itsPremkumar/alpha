"""Group activity: who is working, on what, since when, and what they hold.

`groups/presence.py` resolves a *lifecycle* word per room member from two
sources — BotRegistry (`status`, `last_active`) and CompanyAttendance (a
pulse). Neither source is told that a process died, and the resolver's own
fallback is what makes the distinction impossible:

    registry status "active" (nothing flips it on crash)
      + a `last_active` stamp older than PRESENCE_WINDOW_SECONDS
      = MemberPresence(..., "idle", detail="no recent activity")

`idle` is also what an agent looks like when it has *finished cleanly*. So a
crashed agent and a completed agent render as the same grey dot, which is the
failure this module exists to remove.

The evidence needed to tell them apart already exists, durably, and this
repository simply never connected the display to it. `RunManager`
(`runtime/runs/manager.py`) terminalises every crash path with a named,
persisted reason — `orphan_recovered`, `gateway_shutdown`, `model_failure`,
`network_waiting` (the `RECOVERABLE_RUN_STOP_REASONS` set) — and
`GroupRunService` rewrites an in-flight `running` row to `interrupted` at load.
`presence.py` reads none of it, because it never touches the run store.

So this layer is a **projection of the run lifecycle**, not a second source of
truth:

- `RunManager` publishes on the existing in-process bus (`alpha/events/bus.py`,
  bounded and drop-oldest, a no-op when disabled). `RunManager` itself is not
  restructured and stays the sole lifecycle owner; the coupling is one-way.
- `GroupRunService` reports per-member lifecycle directly, because it already
  holds the room and the member name — a group run is not a `RunManager` run.
- Everything else reconciles **on read**, matching the pattern this codebase
  already uses (`crew.ensure_crew()` is documented as "safe to call on every
  read"; `GET /projects/{id}/locks` calls `sweep_expired()`). That is also the
  cheap option: a supervisor loop would move the generated loop count and force
  five doc files to move with it, for a result that is strictly staler.

## Two axes, never one enum

The original sin was one word answering two questions. Activity and health are
now separate fields:

- ``activity`` — `working` / `idle` / `blocked` / `unresponsive` / `crashed` /
  `offline` / `unknown`
- ``health`` — the existing `alpha.bots.health` verdict
  (`healthy`/`stale`/`stalled`/`dead`/`sleeping`), read alongside

`unresponsive` and `crashed` are deliberately distinct. Heartbeat silence is
*evidence of a problem*, not proof of death: an agent inside a twenty-minute
tool call is silent too. Only a named terminal reason earns `crashed`. A UI
that paints both red would re-create the original lie one layer up.

## The rule this module exists to enforce

**A `working → idle` transition requires proof.** Either a terminal run event
or an explicit release. A heartbeat that merely goes quiet is `unresponsive`,
never `idle`.

## Invariants borrowed from the group layer

- `GroupRoom.members` is crew-owned. `crew._sync_room()` deletes any entry
  project membership does not claim, so activity is **never** written there. A
  nested or rule-matched member is resolved live through `effective_members()`.
- Every derived state carries `evidence`. A state this module cannot explain
  does not exist in it.
- `last_heartbeat_at` follows `presence._parse_epoch`'s discipline: an
  unreadable stamp is `None`, never `time.time()`, which would manufacture a
  fresh heartbeat out of a missing one.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

#: What an agent is *doing*. Deliberately not the presence vocabulary: `idle`
#: here means "proven not working", which the old resolver could not express.
ActivityState = Literal[
    "working",
    "idle",
    "blocked",
    "unresponsive",
    "crashed",
    "offline",
    "unknown",
]

ACTIVITY_STATES: tuple[str, ...] = (
    "working",
    "idle",
    "blocked",
    "unresponsive",
    "crashed",
    "offline",
    "unknown",
)

#: One display dot. `crashed` and `unresponsive` are different colours on
#: purpose — see the module docstring.
ActivityTone = Literal["busy", "ok", "warn", "bad", "off", "unknown"]

#: How an agent's work reached the ledger. `explicit` is a server-assigned room
#: binding; `roster_match` is inferred from the room's resolved membership and
#: is always disclosed, never silent. `unattributed` means the run matched no
#: room and is therefore shown in none of them.
Attribution = Literal["explicit", "roster_match", "unattributed"]

#: A run that is live and has no heartbeat this recent is assumed working; an
#: agent mid-tool-call legitimately goes quiet. Matches the existing display
#: window so a dot and its tooltip cannot disagree.
ACTIVITY_WINDOW_SECONDS = 90

#: Silence past this with no terminal event is `unresponsive`, not `idle`.
UNRESPONSIVE_AFTER_SECONDS = 180

#: Bus topic prefix. Everything the reconciler folds in arrives under it.
ACTIVITY_EVENT_PREFIX = "group.activity"


def _now() -> float:
    return time.time()


def _parse_epoch(value: Any) -> float | None:
    """Read a stamp as epoch seconds, tolerating the ISO-8601 shape.

    Shares `presence._parse_epoch`'s contract: anything unreadable is `None`.
    Returning `time.time()` here would turn a missing stamp into a fresh
    heartbeat, which is the one thing this module exists to stop.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if float(value) > 0 else None
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.replace(".", "", 1).isdigit():
        try:
            seconds = float(raw)
        except ValueError:
            return None
        return seconds if seconds > 0 else None
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.timestamp()


def _default_storage_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "groups" / "activity.json"
    except Exception:
        return Path.cwd() / ".alpha" / "groups" / "activity.json"


# ── Evidence ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunEvidence:
    """What the run store says about the work an agent is doing.

    This is the only input allowed to produce `crashed`. Every field is
    optional because a claim about a run must be as honest as the store row it
    came from: an unreadable record yields `status=None`, never a guess.
    """

    run_id: str
    status: str | None = None
    stop_reason: str | None = None
    error: str | None = None
    #: True when the store had no row for this id at all. Distinct from a row
    #: that exists and is still active — that is `unresponsive`, not `unknown`.
    absent: bool = False

    @property
    def terminal_success(self) -> bool:
        return self.status == "success" and not self.absent

    @property
    def terminal_failure(self) -> bool:
        return self.status in ("error", "timeout", "interrupted") and not self.absent

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunEvidence:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


@dataclass(frozen=True)
class ActivityEvidence:
    """Why this module believes what it believes.

    `reason` is a stable machine word so a test can assert the derivation
    rather than the prose; `detail` is the human sentence for the tooltip.
    Nothing renders an `AgentActivity` whose evidence is empty.
    """

    source: Literal["run_store", "heartbeat", "registry", "health", "absent"]
    reason: str
    detail: str
    run: RunEvidence | None = None
    last_heartbeat_at: float | None = None
    seconds_since_heartbeat: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "reason": self.reason,
            "detail": self.detail,
            "run": self.run.to_dict() if self.run else None,
            "last_heartbeat_at": self.last_heartbeat_at,
            "seconds_since_heartbeat": self.seconds_since_heartbeat,
        }


# ── The record ──────────────────────────────────────────────────────────────


@dataclass
class AgentActivity:
    """One member's resolved activity in one room."""

    bot_name: str
    activity: ActivityState
    detail: str
    tone: ActivityTone
    evidence: ActivityEvidence
    since: float
    run_id: str | None = None
    claim_ids: list[str] = field(default_factory=list)
    held_paths: list[str] = field(default_factory=list)
    #: The `alpha.bots.health` verdict, kept separate from `activity`. A bot
    #: can be `blocked` and `healthy` at once, and collapsing them is how the
    #: two questions got conflated.
    health: str | None = None
    last_heartbeat_at: float | None = None
    attributed_by: Attribution = "unattributed"
    room_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bot_name": self.bot_name,
            "activity": self.activity,
            "detail": self.detail,
            "tone": self.tone,
            "since": self.since,
            "run_id": self.run_id,
            "claim_ids": list(self.claim_ids),
            "held_paths": list(self.held_paths),
            "health": self.health,
            "last_heartbeat_at": self.last_heartbeat_at,
            "attributed_by": self.attributed_by,
            "room_name": self.room_name,
            "evidence": self.evidence.to_dict(),
        }


_TONES: dict[str, ActivityTone] = {
    "working": "busy",
    "idle": "ok",
    "blocked": "warn",
    "unresponsive": "warn",
    "crashed": "bad",
    "offline": "off",
    "unknown": "unknown",
}


def tone_for(activity: str) -> ActivityTone:
    """Map a state word to its display tone.

    An unrecognised word is returned verbatim as `unknown` rather than folded
    into `idle`. A future state must render as visibly-not-measured, never as a
    healthy dot — the same rule `presence._resolve` applies to an unrecognised
    registry status.
    """
    return _TONES.get(activity, "unknown")


# ── The derivation table ────────────────────────────────────────────────────


#: RunManager's four named crash reasons, plus the group-run equivalent.
#: Imported rather than restated so the two vocabularies cannot drift; the
#: fallback literal is only used when the runtime package cannot import (which
#: would otherwise make presence raise, and a display must never do that).
def _recoverable_stop_reasons() -> frozenset[str]:
    try:
        from alpha.runtime.runs.manager import RECOVERABLE_RUN_STOP_REASONS

        return frozenset(RECOVERABLE_RUN_STOP_REASONS)
    except Exception:  # pragma: no cover - defensive; display must not raise
        return frozenset({"orphan_recovered", "gateway_shutdown", "model_failure", "network_waiting"})


def derive_activity(
    *,
    bot_name: str,
    now: float,
    run: RunEvidence | None = None,
    last_heartbeat_at: float | None = None,
    declared: str | None = None,
    health: str | None = None,
    registry_status: str | None = None,
    archived: bool = False,
) -> AgentActivity:
    """Resolve one member's activity from measured inputs.

    Every branch below is a row of the contract table in
    `docs/AGENT_LIVE_STATUS_AND_WORK_COORDINATION.md`. A state this function
    cannot explain does not exist in it.

    Precedence is load-bearing and is not alphabetical:

    1. An administrative lifecycle word outranks every activity claim. A
       suspended bot cannot be crashed; it was told to stop.
    2. A hard run fact outranks a stale heartbeat. The store is ground truth.
    3. Silence only ever becomes `unresponsive`, never `idle`.
    """

    evidence: ActivityEvidence

    # 1. Administrative lifecycle: not a crash, a decision.
    if archived or registry_status in ("suspended", "archived"):
        status = registry_status or ("archived" if archived else "suspended")
        evidence = ActivityEvidence(
            source="registry",
            reason="lifecycle_out",
            detail=f"{bot_name} is {status}",
            last_heartbeat_at=last_heartbeat_at,
        )
        return _build(bot_name, "offline", evidence, now, run, health, last_heartbeat_at)

    # 2. Hard run facts. These are the only rows that earn the word "crashed".
    if run is not None and not run.absent:
        if run.stop_reason and run.stop_reason in _recoverable_stop_reasons():
            evidence = ActivityEvidence(
                source="run_store",
                reason="recoverable_stop_reason",
                detail=_crash_detail(run),
                run=run,
                last_heartbeat_at=last_heartbeat_at,
            )
            return _build(bot_name, "crashed", evidence, now, run, health, last_heartbeat_at)
        if run.terminal_failure:
            evidence = ActivityEvidence(
                source="run_store",
                reason="run_terminal_failure",
                detail=_crash_detail(run),
                run=run,
                last_heartbeat_at=last_heartbeat_at,
            )
            return _build(bot_name, "crashed", evidence, now, run, health, last_heartbeat_at)
        if run.terminal_success:
            # The proof this module was written to require: a real terminal
            # success is the only thing that turns `working` into `idle`.
            evidence = ActivityEvidence(
                source="run_store",
                reason="run_terminal_success",
                detail=f"{bot_name}'s run {run.run_id} completed",
                run=run,
                last_heartbeat_at=last_heartbeat_at,
            )
            return _build(bot_name, "idle", evidence, now, run, health, last_heartbeat_at)
        if run.status == "running" or run.status == "pending":
            evidence = ActivityEvidence(
                source="run_store",
                reason="run_active",
                detail=f"{bot_name}'s run {run.run_id} is {run.status}",
                run=run,
                last_heartbeat_at=last_heartbeat_at,
            )
            return _build(bot_name, "working", evidence, now, run, health, last_heartbeat_at)

    # 3. The run id is known but the store has no row. Something removed it
    #    underneath us, so this is a problem — but naming it "crashed" would be
    #    claiming a terminal fact we do not have.
    if run is not None and run.absent:
        evidence = ActivityEvidence(
            source="run_store",
            reason="run_absent",
            detail=f"{bot_name}'s run {run.run_id} is no longer in the run store",
            run=run,
            last_heartbeat_at=last_heartbeat_at,
        )
        return _build(bot_name, "unresponsive", evidence, now, run, health, last_heartbeat_at)

    # 4. An explicit self-report outranks an inferred liveness signal.
    #
    #    `blocked` and `idle` are *work* states, not liveness signals, and they
    #    stay true until the agent says otherwise. An agent that reported "I'm
    #    blocked on the schema" and then went quiet is still blocked — reporting
    #    it `unresponsive` would let a liveness word overwrite the one piece of
    #    information a peer actually needed, which is the same mistake as
    #    resolving silence to `idle`, just with a different vocabulary.
    age = _age(last_heartbeat_at, now)
    fresh = age is not None and age <= ACTIVITY_WINDOW_SECONDS
    if declared in ("blocked", "idle"):
        evidence = ActivityEvidence(
            source="heartbeat",
            reason=("declared_blocked" if fresh else "declared_blocked_stale") if declared == "blocked" else ("declared_idle" if fresh else "declared_idle_stale"),
            detail=(f"{bot_name} reported itself {declared}" if fresh else f"{bot_name} last reported itself {declared}"),
            run=run,
            last_heartbeat_at=last_heartbeat_at,
            seconds_since_heartbeat=None if age is None else round(age, 1),
        )
        return _build(bot_name, declared, evidence, now, run, health, last_heartbeat_at)

    # 5. A live run plus a fresh pulse is the ordinary working case.
    if fresh:
        evidence = ActivityEvidence(
            source="heartbeat",
            reason="heartbeat_fresh",
            detail=f"{bot_name} reported activity {round(age, 1)}s ago",
            run=run,
            last_heartbeat_at=last_heartbeat_at,
            seconds_since_heartbeat=round(age, 1),
        )
        return _build(bot_name, "working", evidence, now, run, health, last_heartbeat_at)

    # 6. Silence. This is the branch the old resolver collapsed into `idle`.
    #    Whether it is a stall or a crash depends on whether the agent was
    #    working when it went quiet, and nothing here may call it finished.
    if age is not None and age > UNRESPONSIVE_AFTER_SECONDS:
        reason = "heartbeat_timeout" if (run is None or run.status in ("running", "pending")) else "run_ended_without_event"
        detail = f"{bot_name} has not reported for {round(age)}s" if reason == "heartbeat_timeout" else f"{bot_name}'s work ended with no terminal event"
        evidence = ActivityEvidence(
            source="health" if health else "heartbeat",
            reason=reason,
            detail=detail,
            run=run,
            last_heartbeat_at=last_heartbeat_at,
            seconds_since_heartbeat=round(age, 1),
        )
        return _build(bot_name, "unresponsive", evidence, now, run, health, last_heartbeat_at)

    if health in ("stalled", "dead"):
        evidence = ActivityEvidence(
            source="health",
            reason="health_" + health,
            detail=f"{bot_name} liveness is {health}",
            run=run,
            last_heartbeat_at=last_heartbeat_at,
            seconds_since_heartbeat=None if age is None else round(age, 1),
        )
        return _build(bot_name, "unresponsive", evidence, now, run, health, last_heartbeat_at)

    if registry_status == "sleeping":
        evidence = ActivityEvidence(
            source="registry",
            reason="lifecycle_sleeping",
            detail=f"{bot_name} is sleeping",
            run=run,
            last_heartbeat_at=last_heartbeat_at,
        )
        return _build(bot_name, "idle", evidence, now, run, health, last_heartbeat_at)

    # 6. A registry row and nothing else. "We have no record of this member's
    #    work" and "this member finished" are different claims, so this is
    #    `unknown`, never `idle`.
    evidence = ActivityEvidence(
        source="absent",
        reason="no_activity_record",
        detail=f"no work record for {bot_name}",
        run=run,
        last_heartbeat_at=last_heartbeat_at,
        seconds_since_heartbeat=None if age is None else round(age, 1),
    )
    return _build(bot_name, "unknown", evidence, now, run, health, last_heartbeat_at)


def _build(
    bot_name: str,
    activity: ActivityState,
    evidence: ActivityEvidence,
    now: float,
    run: RunEvidence | None,
    health: str | None,
    last_heartbeat_at: float | None,
) -> AgentActivity:
    return AgentActivity(
        bot_name=bot_name,
        activity=activity,
        detail=evidence.detail,
        tone=tone_for(activity),
        evidence=evidence,
        # `since` is stamped by the ledger from the previous state, not here:
        # a pure derivation cannot know how long the current state has held.
        since=now,
        run_id=run.run_id if run else None,
        health=health,
        last_heartbeat_at=last_heartbeat_at,
    )


def _crash_detail(run: RunEvidence) -> str:
    """A crash sentence that names the evidence rather than asserting a cause."""
    if run.stop_reason:
        base = f"{run.run_id} stopped: {run.stop_reason}"
    else:
        base = f"{run.run_id} ended as {run.status}"
    if run.error:
        return f"{base} — {run.error}"
    return base


def _age(stamp: float | None, now: float) -> float | None:
    if stamp is None:
        return None
    return max(0.0, now - stamp)


# ── Input readers ───────────────────────────────────────────────────────────
#
# Both swallow their own failures and return `{}` / `None`, exactly as
# `presence._registry_rows` / `_attendance_rows` do. A display layer that
# raises takes the whole room listing down with it, which is worse than
# showing everything as unmeasured.


def _registry_rows() -> dict[str, dict[str, Any]]:
    try:
        from alpha.bots.registry import get_bot_registry

        bots = get_bot_registry().list_bots()
    except Exception:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for bot in bots:
        try:
            name = (getattr(bot, "name", "") or "").strip().lower()
            if not name:
                continue
            rows[name] = {
                "status": (getattr(bot, "status", "") or "").strip().lower(),
                "last_active": getattr(bot, "last_active", None),
                "archived": bool(getattr(bot, "archived_at", None)),
            }
        except Exception:
            continue
    return rows


def _health_row(bot_name: str) -> str | None:
    """The existing liveness verdict. `None` when it cannot be measured."""
    try:
        from alpha.bots.health import get_health_monitor
        from alpha.bots.registry import get_bot_registry

        registry = get_bot_registry()
        profile = registry.get_bot(bot_name) or registry.get(bot_name)
        if profile is None:
            return None
        return str(get_health_monitor().evaluate_liveness(profile).get("liveness") or "") or None
    except Exception:
        return None


# ── Ledger ──────────────────────────────────────────────────────────────────


class ActivityLedger:
    """File-backed record of what each bot is doing.

    Deliberately small and stateless with respect to *authority*: it records
    observed facts (a run started, a run terminalised, an agent sent a pulse)
    and derives state from them on read. It never decides whether work may
    proceed — that belongs to `projects/locks.py` and, in phase 2, to
    `groups/claims.py`.

    Storage is a single JSON file written with the same `tmp` + `replace()`
    discipline as `membership.json` and `locks.json`. A load failure logs and
    starts empty rather than raising, because a corrupt display store must not
    take down the rooms it describes.
    """

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._lock = threading.Lock()
        #: bot_name -> observed facts. Only agents that actually reported or
        #: ran something appear here; every other room member is derived as
        #: `unknown` from the roster, so the file stays proportional to real
        #: activity rather than to room size.
        self._rows: dict[str, dict[str, Any]] = {}
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            rows = data.get("agents", {})
            if isinstance(rows, dict):
                self._rows = {str(k): dict(v) for k, v in rows.items() if isinstance(v, dict)}
        except Exception:
            logger.warning("Group activity ledger load failed; starting empty", exc_info=True)
            self._rows = {}

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "agents": self._rows}, f, indent=2)
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Group activity ledger save failed", exc_info=True)

    # -- recorders (called by the run lifecycle and the group runner) ---------

    def _row(self, bot_name: str) -> dict[str, Any]:
        return self._rows.setdefault(
            bot_name.lower().strip(),
            {
                "bot_name": bot_name.lower().strip(),
                "run_id": None,
                "run_status": None,
                "run_stop_reason": None,
                "run_error": None,
                "room_name": None,
                "project_id": None,
                "declared": None,
                "detail": "",
                "last_heartbeat_at": None,
                "last_activity_at": None,
                "since": None,
                "activity": None,
            },
        )

    def record_run_started(
        self,
        bot_name: str,
        *,
        run_id: str,
        room_name: str | None = None,
        project_id: str | None = None,
        detail: str = "",
    ) -> dict[str, Any]:
        """A run began. `room_name` is a **server-assigned** binding only."""
        with self._lock:
            row = self._row(bot_name)
            row.update(
                {
                    "run_id": run_id,
                    "run_status": "running",
                    "run_stop_reason": None,
                    "run_error": None,
                    "last_activity_at": _now(),
                }
            )
            if room_name:
                row["room_name"] = room_name
            if project_id:
                row["project_id"] = project_id
            if detail:
                row["detail"] = detail
            row["since"] = _now()
            row["activity"] = "working"
            self._save()
            return dict(row)

    def record_run_terminal(
        self,
        bot_name: str,
        *,
        run_id: str,
        status: str,
        stop_reason: str | None = None,
        error: str | None = None,
        room_name: str | None = None,
    ) -> dict[str, Any]:
        """A run reached a terminal state, however it got there."""
        with self._lock:
            row = self._row(bot_name)
            # A terminal event for a *different* run must not overwrite the
            # current one: an agent may already have started the next task,
            # and a late terminal event would then erase live work.
            if row.get("run_id") not in (None, run_id):
                return dict(row)
            row.update(
                {
                    "run_id": run_id,
                    "run_status": status,
                    "run_stop_reason": stop_reason,
                    "run_error": error,
                    "declared": "idle" if status == "success" else row.get("declared"),
                    "last_activity_at": _now(),
                    "since": _now(),
                }
            )
            if room_name:
                row["room_name"] = room_name
            self._save()
            return dict(row)

    def record_heartbeat(
        self,
        bot_name: str,
        *,
        declared: str | None = None,
        detail: str = "",
        room_name: str | None = None,
        claim_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """An agent published a pulse. This is the *only* self-report path."""
        if declared is not None and declared not in ACTIVITY_STATES:
            raise ValueError(f"Unknown activity state '{declared}'")
        with self._lock:
            row = self._row(bot_name)
            row["last_heartbeat_at"] = _now()
            row["last_activity_at"] = row["last_heartbeat_at"]
            if declared:
                row["declared"] = declared
                row["since"] = row["last_heartbeat_at"]
            if detail:
                row["detail"] = detail[:500]
            if room_name:
                row["room_name"] = room_name
            if claim_ids is not None:
                row["claim_ids"] = [str(c) for c in claim_ids][:50]
            self._save()
            return dict(row)

    def bind_room(self, bot_name: str, *, room_name: str, project_id: str | None = None) -> None:
        """Attribute an agent's activity to one room (server-side only)."""
        with self._lock:
            row = self._row(bot_name)
            row["room_name"] = room_name
            if project_id:
                row["project_id"] = project_id
            self._save()

    def clear_bot(self, bot_name: str) -> bool:
        """Forget an agent. Used when it leaves every room it was recorded in."""
        with self._lock:
            if self._rows.pop(bot_name.lower().strip(), None) is None:
                return False
            self._save()
            return True

    # -- readers (reconcile on read) -----------------------------------------

    def raw_row(self, bot_name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._rows.get(bot_name.lower().strip())
            return dict(row) if row else None

    def resolve(
        self,
        bot_name: str,
        *,
        room_name: str | None = None,
        run: RunEvidence | None = None,
        now: float | None = None,
    ) -> AgentActivity:
        """Derive one agent's activity from live sources.

        `run` is the caller's live read of the run store. Passing `None` is
        honest, not a shortcut: the derivation then falls back to what this
        ledger recorded, and a bot with no recorded run resolves to whatever
        the registry and health monitor say — never to a guess.

        Every read re-reads the registry and the health monitor rather than
        trusting a cached verdict, so a crashed agent is visible on the very
        next poll with no background process having to notice.
        """
        moment = _now() if now is None else now
        key = bot_name.lower().strip()
        row = self.raw_row(key) or {}

        registry = _registry_rows().get(key, {})
        health = _health_row(key)
        evidence = run if run is not None else _run_from_row(row)

        activity = derive_activity(
            bot_name=key,
            now=moment,
            run=evidence,
            last_heartbeat_at=_parse_epoch(row.get("last_heartbeat_at")),
            declared=row.get("declared"),
            health=health,
            registry_status=registry.get("status"),
            archived=bool(registry.get("archived")),
        )

        activity.detail = row.get("detail") or activity.detail
        activity.room_name = room_name
        activity.attributed_by = _attribution(row, room_name)
        activity.since = _since(row, activity.activity, moment)
        activity.claim_ids = list(row.get("claim_ids") or [])
        return activity

    def room_activity(
        self,
        room_name: str,
        members: list[str],
        *,
        run_facts: dict[str, RunEvidence] | None = None,
        claims_by_bot: dict[str, list[str]] | None = None,
        held_by_bot: dict[str, list[str]] | None = None,
        now: float | None = None,
    ) -> list[AgentActivity]:
        """Every member of one room, in the room's own order.

        Order is the caller's — the room is what the operator is looking at,
        and a sorted roster would reorder on every poll. Members with no
        record still appear, as `unknown`, because "we have no record of this
        member" and "this member finished" are different claims.

        `run_facts` and `claims_by_bot` are supplied by the caller because
        both live behind owners this module must not import: the run store
        belongs to `app.gateway.deps`, and claims are a separate store with
        their own lifetime (see `groups/claims.py`).
        """
        moment = _now() if now is None else now
        facts = run_facts or {}
        out: list[AgentActivity] = []
        seen: set[str] = set()
        for raw in members:
            key = (raw or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            activity = self.resolve(key, room_name=room_name, run=facts.get(key), now=moment)
            if claims_by_bot and key in claims_by_bot:
                activity.claim_ids = list(claims_by_bot[key])
            if held_by_bot and key in held_by_bot:
                activity.held_paths = list(held_by_bot[key])
            out.append(activity)
        return out


def _run_from_row(row: dict[str, Any]) -> RunEvidence | None:
    """The ledger's own record of a run, when no live store read was supplied.

    This is what the pure harness path has: what was observed at record time.
    It is **not** the store. A caller holding a `RunManager` must pass a live
    `RunEvidence` through `resolve()` instead, because the store is the
    authority and only the Gateway can read it — `RunManager` is owned by
    `app.gateway.deps` and `packages/harness/` may never import `app.*`
    (`tests/test_harness_boundary.py` enforces that direction).
    """
    run_id = row.get("run_id")
    if not run_id:
        return None
    status = row.get("run_status")
    if not status:
        return None
    return RunEvidence(
        run_id=str(run_id),
        status=status,
        stop_reason=row.get("run_stop_reason"),
        error=row.get("run_error"),
    )


def _attribution(row: dict[str, Any], room_name: str | None) -> Attribution:
    """Say how this agent came to be in this room.

    `roster_match` is an inference from resolved membership and is disclosed as
    such rather than presented as a binding. A run whose recorded room is
    neither this one nor absent is attributed to neither, so one project's
    activity cannot appear in an unrelated room.
    """
    recorded = row.get("room_name")
    if recorded and room_name and recorded == room_name:
        return "explicit"
    return "roster_match"


def _since(row: dict[str, Any], current: str, now: float) -> float:
    """How long the current state has held.

    Re-derived states keep the previous timestamp, so a polled view does not
    show a duration that resets on every refresh.
    """
    previous = row.get("activity")
    if previous == current and row.get("since"):
        return float(row["since"])
    return now


_ledger: ActivityLedger | None = None
_ledger_path: str | None = None
_ledger_lock = threading.Lock()


def get_activity_ledger(storage_path: str | Path | None = None) -> ActivityLedger:
    """Process-wide ledger singleton.

    Rebuilds when `runtime_home()` moves, matching every other store in this
    codebase, so a test that relocates `ALPHA_HOME` gets a real isolated ledger
    rather than a cached one pointing at the previous directory.
    """
    global _ledger, _ledger_path
    with _ledger_lock:
        if storage_path is not None:
            _ledger = ActivityLedger(storage_path)
            try:
                _ledger_path = str(_ledger.storage_path)
            except Exception:
                _ledger_path = None
            return _ledger
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _ledger is None or _ledger_path != live:
            _ledger = ActivityLedger()
            try:
                _ledger_path = str(_ledger.storage_path.resolve())
            except Exception:
                _ledger_path = live
        return _ledger
