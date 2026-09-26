"""Reachability: the war room is wired into a real runtime path, not only tests.

The rule this file exists to enforce: "No feature may be wired only by a test."
Each test here proves a production import edge, not a behaviour, so a refactor
that quietly unplugs the war room from the agent fails here rather than being
discovered by a user.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import alpha.bots  # noqa: F401  (resolves a pre-existing circular import)
from alpha.commands import channel_ops
from alpha.groups import acquisition, lifecycle_ops, supervisor, war_room

#: The ``alpha`` package root. ``war_room.py`` lives at
#: ``alpha/groups/war_room.py``, so parents[0] is ``alpha/groups`` and parents[1]
#: is ``alpha`` itself. Using parents[4] would point at ``backend/``, which
#: includes ``backend/tests`` and would make the "production code only" scans
#: below match this very file.
PACKAGE_ROOT = Path(war_room.__file__).resolve().parents[1]

#: Guard: if this ever points somewhere containing a ``tests`` directory, the
#: production-only scans in this file are scanning test code and will lie.
assert not (PACKAGE_ROOT / "tests").exists(), (
    f"PACKAGE_ROOT {PACKAGE_ROOT} contains a tests/ directory; the production-only "
    f"scans in this file would be meaningless"
)


def _imported_names(path: Path) -> set[str]:
    """Every name a module imports, plus the modules it references by attribute."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def test_the_war_room_tool_is_registered_in_the_builtin_toolset():
    """The tool is in BUILTIN_TOOLS, so every agent can reach the war room.

    Registration is asserted at BOTH ends: the name is exported from
    ``tools.builtins``, and the tool object is a member of the ``BUILTIN_TOOLS``
    list that ``get_available_tools`` copies. Exporting without adding to the
    list is the classic half-wiring, so both are checked.
    """
    import alpha.tools.builtins as builtins_mod
    import alpha.tools.tools as tools_mod
    from alpha.tools.builtins import __all__ as builtins_all

    assert "war_room_tool" in builtins_all, "the tool is not exported from tools.builtins"
    assert getattr(builtins_mod.war_room_tool, "name", None) == "war_room"

    names = {getattr(t, "name", None) for t in tools_mod.BUILTIN_TOOLS}
    assert "war_room" in names, "the tool is exported but absent from BUILTIN_TOOLS"

    # And BUILTIN_TOOLS is what the real assembly path uses.
    source = inspect.getsource(tools_mod.get_available_tools)
    assert "BUILTIN_TOOLS" in source, "get_available_tools does not read BUILTIN_TOOLS"


def test_the_war_room_tool_every_advertised_action_refuses_cleanly():
    """Every advertised action returns a refusal rather than raising.

    Each action is invoked with no arguments, so each takes its own
    missing-argument refusal path. That proves two things at once: the action is
    reachable, and a caller who gets the arguments wrong is told what is missing
    instead of hitting a traceback inside the agent loop.
    """
    from alpha.tools.builtins import war_room_tool

    advertised = (
        "open",
        "hire",
        "clone",
        "rescope",
        "archive",
        "unarchive",
        "acquire",
        "revalidate",
    )
    for action in advertised:
        payload = war_room_tool.invoke({"action": action})
        assert '"ok": false' in payload, f"{action} claimed success with no arguments: {payload}"
        assert "error" in payload, f"{action} returned no error field: {payload}"

    # Spot-check the refusals are specific, not a generic failure.
    opened = war_room_tool.invoke({"action": "open"})
    assert "topic" in opened and "participants" in opened
    assert "acquire" in war_room_tool.invoke({"action": "acquire"})


def test_an_action_outside_the_advertised_set_is_refused_at_the_schema():
    """The action literal is a closed set, so a typo is a validation error.

    Pydantic rejects it before the body runs. That is the right place for it: an
    unknown action never reaches the dispatcher, so it cannot half-execute.
    """
    import pydantic

    from alpha.tools.builtins import war_room_tool

    with pytest.raises(pydantic.ValidationError):
        war_room_tool.invoke({"action": "definitely_not_an_action"})


def test_the_command_router_table_matches_the_documented_commands():
    """channel_ops is the runtime entry: its table must cover the slash commands."""
    assert "war-room" in channel_ops.CHANNEL_COMMANDS
    assert "hire" in channel_ops.CHANNEL_COMMANDS
    assert "clone" in channel_ops.CHANNEL_COMMANDS
    assert "archive" in channel_ops.CHANNEL_COMMANDS
    # Lifecycle commands are the ones that may target another bot.
    for name in ("hire", "clone", "rescope", "archive", "acquire"):
        assert channel_ops.CHANNEL_COMMANDS[name][1] is True, name
    # Self-scoped ones are not.
    for name in ("war-room", "status"):
        assert channel_ops.CHANNEL_COMMANDS[name][1] is False, name


def test_open_channel_assembles_a_real_context(tmp_path):
    """The runtime entry really builds a transcript, a ledger and a router."""
    ctx = channel_ops.open_channel("general", root=tmp_path, roster=["alice", "bob"])
    assert ctx.room == "general"
    assert ctx.transcript.path.exists() or ctx.transcript.path.parent.exists()
    assert ctx.event_store is ctx.ledger_store
    assert ctx.commands is not None
    # A message really lands, with a monotonic sequence number.
    first = ctx.transcript.append("alice", "hello")
    second = ctx.transcript.append("bob", "hi")
    assert (first.seq, second.seq) == (1, 2)


