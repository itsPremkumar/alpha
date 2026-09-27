"""The session lifecycle state machine.

Design
------
A **session** in Alpha is a thread plus its durable run lineage. A
:data:`SessionState` is therefore a *derived* projection, never a second source
of truth: :func:`derive_session_state` folds the signals that existing owners
already hold into one named state, and
:data:`SESSION_STATE_TRANSITIONS` declares which moves are legal so a caller
that wants to *assert* a transition gets a loud error rather than writing a
meaningless string.

The three classes matter more than the seventeen names, because they are the
operating decisions a host actually has to make:

:class:`SessionStateClass.ACTIVE`
    Execution is happening or about to happen. Holds worker capacity and the
    thread's active-operation reservation.
:class:`SessionStateClass.WAITING`
    The task is **alive and durable** but deliberately parked on an external
    condition. It must not consume active worker capacity, and it must be
    resumable without user action once the condition clears. This is the class
    that makes ``WAITING_NETWORK`` different from ``FAILED``.
:class:`SessionStateClass.TERMINAL`
    No further automatic progress. ``COMPLETED`` still carries no claim of
    verification -- acceptance remains an overlay
    (:mod:`alpha.runtime.runs.verification`).

Honesty rules encoded here
--------------------------
* ``UNKNOWN`` is never a session state. This module has no "we could not tell"
  state, because every input is a server-owned durable fact; a caller that
  cannot supply one is a programming error, not a runtime condition.
* A parked session is not a failed session. ``FAILED`` is terminal; every
  ``WAITING_*`` state is resumable.
* ``is_resumable`` is the property the recovery manager asks. It is true for
  ``ACTIVE`` (still going) and ``WAITING`` (parked but recoverable), and false
  for ``TERMINAL``. A recovery pass that re-admits a ``TERMINAL`` session is a
  bug, and :func:`validate_transition` will say so.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from alpha.runtime.runs.schemas import RunStatus

__all__ = [
    "ACTIVE_SESSION_STATES",
    "SESSION_STATE_CLASSES",
    "SESSION_STATE_TRANSITIONS",
    "TERMINAL_SESSION_STATES",
    "WAITING_SESSION_STATES",
    "IllegalSessionTransition",
    "SessionState",
    "SessionStateClass",
    "SessionStateReport",
    "SessionStateSignal",
    "can_transition",
    "derive_session_state",
    "is_active",
    "is_resumable",
    "is_terminal",
    "session_state_class",
    "transition_table",
    "validate_transition",
]


class SessionState(StrEnum):
    """A named point in a session's durable lifecycle.

    The vocabulary is the one the durable-runtime contract requires. It is
    intentionally wider than :class:`RunStatus` because ``RunStatus`` describes
    one run attempt, while a session outlives any single attempt: a run that
    dies and is later resumed is one ``RECOVERING``/``RUNNING`` session with
    several terminal run rows behind it.
    """

    CREATED = "created"
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_NETWORK = "waiting_network"
    WAITING_PROVIDER = "waiting_provider"
    WAITING_PERMISSION = "waiting_permission"
    WAITING_RESOURCE = "waiting_resource"
    WAITING_USER = "waiting_user"
    PAUSED = "paused"
    COMPACTING = "compacting"
    RECOVERING = "recovering"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SessionStateClass(StrEnum):
    """The operating decision a state implies."""

    ACTIVE = "active"
    WAITING = "waiting"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class SessionStateSignal:
    """One server-owned fact the session state is derived from.

    Every field is optional and every field is *server-derived*. Absence means
    "no such condition is active", never "unknown" -- there is deliberately no
    tri-state here. ``run_status`` is the only field the run lifecycle owns;
    the rest are conditions other subsystems already track.
    """

    #: Durable status of the session's current run attempt, if any.
    run_status: RunStatus | None = None
    #: A recovery pass is evaluating/admitting this session right now.
    recovering: bool = False
    #: An admission-retry is scheduled (bounded backoff, not a crash).
    retrying: bool = False
    #: The agent is building or revising an execution plan.
    planning: bool = False
    #: Context compaction is in flight.
    compacting: bool = False
    #: An approval is outstanding.
    permission_pending: bool = False
    #: The user is explicitly asked to answer something.
    user_input_pending: bool = False
    #: No worker slot / lease is currently obtainable.
    resource_pending: bool = False
    #: Connectivity is not ``ONLINE`` (covers ``DEGRADED``, ``OFFLINE`` and
    #: ``UNKNOWN``). Coarse on purpose: a degraded link still blocks a provider
    #: call, so the session parks either way and the *reason* is carried
    #: separately for display.
    network_unavailable: bool = False
    #: Every model provider is failing (auth, quota, outage). Distinct from
    #: ``network_unavailable``: the host has a link, the provider does not work.
    provider_unavailable: bool = False
    #: A hard precondition cannot be satisfied and retrying will not help.
    permanently_blocked: bool = False
    #: The user or an operator paused the session.
    paused: bool = False


@dataclass(frozen=True, slots=True)
class SessionStateReport:
    """A derived session state and the reasons that produced it.

    ``reasons`` is ordered by precedence, so ``reasons[0]`` is *the* reason the
    session is in this state. Callers that need to render "Alpha is waiting for
    network connectivity" read it from here rather than re-deriving.
    """

    state: SessionState
    reasons: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def state_class(self) -> SessionStateClass:
        return SESSION_STATE_CLASSES[self.state]

    @property
    def primary_reason(self) -> str | None:
        return self.reasons[0] if self.reasons else None

    @property
    def is_active(self) -> bool:
        return self.state_class is SessionStateClass.ACTIVE

    @property
    def is_waiting(self) -> bool:
        return self.state_class is SessionStateClass.WAITING

    @property
    def is_terminal(self) -> bool:
        return self.state_class is SessionStateClass.TERMINAL

    @property
    def is_resumable(self) -> bool:
        """True when automatic progress is still possible without user action."""
        return self.state_class in (SessionStateClass.ACTIVE, SessionStateClass.WAITING)

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "state_class": self.state_class.value,
            "reasons": list(self.reasons),
            "primary_reason": self.primary_reason,
            "is_resumable": self.is_resumable,
            "metadata": dict(self.metadata),
        }


class IllegalSessionTransition(ValueError):
    """A requested session state change is not in the transition table.

    Raised rather than clamped, because a silently-ignored transition is how a
    recovery pass ends up re-admitting a cancelled task.
    """

    def __init__(self, current: SessionState, requested: SessionState, *, allowed: Iterable[SessionState]) -> None:
        allowed_values = sorted(state.value for state in allowed)
        super().__init__(f"illegal session transition {current.value!r} -> {requested.value!r}; allowed from {current.value!r}: {allowed_values or ['<none>']}")
        self.current = current
        self.requested = requested
        self.allowed: frozenset[SessionState] = frozenset(allowed)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

SESSION_STATE_CLASSES: Final[Mapping[SessionState, SessionStateClass]] = MappingProxyType(
    {
        # Executing or about to execute. Holds worker capacity.
        SessionState.CREATED: SessionStateClass.ACTIVE,
        SessionState.QUEUED: SessionStateClass.ACTIVE,
        SessionState.PLANNING: SessionStateClass.ACTIVE,
        SessionState.RUNNING: SessionStateClass.ACTIVE,
        SessionState.COMPACTING: SessionStateClass.ACTIVE,
        SessionState.RECOVERING: SessionStateClass.ACTIVE,
        SessionState.RETRYING: SessionStateClass.ACTIVE,
        # Alive and durable, parked on an external condition. Releases worker
        # capacity, stays resumable, and is never reported as a failure.
        SessionState.WAITING_NETWORK: SessionStateClass.WAITING,
        SessionState.WAITING_PROVIDER: SessionStateClass.WAITING,
        SessionState.WAITING_PERMISSION: SessionStateClass.WAITING,
        SessionState.WAITING_RESOURCE: SessionStateClass.WAITING,
        SessionState.WAITING_USER: SessionStateClass.WAITING,
        SessionState.PAUSED: SessionStateClass.WAITING,
        # No further automatic progress.
        SessionState.BLOCKED: SessionStateClass.TERMINAL,
        SessionState.COMPLETED: SessionStateClass.TERMINAL,
        SessionState.FAILED: SessionStateClass.TERMINAL,
        SessionState.CANCELLED: SessionStateClass.TERMINAL,
    }
)

ACTIVE_SESSION_STATES: Final[frozenset[SessionState]] = frozenset(state for state, klass in SESSION_STATE_CLASSES.items() if klass is SessionStateClass.ACTIVE)
WAITING_SESSION_STATES: Final[frozenset[SessionState]] = frozenset(state for state, klass in SESSION_STATE_CLASSES.items() if klass is SessionStateClass.WAITING)
TERMINAL_SESSION_STATES: Final[frozenset[SessionState]] = frozenset(state for state, klass in SESSION_STATE_CLASSES.items() if klass is SessionStateClass.TERMINAL)

#: Every state must be classified exactly once. A new ``SessionState`` member
#: that nobody classified is a build-time bug, not a runtime surprise.
assert set(SESSION_STATE_CLASSES) == set(SessionState), "every SessionState needs exactly one class"
assert ACTIVE_SESSION_STATES and WAITING_SESSION_STATES and TERMINAL_SESSION_STATES, "all three classes must be non-empty"


def session_state_class(state: SessionState) -> SessionStateClass:
    """Return the operating class of *state*."""
    return SESSION_STATE_CLASSES[state]


def is_active(state: SessionState) -> bool:
    return SESSION_STATE_CLASSES[state] is SessionStateClass.ACTIVE


def is_terminal(state: SessionState) -> bool:
    return SESSION_STATE_CLASSES[state] is SessionStateClass.TERMINAL


def is_resumable(state: SessionState) -> bool:
    """True while automatic progress is still possible (active or parked)."""
    return SESSION_STATE_CLASSES[state] in (SessionStateClass.ACTIVE, SessionStateClass.WAITING)


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

#: Legal session-state moves. Two invariants hold for every entry and are
#: pinned by ``tests/test_durable_session_state_machine.py``:
#:
#: 1. **Terminal is final.** A ``TERMINAL`` state has no outgoing edge, not
#:    even to itself.
#: 2. **Self-transition is always legal for a non-terminal state.** A repeated
#:    observation of the same state is not an error; a heartbeat that
#:    re-reports ``RUNNING`` must not raise. Real change is enforced by
#:    comparing to the *previous* value. The rows below therefore list only
#:    genuine state changes, and :func:`allowed_transitions` adds the self edge.
_TERMINAL: tuple[SessionState, ...] = ()

SESSION_STATE_TRANSITIONS: Final[Mapping[SessionState, frozenset[SessionState]]] = MappingProxyType(
    {
        SessionState.CREATED: frozenset({SessionState.QUEUED, SessionState.RUNNING, SessionState.PLANNING, SessionState.CANCELLED, SessionState.BLOCKED, SessionState.FAILED}),
        SessionState.QUEUED: frozenset(
            {
                SessionState.RUNNING,
                SessionState.PLANNING,
                SessionState.WAITING_RESOURCE,
                SessionState.WAITING_NETWORK,
                SessionState.WAITING_PROVIDER,
                SessionState.PAUSED,
                SessionState.CANCELLED,
                SessionState.BLOCKED,
                SessionState.FAILED,
            }
        ),
        SessionState.PLANNING: frozenset({SessionState.RUNNING, SessionState.RETRYING, SessionState.WAITING_NETWORK, SessionState.WAITING_PROVIDER, SessionState.CANCELLED, SessionState.BLOCKED, SessionState.FAILED}),
        SessionState.RUNNING: frozenset(
            {
                SessionState.COMPLETED,
                SessionState.COMPACTING,
                SessionState.RETRYING,
                SessionState.RECOVERING,
                SessionState.WAITING_NETWORK,
                SessionState.WAITING_PROVIDER,
                SessionState.WAITING_PERMISSION,
                SessionState.WAITING_RESOURCE,
                SessionState.WAITING_USER,
                SessionState.PAUSED,
                SessionState.CANCELLED,
                SessionState.BLOCKED,
                SessionState.FAILED,
            }
        ),
        # Compaction preserves the execution phase it interrupted: a compaction
        # entered from RUNNING returns to RUNNING, not to PLANNING.
        SessionState.COMPACTING: frozenset({SessionState.RUNNING, SessionState.RETRYING, SessionState.RECOVERING, SessionState.WAITING_NETWORK, SessionState.CANCELLED, SessionState.FAILED}),
        SessionState.RECOVERING: frozenset(
            {
                SessionState.RUNNING,
                SessionState.RETRYING,
                SessionState.WAITING_NETWORK,
                SessionState.WAITING_PROVIDER,
                SessionState.WAITING_PERMISSION,
                SessionState.WAITING_USER,
                SessionState.COMPLETED,
                SessionState.PAUSED,
                SessionState.CANCELLED,
                SessionState.BLOCKED,
                SessionState.FAILED,
            }
        ),
        SessionState.RETRYING: frozenset(
            {
                SessionState.RUNNING,
                SessionState.RECOVERING,
                SessionState.WAITING_NETWORK,
                SessionState.WAITING_PROVIDER,
                SessionState.WAITING_RESOURCE,
                SessionState.CANCELLED,
                SessionState.BLOCKED,
                SessionState.FAILED,
            }
        ),
        # Every waiting state can go straight back to work once its condition
        # clears, and can be cancelled or escalate to a hard stop at any time.
        SessionState.WAITING_NETWORK: frozenset({SessionState.RUNNING, SessionState.RECOVERING, SessionState.RETRYING, SessionState.PAUSED, SessionState.CANCELLED, SessionState.BLOCKED, SessionState.FAILED}),
        SessionState.WAITING_PROVIDER: frozenset({SessionState.RUNNING, SessionState.RECOVERING, SessionState.RETRYING, SessionState.PAUSED, SessionState.CANCELLED, SessionState.BLOCKED, SessionState.FAILED}),
        SessionState.WAITING_PERMISSION: frozenset({SessionState.RUNNING, SessionState.RECOVERING, SessionState.CANCELLED, SessionState.BLOCKED, SessionState.FAILED}),
        SessionState.WAITING_RESOURCE: frozenset({SessionState.QUEUED, SessionState.RUNNING, SessionState.RETRYING, SessionState.PAUSED, SessionState.CANCELLED, SessionState.BLOCKED, SessionState.FAILED}),
        SessionState.WAITING_USER: frozenset({SessionState.RUNNING, SessionState.RECOVERING, SessionState.PAUSED, SessionState.CANCELLED, SessionState.BLOCKED, SessionState.FAILED}),
        SessionState.PAUSED: frozenset({SessionState.QUEUED, SessionState.RUNNING, SessionState.RECOVERING, SessionState.CANCELLED, SessionState.FAILED}),
        SessionState.BLOCKED: _TERMINAL,
        SessionState.COMPLETED: _TERMINAL,
        SessionState.FAILED: _TERMINAL,
        SessionState.CANCELLED: _TERMINAL,
    }
)

# Every state needs a row, terminal states must be final, and every non-terminal
# state must have somewhere to go.
assert set(SESSION_STATE_TRANSITIONS) == set(SessionState), "every SessionState needs a transition row"
for _state, _successors in SESSION_STATE_TRANSITIONS.items():
    if is_terminal(_state):
        assert not _successors, f"terminal state {_state.value!r} must have no outgoing transition"
    else:
        assert _successors, f"{_state.value!r} must have at least one successor"


def transition_table() -> Mapping[SessionState, frozenset[SessionState]]:
    """Return the read-only forward-transition table.

    Rows list only *real* moves. A self-transition is legal for every
    non-terminal state and is applied by :func:`allowed_transitions` /
    :func:`can_transition` rather than spelled out in every row, so a reader of
    the table sees only genuine state changes.
    """
    return SESSION_STATE_TRANSITIONS


def allowed_transitions(state: SessionState) -> frozenset[SessionState]:
    """Return the legal successors of *state*, including *state* itself.

    A terminal state returns the empty set: there is no legal move out of it,
    not even back to itself.
    """
    successors = SESSION_STATE_TRANSITIONS[state]
    if not successors:
        return frozenset()
    return successors | {state}


def can_transition(current: SessionState, requested: SessionState) -> bool:
    """Return True when ``current -> requested`` is legal (self included)."""
    if current is requested:
        return not is_terminal(current)
    return requested in SESSION_STATE_TRANSITIONS[current]


def validate_transition(current: SessionState, requested: SessionState) -> SessionState:
    """Return *requested* when the move is legal, else raise.

    Self-transition is legal, so a caller that reports the same state twice
    does not have to guard.
    """
    if can_transition(current, requested):
        return requested
    raise IllegalSessionTransition(current, requested, allowed=allowed_transitions(current))


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------

#: ``RunStatus`` -> session state for a run that reached a terminal state.
#: ``pending``/``running`` are intentionally absent: they resolve through the
#: non-terminal branches of :func:`derive_session_state`, which is where the
#: finer conditions (compacting, waiting, recovering) live.
_TERMINAL_RUN_STATES: Final[Mapping[RunStatus, SessionState]] = MappingProxyType(
    {
        RunStatus.success: SessionState.COMPLETED,
        RunStatus.error: SessionState.FAILED,
        RunStatus.timeout: SessionState.FAILED,
        RunStatus.interrupted: SessionState.CANCELLED,
    }
)

_ACTIVE_RUN_STATES: Final[frozenset[RunStatus]] = frozenset({RunStatus.pending, RunStatus.running})


def derive_session_state(signal: SessionStateSignal, /) -> SessionStateReport:
    """Fold server-owned signals into one named session state.

    Precedence, highest first. The order is the contract: it is what makes
    ``reasons[0]`` the reason a session is parked, and it keeps a cancelled or
    failed session from being reported as merely "waiting" because a stale
    network flag was still set.

    1. terminal run status (``success``/``error``/``timeout``/``interrupted``)
    2. ``permanently_blocked`` -> ``BLOCKED``
    3. ``cancelled`` is expressed by the run status; a bare ``paused`` signal
       is the only non-terminal user intent that outranks a park
    4. in-flight recovery -> ``RECOVERING``
    5. scheduled retry -> ``RETRYING``
    6. waiting conditions, in the order a user would ask about them: network,
       provider, permission, resource, user input
    7. explicit pause
    8. compaction
    9. planning
    10. an active run -> ``RUNNING``; no run at all -> ``CREATED`` when the
        signal is entirely empty, else ``QUEUED``

    A ``WAITING_*`` state is only derived while the run is still active (or
    absent). A run that has already reached a terminal state is reported as
    that terminal state, so a network flap during teardown cannot resurrect a
    finished task as "waiting for network".
    """
    run_status = signal.run_status
    reasons: list[str] = []

    if run_status is not None and run_status in _TERMINAL_RUN_STATES:
        state = _TERMINAL_RUN_STATES[run_status]
        reasons.append(f"run_{run_status.value}")
        return SessionStateReport(state=state, reasons=tuple(reasons), metadata={"run_status": run_status.value})

    if signal.permanently_blocked:
        return SessionStateReport(state=SessionState.BLOCKED, reasons=("permanently_blocked",))

    # A terminal run status is authoritative, so everything below describes a
    # session with no run yet or a live one.
    if signal.paused:
        return SessionStateReport(state=SessionState.PAUSED, reasons=("paused_by_user",))

    # Conditions that outrank in-flight phases: a session that cannot proceed
    # is parked, and reporting it as "recovering" would be a lie about progress.
    if signal.network_unavailable:
        return SessionStateReport(state=SessionState.WAITING_NETWORK, reasons=("network_unavailable",))
    if signal.provider_unavailable:
        return SessionStateReport(state=SessionState.WAITING_PROVIDER, reasons=("provider_unavailable",))
    if signal.permission_pending:
        return SessionStateReport(state=SessionState.WAITING_PERMISSION, reasons=("permission_pending",))
    if signal.resource_pending:
        return SessionStateReport(state=SessionState.WAITING_RESOURCE, reasons=("resource_pending",))
    if signal.user_input_pending:
        return SessionStateReport(state=SessionState.WAITING_USER, reasons=("user_input_pending",))

    if signal.recovering:
        return SessionStateReport(state=SessionState.RECOVERING, reasons=("recovering",))
    if signal.compacting:
        return SessionStateReport(state=SessionState.COMPACTING, reasons=("compacting",))
    if signal.retrying:
        return SessionStateReport(state=SessionState.RETRYING, reasons=("retrying",))
    if signal.planning:
        return SessionStateReport(state=SessionState.PLANNING, reasons=("planning",))

    if run_status in _ACTIVE_RUN_STATES:
        return SessionStateReport(state=SessionState.RUNNING, reasons=(f"run_{run_status.value}" if run_status else "running",), metadata={"run_status": run_status.value if run_status else None})

    # No run and no condition at all: the session has been admitted but not
    # started, or nothing has ever been admitted to it.
    if run_status is None and not _any_condition_set(signal):
        return SessionStateReport(state=SessionState.CREATED, reasons=("no_run",))
    return SessionStateReport(state=SessionState.QUEUED, reasons=("awaiting_admission",))


def _any_condition_set(signal: SessionStateSignal) -> bool:
    return any(
        (
            signal.recovering,
            signal.retrying,
            signal.planning,
            signal.compacting,
            signal.permission_pending,
            signal.user_input_pending,
            signal.resource_pending,
            signal.network_unavailable,
            signal.provider_unavailable,
            signal.permanently_blocked,
            signal.paused,
        )
    )
