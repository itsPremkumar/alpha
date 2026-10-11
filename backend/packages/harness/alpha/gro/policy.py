"""GRO policy: classify the task, then decide single vs. group vs. approval.

The decision is deterministic and explainable (plan section 9, phase A/B):
``classify_task_type`` and ``classify_risk_tier`` read the normalized goal and
acceptance criteria with bounded keyword rules that disclose which rule
matched; ``decide_mode`` maps tier -> group size through the operator's
configured tier table. Risk classification is computed from what the task asks
for — a candidate never classifies itself, so describing a task carefully
cannot lower its tier.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from alpha.gro.config import GroConfig
from alpha.gro.models import RiskTier, RunMode, TaskType

_CODING_MARKERS = ("bug", "fix", "patch", "error", "traceback", "test", "refactor", "compile", "type error", "stack trace", "regression", "implement", "code", "function", "module", "exception")
_RESEARCH_MARKERS = ("research", "compare", "sources", "survey", "evidence", "investigate", "find out", "latest", "who ", "what is", "why does", " summarize", "literature", "papers")
_PLANNING_MARKERS = ("plan", "schedule", "milestone", "roadmap", "steps", "strategy", "organize", "migrate", "rollout", "deprecate", "dependency", "timeline")

#: Tasks that can mutate the world outside Alpha (plan section 13.3). Matching
#: any marker raises the risk tier and, with approval required, resolves the
#: run to human approval before any candidate executes.
_EXTERNAL_EFFECT_MARKERS = (
    "deploy",
    "publish",
    "post ",
    "tweet",
    "send email",
    "email ",
    "reply ",
    "message the",
    "purchase",
    "buy ",
    "pay ",
    "order ",
    "delete ",
    "drop table",
    "remove user",
    "revoke",
    "rotate key",
    "transfer",
    "ship to prod",
    "push to main",
    "merge to main",
    "release ",
)
_DESTRUCTIVE_MARKERS = ("delete", "destroy", "purge", "wipe", "drop ", "erase", "terminate", "kill process", "format ")

_COMPLEX_GOAL_MARKERS = ("system", "platform", "end-to-end", "end to end", "integration", "across the", "multi-", "pipeline", "architecture", "production", "migrate", "redesign")
_UNCERTAIN_GOAL_MARKERS = ("investigate", "explore", "evaluate", "compare", "assess", "diagnose", "best approach", "unclear", "maybe", "options")


def _contains(haystack: str, needles: Sequence[str]) -> list[str]:
    return [needle.strip() for needle in needles if needle in haystack]


def classify_task_type(goal: str, acceptance_criteria: Sequence[str] = ()) -> TaskType:
    """Classify the normalized goal; every rule that matched is disclosed via
    ``classify_task_type_with_reason`` when the caller needs it."""

    text = _corpus(goal, acceptance_criteria)
    coding = len(_contains(text, _CODING_MARKERS))
    research = len(_contains(text, _RESEARCH_MARKERS))
    planning = len(_contains(text, _PLANNING_MARKERS))
    best = max(coding, research, planning)
    if best == 0:
        return TaskType.GENERAL
    # Deterministic order on ties: coding > research > planning > general.
    if coding == best:
        return TaskType.CODING
    if research == best:
        return TaskType.RESEARCH
    return TaskType.PLANNING


def classify_risk_tier(goal: str, acceptance_criteria: Sequence[str] = ()) -> tuple[RiskTier, list[str]]:
    """Compute the risk tier from what the task asks for.

    Returns the tier plus the matched reason codes so the decision is
    explainable rather than asserted.
    """

    text = _corpus(goal, acceptance_criteria)
    reasons: list[str] = []
    criteria_count = len([c for c in acceptance_criteria if str(c).strip()])
    tier = RiskTier.ORDINARY
    if criteria_count == 0 and len(goal.split()) <= 8:
        tier = RiskTier.TRIVIAL
        reasons.append("short_goal_few_criteria")
    if criteria_count >= 3 or _contains(text, _COMPLEX_GOAL_MARKERS):
        tier = RiskTier.COMPLEX
        reasons.append("complexity_markers" if _contains(text, _COMPLEX_GOAL_MARKERS) else "criteria_count>=3")
    elif tier is not RiskTier.COMPLEX and (criteria_count == 2 or _contains(text, _UNCERTAIN_GOAL_MARKERS)):
        tier = RiskTier.UNCERTAIN
        reasons.append("uncertainty_markers" if _contains(text, _UNCERTAIN_GOAL_MARKERS) else "criteria_count==2")
    external = _contains(text, _EXTERNAL_EFFECT_MARKERS)
    destructive = _contains(text, _DESTRUCTIVE_MARKERS)
    if external or destructive:
        # An externally visible or destructive task is never below UNCERTAIN.
        if tier.rank < RiskTier.UNCERTAIN.rank:
            tier = RiskTier.UNCERTAIN
        reasons.append(f"external_effect_markers:{','.join((external + destructive)[:4])}")
    return tier, reasons


def has_external_side_effects(goal: str, acceptance_criteria: Sequence[str] = ()) -> bool:
    """Whether the task can mutate state outside Alpha's own workspace."""

    text = _corpus(goal, acceptance_criteria)
    return bool(_contains(text, _EXTERNAL_EFFECT_MARKERS) or _contains(text, _DESTRUCTIVE_MARKERS))


