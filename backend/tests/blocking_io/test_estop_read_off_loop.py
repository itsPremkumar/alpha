"""The fleet ESTOP read must not run on Gateway's event loop.

`EstopCapability` resolves the sentinel and stats it -- `os.getcwd` and `os.stat`
are exactly what the strict gate flags -- and the Mod Kernel's handlers are
`async`, running right on that loop, so reading inline stalls every other run in
the process on a disk read for *every* admission. `runs/worker.py::run_agent`
already offloads the very same fleet control read with `asyncio.to_thread`; this
is the other end of that same read.

The way it surfaced made the defect worse than a slow loop: `is_engaged()`
swallows the `BlockingError` in its fail-closed `except` and answers "engaged",
so a run was denied for an emergency stop nobody ever tripped -- the blocking
violation hiding behind a fabricated safety event.

The sentinel is pointed at `tmp_path` so the outcome does not depend on whether
this machine happens to have a stop engaged, and so a crash mid-test cannot
leave a real fleet stop behind in the developer's `.alpha`.
"""

from __future__ import annotations

import asyncio

import pytest

from alpha.mods.enforcers.estop_mod import FleetEstopMod
from alpha.mods.kernel import ModKernel
from alpha.mods.types import AlphaEvent, CorrelationContext, EventOutcome

pytestmark = pytest.mark.asyncio


@pytest.fixture
def estop(tmp_path, monkeypatch):
    """An isolated stop switch, engaged only when a test asks for it."""
    from alpha.runtime.estop import EmergencyStopManager

    manager = EmergencyStopManager(root_dir=tmp_path)
    monkeypatch.setattr("alpha.runtime.estop.get_estop_manager", lambda root_dir=None: manager)
    return manager


def _admit_event() -> AlphaEvent:
    return AlphaEvent(
        name="run.admit",
        payload={"run_id": "run-off-loop-estop"},
        correlation=CorrelationContext.create(run_id="run-off-loop-estop"),
    )


async def test_a_disengaged_fleet_stop_admits_work_without_blocking_the_loop(estop) -> None:
    """No sentinel exists, so admission must continue -- never deny.

    Before the read moved off-loop this failed with
    ``FLEET_ESTOP_ACTIVE: Emergency stop active across fleet``: the inline stat
    raised, the fail-closed handler read it as an engaged stop, and the run was
    refused for an emergency that had not happened.
    """
    # The test body sits under the same gate as production, so even asking the
    # precondition has to be asked off-loop -- as every other file in this
    # directory does with its own setup.
    assert not await asyncio.to_thread(estop.is_engaged)

    kernel = ModKernel()
    kernel.register_mod(FleetEstopMod())
    result = await kernel.dispatch(_admit_event())

    assert result.outcome == EventOutcome.CONTINUE, f"a fleet with no stop sentinel must admit work; got {result.outcome.name} with reason {result.reason!r}"


async def test_an_engaged_stop_still_denies_when_read_from_off_the_event_loop(estop) -> None:
    """The offload must not weaken the safety control it was moved for."""
    await asyncio.to_thread(estop.engage, "operator test stop")

    kernel = ModKernel()
    kernel.register_mod(FleetEstopMod())
    result = await kernel.dispatch(_admit_event())

    assert result.outcome == EventOutcome.DENY
    assert "FLEET_ESTOP_ACTIVE" in result.reason
    assert "operator test stop" in result.reason
