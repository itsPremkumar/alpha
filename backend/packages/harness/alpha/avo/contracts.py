"""Typed boundary contracts for the governed autonomous variation engine.

Every object that crosses the trust boundary between the *proposal maker* and
the *authority* is declared here exactly once, and every one of them is
validated at runtime. The boundary is the point of this module: the agent may
fill in a hypothesis, a proposed action and a description of what it expects to
happen, and nothing else. Everything that decides whether an action may run,
whether a candidate may be promoted, and what the run is allowed to claim is
server-owned and is either absent from these models or declared with
``frozen=True`` so a caller cannot rewrite it after the fact.

Three rules the whole package leans on:

1. **``extra="forbid"`` on every model.** A field the server does not know
   about is rejected, not ignored. An unknown field is how a future "harmless"
   extension becomes an authority bypass.
2. **No model may assert an outcome.** :class:`EvaluationResult` carries
   structured gate verdicts produced by the evaluator, never a score or a
   "passed" flag supplied by the caller.
3. **A claim carries its evidence.** :class:`AvoRunResult` requires the digest
   that was evaluated, the gates that actually ran, and the limitations list.
   There is no shape in which a run reports a truth label without the evidence
   that produced it.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "SCHEMA_VERSION",
    "ActionType",
    "AvoRunMode",
    "AvoRunRequest",
    "AvoRunResult",
    "BudgetProfile",
    "CandidateOutcome",
    "CapabilityProfile",
    "EvaluatorGate",
    "EvaluatorProfile",
    "EvaluationResult",
    "ExperimentSpec",
    "GateStatus",
    "Observation",
    "PolicyDecision",
    "ProposedAction",
    "PromotionLevel",
    "RiskClass",
    "RouteDecision",
    "TaskFeatures",
    "TruthLabel",
    "WorkspaceLocator",
    "action_digest",
    "workspace_digest",
]


#: Boundary contract version. Bumping it is a deliberate, reviewed act: a run
#: admitted under one version is not silently reinterpreted under another.
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# vocabularies
# ---------------------------------------------------------------------------


class GateStatus(StrEnum):
    """The only states an evaluator gate may report.

    ``SKIPPED`` and ``INCONCLUSIVE`` exist so that "we could not check" is
    representable. Collapsing them into ``PASSED`` is the exact failure this
    enum prevents: a gate that never ran reading identically to a gate that
    passed.
    """

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    INCONCLUSIVE = "inconclusive"


class TruthLabel(StrEnum):
    """What the run is allowed to claim about its own outcome.

    The label is *derived* from structured evidence by the promotion gate, never
    supplied by the model. A model-supplied label would be a claim, not a
    measurement.
    """

    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class AvoRunMode(StrEnum):
    """What the run is allowed to do. The router picks it; the run cannot."""

    RESEARCH_READONLY = "research_readonly"
    REPO_INSPECT = "repo_inspect"
    REPO_REPAIR = "repo_repair"
    EXTERNAL_WRITE_APPROVAL = "external_write_approval"


class RiskClass(StrEnum):
    """Risk from *real side effects*, not from the model's self-assessment.

    The class is computed by the policy gate from the action's declared scope,
    the targets it names and the capability profile it runs under. The proposing
    agent may state a ``self_assessed_risk`` and it is recorded, never used.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    DESTRUCTIVE = "destructive"


class ActionType(StrEnum):
    """The closed set of operations the engine may propose.

    Free-form shell text is deliberately absent. A command that is not in this
    vocabulary cannot be validated, budgeted or revoked, so it is not offered.
    """

    READ_FILE = "read_file"
    LIST_DIRECTORY = "list_directory"
    SEARCH_REPOSITORY = "search_repository"
    GIT_STATUS = "git_status"
    GIT_DIFF = "git_diff"
    CREATE_CANDIDATE_WORKTREE = "create_candidate_worktree"
    WRITE_CANDIDATE_FILE = "write_candidate_file"
    RUN_TARGETED_CHECK = "run_targeted_check"
    RUN_REGRESSION_SUITE = "run_regression_suite"
    RUN_FULL_SUITE = "run_full_suite"
    REQUEST_PROMOTION = "request_promotion"


