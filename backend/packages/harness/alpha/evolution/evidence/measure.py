"""The measurement contract: how a number enters the gate, and what it is worth.

A promotion decision is only as good as the numbers behind it, so this module
makes it structurally hard to fake one:

* :class:`MeasurementSource` is a **protocol** - the caller injects whatever
  harness produced the number (Alpha's ``alpha.benchmarks`` runner, the memory
  evaluation suite, a CI job, ...). The gate never runs a benchmark itself and
  never imports a production stack.
* :class:`ScriptedSource` is a hermetic double for tests: no I/O, no sleeping,
  no subprocesses. It defaults to ``evidence="simulated"`` so a scripted value
  can never be mistaken for a measurement; a test that stands in for a real
  harness must say so explicitly.
* :class:`CommandSource` runs an **argv list** - never a shell string. There is
  no ``shell=True`` anywhere in this package, so nothing here is vulnerable to
  shell interpolation, and a fixed timeout plus an output cap bound the blast
  radius of a misbehaving harness.
* the status mapping is the honesty core:
  ``exit 0`` + parsable output -> ``measured``;
  timeout -> ``unavailable``; non-zero exit / crash -> ``error``;
  unparsable output -> ``unavailable``. **None** of those ever produce ``0.0``.
  A non-zero exit is *not* a measurement of zero, and a timeout is not a pass.
* every measurement carries its sample size and its evidence label, so the
  comparison layer can refuse a number taken from three samples when the policy
  demands twenty (:func:`alpha.evolution.evidence.compare.compare_measurements`).

No network access, no new dependencies, no global mutable state.
"""

from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from alpha.evolution.evidence.models import (
    EVIDENCE_MEASURED,
    EVIDENCE_SIMULATED,
    EVIDENCE_UNVERIFIED,
    MEASUREMENT_SIDES,
    Measurement,
)

__all__ = [
    "DEFAULT_MAX_OUTPUT_CHARS",
    "DEFAULT_TIMEOUT_SECONDS",
    "CommandRun",
    "CommandSource",
    "MeasurementRequest",
    "MeasurementSource",
    "ScriptedSource",
    "UnavailableSource",
    "collect_measurements",
    "parse_measurement_output",
    "run_argv",
]

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_OUTPUT_CHARS = 4000


@dataclass(frozen=True)
class MeasurementRequest:
    """One metric/side pair the gate wants measured.

    ``harness_id`` is caller-declared on purpose: the comparison layer refuses
    a candidate/incumbent pair measured by different harness revisions, and it
    can only do that if the identity travels with the request.
    """

    metric: str
    side: str
    harness_id: str
    unit: str = "ratio"
    sample_size: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric", str(self.metric).strip())
        object.__setattr__(self, "side", str(self.side).strip())
        object.__setattr__(self, "harness_id", str(self.harness_id).strip())
        object.__setattr__(self, "unit", str(self.unit).strip())
        if not self.metric:
            raise ValueError("measurement request metric must be a non-empty string")
        if self.side not in MEASUREMENT_SIDES:
            raise ValueError(f"measurement request side must be one of {list(MEASUREMENT_SIDES)}, got {self.side!r}")
        if not self.harness_id:
            raise ValueError("measurement request harness_id must be a non-empty string")
        if not self.unit:
            raise ValueError("measurement request unit must be a non-empty string")
        if isinstance(self.sample_size, bool) or not isinstance(self.sample_size, int) or self.sample_size < 0:
            raise ValueError(f"measurement request sample_size must be a non-negative int, got {self.sample_size!r}")
        object.__setattr__(self, "sample_size", int(self.sample_size))


@runtime_checkable
class MeasurementSource(Protocol):
    """Injected producer of measurements. One method, no framework."""

    def measure(self, request: MeasurementRequest) -> Measurement:
        """Return the measurement for ``request``; never raise for a missing value."""


def _unavailable(
    request: MeasurementRequest,
    *,
    status: str,
    detail: str,
) -> Measurement:
    return Measurement(
        metric=request.metric,
        side=request.side,
        value=None,
        unit=request.unit,
        sample_size=0,
        harness_id=request.harness_id,
        evidence=EVIDENCE_UNVERIFIED,
        status=status,
        detail=detail,
    )


