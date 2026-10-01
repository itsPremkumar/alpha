"""Regression coverage for `GET /api/bots/{name}/projects`.

The route exists because nothing could answer "which projects is this bot on?".
`GET /projects` returns rows without their crew and `GET /projects/{id}/crew`
needs an id you do not have yet, so the detail page had no way to show project
membership without an N+1 fan-out or a guess.

These tests pin the parts that are easy to get silently wrong:

  - membership is matched on `MemberBrief.bot_name` lowercased, because `attach()`
    stores the lowercased name. Reading `.name` (which does not exist on
    `MemberBrief`) makes every project look empty, so the whole feature fails
    open as "this bot is on nothing" rather than erroring.
  - an unreadable crew is reported in `unreadable` with its reason, never
    silently dropped, so "not on any project" stays distinguishable from
    "we could not tell".
  - the payload states that memory is project-scoped, so a caller hunting for
    per-bot memory is told where it actually lives instead of concluding none
    exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

# Prime `alpha.groups.room` before touching `app.gateway.routers`.
#
# `routers/__init__.py` eagerly imports every router, and `routers/groups.py`
# reads `VALID_INTENTS` at module scope from `alpha.groups.room`. Whichever
# module is imported first in a pytest session decides whether that cycle
# resolves: this file sorts before `test_bots_*`, so it loads the router package
# cold and hits `NameError: name 'VALID_INTENTS' is not defined` unless the
# harness module is already present. Importing it here makes the suite
# order-independent instead of relying on a sibling test running first.
import alpha.groups.room  # noqa: F401
from app.gateway.routers.bots import bot_projects


@dataclass
class MemberBrief:
    """Mirror of the harness shape the route reads.

    Deliberately declares `bot_name` / `role_in_project` only: if the route ever
    reaches for `.name` or `.role`, these tests fail with an AttributeError
    instead of quietly returning empty strings in production.
    """

    bot_name: str
    role_in_project: str = "worker"
    status: str = "active"


@dataclass
class CrewView:
    project_id: str
    members: list[MemberBrief]
    room: Any = None


class FakeRepo:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def list(self, status: Any = None) -> list[dict[str, Any]]:
        return self._rows


class FakeCrew:
    def __init__(self, views: dict[str, CrewView | Exception]) -> None:
        self._views = views

    def crew_view(self, project_id: str) -> CrewView:
        found = self._views.get(project_id)
        if isinstance(found, Exception):
            raise found
        if found is None:
            raise ValueError(f"no crew for {project_id}")
        return found


def _patch(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]], views: dict[str, Any]) -> None:
    import alpha.projects.crew as crew_mod
    import app.gateway.deps as deps

    monkeypatch.setattr(deps, "get_project_repo", lambda request: FakeRepo(rows))
    monkeypatch.setattr(crew_mod, "get_crew_service", lambda *a, **k: FakeCrew(views))


async def _call(monkeypatch: pytest.MonkeyPatch, name: str) -> dict[str, Any]:
    """Invoke the handler with auth already satisfied.

    `require_permission` is a decorator resolved at import time, so calling the
    undecorated coroutine here is what the tests exercise; the permission wiring
    itself is covered by the authz suite.
    """
    return await bot_projects.__wrapped__(name, None)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_returns_only_projects_the_bot_belongs_to(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"id": "p1", "name": "Alpha Core", "status": "active", "updated_at": "2026-01-01"},
        {"id": "p2", "name": "Other Work", "status": "active", "updated_at": "2026-01-02"},
    ]
    views = {
        "p1": CrewView("p1", [MemberBrief("architect", "lead"), MemberBrief("coder")]),
        "p2": CrewView("p2", [MemberBrief("reviewer")]),
    }
    _patch(monkeypatch, rows, views)

    payload = await _call(monkeypatch, "architect")

    assert [p["id"] for p in payload["projects"]] == ["p1"]
    assert payload["projects"][0]["role"] == "lead", "role_in_project must surface as role"
    assert payload["bot"] == "architect"


@pytest.mark.asyncio
async def test_membership_is_case_insensitive_on_the_stored_lower_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`attach()` lowercases, so a mixed-case request must still match."""
    rows = [{"id": "p1", "name": "Core", "status": "active"}]
    views = {"p1": CrewView("p1", [MemberBrief("architect", "lead")])}
    _patch(monkeypatch, rows, views)

    payload = await _call(monkeypatch, "Architect")

    assert len(payload["projects"]) == 1


@pytest.mark.asyncio
async def test_unreadable_crew_is_reported_not_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"id": "p1", "name": "Core", "status": "active"}, {"id": "p2", "name": "Broken", "status": "active"}]
    views = {"p1": CrewView("p1", [MemberBrief("architect")]), "p2": RuntimeError("room store unavailable")}
    _patch(monkeypatch, rows, views)

    payload = await _call(monkeypatch, "architect")

    assert [p["id"] for p in payload["projects"]] == ["p1"]
    assert [u["id"] for u in payload["unreadable"]] == ["p2"]
    assert "room store unavailable" in payload["unreadable"][0]["reason"], "the reason must survive so the caller can tell 'could not read' from 'not a member'"


@pytest.mark.asyncio
async def test_no_membership_is_an_empty_list_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"id": "p1", "name": "Core", "status": "active"}]
    views = {"p1": CrewView("p1", [MemberBrief("coder")])}
    _patch(monkeypatch, rows, views)

    payload = await _call(monkeypatch, "architect")

    assert payload["projects"] == []
    assert payload["unreadable"] == []


@pytest.mark.asyncio
async def test_payload_states_that_memory_is_project_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alpha has no per-bot memory store.

    The page needs to know that before it renders an empty Memory panel, so the
    payload says so explicitly instead of letting a reader conclude none exists.
    """
    _patch(monkeypatch, [], {})

    payload = await _call(monkeypatch, "architect")

    assert payload["memory_scoped"] == "project"
    assert "/memory" in payload["memory_note"]


@pytest.mark.asyncio
async def test_invalid_bot_name_is_rejected_by_the_shared_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, [], {})

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await _call(monkeypatch, "not a valid name!")

    assert excinfo.value.status_code == 422
