"""The verification loop: bounded, anchored to real evidence, and coverage-preserving.

Four properties are load-bearing, and each one is proven by breaking the
implementation and watching the matching test fail. Those proofs are recorded
next to the tests they belong to, because a guard nobody has seen fail is a
comment, not a guard:

===================================  ==========================================
mutation                              test that must fail
===================================  ==========================================
budget check removed from the loop    :meth:`TestBudget.test_budget_bounds_the_loop`
weakening comparison neutered        :meth:`TestWeakenedTests.test_a_repair_that_deletes_the_assertion_is_refused`
UNVERIFIED reported as a pass        :meth:`TestOutcomes.test_exhausted_budget_is_never_a_pass`
recorded-execution seal removed      :meth:`TestRealEvidence.test_a_transcript_cannot_be_sealed_into_a_recording`
===================================  ==========================================

The mutation proofs were run against the real implementation; the observed
output of each is quoted in ``docs/VERIFICATION_LOOP.md`` §5.
"""

from __future__ import annotations

import pytest
from verification_fixtures import (
    BASELINE_TEST_FILE,
    FAILING_OUTPUT,
    PASSING_OUTPUT,
    VERIFY_COMMAND,
    ScriptedExecutor,
    make_execution,
    reading_file,
)

from alpha.verification import (
    RepairOutcome,
    UnverifiedReason,
    VerificationController,
    VerificationOutcome,
    drive_verification,
    render_report,
)

pytestmark = pytest.mark.asyncio

#: A repair that deletes the failing assertion — the exact thing a self-repair
#: loop does when it is allowed to make the failure go away instead of fixing it.
DELETED_ASSERTION = """def test_adds():
    assert compute(2, 3) == 5
"""

#: The test still exists and still asserts; it just no longer runs.
SKIPPED_TEST = """import pytest


@pytest.mark.skip(reason="flaky")
def test_adds():
    assert compute(2, 3) == 5


def test_subtracts():
    assert compute(5, 3) == 2
"""

#: The canonical comparison, loosened so the broken output satisfies it.
WEAKENED_ASSERTION = """def test_adds():
    assert compute(2, 3) == 5


def test_subtracts():
    assert compute(5, 3) >= 2
"""

STRENGTHENED = (
    BASELINE_TEST_FILE
    + """


def test_multiplies():
    assert compute(2, 3) * 2 == 12
"""
)


def _failing_then_passing() -> ScriptedExecutor:
    """An executor whose first run fails and whose re-run passes."""
    return ScriptedExecutor([FAILING_OUTPUT, PASSING_OUTPUT], statuses=["error", "success"])


async def drive(
    controller: VerificationController,
    *,
    reader=None,
    outcome: RepairOutcome = RepairOutcome.COMPLETED,
    max_steps: int = 12,
):
    """Step the loop until it settles, feeding every repair the given outcome.

    Bounded by *max_steps* on purpose. A controller that never settles is a real
    failure mode, and an unbounded driver would hang the suite rather than fail
    it — so the driver stops, and the tests assert the loop stopped because *it*
    settled, not because the driver gave up.
    """
    directive = None
    for _ in range(max_steps):
        directive = await controller.step(surface_reader=reader, repair_outcome=outcome)
        if directive.kind != "repair":
            return directive
    return directive


# ---------------------------------------------------------------------------
# The three outcomes, and only the three
# ---------------------------------------------------------------------------


