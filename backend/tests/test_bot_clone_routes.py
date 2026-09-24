"""HTTP-level honesty proofs for the bots router (audit batch B1: G2 + G3).

Part 1 (G3 — duplicate route shadowing): the router historically declared
TWO ``POST /api/bots/{name}/clone`` routes — the profile-copy handler
(``clone_bot`` / ``BotCloneRequest``) registered first, the clone-engine
handler (``clone_bot_endpoint`` / ``BotCloneApiRequest``) registered later
at the same path. Starlette matches the first registration, so the
clone-engine endpoint was unreachable over HTTP. After disambiguating the
second route to ``/{name}/clone-engine`` these tests prove, over real HTTP
routing (status codes + response-shape markers), that BOTH routes are
reachable and hit their DISTINCT handlers. They also pin that an
engine-shaped body posted to ``/clone`` is rejected by the PROFILE-COPY
request model (``source`` required) — the exact shape of the old shadow.

Pre-existing clone tests never exercised routing: ``test_bot_lifecycle``
and ``test_bot_dynamic_workflow`` call the handler *functions* directly
and ``test_bot_cloning_lease`` unit-tests ``BotCloneEngine`` itself, which
is why the shadowing went unnoticed.

Part 2 (G2 — perfect scores for zero work): a bot with zero recorded runs
must report ``None`` success rate / reputation with an explicit
disclosure, never a fabricated 100.0 / 1.0; recorded runs must produce
real measured values, and a bot with no stored score must move from the
neutral 0.5 prior — never from a perfect 1.0. These pins live here
because the only pre-existing bots-performance test file
(``test_bots_extended_inventory.py``) belongs to another agent.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import bots as bots_router


@pytest.fixture(autouse=True)
def _isolated_bot_state(tmp_path, monkeypatch):
    """Point every bot/ephemeral singleton at a per-test temp home."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))

    import alpha.bots.cloning as cloning_mod
    import alpha.bots.ephemeral as ephemeral_mod
    import alpha.bots.health as health_mod
    import alpha.bots.registry as registry_mod

    monkeypatch.setattr(registry_mod, "_global_registry", None)
    monkeypatch.setattr(registry_mod, "_global_registry_path", None)
    monkeypatch.setattr(ephemeral_mod, "_ephemeral_manager", None)
    monkeypatch.setattr(health_mod, "_global_monitor", None)
    # ``get_bot_clone_engine()`` caches the registry it was constructed
    # with: force a rebuild bound to THIS test's isolated registry;
    # monkeypatch restores the prior global afterwards.
    monkeypatch.setattr(cloning_mod, "_GLOBAL_CLONE_ENGINE", None)
    yield


def _build_app() -> FastAPI:
    def _admin() -> User:
        return User(
            email="bots-routes-admin@example.com",
            password_hash="x",
            system_role="admin",
            id=uuid4(),
        )

    app = make_authed_test_app(user_factory=_admin)
    app.include_router(bots_router.router)
    return app


@pytest.fixture()
def client():
    with TestClient(_build_app()) as test_client:
        yield test_client


def _ensure(client: TestClient, name: str) -> dict:
    resp = client.post(
        f"/api/bots/{name}/ensure",
        json={"display_name": f"Source {name}", "role": "Source Role", "department": "engineering"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Part 1 — G3: both clone routes reachable, distinct handlers
# ---------------------------------------------------------------------------


def test_route_table_exposes_both_clone_paths_to_distinct_handlers():
    app = _build_app()

    profile_routes = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and "POST" in r.methods and r.path.endswith("/clone")
    ]
    engine_routes = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and "POST" in r.methods and r.path.endswith("/clone-engine")
    ]

    # Exactly one profile-copy route remains on /clone — no shadow duplicate.
    assert [r.path for r in profile_routes] == ["/api/bots/{name}/clone"]
    assert [r.endpoint.__name__ for r in profile_routes] == ["clone_bot"]

    # The engine route now lives on its own disambiguated path.
    assert [r.path for r in engine_routes] == ["/api/bots/{name}/clone-engine"]
    assert [r.endpoint.__name__ for r in engine_routes] == ["clone_bot_endpoint"]


