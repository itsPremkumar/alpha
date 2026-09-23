"""Mode mapper (DY-R4): the 7 meta-planner execution paradigms -> DWE constructs.

Section 14 finding this module remediates: ``alpha/planning/bridge.py``
dispatches all seven ``ExecutionParadigm`` values *outside* the
DynamicWorkflowEngine ("none routes through the DWE"). This mapper is the P1
counterpart: each paradigm name resolves to a typed ``WorkflowGraph``
(archetype + node kinds) that the real DWE executes, or to an explicit honest
"not expressible yet" result — never a silent success and never a shape that
pretends to be something it is not.

Mapping table (per section 13/14; asserted literally by
``tests/test_orchestrator_mode_mapper.py``):

===================  =====================  =========================================
Paradigm            DWE construct          Notes
===================  =====================  =========================================
``direct_agent``    sequential AGENT node  BOT node kind in bot mode (section 13)
``subagent``        SUBAGENT delegation    same kind in both modes
``bot_profile``     BOT node               the paradigm's defining construct
``moa``             QUORUM over voters     votes come from bound executors
                                            (``vote_source=executor``); requires a
                                            bound voting executor, else honest fail
``deep_research``   MAP -> REDUCE          fans out over ``state['sources']`` and
                                            folds executor results; missing inputs
                                            fail honestly inside the engine
``deep_think``      bounded LOOP node      ``LoopPolicy(max_iterations=...)``
``swarm``           NOT EXPRESSIBLE YET    the DWE graph has no dynamic swarm-
                                            topology construct (member join/leave,
                                            supervisor hierarchy, cost ledger land
                                            with plan P7); a static fan-out would
                                            misrepresent a swarm
===================  =====================  =========================================

Both modes share one kernel (section 13); only the node kinds and the bound
executors differ. Execution itself always flows through the DWE.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from alpha.orchestrator.executors import DIGEST_EXECUTOR, VOTE_EXECUTOR
from alpha.planning.meta_planner import ExecutionParadigm
from alpha.workflow.models import (
    LoopPolicy,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
)

if TYPE_CHECKING:  # pragma: no cover - typing only; loop imports this module at runtime
    from alpha.orchestrator.loop import ExecutionKernel

# Both modes share the kernel; this is the validated mode vocabulary (section 13).
MODES = ("normal", "bot")

# Paradigms that resolve to a DWE run. The complement within
# ExecutionParadigm is the honest non-expressible set (swarm, see module doc).
EXPRESSIBLE_PARADIGMS: frozenset[str] = frozenset(
    {
        ExecutionParadigm.DEEP_RESEARCH.value,
        ExecutionParadigm.DEEP_THINK.value,
        ExecutionParadigm.MOA.value,
        ExecutionParadigm.BOT_PROFILE.value,
        ExecutionParadigm.SUBAGENT.value,
        ExecutionParadigm.DIRECT_AGENT.value,
    }
)

NON_EXPRESSIBLE_REASONS: dict[str, str] = {
    ExecutionParadigm.SWARM.value: (
        "swarm is not expressible yet: the DWE graph model has no dynamic swarm-topology "
        "construct (member join/leave, supervisor hierarchy, and the swarm cost ledger land "
        "with plan P7); a static MAP fan-out would misrepresent a swarm, so no run is started."
    ),
}


@dataclass(frozen=True)
class ParadigmMapping:
    """Outcome of mapping one paradigm name onto the DWE (or refusing to)."""

    paradigm: str
    expressible: bool
    reason: str
    mode: str = "normal"
    workflow_id: str | None = None
    archetype: str | None = None
    node_kinds: tuple[str, ...] = field(default_factory=tuple)
    definition: WorkflowDefinition | None = None
    run: WorkflowRun | None = None


def _agent_kind(mode: str) -> NodeType:
    """Section 13: normal mode runs AGENT nodes, bot mode runs BOT nodes."""
    return NodeType.BOT if mode == "bot" else NodeType.AGENT


def _single_node(node_id: str, node_type: NodeType, prompt: str, **config: object) -> WorkflowNode:
    return WorkflowNode(
        id=node_id,
        type=node_type,
        executor=DIGEST_EXECUTOR,
        prompt=prompt,
        config=dict(config),
    )


def _build_graph(paradigm: ExecutionParadigm, mode: str, prompt: str) -> tuple[WorkflowGraph, str, str]:
    """Build the paradigm's graph. Returns (graph, archetype, honest reason)."""
    if paradigm == ExecutionParadigm.DIRECT_AGENT:
        node = _single_node("direct", _agent_kind(mode), prompt)
        graph = WorkflowGraph(version=1, nodes={"direct": node}, edges=[])
        reason = (
            "sequential archetype: one direct-agent node executed through the bound "
            f"node_runner ({node.type.value} node kind for mode={mode})"
        )
        return graph, "sequential", reason

    if paradigm == ExecutionParadigm.SUBAGENT:
        node = _single_node("delegate", NodeType.SUBAGENT, prompt)
        graph = WorkflowGraph(version=1, nodes={"delegate": node}, edges=[])
        reason = (
            "subagent-delegation archetype: one SUBAGENT node executed through the bound "
            "node_runner (same kind in normal and bot mode per section 13)"
        )
        return graph, "subagent-delegation", reason

    if paradigm == ExecutionParadigm.BOT_PROFILE:
        node = _single_node("bot_task", NodeType.BOT, prompt)
        graph = WorkflowGraph(version=1, nodes={"bot_task": node}, edges=[])
        reason = (
            "sequential archetype over a BOT node: the bot task itself runs through the "
            "bound node_runner; a specialist clone is only created when node config asks "
            "for one (never fabricated)"
        )
        return graph, "sequential", reason

    if paradigm == ExecutionParadigm.MOA:
        node = WorkflowNode(
            id="deliberate",
            type=NodeType.QUORUM,
            executor=VOTE_EXECUTOR,
            prompt=prompt,
            config={
                "voters": ["ensemble_model_a", "ensemble_model_b"],
                "required_votes": 2,
            },
        )
        graph = WorkflowGraph(version=1, nodes={"deliberate": node}, edges=[])
        reason = (
            "quorum/vote archetype: ballots are real node_runner results "
            "(vote_source=executor); with no voting executor bound the node fails with "
            "the real reason instead of inventing agreement"
        )
        return graph, "quorum-vote", reason

    if paradigm == ExecutionParadigm.DEEP_RESEARCH:
        fan = WorkflowNode(
            id="fan",
            type=NodeType.MAP,
            executor=DIGEST_EXECUTOR,
            prompt=prompt,
            config={"items_key": "sources"},
        )
        synthesize = WorkflowNode(
            id="synthesize",
            type=NodeType.REDUCE,
            executor=DIGEST_EXECUTOR,
            prompt=prompt,
            config={"input_key": f"{fan.id}_mapped", "initial_value": None},
            depends_on=[fan.id],
        )
        graph = WorkflowGraph(
            version=1,
            nodes={fan.id: fan, synthesize.id: synthesize},
            edges=[WorkflowEdge(source=fan.id, target=synthesize.id)],
        )
        reason = (
            "map-reduce archetype: MAP fans out over state['sources'] through the bound "
            "node_runner and REDUCE folds only executor-produced child results; when "
            "'sources' is absent the MAP fails with the engine's real reason"
        )
        return graph, "map-reduce", reason

    if paradigm == ExecutionParadigm.DEEP_THINK:
        node = WorkflowNode(
            id="reflect",
            type=NodeType.LOOP,
            executor=DIGEST_EXECUTOR,
            prompt=prompt,
            loop_policy=LoopPolicy(max_iterations=2),
        )
        graph = WorkflowGraph(version=1, nodes={"reflect": node}, edges=[])
        reason = (
            "bounded-loop archetype: a LOOP node re-executes through the bound "
            "node_runner until LoopPolicy.max_iterations (2) is reached — bounded, "
            "never unbounded self-reflection"
        )
        return graph, "loop-bounded", reason

    raise ValueError(f"unmapped paradigm: {paradigm!r}")


