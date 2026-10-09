"""Named capability, evaluator and approval profiles.

A *profile* is a capability grant, not a label. The distinction matters because
a prompt label can be written by anyone: ``"you are running under the
repo_repair profile"`` is a sentence, while :class:`CapabilityProfile` is a
server-side object that a run is handed and cannot edit.

Default-deny is the shape of every profile here: three path lists, one action
allow-list and one network policy. Anything not named is denied, and an empty
list denies everything rather than everything being named.

The five profiles are the ones the plan calls for. ``alpha_self_update`` is
present and **disabled**, because a capability that exists but cannot be
enabled is more honest than one that is silently reachable.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .contracts import ActionType, BudgetProfile, CapabilityProfile, EvaluatorProfile, PromotionLevel

__all__ = [
    "CAPABILITY_PROFILES",
    "BUDGET_PROFILES",
    "EVALUATOR_PROFILES",
    "APPROVAL_POLICIES",
    "ApprovalPolicy",
    "UnknownProfile",
    "approval_policy",
    "capability_profile",
    "budget_profile",
    "evaluator_profile",
]


class UnknownProfile(KeyError):
    """A profile identifier that does not resolve. Never a silent default."""

    def __init__(self, kind: str, profile_id: str) -> None:
        self.kind = kind
        self.profile_id = profile_id
        super().__init__(f"unknown {kind} profile {profile_id!r}; profiles are resolved server-side and never default silently")


class ApprovalPolicy(BaseModel):
    """What requires a human before it may happen.

    ``tiers`` maps a risk class to a requirement, so an operator can tighten one
    class without re-declaring the others. ``forbidden`` is the absolute floor:
    a destructive action is refused whatever the tier says, because no tier
    table should be able to authorise deleting user data.
    """

    model_config = {"extra": "forbid"}

    policy_id: str
    default_deny: bool = True
    require_approval_for: dict[str, Literal["none", "notify", "require"]] = Field(default_factory=dict)
    forbidden_actions: list[str] = Field(default_factory=list)
    #: Promotion at or above this level always needs an explicit approval.
    promotion_approval_level: PromotionLevel = PromotionLevel.CANDIDATE_ARTIFACT

    def requirement_for(self, risk_class: str) -> Literal["none", "notify", "require"]:
        return self.require_approval_for.get(str(risk_class), "require" if self.default_deny else "none")


# ---------------------------------------------------------------------------
# capability profiles
# ---------------------------------------------------------------------------

CAPABILITY_PROFILES: dict[str, CapabilityProfile] = {
    "research_readonly": CapabilityProfile(
        profile_id="research_readonly",
        description="Reads and searches only. No local mutation of any kind.",
        read_paths=["**"],
        write_paths=[],
        protected_paths=["**"],
        allowed_action_types=[
            ActionType.READ_FILE,
            ActionType.LIST_DIRECTORY,
            ActionType.SEARCH_REPOSITORY,
            ActionType.GIT_STATUS,
            ActionType.GIT_DIFF,
        ],
        network="denied",
        max_promotion_level=PromotionLevel.REPORT_ONLY,
    ),
    "repo_inspect": CapabilityProfile(
        profile_id="repo_inspect",
        description="Repository read plus bounded test discovery. No writes.",
        read_paths=["**"],
        write_paths=[],
        protected_paths=["**"],
        allowed_action_types=[
            ActionType.READ_FILE,
            ActionType.LIST_DIRECTORY,
            ActionType.SEARCH_REPOSITORY,
            ActionType.GIT_STATUS,
            ActionType.GIT_DIFF,
            ActionType.CREATE_CANDIDATE_WORKTREE,
        ],
        network="denied",
        max_promotion_level=PromotionLevel.REPORT_ONLY,
    ),
    "repo_repair": CapabilityProfile(
        profile_id="repo_repair",
        description="Reads, writes inside a candidate worktree only, runs approved checks.",
        read_paths=["**"],
        # A candidate may only write inside its own worktree subtree. Everything
        # else is denied, including the evaluator and the policy themselves.
        write_paths=["candidates/**"],
        protected_paths=[
            "alpha/avo/**",
            "alpha/testing/**",
            "alpha/rsi/**",
            "alpha/evolution/evidence/**",
            "alpha/config/**",
            ".github/**",
            "tests/**",
            "contracts/**",
            "config.yaml",
            "extensions_config.json",
        ],
        allowed_action_types=[
            ActionType.READ_FILE,
            ActionType.LIST_DIRECTORY,
            ActionType.SEARCH_REPOSITORY,
            ActionType.GIT_STATUS,
            ActionType.GIT_DIFF,
            ActionType.CREATE_CANDIDATE_WORKTREE,
            ActionType.WRITE_CANDIDATE_FILE,
            ActionType.RUN_TARGETED_CHECK,
            ActionType.RUN_REGRESSION_SUITE,
        ],
        network="denied",
        allow_auto_merge=False,
        allow_auto_deploy=False,
        allow_policy_mutation=False,
        allow_evaluator_mutation=False,
        max_promotion_level=PromotionLevel.CANDIDATE_ARTIFACT,
    ),
    "external_write_approval": CapabilityProfile(
        profile_id="external_write_approval",
        description="repo_repair plus externally visible actions, every one approval-gated.",
        read_paths=["**"],
        write_paths=["candidates/**"],
        protected_paths=[
            "alpha/avo/**",
            "alpha/testing/**",
            "alpha/rsi/**",
            "alpha/evolution/evidence/**",
            "alpha/config/**",
            ".github/**",
            "tests/**",
            "contracts/**",
        ],
        allowed_action_types=[
            ActionType.READ_FILE,
            ActionType.LIST_DIRECTORY,
            ActionType.SEARCH_REPOSITORY,
            ActionType.GIT_STATUS,
            ActionType.GIT_DIFF,
            ActionType.CREATE_CANDIDATE_WORKTREE,
            ActionType.WRITE_CANDIDATE_FILE,
            ActionType.RUN_TARGETED_CHECK,
            ActionType.RUN_REGRESSION_SUITE,
            ActionType.REQUEST_PROMOTION,
        ],
        network="allowlist",
        max_promotion_level=PromotionLevel.DRAFT_PR,
    ),
    "alpha_self_update": CapabilityProfile(
        profile_id="self_update",
        description="Separate higher-risk workflow for Alpha modifying its own source. Disabled by default.",
        read_paths=["**"],
        write_paths=["candidates/**"],
        protected_paths=["alpha/avo/**", "alpha/testing/**"],
        allowed_action_types=[],
        network="denied",
        # Present so its absence is not mistaken for an oversight; disabled so
        # it cannot be reached by naming it.
        enabled=False,
        max_promotion_level=PromotionLevel.REPORT_ONLY,
    ),
}


# ---------------------------------------------------------------------------
# budget profiles
# ---------------------------------------------------------------------------

BUDGET_PROFILES: dict[str, BudgetProfile] = {
    "local_low_resource": BudgetProfile(
        profile_id="local_low_resource",
        max_experiments=4,
        max_actions=24,
        max_actions_per_experiment=10,
        max_branches=3,
        max_wall_time_seconds=600.0,
        max_cost_usd=None,
        max_parallel_workers=1,
        max_retries_per_action=2,
        max_same_failure_signature=2,
        # A workstation deployment on a free provider usually cannot derive an
        # exact cost. The ceiling is then unenforceable and says so, while the
        # action, experiment and wall-time ceilings still bind.
        cost_is_enforceable=False,
    ),
    "free_provider": BudgetProfile(
        profile_id="free_provider",
        max_experiments=3,
        max_actions=16,
        max_actions_per_experiment=6,
        max_branches=2,
        max_wall_time_seconds=300.0,
        max_cost_usd=None,
        max_parallel_workers=1,
        max_retries_per_action=1,
        max_same_failure_signature=2,
        cost_is_enforceable=False,
    ),
    "paid_bounded": BudgetProfile(
        profile_id="paid_bounded",
        max_experiments=6,
        max_actions=48,
        max_actions_per_experiment=16,
        max_branches=4,
        max_wall_time_seconds=1800.0,
        max_cost_usd=0.50,
        max_parallel_workers=2,
        max_retries_per_action=2,
        max_same_failure_signature=2,
        cost_is_enforceable=True,
    ),
    "ci_verified": BudgetProfile(
        profile_id="ci_verified",
        max_experiments=8,
        max_actions=64,
        max_actions_per_experiment=16,
        max_branches=4,
        max_wall_time_seconds=3600.0,
        max_cost_usd=None,
        max_parallel_workers=1,
        max_retries_per_action=0,
        max_same_failure_signature=1,
        cost_is_enforceable=False,
    ),
}


# ---------------------------------------------------------------------------
# evaluator profiles
# ---------------------------------------------------------------------------

EVALUATOR_PROFILES: dict[str, EvaluatorProfile] = {
    "code_repair": EvaluatorProfile(
        profile_id="code_repair",
        # Ordered by cost, cheapest and most decisive first. A candidate that
        # fails patch sanity is never run through the full suite.
        tiers=[
            "patch_sanity",
            "static_checks",
            "targeted_regression",
            "relevant_tests",
            "security_checks",
            "behaviour_contract",
            "cost_replay_gate",
        ],
        protected_prefixes=["alpha/avo/", "alpha/testing/", "alpha/rsi/", ".github/", "tests/"],
        require_baseline=True,
        allow_skip=["full_suite", "runtime_smoke", "independent_review"],
    ),
    "code_repair_deep": EvaluatorProfile(
        profile_id="code_repair_deep",
        tiers=[
            "patch_sanity",
            "static_checks",
            "targeted_regression",
            "relevant_tests",
            "full_suite",
            "security_checks",
            "behaviour_contract",
            "runtime_smoke",
            "independent_review",
            "cost_replay_gate",
        ],
        protected_prefixes=["alpha/avo/", "alpha/testing/", "alpha/rsi/", ".github/", "tests/"],
        require_baseline=True,
        allow_skip=[],
    ),
    "research_only": EvaluatorProfile(
        profile_id="research_only",
        tiers=["patch_sanity", "behaviour_contract"],
        protected_prefixes=["**"],
        require_baseline=False,
        allow_skip=["static_checks", "targeted_regression", "relevant_tests", "security_checks"],
    ),
}


# ---------------------------------------------------------------------------
# approval policies
# ---------------------------------------------------------------------------

APPROVAL_POLICIES: dict[str, ApprovalPolicy] = {
    "default_deny": ApprovalPolicy(
        policy_id="default_deny",
        default_deny=True,
        require_approval_for={
            "low": "none",
            "medium": "notify",
            "high": "require",
            "destructive": "require",
        },
        # Refused outright at every tier. No tier table may authorise these.
        forbidden_actions=["delete_user_data", "rotate_secret", "modify_policy_code", "modify_evaluator_code"],
        promotion_approval_level=PromotionLevel.CANDIDATE_ARTIFACT,
    ),
    "strict": ApprovalPolicy(
        policy_id="strict",
        default_deny=True,
        require_approval_for={
            "low": "notify",
            "medium": "require",
            "high": "require",
            "destructive": "require",
        },
        forbidden_actions=[
            "delete_user_data",
            "rotate_secret",
            "modify_policy_code",
            "modify_evaluator_code",
            "external_publish",
            "deploy",
        ],
        promotion_approval_level=PromotionLevel.REPORT_ONLY,
    ),
}


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------


def _resolve(table: dict[str, Any], kind: str, profile_id: str) -> Any:
    if profile_id not in table:
        raise UnknownProfile(kind, profile_id)
    return table[profile_id]


def capability_profile(profile_id: str) -> CapabilityProfile:
    return _resolve(CAPABILITY_PROFILES, "capability", profile_id)


def budget_profile(profile_id: str) -> BudgetProfile:
    return _resolve(BUDGET_PROFILES, "budget", profile_id)


def evaluator_profile(profile_id: str) -> EvaluatorProfile:
    return _resolve(EVALUATOR_PROFILES, "evaluator", profile_id)


def approval_policy(policy_id: str) -> ApprovalPolicy:
    return _resolve(APPROVAL_POLICIES, "approval", policy_id)


#: Type alias re-exported so ``from alpha.avo.profiles import ApprovalPolicyId``
#: reads naturally at the call site without importing the contracts module.
ApprovalPolicyId = str