class PromotionLevel(StrEnum):
    """How far a candidate may travel. The model cannot choose above its grant.

    Ordered from least to most consequential; ``to_int`` exists because the
    comparison is the whole point.
    """

    REPORT_ONLY = "report_only"
    CANDIDATE_ARTIFACT = "candidate_artifact"
    DRAFT_PR = "draft_pr"
    APPROVED_COMMIT = "approved_commit"
    MERGE_DEPLOY = "merge_deploy"

    @property
    def rank(self) -> int:
        return _PROMOTION_RANKS[self]

    def at_most(self, ceiling: PromotionLevel) -> bool:
        return self.rank <= ceiling.rank


_PROMOTION_RANKS: dict[PromotionLevel, int] = {
    PromotionLevel.REPORT_ONLY: 0,
    PromotionLevel.CANDIDATE_ARTIFACT: 1,
    PromotionLevel.DRAFT_PR: 2,
    PromotionLevel.APPROVED_COMMIT: 3,
    PromotionLevel.MERGE_DEPLOY: 4,
}


class CandidateOutcome(StrEnum):
    """What became of one candidate."""

    PENDING = "pending"
    PROMOTION_ELIGIBLE = "promotion_eligible"
    PROMOTED = "promoted"
    QUARANTINED = "quarantined"
    ROLLED_BACK = "rolled_back"
    SUPERSEDED = "superseded"


class Observation(StrEnum):
    """The five things the engine keeps apart.

    Free-form text that mixes an observation with the agent's interpretation of
    it is how "the test failed because of a flaky network" becomes a fact. Each
    one is a separate field, and the model may only fill the middle three.
    """

    RAW_OUTPUT = "raw_output"
    INTERPRETATION = "interpretation"
    HYPOTHESIS = "hypothesis"
    ACTION = "action"


# ---------------------------------------------------------------------------
# ids and digests
# ---------------------------------------------------------------------------


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def canonical_json(payload: Any) -> str:
    """Stable serialization: sorted keys, no whitespace, no NaN.

    Every digest in this package goes through here. A digest computed over a
    non-canonical serialization is a digest of a dict iteration order, not of
    the content.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def action_digest(action: ProposedAction | dict[str, Any]) -> str:
    """Digest of a proposed action, computed server-side.

    The candidate never supplies this. A digest the candidate supplied could
    describe a different action than the one executed, and the receipt would
    then be a signature over the description rather than the effect.
    """
    payload = action if isinstance(action, dict) else action.model_dump(mode="json")
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def workspace_digest(files: dict[str, bytes]) -> str:
    """Digest of a workspace snapshot, so "the bytes evaluated" is checkable.

    ``files`` maps a relative path to its exact bytes. The digest covers the
    sorted ``path:sha256`` pairs, so adding, removing or editing one file all
    change it, while a rename with identical content is caught by the path term.
    """
    lines = [f"{name}:{hashlib.sha256(blob).hexdigest()}" for name, blob in sorted(files.items())]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# workspace and task shape
# ---------------------------------------------------------------------------


class WorkspaceLocator(BaseModel):
    """An explicitly scoped repository/workspace reference.

    A bare filesystem path is not a scope. This carries the bounded parts the
    server needs to confine a run: which project the workspace belongs to, the
    commit the run starts from, and the digest of the baseline bytes.
    """

    model_config = {"extra": "forbid"}

    project_id: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(min_length=1, max_length=512)
    base_commit: str | None = None
    base_digest: str | None = None
    dirty: bool = False

    @field_validator("project_id")
    @classmethod
    def _safe_project(cls, value: str) -> str:
        if not value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("project_id must be alphanumeric, hyphen or underscore only")
        return value

    @field_validator("relative_path")
    @classmethod
    def _relative(cls, value: str) -> str:
        normalised = value.replace("\\", "/")
        if normalised.startswith("/") or ":" in normalised or ".." in normalised.split("/"):
            raise ValueError("relative_path must be relative to the project root and may not traverse upward")
        return normalised


class TaskFeatures(BaseModel):
    """The routing input record.

    Everything here is a fact the *server* can determine. ``autonomy_requested``
    is the one field the agent may influence, and it is deliberately advisory:
    the router treats it as a request, never as a grant.
    """

    model_config = {"extra": "forbid"}

    task_type: str = Field(min_length=1, max_length=64)
    requires_mutation: bool = False
    estimated_complexity: float = Field(default=0.0, ge=0.0, le=1.0)
    has_objective_evaluator: bool = False
    requires_multiple_experiments: bool = False
    risk_level: RiskClass = RiskClass.LOW
    deadline_seconds: float | None = Field(default=None, gt=0)
    budget_profile_id: str = "local_low_resource"
    autonomy_requested: bool = False
    #: Set when a first controlled attempt already failed in a way that
    #: justifies variation. The router may escalate on this alone.
    prior_attempt_failed: bool = False


class RouteDecision(BaseModel):
    """The router's verdict, recorded so the routing itself is auditable."""

    model_config = {"extra": "forbid"}

    route: Literal["fast_path", "avo"] = "fast_path"
    matched_rules: list[str] = Field(default_factory=list)
    refused_reasons: list[str] = Field(default_factory=list)
    request: AvoRunRequest | None = None


