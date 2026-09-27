"""Roster activity projection: a last-message preview plus an unread count.

Three properties earn their own tests, and none of them is "the preview shows
the preview":

* **The projection is opt-in.** ``GET /api/bots`` is the roster read the UI
  polls, and the activity read costs one inbox file per Bot. A default-on
  projection would quietly turn a cheap list into an N-file fan-out, so the
  fields are absent unless asked for.
* **It is honest under failure.** A corrupt or unreadable inbox degrades that
  one row to "no activity" instead of taking the whole roster down with it.
* **It is honest under content.** A credential-shaped body is *withheld*, not
  shortened — truncating a secret still ships the first half of it, and the
  roster is a wider blast radius than the DM inbox the user opened on purpose.
"""

from __future__ import annotations

import pytest

import alpha.bots.registry as bot_reg
from alpha.bots.inbox import PREVIEW_CHARS, get_bot_inbox, roster_activity
from alpha.tools.builtins.bot_roster_tool import bot_roster_tool

EMPTY = {
    "unread_count": 0,
    "last_message_preview": None,
    "last_message_at": None,
    "last_message_sender": None,
    "last_message_withheld": False,
}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None
    yield
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None


def _seed_bot(name: str = "coder") -> str:
    bot_roster_tool.invoke({"action": "create", "name": name, "role": "Backend Engineer"})
    return name


def _corrupt_inbox(name: str) -> None:
    from alpha.bots import inbox as inbox_mod

    path = inbox_mod._bot_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")


# ---------------------------------------------------------------------------
# the projection
# ---------------------------------------------------------------------------


def test_a_silent_bot_reports_no_activity_at_all():
    _seed_bot()
    assert roster_activity("coder") == EMPTY


def test_the_newest_message_wins_and_unread_is_counted():
    _seed_bot()
    box = get_bot_inbox("coder")
    box.deliver("alpha", "first")
    box.deliver("alpha", "second")
    out = roster_activity("coder")
    assert out["last_message_preview"] == "second"
    assert out["last_message_sender"] == "alpha"
    assert out["unread_count"] == 2
    assert isinstance(out["last_message_at"], float)


def test_an_acknowledged_message_still_previews_but_stops_counting_as_unread():
    _seed_bot()
    box = get_bot_inbox("coder")
    first = box.deliver("alpha", "first")
    box.deliver("alpha", "second")
    box.ack(first.delivery_id)
    out = roster_activity("coder")
    assert out["last_message_preview"] == "second"
    assert out["unread_count"] == 1, "an ack retires the message from the unread badge"


def test_a_multi_line_body_is_collapsed_and_truncated():
    _seed_bot()
    get_bot_inbox("coder").deliver("alpha", "line one\n\n    line two " + "x" * 400)
    preview = roster_activity("coder")["last_message_preview"]
    assert "\n" not in preview
    assert "  " not in preview
    assert preview.endswith("…"), "a shortened preview must say it was shortened"
    assert len(preview) <= PREVIEW_CHARS + 1


def test_a_short_body_is_not_padded_with_an_ellipsis():
    _seed_bot()
    get_bot_inbox("coder").deliver("alpha", "ship it")
    assert roster_activity("coder")["last_message_preview"] == "ship it"


def test_a_credential_shaped_body_is_withheld_whole():
    """Truncating a secret still ships the first half of it, so withhold it."""
    _seed_bot()
    secret = "deployment key sk-abcdefghijklmnopqrstuvwxyz0123456789"
    get_bot_inbox("coder").deliver("alpha", secret)
    out = roster_activity("coder")
    assert out["last_message_preview"] is None
    assert out["last_message_withheld"] is True
    assert "sk-abcdefghij" not in repr(out), "no part of the body may reach the roster row"
    # The row still says *something happened*, so the UI can explain the gap.
    assert out["last_message_at"] is not None
    assert out["unread_count"] == 1


def test_a_corrupt_inbox_degrades_one_row_instead_of_raising():
    _seed_bot()
    _corrupt_inbox("coder")
    assert roster_activity("coder") == EMPTY


def test_an_unknown_bot_reports_no_activity_instead_of_raising():
    assert roster_activity("nobody-here") == EMPTY


# ---------------------------------------------------------------------------
# the route: opt-in, and never fatal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_roster_carries_no_activity_unless_it_is_asked_for():
    from app.gateway.routers import bots

    _seed_bot()
    get_bot_inbox("coder").deliver("alpha", "hello roster")

    plain = await bots.list_bots()
    row = next(b for b in plain["bots"] if b["name"] == "coder")
    assert "last_message_preview" not in row, "the default roster read must stay a registry read"

    with_activity = await bots.list_bots(activity=True)
    row = next(b for b in with_activity["bots"] if b["name"] == "coder")
    assert row["last_message_preview"] == "hello roster"
    assert row["unread_count"] == 1
    assert row["last_message_withheld"] is False


@pytest.mark.asyncio
async def test_one_broken_inbox_does_not_take_the_roster_down():
    from app.gateway.routers import bots

    _seed_bot("coder")
    _seed_bot("researcher")
    get_bot_inbox("researcher").deliver("alpha", "still fine")
    _corrupt_inbox("coder")

    res = await bots.list_bots(activity=True)
    rows = {b["name"]: b for b in res["bots"]}
    assert rows["coder"]["unread_count"] == 0
    assert rows["coder"]["last_message_preview"] is None
    assert rows["researcher"]["last_message_preview"] == "still fine"
    # The default roster ships its own fleet, so pin the shape, not a headcount.
    assert res["count"] == len(rows) >= 2