class TestOutcomes:
    async def test_a_passing_recording_is_VERIFIED(self):
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([PASSING_OUTPUT], statuses=["success"]))
        directive = await drive(controller)
        assert directive.settled
        assert directive.outcome is VerificationOutcome.VERIFIED
        assert controller.report is not None
        assert controller.report.verified is True
        assert controller.report.reason is None
        assert controller.report.last_reading == "pass"

    async def test_a_declined_repair_on_a_determinate_failure_is_FAILED(self):
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]))
        controller.watch([{"path": "tests/test_math.py"}])
        reader = reading_file(BASELINE_TEST_FILE)
        first = await controller.step(surface_reader=reader)
        assert first.kind == "repair"
        directive = await controller.step(surface_reader=reader, repair_outcome=RepairOutcome.DECLINED)
        assert directive.outcome is VerificationOutcome.FAILED
        assert directive.report is not None
        assert directive.report.decided is True
        assert directive.report.last_reading == "fail"

    async def test_a_verification_only_loop_reports_FAILED_on_a_determinate_failure(self):
        """No repair is a decision, so a determinate negative may be reported as FAILED."""
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]), repair_enabled=False)
        directive = await controller.step()
        assert directive.outcome is VerificationOutcome.FAILED
        assert directive.report is not None
        assert directive.report.attempts_used == 0
        assert directive.report.last_reading == "fail"

    async def test_exhausted_budget_is_never_a_pass(self):
        """MUTATION PROOF: report UNVERIFIED as VERIFIED and this test fails.

        Budget exhaustion is the case most likely to be laundered into a "well,
        it failed" report, which reads like a verdict the loop earned. It is not:
        the loop stopped asking. So the outcome is UNVERIFIED, the failure is
        still stated in ``last_reading`` and in the evidence text, and
        ``verified`` is False.
        """
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]), attempts_allowed=1)
        controller.watch([{"path": "tests/test_math.py"}])
        directive = await drive(controller, reader=reading_file(BASELINE_TEST_FILE))
        assert directive.outcome is VerificationOutcome.UNVERIFIED
        assert directive.report.reason is UnverifiedReason.BUDGET_EXHAUSTED
        assert directive.report.verified is False
        # The determinate failure is not hidden by the exhaustion.
        assert directive.report.last_reading == "fail"
        assert "1 failed" in directive.report.evidence_text

    async def test_undecidable_output_is_UNVERIFIED_not_a_pass(self):
        """A command whose output carries no test-summary shape decides nothing."""
        noisy = "collecting ...\nbuilding wheel\ndone in 3s\n"
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([noisy], statuses=["success"]))
        directive = await drive(controller)
        assert directive.outcome is VerificationOutcome.UNVERIFIED
        assert directive.report.reason is UnverifiedReason.EVIDENCE_UNANCHORED

    async def test_zero_passing_tests_is_not_a_pass(self):
        """``0 passed`` is a veto in the existing binder and stays one here."""
        zero = "collected 0 items\n0 passed in 0.02s\n"
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([zero], statuses=["success"]))
        directive = await drive(controller)
        assert directive.outcome is not VerificationOutcome.VERIFIED

    async def test_persistent_shell_evidence_does_not_verify(self):
        """Provenance that cannot be proven clean degrades to UNVERIFIED.

        This is the controller inheriting a decision the acceptance checker
        already made, not a second opinion about it. The loop's job is to route
        evidence through that checker, and the checker's rule — a shared shell
        session cannot prove the state its run executed in — has to survive
        routing. The unproven case (``shell_persistent=None``) is checked
        separately in the honesty suite, because it is the more likely one.
        """

        class PersistentShellExecutor:
            async def execute(self, command):
                return make_execution(output=PASSING_OUTPUT, status="success", command=command, shell_persistent=True)

        controller = VerificationController(VERIFY_COMMAND, executor=PersistentShellExecutor())
        directive = await drive(controller)
        assert directive.outcome is VerificationOutcome.UNVERIFIED
        assert directive.report.reason is UnverifiedReason.EVIDENCE_UNANCHORED

    async def test_a_finished_run_is_not_a_verified_run(self):
        """The controller never asserts a run finished, and finishing is not a pass."""
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([PASSING_OUTPUT], statuses=["success"]))
        directive = await drive(controller)
        report = directive.report
        assert report is not None
        assert report.run_finished is False
        payload = report.to_dict()
        assert payload["run_finished"] is False
        assert payload["outcome"] == "VERIFIED"

    async def test_a_finished_run_renders_as_not_verified(self):
        from alpha.verification.contract import VerificationReport

        report = VerificationReport(
            evidence_outcome="undecided",
            reason=UnverifiedReason.BUDGET_EXHAUSTED,
            command=VERIFY_COMMAND,
            attempts_allowed=1,
            attempts_used=1,
            attempts_remaining=0,
            decided=False,
            last_reading="fail",
            run_finished=True,
            summary="the run finished",
        )
        text = render_report(report)
        assert "A finished run is not a verified run" in text
        assert "Verification outcome: UNVERIFIED" in text
        assert "VERIFIED" not in text.replace("not a verified run", "").replace("UNVERIFIED", "")


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------


