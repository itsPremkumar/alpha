"""Alpha reasoning package: existing governor/search plus the additive plane.

Public attributes resolve lazily (:pep:`562`) through the shared
``install_lazy_exports`` helper.  Importing :mod:`alpha.reasoning` therefore does
not load pydantic schemas, the governor, the model factory, the config layer, or
any orchestration runtime.

``ReasoningConfig`` at the package root remains the legacy governor dataclass
for backward compatibility.  The default-off reasoning-plane configuration is
``alpha.reasoning.config.ReasoningConfig`` and is exposed here as
``ReasoningPlaneConfig``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "AtomicThought": "models",
    "AtomDAG": "atoms",
    "AtomicState": "atoms",
    "BudgetLimits": "budget",
    "BudgetSnapshot": "models",
    "ContradictionRecord": "models",
    "DecisionRecord": "models",
    "EvidenceRecord": "models",
    "HypothesisRecord": "models",
    "LoopDecision": "loopguard",
    "ObservationRecord": "models",
    "PrefixSearchAdapter": "ttcs",
    "ProcessVerifier": "ttcs",
    "ReasoningBudgetLedger": "budget",
    "ReasoningConfig": "governor",
    "ReasoningEvent": "events",
    "ReasoningGovernor": "governor",
    "ReasoningPlaneConfig": "config",
    "ReasoningPolicy": "policy",
    "ReasoningState": "models",
    "ReasoningSummary": "summary",
    "ReflectionRecord": "models",
    "SafeReasoningRecord": "summary",
    "TTCSAllocator": "ttcs",
    "TTCSRequest": "ttcs",
    "TaskSignals": "policy",
    "UncertaintyDecision": "uncertainty",
    "UncertaintyEstimate": "models",
    "UncertaintyRecord": "models",
    "VerificationRecord": "models",
    "VerifierCapability": "ttcs",
    "account_token_usage": "atoms",
    "assert_no_private_reasoning": "summary",
    "classify_task": "policy",
    "contract": "atoms",
    "decompose": "atoms",
    "decide_uncertainty": "uncertainty",
    "equivalent_key": "atoms",
    "evaluate_loopguard": "loopguard",
    "expand": "atoms",
    "frontier": "atoms",
    "generate_reasoning_summary": "summary",
    "get_reasoning_governor": "governor",
    "is_acyclic": "atoms",
    "is_reasoning_enabled": "config",
    "load_reasoning_config": "config",
    "reset_global_governor": "governor",
    "select_policy": "policy",
    "validated_lesson_handoffs": "summary",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports: the runtime surface is installed lazily
    # above, so this block exists for type checkers and IDEs only.
    from alpha.reasoning.atoms import AtomDAG as AtomDAG
    from alpha.reasoning.atoms import AtomicState as AtomicState
    from alpha.reasoning.atoms import account_token_usage as account_token_usage
    from alpha.reasoning.atoms import contract as contract
    from alpha.reasoning.atoms import decompose as decompose
    from alpha.reasoning.atoms import equivalent_key as equivalent_key
    from alpha.reasoning.atoms import expand as expand
    from alpha.reasoning.atoms import frontier as frontier
    from alpha.reasoning.atoms import is_acyclic as is_acyclic
    from alpha.reasoning.budget import BudgetLimits as BudgetLimits
    from alpha.reasoning.budget import ReasoningBudgetLedger as ReasoningBudgetLedger
    from alpha.reasoning.config import ReasoningPlaneConfig as ReasoningPlaneConfig
    from alpha.reasoning.config import is_reasoning_enabled as is_reasoning_enabled
    from alpha.reasoning.config import load_reasoning_config as load_reasoning_config
    from alpha.reasoning.events import ReasoningEvent as ReasoningEvent
    from alpha.reasoning.governor import ReasoningConfig as ReasoningConfig
    from alpha.reasoning.governor import ReasoningGovernor as ReasoningGovernor
    from alpha.reasoning.governor import get_reasoning_governor as get_reasoning_governor
    from alpha.reasoning.governor import reset_global_governor as reset_global_governor
    from alpha.reasoning.loopguard import LoopDecision as LoopDecision
    from alpha.reasoning.loopguard import evaluate_loopguard as evaluate_loopguard
    from alpha.reasoning.models import AtomicThought as AtomicThought
    from alpha.reasoning.models import BudgetSnapshot as BudgetSnapshot
    from alpha.reasoning.models import ContradictionRecord as ContradictionRecord
    from alpha.reasoning.models import DecisionRecord as DecisionRecord
    from alpha.reasoning.models import EvidenceRecord as EvidenceRecord
    from alpha.reasoning.models import HypothesisRecord as HypothesisRecord
    from alpha.reasoning.models import ObservationRecord as ObservationRecord
    from alpha.reasoning.models import ReasoningState as ReasoningState
    from alpha.reasoning.models import ReflectionRecord as ReflectionRecord
    from alpha.reasoning.models import UncertaintyEstimate as UncertaintyEstimate
    from alpha.reasoning.models import UncertaintyRecord as UncertaintyRecord
    from alpha.reasoning.models import VerificationRecord as VerificationRecord
    from alpha.reasoning.policy import ReasoningPolicy as ReasoningPolicy
    from alpha.reasoning.policy import TaskSignals as TaskSignals
    from alpha.reasoning.policy import classify_task as classify_task
    from alpha.reasoning.policy import select_policy as select_policy
    from alpha.reasoning.summary import ReasoningSummary as ReasoningSummary
    from alpha.reasoning.summary import SafeReasoningRecord as SafeReasoningRecord
    from alpha.reasoning.summary import assert_no_private_reasoning as assert_no_private_reasoning
    from alpha.reasoning.summary import generate_reasoning_summary as generate_reasoning_summary
    from alpha.reasoning.summary import validated_lesson_handoffs as validated_lesson_handoffs
    from alpha.reasoning.ttcs import PrefixSearchAdapter as PrefixSearchAdapter
    from alpha.reasoning.ttcs import ProcessVerifier as ProcessVerifier
    from alpha.reasoning.ttcs import TTCSAllocator as TTCSAllocator
    from alpha.reasoning.ttcs import TTCSRequest as TTCSRequest
    from alpha.reasoning.ttcs import VerifierCapability as VerifierCapability
    from alpha.reasoning.uncertainty import UncertaintyDecision as UncertaintyDecision
    from alpha.reasoning.uncertainty import decide_uncertainty as decide_uncertainty

install_lazy_exports(__name__, _EXPORTS)