def build_paradigm_definition(
    paradigm: str | ExecutionParadigm,
    *,
    mode: str = "normal",
    prompt: str = "",
    workflow_id: str | None = None,
) -> ParadigmMapping:
    """Map a paradigm name to a WorkflowDefinition without starting a run.

    Returns a non-expressible :class:`ParadigmMapping` (``definition is None``)
    for swarm and for unknown names — an honest refusal, never a silent graph.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    if isinstance(paradigm, ExecutionParadigm):
        normalized = paradigm.value
        resolved: ExecutionParadigm = paradigm
    else:
        normalized = str(paradigm)
        try:
            resolved = ExecutionParadigm(normalized)
        except ValueError:
            return ParadigmMapping(
                paradigm=normalized,
                expressible=False,
                mode=mode,
                reason=(
                    f"unknown execution paradigm '{normalized}' has no DWE construct "
                    "mapping — not expressible yet, no run started"
                ),
            )

    if normalized in NON_EXPRESSIBLE_REASONS:
        return ParadigmMapping(
            paradigm=normalized,
            expressible=False,
            mode=mode,
            reason=NON_EXPRESSIBLE_REASONS[normalized],
        )

    graph, archetype, reason = _build_graph(resolved, mode, prompt)
    graph.metadata.update({"paradigm": normalized, "mode": mode, "archetype": archetype})
    wid = workflow_id or f"wf_{normalized}_{uuid.uuid4().hex[:8]}"
    definition = WorkflowDefinition(
        id=wid,
        name=f"{normalized} ({mode} mode)",
        description=reason,
        graph=graph,
        variables={},
    )
    return ParadigmMapping(
        paradigm=normalized,
        expressible=True,
        mode=mode,
        reason=reason,
        workflow_id=wid,
        archetype=archetype,
        node_kinds=tuple(node.type.value for node in graph.nodes.values()),
        definition=definition,
    )


def map_paradigm(
    paradigm: str | ExecutionParadigm,
    *,
    kernel: ExecutionKernel,
    mode: str = "normal",
    prompt: str = "",
    initial_state: dict[str, object] | None = None,
    workflow_id: str | None = None,
) -> ParadigmMapping:
    """Map a paradigm and start a real DWE run for it through the kernel.

    Expressible paradigms: registers the definition on the kernel's engine and
    returns a started run. Non-expressible paradigms: returns the honest
    refusal with ``run is None`` — callers must branch on ``expressible``.
    """
    mapping = build_paradigm_definition(paradigm, mode=mode, prompt=prompt, workflow_id=workflow_id)
    if not mapping.expressible or mapping.definition is None or mapping.workflow_id is None:
        return mapping

    kernel.engine.register_definition(mapping.definition)
    state: dict[str, object] = {"objective": prompt} if not initial_state else dict(initial_state)
    run = kernel.start_run(mapping.workflow_id, initial_state=state, mode=mode)
    return ParadigmMapping(
        paradigm=mapping.paradigm,
        expressible=True,
        reason=mapping.reason,
        mode=mapping.mode,
        workflow_id=mapping.workflow_id,
        archetype=mapping.archetype,
        node_kinds=mapping.node_kinds,
        definition=mapping.definition,
        run=run,
    )
