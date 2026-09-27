"""Phase 0: the war room must not claim more than it does.

Every test here exists because a docstring, a tool signature, or a command table
once asserted a capability the code did not have:

* ``groups/war_room.py`` claimed reasoning came from ``alpha.deliberation``
  while importing nothing from it;
* ``war_room_tool`` advertised ``action="status"`` in a closed ``Literal`` and
  had no branch for it, so a valid call returned "unknown action";
* ``war_room_tool.open`` called ``asyncio.run`` directly, which raises when the
  tool is invoked from the agent's own event loop;
* ``CHANNEL_COMMANDS`` and ``commands/catalog.py`` were mistaken for one
  dispatch table, and "fixing" that by copying the channel commands into the
  interactive catalog would have produced no-op placeholder commands.

Each test pins the *fix*, so the regression cannot come back quietly.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import json
from pathlib import Path

import pytest

# Both names are shadowed by re-exports: ``alpha.groups.war_room`` resolves to a
# StructuredTool and ``alpha.tools.builtins.war_room_tool`` resolves to the tool
# object, so ``from ... import war_room`` would hand back the wrong kind of thing.
# ``importlib`` by dotted name gets the module itself.
war_room_mod = importlib.import_module("alpha.groups.war_room")
war_room_tool_mod = importlib.import_module("alpha.tools.builtins.war_room_tool")

WAR_ROOM_SOURCE = Path(war_room_mod.__file__).resolve()
TOOL_FUNC = war_room_tool_mod.war_room_tool.func


# ---------------------------------------------------------------------------
# the docstring may not claim an import edge it does not have
# ---------------------------------------------------------------------------
def test_a_deliberation_claim_requires_a_real_deliberation_import():
    """If the module says deliberation supplies its reasoning, it must import it.

    The rule distinguishes a *credit* from a *disclaimer*. A docstring that says
    "this module does not import alpha.deliberation" is being honest about a gap
    and is fine. A docstring that credits the package while the module imports
    nothing from it is the defect this test was written for.
    """
    docstring = inspect.getdoc(war_room_mod) or ""

    # The exact claim this file was written to remove, pinned as a literal.
    assert "Reasoning comes from :mod:`alpha.deliberation`" not in docstring, "the module still credits alpha.deliberation as the source of its reasoning; either import the package or state that it is not used"

    tree = ast.parse(WAR_ROOM_SOURCE.read_text(encoding="utf-8"))
    imported = any(isinstance(node, ast.ImportFrom) and (node.module or "").startswith("alpha.deliberation") for node in ast.walk(tree)) or any(
        isinstance(node, ast.Import) and any(a.name.startswith("alpha.deliberation") for a in node.names) for node in ast.walk(tree)
    )

    mentioned = "alpha.deliberation" in docstring
    disclaims = "does not import" in docstring.lower()

    if mentioned and not disclaims:
        assert imported, "the war room docstring names alpha.deliberation without disclaiming it, but the module imports nothing from that package"
    elif not mentioned:
        # Silence is acceptable, but the reader should still learn where the
        # deliberation engines actually live.
        assert "deliberation" in docstring.lower()


def test_the_honest_gap_is_stated_rather_than_omitted():
    """A reader must be able to tell that the multi-model engines are NOT used."""
    docstring = (inspect.getdoc(war_room_mod) or "").lower()
    assert "deliberation" in docstring
    assert "does not import" in docstring or "does not" in docstring


# ---------------------------------------------------------------------------
# status is a real action, not a promise
# ---------------------------------------------------------------------------
def test_status_is_in_the_action_literal_and_has_a_dispatcher_branch():
    """The closed action set and the dispatcher must agree.

    A ``Literal`` entry with no branch is a tool that refuses a call the schema
    told the model was valid, which is worse than not advertising it at all.
    """
    source = inspect.getsource(TOOL_FUNC)
    assert 'action == "status"' in source, "action='status' is advertised but has no implementation"

    sig = inspect.signature(TOOL_FUNC)
    # The Literal annotation is a string under `from __future__ import annotations`.
    annotation = str(sig.parameters["action"].annotation)
    assert "status" in annotation


def test_status_refuses_cleanly_with_no_arguments():
    """A caller who gets the arguments wrong is told what is missing."""
    payload = json.loads(war_room_tool_mod.war_room_tool.invoke({"action": "status"}))
    assert payload["ok"] is True
    assert payload["action"] == "status"
    assert payload["count"] == 0
    assert payload["runs"] == []
    # Never the generic unknown-action refusal.
    assert "unknown action" not in json.dumps(payload)


def test_status_refuses_a_run_id_that_does_not_exist(monkeypatch, tmp_path):
    from alpha.commands import channel_ops

    monkeypatch.setattr(channel_ops, "runtime_root", lambda *a, **k: tmp_path)
    payload = json.loads(war_room_tool_mod.war_room_tool.invoke({"action": "status", "run_id": "wrun_doesnotexist"}))
    assert payload["ok"] is False
    assert "wrun_doesnotexist" in payload["error"]


def test_status_reads_a_persisted_run_with_its_transcript_tail(monkeypatch, tmp_path):
    """A finished run is discoverable, with its recorded verdict and transcript."""
    import time

    from alpha.bots.events import OrgEventStore
    from alpha.commands import channel_ops
    from alpha.groups.war_room import WarRoom, build_default_config

    async def alice(ctx):
        return f"alice: ship it ({ctx.stage.name})"

    async def bob(ctx):
        return f"bob: agree ({ctx.stage.name})"

    config = build_default_config(
        "phase zero status",
        ["alice", "bob"],
        stage_timeout_seconds=1.0,
        stage_grace_seconds=0.3,
    )
    room = WarRoom(
        config,
        participants={"alice": alice, "bob": bob},
        room="status-room",
        root=tmp_path,
        ledger_store=OrgEventStore(tmp_path / "events.jsonl"),
        clock=time.monotonic,
    )
    run = asyncio.run(room.execute())
    assert run.status == "succeeded"

    monkeypatch.setattr(channel_ops, "runtime_root", lambda *a, **k: tmp_path)

    listing = json.loads(war_room_tool_mod.war_room_tool.invoke({"action": "status", "room": "status-room"}))
    assert listing["ok"] is True
    assert listing["count"] == 1
    assert listing["runs"][0]["run_id"] == run.run_id
    assert listing["runs"][0]["status"] == "succeeded"
    assert listing["kill_switch"] is not None

    detail = json.loads(war_room_tool_mod.war_room_tool.invoke({"action": "status", "room": "status-room", "run_id": run.run_id, "transcript_tail": 5}))
    assert detail["ok"] is True
    assert detail["run"]["run_id"] == run.run_id
    assert detail["run"]["synthesis"]
    tail = detail["run"]["transcript_tail"]
    assert tail, "a persisted run must expose at least one transcript message"
    assert all("seq" in m for m in tail)


def test_a_corrupt_run_record_is_reported_not_hidden(tmp_path):
    """A truncated run.json is visible to an operator, not silently absent."""
    broken = tmp_path / "room-a" / "wrun_broken"
    broken.mkdir(parents=True)
    (broken / "run.json").write_text('{"run_id": "wrun_broken", "status": "suc', encoding="utf-8")

    records = war_room_mod.list_persisted_runs(tmp_path)
    assert len(records) == 1
    assert records[0]["status"] == "unreadable"
    assert records[0]["error"]
    assert records[0]["run_id"] == "wrun_broken"

    single = war_room_mod.load_run_record(tmp_path, "room-a", "wrun_broken")
    assert single is not None
    assert single["status"] == "unreadable"


def test_a_partial_trailing_transcript_line_does_not_break_the_tail(tmp_path):
    """A crash mid-append leaves a partial line; status must still work."""
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        '{"seq": 1, "author": "alice", "body": "one"}\n{"seq": 2, "author": "bob", "body": "tw',
        encoding="utf-8",
    )
    tail = war_room_mod.read_transcript_tail(path, limit=10)
    assert [m["seq"] for m in tail] == [1]
    assert war_room_mod.read_transcript_tail(tmp_path / "nope.jsonl") == []


def test_list_persisted_runs_handles_a_missing_runtime_root(tmp_path):
    assert war_room_mod.list_persisted_runs(tmp_path / "does-not-exist") == []
    assert war_room_mod.load_run_record(tmp_path, "r", "wrun_x") is None


# ---------------------------------------------------------------------------
# open must survive being called from the agent's own event loop
# ---------------------------------------------------------------------------
def test_run_off_loop_executes_when_a_loop_is_already_running():
    """``asyncio.run`` raises here; the daemon-thread fallback must not."""

    async def scenario():
        async def work():
            await asyncio.sleep(0)
            return "deliberated"

        return war_room_tool_mod._run_off_loop(work)

    assert asyncio.run(scenario()) == "deliberated"


def test_run_off_loop_executes_when_no_loop_is_running():
    async def work():
        return "plain"

    assert war_room_tool_mod._run_off_loop(work) == "plain"


def test_run_off_loop_reraises_the_worker_failure_on_the_calling_thread():
    """A failed run must reach the caller's handler, not vanish in the thread."""

    async def boom():
        raise ValueError("stage exploded")

    with pytest.raises(ValueError, match="stage exploded"):
        war_room_tool_mod._run_off_loop(boom)


