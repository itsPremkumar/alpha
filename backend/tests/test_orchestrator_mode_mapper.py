"""Mode-mapper (DY-R4, plan section 14) tests: the 7 paradigms -> DWE, literally.

Section 14 found all seven ``ExecutionParadigm`` dispatchers in
``alpha/planning/bridge.py`` bypassing the DynamicWorkflowEngine ("none routes
through the DWE") while ``orchestrator/loop.py::run_turn`` was greenfield. This
suite pins the P1 mapping partition EXACTLY and executes the mapped runs:

- EXPRESSIBLE (6) each produce a real DWE run whose graph carries the mapped
  node kind(s) in BOTH modes (normal/bot share one kernel, section 13);
- ``swarm`` is the ONE honest non-expressible result (dynamic swarm topology
  lands with plan P7) — refused with its stated reason, no run started;
- unknown paradigm names are refused honestly, never silently mapped;
- execution outcomes stay honest: digest-backed runs complete with recomputable
  evidence, ``moa`` WITHOUT a bound voting executor fails naming the real
  missing piece, ``moa`` WITH a bound voting executor records
  ``vote_source=executor`` ballots; ``deep_research`` without ``sources`` fails
  with the engine's real reason.
"""

from __future__ import annotations

import pytest

import alpha.orchestrator.executors as executors_module
from alpha.orchestrator.executors import DIGEST_EXECUTOR, VOTE_EXECUTOR, ExecutorRegistry
from alpha.orchestrator.loop import ExecutionKernel
from alpha.orchestrator.mode_mapper import (
    EXPRESSIBLE_PARADIGMS,
    MODES,
    NON_EXPRESSIBLE_REASONS,
    build_paradigm_definition,
    map_paradigm,
)
from alpha.planning.meta_planner import ExecutionParadigm
from alpha.workflow.models import NodeType, WorkflowRunStatus
from alpha.workflow.runtime import DynamicWorkflowEngine