@dataclass
class ScriptedSource:
    """Hermetic measurement double: a fixed ``{(metric, side): value}`` table.

    No I/O, no clock, no subprocess. ``evidence`` defaults to ``"simulated"``
    because nothing ran; a test that stands in for a real harness passes
    ``evidence=EVIDENCE_MEASURED`` explicitly, and the decision function still
    requires everything else (integrity, gates, noise floor, rollback) before
    it can accept anything.
    """

    values: Mapping[tuple[str, str], float | int] = field(default_factory=dict)
    evidence: str = EVIDENCE_SIMULATED
    detail: str = "scripted value from the injected hermetic source; no benchmark process ran"
    calls: list[MeasurementRequest] = field(default_factory=list)

    def measure(self, request: MeasurementRequest) -> Measurement:
        self.calls.append(request)
        key = (request.metric, request.side)
        if key not in self.values:
            return _unavailable(
                request,
                status="unavailable",
                detail=f"scripted source has no value for metric {request.metric!r} on the {request.side} side; nothing was measured",
            )
        raw = self.values[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
            return _unavailable(
                request,
                status="error",
                detail=f"scripted source returned a non-numeric value {raw!r} for metric {request.metric!r} on the {request.side} side",
            )
        return Measurement(
            metric=request.metric,
            side=request.side,
            value=float(raw),
            unit=request.unit,
            sample_size=request.sample_size,
            harness_id=request.harness_id,
            evidence=self.evidence,
            status="measured",
            detail=self.detail,
        )


class UnavailableSource:
    """A source that measured nothing. Used as an explicit fail-closed stand-in."""

    reason: str = "no measurement source was injected for this side; nothing was measured"

    def measure(self, request: MeasurementRequest) -> Measurement:
        return _unavailable(request, status="unavailable", detail=f"{self.reason} (metric {request.metric!r}, side {request.side})")


@dataclass(frozen=True)
class CommandRun:
    """The real outcome of one argv execution (never a fabricated success)."""

    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str = ""
    truncated: bool = False

    @property
    def ran(self) -> bool:
        return self.exit_code is not None and not self.timed_out and not self.error

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "error": self.error,
            "truncated": self.truncated,
            "stdout_chars": len(self.stdout),
            "stderr_chars": len(self.stderr),
        }


def _cap(text: str, limit: int) -> tuple[str, bool]:
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value, False
    return value[:limit] + f"\n[truncated after {limit} characters]", True


