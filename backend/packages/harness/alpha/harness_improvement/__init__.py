"""Alpha's Harness²-inspired recursive harness improvement subsystem.

The package implements the method of
``docs/ALPHA_HARNESS2_IMPLEMENTATION_GUIDE.md`` in Python, adapted to Alpha's
runtime rather than ported from the guide's Rust sketches:

* :mod:`alpha.harness_improvement.models` — the data model: immutable
  content-addressed :class:`HarnessBundle`s, proposals with falsifiable
  hypotheses, probe outcomes and contrast reports.
* :mod:`alpha.harness_improvement.store` — the single durable owner of every
  artifact, under ``runtime_home()/harness_improvement/``; absent is not
  corrupt, and an unreadable record is reported rather than read as empty.
* :mod:`alpha.harness_improvement.patches` — the validation gate: protected
  policy surface, schema/path hygiene, credential and network screens, size
  bounds. Refusals are specific and recorded.
* :mod:`alpha.harness_improvement.state` — the run state machine with durable
  checkpoints and per-stage idempotency keys.
* :mod:`alpha.harness_improvement.budgets` — hard ceilings, a measured ledger,
  and the deliverable reserve.
* :mod:`alpha.harness_improvement.probes` — isolated per-candidate workspaces
  and the matched-environment fingerprint.
* :mod:`alpha.harness_improvement.validators` — deterministic deliverable
  checks; ``NOT_RUN`` is never a pass.
* :mod:`alpha.harness_improvement.contrast` — the eight narrow questions of
  guide §7 step 6, with confounders and uncertainty surfaced.
* :mod:`alpha.harness_improvement.compose` — evidence-supported composition
  that is allowed to return "no change".
* :mod:`alpha.harness_improvement.experiences` — the evidence-gated experience
  library, separate from conversation/semantic memory.
* :mod:`alpha.harness_improvement.promotion` — the gated ladder, atomic
  activation and one-step rollback.
* :mod:`alpha.harness_improvement.engine` — the orchestrator that drives the
  run, plus the executor/editor seams that stay honest when they are absent.
* :mod:`alpha.harness_improvement.metrics` — process/outcome metrics and the
  fixed synthetic + held-out suites.

Package guide: ``backend/packages/harness/alpha/harness_improvement/AGENTS.md``.
"""

from alpha.harness_improvement.budgets import BudgetCeilings, BudgetExceeded, BudgetState, StageCharge, estimate_run_cost
from alpha.harness_improvement.components import ComponentIdError, UnknownComponentsError, build_bundle, build_component_ref, diff_bundles, validate_component_id
from alpha.harness_improvement.compose import ComposerInput, DeliverableCandidate, compose_successor, select_deliverable
from alpha.harness_improvement.contrast import CandidateEvaluation, build_contrast
from alpha.harness_improvement.engine import HarnessImprovementEngine, HarnessImprovementSettings, ImprovementRunError, qualify_task
from alpha.harness_improvement.executor import (
    EditorAbsent,
    ExecutorAbsent,
    HarnessEditor,
    HarnessExecutor,
    ImprovementContext,
    ScriptedEditor,
    ScriptedExecutor,
    TaskSpec,
)
from alpha.harness_improvement.experiences import (
    ExperienceBuildResult,
    ExperienceMetadata,
    ExperienceQuery,
    ExperienceStatus,
    build_experience,
    is_stale,
    load_experience,
    render_experience_markdown,
    save_experience,
    select_experiences,
    supersede_experience,
)
from alpha.harness_improvement.maintenance import (
    MaintenancePlan,
    analyze_experiences,
    apply_maintenance,
    run_experience_maintenance,
)
from alpha.harness_improvement.metrics import (
    HELD_OUT_TASKS,
    SYNTHETIC_TASKS,
    SyntheticTask,
    compare_baseline_to_improved,
    held_out_suite,
    summarize_run_records,
    synthetic_suite,
)
from alpha.harness_improvement.models import (
    KIND_EXTRACTION_POLICY,
    PRIVILEGED_KINDS,
    PROTECTED_COMPONENT_IDS,
    AdoptedEdit,
    CandidateScope,
    ComponentDiff,
    ComponentKind,
    ComponentOp,
    ComponentRef,
    CompositionDecision,
    ContrastReport,
    ContrastVerdict,
    DeliverableCheck,
    ExtractionPolicy,
    FailureHypothesis,
    HarnessBundle,
    HarnessProposal,
    ImprovementStage,
    ProbeOutcome,
    PromotionState,
    RejectedEdit,
    TaskContract,
    TokenUsage,
    ValidatorResult,
    ValidatorStatus,
    compute_bundle_id,
    compute_task_fingerprint,
    content_hash,
)
from alpha.harness_improvement.patches import PatchPolicy, PatchRejection, PatchValidation, validate_proposal
from alpha.harness_improvement.probes import (
    ProbeEnvironment,
    ProbeWorkspace,
    cleanup_probe_workspace,
    create_probe_workspace,
    environment_confounders,
    environment_fingerprint,
    materialize_bundle,
    probe_summary,
)
from alpha.harness_improvement.promotion import (
    PromotionDecision,
    PromotionEvent,
    PromotionEvidence,
    PromotionPolicy,
    RegressionSuiteResult,
    activate,
    active_bundle,
    evaluate_promotion,
    promote_state,
    promotion_history,
    reject_candidate,
    rollback,
)
from alpha.harness_improvement.redaction import RedactionFinding, redact_mapping, redact_text, scan_for_credential_paths, scan_for_network_commands
from alpha.harness_improvement.state import ImprovementRun, InvalidTransition, StageCheckpoint, new_run, stage_key
from alpha.harness_improvement.store import ActivePointer, BudgetLedgerEntry, HarnessImprovementStore, Listing, StoreCorrupt, StoreUnreadable, safe_id, utc_now
from alpha.harness_improvement.validators import CHECK_KINDS, confined_path, run_deliverable_checks, validator_summary

