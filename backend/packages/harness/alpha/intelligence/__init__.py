"""Alpha's continual-intelligence layer: a composable index over existing subsystems.

This package is **not** a second evolution engine, a second experience store, a
second memory, a second model router, or a second logging framework. Alpha already
has strong implementations of all five (see
``ALPHA_CONTINUAL_INTELLIGENCE_AUDIT.md``), and duplicating any of them would be
the failure mode the design brief explicitly forbids.

What it adds is the layer those four subsystems do not have between them:

============================  =====================================================
Gap this fills                Where it plugs into existing code
============================  =====================================================
Experiences cannot be *sampled*   indexes :class:`alpha.learning.experience.models.ExperienceRecord`
No train/held-out *comparison*   extends :mod:`alpha.rsi.holdout`, :mod:`alpha.benchmarks`
No plasticity dial                 layers over :mod:`alpha.reasoning.budget`
No stable *learned* identity      extends :mod:`alpha.experts` (catalog stays authoritative)
No ranking/exploration            extends :mod:`alpha.capabilities.eligibility`
No disk->RAM->resident tier       new, but backed by :class:`alpha.experts.ExpertCatalog`
No staged sanitiser                reuses :data:`alpha.learning.experience.store.SECRET_PATTERNS`
No unified learning audit trail    parallels the three existing provenance ledgers
No single self-knowledge view      read-only projection over all of the above
============================  =====================================================

Three rules hold everywhere in this package
-------------------------------------------
1. **Default-off.** ``config.yaml -> intelligence:`` ships ``enabled: false`` with
   ``mode: OBSERVE_ONLY``. A fresh install observes and mutates nothing.
2. **Unmeasured is not zero, and unmeasured is not a pass.** Scores, rates and
   durations that were not measured are ``None`` with a reason, never ``0.0`` and
   never ``True``.
3. **Refusals are loud.** Every gate that declines returns a named reason and the
   stage or clause that declined, so "why didn't this learn?" is always
   answerable from the API.

Public names resolve lazily (:pep:`562`) through
:func:`alpha.memory._lazy_exports.install_lazy_exports`, matching the memory and
storekit packages' import-cycle hygiene, so ``import alpha.intelligence`` stays
cheap and side-effect free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

__all__ = [
    # config
    "IntelligenceConfig",
    "ExpertConfig",
    "PagingConfig",
    "PlasticityConfig",
    "ReplayConfig",
    "RegressionConfig",
    "intelligence_config",
    # models
    "ExpertIdentity",
    "ExpertLifecycleState",
    "ExpertMetrics",
    "ExpertRecord",
    "ExperienceTelemetry",
    "LearningEvent",
    "LearningMode",
    "ReplayStratum",
    # experience
    "ExperienceSanitizer",
    "SanitizerReport",
    "Stage",
    "Outcome",
    "default_sanitizer",
    # replay
    "ReplayItem",
    "ReplayReport",
    "ReplayReservoir",
    "replay_path",
    # evaluation
    "Category",
    "CaseResult",
    "Comparison",
    "Decision",
    "EvaluationOutcome",
    "EvaluationRun",
    "RegressionCase",
    "compare",
    "coverage_report",
    "default_regression_suite",
    "evaluate_gate",
    "register_regression_suite",
    # plasticity
    "Observation",
    "PlasticityAction",
    "PlasticityController",
    "PlasticityDecision",
    "PlasticityState",
    "PlasticityTier",
    # experts
    "ExpertFabric",
    "ExpertFabricError",
    "PruneDecision",
    "get_expert_fabric",
    # routing / scoring
    "CapabilityRouter",
    "RouteCandidate",
    "RouteDecision",
    "DefaultUtilityScorer",
    "ExpertScore",
    "UtilityScorer",
    # paging
    "EvictionPolicy",
    "ExpertCache",
    "ExpertStore",
    "LRUResidentSet",
    "PagingManager",
    "PagingSnapshot",
    "ResidentSet",
    # adaptive compute
    "ComputePlan",
    "ContinueDecision",
    "DifficultyBand",
    "DifficultyEstimate",
    "DifficultyEstimator",
    "DifficultySignals",
    "should_continue_reasoning",
    # audit + recovery
    "JournalEntry",
    "JournalVerification",
    "LearningJournal",
    "RetryBudgetExhausted",
    "SnapshotInfo",
    "SnapshotManager",
    "journal_path",
    # self-knowledge
    "SelfKnowledgeService",
    "get_self_knowledge",
    # Phase A - pathway evidence
    "Mechanism",
    "PathwayEvidence",
    "PathwayHypothesis",
    "PathwayProbe",
    "PathwayReport",
    "PathwayVerdict",
    "assert_pathway",
    "probe_registry",
    # Phase B - evaluator stability
    "NoiseFloor",
    "StabilityReport",
    "measure_noise_floor",
    "resolve_noise_floor",
    "stability_report",
    # Phase C - behavioural diversity
    "ActionSignature",
    "BehaviorTrace",
    "DiversityReport",
    "collapse_detected",
    "compare_diversity",
    "normalize_action",
    # Phase D - equal-budget protocol
    "BudgetComparison",
    "BudgetMismatchError",
    "BudgetUnit",
    "MatchedBudgetRun",
    "compare_matched",
    "is_comparable",
    # Phase E - cross-subsystem convergence
    "ConvergenceStatus",
    "ConvergedVerdict",
    "EvidenceLedger",
    "Subsystem",
    "SubsystemRecord",
    "SubsystemVerdict",
    "get_evidence_ledger",
    # Phase F - loop health
    "LoopHealthReport",
    "Regime",
    "SaturationDetector",
    "assess_loop_health",
    # Phase G - investigation selection
    "GapKind",
    "InvestigationProposal",
    "Selection",
    "proposal_is_admissible",
    "select_investigations",
]

_EXPORTS = {
    "Category": "regression",
    "CaseResult": "regression",
    "Comparison": "regression",
    "ComputePlan": "difficulty",
    "ContinueDecision": "difficulty",
    "Decision": "regression",
    "DefaultUtilityScorer": "scoring",
    "DifficultyBand": "difficulty",
    "DifficultyEstimate": "difficulty",
    "DifficultyEstimator": "difficulty",
    "DifficultySignals": "difficulty",
    "EvictionPolicy": "paging",
    "EvaluationOutcome": "regression",
    "EvaluationRun": "regression",
    "ExpertCache": "paging",
    "ExpertConfig": "config",
    "ExpertFabric": "expert_fabric",
    "ExpertFabricError": "expert_fabric",
    "ExpertFabricUnreadable": "expert_fabric",
    "ExpertIdentity": "models",
    "ExpertLifecycleState": "models",
    "ExpertMetrics": "models",
    "ExpertRecord": "models",
    "ExpertScore": "scoring",
    "ExpertStore": "paging",
    "ExperienceSanitizer": "sanitizer",
    "ExperienceTelemetry": "models",
    "IntelligenceConfig": "config",
    "JournalEntry": "journal",
    "JournalVerification": "journal",
    "LRUResidentSet": "paging",
    "LearningEvent": "models",
    "LearningJournal": "journal",
    "LearningMode": "models",
    "Observation": "plasticity",
    "Outcome": "sanitizer",
    "PagingConfig": "config",
    "PagingError": "paging",
    "PagingManager": "paging",
    "PagingSnapshot": "paging",
    "PlasticityAction": "plasticity",
    "PlasticityConfig": "config",
    "PlasticityController": "plasticity",
    "PlasticityDecision": "plasticity",
    "PlasticityState": "plasticity",
    "PlasticityTier": "plasticity",
    "PruneDecision": "expert_fabric",
    "RegressionCase": "regression",
    "RegressionConfig": "config",
    "ReplayConfig": "config",
    "ReplayItem": "replay",
    "ReplayReport": "replay",
    "ReplayReservoir": "replay",
    "ReplayStratum": "models",
    "ResidentSet": "paging",
    "RetryBudgetExhausted": "snapshots",
    "RouteCandidate": "router",
    "RouteDecision": "router",
    "SanitizerReport": "sanitizer",
    "SelfKnowledgeService": "self_knowledge",
    "SnapshotError": "snapshots",
    "SnapshotInfo": "snapshots",
    "SnapshotManager": "snapshots",
    "Stage": "sanitizer",
    "UtilityScorer": "scoring",
    "CapabilityRouter": "router",
    "compare": "regression",
    "coverage_report": "regression",
    "default_regression_suite": "regression",
    "default_sanitizer": "sanitizer",
    "evaluate_gate": "regression",
    "expert_fabric_path": "expert_fabric",
    "get_expert_fabric": "expert_fabric",
    "get_self_knowledge": "self_knowledge",
    "intelligence_config": "config",
    "journal_path": "journal",
    # Phase A
    "Mechanism": "pathway",
    "PathwayEvidence": "pathway",
    "PathwayHypothesis": "pathway",
    "PathwayProbe": "pathway",
    "PathwayReport": "pathway",
    "PathwayVerdict": "pathway",
    "assert_pathway": "pathway",
    "probe_registry": "pathway",
    # Phase B
    "NoiseFloor": "evaluator_stability",
    "StabilityReport": "evaluator_stability",
    "MIN_REPEATS_FOR_OBSERVATION": "evaluator_stability",
    "measure_noise_floor": "evaluator_stability",
    "resolve_noise_floor": "evaluator_stability",
    "stability_report": "evaluator_stability",
    # Phase C
    "ActionSignature": "diversity",
    "BehaviorTrace": "diversity",
    "DiversityReport": "diversity",
    "collapse_detected": "diversity",
    "compare_diversity": "diversity",
    "normalize_action": "diversity",
    # Phase D
    "BudgetComparison": "budget_protocol",
    "BudgetMismatchError": "budget_protocol",
    "BudgetUnit": "budget_protocol",
    "MatchedBudgetRun": "budget_protocol",
    "compare_matched": "budget_protocol",
    "is_comparable": "budget_protocol",
    # Phase E
    "ConvergenceStatus": "evidence_ledger",
    "ConvergedVerdict": "evidence_ledger",
    "EvidenceLedger": "evidence_ledger",
    "Subsystem": "evidence_ledger",
    "SubsystemRecord": "evidence_ledger",
    "SubsystemVerdict": "evidence_ledger",
    "get_evidence_ledger": "evidence_ledger",
    # Phase F
    "LoopHealthReport": "loop_health",
    "Regime": "loop_health",
    "SaturationDetector": "loop_health",
    "assess_loop_health": "loop_health",
    "MIN_OBSERVATIONS_FOR_REGIME": "loop_health",
    # Phase G
    "GapKind": "investigation",
    "InvestigationProposal": "investigation",
    "Selection": "investigation",
    "proposal_is_admissible": "investigation",
    "select_investigations": "investigation",
    "register_regression_suite": "regression",
    "replay_path": "replay",
    "should_continue_reasoning": "difficulty",
    "snapshot_dir": "snapshots",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    from alpha.intelligence.config import (
        ExpertConfig as ExpertConfig,
    )
    from alpha.intelligence.config import (
        IntelligenceConfig as IntelligenceConfig,
    )
    from alpha.intelligence.config import (
        PagingConfig as PagingConfig,
    )
    from alpha.intelligence.config import (
        PlasticityConfig as PlasticityConfig,
    )
    from alpha.intelligence.config import (
        RegressionConfig as RegressionConfig,
    )
    from alpha.intelligence.config import (
        ReplayConfig as ReplayConfig,
    )
    from alpha.intelligence.config import (
        intelligence_config as intelligence_config,
    )
    from alpha.intelligence.difficulty import (
        ComputePlan as ComputePlan,
    )
    from alpha.intelligence.difficulty import (
        ContinueDecision as ContinueDecision,
    )
    from alpha.intelligence.difficulty import (
        DifficultyBand as DifficultyBand,
    )
    from alpha.intelligence.difficulty import (
        DifficultyEstimate as DifficultyEstimate,
    )
    from alpha.intelligence.difficulty import (
        DifficultyEstimator as DifficultyEstimator,
    )
    from alpha.intelligence.difficulty import (
        DifficultySignals as DifficultySignals,
    )
    from alpha.intelligence.difficulty import (
        should_continue_reasoning as should_continue_reasoning,
    )
    from alpha.intelligence.expert_fabric import (
        ExpertFabric as ExpertFabric,
    )
    from alpha.intelligence.expert_fabric import (
        ExpertFabricError as ExpertFabricError,
    )
    from alpha.intelligence.expert_fabric import (
        ExpertFabricUnreadable as ExpertFabricUnreadable,
    )
    from alpha.intelligence.expert_fabric import (
        PruneDecision as PruneDecision,
    )
    from alpha.intelligence.expert_fabric import (
        expert_fabric_path as expert_fabric_path,
    )
    from alpha.intelligence.expert_fabric import (
        get_expert_fabric as get_expert_fabric,
    )
    from alpha.intelligence.journal import (
        JournalEntry as JournalEntry,
    )
    from alpha.intelligence.journal import (
        JournalVerification as JournalVerification,
    )
    from alpha.intelligence.journal import (
        LearningJournal as LearningJournal,
    )
    from alpha.intelligence.journal import (
        journal_path as journal_path,
    )
    from alpha.intelligence.models import (
        ExperienceTelemetry as ExperienceTelemetry,
    )
    from alpha.intelligence.models import (
        ExpertIdentity as ExpertIdentity,
    )
    from alpha.intelligence.models import (
        ExpertLifecycleState as ExpertLifecycleState,
    )
    from alpha.intelligence.models import (
        ExpertMetrics as ExpertMetrics,
    )
    from alpha.intelligence.models import (
        ExpertRecord as ExpertRecord,
    )
    from alpha.intelligence.models import (
        LearningEvent as LearningEvent,
    )
    from alpha.intelligence.models import (
        LearningMode as LearningMode,
    )
    from alpha.intelligence.models import (
        ReplayStratum as ReplayStratum,
    )
    from alpha.intelligence.paging import (
        EvictionPolicy as EvictionPolicy,
    )
    from alpha.intelligence.paging import (
        ExpertCache as ExpertCache,
    )
    from alpha.intelligence.paging import (
        ExpertStore as ExpertStore,
    )
    from alpha.intelligence.paging import (
        LRUResidentSet as LRUResidentSet,
    )
    from alpha.intelligence.paging import (
        PagingError as PagingError,
    )
    from alpha.intelligence.paging import (
        PagingManager as PagingManager,
    )
    from alpha.intelligence.paging import (
        PagingSnapshot as PagingSnapshot,
    )
    from alpha.intelligence.paging import (
        ResidentSet as ResidentSet,
    )
    from alpha.intelligence.plasticity import (
        Observation as Observation,
    )
    from alpha.intelligence.plasticity import (
        PlasticityAction as PlasticityAction,
    )
    from alpha.intelligence.plasticity import (
        PlasticityController as PlasticityController,
    )
    from alpha.intelligence.plasticity import (
        PlasticityDecision as PlasticityDecision,
    )
    from alpha.intelligence.plasticity import (
        PlasticityState as PlasticityState,
    )
    from alpha.intelligence.plasticity import (
        PlasticityTier as PlasticityTier,
    )
    from alpha.intelligence.regression import (
        CaseResult as CaseResult,
    )
    from alpha.intelligence.regression import (
        Category as Category,
    )
    from alpha.intelligence.regression import (
        Comparison as Comparison,
    )
    from alpha.intelligence.regression import (
        Decision as Decision,
    )
    from alpha.intelligence.regression import (
        EvaluationOutcome as EvaluationOutcome,
    )
    from alpha.intelligence.regression import (
        EvaluationRun as EvaluationRun,
    )
    from alpha.intelligence.regression import (
        RegressionCase as RegressionCase,
    )
    from alpha.intelligence.regression import (
        compare as compare,
    )
    from alpha.intelligence.regression import (
        coverage_report as coverage_report,
    )
    from alpha.intelligence.regression import (
        default_regression_suite as default_regression_suite,
    )
    from alpha.intelligence.regression import (
        evaluate_gate as evaluate_gate,
    )
    from alpha.intelligence.regression import (
        register_regression_suite as register_regression_suite,
    )
    from alpha.intelligence.replay import (
        ReplayItem as ReplayItem,
    )
    from alpha.intelligence.replay import (
        ReplayReport as ReplayReport,
    )
    from alpha.intelligence.replay import (
        ReplayReservoir as ReplayReservoir,
    )
    from alpha.intelligence.replay import (
        replay_path as replay_path,
    )
    from alpha.intelligence.router import (
        CapabilityRouter as CapabilityRouter,
    )
    from alpha.intelligence.router import (
        RouteCandidate as RouteCandidate,
    )
    from alpha.intelligence.router import (
        RouteDecision as RouteDecision,
    )
    from alpha.intelligence.sanitizer import (
        ExperienceSanitizer as ExperienceSanitizer,
    )
    from alpha.intelligence.sanitizer import (
        Outcome as Outcome,
    )
    from alpha.intelligence.sanitizer import (
        SanitizerReport as SanitizerReport,
    )
    from alpha.intelligence.sanitizer import (
        Stage as Stage,
    )
    from alpha.intelligence.sanitizer import (
        default_sanitizer as default_sanitizer,
    )
    from alpha.intelligence.scoring import (
        DefaultUtilityScorer as DefaultUtilityScorer,
    )
    from alpha.intelligence.scoring import (
        ExpertScore as ExpertScore,
    )
    from alpha.intelligence.scoring import (
        UtilityScorer as UtilityScorer,
    )
    from alpha.intelligence.self_knowledge import (
        SelfKnowledgeService as SelfKnowledgeService,
    )
    from alpha.intelligence.self_knowledge import (
        get_self_knowledge as get_self_knowledge,
    )
    from alpha.intelligence.snapshots import (
        RetryBudgetExhausted as RetryBudgetExhausted,
    )
    from alpha.intelligence.snapshots import (
        SnapshotError as SnapshotError,
    )
    from alpha.intelligence.snapshots import (
        SnapshotInfo as SnapshotInfo,
    )
    from alpha.intelligence.snapshots import (
        SnapshotManager as SnapshotManager,
    )
    from alpha.intelligence.snapshots import (
        snapshot_dir as snapshot_dir,
    )

install_lazy_exports(__name__, _EXPORTS)
