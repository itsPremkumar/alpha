"""API tests for the advanced Sentinel surface added beside the original one.

Covers, with real payloads only:

* ``GET /api/autonomy/sentinel/analytics`` folds the real journal and fails
  closed on a corrupt one — exactly like ``/reports``;
* the fold is bounded and discloses its window, so a capped total can never be
  quoted as the whole history;
* ``GET /api/autonomy/sentinel/kinds`` declares the repair posture and says in
  words that a repair function is wired only for a repair pass;
* ``GET /api/autonomy/sentinel/escalations`` reads the human-handoff store and
  answers 503 with the reason when it cannot be read — never an empty queue;
* the two decision routes are admin-gated, refuse a caller-sent actor that
  disagrees with the authenticated identity, and 404 a non-Sentinel record.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def _store(tmp_path: Path) -> SentinelReportStore:
    return SentinelReportStore(tmp_path / "sentinel-reports")


def _entry(recorded_at: str, *, outcomes: list[dict] | None = None, **report: Any) -> dict:
    body: dict[str, Any] = {"summary": "s", "fixed": 0, "reverted": 0, "escalated": 0, "errors": []}
    body.update(report)
    if outcomes is not None:
        body["outcomes"] = outcomes
    return {"recorded_at": recorded_at, "trigger": "test", "auto_heal": False, "report": body}


def _outcome(kind: str, status: str, fingerprint: str = "fp1") -> dict:
    return {"fingerprint": fingerprint, "kind": kind, "stage": "diagnose", "status": status, "detail": ""}


# -- GET /sentinel/analytics -------------------------------------------------


def test_analytics_folds_the_real_journal(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    store.append(
        _entry(
            "2026-10-01T00:00:00+00:00",
            scanned=3,
            duration_s=1.5,
            outcomes=[_outcome("missing_bom", "fixed", fingerprint="fp-a")],
        )
    )
    store.append(
        _entry(
            "2026-10-01T00:01:00+00:00",
            scanned=2,
            outcomes=[_outcome("missing_bom", "escalated", fingerprint="fp-a")],
        )
    )
    monkeypatch.setattr(autonomy, "_report_store", lambda: store)

    body = client.get("/api/autonomy/sentinel/analytics").json()

    a = body["analytics"]
    assert a["passes"] == 2
    assert a["scanned_total"] == 5
    assert a["outcome_count"] == 2
    assert a["distinct_fingerprints"] == 1
    assert [r["fingerprint"] for r in a["repeats"]] == ["fp-a"]
    assert [k["kind"] for k in a["kinds"]] == ["missing_bom"]
    assert a["kinds"][0]["verdict"] == "repaired"
    assert a["kinds"][0]["fixed"] == 1 and a["kinds"][0]["escalated"] == 1
    assert a["duration_measured_passes"] == 1
    assert body["total_on_disk"] == 2
    assert body["limit"] == 50


def test_analytics_absent_journal_is_an_honest_zero_not_a_fabricated_one(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autonomy, "_report_store", lambda: _store(tmp_path))

    body = client.get("/api/autonomy/sentinel/analytics").json()

    a = body["analytics"]
    assert a["passes"] == 0
    assert a["scanned_total"] is None
    assert a["duration_mean_s"] is None
    assert a["kinds"] == []
    assert any("no passes were folded" in line for line in a["disclosures"])


def test_analytics_fails_closed_on_a_corrupt_journal_naming_file_and_line(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    store.append(_entry("2026-10-01T00:00:00+00:00"))
    journal = Path(store.path)
    with journal.open("ab") as handle:
        handle.write(b'{"recorded_at": "2026-10-01T00:00:01+00:00", "report": {truncated\n')
    monkeypatch.setattr(autonomy, "_report_store", lambda: store)

    response = client.get("/api/autonomy/sentinel/analytics")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "reports.jsonl" in detail
    assert "line 2" in detail


def test_analytics_discloses_the_window_a_cap_excluded(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    for index in range(5):
        store.append(_entry(f"2026-10-01T00:00:0{index}+00:00", scanned=1))
    monkeypatch.setattr(autonomy, "_report_store", lambda: store)

    body = client.get("/api/autonomy/sentinel/analytics", params={"limit": 2}).json()

    a = body["analytics"]
    assert a["passes"] == 2
    assert a["cap"] == 2
    assert a["dropped_by_cap"] == 3
    # A capped window must not read as the whole journal.
    assert a["scanned_total"] == 2
    assert any("3 older pass(es) were excluded" in line for line in a["disclosures"])
    assert body["total_on_disk"] == 5


def test_analytics_rejects_out_of_range_limit_with_422(client: TestClient) -> None:
    assert client.get("/api/autonomy/sentinel/analytics", params={"limit": 0}).status_code == 422
    assert client.get("/api/autonomy/sentinel/analytics", params={"limit": 201}).status_code == 422


# -- GET /sentinel/kinds -----------------------------------------------------


def test_kinds_declares_the_repair_posture_and_when_it_is_wired(client: TestClient) -> None:
    body = client.get("/api/autonomy/sentinel/kinds").json()

    kinds = {k["kind"]: k for k in body["kinds"]}
    assert "missing_bom" in kinds
    assert kinds["missing_bom"]["repair_registered"] is True
    assert kinds["syntax_error"]["repair_registered"] is False
    # The load-bearing disclosure: an observe pass registers nothing, so every
    # signal escalates. Without this line a reader blames the strategy.
    assert any("observe pass registers none" in line for line in body["disclosures"])
    assert any("auto_heal=true" in line for line in body["disclosures"])
    assert body["verification_commands"], "the repair gate's checks are declared"
    assert any(k["recognised"] is False for k in body["kinds"]) is False


def test_kinds_reports_the_registry_not_a_scan(client: TestClient) -> None:
    body = client.get("/api/autonomy/sentinel/kinds").json()

    assert any("not a scan of the repository" in line for line in body["disclosures"])
    assert Path(body["source_root"]).is_dir()


# -- GET /sentinel/escalations ----------------------------------------------


class _FakeRecord:
    """A stand-in for ``EscalationRecord`` with the fields the routes read."""

    def __init__(self, *, escalation_id: str = "esc-1", domain: str = "sentinel", status: str = "open", created_at: float = 1.0) -> None:
        self.escalation_id = escalation_id
        self.domain = domain
        self.status = status
        self.created_at = created_at
        self.acknowledged_by: str | None = None
        self.resolution = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "escalation_id": self.escalation_id,
            "domain": self.domain,
            "status": self.status,
            "created_at": self.created_at,
            "acknowledged_by": self.acknowledged_by,
            "resolution": self.resolution,
        }


class _FakeEscalationStore:
    """The two reads and the two transitions the routes actually call."""

    def __init__(self, rows: list[_FakeRecord], *, error: Exception | None = None) -> None:
        self._rows = rows
        self._error = error
        self.path = Path("C:/state/handoff-ledger/escalations.json")

    def list(self, *, status: str | None = None, domain: str | None = None) -> list[_FakeRecord]:
        if self._error is not None:
            raise self._error
        rows = list(self._rows)
        if status is not None:
            rows = [r for r in rows if r.status == status]
        if domain is not None:
            rows = [r for r in rows if r.domain == domain]
        return sorted(rows, key=lambda r: r.created_at)

    def _find(self, escalation_id: str) -> _FakeRecord | None:
        return next((r for r in self._rows if r.escalation_id == escalation_id), None)

    def acknowledge(self, escalation_id: str, *, by: str) -> _FakeRecord | None:
        record = self._find(escalation_id)
        if record is None:
            return None
        record.status = "acknowledged"
        record.acknowledged_by = by
        return record

    def resolve(self, escalation_id: str, *, by: str, note: str = "") -> _FakeRecord | None:
        record = self._find(escalation_id)
        if record is None:
            return None
        record.status = "resolved"
        record.acknowledged_by = by
        record.resolution = note
        return record


def _install_store(monkeypatch: pytest.MonkeyPatch, store: Any) -> None:
    from alpha.runtime import escalation as escalation_module

    monkeypatch.setattr(escalation_module, "get_escalation_store", lambda: store)


def test_escalations_lists_sentinel_handoffs_oldest_first(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_store(monkeypatch, _FakeEscalationStore([_FakeRecord(created_at=2.0), _FakeRecord(created_at=1.0)]))

    body = client.get("/api/autonomy/sentinel/escalations").json()

    assert body["total"] == 2
    assert body["returned"] == 2
    assert body["truncated"] is False
    assert [r["created_at"] for r in body["escalations"]] == [1.0, 2.0]
    assert any("oldest first" in line for line in body["disclosures"])
    assert any("handoff, not a fault count" in line for line in body["disclosures"])


def test_escalations_disclose_a_truncated_read(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_FakeRecord(created_at=float(i)) for i in range(4)]
    _install_store(monkeypatch, _FakeEscalationStore(rows))

    body = client.get("/api/autonomy/sentinel/escalations", params={"limit": 2}).json()

    assert body["total"] == 4
    assert body["returned"] == 2
    assert body["truncated"] is True
    assert any("2 older record(s)" in line for line in body["disclosures"])


def test_escalations_empty_queue_says_nothing_is_waiting(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_store(monkeypatch, _FakeEscalationStore([]))

    body = client.get("/api/autonomy/sentinel/escalations").json()

    assert body["escalations"] == []
    assert body["total"] == 0


def test_escalations_unreadable_store_is_503_never_an_empty_queue(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from alpha.runtime.escalation import LedgerError

    _install_store(monkeypatch, _FakeEscalationStore([], error=LedgerError("failed to read escalation store x: bad json")))

    response = client.get("/api/autonomy/sentinel/escalations")

    assert response.status_code == 503
    assert "failed to read escalation store" in response.json()["detail"]
    # "Could not look" must never be rendered as "nothing is waiting".
    assert response.text != '{"escalations": []}'


def test_escalations_reject_out_of_range_limit_with_422(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_store(monkeypatch, _FakeEscalationStore([]))

    assert client.get("/api/autonomy/sentinel/escalations", params={"limit": 0}).status_code == 422
    assert client.get("/api/autonomy/sentinel/escalations", params={"limit": 201}).status_code == 422


# -- the two decision routes -------------------------------------------------


class _State:
    def __init__(self, user: Any) -> None:
        self.user = user


class _User:
    def __init__(self, username: str | None) -> None:
        if username is not None:
            self.username = username


class _Request:
    def __init__(self, username: str | None) -> None:
        self.state = _State(_User(username))


async def _always_admin(request: Any) -> bool:
    return True


def _route_request(request: _Request) -> Any:
    """Hand the fake request to a handler that expects FastAPI's DI object."""
    return request


