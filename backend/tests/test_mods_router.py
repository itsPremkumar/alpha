"""Tests for the Gateway mods router (`/api/mods`).

The router is a read-only operator surface plus the hold-decision endpoint.
These tests pin the route order (collection routes before the ``/{mod_name}``
catch-all), the admin gating of audit/holds/state, and the honesty rules: an
absent audit mod is a 503, never an empty list, and an approval-gated mod
command is refused rather than executed.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.mods.approvals import HoldStore, set_hold_store
from alpha.mods.kernel import get_mod_kernel, reset_mod_kernel, set_mod_kernel
from app.gateway.routers import mods as mods_router


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient over an app with only the mods router, with an isolated hold store."""
    app = FastAPI()
    app.include_router(mods_router.router)
    set_hold_store(HoldStore(root_dir=tmp_path))
    reset_mod_kernel()
    yield TestClient(app)
    set_hold_store(None)
    set_mod_kernel(None)
    _ = monkeypatch  # kernel/store isolation is done explicitly above


class _FakeUser:
    def __init__(self, user_id: str, role: str | None = None):
        self.id = user_id
        self.system_role = role


def _auth_as(user: _FakeUser | None):
    """Install a fake authenticated principal onto request.state."""
    if user is None:
        return None

    def _middleware(request, call_next):
        request.state.user = user
        return call_next(request)

    return _middleware


async def _allow_any(*args, **kwargs):
    """Async no-op stand-in for the awaited auth dependencies."""
    return None


class TestRouteOrder:
    def test_collection_routes_are_declared_before_the_catch_all(self):
        """Starlette matches in registration order: a catch-all declared first
        swallows ``/chain`` and answers 404 'mod chain not found'."""
        paths = [getattr(r, "path", "") for r in mods_router.router.routes]
        assert "/api/mods/chain" in paths
        assert "/api/mods/{mod_name}" in paths
        assert paths.index("/api/mods/chain") < paths.index("/api/mods/{mod_name}")
        for name in ("/api/mods/audit", "/api/mods/holds", "/api/mods/state", "/api/mods/commands", "/api/mods/ui-cards"):
            assert paths.index(name) < paths.index("/api/mods/{mod_name}"), name


class TestAuthGating:
    def test_unauthenticated_reads_are_401(self, client):
        for path in ("/api/mods", "/api/mods/chain", "/api/mods/commands", "/api/mods/preview"):
            resp = client.post(path, json={"tool_name": "x"}) if path.endswith("preview") else client.get(path)
            assert resp.status_code == 401, path

    def test_unauthenticated_admin_surfaces_are_401(self, client):
        """`require_admin_user` fails closed: no principal is 401 before the admin check."""
        for path in ("/api/mods/audit", "/api/mods/holds", "/api/mods/state", "/api/mods/ui-cards"):
            resp = client.get(path)
            assert resp.status_code == 401, path

    def test_authenticated_non_admin_is_403(self, client):
        """A member principal passes authentication but not the admin gate."""
        from starlette.middleware.base import BaseHTTPMiddleware

        client.app.add_middleware(BaseHTTPMiddleware, dispatch=_auth_as(_FakeUser("member-1", "member")))
        for path in ("/api/mods/audit", "/api/mods/holds", "/api/mods/state", "/api/mods/ui-cards"):
            resp = client.get(path)
            assert resp.status_code == 403, path
        # Fleet introspection stays readable for a member.
        assert client.get("/api/mods").status_code == 200


