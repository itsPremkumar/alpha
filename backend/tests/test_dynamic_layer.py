"""DY-R3 contract tests for the dynamic perceive→decompose→assemble→bridge layer.

Covers, against the REAL alpha.workflow.models API (models.py is never edited):
- all four modules import (this alone proves the NodeType.TASK import-time cascade is fixed)
- decomposed intents compile to graphs that pass real model validators
- every slash-command the perception layer can emit exists in alpha.commands.catalog
- the assembler registers NO fabricated skill body into the live skills hub and
  discloses generation_method / skill_generation / mcp_generation honestly
- the bridge records only real (stub-runner-bound) execution, and fails honestly
  with a real reason when no runner is bound — never fake success
- saga compensation executes only through the bound seam, else honest refusal
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from alpha.commands.catalog import get_default_catalog_entries
from alpha.skills.hub.discovery import SkillPackage
from alpha.skills.mcp_lifecycle import SkillMcpLifecycleManager
from alpha.workflow import dynamic_assembler, dynamic_decomposer, dynamic_perception
from alpha.workflow.dynamic_assembler import AssembledResources, DynamicResourceAssembler
from alpha.workflow.dynamic_bridge import DynamicWorkflowBridge
from alpha.workflow.dynamic_decomposer import CATEGORY_NODE_TYPES, DynamicDecomposer
from alpha.workflow.dynamic_perception import (
    DynamicExecutionTier,
    DynamicIntentType,
    DynamicPerceptionEngine,
    PerceivedIntent,
)
from alpha.workflow.models import (
    EdgeMode,
    NodeStatus,
    NodeType,
    RetryPolicy,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
)

CATALOG_COMMANDS = frozenset(entry[0] for entry in get_default_catalog_entries())


@pytest.fixture(autouse=True)
def agent_workspace_home(tmp_path, monkeypatch):
    """Every test runs inside an isolated workspace (DY-R3 requirement)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_PROMPT = "research the codebase, then plan and build the feature with tests"


def _sample_goal(prompt: str = _SAMPLE_PROMPT):
    intent = DynamicPerceptionEngine().perceive(prompt)
    goal = DynamicDecomposer().decompose(intent, prompt)
    return intent, goal


class _StubCloneEngine:
    """Stands in for BotCloneEngine so tests never touch the real singleton."""


class _RecordingBotRegistry:
    def __init__(self) -> None:
        self.registered = []

    def register(self, profile) -> None:
        self.registered.append(profile)


class _RecordingSkillsHub:
    """Duck-typed skills-hub seam: records every registration attempt."""

    def __init__(self, packages=None, installed_skills_dir: Path | None = None,
                 installed: list[dict] | None = None) -> None:
        self.packages = list(packages or [])
        self.added = []
        self.installed_skills_dir = installed_skills_dir
        self._installed = list(installed or [])

    def search(self, query: str):
        return list(self.packages)

    def add_to_catalog(self, package) -> None:
        self.added.append(package)

    def list_installed(self):
        return list(self._installed)


def _make_assembler(hub: _RecordingSkillsHub, mcp_manager=None) -> DynamicResourceAssembler:
    return DynamicResourceAssembler(
        bot_registry=_RecordingBotRegistry(),
        clone_engine=_StubCloneEngine(),
        skills_hub=hub,
        mcp_manager=mcp_manager,
    )


# ---------------------------------------------------------------------------
# 1. Importability — proves the NodeType.TASK import-time cascade is fixed
# ---------------------------------------------------------------------------

