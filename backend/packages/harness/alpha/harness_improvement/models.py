"""Data model for the Harness Improvement subsystem (Harness²-inspired).

One naming decision up front, because it decides every other one: a
**HarnessBundle** is an *immutable, content-addressed snapshot of the editable
harness surface* — prompts, guidance, skills, subagent definitions, scripts and
middleware configuration — and a **candidate** is never an edit to the active
bundle. It is a new bundle whose parent is the baseline. The active pointer
moves only through :mod:`alpha.harness_improvement.promotion`, and a probe
never touches it at all.

Every type here follows the same honesty rules the rest of the repo enforces:

* a measurement that did not happen is ``None``, never ``0``/``False`` — see
  :class:`ProbeOutcome` and :class:`ValidatorResult`;
* a decision travels with the evidence that produced it — see
  :class:`CompositionDecision` and :class:`ContrastReport`;
* an empty result and an unreadable store are different answers — see
  :mod:`alpha.harness_improvement.store`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class CandidateScope(StrEnum):
    """Where a candidate edit claims to apply.

    ``INVARIANT`` is reusable runtime/process behavior (tool selection,
    completion checks, context management). ``TASK_OR_DOMAIN`` is scoped to this
    task, repository, tool or professional domain. The scope decides retrieval
    and promotion eligibility, never the reverse: a task-scoped fact is never
    stored as a universal rule.
    """

    INVARIANT = "invariant"
    TASK_OR_DOMAIN = "task_or_domain"


class ComponentKind(StrEnum):
    """The eight editable component types from the Harness² component model.

    ``GUARDRAIL_GUIDANCE`` is *additional guidance*, never trusted policy: the
    non-bypassable safety core is not part of the editable surface at all, so no
    diff can ever target it (see :data:`PROTECTED_COMPONENT_IDS`).
    """

    SYSTEM_PROMPT = "system_prompt"
    GUARDRAIL_GUIDANCE = "guardrail_guidance"
    MEMORY_REFERENCE = "memory_reference"
    TOOL_GUIDANCE = "tool_guidance"
    SKILL = "skill"
    SUBAGENT = "subagent"
    SCRIPT = "script"
    MIDDLEWARE = "middleware"


class ExtractionPolicy(StrEnum):
    """How a component is activated, which decides its edit-cost class.

    Always-loaded components cost context on every model call; on-demand
    components cost nothing until their trigger matches. The composer prefers
    the cheapest effective intervention, so the policy travels with the kind
    instead of being re-guessed per call site.
    """

    ALWAYS_LOADED = "always_loaded"
    ON_DEMAND = "on_demand"
    SEPARATE_CONTEXT = "separate_context"
    DETERMINISTIC = "deterministic"
    RUNTIME_HOOK = "runtime_hook"


#: Extraction policy per component kind. This is the guide's edit-cost order
#: made mechanical: tool guidance before skills, skills before scripts, scripts
#: before always-on prompt text, middleware last.
KIND_EXTRACTION_POLICY: dict[ComponentKind, ExtractionPolicy] = {
    ComponentKind.SYSTEM_PROMPT: ExtractionPolicy.ALWAYS_LOADED,
    ComponentKind.GUARDRAIL_GUIDANCE: ExtractionPolicy.ALWAYS_LOADED,
    ComponentKind.MEMORY_REFERENCE: ExtractionPolicy.ALWAYS_LOADED,
    ComponentKind.TOOL_GUIDANCE: ExtractionPolicy.ALWAYS_LOADED,
    ComponentKind.SKILL: ExtractionPolicy.ON_DEMAND,
    ComponentKind.SUBAGENT: ExtractionPolicy.SEPARATE_CONTEXT,
    ComponentKind.SCRIPT: ExtractionPolicy.DETERMINISTIC,
    ComponentKind.MIDDLEWARE: ExtractionPolicy.RUNTIME_HOOK,
}

#: Kinds whose edits are high-risk enough that promotion always requires a
#: human approval even when ``automatic_global_promotion`` is enabled.
PRIVILEGED_KINDS: frozenset[ComponentKind] = frozenset({ComponentKind.SCRIPT, ComponentKind.MIDDLEWARE})

#: Component ids that belong to the trusted, non-editable policy core. A
#: candidate diff that names one of these is rejected before it is stored, and
#: the rejection is recorded — an LLM proposing data cannot dictate the
#: enforcement rules for that data.
PROTECTED_COMPONENT_IDS: frozenset[str] = frozenset(
    {
        "policy:permissions",
        "policy:network",
        "policy:process",
        "policy:secrets",
        "policy:filesystem",
        "guardrails:enforced",
    }
)


class ComponentOp(StrEnum):
    """A diff operation on one component."""

    ADD = "add"
    MODIFY = "modify"
    REMOVE = "remove"


class ImprovementStage(StrEnum):
    """The improvement-run state machine (guide §6.3).

    Every transition is persisted before the next stage starts, so a restart
    resumes from the last durable checkpoint and a cancelled run stays
    distinguishable from a missing one.
    """

    QUEUED = "queued"
    SNAPSHOTTING = "snapshotting"
    BASELINE_RUNNING = "baseline_running"
    BASELINE_RECORDED = "baseline_recorded"
    PROPOSING = "proposing"
    CANDIDATES_VALIDATING = "candidates_validating"
    PROBING = "probing"
    CONTRASTING = "contrasting"
    COMPOSING = "composing"
    SUCCESSOR_RUNNING = "successor_running"
    VERIFYING = "verifying"
    DELIVERABLE_SELECTED = "deliverable_selected"
    REFLECTING = "reflecting"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"


class PromotionState(StrEnum):
    """Promotion ladder for a candidate bundle (guide §11.3).

    ``DRAFT -> STATICALLY_VALIDATED -> PROBED -> REGRESSION_TESTED -> SHADOW ->
    CANARY -> ACTIVE`` with explicit off-ramps. A candidate may be *active for
    the current task's deliverable* without being promoted globally; those are
    separate decisions on purpose.
    """

    DRAFT = "draft"
    STATICALLY_VALIDATED = "statically_validated"
    PROBED = "probed"
    REGRESSION_TESTED = "regression_tested"
    SHADOW = "shadow"
    CANARY = "canary"
    ACTIVE = "active"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    ROLLED_BACK = "rolled_back"


class ContrastVerdict(StrEnum):
    """The four honest outcomes of a contrast (guide §7 step 6)."""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    CONFOUNDED = "confounded"


class ValidatorStatus(StrEnum):
    """Tri-state validator outcome. ``NOT_RUN`` is not a pass.

    A check that could not be evaluated (missing input, unreadable file) must
    never collapse into the same value as a check that ran and passed.
    """

    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


# ---------------------------------------------------------------------------
# Canonical serialization
# ---------------------------------------------------------------------------


def canonical_json(payload: Any) -> str:
    """Serialize *payload* deterministically (sorted keys, compact separators).

    Every hash in this subsystem is taken over this function's output, so two
    processes that build the same bundle compute the same ``bundle_id``.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def sha256_hex(payload: bytes | str) -> str:
    """Return the plain hex sha256 of *payload*."""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(data).hexdigest()


