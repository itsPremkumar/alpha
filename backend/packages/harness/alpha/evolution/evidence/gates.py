"""Required precondition gates - the run must be green before the verdict.

A benchmark improvement is not a promotion. Before the decision function will
even look at a delta, these gates must be satisfied. Every gate is a callable
returning a :class:`~alpha.evolution.evidence.models.GateResult` with a closed
status (``pass``/``fail``/``unavailable``/``error``) and a **mandatory** detail
string, and the gate list is injected - the central owner decides which
repository gates (ruff, ``audit_dup_expressions``, ``prod_check``, ...) must
run, and this module never assumes.

Two design points carry most of the weight:

* **An unavailable required gate blocks.** ``unavailable`` and ``error`` are not
  soft passes: :func:`alpha.evolution.evidence.decision.decide_evidence_verdict`
  maps them to ``insufficient_evidence``. A gate that did not run has not
  passed.
* **A gate that cannot explain itself is an error.** A gate returning a
  non-``GateResult``, raising, or disclosing an empty reason is converted to an
  ``error`` result carrying the real text, so silence can never be read as a
  pass.

Shipped gates:

``evaluator_integrity``
    Wraps the report from :mod:`alpha.evolution.evidence.integrity`.
``reproducibility``
    Same inputs must produce the same declared output, via an **injected**
    probe. No probe means ``unavailable`` - reproducibility was never checked,
    which is not a pass.
``rollback_path``
    Verifies a rollback path exists (and, when a probe is injected, that it
    works). An irreversible proposal has no rollback path and fails.
``blast_radius``
    The change must fit inside the configured blast radius.
``command_gate(...)``
    Runs one argv-listed repository gate (no shell string) and maps exit code
    0 / non-zero / timeout / spawn failure onto pass / fail / unavailable /
    error.

Nothing here reads a clock, a global, or the network. Gate durations come from
the injected monotonic clock on the context, so a test is deterministic.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from alpha.evolution.evidence.config import (
    GATE_BLAST_RADIUS,
    GATE_EVALUATOR_INTEGRITY,
    GATE_REPRODUCIBILITY,
    GATE_ROLLBACK_PATH,
)
from alpha.evolution.evidence.measure import run_argv
from alpha.evolution.evidence.models import EvaluationIntegrityReport, GateResult, Proposal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from alpha.evolution.evidence.models import ComparisonReport

__all__ = [
    "GATE_BLAST_RADIUS",
    "GATE_EVALUATOR_INTEGRITY",
    "GATE_REPRODUCIBILITY",
    "GATE_ROLLBACK_PATH",
    "Gate",
    "GateContext",
    "ProbeOutcome",
    "blast_radius_gate",
    "build_repo_precondition_gates",
    "command_gate",
    "default_gate_registry",
    "evaluator_integrity_gate",
    "reproducibility_gate",
    "rollback_path_gate",
    "run_required_gates",
]

#: Name of the built-in gates, so callers cannot typo a required gate name.
REPO_PRECONDITION_GATE_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("audit_dup_expressions", ("scripts/audit_dup_expressions.py",)),
    ("audit_env_wiring", ("scripts/audit_env_wiring.py",)),
    ("check_agent_guidance", ("scripts/check_agent_guidance.py",)),
    ("prod_check", ("scripts/prod_check.py",)),
)


@dataclass(frozen=True)
class ProbeOutcome:
    """Result of an injected probe: ``ok=True/False`` or ``None`` = unavailable."""

    ok: bool | None
    detail: str

    def __post_init__(self) -> None:
        if self.ok is not None:
            object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "detail", str(self.detail or ""))

    @property
    def unavailable(self) -> bool:
        return self.ok is None


#: A probe is any callable the host injects (reproducibility, rollback, ...).
Probe = Callable[[Proposal], ProbeOutcome]


@runtime_checkable
class Gate(Protocol):
    """One gate: a callable that turns a context into an honest result."""

    def __call__(self, context: GateContext) -> GateResult: ...


@dataclass(frozen=True)
class GateContext:
    """Everything a gate is allowed to see. No globals, no hidden state.

    Attributes:
        proposal: The change under evaluation.
        config: The gate's configuration (read for thresholds/timeouts).
        integrity: The evaluator-integrity report, when one was produced.
        comparison: The comparison report, when measurements were collected.
        reproducibility_probe: Injected "same inputs -> same output" probe.
        rollback_probe: Injected rollback verification probe.
        repo_root: Repository root for command gates (optional).
        clock: Injected monotonic clock used only for gate durations.
    """

    proposal: Proposal
    config: Any
    integrity: EvaluationIntegrityReport | None = None
    comparison: ComparisonReport | None = None
    reproducibility_probe: Probe | None = None
    rollback_probe: Probe | None = None
    repo_root: str | None = None
    clock: Callable[[], float] = time.perf_counter

    def __post_init__(self) -> None:
        if not callable(self.clock):
            raise TypeError("GateContext.clock must be a callable returning monotonic seconds")

    def gate_context(self, **_overrides: Any) -> GateContext:
        """Return a copy with selected fields replaced (frozen dataclass helper)."""

        return replace(self, **_overrides)


def _duration_ms(context: GateContext, started: float) -> float:
    try:
        elapsed = float(context.clock()) - float(started)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, elapsed * 1000.0)


def evaluator_integrity_gate(context: GateContext) -> GateResult:
    """Pass only when the evaluator-integrity report is ``clean``.

    ``suspect``/``compromised`` fail (with every finding's path + reason
    verbatim); ``error`` stays an ``error`` so the decision layer reports
    insufficient evidence rather than a rejection.
    """

    report = context.integrity
    if report is None:
        return GateResult(name=GATE_EVALUATOR_INTEGRITY, status="unavailable", detail="no evaluator-integrity report was produced; the judge was never checked, which is not a pass")
    if report.status == "clean":
        return GateResult(name=GATE_EVALUATOR_INTEGRITY, status="pass", detail=report.reason or "no evaluation surface was touched by this change")
    if report.status == "error":
        return GateResult(name=GATE_EVALUATOR_INTEGRITY, status="error", detail=report.reason or "the evaluator-integrity check could not run")
    findings = "; ".join(f"{finding.path}: {finding.reason}" for finding in report.findings)
    return GateResult(name=GATE_EVALUATOR_INTEGRITY, status="fail", detail=f"evaluator integrity is {report.status!r}: {findings or report.reason}")


def reproducibility_gate(context: GateContext) -> GateResult:
    """Verify "same inputs -> same declared output" through an injected probe."""

    probe = context.reproducibility_probe
    if probe is None:
        return GateResult(name=GATE_REPRODUCIBILITY, status="unavailable", detail="no reproducibility probe was injected; the change was never re-run, which is not a pass")
    outcome = probe(context.proposal)
    if not isinstance(outcome, ProbeOutcome):
        return GateResult(name=GATE_REPRODUCIBILITY, status="error", detail=f"the reproducibility probe returned {type(outcome).__name__} instead of ProbeOutcome")
    if outcome.unavailable:
        return GateResult(name=GATE_REPRODUCIBILITY, status="unavailable", detail=outcome.detail or "the reproducibility probe could not decide; nothing was re-run")
    if outcome.ok:
        return GateResult(name=GATE_REPRODUCIBILITY, status="pass", detail=outcome.detail or "the injected probe reproduced the declared output from the same inputs")
    return GateResult(name=GATE_REPRODUCIBILITY, status="fail", detail=outcome.detail or "the change did not reproduce its declared output from the same inputs")


def rollback_path_gate(context: GateContext) -> GateResult:
    """Verify that a rollback path exists (and, when probed, that it works)."""

    proposal = context.proposal
    if not proposal.reversible:
        return GateResult(name=GATE_ROLLBACK_PATH, status="fail", detail=f"proposal {proposal.id!r} is declared irreversible and therefore carries no rollback path")
    probe = context.rollback_probe
    if probe is not None:
        outcome = probe(proposal)
        if not isinstance(outcome, ProbeOutcome):
            return GateResult(name=GATE_ROLLBACK_PATH, status="error", detail=f"the rollback probe returned {type(outcome).__name__} instead of ProbeOutcome")
        if outcome.unavailable:
            return GateResult(name=GATE_ROLLBACK_PATH, status="unavailable", detail=outcome.detail or "the rollback probe could not decide; rollback was never verified")
        if outcome.ok:
            return GateResult(name=GATE_ROLLBACK_PATH, status="pass", detail=outcome.detail or f"the injected probe verified rollback reference {proposal.rollback_ref!r}")
        return GateResult(name=GATE_ROLLBACK_PATH, status="fail", detail=outcome.detail or "the rollback probe could not restore the previous state")
    if proposal.rollback_ref:
        return GateResult(name=GATE_ROLLBACK_PATH, status="pass", detail=f"proposal declares rollback reference {proposal.rollback_ref!r} (no rollback probe was injected, so only the reference is verified)")
    return GateResult(name=GATE_ROLLBACK_PATH, status="fail", detail=f"proposal {proposal.id!r} is reversible but declares no rollback reference; a promotion without a rollback path is refused")


def blast_radius_gate(context: GateContext) -> GateResult:
    """Refuse a change whose effective blast radius exceeds the configured limit."""

    proposal = context.proposal
    maximum = int(getattr(context.config, "max_touched_paths", 0) or 0)
    radius = proposal.effective_blast_radius
    if radius <= maximum:
        return GateResult(name=GATE_BLAST_RADIUS, status="pass", detail=f"effective blast radius {radius} is within the configured maximum {maximum}")
    return GateResult(name=GATE_BLAST_RADIUS, status="fail", detail=f"effective blast radius {radius} exceeds the configured maximum {maximum} ({len(proposal.touched_paths)} touched paths)")


def default_gate_registry() -> dict[str, Gate]:
    """The four built-in gates, keyed by the names ``required_gates`` uses."""

    return {
        GATE_EVALUATOR_INTEGRITY: evaluator_integrity_gate,
        GATE_REPRODUCIBILITY: reproducibility_gate,
        GATE_ROLLBACK_PATH: rollback_path_gate,
        GATE_BLAST_RADIUS: blast_radius_gate,
    }


def command_gate(
    name: str,
    argv: Sequence[str],
    *,
    timeout_seconds: float | None = None,
    max_output_chars: int | None = None,
    cwd: str | None = None,
) -> Gate:
    """Build a gate that runs one argv-listed command and reports its exit code.

    There is no shell string form: the command is an argv list, so a path with
    a space or a ``;`` in it is data. Exit 0 passes, a non-zero exit fails, a
    timeout is ``unavailable`` and a spawn failure is ``error`` - none of which
    can be mistaken for a pass.
    """

    args = tuple(str(item) for item in argv)
    if not name.strip() or not args:
        raise ValueError("command_gate requires a non-empty name and argv list")

    def _gate(context: GateContext) -> GateResult:
        limit = float(timeout_seconds if timeout_seconds is not None else getattr(context.config, "command_timeout_seconds", 600.0))
        cap = int(max_output_chars if max_output_chars is not None else getattr(context.config, "max_output_chars", 4000))
        started = context.clock()
        run = run_argv(args, timeout_seconds=limit, cwd=cwd or context.repo_root, max_output_chars=cap)
        detail = f"{' '.join(args)} -> {run.to_dict()}"
        if run.timed_out:
            status = "unavailable"
        elif run.error:
            status = "error"
        elif run.exit_code == 0:
            status = "pass"
        else:
            status = "fail"
        return GateResult(name=name, status=status, detail=detail, duration_ms=_duration_ms(context, started))

    _gate.gate_name = name  # type: ignore[attr-defined]
    return _gate


def build_repo_precondition_gates(
    *,
    python_executable: str,
    repo_root: str,
    timeout_seconds: float = 600.0,
    max_output_chars: int = 4000,
) -> dict[str, Gate]:
    """The repository's own gates, as injectable argv command gates.

    The four scripts named in the repo's gate list - ``audit_dup_expressions``,
    ``audit_env_wiring``, ``check_agent_guidance`` and ``prod_check`` - run from
    the repository root with the current interpreter. They are *not* added to
    ``required_gates`` by default: the operator decides which ones must be
    green for a self-change (see the integration patch in the report).
    """

    gates: dict[str, Gate] = {}
    for name, script in REPO_PRECONDITION_GATE_SPECS:
        gates[name] = command_gate(
            name,
            (python_executable, *script),
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
            cwd=repo_root,
        )
    return gates


def run_required_gates(
    context: GateContext,
    gates: Mapping[str, Gate] | None = None,
    *,
    required: Sequence[str] | None = None,
) -> tuple[GateResult, ...]:
    """Run every required gate, in order, and return their real results.

    * a required gate with no registered implementation is an ``error`` (the
      gate cannot be satisfied by not existing);
    * a gate that raises, returns a non-``GateResult``, or returns an empty
      detail is converted into an ``error`` result carrying the real text;
    * every required gate runs even after an earlier failure, so the report
      shows the full picture instead of stopping at the first problem.
    """

    registry = dict(gates) if gates is not None else default_gate_registry()
    names = tuple(required) if required is not None else tuple(getattr(context.config, "required_gates", ()))
    results: list[GateResult] = []
    for name in names:
        gate = registry.get(name)
        if gate is None:
            results.append(GateResult(name=name, status="error", detail=f"required gate {name!r} has no registered implementation; an unimplemented gate cannot pass"))
            continue
        started = context.clock()
        try:
            raw = gate(context)
        except Exception as exc:  # a raising gate is an error, never a pass
            results.append(GateResult(name=name, status="error", detail=f"gate {name!r} raised: {type(exc).__name__}: {exc}", duration_ms=_duration_ms(context, started)))
            continue
        duration = _duration_ms(context, started)
        if not isinstance(raw, GateResult):
            results.append(GateResult(name=name, status="error", detail=f"gate {name!r} returned {type(raw).__name__} instead of a GateResult", duration_ms=duration))
            continue
        if not raw.detail.strip():
            results.append(GateResult(name=name, status="error", detail=f"gate {name!r} returned no detail; a verdict without a reason is not reviewable", duration_ms=duration))
            continue
        results.append(raw if raw.duration_ms else GateResult(name=raw.name, status=raw.status, detail=raw.detail, duration_ms=duration))
    return tuple(results)
