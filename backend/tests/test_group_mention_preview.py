"""Group mention preview (2.6): the composer's pre-send answer, server-side.

Pinned here:

1. ``POST /api/groups/mentions/preview`` runs the ONE mention grammar against
   the room's roster AND reports the routing decision for the same draft —
   ``mode`` / ``moderator`` / ``next_speakers`` — computed by the same
   orchestrator method the landed message goes through, so a preview that
   disagrees with the landing message is a bug, not a projection.
2. A resolved mention routes: an ``@coder`` draft in a mention-mode room
   answers ``next_speakers == ["coder"]``; an unaddressed human message
   answers the moderator (or the first member when the room has none).
3. An unresolved token is reported with the server's own reason and never
   enters ``resolved_handles`` or ``next_speakers``.
"""

from __future__ import annotations

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from alpha.groups.orchestration import GroupOrchestrator
from alpha.groups.room import GroupRoom
from app.gateway.routers import groups as groups_router


class _StubGroupService:
    """The four methods the preview route calls, over one in-memory room.

    Resolution, the role index and the speaker decision are the REAL
    implementations (the static orchestrator methods plus the service's
    ``build_role_index``, which only reads the room) — only the service's own
    persistence is stubbed, so the routing math under test is production code.
    """

    def __init__(self, room: GroupRoom) -> None:
        self._room = room
        self.orchestrator = GroupOrchestrator()

    def resolve_mentions(self, room_name, content, *, sender=None):
        return GroupOrchestrator.resolve_mentions(
            content,
            self._room.members,
            roles=self.build_role_index(self._room),
            sender=sender,
        )

    def get_room(self, name):
        return self._room

    def build_role_index(self, room):
        from alpha.groups.service import GroupChatService

        return GroupChatService.build_role_index(self, room)


def _room(**overrides):
    fields = {
        "room_id": "r1",
        "name": "alpha",
        "members": ["coder", "reviewer"],
        "mode": "mention",
        "moderator": "coder",
    }
    fields.update(overrides)
    return GroupRoom(**fields)


@pytest.fixture()
def client(monkeypatch):
    room = _room()
    monkeypatch.setattr(groups_router, "_service", lambda: _StubGroupService(room))
    app = make_authed_test_app()
    app.include_router(groups_router.router)
    return TestClient(app)


def _preview(client, content, sender="user", room_name="alpha"):
    return client.post("/api/groups/mentions/preview", json={"room_name": room_name, "content": content, "sender": sender})


def test_the_response_carries_the_routing_decision_beside_the_resolution(client):
    body = _preview(client, "@coder please review the diff").json()

    assert body["mode"] == "mention"
    assert body["moderator"] == "coder"
    assert body["resolved_handles"] == ["coder"]
    assert body["unresolved"] == []
    assert body["ok"] is True
    assert body["next_speakers"] == ["coder"], "an addressed bot is the next speaker in mention mode"


def test_an_unaddressed_human_message_invites_the_moderator(client):
    body = _preview(client, "hello team, status?").json()

    assert body["resolved_handles"] == []
    assert body["unresolved"] == []
    assert body["next_speakers"] == ["coder"], "the moderator opens an unaddressed human message"


def test_an_unresolved_token_is_reported_and_never_routes(client):
    body = _preview(client, "@ghost please help").json()

    assert body["resolved_handles"] == [], "an unknown handle resolves to nothing"
    assert body["ok"] is False
    assert len(body["unresolved"]) == 1
    assert body["unresolved"][0]["raw"] == "@ghost"
    assert "unknown handle" in body["unresolved"][0]["reason"]
    assert body["next_speakers"] == ["coder"], "the draft still invites the moderator — the dead tag changes nothing"


def test_a_mixed_draft_routes_only_on_the_resolved_half(client):
    body = _preview(client, "@coder and @ghost, together").json()

    assert body["resolved_handles"] == ["coder"]
    assert body["next_speakers"] == ["coder"]


def test_the_moderated_mode_decision_comes_from_the_same_orchestrator(client, monkeypatch):
    room = _room(mode="moderated", moderator="reviewer")
    monkeypatch.setattr(groups_router, "_service", lambda: _StubGroupService(room))
    body = _preview(client, "@coder").json()

    assert body["mode"] == "moderated"
    assert body["next_speakers"] == ["reviewer"], "a non-moderator speaker returns to the moderator"


def test_the_round_robin_mode_decision_comes_from_the_same_orchestrator(client, monkeypatch):
    room = _room(mode="round_robin", moderator=None)
    monkeypatch.setattr(groups_router, "_service", lambda: _StubGroupService(room))
    body = _preview(client, "@reviewer").json()

    assert body["mode"] == "round_robin"
    # The operator is not a room member, so the turn opens at the first
    # member — the mode's own rule, not a mention's.
    assert body["next_speakers"] == ["coder"]
