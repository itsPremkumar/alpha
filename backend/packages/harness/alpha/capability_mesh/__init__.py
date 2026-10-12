"""Alpha Capability Mesh (ACM): evidence-backed capability records for
agents, models, tools, and skills. See
``docs/alpha-arena-new-capability-mesh-features-plan.md``.

Phase A ships the *contract*: the value types with their honesty rules
(``models.py``), the durable JSON store (``store.py``), and UNMEASURED
passport derivation from existing registries (``passports.py``).

Phase B adds the learning instruments on top of that contract, without
changing it: the cross-run capability memory (``memory.py`` — compact,
versioned outcome records, honest estimates, conservative credit assignment,
bounded retention), confidence-vs-outcome calibration (``calibration.py``),
and capability drift detection (``drift.py`` — a detector that reports and
never acts).

Phase C adds the routing plane on top of all of it, again without changing
any earlier contract: the situation fingerprint (``fingerprint.py`` — an
honest, deliberately crude keyword baseline), the microtask decomposition
contract with its injected model seam (``decomposition.py``), hard-gated
fit scoring (``fit.py``), PRIMARY/CHALLENGER/VERIFIER routing
(``assignment.py``) and the user-selectable decision policy resolution
table (``policy.py``). A later phase wires team formation and composition
on top — an estimate that arrives later is just the same record with real
observations behind it.

Phase D adds the graph, pair complementarity, greedy team selection and gap
detection (`graph.py`, `complementarity.py`, `team.py`, `gaps.py`), and
Phase E adds the composition compiler (`synthesis.py`) — conflicts become
named alternatives, never a synthetic "best of both" answer.

There is deliberately **no executor integration** here yet; see the package
``AGENTS.md`` for what that means for claims made about this package today.

Phase F adds three additive modules on top, each with the same contract and
still **no tool, no router and no executor wiring**: ``independence.py``
(correlated-error awareness and independence scoring — weak priors only for
model family/provider, source independence tracked separately from model
independence, and a majority vote refused by name), ``shadow.py`` (shadow
routing, canary policy and router self-evaluation — PREDICTIONS ONLY: nothing
spends budget, the candidate router is a caller-injected callable, and
promotion is a caller decision with a named rollback baseline) and
``explain.py`` (bounded explainability panels for the UI plan, never a
leaderboard, with advertised-but-unimplemented learning controls).

Phase G adds four more planning modules, still pure and offline:
``scheduler.py`` (value-of-information: the plan's exact expected-net-value
formula with an ``UNPRICED_PRIOR`` for anything unpriced, a settled-point
guard, and an audited stop reason), ``joint_routing.py`` (model +
instructions + skills + toolset + context slice + verification method as one
unit, resolved to exactly one ``Availability`` state naming the component
that failed), ``evaluation.py`` (rubric selection that refuses a version
swap, a holdout set that cannot overlap the suite, per-category reporting
with ``global_rank_available`` fixed False) and ``feedback.py``
(specialist-generated checks that cannot be accepted while non-deterministic,
and a durable rating ledger whose preference weight is ``0.0`` below
``FEEDBACK_MIN_SAMPLES``).

Phase H is the only phase that reaches a caller: ``service.py`` holds the
single implementation the ``capability_mesh`` tool and the
``/api/capability-mesh`` router both call, and ``config.py`` declares the
``config.yaml -> capability_mesh`` slice (``enabled: false`` by default).
Every planning surface is read/preview only — nothing in this package
dispatches a real agent run.
"""

