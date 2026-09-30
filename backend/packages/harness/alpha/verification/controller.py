"""The controller: run the tests, read the actual failure, fix, re-run, decide.

This is the component the research audit found missing. Alpha owned the verify
tools, the evidence binder, and the budgeted retry; nothing connected them, so
the agent *noticed* a missing verification and *asked* for one. The gap was never
a capability, it was an operator.

## The state machine

``step()`` advances exactly one transition per call and returns what the caller
should do, so the loop is driven from here rather than from whatever the caller
feels like doing next::

    IDLE ──execute──▶ ASSESSING ──pass──▶ SETTLED(VERIFIED)
                        │
                        ├─undecided──▶ SETTLED(UNVERIFIED / evidence_unanchored)
                        │
                        └─fail──▶ budget left? ──no──▶ SETTLED(UNVERIFIED / budget_exhausted)
                                    │yes
                                    ▼
                              CAPTURING_SURFACE ──▶ DISPATCHING_REPAIR ──▶ AWAITING_REPAIR
                                                                             │
                            ┌────────────────────────────────────────────────┤
                            │ declined                                      │ completed
                            ▼                                                ▼
                   SETTLED(FAILED)                                  COMPARING_SURFACE
                                                                            │
                                            reduced/indeterminate ──yes──▶ SETTLED(UNVERIFIED)
                                                                            │no
                                                                            ▼
                                                                       EXECUTING (loop)

The caller supplies only two things it genuinely owns: a **surface reader** (the
thread's workspace reader, so the comparison sees the bytes the tests ran
against) and a **repair outcome** (the model came back, or it declined). Every
decision — whether to repair, whether to accept the repair, when to stop — is
made here, and ``step()`` refuses to move past a settled loop.

## The attempt budget

The budget bounds **repairs**, not executions: the first verification run is
free, and each dispatch spends one. It is checked *before* every dispatch, so an
exhausted budget is a state the loop settles into rather than an overrun it
notices afterwards. Exhaustion is reported UNVERIFIED, and the last determinate
reading is carried in the report — see :mod:`alpha.verification.contract` for
why exhaustion is not promoted to FAILED.

## What this will not do

It does not start, adopt, cancel, or terminate a run. ``RunManager`` remains the
sole run lifecycle owner and ``SafeRunRecoveryService`` the only safe-continuation
authority; the controller drives a loop *within* a run that already exists, and
its only outward effect is a directive the calling middleware acts on. A design
that needed a parallel run path is a design that should not be built; this one
reports honestly that it was not needed.

It also cannot make a failing test pass by removing the test. :mod:`alpha.verification.surface`
captures the surface before and after every repair and refuses a reduced one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.verification.budget import AttemptBudget
from alpha.verification.contract import (
    LoopState,
    RecordedReading,
    UnverifiedReason,
    VerificationOutcome,
    VerificationReport,
    attempt_trail_entry,
    bound_text,
    render_report,
)
from alpha.verification.execution import CommandExecutor, RecordedExecution
from alpha.verification.surface import (
    TestSurface,
    capture_surface,
    compare_surfaces,
    surface_from_changes,
)

logger = logging.getLogger(__name__)

__all__ = [
    "LoopDirective",
    "RepairOutcome",
    "VerificationController",
]

#: The criterion form the evidence binder already understands. The controller
#: routes its reading through that checker rather than re-implementing it, so
#: "VERIFIED" means exactly what it means everywhere else in Alpha: the binder
#: accepted a real recorded execution.
_CRITERION_PREFIX = "tests_passed:"


class RepairOutcome(StrEnum):
    """What happened to a dispatched repair, as reported by the caller.

    ``COMPLETED`` means the model took its turn and the controller may compare
    surfaces. ``DECLINED`` means the caller decided no repair would be attempted
    — the loop then settles FAILED, because that is a *decision* about a
    determinate negative rather than an unanswered question.
    """

    COMPLETED = "completed"
    DECLINED = "declined"


@dataclass(frozen=True)
class LoopDirective:
    """What the caller should do after one transition.

    ``REPAIR`` is the only directive with work in it, and it carries the **actual
    recorded failure output** — the bytes the test runner printed, bounded, with
    the truncation flag attached. Not a summary of it, not a restatement: the
    whole value of the loop is that the model sees what actually happened.
    """

    kind: str
    prompt: str = ""
    state: LoopState = LoopState.IDLE
    report: VerificationReport | None = None
    budget_remaining: int = 0

    @property
    def settled(self) -> bool:
        return self.kind == "settled"

    @property
    def outcome(self) -> VerificationOutcome | None:
        return self.report.outcome if self.report is not None else None


@dataclass
class _Progress:
    """Everything the loop accumulates, so a report can be built at settlement."""

    trail: list[Mapping[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    last_reading: RecordedReading | None = None


class VerificationController:
    """A bounded, honest verify -> read failure -> fix -> re-run loop.

    Construct one per run. It holds the attempt budget, the captured test
    surface, and the last reading; it holds no run lifecycle, no model client,
    and no thread state of its own.
    """

    def __init__(
        self,
        command: str,
        *,
        executor: CommandExecutor,
        attempts_allowed: int | None = None,
        failure_evidence_chars: int | None = None,
        repair_enabled: bool = True,
        thread_data: Mapping[str, Any] | None = None,
        runtime: Any = None,
        config: Any = None,
    ) -> None:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("a verification loop needs a command to run")
        self._config = config if config is not None else _resolve_config()
        self._enabled = bool(getattr(self._config, "enabled", True))
        self._command = command
        self._executor = executor
        self._budget = AttemptBudget(total=attempts_allowed if attempts_allowed is not None else int(self._config.max_attempts))
        self._failure_evidence_chars = failure_evidence_chars if failure_evidence_chars is not None else int(self._config.failure_evidence_chars)
        self._repair_enabled = repair_enabled
        self._thread_data = thread_data
        self._runtime = runtime
        self._state = LoopState.IDLE
        self._progress = _Progress()
        self._report: VerificationReport | None = None
        self._watched_paths: tuple[str, ...] = ()
        self._surface_before: TestSurface | None = None
        self._pending_prompt: str = ""
        self._pending_truncated: bool = False
        self._pending_tool_call_id: str = ""
        self._pending_detail: str = ""

    # -- introspection ---------------------------------------------------

    @property
    def state(self) -> LoopState:
        return self._state

    @property
    def settled(self) -> bool:
        return self._report is not None

    @property
    def report(self) -> VerificationReport | None:
        """The final report, or ``None`` while the loop is still running."""
        return self._report

    @property
    def attempts_remaining(self) -> int:
        return self._budget.remaining

    @property
    def watched_paths(self) -> tuple[str, ...]:
        """The test files the loop is guarding, resolved from the run's changes."""
        return self._watched_paths

    def watch(self, changed_paths: Any) -> tuple[str, ...]:
        """Set the test surface to guard, from the paths the run has changed.

        Must be called before the first repair, because the *before* capture is
        the whole point: a surface captured after the repair has already been
        changed is a surface that cannot detect the change.
        """
        self._watched_paths = surface_from_changes(changed_paths or ())
        return self._watched_paths

    # -- the loop --------------------------------------------------------

    async def step(
        self,
        *,
        surface_reader: Callable[[str], str | None] | None = None,
        repair_outcome: RepairOutcome | None = None,
    ) -> LoopDirective:
        """Advance the loop by one transition and return the caller's directive.

        *surface_reader* returns a file's current text or ``None`` when it does
        not exist; the caller passes the thread's workspace reader so both
        captures see the bytes the tests ran against.

        *repair_outcome* is required only when the loop is ``AWAITING_REPAIR``.
        Calling ``step()`` in any other state without one is a no-op that
        redelivers the current directive, so a caller that polls does not
        accidentally skip a transition.
        """
        if self._report is not None:
            return LoopDirective(kind="settled", state=self._state, report=self._report, budget_remaining=0)

        if not self._enabled:
            # A disabled loop reports rather than vanishes. A caller that wired
            # the seam and then turned the feature off gets an UNVERIFIED it can
            # see, not a silence it has to interpret. The reason carries the
            # "disabled" fact; the state stays SETTLED because the loop is done,
            # which is what a terminal directive means.
            return self._settle_unverified(
                UnverifiedReason.LOOP_DISABLED,
                summary="the verification loop is disabled by configuration; nothing was verified",
                notes=("set verification_loop.enabled: true to operate it",),
            )

        # Only three states are observable from here. EXECUTING, ASSESSING,
        # CAPTURING_SURFACE, DISPATCHING_REPAIR and COMPARING_SURFACE are
        # internal: a transition that entered one of them always leaves it before
        # returning a directive, so reaching one here means the machine lost a
        # transition. That is a bug in the state machine, not a caller error, and
        # raising is the honest response — a loop that silently restarts a
        # transition is a loop that can run a command twice without saying so.
        match self._state:
            case LoopState.IDLE:
                return await self._execute(surface_reader=surface_reader)
            case LoopState.AWAITING_REPAIR:
                if repair_outcome is None:
                    return self._repair_directive()
                return await self._finish_repair(repair_outcome, surface_reader=surface_reader)
            case _:
                raise RuntimeError(f"verification loop is in an internal state {self._state.value!r}; this is a bug in the state machine, not a caller error")

    # -- transitions -----------------------------------------------------

    async def _execute(self, *, surface_reader: Callable[[str], str | None] | None = None) -> LoopDirective:
        """Run the command for real and read what came back.

        *surface_reader* is threaded through because the first verification run
        may also be the one that discovers a failure worth repairing, and the
        repair's *before* capture has to happen in the same step that noticed it
        — a surface captured a step later would be captured after whatever the
        caller did in between.
        """
        self._state = LoopState.EXECUTING
        try:
            record = await self._executor.execute(self._command)
        except Exception as exc:
            logger.warning("Verification loop could not execute %r: %s", self._command, exc, exc_info=True)
            return self._settle_unverified(
                UnverifiedReason.EXECUTION_ERROR,
                summary=f"the verification command could not be executed ({type(exc).__name__})",
                notes=("no recorded execution exists, so nothing can be read from one",),
            )
        if not isinstance(record, RecordedExecution):
            # A synthesised transcript cannot be sealed into a RecordedExecution,
            # so reaching this branch means an executor contract was broken. It
            # is not evidence and it is not a pass.
            logger.error("Verification loop executor returned %s, not a RecordedExecution; treating as no evidence", type(record).__name__)
            return self._settle_unverified(
                UnverifiedReason.NO_RECORDED_EXECUTION,
                summary="the executor returned something that is not a recorded execution",
                notes=("a recorded execution is the only thing this loop will read; see alpha.verification.execution",),
            )
        self._state = LoopState.ASSESSING
        return await self._assess(record=record, surface_reader=surface_reader)

    async def _assess(
        self,
        *,
        record: RecordedExecution | None,
        surface_reader: Callable[[str], str | None] | None = None,
    ) -> LoopDirective:
        """Read the recorded execution through the existing evidence binder."""
        if record is None:
            return await self._execute(surface_reader=surface_reader)

        reading = self._read(record)
        self._progress.last_reading = reading

        if reading.reading == "pass":
            self._progress.trail.append(attempt_trail_entry(len(self._progress.trail), reading, repaired=bool(self._progress.trail), surface_reduced=False))
            return self._settle(
                evidence_outcome="pass",
                reading=reading,
                summary="the evidence binder accepted a recorded passing execution",
            )

        if reading.reading == "undecided":
            self._progress.trail.append(attempt_trail_entry(len(self._progress.trail), reading, repaired=bool(self._progress.trail), surface_reduced=False))
            return self._settle_unverified(
                UnverifiedReason.EVIDENCE_UNANCHORED,
                summary="a command ran, but its evidence could not be anchored to a pass or a fail",
                reading=reading,
                notes=(f"binder detail: {reading.detail}",),
            )

        # A determinate failure. Repair is optional, and the decision about
        # whether to try is made here, once, with the budget visible.
        if not self._repair_enabled:
            return self._settle(
                evidence_outcome="fail",
                reading=reading,
                decided=True,
                summary="the recorded execution did not pass and no repair was attempted",
            )
        if self._budget.exhausted:
            self._progress.trail.append(attempt_trail_entry(len(self._progress.trail), reading, repaired=bool(self._progress.trail), surface_reduced=False))
            return self._settle_unverified(
                UnverifiedReason.BUDGET_EXHAUSTED,
                summary=f"the attempt budget of {self._budget.total} ran out; the last recorded run did not pass",
                reading=reading,
                notes=(f"binder detail: {reading.detail}",),
            )
        if not self._watched_paths:
            self._progress.trail.append(attempt_trail_entry(len(self._progress.trail), reading, repaired=bool(self._progress.trail), surface_reduced=False))
            return self._settle_unverified(
                UnverifiedReason.SURFACE_UNAVAILABLE,
                summary="the run named no test files, so a repair could not be checked for coverage reduction",
                reading=reading,
                notes=("call watch() with the run's changed paths before the first repair",),
            )

        # Capture the surface *before* the repair, then hand the real failure over.
        self._state = LoopState.CAPTURING_SURFACE
        self._surface_before = capture_surface(self._watched_paths, surface_reader)
        if not self._surface_before.complete:
            return self._settle_unverified(
                UnverifiedReason.SURFACE_INDETERMINATE,
                summary="the test surface could not be captured completely, so a repair could not be checked",
                reading=reading,
                notes=tuple(f"{path} could not be read" for path, entry in sorted(self._surface_before.files.items()) if not entry.readable) or ("the surface capture was truncated",),
            )

        self._state = LoopState.DISPATCHING_REPAIR
        # Spend the attempt BEFORE rendering, so the prompt's "attempt N of M"
        # describes the attempt it is actually announcing. Rendering first and
        # consuming after would tell the model it had one try left on the turn
        # that spent the last one.
        self._budget.consume()
        text, truncated = bound_text(reading.output, self._failure_evidence_chars)
        self._pending_prompt = self._render_repair_prompt(reading=reading, failure_text=text, truncated=truncated)
        self._pending_truncated = truncated
        self._pending_tool_call_id = reading.tool_call_id
        self._pending_detail = reading.detail
        self._state = LoopState.AWAITING_REPAIR
        return self._repair_directive()

    async def _finish_repair(
        self,
        outcome: RepairOutcome,
        *,
        surface_reader: Callable[[str], str | None] | None,
    ) -> LoopDirective:
        """Close out a dispatched repair: compare the surface, then re-run or stop."""
        if outcome is RepairOutcome.DECLINED:
            reading = self._progress.last_reading
            return self._settle(
                evidence_outcome="fail",
                reading=reading,
                decided=True,
                summary="the recorded execution did not pass and the repair was declined",
            )

        self._state = LoopState.COMPARING_SURFACE
        assert self._surface_before is not None  # set in _assess before any dispatch
        after = capture_surface(self._watched_paths, surface_reader)
        comparison = compare_surfaces(self._surface_before, after)
        self._surface_before = None
        if comparison.blocking:
            reason = UnverifiedReason.WEAKENED_TEST if comparison.reduced else UnverifiedReason.SURFACE_INDETERMINATE
            reading = self._progress.last_reading
            self._progress.trail.append(
                attempt_trail_entry(
                    len(self._progress.trail),
                    _synthetic_reading(reading, detail=comparison.render()),
                    repaired=True,
                    surface_reduced=True,
                )
            )
            return self._settle_unverified(
                reason,
                summary="the repair was refused: it reduced the test surface",
                reading=reading,
                notes=comparison.reasons,
            )

        self._progress.notes.append(f"repair attempt accepted; {comparison.render()}")
        self._state = LoopState.EXECUTING
        return await self._execute(surface_reader=surface_reader)

    # -- reading ---------------------------------------------------------

    def _read(self, record: RecordedExecution) -> RecordedReading:
        """Route one recorded execution through the existing evidence binder.

        The controller contributes no acceptance logic of its own. If the binder
        cannot decide, the reading is ``undecided`` and the loop settles
        UNVERIFIED — it never fills the gap with an opinion.

        Lazy import: ``alpha.subagents`` pulls the executor and the lead-agent
        chain on first touch, and this module is imported by the middleware stack
        (same precedent as ``acceptance_checks`` importing ``report_contract``).
        """
        from alpha.subagents.acceptance_checks import check_acceptance_criteria

        verdict = check_acceptance_criteria(
            [f"{_CRITERION_PREFIX}{self._command}"],
            runtime=self._runtime,
            thread_data=self._thread_data,
            bash_executions=[record.to_acceptance_execution()],
            # The checker resolves its file-leaf probes eagerly when they are
            # omitted, and resolving them imports the whole sandbox provider
            # stack. This controller only ever raises a ``tests_passed:``
            # criterion, which reads no file, so it hands over the *same*
            # production callables as lazy delegates rather than as stubs: if a
            # future criterion family does read a file, these resolve to the real
            # probes instead of quietly doing nothing. Measured in this tree the
            # eager resolution costs ~113s of import time for code this path
            # cannot reach.
            content_reader=_lazy_callable("alpha.sandbox.tools", "read_current_file_content"),
            size_prober=_lazy_callable("alpha.subagents.acceptance_checks", "_probe_file_size"),
            readable_prober=_lazy_callable("alpha.subagents.acceptance_checks", "_probe_file_readable"),
        )
        if not verdict or not verdict.get("leaves"):
            return RecordedReading(
                reading="undecided",
                detail="the acceptance checker produced no verdict",
                tool_call_id=record.tool_call_id,
                command=record.command,
                output=record.output,
                output_truncated=False,
                status=record.status,
            )
        leaf = verdict["leaves"][0]
        checked = bool(leaf.get("checked"))
        holds = bool(leaf.get("holds"))
        reading = "pass" if checked and holds else "fail" if checked else "undecided"
        return RecordedReading(
            reading=reading,
            detail=str(leaf.get("detail") or ""),
            tool_call_id=record.tool_call_id,
            command=record.command,
            output=record.output,
            output_truncated=record.command_truncated,
            status=record.status,
        )

    def _render_repair_prompt(self, *, reading: RecordedReading, failure_text: str, truncated: bool) -> str:
        """The text handed to the repairing model.

        It carries the recorded output itself. A summary would be a paraphrase of
        the evidence, which is the one thing this loop exists to avoid — the model
        has to see what the runner actually printed, including the parts that look
        like noise.
        """
        budget_line = f"This was repair attempt {self._budget.spent} of {self._budget.total}. {'This is the last one.' if self._budget.remaining == 0 else f'{self._budget.remaining} remain after it.'}"
        truncation_line = "\n(The recorded output above was truncated to fit; the full run may have printed more.)" if truncated else ""
        return (
            "The verification command ran and did not pass. Here is the recorded output of that run, "
            "exactly as the runner printed it.\n\n"
            f"Command: {reading.command}\n"
            f"Recorded status: {reading.status}\n"
            f"Binder reading: {reading.detail}\n\n"
            f"--- recorded test output ---\n{failure_text}\n--- end recorded test output ---{truncation_line}\n\n"
            "Diagnose the failure from that output and fix the implementation it names. "
            "Do not weaken, delete, skip, or loosen the tests to make them pass: the change you are making is "
            "checked against the test surface before and after, and a repair that reduced it is refused and "
            "reported as UNVERIFIED. If the test itself is what is wrong, say so explicitly in your next message "
            "rather than editing it.\n\n"
            f"{budget_line}"
        )

    def _repair_directive(self) -> LoopDirective:
        return LoopDirective(
            kind="repair",
            prompt=self._pending_prompt,
            state=self._state,
            budget_remaining=self._budget.remaining,
        )

    # -- settlement ------------------------------------------------------

    def _settle(
        self,
        *,
        evidence_outcome: str,
        reading: RecordedReading | None,
        summary: str,
        decided: bool = False,
        notes: tuple[str, ...] = (),
    ) -> LoopDirective:
        self._state = LoopState.SETTLED
        self._report = self._build_report(
            evidence_outcome=evidence_outcome,
            reason=None,
            reading=reading,
            summary=summary,
            decided=decided,
            notes=notes,
        )
        return LoopDirective(kind="settled", state=self._state, report=self._report, budget_remaining=0)

    def _settle_unverified(
        self,
        reason: UnverifiedReason,
        *,
        summary: str,
        reading: RecordedReading | None = None,
        notes: tuple[str, ...] = (),
    ) -> LoopDirective:
        """Settle as UNVERIFIED, which is the default whenever nothing is proven.

        Every parameter that can be the cause of an undetermined verdict is a
        reason, and there is no path into this method without one. That is the
        point: UNVERIFIED is a claim about *why*, never a shrug.
        """
        self._state = LoopState.SETTLED
        self._report = self._build_report(
            evidence_outcome="undecided" if reading is None or reading.reading == "undecided" else "fail",
            reason=reason,
            reading=reading,
            summary=summary,
            decided=False,
            notes=notes,
        )
        return LoopDirective(kind="settled", state=self._state, report=self._report, budget_remaining=0)

    def _build_report(
        self,
        *,
        evidence_outcome: str,
        reason: UnverifiedReason | None,
        reading: RecordedReading | None,
        summary: str,
        decided: bool,
        notes: tuple[str, ...],
    ) -> VerificationReport:
        text = ""
        truncated = False
        if reading is not None:
            text, truncated = bound_text(reading.output, self._failure_evidence_chars)
        if reason is not None and decided:
            # Defensive: an exhausted budget can never be a decided loop. The
            # report enforces this too, but a controller that settled a decided
            # failure with a budget reason would be reporting a conclusion it
            # did not reach.
            decided = False
        return VerificationReport(
            evidence_outcome=evidence_outcome,
            reason=reason,
            command=self._command,
            attempts_allowed=self._budget.total,
            attempts_used=self._budget.spent,
            attempts_remaining=self._budget.remaining,
            decided=decided,
            last_reading=reading.reading if reading is not None else "none",
            evidence_text=text,
            evidence_truncated=truncated,
            run_finished=False,
            summary=summary,
            attempts=tuple(dict(entry) for entry in self._progress.trail),
            notes=tuple([*self._progress.notes, *notes]),
            state=self._state,
        )


