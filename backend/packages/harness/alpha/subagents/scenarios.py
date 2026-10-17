"""Delegation scenarios: the catalogue of *shapes* a request could take.

A scenario is a **shape**, not an engine. It says what kind of fan-out would
serve a request and what has to be true before that shape is honest. It
executes nothing - `alpha.subagents.plan` turns one into an `ExecutionPlan`,
and even that is advisory until shadow mode is turned off.

The catalogue exists so that the choice is **inspectable and refusable** rather
than implied. Alpha already contains a bracket tournament (`arena`), a
group-relative comparison (`gro`), a debate council (`deliberation`), a DAG
engine (`workflow`) and plain delegation (`task`). Before this module those were
five separate operator choices; a scenario names them in one vocabulary and,
crucially, records *why a named scenario was not used*.

Three rules are load-bearing:

- **A requirement that is unmet degrades and says so.** It never silently
  substitutes a different scenario, because "it ran something else" is exactly
  the report an operator cannot detect.
- **A scenario whose mechanism is absent is `unavailable`, never a fallback.**
  See `ArenaConfig`'s `compare`/`synthesize` precedent: those modes are refused
  by name rather than degraded into `decide`.
- **The catalogue is data.** `alpha.capabilities.catalog` and the Execution
  modes UI read it; nothing here imports an engine, so adding a scenario cannot
  create an import cycle or drag in a disabled subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


#: How a scenario would reach the system, if it were ever executed. These name
#: the *mechanism*, never a promise that it ran.
class Dispatch(StrEnum):
    #: The lead agent answers directly. No delegation.
    NONE = "none"
    #: One child, the lead blocks on it. This is ordinary `task`.
    SINGLE = "single"
    #: N independent children, synthesised by the parent.
    FAN_OUT = "fan_out"
    #: A bracket tournament - `alpha.arena`.
    ARENA = "arena"
    #: Several models debate to a verdict - `alpha.deliberation`.
    COUNCIL = "council"
    #: A staged DAG - `alpha.workflow`.
    PIPELINE = "pipeline"
    #: A background child the parent does not block on.
    ASYNC = "async"


class ScenarioId(StrEnum):
    """Stable scenario identities.

    These are wire values: they travel in an `ExecutionPlan`, are reported by the
    Gateway and are rendered by the UI. Renaming one is a contract change, so
    prefer adding a member over editing an existing value.
    """

    DIRECT = "direct"
    SINGLE_DELEGATE = "single_delegate"
    PARALLEL_FAN_OUT = "parallel_fan_out"
    TOURNAMENT = "tournament"
    ADVISORY_COUNCIL = "advisory_council"
    STAGED_PIPELINE = "staged_pipeline"
    SWARM = "swarm"
    BACKGROUND_DELEGATE = "background_delegate"


class Requirement(StrEnum):
    """A precondition that must hold before a scenario is honest to propose.

    A requirement is evaluated against **measured** state, never configuration
    intent. `delegation_enabled` reads what the run was actually admitted with,
    not what `config.yaml` says a deployment could do.
    """

    #: The `task` tool exists for this run. Absent when delegation is off, when
    #: an agent preset narrows it away, or when policy forbids it.
    DELEGATION_ENABLED = "delegation_enabled"
    #: A live `GET /arena/config` read succeeded and Arena is enabled. A failed
    #: or absent read is *unknown*, which is not the same as false.
    ARENA_CONFIGURED = "arena_configured"
    #: `alpha.deliberation` answered a config/health read.
    COUNCIL_CONFIGURED = "council_configured"
    #: The dynamic-workflow engine answered a registries read.
    WORKFLOW_CONFIGURED = "workflow_configured"
    #: The swarm runtime answered a health read.  Swarm is an *orchestration*
    #: capability rather than a single ``task`` call, so its availability is
    #: measured separately from delegation.
    SWARM_CONFIGURED = "swarm_configured"


@dataclass(frozen=True)
class Scenario:
    """One entry in the catalogue. Immutable, and carries its own refusals."""

    id: ScenarioId
    title: str
    #: One sentence an operator can check against the request they typed.
    summary: str
    dispatch: Dispatch
    #: Every requirement must hold. An empty tuple means "always proposable".
    requirements: tuple[Requirement, ...] = ()
    #: Rough upper bound on children, used to size a plan against a budget. It is
    #: an *estimate*, never a reservation.
    estimated_children: int = 0
    #: The engine that would carry it out. Recorded so a plan names the
    #: mechanism rather than implying one.
    engine: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "title": self.title,
            "summary": self.summary,
            "dispatch": str(self.dispatch),
            "requirements": [str(r) for r in self.requirements],
            "estimated_children": self.estimated_children,
            "engine": self.engine,
        }


#: The catalogue, in proposal order. `alpha.subagents.plan` walks it top to
#: bottom and takes the first match whose requirements are met, which makes the
#: ordering a **priority**, not an implementation detail: a tournament request
#: that also reads as a fan-out must not be answered by the fan-out.
SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id=ScenarioId.TOURNAMENT,
        title="Tournament",
        summary="Put N strategies on the same task and let a bracket pick a winner.",
        dispatch=Dispatch.ARENA,
        requirements=(Requirement.DELEGATION_ENABLED, Requirement.ARENA_CONFIGURED),
        estimated_children=8,
        engine="alpha.arena",
    ),
    Scenario(
        id=ScenarioId.ADVISORY_COUNCIL,
        title="Advisory council",
        summary="Ask several models to argue a disagreement out to one verdict.",
        dispatch=Dispatch.COUNCIL,
        requirements=(Requirement.COUNCIL_CONFIGURED,),
        estimated_children=5,
        engine="alpha.deliberation",
    ),
    Scenario(
        id=ScenarioId.STAGED_PIPELINE,
        title="Staged pipeline",
        summary="Run ordered dependent stages as a DAG, each gated on the last.",
        dispatch=Dispatch.PIPELINE,
        requirements=(Requirement.DELEGATION_ENABLED, Requirement.WORKFLOW_CONFIGURED),
        estimated_children=6,
        engine="alpha.workflow",
    ),
    Scenario(
        id=ScenarioId.SWARM,
        title="Swarm",
        summary="Decompose a complex goal into a parallel DAG of workers with a coordinator.",
        dispatch=Dispatch.PIPELINE,
        requirements=(Requirement.DELEGATION_ENABLED, Requirement.SWARM_CONFIGURED),
        estimated_children=8,
        engine="alpha.swarm",
    ),
    Scenario(
        id=ScenarioId.PARALLEL_FAN_OUT,
        title="Parallel fan-out",
        summary="Split independent work across children and synthesise the results.",
        dispatch=Dispatch.FAN_OUT,
        requirements=(Requirement.DELEGATION_ENABLED,),
        estimated_children=4,
        engine="alpha.tools.builtins.task_tool",
    ),
    Scenario(
        id=ScenarioId.BACKGROUND_DELEGATE,
        title="Background delegate",
        summary="Hand off one long job and keep the conversation moving.",
        dispatch=Dispatch.ASYNC,
        requirements=(Requirement.DELEGATION_ENABLED,),
        estimated_children=1,
        engine="alpha.subagents.batch_service",
    ),
    Scenario(
        id=ScenarioId.SINGLE_DELEGATE,
        title="Single delegate",
        summary="Give one bounded job to a specialist and wait for it.",
        dispatch=Dispatch.SINGLE,
        requirements=(Requirement.DELEGATION_ENABLED,),
        estimated_children=1,
        engine="alpha.tools.builtins.task_tool",
    ),
    Scenario(
        id=ScenarioId.DIRECT,
        title="Direct",
        summary="The lead agent handles it without delegation.",
        dispatch=Dispatch.NONE,
        requirements=(),
        estimated_children=0,
        engine="",
    ),
)


def get_scenario(identifier: str) -> Scenario | None:
    """Look a scenario up by id. `None` when unknown - never a default.

    Returning `None` rather than falling back to `DIRECT` matters: a caller that
    asked for a scenario by an id this build does not have must be able to tell
    that apart from "we decided direct is right".
    """

    for scenario in SCENARIOS:
        if str(scenario.id) == identifier:
            return scenario
    return None


def catalogue() -> list[dict[str, Any]]:
    """The whole catalogue as plain data, for the Gateway and the UI."""

    return [scenario.to_dict() for scenario in SCENARIOS]


__all__ = [
    "Dispatch",
    "Requirement",
    "SCENARIOS",
    "Scenario",
    "ScenarioId",
    "catalogue",
    "get_scenario",
]