from alpha.capability_mesh.assignment import (
    CHALLENGER_MARGIN,
    NO_ELIGIBLE_ENTITY,
    MicrotaskAssignment,
    Role,
    RoutingPlan,
    route,
)
from alpha.capability_mesh.calibration import (
    CALIBRATION_SAMPLE_FLOOR,
    CALIBRATION_TOLERANCE,
    CalibrationBucket,
    CalibrationReport,
    calibration,
)
from alpha.capability_mesh.decomposition import (
    HEURISTIC_NOTES,
    MAX_MICROTASKS,
    MAX_MODEL_OUTPUT_CHARS,
    MAX_SUMMARY_CHARS,
    MAX_TITLE_CHARS,
    Decomposition,
    DecompositionError,
    Microtask,
    decompose_heuristic,
    decompose_model,
    try_decompose,
)
from alpha.capability_mesh.drift import (
    DriftDirection,
    DriftReport,
    DriftSignal,
    detect_drift,
)
from alpha.capability_mesh.evaluation import (
    BENCHMARK_KEY_SEPARATOR,
    THIN_INCUMBENT_FLOOR,
    EvaluationCase,
    EvaluationReport,
    EvaluationSuite,
    ExplorationDecision,
    Rubric,
    RubricSelection,
    evaluate,
    select_rubric,
    should_explore,
)
from alpha.capability_mesh.explain import (
    CAPTION_LIMITED_HISTORY,
    CAPTION_UNMEASURED,
    CAPTION_WELL_MEASURED,
    MAX_REASON_CHARS,
    MAX_VISIBLE_ASSIGNMENTS,
    MAX_VISIBLE_CAPABILITIES,
    MAX_VISIBLE_CONFIDENCE,
    MAX_VISIBLE_DISAGREEMENTS,
    MAX_VISIBLE_FALLBACKS,
    MAX_VISIBLE_HONEST_NOTES,
    MAX_VISIBLE_SLOTS,
    MAX_VISIBLE_STEPS,
    RESOLUTION_LEFT_AS_ALTERNATIVES,
    RESOLUTION_UNRESOLVED,
    TASK_STATUS_ROUTED,
    TASK_STATUS_UNASSIGNED,
    TRUNCATION_MARKER,
    WELL_MEASURED_MIN_SAMPLES,
    ConfidenceInput,
    ConfidenceView,
    ContributionMapView,
    DisagreementView,
    ExplanationBundle,
    FallbackNotice,
    LearningControls,
    ResourceView,
    SpecialistAssignmentView,
    TaskMapItem,
    explain_plan,
)
from alpha.capability_mesh.feedback import (
    FEEDBACK_FILE_NAME,
    FEEDBACK_MIN_SAMPLES,
    FEEDBACK_STORE_VERSION,
    INSUFFICIENT_FEEDBACK_REASON,
    MAX_CORRECTION_CHARS,
    MAX_FINGERPRINT_CHARS,
    CheckKind,
    CheckProvenance,
    FeedbackLedger,
    FeedbackStoreError,
    GeneratedCheck,
    PreferenceUpdate,
    UserFeedback,
    review_check,
)
from alpha.capability_mesh.fingerprint import (
    LONG_TASK_PRESSURE_CHARS,
    TERMS,
    Band,
    Pressure,
    Risk,
    SituationFingerprint,
    fingerprint_task,
)
from alpha.capability_mesh.fit import (
    COVERAGE_WEIGHT,
    NEUTRAL_QUALITY,
    QUALITY_WEIGHT,
    RECENCY_PENALTY,
    RECENCY_WINDOW,
    TOOL_WEIGHT,
    UNMEASURED_PRIOR,
    UNMEASURED_UNCERTAINTY,
    FitScore,
    HardConstraintRefusal,
    eligible_entities,
    fit_score,
    rank_entities,
)
from alpha.capability_mesh.gaps import (
    DEFAULT_PROMOTION_GATE,
    DEFAULT_QUALITY_FLOOR,
    TOOL_CAPABILITY_PREFIX,
    CapabilityGap,
    GapKind,
    TemporarySpecialistPlan,
    detect_gaps,
    plan_specialist,
)
from alpha.capability_mesh.independence import (
    DEFAULT_TASK_CATEGORY,
    INDEPENDENCE_FILE_NAME,
    INDEPENDENT_METHODS,
    MODEL_FAMILY_PRIOR,
    NOTHING_MEASURED_BASIS,
    PAIR_SHORTFALL_PENALTY,
    PROVIDER_PRIOR,
    RISK_PENALTY,
    VERIFICATION_NOTES,
    IndependenceAxis,
    IndependenceNote,
    IndependenceScore,
    IndependenceScoreboard,
    IndependenceStoreError,
    VerificationMethod,
    independence_score,
    source_independence,
    verify_with_independent_method,
)
from alpha.capability_mesh.joint_routing import (
    NO_VERIFICATION_METHOD,
    UNSPECIFIED_INSTRUCTIONS_REF,
    Availability,
    ExecutionConfiguration,
    JointComponent,
    JointEligibility,
    JointRouter,
)
from alpha.capability_mesh.memory import (
    DEFAULT_MAX_RECORDS,
    DELIVERED_OUTCOMES,
    MEMORY_FILE_NAME,
    SELECTION_OUTCOMES,
    CapabilityMemory,
    CapabilityRecord,
    CreditReport,
    CreditRow,
    MemoryStoreError,
    OutcomeKind,
    fingerprint,
)
from alpha.capability_mesh.models import (
    EVIDENCE_RANK,
    CapabilityEstimate,
    CapabilityPassport,
    EntityKind,
    EvidenceLevel,
    derive_evidence_level,
    evidence_rank,
    worst_evidence_level,
)
from alpha.capability_mesh.passports import (
    WILDCARD_TASK_CATEGORY,
    agent_passport,
    model_passport,
    skill_passport,
    tool_passport,
)
from alpha.capability_mesh.policy import (
    MAX_AGENT_COUNT,
    MIN_AGENT_COUNT,
    DecisionPolicy,
    Depth,
    PolicyProfile,
    merge_fingerprint_policy,
    resolve_policy,
)
from alpha.capability_mesh.scheduler import (
    DEFAULT_UNRESOLVED_RISK_THRESHOLD,
    MAX_DESCRIPTION_CHARS,
    MAX_QUOTED_UNMET_CRITERIA,
    UNPRICED_PRIOR,
    ActionKind,
    CandidateAction,
    StopDecision,
    VoIEstimate,
    choose_next_action,
    expected_net_value,
    should_stop,
)
from alpha.capability_mesh.shadow import (
    DEFAULT_MIN_SAMPLE_BEFORE_PROMOTE,
    MIN_AGREEMENT_FOR_PROMOTE,
    NO_REPLAY_DATASET_NOTE,
    SHADOW_FILE_NAME,
    CanaryPolicy,
    ShadowComparison,
    ShadowReport,
    ShadowRunner,
    ShadowStoreError,
)
from alpha.capability_mesh.store import (
    STORE_DOCUMENT_VERSION,
    STORE_FILE_NAME,
    PassportStore,
    PassportStoreError,
    StoreState,
)
from alpha.capability_mesh.synthesis import (
    ASSUMPTION_PATTERNS,
    CONTEXT_WINDOW,
    QUALITY_FLOOR,
    ArtifactRecord,
    ArtifactStore,
    ArtifactStoreError,
    CompatibilityStatus,
    CompositionResult,
    ContributionSlot,
    SlotStatus,
    build_slots,
    compose_deliverable,
    detect_conflicts,
    extract_assumptions,
    resolve_slots,
)

