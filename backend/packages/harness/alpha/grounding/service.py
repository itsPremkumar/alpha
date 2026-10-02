"""One object that holds the whole grounding posture for a run.

Every layer in this package is independently useful and independently testable.
:class:`GroundingService` exists because the *ordering* between them is the
actual contract, and an ordering that lives in each caller's head does not hold:

1. **Build the manifest.** Before anything is claimed, the agent should know what
   it has. Cheap, and every later layer refers back to it.
2. **Gate the step.** Cheapest checks first. A tool that does not exist is
   reported as such, not as an unconfirmed destructive action.
3. **Judge solvability**, with the gate results already in hand. A pre-existing
   block outranks any solvability answer, because "this is solvable" is also true
   and only the block is actionable.
4. **Track claims** as steps consume them. Promotion to load-bearing is automatic
   on first use.
5. **Record the reuse decision**, so :class:`~alpha.grounding.metrics.GroundingMetrics`
   can tell an exploration gap from a disposition gap.
6. **Advance effort.** The brake is checked here rather than by the caller,
   because the caller is exactly the party that will ask for one more attempt.

Every step returns a :class:`GroundingDecision` carrying both the verdict and the
remediation, so a caller cannot act on the verdict without seeing the fix. The
service never decides a run is *correct*: it decides whether to continue, and
"continued" is not "verified".
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.grounding.claims import Claim, ClaimKind, ClaimLedger, EvidenceRef, audit_claims
from alpha.grounding.effort import EffortController, StopReason, WorkOutcome, escalation_for_stop
from alpha.grounding.gates import GatePipeline, GateResult, GateSubject
from alpha.grounding.manifest import CapabilityManifest
from alpha.grounding.metrics import GroundingMetrics, Origin
from alpha.grounding.models import Escalation, GateLayer
from alpha.grounding.provenance import MemoryStore, Provenance
from alpha.grounding.solvability import SolvabilityGate, SolvabilityRequest, SolvabilityVerdict

__all__ = ["GroundingDecision", "GroundingService"]


@dataclass
class GroundingDecision:
    """The outcome of one grounded step.

    ``proceed`` is the only field that authorises work. It is deliberately
    conservative: a step proceeds when nothing blocked, when a block was cleared
    by a legitimate remedy, or when the only objection came from a model gate
    (advisory by construction).
    """

    proceed: bool
    #: Ordered reasons, most actionable first. Empty when ``proceed``.
    reasons: tuple[str, ...] = ()
    gate_results: tuple[GateResult, ...] = ()
    solvability: SolvabilityVerdict | None = None
    escalation: Escalation = Escalation.PROCEED
    stop_reason: StopReason | None = None
    #: Anything model-layer flagged. Kept even when it did not block, because
    #: "a judge objected and was overruled" is not the same as "nothing happened".
    advisories: tuple[GateResult, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proceed": self.proceed,
            "reasons": list(self.reasons),
            "escalation": self.escalation.value,
            "stop_reason": self.stop_reason.value if self.stop_reason else None,
            "gates": [r.to_dict() for r in self.gate_results],
            "advisories": [r.to_dict() for r in self.advisories],
            "solvability": self.solvability.to_dict() if self.solvability else None,
            "detail": dict(self.detail),
        }


@dataclass
class GroundingService:
    """Composes the layers for one run or thread scope."""

    manifest: CapabilityManifest = field(default_factory=CapabilityManifest)
    pipeline: GatePipeline = field(default_factory=GatePipeline.standard)
    ledger: ClaimLedger = field(default_factory=ClaimLedger)
    memory: MemoryStore = field(default_factory=MemoryStore)
    effort: EffortController = field(default_factory=EffortController)
    metrics: GroundingMetrics = field(default_factory=GroundingMetrics)
    #: Emit the manifest into the agent's context at task start.
    inject_manifest: bool = True

    def __post_init__(self) -> None:
        self.metrics.reuse_probed = self.metrics.reuse_probed or False
        self._solvability = SolvabilityGate(self.manifest)

    # -- layer 1 ---------------------------------------------------------

    def context_block(self, objective: str = "") -> str:
        """What to put in front of the model before it plans.

        The manifest and the ledger's blocking claims, in that order. The ledger
        comes second because a blocking claim is a statement about the *current*
        trajectory while the manifest is about the system, and a run that already
        knows it is standing on something unsupported should hear that first.
        """
        parts: list[str] = []
        if self.inject_manifest:
            rendered = self.manifest.render_for_prompt(objective)
            if rendered:
                parts.append(rendered)
        ledger_block = self.ledger.render_for_prompt()
        if ledger_block:
            parts.append(ledger_block)
        return "\n\n".join(parts)

    # -- layers 2 + 3 ----------------------------------------------------

    def evaluate_step(
        self,
        subject: GateSubject,
        *,
        objective: str = "",
        required_capabilities: Sequence[str] = (),
        allow_partial: bool = True,
        model_stated_confidence: float | None = None,
    ) -> GroundingDecision:
        """Gate the step, then judge solvability, and decide whether to continue."""
        subject.ledger = self.ledger
        results = self.pipeline.run(subject)
        block = self.pipeline.block(results)
        advisories = tuple(r for r in results if r.blocked and r.layer is GateLayer.MODEL)

        verdict: SolvabilityVerdict | None = None
        reasons: list[str] = []
        escalation = Escalation.PROCEED
        proceed = True

        if block is not None:
            reasons.append(f"{block.code or block.gate}: {block.reason}")
            if block.remediation:
                reasons.append(block.remediation)
            escalation = Escalation.CHANGE_TOOL if block.code in {"unknown_tool", "unknown_subagent", "unknown_skill"} else Escalation.ABSTAIN
            proceed = False

        if objective:
            verdict = self._solvability.evaluate(
                SolvabilityRequest(
                    objective=objective,
                    required_capabilities=tuple(required_capabilities),
                    model_stated_confidence=model_stated_confidence,
                ),
                gate_results=results,
                allow_partial=allow_partial,
            )
            if not verdict.may_proceed and verdict.authoritative:
                reasons.append(f"{verdict.solvability.value}: {verdict.reason}")
                escalation = verdict.escalation
                proceed = False

        stop = self.effort.stop_reason()
        if stop is not None:
            reasons.append(f"stop ({stop.value}): {escalation_for_stop(stop).value}")
            escalation = escalation_for_stop(stop)
            proceed = False

        return GroundingDecision(
            proceed=proceed,
            reasons=tuple(reasons),
            gate_results=results,
            solvability=verdict,
            escalation=escalation,
            stop_reason=stop,
            advisories=advisories,
            detail={"objective": objective, "required_capabilities": list(required_capabilities)},
        )

    # -- layer 4 ---------------------------------------------------------

    def assert_claim(
        self,
        text: str,
        *,
        kind: ClaimKind = ClaimKind.FACT,
        evidence: Iterable[EvidenceRef] = (),
        step: int | None = None,
        origin: str = "",
    ) -> Claim:
        """Register a premise. Convenience wrapper that keeps ``step`` honest."""
        at = self.effort.steps_used if step is None else step
        claim = self.ledger.register(text, kind=kind, evidence=evidence, step=at, origin=origin)
        self.metrics.ledger = self.ledger
        return claim

    def depend_on(self, claim_ids: Iterable[str], *, step: int | None = None) -> tuple[Claim, ...]:
        """Record that this step stood on these premises."""
        at = self.effort.steps_used if step is None else step
        claims = self.ledger.record_uses(claim_ids, at)
        return claims

    def unsupported_premises(self) -> tuple[Claim, ...]:
        return self.ledger.blocking()

    def claim_audit(self) -> dict[str, Any]:
        return audit_claims(self.ledger)

    # -- layer 5 ---------------------------------------------------------

    def record_reuse(
        self,
        target: str,
        origin: Origin | str,
        *,
        recalled: bool,
        reused: bool,
        reimplemented: bool = False,
    ) -> None:
        """Record a reuse decision, and mark the probe as having run."""
        self.metrics.reuse.record(target, origin, recalled=recalled, reused=reused, reimplemented=reimplemented)
        self.metrics.reuse_probed = True

    # -- layer 6 ---------------------------------------------------------

    def remember(self, text: str, provenance: Provenance, **kwargs: Any) -> Any:
        """Write through the screened store, refusing on a structural hit."""
        return self.memory.add(text, provenance, **kwargs)

    # -- layer 7 ---------------------------------------------------------

    def complete_step(self, outcome: WorkOutcome, *, signature: str = "", escalate: bool = False) -> None:
        """Fold one attempt into the effort controller.

        Delegates the brake decision to :meth:`evaluate_step` rather than
        deciding here, so there is exactly one place that can stop a run.
        """
        self.effort.record(outcome, signature=signature, escalate=escalate)

    # -- layer 8 ---------------------------------------------------------

    def report(self) -> dict[str, Any]:
        """Everything, for run metadata and the ops surface."""
        return {
            "manifest": self.manifest.to_dict(),
            "effort": self.effort.report(),
            "metrics": self.metrics.to_dict(),
            "memory": self.memory.to_report(),
            "claims": {
                "total": len(self.ledger),
                "blocking": [c.claim_id for c in self.ledger.blocking()],
                "conflicts": [{"a": a, "b": b} for a, b in self.ledger.conflicts()],
                "audit_passed": self.claim_audit()["passed"],
            },
        }

    def grounding_context(self, objective: str = "") -> str:
        """Alias kept for readability at call sites that inject into a prompt."""
        return self.context_block(objective)