def test_all_dynamic_layer_modules_import_cleanly():
    import alpha.workflow as wf
    import alpha.workflow.dynamic_assembler as a
    import alpha.workflow.dynamic_bridge as b
    import alpha.workflow.dynamic_decomposer as d
    import alpha.workflow.dynamic_perception as p

    for mod in (p, d, a, b):
        assert mod is not None

    # Package re-exports are additive and present
    for name in (
        "DynamicPerceptionEngine",
        "DynamicDecomposer",
        "DynamicResourceAssembler",
        "DynamicWorkflowBridge",
        "PerceivedIntent",
        "DynamicGoal",
        "AssembledResources",
        "DynamicExecutionResult",
    ):
        assert hasattr(wf, name), f"workflow package must export {name}"

    # The exact symbols that used to crash at import time
    assert "TASK" not in NodeType.__members__
    assert "COMPLETED" not in NodeStatus.__members__
    assert "ALWAYS" not in EdgeMode.__members__

    # RetryPolicy real fields construct with the values the decomposer chose
    rp = dynamic_decomposer.DynamicTaskItem(
        task_id="t", title="t", description="t", category="coding", assigned_role="r"
    ).retry_policy
    assert rp.max_attempts == 3
    assert rp.backoff == "exponential"
    assert rp.initial_delay_seconds == 1.5

    # Dataclass default node_type is a real member
    item = dynamic_decomposer.DynamicTaskItem(
        task_id="t", title="t", description="t", category="coding", assigned_role="r"
    )
    assert item.node_type is NodeType.AGENT


# ---------------------------------------------------------------------------
# 2. Decomposition → real-model-valid graph
# ---------------------------------------------------------------------------

def test_decompose_sample_intent_graph_validates_against_real_models():
    intent, goal = _sample_goal()
    assert goal.tasks, "sample intent must decompose into tasks"
    assert goal.execution_waves

    # Every synthesized task carries a REAL NodeType and a real RetryPolicy
    for task in goal.tasks:
        assert isinstance(task.node_type, NodeType)
        assert task.node_type in set(CATEGORY_NODE_TYPES.values()) | {NodeType.AGENT}
        assert isinstance(task.retry_policy, RetryPolicy)
        assert task.retry_policy.max_attempts >= 1

    # Compile through the real bridge, then validate the real graph
    resources = AssembledResources(goal_id=goal.goal_id, tools=["read_file"])
    definition = DynamicWorkflowBridge().build_workflow_definition(goal, resources)
    graph = definition.graph

    # (a) pydantic schema validator round-trip
    revalidated = WorkflowGraph.model_validate(graph.model_dump())
    assert set(revalidated.nodes) == set(graph.nodes)
    assert len(revalidated.edges) == len(graph.edges)

    # (b) real structural validators (add_node / add_edge raise on bad graphs)
    clone = WorkflowGraph(version=1, nodes={}, edges=[])
    for node in graph.nodes.values():
        clone.add_node(node.model_copy(deep=True))
    for edge in graph.edges:
        clone.add_edge(edge)
    with pytest.raises(ValueError):
        clone.add_node(WorkflowNode(id=next(iter(graph.nodes))))  # duplicate id
    with pytest.raises(ValueError):
        clone.add_edge(WorkflowEdge(source="no_such_node", target=next(iter(graph.nodes))))

    # (c) real Kahn wave computation: every node exactly once, deps before dependents
    waves = graph.get_executable_waves()
    flat = [nid for wave in waves for nid in wave]
    assert sorted(flat) == sorted(graph.nodes)
    order = {nid: i for i, wave in enumerate(waves) for nid in wave}
    for edge in graph.edges:
        assert order[edge.source] < order[edge.target], f"edge out of order: {edge}"

    # DY-R3 contract checks on the compiled graph
    assert all(edge.mode is EdgeMode.NORMAL for edge in graph.edges)
    task_nodes = [n for n in graph.nodes.values() if n.category != "compensation"]
    assert all("assigned_bot" in n.config and "inputs" in n.config for n in task_nodes)
    assert all(n.type in NodeType for n in graph.nodes.values())  # every type is a real member

    comp_nodes = [n for n in graph.nodes.values() if n.type is NodeType.COMPENSATION]
    assert comp_nodes, "goal declares saga compensations"
    for cnode in comp_nodes:
        assert cnode.config["is_compensation"] is True
        target_id = cnode.config["target_rollback_node"]
        assert target_id in graph.nodes
        assert graph.nodes[target_id].compensation_node_id == cnode.id