class TestBudget:
    async def test_budget_bounds_the_loop(self):
        """MUTATION PROOF: remove the exhaustion check and this test fails.

        The driver is bounded at 12 steps, so a loop that never settles fails by
        assertion rather than by hanging. The call count is the property: the free
        first run plus exactly one execution per repair, and not one more.
        """
        executor = ScriptedExecutor([FAILING_OUTPUT])
        controller = VerificationController(VERIFY_COMMAND, executor=executor, attempts_allowed=2)
        controller.watch([{"path": "tests/test_math.py"}])
        directive = await drive(controller, reader=reading_file(BASELINE_TEST_FILE))
        assert directive.settled is True
        assert controller.report.reason is UnverifiedReason.BUDGET_EXHAUSTED
        assert controller.report.attempts_allowed == 2
        assert controller.report.attempts_used == 2
        assert controller.report.attempts_remaining == 0
        assert len(executor.calls) == 3

    async def test_the_remainder_is_visible_in_the_repair_prompt(self):
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]), attempts_allowed=3)
        controller.watch([{"path": "tests/test_math.py"}])
        directive = await controller.step(surface_reader=reading_file(BASELINE_TEST_FILE))
        assert directive.kind == "repair"
        assert "repair attempt 1 of 3" in directive.prompt
        assert "2 remain after it" in directive.prompt
        assert directive.budget_remaining == 2

    async def test_the_last_attempt_says_so(self):
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]), attempts_allowed=1)
        controller.watch([{"path": "tests/test_math.py"}])
        directive = await controller.step(surface_reader=reading_file(BASELINE_TEST_FILE))
        assert "This is the last one." in directive.prompt
        assert directive.budget_remaining == 0

    async def test_polling_does_not_spend_a_second_attempt(self):
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]), attempts_allowed=2)
        controller.watch([{"path": "tests/test_math.py"}])
        first = await controller.step(surface_reader=reading_file(BASELINE_TEST_FILE))
        polled = await controller.step()
        assert polled.kind == "repair"
        assert polled.prompt == first.prompt
        assert controller.attempts_remaining == 1


# ---------------------------------------------------------------------------
# The real failure is what gets fed back
# ---------------------------------------------------------------------------


class TestFailureIsFedBack:
    async def test_the_repair_prompt_carries_the_recorded_output_not_a_summary(self):
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]))
        controller.watch([{"path": "tests/test_math.py"}])
        directive = await controller.step(surface_reader=reading_file(BASELINE_TEST_FILE))
        assert directive.kind == "repair"
        # The whole recorded tail is present, including the line that matters and
        # the exit marker. A summary would have dropped the file:line and the code.
        assert "tests/test_math.py:9: AssertionError" in directive.prompt
        assert "assert 2 == 3" in directive.prompt
        assert "Exit Code: 1" in directive.prompt
        assert "4 passed" in directive.prompt

    async def test_a_truncated_failure_says_it_is_truncated(self):
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]), failure_evidence_chars=200)
        controller.watch([{"path": "tests/test_math.py"}])
        directive = await controller.step(surface_reader=reading_file(BASELINE_TEST_FILE))
        assert directive.kind == "repair"
        assert "was truncated to fit" in directive.prompt

    async def test_the_prompt_refuses_weakening_explicitly(self):
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]))
        controller.watch([{"path": "tests/test_math.py"}])
        directive = await controller.step(surface_reader=reading_file(BASELINE_TEST_FILE))
        assert "Do not weaken, delete, skip, or loosen the tests" in directive.prompt

    async def test_the_prompt_names_the_command_that_ran(self):
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]))
        controller.watch([{"path": "tests/test_math.py"}])
        directive = await controller.step(surface_reader=reading_file(BASELINE_TEST_FILE))
        assert f"Command: {VERIFY_COMMAND}" in directive.prompt


