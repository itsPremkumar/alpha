from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Barrier

import pytest

from agent_workspace.ledger import MAX_LIST_LIMIT, ActionIntent, ActionLedger, ActionReceipt


def test_lifecycle_is_append_only_and_survives_reload(tmp_path):
    ledger = ActionLedger(tmp_path)
    intent = ledger.record_intent("alice", "write_file", {"content": "hello"}, thread_id="thread-1")
    assert isinstance(intent, ActionIntent)
    assert intent.status == "pending"
    path = tmp_path / "actions.jsonl"
    original = path.read_bytes()
    receipt = ledger.record_receipt(intent.id, "succeeded", owner_id="alice", started_at=intent.created_at, exit_ref="artifact-1")
    assert isinstance(receipt, ActionReceipt)
    assert receipt.intent_id == intent.id
    assert receipt.completed_at >= receipt.started_at >= intent.created_at
    assert receipt.exit_ref == "artifact-1"
    assert path.read_bytes().startswith(original)
    assert len(path.read_bytes().splitlines()) == 2
    assert intent.status == "pending"
    reloaded = ActionLedger(tmp_path)
    assert reloaded.list_intents("alice")[0].status == "succeeded"
    assert reloaded.list_receipts("alice") == [receipt]
    with pytest.raises(FrozenInstanceError):
        intent.owner_id = "bob"
    with pytest.raises(ValueError, match="already completed"):
        reloaded.record_receipt(intent.id, "failed", owner_id="alice")


@pytest.mark.parametrize("outcome,category", [("failed", "timeout"), ("denied", "permission_denied")])
def test_terminal_outcomes(tmp_path, outcome, category):
    ledger = ActionLedger(tmp_path)
    intent = ledger.record_intent("alice", "tool", {})
    receipt = ledger.record_receipt(intent.id, outcome, owner_id="alice", error_category=category)
    assert receipt.error_category == category
    assert ledger.list_intents("alice", status=outcome)[0].status == outcome
    assert ActionLedger(tmp_path).list_receipts("alice", outcome=outcome) == [receipt]


def test_canonical_digest_persists_no_raw_arguments_or_secrets(tmp_path):
    ledger = ActionLedger(tmp_path)
    args = {"password": "secret-password-123", "nested": {"token": "secret-token-456", "values": [True, None, "é"]}}
    intent = ledger.record_intent("alice", "tool", args)
    reordered = {"nested": {"values": [True, None, "é"], "token": "secret-token-456"}, "password": "secret-password-123"}
    other = ledger.record_intent("alice", "tool", reordered)
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    assert intent.arguments_digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert intent.arguments_digest == other.arguments_digest
    assert len(intent.arguments_digest) == 64
    assert ledger.record_intent("alice", "tool", {"different": True}).arguments_digest != intent.arguments_digest
    for path in tmp_path.iterdir():
        text = path.read_text(encoding="utf-8")
        for secret in ("password", "secret-password-123", "nested", "token", "secret-token-456", "values"):
            assert secret not in text
    assert not hasattr(intent, "args")


def test_owner_isolation_and_non_disclosing_receipt_lookup(tmp_path):
    ledger = ActionLedger(tmp_path)
    alice = ledger.record_intent("alice", "tool", {}, thread_id="shared")
    bob = ledger.record_intent("bob", "tool", {}, thread_id="shared")
    receipt = ledger.record_receipt(alice.id, "succeeded", owner_id="alice")
    errors = []
    before = (tmp_path / "actions.jsonl").read_bytes()
    for intent_id in (alice.id, "missing"):
        with pytest.raises(LookupError) as caught:
            ledger.record_receipt(intent_id, "failed", owner_id="bob")
        errors.append(str(caught.value))
    assert errors[0] == errors[1]
    assert (tmp_path / "actions.jsonl").read_bytes() == before
    assert ledger.list_intents("bob", thread_id="shared") == [bob]
    assert ledger.list_receipts("bob", intent_id=alice.id) == []
    assert ledger.list_receipts("alice") == [receipt]
    assert ActionLedger(tmp_path).list_intents("alice")[0].id == alice.id
    assert ActionLedger(tmp_path).list_receipts("bob") == []