@pytest.fixture(autouse=True)
def _isolate_agent_workspace(tmp_path, monkeypatch):
    """AGENT_WORKSPACE_HOME points at a per-test temp dir (process-global env)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


@pytest.fixture()
def registry(monkeypatch):
    """Fresh EMPTY executor registry per test (module-seam swap, auto-restored)."""
    reg = ExecutorRegistry()
    monkeypatch.setattr(executors_module, "_REGISTRY", reg)
    return reg


def _bind_digest(reg: ExecutorRegistry) -> None:
    reg.register(DIGEST_EXECUTOR, executors_module.local_digest_executor)


def _bare_kernel() -> ExecutionKernel:
    return ExecutionKernel(engine=DynamicWorkflowEngine())


# ----------------------------------------------------------------- partition


def test_seven_paradigms_partition_exactly_into_expressible_and_refused():
    """The literal section-14 partition: 6 expressible + swarm refused = all 7."""
    all_paradigms = {p.value for p in ExecutionParadigm}
    assert all_paradigms == {
        "deep_research",
        "deep_think",
        "swarm",
        "moa",
        "bot_profile",
        "subagent",
        "direct_agent",
    }
    assert EXPRESSIBLE_PARADIGMS == {
        "deep_research",
        "deep_think",
        "moa",
        "bot_profile",
        "subagent",
        "direct_agent",
    }
    assert set(NON_EXPRESSIBLE_REASONS) == {"swarm"}
    assert EXPRESSIBLE_PARADIGMS | set(NON_EXPRESSIBLE_REASONS) == all_paradigms
    assert EXPRESSIBLE_PARADIGMS & set(NON_EXPRESSIBLE_REASONS) == set()
    assert MODES == ("normal", "bot")


@pytest.mark.parametrize(
    "paradigm,mode,expected_kinds,expected_archetype",
    [
        ("direct_agent", "normal", ("agent",), "sequential"),
        ("direct_agent", "bot", ("bot",), "sequential"),  # section 13: bot kind in bot mode
        ("subagent", "normal", ("subagent",), "subagent-delegation"),
        ("subagent", "bot", ("subagent",), "subagent-delegation"),
        ("bot_profile", "normal", ("bot",), "sequential"),
        ("bot_profile", "bot", ("bot",), "sequential"),
        ("moa", "normal", ("quorum",), "quorum-vote"),
        ("moa", "bot", ("quorum",), "quorum-vote"),
        ("deep_research", "normal", ("map", "reduce"), "map-reduce"),
        ("deep_research", "bot", ("map", "reduce"), "map-reduce"),
        ("deep_think", "normal", ("loop",), "loop-bounded"),
        ("deep_think", "bot", ("loop",), "loop-bounded"),
    ],
)
def test_expressible_paradigms_map_to_expected_dwe_construct(paradigm, mode, expected_kinds, expected_archetype):
    """Every expressible paradigm maps to its stated DWE construct (both modes)."""
    mapping = build_paradigm_definition(paradigm, mode=mode, prompt="do the thing")

    assert mapping.expressible is True
    assert mapping.definition is not None
    assert mapping.run is None  # build only maps; runs start via map_paradigm
    assert mapping.node_kinds == expected_kinds
    assert mapping.archetype == expected_archetype
    assert mapping.reason  # a stated mapping reason, never silence
    meta = mapping.definition.graph.metadata
    assert meta["paradigm"] == paradigm
    assert meta["mode"] == mode
    assert meta["archetype"] == expected_archetype


def test_mapped_nodes_declare_their_executors_literally():
    """Construct-level executor declarations (vote executor intentionally unbound)."""
    direct = build_paradigm_definition("direct_agent").definition
    assert direct.graph.nodes["direct"].executor == DIGEST_EXECUTOR

    moa = build_paradigm_definition("moa").definition
    assert moa.graph.nodes["deliberate"].executor == VOTE_EXECUTOR
    assert moa.graph.nodes["deliberate"].config["voters"] == ["ensemble_model_a", "ensemble_model_b"]
    assert moa.graph.nodes["deliberate"].config["required_votes"] == 2

    research = build_paradigm_definition("deep_research").definition
    assert research.graph.nodes["fan"].type == NodeType.MAP
    assert research.graph.nodes["fan"].config["items_key"] == "sources"
    assert research.graph.nodes["fan"].executor == DIGEST_EXECUTOR
    assert research.graph.nodes["synthesize"].type == NodeType.REDUCE
    assert research.graph.nodes["synthesize"].config["input_key"] == "fan_mapped"
    assert research.graph.nodes["synthesize"].executor == DIGEST_EXECUTOR

    think = build_paradigm_definition("deep_think").definition
    reflect = think.graph.nodes["reflect"]
    assert reflect.type == NodeType.LOOP
    assert reflect.executor == DIGEST_EXECUTOR
    assert reflect.loop_policy is not None
    assert reflect.loop_policy.max_iterations == 2  # bounded, never unbounded


@pytest.mark.parametrize("mode", ["normal", "bot"])
def test_swarm_is_honestly_not_expressible(mode):
    """Swarm: the one explicit refusal — no run, no fake static fan-out."""
    mapping = build_paradigm_definition("swarm", mode=mode, prompt="go wild")

    assert mapping.expressible is False
    assert mapping.definition is None
    assert mapping.run is None
    assert mapping.workflow_id is None
    assert "not expressible yet" in mapping.reason
    assert "P7" in mapping.reason  # names the real landing phase
    assert "no run is started" in mapping.reason


def test_unknown_paradigm_is_honestly_refused():
    mapping = build_paradigm_definition("teleport_through_walls")

    assert mapping.expressible is False
    assert mapping.definition is None
    assert "unknown execution paradigm 'teleport_through_walls'" in mapping.reason
    assert "not expressible yet" in mapping.reason


def test_enum_and_string_parity():
    by_enum = build_paradigm_definition(ExecutionParadigm.DEEP_THINK)
    by_name = build_paradigm_definition("deep_think")
    assert by_enum.expressible and by_name.expressible
    assert by_enum.node_kinds == by_name.node_kinds
    assert by_enum.archetype == by_name.archetype


# ------------------------------------------------------------- run creation


@pytest.mark.parametrize("mode", ["normal", "bot"])
def test_map_paradigm_starts_a_real_run_per_expressible_paradigm(registry, mode):
    """Each expressible paradigm yields a started DWE run; swarm yields none."""
    _bind_digest(registry)
    kernel = _bare_kernel()

    started: dict[str, str] = {}
    for paradigm in sorted(EXPRESSIBLE_PARADIGMS):
        mapping = map_paradigm(paradigm, kernel=kernel, prompt=f"task for {paradigm}", mode=mode)
        assert mapping.expressible is True, paradigm
        assert mapping.run is not None, paradigm
        assert mapping.run.status == WorkflowRunStatus.RUNNING, paradigm
        assert kernel.engine.get_run(mapping.run.run_id) is mapping.run
        assert kernel.engine.get_definition(mapping.workflow_id) is not None
        assert mapping.run.metrics.get("execution_mode") == mode
        started[paradigm] = mapping.run.run_id

    assert len(started) == 6
    assert len(set(started.values())) == 6  # distinct runs, no shared identity

    runs_before = dict(kernel.engine.runs)
    swarm = map_paradigm("swarm", kernel=kernel, prompt="nope", mode=mode)
    assert swarm.expressible is False
    assert swarm.run is None
    assert kernel.engine.runs == runs_before, "the refused paradigm must not start a run"


# --------------------------------------------------------------- execution


@pytest.mark.parametrize(
    "paradigm,initial_state",
    [
        ("direct_agent", None),
        ("subagent", None),
        ("bot_profile", None),
        ("deep_research", {"sources": ["https://a.example", "https://b.example"]}),
        ("deep_think", None),
    ],
)
def test_mapped_runs_complete_with_recomputable_evidence(registry, paradigm, initial_state):
    """Digest-backed mappings execute to COMPLETED with real hashed evidence."""
    _bind_digest(registry)
    kernel = _bare_kernel()
    mapping = map_paradigm(paradigm, kernel=kernel, prompt=f"run {paradigm}", initial_state=initial_state)
    assert mapping.run is not None and mapping.workflow_id is not None

    run, waves = kernel.run_to_completion(mapping.run.run_id)

    assert run.status == WorkflowRunStatus.COMPLETED, f"{paradigm} ended {run.status}"
    assert waves >= 1
    assert run.failed_nodes == []
    assert set(run.completed_nodes) == set(run.node_states), "every mapped node executed"

    graph = kernel.engine.graphs[f"{mapping.workflow_id}:v{run.graph_version}"]
    for nid in run.completed_nodes:
        node = graph.nodes[nid]
        assert node.evidence, f"{nid} completed without evidence"
        # Evidence derives from the real sha256 executor (wrappers for map/reduce).
        assert all("alpha.local.digest sha256=" in e for e in node.evidence)


def test_deep_research_fans_out_and_folds_only_executor_results(registry):
    """MAP writes <id>_mapped, REDUCE folds it into <id>_reduced, both journaled."""
    _bind_digest(registry)
    kernel = _bare_kernel()
    mapping = map_paradigm(
        "deep_research",
        kernel=kernel,
        prompt="research it",
        initial_state={"sources": ["s1", "s2", "s3"]},
    )
    run, waves = kernel.run_to_completion(mapping.run.run_id)

    assert run.status == WorkflowRunStatus.COMPLETED
    assert waves == 2  # fan-out wave, then fold wave
    assert len(run.state["fan_mapped"]) == 3
    # The REDUCE node folds into ITS OWN documented state key: <reduce_id>_reduced.
    assert run.state["synthesize_reduced"]["node_id"] == "synthesize[2]"  # last folded child
    assert run.metrics.get("executor_state_keys") == ["fan_mapped", "synthesize_reduced"]
    assert run.completed_nodes == ["fan", "synthesize"]


def test_deep_research_without_sources_fails_honestly(registry):
    """Missing map input: the engine's real reason, fail-closed, no evidence."""
    _bind_digest(registry)
    kernel = _bare_kernel()
    mapping = map_paradigm("deep_research", kernel=kernel, prompt="research", initial_state={"unrelated": 1})

    run, _waves = kernel.run_to_completion(mapping.run.run_id)

    assert run.status == WorkflowRunStatus.FAILED
    assert run.failed_nodes == ["fan"]
    assert "synthesize" not in run.completed_nodes  # dependent never ran

    graph = kernel.engine.graphs[f"{mapping.workflow_id}:v{run.graph_version}"]
    fan = graph.nodes["fan"]
    assert "map input 'sources' not present as a list in run state" in str(fan.output.get("reason", ""))
    assert fan.evidence == []
    assert not [k for k in run.state if k.startswith("fan_")]


