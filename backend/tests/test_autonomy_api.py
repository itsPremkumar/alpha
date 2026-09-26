"""API tests for the autonomy router (supervisor status + Sentinel surface).

Covers, with real payloads only:
* GET /api/autonomy/status returns the live AutonomySupervisor payload verbatim
  plus the request-time config block and disclosure notes;
* GET /api/autonomy/sentinel/reports is capped, ordered, and fails closed with
  the file + line when the journal is unreadable (never an empty list);
* POST /api/autonomy/sentinel/run forwards auto_heal/trigger to the real tick
  seam and surfaces tick failures as 500 with the verbatim reason;
* limit validation is 422 out of range, not a silent clamp.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.runtime.sentinel.report_store import SentinelReportStore
from app.gateway.routers import autonomy


@pytest.fixture()
def client() -> TestClient:
    """Bare app + router: the gateway's AuthMiddleware is out of scope here."""
    app = FastAPI()
    app.include_router(autonomy.router)
    return TestClient(app)


# -- GET /api/autonomy/status ------------------------------------------------


def test_status_returns_live_supervisor_payload_verbatim_with_config_block(client: TestClient) -> None:
    from app.gateway.autonomy.supervisor import get_autonomy_supervisor

    expected = get_autonomy_supervisor().status()

    response = client.get("/api/autonomy/status")

    assert response.status_code == 200
    body = response.json()
    assert body["supervisor"] == expected
    # Every registered loop is present with its real counters, not a subset.
    assert set(body["supervisor"]["loops"]) == set(expected["loops"])
    for loop_state in body["supervisor"]["loops"].values():
        for key in ("description", "enabled", "runs", "failures", "parked", "running", "last_error", "task_alive"):
            assert key in loop_state
    # Config block is either the real config.yaml view or an honest error.
    config = body["config"]
    assert isinstance(config["available"], bool)
    if config["available"]:
        assert "autonomy_enabled" in config
        assert isinstance(config["loops"], dict)
    else:
        assert "error" in config
    assert body["notes"] and all(isinstance(note, str) and note for note in body["notes"])


def test_status_notes_flag_master_switch_mismatch_when_present(client: TestClient) -> None:
    from app.gateway.autonomy.supervisor import get_autonomy_supervisor

    status = get_autonomy_supervisor().status()
    body = client.get("/api/autonomy/status").json()
    config = body["config"]
    if config.get("available"):
        mismatched = bool(config.get("autonomy_enabled")) != bool(status.get("enabled"))
        has_note = any("master-switch mismatch" in note for note in body["notes"])
        assert has_note == mismatched


# -- GET /api/autonomy/sentinel/reports --------------------------------------


def _store(tmp_path: Path) -> SentinelReportStore:
    return SentinelReportStore(tmp_path / "sentinel-reports")


def _entry(recorded_at: str, scanned: int = 0) -> dict:
    return {
        "recorded_at": recorded_at,
        "trigger": "test",
        "auto_heal": False,
        "report": {"duration_s": 0.001, "scanned": scanned, "summary": f"scanned {scanned} signal(s)", "fixed": 0, "reverted": 0, "escalated": 0, "errors": [], "outcomes": []},
    }


def test_reports_absent_journal_reads_as_honest_empty_with_disclosures(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(autonomy, "_report_store", lambda: store)

    body = client.get("/api/autonomy/sentinel/reports").json()

    assert body["reports"] == []
    assert body["total"] == 0
    assert body["order"] == "oldest_first"
    assert any("absent" in line for line in body["disclosures"])
    assert any("no passes recorded yet" in line for line in body["disclosures"])


def test_reports_returns_newest_entries_oldest_first_with_cap_disclosure(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    for index in range(5):
        store.append(_entry(f"2026-09-24T00:00:0{index}+00:00", scanned=index))
    monkeypatch.setattr(autonomy, "_report_store", lambda: store)

    body = client.get("/api/autonomy/sentinel/reports", params={"limit": 2}).json()

    assert [entry["report"]["scanned"] for entry in body["reports"]] == [3, 4]
    assert body["total"] == 5
    assert body["cap"] == 2
    assert body["source"].endswith("reports.jsonl")
    assert any("newest 2 of 5" in line for line in body["disclosures"])


def test_reports_fail_closed_on_corrupt_journal_naming_file_and_line(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    store.append(_entry("2026-09-24T00:00:00+00:00"))
    journal = Path(store.path)
    with journal.open("ab") as handle:
        handle.write(b'{"recorded_at": "2026-09-24T00:00:01+00:00", "report": {truncated\n')
    monkeypatch.setattr(autonomy, "_report_store", lambda: store)

    response = client.get("/api/autonomy/sentinel/reports")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "reports.jsonl" in detail
    assert "line 2" in detail
    # Never an empty list standing in for an unreadable history.
    assert response.text != '{"reports": []}'


def test_reports_rejects_out_of_range_limit_with_422(client: TestClient) -> None:
    assert client.get("/api/autonomy/sentinel/reports", params={"limit": 0}).status_code == 422
    assert client.get("/api/autonomy/sentinel/reports", params={"limit": 201}).status_code == 422


# -- POST /api/autonomy/sentinel/run -----------------------------------------


def test_run_forwards_default_auto_heal_false_and_trigger_api(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway.autonomy import loops

    seen: dict = {}

    def fake_tick(**kwargs):
        seen.update(kwargs)
        return {"summary": "scanned 0 signal(s)", "persistence": {"persisted": True, "store": "x"}}

    monkeypatch.setattr(loops, "sentinel_tick", fake_tick)

    response = client.post("/api/autonomy/sentinel/run")

    assert response.status_code == 200
    assert seen == {"auto_heal": False, "trigger": "api"}
    assert response.json()["persistence"]["persisted"] is True


def test_run_forwards_auto_heal_true(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway.autonomy import loops

    seen: dict = {}

    def fake_tick(**kwargs):
        seen.update(kwargs)
        return {"summary": "heal pass", "persistence": {"persisted": True, "store": "x"}}

    monkeypatch.setattr(loops, "sentinel_tick", fake_tick)

    response = client.post("/api/autonomy/sentinel/run", json={"auto_heal": True})

    assert response.status_code == 200
    assert seen["auto_heal"] is True
    assert seen["trigger"] == "api"


def test_run_surfaces_tick_failure_as_500_with_verbatim_reason(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway.autonomy import loops

    def exploding_tick(**kwargs):
        raise RuntimeError("sentinel engine exploded: disk read-only")

    monkeypatch.setattr(loops, "sentinel_tick", exploding_tick)

    response = client.post("/api/autonomy/sentinel/run", json={"auto_heal": False})

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "RuntimeError" in detail
    assert "sentinel engine exploded: disk read-only" in detail


# -- GET /api/autonomy/sentinel/signals --------------------------------------


def test_signals_are_observe_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway.autonomy import loops

    class FakeRunner:
        def __init__(self, repo_root):
            self.repo_root = repo_root

        def collect(self):
            return []


    monkeypatch.setattr(loops, "_resolve_project_root", lambda: Path("."))
    from alpha.runtime.sentinel import runner as runner_module

    monkeypatch.setattr(runner_module, "SentinelRunner", FakeRunner)

    body = client.get("/api/autonomy/sentinel/signals").json()

    assert body["observe_only"] is True
    assert body["fixes_applied"] is False
    assert body["signals"] == []
    assert body["count"] == 0
    assert "no diagnosis, no fixes, no commits" in body["note"]
