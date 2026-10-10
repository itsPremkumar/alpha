"""Durable workflow triggers: schedules as data, never a second cron loop.

Why this exists
---------------
The dynamic workflow plane could compile and run a graph, but a recurring
prompt had nowhere to go: ``DynamicWorkflowService`` answered the recurring
topology with an honest refusal naming a "missing scheduler handoff", and no
store, model, or route existed on the other side of that sentence. So a
workflow that should run every morning had to be re-issued by a human, and the
"handoff" was a paragraph rather than a mechanism.

This module is the mechanism, deliberately split in two:

* **The store** (here) owns *what should fire when* as durable, inspectable,
  owner-scoped data: a cron expression, an interval, or a named event.
* **The firing** stays with the host. Nothing in this module executes a
  workflow, sleeps on a timer, or spawns a thread. ``due_triggers()`` is the
  question a scheduler asks; ``fire_trigger_on_engine()`` is the one call that
  starts a run, and it takes the engine as an argument rather than importing
  one — the Gateway's supervised ``workflow_triggers`` loop (default off) and
  the ``POST /api/workflows/triggers/{id}/fire`` route are its only intended
  callers. That is the whole "ride the existing scheduler, do not become a
  second cron owner" contract expressed as code instead of prose.

Honesty rules
-------------
* **The store is single-Gateway, exactly like the lease store and the event
  log beside it.** An atomically-replaced local JSON file: restart-recoverable
  for one Gateway process, not cross-process coordination. ``worker_id`` stays
  a pid for the same reason.
* **A schedule that can never match is refused at creation**, with the real
  reason, rather than stored and silently never firing.
* **``fire_count``/``max_fires`` are durable totals.** A resumed or re-armed
  schedule keeps counting; re-arming never resets what already ran, because a
  "fresh allowance" would let an unbounded trigger dodge its own bound.
* **Firing reports the run it started, or the real refusal.** An unregistered
  workflow, a disabled trigger, and a store failure each end in a typed
  outcome; nothing fabricates a run id.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "CRON_FIELD_BOUNDS",
    "MAX_INTERVAL_SECONDS",
    "MIN_INTERVAL_SECONDS",
    "TriggerFireResult",
    "TriggerKind",
    "TriggerStore",
    "TriggerStoreError",
    "WorkflowTrigger",
    "cron_next_after",
    "parse_cron_expression",
]

#: Bounds on an interval trigger. The floor is a cost guard (a 1-second
#: interval is a hot loop wearing a trigger costume); the ceiling keeps a typo
#: ("100000000") from parking a schedule past the lifetime of the deployment.
MIN_INTERVAL_SECONDS = 60.0
MAX_INTERVAL_SECONDS = 30 * 24 * 3600.0

#: One lock for every trigger store in the process: operations are
#: microsecond-scale and a single guard is simpler to reason about than a
#: per-directory one. It does NOT protect against another process — see the
#: module docstring's single-Gateway boundary.
_PERSIST_LOCK = threading.RLock()

_SCHEMA_VERSION = 1

#: Inclusive bounds for the five cron fields (minute, hour, day-of-month,
#: month, day-of-week). Day-of-week follows cron's own convention: 0 and 7 are
#: both Sunday.
CRON_FIELD_BOUNDS: tuple[tuple[int, int], ...] = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))

#: How far ahead ``cron_next_after`` searches before declaring an expression
#: unsatisfiable. Eight years, so a legitimate rare schedule (Feb 29 leap-day,
#: a specific weekday that lines up with a date) is honoured rather than
#: refused; the walk skips whole months and days, so the horizon costs a few
#: thousand iterations rather than millions of minute-steps. A schedule that
#: does not fire within eight years is not a slow schedule, it is an
#: impossible one.
_CRON_SEARCH_DAYS = 8 * 366


class TriggerStoreError(RuntimeError):
    """The trigger store could not be read or written (fail-closed, never silent)."""


class TriggerKind(StrEnum):
    """How a trigger decides it is due.

    ``CRON`` and ``INTERVAL`` are time-based and answered by
    :meth:`TriggerStore.due_triggers`; ``EVENT`` has no time basis at all and
    is fired explicitly by a host, which is why it is a separate kind rather
    than an interval of zero.
    """

    CRON = "cron"
    INTERVAL = "interval"
    EVENT = "event"


def _parse_field(spec: str, low: int, high: int, field_name: str) -> frozenset[int]:
    """Expand one cron field into the set of values it matches.

    Supports ``*``, ``*/step``, single values, ranges ``a-b``, and
    ``a-b/step`` / ``*/step`` combinations. Every other shape — including an
    empty field or a reversed range — is a ``ValueError`` naming the field, so
    a malformed expression is refused at creation instead of silently matching
    nothing.
    """
    spec = spec.strip()
    if not spec:
        raise ValueError(f"cron {field_name} field is empty")
    values: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"cron {field_name} field has an empty list entry: {spec!r}")
        step = 1
        if "/" in part:
            base, _, step_text = part.partition("/")
            try:
                step = int(step_text)
            except ValueError as exc:
                raise ValueError(f"cron {field_name} step {step_text!r} is not an integer") from exc
            if step < 1:
                raise ValueError(f"cron {field_name} step must be >= 1, got {step}")
            part = base.strip()
        if part in ("*", ""):
            start, end = low, high
        elif "-" in part:
            start_text, _, end_text = part.partition("-")
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise ValueError(f"cron {field_name} range {part!r} is not a pair of integers") from exc
        else:
            try:
                start = end = int(part)
            except ValueError as exc:
                raise ValueError(f"cron {field_name} value {part!r} is not an integer") from exc
        if start > end:
            raise ValueError(f"cron {field_name} range {part!r} is reversed ({start} > {end})")
        if start < low or end > high:
            raise ValueError(f"cron {field_name} entry {part!r} leaves its allowed range {low}-{high}")
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError(f"cron {field_name} field matches no value: {spec!r}")
    return frozenset(values)


def parse_cron_expression(expression: str) -> tuple[frozenset[int], ...]:
    """Parse a 5-field cron expression into per-field match sets.

    Returns ``(minutes, hours, days_of_month, months, days_of_week)`` with
    day-of-week 0 and 7 both meaning Sunday. Raises ``ValueError`` naming the
    offending field for anything it cannot parse exactly.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("cron expression must be a non-empty string")
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(f"cron expression must have 5 fields (minute hour day-of-month month day-of-week), got {len(fields)}: {expression!r}")
    parsed = tuple(_parse_field(field, low, high, name) for field, (low, high), name in zip(fields, CRON_FIELD_BOUNDS, ("minute", "hour", "day-of-month", "month", "day-of-week")))
    # Normalize day-of-week 7 to 0 so the match test has one Sunday.
    dow = parsed[4]
    if 7 in dow:
        dow = frozenset((0 if value == 7 else value) for value in dow)
        parsed = (*parsed[:4], dow)
    return parsed


