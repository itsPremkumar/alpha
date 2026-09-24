"""``run_turn`` + default-kernel seam tests (P1 kernel, section 6 module seam).

Complements ``test_orchestrator_kernel.py`` (claim/dispatch/handoff) with the
module-level entry points that file does not exercise:

- ``get_default_kernel``/``set_default_kernel`` — the host/test injection seam:
  ``run_turn`` WITHOUT an explicit ``kernel=`` resolves through it, and a
  ``None`` reset yields a fresh default instead of a polluted singleton;
- unbound honesty THROUGH ``run_turn`` in BOTH modes (normal + bot): an empty
  registry plus an unbound ``alpha.workflow.runtime`` module seam must fail the
  run closed with the engine's exact ``no node_runner bound to execute node
  'direct' (kind=...)`` refusal — never a fabricated completion;
- the swarm refusal carries the mode mapper's VERBATIM
  ``NON_EXPRESSIBLE_REASONS['swarm']`` text and starts no run;
- a mode outside ``MODES`` raises before any run exists (fail-closed input).

Seam discipline: every test swaps ``executors._REGISTRY`` (and, for the unbound
world, ``runtime._NODE_RUNNER``) via ``monkeypatch`` so bound and unbound
realms can never leak into each other or into other suites.
"""

from __future__ import annotations

import pytest

import alpha.orchestrator.executors as executors_module
import alpha.workflow.runtime as runtime_module
from alpha.orchestrator.executors import DIGEST_EXECUTOR, ExecutorRegistry
from alpha.orchestrator.loop import (
    ExecutionKernel,
    TurnContext,
    get_default_kernel,
    run_turn,
    set_default_kernel,
)
from alpha.orchestrator.mode_mapper import NON_EXPRESSIBLE_REASONS
from alpha.workflow.models import WorkflowRunStatus
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


def _reset_default_kernel() -> None:
    """Leave the process-wide seam exactly as it was found (None)."""
    set_default_kernel(None)


# ------------------------------------------------------- default kernel seam


def test_default_kernel_seam_injects_and_resets(registry):
    """``set_default_kernel`` is honored by ``run_turn``'s implicit resolution."""
    _bind_digest(registry)
    injected = _bare_kernel()
    set_default_kernel(injected)
    try:
        assert get_default_kernel() is injected

        # No ``kernel=`` argument: the seam MUST be what executes this turn.
        outcome = run_turn("task through the default seam", TurnContext(paradigm="direct_agent"))

        assert outcome.status == "completed"
        assert outcome.run_id is not None
        run = injected.engine.get_run(outcome.run_id)
        assert run is not None
        assert run.status == WorkflowRunStatus.COMPLETED
        # The mode mapper registered the paradigm's definition on the INJECTED
        # kernel's engine — proof the seam, not some private default, ran it.
        assert injected.engine.get_definition(outcome.workflow_id) is not None
    finally:
        _reset_default_kernel()

    fresh = get_default_kernel()
    assert fresh is not injected
    assert isinstance(fresh, ExecutionKernel)
    _reset_default_kernel()


# ------------------------------------------------------- unbound honesty (2 modes)


@pytest.mark.parametrize("mode,expected_kind", [("normal", "agent"), ("bot", "bot")])
def test_run_turn_unbound_fails_honestly_in_both_modes(registry, monkeypatch, mode, expected_kind):
    """Empty registry + unbound module seam: the exact engine refusal, per mode."""
    monkeypatch.setattr(runtime_module, "_NODE_RUNNER", None)
    assert registry.build_runner() is None  # nothing bound anywhere

    kernel = _bare_kernel()
    outcome = run_turn("do the real work", TurnContext(mode=mode, paradigm="direct_agent"), kernel=kernel)

    # Honest failure: a run exists and it FAILED — never a fabricated success.
    assert outcome.status == "failed"
    assert outcome.run_id is not None
    assert outcome.failed_nodes == ("direct",)
    assert outcome.waves == 1  # wave 1 fails -> the engine fail-closes, no more dispatch

    run = kernel.engine.get_run(outcome.run_id)
    assert run is not None
    assert run.status == WorkflowRunStatus.FAILED
    assert run.completed_nodes == []
    assert run.metrics["execution_mode"] == mode
    # No state artifact was invented for the never-executed node.
    assert not [k for k in run.state if k.startswith("direct_")]

    # The engine's exact refusal is journaled, carrying the mode's node kind.
    events = kernel.engine.events.get_events(outcome.run_id)
    node_failed = [e for e in events if e.event_type == "node_failed"]
    assert node_failed, "the honest refusal must surface as an event"
    reason = str(node_failed[-1].payload.get("reason", ""))
    assert f"no node_runner bound to execute node 'direct' (kind={expected_kind})" in reason

    # The handoff contract reports the same failure — no invented findings.
    handoff = outcome.handoff
    assert handoff is not None
    assert handoff.status == "failed"
    assert handoff.mode_from == mode
    assert handoff.completed == []
    assert handoff.findings == []
    assert handoff.remaining == ["direct"]
    assert handoff.files == [] and handoff.decisions == []


# ------------------------------------------------------------ honest refusals


def test_run_turn_swarm_refusal_is_the_literal_mapper_reason(registry):
    """Swarm: the VERBATIM module constant, no run, no handoff, no log noise."""
    kernel = _bare_kernel()

    outcome = run_turn("spin up a self-organizing swarm", TurnContext(paradigm="swarm"), kernel=kernel)

    assert outcome.status == "not_expressible"
    assert outcome.run_id is None
    assert outcome.handoff is None
    assert outcome.reason == NON_EXPRESSIBLE_REASONS["swarm"]
    assert kernel.engine.runs == {}, "a refused paradigm must not start a run"
    assert kernel.engine.definitions == {}, "a refused paradigm must not register a graph"


def test_run_turn_invalid_mode_refuses_before_any_run(registry):
    """Mode outside ``MODES``: ValueError before a definition or run exists."""
    kernel = _bare_kernel()

    with pytest.raises(ValueError, match="mode must be one of"):
        run_turn("chaos mode task", TurnContext(mode="chaos"), kernel=kernel)

    assert kernel.engine.runs == {}
    assert kernel.engine.definitions == {}