__all__ = [
    "CALIBRATION_SAMPLE_FLOOR",
    "CALIBRATION_TOLERANCE",
    "CHALLENGER_MARGIN",
    "COVERAGE_WEIGHT",
    "DEFAULT_MAX_RECORDS",
    "DEFAULT_PROMOTION_GATE",
    "DEFAULT_QUALITY_FLOOR",
    "DELIVERED_OUTCOMES",
    "EVIDENCE_RANK",
    "HEURISTIC_NOTES",
    "LONG_TASK_PRESSURE_CHARS",
    "MAX_AGENT_COUNT",
    "MAX_MICROTASKS",
    "MAX_MODEL_OUTPUT_CHARS",
    "MAX_SUMMARY_CHARS",
    "MAX_TITLE_CHARS",
    "MEMORY_FILE_NAME",
    "MIN_AGENT_COUNT",
    "NEUTRAL_QUALITY",
    "NO_ELIGIBLE_ENTITY",
    "QUALITY_WEIGHT",
    "RECENCY_PENALTY",
    "RECENCY_WINDOW",
    "SELECTION_OUTCOMES",
    "STORE_DOCUMENT_VERSION",
    "STORE_FILE_NAME",
    "TERMS",
    "TOOL_CAPABILITY_PREFIX",
    "TOOL_WEIGHT",
    "UNMEASURED_PRIOR",
    "UNMEASURED_UNCERTAINTY",
    "WILDCARD_TASK_CATEGORY",
    "ASSUMPTION_PATTERNS",
    "CONTEXT_WINDOW",
    "QUALITY_FLOOR",
    "DEFAULT_MIN_SAMPLE_BEFORE_PROMOTE",
    "MIN_AGREEMENT_FOR_PROMOTE",
    "NO_REPLAY_DATASET_NOTE",
    "SHADOW_FILE_NAME",
    "DEFAULT_TASK_CATEGORY",
    "INDEPENDENCE_FILE_NAME",
    "INDEPENDENT_METHODS",
    "MODEL_FAMILY_PRIOR",
    "NOTHING_MEASURED_BASIS",
    "PAIR_SHORTFALL_PENALTY",
    "PROVIDER_PRIOR",
    "RISK_PENALTY",
    "VERIFICATION_NOTES",
    "MAX_REASON_CHARS",
    "MAX_VISIBLE_ASSIGNMENTS",
    "MAX_VISIBLE_CAPABILITIES",
    "MAX_VISIBLE_CONFIDENCE",
    "MAX_VISIBLE_DISAGREEMENTS",
    "MAX_VISIBLE_FALLBACKS",
    "MAX_VISIBLE_HONEST_NOTES",
    "MAX_VISIBLE_SLOTS",
    "MAX_VISIBLE_STEPS",
    "CAPTION_LIMITED_HISTORY",
    "CAPTION_UNMEASURED",
    "CAPTION_WELL_MEASURED",
    "RESOLUTION_LEFT_AS_ALTERNATIVES",
    "RESOLUTION_UNRESOLVED",
    "TASK_STATUS_ROUTED",
    "TASK_STATUS_UNASSIGNED",
    "TRUNCATION_MARKER",
    "WELL_MEASURED_MIN_SAMPLES",
    "ArtifactRecord",
    "ArtifactStore",
    "ArtifactStoreError",
    "CompatibilityStatus",
    "CompositionResult",
    "ContributionSlot",
    "SlotStatus",
    "CanaryPolicy",
    "ShadowComparison",
    "ShadowReport",
    "ShadowRunner",
    "ShadowStoreError",
    "IndependenceAxis",
    "IndependenceNote",
    "IndependenceScore",
    "IndependenceScoreboard",
    "IndependenceStoreError",
    "VerificationMethod",
    "ConfidenceInput",
    "ConfidenceView",
    "ContributionMapView",
    "DisagreementView",
    "ExplanationBundle",
    "FallbackNotice",
    "LearningControls",
    "ResourceView",
    "SpecialistAssignmentView",
    "TaskMapItem",
    "ActionKind",
    "Availability",
    "BENCHMARK_KEY_SEPARATOR",
    "CandidateAction",
    "CheckKind",
    "CheckProvenance",
    "DEFAULT_UNRESOLVED_RISK_THRESHOLD",
    "EvaluationCase",
    "EvaluationReport",
    "EvaluationSuite",
    "ExecutionConfiguration",
    "ExplorationDecision",
    "FEEDBACK_FILE_NAME",
    "FEEDBACK_MIN_SAMPLES",
    "FEEDBACK_STORE_VERSION",
    "FeedbackLedger",
    "FeedbackStoreError",
    "GeneratedCheck",
    "INSUFFICIENT_FEEDBACK_REASON",
    "JointComponent",
    "JointEligibility",
    "JointRouter",
    "MAX_CORRECTION_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_FINGERPRINT_CHARS",
    "MAX_QUOTED_UNMET_CRITERIA",
    "NO_VERIFICATION_METHOD",
    "PreferenceUpdate",
    "Rubric",
    "RubricSelection",
    "StopDecision",
    "THIN_INCUMBENT_FLOOR",
    "UNPRICED_PRIOR",
    "UNSPECIFIED_INSTRUCTIONS_REF",
    "UserFeedback",
    "VoIEstimate",
    "choose_next_action",
    "evaluate",
    "expected_net_value",
    "review_check",
    "select_rubric",
    "should_explore",
    "should_stop",
    "Band",
    "CalibrationBucket",
    "CalibrationReport",
    "CapabilityEstimate",
    "CapabilityGap",
    "CapabilityMemory",
    "CapabilityPassport",
    "CapabilityRecord",
    "CreditReport",
    "CreditRow",
    "DecisionPolicy",
    "Decomposition",
    "DecompositionError",
    "Depth",
    "DriftDirection",
    "DriftReport",
    "DriftSignal",
    "EntityKind",
    "EvidenceLevel",
    "FitScore",
    "GapKind",
    "HardConstraintRefusal",
    "MemoryStoreError",
    "Microtask",
    "MicrotaskAssignment",
    "OutcomeKind",
    "PassportStore",
    "PassportStoreError",
    "PolicyProfile",
    "Pressure",
    "Risk",
    "Role",
    "RoutingPlan",
    "SituationFingerprint",
    "StoreState",
    "TemporarySpecialistPlan",
    "agent_passport",
    "build_slots",
    "calibration",
    "compose_deliverable",
    "decompose_heuristic",
    "decompose_model",
    "derive_evidence_level",
    "detect_conflicts",
    "detect_drift",
    "detect_gaps",
    "eligible_entities",
    "evidence_rank",
    "explain_plan",
    "extract_assumptions",
    "fit_score",
    "fingerprint",
    "fingerprint_task",
    "independence_score",
    "merge_fingerprint_policy",
    "model_passport",
    "plan_specialist",
    "rank_entities",
    "resolve_policy",
    "resolve_slots",
    "route",
    "skill_passport",
    "source_independence",
    "tool_passport",
    "try_decompose",
    "verify_with_independent_method",
    "worst_evidence_level",
]