# ---------------------------------------------------------------------------
# Refusing a weakened test
# ---------------------------------------------------------------------------


class TestWeakenedTests:
    async def test_a_repair_that_deletes_the_assertion_is_refused(self):
        """MUTATION PROOF: make ``compare_surfaces`` always clean and this fails.

        The repair deletes the failing assertion, so the re-run "passes" and a
        loop that only asked about passing would report VERIFIED. The loop must
        refuse the attempt instead, and the outcome must be UNVERIFIED with the
        reason naming the weakening — not FAILED, because nothing was decided.
        """
        reader = reading_file(BASELINE_TEST_FILE)
        controller = VerificationController(VERIFY_COMMAND, executor=_failing_then_passing())
        controller.watch([{"path": "tests/test_math.py"}])
        first = await controller.step(surface_reader=reader)
        assert first.kind == "repair"
        reader.set_text(DELETED_ASSERTION)
        directive = await controller.step(surface_reader=reader, repair_outcome=RepairOutcome.COMPLETED)
        assert directive.settled
        assert directive.outcome is VerificationOutcome.UNVERIFIED
        assert directive.report.reason is UnverifiedReason.WEAKENED_TEST
        assert any("assertion removed or weakened" in note for note in directive.report.notes)

    async def test_a_repair_that_marks_the_test_skip_is_refused(self):
        reader = reading_file(BASELINE_TEST_FILE)
        controller = VerificationController(VERIFY_COMMAND, executor=_failing_then_passing())
        controller.watch([{"path": "tests/test_math.py"}])
        await controller.step(surface_reader=reader)
        reader.set_text(SKIPPED_TEST)
        directive = await controller.step(surface_reader=reader, repair_outcome=RepairOutcome.COMPLETED)
        assert directive.outcome is VerificationOutcome.UNVERIFIED
        assert directive.report.reason is UnverifiedReason.WEAKENED_TEST
        assert any("skip/xfail" in note for note in directive.report.notes)

    async def test_a_loosened_matcher_is_refused(self):
        reader = reading_file(BASELINE_TEST_FILE)
        controller = VerificationController(VERIFY_COMMAND, executor=_failing_then_passing())
        controller.watch([{"path": "tests/test_math.py"}])
        await controller.step(surface_reader=reader)
        reader.set_text(WEAKENED_ASSERTION)
        directive = await controller.step(surface_reader=reader, repair_outcome=RepairOutcome.COMPLETED)
        assert directive.outcome is VerificationOutcome.UNVERIFIED
        assert directive.report.reason is UnverifiedReason.WEAKENED_TEST

    async def test_a_repair_that_only_adds_tests_is_accepted(self):
        reader = reading_file(BASELINE_TEST_FILE)
        controller = VerificationController(VERIFY_COMMAND, executor=_failing_then_passing())
        controller.watch([{"path": "tests/test_math.py"}])
        await controller.step(surface_reader=reader)
        reader.set_text(STRENGTHENED)
        directive = await controller.step(surface_reader=reader, repair_outcome=RepairOutcome.COMPLETED)
        assert directive.outcome is VerificationOutcome.VERIFIED

    async def test_a_healthy_repair_that_fixes_the_code_passes(self):
        reader = reading_file(BASELINE_TEST_FILE)
        controller = VerificationController(VERIFY_COMMAND, executor=_failing_then_passing())
        controller.watch([{"path": "tests/test_math.py"}])
        await controller.step(surface_reader=reader)
        directive = await controller.step(surface_reader=reader, repair_outcome=RepairOutcome.COMPLETED)
        assert directive.outcome is VerificationOutcome.VERIFIED
        assert directive.report.attempts_used == 1

    async def test_no_watched_tests_refuses_the_repair_rather_than_approving_it(self):
        """No surface to compare is not permission to proceed."""
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]))
        directive = await drive(controller)
        assert directive.outcome is VerificationOutcome.UNVERIFIED
        assert directive.report.reason is UnverifiedReason.SURFACE_UNAVAILABLE

    async def test_an_unreadable_surface_is_indeterminate_not_clean(self):
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]))
        controller.watch([{"path": "tests/test_math.py"}])
        directive = await drive(controller, reader=lambda path: None)
        assert directive.outcome is VerificationOutcome.UNVERIFIED
        assert directive.report.reason is UnverifiedReason.SURFACE_INDETERMINATE


