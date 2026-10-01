"""Fleet-wide runtime control: one authoritative mode and generation.

## Why this exists

Two stop mechanisms already existed and neither was fleet-wide:

* ``alpha.bots.kill_switch`` - an in-memory boolean. It vanishes on restart, so it
  cannot stop work that a restart resumed.
* ``alpha.runtime.estop`` - a sentinel *file*, and its only consumer was
  ``alpha.rsi.switchboard``. It gated the recursive-self-improvement cycle and
  nothing else: not the run worker, not admission, not the eight autonomy loops.

So an operator who engaged the emergency stop saw "Fleet execution paused." while
the fleet kept running. The decision was recorded; the enforcement was not.

## The design, and why it is a generation counter

The obvious implementation - check a flag at the top of every execution path -
has a hole that shows up under exactly the conditions a kill switch exists for.
A worker already past its check when the operator engages the stop will not see
it. A long-running loop that checked once at entry keeps running for its whole
cycle.

The fix, borrowed from distributed-lock practice (Kleppminn's fencing-token rule:
*assume your view of the world is stale; validate at the resource level*), is to
make the state **monotonic**. Engaging a stop advances a generation counter, and
work carries the generation it was admitted under. Before any *effect*, the
worker revalidates that its generation is still current. A worker that was
admitted under generation 7 cannot perform an effect under generation 8, no
matter where in its own body it is.

This is why the mode is not a boolean:

* ``RUN``   - normal execution.
* ``PAUSE`` - refuse *new* work; let admitted work finish. Recoverable, so a
  restart does not inherit it.
* ``ESTOP`` - refuse new work *and* refuse effects from admitted work. Only an
  explicit operator clear lifts it.

## Two properties this deliberately does not claim

**It does not roll back an effect that already happened.** It prevents the next
one. A tool that completed before the stop engaged stays completed; recovery and
the side-effect ledger own that question, and duplicating their authority here
would be the second lifecycle owner this layer is not allowed to become.

**It does not cancel a process.** ``bash`` has no cooperative cancellation point;
the worker refuses to *dispatch* while stopped, and an already-running child is
left to its own timeout. Promising instantaneous fleet-wide termination would be
a claim this code cannot keep.

## Durability and failure direction

State lives in a single JSON file under ``runtime_home()``. The original
``EmergencyStopManager`` resolved its directory as ``Path.cwd() / ".alpha"``
while the rest of the runtime resolves through ``alpha.config.runtime_paths``;
that divergence could put the sentinel somewhere the runtime never looks, so it
is resolved the same way here.

An unreadable or corrupt state file fails **closed** - it reports ESTOP and names
the real error. A stop mechanism that fails open is not a stop mechanism. The
exception is a genuinely absent file, which means "never engaged".
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)

STATE_FILENAME = "runtime_control.json"


class ControlMode(StrEnum):
    """The one authoritative execution mode."""

    RUN = "run"
    PAUSE = "pause"
    ESTOP = "estop"

    @property
    def refuses_admission(self) -> bool:
        """True when new work must not start under this mode."""
        return self is not ControlMode.RUN

    @property
    def refuses_effects(self) -> bool:
        """True when already-admitted work must also stop acting.

        Only ``ESTOP``. ``PAUSE`` deliberately lets admitted work finish, which
        is what makes it safe to pause for a maintenance window.
        """
        return self is ControlMode.ESTOP


@dataclass(frozen=True)
class ControlState:
    """An immutable snapshot of fleet control."""

    mode: ControlMode = ControlMode.RUN
    #: Monotonic. Incremented on every transition away from RUN. A worker records
    #: the generation it was admitted under and revalidates before each effect.
    generation: int = 0
    reason: str = ""
    engaged_at: str | None = None
    #: Set when the state file could not be read. Fail-closed: the reported mode
    #: is ESTOP and this carries the real error, never a fabricated one.
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "generation": self.generation,
            "reason": self.reason,
            "engaged_at": self.engaged_at,
            "error": self.error,
        }


#: Raised when work is refused by fleet control. Distinct from a policy denial so
#: a caller can tell "the operator stopped the fleet" from "you may not do this".
class FleetStopped(RuntimeError):
    """Fleet control refused this action. Carries the real reason and mode."""

    def __init__(self, message: str, *, mode: ControlMode, phase: str) -> None:
        super().__init__(message)
        self.mode = mode
        self.phase = phase


def _state_path(root_dir: Path | str | None = None) -> Path:
    base = Path(root_dir) if root_dir is not None else runtime_home()
    return base / STATE_FILENAME


def read_state(root_dir: Path | str | None = None) -> ControlState:
    """Read fleet control state. Never raises; a bad file fails closed."""
    path = _state_path(root_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # No file has ever been written: nobody has engaged a stop.
        return ControlState()
    except OSError as exc:
        return ControlState(
            mode=ControlMode.ESTOP,
            error=f"runtime control state unreadable at {path}: {exc}",
            reason="fail-closed: control state could not be read",
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ControlState(
            mode=ControlMode.ESTOP,
            error=f"runtime control state corrupt at {path}: {exc}",
            reason="fail-closed: control state could not be parsed",
        )

    try:
        mode = ControlMode(str(data.get("mode", "run")).lower())
    except ValueError:
        # An unrecognised mode is not permission to run.
        return ControlState(
            mode=ControlMode.ESTOP,
            error=f"runtime control state has an unknown mode {data.get('mode')!r}",
            reason="fail-closed: control state mode is not recognised",
        )

    try:
        generation = int(data.get("generation", 0))
    except (TypeError, ValueError):
        generation = 0

    return ControlState(
        mode=mode,
        generation=generation,
        reason=str(data.get("reason", "") or ""),
        engaged_at=data.get("engaged_at"),
    )


def write_state(
    mode: ControlMode,
    *,
    reason: str = "",
    root_dir: Path | str | None = None,
    generation: int | None = None,
) -> ControlState:
    """Persist *mode*, advancing the generation when leaving RUN.

    Atomic tmp+replace, so a crash mid-write cannot leave a truncated file that
    reads back as "unknown mode" and silently freezes the fleet. The generation
    is read-modify-written under a process lock; two concurrent engagements then
    produce two advances rather than one, which is the safe direction.
    """
    path = _state_path(root_dir)
    with _write_lock():
        previous = read_state(root_dir)
        if generation is not None:
            new_generation = generation
        elif mode is ControlMode.RUN:
            # Clearing a stop ends it. The generation does not advance, because
            # an advance is precisely what tells already-admitted work that its
            # view of the world is stale - and clearing a stop invalidates
            # nothing. Advancing here would fence work for the privilege of
            # resuming.
            new_generation = previous.generation
        elif mode is ControlMode.PAUSE:
            # PAUSE deliberately does NOT advance the generation. The generation
            # is the "work admitted before this is now stale" counter, and
            # pausing invalidates no in-flight work: that is the whole difference
            # between PAUSE and ESTOP. Advancing here would make PAUSE fence the
            # admitted work it is defined to let finish.
            new_generation = previous.generation
        else:
            # ESTOP is the only transition that invalidates in-flight work.
            new_generation = previous.generation + 1

        from datetime import UTC, datetime

        payload = {
            "mode": mode.value,
            "generation": new_generation,
            "reason": reason,
            "engaged_at": None if mode is ControlMode.RUN else datetime.now(UTC).isoformat(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return ControlState(
            mode=mode,
            generation=new_generation,
            reason=reason,
            engaged_at=payload["engaged_at"],
        )


_write_lock_guard = threading.Lock()


def _write_lock() -> threading.Lock:
    return _write_lock_guard


def get_state(root_dir: Path | str | None = None) -> ControlState:
    """Current fleet control state."""
    return read_state(root_dir)


def engage(reason: str = "", *, root_dir: Path | str | None = None) -> ControlState:
    """Engage the fleet-wide emergency stop. Advances the generation."""
    state = write_state(ControlMode.ESTOP, reason=reason or "Operator emergency stop", root_dir=root_dir)
    logger.warning("Fleet ESTOP engaged (generation=%s): %s", state.generation, state.reason)
    return state


def pause(reason: str = "", *, root_dir: Path | str | None = None) -> ControlState:
    """Refuse new work; let admitted work finish. Advances the generation."""
    state = write_state(ControlMode.PAUSE, reason=reason or "Operator pause", root_dir=root_dir)
    logger.warning("Fleet PAUSE engaged (generation=%s): %s", state.generation, state.reason)
    return state


def clear(root_dir: Path | str | None = None) -> ControlState:
    """Return to RUN. The only way out of ESTOP; never automatic."""
    state = write_state(ControlMode.RUN, reason="", root_dir=root_dir)
    logger.warning("Fleet control cleared (generation=%s): execution resumed", state.generation)
    return state


# ---------------------------------------------------------------------------
# The guard every execution path consults
# ---------------------------------------------------------------------------


def assert_admissible(phase: str, *, root_dir: Path | str | None = None) -> ControlState:
    """Refuse *admitting* new work under PAUSE/ESTOP.

    Args:
        phase: What is trying to start, for the refusal message and the log.

    Raises:
        FleetStopped: Under PAUSE or ESTOP, or when the state is unreadable.
    """
    state = read_state(root_dir)
    if state.mode.refuses_admission:
        raise FleetStopped(_refusal(state, phase), mode=state.mode, phase=phase)
    return state


def assert_effect_allowed(generation: int, phase: str, *, root_dir: Path | str | None = None) -> ControlState:
    """Refuse an *effect* from work admitted under a superseded generation.

    This is the fencing check. ``generation`` is the value the worker captured at
    admission; if fleet control has advanced since, the worker is acting on a view
    of the world that is stale and its effect is refused.

    Under ``PAUSE`` this does not fire for current-generation work, because
    pausing is defined as letting admitted work finish.

    Raises:
        FleetStopped: Under ESTOP, or when this work's generation is stale.
    """
    state = read_state(root_dir)
    # Staleness is checked FIRST, and deliberately: it is the more specific fact.
    # Under ESTOP a stale worker would otherwise be told only "the fleet is
    # stopped", hiding that the deeper problem is that its authority has moved on
    # and re-admission would not give it the same view.
    if generation < state.generation:
        raise FleetStopped(
            f"Fleet control advanced to generation {state.generation} after this work was admitted at generation {generation}; refusing to perform {phase}. Reason: {state.reason or 'unspecified'}",
            mode=state.mode,
            phase=phase,
        )
    if state.mode.refuses_effects:
        raise FleetStopped(_refusal(state, phase), mode=state.mode, phase=phase)
    return state


def _refusal(state: ControlState, phase: str) -> str:
    detail = state.reason or state.error or "unspecified"
    if state.error:
        return f"Fleet control state is unreadable, so {phase} is refused: {state.error}"
    return f"Fleet control is {state.mode.value.upper()} (generation {state.generation}); refusing {phase}: {detail}"


@contextmanager
def admitted(phase: str, *, root_dir: Path | str | None = None):
    """Admit a unit of work, then fence every effect it performs.

    Usage::

        with runtime_control.admitted("run admission") as ticket:
            ...do work...
            ticket.before_effect("model call")

    The ticket captures the generation at admission; ``before_effect`` revalidates
    it. That is the whole point: a long body cannot keep acting on a world that
    moved while it was running.
    """
    state = assert_admissible(phase, root_dir=root_dir)
    yield AdmissionTicket(generation=state.generation, phase=phase, root_dir=root_dir)


@dataclass
class AdmissionTicket:
    """Captured authority for one unit of work."""

    generation: int
    phase: str
    root_dir: Path | str | None = None

    def before_effect(self, effect: str) -> None:
        """Revalidate this ticket before performing *effect*.

        Raises:
            FleetStopped: If control advanced, or ESTOP is engaged.
        """
        assert_effect_allowed(self.generation, effect, root_dir=self.root_dir)


def status(root_dir: Path | str | None = None) -> dict[str, Any]:
    """Operator-facing status, shaped like the existing ESTOP status dict."""
    state = read_state(root_dir)
    payload = state.to_dict()
    payload["state_path"] = str(_state_path(root_dir))
    payload["refuses_admission"] = state.mode.refuses_admission
    payload["refuses_effects"] = state.mode.refuses_effects
    return payload
