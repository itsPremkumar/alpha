"""Swarm workers must execute for real — no canned summaries (Stage 3).

Before the fix every backend echoed the objective back as its "summary",
fabricated evidence (``confidence: 0.95`` / ``verified: True``), recorded a
model it never called, and returned ``success`` unconditionally — even with
no chat models configured. Every assertion here has teeth against that exact
behavior.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import alpha.swarm.worker as worker_mod
from alpha.swarm.coordinator import SwarmCoordinator
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState
from alpha.swarm.runner import AsyncSwarmRunner


@pytest.fixture(autouse=True)
def _isolated_worker_home(tmp_path, monkeypatch):
    """Keep BotRegistry writes inside the test home (<home>/bots)."""
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    import alpha.bots.registry as bot_reg

    monkeypatch.setattr(bot_reg, "_global_registry", None)
    monkeypatch.setattr(bot_reg, "_global_registry_path", None)
    yield


class _Message:
    def __init__(self, content):
        self.content = content


class _FakeModel:
    model_name = "stub-swarm-model"

    def __init__(self, content):
        self.content = content
        self.seen: list = []

    def invoke(self, messages):
        self.seen.append(messages)
        return _Message(self.content)


def _plan() -> SwarmPlan:
    return SwarmPlan(swarm_id="swm-worker-real", goal="Ship the feature", mode=SwarmMode.PARALLEL)


def _task(task_id: str = "task-1") -> SwarmTaskNode:
    return SwarmTaskNode(task_id=task_id, objective="Inspect the flaky test")


def test_specialist_bot_reports_real_model_output(monkeypatch):
    fake = _FakeModel("ACTUAL-MODEL-OUTPUT")
    monkeypatch.setattr(worker_mod, "_resolve_model", lambda model_name, *, worker_label: fake)

    worker = worker_mod.SpecialistBotWorker("qa-analyst")
    outcome = worker.execute_task(_task(), _plan())

    # Teeth: the old code returned an f-string echo of the objective.
    assert outcome["summary"] == "ACTUAL-MODEL-OUTPUT"
    assert outcome["status"] == "success"
    # Teeth: confidence 0.95 was invented; evidence now records what ran.
    evidence = outcome["evidence"][0]
    assert "confidence" not in evidence
    assert evidence["model"] == "stub-swarm-model"
    assert evidence["source"] == "bot:qa-analyst"

    # The model really received soul+goal (system) and objective (human).
    system_msg, human_msg = fake.seen[0]
    assert "Ship the feature" in system_msg.content
    assert "Inspect the flaky test" in human_msg.content


def test_ephemeral_worker_honors_model_override(monkeypatch):
    fake = _FakeModel("EPHEMERAL-REAL-OUTPUT")
    seen_names: list = []

    def _resolve(model_name, *, worker_label):
        seen_names.append(model_name)
        return fake

    monkeypatch.setattr(worker_mod, "_resolve_model", _resolve)

    worker = worker_mod.EphemeralSubagentWorker("worker-1", model="custom-model")
    outcome = worker.execute_task(_task(), _plan())

    # Teeth: model_override used to be recorded as evidence but never used.
    assert seen_names == ["custom-model"]
    assert outcome["summary"] == "EPHEMERAL-REAL-OUTPUT"
    evidence = outcome["evidence"][0]
    # Teeth: `verified: True` was fabricated.
    assert "verified" not in evidence
    assert evidence["worker_id"] == "worker-1"
    assert evidence["model"] == "stub-swarm-model"


def test_specialist_subagent_worker_flags_action_claims_without_receipt_citations(monkeypatch):
    from alpha.subagents import executor as executor_mod

    completed = object()
    monkeypatch.setattr(executor_mod, "SubagentStatus", SimpleNamespace(COMPLETED=completed))
    summary = (
        "I reviewed the implementation and found four issues in schema_deferral.py:123, "
        "lead_subagent_wiring.py:456, subagent_wiring.py:789, and lead_wiring.py:234. "
        "I inspected error handling, data consistency, and logging behavior, then prepared "
        "the suggested fixes and confirmed the affected code paths."
    )

    class _FakeExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute(self, _prompt):
            return SimpleNamespace(
                status=completed,
                result=summary,
                stop_reason=None,
                error=None,
                token_usage_records=[],
                tool_receipts=[],
                bash_executions=[],
            )

    monkeypatch.setattr(executor_mod, "SubagentExecutor", _FakeExecutor)
    worker = worker_mod.SpecialistSubagentWorker("tester", tool_pool=[object()])

    outcome = worker.execute_task(_task(), _plan())

    audit = outcome["citation_audit"]
    assert audit["citation_resolved"] is False
    assert audit["no_citation_claims"] is True
    assert audit["cited"] == []


def test_default_model_is_not_a_made_up_alias():
    # Teeth: the old default was "fast-model", a name resolvable nowhere else.
    assert worker_mod.EphemeralSubagentWorker("worker-x").model is None


def test_model_label_never_claims_a_default_nobody_measured():
    """The label feeds governor ``models_used`` — the latest rung of the
    fallback used to answer ``"default"``, which borrows the deployment's
    default-model identity for a lookup nobody performed. Object names win;
    the requested override is then honest (it names what was asked to run);
    past that the only truthful word is ``unknown``."""

    class _Unnamed:
        pass

    named = worker_mod._model_label(object, None)  # neither model_name nor model attribute
    assert named == "unknown", "an unnameable model must not be reported as 'default'"

    class _HasModel:
        model = "union-alpha"

    assert worker_mod._model_label(_HasModel(), None) == "union-alpha"
    assert worker_mod._model_label(None, "union-alpha") == "union-alpha"


def test_worker_without_models_fails_loudly(monkeypatch):
    # Teeth: with zero models configured the old code still returned
    # status=success plus fabricated evidence.
    monkeypatch.setattr("alpha.config.get_app_config", lambda: SimpleNamespace(models=[]))
    worker = worker_mod.EphemeralSubagentWorker("worker-no-models")
    with pytest.raises(RuntimeError, match="No chat models"):
        worker.execute_task(_task(), _plan())


def test_worktree_worker_applies_model_authored_patch(tmp_path, monkeypatch):
    import subprocess

    fake_wt = tmp_path / "wt-task-w1"
    fake_wt.mkdir()
    subprocess.run(["git", "init", str(fake_wt)], check=True, capture_output=True)
    (fake_wt / "thing.py").write_text("old\n", encoding="utf-8")

    class _FakeWorktreeManager:
        def __init__(self, repo_root=None, **kwargs):
            pass

        def create_worktree(self, *, branch_name, base_ref="HEAD"):
            return SimpleNamespace(path=fake_wt)

        def run_in_worktree(self, path, args, *, check=False):
            return subprocess.run(
                ["git", "-C", str(path), *args],
                capture_output=True,
                text=True,
                check=check,
            )

    monkeypatch.setattr(worker_mod, "WorktreeManager", _FakeWorktreeManager)
    diff = "--- a/thing.py\n+++ b/thing.py\n@@ -1 +1 @@\n-old\n+new\n"
    fake = _FakeModel(diff)
    monkeypatch.setattr(worker_mod, "_resolve_model", lambda model_name, *, worker_label: fake)

    worker = worker_mod.CodingWorktreeWorker(repo_root=".", branch_name="wt-task-w1")
    outcome = worker.execute_task(_task("task-wt-1"), _plan())

    patch_file = fake_wt / "task-wt-1.patch"
    assert (fake_wt / "thing.py").read_text(encoding="utf-8") == "new\n"
    assert not patch_file.exists(), "the successfully applied patch should not remain as a stale artifact"
    assert str(patch_file) not in outcome["artifacts"]
    assert "applied via git apply" in outcome["summary"]
    assert outcome["worktree_path"] == str(fake_wt)
    assert outcome["evidence"][0]["patch_file"] == str(patch_file)
    assert outcome["evidence"][0]["applied"] is True


@pytest.mark.asyncio
async def test_worker_exception_marks_plan_failed(tmp_path, monkeypatch):
    """A worker that cannot run (here: no models) must surface as an honest
    task/plan failure through the runner — never a silent success."""
    import asyncio

    def _boom(model_name, *, worker_label):
        raise RuntimeError("No chat models configured; ephemeral worker cannot execute its task objective.")

    monkeypatch.setattr(worker_mod, "_resolve_model", _boom)

    coord = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = SwarmPlan(
        swarm_id="swm-worker-fail",
        goal="Cannot run without models",
        mode=SwarmMode.PARALLEL,
        status="running",
        tasks={"task-1": SwarmTaskNode(task_id="task-1", objective="o")},
    )
    coord._swarms[plan.swarm_id] = plan

    runner = AsyncSwarmRunner(coord, poll_interval=0.01)
    result = await asyncio.wait_for(runner.run_swarm_async(plan.swarm_id), timeout=20)

    assert result["status"] == "failed"
    assert plan.status == "failed"
    task = plan.tasks["task-1"]
    assert task.state == TaskNodeState.FAILED
    assert "No chat models" in (task.error_message or "")
    event_types = [e.event_type for e in coord.get_events(plan.swarm_id, limit=50)]
    assert "TASK_FAILED" in event_types