def test_bot_typed_task_binds_bot_or_downgrades_honestly():
    intent = PerceivedIntent(
        raw_prompt="assemble a bot swarm",
        intent_type=DynamicIntentType.ORCHESTRATE,
        primary_domain="bots",
        execution_tier=DynamicExecutionTier.MULTIAGENT_SWARM,
        need_subagents=True,
        need_bot_creation=True,
    )
    goal = DynamicDecomposer().decompose(intent, intent.raw_prompt)
    assert any(t.node_type is NodeType.BOT for t in goal.tasks)

    bridge = DynamicWorkflowBridge()
    # No assembled bot: BOT node downgrades to AGENT with an honest reason
    definition = bridge.build_workflow_definition(goal, AssembledResources(goal_id=goal.goal_id))
    node = definition.graph.nodes["task_04_bot_swarm_provisioning"]
    assert node.type is NodeType.AGENT
    assert "bot_downgrade_reason" in node.config

    # Matching bot bound: stays BOT with the real config["bot_name"] the engine reads
    resources = AssembledResources(
        goal_id=goal.goal_id,
        bots={"bot_probe_1": {"capabilities": ["bot_cloner", "bots"]}},
    )
    definition = bridge.build_workflow_definition(goal, resources)
    node = definition.graph.nodes["task_04_bot_swarm_provisioning"]
    assert node.type is NodeType.BOT
    assert node.config["bot_name"] == "bot_probe_1"


# ---------------------------------------------------------------------------
# 3. Perception — every emitted command exists in the real catalog
# ---------------------------------------------------------------------------

def test_perception_emits_only_real_catalog_commands():
    engine = DynamicPerceptionEngine()

    # Static contract: pattern table and emittable set are catalog-real
    assert set(engine.SLASH_COMMAND_PATTERNS) <= set(engine.EMITTABLE_COMMANDS)
    for cmd in engine.EMITTABLE_COMMANDS:
        assert cmd in CATALOG_COMMANDS, f"phantom command emitted: {cmd}"

    prompts = [
        "/plan my sprint",
        "fix this bug in the parser",
        "review the diff before merge",
        "research vector databases",
        "create skill for packaging",
        "spin up a multi-agent swarm",
        "boost the dynamic workflow process",
        "implement a small feature with tests",
        "survey and investigate options",
    ]
    for prompt in prompts:
        intent = engine.perceive(prompt)
        assert intent.detected_slash_command is None or intent.detected_slash_command in CATALOG_COMMANDS
        assert intent.suggested_slash_command is None or intent.suggested_slash_command in CATALOG_COMMANDS
        assert intent.detected_slash_command != "/boost"
        assert intent.suggested_slash_command != "/boost"

    # BOOST remains an internal intent classification, not a command suggestion
    boosted = engine.perceive("boost the dynamic workflow")
    assert boosted.intent_type is DynamicIntentType.BOOST


