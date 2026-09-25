"""Hermetic contract tests for Alpha's prospective-memory subsystem."""

from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from alpha.memory.prospective import (
    ProspectiveConfig,
    ProspectiveItem,
    ProspectiveKind,
    ProspectiveStatus,
    ProspectiveStore,
    TriggerContext,
    TriggerSpec,
    create_item,
    evaluate_trigger,
    evaluate_trigger_detailed,
    expand_recurring,
    load_prospective_config,
    prospective_enabled,
    read_entries,
    render_block,
    summarize,
)
from alpha.memory.prospective.lifecycle import cancel as lifecycle_cancel
from alpha.memory.prospective.lifecycle import complete as lifecycle_complete
from alpha.memory.prospective.store import prospective_items_path


def make_config(tmp_path: Path, **overrides: object) -> ProspectiveConfig:
    """Build an explicit enabled config rooted in the test's temp directory."""

    values: dict[str, object] = {
        "enabled": True,
        "storage_path": str(tmp_path / "state"),
        "expiry_grace_hours": 2.0,
        "max_pending_per_user": 20,
        "max_surfaced_per_recall": 2,
        "max_items_per_user": 30,
        "recurring_max_occurrences": 3,
    }
    values.update(overrides)
    return ProspectiveConfig.model_validate(values)


def make_store(tmp_path: Path, **overrides: object) -> ProspectiveStore:
    return ProspectiveStore(make_config(tmp_path, **overrides))


def test_gate_is_off_by_default_and_does_not_write(tmp_path: Path) -> None:
    config = ProspectiveConfig(storage_path=str(tmp_path / "state"))
    store = ProspectiveStore(config)

    result = store.create(user_id="u1", content="not written while disabled", now=10.0)

    assert result.status == "skipped"
    assert result.reason == "disabled"
    assert store.list_items("u1") == []
    assert not prospective_items_path(store.root, "u1").exists()


