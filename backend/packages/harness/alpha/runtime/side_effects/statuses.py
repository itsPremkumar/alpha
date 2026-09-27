"""Side-effect state: including the state that is not a state.

The problem
-----------
Alpha already has excellent machinery for *knowing* that something failed, and
for refusing to replay a side effect blindly: `SafeRunRecoveryService` refuses
to auto-resume a checkpoint whose pending node is a tool, MCP, shell, browser,
write/delete, payment, or unknown node, and records
`stop_reason="recovery_confirmation_required"` instead. That is the right call
and it is well tested.

What it cannot do is name the thing that needs confirming. The stop reason is
attached to a *run*; the actual unknown is attached to a *specific external
effect* — "this `charge_customer` call may or may not have charged somebody".
There was no durable, queryable row saying so. A run-level stop reason cannot be
reconciled, re-listed, or worked off; it can only be read once, in the context of
the run that produced it.

So this module supplies the missing vocabulary, and only the vocabulary: what a
side effect's status is, how risky it is, and what a reconciliation concluded.
The durable rows live in `alpha/persistence/side_effects/`; the ledger that owns
them is `alpha/runtime/side_effects/ledger.py`.

``UNKNOWN`` is the whole point
------------------------------
A side-effecting operation has three honest outcomes: it succeeded, it failed,
or **we cannot tell**. Collapsing the third into either of the first two is how
a crash becomes a double charge or a duplicate deploy. So:

* ``UNKNOWN`` is a first-class status, not an error value.
* It is never resolved by *assuming*. Only an explicit
  :class:`ReconciliationVerdict` moves it, and ``UNDETERMINED`` is a real
  verdict meaning "still cannot tell" — not a synonym for failure.
* An ``UNKNOWN`` entry is *durable and enumerable*, so a human or a diagnostic
  agent can be pointed at exactly the set of effects that need checking.

:data:`SIDE_EFFECT_STATUS_TRANSITIONS` makes the rule mechanical: an ``UNKNOWN``
entry can only leave via :meth:`reconcile`, and only for a non-``UNDETERMINED``
verdict. There is no path from ``UNKNOWN`` straight back to ``COMPLETED``,
because "the worker felt like it" is not evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

__all__ = [
    "OPEN_SIDE_EFFECT_STATUSES",
    "RECONCILING_SIDE_EFFECT_STATUSES",
    "SIDE_EFFECT_LEVELS",
    "SIDE_EFFECT_STATUS_TRANSITIONS",
    "TERMINAL_SIDE_EFFECT_STATUSES",
    "IllegalSideEffectTransition",
    "ReconciliationVerdict",
    "SideEffectEntry",
    "SideEffectLevel",
    "SideEffectStatus",
    "can_transition",
    "status_for_verdict",
    "validate_transition",
    "verdict_is_settled",
]


class SideEffectStatus(StrEnum):
    """Where one external effect is in its own lifecycle.

    This is deliberately a *per-effect* lifecycle, independent of the run's. A
    run can be `error` while one of its effects is still `UNKNOWN`, and the run
    being terminal is exactly why the effect needs its own durable row.
    """

    #: Recorded before the call is attempted. A crash here means nothing ran.
    PENDING = "pending"
    #: The call is in flight in a live worker.
    IN_FLIGHT = "in_flight"
    #: The effect definitely took place.
    COMPLETED = "completed"
    #: The effect definitely did not take place.
    FAILED = "failed"
    #: The worker died or was lost while the effect was in flight, so whether it
    #: took place is not established. Requires reconciliation.
    UNKNOWN = "unknown"
    #: A reconciler looked and reached a verdict. ``undetermined`` keeps the
    #: entry open for a later attempt; the other two are terminal.
    RECONCILED = "reconciled"


class SideEffectLevel(StrEnum):
    """How much damage a duplicate or an unknown would do.

    The level is recorded with the entry, not derived at read time, because the
    risk of "charge a card" is a property of the tool that was called -- and
    because a tool's risk can change between releases, so a report that
    re-derived it would silently reclassify history.
    """

    READ_ONLY = "read_only"
    LOW_RISK = "low_risk"
    MODERATE = "moderate"
    HIGH_RISK = "high_risk"
    DESTRUCTIVE = "destructive"

    @property
    def rank(self) -> int:
        return _LEVEL_RANK[self]

    @property
    def requires_reconciliation(self) -> bool:
        """True when an ``UNKNOWN`` result must be resolved by a human.

        Read-only and low-risk unknowns are usually harmless to leave open;
        high-risk and destructive ones are not, and the ledger escalates them
        rather than letting them sit unnoticed.
        """
        return self.rank >= _LEVEL_RANK[SideEffectLevel.HIGH_RISK]


#: A tool's risk is a property of the call, and is recorded with the entry
#: rather than re-derived at read time: a tool's risk can change between
#: releases, so a report that re-derived it would silently reclassify history.
SIDE_EFFECT_LEVELS: Final[Mapping[SideEffectLevel, int]] = MappingProxyType({level: index for index, level in enumerate(SideEffectLevel)})

_LEVEL_RANK: Final[Mapping[SideEffectLevel, int]] = SIDE_EFFECT_LEVELS


class ReconciliationVerdict(StrEnum):
    """What a reconciler concluded about an ``UNKNOWN`` effect.

    ``UNDETERMINED`` is a real answer meaning "I looked and still cannot tell".
    It is **not** a failure: it keeps the entry open for another attempt. This
    distinction is the reason the enum exists rather than a boolean -- a boolean
    would force "could not determine" to be spelled as either yes or no.
    """

    CONFIRMED_SUCCESS = "confirmed_success"
    CONFIRMED_FAILURE = "confirmed_failure"
    UNDETERMINED = "undetermined"


class IllegalSideEffectTransition(ValueError):
    """A requested side-effect status change is not in the transition table."""

    def __init__(self, current: SideEffectStatus, requested: SideEffectStatus) -> None:
        allowed = sorted(status.value for status in SIDE_EFFECT_STATUS_TRANSITIONS[current])
        super().__init__(f"illegal side-effect transition {current.value!r} -> {requested.value!r}; allowed from {current.value!r}: {allowed or ['<none>']}")
        self.current = current
        self.requested = requested


#: Statuses with no further automatic progress.
TERMINAL_SIDE_EFFECT_STATUSES: Final[frozenset[SideEffectStatus]] = frozenset({SideEffectStatus.COMPLETED, SideEffectStatus.FAILED})
#: Statuses that are neither settled nor finished: something still owes an answer.
OPEN_SIDE_EFFECT_STATUSES: Final[frozenset[SideEffectStatus]] = frozenset({SideEffectStatus.UNKNOWN, SideEffectStatus.RECONCILED})
#: Statuses a live worker owns right now.
RECONCILING_SIDE_EFFECT_STATUSES: Final[frozenset[SideEffectStatus]] = frozenset({SideEffectStatus.PENDING, SideEffectStatus.IN_FLIGHT})

#: Legal per-effect moves. Note that ``UNKNOWN`` has exactly one exit, and it
#: goes to ``RECONCILED`` -- never straight to ``COMPLETED`` or ``FAILED``. The
#: verdict carried on the reconciliation is what distinguishes those two, so a
#: status change alone can never assert an outcome nobody verified.
SIDE_EFFECT_STATUS_TRANSITIONS: Final[Mapping[SideEffectStatus, frozenset[SideEffectStatus]]] = MappingProxyType(
    {
        SideEffectStatus.PENDING: frozenset({SideEffectStatus.IN_FLIGHT, SideEffectStatus.FAILED, SideEffectStatus.UNKNOWN}),
        SideEffectStatus.IN_FLIGHT: frozenset({SideEffectStatus.COMPLETED, SideEffectStatus.FAILED, SideEffectStatus.UNKNOWN}),
        # The only exit. A reconciler must be the one to move it.
        SideEffectStatus.UNKNOWN: frozenset({SideEffectStatus.RECONCILED}),
        # A reconciliation that could not determine anything reopens the entry
        # so a later pass can try again. Settled ones stay put.
        SideEffectStatus.RECONCILED: frozenset({SideEffectStatus.UNKNOWN}),
        SideEffectStatus.COMPLETED: frozenset(),
        SideEffectStatus.FAILED: frozenset(),
    }
)

#: A verdict's effect on the entry's status. ``UNDETERMINED`` maps to the same
#: status it came from's successor, which is ``RECONCILED``; the distinction is
#: carried by the verdict itself and by ``SideEffectEntry.needs_reconciliation``.
_VERDICT_SUCCESS: Final[frozenset[ReconciliationVerdict]] = frozenset({ReconciliationVerdict.CONFIRMED_SUCCESS, ReconciliationVerdict.CONFIRMED_FAILURE})

assert set(SIDE_EFFECT_STATUS_TRANSITIONS) == set(SideEffectStatus), "every SideEffectStatus needs a transition row"
for _state, _successors in SIDE_EFFECT_STATUS_TRANSITIONS.items():
    if _state in (SideEffectStatus.COMPLETED, SideEffectStatus.FAILED):
        assert not _successors, f"{_state.value!r} must be terminal"


@dataclass(frozen=True, slots=True)
class SideEffectEntry:
    """One external effect, and what is known about it.

    ``arguments_digest`` is a hash, never the arguments themselves: a side-effect
    record is durable, is exported into support bundles, and lives longer than
    the thread, so it must not become a place secrets and personal data
    accumulate. The digest is what makes two attempts comparable without keeping
    either one's payload.
    """

    tool_call_id: str
    tool_name: str
    status: SideEffectStatus
    level: SideEffectLevel
    thread_id: str = ""
    run_id: str = ""
    user_id: str = ""
    #: Canonical digest of the invocation arguments. Absent when unknown, which
    #: is itself meaningful: a reconciler cannot compare attempts it cannot
    #: identify.
    arguments_digest: str | None = None
    #: Result digest on a confirmed effect. Also a digest, for the same reason.
    result_digest: str | None = None
    verdict: ReconciliationVerdict | None = None
    #: Human-readable, already-redacted explanation. Never an exception repr.
    detail: str = ""
    #: Worker that owns an ``IN_FLIGHT`` entry; a lost lease is what makes an
    #: entry eligible to be marked ``UNKNOWN``.
    owner_worker_id: str | None = None
    lease_expires_at: float | None = None
    attempt: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def needs_reconciliation(self) -> bool:
        """True when this entry still owes someone an answer."""
        return self.status is SideEffectStatus.UNKNOWN

    @property
    def is_undetermined_reconciliation(self) -> bool:
        """True when a reconciler looked and could still not tell."""
        return self.status is SideEffectStatus.RECONCILED and self.verdict is ReconciliationVerdict.UNDETERMINED

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_SIDE_EFFECT_STATUSES

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "level": self.level.value,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "arguments_digest": self.arguments_digest,
            "result_digest": self.result_digest,
            "verdict": self.verdict.value if self.verdict else None,
            "detail": self.detail,
            "attempt": self.attempt,
            "needs_reconciliation": self.needs_reconciliation,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def can_transition(current: SideEffectStatus, requested: SideEffectStatus) -> bool:
    """Return True when the move is legal (self-transition included)."""
    return requested is current or requested in SIDE_EFFECT_STATUS_TRANSITIONS[current]


def validate_transition(current: SideEffectStatus, requested: SideEffectStatus) -> SideEffectStatus:
    """Return *requested* when the move is legal, else raise.

    ``UNKNOWN -> COMPLETED`` is refused. That refusal is the entire reason this
    table exists: a worker that died mid-call cannot be the one to decide the
    call succeeded.
    """
    if can_transition(current, requested):
        return requested
    raise IllegalSideEffectTransition(current, requested)


def status_for_verdict(verdict: ReconciliationVerdict) -> SideEffectStatus:
    """The status a reconciled entry lands in for *verdict*.

    Both settled verdicts land on ``RECONCILED`` rather than on ``COMPLETED`` /
    ``FAILED`` directly, because the status alone cannot express *which* of the
    two was established. A reader must consult :attr:`SideEffectEntry.verdict`;
    the status records only that someone looked and reached a conclusion.
    """
    return SideEffectStatus.RECONCILED


def verdict_is_settled(verdict: ReconciliationVerdict) -> bool:
    """True when *verdict* is a real conclusion rather than "still unknown"."""
    return verdict in _VERDICT_SUCCESS