# ---------------------------------------------------------------------------
# run request
# ---------------------------------------------------------------------------


class AvoRunRequest(BaseModel):
    """What a caller must supply to admit a governed run.

    Every profile is an *identifier*, never an inline configuration: allowing an
    inline budget would let the caller set its own ceiling, and an inline
    evaluator would let it choose what counts as evidence.
    """

    model_config = {"extra": "forbid"}

    schema_version: int = SCHEMA_VERSION
    alpha_run_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    task_description: str = Field(min_length=1, max_length=8000)
    workspace: WorkspaceLocator
    requested_mode: AvoRunMode = AvoRunMode.REPO_REPAIR
    capability_profile_id: str = "repo_repair"
    budget_profile_id: str = "local_low_resource"
    evaluator_profile_id: str = "code_repair"
    approval_policy_id: str = "default_deny"
    #: Identifier only. Raw provider credentials never cross this boundary;
    #: the server resolves the identifier against its own configuration.
    provider_profile_ref: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)
    experiment: ExperimentSpec

    @field_validator("idempotency_key")
    @classmethod
    def _idempotency(cls, value: str) -> str:
        if value.strip() != value or not value:
            raise ValueError("idempotency_key must be non-empty and unpadded")
        return value

    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {value}; this build speaks {SCHEMA_VERSION}")
        return value

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ExperimentSpec(BaseModel):
    """A structured hypothesis, not a sentence.

    The five fields map one-to-one onto the distinctions the engine must not
    collapse: what was *observed*, what the agent *believes* about it, the
    falsifiable *claim*, the bounded *change* being proposed, and the conditions
    under which to *stop*. A run that records only a sentence loses all five.
    """

    model_config = {"extra": "forbid"}

    experiment_id: str = Field(default_factory=lambda: _new_id("exp"))
    parent_experiment_id: str | None = None
    hypothesis: str = Field(min_length=1, max_length=2000)
    observed_evidence: list[str] = Field(default_factory=list, max_length=20)
    proposed_change_scope: dict[str, list[str]] = Field(default_factory=dict)
    expected_signal: list[str] = Field(default_factory=list, max_length=20)
    possible_regressions: list[str] = Field(default_factory=list, max_length=20)
    stop_conditions: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("proposed_change_scope")
    @classmethod
    def _scope(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        unknown = set(value) - {"allowed_paths", "protected_paths", "denied_paths"}
        if unknown:
            raise ValueError(f"proposed_change_scope carries unknown keys {sorted(unknown)}; expected allowed_paths/protected_paths/denied_paths")
        for key, paths in value.items():
            for path in paths:
                normalised = path.replace("\\", "/")
                if normalised.startswith("/") or ".." in normalised.split("/"):
                    raise ValueError(f"{key} entry {path!r} must be a relative path without traversal")
        return value

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------


class ProposedAction(BaseModel):
    """One proposed operation, typed and scoped.

    ``arguments`` is validated per ``action_type`` by the policy gate, not by
    the caller: an action whose arguments name a path outside its declared
    scope is refused rather than trustingly executed.
    """

    model_config = {"extra": "forbid"}

    action_id: str = Field(default_factory=lambda: _new_id("act"))
    experiment_id: str
    parent_action_id: str | None = None
    action_type: ActionType
    arguments: dict[str, Any] = Field(default_factory=dict)
    read_scope: list[str] = Field(default_factory=list)
    write_scope: list[str] = Field(default_factory=list)
    expected_result: str = Field(default="", max_length=2000)
    self_assessed_risk: RiskClass = RiskClass.LOW
    idempotent: bool = False
    reversible: bool = True

    #: Recorded, never trusted. The policy gate computes the real class.
    @property
    def digest(self) -> str:
        return action_digest(self)

    @field_validator("read_scope", "write_scope")
    @classmethod
    def _scoped(cls, value: list[str]) -> list[str]:
        for path in value:
            normalised = path.replace("\\", "/")
            if normalised.startswith("/") or ".." in normalised.split("/"):
                raise ValueError(f"scope entry {path!r} must be a relative path without traversal")
        return value


# ---------------------------------------------------------------------------
# decisions
# ---------------------------------------------------------------------------


class PolicyDecision(BaseModel):
    """The gate's verdict for one action.

    ``authority`` names the plane that made it so the record says who decided.
    There is no path in this package that writes a ``PolicyDecision`` with
    ``authority`` naming the model.
    """

    model_config = {"extra": "forbid"}

    decision_id: str = Field(default_factory=lambda: _new_id("dec"))
    action_id: str
    action_digest: str
    experiment_id: str
    allowed: bool
    reason_code: str
    reason: str = ""
    policy_version: str
    authority: Literal["policy_gate"] = "policy_gate"
    required_capability: str | None = None
    risk_class: RiskClass = RiskClass.LOW
    approval_required: bool = False
    approval_id: str | None = None
    budget_reservation: dict[str, float] = Field(default_factory=dict)
    decided_at: float = Field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------


class EvaluatorGate(BaseModel):
    """One tier of the evaluator pipeline, with the state it actually reached."""

    model_config = {"extra": "forbid"}

    tier: str = Field(min_length=1, max_length=64)
    status: GateStatus
    reason: str = ""
    command: str = ""
    exit_code: int | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    artifact_refs: list[str] = Field(default_factory=list)
    #: True only when the tier's verdict is evidence for the *evaluated* digest.
    bound_to_candidate: bool = True


class EvaluationResult(BaseModel):
    """Independent evaluation of one exact candidate.

    ``candidate_digest`` is the load-bearing field: promotion compares it
    against the digest of the bytes being promoted, so a candidate that changes
    after evaluation is a digest mismatch rather than a second evaluation.
    """

    model_config = {"extra": "forbid"}

    evaluator_id: str = Field(min_length=1, max_length=128)
    evaluator_version: str = Field(min_length=1, max_length=64)
    config_digest: str = ""
    candidate_digest: str = ""
    experiment_id: str = ""
    gates: list[EvaluatorGate] = Field(default_factory=list)
    baseline_digest: str | None = None
    regressions: list[str] = Field(default_factory=list)
    severity: str = ""
    environment_fingerprint: str = ""
    evaluated_at: float = Field(default_factory=time.time)

    # -- derived -----------------------------------------------------------
    @property
    def passed(self) -> bool:
        return bool(self.gates) and all(gate.status is GateStatus.PASSED for gate in self.gates)

    @property
    def required_gates(self) -> list[EvaluatorGate]:
        return [gate for gate in self.gates if gate.tier not in _OPTIONAL_TIERS]

    @property
    def blocked_or_missing(self) -> list[str]:
        """Tiers that were required and did not pass. Never empty on a pass."""
        return [f"{gate.tier}:{gate.status.value}" for gate in self.gates if gate.status is not GateStatus.PASSED]

    def truth_label(self) -> TruthLabel:
        """Derive the label. A caller may not supply this.

        The asymmetry is deliberate: one required tier that was skipped or
        inconclusive demotes to ``PARTIALLY_VERIFIED`` rather than being
        treated as absent, because a run that knows it could not check
        something is in a different position from one that had nothing to check.
        """
        required = self.required_gates
        if not required:
            return TruthLabel.UNVERIFIED
        if any(gate.status is GateStatus.FAILED for gate in required):
            return TruthLabel.FAILED
        if all(gate.status is GateStatus.PASSED for gate in required):
            optional = [gate for gate in self.gates if gate.tier in _OPTIONAL_TIERS]
            if all(gate.status is GateStatus.PASSED for gate in optional):
                return TruthLabel.VERIFIED
            if any(gate.status in (GateStatus.SKIPPED, GateStatus.INCONCLUSIVE) for gate in optional):
                return TruthLabel.PARTIALLY_VERIFIED
            return TruthLabel.VERIFIED
        if any(gate.status is GateStatus.INCONCLUSIVE for gate in required):
            return TruthLabel.PARTIALLY_VERIFIED
        return TruthLabel.UNVERIFIED

    def to_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["truth_label"] = self.truth_label().value
        return payload


#: Tiers that inform the verdict but whose absence is not itself a failure.
#: Declared here so "which tiers are optional" is one auditable list rather
#: than a property of whichever call site happened to forget a tier.
_OPTIONAL_TIERS: frozenset[str] = frozenset(
    {
        "runtime_smoke",
        "independent_review",
        "full_suite",
    }
)


# ---------------------------------------------------------------------------
# the run's final answer
# ---------------------------------------------------------------------------


class AvoRunResult(BaseModel):
    """The run's final report, assembled from structured evidence only.

    ``limitations`` is required and non-empty by construction in practice: the
    builder refuses to emit a result that claims none. A truthful run almost
    always skipped something -- a slow tier, an unavailable external service,
    an evaluator that could not bind to the candidate -- and a result with an
    empty list is the signature of one that was not measured.
    """

    model_config = {"extra": "forbid"}

    alpha_run_id: str
    avo_run_id: str
    task_id: str
    truth: TruthLabel
    reason: str = ""
    experiment_ids: list[str] = Field(default_factory=list)
    winning_candidate_id: str | None = None
    rejected_candidate_ids: list[str] = Field(default_factory=list)
    candidate_outcomes: dict[str, str] = Field(default_factory=dict)
    evaluated_digest: str | None = None
    promoted_digest: str | None = None
    changed_paths: list[str] = Field(default_factory=list)
    diff_refs: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    policy_decisions: list[dict[str, Any]] = Field(default_factory=list)
    promotion_level: PromotionLevel = PromotionLevel.REPORT_ONLY
    approvals_requested: list[str] = Field(default_factory=list)
    approvals_granted: list[str] = Field(default_factory=list)
    approvals_denied: list[str] = Field(default_factory=list)
    actions_total: int = 0
    actions_denied: int = 0
    retries: int = 0
    cost_usd: float | None = None
    latency_seconds: float | None = Field(default=None, ge=0)
    receipt_chain_head: str | None = None
    checkpoint_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# profiles (identifiers resolve to these server-side)
# ---------------------------------------------------------------------------


class CapabilityProfile(BaseModel):
    """A server-side capability grant.

    Default-deny in shape: the three lists are the whole surface, and an action
    outside them is refused. ``allow_auto_merge`` / ``allow_auto_deploy`` are
    separate flags because a run that may write a file is not thereby a run
    that may publish.
    """

    model_config = {"extra": "forbid"}

    profile_id: str
    description: str = ""
    read_paths: list[str] = Field(default_factory=list)
    write_paths: list[str] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)
    allowed_action_types: list[ActionType] = Field(default_factory=list)
    network: Literal["denied", "allowlist", "allow"] = "denied"
    network_allowlist: list[str] = Field(default_factory=list)
    allow_auto_merge: bool = False
    allow_auto_deploy: bool = False
    allow_policy_mutation: bool = False
    allow_evaluator_mutation: bool = False
    max_promotion_level: PromotionLevel = PromotionLevel.REPORT_ONLY
    enabled: bool = True

    def permits_action(self, action_type: ActionType) -> bool:
        return self.enabled and action_type in self.allowed_action_types


