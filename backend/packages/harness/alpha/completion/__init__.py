"""Alpha Completion Engine — acceptance contracts, verifiable gates, honest done.

The subsystem answers one question that a chat transcript cannot: **did the
work actually succeed?** It does that with four pieces that must be used
together:

1. An **acceptance contract** fixed before work starts
   (``contract.build_contract`` + ``validate_contract``), validated
   deterministically so a model is never the sole validator of its own plan.
2. A **typed gate registry** (``gates``) — reviewed check implementations and
   allowlisted arguments, never free-form shell text from a model.
3. **Evidence with freshness** (``evidence``) — a structured record of what a
   check observed, bound to its definition and inputs, so a pass from 10:00
   cannot answer for code edited at 10:02.
4. A **finalization guard** (``finalization``) that is the only route to
   ``complete`` — and in ``observe`` mode computes the same decision without
   applying it.

Layering, bottom to top:

``storage`` -> ``repository`` -> (``models`` | ``state_machine`` | ``policy``)
-> ``contract`` / ``ownership`` / ``scheduler`` / ``dispatch`` / ``approvals``
-> ``gates`` -> ``evidence`` / ``verifier`` -> ``reconciliation`` ->
``finalization`` -> ``service`` (the facade) -> the Gateway router and the
model-facing ``alpha_completion`` tool.

Two vocabulary rules travel through every layer and are never relaxed:

* **Returned is not verified.** A worker's prose lands in
  ``DispatchRecord.state = returned``; only its own gates make it verified.
* **Not run is not passed.** ``CheckOutcome.NOT_RUN``, ``GateStatus.PENDING``
  and ``GateStatus.WAIVED`` can never satisfy a required gate.

Honesty constraints for callers: the ledger is JSON under an *advisory*
file lock — single-process and restart-recoverable, never cross-process
exactly-once; and a completed run is never described as a verified one.
"""

from __future__ import annotations

from .approvals import grant as grant_approval
from .approvals import is_valid as approval_is_valid
from .contract import RequirementInput, apply_validation, build_contract, validate_contract
from .errors import (
    ApprovalRefused,
    ApprovalRequired,
    BudgetExhausted,
    CheckUnavailable,
    CompletionError,
    ContractInvalid,
    IllegalTransition,
    NotFound,
    OwnershipConflict,
    PathEscape,
    ScopeStateError,
    StoreCorrupt,
    StoreUnreadable,
    StoreWriteFailed,
)
from .events import CompletionEvent, EventKind
from .evidence import current_passing, definition_digest, evaluate_freshness, record_evidence, refresh_freshness
from .finalization import collect_blockers, evaluate, finalize, progress_fingerprint
from .gates import CheckContext, CheckRegistry, CheckResult, default_check_registry, default_registry
from .models import (
    ApprovalPolicy,
    Blocker,
    CheckOutcome,
    CompletionScope,
    Contract,
    ContractFinding,
    DispatchRecord,
    DispatchState,
    Evidence,
    Expectation,
    FinalReport,
    Freshness,
    Gate,
    GateKind,
    GateStatus,
    OwnershipClaim,
    ReconciliationReport,
    Requirement,
    RequirementStatus,
    RiskTier,
    ScopeBudget,
    ScopeStatus,
    TaskNode,
    TaskState,
)
from .ownership import claim_paths, detect_conflicts, normalize_paths, release_claim, sweep_expired
from .policy import CompletionSettings, settings_from_config
from .reconciliation import reconcile, requirement_statuses
from .repository import CompletionRepository, scope_summary
from .scheduler import assert_acyclic, detect_cycle, promote_ready, scheduling_view, verdict_for
from .service import CompletionEngine, GateInput, ScopeCreateRequest, TaskInput
from .state_machine import (
    SCOPE_TRANSITIONS,
    TASK_TRANSITIONS,
    assert_scope_transition,
    assert_task_transition,
    can_transition_scope,
    can_transition_task,
    is_terminal_scope,
    is_terminal_task,
)
from .storage import CompletionStore
from .verifier import VerificationResult, run_gate, verify_task

__all__ = [
    "SCOPE_TRANSITIONS",
    "TASK_TRANSITIONS",
    "ApprovalPolicy",
    "ApprovalRefused",
    "ApprovalRequired",
    "Blocker",
    "BudgetExhausted",
    "CheckContext",
    "CheckOutcome",
    "CheckRegistry",
    "CheckResult",
    "CheckUnavailable",
    "CompletionEngine",
    "CompletionError",
    "CompletionEvent",
    "CompletionRepository",
    "CompletionScope",
    "CompletionSettings",
    "Contract",
    "ContractFinding",
    "ContractInvalid",
    "DispatchRecord",
    "DispatchState",
    "EventKind",
    "Evidence",
    "Expectation",
    "FinalReport",
    "Freshness",
    "Gate",
    "GateInput",
    "GateKind",
    "GateStatus",
    "IllegalTransition",
    "NotFound",
    "OwnershipClaim",
    "OwnershipConflict",
    "PathEscape",
    "ReconciliationReport",
    "Requirement",
    "RequirementInput",
    "RequirementStatus",
    "RiskTier",
    "ScopeBudget",
    "ScopeCreateRequest",
    "ScopeStateError",
    "ScopeStatus",
    "StoreCorrupt",
    "StoreUnreadable",
    "StoreWriteFailed",
    "TaskInput",
    "TaskNode",
    "TaskState",
    "VerificationResult",
    "apply_validation",
    "assert_acyclic",
    "assert_scope_transition",
    "assert_task_transition",
    "build_contract",
    "can_transition_scope",
    "can_transition_task",
    "claim_paths",
    "collect_blockers",
    "current_passing",
    "default_check_registry",
    "default_registry",
    "definition_digest",
    "detect_conflicts",
    "detect_cycle",
    "evaluate",
    "evaluate_freshness",
    "finalize",
    "grant_approval",
    "is_terminal_scope",
    "is_terminal_task",
    "normalize_paths",
    "promote_ready",
    "progress_fingerprint",
    "reconcile",
    "record_evidence",
    "refresh_freshness",
    "release_claim",
    "requirement_statuses",
    "run_gate",
    "scheduling_view",
    "scope_summary",
    "settings_from_config",
    "sweep_expired",
    "validate_contract",
    "verdict_for",
    "verify_task",
    "approval_is_valid",
    "CompletionStore",
]
