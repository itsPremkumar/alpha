"""Budgeted test-time-compute allocation across three researched regimes.

Design inputs:

* *Test-Time Scaling in Reasoning LLMs* (arXiv:2608.04001v2) distinguishes
  single-trajectory sequential scaling, leaf-level scaling with a terminal
  reduction, and prefix-level search over partial states.  This module keeps
  those protocols and their compute accounting distinct.
* *Step-level Verifier-guided Hybrid Test-Time Scaling* (arXiv:2507.15512v3)
  motivates a real step-level process verifier for search/refinement.
* *T1: Tool-integrated Verification* (arXiv:2504.04718v2) shows that cheap
  small-model verifiers fail on memorization-heavy checks.  Prefix-level search
  therefore refuses to relabel a self-rating as a process verifier.

This module allocates budget and validates verifier provenance.  It does not
implement Best-of-N, self-consistency, MBR, MCTS, or any other search engine.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alpha.reasoning.budget import BudgetAdmission, ReasoningBudgetLedger
from alpha.reasoning.models import BudgetDimension, ClampedModel, ReasoningState, VerificationRecord

__all__ = [
    "LeafStrategy",
    "PrefixSearchAdapter",
    "ProcessVerifier",
    "ProcessVerifierKind",
    "TTCSAllocation",
    "TTCSAllocator",
    "TTCSRefused",
    "TTCSRegime",
    "TTCSRequest",
    "TTCSSettings",
    "VerifierCapability",
]


class TTCSRegime(StrEnum):
    SINGLE_TRAJECTORY = "single_trajectory"
    LEAF_LEVEL = "leaf_level"
    PREFIX_LEVEL = "prefix_level"


class LeafStrategy(StrEnum):
    BEST_OF_N = "best_of_n"
    SELF_CONSISTENCY = "self_consistency"
    MINIMUM_BAYES_RISK = "minimum_bayes_risk"


class ProcessVerifierKind(StrEnum):
    DETERMINISTIC = "deterministic"
    TRAINED_PROCESS_REWARD_MODEL = "trained_process_reward_model"
    TOOL_INTEGRATED = "tool_integrated"
    HUMAN = "human"
    MODEL_CRITIC = "model_critic"
    LLM_SELF_RATING = "llm_self_rating"


_PREFIX_VERIFIER_KINDS = {
    ProcessVerifierKind.DETERMINISTIC,
    ProcessVerifierKind.TRAINED_PROCESS_REWARD_MODEL,
    ProcessVerifierKind.TOOL_INTEGRATED,
    ProcessVerifierKind.HUMAN,
}


class VerifierCapability(BaseModel):
    """Provenance contract an injected step verifier must satisfy."""

    name: str = Field(min_length=1, max_length=128)
    kind: ProcessVerifierKind
    is_step_level: bool = False
    is_independent: bool = False
    uses_external_tools: bool = False
    checks_invariants: bool = False
    calibration_ref: str | None = Field(default=None, max_length=512)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def _validate_provenance(self) -> Self:
        if self.kind is ProcessVerifierKind.TOOL_INTEGRATED and not (self.uses_external_tools or self.checks_invariants):
            raise ValueError("tool-integrated verifier must declare external tools or invariant checks")
        if self.kind is ProcessVerifierKind.TRAINED_PROCESS_REWARD_MODEL and not (self.calibration_ref or "").strip():
            raise ValueError("trained process verifier must name its calibration/evaluation reference")
        return self


class ProcessVerifier(Protocol):
    """Narrow provider-neutral interface for a real step-level verifier."""

    def capabilities(self) -> VerifierCapability: ...

    async def verify_step(
        self,
        state: ReasoningState,
        candidate_step: str,
    ) -> VerificationRecord: ...


class PrefixSearchAdapter(Protocol):
    """Host-supplied search implementation.

    Alpha's existing introspective tree search or dynamic workflow runtime may
    implement this.  This package intentionally provides no default engine.
    """

    async def search(
        self,
        state: ReasoningState,
        allocation: TTCSAllocation,
        verifier: ProcessVerifier,
    ) -> ReasoningState: ...


class TTCSSettings(ClampedModel):
    regime: TTCSRegime = TTCSRegime.SINGLE_TRAJECTORY
    default_samples: int = Field(default=1)
    max_samples: int = Field(default=8)
    max_tokens_per_sample: int = Field(default=4000)
    require_independent_prefix_verifier: bool = True

    @model_validator(mode="after")
    def _clamp_and_order(self) -> Self:
        self.clamp_field("default_samples", 1, 1000, integer=True)
        self.clamp_field("max_samples", 1, 1000, integer=True)
        self.clamp_field("max_tokens_per_sample", 0, 10_000_000, integer=True)
        if self.max_samples < self.default_samples:
            raise ValueError("max_samples must be at least default_samples")
        return self


class TTCSRequest(ClampedModel):
    regime: TTCSRegime = TTCSRegime.SINGLE_TRAJECTORY
    requested_samples: int = Field(default=1)
    token_estimate_per_sample: int = Field(default=0)
    leaf_strategy: LeafStrategy | None = None
    branch_count: int = Field(default=0)
    reason: str = Field(default="unspecified test-time allocation", min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _clamp_values(self) -> Self:
        self.clamp_field("requested_samples", 1, 1000, integer=True)
        self.clamp_field("token_estimate_per_sample", 0, 10_000_000, integer=True)
        self.clamp_field("branch_count", 0, 1000, integer=True)
        return self


class TTCSAllocation(BaseModel):
    admitted: bool
    regime: TTCSRegime
    samples: int
    model_calls: int
    token_reservation: int
    branches: int
    leaf_strategy: LeafStrategy | None
    verifier: VerifierCapability | None
    reason: str
    budget_admission: BudgetAdmission | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class TTCSRefused(RuntimeError):
    """Prefix-level search was requested without an honest verifier."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TTCSAllocator:
    """Allocate reserved compute without executing a scaling algorithm."""

    def __init__(self, settings: TTCSSettings = TTCSSettings()) -> None:
        self.settings = settings

    @classmethod
    def from_config(cls, config: object) -> TTCSAllocator:
        from alpha.reasoning.config import ReasoningConfig

        if not isinstance(config, ReasoningConfig):
            raise TypeError("config must be alpha.reasoning.config.ReasoningConfig")
        return cls(config.ttcs)

    def default_request(self) -> TTCSRequest:
        """Build the configured single/leaf/prefix request shell.

        The caller still supplies a real verifier for prefix-level search.
        """

        return TTCSRequest(
            regime=self.settings.regime,
            requested_samples=self.settings.default_samples,
            token_estimate_per_sample=0,
            leaf_strategy=LeafStrategy.SELF_CONSISTENCY if self.settings.regime is TTCSRegime.LEAF_LEVEL else None,
            branch_count=1 if self.settings.regime is TTCSRegime.PREFIX_LEVEL else 0,
            reason="configured default test-time allocation",
        )

    def _validate_verifier(
        self,
        request: TTCSRequest,
        verifier: VerifierCapability | None,
    ) -> None:
        if request.regime is not TTCSRegime.PREFIX_LEVEL:
            return
        if verifier is None:
            raise TTCSRefused("prefix-level search requires an injected step-level process verifier")
        if not verifier.is_step_level:
            raise TTCSRefused("prefix-level search verifier is not step level")
        if verifier.kind not in _PREFIX_VERIFIER_KINDS:
            raise TTCSRefused(f"prefix-level search refuses {verifier.kind.value}; a self-rating or generic model critic is not a process verifier")
        if self.settings.require_independent_prefix_verifier and not verifier.is_independent:
            raise TTCSRefused("prefix-level search requires an independent process verifier")
        if request.branch_count < 1:
            raise TTCSRefused("prefix-level search requires at least one reserved branch")

    def _validate_leaf_strategy(
        self,
        request: TTCSRequest,
        verifier: VerifierCapability | None,
    ) -> None:
        if request.regime is not TTCSRegime.LEAF_LEVEL:
            return
        if request.leaf_strategy is None:
            raise TTCSRefused("leaf-level allocation requires an explicit Best-of-N, self-consistency, or MBR reduction")
        if request.leaf_strategy is LeafStrategy.BEST_OF_N:
            if verifier is None:
                raise TTCSRefused("Best-of-N requires an injected candidate verifier")
            if verifier.kind is ProcessVerifierKind.LLM_SELF_RATING:
                raise TTCSRefused("Best-of-N refuses a generator self-rating as its candidate verifier")

    def allocate(
        self,
        request: TTCSRequest,
        budget: ReasoningBudgetLedger,
        *,
        verifier: VerifierCapability | None = None,
    ) -> TTCSAllocation:
        """Reserve shared budget atomically for one test-time regime."""

        self._validate_verifier(request, verifier)
        self._validate_leaf_strategy(request, verifier)
        if request.requested_samples > self.settings.max_samples:
            return TTCSAllocation(
                admitted=False,
                regime=request.regime,
                samples=request.requested_samples,
                model_calls=0,
                token_reservation=0,
                branches=request.branch_count,
                leaf_strategy=request.leaf_strategy,
                verifier=verifier,
                reason=f"requested {request.requested_samples} samples exceeds configured max_samples={self.settings.max_samples}",
            )
        token_estimate = min(request.token_estimate_per_sample, self.settings.max_tokens_per_sample)
        samples = 1 if request.regime is TTCSRegime.SINGLE_TRAJECTORY else request.requested_samples
        model_calls = samples
        token_reservation = samples * token_estimate
        resources = {
            BudgetDimension.MODEL_CALLS: float(model_calls),
            BudgetDimension.TOKENS: float(token_reservation),
        }
        if request.branch_count:
            resources[BudgetDimension.BRANCHES] = float(request.branch_count)
        admission = budget.consume_many(
            resources,
            reason_code=f"ttcs.{request.regime.value}",
            metadata={"samples": str(samples), "reduction": request.leaf_strategy.value if request.leaf_strategy else "none"},
        )
        return TTCSAllocation(
            admitted=admission.allowed,
            regime=request.regime,
            samples=samples,
            model_calls=model_calls,
            token_reservation=token_reservation,
            branches=request.branch_count,
            leaf_strategy=request.leaf_strategy,
            verifier=verifier,
            reason=admission.reason if not admission.allowed else f"reserved {request.regime.value} compute: {request.reason}",
            budget_admission=admission,
        )
