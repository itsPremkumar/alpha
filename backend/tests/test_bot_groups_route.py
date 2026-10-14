"""Regression coverage for `GET /api/bots/{name}/groups`.

The route exists because nothing could answer "which rooms is this bot in, and
why". `GET /api/groups/rooms` lists rooms without rosters and
`GET /api/groups/rooms/{name}` needs a name you do not have yet, so the detail
page would have had to fan out over every room from the client.

These tests pin the parts that are easy to get silently wrong:

  - membership is resolved **live** out of `resolved_roster()`, never read off
    `GroupRoom.members` (crew-owned), so an inherited member is reported as
    inherited rather than as somebody hand-added to this room.
  - `origins` lists *every* bucket the bot appears in, verbatim. A bot that is
    both direct and excluded must not collapse to one flattering word, and
    `effective` must then be `False`.
  - `direct_count` and `effective_count` travel together, because a header
    claiming three members over six visible bots is a fabricated count.
  - a room whose roster cannot be read goes to `unreadable` with its reason and
    the room list itself failing is 503 - neither may become an empty 200.
  - a root room renders as `[own name]`: `breadcrumbs()` deliberately excludes
    the room itself, so appending it is what stops an empty path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

# Prime `alpha.groups.room` before touching `app.gateway.routers`.
#
# `routers/__init__.py` eagerly imports every router, and `routers/groups.py`
# reads `VALID_INTENTS` at module scope from `alpha.groups.room`. Whichever
# module is imported first in a pytest session decides whether that cycle
# resolves: this file sorts before `test_bot_projects_route.py`, so it loads the
# router package cold and hits `NameError: name 'VALID_INTENTS' is not defined`
# unless the harness module is already present. Importing it here makes the
# suite order-independent instead of relying on a sibling test running first.
import alpha.groups.room  # noqa: F401
from app.gateway.routers.bots import bot_groups


@dataclass
class FakeRoom:
    """Only the `GroupRoom` fields the route reads."""

    room_id: str
    name: str
    mode: str = "mention"
    lifecycle: str = "active"
    project_id: str | None = None
    parent_ids: list[str] = field(default_factory=list)


@dataclass
class FakeRoster:
    """Mirror of `ResolvedRoster`'s five buckets."""

    direct: list[str] = field(default_factory=list)
    rule_matched: list[str] = field(default_factory=list)
    inherited: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)

    @property
    def effective(self) -> list[str]:
        dead = set(self.excluded) | set(self.expired)
        live = set(self.direct) | set(self.rule_matched) | set(self.inherited)
        return sorted(live - dead)

    @property
    def effective_count(self) -> int:
        return len(self.effective)

    @property
    def direct_count(self) -> int:
        return len(self.direct)


class FakeService:
    def __init__(
        self,
        rooms: list[FakeRoom],
        rosters: dict[str, FakeRoster | Exception],
        crumbs: dict[str, list[dict[str, Any]]] | None = None,
        list_error: Exception | None = None,
    ) -> None:
        self._rooms = rooms
        self._rosters = rosters
        self._crumbs = crumbs or {}
        self._list_error = list_error

    def list_rooms(self) -> list[FakeRoom]:
        if self._list_error is not None:
            raise self._list_error
        return self._rooms

    def resolved_roster(self, name: str) -> FakeRoster:
        found = self._rosters.get(name)
        if isinstance(found, Exception):
            raise found
        if found is None:
            raise ValueError(f"no such room: {name}")
        return found

    def breadcrumbs(self, name: str) -> list[dict[str, Any]]:
        return self._crumbs.get(name, [])


def _patch(monkeypatch: pytest.MonkeyPatch, service: FakeService) -> None:
    import alpha.groups.service as svc_mod

    monkeypatch.setattr(svc_mod, "get_group_chat_service", lambda *a, **k: service)


async def _call(name: str) -> dict[str, Any]:
    """Invoke the handler with auth already satisfied.

    `require_permission` wraps the coroutine at import time, so the tests call
    the undecorated body; the permission wiring itself is covered by the authz
    suite. The `request` argument is required by the decorator's signature
    binding and unused by the body.
    """
    return await bot_groups.__wrapped__(name, None)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_returns_only_rooms_the_bot_appears_in(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        FakeService(
            rooms=[FakeRoom("r1", "standup"), FakeRoom("r2", "arch-review")],
            rosters={
                "standup": FakeRoster(direct=["coder", "architect"]),
                "arch-review": FakeRoster(direct=["reviewer"]),
            },
        ),
    )

    payload = await _call("architect")

    assert [r["name"] for r in payload["rooms"]] == ["standup"]
    assert payload["bot"] == "architect"
    assert payload["rooms_checked"] == 2


