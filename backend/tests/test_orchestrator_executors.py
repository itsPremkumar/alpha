"""Executor-registry unit tests (P1 kernel, DY-R1 honesty carried upward).

Pins the honesty semantics of ``alpha.orchestrator.executors``:

- an empty registry builds NO runner (the engine's exact unbound-refusal
  world); this module never binds the ``alpha.workflow.runtime`` module seam;
- the digest executor does genuine, independently recomputable sha256 work
  with a real zero token accounting;
- unresolvable / raising / malformed executors produce honest FAILED results
  naming the real problem — including the traceback captured at the raise
  site — never a fabricated completion;
- ``bind_default_executors`` binds exactly the digest executor; the vote
  executor stays unbound on purpose (a local process cannot honestly cast
  model votes, so no ballots are ever invented);
- the registry stays consistent under concurrent registration.
"""

from __future__ import annotations

import hashlib
import threading

import pytest

import alpha.orchestrator.executors as executors_module
from alpha.orchestrator.executors import (
    DIGEST_EXECUTOR,
    VOTE_EXECUTOR,
    ExecutorRegistry,
    digest_input,
    local_digest_executor,
)
from alpha.workflow.models import WorkflowNode, WorkflowRun


@pytest.fixture(autouse=True)
def _isolate_agent_workspace(tmp_path, monkeypatch):
    """AGENT_WORKSPACE_HOME points at a per-test temp dir (process-global env)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def _run(run_id: str = "run_unit") -> WorkflowRun:
    return WorkflowRun(run_id=run_id, workflow_id="wf_unit")


def _completed(node: WorkflowNode, run: WorkflowRun) -> dict:
    return {"status": "completed", "output": f"did:{node.id}", "evidence": f"ev:{node.id}", "tokens_used": 0}


# ---------------------------------------------------------------- registry


def test_registry_register_resolve_names_and_unregister():
    registry = ExecutorRegistry()
    assert registry.names() == ()
    assert registry.build_runner() is None  # empty registry => engine's unbound world

    registry.register("ext.one", _completed)
    assert registry.names() == ("ext.one",)
    node = WorkflowNode(id="n1", executor="ext.one")
    resolved = registry.resolve(node)
    assert resolved is not None
    assert resolved[0] == "ext.one"
    assert resolved[1] is _completed
    assert registry.build_runner() is not None

    # Re-registration by key is idempotent (last binding wins, no dupes).
    registry.register("ext.one", _completed)
    assert registry.names() == ("ext.one",)

    assert registry.unregister("ext.one") is True
    assert registry.unregister("ext.one") is False
    assert registry.names() == ()
    assert registry.build_runner() is None


def test_registry_rejects_empty_name():
    with pytest.raises(ValueError, match="non-empty"):
        ExecutorRegistry().register("", _completed)


def test_registry_clear_unbinds_everything():
    registry = ExecutorRegistry()
    registry.register("ext.a", _completed)
    registry.register("ext.b", _completed)
    assert len(registry.names()) == 2

    registry.clear()
    assert registry.names() == ()
    assert registry.build_runner() is None


def test_resolve_of_unbound_node_returns_none():
    registry = ExecutorRegistry()
    registry.register("ext.one", _completed)
    assert registry.resolve(WorkflowNode(id="n1", executor="alpha.tool")) is None  # default executor, unbound


def test_get_executor_registry_returns_the_live_module_seam(monkeypatch):
    fresh = ExecutorRegistry()
    monkeypatch.setattr(executors_module, "_REGISTRY", fresh)
    assert executors_module.get_executor_registry() is fresh


def test_registry_registers_safely_under_concurrency():
    registry = ExecutorRegistry()
    errors: list[BaseException] = []

    def writer(worker: int) -> None:
        try:
            for idx in range(20):
                registry.register(f"ext.{worker}.{idx}", _completed)
        except BaseException as exc:  # noqa: BLE001 - surfaced via assert below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(registry.names()) == 8 * 20


# ------------------------------------------------------------------ digest


def test_digest_executor_is_deterministic_and_recomputable():
    node = WorkflowNode(id="n1", prompt="Do the thing")
    run = _run("run_abc")

    first = local_digest_executor(node, run)
    second = local_digest_executor(node, run)
    assert first == second

    # Independently recomputable from the documented formula:
    # sha256(run_id + "\n" + node_id + "\n" + prompt)
    expected = hashlib.sha256(b"run_abc\nn1\nDo the thing").hexdigest()
    assert digest_input(node, run) == b"run_abc\nn1\nDo the thing"
    assert first["output"] == {"executor": DIGEST_EXECUTOR, "node_id": "n1", "sha256": expected}
    assert first["evidence"] == f"{DIGEST_EXECUTOR} sha256={expected} over run_id+node_id+node.prompt"
    assert first["status"] == "completed"
    # Real accounting: local hashing consumed zero model tokens.
    assert first["tokens_used"] == 0

    # Different run identity -> different digest (evidence is never canned).
    other = local_digest_executor(node, _run("run_xyz"))
    assert other["output"]["sha256"] != expected


def test_digest_handles_missing_prompt_as_empty():
    node = WorkflowNode(id="n1")  # prompt defaults to None
    result = local_digest_executor(node, _run("run_abc"))
    expected = hashlib.sha256(b"run_abc\nn1\n").hexdigest()
    assert result["output"]["sha256"] == expected


# ---------------------------------------------------- failure honesty paths


def test_unresolvable_executor_fails_honestly_naming_the_gap():
    registry = ExecutorRegistry()
    registry.register("ext.other", _completed)
    runner = registry.build_runner()
    assert runner is not None

    node = WorkflowNode(id="n1")  # default executor "alpha.tool" is NOT bound
    result = runner(node, _run())

    assert result["status"] == "failed"
    assert result["output"] == "no executor registered for node 'n1' (executor='alpha.tool', kind=tool)"
    assert result["evidence"] == ""
    assert result["tokens_used"] == 0


def test_raising_executor_carries_the_real_traceback():
    def exploding(node, run):
        raise ZeroDivisionError("division by zero in ledger")

    registry = ExecutorRegistry()
    registry.register("ext.explode", exploding)
    runner = registry.build_runner()
    assert runner is not None

    result = runner(WorkflowNode(id="n1", executor="ext.explode"), _run())

    assert result["status"] == "failed"
    output = result["output"]
    assert "executor 'ext.explode' raised ZeroDivisionError: division by zero in ledger" in output
    assert "Traceback (most recent call last)" in output
    assert 'File "' in output  # a real captured frame
    assert "ZeroDivisionError" in output
    assert result["evidence"] == "" and result["tokens_used"] == 0


def test_malformed_executor_results_fail_honestly():
    # Not a dict at all.
    registry_str = ExecutorRegistry()
    registry_str.register("ext.str", lambda node, run: "all done, trust me")
    result_str = registry_str.build_runner()(WorkflowNode(id="n1", executor="ext.str"), _run())
    assert result_str["status"] == "failed"
    assert "invalid result" in result_str["output"]
    assert "'all done, trust me'" in result_str["output"]

    # Dict without a recognised status: never coerced to success.
    registry_bad = ExecutorRegistry()
    registry_bad.register("ext.bad", lambda node, run: {"status": "ok"})
    result_bad = registry_bad.build_runner()(WorkflowNode(id="n1", executor="ext.bad"), _run())
    assert result_bad["status"] == "failed"
    assert "invalid result" in result_bad["output"]
    assert "expected a dict with status 'completed' or 'failed'" in result_bad["output"]


def test_runner_reporting_failed_passes_through_honestly():
    def failing(node, run):
        return {"status": "failed", "output": "provider timeout", "evidence": "", "tokens_used": 0}

    registry = ExecutorRegistry()
    registry.register("ext.fail", failing)
    result = registry.build_runner()(WorkflowNode(id="n1", executor="ext.fail"), _run())
    assert result == {"status": "failed", "output": "provider timeout", "evidence": "", "tokens_used": 0}


# ---------------------------------------------------------- default binding


def test_bind_default_executors_binds_exactly_the_digest(monkeypatch):
    fresh = ExecutorRegistry()
    monkeypatch.setattr(executors_module, "_REGISTRY", fresh)

    bound = executors_module.bind_default_executors()
    assert bound is fresh
    assert fresh.names() == (DIGEST_EXECUTOR,)
    # The vote executor is deliberately NOT bound: no fabricated ballots.
    assert VOTE_EXECUTOR not in fresh.names()
    assert fresh.resolve(WorkflowNode(id="n", executor=DIGEST_EXECUTOR)) is not None
    assert fresh.resolve(WorkflowNode(id="n", executor=VOTE_EXECUTOR)) is None

    # Idempotent: binding twice changes nothing.
    executors_module.bind_default_executors()
    assert fresh.names() == (DIGEST_EXECUTOR,)
