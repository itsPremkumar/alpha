"""The evidence / action-ledger plane must speak ISO 8601, and must publish an
explicit projection rather than a raw ``__dict__``.

Two defects, one root cause
---------------------------
``EvidenceRecord.created_at``, ``Evaluation.created_at``, ``Promotion.promoted_at``,
``ActionIntent.created_at`` and ``ActionReceipt.started_at`` / ``completed_at``
are all declared ``float`` with a ``time.time()`` default, and all four Gateway
routes serialised the dataclass ``__dict__`` verbatim. So:

1. A raw epoch went on the wire next to every sibling Gateway route that emits
   ISO 8601. A client parsing the field as a date cannot read a float.
2. ``__dict__`` published dataclass *internals* as the HTTP contract.
   ``EvidenceRecord.tags`` is normalised to a ``tuple`` by ``__post_init__``, so
   the tuple leaked out; and because the shape was "whatever the dataclass
   happens to hold", adding a field to the dataclass silently became a public
   API change, with ``__post_init__``'s sanitisation as the documented response.

The fix is an explicit projection at the API boundary. The stores on disk keep
the float, so every existing record still loads - these tests pin both halves.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import evidence as evidence_router


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    # Both default stores resolve `runtime_home()` at CALL time, so patching
    # that one function redirects every store the routes build.
    import alpha.config.runtime_paths as runtime_paths

    monkeypatch.setattr(runtime_paths, "runtime_home", lambda *a, **k: tmp_path)
    app = FastAPI()
    app.include_router(evidence_router.router)
    return TestClient(app)


def _assert_iso(value: object, where: str) -> None:
    assert isinstance(value, str), f"{where} is {type(value).__name__}, not a string: {value!r}"
    assert value, f"{where} is an empty string"
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, f"{where} is not an aware ISO timestamp: {value!r}"


def _assert_no_raw_epoch(payload: object, where: str = "payload") -> None:
    """No timestamp-named field may still be a bare JSON number."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and (key.endswith("_at") or key.endswith("_at_")):
                raise AssertionError(f"{where}.{key} is still a raw epoch number: {value!r}")
            _assert_no_raw_epoch(value, f"{where}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _assert_no_raw_epoch(item, f"{where}[{index}]")


def _seed(tmp_path: Path) -> None:
    from alpha.evidence.store import EvidenceStore
    from alpha.ledger.store import ActionLedger

    evidence = EvidenceStore(tmp_path / "evidence")
    record = evidence.add_evidence("alice", "observation", "finish-first:msg-1", "a real violation", tags=["finish_first", "handoff"])
    candidate = evidence.propose_candidate("alice", "Adopt fast router", [record.id])
    evaluation = evidence.evaluate_candidate(candidate.id, owner_id="alice", score=0.9, verdict="promote", rationale="measured p95 improvement", evaluator="lead")
    evidence.promote_candidate(candidate.id, evaluation.id, owner_id="alice")

    ledger = ActionLedger(tmp_path / "action-ledger")
    intent = ledger.record_intent("alice", "emergency_stop_manage", {"action": "engage", "reason": "runaway"})
    ledger.record_receipt(intent.id, "succeeded", owner_id="alice", exit_ref="artifact-1")


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------


def test_evidence_record_created_at_is_iso(client: TestClient, tmp_path: Path) -> None:
    _seed(tmp_path)
    body = client.get("/api/evidence/records", params={"owner_id": "alice"}).json()
    records = body["records"]
    assert records, "no records were returned"
    for record in records:
        _assert_iso(record["created_at"], f"records[{record['ref']}].created_at")
    _assert_no_raw_epoch(body, "evidence/records")


def test_candidate_chain_timestamps_are_iso(client: TestClient, tmp_path: Path) -> None:
    _seed(tmp_path)
    body = client.get("/api/evidence/candidates", params={"owner_id": "alice"}).json()

    for evaluation in body["evaluations"]:
        _assert_iso(evaluation["created_at"], "evaluations[].created_at")
    for promotion in body["promotions"]:
        _assert_iso(promotion["promoted_at"], "promotions[].promoted_at")
    _assert_no_raw_epoch(body, "evidence/candidates")


def test_action_intent_created_at_is_iso(client: TestClient, tmp_path: Path) -> None:
    _seed(tmp_path)
    body = client.get("/api/action-ledger/intents", params={"owner_id": "alice"}).json()
    intents = body["intents"]
    assert intents, "no intents were returned"
    for intent in intents:
        _assert_iso(intent["created_at"], "intents[].created_at")
    _assert_no_raw_epoch(body, "action-ledger/intents")


def test_action_receipt_timestamps_are_iso(client: TestClient, tmp_path: Path) -> None:
    _seed(tmp_path)
    body = client.get("/api/action-ledger/receipts", params={"owner_id": "alice"}).json()
    receipts = body["receipts"]
    assert receipts, "no receipts were returned"
    for receipt in receipts:
        _assert_iso(receipt["started_at"], "receipts[].started_at")
        _assert_iso(receipt["completed_at"], "receipts[].completed_at")
        assert datetime.fromisoformat(receipt["completed_at"]) >= datetime.fromisoformat(receipt["started_at"]), "coercion inverted the measured ordering"
    _assert_no_raw_epoch(body, "action-ledger/receipts")


def test_the_kind_filter_path_is_coerced_too(client: TestClient, tmp_path: Path) -> None:
    """A separate branch in the route; it must not be a second convention."""
    _seed(tmp_path)
    body = client.get("/api/evidence/records", params={"owner_id": "alice", "kind": "observation"}).json()
    assert body["records"], "the kind filter returned nothing"
    for record in body["records"]:
        _assert_iso(record["created_at"], "filtered.records[].created_at")


# ---------------------------------------------------------------------------
# The explicit projection: no `__dict__`, no tuples, no post-init leakage
# ---------------------------------------------------------------------------


def test_tags_go_out_as_a_json_array_not_a_tuple(client: TestClient, tmp_path: Path) -> None:
    """`__post_init__` normalises `tags` to a tuple; the wire shape is an array.

    JSON serialised the tuple to an array anyway, so this is not a wire break -
    the point is that the published shape is now stated rather than inherited.
    """
    _seed(tmp_path)
    record = next(r for r in client.get("/api/evidence/records", params={"owner_id": "alice"}).json()["records"] if r["ref"] == "finish-first:msg-1")
    assert isinstance(record["tags"], list), f"tags is {type(record['tags']).__name__}, not a list"
    assert record["tags"] == ["finish_first", "handoff"]


def test_evidence_ids_go_out_as_a_json_array(client: TestClient, tmp_path: Path) -> None:
    _seed(tmp_path)
    candidate = client.get("/api/evidence/candidates", params={"owner_id": "alice"}).json()["candidates"][0]
    assert isinstance(candidate["evidence_ids"], list)
    assert len(candidate["evidence_ids"]) == 1


def test_the_published_shape_is_exactly_the_declared_one(client: TestClient, tmp_path: Path) -> None:
    """An explicit projection, not "whatever `__dict__` holds today".

    This is the assertion that makes a future dataclass field a deliberate
    decision: adding one to the model no longer silently changes the API.
    """
    _seed(tmp_path)
    records = client.get("/api/evidence/records", params={"owner_id": "alice"}).json()["records"]
    assert set(records[0]) == {"id", "owner_id", "kind", "ref", "summary", "created_at", "tags"}

    chain = client.get("/api/evidence/candidates", params={"owner_id": "alice"}).json()
    assert set(chain["candidates"][0]) == {"id", "owner_id", "title", "evidence_ids", "status"}
    assert set(chain["evaluations"][0]) == {"id", "candidate_id", "score", "verdict", "rationale", "evaluator", "created_at"}
    assert set(chain["promotions"][0]) == {"candidate_id", "evaluation_id", "promoted_at"}

    intents = client.get("/api/action-ledger/intents", params={"owner_id": "alice"}).json()["intents"]
    assert set(intents[0]) == {"id", "owner_id", "tool_name", "arguments_digest", "created_at", "thread_id", "status"}

    receipts = client.get("/api/action-ledger/receipts", params={"owner_id": "alice"}).json()["receipts"]
    assert set(receipts[0]) == {"intent_id", "outcome", "started_at", "completed_at", "error_category", "exit_ref"}


def test_private_dataclass_internals_are_not_published(client: TestClient, tmp_path: Path) -> None:
    """`frozen=True` dataclasses carry `__dataclass_fields__` and friends.

    Under `__dict__` the wire was whatever Python happened to store; the
    projection is the contract now.
    """
    _seed(tmp_path)
    body = client.get("/api/evidence/records", params={"owner_id": "alice"}).json()
    forbidden = {"__dataclass_fields__", "__dataclass_params__", "_abc_impl", "__weakref__", "__dict__"}
    for record in body["records"]:
        assert not (set(record) & forbidden), f"a dataclass internal leaked onto the wire: {sorted(set(record) & forbidden)}"


# ---------------------------------------------------------------------------
# Persistence is untouched
# ---------------------------------------------------------------------------


def test_the_stored_record_keeps_the_float(tmp_path: Path) -> None:
    """Rewriting the stored type would break every evidence store on disk.

    The store is stricter than that: ``_records`` (evidence/store.py:42-47)
    requires the persisted dict's key set to equal the dataclass field set and
    re-serialises the loaded record to compare, so a changed stored type is a
    hard "Invalid record", not a silent migration.
    """
    from alpha.evidence.store import EvidenceStore

    store = EvidenceStore(tmp_path / "evidence")
    store.add_evidence("alice", "observation", "ref-1", "summary", tags=["finish_first"])

    path = tmp_path / "evidence" / "evidence.json"
    assert path.exists(), f"the store wrote nothing at {path}"
    stored = json.loads(path.read_text(encoding="utf-8"))
    row = next(iter(stored["evidence"].values()))
    assert isinstance(row["created_at"], float), f"the stored timestamp changed shape to {type(row['created_at']).__name__}"
    # `asdict` renders the tuple as a JSON array on disk; the loader compares
    # against that same rendering, so this shape is load-bearing too.
    assert isinstance(row["tags"], list) and row["tags"] == ["finish_first"]

    # And the store still loads its own output.
    reloaded = EvidenceStore(tmp_path / "evidence").list_evidence("alice")
    assert len(reloaded) == 1
    assert isinstance(reloaded[0].created_at, float)


def test_a_legacy_float_store_still_loads(tmp_path: Path) -> None:
    """A store written before the fix must still construct from disk."""
    from alpha.evidence.models import EvidenceRecord
    from alpha.evidence.store import EvidenceStore

    store_dir = tmp_path / "evidence"
    store_dir.mkdir(parents=True, exist_ok=True)
    legacy = {
        "version": 1,
        "evidence": {
            "ev-legacy": {
                "id": "ev-legacy",
                "owner_id": "alice",
                "kind": "observation",
                "ref": "ref-legacy",
                "summary": "recorded before the fix",
                "created_at": 1790528973.1918123,
                "tags": ["finish_first"],
            }
        },
        "candidates": {},
        "evaluations": {},
        "promotions": {},
    }
    (store_dir / "evidence.json").write_text(json.dumps(legacy), encoding="utf-8")

    loaded = EvidenceStore(store_dir).list_evidence("alice")
    assert len(loaded) == 1
    assert isinstance(loaded[0], EvidenceRecord)
    assert isinstance(loaded[0].created_at, float), "the stored timestamp must stay a float"
    assert loaded[0].created_at == pytest.approx(1790528973.1918123)


# ---------------------------------------------------------------------------
# The zero case
# ---------------------------------------------------------------------------


def test_the_zero_epoch_decision_for_this_plane() -> None:
    """Why there is no zero-translation here, stated as an executable claim.

    Every timestamp routed through this file is a REQUIRED stamp: the models
    validate it with ``validate_timestamp`` (finite, non-negative) at
    construction, and the stores write ``time.time()``. None of them has a
    "never happened" state, so there is no ``0.0`` sentinel to translate, and
    inventing one would be less honest than rendering the real epoch the record
    claims. The moment a field here gains a ``0.0``-means-never meaning, THIS
    test is what should change - deliberately, with the field named.
    """
    from alpha.evidence.models import EvidenceRecord
    from app.gateway.routers.evidence import _ts

    record = EvidenceRecord(id="ev-1", owner_id="alice", kind="observation", ref="r", summary="s", created_at=1790528973.1918123)
    assert record.created_at > 0, "the store always writes a real time.time(); a zero default would be a new contract"

    # A stored 0.0 is the epoch it says it is.
    assert _ts(0.0) == "1970-01-01T00:00:00+00:00"
    # A real epoch renders as a real, parseable, UTC date.
    assert _ts(1790528973.1918123) == "2026-09-27T17:09:33.191812+00:00"
    assert datetime.fromisoformat(_ts(1790528973.1918123)).tzinfo is not None


def test_validate_timestamp_still_rejects_a_negative_stamp() -> None:
    """The zero decision must not have loosened model validation."""
    import pytest as _pytest

    from alpha.evidence.models import validate_timestamp

    validate_timestamp(0.0)  # legal: the epoch is a legal timestamp
    with _pytest.raises(ValueError):
        validate_timestamp(-1.0)