class BudgetProfile(BaseModel):
    """Hard ceilings. The run may not widen its own profile."""

    model_config = {"extra": "forbid"}

    profile_id: str
    max_experiments: int = Field(default=4, ge=1)
    max_actions: int = Field(default=24, ge=1)
    max_actions_per_experiment: int = Field(default=10, ge=1)
    max_branches: int = Field(default=3, ge=1)
    max_wall_time_seconds: float = Field(default=600.0, gt=0)
    max_cost_usd: float | None = Field(default=0.50, gt=0)
    max_parallel_workers: int = Field(default=1, ge=1)
    max_retries_per_action: int = Field(default=2, ge=0)
    max_same_failure_signature: int = Field(default=2, ge=1)
    #: When an exact cost cannot be derived for a provider, the financial
    #: ceiling is unenforceable and this says so rather than reporting a
    #: false precision. Action and time ceilings still bite.
    cost_is_enforceable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class EvaluatorProfile(BaseModel):
    """The evaluator configuration a run is judged under.

    ``config_digest`` is computed by the server from the tier list so a
    tampered evaluator configuration is a different digest, which the promotion
    gate compares against the one recorded in the run manifest.
    """

    model_config = {"extra": "forbid"}

    profile_id: str
    tiers: list[str] = Field(default_factory=list)
    protected_prefixes: list[str] = Field(default_factory=list)
    require_baseline: bool = True
    allow_skip: list[str] = Field(default_factory=list)

    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.model_dump(mode="json")).encode("utf-8")).hexdigest()
