"""Contract tests for the evidence + action-ledger REST surface (wave P6).

Pins the honesty contract of ``/api/evidence/*`` and ``/api/action-ledger/*``:

* ``owner_id`` is required and owners are never merged,
* real recorded rows round-trip through the REST layer,
* a corrupt store answers 500 with the store's real reason (never an empty list),
* a readable-but-empty store answers an honest empty count,
* invalid filter values answer 422 with the store's own message.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import evidence as evidence_router


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    # Both default stores resolve `runtime_home()` at CALL time from
    # alpha.config.runtime_paths, so patching that one function redirects every
    # store the routes build.
    import alpha.config.runtime_paths as runtime_paths

    monkeypatch.setattr(runtime_paths, "runtime_home", lambda *a, **k: tmp_path)
    app = FastAPI()
    app.include_router(evidence_router.router)
    return TestClient(app)


def test_owner_id_is_required_on_every_route(client: TestClient) -> None:
    for path in (
        "/api/evidence/records",
        "/api/evidence/candidates",
        "/api/action-ledger/intents",
        "/api/action-ledger/receipts",
    ):
        assert client.get(path).status_code == 422, path


def test_evidence_records_round_trip_and_filter_by_kind(client: TestClient, tmp_path: Path) -> None:
    from alpha.evidence.store import EvidenceStore

    store = EvidenceStore(tmp_path / "evidence")
    store.add_evidence("alice", "observation", "finish-first:msg-1", "a real violation", tags=["finish_first"])
    store.add_evidence("alice", "benchmark", "suite-1", "12 passed")
    store.add_evidence("bob", "trace", "trace-9", "bob's trace")

    everything = client.get("/api/evidence/records", params={"owner_id": "alice"})
    assert everything.status_code == 200
    body = everything.json()
    assert body["owner_id"] == "alice"
    assert body["count"] == 2, "owners are never merged"
    refs = {record["ref"] for record in body["records"]}
    assert refs == {"finish-first:msg-1", "suite-1"}

    observations = client.get("/api/evidence/records", params={"owner_id": "alice", "kind": "observation"})
    assert observations.json()["count"] == 1
    assert observations.json()["records"][0]["kind"] == "observation"

    bobs = client.get("/api/evidence/records", params={"owner_id": "bob"})
    assert bobs.json()["count"] == 1
    assert bobs.json()["records"][0]["ref"] == "trace-9"


def test_candidate_chain_is_exposed(client: TestClient, tmp_path: Path) -> None:
    from alpha.evidence.store import EvidenceStore

    store = EvidenceStore(tmp_path / "evidence")
    evidence = store.add_evidence("alice", "benchmark", "suite-2", "40 passed")
    candidate = store.propose_candidate("alice", "Adopt fast router", [evidence.id])
    evaluation = store.evaluate_candidate(
        candidate.id,
        owner_id="alice",
        score=0.9,
        verdict="promote",
        rationale="measured p95 improvement",
        evaluator="lead",
    )
    store.promote_candidate(candidate.id, evaluation.id, owner_id="alice")

    body = client.get("/api/evidence/candidates", params={"owner_id": "alice"}).json()
    assert body["counts"] == {"candidates": 1, "evaluations": 1, "promotions": 1}
    assert body["candidates"][0]["evidence_ids"] == [evidence.id]


def test_receipts_and_intents_round_trip(client: TestClient, tmp_path: Path) -> None:
    from alpha.ledger.store import ActionLedger

    ledger = ActionLedger(tmp_path / "action-ledger")
    intent = ledger.record_intent("runtime", "emergency_stop_manage", {"action": "engage", "reason": "runaway"})
    ledger.record_receipt(intent.id, "succeeded", owner_id="runtime", exit_ref="C:/state/estop.sentinel")

    intents = client.get("/api/action-ledger/intents", params={"owner_id": "runtime"}).json()
    assert intents["count"] == 1
    assert intents["intents"][0]["status"] == "succeeded", "the receipt closed the intent"
    assert intents["intents"][0]["arguments_digest"], "arguments are stored as a digest, not raw text"

    receipts = client.get("/api/action-ledger/receipts", params={"owner_id": "runtime", "outcome": "succeeded"}).json()
    assert receipts["count"] == 1
    assert receipts["receipts"][0]["exit_ref"] == "C:/state/estop.sentinel"

    failed_filter = client.get("/api/action-ledger/receipts", params={"owner_id": "runtime", "outcome": "failed"})
    assert failed_filter.json()["count"] == 0

    invalid = client.get("/api/action-ledger/receipts", params={"owner_id": "runtime", "outcome": "banana"})
    assert invalid.status_code == 422
    assert "outcome" in invalid.json()["detail"]


def test_empty_store_is_an_honest_empty_answer(client: TestClient) -> None:
    body = client.get("/api/action-ledger/receipts", params={"owner_id": "nobody"}).json()
    assert body["count"] == 0
    assert body["receipts"] == []


def test_corrupt_evidence_store_fails_closed_with_the_real_reason(client: TestClient, tmp_path: Path) -> None:
    store_dir = tmp_path / "evidence"
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "evidence.json").write_text("{ this is not json", encoding="utf-8")

    resp = client.get("/api/evidence/records", params={"owner_id": "alice"})
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "Invalid evidence store" in detail
    # The corrupt bytes are never silently repaired away by the API layer.
    assert json.loads is not None
    assert (store_dir / "evidence.json").read_text(encoding="utf-8") == "{ this is not json"