def _resolve_config() -> Any:
    """Read the operator policy, falling back to the shipped defaults.

    A malformed or missing config must not stop a loop from reporting something
    honest, so a resolution failure degrades to defaults rather than raising: the
    controller's own guards (budget, surface comparison, fail-closed outcomes)
    do not depend on operator input.
    """
    try:
        from alpha.config.verification_loop_config import resolve_verification_loop_config

        return resolve_verification_loop_config()
    except Exception:  # pragma: no cover - the defaults are always importable
        logger.warning("Verification loop config could not be resolved; using defaults", exc_info=True)
        from alpha.config.verification_loop_config import VerificationLoopConfig

        return VerificationLoopConfig()


def _lazy_callable(module_path: str, attribute: str) -> Callable[..., Any]:
    """A deferred reference to a production callable.

    Used only to avoid paying an import a call path cannot use. It is a *deferral*,
    not a substitution: the first call resolves and invokes the real function, so
    a caller that does reach the code gets production behaviour.
    """

    def resolve(*args: Any, **kwargs: Any) -> Any:
        import importlib

        return getattr(importlib.import_module(module_path), attribute)(*args, **kwargs)

    resolve.__name__ = attribute
    resolve.__qualname__ = f"{module_path}.{attribute}"
    resolve.__doc__ = f"Deferred reference to the production {module_path}.{attribute}."
    return resolve


def _synthetic_reading(reading: RecordedReading | None, *, detail: str) -> RecordedReading:
    """A reading row for an attempt that ended in a refusal rather than a run.

    Carries the previous reading's provenance so the trail still names the call
    the row is about, and the refusal text as its detail.
    """
    if reading is not None:
        return RecordedReading(
            reading=reading.reading,
            detail=detail,
            tool_call_id=reading.tool_call_id,
            command=reading.command,
            output=reading.output,
            output_truncated=reading.output_truncated,
            status=reading.status,
        )
    return RecordedReading(reading="undecided", detail=detail, tool_call_id="", command="", output="", output_truncated=False, status="unknown")


def render_verification_report(report: VerificationReport) -> str:
    """Public re-export of the report renderer, for callers that only import here."""
    return render_report(report)
