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


def test_the_team_routes_are_not_mounted_yet():
    """The honest state, pinned so it cannot rot into a false claim.

    Two lines in `app/gateway/app.py` (the import in the `app.gateway.routers`
    block and one `app.include_router(teams.router)` beside the swarms mount)
    are required. This work does not own that file. Until they land, every
    route here is dead over HTTP, and this test is the thing that says so.
    """

    from pathlib import Path

    app_py = Path(__file__).resolve().parents[1] / "app" / "gateway" / "app.py"
    source = app_py.read_text(encoding="utf-8")
    assert "include_router(teams.router)" not in source, "the team router is now mounted: update docs/TEAM_RUNTIME.md, which states it is not, and re-check the reachability claim in the team report before flipping this assertion"
