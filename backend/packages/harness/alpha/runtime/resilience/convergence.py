"""The anti-oscillation layer: detect thrash, escalate, and stop.

This module is the reason the package exists. A retry loop with a ceiling still
oscillates happily: the agent flips a config, the symptom disappears, the
symptom comes back two minutes later, and a host that is supposed to run for
years spends its life ping-ponging. :class:`ConvergenceGuard` watches the
*sequence* of what a loop does and intervenes on a fixed ladder.

Thrash signals (checked in this priority order)
-----------------------------------------------
1. :attr:`ThrashSignal.PROHIBITED_TRANSITION` - the loop tried to "fix" a
   symptom by weakening its own check. This is the Goodhart/Campbell trap
   (Strickland, *Where the Flywheel Got Stuck*, 2000): the measure stops
   measuring. It terminates immediately.
2. :attr:`ThrashSignal.REPEATED_ACTION` - the same action, ``repeat_threshold``
   times in a row, with no progress.
3. :attr:`ThrashSignal.PING_PONG` - the observed state sequence is A, B, A
   (period 2) over ``ping_pong_window`` observations.
4. :attr:`ThrashSignal.ALTERNATING_FAILURES` - the failure signatures alternate
   A, B, A over ``alternating_failure_window`` observations: the loop is
   bouncing between two different bugs, which means neither diagnosis is right.
5. :attr:`ThrashSignal.NO_PROGRESS` - ``no_progress_threshold`` consecutive
   observations that reported no progress.

Escalation ladder (fixed order, one rung per fired signal)
----------------------------------------------------------
``WARN`` -> ``CHANGE_CONTEXT`` -> ``REQUEST_DISCRIMINATING_EVIDENCE`` ->
``CHANGE_STRATEGY`` -> ``DELEGATE`` -> ``TERMINATE``.

The ratchet is monotonic: the level only ever increases, so a loop that keeps
trips cannot oscillate *between interventions* either, and reaching
``TERMINATE`` pins the guard (further observations are refused with the same
terminal reason) - a state transition that cannot repeat.

Hard caps
---------
``max_attempts``, ``max_reflections`` and ``max_replans`` bound the three kinds
of work a healing loop can do (:attr:`ObservationKind`). Exceeding any cap
returns :attr:`GuardStatus.EXHAUSTED` with the intervention forced to
``TERMINATE`` - the caller learns it is out of budget instead of looping.

Deliberately *not* in this module: deciding what "progress" means for a
particular domain. The caller passes ``progressed`` and the action/state/
failure signature it actually observed, so the guard stays a pure state
machine with no opinion about healing.

References (paraphrased, nothing copied):
* Michael Nygard, *Release It!* (2018), ch. 6: the difference between fixing a
  fault and "fixing" the alarm that reported it.
* Campbell's law (1979), as formulated by Strickland, *Where the Flywheel Got
  Stuck* (2000): when a measure becomes a target, it ceases to be a good
  measure. A loop that disables its failing check is Campbell's law running in
  production.
* H. B. Bronson et al., *Metastable Failures in Distributed Systems* (OSDI'22):
  positive feedback (retries driving load, load driving retries) is the
  mechanism that turns a small fault into a persistent one; the fix is a bound
  on the loop, not a better delay.
* Shinn, Kasai & Gopinath, *Reflexion* (NeurIPS 2023): agent self-reflection
  helps, but only under a hard attempt budget - the same cap this module
  enforces structurally.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from alpha.runtime.resilience.errors import ProhibitedTransitionError

__all__ = [
    "ESCALATION_ORDER",
    "PROHIBITED_TRANSITION_KINDS",
    "ConvergenceDecision",
    "ConvergenceGuard",
    "ConvergenceSnapshot",
    "GuardStatus",
    "Intervention",
    "Observation",
    "ObservationKind",
    "ThrashSignal",
    "Transition",
    "is_prohibited_transition",
]


class Intervention(StrEnum):
    """What the caller should do next. Fixed ladder, no reordering."""

    NONE = "none"
    """No intervention: the observed sequence is healthy so far."""

    WARN = "warn"
    """Log/annotate the repeat; keep going (cheapest rung)."""

    CHANGE_CONTEXT = "change_context"
    """Change the context the loop works in (different inputs, files, env)."""

    REQUEST_DISCRIMINATING_EVIDENCE = "request_discriminating_evidence"
    """Gather evidence that separates two competing hypotheses before acting."""

    CHANGE_STRATEGY = "change_strategy"
    """The same approach cannot work; switch approach."""

    DELEGATE = "delegate"
    """Hand the work to a different (usually more capable) executor."""

    TERMINATE = "terminate"
    """Stop the loop. Terminal, pinned, and reported as exhaustion."""


#: The fixed escalation order. Index in this tuple IS the rung.
ESCALATION_ORDER: tuple[Intervention, ...] = (
    Intervention.WARN,
    Intervention.CHANGE_CONTEXT,
    Intervention.REQUEST_DISCRIMINATING_EVIDENCE,
    Intervention.CHANGE_STRATEGY,
    Intervention.DELEGATE,
    Intervention.TERMINATE,
)

#: Transition kinds that mean "make the symptom stop appearing" rather than
#: "make the problem stop". Passing one of these to the guard terminates the
#: loop instead of accepting the transition.
PROHIBITED_TRANSITION_KINDS: frozenset[str] = frozenset(
    {
        "disable_check",
        "bypass_verification",
        "widen_threshold",
        "suppress_alert",
        "delete_guard",
        "ignore_failure",
    }
)


class ThrashSignal(StrEnum):
    """What pattern tripped, in the order it is checked."""

    PROHIBITED_TRANSITION = "prohibited_transition"
    REPEATED_ACTION = "repeated_action"
    PING_PONG = "ping_pong"
    ALTERNATING_FAILURES = "alternating_failures"
    NO_PROGRESS = "no_progress"
    CAP_EXCEEDED = "cap_exceeded"


class ObservationKind(StrEnum):
    """Which of a healing loop's three kinds of work this observation was.

    The caps in :class:`ConvergenceGuard` are per kind, so "2 attempts, 3
    reflections, 1 replan" is a real budget rather than one lump sum.
    """

    ATTEMPT = "attempt"
    REFLECTION = "reflection"
    REPLAN = "replan"


class GuardStatus(StrEnum):
    """Closed set of what the guard says about the next step."""

    CONTINUE = "continue"
    """No signal; carry on."""

    INTERVENE = "intervene"
    """A signal tripped; the caller must act on ``intervention``."""

    EXHAUSTED = "exhausted"
    """A hard cap (or a prohibited transition) fired; stop the loop."""


@dataclass(frozen=True, slots=True)
class Transition:
    """A proposed state change, e.g. ``contract_gate -> contract_gate:off``.

    ``kind`` is what the change *is*; :func:`is_prohibited_transition` decides
    whether it is a legitimate repair or a symptom-defeating move. The guard
    accepts the tuple verbatim so a caller can represent the bad transition at
    all - refusing to model it would just push the check-disabling move out of
    the audited path.
    """

    source: str
    target: str
    kind: str
    detail: str = ""


def is_prohibited_transition(transition: Transition) -> bool:
    """True when this transition weakens a check instead of fixing a fault."""
    return transition.kind in PROHIBITED_TRANSITION_KINDS


@dataclass(frozen=True, slots=True)
class Observation:
    """One recorded step of the loop: what it did, where it ended up."""

    kind: ObservationKind
    action: str
    state: str = ""
    failure_signature: str = ""
    progressed: bool = True
    transition: Transition | None = None


@dataclass(frozen=True, slots=True)
class ConvergenceDecision:
    """The guard's verdict for one observation."""

    status: GuardStatus
    intervention: Intervention
    signal: ThrashSignal | None
    reason: str
    """Non-empty disclosure; safe to log verbatim (no payloads, no secrets)."""

    rung: int
    """Index into :data:`ESCALATION_ORDER`; -1 while nothing has intervened."""

    kind: ObservationKind
    counts: dict[str, int] = field(default_factory=dict)
    evidence_request: str = ""
    """For ``REQUEST_DISCRIMINATING_EVIDENCE``: what to capture, concretely."""

    @property
    def exhausted(self) -> bool:
        """True when a cap (or a prohibited transition) fired.

        A ratchet that merely *reached* ``TERMINATE`` is reported as
        ``INTERVENE`` with ``intervention=TERMINATE`` - the caller has to act on
        it. Once that happens the guard pins itself, so read
        :attr:`ConvergenceGuard.exhausted` for the terminal state rather than
        this flag.
        """
        return self.status is GuardStatus.EXHAUSTED

    @property
    def should_act(self) -> bool:
        return self.status is not GuardStatus.CONTINUE