@dataclass(frozen=True)
class GroDecision:
    """Policy verdict for one task: mode, group size, and why."""

    mode: RunMode
    group_size: int
    task_type: TaskType
    risk_tier: RiskTier
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "group_size": self.group_size,
            "task_type": self.task_type.value,
            "risk_tier": self.risk_tier.value,
            "reasons": list(self.reasons),
        }


#: Tier -> group size defaults; the operator's ``gro.tiers``-style values come
#: from the configured candidate ceiling, so the smaller of the two wins.
_DEFAULT_GROUP_SIZES: dict[RiskTier, int] = {
    RiskTier.TRIVIAL: 1,
    RiskTier.ORDINARY: 1,
    RiskTier.UNCERTAIN: 2,
    RiskTier.COMPLEX: 3,
}


def decide_mode(
    goal: str,
    acceptance_criteria: Sequence[str] = (),
    *,
    config: GroConfig | None = None,
    task_type: TaskType | None = None,
    candidate_count: int | None = None,
) -> GroDecision:
    """Choose single / candidate_group / human_approval_first for this task.

    Precedence: explicit caller ``candidate_count`` > configured posture >
    tier table. An explicit count is a requirement, not a suggestion, but it
    is still bounded by the configured ceiling.
    """

    cfg = config or GroConfig()
    resolved_type = task_type or classify_task_type(goal, acceptance_criteria)
    tier, reasons = classify_risk_tier(goal, acceptance_criteria)

    if cfg.require_approval_for_external_side_effects and has_external_side_effects(goal, acceptance_criteria):
        return GroDecision(mode=RunMode.HUMAN_APPROVAL_FIRST, group_size=0, task_type=resolved_type, risk_tier=tier, reasons=tuple(reasons + ["external_side_effects_require_approval"]))

    if cfg.default_mode == "single":
        group_size = 1
        reasons.append("posture=single")
    elif cfg.default_mode == "group":
        group_size = max(1, min(cfg.max_candidates_per_task, candidate_count or cfg.max_candidates_per_task))
        reasons.append("posture=group")
    else:  # adaptive: tier table, bounded by the ceiling
        group_size = _DEFAULT_GROUP_SIZES[tier]
        reasons.append(f"adaptive_tier={tier.value}")
    if candidate_count is not None:
        if candidate_count < 1:
            raise ValueError(f"candidate_count must be >= 1 (got {candidate_count})")
        group_size = min(candidate_count, cfg.max_candidates_per_task)
        if candidate_count > cfg.max_candidates_per_task:
            reasons.append(f"candidate_count_bounded_by_ceiling:{cfg.max_candidates_per_task}")
    group_size = max(1, min(group_size, cfg.max_candidates_per_task))
    mode = RunMode.CANDIDATE_GROUP if group_size > 1 else RunMode.SINGLE
    return GroDecision(mode=mode, group_size=group_size, task_type=resolved_type, risk_tier=tier, reasons=tuple(reasons))


def _corpus(goal: str, acceptance_criteria: Sequence[str]) -> str:
    return (str(goal) + "\n" + "\n".join(str(c) for c in acceptance_criteria)).strip().lower()
