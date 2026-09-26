"""A killed process resumes its work; a spent ceiling escalates instead.

Before this, only graph runs and model nodes had a crash-resume path
(``app.gateway.run_recovery`` + ``alpha.orchestrator.replay``). A swarm plan was
parked on restart and nothing ever resumed it; a subagent record survived on
disk with no attempt counter and no way back; a bot work unit had no durable
owner at all. These tests kill a REAL process mid-work and prove the survivors
are picked up by a fresh one, bounded by the same attempt ceiling.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import alpha.orchestrator.restart as restart_mod
import alpha.subagents.lifecycle as lifecycle_mod
import alpha.swarm.coordinator as swarm_coordinator
from alpha.bots.failure_reasons import ATTEMPTS_EXHAUSTED, WORKER_CRASH
from alpha.runtime.escalation import (
    DOMAIN_BOT,
    DOMAIN_SUBAGENT,
    DOMAIN_SWARM,
    HandoffLedger,
    get_handoff_ledger,
    get_work_unit_store,
    list_open_escalations,
    recover_bot_work,
    recover_incomplete_work,
    recover_swarm_work,
    register_resume_handler,
    reset_ledger_caches,
)
from alpha.subagents.lifecycle import SubagentLifecycleManager, SubagentStatusEnum

# The child does the work and then blocks. The parent kills it: no cooperative
# shutdown, no flush, no atexit hook -- a real crash.
CHILD_SCRIPT = """
import json, os, sys, time
from pathlib import Path

home = Path(os.environ["AGENT_WORKSPACE_HOME"])
ready = Path(sys.argv[1])

from alpha.runtime.escalation import get_work_unit_store, process_identity
from alpha.subagents.lifecycle import SubagentContract, SubagentLifecycleManager
from alpha.swarm.coordinator import SwarmCoordinator

# 1. subagent work, mid-flight, with a progress checkpoint written to disk
manager = SubagentLifecycleManager(storage_dir=home / "subagents")
record = manager.spawn_subagent("lead", SubagentContract(objective="Finish the migration", lease_duration_seconds=1, max_attempts=2))
manager.start_subagent(record.subagent_id)
manager.record_progress(record.subagent_id, next_action="run the dry-run migration", step_index=3, progress_percent=40.0)

# 2. a bot work unit owned by THIS process, with its payload and checkpoint
unit = get_work_unit_store().register(
    domain="bot",
    task_id="bot-task-1",
    resume_key="bot:daily-briefing",
    payload={"thread_id": "thread-1", "objective": "write the daily briefing"},
    checkpoint={"drafted_sections": 3},
    attempt=1,
    max_attempts=3,
    lease_seconds=1,
)

# 3. a swarm plan whose task this process had leased and never finished
coordinator = SwarmCoordinator(storage_dir=home / "swarms")
plan = coordinator.create_swarm("resume my swarm work", items=["alpha", "beta"])
root = next(t for t in plan.tasks.values() if not t.dependencies)
coordinator.claim_task(plan.swarm_id, root.task_id, owner="worker-child", lease_seconds=1)
node = coordinator.get_swarm(plan.swarm_id).tasks[root.task_id]
node.lease_expires_at = time.time() - 60
coordinator.checkpoint(plan.swarm_id)

ready.write_text(
    json.dumps(
        {
            "subagent_id": record.subagent_id,
            "unit_id": unit.unit_id,
            "owner": unit.owner,
            "swarm_id": plan.swarm_id,
            "swarm_task_id": root.task_id,
            "lease_expires_at": record.lease.expires_at,
            "pid": os.getpid(),
            "identity": process_identity(),
        }
    ),
    encoding="utf-8",
)

# Block forever so the parent can kill us mid-work.
while True:
    time.sleep(0.2)
