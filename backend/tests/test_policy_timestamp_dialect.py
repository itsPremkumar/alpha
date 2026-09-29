"""The policy plane must speak ISO 8601.

``ApprovalPolicy.created_at`` (alpha/policy/engine.py:62) is a ``time.time()``
float with ``to_dict() = asdict(self)``, and the router-local
``ApprovalRequest.created_at`` is the same. So ``GET /api/policy/policies``,
``POST /api/policy/policies``, ``POST /api/policy/approvals``,
``GET /api/policy/approvals`` and ``POST /api/policy/approvals/{id}/decide``
emitted a raw epoch while every sibling Gateway route emitted ISO 8601.

Why the fix is in the router and not in the model
-------------------------------------------------
``ApprovalPolicy.to_dict()`` is ALSO the on-disk format: ``PolicyEngine._save``
writes ``[p.to_dict() for p in self._policies]`` into ``policies.json``, and
``ApprovalPolicy.from_dict`` reads the same ``created_at`` back on the next
boot. Changing the stored type would make every existing policy file
unreadable. These tests pin that the file on disk is still a float.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import policy as policy_router


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """A client whose PolicyEngine writes into tmp_path, like production does.

    ``get_policy_engine`` (alpha/policy/engine.py:147) re-resolves
    ``_default_storage_path()`` on every call and rebuilds the singleton when
    the resolved path changes, so patching that one function is enough to
    redirect the engine - and the redirect also holds for the route's own
    function-local ``from alpha.policy import get_policy_engine`` import.
    """
    from alpha.policy import engine as policy_engine

    monkeypatch.setattr(policy_engine, "_default_storage_path", lambda: tmp_path / "policy" / "policies.json")
    return TestClient(_app())


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(policy_router.router)
    return app


@pytest.fixture(autouse=True)
def _reset_approvals():
    """`policy._approvals` is process-global; keep tests independent."""
    policy_router._approvals.clear()
    yield
    policy_router._approvals.clear()


def _assert_iso(value: object, where: str) -> None:
    assert isinstance(value, str), f"{where} is {type(value).__name__}, not a string: {value!r}"
    assert value, f"{where} is an empty string"
    assert datetime.fromisoformat(value).tzinfo is not None, f"{where} is not an aware ISO timestamp: {value!r}"


def _add_policy(client: TestClient, pattern: str = "shell:*") -> dict:
    response = client.post("/api/policy/policies", json={"action_pattern": pattern, "auto": "approval", "note": "needs a human"})
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------


def test_added_policy_created_at_is_iso(client: TestClient) -> None:
    """`POST /api/policy/policies` is the route a client writes through."""
    policy = _add_policy(client)
    _assert_iso(policy["created_at"], "policies[].created_at")


def test_listed_policy_created_at_is_iso(client: TestClient) -> None:
    _add_policy(client)
    body = client.get("/api/policy/policies").json()
    policies = body["policies"]
    assert policies, "no policies were returned"
    for policy in policies:
        _assert_iso(policy["created_at"], "listed.created_at")


def test_create_and_list_agree_on_the_timestamp_type(client: TestClient) -> None:
    """Two separate call sites of the same projection; they must not drift."""
    created = _add_policy(client)
    listed = client.get("/api/policy/policies").json()["policies"][0]
    assert type(created["created_at"]) is type(listed["created_at"]), "create and list disagree on the timestamp type"


def test_approval_created_at_is_iso_on_every_route(client: TestClient) -> None:
    created = client.post("/api/policy/approvals", json={"action": "deploy:prod", "actor": "agent", "reason": "release window"})
    assert created.status_code == 201, created.text
    approval = created.json()
    _assert_iso(approval["created_at"], "approvals(created).created_at")

    listed = client.get("/api/policy/approvals").json()["approvals"]
    assert listed, "no approvals were returned"
    for row in listed:
        _assert_iso(row["created_at"], "approvals(listed).created_at")

    decided = client.post(f"/api/policy/approvals/{approval['request_id']}/decide", json={"approved": True})
    assert decided.status_code == 200, decided.text
    _assert_iso(decided.json()["created_at"], "approvals(decided).created_at")
    # The decision must not restamp the row.
    assert decided.json()["created_at"] == approval["created_at"]


def test_the_status_filter_is_a_separate_branch(client: TestClient) -> None:
    client.post("/api/policy/approvals", json={"action": "deploy:prod", "actor": "agent"})
    pending = client.get("/api/policy/approvals", params={"status": "pending"}).json()["approvals"]
    assert pending, "the status filter returned nothing"
    for row in pending:
        _assert_iso(row["created_at"], "filtered.created_at")


def test_no_raw_epoch_survives_anywhere_on_the_plane(client: TestClient) -> None:
    """The assertion that pins the regression itself, not a formatting detail."""
    _add_policy(client)
    client.post("/api/policy/approvals", json={"action": "deploy:prod", "actor": "agent"})

    payloads = [
        client.get("/api/policy/policies").json(),
        client.get("/api/policy/approvals").json(),
        client.post("/api/policy/evaluate", json={"action": "shell:rm"}).json(),
    ]
    for payload in payloads:
        _assert_no_raw_epoch(payload)


def _assert_no_raw_epoch(payload: object, where: str = "payload") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and (key.endswith("_at") or key.endswith("_at_")):
                raise AssertionError(f"{where}.{key} is still a raw epoch number: {value!r}")
            _assert_no_raw_epoch(value, f"{where}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _assert_no_raw_epoch(item, f"{where}[{index}]")


# ---------------------------------------------------------------------------
# Persistence: the coercion is an API-boundary concern, nothing more
# ---------------------------------------------------------------------------


def test_the_policy_file_on_disk_keeps_the_float(tmp_path: Path, client: TestClient) -> None:
    """`ApprovalPolicy.to_dict()` is the on-disk format; it must not change."""
    _add_policy(client)
    path = tmp_path / "policy" / "policies.json"
    assert path.exists(), f"the engine wrote nothing at {path}"
    stored = json.loads(path.read_text(encoding="utf-8"))
    policies = stored["policies"]
    assert policies, "the stored policy file is empty"
    assert isinstance(policies[0]["created_at"], float), f"the stored timestamp changed shape to {type(policies[0]['created_at']).__name__}"


def test_an_existing_policy_file_with_a_float_still_loads(tmp_path: Path) -> None:
    """The upgrade path: a policies.json written before the fix must boot."""
    from alpha.policy.engine import PolicyEngine

    path = tmp_path / "policies.json"
    legacy = {
        "version": 1,
        "policies": [
            {
                "policy_id": "pol-legacy",
                "action_pattern": "shell:*",
                "actor": "*",
                "project_id": "*",
                "auto": "approval",
                "note": "written before the fix",
                "created_at": 1790528973.1918123,
            }
        ],
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")

    engine = PolicyEngine(storage_path=path)
    loaded = engine.list_policies()
    assert len(loaded) == 1
    assert loaded[0].policy_id == "pol-legacy"
    assert isinstance(loaded[0].created_at, float), "the stored timestamp must stay a float"
    assert loaded[0].created_at == pytest.approx(1790528973.1918123)


# ---------------------------------------------------------------------------
# The zero case
# ---------------------------------------------------------------------------


def test_the_zero_epoch_decision_for_this_plane() -> None:
    """Why there is no zero-translation here, stated as an executable claim.

    Both ``created_at`` fields are ``default_factory=time.time``: they are
    stamped the moment the row is created, so neither has a "never" state and
    therefore no ``0.0`` sentinel to translate. Inventing one would be less
    honest than rendering the real epoch the record claims. The day a field
    here gains a ``0.0``-means-never meaning, THIS test is what should change -
    deliberately, with the field named.
    """
    from app.gateway.routers.policy import _wire_ts

    approval = policy_router.ApprovalRequest(request_id="ap-1", action="a", actor="b", project_id="c")
    assert approval.created_at > 0, "the request stamps time.time() at construction; a zero default would be a new contract"

    # A stored 0.0 is the epoch it says it is, not a null and not "".
    assert _wire_ts(0.0) == "1970-01-01T00:00:00+00:00"
    # A real epoch renders as a real, parseable, UTC date.
    assert _wire_ts(1790528973.1918123) == "2026-09-27T17:09:33.191812+00:00"
    assert datetime.fromisoformat(_wire_ts(1790528973.1918123)).tzinfo is not None