def test_perception_source_contains_no_phantom_command_literals():
    """Static scan: no string constant in the module names a non-catalog command."""
    source = Path(dynamic_perception.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    tokens: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            tokens.update(re.findall(r"/[a-z][a-z0-9\-]*", node.value))
    assert tokens, "expected slash-command literals in the perception module"

    def in_catalog(token: str) -> bool:
        return any(
            cmd == token or cmd.startswith(token + " ") for cmd in CATALOG_COMMANDS
        )

    phantoms = sorted(t for t in tokens if not in_catalog(t))
    assert phantoms == [], f"phantom command literals in dynamic_perception: {phantoms}"


# ---------------------------------------------------------------------------
# 4. Assembler — no fabrication, honest disclosure
# ---------------------------------------------------------------------------

_DISCOVERY_PROMPT = "author a skill for data analysis that uses mcp protocol"


def test_assembler_never_registers_mock_skill_and_discloses_honestly():
    hub = _RecordingSkillsHub(
        packages=[SkillPackage(name="real_discovery_tool", description="real tool", source="github")]
    )
    assembler = _make_assembler(hub, mcp_manager=None)

    intent, goal = _sample_goal(_DISCOVERY_PROMPT)
    assert intent.need_skill_creation and intent.need_mcp_selection

    resources = assembler.assemble(goal, _DISCOVERY_PROMPT)

    # (1) NOTHING was ever registered into the skills hub — no mock body, no pkg
    assert hub.added == []

    # Static proof: the module never calls add_to_catalog at all
    tree = ast.parse(Path(dynamic_assembler.__file__).read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add_to_catalog"
    ]
    assert calls == [], "dynamic_assembler must not register into the skills hub"

    # (2) Skills listed are only real hub discoveries
    assert resources.skills, "discovered skills should still be listed"
    assert all(e["status"] == "discovered_in_hub" for e in resources.skills)
    assert any(e["name"] == "real_discovery_tool" for e in resources.skills)

    # (3) Disclosure fields are honest
    assert resources.metadata["skill_generation"] == "unavailable — no generator bound"
    assert resources.metadata["mcp_generation"].startswith("unavailable")
    assert resources.mcp_servers == [], "no MCP servers may be fabricated"

    # (4) The old fabricated artifacts appear nowhere in the payload
    payload = json.dumps(resources.to_dict(), default=str)
    assert "dynamic tasks autonomously" not in payload
    assert "def run_skill" not in payload

    # (5) Bot registration and workgroup creation are real and template-labeled
    assert assembler.bot_registry.registered, "bot profiles must really be registered"
    for profile in assembler.bot_registry.registered:
        assert profile.metadata["generation_method"] == "template"
    for bot_payload in resources.bots.values():
        assert bot_payload["metadata"]["generation_method"] == "template"
    assert resources.swarm is not None
    assert sorted(resources.swarm.member_bots) == sorted(resources.bots.keys())


def test_assembler_mcp_disclosure_when_no_declared_specs(tmp_path):
    hub = _RecordingSkillsHub(installed_skills_dir=tmp_path / "skills", installed=[])
    assembler = _make_assembler(hub, mcp_manager=SkillMcpLifecycleManager())

    intent, goal = _sample_goal(_DISCOVERY_PROMPT)
    resources = assembler.assemble(goal, _DISCOVERY_PROMPT)

    assert resources.mcp_servers == []
    assert resources.metadata["mcp_generation"] == (
        "unavailable — no MCP server specs declared by installed skills"
    )


def test_assembler_acquires_only_declared_mcp_specs_from_real_seam(tmp_path):
    skills_root = tmp_path / "skills"
    demo_dir = skills_root / "demo"
    demo_dir.mkdir(parents=True)
    (demo_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo\n"
        "mcp-servers:\n"
        "  demo_mcp:\n"
        "    command: python\n"
        "    args: [\"-m\", \"demo.gateway\"]\n"
        "---\n"
        "# demo skill\n",
        encoding="utf-8",
    )
    hub = _RecordingSkillsHub(
        installed_skills_dir=skills_root, installed=[{"name": "demo"}]
    )
    assembler = _make_assembler(hub, mcp_manager=SkillMcpLifecycleManager())

    intent, goal = _sample_goal(_DISCOVERY_PROMPT)
    resources = assembler.assemble(goal, _DISCOVERY_PROMPT)

    # Real declared spec acquired through the real lifecycle seam
    assert "demo_mcp" in resources.mcp_servers
    assert resources.metadata["mcp_generation"] == "declared_in_installed_skills"


def test_assembler_reports_unbound_mcp_seam_honestly():
    hub = _RecordingSkillsHub()
    assembler = _make_assembler(hub, mcp_manager=None)  # unbound seam

    intent, goal = _sample_goal(_DISCOVERY_PROMPT)
    resources = assembler.assemble(goal, _DISCOVERY_PROMPT)

    assert resources.mcp_servers == []
    assert resources.metadata["mcp_generation"] == "unavailable — no MCP registry seam bound"


# ---------------------------------------------------------------------------
# 5. Bridge — stub runner records real execution; no runner = honest failure
# ---------------------------------------------------------------------------

def test_bridge_with_stub_runner_records_real_execution():
    intent, goal = _sample_goal()
    resources = AssembledResources(goal_id=goal.goal_id, tools=["read_file"])

    calls: list[str] = []

    def stub_runner(node, run):
        calls.append(node.id)
        return {
            "status": "completed",
            "output": {"ran": node.id},
            "evidence": f"stub runner executed '{node.id}'",
        }

    bridge = DynamicWorkflowBridge(node_runner=stub_runner)
    result = bridge.execute_goal(goal, resources)

    assert result.status == "completed", result.error_summary
    assert result.metadata["acceptance_passed"] is True
    assert calls, "stub runner must actually have executed nodes"
    # Every recorded completion came from the bound runner — no fabrication
    assert set(result.completed_nodes) == set(calls)
    assert all(result.node_outputs[nid] == {"ran": nid} for nid in result.completed_nodes)
    assert result.failed_nodes == []
    # Compensation was never claimed without execution
    assert result.compensated_nodes == []
    assert result.metadata["compensation"]["executed"] is False
    assert result.metadata["node_runner_bound"] is True


def test_bridge_without_runner_fails_honestly_never_fakes_success():
    intent, goal = _sample_goal()
    resources = AssembledResources(goal_id=goal.goal_id)

    result = DynamicWorkflowBridge(node_runner=None).execute_goal(goal, resources)

    assert result.status == "failed"
    assert result.completed_nodes == [], "no fabricated completions may be recorded"
    assert result.error_summary, "honest failure must carry the real reason"
    assert result.metadata["node_runner_bound"] is False
    assert result.metadata["acceptance_passed"] is False
    assert result.metadata["compensation"]["executed"] is False


# ---------------------------------------------------------------------------
# 6. Saga compensation — real seam only, honest refusal otherwise
# ---------------------------------------------------------------------------

def _compensation_fixture():
    intent, goal = _sample_goal()
    bridge = DynamicWorkflowBridge()
    definition = bridge.build_workflow_definition(goal, AssembledResources(goal_id=goal.goal_id))
    run = WorkflowRun(run_id="run_comp_test", workflow_id=definition.id)
    completed = [t.task_id for t in goal.tasks]
    return bridge, goal, definition.graph, run, completed


def test_saga_compensation_without_executor_refuses_honestly():
    bridge, goal, graph, run, completed = _compensation_fixture()
    compensated: list[str] = []
    error_parts: list[str] = []

    executed, reason = bridge._execute_saga_compensation(
        run, graph, goal, completed, compensated, error_parts
    )

    assert executed is False
    assert compensated == []
    assert "no compensation executor bound" in reason
    assert error_parts, "refusal must be surfaced in the error summary parts"
    for node in graph.nodes.values():
        if node.type is NodeType.COMPENSATION:
            assert node.status is NodeStatus.PENDING
            assert node.output is None


def test_saga_compensation_with_stub_executor_records_real_rollback():
    bridge, goal, graph, run, completed = _compensation_fixture()
    bridge.compensation_runner = lambda comp, _run: f"rolled back {comp.action_type}"
    compensated: list[str] = []
    error_parts: list[str] = []

    executed, reason = bridge._execute_saga_compensation(
        run, graph, goal, completed, compensated, error_parts
    )

    assert executed is True
    comp_nodes = [n for n in graph.nodes.values() if n.type is NodeType.COMPENSATION]
    assert compensated == [n.id for n in comp_nodes if n.status is NodeStatus.SUCCEEDED]
    assert compensated, "every declared compensation with a completed target executed"
    for node in comp_nodes:
        if node.id in compensated:
            assert node.output.startswith("rolled back ")
            assert any("compensation_runner executed" in ev for ev in node.evidence)
            assert run.node_states[node.id] is NodeStatus.SUCCEEDED