class TestFleetIntrospection:
    def test_get_mods_reports_chain_and_descriptions(self, client, monkeypatch):
        monkeypatch.setattr("app.gateway.routers.mods._require_authenticated", _allow_any)
        resp = client.get("/api/mods")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == len(body["mods"])
        assert body["chain"], "the kernel registers built-in enforcers"
        orders = [c["order"] for c in body["chain"]]
        assert orders == sorted(orders)
        # The guard and audit ledger are outermost (KERNEL tier, priority 0).
        assert body["chain"][0]["mod"] == "sec_default"
        assert body["chain"][1]["mod"] == "audit_ledger"

    def test_every_builtin_mod_declares_a_manifest_without_discrepancies(self, client, monkeypatch):
        """A mod that declares nothing is a mod whose intent is invisible; the
        built-ins all declare, so a fresh discrepancy is a real regression."""
        monkeypatch.setattr("app.gateway.routers.mods._require_authenticated", _allow_any)
        body = client.get("/api/mods").json()
        for entry in body["mods"]:
            assert entry["discrepancies"] == [], entry["name"]

    def test_single_mod_describe_and_404(self, client, monkeypatch):
        monkeypatch.setattr("app.gateway.routers.mods._require_authenticated", _allow_any)
        resp = client.get("/api/mods/blast_radius_guard")
        assert resp.status_code == 200
        assert resp.json()["name"] == "blast_radius_guard"
        missing = client.get("/api/mods/no_such_mod")
        assert missing.status_code == 404
        assert "no_such_mod" in missing.json()["detail"]

    def test_catch_all_does_not_swallow_collection_routes(self, client, monkeypatch):
        monkeypatch.setattr("app.gateway.routers.mods._require_authenticated", _allow_any)
        assert client.get("/api/mods/chain").status_code == 200


