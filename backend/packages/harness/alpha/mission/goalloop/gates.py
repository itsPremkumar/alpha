"""Quality gates: a deterministic command that must exit 0 before "done".

The judge in :mod:`judge` is an LLM reading prose. A gate is stronger than that
because it is not an opinion: it is a command, an exit code, and an output tail.
The loop in :mod:`engine` therefore runs gates *first* and does not call the
judge at all while any gate is red - a red gate is evidence, not an opinion, and
letting a model argue with an exit code is how a goal gets "completed" against a
failing test suite.

Three properties matter more than the feature itself:

* **Never stale.** Every turn boundary re-executes the command against current
  inputs. There is no memoisation anywhere in this module, so a gate whose
  input the agent just repaired passes on the very next boundary.
* **Never unbounded.** One attempt per boundary, each capped at
  ``timeout_seconds``, and the *cumulative* failure bound lives on the goal
  state (:data:`DEFAULT_MAX_GATE_FAILURES`). Keeping the bound off the gate
  itself is deliberate: a bound applied inside a single boundary would convert
  the first red boundary straight into a pause, and the agent would never get
  to read the failure it is being asked to fix. Bounding across boundaries is
  what makes "here is the exit code and the tail, go again" reachable while
  still guaranteeing the loop cannot spin forever.
* **Never a wedge.** A gate that cannot be executed at all (empty command,
  unshippable interpreter) is reported as :data:`GateStatus.ERROR` and treated
  as red, not as a pass. There is no code path here that returns "pass"
  without a real ``returncode == 0``.

The output *tail* is what the agent gets to iterate against, so it is bounded
(:data:`DEFAULT_TAIL_BYTES`) and carries the exit code in its header - the
failure the agent needs is the last thing the command printed, not the first.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

#: One attempt per boundary. The cumulative bound across boundaries is
#: :data:`DEFAULT_MAX_GATE_FAILURES`, carried on the goal state.
DEFAULT_MAX_ATTEMPTS: Final[int] = 1

#: Consecutive red boundaries tolerated per gate before the goal auto-pauses.
#: This is the "3 retries" bound: it is spent across boundaries, not inside one.
DEFAULT_MAX_GATE_FAILURES: Final[int] = 3

DEFAULT_TIMEOUT_SECONDS: Final[float] = 300.0

#: How much of the combined output survives into the continuation prompt.
DEFAULT_TAIL_BYTES: Final[int] = 3 * 1024

#: Refusal reasons, stable strings so tests and operators can match on them.
REASON_NO_COMMAND: Final[str] = "gate has no command to run"
REASON_EXHAUSTED: Final[str] = "gate exhausted its retries without exiting 0"

#: Minimum wall-clock gap between attempts, so a command that fails instantly
#: does not burn all its attempts in microseconds.
RETRY_BACKOFF_SECONDS: Final[float] = 0.0


class GateStatus(StrEnum):
    """Terminal state of one gate attempt."""

    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    ERROR = "error"

    @property
    def is_pass(self) -> bool:
        return self is GateStatus.PASS


@dataclass(frozen=True)
class QualityGate:
    """One deterministic command attached to a goal.

    ``command`` is a shell command line, matching the operator-facing syntax of
    ``/goal gate add <command>``. ``label`` is display text only.
    """

    command: str
    label: str = ""
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    cwd: str = ""

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("a gate needs at least one attempt")
        if self.timeout_seconds <= 0:
            raise ValueError("a gate needs a positive timeout")

    @property
    def name(self) -> str:
        return self.label or self.command

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "label": self.label,
            "max_attempts": self.max_attempts,
            "timeout_seconds": self.timeout_seconds,
            "cwd": self.cwd,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QualityGate:
        return cls(
            command=str(data.get("command", "")),
            label=str(data.get("label", "")),
            max_attempts=int(data.get("max_attempts", DEFAULT_MAX_ATTEMPTS) or DEFAULT_MAX_ATTEMPTS),
            timeout_seconds=float(data.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS),
            cwd=str(data.get("cwd", "")),
        )


@dataclass(frozen=True)
class GateResult:
    """What one gate did on this boundary. Never cached across boundaries."""

    gate: QualityGate
    status: GateStatus
    exit_code: int | None = None
    output_tail: str = ""
    attempts: int = 0
    duration_seconds: float = 0.0
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.status.is_pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.gate.command,
            "label": self.gate.label,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "output_tail": self.output_tail,
            "attempts": self.attempts,
            "duration_seconds": self.duration_seconds,
            "reason": self.reason,
        }


def _tail(text: str, limit: int = DEFAULT_TAIL_BYTES) -> str:
    """Return the last *limit* bytes of *text*, decoded defensively."""
    if not text:
        return ""
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return text
    clipped = raw[-limit:].decode("utf-8", errors="replace")
    return f"[... {len(raw) - limit} earlier bytes omitted ...]\n{clipped}"


def _render_tail(command: str, exit_code: int | None, status: GateStatus, body: str) -> str:
    header = f"$ {command}\nexit={exit_code if exit_code is not None else status.value}"
    return f"{header}\n{_tail(body)}".rstrip()


#: Signature of the process runner, injectable so tests never shell out and so
#: a host can interpose its own sandbox. Returns ``(exit_code, combined_output)``
#: or raises :class:`subprocess.TimeoutExpired` / any other exception.
ProcessRunner = Callable[[QualityGate], "tuple[int | None, str]"]


def _default_runner(gate: QualityGate) -> tuple[int | None, str]:
    completed = subprocess.run(  # noqa: S602 - operator-supplied shell command by design
        gate.command,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=gate.timeout_seconds,
        cwd=gate.cwd or None,
        check=False,
    )
    return completed.returncode, f"{completed.stdout}{completed.stderr}"


class GateRunner:
    """Executes gates. Holds no result cache, on purpose.

    The absence of memoisation is the feature: :meth:`run_all` is called at
    every turn boundary and re-executes each command, so a gate can never
    report a stale green.
    """

    def __init__(
        self,
        runner: ProcessRunner | None = None,
        *,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._runner = runner or _default_runner
        self._sleep = sleep or time.sleep

    def run(self, gate: QualityGate) -> GateResult:
        """Run the gate exactly once, against current inputs.

        No internal retry and no result cache. Both are on purpose: retrying
        inside one boundary would burn the whole failure budget before the
        agent ever saw the output, and caching would let a stale green survive
        into the next boundary.
        """
        started = time.monotonic()
        if not gate.command.strip():
            return GateResult(
                gate=gate,
                status=GateStatus.ERROR,
                reason=REASON_NO_COMMAND,
                duration_seconds=time.monotonic() - started,
            )
        try:
            exit_code, body = self._runner(gate)
        except subprocess.TimeoutExpired:
            return GateResult(
                gate=gate,
                status=GateStatus.TIMEOUT,
                exit_code=None,
                output_tail=_render_tail(
                    gate.command, None, GateStatus.TIMEOUT, f"timed out after {gate.timeout_seconds:g}s"
                ),
                attempts=1,
                duration_seconds=time.monotonic() - started,
                reason=f"gate timed out after {gate.timeout_seconds:g}s",
            )
        except Exception as exc:  # noqa: BLE001 - an unrunnable gate is red, never green
            return GateResult(
                gate=gate,
                status=GateStatus.ERROR,
                exit_code=None,
                output_tail=_render_tail(gate.command, None, GateStatus.ERROR, f"{type(exc).__name__}: {exc}"),
                attempts=1,
                duration_seconds=time.monotonic() - started,
                reason=f"{type(exc).__name__}: {exc}",
            )

        status = GateStatus.PASS if exit_code == 0 else GateStatus.FAIL
        return GateResult(
            gate=gate,
            status=status,
            exit_code=exit_code,
            output_tail=_render_tail(gate.command, exit_code, status, body),
            attempts=1,
            duration_seconds=time.monotonic() - started,
        )

    def run_all(self, gates: Sequence[QualityGate]) -> list[GateResult]:
        """Run every gate; the first red one is *not* short-circuited away.

        Running all of them means the agent sees every failure at once instead
        of discovering them one boundary at a time. An empty list is all-pass by
        definition - with no gates there is nothing to be red.
        """
        return [self.run(gate) for gate in gates]


@dataclass
class GateReport:
    """The aggregate for one boundary."""

    results: list[GateResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def red(self) -> list[GateResult]:
        return [r for r in self.results if not r.passed]

    def render_failures(self, *, attempts: dict[int, int] | None = None) -> str:
        """The continuation-prompt body for a red boundary.

        *attempts* maps the 1-based gate index to how many consecutive
        boundaries that gate has now been red, so the agent can see whether it
        is close to the bound that pauses the goal.
        """
        blocks = []
        for index, result in enumerate(self.red, start=1):
            actual = self.results.index(result) + 1
            count = (attempts or {}).get(actual)
            progress = f"\nconsecutive failures: {count}" if count else ""
            blocks.append(
                f"### gate {actual}: {result.gate.name}\n"
                f"status: {result.status.value}{progress}\n{result.output_tail}"
            )
        return "\n\n".join(blocks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_passed": self.all_passed,
            "results": [result.to_dict() for result in self.results],
        }


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_GATE_FAILURES",
    "DEFAULT_TAIL_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "REASON_EXHAUSTED",
    "REASON_NO_COMMAND",
    "GateReport",
    "GateResult",
    "GateRunner",
    "GateStatus",
    "ProcessRunner",
    "QualityGate",
]