@pytest.mark.parametrize("owner", [None, "", " ", " alice ", 42, True])
def test_all_operations_require_valid_explicit_owner(tmp_path, owner):
    ledger = ActionLedger(tmp_path)
    intent = ledger.record_intent("alice", "tool", {})
    before = (tmp_path / "actions.jsonl").read_bytes()
    calls = [
        lambda: ledger.record_intent(owner, "tool", {}),
        lambda: ledger.record_receipt(intent.id, "succeeded", owner_id=owner),
        lambda: ledger.list_intents(owner),
        lambda: ledger.list_receipts(owner),
    ]
    for call in calls:
        with pytest.raises(ValueError, match="owner_id"):
            call()
    assert (tmp_path / "actions.jsonl").read_bytes() == before
    with pytest.raises(TypeError):
        ledger.record_receipt(intent.id, "succeeded")
    with pytest.raises(TypeError):
        ledger.list_intents()
    with pytest.raises(TypeError):
        ledger.list_receipts()


@pytest.mark.parametrize("tail", [b'{"type":"intent","record":', b'{"type":"receipt","record":"\xe2\x82'])
def test_partial_trailing_line_is_skipped_and_later_appends_survive(tmp_path, tail):
    ledger = ActionLedger(tmp_path)
    intent = ledger.record_intent("alice", "tool", {})
    path = tmp_path / "actions.jsonl"
    with path.open("ab") as stream:
        stream.write(tail)
    corrupted = path.read_bytes()
    reloaded = ActionLedger(tmp_path)
    assert reloaded.list_intents("alice") == [intent]
    assert reloaded.list_receipts("alice") == []
    receipt = reloaded.record_receipt(intent.id, "succeeded", owner_id="alice")
    assert path.read_bytes().startswith(corrupted)
    final = ActionLedger(tmp_path)
    assert final.list_receipts("alice") == [receipt]
    assert final.list_intents("alice")[0].status == "succeeded"


def test_bounded_lists_and_filters_apply_before_limit(tmp_path):
    ledger = ActionLedger(tmp_path)
    for index in range(MAX_LIST_LIMIT + 2):
        intent = ledger.record_intent("alice", "even" if index % 2 == 0 else "odd", {}, thread_id=str(index % 2))
        ledger.record_receipt(intent.id, "succeeded" if index % 2 == 0 else "failed", owner_id="alice")
    assert len(ledger.list_intents("alice", limit=MAX_LIST_LIMIT + 100)) == MAX_LIST_LIMIT
    assert len(ledger.list_receipts("alice", limit=MAX_LIST_LIMIT + 100)) == MAX_LIST_LIMIT
    assert len(ledger.list_intents("alice")) == 100
    assert len(ledger.list_receipts("alice")) == 100
    intents = ledger.list_intents("alice", tool_name="even", thread_id="0", status="succeeded", limit=3)
    assert len(intents) == 3
    assert all(intent.tool_name == "even" and intent.thread_id == "0" and intent.status == "succeeded" for intent in intents)
    receipts = ledger.list_receipts("alice", tool_name="odd", thread_id="1", outcome="failed", limit=3)
    assert len(receipts) == 3
    assert all(receipt.outcome == "failed" for receipt in receipts)
    assert ledger.list_receipts("alice", intent_id=intents[0].id, limit=1)[0].intent_id == intents[0].id
    assert ledger.list_intents("alice", limit=0) == []
    assert ledger.list_receipts("alice", limit=0) == []


@pytest.mark.parametrize("limit", [-1, None, True, 1.5, "10"])
def test_invalid_limits_fail_closed(tmp_path, limit):
    ledger = ActionLedger(tmp_path)
    for listing in (ledger.list_intents, ledger.list_receipts):
        with pytest.raises(ValueError, match="limit"):
            listing("alice", limit=limit)


@pytest.mark.parametrize("args", [{"value": float("nan")}, {"value": float("inf")}, {1: "secret"}, {"value": object()}, {"value": (1, 2)}])
def test_non_json_arguments_are_rejected_without_persistence(tmp_path, args):
    ledger = ActionLedger(tmp_path)
    with pytest.raises(ValueError, match="JSON"):
        ledger.record_intent("alice", "tool", args)
    assert ledger.list_intents("alice") == []
    assert not (tmp_path / "actions.jsonl").exists()