def _day_matches(parsed: tuple[frozenset[int], ...], moment: datetime) -> bool:
    """Whether ``moment``'s date satisfies the day-of-month/day-of-week fields.

    Day-of-month and day-of-week follow the Vixie-cron rule: when BOTH are
    restricted the day matches if EITHER matches; when only one is restricted
    that one decides. Collapsing them into an AND is the classic cron bug —
    ``0 9 1 * 1`` ("09:00 on the 1st or on Mondays") would then fire only when
    the 1st is a Monday, i.e. almost never — so the rule is implemented and
    documented rather than inferred.
    """
    _minutes, _hours, days, _months, dows = parsed
    dom_any = len(days) == 31  # every day of the month is listed
    dow_any = len(dows) == 7  # every day of the week is listed
    dom_hit = moment.day in days
    dow_hit = ((moment.weekday() + 1) % 7) in dows
    if dom_any or dow_any:
        return dom_hit and dow_hit
    return dom_hit or dow_hit


def cron_next_after(expression: str, after_epoch: float) -> float | None:
    """The next epoch second at which ``expression`` fires, strictly after ``after_epoch``.

    Walks forward by whole months, days, hours and finally minutes — skipping
    rather than stepping through stretches that cannot match — bounded by
    :data:`_CRON_SEARCH_DAYS`. Returns ``None`` when nothing matches inside
    that horizon; the caller refuses the schedule at creation with this as the
    reason rather than storing a trigger that never fires.
    """
    parsed = parse_cron_expression(expression)
    minutes, hours, _days, months, _dows = parsed
    moment = datetime.fromtimestamp(after_epoch, tz=UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    deadline = moment + timedelta(days=_CRON_SEARCH_DAYS)
    while moment <= deadline:
        if moment.month not in months:
            # No month in the rest of this year can match: jump to the 1st of
            # the next month, which is the earliest instant a later month could
            # possibly match.
            moment = datetime(moment.year + 1, 1, 1, tzinfo=UTC) if moment.month == 12 else datetime(moment.year, moment.month + 1, 1, tzinfo=UTC)
            continue
        if not _day_matches(parsed, moment):
            moment = (moment + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if moment.hour not in hours:
            moment = moment.replace(minute=0) + timedelta(hours=1)
            continue
        if moment.minute not in minutes:
            moment = moment + timedelta(minutes=1)
            continue
        return moment.timestamp()
    return None


class WorkflowTrigger(BaseModel):
    """One durable schedule: a workflow, a rule, and the state every fire passes in."""

    trigger_id: str
    workflow_id: str
    owner_id: str | None = None
    kind: TriggerKind
    #: Cron expression for ``kind=cron`` (5 fields, UTC).
    expression: str | None = None
    #: Seconds between fires for ``kind=interval``.
    interval_seconds: float | None = None
    #: Signal name for ``kind=event``; fired explicitly by a host.
    event_name: str | None = None
    #: Whether the schedule may fire. Disarmed by an operator, and set False
    #: automatically when ``max_fires`` is reached (the reason is recorded).
    enabled: bool = True
    #: Why a trigger is disabled, when it was not an operator choice.
    disabled_reason: str | None = None
    #: Values every fire seeds the new run's state with.
    input_state: dict[str, Any] = Field(default_factory=dict)
    #: Hard cap on fires. ``None`` is unbounded — allowed, and the loop that
    #: fires it is default-off, so an unbounded trigger is a declared choice.
    max_fires: int | None = Field(default=None, ge=1)
    fire_count: int = 0
    last_fired_at: float | None = None
    #: Epoch seconds when the trigger next becomes due (time-based kinds only).
    next_fire_at: float | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    #: Free-text provenance for the schedule (the prompt or request that made it).
    note: str = ""
    #: Monotonic fencing token: advances by 1 on every accepted fire. A reader
    #: that holds a stale token loses the compare-and-set race, so a double-fire
    #: from a stale scheduler is refused rather than silently executed twice.
    fire_token: int = 0

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class TriggerFireResult(BaseModel):
    """What one fire attempt did — a started run, or the real refusal."""

    fired: bool
    trigger_id: str
    reason: str = ""
    run_id: str | None = None
    workflow_id: str | None = None
    fire_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class TriggerStore:
    """Durable registry of workflow triggers, atomically replaced on every mutation.

    Construct with ``store_dir`` to persist (the Gateway does); construct
    without it for a process-local store, which is what a test or a throwaway
    harness wants. A corrupt or unreadable file raises :class:`TriggerStoreError`
    rather than being treated as an empty registry — "I could not look" and
    "there is nothing scheduled" lead to opposite decisions.
    """

    def __init__(self, store_dir: Path | None = None, *, store_name: str = "triggers.json") -> None:
        self._path = (Path(store_dir) / store_name) if store_dir is not None else None
        self._triggers: dict[str, WorkflowTrigger] = {}
        if self._path is not None:
            self._load()

    # ---------------------------------------------------------------- persistence

    @property
    def store_path(self) -> Path | None:
        """Where triggers are persisted, or ``None`` when process-local."""
        return self._path

    def _load(self) -> None:
        path = self._path
        if path is None:
            return
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            raise TriggerStoreError(f"failed to read trigger store {path}: {exc}") from exc
        if not raw.strip():
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TriggerStoreError(f"trigger store {path} is not valid JSON: {exc}") from exc
        version = payload.get("schema_version")
        if version != _SCHEMA_VERSION:
            raise TriggerStoreError(f"trigger store {path} has unsupported schema_version {version!r} (expected {_SCHEMA_VERSION})")
        for key, entry in (payload.get("triggers") or {}).items():
            try:
                self._triggers[key] = WorkflowTrigger.model_validate(entry)
            except Exception as exc:  # noqa: BLE001 - a malformed record is a store error, not a crash
                raise TriggerStoreError(f"trigger store {path} has a malformed trigger for {key!r}: {exc}") from exc

    def _persist(self) -> None:
        path = self._path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        try:
            with _PERSIST_LOCK:
                payload = {
                    "schema_version": _SCHEMA_VERSION,
                    "triggers": {key: trigger.to_dict() for key, trigger in self._triggers.items()},
                }
                tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
                os.replace(tmp, path)
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise TriggerStoreError(f"failed to persist trigger store {path}: {exc}") from exc

    # -------------------------------------------------------------------- records

    def add(self, trigger: WorkflowTrigger) -> WorkflowTrigger:
        """Register a new trigger. A duplicate id is refused, never overwritten."""
        with _PERSIST_LOCK:
            if trigger.trigger_id in self._triggers:
                raise ValueError(f"trigger '{trigger.trigger_id}' already exists")
            self._validate(trigger)
            self._triggers[trigger.trigger_id] = trigger
            self._persist()
        return trigger

    def _validate(self, trigger: WorkflowTrigger) -> None:
        """Refuse a schedule that is unrunnable, before it is stored.

        An interval outside the bounds, an unparsable or unsatisfiable cron
        expression, an event kind with no name, or a missing workflow id each
        end here with the real reason — the alternative is a stored trigger
        that silently never fires, which is the failure this module exists to
        make impossible.
        """
        if not trigger.workflow_id:
            raise ValueError("trigger must name a workflow_id")
        if trigger.kind is TriggerKind.CRON:
            if not trigger.expression:
                raise ValueError("a cron trigger must declare a 5-field expression")
            if cron_next_after(trigger.expression, time.time()) is None:
                raise ValueError(f"cron expression {trigger.expression!r} matches no time within the search horizon; it would never fire")
        elif trigger.kind is TriggerKind.INTERVAL:
            if trigger.interval_seconds is None:
                raise ValueError("an interval trigger must declare interval_seconds")
            if not (MIN_INTERVAL_SECONDS <= float(trigger.interval_seconds) <= MAX_INTERVAL_SECONDS):
                raise ValueError(f"interval_seconds must be between {MIN_INTERVAL_SECONDS:g} and {MAX_INTERVAL_SECONDS:g}, got {trigger.interval_seconds}")
        elif trigger.kind is TriggerKind.EVENT:
            if not trigger.event_name or not str(trigger.event_name).strip():
                raise ValueError("an event trigger must declare an event_name")
        else:  # pragma: no cover - StrEnum is exhaustive
            raise ValueError(f"unsupported trigger kind {trigger.kind!r}")

    def get(self, trigger_id: str) -> WorkflowTrigger | None:
        return self._triggers.get(trigger_id)

    def list(self, *, owner_id: str | None = None, workflow_id: str | None = None) -> list[WorkflowTrigger]:
        """Triggers, owner-scoped when an owner is given.

        ``owner_id=None`` is the unscoped administrative read; a request-scoped
        caller must pass the server-resolved owner, exactly like every other
        workflow-plane read.
        """
        triggers = list(self._triggers.values())
        if owner_id is not None:
            triggers = [trigger for trigger in triggers if trigger.owner_id == owner_id]
        if workflow_id is not None:
            triggers = [trigger for trigger in triggers if trigger.workflow_id == workflow_id]
        return sorted(triggers, key=lambda trigger: (trigger.next_fire_at if trigger.next_fire_at is not None else float("inf"), trigger.trigger_id))

    def set_enabled(self, trigger_id: str, enabled: bool, *, reason: str | None = None) -> WorkflowTrigger:
        """Arm or disarm a schedule. Disarming records its reason."""
        with _PERSIST_LOCK:
            trigger = self._triggers.get(trigger_id)
            if trigger is None:
                raise KeyError(f"trigger '{trigger_id}' not found")
            trigger.enabled = enabled
            trigger.disabled_reason = None if enabled else (reason or "disabled by operator")
            if enabled and trigger.kind is TriggerKind.INTERVAL and trigger.next_fire_at is None:
                trigger.next_fire_at = time.time() + float(trigger.interval_seconds or MIN_INTERVAL_SECONDS)
            trigger.touch()
            self._persist()
            return trigger

    def delete(self, trigger_id: str) -> bool:
        with _PERSIST_LOCK:
            if trigger_id not in self._triggers:
                return False
            del self._triggers[trigger_id]
            self._persist()
            return True

    def due_triggers(self, now: float | None = None, *, limit: int = 50) -> list[WorkflowTrigger]:
        """Enabled time-based triggers whose fire time has arrived.

        Event triggers are never returned: they have no time basis, so a
        scheduler asking "what is due?" must not be handed a trigger it cannot
        decide about. ``max_fires`` is respected here so a bounded schedule
        stops being due without needing a fire attempt to discover it.
        """
        moment = time.time() if now is None else float(now)
        due = [
            trigger
            for trigger in self._triggers.values()
            if trigger.enabled and trigger.kind in (TriggerKind.CRON, TriggerKind.INTERVAL) and trigger.next_fire_at is not None and trigger.next_fire_at <= moment and (trigger.max_fires is None or trigger.fire_count < trigger.max_fires)
        ]
        due.sort(key=lambda trigger: (trigger.next_fire_at or 0.0, trigger.trigger_id))
        return due[: max(0, limit)]

    def record_fire(self, trigger_id: str, fired_at: float | None = None) -> WorkflowTrigger:
        """Advance a fired schedule and return it.

        The next fire time is computed from the CURRENT due time, not from
        now: a scheduler that fires a trigger late (a paused loop, a suspended
        host) must not let the lateness accumulate into every subsequent fire,
        and must not let it reset the schedule to "now" either — an interval
        measured from the fire moment would slowly drift against a cron twin.
        A trigger that reaches ``max_fires`` is disarmed with the reason
        recorded, so an exhausted schedule reads as complete rather than as a
        silent no-op.
        """
        with _PERSIST_LOCK:
            trigger = self._triggers.get(trigger_id)
            if trigger is None:
                raise KeyError(f"trigger '{trigger_id}' not found")
            fired = time.time() if fired_at is None else float(fired_at)
            trigger.fire_token += 1
            trigger.fire_count += 1
            trigger.last_fired_at = fired
            if trigger.kind is TriggerKind.CRON and trigger.expression:
                base = trigger.next_fire_at if trigger.next_fire_at is not None else fired
                nxt = cron_next_after(trigger.expression, base)
                trigger.next_fire_at = nxt
                if nxt is None:
                    trigger.enabled = False
                    trigger.disabled_reason = "cron expression matched no further time inside the search horizon"
            elif trigger.kind is TriggerKind.INTERVAL:
                base = trigger.next_fire_at if trigger.next_fire_at is not None else fired
                trigger.next_fire_at = base + float(trigger.interval_seconds or MIN_INTERVAL_SECONDS)
            if trigger.max_fires is not None and trigger.fire_count >= trigger.max_fires:
                trigger.enabled = False
                trigger.disabled_reason = f"max_fires ({trigger.max_fires}) reached"
            trigger.touch()
            self._persist()
            return trigger

    def forget_workflow(self, workflow_id: str) -> int:
        """Drop every trigger for a workflow (e.g. a deleted definition)."""
        with _PERSIST_LOCK:
            doomed = [key for key, trigger in self._triggers.items() if trigger.workflow_id == workflow_id]
            for key in doomed:
                del self._triggers[key]
            if doomed:
                self._persist()
            return len(doomed)

    def try_claim_fire(
        self,
        trigger_id: str,
        *,
        expected_token: int,
        fired_at: float | None = None,
    ) -> tuple[bool, WorkflowTrigger | None, str]:
        """Compare-and-set fire claim: succeeds only when ``fire_token`` still equals ``expected_token``.

        Returns ``(claimed, trigger, reason)``. A successful claim advances the
        token by 1 and records the fire (same bookkeeping as ``record_fire``).
        A stale reader — a scheduler that read the trigger, was descheduled,
        and woke after another worker already fired it — loses the race and
        receives ``(False, None, reason)`` with the current token named, so it
        can re-read rather than double-fire. This is the fencing primitive a
        multi-worker deployment needs; it is atomic within this process (under
        the persist lock) and across processes only to the extent the
        underlying file store's atomic replace gives atomic read-modify-write —
        it does not claim cross-process exactly-once.
        """
        with _PERSIST_LOCK:
            trigger = self._triggers.get(trigger_id)
            if trigger is None:
                return False, None, f"trigger '{trigger_id}' not found"
            if trigger.fire_token != expected_token:
                return False, None, (f"stale fire token {expected_token} (current {trigger.fire_token}): another worker already fired this occurrence")
            fired = time.time() if fired_at is None else float(fired_at)
            trigger.fire_token += 1
            trigger.fire_count += 1
            trigger.last_fired_at = fired
            if trigger.kind is TriggerKind.CRON and trigger.expression:
                base = trigger.next_fire_at if trigger.next_fire_at is not None else fired
                nxt = cron_next_after(trigger.expression, base)
                trigger.next_fire_at = nxt
                if nxt is None:
                    trigger.enabled = False
                    trigger.disabled_reason = "cron expression matched no further time inside the search horizon"
            elif trigger.kind is TriggerKind.INTERVAL:
                base = trigger.next_fire_at if trigger.next_fire_at is not None else fired
                trigger.next_fire_at = base + float(trigger.interval_seconds or MIN_INTERVAL_SECONDS)
            if trigger.max_fires is not None and trigger.fire_count >= trigger.max_fires:
                trigger.enabled = False
                trigger.disabled_reason = f"max_fires ({trigger.max_fires}) reached"
            trigger.touch()
            self._persist()
            return True, trigger, ""


def fire_trigger_on_engine(engine: Any, store: TriggerStore, trigger_id: str, *, fired_at: float | None = None) -> TriggerFireResult:
    """Start one run for a due trigger, then advance the schedule.

    ``engine`` is passed in rather than imported: this module owns schedule
    bookkeeping, and the engine owns runs. The order is load-bearing — the run
    is started BEFORE the schedule advances, so a failed start (unregistered
    workflow, a start_run refusal) leaves the trigger due again instead of
    consuming a fire on work that never happened.

    The engine's own honesty rules apply unchanged: a workflow whose nodes
    cannot be executed still starts, and its nodes fail with the real reason.
    This function claims to have started a run, never that the run succeeded.
    """
    trigger = store.get(trigger_id)
    if trigger is None:
        return TriggerFireResult(fired=False, trigger_id=trigger_id, reason="trigger not found")
    if not trigger.enabled:
        return TriggerFireResult(fired=False, trigger_id=trigger_id, reason=f"trigger is disabled: {trigger.disabled_reason or 'no reason recorded'}")
    if engine is None or not hasattr(engine, "start_run"):
        return TriggerFireResult(fired=False, trigger_id=trigger_id, reason="no workflow engine is bound to fire this trigger")
    try:
        run = engine.start_run(trigger.workflow_id, initial_state=dict(trigger.input_state), owner_id=trigger.owner_id)
    except KeyError as exc:
        return TriggerFireResult(fired=False, trigger_id=trigger_id, workflow_id=trigger.workflow_id, reason=f"workflow not registered: {exc}")
    except Exception as exc:  # noqa: BLE001 - a refused start is a result, not a crash
        return TriggerFireResult(fired=False, trigger_id=trigger_id, workflow_id=trigger.workflow_id, reason=f"{type(exc).__name__}: {exc}")
    advanced = store.record_fire(trigger_id, fired_at=fired_at)
    return TriggerFireResult(fired=True, trigger_id=trigger_id, run_id=run.run_id, workflow_id=trigger.workflow_id, fire_count=advanced.fire_count)
