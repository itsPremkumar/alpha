"""APEX control-plane authorization, owner scoping, and honest failure.

These tests exist because the router used to ask ``getattr(user, "is_admin")``
— an attribute **no principal in this repository carries**. The real ``User``
model and the auth-disabled synthetic user both carry ``system_role``, so every
admin-gated APEX route answered 403 in production while the suite stayed green
on a fixture that invented the field. Each test below therefore builds its
principal from the shape production actually sends.

Three properties are pinned:

1. **The admin predicate is the repository's.** One definition
   (``app.gateway.deps.is_admin_user``), including its rule that a PAT
   credential never carries admin.
2. **A member route is owner-scoped.** Its collection already filters by
   owner, so a member read that resolves by id alone would be an existence
   oracle and a cross-user read. A foreign id answers the same 404 an absent
   one does.
3. **An unreadable store is unknown, not empty.** A degraded store is 503 with
   its reason rather than a 404 that claims the row is absent — the repository
   rule is that an unreadable source reports *null/unknown*, never a confident
   absence.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.apex.contract import profile_for
from alpha.apex.store import ApexSessionState, ApexStore
from app.gateway.auth.models import User
from app.gateway.auth_disabled import AUTH_SOURCE_PAT, get_auth_disabled_user
from app.gateway.routers import apex as apex_router


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ApexStore:
    import alpha.apex.store as store_module

    instance = ApexStore(tmp_path / "sessions.json")
    monkeypatch.setattr(store_module, "_store", instance)
    monkeypatch.setattr(store_module, "_default_storage_path", lambda: instance.storage_path)
    return instance


@pytest.fixture()
def goal_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import alpha.apex.goals as goals_module

    instance = goals_module.ApexGoalStore(tmp_path / "goals.json")
    monkeypatch.setattr(goals_module, "_store", instance)
    monkeypatch.setattr(goals_module, "_default_storage_path", lambda: instance.storage_path)
    return instance


def _client(user: object | None, *, auth_source: str | None = None) -> TestClient:
    """A client whose principal is built the way ``AuthMiddleware`` builds one.

    ``user=None`` builds a router with **no** injected principal at all, which
    is how the "an unauthenticated caller must not read every owner's rows"
    case is exercised.
    """
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request, call_next):
        if user is not None:
            request.state.user = user
        if auth_source is not None:
            request.state.auth_source = auth_source
        return await call_next(request)

    app.include_router(apex_router.router)
    return TestClient(app)


def _admin() -> TestClient:
    return _client(SimpleNamespace(id="admin-1", system_role="admin"))


def _plain(uid: str = "user-2") -> TestClient:
    return _client(SimpleNamespace(id=uid, system_role="user"))


def _create_session(client: TestClient) -> dict:
    response = client.post("/api/apex/sessions", json={"objective": "fix the failing workflow", "profile": "autonomous"})
    assert response.status_code == 200, response.text
    return response.json()["session"]


# --------------------------------------------------------------------------- #
# 1. The admin predicate
# --------------------------------------------------------------------------- #


class TestAdminPredicate:
    """The field the router reads must be the field production writes."""

    def test_the_real_user_model_grants_admin_control(self, store: ApexStore) -> None:
        """A genuine ``User`` with ``system_role="admin"`` must be able to act.

        Before the fix this returned 403: the router read ``is_admin``, which
        ``User`` has never declared, so the control plane was unreachable for
        every administrator.
        """
        real_admin = User(email="ops@example.com", system_role="admin")
        client = _client(real_admin)
        assert client.post("/api/apex/sessions", json={"objective": "x"}).status_code == 200

    def test_a_real_non_admin_is_refused(self, store: ApexStore) -> None:
        real_user = User(email="someone@example.com", system_role="user")
        client = _client(real_user)
        response = client.post("/api/apex/sessions", json={"objective": "x"})
        assert response.status_code == 403
        assert "administrator" in response.json()["detail"]

    def test_the_auth_disabled_principal_is_admin(self, store: ApexStore) -> None:
        """Local dev runs with authentication bypassed; it must still work."""
        client = _client(get_auth_disabled_user())
        assert client.post("/api/apex/sessions", json={"objective": "x"}).status_code == 200

    def test_the_dead_is_admin_attribute_grants_nothing(self, store: ApexStore) -> None:
        """A principal claiming only ``is_admin`` must not be treated as admin.

        This pins the fix from the other side: if the router ever goes back to
        ``getattr(user, "is_admin")``, this passes and the real ``User`` test
        above fails — one of the two must fail either way.
        """
        client = _client(SimpleNamespace(id="admin-1", is_admin=True, system_role="user"))
        assert client.post("/api/apex/sessions", json={"objective": "x"}).status_code == 403

    def test_a_pat_never_carries_admin(self, store: ApexStore) -> None:
        """``deps.is_admin_user`` suppresses admin for a PAT; this router must too."""
        client = _client(SimpleNamespace(id="admin-1", system_role="admin"), auth_source=AUTH_SOURCE_PAT)
        response = client.post("/api/apex/sessions", json={"objective": "x"})
        assert response.status_code == 403
        assert "administrator" in response.json()["detail"]

    @pytest.mark.parametrize(
        "path,payload",
        [
            ("/api/apex/sessions", {"objective": "x"}),
            ("/api/apex/cycle", {}),
            ("/api/apex/enable", {"profile": "assist"}),
            ("/api/apex/disable", {}),
            ("/api/apex/pause", {"scope_key": "thread-1"}),
            ("/api/apex/resume", {"scope_key": "thread-1"}),
            ("/api/apex/stop", {"scope_key": "thread-1"}),
        ],
    )
    def test_every_control_write_is_admin_gated(self, store: ApexStore, path: str, payload: dict) -> None:
        assert _plain().post(path, json=payload).status_code == 403

    def test_a_state_write_is_admin_gated(self, store: ApexStore) -> None:
        """The raw state machine is a control write, not a self-service one."""
        session = _create_session(_admin())
        response = _plain().post(f"/api/apex/sessions/{session['session_id']}/state", json={"state": "active"})
        assert response.status_code == 403

    def test_delete_is_admin_gated(self, store: ApexStore) -> None:
        session = _create_session(_admin())
        assert _plain().delete(f"/api/apex/sessions/{session['session_id']}").status_code == 403
        assert store.get(session["session_id"]) is not None

    def test_reads_stay_open_to_any_signed_in_caller(self, store: ApexStore) -> None:
        assert _plain().get("/api/apex/status").status_code == 200


# --------------------------------------------------------------------------- #
# 2. Owner scoping on member routes
# --------------------------------------------------------------------------- #


class TestMemberRoutesAreOwnerScoped:
    def test_a_foreign_session_reads_as_absent(self, store: ApexStore) -> None:
        session = _create_session(_admin())
        sid = session["session_id"]

        foreign = _plain().get(f"/api/apex/sessions/{sid}")
        absent = _plain().get("/api/apex/sessions/apx-nope")

        assert foreign.status_code == absent.status_code == 404
        # Byte-identical shape: the 404 must not become an existence oracle.
        assert foreign.json()["detail"] == f"no APEX session '{sid}'"
        assert absent.json()["detail"] == "no APEX session 'apx-nope'"

    def test_an_admin_still_reaches_a_foreign_session(self, store: ApexStore) -> None:
        session = _create_session(_admin())
        assert _admin().get(f"/api/apex/sessions/{session['session_id']}").status_code == 200

    def test_a_foreign_session_cannot_be_steered(self, store: ApexStore) -> None:
        session = _create_session(_admin())
        response = _plain().post(f"/api/apex/sessions/{session['session_id']}/steer", json={"instruction": "use local models only"})
        assert response.status_code == 404
        assert store.get(session["session_id"]).state is ApexSessionState.IDLE

    def test_the_event_stream_is_owner_scoped_too(self, store: ApexStore) -> None:
        session = _create_session(_admin())
        assert _plain().get(f"/api/apex/sessions/{session['session_id']}/events").status_code == 404

    def test_a_goal_belongs_to_its_creator(self, goal_store) -> None:
        created = _admin().post("/api/apex/goals", json={"objective": "fix the browser"})
        goal_id = created.json()["goal"]["goal_id"]

        foreign = _plain().get(f"/api/apex/goals/{goal_id}")
        assert foreign.status_code == 404
        assert foreign.json()["detail"] == f"no APEX goal '{goal_id}'"
        assert _admin().get(f"/api/apex/goals/{goal_id}").status_code == 200

    def test_a_goal_cannot_be_created_under_another_owners_parent(self, goal_store) -> None:
        parent = _admin().post("/api/apex/goals", json={"objective": "private parent"}).json()["goal"]

        response = _plain().post(
            "/api/apex/goals",
            json={"objective": "foreign child", "parent_goal_id": parent["goal_id"]},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == f"no APEX goal '{parent['goal_id']}'"
        tree = _admin().get(f"/api/apex/goals/{parent['goal_id']}").json()["tree"]
        assert tree["children"] == []

    def test_a_goal_cannot_link_to_another_owners_session(self, store, goal_store) -> None:
        session = store.create(
            objective="private session",
            owner="admin-1",
            profile="autonomous",
            contract_digest=profile_for("autonomous").digest(),
        )

        response = _plain().post(
            "/api/apex/goals",
            json={"objective": "foreign session link", "session_id": session.session_id},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == f"no APEX session '{session.session_id}'"

    def test_a_stale_foreign_session_link_cannot_read_session_decisions(self, store, goal_store) -> None:
        session = store.create(
            objective="private session",
            owner="admin-1",
            profile="autonomous",
            contract_digest=profile_for("autonomous").digest(),
        )
        store.emit(session.session_id, "cycle.secret", marker="must not leak")
        goal = goal_store.create(objective="user-owned goal", owner="user-2", session_id=session.session_id)

        response = _plain().get(f"/api/apex/goals/{goal.goal_id}/decisions")

        assert response.status_code == 409
        assert "owner boundary" in response.json()["detail"]

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/apex/goals/{id}"),
            ("GET", "/api/apex/goals/{id}/tasks"),
            ("GET", "/api/apex/goals/{id}/agents"),
            ("GET", "/api/apex/goals/{id}/workflow"),
            ("GET", "/api/apex/goals/{id}/events"),
            ("GET", "/api/apex/goals/{id}/decisions"),
            ("GET", "/api/apex/goals/{id}/evidence"),
            ("GET", "/api/apex/goals/{id}/failures"),
            ("POST", "/api/apex/goals/{id}/replan"),
            ("POST", "/api/apex/goals/{id}/verify"),
        ],
    )
    def test_every_goal_member_route_is_owner_scoped(self, goal_store, method: str, path: str) -> None:
        goal_id = _admin().post("/api/apex/goals", json={"objective": "parent"}).json()["goal"]["goal_id"]
        url = path.format(id=goal_id)
        response = _plain().request(method, url, json={"instruction": "narrower"})
        assert response.status_code == 404, f"{method} {url} was reachable across owners"

    def test_a_creator_still_reaches_their_own_goal(self, goal_store) -> None:
        goal_id = _plain().post("/api/apex/goals", json={"objective": "mine"}).json()["goal"]["goal_id"]
        assert _plain().get(f"/api/apex/goals/{goal_id}").status_code == 200
        assert _plain().post(f"/api/apex/goals/{goal_id}/steer", json={"instruction": "narrower"}).status_code == 200


# --------------------------------------------------------------------------- #
# 3. Fail-closed: no principal means no rows, an unreadable store means unknown
# --------------------------------------------------------------------------- #


class TestFailClosedReads:
    def test_no_principal_is_401_not_every_owner_rows(self, store: ApexStore, goal_store) -> None:
        """``owner=None`` means *all* owners in these stores.

        The list routes used to fall back to ``owner=None`` when no user was
        stamped, which is the difference between "authentication is missing"
        and "here is everyone's data".
        """
        _create_session(_admin())
        _admin().post("/api/apex/goals", json={"objective": "someone else's"})

        anonymous = _client(user=None)
        for path in ("/api/apex/sessions", "/api/apex/goals", "/api/apex/approvals"):
            response = anonymous.get(path)
            assert response.status_code == 401, f"{path} answered {response.status_code} with rows"

    def test_status_without_a_principal_is_401_not_a_scopeless_projection(self, store: ApexStore) -> None:
        """``/status`` now resolves a scope, and an anonymous caller has none.

        Every other read on this router already refuses the same way: with no
        principal there is no owner to scope to, and answering anyway would
        mean projecting a contract for a scope nobody named.
        """
        assert _client(user=None).get("/api/apex/status").status_code == 401

    def test_an_unreadable_session_store_is_503_on_a_member_read(self, store: ApexStore) -> None:
        session = _create_session(_admin())
        store._load_error = "JSONDecodeError: sessions.json is not JSON"

        response = _admin().get(f"/api/apex/sessions/{session['session_id']}")

        assert response.status_code == 503
        assert "unreadable" in response.json()["detail"]
        assert "JSONDecodeError" in response.json()["detail"]

    def test_an_unreadable_goal_store_is_503_not_a_404(self, goal_store) -> None:
        goal_id = _admin().post("/api/apex/goals", json={"objective": "fix"}).json()["goal"]["goal_id"]
        goal_store._load_error = "OSError: goals.json unreadable"

        response = _admin().get(f"/api/apex/goals/{goal_id}")

        assert response.status_code == 503
        assert "OSError" in response.json()["detail"]

    def test_corruption_discovered_during_goal_read_is_503_not_a_404(self, goal_store) -> None:
        goal_id = _admin().post("/api/apex/goals", json={"objective": "fix"}).json()["goal"]["goal_id"]
        goal_store.storage_path.write_text("{broken", encoding="utf-8")

        response = _admin().get(f"/api/apex/goals/{goal_id}")

        assert response.status_code == 503
        assert "JSONDecodeError" in response.json()["detail"]

    def test_corruption_discovered_during_session_read_is_503_not_a_404(self, store: ApexStore) -> None:
        session = _create_session(_admin())
        store.storage_path.write_text("{broken", encoding="utf-8")

        response = _admin().get(f"/api/apex/sessions/{session['session_id']}")

        assert response.status_code == 503
        assert "JSONDecodeError" in response.json()["detail"]

    def test_corruption_discovered_during_goal_list_is_unknown_not_empty(self, goal_store) -> None:
        _admin().post("/api/apex/goals", json={"objective": "fix"})
        goal_store.storage_path.write_text("{broken", encoding="utf-8")

        response = _admin().get("/api/apex/goals")

        assert response.status_code == 200
        assert response.json()["available"] is False
        assert response.json()["count"] is None

    def test_corruption_discovered_during_session_list_is_unknown_not_empty(self, store: ApexStore) -> None:
        _create_session(_admin())
        store.storage_path.write_text("{broken", encoding="utf-8")

        response = _admin().get("/api/apex/sessions")

        assert response.status_code == 200
        assert response.json()["available"] is False
        assert response.json()["count"] is None

    def test_corruption_discovered_during_goal_decisions_is_503(self, store: ApexStore, goal_store) -> None:
        session = store.create(
            objective="session",
            owner="admin-1",
            profile="autonomous",
            contract_digest=profile_for("autonomous").digest(),
        )
        goal = goal_store.create(objective="goal", owner="admin-1", session_id=session.session_id)
        store.storage_path.write_text("{broken", encoding="utf-8")

        response = _admin().get(f"/api/apex/goals/{goal.goal_id}/decisions")

        assert response.status_code == 503
        assert "JSONDecodeError" in response.json()["detail"]

    def test_a_missing_file_is_still_just_a_404(self, store: ApexStore) -> None:
        """Absent must not become a fault: only an unreadable source is 503."""
        response = _admin().get("/api/apex/sessions/apx-never-existed")
        assert response.status_code == 404

    def test_a_goal_write_refuses_on_an_unreadable_store(self, goal_store) -> None:
        goal_store._load_error = "OSError: goals.json unreadable"
        response = _admin().post("/api/apex/goals", json={"objective": "fix"})
        assert response.status_code == 503
        assert "OSError" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# 4. Bounded approvals, with the bound disclosed
# --------------------------------------------------------------------------- #


class TestApprovalsAreBounded:
    def test_the_bound_is_returned_and_truncated(self, store: ApexStore) -> None:
        for index in range(5):
            session = store.create(
                owner="admin-1",
                objective=f"mission {index}",
                profile="autonomous",
                contract_digest=profile_for("autonomous").digest(),
            )
            store.set_state(session.session_id, ApexSessionState.BLOCKED, reason="acceptance pending")
            store.request_approval(session.session_id, note="acceptance pending", requester="apex.executive")

        body = _admin().get("/api/apex/approvals", params={"limit": 2}).json()

        # The backlog is the whole set even though the list is bounded: a
        # truncated response reporting its own length as the backlog would be
        # a smaller number than the gate is actually holding.
        assert body["count"] == 5
        assert body["pending"] == 5
        assert body["returned"] == 2
        assert body["truncated"] is True
        assert len(body["approvals"]) == 2

    def test_an_unbounded_response_says_so(self, store: ApexStore) -> None:
        body = _admin().get("/api/apex/approvals").json()
        assert body["returned"] == body["count"]
        assert body["truncated"] is False

    def test_the_limit_is_refused_out_of_range(self, store: ApexStore) -> None:
        assert _admin().get("/api/apex/approvals", params={"limit": 0}).status_code == 422
        assert _admin().get("/api/apex/approvals", params={"limit": 501}).status_code == 422


# --------------------------------------------------------------------------- #
# 5. The stream must not drop silently
# --------------------------------------------------------------------------- #


class TestBacklogDisclosure:
    def test_drops_are_counted_once_then_cleared(self) -> None:
        discloser = apex_router._BacklogDiscloser()
        assert discloser.take() == 0
        discloser.note_drop()
        discloser.note_drop()
        assert discloser.take() == 2
        assert discloser.take() == 0

    def test_a_full_queue_counts_the_drop_instead_of_swallowing_it(self) -> None:
        """The real overflow path, exercised rather than inspected.

        ``MissionEventFeed`` declares an ``_overflowed`` counter that nothing
        ever reads, so its docstring promises a disclosure that never happens.
        This drives the actual function the stream's subscriber calls, on a
        queue that is already full, so the count cannot rot into the same dead
        declaration.
        """
        discloser = apex_router._BacklogDiscloser()
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)

        apex_router._enqueue_or_disclose(queue, object(), discloser)
        apex_router._enqueue_or_disclose(queue, object(), discloser)
        apex_router._enqueue_or_disclose(queue, object(), discloser)

        assert queue.qsize() == 1, "the first event must still be delivered"
        # Two were refused; both must be disclosed, not one and not none.
        assert discloser.take() == 2

    def test_the_stream_actually_uses_it(self) -> None:
        """Pin the wiring, not just the helper — the disclosure must reach a client."""
        source = inspect.getsource(apex_router.session_events)
        assert "_enqueue_or_disclose" in source, "the subscriber must go through the counted path"
        assert "event: gap" in source, "a counted drop must reach the client as a frame"
        assert "recovery" in source, "the disclosure must name how to recover the lost events"
        assert "discloser.take()" in source, "the count must be cleared each turn or it repeats forever"

    def test_the_overflow_path_never_says_pass(self) -> None:
        tree = ast.parse(inspect.getsource(apex_router._enqueue_or_disclose))
        handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]
        assert handlers, "the overflow path must catch QueueFull"
        assert not any(isinstance(node, ast.Pass) for handler in handlers for node in handler.body), "a silent QueueFull is the defect this replaced"


# --------------------------------------------------------------------------- #
# 6. The owner-scoped list is still a real list
# --------------------------------------------------------------------------- #


class TestOwnerScopingDoesNotBlankTheList:
    def test_an_admin_sees_every_session(self, store: ApexStore) -> None:
        store.create(owner="someone-else", objective="theirs", profile="autonomous", contract_digest=profile_for("autonomous").digest())
        body = _admin().get("/api/apex/sessions").json()
        assert body["count"] == 1

    def test_a_non_admin_sees_only_their_own(self, store: ApexStore) -> None:
        store.create(owner="someone-else", objective="theirs", profile="autonomous", contract_digest=profile_for("autonomous").digest())
        body = _plain().get("/api/apex/sessions").json()
        assert body["available"] is True
        assert body["count"] == 0
        assert body["sessions"] == []

    def test_a_goal_list_is_still_owner_scoped(self, goal_store) -> None:
        _admin().post("/api/apex/goals", json={"objective": "admin's"})
        _plain().post("/api/apex/goals", json={"objective": "mine"})
        mine = _plain().get("/api/apex/goals").json()
        assert [g["objective"] for g in mine["goals"]] == ["mine"]