def test_receipt_validation_never_persists_raw_error_text(tmp_path):
    ledger = ActionLedger(tmp_path)
    intent = ledger.record_intent("alice", "tool", {})
    before = (tmp_path / "actions.jsonl").read_bytes()
    for kwargs in (
        {"outcome": "unknown"},
        {"outcome": "failed", "error_category": "password=secret-token"},
        {"outcome": "failed", "error_category": "x" * 10000},
        {"outcome": "succeeded", "error_category": "timeout"},
        {"outcome": "failed", "started_at": intent.created_at - 1},
        {"outcome": "failed", "started_at": intent.created_at + 2, "completed_at": intent.created_at + 1},
        {"outcome": "failed", "completed_at": float("nan")},
    ):
        with pytest.raises(ValueError):
            ledger.record_receipt(intent.id, owner_id="alice", **kwargs)
    assert (tmp_path / "actions.jsonl").read_bytes() == before
    assert ledger.list_receipts("alice") == []


def test_same_process_store_instances_serialize_concurrent_operations(tmp_path):
    ledgers = [ActionLedger(tmp_path), ActionLedger(tmp_path / ".")]
    barrier = Barrier(8)

    def work(index):
        barrier.wait(timeout=10)
        ledger = ledgers[index % 2]
        for item in range(10):
            owner = f"owner-{index % 2}"
            intent = ledger.record_intent(owner, "tool", {"item": item})
            ledger.record_receipt(intent.id, "succeeded", owner_id=owner)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(8)))
    for ledger in (*ledgers, ActionLedger(tmp_path)):
        for owner in ("owner-0", "owner-1"):
            assert len(ledger.list_intents(owner)) == 40
            assert len(ledger.list_receipts(owner)) == 40
            assert all(intent.status == "succeeded" for intent in ledger.list_intents(owner))
    records = [json.loads(line) for line in (tmp_path / "actions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(records) == 160


def test_failed_append_does_not_publish_intent_or_receipt(tmp_path, monkeypatch):
    ledger = ActionLedger(tmp_path)
    intent = ledger.record_intent("alice", "tool", {})
    original_open = type(tmp_path).open

    def fail_append(path, mode="r", *args, **kwargs):
        if mode == "ab":
            raise OSError("disk unavailable")
        return original_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(type(tmp_path), "open", fail_append)
        with pytest.raises(OSError):
            ledger.record_intent("alice", "tool", {})
        with pytest.raises(OSError):
            ledger.record_receipt(intent.id, "succeeded", owner_id="alice")
    assert ledger.list_intents("alice") == [intent]
    assert ledger.list_receipts("alice") == []


def test_concurrent_receipts_cannot_complete_an_intent_twice(tmp_path):
    ledgers = [ActionLedger(tmp_path), ActionLedger(tmp_path)]
    intent = ledgers[0].record_intent("alice", "tool", {})
    barrier = Barrier(2)

    def complete(index):
        barrier.wait(timeout=10)
        try:
            return ledgers[index].record_receipt(intent.id, "succeeded", owner_id="alice")
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(complete, range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(ActionLedger(tmp_path).list_receipts("alice")) == 1


def test_replay_rejects_receipts_with_wrong_owner_or_missing_intent(tmp_path):
    ledger = ActionLedger(tmp_path)
    intent = ledger.record_intent("alice", "tool", {})
    path = tmp_path / "actions.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        for owner, intent_id in (("bob", intent.id), ("alice", "missing")):
            envelope = {
                "type": "receipt",
                "owner_id": owner,
                "record": {"intent_id": intent_id, "outcome": "succeeded", "started_at": intent.created_at, "completed_at": intent.created_at},
            }
            stream.write(json.dumps(envelope) + "\n")
    reloaded = ActionLedger(tmp_path)
    assert reloaded.list_intents("alice") == [intent]
    assert reloaded.list_intents("bob") == []
    assert reloaded.list_receipts("alice") == []
    assert reloaded.list_receipts("bob") == []