def test_deep_think_executes_a_bounded_loop(registry):
    """deep_think re-executes up to LoopPolicy.max_iterations, then completes."""
    _bind_digest(registry)
    kernel = _bare_kernel()
    mapping = map_paradigm("deep_think", kernel=kernel, prompt="think it through")

    run, waves = kernel.run_to_completion(mapping.run.run_id)

    assert run.status == WorkflowRunStatus.COMPLETED
    assert waves == 2  # iteration 1, iteration 2 -> bounded stop
    assert run.iteration_counts == {"reflect": 2}
    assert run.completed_nodes == ["reflect"]


def test_moa_without_voting_executor_fails_naming_the_missing_piece(registry):
    """Unbound vote executor: honest quorum failure, zero fabricated ballots."""
    _bind_digest(registry)  # digest bound, vote executor deliberately NOT bound
    assert VOTE_EXECUTOR not in registry.names()

    kernel = _bare_kernel()
    mapping = map_paradigm("moa", kernel=kernel, prompt="reach consensus")

    run, _waves = kernel.run_to_completion(mapping.run.run_id)

    assert run.status == WorkflowRunStatus.FAILED
    assert run.failed_nodes == ["deliberate"]
    graph = kernel.engine.graphs[f"{mapping.workflow_id}:v{run.graph_version}"]
    node = graph.nodes["deliberate"]
    reason = str(node.output.get("reason", ""))
    assert "quorum voter 'ensemble_model_a'" in reason
    assert "no executor registered for node 'deliberate[0]'" in reason
    assert VOTE_EXECUTOR in reason
    assert node.evidence == []  # no invented agreement


