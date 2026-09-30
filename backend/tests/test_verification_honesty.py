"""Honesty guards for the verification loop, and for the checker it composes with.

This suite exists because the failure it is written against is *invisible*. A
controller that reports UNVERIFIED as a pass, or that accepts a repaired suite
which lost its assertions, does not crash. It produces a confident, wrong, green
answer — which is the exact defect the whole component exists to prevent, and the
reason the project's own rules treat claim honesty as mandatory.

Four guards, each with its mutation proof:

=========================================  ================================================
mutation                                    test that must fail
=========================================  ================================================
``VerificationReport.outcome`` flattens      :func:`test_unverified_can_never_render_as_verified`
budget exhaustion promoted to FAILED         :func:`test_an_exhausted_loop_cannot_report_failed`
surface comparison neutralised                :func:`test_a_reduction_is_visible_before_the_comparison_is_neutered`
``acceptance_checks`` weakened                :func:`test_the_existing_checker_still_rejects_what_it_rejected`
=========================================  ================================================

Plus the properties that keep this from drifting into a green machine: the
acceptance checker is not relaxed, the run lifecycle is not duplicated, the
budget cannot be over-drawn, and a completed run is never described as verified.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from verification_fixtures import (
    BASELINE_TEST_FILE,
    FAILING_OUTPUT,
    PASSING_OUTPUT,
    VERIFY_COMMAND,
    ScriptedExecutor,
)

from alpha.subagents import acceptance_checks
from alpha.subagents.acceptance_checks import check_acceptance_criteria
from alpha.verification import (
    UNVERIFIED,
    AttemptBudget,
    LoopState,
    UnverifiedReason,
    VerificationController,
    VerificationOutcome,
    VerificationReport,
    capture_surface,
    compare_surfaces,
    record_execution,
    render_report,
)
from alpha.verification.contract import FAILED, VERIFIED

# ---------------------------------------------------------------------------
# The three outcomes cannot collapse into each other
# ---------------------------------------------------------------------------


def _report(**overrides) -> VerificationReport:
    base = {
        "evidence_outcome": "undecided",
        "reason": UnverifiedReason.BUDGET_EXHAUSTED,
        "command": VERIFY_COMMAND,
        "attempts_allowed": 1,
        "attempts_used": 1,
        "attempts_remaining": 0,
        "decided": False,
        "last_reading": "fail",
    }
    return VerificationReport(**{**base, **overrides})


class TestOutcomeSeparation:
    def test_unverified_can_never_render_as_verified(self):
        """MUTATION PROOF: return VERIFIED for any non-fail reading and this fails.

        The three outcomes exist so that "we could not tell" is a value rather
        than an absence. If UNVERIFIED can be rendered as anything else, the
        absence is back and every caller has to re-derive the distinction.
        """
        report = _report()
        assert report.outcome is VerificationOutcome.UNVERIFIED
        assert report.verified is False
        text = render_report(report)
        assert "Verification outcome: UNVERIFIED" in text
        assert "Reason: budget_exhausted" in text
        # Nothing in the rendered text can be read as a pass.
        stripped = text.replace("UNVERIFIED", "").replace("not a verified run", "")
        assert "VERIFIED" not in stripped
        assert "PASSED" not in stripped
        assert "passed" not in stripped.replace("no longer passes", "")

    def test_an_exhausted_loop_cannot_report_failed(self):
        """MUTATION PROOF: report exhaustion as FAILED and this test fails.

        A loop that stopped trying has not decided anything. Reporting FAILED
        there converts an unanswered question into a recorded conclusion, so the
        exhaustion report must be undecided, and the determinate failure it saw
        must still be visible in ``last_reading`` rather than promoted.
        """
        report = _report(evidence_outcome="fail", decided=False)
        assert report.outcome is VerificationOutcome.UNVERIFIED
        assert report.last_reading == "fail"  # the failure is still visible
        # The decided form of the same reading is a different thing entirely:
        # it is legal, and it is FAILED. The distinction is the flag, not the
        # evidence, which is why the controller owns it.
        assert _report(evidence_outcome="fail", decided=True, reason=None).outcome is FAILED
        with pytest.raises(ValueError, match="budget-exhausted loop is not a decided loop"):
            _report(evidence_outcome="undecided", decided=True)

    def test_a_decided_failure_is_FAILED(self):
        assert _report(evidence_outcome="fail", decided=True, reason=None).outcome is FAILED

    def test_only_a_passing_reading_is_VERIFIED(self):
        assert _report(evidence_outcome="pass", reason=None, decided=False, last_reading="pass").outcome is VERIFIED
        assert _report(evidence_outcome="undecided", reason=UnverifiedReason.EVIDENCE_UNANCHORED).outcome is UNVERIFIED

    def test_a_passing_reading_cannot_carry_a_failure_reason(self):
        with pytest.raises(ValueError, match="cannot carry an unverified reason"):
            _report(evidence_outcome="pass", reason=UnverifiedReason.BUDGET_EXHAUSTED)

    def test_an_undetermined_reading_requires_a_reason(self):
        with pytest.raises(ValueError, match="requires an unverified reason"):
            _report(evidence_outcome="undecided", reason=None)

    def test_a_decided_failure_cannot_carry_an_undetermined_reason(self):
        """FAILED is a conclusion, not a hedge; it has no "we could not tell" reason."""
        with pytest.raises(ValueError, match="decided failure cannot carry an unverified reason"):
            _report(evidence_outcome="fail", decided=True, reason=UnverifiedReason.EVIDENCE_UNANCHORED)
        # The bare form is legal, and is the only legal decided-failure form.
        assert _report(evidence_outcome="fail", decided=True, reason=None).outcome is FAILED

    def test_the_budget_arithmetic_is_checked_at_construction(self):
        with pytest.raises(ValueError, match="attempts_remaining must be the budget"):
            _report(attempts_used=0, attempts_remaining=7)
        with pytest.raises(ValueError, match="attempts_used cannot exceed"):
            _report(attempts_allowed=1, attempts_used=5, attempts_remaining=0)

    def test_every_unverified_reason_is_a_real_unverified_reason(self):
        """No reason may exist that a caller could use to excuse a pass."""
        for reason in UnverifiedReason:
            report = _report(evidence_outcome="undecided", reason=reason)
            assert report.outcome is UNVERIFIED

    def test_state_names_are_actions_not_conclusions(self):
        """A state may not read as a verdict; the outcome field carries the verdict."""
        verdicts = {"verified", "failed", "passed", "unverified", "ok", "done"}
        for state in LoopState:
            assert state.value not in verdicts


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------


class TestBudgetLedger:
    def test_a_budget_counts_down_and_exposes_the_remainder(self):
        budget = AttemptBudget(total=3)
        assert budget.remaining == 3
        assert budget.consume() == 2
        assert budget.consume() == 1
        assert not budget.exhausted
        assert budget.consume() == 0
        assert budget.exhausted

    def test_a_spent_budget_cannot_be_overdrawn(self):
        """MUTATION PROOF: make ``consume`` a no-op and this test fails."""
        budget = AttemptBudget(total=1)
        budget.consume()
        with pytest.raises(ValueError, match="exhausted"):
            budget.consume()

    @pytest.mark.parametrize("total", [1, 3, 7])
    def test_a_budget_of_any_size_is_consistent(self, total):
        budget = AttemptBudget(total=total)
        for spent in range(total):
            assert budget.remaining == total - spent
            budget.consume()
        assert budget.exhausted
        assert budget.remaining == 0

    def test_a_zero_budget_is_rejected_at_construction(self):
        with pytest.raises(ValueError, match="at least one attempt"):
            AttemptBudget(total=0)
        with pytest.raises(ValueError, match="cannot exceed the budget"):
            AttemptBudget(total=1, spent=2)


# ---------------------------------------------------------------------------
# The surface comparison cannot be neutralised
# ---------------------------------------------------------------------------


class TestSurfaceGuard:
    def test_a_reduction_is_visible_before_the_comparison_is_neutered(self):
        """MUTATION PROOF: return ``reduced=False`` unconditionally and this fails.

        Every other test in the loop suite that covers a refused repair routes
        through this comparison, so neutering it turns the whole coverage guard
        into a no-op while leaving those tests' happy paths green. It is pinned
        here on its own so the neutering is caught at the comparison rather than
        three layers up.
        """
        before = capture_surface(["tests/a.py"], lambda path: BASELINE_TEST_FILE)
        after = capture_surface(["tests/a.py"], lambda path: "def test_adds():\n    assert compute(2, 3) == 5\n")
        comparison = compare_surfaces(before, after)
        assert comparison.reduced is True
        assert comparison.blocking is True
        assert comparison.reasons

    def test_an_unchanged_surface_is_not_a_reduction(self):
        before = capture_surface(["tests/a.py"], lambda path: BASELINE_TEST_FILE)
        after = capture_surface(["tests/a.py"], lambda path: BASELINE_TEST_FILE)
        assert compare_surfaces(before, after).reduced is False

    def test_strengthening_is_not_a_reduction(self):
        before = capture_surface(["tests/a.py"], lambda path: BASELINE_TEST_FILE)
        after = capture_surface(["tests/a.py"], lambda path: BASELINE_TEST_FILE + "\ndef test_more():\n    assert True\n")
        comparison = compare_surfaces(before, after)
        assert comparison.reduced is False
        assert comparison.after_assertions > comparison.before_assertions

    def test_a_missing_file_is_a_reduction(self):
        before = capture_surface(["tests/a.py"], lambda path: BASELINE_TEST_FILE)
        after = capture_surface(["tests/a.py"], lambda path: None)
        assert compare_surfaces(before, after).reduced is True

    def test_an_unreadable_before_capture_is_indeterminate_not_clean(self):
        before = capture_surface(["tests/a.py"], lambda path: None)
        after = capture_surface(["tests/a.py"], lambda path: BASELINE_TEST_FILE)
        comparison = compare_surfaces(before, after)
        assert comparison.indeterminate is True
        assert comparison.blocking is True

    def test_an_unreadable_after_capture_is_a_reduction_not_a_pass(self):
        """A test file that cannot be read after a repair contributes no coverage.

        This is deliberately *not* filed as indeterminacy. Indeterminacy means
        "we cannot tell", which is the right answer when the before-capture failed
        and the right answer nowhere else: a file that was readable and is now not
        is a file whose assertions are not running any more, and calling that
        "we cannot tell" would let a repair delete the suite and be described as
        merely unclear.
        """
        before = capture_surface(["tests/a.py"], lambda path: BASELINE_TEST_FILE)
        after = capture_surface(["tests/a.py"], lambda path: None)
        comparison = compare_surfaces(before, after)
        assert comparison.reduced is True
        assert comparison.blocking is True
        assert any("not running" in reason for reason in comparison.reasons)

    def test_a_production_file_is_not_part_of_the_test_surface(self):
        before = capture_surface(["src/app.py"], lambda path: "x = 1\n")
        after = capture_surface(["src/app.py"], lambda path: "x = 2\n")
        assert compare_surfaces(before, after).reduced is False


# ---------------------------------------------------------------------------
# The existing checker must not have been relaxed to make any of this pass
# ---------------------------------------------------------------------------


class TestAcceptanceCheckerNotWeakened:
    def test_the_existing_checker_still_rejects_what_it_rejected(self):
        """MUTATION PROOF: loosen ``_check_tests_passed_leaf`` and this test fails.

        A controller that reports VERIFIED because the binder it routes through
        was made lenient is the exact defect this component exists to prevent.
        These four are the rejections the controller's VERIFIED verdict depends
        on, asserted here so a change to the checker cannot be mistaken for a
        change to the loop.
        """
        # 1. No recorded execution at all.
        leaf = _leaf(["tests_passed:make test"], [])
        assert (leaf["checked"], leaf["holds"]) == (False, False)

        # 2. A recorded execution whose output carries no test-summary shape.
        execution = record_execution(command="make test", tool_call_id="c1", tool_name="bash", output="building...\ndone", status="success", shell_persistent=False)
        leaf = _leaf(["tests_passed:make test"], [execution.to_acceptance_execution()])
        assert (leaf["checked"], leaf["holds"]) == (False, False)

        # 3. A passing summary from a persistent shell session.
        execution = record_execution(command="make test", tool_call_id="c2", tool_name="bash", output="277 passed in 76.6s", status="success", shell_persistent=True)
        leaf = _leaf(["tests_passed:make test"], [execution.to_acceptance_execution()])
        assert (leaf["checked"], leaf["holds"]) == (False, False)

        # 4. "0 passed" is a veto, not a pass.
        execution = record_execution(command="make test", tool_call_id="c3", tool_name="bash", output="collected 0 items\n0 passed in 0.02s", status="success", shell_persistent=False)
        leaf = _leaf(["tests_passed:make test"], [execution.to_acceptance_execution()])
        assert leaf["holds"] is False

    def test_the_checker_still_accepts_a_real_passing_execution(self):
        """The counterpart, so the test above cannot be satisfied by breaking it."""
        execution = record_execution(command="make test", tool_call_id="c4", tool_name="bash", output=".....\n277 passed in 76.6s\n", status="success", shell_persistent=False)
        leaf = _leaf(["tests_passed:make test"], [execution.to_acceptance_execution()])
        assert (leaf["checked"], leaf["holds"]) == (True, True)

    def test_the_leaf_vocabulary_is_still_checked_and_holds(self):
        """The strong-positive words stay out of the checker's own vocabulary."""
        verdict = check_acceptance_criteria(
            ["tests_passed:make test"],
            bash_executions=[],
            **_lazy_probes(),
        )
        rendered = acceptance_checks.render_acceptance_section(verdict)
        assert "UNVERIFIED" in rendered
        for forbidden in ("satisfied", "verified", "passed"):
            assert f"[{forbidden}]" not in rendered
        assert "execution evidence only" in rendered


