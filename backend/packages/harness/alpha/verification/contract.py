"""The three outcomes, the reasons behind them, and the report they render as.

This is the vocabulary contract for the bounded verification controller
(:mod:`alpha.verification.controller`). It is deliberately the smallest module in
the package: the state machine imports it, the renderers import it, and the tests
assert against it, so a change to *what the words mean* is a change to one file.

Three outcomes, never conflated:

``VERIFIED``
    The existing evidence binder accepted it. Concretely: a real recorded bash
    execution was harvested, handed to
    :func:`alpha.subagents.acceptance_checks.check_acceptance_criteria` as a
    ``tests_passed:<command>`` criterion, and the leaf came back
    ``checked and holds``. The controller adds no acceptance logic of its own; a
    pass it cannot route through the binder is not a pass it may claim.

``FAILED``
    It ran and it did not pass — a determinate negative. A recorded execution
    exists, the binder *checked* it, and the leaf does not hold. This is reached
    when the loop reached a decision and stopped there (no repair handler, or a
    repair handler that declined).

``UNVERIFIED``
    It could not be determined. This is the default and it is fail-closed: no
    recorded execution to anchor to, an execution whose provenance cannot be
    proven, a repair attempt that reduced the test surface, an unavailable test
    surface, the loop being disabled, or **the attempt budget running out**.

The budget rule is the sharp one and it is a deliberate choice, not an
oversight. A loop that exhausts its attempts has, by definition, not determined
the answer — it stopped asking. The last determinate reading is still reported,
verbatim, in ``last_reading`` and in the attached evidence, so nothing is hidden;
it simply is not promoted to ``FAILED``, because ``FAILED`` means *decided*, and
an exhausted budget is not a decision. A caller that wants the decided-negative
form asks for verification with no repair handler, which is a decision.

**A completed run is never a verified run.** ``render_report`` says so in its own
words, and ``VerificationReport.to_dict`` keeps ``outcome`` and any run-completion
field in separate keys so no consumer can collapse them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "FAILED",
    "UNVERIFIED",
    "VERIFIED",
    "LoopState",
    "UnverifiedReason",
    "VerificationOutcome",
    "VerificationReport",
]


class VerificationOutcome(StrEnum):
    """The only three answers the controller is allowed to give."""

    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNVERIFIED = "UNVERIFIED"


#: Module-level aliases so a caller can import the vocabulary without the enum.
VERIFIED = VerificationOutcome.VERIFIED
FAILED = VerificationOutcome.FAILED
UNVERIFIED = VerificationOutcome.UNVERIFIED

#: Anti-automation-bias line, same fixed wording the acceptance citation layer
#: renders. Restated here because a controller verdict is a second kind of
#: evidence claim, not a first.
_LIMITATION = "recorded test-run evidence only; it does not validate the correctness of the change"


class LoopState(StrEnum):
    """Where the state machine is, for reporting and for the seam's decision.

    The names describe *what the controller is doing*, never what it concluded.
    Only :data:`LoopState.SETTLED` carries a conclusion, and it carries one in
    :attr:`VerificationReport.outcome`, never in the state name.
    """

    IDLE = "idle"
    EXECUTING = "executing"
    ASSESSING = "assessing"
    CAPTURING_SURFACE = "capturing_surface"
    DISPATCHING_REPAIR = "dispatching_repair"
    #: The only non-terminal state a caller can observe. ``step()`` here without
    #: a repair outcome redelivers the same directive rather than advancing, so
    #: polling costs nothing and a second poll cannot spend a second attempt.
    AWAITING_REPAIR = "awaiting_repair"
    COMPARING_SURFACE = "comparing_surface"
    SETTLED = "settled"


class UnverifiedReason(StrEnum):
    """Why a determination could not be made. Every UNVERIFIED carries one.

    None of these is a failure of the code under test. They are failures to
    *establish* the answer, which is the distinction the whole controller exists
    to keep.
    """

    LOOP_DISABLED = "verification_loop_disabled"
    NO_RECORDED_EXECUTION = "no_recorded_execution"
    EXECUTION_ERROR = "execution_error"
    EVIDENCE_UNANCHORED = "evidence_unanchored"
    BUDGET_EXHAUSTED = "budget_exhausted"
    WEAKENED_TEST = "weakened_test"
    SURFACE_UNAVAILABLE = "test_surface_unavailable"
    SURFACE_INDETERMINATE = "test_surface_indeterminate"
    REPAIR_UNAVAILABLE = "repair_unavailable"
    REPAIR_ERROR = "repair_error"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class VerificationReport:
    """The controller's final answer, with everything a reader needs to audit it.

    Immutable on purpose: a verdict that can be edited after the fact is not
    evidence. ``outcome`` is derived once at construction from
    :attr:`evidence_outcome` and the budget state, and the derived value is the
    only one exposed.
    """

    #: What the binder said about the last recorded execution it was given.
    #: ``"pass"`` / ``"fail"`` / ``"undecided"`` — see ``evidence_outcome`` below.
    evidence_outcome: str
    #: The reason for an UNVERIFIED outcome. Required when the outcome is
    #: UNVERIFIED and forbidden otherwise.
    reason: UnverifiedReason | None
    #: The verification command, exactly as it was executed.
    command: str
    #: How many repair attempts the budget allowed, used, and has left.
    attempts_allowed: int
    attempts_used: int
    attempts_remaining: int
    #: Whether the loop ended on a *decision* (settled) or ran out of budget.
    #: Only a decided loop may report FAILED; see the module docstring.
    decided: bool
    #: The last determinate reading of the evidence, kept even when the outcome
    #: is UNVERIFIED, so an exhausted budget never hides a known failure.
    last_reading: str
    #: The recorded execution the outcome was derived from, as text, bounded.
    evidence_text: str = ""
    #: Whether ``evidence_text`` is a truncation of the recorded output.
    evidence_truncated: bool = False
    #: Whether the run the loop ran inside finished. Kept deliberately separate
    #: from ``outcome``: a finished run is not a verified run.
    run_finished: bool = False
    #: Why the loop stopped, in one line, for the report header.
    summary: str = ""
    #: Per-attempt trail, oldest first.
    attempts: tuple[Mapping[str, Any], ...] = ()
    #: Free-form, bounded notes (surface findings, executor details).
    notes: tuple[str, ...] = ()
    #: The terminal state the controller was in when it settled.
    state: LoopState = LoopState.SETTLED

    def __post_init__(self) -> None:
        if self.evidence_outcome not in _EVIDENCE_OUTCOMES:
            raise ValueError(f"evidence_outcome must be one of {sorted(_EVIDENCE_OUTCOMES)}, got {self.evidence_outcome!r}")
        undetermined = self.evidence_outcome != "pass" and not self.decided
        if self.evidence_outcome == "pass" and self.reason is not None:
            # A verified reading has no "why unverified" reason; allowing one
            # would let a caller label a pass with a failure reason and have
            # the label survive into the report.
            raise ValueError("a passing evidence reading cannot carry an unverified reason")
        if self.decided and self.evidence_outcome == "fail" and self.reason is not None:
            # The mirror of the check below, and the reason FAILED is allowed to
            # exist at all: a decided negative is not a "we could not tell"
            # answer, so it has no undetermined reason to give, and attaching one
            # would let a FAILED verdict be rendered with a hedge on it.
            raise ValueError("a decided failure cannot carry an unverified reason; it is not undetermined")
        if undetermined and self.reason is None:
            # Fail closed at construction: no reason means somebody is about to
            # render an undetermined verdict with no explanation.
            raise ValueError(f"a {self.evidence_outcome!r} evidence reading that was not decided requires an unverified reason")
        if self.decided and self.reason is UnverifiedReason.BUDGET_EXHAUSTED:
            raise ValueError("a budget-exhausted loop is not a decided loop; it must not report FAILED")
        if self.attempts_used > self.attempts_allowed:
            raise ValueError("attempts_used cannot exceed attempts_allowed")
        if self.attempts_remaining != self.attempts_allowed - self.attempts_used:
            raise ValueError("attempts_remaining must be the budget's remainder")

    @property
    def outcome(self) -> VerificationOutcome:
        """The reported outcome. Read-only and derived; never assignable.

        ``VERIFIED`` requires a *pass* reading. A decided loop with a ``fail``
        reading is ``FAILED``. Everything else — including a ``fail`` reading on
        an undecided (budget-exhausted) loop — is ``UNVERIFIED``, and the last
        reading is still reported.
        """
        if self.evidence_outcome == "pass":
            return VerificationOutcome.VERIFIED
        if self.evidence_outcome == "fail" and self.decided:
            return VerificationOutcome.FAILED
        return VerificationOutcome.UNVERIFIED

    @property
    def verified(self) -> bool:
        """``True`` only for :data:`VerificationOutcome.VERIFIED`.

        Named as a predicate rather than a field so that no consumer can set it,
        and so the "completed run is not a verified run" distinction survives
        every serialization path.
        """
        return self.outcome is VerificationOutcome.VERIFIED

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form. ``outcome`` and ``run_finished`` stay separate keys."""
        return {
            "outcome": self.outcome.value,
            "reason": self.reason.value if self.reason is not None else None,
            "command": self.command,
            "attempts_allowed": self.attempts_allowed,
            "attempts_used": self.attempts_used,
            "attempts_remaining": self.attempts_remaining,
            "decided": self.decided,
            "last_reading": self.last_reading,
            "evidence_text": self.evidence_text,
            "evidence_truncated": self.evidence_truncated,
            "run_finished": self.run_finished,
            "summary": self.summary,
            "state": self.state.value,
            "attempts": [dict(attempt) for attempt in self.attempts],
            "notes": list(self.notes),
        }


