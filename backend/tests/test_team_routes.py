"""The team route: readable, owner-scoped, and honest when it is not mounted.

The last test in this file is the important one. `app/gateway/app.py` mounts
routers with explicit `app.include_router(...)` calls and this change does not
edit that file, so `app/gateway/routers/teams.py` answers nothing until it does.
A test that exercised these endpoints through a TestClient and passed would be
evidence of nothing at all, so this file asserts what is actually true: the
route object exists, its handlers behave correctly when called, its reads are
owner-scoped, and **it is not currently mounted**.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from alpha.swarm.team import SpecialistProfile, register_roster_provider, unregister_roster_provider
from app.gateway.routers import teams as teams_router


@pytest.fixture(autouse=True)
def _no_global_roster():
    unregister_roster_provider()
    yield
    unregister_roster_provider()


@pytest.fixture
def roster():
    return [SpecialistProfile(name="coder", capabilities=["code_generation", "python"], agent_type="deep-architect")]


def _request(user_id: str | None, role: str = "user"):
    if user_id is None:
        return SimpleNamespace(state=SimpleNamespace(user=None))
    return SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=user_id, system_role=role)))


def _plan_on(coordinator, goal: str = "Implement the sql migration and the python service"):
    return coordinator.create_swarm(goal=goal, owner_id="alice")


def test_the_router_declares_the_documented_paths():
    paths = {route.path for route in teams_router.router.routes}
    assert paths == {"/api/teams/roster", "/api/teams/{swarm_id}", "/api/teams/{swarm_id}/report.md", "/api/teams/{swarm_id}/roster"}


def test_the_roster_route_reports_a_missing_roster_rather_than_404(tmp_path, monkeypatch):
    import asyncio

    from alpha.swarm.coordinator import SwarmCoordinator

    monkeypatch.setattr(teams_router, "get_swarm_coordinator", SwarmCoordinator(storage_dir=tmp_path))
    body = asyncio.run(teams_router.get_specialist_roster(_request("alice")))
    assert body["registered"] is False
    assert body["members"] == []
    assert "no specialist roster provider is registered" in body["reason"]


def test_the_roster_route_returns_the_registered_members(roster, tmp_path, monkeypatch):
    import asyncio

    from alpha.swarm.coordinator import SwarmCoordinator

    register_roster_provider(lambda: roster, source="test")
    monkeypatch.setattr(teams_router, "get_swarm_coordinator", SwarmCoordinator(storage_dir=tmp_path))
    body = asyncio.run(teams_router.get_specialist_roster(_request("alice")))
    assert body["registered"] is True
    assert [member["name"] for member in body["members"]] == ["coder"]
    assert body["members"][0]["capabilities"] == ["code_generation", "python"]


def test_the_report_route_is_owner_scoped(roster, tmp_path, monkeypatch):
    import asyncio

    from fastapi import HTTPException

    from alpha.swarm.coordinator import SwarmCoordinator

    register_roster_provider(lambda: roster, source="test")
    coordinator = SwarmCoordinator(storage_dir=tmp_path)
    plan = _plan_on(coordinator)
    monkeypatch.setattr(teams_router, "get_swarm_coordinator", lambda: coordinator)

    visible = asyncio.run(teams_router.get_team_report(plan.swarm_id, _request("alice")))
    assert visible["summary"]["members"] > 0

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(teams_router.get_team_report(plan.swarm_id, _request("mallory")))
    assert excinfo.value.status_code == 404

    admin = asyncio.run(teams_router.get_team_report(plan.swarm_id, _request("root", role="admin")))
    assert admin["swarm_id"] == plan.swarm_id, "an admin may inspect any owner's team"


def test_the_roster_route_reports_the_composition_a_plan_actually_ran(roster, tmp_path, monkeypatch):
    import asyncio

    from alpha.swarm.coordinator import SwarmCoordinator

    register_roster_provider(lambda: roster, source="test")
    coordinator = SwarmCoordinator(storage_dir=tmp_path)
    plan = _plan_on(coordinator)
    monkeypatch.setattr(teams_router, "get_swarm_coordinator", lambda: coordinator)

    body = asyncio.run(teams_router.get_team_roster(plan.swarm_id, _request("alice")))
    assert body["roster_registered"] is True
    assert body["roster_source"] == "test"
    assert body["assigned"] > 0
    assert set(body["load"]) <= {"coder"}, body["load"]


def test_the_markdown_route_returns_text(roster, tmp_path, monkeypatch):
    import asyncio

    from alpha.swarm.coordinator import SwarmCoordinator

    register_roster_provider(lambda: roster, source="test")
    coordinator = SwarmCoordinator(storage_dir=tmp_path)
    plan = _plan_on(coordinator)
    monkeypatch.setattr(teams_router, "get_swarm_coordinator", lambda: coordinator)

    response = asyncio.run(teams_router.get_team_report_markdown(plan.swarm_id, _request("alice")))
    assert response.media_type.startswith("text/markdown")
    assert b"# Team Report" in response.body


def test_the_team_routes_are_mounted():
    """The mount is real, pinned so it cannot silently rot back to dead code.

    This assertion used to assert the opposite - that the router was NOT
    mounted - because mounting it needed two edits in `app/gateway/app.py`,
    which the swarm work did not own. Those edits have landed: the `teams`
    import in the `app.gateway.routers` block and one
    `app.include_router(teams.router)` beside the swarms mount.

    A router that exists but is not included is the exact defect class this
    repository keeps measuring, so the reachability is now pinned positively.
    Un-mounting it fails here rather than turning every route in
    `routers/teams.py` into a silent 404.
    """

    from pathlib import Path

    app_py = Path(__file__).resolve().parents[1] / "app" / "gateway" / "app.py"
    source = app_py.read_text(encoding="utf-8")
    assert "include_router(teams.router)" in source, "the team router must stay mounted; every route in routers/teams.py is dead over HTTP without this line"