__all__ = [
    "AdoptedEdit",
    "ActivePointer",
    "BudgetCeilings",
    "BudgetExceeded",
    "BudgetLedgerEntry",
    "BudgetState",
    "CHECK_KINDS",
    "CandidateEvaluation",
    "CandidateScope",
    "ComponentDiff",
    "ComponentIdError",
    "ComponentKind",
    "ComponentOp",
    "ComponentRef",
    "ComposerInput",
    "CompositionDecision",
    "ContrastReport",
    "ContrastVerdict",
    "DeliverableCandidate",
    "DeliverableCheck",
    "EditorAbsent",
    "ExecutorAbsent",
    "ExperienceBuildResult",
    "ExperienceMetadata",
    "ExperienceQuery",
    "ExperienceStatus",
    "ExtractionPolicy",
    "FailureHypothesis",
    "HELD_OUT_TASKS",
    "HarnessBundle",
    "HarnessEditor",
    "HarnessExecutor",
    "HarnessImprovementEngine",
    "HarnessImprovementSettings",
    "HarnessImprovementStore",
    "HarnessProposal",
    "ImprovementContext",
    "ImprovementRun",
    "ImprovementRunError",
    "ImprovementStage",
    "InvalidTransition",
    "KIND_EXTRACTION_POLICY",
    "Listing",
    "MaintenancePlan",
    "PRIVILEGED_KINDS",
    "PROTECTED_COMPONENT_IDS",
    "PatchPolicy",
    "PatchRejection",
    "PatchValidation",
    "ProbeEnvironment",
    "ProbeOutcome",
    "ProbeWorkspace",
    "PromotionDecision",
    "PromotionEvent",
    "PromotionEvidence",
    "PromotionPolicy",
    "PromotionState",
    "RedactionFinding",
    "RegressionSuiteResult",
    "RejectedEdit",
    "SYNTHETIC_TASKS",
    "ScriptedEditor",
    "ScriptedExecutor",
    "StageCharge",
    "StageCheckpoint",
    "StoreCorrupt",
    "StoreUnreadable",
    "SyntheticTask",
    "TaskContract",
    "TaskSpec",
    "TokenUsage",
    "UnknownComponentsError",
    "ValidatorResult",
    "ValidatorStatus",
    "activate",
    "analyze_experiences",
    "apply_maintenance",
    "active_bundle",
    "build_bundle",
    "build_component_ref",
    "build_contrast",
    "build_experience",
    "cleanup_probe_workspace",
    "compare_baseline_to_improved",
    "compose_successor",
    "compute_bundle_id",
    "compute_task_fingerprint",
    "confined_path",
    "content_hash",
    "create_probe_workspace",
    "diff_bundles",
    "environment_confounders",
    "environment_fingerprint",
    "estimate_run_cost",
    "evaluate_promotion",
    "held_out_suite",
    "is_stale",
    "load_experience",
    "materialize_bundle",
    "new_run",
    "probe_summary",
    "promote_state",
    "promotion_history",
    "qualify_task",
    "redact_mapping",
    "redact_text",
    "reject_candidate",
    "render_experience_markdown",
    "rollback",
    "run_deliverable_checks",
    "run_experience_maintenance",
    "safe_id",
    "save_experience",
    "scan_for_credential_paths",
    "scan_for_network_commands",
    "select_deliverable",
    "select_experiences",
    "stage_key",
    "summarize_run_records",
    "supersede_experience",
    "synthetic_suite",
    "utc_now",
    "validate_component_id",
    "validate_proposal",
    "validator_summary",
]
