"""Timing for chat-driven work: stage clocks and live message coalescing.

Two things live here.

**The stage clock.** Every bounded stage in this package runs on an explicit
deadline derived from a monotonic clock. The clock is INJECTED, so a test can
drive a timeout deterministically instead of sleeping, and so a wedged
participant is bounded by arithmetic rather than by hope. Grace is explicit and
additive: a participant gets ``timeout_seconds`` to finish and ``grace_seconds``
to be reaped, and neither number is secretly multiplied by a retry count.

**Live message coalescing.** :mod:`alpha.channels.debounce` already exists and
was unreachable. This module is the wiring that makes it reachable from a real
runtime path: :class:`RoomIntake` is constructed by the war-room scheduler and
is the only way a message enters a running stage.

Honest limitation, inherited rather than hidden: ``InboundDebouncer`` has no
quiescence logic. ``wait_and_flush`` sleeps a fixed interval and flushes
whatever has arrived, so it coalesces a burst but cannot wait for a room to fall
silent. That is adequate for a stage-scoped intake and is NOT adequate for
"wait until the conversation ends". Reimplementing a debouncer here would
create the parallel mechanism the wiring rule forbids, so the real behaviour is
used and the limitation is stated instead of papered over.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from alpha.channels.debounce.debouncer import BatchedTurn, InboundDebouncer

#: Injected monotonic clock. Seconds, strictly increasing.
Clock = Callable[[], float]

#: Default per-stage budget when a caller does not set one.
DEFAULT_STAGE_TIMEOUT_SECONDS = 30.0

#: Default grace added on top of the stage budget before a stage is declared
#: timed out. This is the reaping window, not extra thinking time.
DEFAULT_STAGE_GRACE_SECONDS = 5.0

#: Hard ceiling on a single stage regardless of configuration. A stage that
#: asks for more than this is clamped, so a config typo cannot park a room
#: open for a day.
MAX_STAGE_TIMEOUT_SECONDS = 3600.0


def monotonic_clock() -> float:
    return time.monotonic()


@dataclass(slots=True)
class StageBudget:
    """A bounded time budget for one stage, plus its grace window."""

    name: str
    timeout_seconds: float = DEFAULT_STAGE_TIMEOUT_SECONDS
    grace_seconds: float = DEFAULT_STAGE_GRACE_SECONDS

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError(f"stage {self.name!r} needs a positive timeout_seconds")
        if self.grace_seconds < 0:
            raise ValueError(f"stage {self.name!r} cannot have negative grace_seconds")
        # Clamp rather than refuse: an over-long budget is a config error, and
        # silently honouring it is how a room stays open forever.
        self.timeout_seconds = min(float(self.timeout_seconds), MAX_STAGE_TIMEOUT_SECONDS)
        self.grace_seconds = float(self.grace_seconds)

    @property
    def total_seconds(self) -> float:
        """Budget plus grace: the hard bound on this stage."""
        return self.timeout_seconds + self.grace_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.name,
            "timeout_seconds": self.timeout_seconds,
            "grace_seconds": self.grace_seconds,
            "total_seconds": self.total_seconds,
        }


class Deadline:
    """A one-shot deadline on an injected monotonic clock.

    Three states, and they are genuinely distinct:

    * ``remaining() > 0`` inside budget;
    * ``expired_budget`` after ``timeout_seconds`` but inside grace, meaning
      "stop waiting for new work, start reaping what is outstanding";
    * ``expired`` after grace, meaning the stage is over and must be recorded
      as a timeout.
    """

    def __init__(self, budget: StageBudget, *, clock: Clock = monotonic_clock) -> None:
        self.budget = budget
        self._clock = clock
        self._start = clock()
        self._budget_end = self._start + budget.timeout_seconds
        self._end = self._start + budget.total_seconds

    @property
    def started_at(self) -> float:
        return self._start

    def elapsed(self) -> float:
        return self._clock() - self._start

    def remaining(self) -> float:
        """Seconds left before the HARD bound. Never negative."""
        return max(0.0, self._end - self._clock())

    def remaining_budget(self) -> float:
        """Seconds left before the budget runs out, ignoring grace."""
        return max(0.0, self._budget_end - self._clock())

    @property
    def expired_budget(self) -> bool:
        """True in the grace window: budget spent, not yet over."""
        return self._clock() >= self._budget_end and self._clock() < self._end

    @property
    def expired(self) -> bool:
        return self._clock() >= self._end

    def expired_after(self, started: float) -> float:
        """How long ago ``started`` was, for per-participant reaping."""
        return max(0.0, self._clock() - started)

    async def wait(self) -> None:
        """Sleep until the hard bound. Returns immediately if already past it."""
        left = self.remaining()
        if left > 0:
            await asyncio.sleep(left)


async def run_bounded(
    work: Callable[[], Awaitable[Any]],
    deadline: Deadline,
    *,
    cancel: Callable[[], None] | None = None,
) -> tuple[str, Any, str]:
    """Run ``work`` under ``deadline`` and return ``(status, value, error)``.

    ``status`` is one of ``completed``, ``failed``, ``timeout``, ``cancelled``.
    A wedged coroutine is CANCELLED at the hard bound and reported as
    ``timeout``; it is never awaited to completion and never left pending.

    ``cancel`` is invoked for the non-timeout outcomes too, so a caller that
    owns an external handle (a subagent execution id, a queued task) can always
    release it in one place.
    """
    task = asyncio.ensure_future(work())
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=deadline.remaining())
    except TimeoutError:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        if cancel is not None:
            cancel()
        return ("timeout", None, f"exceeded {deadline.budget.total_seconds:.3f}s bound (timeout+grace)")
    except asyncio.CancelledError:
        task.cancel()
        if cancel is not None:
            cancel()
        return ("cancelled", None, "cancelled")
    except BaseException as exc:  # noqa: BLE001
        if cancel is not None:
            cancel()
        return ("failed", None, f"{type(exc).__name__}: {exc}")

    if cancel is not None:
        cancel()
    try:
        return ("completed", task.result(), "")
    except BaseException as exc:  # noqa: BLE001
        return ("failed", None, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# live message coalescing (wires alpha.channels.debounce)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class IntakeMessage:
    sender: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"sender": self.sender, "content": self.content, "metadata": dict(self.metadata)}


class RoomIntake:
    """The only door a message enters a running stage through.

    Wraps :class:`alpha.channels.debounce.InboundDebouncer` rather than
    reimplementing it, which is what makes that module reachable from a real
    runtime path. Messages arriving inside one coalescing window become a single
    :class:`BatchedTurn` attributed per sender.
    """

    def __init__(
        self,
        session_id: str,
        *,
        debounce_seconds: float = 0.5,
        max_batch_messages: int = 64,
    ) -> None:
        self.session_id = session_id
        self.debouncer = InboundDebouncer(debounce_seconds=debounce_seconds)
        self.max_batch_messages = max_batch_messages
        self._received = 0
        self._dropped = 0

    def push(self, sender: str, content: str, **metadata: Any) -> int:
        """Queue one inbound message. Returns the current queue depth."""
        self._received += 1
        return int(
            self.debouncer.push(self.session_id, sender, content, dict(metadata) or None)
        )

    async def flush(self, *, max_wait_seconds: float | None = None) -> BatchedTurn | None:
        """Wait one coalescing window, then take the batch.

        Returns ``None`` when nothing arrived, which the caller must treat as an
        empty contribution rather than as an error.
        """
        turn = await self.debouncer.wait_and_flush(
            self.session_id, max_wait_seconds=max_wait_seconds
        )
        if turn is None:
            return None
        if turn.message_count > self.max_batch_messages:
            self._dropped += turn.message_count - self.max_batch_messages
        return turn

    async def gather_stage_input(
        self, *, max_wait_seconds: float | None = None
    ) -> tuple[list[dict[str, Any]], BatchedTurn | None]:
        """Collect one stage's worth of live traffic, in order, with attribution.

        The debouncer's merged blob loses per-message attribution, so this
        returns the ordered messages as well as the batch. ``BatchedTurn`` is
        None when nothing arrived, which the caller must treat as an empty
        contribution rather than as an error.
        """
        turn = await self.flush(max_wait_seconds=max_wait_seconds)
        if turn is None:
            return [], None
        ordered: list[dict[str, Any]] = []
        for sender, line in zip(turn.senders, turn.merged_content.splitlines(), strict=False):
            ordered.append({"sender": sender, "content": line})
        return ordered, turn

    def flush_now(self) -> BatchedTurn | None:
        return self.debouncer.flush(self.session_id)

    @property
    def stats(self) -> dict[str, int]:
        return {
            "received": self._received,
            "dropped_over_batch_ceiling": self._dropped,
            "max_batch_messages": self.max_batch_messages,
        }

    def render(self, turn: BatchedTurn) -> str:
        """Per-sender attribution, which the raw merged blob does not preserve."""
        return "\n".join(f"@{sender}: {line}" for sender, line in zip(turn.senders, turn.merged_content.splitlines(), strict=False))