# ---------------------------------------------------------------------------
# The evidence has to be real
# ---------------------------------------------------------------------------


class TestRealEvidence:
    async def test_a_transcript_cannot_be_sealed_into_a_recording(self):
        """MUTATION PROOF: give ``_seal`` a default and this test fails.

        This is the guard that makes "the loop was fed a synthesised transcript"
        impossible rather than merely unlikely. Without the seal a caller could
        construct ``RecordedExecution(output="5 passed")`` and the loop would
        report VERIFIED on text nobody ran.
        """
        from alpha.verification.execution import RecordedExecution

        with pytest.raises(TypeError):
            RecordedExecution(  # type: ignore[call-arg]
                command="pytest tests/",
                tool_call_id="fake",
                tool_name="bash",
                output="5 passed in 0.1s",
                status="success",
                status_marker=None,
                command_truncated=False,
                shell_persistent=False,
            )

    async def test_an_executor_returning_a_plain_dict_is_not_evidence(self):
        """A synthesised transcript arriving through the executor is refused too."""

        class TranscriptExecutor:
            async def execute(self, command):
                return {"command": command, "output_tail": "5 passed in 0.1s", "status": "success", "shell_persistent": False}

        controller = VerificationController(VERIFY_COMMAND, executor=TranscriptExecutor())  # type: ignore[arg-type]
        directive = await drive(controller)
        assert directive.outcome is VerificationOutcome.UNVERIFIED
        assert directive.report.reason is UnverifiedReason.NO_RECORDED_EXECUTION
        assert directive.report.verified is False

    async def test_an_executor_that_raises_is_not_a_failure_and_not_a_pass(self):
        class BrokenExecutor:
            async def execute(self, command):
                raise RuntimeError("sandbox unavailable")

        controller = VerificationController(VERIFY_COMMAND, executor=BrokenExecutor())  # type: ignore[arg-type]
        directive = await drive(controller)
        assert directive.outcome is VerificationOutcome.UNVERIFIED
        assert directive.report.reason is UnverifiedReason.EXECUTION_ERROR


# ---------------------------------------------------------------------------
# Loop mechanics
# ---------------------------------------------------------------------------


class TestLoopMechanics:
    async def test_a_settled_loop_keeps_its_verdict(self):
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([PASSING_OUTPUT], statuses=["success"]))
        first = await drive(controller)
        second = await controller.step()
        assert first.report is second.report
        assert second.settled

    async def test_drive_verification_survives_a_raising_step(self):
        class ExplodingController(VerificationController):
            async def step(self, **kwargs):
                raise RuntimeError("state machine lost a transition")

        controller = ExplodingController(VERIFY_COMMAND, executor=ScriptedExecutor([PASSING_OUTPUT]))
        directive = await drive_verification(controller=controller)
        assert directive.settled
        assert directive.outcome is VerificationOutcome.UNVERIFIED
        assert directive.report.reason is UnverifiedReason.INTERNAL_ERROR

    async def test_two_controllers_do_not_share_a_budget_or_a_surface(self):
        first = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]), attempts_allowed=2)
        second = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]), attempts_allowed=2)
        first.watch([{"path": "tests/a.py"}])
        second.watch([{"path": "tests/b.py"}])
        assert first.watched_paths == ("tests/a.py",)
        assert second.watched_paths == ("tests/b.py",)
        assert first.attempts_remaining == second.attempts_remaining == 2

    async def test_a_settled_report_renders_its_own_reason(self):
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([FAILING_OUTPUT]), attempts_allowed=1)
        controller.watch([{"path": "tests/test_math.py"}])
        directive = await drive(controller, reader=reading_file(BASELINE_TEST_FILE))
        text = render_report(directive.report)
        assert "Verification outcome: UNVERIFIED" in text
        assert "Reason: budget_exhausted" in text
        assert "Repair attempts: 1 used of 1 (0 remaining)" in text
        assert "does not validate the correctness" in text
