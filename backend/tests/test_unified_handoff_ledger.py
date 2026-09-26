"""ONE globally-ordered handoff ledger spanning agent/bot/subagent/swarm/batch.

Alpha kept a separate, mutually invisible record per kind of work transfer
(project handoffs JSON, the in-memory bot ``TaskHandoffPackage``, swarm plan
snapshots, batch rows, ephemeral bot leases). These tests pin the replacement:
every transfer lands in one append-only JSONL ledger with a global sequence and
carries ``(from, to, reason, attempt, timestamp)``.
"""

from __future__ import annotations

import json
import time

import pytest

import alpha.projects.handoffs as project_handoffs
import alpha.swarm.coordinator as swarm_coordinator
from alpha.bots.handoff import execute_handoff
from alpha.bots.registry import BotRegistry
from alpha.runtime.escalation import (
    DOMAIN_AGENT,
    DOMAIN_BATCH,
    DOMAIN_BOT,
    DOMAIN_SENTINEL,
    DOMAIN_SUBAGENT,
    DOMAIN_SWARM,
    HandoffLedger,
    record_failure,
    record_handoff,
    reset_ledger_caches,
)
from alpha.subagents.lifecycle import SubagentContract, SubagentLifecycleManager


@pytest.fixture(autouse=True)
def _isolated_runtime_home(tmp_path, monkeypatch):
    """Point every runtime-owned store at a private directory per test."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("alpha.bots.registry._global_registry", None)
    monkeypatch.setattr("alpha.bots.registry._global_registry_path", None)
    project_handoffs._stores.clear()
    swarm_coordinator._GLOBAL_COORDINATOR = None
    reset_ledger_caches()
    yield
    project_handoffs._stores.clear()
    swarm_coordinator._GLOBAL_COORDINATOR = None
    reset_ledger_caches()


def test_handoff_emits_from_to_reason_attempt_in_one_ordered_log(tmp_path):
    """The acceptance test: one ordered log, not five private records."""
    from alpha.bots.failure_reasons import CAPABILITY_MISSING, HANDOFF_REQUESTED, SUCCESSION_FALLBACK
    from alpha.runtime.escalation import get_handoff_ledger, list_open_escalations

    ledger = get_handoff_ledger()
    registry = BotRegistry()

    # 1. project/agent handoff (the project store, not the bot package)
    from alpha.projects.handoffs import get_handoff_store

    store = get_handoff_store("proj-ledger")
    project_record = store.create(
        task_id="task-alpha",
        from_bot="architect",
        to_bot="coder",
        objective="Implement the auth router",
        completed_work="Spec is written",
    )

    # 2. bot handoff straight through the bot protocol
    execute_handoff(
        task_id="task-beta",
        from_bot="coder",
        to_bot="reviewer",
        objective="Review the auth router",
        attempt=1,
        max_attempts=3,
        registry=registry,
    )

    # 3. subagent work that moved because of a capability gap
    disposition = record_failure(
        DOMAIN_SUBAGENT,
        "sub-42",
        "subagent:sub-42",
        attempt=1,
        max_attempts=3,
        error="no such tool: postgres_execute",
        successor="subagent:sub-99",
    )
    assert disposition.decision.reason == CAPABILITY_MISSING
    assert disposition.decision.action == "reassign"

    # 4. a swarm task resumed after a crash
    from alpha.runtime.escalation import record_resume

    record_resume(
        DOMAIN_SWARM,
        "task-map-1",
        from_ref="swarm:swm-1:worker-x",
        to_ref="host:1",
        attempt=1,
        max_attempts=3,
        checkpoint={"step_index": 7},
    )

    # 5. a human escalation, and 6. a sentinel escalation
    escalate = record_failure(
        DOMAIN_BATCH,
        "batch-item-1",
        "batch:b-1:general-purpose",
        attempt=3,
        max_attempts=3,
        error="429 rate limit exceeded",
    )
    assert escalate.escalated
    record_handoff(DOMAIN_SENTINEL, "fp-1", "sentinel:logs", "human", reason="unknown", attempt=0, max_attempts=0)

    entries = ledger.entries()
    # --- one log, one order -------------------------------------------------
    assert [e.seq for e in entries] == list(range(1, len(entries) + 1))
    assert [e.domain for e in entries] == [DOMAIN_AGENT, DOMAIN_BOT, DOMAIN_SUBAGENT, DOMAIN_SWARM, DOMAIN_BATCH, DOMAIN_SENTINEL]

    # --- every entry carries from / to / reason / attempt / timestamp -------
    for entry in entries:
        assert entry.from_ref
        assert entry.to_ref
        assert entry.reason
        assert entry.reason_class
        assert entry.recorded_at > 0
        assert entry.recorded_at_iso.endswith("+00:00")
        assert entry.entry_id.startswith("ho-")

    by_domain = {e.domain: e for e in entries}
    assert by_domain[DOMAIN_AGENT].from_ref == "architect"
    assert by_domain[DOMAIN_AGENT].to_ref == "coder"
    assert by_domain[DOMAIN_AGENT].reason == HANDOFF_REQUESTED
    assert by_domain[DOMAIN_AGENT].details["project_id"] == "proj-ledger"
    assert by_domain[DOMAIN_AGENT].details["handoff_id"] == project_record.handoff_id

    assert by_domain[DOMAIN_BOT].from_ref == "coder"
    assert by_domain[DOMAIN_BOT].to_ref == "reviewer"
    assert by_domain[DOMAIN_BOT].attempt == 1
    assert by_domain[DOMAIN_BOT].max_attempts == 3

    # A capability gap moves work to a named successor, and says why.
    assert by_domain[DOMAIN_SUBAGENT].kind == "handoff"
    assert by_domain[DOMAIN_SUBAGENT].from_ref == "subagent:sub-42"
    assert by_domain[DOMAIN_SUBAGENT].to_ref == "subagent:sub-99"
    assert by_domain[DOMAIN_SUBAGENT].reason_class == "capability"
    assert by_domain[DOMAIN_SUBAGENT].reason == CAPABILITY_MISSING
    assert by_domain[DOMAIN_SUBAGENT].details["action"] == "reassign"

    assert by_domain[DOMAIN_SWARM].kind == "resume"
    assert by_domain[DOMAIN_SWARM].details["checkpoint"] == {"step_index": 7}

    # An exhausted unit is handed to a human, in the same log, in order.
    assert by_domain[DOMAIN_BATCH].kind == "escalation"
    assert by_domain[DOMAIN_BATCH].to_ref == "human"
    assert by_domain[DOMAIN_BATCH].reason == "attempts_exhausted"
    assert by_domain[DOMAIN_BATCH].attempt == 3
    # The typed class of the underlying failure is preserved next to the
    # ceiling, so "we gave up" and "why" are both answerable.
    assert by_domain[DOMAIN_BATCH].details["failure_reason"] == "provider_rate_limit"
    assert [r.escalation_id for r in list_open_escalations()] == [escalate.escalation.escalation_id]
    assert escalate.escalation.status == "open"
    assert escalate.escalation.ledger_seq == by_domain[DOMAIN_BATCH].seq

    # A deliberate transfer is a reason code, not free text.
    record_handoff(DOMAIN_BOT, "task-gamma", "coder", "architect", reason=SUCCESSION_FALLBACK)
    assert ledger.latest(domain=DOMAIN_BOT, task_id="task-gamma").reason_class == "routed"


def test_ledger_is_durable_and_globally_ordered_across_instances(tmp_path):
    """A second reader (another process) sees one total order, no duplicates."""
    from alpha.runtime.escalation import get_handoff_ledger

    first = get_handoff_ledger()
    for index in range(5):
        first.append(kind="handoff", domain=DOMAIN_BOT, task_id=f"t{index}", from_ref="a", to_ref="b", reason="handoff_requested", attempt=index)

    # A brand-new instance over the same root: what a restarted process sees.
    reopened = HandoffLedger(first.root)
    entries = reopened.entries()
    assert [e.seq for e in entries] == [1, 2, 3, 4, 5]
    assert [e.task_id for e in entries] == ["t0", "t1", "t2", "t3", "t4"]

    appended = reopened.append(kind="failure", domain=DOMAIN_SUBAGENT, task_id="t6", from_ref="x", to_ref="x", reason="worker_crash", attempt=1, max_attempts=3)
    assert appended.seq == 6
    assert [e.seq for e in get_handoff_ledger().entries()] == [1, 2, 3, 4, 5, 6]

    # One JSON object per line, so a truncated tail cannot corrupt the prefix.
    lines = (first.path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 6
    assert all(json.loads(line)["seq"] == index + 1 for index, line in enumerate(lines))


def test_ledger_read_fails_closed_on_a_corrupt_line(tmp_path):
    """A corrupt line is reported, never skipped and never repaired."""
    from alpha.runtime.escalation import LedgerError, get_handoff_ledger

    ledger = get_handoff_ledger()
    ledger.append(kind="handoff", domain=DOMAIN_BOT, task_id="t1", from_ref="a", to_ref="b", reason="handoff_requested")
    with open(ledger.path, "a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    with pytest.raises(LedgerError) as excinfo:
        ledger.entries()
    assert "line 2" in str(excinfo.value)


def test_ledger_write_failure_never_breaks_the_caller(tmp_path, monkeypatch):
    """Recording a handoff is bookkeeping, never the reason work fails."""
    import alpha.runtime.escalation as escalation

    def _boom(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(escalation.HandoffLedger, "append", _boom)
    assert escalation.record_handoff(DOMAIN_BOT, "t1", "a", "b", reason="handoff_requested") is None
    assert escalation.escalate_to_human(DOMAIN_BOT, "t1", "a", reason="attempts_exhausted") is None
    assert escalation.list_open_escalations() == []


def test_project_and_bot_records_both_reach_the_single_ledger(tmp_path):
    """The two private records stay authoritative, and both feed the one log."""
    from alpha.projects.handoffs import get_handoff_store
    from alpha.runtime.escalation import get_handoff_ledger

    registry = BotRegistry()
    store = get_handoff_store("proj-two")
    record = store.create(
        task_id="task-1",
        from_bot="architect",
        to_bot="reviewer",
        objective="Check the ledger",
        attempt=2,
        max_attempts=4,
        reason="capability_missing",
    )
    # The project record itself is unchanged...
    assert record.from_bot == "architect"
    assert record.to_bot == "reviewer"
    assert store.list(status="open")[0].handoff_id == record.handoff_id
    # ...and the same transfer is queryable in the global log, exactly once.
    agent_entries = get_handoff_ledger().entries(domain=DOMAIN_AGENT)
    assert len(agent_entries) == 1
    assert agent_entries[0].reason == "capability_missing"
    assert agent_entries[0].attempt == 2
    assert agent_entries[0].max_attempts == 4
    assert agent_entries[0].task_id == "proj-two:task-1"
    # A direct bot handoff is NOT double-recorded by the project store.
    execute_handoff(task_id="task-2", from_bot="coder", to_bot="tester", objective="Run the suite", registry=registry)
    assert [e.task_id for e in get_handoff_ledger().entries(domain=DOMAIN_BOT)] == ["task-2"]


def test_subagent_ledger_entry_carries_attempt_and_ceiling(tmp_path):
    """A subagent failure is in the same log, with its attempt budget."""
    from alpha.runtime.escalation import get_handoff_ledger

    manager = SubagentLifecycleManager()
    record = manager.spawn_subagent("lead", SubagentContract(objective="Do the thing", max_attempts=2))
    manager.start_subagent(record.subagent_id)
    manager.fail_subagent(record.subagent_id, "429 too many requests")

    entry = get_handoff_ledger().latest(domain=DOMAIN_SUBAGENT, task_id=record.subagent_id)
    assert entry is not None
    assert entry.attempt == 1
    assert entry.max_attempts == 2
    assert entry.reason == "provider_rate_limit"
    assert entry.reason_class == "transient"
    assert entry.details["action"] in {"retry", "resume"}
    assert time.time() - entry.recorded_at < 60