# ---------------------------------------------------------------------------
# No second run lifecycle owner
# ---------------------------------------------------------------------------


def _verification_package() -> Path:
    import alpha.verification

    return Path(alpha.verification.__file__).parent  # type: ignore[arg-type]


class TestNoSecondRunLifecycle:
    def test_the_package_imports_no_run_lifecycle_symbol(self):
        """MUTATION PROOF: import RunManager here and this test fails.

        ``RunManager`` is the sole run lifecycle owner and
        ``SafeRunRecoveryService`` the only safe-continuation authority. A
        controller that could start, adopt, or terminate a run would be a second
        owner, and the project treats that as a defect rather than a feature.
        """
        forbidden = {"RunManager", "run_agent", "SafeRunRecoveryService", "start_run", "cancel_run", "resume_run", "RunRecord"}
        package = _verification_package()
        offenders: list[str] = []
        for module in sorted(package.glob("*.py")):
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: set[str] = set()
                if isinstance(node, ast.ImportFrom):
                    names = {alias.asname or alias.name for alias in node.names} | {node.module or ""}
                elif isinstance(node, ast.Import):
                    names = {alias.asname or alias.name for alias in node.names}
                for name in names:
                    if name.split(".")[-1] in forbidden:
                        offenders.append(f"{module.name}: {name}")
        assert not offenders, f"the verification controller must not reach the run lifecycle: {offenders}"

    def test_the_controller_exposes_no_lifecycle_method(self):
        controller = VerificationController(VERIFY_COMMAND, executor=ScriptedExecutor([PASSING_OUTPUT], statuses=["success"]))
        for forbidden in ("start_run", "cancel_run", "resume_run", "terminate", "recover"):
            assert not hasattr(controller, forbidden), f"VerificationController gained a {forbidden} method; it drives a loop within a run, it does not own runs"

    def test_the_controller_reports_the_run_as_not_finished(self):
        """The controller never claims a run finished — that is RunManager's fact to report."""
        report = _report(evidence_outcome="pass", reason=None, last_reading="pass", run_finished=False)
        assert report.run_finished is False
        assert report.to_dict()["run_finished"] is False


