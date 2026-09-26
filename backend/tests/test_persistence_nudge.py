"""M2 tests: persistence nudge gate matrix, hydration, fire cycle, honesty.

Pins Hermes turn_context.py:709-718 tick gates (capability present), the
import-only seam onto alpha.learning.nudges.build_memory_nudge (no second
divergent prompt string), the honest "evidence unknown" fallback, and
should_suppress for autonomous forks.
"""

import pytest

from alpha.learning.nudges import build_memory_nudge
from alpha.memory import persistence_nudge
from alpha.memory.persistence_nudge import build_persistence_nudge, hydrate, should_suppress, tick


@pytest.fixture(autouse=True)
def _workspace_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    return tmp_path


def _tick(counter: int, interval: int = 3, *, tool: bool = True, store: bool = True) -> tuple[int, bool]:
    return tick(counter, interval, memory_tool_available=tool, has_store=store)


def test_gate_matrix_capability_required():
    # no store -> never fires, counter untouched
    assert _tick(0, 1, store=False) == (0, False)
    assert _tick(5, 1, store=False) == (5, False)
    # memory tool unavailable -> never fires, counter untouched
    assert _tick(0, 1, tool=False) == (0, False)
    assert _tick(5, 1, tool=False) == (5, False)
    # interval disabled (0 or negative) -> never fires, counter untouched
    assert _tick(5, 0) == (5, False)
    assert _tick(5, -3) == (5, False)


def test_fire_reset_cycle_returns_real_counter():
    assert _tick(0) == (1, False)
    assert _tick(1) == (2, False)
    assert _tick(2) == (0, True)  # fired at threshold AND reset to 0
    # second cycle needs the full cadence again
    assert _tick(0) == (1, False)
    assert _tick(1) == (2, False)
    assert _tick(2) == (0, True)
    # interval of 1 fires immediately
    assert _tick(0, 1) == (0, True)
    # gated state holds its counter until the capability returns
    gated = _tick(2, store=False)
    assert gated == (2, False)
    assert _tick(gated[0]) == (0, True)


def test_hydrate_resume_modulo():
    assert hydrate(7, 3) == 1
    assert hydrate(4, 3) == 1
    assert hydrate(3, 3) == 0
    assert hydrate(0, 3) == 0
    assert hydrate(-7, 3) == 0
    assert hydrate(5, 0) == 0


def _caller_tick(turns: int, *, is_autonomous_fork: bool, interval: int = 3) -> tuple[int, bool]:
    """Documented caller seam: a suppressed fork never advances the counter."""
    if should_suppress(is_autonomous_fork):
        return turns, False
    return tick(turns, interval, memory_tool_available=True, has_store=True)


def test_autonomous_forks_never_nudge():
    assert should_suppress(is_autonomous_fork=True) is True
    assert should_suppress(is_autonomous_fork=False) is False
    assert _caller_tick(2, is_autonomous_fork=True) == (2, False)  # never fires
    assert _caller_tick(2, is_autonomous_fork=False) == (0, True)


def test_build_reuses_canonical_memory_nudge_text():
    # identity pin: the module uses the imported object, no redefinition
    assert persistence_nudge.build_memory_nudge is build_memory_nudge
    text = build_persistence_nudge("payments")
    assert build_memory_nudge("payments") in text  # canonical reminder verbatim
    assert "evidence unknown" in text  # no count given -> honest unknown
    default_text = build_persistence_nudge()
    assert build_memory_nudge("") in default_text


def test_build_counter_lines_real_numbers_and_unknown_fallback():
    with_both = build_persistence_nudge(stored_memory_count=7, turns_since=4)
    assert "evidence unknown" not in with_both
    assert "Stored-memory records on hand: 7." in with_both
    assert "Turns since the last persistence checkpoint: 4." in with_both
    # a provided 0 is a real count, not "unknown"
    zero = build_persistence_nudge(stored_memory_count=0)
    assert "evidence unknown" not in zero
    assert "Stored-memory records on hand: 0." in zero
    # count omitted but turns given -> counter line still real, evidence honest
    turns_only = build_persistence_nudge(turns_since=9)
    assert "checkpoint: 9." in turns_only
    assert "evidence unknown" in turns_only