@dataclass(frozen=True, slots=True)
class ConvergenceSnapshot:
    """Immutable view of the guard, for metrics and post-mortems."""

    observations: int
    rung: int
    intervention: Intervention
    counts: dict[str, int]
    exhausted: bool
    last_signal: ThrashSignal | None
    last_reason: str
    caps: dict[str, int]
    prohibited_kinds: frozenset[str]


_EVIDENCE_REQUESTS: dict[ThrashSignal, str] = {
    ThrashSignal.PING_PONG: "capture the exact field that differs between state A and state B; do not act until one of the two states is explained",
    ThrashSignal.ALTERNATING_FAILURES: "capture both full failure signatures and their differing inputs; the loop is bouncing between two bugs",
    ThrashSignal.REPEATED_ACTION: "capture one full failing artifact (log, diff, response body) for the repeated action before trying it again",
    ThrashSignal.NO_PROGRESS: "capture the last observable state change; if there is none, the action is a no-op and must change",
    ThrashSignal.PROHIBITED_TRANSITION: "capture the disabled check and the fault it was hiding; the transition is refused, the fault is not",
    ThrashSignal.CAP_EXCEEDED: "capture the attempt/reflection/replan counts that hit the cap; the budget is spent, not the options",
}


class ConvergenceGuard:
    """Stateful, single-threaded-per-owner anti-thrash ratchet.

    One guard instance per healing loop. The guard is deterministic and
    clock-free (it counts observations; the caller decides what an observation
    is), which is what makes the escalation order assertable in a test.

    Every call to :meth:`observe` returns a decision; the guard never raises for
    an expected signal, but it does raise :class:`ProhibitedTransitionError`
    from :meth:`require_transition_allowed` for callers that want an exception
    contract on the same check.
    """

    __slots__ = (
        "_counts",
        "_exhausted",
        "_history",
        "_last_reason",
        "_last_signal",
        "_max_attempts",
        "_max_history",
        "_max_reflections",
        "_max_replans",
        "_no_progress_threshold",
        "_ping_pong_window",
        "_prohibited_kinds",
        "_repeat_threshold",
        "_alternating_failure_window",
        "_rung",
    )

    def __init__(
        self,
        *,
        repeat_threshold: int = 3,
        ping_pong_window: int = 3,
        no_progress_threshold: int = 3,
        alternating_failure_window: int = 3,
        max_attempts: int = 8,
        max_reflections: int = 2,
        max_replans: int = 2,
        max_history: int = 64,
        prohibited_kinds: Sequence[str] | None = None,
    ) -> None:
        for name, value in (
            ("repeat_threshold", repeat_threshold),
            ("no_progress_threshold", no_progress_threshold),
            ("alternating_failure_window", alternating_failure_window),
            ("max_attempts", max_attempts),
            ("max_reflections", max_reflections),
            ("max_replans", max_replans),
            ("max_history", max_history),
        ):
            if int(value) < 1:
                raise ValueError(f"{name} must be >= 1, got {value!r}")
        if int(ping_pong_window) < 3:
            raise ValueError("ping_pong_window must be >= 3 to express an A->B->A cycle")
        if int(alternating_failure_window) < 3:
            raise ValueError("alternating_failure_window must be >= 3 to express an A-B-A cycle")
        self._repeat_threshold = int(repeat_threshold)
        self._ping_pong_window = int(ping_pong_window)
        self._no_progress_threshold = int(no_progress_threshold)
        self._alternating_failure_window = int(alternating_failure_window)
        self._max_attempts = int(max_attempts)
        self._max_reflections = int(max_reflections)
        self._max_replans = int(max_replans)
        self._max_history = int(max_history)
        self._prohibited_kinds = frozenset(prohibited_kinds) if prohibited_kinds is not None else PROHIBITED_TRANSITION_KINDS
        self._history: deque[Observation] = deque(maxlen=self._max_history)
        self._counts: dict[str, int] = {kind.value: 0 for kind in ObservationKind}
        self._rung = -1
        self._exhausted = False
        self._last_signal: ThrashSignal | None = None
        self._last_reason = ""

    # -- read-only views --------------------------------------------------

    @property
    def rung(self) -> int:
        """Current rung index into :data:`ESCALATION_ORDER` (-1 = untouched)."""
        return self._rung

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    @property
    def last_signal(self) -> ThrashSignal | None:
        return self._last_signal

    @property
    def repeat_threshold(self) -> int:
        return self._repeat_threshold

    @property
    def ping_pong_window(self) -> int:
        return self._ping_pong_window

    @property
    def no_progress_threshold(self) -> int:
        return self._no_progress_threshold

    @property
    def alternating_failure_window(self) -> int:
        return self._alternating_failure_window

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    @property
    def max_reflections(self) -> int:
        return self._max_reflections

    @property
    def max_replans(self) -> int:
        return self._max_replans

    @property
    def history_limit(self) -> int:
        return self._max_history

    @property
    def history(self) -> tuple[Observation, ...]:
        return tuple(self._history)

    def count(self, kind: ObservationKind) -> int:
        return self._counts[kind.value]

    def snapshot(self) -> ConvergenceSnapshot:
        """Immutable view for metrics/post-mortems."""
        return ConvergenceSnapshot(
            observations=len(self._history),
            rung=self._rung,
            intervention=self._intervention(),
            counts=dict(self._counts),
            exhausted=self._exhausted,
            last_signal=self._last_signal,
            last_reason=self._last_reason,
            caps={
                "attempts": self._max_attempts,
                "reflections": self._max_reflections,
                "replans": self._max_replans,
            },
            prohibited_kinds=self._prohibited_kinds,
        )

    def _intervention(self) -> Intervention:
        if self._rung < 0:
            return Intervention.NONE
        return ESCALATION_ORDER[self._rung]

    # -- prohibited transitions -------------------------------------------

    def is_prohibited(self, transition: Transition) -> bool:
        """True when ``transition`` is a check-weakening move (this guard's set)."""
        return transition.kind in self._prohibited_kinds

    def require_transition_allowed(self, transition: Transition) -> Transition:
        """Return ``transition`` or raise :class:`ProhibitedTransitionError`.

        For call sites that want an exception contract. The same rule is applied
        inside :meth:`observe` for callers that would rather read a status.
        """
        if self.is_prohibited(transition):
            raise ProhibitedTransitionError(
                f"refusing prohibited transition {transition.source!r} -> {transition.target!r} ({transition.kind})",
                kind=transition.kind,
                source=transition.source,
                target=transition.target,
            )
        return transition

    # -- detection --------------------------------------------------------

    def _repeated_action(self) -> bool:
        if len(self._history) < self._repeat_threshold:
            return False
        window = list(self._history)[-self._repeat_threshold :]
        return len({item.action for item in window}) == 1 and not any(item.progressed for item in window)

    def _alternates(self, values: list[str], window: int) -> bool:
        """True when the tail is period-2 over two distinct values (A,B,A,B...)."""
        if len(values) < window or window < 3:
            return False
        tail = values[-window:]
        if any(value == "" for value in tail):
            return False
        if tail[0] == tail[1]:
            return False
        return all(tail[index] == tail[index - 2] for index in range(2, window))

    def _ping_pong(self) -> bool:
        return self._alternates([item.state for item in self._history], self._ping_pong_window)

    def _alternating_failures(self) -> bool:
        return self._alternates([item.failure_signature for item in self._history], self._alternating_failure_window)

    def _no_progress(self) -> bool:
        if len(self._history) < self._no_progress_threshold:
            return False
        return not any(item.progressed for item in list(self._history)[-self._no_progress_threshold :])

    def _detect(self) -> ThrashSignal | None:
        history = list(self._history)
        if history and history[-1].transition is not None and self.is_prohibited(history[-1].transition):
            return ThrashSignal.PROHIBITED_TRANSITION
        if self._repeated_action():
            return ThrashSignal.REPEATED_ACTION
        if self._ping_pong():
            return ThrashSignal.PING_PONG
        if self._alternating_failures():
            return ThrashSignal.ALTERNATING_FAILURES
        if self._no_progress():
            return ThrashSignal.NO_PROGRESS
        return None

    def _cap_for(self, kind: ObservationKind) -> int:
        if kind is ObservationKind.ATTEMPT:
            return self._max_attempts
        if kind is ObservationKind.REFLECTION:
            return self._max_reflections
        return self._max_replans

    def _cap_hit(self, kind: ObservationKind) -> bool:
        return self._counts[kind.value] > self._cap_for(kind)

    # -- the loop ---------------------------------------------------------

    def observe(
        self,
        *,
        action: str,
        state: str = "",
        kind: ObservationKind = ObservationKind.ATTEMPT,
        progressed: bool = True,
        failure_signature: str = "",
        transition: Transition | None = None,
    ) -> ConvergenceDecision:
        """Record one loop step and return the decision for the next one.

        Order of evaluation: the pin check (an already-terminated guard returns
        the same terminal decision), the prohibited transition, the per-kind
        cap, then the thrash patterns in priority order. The ratchet never moves
        down and the counters never reset on progress: a loop that made
        progress once and then starts thrashing still escalates from where it
        left off and still spends its cap.
        """
        observation = Observation(
            kind=kind,
            action=action,
            state=state,
            failure_signature=failure_signature,
            progressed=progressed,
            transition=transition,
        )
        self._history.append(observation)
        self._counts[kind.value] += 1

        if self._exhausted:
            # Terminal is terminal: repeat observations cannot re-open the loop
            # or move the rung. This is the "state transition that cannot
            # repeat" that bounds an unattended host.
            return self._decision(
                status=GuardStatus.EXHAUSTED,
                kind=kind,
                signal=self._last_signal,
                reason=f"already terminated: {self._last_reason}",
            )

        signal = self._detect()
        if signal is ThrashSignal.PROHIBITED_TRANSITION:
            self._rung = len(ESCALATION_ORDER) - 1
            self._exhausted = True
            detail = observation.transition
            reason = f"prohibited transition refused: {detail.source!r} -> {detail.target!r} ({detail.kind}) would hide the fault instead of fixing it"
            self._last_signal = signal
            self._last_reason = reason
            return self._decision(status=GuardStatus.EXHAUSTED, kind=kind, signal=signal, reason=reason)

        if self._cap_hit(kind):
            self._rung = len(ESCALATION_ORDER) - 1
            self._exhausted = True
            reason = f"{kind.value} cap exceeded: {self._counts[kind.value]} > {self._cap_for(kind)}"
            self._last_signal = ThrashSignal.CAP_EXCEEDED
            self._last_reason = reason
            return self._decision(status=GuardStatus.EXHAUSTED, kind=kind, signal=ThrashSignal.CAP_EXCEEDED, reason=reason)

        if signal is None:
            return self._decision(status=GuardStatus.CONTINUE, kind=kind, signal=None, reason="no thrash signal")

        # Monotonic ratchet: one rung per fired signal, never down.
        self._rung = min(self._rung + 1, len(ESCALATION_ORDER) - 1)
        intervention = ESCALATION_ORDER[self._rung]
        reason = f"{signal.value} at rung {self._rung} ({intervention.value})"
        self._last_signal = signal
        self._last_reason = reason
        if intervention is Intervention.TERMINATE:
            self._exhausted = True
        return self._decision(status=GuardStatus.INTERVENE, kind=kind, signal=signal, reason=reason)

    def _decision(
        self,
        *,
        status: GuardStatus,
        kind: ObservationKind,
        signal: ThrashSignal | None,
        reason: str,
    ) -> ConvergenceDecision:
        intervention = self._intervention()
        if status is GuardStatus.EXHAUSTED:
            intervention = Intervention.TERMINATE
        evidence = _EVIDENCE_REQUESTS.get(signal, "") if intervention is Intervention.REQUEST_DISCRIMINATING_EVIDENCE else ""
        return ConvergenceDecision(
            status=status,
            intervention=intervention,
            signal=signal,
            reason=reason,
            rung=self._rung,
            kind=kind,
            counts=dict(self._counts),
            evidence_request=evidence,
        )

    def reset(self) -> None:
        """Clear the ratchet and counters (a genuinely new loop, not a retry)."""
        self._history.clear()
        self._counts = {kind.value: 0 for kind in ObservationKind}
        self._rung = -1
        self._exhausted = False
        self._last_signal = None
        self._last_reason = ""

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"ConvergenceGuard(rung={self._rung}, exhausted={self._exhausted}, last_signal={self._last_signal})"
