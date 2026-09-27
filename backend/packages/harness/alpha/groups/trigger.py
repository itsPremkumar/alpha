"""Should this conversation open a war room? The auto-trigger decision.

The hard part of auto-deliberation is not running the room, it is *not* running
it. A super agent that convenes a panel on every turn is worse than useless: it
burns tokens, adds latency, and launders a confident-sounding synthesis over a
question nobody needed a committee for.

So this module is mostly vetoes. It reuses
:class:`alpha.deliberation.router.DeliberationRouter` - the same classifier the
``deliberate`` tool uses, so a room and a council never disagree about how hard a
prompt is - and then applies the gates that a classifier cannot know about:
budget, cooldown, duplication, and whether anyone is there to answer.

Design rules, in priority order:

1. **Default off.** :func:`decide` returns ``open_room=False`` unless the policy
   enables it. Opt-in is per deployment, not per prompt.
2. **Never in a non-interactive turn.** A scheduled or IM autonomous run has no
   human to arbitrate, so it must not silently spend a panel. This mirrors the
   existing rule that excludes ``ask_clarification`` from non-interactive runs.
3. **Every refusal names its reason.** ``rationale`` is always populated, so a
   skipped deliberation is auditable rather than invisible.
4. **Escalation, not replacement.** The router is asked whether deliberation is
   *worthwhile*; this module only decides whether to open a room *now*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from alpha.deliberation.models import DeliberationStrategy
from alpha.deliberation.router import DeliberationRouter, TaskDifficulty, TaskRisk

#: Difficulty/risk above which deliberation earns its cost even on a mediocre
#: prompt. Below these, only a genuinely contested prompt qualifies.
_HIGH_STAKES = {TaskDifficulty.HIGH_RISK, TaskDifficulty.COMPLEX, TaskDifficulty.AMBIGUOUS}
_HIGH_RISK = {TaskRisk.HIGH, TaskRisk.CRITICAL}

#: Prompt shapes that mean "there is a real disagreement to resolve", used when
#: risk is only MEDIUM.
_CONTESTED_MARKERS = (
    " vs ",
    " versus ",
    "pros and cons",
    "trade-off",
    "tradeoff",
    "should we",
    "which one",
    "compare ",
    "debate",
    "second opinion",
    "red team",
    "pre-mortem",
    "post-mortem",
)


@dataclass(slots=True)
class TriggerPolicy:
    """Operator-controlled switches. All default to the conservative answer."""

    #: Master switch. Nothing auto-opens while this is False.
    enabled: bool = False
    #: Refuse to open a room in a turn with no human present.
    require_interactive: bool = True
    #: Only open for these risk tiers. HIGH, not MEDIUM: an agent that convenes a
    #: panel on every medium-risk task is worse than one that never does.
    min_risk: TaskRisk = TaskRisk.HIGH
    #: Open for any difficulty at or above this, even at low risk.
    min_difficulty: TaskDifficulty = TaskDifficulty.COMPLEX
    #: Treat a contested-prompt marker as sufficient on its own.
    open_on_contested: bool = True
    #: Seconds before the same topic may open another room.
    cooldown_seconds: float = 900.0
    #: Reuse a recent run on the same topic instead of opening a new room.
    duplicate_window_seconds: float = 3600.0
    #: Hard cap on rooms opened per turn, as a blast-radius guard.
    max_rooms_per_turn: int = 1
    #: Never open for these strategies automatically; they need a human.
    human_only_strategies: tuple[str, ...] = ("red_team",)
    #: Ask a human before opening at all.
    require_confirmation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "require_interactive": self.require_interactive,
            "min_risk": str(self.min_risk),
            "min_difficulty": str(self.min_difficulty),
            "open_on_contested": self.open_on_contested,
            "cooldown_seconds": self.cooldown_seconds,
            "duplicate_window_seconds": self.duplicate_window_seconds,
            "max_rooms_per_turn": self.max_rooms_per_turn,
            "human_only_strategies": list(self.human_only_strategies),
            "require_confirmation": self.require_confirmation,
        }


@dataclass(slots=True)
class TriggerDecision:
    """Why a room did or did not open. ``rationale`` is never empty."""

    open_room: bool
    strategy: DeliberationStrategy = DeliberationStrategy.AUTO
    difficulty: TaskDifficulty = TaskDifficulty.SIMPLE
    risk: TaskRisk = TaskRisk.LOW
    #: The gate that decided it, e.g. ``"policy_disabled"`` or ``"eligible"``.
    gate: str = ""
    rationale: str = ""
    #: Set when this reuses a recent run rather than opening a new room.
    duplicate_of: str = ""
    #: True when a human must confirm before the room opens.
    needs_confirmation: bool = False
    roster_models: list[str] = field(default_factory=list)
    #: Every gate that fired, so a refusal explains itself fully.
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "open_room": self.open_room,
            "strategy": str(self.strategy),
            "difficulty": str(self.difficulty),
            "risk": str(self.risk),
            "gate": self.gate,
            "rationale": self.rationale,
            "duplicate_of": self.duplicate_of,
            "needs_confirmation": self.needs_confirmation,
            "roster_models": list(self.roster_models),
            "blockers": list(self.blockers),
        }


def _risk_rank(risk: TaskRisk) -> int:
    order = [TaskRisk.LOW, TaskRisk.MEDIUM, TaskRisk.HIGH, TaskRisk.CRITICAL]
    return order.index(risk)


def _difficulty_rank(difficulty: TaskDifficulty) -> int:
    order = [
        TaskDifficulty.TRIVIAL,
        TaskDifficulty.SIMPLE,
        TaskDifficulty.MEDIUM,
        TaskDifficulty.COMPLEX,
        TaskDifficulty.HIGH_RISK,
        TaskDifficulty.AMBIGUOUS,
    ]
    return order.index(difficulty)


def _is_contested(prompt: str) -> bool:
    lowered = f" {(prompt or '').lower()} "
    return any(marker in lowered for marker in _CONTESTED_MARKERS)


def decide(
    prompt: str,
    *,
    policy: TriggerPolicy | None = None,
    interactive: bool = True,
    recent_topics: list[tuple[str, float]] | None = None,
    rooms_opened_this_turn: int = 0,
    now: float = 0.0,
) -> TriggerDecision:
    """Decide whether this turn should open a war room.

    ``recent_topics`` is a list of ``(topic, opened_at)`` from this installation's
    own run history; the caller owns that store. Passing it is what makes
    cooldown and duplicate-reuse possible at all.
    """
    active = policy or TriggerPolicy()
    blockers: list[str] = []

    def refuse(gate: str, why: str) -> TriggerDecision:
        blockers.insert(0, gate)
        return TriggerDecision(open_room=False, gate=gate, rationale=why, blockers=list(blockers))

    if not active.enabled:
        return refuse("policy_disabled", "auto-triggering is disabled; open a war room explicitly if you want one")

    if active.require_interactive and not interactive:
        return refuse("non_interactive", "no human is present in this turn, so a panel cannot be arbitrated")

    if rooms_opened_this_turn >= active.max_rooms_per_turn:
        return refuse(
            "per_turn_cap",
            f"already opened {rooms_opened_this_turn} room(s) this turn; cap is {active.max_rooms_per_turn}",
        )

    if not (prompt or "").strip():
        return refuse("empty_prompt", "an empty prompt has nothing to deliberate")

    # Classify. A router fault must not become an accidental "yes".
    try:
        evaluation = DeliberationRouter.classify_smart(prompt, user_strategy=DeliberationStrategy.AUTO)
    except Exception as exc:  # noqa: BLE001 - fail closed, never open on a fault
        return refuse("router_unavailable", f"deliberation router unavailable ({type(exc).__name__}: {exc}); not opening a room")

    strategy = evaluation.strategy
    if strategy is DeliberationStrategy.SINGLE or not evaluation.worthwhile:
        return refuse("not_worthwhile", f"router says deliberation adds nothing here: {evaluation.rationale}")

    if str(strategy) in active.human_only_strategies:
        return refuse("human_only_strategy", f"{strategy} is reserved for an explicit human request")

    # Cooldown and duplicate reuse, checked against the install's own history.
    duplicate = ""
    if recent_topics:
        normalised = " ".join((prompt or "").lower().split())
        for topic, opened_at in recent_topics:
            age = now - opened_at
            if age < 0:
                continue
            if age < active.cooldown_seconds and " ".join((topic or "").lower().split()) == normalised:
                return refuse(
                    "cooldown",
                    f"an identical topic was deliberated {int(age)}s ago; cooldown is {int(active.cooldown_seconds)}s",
                )
            # Overlap means the PREVIOUS topic is contained in the new prompt: the
            # room already covered this ground, perhaps in a wider form. Testing
            # the other direction would only match a prompt that is a strict
            # subset of an old one, which is the rare case.
            prior = " ".join((topic or "").lower().split())
            if age < active.duplicate_window_seconds and prior and prior in normalised:
                duplicate = topic
                blockers.append("duplicate_reuse")

    # The eligibility test: high stakes, or a genuinely contested prompt.
    risk_ok = _risk_rank(evaluation.risk) >= _risk_rank(active.min_risk)
    difficulty_ok = _difficulty_rank(evaluation.difficulty) >= _difficulty_rank(active.min_difficulty)
    contested = active.open_on_contested and _is_contested(prompt)

    if not (risk_ok or difficulty_ok or contested):
        return refuse(
            "below_threshold",
            f"difficulty={evaluation.difficulty} risk={evaluation.risk} is below the auto-open thresholds and the prompt is not contested; {{evaluation.rationale}}",
        )

    needs_confirmation = active.require_confirmation
    if duplicate:
        return TriggerDecision(
            open_room=False,
            strategy=strategy,
            difficulty=evaluation.difficulty,
            risk=evaluation.risk,
            gate="duplicate_reuse",
            rationale=f"a room on this topic already exists; reuse it rather than spending another panel. {evaluation.rationale}",
            duplicate_of=duplicate,
            needs_confirmation=False,
            roster_models=list(evaluation.roster_models),
            blockers=["duplicate_reuse"],
        )

    return TriggerDecision(
        open_room=True,
        strategy=strategy,
        difficulty=evaluation.difficulty,
        risk=evaluation.risk,
        gate="eligible",
        rationale=evaluation.rationale,
        needs_confirmation=needs_confirmation,
        roster_models=list(evaluation.roster_models),
        blockers=blockers,
    )
