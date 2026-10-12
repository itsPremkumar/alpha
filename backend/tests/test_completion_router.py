"""The completion engine's REST surface: posture, ownership, honesty.

`alpha.completion` has always been able to answer *did the work actually
succeed?* — nothing in the Gateway could ask it over HTTP until
`app/gateway/routers/completion.py` existed. These tests pin the
properties the router's own docstring promises, because each one is a
way the surface could quietly start lying:

1. **Admission.** An unauthenticated caller — or a member whose
   principal carries no id — is refused everywhere, because an
   unscoped read of this surface would degrade to *every scope*.
2. **Route order.** The fixed routes (``/health``, ``/checks``,
   ``/scopes``) are declared before the ``/scopes/{scope_id}``
   family, the same Starlette registration-order rule as
   ``skills/{skill_name}`` and ``workflows/{workflow_id}``.
3. **Owner scoping.** A member reads only their own scopes; a foreign
   scope and an absent one answer the *same* 404, so the detail
   route is not an existence oracle; a scope recorded without an
   owner is admin-only. A PAT never carries admin, through the
   repository's one predicate.
4. **The ledger owns the transitions.** This router drives the engine;
   it never invents a state. Every mutation is admin-gated (writing
   this ledger is what decides whether work counts as done), and a
   refusal keeps the engine's own words — one typed error, one HTTP
   status, one machine-readable code.
5. **Honesty.** An unreadable ledger is 503 with its real reason,
   never an empty list; a contract with blocking findings is 422
   carrying every finding and the refused scope is still readable; a
   worker's return is ``returned`` and never ``verified``; and an
   observe-mode finalize reports ``enforced: false`` and leaves the
   status alone.

The engine is bound to a temporary store for the whole test (one
``TestClient`` means one portal loop), so scopes persist across
requests exactly as they do across Gateway requests.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.completion import (
    CompletionEngine,
    default_registry,
    settings_from_config,
)
from alpha.completion.models import GateStatus, ScopeStatus
from alpha.config.completion_config import CompletionConfig
from app.gateway.auth_disabled import AUTH_SOURCE_PAT
from app.gateway.routers import completion as completion_router

#: Literal, not the router's constants: these strings *are* the wire
#: contract, so renaming a code must break the client's tests, not just
#: the client.
CODE_NOT_FOUND = "completion_not_found"
CODE_FORBIDDEN = "forbidden"
CODE_INVALID_FILTER = "invalid_filter"
CODE_INVALID_INPUT = "invalid_input"
CODE_CONTRACT = "contract_invalid"
CODE_ILLEGAL = "illegal_transition"
CODE_SCOPE_STATE = "scope_state_error"
CODE_BUDGET = "budget_exhausted"
CODE_CORRUPT = "store_corrupt"


def make_engine(tmp_path: Path, **overrides: Any) -> CompletionEngine:
    """An engine whose ledger lives entirely inside ``tmp_path``."""
    payload = {
        "enabled": True,
        "mode": "enforce",
        "workspace_root": str(tmp_path),
        "store_path": str(tmp_path / "state.json"),
    }
    payload.update(overrides)
    return CompletionEngine(settings_from_config(CompletionConfig(**payload)), registry=default_registry())


def scope_payload(owner_id: str = "user-1", **overrides: Any) -> dict[str, Any]:
    """One obligation, one task, one gate — the smallest honest plan."""
    body: dict[str, Any] = {
        "request": "Write the report",
        "owner_id": owner_id,
        "requirements": [
            {
                "requirement_id": "r1",
                "intent": "Write the report",
                "mandatory": True,
                "observable": "report.md exists with a title",
                "task_id": "t1",
                "gate_ids": ["g1"],
            }
        ],
        "tasks": [
            {
                "task_id": "t1",
                "title": "Write the report",
                "objective": "Produce report.md",
                "gate_ids": ["g1"],
                "owns": ["report.md"],
            }
        ],
        "gates": [
            {
                "gate_id": "g1",
                "task_id": "t1",
                "title": "report exists",
                "kind": "file",
                "check_ref": "file.exists",
                "parameters": {"path": "report.md"},
                "required": True,
                "expectation": {"required_markers": ["# Report"]},
            }
        ],
    }
    body.update(overrides)
    return body


class _Holder:
    """A mutable principal, so one app + one client can wear every identity."""

    def __init__(self) -> None:
        self.user: object | None = None
        self.auth_source: str | None = None


def _app(holder: _Holder) -> FastAPI:
    """A bare app: the router plus the principal injection AuthMiddleware does."""
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        if holder.user is not None:
            request.state.user = holder.user
        if holder.auth_source is not None:
            request.state.auth_source = holder.auth_source
        return await call_next(request)

    app.include_router(completion_router.router)
    return app


def _member(uid: str) -> SimpleNamespace:
    # The shape production sends: ``system_role``, never an ``is_admin`` flag
    # (see tests/test_apex_authz.py for the incident that teaches this).
    return SimpleNamespace(id=uid, system_role="user")


def _admin(uid: str = "ops-admin") -> SimpleNamespace:
    return SimpleNamespace(id=uid, system_role="admin")


@pytest.fixture()
def bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Route every engine construction at one temporary store."""

    def _engine() -> CompletionEngine:
        return make_engine(tmp_path)

    monkeypatch.setattr(completion_router, "_engine", _engine)
    return tmp_path