def content_hash(content: str) -> str:
    """Return ``sha256:<hex>`` (the repo's hash-tag vocabulary) for *content*."""
    return f"sha256:{sha256_hex(content)}"


# ---------------------------------------------------------------------------
# Components and bundles
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComponentRef:
    """A pointer to one immutable component *content* blob.

    ``component_id`` is a logical name — ``skills/report``, ``tool_guidance/bash``
    — never a filesystem path; path-shaped ids are rejected during validation so
    a candidate cannot address anything outside the harness surface.
    """

    kind: ComponentKind
    component_id: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "component_id": self.component_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComponentRef:
        return cls(
            kind=ComponentKind(data["kind"]),
            component_id=str(data["component_id"]),
            sha256=str(data["sha256"]),
            size_bytes=int(data.get("size_bytes", 0)),
        )


@dataclass(frozen=True, slots=True)
class HarnessBundle:
    """An immutable harness snapshot with parent lineage.

    ``bundle_id`` is the content hash of the canonical manifest payload, so the
    id *is* the integrity check: two bundles that differ in any component,
    lineage field or compatibility field cannot share an id. ``policy_version``
    records which trusted policy the bundle was compatible with; a candidate
    can never weaken it (enforced in :mod:`patches`).
    """

    bundle_id: str
    parent_bundle_id: str | None
    created_at: str
    created_by: str
    agent_profile: str
    components: tuple[ComponentRef, ...]
    alpha_runtime_version: str
    tool_schema_version: str
    policy_version: str
    source_run_id: str | None = None
    source_task_type: str | None = None
    promotion_state: PromotionState = PromotionState.DRAFT

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "bundle_id": self.bundle_id,
            "parent_bundle_id": self.parent_bundle_id,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "agent_profile": self.agent_profile,
            "components": [c.to_dict() for c in self.components],
            "compatibility": {
                "alpha_runtime_version": self.alpha_runtime_version,
                "tool_schema_version": self.tool_schema_version,
                "policy_version": self.policy_version,
            },
            "provenance": {
                "source_run_id": self.source_run_id,
                "source_task_type": self.source_task_type,
                "promotion_state": str(self.promotion_state),
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HarnessBundle:
        compat = data.get("compatibility", {})
        provenance = data.get("provenance", {})
        return cls(
            bundle_id=str(data["bundle_id"]),
            parent_bundle_id=data.get("parent_bundle_id"),
            created_at=str(data.get("created_at", "")),
            created_by=str(data.get("created_by", "")),
            agent_profile=str(data.get("agent_profile", "general")),
            components=tuple(ComponentRef.from_dict(c) for c in data.get("components", [])),
            alpha_runtime_version=str(compat.get("alpha_runtime_version", "")),
            tool_schema_version=str(compat.get("tool_schema_version", "")),
            policy_version=str(compat.get("policy_version", "")),
            source_run_id=provenance.get("source_run_id"),
            source_task_type=provenance.get("source_task_type"),
            promotion_state=PromotionState(provenance.get("promotion_state", PromotionState.DRAFT)),
        )

    def component(self, component_id: str) -> ComponentRef | None:
        for ref in self.components:
            if ref.component_id == component_id:
                return ref
        return None


def bundle_digest_payload(
    *,
    parent_bundle_id: str | None,
    components: tuple[ComponentRef, ...],
    alpha_runtime_version: str,
    tool_schema_version: str,
    policy_version: str,
) -> dict[str, Any]:
    """The exact payload a ``bundle_id`` hashes. Sorted components make the id
    independent of insertion order."""
    return {
        "schema_version": 1,
        "parent_bundle_id": parent_bundle_id,
        "components": [c.to_dict() for c in sorted(components, key=lambda c: (str(c.kind), c.component_id))],
        "compatibility": {
            "alpha_runtime_version": alpha_runtime_version,
            "tool_schema_version": tool_schema_version,
            "policy_version": policy_version,
        },
    }


def compute_bundle_id(
    *,
    parent_bundle_id: str | None,
    components: tuple[ComponentRef, ...],
    alpha_runtime_version: str,
    tool_schema_version: str,
    policy_version: str,
) -> str:
    """Content-addressed bundle id: ``sha256:<hex>`` over the manifest payload."""
    digest = sha256_hex(
        canonical_json(
            bundle_digest_payload(
                parent_bundle_id=parent_bundle_id,
                components=components,
                alpha_runtime_version=alpha_runtime_version,
                tool_schema_version=tool_schema_version,
                policy_version=policy_version,
            )
        )
    )
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------
# Diffs, hypotheses, proposals
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComponentDiff:
    """One candidate edit to one component.

    ``content`` is the full new content (never a patch-with-offsets the editor
    could misapply); for ``REMOVE`` it is ``None``. ``evidence_event_ids`` make
    the change traceable to observed trace events — a diff without evidence is
    rejected by the proposal validator unless it is explicitly declared as
    exploratory and the config allows it.
    """

    op: ComponentOp
    kind: ComponentKind
    component_id: str
    content: str | None
    rationale: str
    evidence_event_ids: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": str(self.op),
            "kind": str(self.kind),
            "component_id": self.component_id,
            "content": self.content,
            "rationale": self.rationale,
            "evidence_event_ids": list(self.evidence_event_ids),
            "depends_on": list(self.depends_on),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComponentDiff:
        return cls(
            op=ComponentOp(data["op"]),
            kind=ComponentKind(data["kind"]),
            component_id=str(data["component_id"]),
            content=data.get("content"),
            rationale=str(data.get("rationale", "")),
            evidence_event_ids=tuple(str(e) for e in data.get("evidence_event_ids", [])),
            depends_on=tuple(str(d) for d in data.get("depends_on", [])),
        )


@dataclass(frozen=True, slots=True)
class FailureHypothesis:
    """What the candidate believes it fixes, and how it could be wrong.

    ``falsification_condition`` is mandatory: a hypothesis that cannot be
    disconfirmed cannot be probed, only believed.
    """

    summary: str
    trace_event_ids: tuple[str, ...]
    expected_behavioral_change: str
    falsification_condition: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "trace_event_ids": list(self.trace_event_ids),
            "expected_behavioral_change": self.expected_behavioral_change,
            "falsification_condition": self.falsification_condition,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailureHypothesis:
        return cls(
            summary=str(data.get("summary", "")),
            trace_event_ids=tuple(str(e) for e in data.get("trace_event_ids", [])),
            expected_behavioral_change=str(data.get("expected_behavioral_change", "")),
            falsification_condition=str(data.get("falsification_condition", "")),
        )


@dataclass(frozen=True, slots=True)
class HarnessProposal:
    """An editor's bounded proposal: one scope, one hypothesis, N diffs."""

    proposal_id: str
    scope: CandidateScope
    base_bundle_id: str
    hypothesis: FailureHypothesis
    component_diffs: tuple[ComponentDiff, ...]
    expected_regressions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "scope": str(self.scope),
            "base_bundle_id": self.base_bundle_id,
            "hypothesis": self.hypothesis.to_dict(),
            "component_diffs": [d.to_dict() for d in self.component_diffs],
            "expected_regressions": list(self.expected_regressions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HarnessProposal:
        return cls(
            proposal_id=str(data["proposal_id"]),
            scope=CandidateScope(data.get("scope", CandidateScope.INVARIANT)),
            base_bundle_id=str(data.get("base_bundle_id", "")),
            hypothesis=FailureHypothesis.from_dict(data.get("hypothesis", {})),
            component_diffs=tuple(ComponentDiff.from_dict(d) for d in data.get("component_diffs", [])),
            expected_regressions=tuple(str(r) for r in data.get("expected_regressions", [])),
        )


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValidatorResult:
    """One deterministic check. ``detail`` carries the reason, not a verdict
    message: a failed check must say *why* it failed."""

    name: str
    status: ValidatorStatus
    detail: str = ""
    kind: str = "deliverable"

    @property
    def passed(self) -> bool | None:
        """``True``/``False`` for a check that ran, ``None`` for one that did not.

        ``None`` propagates to "unknown" everywhere consumer code would
        otherwise read a default of ``False`` as a real failure.
        """
        if self.status is ValidatorStatus.PASSED:
            return True
        if self.status is ValidatorStatus.FAILED:
            return False
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": str(self.status), "detail": self.detail, "kind": self.kind}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ValidatorResult:
        return cls(
            name=str(data.get("name", "")),
            status=ValidatorStatus(data.get("status", ValidatorStatus.NOT_RUN)),
            detail=str(data.get("detail", "")),
            kind=str(data.get("kind", "deliverable")),
        )


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Provider usage. Fields the provider did not report stay ``None``.

    A local model has zero *API* charge but still costs latency and RAM, so
    ``cost_usd`` is only ever the provider's own accounting; absence is not a
    zero-cost claim.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "cost_usd": self.cost_usd}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenUsage:
        return cls(
            input_tokens=data.get("input_tokens"),
            output_tokens=data.get("output_tokens"),
            cost_usd=data.get("cost_usd"),
        )


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """One execution of the task under one bundle (baseline or candidate).

    ``errored`` records that the *execution* failed; it is not the same as a
    deliverable that failed validation, and neither is a pass. ``environment``
    is the matched-conditions fingerprint so a contrast can detect a confounded
    comparison instead of assuming every probe ran identically.
    """

    run_id: str
    bundle_id: str
    candidate_id: str
    task_fingerprint: str
    trace_artifact: str
    deliverable_refs: tuple[str, ...]
    validator_results: tuple[ValidatorResult, ...]
    token_usage: TokenUsage
    elapsed_ms: int
    tool_error_count: int
    model_turns: int
    tool_calls: int
    environment: dict[str, Any] = field(default_factory=dict)
    errored: bool = False
    error: str | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "bundle_id": self.bundle_id,
            "candidate_id": self.candidate_id,
            "task_fingerprint": self.task_fingerprint,
            "trace_artifact": self.trace_artifact,
            "deliverable_refs": list(self.deliverable_refs),
            "validator_results": [v.to_dict() for v in self.validator_results],
            "token_usage": self.token_usage.to_dict(),
            "elapsed_ms": self.elapsed_ms,
            "tool_error_count": self.tool_error_count,
            "model_turns": self.model_turns,
            "tool_calls": self.tool_calls,
            "environment": self.environment,
            "errored": self.errored,
            "error": self.error,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProbeOutcome:
        return cls(
            run_id=str(data.get("run_id", "")),
            bundle_id=str(data.get("bundle_id", "")),
            candidate_id=str(data.get("candidate_id", "")),
            task_fingerprint=str(data.get("task_fingerprint", "")),
            trace_artifact=str(data.get("trace_artifact", "")),
            deliverable_refs=tuple(str(d) for d in data.get("deliverable_refs", [])),
            validator_results=tuple(ValidatorResult.from_dict(v) for v in data.get("validator_results", [])),
            token_usage=TokenUsage.from_dict(data.get("token_usage", {})),
            elapsed_ms=int(data.get("elapsed_ms", 0)),
            tool_error_count=int(data.get("tool_error_count", 0)),
            model_turns=int(data.get("model_turns", 0)),
            tool_calls=int(data.get("tool_calls", 0)),
            environment=dict(data.get("environment", {})),
            errored=bool(data.get("errored", False)),
            error=data.get("error"),
            notes=tuple(str(n) for n in data.get("notes", [])),
        )


@dataclass(frozen=True, slots=True)
class ContrastReport:
    """The narrow questions of guide §7 step 6, answered against evidence.

    ``uncertainty`` is mandatory reading, not a footnote: single-run probes
    cannot separate an improvement from run-to-run noise, and the report says so
    rather than implying otherwise.
    """

    candidate_id: str
    scope: CandidateScope
    verdict: ContrastVerdict
    targeted_failure_resolved: bool | None
    improvements: tuple[str, ...]
    regressions: tuple[str, ...]
    deltas: dict[str, Any]
    uncertainty: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "scope": str(self.scope),
            "verdict": str(self.verdict),
            "targeted_failure_resolved": self.targeted_failure_resolved,
            "improvements": list(self.improvements),
            "regressions": list(self.regressions),
            "deltas": self.deltas,
            "uncertainty": list(self.uncertainty),
            "evidence_refs": list(self.evidence_refs),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContrastReport:
        return cls(
            candidate_id=str(data["candidate_id"]),
            scope=CandidateScope(data.get("scope", CandidateScope.INVARIANT)),
            verdict=ContrastVerdict(data.get("verdict", ContrastVerdict.NEUTRAL)),
            targeted_failure_resolved=data.get("targeted_failure_resolved"),
            improvements=tuple(str(i) for i in data.get("improvements", [])),
            regressions=tuple(str(r) for r in data.get("regressions", [])),
            deltas=dict(data.get("deltas", {})),
            uncertainty=tuple(str(u) for u in data.get("uncertainty", [])),
            evidence_refs=tuple(str(e) for e in data.get("evidence_refs", [])),
            notes=str(data.get("notes", "")),
        )


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdoptedEdit:
    """A diff adopted into the successor, tied to the evidence for it."""

    diff: ComponentDiff
    candidate_id: str
    justification: str
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "diff": self.diff.to_dict(),
            "candidate_id": self.candidate_id,
            "justification": self.justification,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class RejectedEdit:
    """A diff the composer refused, with the reason — including "no evidence"."""

    diff: ComponentDiff
    candidate_id: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"diff": self.diff.to_dict(), "candidate_id": self.candidate_id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class CompositionDecision:
    """What the successor bundle is, and why — every edit tied to evidence.

    ``successor_bundle_id`` equal to the baseline bundle id means "no change",
    which is a valid and sometimes preferred result, not a failure.
    """

    baseline_bundle_id: str
    successor_bundle_id: str
    adopted: tuple[AdoptedEdit, ...]
    rejected: tuple[RejectedEdit, ...]
    retained_baseline: bool
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_bundle_id": self.baseline_bundle_id,
            "successor_bundle_id": self.successor_bundle_id,
            "adopted": [a.to_dict() for a in self.adopted],
            "rejected": [r.to_dict() for r in self.rejected],
            "retained_baseline": self.retained_baseline,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompositionDecision:
        return cls(
            baseline_bundle_id=str(data.get("baseline_bundle_id", "")),
            successor_bundle_id=str(data.get("successor_bundle_id", "")),
            adopted=tuple(
                AdoptedEdit(
                    diff=ComponentDiff.from_dict(a["diff"]),
                    candidate_id=str(a.get("candidate_id", "")),
                    justification=str(a.get("justification", "")),
                    evidence_refs=tuple(str(e) for e in a.get("evidence_refs", [])),
                )
                for a in data.get("adopted", [])
            ),
            rejected=tuple(
                RejectedEdit(
                    diff=ComponentDiff.from_dict(r["diff"]),
                    candidate_id=str(r.get("candidate_id", "")),
                    reason=str(r.get("reason", "")),
                )
                for r in data.get("rejected", [])
            ),
            retained_baseline=bool(data.get("retained_baseline", False)),
            rationale=str(data.get("rationale", "")),
        )


# ---------------------------------------------------------------------------
# Task contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeliverableCheck:
    """One deterministic acceptance check on a deliverable.

    ``kind`` is one of ``exists``, ``parses_json``, ``min_bytes``,
    ``required_sections``, ``text_contains``: all deterministic, all runnable
    without a model.
    """

    name: str
    path: str
    kind: str
    detail: str = ""
    sections: tuple[str, ...] = ()
    min_bytes: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "detail": self.detail,
            "sections": list(self.sections),
            "min_bytes": self.min_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeliverableCheck:
        return cls(
            name=str(data.get("name", "")),
            path=str(data.get("path", "")),
            kind=str(data.get("kind", "exists")),
            detail=str(data.get("detail", "")),
            sections=tuple(str(s) for s in data.get("sections", [])),
            min_bytes=int(data.get("min_bytes", 1)),
        )


@dataclass(frozen=True, slots=True)
class TaskContract:
    """The task as the improvement run sees it (guide §7 step 0).

    ``task_fingerprint`` is a **salted hash of the task text**: content stays in
    the run record, the fingerprint is what travels to cross-task surfaces so
    private task data does not leak into reusable summaries.
    """

    task: str
    outcome: str
    deliverables: tuple[str, ...] = ()
    checks: tuple[DeliverableCheck, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    domain_tags: tuple[str, ...] = ()
    task_type: str = "general"
    uncertainties: tuple[str, ...] = ()
    task_fingerprint: str = ""

    def to_dict(self, *, include_task: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_fingerprint": self.task_fingerprint,
            "outcome": self.outcome,
            "deliverables": list(self.deliverables),
            "checks": [c.to_dict() for c in self.checks],
            "allowed_tools": list(self.allowed_tools),
            "domain_tags": list(self.domain_tags),
            "task_type": self.task_type,
            "uncertainties": list(self.uncertainties),
        }
        if include_task:
            payload["task"] = self.task
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskContract:
        return cls(
            task=str(data.get("task", "")),
            outcome=str(data.get("outcome", "")),
            deliverables=tuple(str(d) for d in data.get("deliverables", [])),
            checks=tuple(DeliverableCheck.from_dict(c) for c in data.get("checks", [])),
            allowed_tools=tuple(str(t) for t in data.get("allowed_tools", [])),
            domain_tags=tuple(str(t) for t in data.get("domain_tags", [])),
            task_type=str(data.get("task_type", "general")),
            uncertainties=tuple(str(u) for u in data.get("uncertainties", [])),
            task_fingerprint=str(data.get("task_fingerprint", "")),
        )


def compute_task_fingerprint(task: str, *, salt: str = "alpha.harness_improvement") -> str:
    """Salted, non-reversible task fingerprint.

    The salt keeps the hash from being a rainbow-table lookup of chat prompts;
    it is deliberately a constant, not a per-installation secret, so two runs of
    the same task in different deployments still agree.
    """
    return f"sha256:{sha256_hex(f'{salt}:{task}')}"
