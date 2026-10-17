"""`ExecutionPlan` - what a request *would* take, resolved and inspectable.

This module answers one question: given what we perceive about a prompt and what
is actually enabled, which delegation shape is honest to propose? It **executes
nothing**.

Shadow mode is not optional here. A resolved plan is advisory until an operator
turns shadow off, because a classifier that silently re-routed real traffic would
be a behaviour change disguised as a feature. `SHADOW_BY_DEFAULT` is `True`, and
the plan carries the flag so a consumer can never mistake an advisory plan for an
executed one.

Three properties the tests hold:

- **The plan is immutable.** `@dataclass(frozen=True)`, so a consumer cannot
  rewrite a resolved scenario into the one it wanted.
- **Every skip is recorded.** `considered` names each higher-priority scenario
  that was passed over and why. Without it, a plan that silently ran `direct`
  when a tournament was asked for is indistinguishable from a correct plan.
- **A budget may only lower.** `admitted_children <= estimated_children`, and any
  reduction appears in `degradations` naming the bound. A scenario can never
  raise a ceiling.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from alpha.subagents.scenarios import Dispatch, Requirement, Scenario, ScenarioId, get_scenario

if TYPE_CHECKING:  # pragma: no cover - typing only, never a runtime import
    from alpha.workflow.dynamic_perception import PerceivedIntent

#: A resolved plan never dispatches until this is explicitly turned off.
SHADOW_BY_DEFAULT = True

#: Below this complexity a request is answered directly. Sits above the
#: perception engine's own `fast_direct` cut (0.3) so a plan proposes delegation
#: slightly later than the tier does, and never earlier.
_SINGLE_DELEGATE_COMPLEXITY = 0.35
#: At or above this, independent work is worth splitting.
_FAN_OUT_COMPLEXITY = 0.55

#: Signal phrases per scenario. Deliberately a small, reviewable keyword list
#: rather than a model call: this runs on every prompt, must be deterministic,
#: and a heuristic that costs a model call per keystroke would be a worse product
#: than no heuristic. A miss degrades to a lower scenario and says so.
_TOURNAMENT_SIGNALS = re.compile(r"\b(tournament|arena|compete|best[- ]of|head[- ]to[- ]head|versus|which (approach|option|implementation) (is |wins|better))\b", re.IGNORECASE)
_COUNCIL_SIGNALS = re.compile(r"\b(council|debate|disagree|second opinion|red team|adversarial|critique|devil'?s advocate|argue (this|both))\b", re.IGNORECASE)
_PIPELINE_SIGNALS = re.compile(r"\b(pipeline|staged?|stage[- ]by[- ]stage|then (build|deploy|migrate)|migrate|in order,)\b", re.IGNORECASE)
_SWARM_SIGNALS = re.compile(r"\b(swarm|multi[- ]?agent|bot team|workgroup|parallel workers|fan[- ]out team)\b", re.IGNORECASE)
_ASYNC_SIGNALS = re.compile(r"\b(in the background|background(?:ed)?|while i (work|do)|let me know when|don'?t wait|fire (it |this )?off)\b", re.IGNORECASE)
_FAN_OUT_SIGNALS = re.compile(r"\b(in parallel|at the same time|fan[- ]out|simultaneously|all of these|each of these|compare .* and )\b", re.IGNORECASE)


@dataclass(frozen=True)
class ExecutionPlan:
    """An immutable, inspectable proposal. Advisory until shadow is off."""

    scenario: ScenarioId
    #: The mechanism this shape would use, read from the catalogue.
    dispatch: Dispatch
    #: Why this scenario matched, in operator-checkable words.
    reason: str
    #: What the catalogue believes the shape costs.
    estimated_children: int
    #: What the budget actually allows. Always <= `estimated_children`.
    admitted_children: int
    #: Every higher-priority scenario passed over, with the reason.
    considered: tuple[tuple[str, str], ...] = ()
    #: Anything that had to be lowered, naming the bound. Never empty-and-silent.
    degradations: tuple[str, ...] = ()
    #: True while the plan is advisory only.
    shadow: bool = SHADOW_BY_DEFAULT
    #: The perception inputs the plan was resolved from, so a reader can check
    #: the classifier's starting point rather than trusting its conclusion.
    perceived_tier: str = ""
    complexity: float = 0.0

    @property
    def is_executable(self) -> bool:
        """Whether this plan is authorised to dispatch.

        A shadow plan is never executable, even when every requirement is met.
        That is the whole point of the flag: an advisory classifier and an
        executing one must never be the same object.
        """

        return not self.shadow

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # Tuples serialise as lists over JSON; normalise here so every consumer
        # sees one shape rather than depending on the transport.
        payload["considered"] = [list(row) for row in self.considered]
        payload["degradations"] = list(self.degradations)
        payload["scenario"] = str(self.scenario)
        payload["dispatch"] = str(self.dispatch)
        payload["is_executable"] = self.is_executable
        return payload


@dataclass(frozen=True)
class PlanContext:
    """The measured state a plan is resolved against.

    Every field is **measured**, never configuration intent. `arena_configured`
    in particular is `None` when the read failed, which is not the same claim as
    `False`: a failed read means "unknown", and treating unknown as absent is how
    a working capability silently stops being offered.
    """

    delegation_enabled: bool = False
    arena_configured: bool | None = None
    council_configured: bool | None = None
    workflow_configured: bool | None = None
    swarm_configured: bool | None = None
    #: Children this run may actually spend. `None` means "unstated", which is
    #: not the same as unlimited.
    child_budget: int | None = None

    def requirement_met(self, requirement: Requirement) -> bool | None:
        """`True`/`False` when measured; `None` when the read never answered."""

        if requirement is Requirement.DELEGATION_ENABLED:
            return self.delegation_enabled
        if requirement is Requirement.ARENA_CONFIGURED:
            return self.arena_configured
        if requirement is Requirement.COUNCIL_CONFIGURED:
            return self.council_configured
        if requirement is Requirement.WORKFLOW_CONFIGURED:
            return self.workflow_configured
        if requirement is Requirement.SWARM_CONFIGURED:
            return self.swarm_configured
        return None


def _requirement_state(context: PlanContext, requirement: Requirement) -> str:
    """Render a requirement check as an operator-readable clause."""

    met = context.requirement_met(requirement)
    if met is True:
        return f"{_requirement_label(requirement)} is available"
    if met is None:
        return f"{_requirement_label(requirement)} could not be read, so it is unknown rather than absent"
    return f"{_requirement_label(requirement)} is not available"


def _requirement_label(requirement: Requirement) -> str:
    return {
        Requirement.DELEGATION_ENABLED: "delegation",
        Requirement.ARENA_CONFIGURED: "Arena",
        Requirement.COUNCIL_CONFIGURED: "the council",
        Requirement.WORKFLOW_CONFIGURED: "the workflow engine",
        Requirement.SWARM_CONFIGURED: "the swarm runtime",
    }.get(requirement, str(requirement))


def _unmet_requirements(scenario: Scenario, context: PlanContext) -> tuple[Requirement, ...]:
    return tuple(r for r in scenario.requirements if context.requirement_met(r) is not True)


def _match_reason(scenario_id: ScenarioId, prompt: str, perceived: PerceivedIntent | None) -> str | None:
    """Why this scenario matched this prompt, or `None` when it does not.

    A signal phrase always wins over a tier, because a phrase is something the
    operator typed and a tier is something a heuristic inferred.
    """

    if scenario_id is ScenarioId.TOURNAMENT:
        if _TOURNAMENT_SIGNALS.search(prompt):
            return "the request names a contest, an arena or competing approaches"
        if perceived is not None and str(perceived.intent_type) == "orchestrate":
            return "the perceived intent is orchestration"
        return None
    if scenario_id is ScenarioId.ADVISORY_COUNCIL:
        if _COUNCIL_SIGNALS.search(prompt):
            return "the request asks for a disagreement to be argued out"
        return None
    if scenario_id is ScenarioId.STAGED_PIPELINE:
        if _PIPELINE_SIGNALS.search(prompt):
            return "the request describes ordered dependent stages"
        return None
    if scenario_id is ScenarioId.SWARM:
        if _SWARM_SIGNALS.search(prompt):
            return "the request asks for a swarm of parallel workers"
        if perceived is not None and perceived.complexity_score >= 0.7:
            return f"the perceived complexity {perceived.complexity_score:.2f} is high enough that a parallel fan-out team is worth considering"
        return None
    if scenario_id is ScenarioId.BACKGROUND_DELEGATE:
        if _ASYNC_SIGNALS.search(prompt):
            return "the request explicitly asks not to wait for the result"
        return None
    if scenario_id is ScenarioId.PARALLEL_FAN_OUT:
        if _FAN_OUT_SIGNALS.search(prompt):
            return "the request asks for work at the same time"
        if perceived is not None and perceived.complexity_score >= _FAN_OUT_COMPLEXITY:
            return f"the perceived complexity {perceived.complexity_score:.2f} is at or above {_FAN_OUT_COMPLEXITY:.2f}, so independent work is worth splitting"
        return None
    if scenario_id is ScenarioId.SINGLE_DELEGATE:
        if perceived is not None and perceived.complexity_score >= _SINGLE_DELEGATE_COMPLEXITY:
            return f"the perceived complexity {perceived.complexity_score:.2f} is at or above {_SINGLE_DELEGATE_COMPLEXITY:.2f}, so one bounded child beats doing it inline"
        return None
    return None


def resolve(
    prompt: str,
    *,
    context: PlanContext | None = None,
    perceived: PerceivedIntent | None = None,
    scenario: str | ScenarioId | None = None,
    shadow: bool = SHADOW_BY_DEFAULT,
) -> ExecutionPlan:
    """Resolve a prompt to an `ExecutionPlan`.

    Walks the catalogue in priority order and takes the first scenario that both
    matches the prompt and has every requirement met, recording each skip in
    `considered`. A scenario may be **requested** with `scenario=`, in which case
    an unmet requirement is an honest refusal to that shape rather than a silent
    substitution.
    """

    context = context or PlanContext()
    degradations: list[str] = []

    if scenario is not None:
        # Look up by string first. `ScenarioId(identifier)` raises `ValueError`
        # for an id this build does not have, so building the enum first would
        # turn "unknown scenario" into a crash instead of a refusal.
        identifier = str(scenario)
        requested = get_scenario(identifier)
        if requested is None:
            # An unknown id is a refusal, not a fallback to `direct`: a caller
            # naming a scenario this build lacks must be able to tell that from
            # a deliberate choice.
            return ExecutionPlan(
                scenario=ScenarioId.DIRECT,
                dispatch=Dispatch.NONE,
                reason=f"the requested scenario '{identifier}' does not exist in this build, so nothing was proposed",
                estimated_children=0,
                admitted_children=0,
                degradations=(f"requested scenario '{identifier}' is unknown to this build",),
                shadow=shadow,
                perceived_tier=str(perceived.execution_tier) if perceived is not None else "",
                complexity=perceived.complexity_score if perceived is not None else 0.0,
            )
        return _build(
            requested,
            reason=f"the scenario '{requested.id}' was requested explicitly",
            context=context,
            perceived=perceived,
            considered=(),
            shadow=shadow,
            degradations=degradations,
        )

    considered: list[tuple[str, str]] = []
    for candidate in _ordered_candidates():
        reason = _match_reason(candidate.id, prompt, perceived)
        if reason is None:
            continue
        unmet = _unmet_requirements(candidate, context)
        if unmet:
            considered.append((str(candidate.id), " and ".join(_requirement_state(context, r) for r in unmet)))
            continue
        considered.append((str(candidate.id), reason))
        return _build(
            candidate,
            reason=reason,
            context=context,
            perceived=perceived,
            considered=tuple(considered),
            shadow=shadow,
            degradations=degradations,
        )

    # Nothing matched. `direct` is the honest answer, and it is reached by
    # exhaustion rather than by a guess - a request with no delegation signal in
    # it is exactly the request that should not be delegated.
    return _build(
        get_scenario(str(ScenarioId.DIRECT)) or _fallback_direct(),
        reason="no delegation signal was present in the request",
        context=context,
        perceived=perceived,
        considered=tuple(considered),
        shadow=shadow,
        degradations=degradations,
    )


def _ordered_candidates() -> tuple[Scenario, ...]:
    """The catalogue in proposal order, excluding `DIRECT`.

    `DIRECT` is the exhaustion answer, not a candidate: letting it match on an
    empty reason would make every prompt match it first.
    """

    return tuple(s for s in _all_scenarios() if s.id is not ScenarioId.DIRECT)


def _all_scenarios() -> tuple[Scenario, ...]:
    from alpha.subagents.scenarios import SCENARIOS

    return SCENARIOS


def _fallback_direct() -> Scenario:
    return Scenario(
        id=ScenarioId.DIRECT,
        title="Direct",
        summary="The lead agent handles it without delegation.",
        dispatch=Dispatch.NONE,
    )


def _build(
    scenario: Scenario,
    *,
    reason: str,
    context: PlanContext,
    perceived: PerceivedIntent | None,
    considered: tuple[tuple[str, str], ...],
    shadow: bool,
    degradations: list[str],
) -> ExecutionPlan:
    """Apply the budget to a matched scenario. May only lower, never raise."""

    admitted = scenario.estimated_children
    budget = context.child_budget
    if budget is not None and scenario.estimated_children > budget:
        admitted = max(0, budget)
        degradations.append(f"'{scenario.id}' asks for about {scenario.estimated_children} children but this run has a budget of {budget}, so the plan was reduced to {admitted}")

    unmet = _unmet_requirements(scenario, context)
    for requirement in unmet:
        state = context.requirement_met(requirement)
        if state is None:
            degradations.append(f"'{scenario.id}' was requested explicitly but {_requirement_label(requirement)} could not be read")
        else:
            degradations.append(f"'{scenario.id}' was requested explicitly but {_requirement_label(requirement)} is not available")

    return ExecutionPlan(
        scenario=scenario.id,
        dispatch=scenario.dispatch,
        reason=reason,
        estimated_children=scenario.estimated_children,
        admitted_children=admitted,
        considered=considered,
        degradations=tuple(degradations),
        shadow=shadow,
        perceived_tier=str(perceived.execution_tier) if perceived is not None else "",
        complexity=perceived.complexity_score if perceived is not None else 0.0,
    )


__all__ = [
    "SHADOW_BY_DEFAULT",
    "ExecutionPlan",
    "PlanContext",
    "resolve",
]
