"""Autonomous Self-Healing Test & Debug Loop.

Executes test suites and verification obligations inside task worktrees.
When tests fail, it automatically extracts failure tracebacks, consults project
heuristics from postmortem memory, prompts the assigned coder bot for a diagnostic patch,
and re-executes tests up to a configurable retry limit.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

from agent_workspace.projects.contracts import EvidenceReceipt, get_contract_gatekeeper
from agent_workspace.projects.events import get_event_bus
from agent_workspace.projects.postmortem import get_postmortem_engine

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class TestRunResult:
    """Outcome of a single test or verification execution."""

    __test__ = False

    passed: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    traceback_summary: str = ""
    attempt_index: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SelfHealingOutcome:
    """Final outcome of the self-healing test and debug loop."""

    task_id: str
    project_id: str
    success: bool
    attempts: int
    history: list[dict[str, Any]] = field(default_factory=list)
    applied_heuristics: list[str] = field(default_factory=list)
    evidence_receipt: dict[str, Any] | None = None
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_traceback_summary(output: str) -> str:
    """Extract key exception lines or failure summary from test output."""
    if not output:
        return "Unknown failure (empty output)"

    lines = output.strip().splitlines()
    error_lines: list[str] = []
    for line in lines:
        if any(marker in line for marker in ("FAILED", "Error:", "Exception:", "AssertionError", "Traceback")):
            error_lines.append(line.strip())

    if error_lines:
        return " | ".join(error_lines[-5:])
    return lines[-1].strip() if lines else "Test execution failed."


class SelfHealingTestRunner:
    """Orchestrates test verification, error diagnosis, and autonomous fix iterations."""

    def __init__(self, project_id: str):
        self.project_id = project_id

    def run_with_self_healing(
        self,
        task_id: str,
        bot_name: str,
        test_fn: Callable[[], TestRunResult],
        *,
        patch_synthesizer: Callable[[TestRunResult, list[str]], str | None] | None = None,
        max_attempts: int = 3,
        auto_attach_contract_evidence: bool = True,
    ) -> SelfHealingOutcome:
        """Run verification loop with automatic heuristic retrieval and diagnostic retries."""
        history: list[dict[str, Any]] = []
        applied_heuristics: list[str] = []
        pm_engine = get_postmortem_engine(self.project_id)
        prior_heuristics = pm_engine.get_heuristics_summary()

        for attempt in range(1, max_attempts + 1):
            logger.info("Executing verification for %s (attempt %d/%d)", task_id, attempt, max_attempts)
            try:
                run_result = test_fn()
            except Exception as exc:
                # Fault isolation: a verification harness that raises (bad command,
                # subprocess/OS error, import failure) must not escape the loop.
                # Escaping would abort self-healing, skip the remaining retries,
                # and lose the postmortem — the caller would get an exception
                # instead of an outcome. Record it as a failed attempt so the
                # loop can retry, repair, and finally report like any other failure.
                logger.warning(
                    "Verification harness raised for %s on attempt %d; recording as a failed attempt",
                    task_id,
                    attempt,
                    exc_info=True,
                )
                run_result = TestRunResult(
                    passed=False,
                    stdout="",
                    stderr=f"{type(exc).__name__}: {exc}",
                    exit_code=1,
                    traceback_summary=f"{type(exc).__name__}: {exc}",
                )
            run_result.attempt_index = attempt

            if not run_result.traceback_summary and not run_result.passed:
                run_result.traceback_summary = extract_traceback_summary(
                    run_result.stderr or run_result.stdout
                )

            history.append(run_result.to_dict())

            if run_result.passed:
                # Tests passed!
                receipt_dict: dict[str, Any] | None = None
                if auto_attach_contract_evidence:
                    gk = get_contract_gatekeeper(self.project_id)
                    receipt = EvidenceReceipt(
                        kind="tests_passed",
                        reference=f"test_verified_attempt_{attempt}",
                        verified_by=bot_name,
                        detail=f"Automated verification succeeded on attempt {attempt}.",
                    )
                    try:
                        gk.add_evidence(task_id, receipt)
                        receipt_dict = receipt.to_dict()
                    except KeyError:
                        pass

                get_event_bus(self.project_id).emit(
                    "test_passed",
                    bot_name,
                    {"task_id": task_id, "attempt": attempt, "resolved_automatically": attempt > 1},
                )

                return SelfHealingOutcome(
                    task_id=task_id,
                    project_id=self.project_id,
                    success=True,
                    attempts=attempt,
                    history=history,
                    applied_heuristics=applied_heuristics,
                    evidence_receipt=receipt_dict,
                )

            # Test failed on this attempt
            get_event_bus(self.project_id).emit(
                "test_failed",
                bot_name,
                {"task_id": task_id, "attempt": attempt, "summary": run_result.traceback_summary},
            )

            # If more attempts remain and a patch synthesizer was provided, attempt repair
            if attempt < max_attempts and patch_synthesizer is not None:
                # Find matching heuristics
                matching_heuristics = [
                    h for h in prior_heuristics
                    if any(w.lower() in run_result.traceback_summary.lower() for w in re.findall(r"\w+", h)[:3])
                ] or prior_heuristics[:3]
                applied_heuristics.extend([h for h in matching_heuristics if h not in applied_heuristics])

                try:
                    patch = patch_synthesizer(run_result, matching_heuristics)
                    if patch:
                        logger.info("Applied diagnostic patch for %s on attempt %d", task_id, attempt)
                except Exception:
                    logger.warning("Patch synthesizer failed during self-healing for %s", task_id, exc_info=True)

        # Exhausted all attempts without passing
        last_failure = history[-1] if history else {}
        err_summary = last_failure.get("traceback_summary", "Tests failed after max attempts.")

        # Record postmortem for RSI learning
        try:
            pm_engine.analyze_failure(
                task_id=task_id,
                bot_name=bot_name,
                error_summary=err_summary,
                root_cause=f"Automated self-healing loop exhausted {max_attempts} attempts without resolution.",
                erroneous_assumptions=["Assumed test would pass without further architectural intervention"],
                preventative_rule=f"Review failure: {err_summary[:100]}",
                sync_to_memory=True,
            )
        except Exception:
            logger.debug("Failed to record postmortem from self-healing loop", exc_info=True)

        return SelfHealingOutcome(
            task_id=task_id,
            project_id=self.project_id,
            success=False,
            attempts=max_attempts,
            history=history,
            applied_heuristics=applied_heuristics,
        )


def get_self_healing_runner(project_id: str) -> SelfHealingTestRunner:
    return SelfHealingTestRunner(project_id)
