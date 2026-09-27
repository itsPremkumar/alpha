"""The war-room REST surface: what a caller is told, and what it is not.

These routes are the only way a person can see what a deliberation actually did,
so the tests here are about *honesty under failure* as much as about shape:

- a corrupt ``run.json`` must be **disclosed**, not silently absent from the list;
- a run that never reached a synthesis must not read as a clean pass;
- ``verification`` must never say "verified", because a quorum established that
  agents agreed, not that they were right;
- a path-traversal attempt in ``run_id`` must be refused, not resolved;
- the trigger pre-flight must not mutate anything.

No model is ever contacted: participants are test doubles and the deliberation
engine is never invoked from a read route.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from alpha.bots.events import OrgEventStore
from alpha.commands import channel_ops
from alpha.groups.war_room import WarRoom, build_default_config
from app.gateway.app import create_app
from app.gateway.routers import war_rooms

pytestmark = pytest.mark.asyncio


@pytest.fixture
def runtime_root(tmp_path, monkeypatch):
    root = tmp_path / "war_room"
    monkeypatch.setattr(channel_ops, "runtime_root", lambda *a, **k: root)
    monkeypatch.setattr(war_rooms, "_root", lambda: root)
    return root


async def _seed(root: Path, *, topic: str = "should we migrate") -> str:
    """Run one real room with test participants and return its run id."""

    async def alice(ctx):
        return f"alice\nSTATED CLAIMS: adopt postgres | keep the ops team\nSELF CONFIDENCE: 0.8"

    async def bob(ctx):
        return f"bob\nSTATED CLAIMS: adopt postgres\nSELF CONFIDENCE: 0.7"

    async def moderator(ctx):
        return "DECISION: adopt postgres"

    config = build_default_config(topic, ["alice", "bob"], stage_timeout_seconds=2.0, stage_grace_seconds=0.5)
    room = WarRoom(
        config,
        participants={"alice": alice, "bob": bob},
        moderator=moderator,
        room="api-room",
        root=root,
        ledger_store=OrgEventStore(root / "events.jsonl"),
        clock=time.monotonic,
    )
    run = await room.execute()
    return run.run_id


@pytest.fixture
def client(runtime_root):
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------- list
async def test_list_is_empty_before_any_room_ran(client):
    response = client.get("/api/war-rooms")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["count"] == 0
    assert payload["runs"] == []


async def test_list_returns_a_seeded_run_with_a_verification_label(client, runtime_root):
    run_id = await _seed(runtime_root)
    payload = client.get("/api/war-rooms").json()
    assert payload["count"] == 1
    record = payload["runs"][0]
    assert record["run_id"] == run_id
    assert record["status"] == "succeeded"
    # The label must never overstate what a quorum established.
    assert record["verification"] == "consensus_supported"
    assert "verified" not in json.dumps(record).lower().replace("unverified", "")


async def test_list_can_be_scoped_to_one_room(client, runtime_root):
    await _seed(runtime_root)
    assert client.get("/api/war-rooms", params={"room": "api-room"}).json()["count"] == 1
    assert client.get("/api/war-rooms", params={"room": "other-room"}).json()["count"] == 0


async def test_list_clamps_the_limit(client, runtime_root):
    await _seed(runtime_root)
    # A hostile or fat-fingered limit must not be honoured verbatim.
    assert len(client.get("/api/war-rooms", params={"limit": 0}).json()["runs"]) == 1
    assert len(client.get("/api/war-rooms", params={"limit": 99999}).json()["runs"]) == 1


# ---------------------------------------------------------------- one run
async def test_one_run_carries_its_evidence(client, runtime_root):
    run_id = await _seed(runtime_root)
    response = client.get(f"/api/war-rooms/{run_id}", params={"room": "api-room"})
    assert response.status_code == 200
    run = response.json()["run"]
    assert run["run_id"] == run_id
    assert run["synthesis"]
    assert run["stages"], "a run must expose its stages"
    first = run["stages"][0]
    assert first["quorum"]["consensus"]["agreeing"] == ["alice", "bob"]
    assert first["quorum"]["consensus"]["agreed_claims"] == ["adopt postgres"]
    # Receipts carry the claims that were parsed, and the taint verdict.
    receipt = next(r for r in first["receipts"] if r["participant"] == "alice")
    assert receipt["claims"] == ["adopt postgres", "keep the ops team"]
    assert receipt["taint"] == "clean"
    assert receipt["tainted"] is False


async def test_a_missing_run_is_a_404_with_the_id_in_the_reason(client):
    response = client.get("/api/war-rooms/wrun_nope")
    assert response.status_code == 404
    assert "wrun_nope" in response.json()["detail"]


@pytest.mark.parametrize("run_id", ["../escape", "..%2Fescape", "a/b", "a\\b", ".hidden", ""])
async def test_a_traversal_attempt_in_run_id_is_refused_not_resolved(client, run_id):
    response = client.get(f"/api/war-rooms/{run_id}")
    assert response.status_code in (400, 404), f"{run_id!r} produced {response.status_code}"


# ---------------------------------------------------------------- corrupt state
async def test_a_corrupt_record_is_disclosed_rather_than_hidden(client, runtime_root):
    broken = runtime_root / "api-room" / "wrun_broken"
    broken.mkdir(parents=True)
    (broken / "run.json").write_text('{"run_id": "wrun_broken", "status": "suc', encoding="utf-8")

    payload = client.get("/api/war-rooms", params={"room": "api-room"}).json()
    assert payload["count"] == 1
    record = payload["runs"][0]
    assert record["status"] == "unreadable"
    assert record["error"]
    assert record["verification"] == "unverified"

    detail = client.get("/api/war-rooms/wrun_broken", params={"room": "api-room"})
    assert detail.status_code == 200
    assert detail.json()["run"]["status"] == "unreadable"


# ---------------------------------------------------------------- transcript
async def test_transcript_reports_whether_it_is_actually_gap_free(client, runtime_root):
    run_id = await _seed(runtime_root)
    response = client.get(f"/api/war-rooms/{run_id}/transcript", params={"room": "api-room"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] > 0
    assert payload["gap_free"] is True
    assert all("seq" in message for message in payload["messages"])


async def test_transcript_surfaces_a_sequence_gap(client, runtime_root):
    run_id = await _seed(runtime_root)
    path = runtime_root / "api-room" / run_id / "transcript.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    # Drop the middle of the sequence: the check must notice.
    path.write_text("\n".join([lines[0], *lines[2:]]) + "\n", encoding="utf-8")

    payload = client.get(f"/api/war-rooms/{run_id}/transcript", params={"room": "api-room"}).json()
    assert payload["gap_free"] is False


async def test_a_missing_transcript_is_a_404(client):
    assert client.get("/api/war-rooms/wrun_nope/transcript").status_code == 404


# ---------------------------------------------------------------- analytics
async def test_analytics_totals_the_runs(client, runtime_root):
    await _seed(runtime_root)
    payload = client.get("/api/war-rooms/analytics").json()
    assert payload["runs"] == 1
    assert payload["unreadable"] == 0
    assert payload["by_status"]["succeeded"] == 1
    assert payload["duration_seconds"]["count"] == 1
    assert payload["duration_seconds"]["p95"] >= 0


async def test_analytics_counts_a_corrupt_record_separately(client, runtime_root):
    broken = runtime_root / "api-room" / "wrun_broken"
    broken.mkdir(parents=True)
    (broken / "run.json").write_text("{not json", encoding="utf-8")
    payload = client.get("/api/war-rooms/analytics").json()
    assert payload["unreadable"] == 1
    assert payload["runs"] == 0


async def test_analytics_on_an_empty_installation_is_zero_not_an_error(client):
    payload = client.get("/api/war-rooms/analytics").json()
    assert payload["runs"] == 0
    assert payload["duration_seconds"] == {"count": 0, "p50": 0.0, "p95": 0.0, "max": 0.0}


# ---------------------------------------------------------------- trigger
async def test_the_trigger_policy_reports_its_conservative_defaults(client):
    policy = client.get("/api/war-rooms/trigger-policy").json()["policy"]
    assert policy["enabled"] is False, "auto-triggering must ship off"
    assert policy["require_interactive"] is True
    assert policy["max_rooms_per_turn"] == 1
    assert "red_team" in policy["human_only_strategies"]


async def test_evaluate_refuses_an_empty_topic(client):
    response = client.post("/api/war-rooms/evaluate", json={"topic": ""})
    assert response.status_code == 422


async def test_evaluate_is_read_only_and_explains_itself(client, runtime_root):
    before = sorted(p.name for p in runtime_root.rglob("*")) if runtime_root.exists() else []

    response = client.post(
        "/api/war-rooms/evaluate",
        json={"topic": "we need to drop table users in production", "simulate_enabled": True},
    )
    assert response.status_code == 200
    decision = response.json()["decision"]
    assert decision["rationale"]
    assert decision["gate"]
    # Destructive work is exactly the case the trigger exists to catch.
    assert decision["open_room"] is True
    assert decision["risk"] in {"high", "critical"}

    after = sorted(p.name for p in runtime_root.rglob("*")) if runtime_root.exists() else []
    assert before == after, "evaluate must not write anything"


async def test_evaluate_reports_the_disabled_default_when_not_simulating(client):
    decision = client.post("/api/war-rooms/evaluate", json={"topic": "drop table users in production", "simulate_enabled": False}).json()["decision"]
    assert decision["open_room"] is False
    assert decision["gate"] == "policy_disabled"


# ---------------------------------------------------------------- verification helper
def test_verification_never_reports_a_verified_run():
    for record in (
        {"status": "succeeded"},
        {"status": "partial", "tainted": False},
        {"status": "succeeded", "tainted": True},
        {"status": "failed"},
        {"status": "timeout"},
        {"status": "cancelled"},
        {"status": "unreadable"},
    ):
        label = war_rooms._verification(record)
        assert label != "verified", record
        assert label in {"consensus_supported", "consensus_degraded", "tainted", "unverified"}


def test_a_degraded_run_is_not_labelled_a_clean_consensus():
    assert war_rooms._verification({"status": "partial", "tainted": False}) == "consensus_degraded"
    assert war_rooms._verification({"status": "succeeded", "tainted": True}) == "tainted"