def run_argv(
    argv: Sequence[str],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> CommandRun:
    """Run one command from an **argv list** with a fixed timeout.

    There is deliberately no ``shell=True`` and no string command form: shell
    metacharacters in an argument are data, never syntax. The command runs with
    no shell, output is capped, and every failure mode (timeout, spawn error,
    non-zero exit) is reported as itself.
    """

    if isinstance(argv, str):
        raise ValueError("run_argv requires an argv list, not a command string: this gate never interpolates a shell command line")
    if not isinstance(argv, Sequence) or not argv:
        raise ValueError("run_argv requires a non-empty argv sequence")
    args = tuple(str(item) for item in argv)
    if any(not item.strip() for item in args):
        raise ValueError("run_argv requires non-empty argv entries")
    timeout = float(timeout_seconds)
    if timeout <= 0 or not math.isfinite(timeout):
        raise ValueError(f"timeout_seconds must be a positive finite number, got {timeout_seconds!r}")
    cap = int(max_output_chars)
    if cap <= 0:
        raise ValueError(f"max_output_chars must be a positive int, got {max_output_chars!r}")
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, shell=False, operator-declared command
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout, _ = _cap(exc.stdout if isinstance(exc.stdout, str) else "", cap)
        stderr, _ = _cap(exc.stderr if isinstance(exc.stderr, str) else "", cap)
        return CommandRun(argv=args, exit_code=None, stdout=stdout, stderr=stderr, timed_out=True, error=f"timed out after {timeout}s")
    except (OSError, ValueError) as exc:
        return CommandRun(argv=args, exit_code=None, stdout="", stderr="", error=f"{type(exc).__name__}: {exc}")
    stdout, stdout_truncated = _cap(completed.stdout, cap)
    stderr, stderr_truncated = _cap(completed.stderr, cap)
    return CommandRun(argv=args, exit_code=int(completed.returncode), stdout=stdout, stderr=stderr, truncated=stdout_truncated or stderr_truncated)


def parse_measurement_output(text: str) -> float:
    """Parse a metric value from a command's stdout.

    Accepts a bare number (``0.87``) or a JSON object carrying a ``value``
    field. Anything else raises ``ValueError`` - an unparsable output is
    ``unavailable``, never ``0.0``.
    """

    raw = (text or "").strip()
    if not raw:
        raise ValueError("command produced no stdout to parse")
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    if isinstance(payload, Mapping) and "value" in payload:
        candidate = payload["value"]
    else:
        candidate = payload if isinstance(payload, (int, float)) and not isinstance(payload, bool) else raw
    if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
        try:
            candidate = float(str(candidate).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"could not parse a numeric measurement from {raw[:120]!r}") from exc
    number = float(candidate)
    if not math.isfinite(number):
        raise ValueError(f"measurement parsed to a non-finite value: {raw[:120]!r}")
    return number


@dataclass
class CommandSource:
    """Measurement source backed by one argv-listed command.

    The command is run for each requested metric/side; the caller owns the argv
    (the central owner wires the repository's own gates, e.g. the benchmark
    suite runner). Exit code 0 plus a parsable value is the *only* path to
    ``evidence=measured``.
    """

    argv: tuple[str, ...]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS
    cwd: str | None = None
    env: Mapping[str, str] | None = None
    parser: Callable[[str], float] = parse_measurement_output
    runs: list[CommandRun] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.argv, str):
            raise ValueError("CommandSource requires an argv list, not a command string")
        args = tuple(str(item) for item in self.argv)
        if not args:
            raise ValueError("CommandSource requires a non-empty argv list")
        self.argv = args
        self.timeout_seconds = float(self.timeout_seconds)
        self.max_output_chars = int(self.max_output_chars)

    @property
    def command_label(self) -> str:
        """A short, log-safe description of the command (argv, not a shell string)."""

        return " ".join(self.argv)

    def measure(self, request: MeasurementRequest) -> Measurement:
        run = run_argv(
            self.argv,
            timeout_seconds=self.timeout_seconds,
            cwd=self.cwd,
            env=self.env,
            max_output_chars=self.max_output_chars,
        )
        self.runs.append(run)
        label = self.command_label
        if run.timed_out:
            return _unavailable(request, status="unavailable", detail=f"measurement command timed out: {label} ({run.error}); no value was measured")
        if run.error:
            return _unavailable(request, status="error", detail=f"measurement command could not run: {label} ({run.error})")
        if run.exit_code != 0:
            return _unavailable(request, status="error", detail=f"measurement command failed: {label} exited {run.exit_code}; a non-zero exit is not a measurement of 0.0: {(run.stderr or run.stdout)[:200]!r}")
        try:
            value = self.parser(run.stdout)
        except ValueError as exc:
            return _unavailable(request, status="unavailable", detail=f"measurement command exited 0 but its output could not be parsed into a value: {label} ({exc}); no value was measured")
        detail = f"exit 0 from {label}; parsed {len(run.stdout)} stdout characters"
        if run.truncated:
            detail += " (output truncated at the configured cap)"
        return Measurement(
            metric=request.metric,
            side=request.side,
            value=value,
            unit=request.unit,
            sample_size=request.sample_size,
            harness_id=request.harness_id,
            evidence=EVIDENCE_MEASURED,
            status="measured",
            detail=detail,
        )


def collect_measurements(
    source: MeasurementSource | None,
    requests: Sequence[MeasurementRequest],
    *,
    missing_detail: str = "no measurement source was injected for this side; nothing was measured",
) -> tuple[Measurement, ...]:
    """Measure every request with ``source``, never raising on a bad source.

    A missing source produces an ``unavailable`` measurement carrying the real
    reason; a source that raises produces an ``error`` measurement. Neither is
    dropped, so the comparison layer sees the gap instead of a shorter list.
    """

    collected: list[Measurement] = []
    for request in requests:
        if source is None:
            collected.append(_unavailable(request, status="unavailable", detail=f"{missing_detail} (metric {request.metric!r}, side {request.side})"))
            continue
        try:
            result = source.measure(request)
        except Exception as exc:  # a broken source is an error, never a fake zero
            collected.append(_unavailable(request, status="error", detail=f"measurement source raised for metric {request.metric!r} on the {request.side} side: {type(exc).__name__}: {exc}"))
            continue
        if not isinstance(result, Measurement):
            collected.append(
                _unavailable(
                    request,
                    status="error",
                    detail=f"measurement source returned {type(result).__name__} instead of a Measurement for metric {request.metric!r} on the {request.side} side",
                )
            )
            continue
        collected.append(result)
    return tuple(collected)