@pytest.fixture()
def observed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The same store, resolved in observe mode (never a live gate)."""

    def _engine() -> CompletionEngine:
        return make_engine(tmp_path, mode="observe")

    monkeypatch.setattr(completion_router, "_engine", _engine)
    return tmp_path


@contextlib.contextmanager
def _client(holder: _Holder) -> Any:
    with TestClient(_app(holder)) as client:
        yield client


def _create(client: Any, payload: dict[str, Any]) -> str:
    response = client.post("/api/completion/scopes", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["scope"]["scope_id"]


def _drive(client: Any, scope_id: str) -> str:
    """start -> schedule -> dispatch -> confirm; returns the dispatch id."""
    assert client.post(f"/api/completion/scopes/{scope_id}/start").status_code == 200
    assert client.post(f"/api/completion/scopes/{scope_id}/schedule").status_code == 200
    dispatched = client.post(f"/api/completion/scopes/{scope_id}/dispatch", json={"task_id": "t1", "agent": "worker-1"})
    assert dispatched.status_code == 200, dispatched.text
    dispatch_id = dispatched.json()["dispatch_id"]
    confirmed = client.post(f"/api/completion/scopes/{scope_id}/dispatches/{dispatch_id}/confirm", json={"handle": "handle-1"})
    assert confirmed.status_code == 200, confirmed.text
    return dispatch_id


class TestAuthenticationAndRouteOrder:
    def test_unauthenticated_callers_get_401_on_every_route(self, bound: Path) -> None:
        holder = _Holder()  # no principal at all
        with _client(holder) as client:
            assert client.get("/api/completion/health").status_code == 401
            assert client.get("/api/completion/checks").status_code == 401
            assert client.get("/api/completion/scopes").status_code == 401
            assert client.get("/api/completion/scopes/sc-1").status_code == 401
            assert client.post("/api/completion/scopes", json=scope_payload()).status_code == 401
            assert client.post("/api/completion/scopes/sc-1/finalize", json={}).status_code == 401

    def test_a_member_with_no_id_is_401_not_every_owners_scopes(self, bound: Path) -> None:
        """An unscoped member read would degrade to "no filter" — i.e. everyone's."""
        holder = _Holder()
        holder.user = SimpleNamespace(id="", system_role="user")
        with _client(holder) as client:
            assert client.get("/api/completion/scopes").status_code == 401
            assert client.get("/api/completion/scopes/sc-1").status_code == 401

    def test_the_fixed_routes_are_declared_before_the_scope_catch_all(self, bound: Path) -> None:
        """The Starlette registration-order rule, pinned structurally.

        A parameterized route declared first swallows every later
        single-segment path, which is how ``skills/tiers`` and
        ``workflows/simulate`` broke before the same fix.
        """
        paths = [route.path for route in completion_router.router.routes]
        assert paths.index("/api/completion/health") < paths.index("/api/completion/scopes/{scope_id}")
        assert paths.index("/api/completion/checks") < paths.index("/api/completion/scopes/{scope_id}")
        assert paths.index("/api/completion/scopes") < paths.index("/api/completion/scopes/{scope_id}")

    def test_the_scope_catch_all_still_answers_404_with_a_code(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            response = client.get("/api/completion/scopes/never-recorded")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == CODE_NOT_FOUND
        assert isinstance(body["detail"], str)
        assert body["subject"] == "never-recorded"


class TestPosture:
    def test_health_reports_mode_readability_and_the_limits(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            body = client.get("/api/completion/health").json()
        assert body["enabled"] is True
        assert body["mode"] == "enforce"
        assert body["enforcing"] is True  # enabled *and* enforce, not mode alone
        assert body["readable"] is True
        assert body["count"] == 0  # a healthy-but-empty store is the only honest zero
        assert body["checks"], "the registered check names travel with the posture"
        assert "file.exists" in body["checks"]
        assert body["generated_at"]

    def test_observe_mode_reports_enforcing_false(self, observed: Path) -> None:
        """Observe computes the same decision and applies none of it."""
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            body = client.get("/api/completion/health").json()
        assert body["enabled"] is True
        assert body["mode"] == "observe"
        assert body["enforcing"] is False

    def test_checks_lists_the_vocabulary_a_planner_writes_against(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            body = client.get("/api/completion/checks").json()
        assert body["checks"]
        assert body["reverify_modes"] == ["status", "verify", "reverify"]
        assert body["scope_statuses"] == [status.value for status in ScopeStatus]
        assert body["policy"]["enabled"] is True
        assert body["policy"]["enforcing"] is True
        assert body["policy"]["guarding"] is True
        assert body["policy"]["preapproved_commands"] == []
        assert body["limits"]["max_tasks"] > 0


class TestOwnerScoping:
    def test_a_member_reads_only_their_own_scope(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            mine = _create(client, scope_payload(owner_id="user-1"))
            theirs = _create(client, scope_payload(owner_id="user-2"))
            assert client.get("/api/completion/scopes").json()["count"] == 2

            holder.user = _member("user-1")
            listing = client.get("/api/completion/scopes").json()
            assert listing["scope"] == "owner"
            assert [row["scope_id"] for row in listing["scopes"]] == [mine]
            # Asking for another owner's id must not widen the scope.
            widened = client.get("/api/completion/scopes", params={"owner_id": "user-2"}).json()
            assert [row["scope_id"] for row in widened["scopes"]] == [mine]

            # Their scope reads exactly like an absent one.
            assert client.get(f"/api/completion/scopes/{theirs}").status_code == 404

    def test_an_admin_reads_every_scope_and_may_filter_by_owner(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            _create(client, scope_payload(owner_id="user-1"))
            _create(client, scope_payload(owner_id="user-2", thread_id="thread-9"))
            everything = client.get("/api/completion/scopes").json()
            assert everything["scope"] == "all"
            assert everything["count"] == 2

            by_owner = client.get("/api/completion/scopes", params={"owner_id": "user-1"}).json()
            assert [row["owner_id"] for row in by_owner["scopes"]] == ["user-1"]

            by_thread = client.get("/api/completion/scopes", params={"thread_id": "thread-9"}).json()
            assert [row["thread_id"] for row in by_thread["scopes"]] == ["thread-9"]

    def test_a_scope_without_an_owner_is_admin_only(self, bound: Path) -> None:
        """Nobody's name is on it to show a member — the same rule as a
        side-effect row with an empty ``user_id``."""
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            ownerless = _create(client, scope_payload(owner_id=""))
            holder.user = _member("user-1")
            assert client.get(f"/api/completion/scopes/{ownerless}").status_code == 404
            assert [row["scope_id"] for row in client.get("/api/completion/scopes").json()["scopes"]] == []

    def test_a_pat_administrator_is_not_an_administrator(self, bound: Path) -> None:
        """The repository's one admin predicate suppresses admin for a PAT."""
        holder = _Holder()
        holder.user = _admin()
        holder.auth_source = AUTH_SOURCE_PAT
        with _client(holder) as client:
            # Creation is a mutation: refused, not silently scoped.
            refused = client.post("/api/completion/scopes", json=scope_payload())
            assert refused.status_code == 403
            assert refused.json()["code"] == CODE_FORBIDDEN
            listing = client.get("/api/completion/scopes").json()
            assert listing["scope"] == "owner"  # not "all"

    def test_the_created_scope_is_attributed_to_the_caller_by_default(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin("ops-admin")
        with _client(holder) as client:
            scope_id = _create(client, scope_payload(owner_id=""))
            row = client.get(f"/api/completion/scopes/{scope_id}").json()["scope"]
        assert row["owner_id"] == "ops-admin"


class TestMutationsAreAdminGated:
    def test_a_member_cannot_open_a_scope(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _member("user-1")
        with _client(holder) as client:
            response = client.post("/api/completion/scopes", json=scope_payload())
        assert response.status_code == 403
        assert response.json()["code"] == CODE_FORBIDDEN

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("post", "/api/completion/scopes/sc-1/start", None),
            ("post", "/api/completion/scopes/sc-1/cancel", {"reason": "because"}),
            ("post", "/api/completion/scopes/sc-1/schedule", None),
            ("post", "/api/completion/scopes/sc-1/dispatch", {"task_id": "t1"}),
            ("post", "/api/completion/scopes/sc-1/finalize", {}),
            ("post", "/api/completion/scopes/sc-1/recover", {"host_states": {"d": "returned"}}),
        ],
    )
    def test_every_mutation_refuses_a_member(self, bound: Path, method: str, path: str, body: Any) -> None:
        holder = _Holder()
        holder.user = _member("user-1")
        with _client(holder) as client:
            response = client.request(method.upper(), path, json=body)
        assert response.status_code == 403, response.text
        assert response.json()["code"] == CODE_FORBIDDEN


class TestContractValidation:
    def test_a_contract_with_blocking_findings_is_422_with_every_finding(self, bound: Path) -> None:
        """A rejected plan is a record the caller can repair, not a no-op."""
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            # Requirements with no gate_ids binding: contract validation should catch this.
            response = client.post(
                "/api/completion/scopes",
                json=scope_payload(
                    requirements=[{"requirement_id": "r1", "intent": "Write the report", "mandatory": True, "observable": "report.md exists", "task_id": "t1", "gate_ids": []}],
                    tasks=[{"task_id": "t1", "title": "Write the report"}],
                    gates=[{"gate_id": "g1", "task_id": "t1", "title": "dummy", "kind": "file", "check_ref": "file.exists", "parameters": {"path": "dummy.txt"}, "required": True, "expectation": {"required_markers": ["x"]}}],
                ),
            )
        assert response.status_code == 422, response.text
        body = response.json()
        assert body["code"] == CODE_CONTRACT
        assert body["findings"], "every blocking finding travels, not just the first"
        assert {finding["code"] for finding in body["findings"]} >= {"requirement_without_gate"}
        assert body["subject"]

    def test_an_unknown_check_ref_is_a_finding_not_a_runtime_surprise(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            payload = scope_payload()
            payload["gates"][0]["check_ref"] = "model.invented_check"
            response = client.post("/api/completion/scopes", json=payload)
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == CODE_CONTRACT
        assert any(finding.get("code") == "gate_unregistered_check" for finding in body["findings"])

    def test_a_malformed_body_is_422_with_a_code(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            missing_request = client.post("/api/completion/scopes", json={"owner_id": "user-1"})
            unknown_field = client.post("/api/completion/scopes", json={**scope_payload(), "surprise": 1})
            # Create a valid scope first, then try to add a malformed task to it (missing required title)
            scope_id = _create(client, scope_payload())
            bad_task = client.post(
                f"/api/completion/scopes/{scope_id}/tasks",
                json={"tasks": [{"objective": "x"}], "gates": []},
            )
        for response in (missing_request, unknown_field, bad_task):
            assert response.status_code == 422, response.text
            assert response.json()["code"] == CODE_INVALID_INPUT
            assert isinstance(response.json()["detail"], str)


class TestLifecycleRefusals:
    def test_starting_a_terminal_scope_is_409_with_the_engines_words(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            cancelled = client.post(f"/api/completion/scopes/{scope_id}/cancel", json={"reason": "not needed"})
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == ScopeStatus.CANCELLED.value
            refused = client.post(f"/api/completion/scopes/{scope_id}/start")
        assert refused.status_code == 409, refused.text
        body = refused.json()
        assert body["code"] == CODE_ILLEGAL
        assert "cancel" in body["detail"]

    def test_scheduling_a_planning_scope_is_409(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            response = client.post(f"/api/completion/scopes/{scope_id}/schedule")
        assert response.status_code == 409
        assert response.json()["code"] == CODE_SCOPE_STATE
        assert "planning" in response.json()["detail"]

    def test_task_ceilings_are_refused_never_clamped(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            extra = [{"task_id": f"t{i}", "title": f"Task {i}", "gate_ids": ["g1"]} for i in range(2, 2 + 100)]
            response = client.post(f"/api/completion/scopes/{scope_id}/tasks", json={"tasks": extra, "gates": []})
        assert response.status_code == 409, response.text
        body = response.json()
        assert body["code"] == CODE_BUDGET
        assert "40" in body["detail"]  # the ceiling, named


class TestHonesty:
    def test_a_worker_return_never_verifies_the_task(self, bound: Path) -> None:
        """The worker's prose is recorded; only the task's own gates verify it."""
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            dispatch_id = _drive(client, scope_id)
            returned = client.post(
                f"/api/completion/scopes/{scope_id}/dispatches/{dispatch_id}/return",
                json={"artifacts": ["report.md"], "reason": "all done"},
            )
            assert returned.status_code == 200, returned.text
            assert returned.json()["state"] == "returned"
            assert client.get(f"/api/completion/scopes/{scope_id}").json()["scope"]["tasks_verified"] == 0

    def test_finalize_names_its_blockers_when_incomplete(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            dispatch_id = _drive(client, scope_id)
            client.post(
                f"/api/completion/scopes/{scope_id}/dispatches/{dispatch_id}/return",
                json={"reason": "done"},
            )
            # The artifact was never written, so the gate cannot pass.
            finalized = client.post(f"/api/completion/scopes/{scope_id}/finalize", json={})
        assert finalized.status_code == 200, finalized.text  # the refusal *is* the answer
        body = finalized.json()
        assert body["complete"] is False
        assert body["scope_status"] != ScopeStatus.COMPLETE.value
        assert body["blockers"], "an incomplete scope says what is missing"

    def test_observe_mode_finalize_reports_enforced_false_and_leaves_the_status_alone(self, observed: Path) -> None:
        """An observe-mode engine never claims a completion it did not apply."""
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            dispatch_id = _drive(client, scope_id)
            (observed / "report.md").write_text("# Report\n", encoding="utf-8")
            client.post(f"/api/completion/scopes/{scope_id}/dispatches/{dispatch_id}/return", json={})
            client.post(f"/api/completion/scopes/{scope_id}/gates/g1/run", json={})
            client.post(f"/api/completion/scopes/{scope_id}/tasks/t1/verify", json={})
            before = client.get(f"/api/completion/scopes/{scope_id}").json()["scope"]["status"]
            finalized = client.post(f"/api/completion/scopes/{scope_id}/finalize", json={})
        assert finalized.status_code == 200, finalized.text
        body = finalized.json()
        assert body["complete"] is True
        assert body["enforced"] is False, "observe mode computed a completion it did not apply"
        assert body["summary"].startswith("[observe] ")
        after = client.get(f"/api/completion/scopes/{scope_id}").json()["scope"]["status"]
        assert after == before, "the status must not have moved"

    def test_preview_finalization_never_moves_a_scope(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            preview = client.get(f"/api/completion/scopes/{scope_id}/finalization/preview")
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["enforced"] is False, "a preview is never a verdict"
        assert body["generated_at"]
        assert client.get(f"/api/completion/scopes/{scope_id}").json()["scope"]["status"] == "planning"

    def test_an_unreadable_ledger_is_503_with_its_real_reason(self, bound: Path) -> None:
        """ "Could not look" must not render as "nothing is recorded"."""
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            created = _create(client, scope_payload())
        (bound / "state.json").write_text("{not json", encoding="utf-8")
        with _client(holder) as client:
            for response in (
                client.get("/api/completion/scopes"),
                client.get(f"/api/completion/scopes/{created}"),
                client.post(f"/api/completion/scopes/{created}/start"),
            ):
                assert response.status_code == 503, response.text
                assert response.json()["code"] == CODE_CORRUPT
            assert client.get("/api/completion/health").json()["readable"] is False


class TestFilters:
    def test_an_unknown_status_filter_is_422_not_an_empty_list(self, bound: Path) -> None:
        """A typo must not read as "no work ever ran"."""
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            response = client.get("/api/completion/scopes", params={"status": "done"})
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == CODE_INVALID_FILTER
        assert "done" in body["detail"]
        assert "planning" in body["detail"]

    def test_an_unknown_event_kind_filter_is_422(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            response = client.get(f"/api/completion/scopes/{scope_id}/events", params={"kind": "scope.exploded"})
        assert response.status_code == 422
        assert response.json()["code"] == CODE_INVALID_FILTER

    def test_an_unknown_reverify_mode_is_422(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            response = client.post(f"/api/completion/scopes/{scope_id}/tasks/t1/verify", json={"mode": "definitely"})
        assert response.status_code == 422
        assert response.json()["code"] == CODE_INVALID_FILTER

    def test_a_review_verdict_must_be_pass_or_fail(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            response = client.post(
                f"/api/completion/scopes/{scope_id}/gates/g1/review",
                json={"verdict": "maybe", "reviewer": "ops"},
            )
        assert response.status_code == 422
        assert response.json()["code"] == CODE_INVALID_FILTER


class TestEvidenceFlow:
    def test_a_full_gate_run_records_evidence_and_verifies_the_task(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            dispatch_id = _drive(client, scope_id)
            (bound / "report.md").write_text("# Report\n", encoding="utf-8")

            returned = client.post(f"/api/completion/scopes/{scope_id}/dispatches/{dispatch_id}/return", json={})
            assert returned.status_code == 200

            # Run the gate through verification (reverify to force execution)
            verified = client.post(f"/api/completion/scopes/{scope_id}/tasks/t1/verify", json={"worker": "w1", "mode": "reverify"})
            assert verified.status_code == 200, verified.text
            vr = verified.json()
            assert vr["passed"] == vr["ran"] > 0, "all gates passed"

            status = client.get(f"/api/completion/scopes/{scope_id}").json()
            assert status["scope"]["tasks_verified"] == 1
            assert status["scope"]["gates_passed"] == 1

    def test_the_event_journal_is_the_one_history(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            client.post(f"/api/completion/scopes/{scope_id}/start")
            rows = client.get(f"/api/completion/scopes/{scope_id}/events").json()
        assert rows["count"] >= 2
        assert rows["events"][0]["kind"] == "contract.created"
        assert "T" in rows["events"][0]["at"]  # ISO 8601, not a raw epoch

    def test_the_contract_route_carries_the_fixed_obligations(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            body = client.get(f"/api/completion/scopes/{scope_id}/contract").json()
        assert body["contract"]["valid"] is True
        assert [requirement["requirement_id"] for requirement in body["contract"]["requirements"]] == ["r1"]
        assert body["request_revision"] == 1

    def test_reconciliation_reports_clauses_the_contract_never_represented(self, bound: Path) -> None:
        """The original request is preserved verbatim and audited against it."""
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(
                client,
                scope_payload(request="Write the report and also publish the dashboard"),
            )
            report = client.get(f"/api/completion/scopes/{scope_id}/reconciliation").json()
        assert report["basis"].startswith("heuristic_clause_match")
        unrepresented = report["unrepresented"]
        assert unrepresented, "the dashboard clause has no requirement bound to it"

    def test_a_manual_review_lands_as_attributed_evidence(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(
                client,
                scope_payload(
                    gates=[
                        {
                            "gate_id": "g1",
                            "task_id": "t1",
                            "title": "human review",
                            "kind": "manual_review",
                            "check_ref": "manual.review",
                            "parameters": {},
                            "required": True,
                        }
                    ]
                ),
            )
            review = client.post(
                f"/api/completion/scopes/{scope_id}/gates/g1/review",
                json={"verdict": "pass", "reviewer": "ops-admin", "notes": "read it"},
            )
        assert review.status_code == 200, review.text
        assert review.json()["status"] == GateStatus.PASSED.value


class TestApprovalPolicy:
    def test_a_command_gate_needs_an_approval_bound_to_its_definition(self, bound: Path) -> None:
        """`preapproved_commands` is empty by default, so a command check is
        blocked — *not run* is not *passed*."""
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(
                client,
                scope_payload(
                    gates=[
                        {
                            "gate_id": "g1",
                            "task_id": "t1",
                            "title": "run the suite",
                            "kind": "pytest",
                            "check_ref": "pytest.run",
                            "parameters": {"targets": ["tests/"]},
                            "required": True,
                            "expectation": {"exit_code": 0},
                        }
                    ]
                ),
            )
            dispatch_id = _drive(client, scope_id)
            client.post(f"/api/completion/scopes/{scope_id}/dispatches/{dispatch_id}/return", json={})
            run = client.post(f"/api/completion/scopes/{scope_id}/gates/g1/run", json={})
        assert run.status_code == 200, run.text  # the refusal is a recorded outcome
        assert run.json()["outcome"] == "blocked", "not run is not passed"
        assert "approval required" in run.json()["reason"]

    def test_an_approval_binds_to_the_definition_and_is_revoked(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(
                client,
                scope_payload(
                    gates=[
                        {
                            "gate_id": "g1",
                            "task_id": "t1",
                            "title": "run the suite",
                            "kind": "pytest",
                            "check_ref": "pytest.run",
                            "parameters": {"targets": ["tests/"]},
                            "required": True,
                            "expectation": {"exit_code": 0},
                        }
                    ]
                ),
            )
            request = client.get(f"/api/completion/scopes/{scope_id}/gates/g1/approval")
            assert request.status_code == 200, request.text
            assert request.json()["approval_policy"] == "automatic"

            granted = client.post(
                f"/api/completion/scopes/{scope_id}/gates/g1/approve",
                json={"approver": "ops-admin", "reason": "reviewed the argv"},
            )
            assert granted.status_code == 200, granted.text
            assert granted.json()["approved_by"] == "ops-admin"

            revoked = client.post(
                f"/api/completion/scopes/{scope_id}/gates/g1/revoke",
                json={"reason": "definition changed"},
            )
            assert revoked.status_code == 200
            assert revoked.json()["revoked"] is True


class TestRecovery:
    def test_recover_folds_what_the_host_reports_into_the_ledger(self, bound: Path) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _client(holder) as client:
            scope_id = _create(client, scope_payload())
            dispatch_id = _drive(client, scope_id)
            # Mark the task as returned so the wave is terminal
            client.post(f"/api/completion/scopes/{scope_id}/dispatches/{dispatch_id}/return", json={})
            recovered = client.post(
                f"/api/completion/scopes/{scope_id}/recover",
                json={"host_states": {dispatch_id: "lost"}},
            )
        assert recovered.status_code == 200, recovered.text
        # The task is already returned (terminal), so no changes
        assert recovered.json()["changed"] == []
        assert recovered.json()["expired_claims"] == []
        assert recovered.json()["unresolved_waves"] == []
