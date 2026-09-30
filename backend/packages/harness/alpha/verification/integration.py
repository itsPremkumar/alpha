"""The integration point the middleware owner calls.

``agents/middlewares/**`` is owned by another workstream, so this package does
not touch it. Instead it defines the seam and implements against it, and the
wiring is one call the middleware owner places where the retry already lives.

## Where it goes

In ``FinishFirstVerifierMiddleware.after_model``, at the point where the loop
would begin — the same place the Finish-First notice is produced, i.e. where the
code has decided the turn made code changes with no verification step::

    from alpha.verification import drive_verification, drive_verification_from_turn

    directive = drive_verification_from_turn(
        turn_messages=turn_messages,
        runtime=runtime,
        controller=self._verification,   # a controller cached per (thread_id, run_id)
    )
    if directive is not None and directive.kind == "repair":
        return {"messages": [RemoveMessage(id=last_ai.id)], "jump_to": "model"}
    if directive is not None and directive.settled:
        return self._reject(last_ai, key=key, attempts=attempts, prompt=directive.report.render())

``drive_verification_from_turn`` is deliberately the *whole* seam: it does the
turn scan the middleware already does (did anything write code? was anything
verified?), starts or advances a controller, and returns ``None`` when the loop
has nothing to say. A middleware owner who prefers to keep the scan in place
calls :func:`drive_verification` directly.

## Why it is theirs to make

Two reasons, and the second is the one that matters.

*Mechanical:* the retry mechanism — ``RemoveMessage`` + ``jump_to: "model"`` with
a diagnostic prompt — is the middleware's. It is budgeted there, it is
thread-safe there, and duplicating it here would create a second retry path with
its own budget. The controller deliberately cannot inject a message: it returns a
directive and waits.

*Ownership:* deciding *when* a turn warrants a verification loop is agent-behaviour
policy, and the turn boundary is the middleware's. This controller answers a
narrower question — given that you want a loop, what happened — and would be
wrong if it decided for itself that every terminal message needed one.

## What it does not do

It never starts, adopts, or terminates a run, and it never edits the turn's
message list. Both are the caller's.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from alpha.verification.controller import LoopDirective, RepairOutcome, VerificationController
from alpha.verification.execution import BashToolExecutor, RecordedExecution
from alpha.verification.surface import surface_from_changes

logger = logging.getLogger(__name__)

__all__ = [
    "BashToolExecutor",
    "LoopDirective",
    "RecordedExecution",
    "RepairOutcome",
    "VerificationController",
    "drive_verification",
    "drive_verification_from_turn",
    "infer_verification_command",
    "workspace_surface_reader",
]

#: Tool calls that write code, matching the middleware's own list. A turn with one
#: of these and no verification is the case the loop exists for.
_WRITE_TOOLS = frozenset({"write_file", "str_replace", "hashline_edit"})

#: Tools that already count as a verification step. The loop exists to drive a
#: turn that has neither, not to override a turn that already verified.
_VERIFY_TOOLS = frozenset(
    {
        "auto_test_and_repair",
        "reproduce_and_verify",
        "run_task_evaluation_benchmark",
        "audit_finish_first_evidence",
        "tests_passed",
    }
)


def drive_verification_from_turn(
    *,
    turn_messages: Sequence[Any],
    runtime: Any,
    controller: VerificationController | None,
    surface_reader: Callable[[str], str | None] | None = None,
    repair_outcome: RepairOutcome | None = None,
    changed_paths: Iterable[Any] | None = None,
) -> LoopDirective | None:
    """Advance (or start) the loop for one turn. ``None`` means "nothing to do".

    The turn scan mirrors the middleware's own: a code write with no verification
    step is the trigger. A turn with neither, or that already verified, returns
    ``None`` and the caller proceeds exactly as it does today.

    When *controller* is ``None`` this returns ``None`` rather than constructing
    one. A loop needs a command to run, and inventing one from turn history would
    mean the controller chose its own verification target — which is a judgement
    the caller, not this seam, is entitled to make.
    """
    if controller is None:
        return None
    if not _turn_needs_verification(turn_messages):
        return None
    if changed_paths is not None and not controller.watched_paths:
        controller.watch(changed_paths)
    if controller.settled:
        return LoopDirective(kind="settled", state=controller.state, report=controller.report, budget_remaining=0)
    return _run_step(controller, surface_reader=surface_reader, repair_outcome=repair_outcome)


async def drive_verification(
    *,
    controller: VerificationController,
    surface_reader: Callable[[str], str | None] | None = None,
    repair_outcome: RepairOutcome | None = None,
) -> LoopDirective:
    """Advance an existing controller by one transition. The narrow seam.

    Use this when the caller has already decided a loop should run and holds the
    controller. It is the function to call from a middleware; everything else in
    this module is a convenience over it.
    """
    return await _run_step(controller, surface_reader=surface_reader, repair_outcome=repair_outcome)


async def _run_step(
    controller: VerificationController,
    *,
    surface_reader: Callable[[str], str | None] | None,
    repair_outcome: RepairOutcome | None,
) -> LoopDirective:
    """Await one transition, keeping an executor contract failure fail-closed.

    A controller step is not supposed to raise, but "not supposed to" is not a
    guarantee, and a verification loop that takes a run down when it breaks is
    worse than one that reports UNVERIFIED. A raised step becomes an UNVERIFIED
    verdict with the reason attached rather than an exception in the caller's
    turn.
    """
    try:
        return await controller.step(surface_reader=surface_reader, repair_outcome=repair_outcome)
    except Exception as exc:  # pragma: no cover - exercised by the fault-injection test
        logger.exception("Verification loop raised; reporting UNVERIFIED rather than breaking the turn")
        return _internal_error_directive(controller, exc)


def _internal_error_directive(controller: VerificationController, exc: BaseException) -> LoopDirective:
    from alpha.verification.contract import LoopState, UnverifiedReason, VerificationReport

    report = VerificationReport(
        evidence_outcome="undecided",
        reason=UnverifiedReason.INTERNAL_ERROR,
        command=getattr(controller, "_command", ""),
        attempts_allowed=controller.attempts_remaining,
        attempts_used=0,
        attempts_remaining=controller.attempts_remaining,
        decided=False,
        last_reading="none",
        summary=f"the verification loop raised ({type(exc).__name__}) and could not determine an outcome",
        notes=(f"{type(exc).__name__}: {exc}",),
        state=LoopState.SETTLED,
    )
    return LoopDirective(kind="settled", state=LoopState.SETTLED, report=report, budget_remaining=0)


def _turn_needs_verification(turn_messages: Sequence[Any]) -> bool:
    """Whether this turn wrote code and never verified it.

    Deliberately narrow, and deliberately the same shape as the middleware's own
    check. The loop is for the turn that skipped verification, not a second
    opinion on a turn that already ran its tests.
    """
    wrote_code = False
    verified = False
    for message in turn_messages or ():
        name = getattr(message, "name", None)
        if not isinstance(name, str):
            continue
        if name in _WRITE_TOOLS:
            wrote_code = True
        elif name in _VERIFY_TOOLS:
            verified = True
    return wrote_code and not verified


def infer_verification_command(turn_messages: Sequence[Any]) -> str | None:
    """The test command a turn already tried to run, if it mentioned one.

    Returns ``None`` rather than a guess. The controller is handed a command by
    its caller; this helper only reports a command the turn itself attempted, and
    a caller with no better source can use it without the controller inventing a
    verification target out of thin air.
    """
    for message in turn_messages or ():
        if getattr(message, "name", None) not in ("bash", "bash_tool"):
            continue
        text = _flatten(getattr(message, "content", ""))
        for line in text.splitlines():
            candidate = line.strip()
            if candidate.startswith(("pytest", "python -m pytest", "npm test", "pnpm test", "yarn test", "cargo test", "go test")):
                return candidate
    return None


def _flatten(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts)
    return ""


def workspace_surface_reader(workspace_path: str) -> Callable[[str], str | None]:
    """The production surface reader: workspace-scoped, containment-checked.

    The before/after comparison has to see the bytes the test run saw, so it
    reads the same tree the run works in rather than a copy. Every path is
    resolved under *workspace_path* and anything that escapes is answered with
    ``None`` — unreadable, which the comparison treats as "these assertions are
    not running" rather than as a clean bill of health.

    Known boundary, and the reason it fails closed: a remote sandbox that does
    not mirror the workspace onto the host reads back as empty here, so every
    watched test file comes back unreadable, every repair is refused, and the
    loop settles UNVERIFIED. That is the correct direction to be wrong in, and
    it is why a surface that cannot be established never approves a repair.
    """
    root = Path(workspace_path).resolve()

    def read(path: str) -> str | None:
        candidate = (root / str(path).replace("\\", "/").lstrip("/")).resolve()
        if candidate != root and root not in candidate.parents:
            return None
        if not candidate.is_file():
            return None
        try:
            return candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    return read


def changed_test_paths(state: Mapping[str, Any]) -> tuple[str, ...]:
    """The test files among a run's recorded workspace changes.

    A thin adapter over :func:`alpha.verification.surface.surface_from_changes`,
    so the caller does not have to know the shape its own state uses for changes.
    """
    changed = state.get("changed_files") or state.get("workspace_changes") or ()
    if isinstance(changed, Mapping):
        changed = changed.get("files") or ()
    return surface_from_changes(changed or ())