def test_moa_with_bound_voting_executor_records_real_ballots(registry):
    """A caller-bound voting executor produces executor-sourced quorum votes."""
    _bind_digest(registry)

    def voting_executor(node, run):
        voter = node.config.get("voter")
        assert voter in ("ensemble_model_a", "ensemble_model_b")
        return {
            "status": "completed",
            "output": "agree",
            "evidence": f"ballot recorded for {voter}: agree",
            "tokens_used": 0,
        }

    registry.register(VOTE_EXECUTOR, voting_executor)

    kernel = _bare_kernel()
    mapping = map_paradigm("moa", kernel=kernel, prompt="reach consensus")

    run, _waves = kernel.run_to_completion(mapping.run.run_id)

    assert run.status == WorkflowRunStatus.COMPLETED
    graph = kernel.engine.graphs[f"{mapping.workflow_id}:v{run.graph_version}"]
    node = graph.nodes["deliberate"]
    assert node.output == {
        "agreed": True,
        "votes": ["agree", "agree"],
        "vote_source": "executor",
        "agrees": 2,
        "required_votes": 2,
    }
    assert any("ballot recorded for ensemble_model_a" in e for e in node.evidence)
    assert any("vote_source=executor" in e for e in node.evidence)


# ------------------------------------------- literal-reason + structure pins


@pytest.mark.parametrize("mode", ["normal", "bot"])
def test_swarm_refusal_carries_the_literal_module_reason_verbatim(mode):
    """Both modes refuse with EXACTLY ``NON_EXPRESSIBLE_REASONS['swarm']``."""
    mapping = build_paradigm_definition("swarm", mode=mode, prompt="whatever")
    assert mapping.reason == NON_EXPRESSIBLE_REASONS["swarm"]


def test_invalid_mode_is_refused_before_any_mapping():
    """``mode`` outside ``MODES`` raises before a graph or run can exist."""
    with pytest.raises(ValueError, match="mode must be one of"):
        build_paradigm_definition("direct_agent", mode="chaos")
    with pytest.raises(ValueError, match="mode must be one of"):
        map_paradigm("direct_agent", kernel=_bare_kernel(), mode="chaos", prompt="nope")


def test_deep_research_declares_its_fan_in_both_ways():
    """REDUCE depends on MAP via ``depends_on`` AND the graph edge (both pinned)."""
    definition = build_paradigm_definition("deep_research").definition
    assert definition.graph.nodes["synthesize"].depends_on == ["fan"]
    assert [(e.source, e.target) for e in definition.graph.edges] == [("fan", "synthesize")]