# ---------------------------------------------------------------------------
# The package surface
# ---------------------------------------------------------------------------


def test_the_package_exports_the_documented_vocabulary():
    import alpha.verification as verification

    for name in (
        "VERIFIED",
        "FAILED",
        "UNVERIFIED",
        "VerificationController",
        "VerificationReport",
        "LoopDirective",
        "RepairOutcome",
        "RecordedExecution",
        "AttemptBudget",
        "UnverifiedReason",
        "drive_verification",
        "drive_verification_from_turn",
        "render_report",
    ):
        assert hasattr(verification, name), f"alpha.verification is missing {name}"


def test_the_config_model_refuses_a_refusal_switch():
    """There is no config key that turns the coverage guard off, by design."""
    from alpha.config.verification_loop_config import VerificationLoopConfig

    config = VerificationLoopConfig()
    assert not hasattr(config, "refuse_weakened_tests")
    with pytest.raises(Exception):
        VerificationLoopConfig(refuse_weakened_tests=False)  # type: ignore[call-arg]


def test_the_config_bounds_the_attempt_budget():
    from alpha.config.verification_loop_config import VerificationLoopConfig

    assert VerificationLoopConfig().max_attempts == 2
    with pytest.raises(Exception):
        VerificationLoopConfig(max_attempts=0)  # type: ignore[call-arg]
    with pytest.raises(Exception):
        VerificationLoopConfig(max_attempts=999)  # type: ignore[call-arg]