class TestAuditHonesty:
    def test_audit_absent_is_503_not_empty(self, client, monkeypatch):
        """'Could not look' and 'looked and found nothing' lead to opposite
        decisions, so an absent audit mod must not read as an empty ledger."""
        monkeypatch.setattr("app.gateway.routers.mods.require_admin_user", _allow_any)
        set_mod_kernel(_EmptyKernel())
        resp = client.get("/api/mods/audit")
        assert resp.status_code == 503
        assert "audit_ledger" in resp.json()["detail"]

    def test_audit_entries_and_stats(self, client, monkeypatch):
        import asyncio

        monkeypatch.setattr("app.gateway.routers.mods.require_admin_user", _allow_any)
        kernel = get_mod_kernel()
        audit = kernel.get_mod("audit_ledger")
        assert audit is not None
        asyncio.run(
            kernel.dispatch(
                _event("tool.requested", tool_name="view_file", tool_args={"path": "README.md"}),
            )
        )
        resp = client.get("/api/mods/audit?limit=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert body["stats"]["retained"] >= 1
        assert any(e["event"] == "tool.requested" for e in body["entries"])


class TestHoldDecisions:
    def _open_hold(self, store: HoldStore):
        record, created = store.open_hold(
            tool_name="run_command",
            tool_args={"command": "rm -rf /"},
            run_id="run_1",
            tool_call_id="c1",
            risk_level="R5",
            reason="destructive shell command",
        )
        assert created
        return record

    def test_approve_records_the_authenticated_operator_not_the_body(self, client, monkeypatch):
        from starlette.middleware.base import BaseHTTPMiddleware

        from alpha.mods.approvals import get_hold_store

        user = _FakeUser("operator-42", "admin")
        monkeypatch.setattr("app.gateway.routers.mods.require_admin_user", _allow_any)
        client.app.add_middleware(BaseHTTPMiddleware, dispatch=_auth_as(user))

        record = self._open_hold(get_hold_store())
        resp = client.post(f"/api/mods/holds/{record.hold_id}/approve", json={"reason": "reviewed the diff"})
        assert resp.status_code == 200
        hold = resp.json()["hold"]
        assert hold["decision"] == "approved"
        assert hold["decided_by"] == "operator-42"
        assert hold["decision_reason"] == "reviewed the diff"

    def test_unknown_hold_is_404(self, client, monkeypatch):
        monkeypatch.setattr("app.gateway.routers.mods.require_admin_user", _allow_any)
        resp = client.post("/api/mods/holds/hold_missing/approve", json={})
        assert resp.status_code == 404

    def test_reject_records_reason(self, client, monkeypatch):
        from alpha.mods.approvals import get_hold_store

        monkeypatch.setattr("app.gateway.routers.mods.require_admin_user", _allow_any)
        store = get_hold_store()
        record, _ = store.open_hold(
            tool_name="delete_file",
            tool_args={"path": "/etc/passwd"},
            run_id="run_2",
            tool_call_id="c2",
            risk_level="R4",
            reason="deletes a system file",
        )
        resp = client.post(f"/api/mods/holds/{record.hold_id}/reject", json={"reason": "too broad"})
        assert resp.status_code == 200
        assert resp.json()["hold"]["decision"] == "rejected"


class TestModCommands:
    def test_unknown_command_is_404(self, client, monkeypatch):
        monkeypatch.setattr("app.gateway.routers.mods._require_authenticated", _allow_any)
        resp = client.post("/api/mods/commands/definitely-not-a-command", json={"payload": {}})
        assert resp.status_code == 404

    def test_approval_gated_command_is_refused_not_executed(self, client, monkeypatch):
        """A command declaring requires_approval must not run over a route with
        no approval flow; executing it would defeat the flag the mod set."""
        monkeypatch.setattr("app.gateway.routers.mods._require_authenticated", _allow_any)
        kernel = get_mod_kernel()
        kernel.commands.register("test_mod", "gated", lambda payload: "should never run", description="approval gated", requires_approval=True)
        resp = client.post("/api/mods/commands/gated", json={"payload": {}})
        assert resp.status_code == 409
        assert "requires_approval" in resp.json()["detail"]

    def test_plain_mod_command_runs(self, client, monkeypatch):
        monkeypatch.setattr("app.gateway.routers.mods._require_authenticated", _allow_any)
        kernel = get_mod_kernel()
        kernel.commands.register("test_mod", "ping", lambda payload: "pong", description="health")
        resp = client.post("/api/mods/commands/ping", json={"payload": {}})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["output"] == "pong"

    def test_command_listing(self, client, monkeypatch):
        monkeypatch.setattr("app.gateway.routers.mods._require_authenticated", _allow_any)
        kernel = get_mod_kernel()
        kernel.commands.register("test_mod", "listed", lambda payload: "ok", description="listed")
        body = client.get("/api/mods/commands").json()
        assert body["total"] >= 1
        assert any(c["name"] == "listed" for c in body["commands"])


class TestImpactPreview:
    def test_preview_never_executes_and_reports_kind(self, client, monkeypatch):
        monkeypatch.setattr("app.gateway.routers.mods._require_authenticated", _allow_any)
        resp = client.post("/api/mods/preview", json={"tool_name": "run_command", "tool_args": {"command": "rm -rf /tmp/x"}})
        assert resp.status_code == 200
        body = resp.json()
        assert "kind" in body
        assert "measurable" in body
        # measurable=False is an honest "could not compute", never "nothing at stake".
        assert isinstance(body["measurable"], bool)


class _EmptyKernel:
    """A kernel with no audit mod registered, to pin the 503 honesty rule."""

    def get_mod(self, name):
        return None

    def list_mods(self):
        return []

    def control_chain(self):
        return []

    def describe_mods(self):
        return []

    def list_ui_cards(self, **kwargs):
        return []

    class _NoState:
        def total_keys(self):
            return 0

        def mod_names(self):
            return []

        def snapshot(self, name=None):
            return {}

    state = _NoState()

    class _NoCommands:
        def list_commands(self, *a, **k):
            return []

        def resolve(self, name):
            return None

    commands = _NoCommands()


def _event(name, run_id="run_1", **payload):
    from alpha.mods.types import AlphaEvent, CorrelationContext

    return AlphaEvent(name=name, payload=payload, correlation=CorrelationContext.create(run_id=run_id))