def test_profile_clone_route_reaches_profile_copy_handler(client):
    """POST /api/bots/{name}/clone → profile-copy handler (201, copy shape)."""
    source = _ensure(client, "profile-src")

    resp = client.post(
        "/api/bots/profile-copy/clone",
        json={"source": "profile-src", "display_name": "Profile Copy"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "profile-copy"
    assert body["display_name"] == "Profile Copy"
    # registry.clone_bot copies role/soul verbatim; the engine's specialist
    # markers must be absent — proof this hit the profile-copy handler.
    assert body["role"] == source["role"]
    assert body["soul"] == source["soul"]
    assert "SPECIALIST MISSION DIRECTIVE" not in body["soul"]
    assert body["skills"] == source["skills"]
    assert "(Fork:" not in body["display_name"]
    # A fresh copy has no recorded runs: reputation is disclosed, not invented.
    assert body["reputation_score"] is None
    assert body["reputation_basis"] == "no recorded runs — unverified"


def test_clone_engine_route_reaches_engine_handler(client):
    """POST /api/bots/{name}/clone-engine → engine handler (200, fork shape)."""
    _ensure(client, "engine-src")

    resp = client.post(
        "/api/bots/engine-src/clone-engine",
        json={
            "target_name": "engine-fork",
            "mode": "specialist_fork",
            "specialist_directive": "Handle the engine-route reachability proof.",
            "skills_to_add": ["route_proof"],
            "tools_to_add": ["route_proof_tool"],
            "ttl_seconds": 600,
        },
    )
    assert resp.status_code == 200, resp.text  # engine handler default, not 201
    body = resp.json()
    assert body["name"] == "engine-fork"
    # Only the clone engine produces these markers; the profile-copy handler
    # cannot (its request model has no skills/tools/directive fields).
    assert "route_proof" in body["skills"]
    assert "route_proof_tool" in body["toolsets"]
    assert "SPECIALIST MISSION DIRECTIVE" in body["soul"]
    assert body["role"].endswith(" [Specialist]")
    assert "(Fork: engine-fork)" in body["display_name"]
    assert body["reputation_score"] is None
    assert body["reputation_basis"] == "no recorded runs — unverified"


def test_clone_engine_route_unknown_source_is_404(client):
    resp = client.post(
        "/api/bots/ghost-bot/clone-engine",
        json={"target_name": "should-not-exist"},
    )
    assert resp.status_code == 404, resp.text


def test_engine_shaped_body_on_clone_path_is_bound_to_profile_copy_model(client):
    """Documents the old shadow: /clone is served by the profile-copy model.

    An engine-shaped body (``target_name``/``mode``/``skills_to_add`` — the
    only shape the previously unreachable engine route accepted) posted to
    ``/clone`` is rejected with 422 because the PROFILE-COPY request model
    requires ``source``. Before the disambiguation this body silently
    failed the same way while the engine handler stayed unreachable.
    """
    _ensure(client, "shadow-src")
    resp = client.post(
        "/api/bots/shadow-src/clone",
        json={
            "target_name": "engine-shaped",
            "mode": "specialist_fork",
            "skills_to_add": ["route_proof"],
        },
    )
    assert resp.status_code == 422, resp.text
    assert "source" in resp.text


# ---------------------------------------------------------------------------
# Part 2 — G2: zero-record states are None + disclosed, never 100.0 / 1.0
# ---------------------------------------------------------------------------


def test_zero_record_bot_reports_none_with_disclosure(client):
    _ensure(client, "fresh-bot")

    profile = client.get("/api/bots/fresh-bot")
    assert profile.status_code == 200, profile.text
    profile_body = profile.json()
    assert profile_body["reputation_score"] is None
    assert profile_body["reputation_basis"] == "no recorded runs — unverified"

    perf = client.get("/api/bots/fresh-bot/performance")
    assert perf.status_code == 200, perf.text
    perf_body = perf.json()
    # Old behavior fabricated perfect scores for zero work: 100.0 success
    # and 1.0 reputation. Both must now be None with a disclosure.
    assert perf_body["success_rate_percent"] is None
    assert perf_body["reputation_score"] is None
    assert perf_body["reputation_tier"] == "Unverified"
    assert perf_body["total_runs"] == 0
    assert perf_body["completed_runs"] == 0
    assert perf_body["basis"] == "no recorded runs — unverified"


def test_recorded_runs_produce_measured_values(client):
    _ensure(client, "runner")

    first = client.post(
        "/api/bots/runner/record-task",
        json={"success": True, "duration_sec": 2.0, "task_id": "run-1"},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["task_stats"]["total_runs"] == 1
    assert isinstance(first_body["reputation_score"], (int, float))
    assert first_body["reputation_basis"] == "based on 1 recorded run(s)"

    second = client.post(
        "/api/bots/runner/record-task",
        json={"success": False, "duration_sec": 1.0, "task_id": "run-2"},
    )
    assert second.status_code == 200, second.text

    perf = client.get("/api/bots/runner/performance").json()
    assert perf["total_runs"] == 2
    assert perf["completed_runs"] == 1
    assert perf["failed_runs"] == 1
    assert perf["success_rate_percent"] == 50.0
    assert isinstance(perf["reputation_score"], (int, float))
    assert 0.0 <= perf["reputation_score"] <= 1.0
    assert perf["basis"] == "based on 2 recorded run(s)"
    # The failure must lower reputation below the post-success value.
    assert perf["reputation_score"] < first_body["reputation_score"]

    profile = client.get("/api/bots/runner").json()
    assert isinstance(profile["reputation_score"], (int, float))
    assert profile["reputation_basis"] == "based on 2 recorded run(s)"


def test_first_recorded_outcome_neutral_prior_not_perfect():
    """A bot with no stored score starts from the neutral 0.5 prior.

    The old ``else 1.0`` fallback let an unverified bot's FIRST recorded
    run land on a perfect score; the honest baseline is the disclosed
    neutral prior, nudged by exactly one run's evidence.
    """
    from alpha.bots.performance import get_bot_performance, record_task_outcome
    from alpha.bots.profile import BotProfile
    from alpha.bots.registry import get_bot_registry

    reg = get_bot_registry()
    reg.register(
        BotProfile(
            name="unverified-win",
            display_name="Unverified Winner",
            role="Rookie",
            soul="No runs recorded yet.",
            reputation_score=None,  # explicitly unverified: never measured
        )
    )
    reg.register(
        BotProfile(
            name="unverified-loss",
            display_name="Unverified Loser",
            role="Rookie",
            soul="No runs recorded yet.",
            reputation_score=None,
        )
    )

    # Zero runs on a None score: every reported metric is None + disclosed.
    perf = get_bot_performance("unverified-win", registry=reg)
    assert perf["success_rate_percent"] is None
    assert perf["reputation_score"] is None
    assert perf["basis"] == "no recorded runs — unverified"

    # First success: neutral 0.5 prior + one-run boost (0.02) — not 1.0.
    winner = record_task_outcome("unverified-win", success=True, registry=reg)
    assert winner is not None
    assert winner.reputation_score == pytest.approx(0.52)
    assert winner.reputation_score < 1.0

    # First failure: neutral 0.5 prior − failure penalty (0.05) — not 0.0
    # and not a no-op on a perfect default.
    loser = record_task_outcome("unverified-loss", success=False, registry=reg)
    assert loser is not None
    assert loser.reputation_score == pytest.approx(0.45)