class TestDisabledLoop:
    @pytest.mark.asyncio
    async def test_a_disabled_loop_reports_rather_than_vanishes(self):
        """Turning the feature off must be visible, not silent.

        A caller that wired the seam and then disabled the feature gets an
        UNVERIFIED it can read. Silence here would be indistinguishable from a
        controller that verified, which is the one reading that must never be
        available by accident.
        """
        from alpha.config.verification_loop_config import VerificationLoopConfig

        controller = VerificationController(
            VERIFY_COMMAND,
            executor=ScriptedExecutor([PASSING_OUTPUT], statuses=["success"]),
            config=VerificationLoopConfig(enabled=False),
        )
        directive = await controller.step()
        assert directive.settled
        assert directive.outcome is VerificationOutcome.UNVERIFIED
        assert directive.report.reason is UnverifiedReason.LOOP_DISABLED
        assert directive.state is LoopState.SETTLED

    @pytest.mark.asyncio
    async def test_the_controller_reads_its_budget_from_config_by_default(self):
        """The config module is a production reference, not a decorative one."""
        from alpha.config.verification_loop_config import VerificationLoopConfig

        controller = VerificationController(
            VERIFY_COMMAND,
            executor=ScriptedExecutor([FAILING_OUTPUT]),
            config=VerificationLoopConfig(max_attempts=5),
        )
        assert controller.attempts_remaining == 5