def test_acknowledge_refuses_a_non_admin_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway import deps

    async def never_admin(request: Any) -> bool:
        return False

    monkeypatch.setattr(deps, "is_admin_user", never_admin)
    _install_store(monkeypatch, _FakeEscalationStore([_FakeRecord()]))

    response = client.post(
        "/api/autonomy/sentinel/escalations/esc-1/acknowledge",
        json={"by": "operator"},
    )

    assert response.status_code == 403
    assert "admin session required" in response.json()["detail"]


def test_acknowledge_applies_and_re_reads_the_record(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway import deps

    monkeypatch.setattr(deps, "is_admin_user", _always_admin)
    _install_store(monkeypatch, _FakeEscalationStore([_FakeRecord(status="open")]))
    monkeypatch.setattr(autonomy, "_escalation_actor", lambda request: "operator")

    response = client.post(
        "/api/autonomy/sentinel/escalations/esc-1/acknowledge",
        json={"by": "operator"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is True
    # A decision is a decision: it changes no engine state and repairs nothing.
    assert "no repair was performed" in body["note"]


def test_decision_refuses_a_caller_sent_actor_that_disagrees(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway import deps

    monkeypatch.setattr(deps, "is_admin_user", _always_admin)
    _install_store(monkeypatch, _FakeEscalationStore([_FakeRecord()]))
    monkeypatch.setattr(autonomy, "_escalation_actor", lambda request: "operator")

    response = client.post(
        "/api/autonomy/sentinel/escalations/esc-1/resolve",
        json={"by": "someone-else", "note": "looks fine"},
    )

    assert response.status_code == 409
    assert "does not match the authenticated caller" in response.json()["detail"]


def test_resolve_records_the_note_and_says_it_repairs_nothing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway import deps

    monkeypatch.setattr(deps, "is_admin_user", _always_admin)
    _install_store(monkeypatch, _FakeEscalationStore([_FakeRecord()]))
    monkeypatch.setattr(autonomy, "_escalation_actor", lambda request: "operator")

    response = client.post(
        "/api/autonomy/sentinel/escalations/esc-1/resolve",
        json={"by": "operator", "note": "handled by hand"},
    )

    assert response.status_code == 200
    assert "starts no repair" in response.json()["note"]


def test_decision_404s_a_non_sentinel_handoff(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway import deps

    monkeypatch.setattr(deps, "is_admin_user", _always_admin)
    _install_store(monkeypatch, _FakeEscalationStore([_FakeRecord(domain="agent")]))
    monkeypatch.setattr(autonomy, "_escalation_actor", lambda request: "operator")

    response = client.post(
        "/api/autonomy/sentinel/escalations/esc-1/acknowledge",
        json={"by": "operator"},
    )

    assert response.status_code == 404
    assert "not a Sentinel handoff" in response.json()["detail"]


def test_decision_404s_an_unknown_escalation(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway import deps

    monkeypatch.setattr(deps, "is_admin_user", _always_admin)
    _install_store(monkeypatch, _FakeEscalationStore([]))
    monkeypatch.setattr(autonomy, "_escalation_actor", lambda request: "operator")

    response = client.post(
        "/api/autonomy/sentinel/escalations/missing/acknowledge",
        json={"by": "operator"},
    )

    assert response.status_code == 404