_EVIDENCE_OUTCOMES = frozenset({"pass", "fail", "undecided"})


def render_report(report: VerificationReport) -> str:
    """Render the report as the text a model or an operator actually reads.

    Three rules this renderer exists to enforce:

    * the outcome word is the only strong-positive word, and it appears once;
    * a finished run is stated separately, in terms that cannot be read as
      verification;
    * UNVERIFIED says *why*, always.
    """
    outcome = report.outcome
    lines = [f"Verification outcome: {outcome.value}"]
    if report.command:
        lines.append(f"Command: {report.command}")
    if outcome is VerificationOutcome.UNVERIFIED and report.reason is not None:
        lines.append(f"Reason: {report.reason.value}")
    lines.append(f"Reading of the recorded evidence: {report.last_reading}")
    lines.append(f"Repair attempts: {report.attempts_used} used of {report.attempts_allowed} ({report.attempts_remaining} remaining)")
    if report.run_finished:
        lines.append("The run finished. A finished run is not a verified run; the outcome above is the verification answer.")
    if report.evidence_text:
        suffix = " (truncated)" if report.evidence_truncated else ""
        lines.append(f"Recorded test output{suffix}:")
        lines.append(report.evidence_text)
    for note in report.notes:
        lines.append(f"- {note}")
    lines.append(f"({_LIMITATION})")
    return "\n".join(lines)


@dataclass(frozen=True)
class RecordedReading:
    """What one recorded execution was read as.

    ``reading`` is one of :data:`_EVIDENCE_OUTCOMES`. ``detail`` is the binder's
    own detail string, kept verbatim so a reader can see the binder's reasoning
    rather than the controller's paraphrase of it.
    """

    reading: str
    detail: str
    tool_call_id: str
    command: str
    output: str
    output_truncated: bool
    status: str


def attempt_trail_entry(index: int, reading: RecordedReading, *, repaired: bool, surface_reduced: bool) -> dict[str, Any]:
    """One bounded row of the per-attempt trail."""
    return {
        "index": index,
        "reading": reading.reading,
        "detail": reading.detail,
        "tool_call_id": reading.tool_call_id,
        "output_truncated": reading.output_truncated,
        "repaired": repaired,
        "surface_reduced": surface_reduced,
    }


def bound_text(text: str, limit: int) -> tuple[str, bool]:
    """Clamp *text* to *limit* characters, reporting whether it was clamped.

    Returns ``(text, truncated)``. A clamp is always visible: the report and the
    repair prompt both carry the flag, so a model reading half a failure is told
    it is reading half a failure.
    """
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit], True