class TestWorkspaceSurfaceReader:
    def test_it_reads_a_file_inside_the_workspace(self, tmp_path):
        from alpha.verification.integration import workspace_surface_reader

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_math.py").write_text(BASELINE_TEST_FILE, encoding="utf-8")
        read = workspace_surface_reader(str(tmp_path))
        assert read("tests/test_math.py") == BASELINE_TEST_FILE

    def test_it_refuses_a_path_that_escapes_the_workspace(self, tmp_path):
        """Containment is checked on the resolved path, not on the spelling."""
        from alpha.verification.integration import workspace_surface_reader

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (tmp_path / "secret.py").write_text("TOKEN = 'x'\n", encoding="utf-8")
        read = workspace_surface_reader(str(workspace))
        assert read("../secret.py") is None
        assert read("tests/../../secret.py") is None

    def test_a_missing_file_reads_as_none_not_as_empty(self, tmp_path):
        from alpha.verification.integration import workspace_surface_reader

        read = workspace_surface_reader(str(tmp_path))
        assert read("tests/absent.py") is None

    def test_a_symlink_out_of_the_workspace_is_refused(self, tmp_path):
        import os

        from alpha.verification.integration import workspace_surface_reader

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("assert True\n", encoding="utf-8")
        try:
            os.symlink(outside, workspace / "linked.py")
        except (OSError, NotImplementedError):  # pragma: no cover - Windows without privileges
            pytest.skip("symlink creation is not permitted here")
        read = workspace_surface_reader(str(workspace))
        assert read("linked.py") is None


def _lazy_probes():
    from alpha.verification.controller import _lazy_callable

    return {
        "content_reader": _lazy_callable("alpha.sandbox.tools", "read_current_file_content"),
        "size_prober": _lazy_callable("alpha.subagents.acceptance_checks", "_probe_file_size"),
        "readable_prober": _lazy_callable("alpha.subagents.acceptance_checks", "_probe_file_readable"),
    }


def _leaf(criteria, executions):
    # The same composed call the controller makes, lazy probes included: this
    # asserts what the loop actually routes through, not a variant of it.
    verdict = check_acceptance_criteria(criteria, bash_executions=executions, **_lazy_probes())
    return verdict["leaves"][0]