def test_create_list_cancel_and_complete_round_trip(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    created = store.create(
        user_id="u1",
        kind="commitment",
        content="send the weekly report",
        priority=80,
        now=100.0,
    )
    assert created.ok
    assert created.item is not None
    assert created.item.kind is ProspectiveKind.COMMITMENT
    assert [item.id for item in store.list_items("u1")] == [created.item.id]

    cancelled = store.cancel(created.item.id, user_id="u1", now=110.0)
    assert cancelled.ok
    assert cancelled.item is not None
    assert cancelled.item.status is ProspectiveStatus.CANCELLED
    illegal = store.complete(created.item.id, user_id="u1", now=120.0)
    assert illegal.status == "illegal_transition"
    assert illegal.reason

    second = store.create(user_id="u1", content="call the customer", now=130.0)
    assert second.ok
    completed = store.complete(second.item.id, user_id="u1", now=140.0)
    assert completed.ok
    assert completed.item is not None
    assert completed.item.status is ProspectiveStatus.DONE
    assert completed.item.completed_at == 140.0


def test_due_detection_respects_explicit_and_configured_grace(tmp_path: Path) -> None:
    store = make_store(tmp_path, expiry_grace_hours=2.0)
    with_grace = store.create(
        user_id="u1",
        content="renew the domain",
        due_at=100.0,
        now=0.0,
    )
    no_grace = store.create(
        user_id="u1",
        content="call the bank",
        due_at=199.0,
        grace_until=200.0,
        now=0.0,
    )
    assert with_grace.item is not None
    assert with_grace.item.grace_until == 100.0 + 2.0 * 3600.0
    assert no_grace.item is not None
    assert no_grace.item.grace_until == 200.0

    due = store.due_items("u1", now=199.999)
    assert {item.content for item in due} == {"renew the domain", "call the bank"}
    assert {item.content for item in store.due_items("u1", now=200.0)} == {"renew the domain"}
    assert store.due_items("u1", now=200.0 + 2.0 * 3600.0 + 1.0) == []

    expired = store.expire_due(now=200.0 + 2.0 * 3600.0 + 1.0, user_id="u1")
    assert expired.status == "succeeded"
    assert {item.content for item in expired.items} == {"renew the domain", "call the bank"}


def test_recurring_expansion_is_bounded(tmp_path: Path) -> None:
    store = make_store(tmp_path, recurring_max_occurrences=2)
    first = store.create(
        user_id="u1",
        kind="recurring",
        content="send the report",
        due_at=100.0,
        recurrence_interval=10.0,
        recurring_max_occurrences=2,
        now=0.0,
    )
    assert first.ok
    assert first.item is not None
    completed_first = store.complete(first.item.id, user_id="u1", now=100.0)
    assert completed_first.ok
    assert completed_first.next_item is not None
    assert completed_first.next_item.occurrence == 2

    completed_second = store.complete(completed_first.next_item.id, user_id="u1", now=110.0)
    assert completed_second.ok
    assert completed_second.next_item is None
    assert completed_second.details["recurring_status"] == "skipped"
    assert len(store.list_items("u1")) == 2


def test_trigger_kinds_match_and_unknown_kind_fails_closed() -> None:
    tool = TriggerSpec(kind="tool", params={"name": "deploy"})
    event = TriggerSpec(kind="event", params={"name": "deploy_finished"})
    condition = TriggerSpec(kind="condition", params={"key": "error_budget", "value": "healthy"})
    context = TriggerContext(
        user_id="u1",
        tool_name="deploy",
        event_name="deploy_finished",
        values={"error_budget": "healthy"},
    )

    assert evaluate_trigger(tool, context) is True
    assert evaluate_trigger(event, context) is True
    assert evaluate_trigger(condition, context) is True
    assert evaluate_trigger(condition, TriggerContext(values={"error_budget": "spent"})) is False

    unknown = evaluate_trigger_detailed({"kind": "future_quantum_trigger", "params": {}}, context)
    assert unknown.matched is False
    assert unknown.reason == "unknown_trigger_kind"


def test_trigger_recall_filters_scope_and_unknown_records(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    matching = store.create(
        user_id="u1",
        content="check the error budget",
        trigger={"kind": "event", "params": {"name": "deploy_finished"}},
        now=0.0,
    )
    other_user = store.create(
        user_id="u2",
        content="private obligation",
        trigger={"kind": "event", "params": {"name": "deploy_finished"}},
        now=0.0,
    )
    unknown = store.create(
        user_id="u1",
        content="unknown trigger",
        trigger={"kind": "not-supported", "params": {}},
        now=0.0,
    )
    assert matching.ok and other_user.ok and unknown.ok

    found = store.items_for_trigger(
        TriggerContext(user_id="u1", event_name="deploy_finished"),
        user_id="u1",
    )

    assert [item.id for item in found] == [matching.item.id]
    fired = store.fire(matching.item.id, user_id="u1", now=10.0)
    assert fired.ok
    assert fired.item is not None
    assert fired.item.due_at == 10.0
    assert other_user.item is not None
    assert other_user.item.user_id not in {item.user_id for item in found}


def test_surfaced_once_semantics_and_empty_render(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    created = store.create(user_id="u1", content="one reminder", due_at=10.0, now=0.0)
    assert created.ok

    first = store.mark_surfaced(created.item.id, user_id="u1", now=10.0)
    second = store.mark_surfaced(created.item.id, user_id="u1", now=11.0)
    assert first.ok
    assert first.item is not None
    assert first.item.surfaced_at == 10.0
    assert second.status == "skipped"
    assert second.reason == "already_surfaced"
    assert store.due_items("u1", now=10.0) == []
    assert render_block(store.list_items("u1"), config=store.config) == ""


def test_expiry_is_a_terminal_disclosed_transition(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    created = store.create(user_id="u1", content="expire me", due_at=1.0, grace_until=2.0, now=0.0)
    assert created.ok

    result = store.expire_due(now=3.0, user_id="u1")

    assert result.status == "succeeded"
    assert result.items[0].status is ProspectiveStatus.EXPIRED
    again = store.expire_due(now=4.0, user_id="u1")
    assert again.status == "skipped"
    assert again.reason == "no_due_items"


def test_users_are_isolated(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    a = store.create(user_id="user-a", content="A obligation", now=1.0)
    b = store.create(user_id="user-b", content="B obligation", now=1.0)

    assert a.ok and b.ok
    assert [item.content for item in store.list_items("user-a")] == ["A obligation"]
    assert [item.content for item in store.list_items("user-b")] == ["B obligation"]
    assert store.list_items("user-a")[0].user_id != store.list_items("user-b")[0].user_id


def test_max_pending_refusal_is_disclosed(tmp_path: Path) -> None:
    store = make_store(tmp_path, max_pending_per_user=1)
    first = store.create(user_id="u1", content="first", now=1.0)
    second = store.create(user_id="u1", content="second", now=2.0)

    assert first.ok
    assert second.status == "refused"
    assert second.reason == "max_pending_per_user"
    assert second.current == 1
    assert second.limit == 1
    assert len(store.list_items("u1")) == 1

    bounded = make_store(tmp_path / "bounded", max_pending_per_user=10, max_items_per_user=1)
    assert bounded.create(user_id="u1", content="first", now=1.0).ok
    refused = bounded.create(user_id="u1", content="second", now=2.0)
    assert refused.status == "refused"
    assert refused.reason == "max_items_per_user"


def test_corrupt_document_is_preserved_and_scope_restarts_empty(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    first_store = ProspectiveStore(config)
    assert first_store.create(user_id="u1", content="before corruption", now=1.0).ok
    path = prospective_items_path(first_store.root, "u1")
    path.write_text("{not valid json", encoding="utf-8")

    recovered_store = ProspectiveStore(config)
    read = recovered_store.list_items_result("u1")
    assert read.status == "recovered"
    assert read.items == []
    assert list(path.parent.glob("items.json.corrupt-*"))
    replacement = recovered_store.create(user_id="u1", content="after recovery", now=2.0)
    assert replacement.ok
    assert [item.content for item in recovered_store.list_items("u1")] == ["after recovery"]


def test_provenance_records_each_state_change(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = ProspectiveStore(config)
    created = store.create(user_id="u1", content="audit me", now=100.0)
    assert created.ok and created.item is not None
    item_id = created.item.id
    assert store.fire(item_id, user_id="u1", now=101.0).ok
    assert store.mark_surfaced(item_id, user_id="u1", now=102.0).ok
    assert store.complete(item_id, user_id="u1", now=103.0).ok
    cancelled = store.create(user_id="u1", content="cancel audit", now=104.0)
    assert cancelled.ok and cancelled.item is not None
    assert store.cancel(cancelled.item.id, user_id="u1", now=105.0).ok
    expiring = store.create(user_id="u1", content="expire audit", due_at=1.0, grace_until=2.0, now=106.0)
    assert expiring.ok and expiring.item is not None
    assert store.expire_due(now=107.0, user_id="u1").status == "succeeded"

    entries = read_entries("1970-01-01", user_id="u1", config=config)
    assert [entry["event"] for entry in entries] == [
        "created",
        "fired",
        "surfaced",
        "done",
        "created",
        "cancelled",
        "created",
        "expired",
    ]
    assert all("timestamp" in entry and "reason" in entry for entry in entries)


def test_provenance_failure_does_not_take_state_change_down(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = ProspectiveStore(config)
    created = store.create(user_id="u1", content="state survives", now=1.0)
    assert created.ok and created.item is not None
    provenance_parent = store.root / "users" / "u1" / "prospective" / "provenance"
    if provenance_parent.exists():
        shutil.rmtree(provenance_parent)
    provenance_parent.write_text("not a directory", encoding="utf-8")

    result = store.cancel(created.item.id, user_id="u1", now=2.0)

    assert result.ok
    assert result.provenance_status == "failed"
    assert result.provenance_error
    assert store.get(created.item.id, user_id="u1").status is ProspectiveStatus.CANCELLED


def test_render_block_is_bounded_and_empty_state_is_honest(tmp_path: Path) -> None:
    config = make_config(tmp_path, max_surfaced_per_recall=2)
    store = ProspectiveStore(config)
    for index, priority in enumerate((10, 30, 20), start=1):
        assert store.create(user_id="u1", content=f"obligation-{index}", priority=priority, now=1.0).ok

    rendered = render_block(store.list_items("u1"), config=config)
    lines = [line for line in rendered.splitlines() if line.startswith("- [")]
    assert len(lines) == 2
    assert "obligation-2" in rendered
    assert "obligation-3" in rendered
    assert "obligation-1" not in rendered
    assert render_block([], config=config) == ""
    assert summarize([]) == "Prospective memory: no items."


def test_concurrent_creates_keep_every_record(tmp_path: Path) -> None:
    store = make_store(tmp_path, max_pending_per_user=100, max_items_per_user=100)

    def create_one(index: int):
        return store.create(user_id="u1", content=f"obligation-{index}", now=float(index))

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(create_one, range(40)))

    assert all(result.ok for result in results)
    assert len(store.list_items("u1")) == 40
    payload = json.loads(prospective_items_path(store.root, "u1").read_text(encoding="utf-8"))
    assert len(payload["items"]) == 40


def test_pure_lifecycle_illegal_transition_is_result_not_exception() -> None:
    item = create_item("remember", user_id="u1", now=1.0)
    assert item.ok and item.item is not None
    cancelled = lifecycle_cancel(item.item, now=2.0)
    assert cancelled.ok
    assert lifecycle_complete(cancelled.item, now=3.0).status == "illegal_transition"

    recurring = ProspectiveItem(
        user_id="u1",
        kind="recurring",
        content="repeat",
        occurrence=2,
        recurrence_interval=60.0,
        recurring_max_occurrences=2,
        due_at=100.0,
    )
    expansion = expand_recurring(recurring, now=100.0)
    assert expansion.status == "skipped"
    assert expansion.reason == "recurring_max_occurrences"


def test_config_override_loader_uses_runtime_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "runtime-home"
    home.mkdir()
    (home / "prospective-memory.yaml").write_text(
        "enabled: true\nmax_surfaced_per_recall: 4\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(home))

    config = load_prospective_config()

    assert config.enabled is True
    assert config.max_surfaced_per_recall == 4
    assert prospective_enabled(config) is True
    assert config.storage_path is None


def test_every_prospective_config_key_has_a_reader() -> None:
    package_root = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "memory" / "prospective"
    blob = "\n".join(path.read_text(encoding="utf-8") for path in package_root.glob("*.py"))
    fields = set(ProspectiveConfig.model_fields)

    assert fields
    assert sorted(field for field in fields if field not in blob) == []
