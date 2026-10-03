"""Can this actually be done, with what is actually installed?

## The defect

Misjudged solvability — telling the agent a job is possible when the capability
is not present — accounts for **over 40% of deep planning errors** (ToolBeHonest),
and it is the most expensive kind, because it is not a bad step in the middle of
a trajectory. It is a fabricated plan, and every step after it inherits the lie.

It also has a known, cheap remedy. Reliability alignment adds ``ChangeTools`` and
``TalkToUser`` to the agent's action space as first-class moves and takes
hallucination from 61.5% to 18.8% *while reducing tool usage by 76%*. The agents
that fabricate are largely agents that could only proceed or stall.

## So abstention is an action, not a feeling

:attr:`Escalation` is the verdict's second half, and it is the half that does
the work. A verdict that only says "no" leaves the agent with two options —
fabricate, or stop — and a model that has already begun a plan will pick the
first. Naming the move (``ASK_USER``, ``CHANGE_TOOL``, ``DELEGATE``,
``PROCEED_PARTIAL``) turns a dead end into a route.

## Confidence is never read from the model

Every verdict carries ``measured``. A verdict assembled purely from the manifest
is measured. One that also carries a model-stated confidence is **not**, and
:meth:`SolvabilityVerdict.authoritative` goes false — which is the signal for a
caller to treat it as advice rather than as a gate.

Models are badly calibrated about this specific question: abstention rates on
knowledge benchmarks range from ~1% to ~52% across frontier models with accuracy
barely moving, and models remain poor at knowing when they do not know. That is
the empirical reason the number is quarantined rather than trusted, and the
reason a *measured* answer here is worth more than a confident one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.grounding.gates import GateResult, blocked_by
from alpha.grounding.manifest import Availability, CapabilityManifest
from alpha.grounding.models import Escalation, GateLayer

__all__ = ["SolvabilityRequest", "SolvabilityVerdict", "Solvability", "evaluate_solvability"]


class Solvability(StrEnum):
    """Whether the preconditions hold."""

    #: Every required capability is present, wired and reachable.
    SOLVABLE = "solvable"
    #: Something required is absent or has no production caller.
    NO_CAPABILITY = "no_capability"
    #: Declared but not answering right now.
    UNAVAILABLE = "unavailable"
    #: Part of the job is covered; the rest is not.
    PARTIAL = "partial"
    #: The objective itself does not determine what is being asked for.
    UNDERSPECIFIED = "underspecified"
    #: A prior deterministic gate already refused. Reported rather than replaced,
    #: because "this is solvable" and "this is blocked" are both true and only
    #: the second one is actionable.
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SolvabilityRequest:
    """What the agent says it is about to do.

    ``required_capabilities`` are **named** requirements — a tool, a subsystem, a
    skill — as opposed to ``required_tags``, which are topic tags inferred from
    the objective. The distinction matters: a missing tag is a routing hint,
    while a missing named capability is a hard precondition, and treating them
    alike is how a hard blocker gets downgraded to a partial score.
    """

    objective: str
    required_capabilities: tuple[str, ...] = ()
    required_tags: tuple[str, ...] = ()
    #: A confidence the model produced. Recorded, and never used as a gate.
    model_stated_confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("solvability request needs an objective")
        if self.model_stated_confidence is not None and not 0.0 <= self.model_stated_confidence <= 1.0:
            raise ValueError("model_stated_confidence must be within [0, 1]")


@dataclass(frozen=True)
class SolvabilityVerdict:
    """The answer, the move, and how much to trust it."""

    solvability: Solvability
    escalation: Escalation
    #: Human-readable cause. Never empty on a non-solvable verdict — a refusal
    #: with no reason is indistinguishable from a failure and gets retried.
    reason: str
    #: Named capabilities that are absent, unwired, or down.
    missing: tuple[str, ...] = ()
    #: Named capabilities that are present and usable.
    satisfied: tuple[str, ...] = ()
    #: The subset of requirements the job can actually be completed against.
    uncovered: tuple[str, ...] = ()
    #: False whenever a model-supplied number is part of the input. See the
    #: module docstring: this is a quarantine, not a calibration curve.
    measured: bool = True
    #: Any gate result that already blocked, carried so the caller sees both facts.
    blocking_gate: GateResult | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def authoritative(self) -> bool:
        """Whether this verdict may gate work.

        True only for a verdict derived entirely from observed state. An
        unmeasured verdict is still returned — discarding the agent's own read
        would throw away a real signal — but it may not stop anything.
        """
        return self.measured and self.blocking_gate is None

    @property
    def may_proceed(self) -> bool:
        return self.solvability in (Solvability.SOLVABLE, Solvability.PARTIAL) and self.escalation in (
            Escalation.PROCEED,
            Escalation.PROCEED_PARTIAL,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "solvability": self.solvability.value,
            "escalation": self.escalation.value,
            "reason": self.reason,
            "missing": list(self.missing),
            "satisfied": list(self.satisfied),
            "uncovered": list(self.uncovered),
            "measured": self.measured,
            "authoritative": self.authoritative,
            "blocking_gate": self.blocking_gate.to_dict() if self.blocking_gate else None,
            "detail": dict(self.detail),
        }


#: Availability -> (solvability, escalation). A capability in the manifest but
#: unreachable is a *different answer* from one that is not in the manifest, and
#: they point at different fixes.
_AVAILABILITY_VERDICT: dict[Availability, tuple[Solvability, Escalation]] = {
    Availability.AVAILABLE: (Solvability.SOLVABLE, Escalation.PROCEED),
    Availability.DECLARED: (Solvability.SOLVABLE, Escalation.PROCEED),
    Availability.UNPROVEN: (Solvability.SOLVABLE, Escalation.PROCEED),
    Availability.UNWIRED: (Solvability.NO_CAPABILITY, Escalation.CHANGE_TOOL),
    Availability.DOWN: (Solvability.UNAVAILABLE, Escalation.ABSTAIN),
}

#: States that do not satisfy a named requirement at all.
_UNSATISFIED: frozenset[Availability] = frozenset({Availability.UNWIRED, Availability.DOWN})


def evaluate_solvability(
    request: SolvabilityRequest,
    manifest: CapabilityManifest,
    *,
    gate_results: Sequence[GateResult] = (),
    allow_partial: bool = True,
) -> SolvabilityVerdict:
    """Decide whether the task can be done, and say what to do if not.

    Precedence is deliberate and load-bearing:

    1. **An existing gate block wins.** Reporting "solvable" while a schema or
       existence check has already refused would let a caller read a stale
       "yes". Both facts travel; the block is the actionable one.
    2. **Absent capability beats present-but-partial.** An unwired subsystem is a
       hard miss, not a degraded hit — it has no production caller, so nothing
       the agent does will reach it.
    3. **Only then does tag coverage apply**, and a tag miss can only produce
       :attr:`Solvability.PARTIAL`, never a refusal. Tags are hints.
    """
    gate = blocked_by(tuple(gate_results))
    if gate is not None:
        return SolvabilityVerdict(
            solvability=Solvability.BLOCKED,
            escalation=_escalation_for_gate(gate),
            reason=gate.reason or gate.code or "a prior gate refused",
            blocking_gate=gate,
            detail={"gate_code": gate.code, "gate_layer": gate.layer.value},
        )

    measured = request.model_stated_confidence is None
    missing: list[str] = []
    unsatisfied: list[str] = []
    satisfied: list[str] = []

    for name in request.required_capabilities:
        entry = manifest.get(name)
        if entry is None:
            missing.append(name)
            unsatisfied.append(name)
            continue
        if entry.availability in _UNSATISFIED:
            missing.append(name)
            unsatisfied.append(name)
            continue
        satisfied.append(name)

    if missing:
        unwired = sorted(n for n in unsatisfied if (e := manifest.get(n)) and e.availability is Availability.UNWIRED)
        down = sorted(n for n in unsatisfied if (e := manifest.get(n)) and e.availability is Availability.DOWN)
        details: list[str] = []
        if unwired:
            details.append(f"declared but unwired: {', '.join(unwired)}")
        if down:
            details.append(f"configured but not answering: {', '.join(down)}")
        absent = sorted(set(unsatisfied) - set(unwired) - set(down))
        if absent:
            details.append(f"not installed: {', '.join(absent)}")
        escalation = Escalation.CHANGE_TOOL if unwired or satisfied else Escalation.ABSTAIN
        return SolvabilityVerdict(
            solvability=Solvability.UNAVAILABLE if down else Solvability.NO_CAPABILITY,
            escalation=escalation,
            reason="; ".join(details),
            missing=tuple(sorted(unsatisfied)),
            satisfied=tuple(sorted(satisfied)),
            measured=measured,
            detail={"unwired": unwired, "down": down},
        )

    if request.required_tags:
        match = manifest.select(request.objective)
        requested = {t.lower().replace("-", "_").replace(" ", "_") for t in request.required_tags}
        unmet = sorted(requested - set(match.matched))
        if unmet:
            if not allow_partial:
                return SolvabilityVerdict(
                    solvability=Solvability.UNDERSPECIFIED,
                    escalation=Escalation.ASK_USER,
                    reason=f"no capability carries {', '.join(unmet)}; the request needs a decision about scope",
                    satisfied=tuple(sorted(satisfied)),
                    uncovered=tuple(unmet),
                    measured=measured,
                    detail={"missing_tags": unmet},
                )
            return SolvabilityVerdict(
                solvability=Solvability.PARTIAL,
                escalation=Escalation.PROCEED_PARTIAL,
                reason=f"no capability carries {', '.join(unmet)}; proceed on the covered part and name the rest",
                satisfied=tuple(sorted(satisfied)),
                uncovered=tuple(unmet),
                measured=measured,
                detail={"missing_tags": unmet},
            )

    return SolvabilityVerdict(
        solvability=Solvability.SOLVABLE,
        escalation=Escalation.PROCEED,
        reason="every required capability is present and reachable",
        satisfied=tuple(sorted(satisfied)),
        measured=measured,
        detail={"checked_capabilities": len(request.required_capabilities)},
    )


def _escalation_for_gate(gate: GateResult) -> Escalation:
    """Map a gate block onto the action that resolves it.

    A missing tool is a routing problem, not a permission problem, so it routes
    to ``CHANGE_TOOL`` rather than to the user.
    """
    if gate.code in {"unknown_tool", "unknown_subagent", "unknown_skill"}:
        return Escalation.CHANGE_TOOL
    if gate.code in {"unsupported_claim", "contradictory_claims"}:
        return Escalation.ASK_USER
    if gate.layer is GateLayer.DETERMINISTIC:
        return Escalation.CHANGE_TOOL
    return Escalation.ABSTAIN


class SolvabilityGate:
    """A reusable evaluator bound to one manifest.

    Exists so the middleware can hold one instance for the request instead of
    re-resolving, and so a test can drive it without a process-wide registry.
    """

    __slots__ = ("_manifest",)

    def __init__(self, manifest: CapabilityManifest) -> None:
        self._manifest = manifest

    @property
    def manifest(self) -> CapabilityManifest:
        return self._manifest

    def evaluate(
        self,
        request: SolvabilityRequest,
        *,
        gate_results: Sequence[GateResult] = (),
        allow_partial: bool = True,
    ) -> SolvabilityVerdict:
        return evaluate_solvability(request, self._manifest, gate_results=gate_results, allow_partial=allow_partial)

    def refuse_if_unsolvable(
        self,
        request: SolvabilityRequest,
        *,
        gate_results: Sequence[GateResult] = (),
        allow_partial: bool = True,
    ) -> SolvabilityVerdict | None:
        """Return a verdict only when it should stop work.

        The single decision a caller normally wants: ``None`` means carry on.
        An unmeasured verdict never stops work — see
        :attr:`SolvabilityVerdict.authoritative`.
        """
        verdict = self.evaluate(request, gate_results=gate_results, allow_partial=allow_partial)
        if verdict.may_proceed:
            return None
        if not verdict.authoritative:
            return None
        return verdict
