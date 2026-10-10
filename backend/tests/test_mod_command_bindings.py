"""Mod commands projected into the Gateway slash-command catalog.

A mod command is only reachable from the operator command plane if the catalog
knows about it, and the projection has to stay honest while mods register and
withdraw commands at runtime. These tests pin the three properties the bridge
promises: a bound row really executes, a mod cannot take a name the catalog
already owns, and withdrawing a command stops advertising it.
"""

from __future__ import annotations

import asyncio

import pytest

from alpha.commands.registry import APPROVAL_CONTEXT_KEY, command_registry
from alpha.mods.kernel import get_mod_kernel, reset_mod_kernel

#: A core command every catalog assertion can measure itself against: it must
#: survive any projection attempt unchanged.
CORE_COMMAND = "/doctor"


@pytest.fixture(autouse=True)
def _clean_projection():
    """Leave the shared catalog exactly as these tests found it."""
    yield
    get_mod_kernel().commands.clear()  # an empty projection prunes every mod row
    reset_mod_kernel()
    command_registry.unregister("/mod-bind-test")
    command_registry.unregister("/mod-bind-async")
    command_registry.unregister("/mod-bind-approved")


def _rows_for(prefix: str = "/mod-bind") -> list:
    return [row for row in command_registry.list_commands() if row.command.startswith(prefix)]


def test_registration_projects_an_executable_row():
    kernel = get_mod_kernel()
    kernel.commands.register("test_mod", "mod-bind-test", lambda payload: "pong", description="health probe")

    rows = _rows_for("/mod-bind-test")
    assert len(rows) == 1
    row = rows[0]
    # Runnable is a fact the discovery plane reports, so the row must really be
    # handler-backed rather than a catalogue stub that answers "nothing ran".
    assert command_registry.has_handler(row.command) is True
    assert row.is_core is False
    assert row.is_autonomous_trigger is False
    assert row.metadata["source"] == "alpha_mod_kernel"
    assert row.metadata["mod_name"] == "test_mod"
    assert row.description.startswith("[mod:test_mod]")


def test_catalog_dispatch_runs_the_mod_handler():
    kernel = get_mod_kernel()
    kernel.commands.register("test_mod", "mod-bind-test", lambda payload: {"echo": payload.get("args")})

    result = command_registry.execute("/mod-bind-test hello")

    assert result.status == "success"
    assert result.data["executed"] is True
    assert result.data["mod_name"] == "test_mod"
    assert result.data["source"] == "alpha_mod_kernel"


def test_verdict_reads_as_succeeded_only_because_a_handler_is_bound():
    """The router's defence-in-depth refuses success without a handler."""
    from app.gateway.routers.commands import _verdict_for

    kernel = get_mod_kernel()
    kernel.commands.register("test_mod", "mod-bind-test", lambda payload: "pong")

    assert _verdict_for(command_registry.execute("/mod-bind-test").to_dict()) == "succeeded"


def test_async_mod_handler_is_driven_from_the_sync_dispatch():
    kernel = get_mod_kernel()

    async def _handler(payload):
        await asyncio.sleep(0)
        return "async pong"

    kernel.commands.register("test_mod", "mod-bind-async", _handler)

    result = command_registry.execute("/mod-bind-async")

    assert result.status == "success"
    assert result.output == "async pong"


def test_a_raising_handler_fails_the_command_instead_of_unwinding():
    kernel = get_mod_kernel()

    def _handler(payload):
        raise RuntimeError("mod blew up")

    kernel.commands.register("test_mod", "mod-bind-test", _handler)

    result = command_registry.execute("/mod-bind-test")

    assert result.status == "error"
    assert result.data["executed"] is False
    assert "mod blew up" in result.output


def test_withdrawing_a_command_stops_advertising_it():
    kernel = get_mod_kernel()
    kernel.commands.register("test_mod", "mod-bind-test", lambda payload: "pong")
    assert _rows_for("/mod-bind-test"), "projection never happened"

    assert kernel.commands.unregister("test_mod", "/mod-bind-test") is True

    assert _rows_for("/mod-bind-test") == []
    assert command_registry.execute("/mod-bind-test").status == "not_found"


def test_clearing_the_kernel_prunes_every_projected_row():
    kernel = get_mod_kernel()
    kernel.commands.register("test_mod", "mod-bind-test", lambda payload: "a")
    kernel.commands.register("test_mod", "mod-bind-async", lambda payload: "b")
    assert len(_rows_for()) == 2

    assert kernel.commands.clear() == 2

    assert _rows_for() == []


def test_a_stale_row_reports_nothing_ran():
    """A handler called after its mod withdrew the command must not claim success."""
    from alpha.mods.bindings import _make_handler

    kernel = get_mod_kernel()
    kernel.commands.register("test_mod", "mod-bind-test", lambda payload: "pong")
    # The handler a dispatch in flight would already be holding when the mod
    # withdraws the command underneath it.
    handler = _make_handler("mod-bind-test")

    kernel.commands.unregister("test_mod", "/mod-bind-test")

    result = handler("", context={})

    assert result.status == "not_found"
    assert result.data["executed"] is False
    assert "no longer registered" in result.output


def test_a_mod_may_not_take_a_catalogued_name():
    """The operator's /doctor keeps its own row, description and handler."""
    kernel = get_mod_kernel()
    before = command_registry.get(CORE_COMMAND)
    assert before is not None
    had_handler = command_registry.has_handler(CORE_COMMAND)

    kernel.commands.register("test_mod", "doctor", lambda payload: "hijacked")

    row = command_registry.get(CORE_COMMAND)
    assert row is before
    assert not row.description.startswith("[mod:test_mod]")
    assert row.metadata.get("source") != "alpha_mod_kernel"
    # The catalog's own binding is untouched, so dispatch still reaches the core
    # handler rather than the mod's.
    assert command_registry.has_handler(CORE_COMMAND) is had_handler


def test_approval_declared_by_a_mod_uses_the_catalog_gate():
    kernel = get_mod_kernel()
    kernel.commands.register(
        "test_mod",
        "mod-bind-approved",
        lambda payload: "gated ran",
        description="needs a human",
        requires_approval=True,
    )

    blocked = command_registry.execute("/mod-bind-approved")
    assert blocked.status == "approval_required"
    assert blocked.data.get("executed") is not True

    granted = command_registry.execute("/mod-bind-approved", context={APPROVAL_CONTEXT_KEY: True})
    assert granted.status == "success"
    assert granted.output == "gated ran"


def test_driving_a_coroutine_on_a_running_loop_does_not_deadlock():
    """The catalog's sync dispatch can be reached from a thread that owns a loop."""
    from alpha.mods.bindings import _drive

    async def _work():
        await asyncio.sleep(0)
        return 42

    async def _caller():
        # No await on this call site: blocking the caller's own loop is exactly
        # what the thread fallback exists to avoid.
        return _drive(_work())

    assert asyncio.run(_caller()) == 42