"""


@pytest.fixture(autouse=True)
def _isolated_runtime_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("alpha.bots.registry._global_registry", None)
    monkeypatch.setattr("alpha.bots.registry._global_registry_path", None)
    monkeypatch.setattr(lifecycle_mod, "_GLOBAL_LIFECYCLE_MANAGER", None)
    monkeypatch.setattr(swarm_coordinator, "_GLOBAL_COORDINATOR", None)
    monkeypatch.setattr(restart_mod, "_HOOKS", [])
    monkeypatch.setattr(restart_mod, "_INSTALLED", False)
    reset_ledger_caches()
    import alpha.runtime.escalation as escalation_mod

    monkeypatch.setattr(escalation_mod, "_RESUME_HANDLERS", {})
    yield
    monkeypatch.setattr(lifecycle_mod, "_GLOBAL_LIFECYCLE_MANAGER", None)
    monkeypatch.setattr(swarm_coordinator, "_GLOBAL_COORDINATOR", None)
    reset_ledger_caches()


def _kill_a_working_child(tmp_path: Path) -> dict:
    """Start a child that does real work, then kill it. Returns its report.

    The venv interpreter on this host is a launcher that runs the real
    interpreter as a child, so killing the ``Popen`` handle alone can leave the
    process that actually did the work orphaned and spinning. Every process the
    child started is killed, which is what "the process died" means here.
    """
    script = tmp_path / "child_worker.py"
    script.write_text(CHILD_SCRIPT, encoding="utf-8")
    ready = tmp_path / "child_ready.json"
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen([sys.executable, str(script), str(ready)], env=env, cwd=str(tmp_path))

    # The child pays a full cold import of the harness (seconds, and much more
    # on a loaded host), so the bound is generous: it exists to turn a hang
    # into a failure, not to race a healthy start.
    deadline = time.time() + 600
    while time.time() < deadline:
        if ready.exists():
            break
        if process.poll() is not None:
            _kill_tree(process.pid)
            raise AssertionError(f"child exited early with code {process.returncode}")
        time.sleep(0.25)
    else:  # pragma: no cover - only on a pathologically slow host
        _kill_tree(process.pid)
        raise AssertionError("child never reported that it had started working")

    report = json.loads(ready.read_text(encoding="utf-8"))
    time.sleep(0.3)
    _kill_tree(process.pid)
    process.wait(timeout=60)
    assert process.returncode != 0, "the child was supposed to die, not exit cleanly"
    _wait_until_process_gone(report["owner"])
    return report


def _kill_tree(pid: int) -> None:
    """Terminate a process and everything it started, best effort."""
    import psutil

    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return
    targets = root.children(recursive=True) + [root]
    for target in targets:
        try:
            target.kill()
        except psutil.Error:  # noqa: PERF203 - best effort across the whole tree
            pass


def _wait_until_process_gone(owner: str) -> None:
    from alpha.runtime.escalation import _process_alive

    deadline = time.time() + 60
    while time.time() < deadline:
        if not _process_alive(owner):
            return
        time.sleep(0.05)
    raise AssertionError(f"process {owner} is still alive after being killed")


def _wait_until_dead_lease(expires_at: float) -> None:
    """Block until the crashed worker's lease has actually lapsed.

    A crash is not a magic instant: the lease is what proves the worker is
    gone, so recovery waits for the lease to lapse instead of guessing with a
    fixed sleep.
    """
    deadline = time.time() + 60
    while time.time() < deadline:
        if time.time() >= expires_at:
            return
        time.sleep(0.05)
    raise AssertionError("the crashed worker's lease never lapsed")


def test_a_killed_process_resumes_its_subagent_bot_and_swarm_work(tmp_path):
    """THE acceptance test: kill -9 mid-work, then recover from a fresh reader."""
    from alpha.runtime.escalation import _process_alive

    child = _kill_a_working_child(tmp_path)
    assert child["pid"] != os.getpid()
    # The owning process is genuinely gone, which is exactly the crash signal
    # the work-unit journal keys its recovery on.
    assert _process_alive(child["owner"]) is False
    # 1. The subagent: still RUNNING on disk, lease dead, checkpoint intact.
    manager = SubagentLifecycleManager(storage_dir=Path(os.environ["AGENT_WORKSPACE_HOME"]) / "subagents")
    stored = manager.get_subagent(child["subagent_id"])
    assert stored is not None
    assert stored.status == SubagentStatusEnum.RUNNING  # it died mid-flight
    assert stored.attempt == 1
    assert stored.progress["step_index"] == 3
    assert stored.progress["next_action"] == "run the dry-run migration"

    _wait_until_dead_lease(child["lease_expires_at"])
    summary = manager.recover_after_restart()
    assert summary["resumed"] == [child["subagent_id"]]
    resumed = manager.get_subagent(child["subagent_id"])
    assert resumed.status == SubagentStatusEnum.READY
    assert resumed.attempt == 1, "a crash must not be charged as a second attempt"
    assert resumed.progress["step_index"] == 3
    assert resumed.lease.renew_count >= 2  # the dead worker's renewals plus recovery's
    # ...and it can now actually be finished, on attempt 2.
    assert manager.start_subagent(child["subagent_id"]) is True
    assert manager.get_subagent(child["subagent_id"]).attempt == 2

    # 2. The bot work unit: the dead owner's unit is re-dispatched, payload intact.
    resumed_payloads: list[dict] = []
    register_resume_handler(DOMAIN_BOT, lambda unit: resumed_payloads.append({"unit": unit.unit_id, "task": unit.task_id, "payload": dict(unit.payload), "checkpoint": dict(unit.checkpoint), "attempt": unit.attempt}))
    bot_summary = recover_bot_work()
    assert bot_summary["resumed"] == 1
    assert resumed_payloads[0]["task"] == "bot-task-1"
    assert resumed_payloads[0]["payload"]["objective"] == "write the daily briefing"
    assert resumed_payloads[0]["checkpoint"]["drafted_sections"] == 3
    # The journal is terminal for that unit: a second sweep cannot double-dispatch.
    assert recover_bot_work()["resumed"] == 0
    assert get_work_unit_store().get(resumed_payloads[0]["unit"]).state == "resumed"

    # 3. The swarm: the plan the coordinator parked is resumed by a fresh reader.
    swarm_coordinator._GLOBAL_COORDINATOR = None
    swarm_summary = recover_swarm_work(start_runner=False)
    assert child["swarm_id"] in swarm_summary["resumed_plans"]
    plan = swarm_coordinator.get_swarm_coordinator().get_swarm(child["swarm_id"])
    assert plan.status == "running"
    assert plan.terminal_reason is None
    task = plan.tasks[child["swarm_task_id"]]
    assert task.state.value == "pending"
    assert task.lease_id is None
    assert task.attempts == 1

    # 4. One ordered ledger shows the whole recovery story.
    entries = get_handoff_ledger().entries()
    assert [e.seq for e in entries] == list(range(1, len(entries) + 1))
    kinds = {(e.domain, e.kind) for e in entries}
    assert (DOMAIN_SUBAGENT, "resume") in kinds
    assert (DOMAIN_BOT, "resume") in kinds
    assert (DOMAIN_SWARM, "resume") in kinds
    for entry in entries:
        assert entry.reason == WORKER_CRASH
        assert entry.from_ref
        assert entry.to_ref
    # A task-level resume carries the attempt it is resuming; the plan-level
    # resume is a scope marker and has no attempt of its own.
    task_resumes = [e for e in entries if e.details.get("scope") != "plan"]
    assert task_resumes
    assert all(e.attempt >= 1 for e in task_resumes)
    assert [e.attempt for e in entries if e.domain == DOMAIN_SWARM and e.details.get("scope") == "plan"] == [0]
    subagent_resume = get_handoff_ledger().latest(domain=DOMAIN_SUBAGENT, kind="resume")
    assert subagent_resume.details["checkpoint"]["step_index"] == 3


def test_recovery_survives_a_hard_kill_for_work_units_whose_ceiling_is_spent(tmp_path):
    """A unit whose process died with no attempts left escalates, never re-runs."""
    # The singleton accessors, exactly as production callers use them: one store
    # instance per process per root.
    store = get_work_unit_store()
    unit = store.register(
        domain=DOMAIN_BOT,
        task_id="bot-task-doomed",
        resume_key="bot:doomed",
        payload={"objective": "never finishes"},
        attempt=3,
        max_attempts=3,
        lease_seconds=0.01,
    )
    time.sleep(0.1)

    handled: list[str] = []
    register_resume_handler(DOMAIN_BOT, lambda u: handled.append(u.unit_id))
    summary = recover_bot_work()
    assert summary["resumed"] == 0
    assert summary["escalated"] == 1
    assert handled == [], "an exhausted unit must not be re-dispatched"
    assert store.get(unit.unit_id).state == "escalated"

    escalations = list_open_escalations(domain=DOMAIN_BOT)
    assert len(escalations) == 1
    assert escalations[0].task_id == "bot-task-doomed"
    assert escalations[0].reason == ATTEMPTS_EXHAUSTED
    assert escalations[0].attempt == 3
    assert escalations[0].max_attempts == 3
    assert escalations[0].to_ref == "human"
    assert get_handoff_ledger().latest(domain=DOMAIN_BOT, kind="escalation") is not None

    # And the sweep is idempotent: running again pages nobody a second time.
    assert recover_bot_work()["escalated"] == 0
    assert len(list_open_escalations(domain=DOMAIN_BOT)) == 1


def test_full_restart_sweep_covers_every_domain_and_isolates_failures(monkeypatch):
    import alpha.runtime.escalation as escalation_mod

    calls: list[str] = []
    monkeypatch.setattr(escalation_mod, "recover_swarm_work", lambda **kw: (calls.append("swarm"), {"resumed_tasks": []})[1])
    monkeypatch.setattr(escalation_mod, "recover_subagent_work", lambda **kw: (calls.append("subagent"), {"resumed": []})[1])
    monkeypatch.setattr(escalation_mod, "recover_bot_work", lambda **kw: (calls.append("bot"), {"resumed": 0})[1])

    def _explode(**kwargs):
        calls.append("work_units")
        raise RuntimeError("journal unreadable")

    monkeypatch.setattr(escalation_mod, "resume_pending_work", _explode)
    report = recover_incomplete_work()
    assert calls == ["swarm", "subagent", "bot", "work_units"]
    assert "errors" in report["domains"]["work_units"]
    assert report["domains"]["swarm"] == {"resumed_tasks": []}
    assert "open_escalations" in report


def test_restart_hooks_are_idempotent_and_run_the_recovery_sweep(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(restart_mod, "run_restart_recovery", lambda **kw: (calls.append(kw), {"domains": {}})[1])

    assert restart_mod.install_restart_hooks() is None
    first = len(restart_mod.registered_hooks())
    restart_mod.install_restart_hooks()
    assert len(restart_mod.registered_hooks()) == first == 1

    report = restart_mod.run_restart_hooks()
    assert len(calls) == 1
    assert [h["ok"] for h in report["hooks"]] == [True]

    # A broken hook is reported, and the others still run.
    restart_mod.register_restart_hook(lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    report = restart_mod.run_restart_hooks()
    assert [h["ok"] for h in report["hooks"]] == [True, False]
    assert "RuntimeError" in report["hooks"][1]["error"]


def test_ledger_and_escalation_survive_a_restart_of_the_reader(tmp_path):
    """Durability across readers: a new store instance sees the same records."""
    ledger = get_handoff_ledger()
    ledger.append(kind="escalation", domain=DOMAIN_SWARM, task_id="t1", from_ref="w", to_ref="human", reason=ATTEMPTS_EXHAUSTED, attempt=3, max_attempts=3)

    reopened = HandoffLedger(ledger.root)
    entries = reopened.entries(kind="escalation")
    assert len(entries) == 1
    assert entries[0].reason == ATTEMPTS_EXHAUSTED
    assert entries[0].attempt == 3

    from alpha.runtime.escalation import EscalationStore, get_escalation_store

    record = get_escalation_store().open_escalation(domain=DOMAIN_SWARM, task_id="t1", from_ref="w", reason=ATTEMPTS_EXHAUSTED, attempt=3, max_attempts=3)
    again = EscalationStore(reopened.root).list(status="open")
    assert [r.escalation_id for r in again] == [record.escalation_id]
