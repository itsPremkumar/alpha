"""APEX HTTP + supervisor-loop surface: spec §60, §132, and the route-order trap.

Three groups of tests:

* **Route order.** Starlette matches in registration order, so a collection path
  declared after a catch-all answers ``404 Session '<name>' not found`` —
  indistinguishable from a missing feature. This is the same trap the groups,
  skills and workflows routers document, and it is why ``/invariants``,
  ``/status``, ``/policy`` and ``/events`` are declared before
  ``/sessions/{session_id}``.
* **Refusals that must stay refusals.** Widening a contract, writing a terminal
  state by hand, steering a terminal session, and a non-admin control action are
  the four ways this router could become an autonomy bypass.
* **The supervisor loop.** ``apex_tick`` counts refusals rather than hiding them,
  and a degraded store is reported rather than read as "no sessions".
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.apex.store import ApexSessionState, ApexStore
from app.gateway.autonomy import loops as loop_adapters
from app.gateway.routers import apex

COLLECTION_ROUTES = ("/status", "/policy", "/invariants", "/sessions")


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ApexStore:
    """Point the process-wide store at a temp file for the whole request."""
    import alpha.apex.store as store_module

    instance = ApexStore(tmp_path / "sessions.json")
    monkeypatch.setattr(store_module, "_store", instance)
    monkeypatch.setattr(store_module, "_default_storage_path", lambda: instance.storage_path)
    return instance


def _client(is_admin: bool = True) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request, call_next):
        request.state.user = SimpleNamespace(
            id="admin-1" if is_admin else "user-2",
            is_admin=is_admin,
        )
        return await call_next(request)

    app.include_router(apex.router)
    return TestClient(app)


@pytest.fixture()
def client() -> TestClient:
    return _client()


def _create(client: TestClient, **overrides) -> dict:
    payload = {"objective": "fix the failing workflow", "profile": "autonomous", **overrides}
    response = client.post("/api/apex/sessions", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


class TestRouteOrder:
    @pytest.mark.parametrize("path", COLLECTION_ROUTES)
    def test_collection_routes_are_not_swallowed(self, client: TestClient, store: ApexStore, path: str) -> None:
        response = client.get(f"/api/apex{path}")
        assert response.status_code == 200, f"{path} resolved to {response.status_code}"

    def test_a_known_session_id_still_resolves(self, client: TestClient, store: ApexStore) -> None:
        session = _create(client)["session"]
        assert client.get(f"/api/apex/sessions/{session['session_id']}").status_code == 200

    def test_an_unknown_session_is_404_with_a_named_reason(self, client: TestClient, store: ApexStore) -> None:
        body = client.get("/api/apex/sessions/apx-does-not-exist").json()
        assert body["detail"] == "no APEX session 'apx-does-not-exist'"


class TestInvariantsEndpoint:
    def test_reports_declared_and_live_separately(self, client: TestClient) -> None:
        payload = client.get("/api/apex/invariants").json()
        assert payload["schema"] == "alpha.apex.invariants.v1"
        assert payload["declared"] == 12
        assert payload["live"] == 12
        assert payload["all_live"] is True

    def test_every_row_names_its_enforcement_site(self, client: TestClient) -> None:
        for row in client.get("/api/apex/invariants").json()["invariants"]:
            assert row["module"]
            assert row["symbol"]
            assert row["spec_section"].startswith("§188-")


class TestPolicyEndpoint:
    def test_apex_max_profile_is_queryable(self, client: TestClient) -> None:
        payload = client.get("/api/apex/policy?profile=apex_max").json()
        assert payload["profile"] == "apex_max"
        assert payload["budget"]["max_tool_calls"] == 5000

    def test_unknown_profile_is_422_listing_the_valid_ones(self, client: TestClient) -> None:
        response = client.get("/api/apex/policy?profile=god_mode")
        assert response.status_code == 422
        assert "apex_max" in response.json()["detail"]

    def test_policy_says_apEX_is_not_the_kernel(self, client: TestClient) -> None:
        payload = client.get("/api/apex/policy?profile=apex_max").json()
        assert payload["note"]
        assert payload["policy_sites"]["run_lifecycle"].startswith("alpha.runtime.runs.manager")


class TestSessionLifecycleOverHttp:
    def test_create_returns_the_contract_and_its_digest(self, client: TestClient, store: ApexStore) -> None:
        body = _create(client, acceptance_criteria=["suite passes"])
        assert body["contract"]["profile"] == "autonomous"
        assert body["contract"]["digest"] == body["session"]["contract_digest"]
        assert body["note"]

    def test_narrowing_a_contract_at_creation_is_allowed(self, client: TestClient, store: ApexStore) -> None:
        body = _create(client, profile="apex_max", authority={"terminal": False})
        assert body["contract"]["authority"]["terminal"] is False

    def test_widening_a_contract_at_creation_is_422(self, client: TestClient, store: ApexStore) -> None:
        # `assist` does not offer `terminal`; asking for it must fail loudly
        # rather than quietly granting more than the profile carries.
        response = client.post(
            "/api/apex/sessions",
            json={"objective": "x", "profile": "assist", "authority": {"terminal": True}},
        )
        assert response.status_code == 422
        assert "terminal" in response.json()["detail"]

    def test_cycle_with_unmet_criteria_does_not_complete(self, client: TestClient, store: ApexStore) -> None:
        session = _create(client, acceptance_criteria=["suite passes"])["session"]
        result = client.post(f"/api/apex/sessions/{session['session_id']}/cycle", json={}).json()
        assert result["decision"]["action"] == "await_verification"
        assert result["decision"]["blocked"] is True
        assert store.get(session["session_id"]).state is not ApexSessionState.COMPLETED

    def test_cycle_returns_its_steps_for_debugging(self, client: TestClient, store: ApexStore) -> None:
        session = _create(client)["session"]
        result = client.post(f"/api/apex/sessions/{session['session_id']}/cycle", json={}).json()
        assert [s["name"] for s in result["steps"]] == [
            "load_session",
            "check_policy",
            "apply_decision",
            "checkpoint",
        ]

    def test_cycling_all_sessions_skips_terminal_ones(self, client: TestClient, store: ApexStore) -> None:
        first = _create(client)["session"]
        second = _create(client)["session"]
        store.set_state(second["session_id"], ApexSessionState.CANCELLED, reason="operator")
        payload = client.post("/api/apex/cycle", json={}).json()
        assert payload["cycles"] == 1
        assert payload["results"][0]["session_id"] == first["session_id"]

    def test_cycle_endpoint_rejects_the_all_sessions_flag(self, client: TestClient, store: ApexStore) -> None:
        session = _create(client)["session"]
        response = client.post(f"/api/apex/sessions/{session['session_id']}/cycle", json={"all_sessions": True})
        assert response.status_code == 400

    def test_driving_one_session_forward_is_admin_only(self, store: ApexStore) -> None:
        created = _client(is_admin=False).post("/api/apex/sessions", json={"objective": "x", "profile": "autonomous"})
        # Creation is refused first for a non-admin, so there is no session to
        # try to drive; assert on the cycle endpoint directly instead.
        assert created.status_code == 403
        assert _client(is_admin=False).post("/api/apex/sessions/apx-x/cycle", json={}).status_code == 403


class TestTerminalStateIsNotWritable:
    @pytest.mark.parametrize("target", ["completed", "failed", "cancelled"])
    def test_no_terminal_state_is_writable_over_http(self, client: TestClient, store: ApexStore, target: str) -> None:
        """A terminal outcome must be reached by the acceptance gate, not a PUT.

        ``COMPLETED`` needs passing evidence; ``FAILED`` and ``CANCELLED`` are
        outcomes too, and letting a request body assert one would be the same
        false-completion hole wearing a different label.
        """
        session = _create(client)["session"]
        response = client.post(f"/api/apex/sessions/{session['session_id']}/state", json={"state": target})
        assert response.status_code == 409
        assert "terminal" in response.json()["detail"].lower()
        assert store.get(session["session_id"]).state is ApexSessionState.IDLE

    def test_a_non_terminal_transition_is_allowed(self, client: TestClient, store: ApexStore) -> None:
        session = _create(client)["session"]
        response = client.post(
            f"/api/apex/sessions/{session['session_id']}/state",
            json={"state": "active", "reason": "host adapter dispatched work"},
        )
        assert response.status_code == 200
        assert store.get(session["session_id"]).state is ApexSessionState.ACTIVE

    def test_an_unknown_state_is_422(self, client: TestClient, store: ApexStore) -> None:
        session = _create(client)["session"]
        response = client.post(f"/api/apex/sessions/{session['session_id']}/state", json={"state": "vibing"})
        assert response.status_code == 422


class TestSteeringOverHttp:
    def test_a_constraint_is_recorded(self, client: TestClient, store: ApexStore) -> None:
        session = _create(client)["session"]
        response = client.post(
            f"/api/apex/sessions/{session['session_id']}/steer",
            json={"instruction": "use local models only"},
        )
        assert response.status_code == 200
        assert response.json()["constraint"]["source"] == "user"

    def test_steering_a_terminal_session_is_409(self, client: TestClient, store: ApexStore) -> None:
        session = _create(client)["session"]
        store.set_state(session["session_id"], ApexSessionState.CANCELLED, reason="operator")
        response = client.post(
            f"/api/apex/sessions/{session['session_id']}/steer",
            json={"instruction": "too late"},
        )
        assert response.status_code == 409

    def test_steering_an_empty_instruction_is_422(self, client: TestClient, store: ApexStore) -> None:
        session = _create(client)["session"]
        assert client.post(f"/api/apex/sessions/{session['session_id']}/steer", json={"instruction": ""}).status_code == 422


class TestControlRequiresAdmin:
    @pytest.mark.parametrize("path", ["/sessions", "/cycle"])
    def test_a_non_admin_cannot_control(self, store: ApexStore, path: str) -> None:
        payload = {"objective": "x"} if path == "/sessions" else {}
        assert _client(is_admin=False).post(f"/api/apex{path}", json=payload).status_code == 403

    def test_a_non_admin_may_still_read(self, store: ApexStore) -> None:
        assert _client(is_admin=False).get("/api/apex/status").status_code == 200

    def test_status_binds_the_app_side_supervisor(self, client: TestClient, store: ApexStore) -> None:
        """The router is where the app→harness dependency gets resolved.

        The harness may not import ``app.*``, so the supervisor reaches the
        projection by injection from here. This also proves the registered loop
        is visible through ``/api/apex/status``, which is how an operator
        confirms the loop exists without reading the source.
        """
        payload = client.get("/api/apex/status").json()
        assert payload["supervisor"]["available"] is True
        assert "apex" in payload["supervisor"]["status"]["loops"]


class TestDegradedStoreOverHttp:
    def test_listing_reports_unavailable_rather_than_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import alpha.apex.store as store_module

        path = tmp_path / "sessions.json"
        path.write_text("{ broken", encoding="utf-8")
        instance = ApexStore(path)
        monkeypatch.setattr(store_module, "_store", instance)
        monkeypatch.setattr(store_module, "_default_storage_path", lambda: path)

        body = _client().get("/api/apex/sessions").json()
        assert body["available"] is False
        assert body["count"] is None
        assert "JSONDecodeError" in body["reason"]

    def test_status_discloses_a_degraded_store(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import alpha.apex.store as store_module

        path = tmp_path / "sessions.json"
        path.write_text("{ broken", encoding="utf-8")
        instance = ApexStore(path)
        monkeypatch.setattr(store_module, "_store", instance)
        monkeypatch.setattr(store_module, "_default_storage_path", lambda: path)

        body = _client().get("/api/apex/status").json()
        assert body["sessions"]["available"] is False
        assert body["sessions"]["count"] is None


class TestEventStream:
    def test_replays_the_durable_journal_then_reports_ready(self, client: TestClient, store: ApexStore) -> None:
        """The stream must show the durable history a subscriber would have seen.

        One pass over the response body: ``iter_lines`` cannot be called twice
        on an already-consumed httpx stream.
        """
        session = _create(client)["session"]
        with client.stream("GET", f"/api/apex/sessions/{session['session_id']}/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join(response.iter_text())

        assert "event: ready" in body
        assert "event: session.created" in body
        # The creation event is journalled, so its durability marker is visible.
        assert '"durable": true' in body

    def test_streaming_an_unknown_session_is_404(self, client: TestClient, store: ApexStore) -> None:
        assert client.get("/api/apex/sessions/apx-nope/events").status_code == 404

    def test_event_replay_honours_after_seq(self, client: TestClient, store: ApexStore) -> None:
        session = _create(client)["session"]
        # Record a second event so `after_seq` has something to exclude.
        store.record_constraint(session["session_id"], "prioritise reliability")
        events = store.read_events(session["session_id"])
        assert len(events) > 1

        with client.stream(
            "GET",
            f"/api/apex/sessions/{session['session_id']}/events",
            params={"after_seq": events[0].seq},
        ) as response:
            body = "".join(response.iter_text())

        assert "event: constraint.recorded" in body
        assert "event: session.created" not in body

    def test_journaled_events_carry_their_durability_marker(self, client: TestClient, store: ApexStore) -> None:
        session = _create(client)["session"]
        for event in store.read_events(session["session_id"]):
            assert event.payload["durable"] is True


class TestDeleteSession:
    def test_delete_removes_the_row(self, client: TestClient, store: ApexStore) -> None:
        session = _create(client)["session"]
        assert client.delete(f"/api/apex/sessions/{session['session_id']}").status_code == 200
        assert store.get(session["session_id"]) is None

    def test_deleting_twice_is_404(self, client: TestClient, store: ApexStore) -> None:
        session = _create(client)["session"]
        client.delete(f"/api/apex/sessions/{session['session_id']}")
        assert client.delete(f"/api/apex/sessions/{session['session_id']}").status_code == 404


class TestSupervisorLoop:
    """`apex_tick` is the one adapter backing the registered `apex` loop."""

    def test_loop_is_registered_in_the_supervisor(self) -> None:
        # The supervisor is the single owner of background loops; a loop
        # declared anywhere else would be a second owner.
        from app.gateway.autonomy.supervisor import AutonomySupervisor

        supervisor = AutonomySupervisor()
        supervisor.register_default_loops()
        assert "apex" in supervisor._specs

    def test_tick_counts_refusals_rather_than_hiding_them(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import alpha.apex.store as store_module

        instance = ApexStore(tmp_path / "sessions.json")
        monkeypatch.setattr(store_module, "_store", instance)
        monkeypatch.setattr(store_module, "_default_storage_path", lambda: instance.storage_path)

        from alpha.apex.contract import profile_for as build_contract

        active = instance.create(owner="o", objective="a", profile="autonomous", contract_digest=build_contract("autonomous").digest())
        waiting = instance.create(
            owner="o",
            objective="b",
            profile="autonomous",
            contract_digest=build_contract("autonomous").digest(),
            acceptance_criteria=["tests pass"],
        )
        terminal = instance.create(owner="o", objective="c", profile="autonomous", contract_digest=build_contract("autonomous").digest())
        instance.set_state(terminal.session_id, ApexSessionState.CANCELLED, reason="operator")

        summary = loop_adapters.apex_tick()
        assert summary["sessions"] == 3
        assert summary["cycles"] == 2
        assert summary["skipped_terminal"] == 1
        assert summary["cycles_blocked"] == 1
        assert active.session_id and waiting.session_id

    def test_tick_reports_a_degraded_store_instead_of_zero_sessions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import alpha.apex.store as store_module

        path = tmp_path / "sessions.json"
        path.write_text("{ broken", encoding="utf-8")
        instance = ApexStore(path)
        monkeypatch.setattr(store_module, "_store", instance)
        monkeypatch.setattr(store_module, "_default_storage_path", lambda: path)

        summary = loop_adapters.apex_tick()
        assert summary["error"] == "apex_store_unreadable"
        assert summary["sessions"] is None
        assert summary["cycles"] == 0

    def test_tick_never_raises_when_apex_is_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import alpha.apex.store as store_module

        monkeypatch.setattr(
            store_module.ApexStore,
            "list",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        summary = loop_adapters.apex_tick()
        assert "error" in summary or summary["cycles"] == 0