def test_open_does_not_call_asyncio_run_directly():
    """Guard the regression at the source, not just at the helper.

    Both ``asyncio.run`` calls must live inside ``_run_off_loop`` - the one
    place that knows how to get onto a thread when a loop is already running.
    """
    tool_src = inspect.getsource(TOOL_FUNC)
    helper_src = inspect.getsource(war_room_tool_mod._run_off_loop)

    assert "_run_off_loop(war_room.execute)" in tool_src
    assert "asyncio.run" not in tool_src, "the tool body must not reach for asyncio.run directly"
    # One call for the no-loop path, one for the worker thread.
    assert helper_src.count("asyncio.run(") == 2


# ---------------------------------------------------------------------------
# the two command planes are separate on purpose
# ---------------------------------------------------------------------------
def test_channel_commands_are_never_interactive_placeholders():
    """``CHANNEL_COMMANDS`` and ``catalog.py`` are DIFFERENT dispatch planes.

    ``channel_ops.CHANNEL_COMMANDS`` feeds ``ChannelCommandRouter`` on the
    bot-to-bot channel plane. ``commands/catalog.py`` feeds the interactive
    ``command_registry`` behind ``/help`` and ``/commands``. A war-room command
    is therefore legitimately absent from the catalog.

    This test exists because the obvious "fix" - copying the channel commands
    into the catalog - is a regression, not a repair: the catalog rows would have
    no interactive handler, so every one of them would take the registry's
    documented placeholder path and tell a user the command exists while doing
    nothing. ``test_discovery_plane_parity.py`` permits placeholder rows to
    exist; it does not make inventing new ones an improvement.

    The rule is about *placeholders*, not about overlap. A name may legitimately
    appear in both tables - ``/status`` does, and it is a pre-existing
    interactive command the channel plane also reuses - but a war-room command
    must never be a handler-less catalog row.
    """
    from alpha.commands import catalog, channel_ops
    from alpha.commands.registry import command_registry

    #: The war room's own channel commands. ``status`` is deliberately excluded:
    #: it is a shared pre-existing command, and asserting on it here would be
    #: testing a different subsystem.
    war_room_commands = ("war-room", "hire", "clone", "rescope", "archive", "acquire")
    catalog_names = {row[0] for row in catalog.get_default_catalog_entries()}
    for name in war_room_commands:
        assert name in channel_ops.CHANNEL_COMMANDS, f"{name} left the channel command table"
        if "/" + name not in catalog_names:
            continue
        assert command_registry._handlers.get("/" + name) is not None, "/" + name + " is in the interactive catalog and in CHANNEL_COMMANDS, but has no interactive handler; it would advertise a capability that does nothing"


def test_the_war_room_really_is_in_the_channel_command_table():
    """The flip side: the channel plane must actually carry the war room."""
    from alpha.commands import channel_ops

    assert "war-room" in channel_ops.CHANNEL_COMMANDS
    # Self-scoped commands must not carry the cross-bot target-authority flag.
    assert channel_ops.CHANNEL_COMMANDS["war-room"][1] is False
    # Lifecycle commands that may target another bot must carry it.
    for name in ("hire", "clone", "rescope", "archive", "acquire"):
        assert channel_ops.CHANNEL_COMMANDS[name][1] is True, name
