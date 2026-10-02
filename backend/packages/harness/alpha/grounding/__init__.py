"""Grounding: the layer that stops the agent claiming things it did not check.

## What problem this is

Alpha already had a lot of correctness machinery — a belief engine, an evidence
store, a calibration tracker, a claim auditor, a static advertised-vs-wired
check. The defect was not missing pieces. It was that none of them were **on the
path between a model request and a tool call**, so an agent could proceed through
all of them while every claim it made went unchecked.

This package is the wiring, plus the three things that genuinely did not exist:
a live capability manifest the agent can read, a claim ledger with support status
and blast radius, and an effort brake.

## The eight layers, and why the order is the design

| Layer | Module | What it prevents |
| --- | --- | --- |
| L0 deterministic ground truth | `gates` | A span with no possible check being delivered as fact |
| L1 capability manifest | `manifest` | Re-implementing what already exists; calling a tool that is not installed |
| L2 solvability gate | `solvability` | Planning work whose preconditions do not hold |
| L3 pre-flight research | `service` | Acting before knowing what is already known |
| L4 claim ledger | `claims` | Standing on an unsupported premise for twenty steps |
| L5 per-step gates | `gates` | Verifying only at the end, which is too late |
| L6 memory provenance | `provenance` | A poisoned fact outliving the session that planted it |
| L7 effort brake | `effort` | Talking itself out of a correct answer |
| L8 measurement | `metrics` | Believing it worked because pass rate held |

## The four rules that keep it from becoming another layer of prose

1. **A model gate may raise suspicion; it may never clear a block.** Structural,
   in :meth:`alpha.grounding.gates.GatePipeline.block`.
2. **Never inject source, only addresses.** Measured: an interface map more than
   doubles reuse of the agent's own work, while the full source achieves nothing
   and *increases* duplication.
3. **A refusal names the way out.** Every block carries a remediation, because an
   agent that can only proceed or stall will fabricate.
4. **Unmeasured is not zero.** Rates report ``None`` and audits report
   ``audited=False`` when nothing looked. This repo's own guidance — a completed
   run is never "verified" — is the same rule applied to the instruments.

## Relationship to what already existed

* :mod:`alpha.epistemics` updates a Bayesian posterior over a claim; `claims`
  records what the trajectory cited and who leaned on it. Different questions.
* :mod:`alpha.evidence` is a durable candidate/evaluation/promotion ledger;
  `claims` is per-trajectory and support-scored.
* :mod:`alpha.metacognition.calibration` already computes Brier scores; this
  package reports claim support and duplication, and does not re-derive
  calibration.
* :mod:`alpha.capabilities.honesty` answers "is this wired" statically. `manifest`
  consumes that verdict rather than re-deriving it, so a documented-but-uncalled
  subsystem cannot be advertised as available.

The normative contract is ``AGENTS.md`` beside this file.
"""

from alpha.grounding.claims import Claim, ClaimLedger, EvidenceRef, audit_claims, normalize_claim_text
from alpha.grounding.effort import (
    EffortController,
    EffortTier,
    EscalationStep,
    StopReason,
    WorkOutcome,
    escalation_for_stop,
)
from alpha.grounding.gates import (
    GatePipeline,
    GateSubject,
    SideEffectClass,
    check_claim_authorization,
    check_named_resource_exists,
    check_output_schema,
    check_reuse_probe,
    check_side_effect,
    check_tool_exists,
    parse_payload,
)
from alpha.grounding.manifest import (
    Availability,
    CapabilityEntry,
    CapabilityManifest,
    CapabilityProbe,
    EntrySource,
    build_manifest,
)
from alpha.grounding.metrics import GroundingMetrics, Origin, Rate, ReuseLedger, ReuseSample
from alpha.grounding.models import (
    BLOCKING_SUPPORT,
    TRUST_WEIGHT,
    ClaimKind,
    Escalation,
    GateLayer,
    GateResult,
    Groundability,
    SupportStatus,
    TrustTier,
    blocked_by,
)
from alpha.grounding.provenance import MemoryRecord, MemoryStore, Provenance, screen_text, tier_for_origin, verify_provenance
from alpha.grounding.service import GroundingDecision, GroundingService
from alpha.grounding.solvability import Solvability, SolvabilityGate, SolvabilityRequest, SolvabilityVerdict, evaluate_solvability

__all__ = [
    "BLOCKING_SUPPORT",
    "TRUST_WEIGHT",
    "Availability",
    "CapabilityEntry",
    "CapabilityManifest",
    "CapabilityProbe",
    "Claim",
    "ClaimKind",
    "ClaimLedger",
    "EntrySource",
    "Escalation",
    "EscalationStep",
    "EvidenceRef",
    "EffortController",
    "EffortTier",
    "GateLayer",
    "GatePipeline",
    "GateResult",
    "GateSubject",
    "Groundability",
    "GroundingDecision",
    "GroundingMetrics",
    "GroundingService",
    "MemoryRecord",
    "MemoryStore",
    "Origin",
    "Provenance",
    "Rate",
    "ReuseLedger",
    "ReuseSample",
    "SideEffectClass",
    "Solvability",
    "SolvabilityGate",
    "SolvabilityRequest",
    "SolvabilityVerdict",
    "StopReason",
    "SupportStatus",
    "TrustTier",
    "WorkOutcome",
    "audit_claims",
    "blocked_by",
    "build_manifest",
    "check_claim_authorization",
    "check_named_resource_exists",
    "check_output_schema",
    "check_reuse_probe",
    "check_side_effect",
    "check_tool_exists",
    "escalation_for_stop",
    "evaluate_solvability",
    "normalize_claim_text",
    "parse_payload",
    "screen_text",
    "tier_for_origin",
    "verify_provenance",
]
