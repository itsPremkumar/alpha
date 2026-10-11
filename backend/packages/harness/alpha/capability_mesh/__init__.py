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

There is deliberately **no tool, no HTTP router and no executor
integration** here yet; see the package ``AGENTS.md`` for what that means
for claims made about this package today.
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
    "ArtifactRecord",
    "ArtifactStore",
    "ArtifactStoreError",
    "CompatibilityStatus",
    "CompositionResult",
    "ContributionSlot",
    "SlotStatus",
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
    "extract_assumptions",
    "fit_score",
    "fingerprint",
    "fingerprint_task",
    "merge_fingerprint_policy",
    "model_passport",
    "plan_specialist",
    "rank_entities",
    "resolve_policy",
    "resolve_slots",
    "route",
    "skill_passport",
    "tool_passport",
    "try_decompose",
    "worst_evidence_level",
]