@pytest.mark.asyncio
async def test_inherited_member_is_reported_as_inherited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inheritance is a projection; reporting it as `direct` is the whole bug."""
    _patch(
        monkeypatch,
        FakeService(
            rooms=[FakeRoom("child", "child-room", parent_ids=["root"])],
            rosters={"child-room": FakeRoster(inherited=["architect"])},
            crumbs={"child-room": [{"room_id": "root", "name": "root-room", "path": "/root-room", "depth": 0}]},
        ),
    )

    payload = await _call("architect")

    assert payload["rooms"][0]["origins"] == ["inherited"]
    assert payload["rooms"][0]["direct_count"] == 0
    assert payload["rooms"][0]["effective_count"] == 1
    assert payload["rooms"][0]["effective"] is True


@pytest.mark.asyncio
async def test_every_bucket_the_bot_appears_in_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A direct member who is also excluded must read as both, with `effective` false."""
    _patch(
        monkeypatch,
        FakeService(
            rooms=[FakeRoom("r1", "war-room")],
            rosters={"war-room": FakeRoster(direct=["architect"], excluded=["architect"])},
        ),
    )

    payload = await _call("architect")

    assert payload["rooms"][0]["origins"] == ["direct", "excluded"]
    assert payload["rooms"][0]["effective"] is False, "an excluded member cannot be reported as able to take part"


@pytest.mark.asyncio
async def test_root_room_path_includes_its_own_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """`breadcrumbs()` excludes the room itself, so a root room has no ancestors."""
    _patch(
        monkeypatch,
        FakeService(
            rooms=[FakeRoom("r1", "root-room")],
            rosters={"root-room": FakeRoster(direct=["architect"])},
            crumbs={"root-room": []},
        ),
    )

    payload = await _call("architect")

    assert payload["rooms"][0]["path"] == ["root-room"]


@pytest.mark.asyncio
async def test_unreadable_roster_is_reported_not_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        FakeService(
            rooms=[FakeRoom("r1", "good"), FakeRoom("r2", "broken")],
            rosters={
                "good": FakeRoster(direct=["architect"]),
                "broken": RuntimeError("roster store unavailable"),
            },
        ),
    )

    payload = await _call("architect")

    assert [r["name"] for r in payload["rooms"]] == ["good"]
    assert [u["name"] for u in payload["unreadable"]] == ["broken"]
    assert "roster store unavailable" in payload["unreadable"][0]["reason"], "the reason must survive so the caller can tell 'could not read' from 'not a member'"


@pytest.mark.asyncio
async def test_unreadable_room_list_is_503_not_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """A corrupt store and an installation with no rooms drive opposite decisions."""
    _patch(monkeypatch, FakeService(rooms=[], rosters={}, list_error=RuntimeError("groups store corrupt")))

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await _call("architect")

    assert excinfo.value.status_code == 503
    assert "groups store corrupt" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_no_membership_is_an_empty_list_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, FakeService(rooms=[FakeRoom("r1", "somewhere")], rosters={"somewhere": FakeRoster(direct=["coder"])}))

    payload = await _call("architect")

    assert payload["rooms"] == []
    assert payload["unreadable"] == []
    assert payload["rooms_checked"] == 1


@pytest.mark.asyncio
async def test_invalid_bot_name_is_rejected_by_the_shared_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    _patch(monkeypatch, FakeService(rooms=[], rosters={}))

    with pytest.raises(HTTPException) as excinfo:
        await _call("not a valid name!")

    assert excinfo.value.status_code == 422


def test_permission_layer_is_applied_and_actually_runs() -> None:
    """`threads: read` guards this route - and `request` is what makes it run.

    `require_permission` returns early, *without* checking anything, when the
    handler declares no `request` parameter (`authz.py`: it has nothing to
    authenticate against, so it just calls through). Dropping `request` from the
    signature would therefore remove the permission check silently rather than
    failing loudly, which is exactly the shape this test exists to catch.
    """
    import inspect

    from app.gateway.routers.bots import bot_groups as decorated

    assert getattr(decorated, "__wrapped__", None) is not None, "the permission layer must actually be applied"
    assert "request" in inspect.signature(decorated.__wrapped__).parameters, "without a `request` parameter the decorator calls through unchecked"
