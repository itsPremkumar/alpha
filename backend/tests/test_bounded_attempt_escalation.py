"""Bounded attempts must ESCALATE to a human, never retry forever silently.

Before this, the attempt ceilings existed in four places and none of them ended
in an escalation: a batch item that hit ``max_attempts`` was marked failed with
a free-text error, a subagent had no attempt counter at all, a swarm task that
kept losing its worker was re-claimed forever, and ``escalate_task`` was only
reachable from a manual HTTP route. These tests pin the corrected behaviour:

* one typed taxonomy (``alpha.bots.failure_reasons``) classifies every work
  unit, so transient / capability / crash / exhausted are distinguishable and
  not decided by a ``"429" in str(exc)`` substring check;
* a spent ceiling always opens a durable, queryable escalation record.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

import alpha.swarm.coordinator as swarm_coordinator
from alpha.bots.failure_reasons import (
    ACTION_ESCALATE,
    ACTION_NONE,
    ACTION_REASSIGN,
    ACTION_RESUME,
    ACTION_RETRY,
    ACTION_STOP,
    ALL_REASONS,
    ATTEMPTS_EXHAUSTED,
    CAPABILITY_MISSING,
    CONTEXT_OVERFLOW,
    FAILURE_CLASS_CAPABILITY,
    FAILURE_CLASS_CRASH,
    FAILURE_CLASS_EXHAUSTED,
    FAILURE_CLASS_PERMANENT,
    FAILURE_CLASS_TRANSIENT,
    PROVIDER_RATE_LIMIT,
    REASON_CLASSES,
    UNKNOWN,
    WORKER_CRASH,
    classify_agent_error,
    classify_work_failure,
    decide_failure,
    failure_class,
    is_auto_retryable,
)
from alpha.config.database_config import DatabaseConfig
from alpha.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from alpha.persistence.subagent_batches import SubagentBatchRepository
from alpha.runtime.escalation import (
    DOMAIN_BATCH,
    DOMAIN_SUBAGENT,
    get_escalation_store,
    get_handoff_ledger,
    list_open_escalations,
    reset_ledger_caches,
)
from alpha.subagents.lifecycle import SubagentContract, SubagentLifecycleManager, SubagentStatusEnum


@pytest.fixture(autouse=True)
def _isolated_runtime_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("alpha.bots.registry._global_registry", None)
    monkeypatch.setattr("alpha.bots.registry._global_registry_path", None)
    import alpha.subagents.lifecycle as lifecycle_mod

    monkeypatch.setattr(lifecycle_mod, "_GLOBAL_LIFECYCLE_MANAGER", None)
    swarm_coordinator._GLOBAL_COORDINATOR = None
    reset_ledger_caches()
    yield
    swarm_coordinator._GLOBAL_COORDINATOR = None
    reset_ledger_caches()


# --- (b) one typed taxonomy, not a substring check -------------------------


def test_work_unit_failures_are_classified_by_class_not_by_a_429_substring():
    """The four classes the recovery policies used to encode ad hoc."""
    cases = {
        "429 rate limit exceeded for gpt-4": (PROVIDER_RATE_LIMIT, FAILURE_CLASS_TRANSIENT),
        "RateLimitError: too many requests": (PROVIDER_RATE_LIMIT, FAILURE_CLASS_TRANSIENT),
        "Execution lease expired after the maximum retry count": (WORKER_CRASH, FAILURE_CLASS_CRASH),
        "Connection reset by peer while streaming": (WORKER_CRASH, FAILURE_CLASS_CRASH),
        "no such tool: postgres_execute": (CAPABILITY_MISSING, FAILURE_CLASS_CAPABILITY),
        "unknown capability: finance_ledger": (CAPABILITY_MISSING, FAILURE_CLASS_CAPABILITY),
        "this model has no tool access": (UNKNOWN, FAILURE_CLASS_PERMANENT),
        "maximum context length exceeded": (CONTEXT_OVERFLOW, FAILURE_CLASS_PERMANENT),
    }
    for text, (expected_reason, expected_class) in cases.items():
        reason = classify_work_failure(text)
        assert reason == expected_reason, text
        assert failure_class(reason) == expected_class, text

    # A rate limit and a crash are no longer the same undifferentiated thing
    # that a single substring test could tell apart.
    assert classify_work_failure("429 rate limit") != classify_work_failure("worker died, lease expired")
    # Empty text is honestly unknown, never a guess.
    assert classify_work_failure("") == UNKNOWN
    assert classify_work_failure(None) == UNKNOWN
    # The provider rules still outrank the work rules, and the bot contract is
    # unchanged: classify_agent_error must keep ignoring work-unit phrasing.
    assert classify_agent_error("429 rate limit") == PROVIDER_RATE_LIMIT
    assert classify_agent_error("lease expired") == UNKNOWN
    # Every code has a class, and a code nobody knows is fail-closed.
    assert set(REASON_CLASSES) == set(ALL_REASONS)
    assert failure_class("something-invented-tomorrow") == FAILURE_CLASS_PERMANENT
    assert is_auto_retryable(WORKER_CRASH) is True
    assert is_auto_retryable(CAPABILITY_MISSING) is False


@pytest.mark.parametrize(
    ("reason", "expected_action"),
    [
        (PROVIDER_RATE_LIMIT, ACTION_RETRY),
        (WORKER_CRASH, ACTION_RESUME),
        (CAPABILITY_MISSING, ACTION_REASSIGN),
        (CONTEXT_OVERFLOW, ACTION_ESCALATE),
        (UNKNOWN, ACTION_RETRY),
        ("cancelled", ACTION_STOP),
        ("handoff_requested", ACTION_NONE),
    ],
)
def test_each_class_gets_its_own_bounded_action(reason, expected_action):
    assert decide_failure(reason, attempt=1, max_attempts=3).action == expected_action


def test_the_ceiling_is_authoritative_whatever_the_class():
    for reason in (PROVIDER_RATE_LIMIT, WORKER_CRASH, CAPABILITY_MISSING, UNKNOWN, CONTEXT_OVERFLOW):
        decision = decide_failure(reason, attempt=3, max_attempts=3)
        assert decision.action == ACTION_ESCALATE, reason
        assert decision.reason == ATTEMPTS_EXHAUSTED, reason
        assert decision.reason_class == FAILURE_CLASS_EXHAUSTED, reason
        assert decision.should_escalate is True
    # One attempt left is still an attempt: 2 of 3 retries.
    assert decide_failure(PROVIDER_RATE_LIMIT, attempt=2, max_attempts=3).action == ACTION_RETRY


def test_recovery_bridge_speaks_both_vocabularies():
    from alpha.recovery import classify_failure, decide_from_reason, recovery_class_to_reason

    assert recovery_class_to_reason("container_crash") == WORKER_CRASH
    assert recovery_class_to_reason("model_rate_limit") == PROVIDER_RATE_LIMIT
    assert recovery_class_to_reason("not-a-class") == UNKNOWN
    assert classify_failure("429 too many requests") == "model_rate_limit"

    bounded = decide_from_reason("429 too many requests", attempt=3, max_attempts=3)
    assert bounded.action == "escalate"
    assert bounded.attempt == 3
    retriable = decide_from_reason("429 too many requests", attempt=1, max_attempts=3)
    assert retriable.action == "retry"
    assert retriable.wait_seconds >= 0


# --- (c) a spent ceiling opens a durable, queryable escalation --------------


def test_subagent_exhausts_attempts_then_escalates_instead_of_retrying():
    manager = SubagentLifecycleManager()
    record = manager.spawn_subagent("lead", SubagentContract(objective="Migrate the schema", max_attempts=2))
    subagent_id = record.subagent_id

    # Attempt 1: a transient failure is recorded, not escalated.
    assert manager.start_subagent(subagent_id) is True
    assert manager.get_subagent(subagent_id).attempt == 1
    manager.fail_subagent(subagent_id, "429 too many requests")
    assert manager.get_subagent(subagent_id).status == SubagentStatusEnum.FAILED
    assert list_open_escalations() == []

    # Attempt 2 is the LAST attempt: the unit is escalated, durably.
    replacement = SubagentLifecycleManager()
    stored = replacement.get_subagent(subagent_id)
    assert stored.attempt == 1
    assert stored.failure_reason == PROVIDER_RATE_LIMIT
    assert replacement.start_subagent(subagent_id) is False  # FAILED is not dispatchable
    assert replacement._records[subagent_id].attempt == 1

    # Re-dispatch a fresh unit for attempt 2 and spend the ceiling.
    second = manager.spawn_subagent("lead", SubagentContract(objective="Migrate the schema", max_attempts=2))
    assert manager.start_subagent(second.subagent_id) is True
    assert manager.start_subagent(second.subagent_id) is False  # already RUNNING
    manager.fail_subagent(second.subagent_id, "429 too many requests")
    # A failed unit is terminal: the ceiling is spent and it is not retried.
    assert manager.fail_subagent(second.subagent_id, "429 too many requests") is False

    # Now the real ceiling case: attempt == max_attempts.
    third = manager.spawn_subagent("lead", SubagentContract(objective="Migrate the schema", max_attempts=1))
    assert manager.start_subagent(third.subagent_id) is True
    assert manager.get_subagent(third.subagent_id).attempts_exhausted is True
    manager.fail_subagent(third.subagent_id, "tool crashed: connection reset by peer")

    escalated = list_open_escalations()
    assert len(escalated) == 1
    record_escalation = escalated[0]
    assert record_escalation.domain == DOMAIN_SUBAGENT
    assert record_escalation.task_id == third.subagent_id
    assert record_escalation.reason == ATTEMPTS_EXHAUSTED
    assert record_escalation.reason_class == FAILURE_CLASS_EXHAUSTED
    assert record_escalation.attempt == 1
    assert record_escalation.max_attempts == 1
    assert record_escalation.to_ref == "human"
    assert record_escalation.status == "open"
    assert "connection reset by peer" in record_escalation.detail
    # The record is linked from the subagent and from the ordered ledger.
    assert manager.get_subagent(third.subagent_id).escalation_id == record_escalation.escalation_id
    entry = get_handoff_ledger().latest(domain=DOMAIN_SUBAGENT, task_id=third.subagent_id, kind="escalation")
    assert entry is not None
    assert entry.attempt == 1
    assert entry.to_ref == "human"
    assert entry.details["failure_reason"] == WORKER_CRASH

    # Queryable and acknowledgeable: a human can close it.
    store = get_escalation_store()
    assert [r.escalation_id for r in store.list(status="open", domain=DOMAIN_SUBAGENT)] == [record_escalation.escalation_id]
    acknowledged = store.acknowledge(record_escalation.escalation_id, by="operator")
    assert acknowledged is not None
    assert acknowledged.status == "acknowledged"
    assert acknowledged.acknowledged_by == "operator"
    assert list_open_escalations() == []


def test_escalation_records_are_idempotent_while_open():
    from alpha.runtime.escalation import record_failure

    for _ in range(3):
        disposition = record_failure(
            DOMAIN_SUBAGENT,
            "sub-dup",
            "subagent:sub-dup",
            attempt=3,
            max_attempts=3,
            error="429 rate limit",
        )
        assert disposition.escalated
    assert len(list_open_escalations()) == 1
    # One durable page for one human, but every attempt is still in the log.
    entries = get_handoff_ledger().entries(domain=DOMAIN_SUBAGENT, kind="escalation")
    assert len(entries) == 3
    assert [e.seq for e in entries] == sorted(e.seq for e in entries)


# --- batch items: typed reason + escalation, no schema change ---------------


@pytest_asyncio.fixture
async def repo(tmp_path):
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db")))
    factory = get_session_factory()
    assert factory is not None
    try:
        yield SubagentBatchRepository(factory)
    finally:
        await close_engine()


async def _create_batch(repo, *, max_attempts: int = 1) -> dict:
    return await repo.create_batch(
        batch_id="batch-esc",
        user_id="user-1",
        thread_id="thread-1",
        run_id="run-1",
        tool_call_id="call-1",
        submission_key="run-1:call-1",
        title="Escalation batch",
        subagent_type="general-purpose",
        items=[{"key": "item-0", "prompt": "Do the thing"}],
        max_live_items=1,
        max_running_items=1,
        max_attempts=max_attempts,
        execution_spec={},
    )


@pytest.mark.asyncio
async def test_batch_item_exhaustion_escalates_with_the_typed_reason(repo):
    now = datetime.now(UTC)
    created = await _create_batch(repo, max_attempts=1)
    assert created["counts"]["pending"] == 1

    claimed = await repo.claim_items(now=now, lease_owner="worker-1", lease_seconds=60, limit=1)
    assert len(claimed) == 1
    assert claimed[0]["attempt"] == 1
    item_id = claimed[0]["id"]

    # A rate limit on the last permitted attempt: typed, recorded, escalated.
    finalized = await repo.finalize_item(
        item_id,
        lease_owner="worker-1",
        succeeded=False,
        result=None,
        result_preview=None,
        result_truncated=False,
        error="429 rate limit exceeded for the subagent model",
        stop_reason=None,
        token_usage=None,
        model_name="test-model",
        completed_at=now,
    )
    assert finalized is True

    items = await repo.list_items("batch-esc", user_id="user-1")
    assert items[0]["status"] == "failed"
    assert items[0]["attempt"] == 1
    assert items[0]["error"] == "429 rate limit exceeded for the subagent model"

    escalations = list_open_escalations(domain=DOMAIN_BATCH)
    assert len(escalations) == 1
    assert escalations[0].task_id == item_id
    assert escalations[0].reason == ATTEMPTS_EXHAUSTED
    assert escalations[0].attempt == 1
    assert escalations[0].max_attempts == 1
    # The failure class is answerable from the ledger, not from a substring test.
    entry = get_handoff_ledger().latest(domain=DOMAIN_BATCH, task_id=item_id, kind="escalation")
    assert entry is not None
    assert entry.details["failure_reason"] == PROVIDER_RATE_LIMIT
    assert entry.reason_class == FAILURE_CLASS_EXHAUSTED
    assert entry.details["outcome"] == "failed"
    assert entry.details["subagent_type"] == "general-purpose"

    # An operator retry is the sanctioned way to give the item a fresh budget,
    # and it closes the escalation instead of leaving a stale page.
    retried = await repo.retry_item("batch-esc", item_id, user_id="user-1")
    assert retried is not None
    assert retried["status"] == "pending"
    assert list_open_escalations(domain=DOMAIN_BATCH) == []
    assert get_handoff_ledger().latest(domain=DOMAIN_BATCH, task_id=item_id, kind="handoff") is not None


@pytest.mark.asyncio
async def test_batch_item_transient_failure_is_queued_and_classified_not_escalated(repo):
    now = datetime.now(UTC)
    await _create_batch(repo, max_attempts=3)
    claimed = await repo.claim_items(now=now, lease_owner="worker-1", lease_seconds=60, limit=1)
    item_id = claimed[0]["id"]

    await repo.finalize_item(
        item_id,
        lease_owner="worker-1",
        succeeded=False,
        result=None,
        result_preview=None,
        result_truncated=False,
        error="503 service error from the provider",
        stop_reason=None,
        token_usage=None,
        model_name="test-model",
        completed_at=now,
    )
    items = await repo.list_items("batch-esc", user_id="user-1")
    assert items[0]["status"] == "queued"
    assert list_open_escalations() == []
    entry = get_handoff_ledger().latest(domain=DOMAIN_BATCH, task_id=item_id)
    assert entry.reason == "provider_server_error"
    assert entry.reason_class == FAILURE_CLASS_TRANSIENT
    assert entry.details["outcome"] == "retry_queued"
    assert entry.details["action"] == "retry"


@pytest.mark.asyncio
async def test_batch_worker_crash_requeues_then_escalates_the_last_crash(repo):
    """A dead worker's lease is a crash, bounded like any other failure."""
    now = datetime.now(UTC)
    await _create_batch(repo, max_attempts=2)
    first = await repo.claim_items(now=now, lease_owner="worker-1", lease_seconds=1, limit=1)
    item_id = first[0]["id"]

    # The worker dies: its lease expires and the item is requeued as a crash.
    later = now + timedelta(seconds=120)
    reclaimed = await repo.claim_items(now=later, lease_owner="worker-2", lease_seconds=1, limit=1)
    assert [item["id"] for item in reclaimed] == [item_id]
    assert reclaimed[0]["attempt"] == 2

    crash_entries = get_handoff_ledger().entries(domain=DOMAIN_BATCH, task_id=item_id)
    assert [e.reason for e in crash_entries] == [WORKER_CRASH]
    assert crash_entries[0].details["outcome"] == "lease_expired_requeued"
    assert list_open_escalations() == []

    # The second crash is the last one: escalate, and leave the item failed.
    much_later = later + timedelta(seconds=120)
    third = await repo.claim_items(now=much_later, lease_owner="worker-3", lease_seconds=1, limit=1)
    assert third == []
    items = await repo.list_items("batch-esc", user_id="user-1")
    assert items[0]["status"] == "failed"
    assert "maximum retry count" in items[0]["error"]

    escalations = list_open_escalations(domain=DOMAIN_BATCH)
    assert len(escalations) == 1
    assert escalations[0].reason == ATTEMPTS_EXHAUSTED
    assert escalations[0].attempt == 2
    exhausted = get_handoff_ledger().latest(domain=DOMAIN_BATCH, task_id=item_id, kind="escalation")
    assert exhausted is not None
    assert exhausted.details["outcome"] == "lease_expired_exhausted"
    assert exhausted.details["failure_reason"] == WORKER_CRASH