def test_the_debouncer_is_reachable_from_the_war_room_runtime_path():
    """alpha.channels.debounce was reported unreachable. It now has an edge.

    The room intake is constructed by the war room for every stage, which is a
    production import edge from groups/war_room.py to the debouncer.
    """
    from alpha.channels.debounce.debouncer import InboundDebouncer
    from alpha.channels.timing import RoomIntake

    # The war room module really constructs a RoomIntake.
    war_room_source = inspect.getsource(war_room)
    assert "RoomIntake(" in war_room_source, "the war room does not construct a RoomIntake"

    intake = RoomIntake("s1", debounce_seconds=0.01)
    assert isinstance(intake.debouncer, InboundDebouncer)
    assert intake.push("alice", "one") == 1
    assert intake.push("bob", "two") == 2
    batched = intake.flush_now()
    assert batched is not None
    assert batched.message_count == 2
    assert set(batched.senders) == {"alice", "bob"}


def test_the_room_intake_gathers_with_per_sender_attribution():
    import asyncio

    from alpha.channels.timing import RoomIntake

    async def scenario():
        intake = RoomIntake("s2", debounce_seconds=0.01)
        intake.push("alice", "first")
        intake.push("bob", "second")
        return await intake.gather_stage_input(max_wait_seconds=0.01)

    ordered, turn = asyncio.run(scenario())
    assert turn is not None
    assert [m["sender"] for m in ordered] == ["alice", "bob"]
    assert [m["content"] for m in ordered] == ["first", "second"]


def test_every_new_module_is_imported_by_a_production_module():
    """No module in this work is reachable only from a test file."""
    # channel_ops is the runtime entry and imports the channel layer.
    ops_source = Path(channel_ops.__file__).read_text(encoding="utf-8")
    assert "alpha.channels.routing" in ops_source
    assert "alpha.groups.supervisor" in ops_source
    assert "alpha.groups.lifecycle_ops" in ops_source

    # The tool imports the war room and the lifecycle and the acquisition fence.
    # Resolved by module, not by the exported name: the export is a StructuredTool.
    import sys

    tool_mod = sys.modules["alpha.tools.builtins.war_room_tool"]
    tool_source = Path(tool_mod.__file__).read_text(encoding="utf-8")
    assert "alpha.groups.war_room" in tool_source
    assert "alpha.groups.acquisition" in tool_source

    # The lifecycle facade imports the existing components it reuses.
    lc_source = Path(lifecycle_ops.__file__).read_text(encoding="utf-8")
    for reused in (
        "alpha.bots.dynamic_profiles",
        "alpha.bots.cloning",
        "alpha.bots.lifecycle_governor",
        "alpha.bots.authority_ceiling",
    ):
        assert reused in lc_source, f"{reused} is not reused by lifecycle_ops"

    # The acquisition fence reuses the existing scanners rather than reinventing.
    acq_source = Path(acquisition.__file__).read_text(encoding="utf-8")
    assert "alpha.skills.skillscan.orchestrator" in acq_source
    assert "alpha.bots.authority_ceiling" in acq_source


def test_the_supervisor_reuses_the_existing_taxonomy_without_editing_it():
    """The taxonomy module must not have been modified by this work."""
    import alpha.bots.failure_reasons as taxonomy

    source = Path(taxonomy.__file__).read_text(encoding="utf-8")
    assert "class FailureReason" not in source, "a new enum was added to the taxonomy"
    assert len(taxonomy.ALL_REASONS) == 19, "the taxonomy's reason set changed"
    # The supervisor imports it, and the import is what it uses.
    sup_source = Path(supervisor.__file__).read_text(encoding="utf-8")
    assert "from alpha.bots.failure_reasons import" in sup_source
    assert "decide_failure(" in sup_source
    assert "classify_work_failure(" in sup_source


def test_the_war_room_reuses_the_group_and_deliberation_engines():
    """groups/ and deliberation/ are the engines; the war room schedules them."""
    source = Path(war_room.__file__).read_text(encoding="utf-8")
    assert "from alpha.groups.quorum import QuorumEngine" in source
    assert "from alpha.channels.timing import" in source
    # The real runtime participant is a subagent, not a bespoke executor.
    assert "from alpha.subagents.executor import" in source
    assert "SubagentExecutor" in source


def test_no_second_war_room_engine_exists_beside_the_one_in_groups():
    """A parallel war room next to groups/ would be a defect, not a feature.

    Scans production code only (``PACKAGE_ROOT`` is the ``alpha`` package, which
    does not contain ``backend/tests``). Two names are legitimate and expected:
    the engine (``groups/war_room.py``) and the tool that exposes it
    (``tools/builtins/war_room_tool.py``). Anything else implementing a second
    scheduler is the defect this guards.
    """
    allowed = {
        (PACKAGE_ROOT / "groups" / "war_room.py").resolve(),
        (PACKAGE_ROOT / "tools" / "builtins" / "war_room_tool.py").resolve(),
    }
    offenders = [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "war_room" in path.stem and path.resolve() not in allowed
    ]
    assert not offenders, f"a parallel war room exists: {offenders}"

    # And there is exactly one WarRoom class in the whole package.
    definitions: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "WarRoom":
                definitions.append(str(path.resolve()))
    expected = str((PACKAGE_ROOT / "groups" / "war_room.py").resolve())
    assert definitions == [expected], f"expected exactly one WarRoom in {expected}, got {definitions}"
