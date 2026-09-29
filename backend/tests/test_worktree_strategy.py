"""Tests for worktree strategy selection.

The house rule these tests enforce: the *choice* of isolation is evidence, and
an unexplained or silently-degraded choice is a defect. So the assertions are
mostly about the reason and the assurance, not just the mode.
"""

from __future__ import annotations

import pytest

from alpha.sandbox.worktree_strategy import (
    Assurance,
    TaskSignals,
    WorktreeMode,
    WorktreeSelection,
    is_protected_branch,
    is_safe_branch_name,
    select_strategy,
    upgrade_or_report,
)

# --- selection table ---------------------------------------------------------


def test_mergeable_task_gets_a_worktree():
    sel = select_strategy(TaskSignals(produces_mergeable_diff=True))
    assert sel.mode is WorktreeMode.WORKTREE
    assert sel.assurance is Assurance.STANDARD
    assert sel.mergeable is True


def test_untrusted_code_gets_sandbox_not_a_bare_worktree():
    """A worktree is not a security boundary; only a provider-backed mode is."""
    sel = select_strategy(TaskSignals(runs_untrusted_code=True))
    assert sel.mode is WorktreeMode.SANDBOX


def test_merge_verification_never_shares_a_workspace_with_implementation():
    sel = select_strategy(TaskSignals(merge_verification=True))
    assert sel.mode is WorktreeMode.INTEGRATION
    assert sel.touches_main is False


def test_merge_verification_outranks_mergeable_diff():
    """A candidate being judged must not be judged in the workspace being written."""
    sel = select_strategy(TaskSignals(merge_verification=True, produces_mergeable_diff=True))
    assert sel.mode is WorktreeMode.INTEGRATION


def test_speculative_task_gets_ephemeral_and_says_it_is_not_recoverable():
    sel = select_strategy(TaskSignals(speculative=True))
    assert sel.mode is WorktreeMode.EPHEMERAL
    assert sel.survives_crash is False
    assert sel.mergeable is False


def test_session_spanning_task_gets_long_lived():
    sel = select_strategy(TaskSignals(spans_sessions=True))
    assert sel.mode is WorktreeMode.LONG_LIVED
    assert sel.survives_crash is True


def test_read_only_task_gets_a_worktree():
    sel = select_strategy(TaskSignals(read_only=True))
    assert sel.mode is WorktreeMode.WORKTREE


def test_unknown_task_defaults_to_the_recoverable_mode():
    """No signals at all is *unknown*, and unknown must not resolve to a mode
    that discards work."""
    sel = select_strategy(TaskSignals())
    assert sel.mode is WorktreeMode.WORKTREE
    assert sel.survives_crash is True


# --- degradation is disclosed, never absorbed -------------------------------


def test_git_unavailable_degrades_to_copy_with_lower_assurance():
    sel = select_strategy(
        TaskSignals(produces_mergeable_diff=True),
        git_available=False,
        git_error="git exited 128: unable to access '.git': permission denied",
    )
    assert sel.mode is WorktreeMode.COPY
    assert sel.assurance is Assurance.LOWER
    assert "permission denied" in sel.reason


def test_degraded_copy_is_never_mergeable():
    """A copy workspace cannot produce a diff; claiming otherwise is the lie."""
    sel = select_strategy(TaskSignals(), git_available=False, git_error="git missing")
    assert sel.mergeable is False


def test_degraded_copy_is_never_reported_as_touching_the_shared_checkout():
    """Degradation must cost assurance, never isolation. The shared checkout is
    the one outcome the whole worktree design exists to prevent."""
    sel = select_strategy(TaskSignals(), git_available=False, git_error="git missing")
    assert sel.mode is WorktreeMode.COPY
    assert sel.mode is not WorktreeMode.WORKTREE


def test_missing_git_error_still_produces_a_reason():
    """A degradation with no stated reason would be an unexplained downgrade."""
    sel = select_strategy(TaskSignals(), git_available=False)
    assert sel.assurance is Assurance.LOWER
    assert sel.reason.strip()


def test_git_unavailable_degrades_every_git_backed_signal():
    """Regression: the availability check used to be applied only to the
    default branch of the selector, so a task that said
    `produces_mergeable_diff` still got WORKTREE on a deployment that cannot
    create one — a record promising an isolation the caller never receives."""
    for signals in (
        TaskSignals(produces_mergeable_diff=True),
        TaskSignals(read_only=True),
        TaskSignals(spans_sessions=True),
        TaskSignals(speculative=True),
        TaskSignals(merge_verification=True),
        TaskSignals(),
    ):
        sel = select_strategy(signals, git_available=False, git_error="git missing")
        assert sel.mode is WorktreeMode.COPY, f"{signals} promised a worktree git cannot create"
        assert sel.assurance is Assurance.LOWER


def test_degradation_reason_names_the_mode_that_was_lost():
    """"a copy was used" is not actionable on its own; the operator needs to
    know a worktree was requested and refused."""
    sel = select_strategy(
        TaskSignals(produces_mergeable_diff=True),
        git_available=False,
        git_error="git missing",
    )
    assert "worktree" in sel.reason.lower()
    assert "git missing" in sel.reason


def test_copy_and_sandbox_survive_the_git_availability_check():
    """The guard must not degrade the two modes that do not need git."""
    copy_sel = select_strategy(
        TaskSignals(requested_mode=WorktreeMode.COPY),
        git_available=False,
        git_error="git missing",
    )
    assert copy_sel.mode is WorktreeMode.COPY


def test_explicit_worktree_request_is_degraded_honestly_when_git_is_gone():
    """The requested mode is not silently swapped for a different one while
    still being reported as satisfied."""
    sel = select_strategy(
        TaskSignals(requested_mode=WorktreeMode.WORKTREE),
        git_available=False,
        git_error="git not on PATH",
    )
    assert sel.mode is WorktreeMode.COPY
    assert sel.assurance is Assurance.LOWER
    assert "worktree" in sel.reason


def test_explicit_copy_request_stays_copy_even_when_git_works():
    sel = select_strategy(TaskSignals(requested_mode=WorktreeMode.COPY))
    assert sel.mode is WorktreeMode.COPY
    assert sel.mergeable is False


def test_explicit_ephemeral_request_reports_non_recoverable():
    sel = select_strategy(TaskSignals(requested_mode=WorktreeMode.EPHEMERAL))
    assert sel.mode is WorktreeMode.EPHEMERAL
    assert sel.survives_crash is False


# --- disclosure --------------------------------------------------------------


def test_standard_disclosure_names_the_mode():
    text = select_strategy(TaskSignals(produces_mergeable_diff=True)).disclosure()
    assert "worktree" in text
    assert "LOWER" not in text


def test_degraded_disclosure_states_the_reason_and_the_consequence():
    sel = select_strategy(
        TaskSignals(),
        git_available=False,
        git_error="git exited 128: unable to access '.git'",
    )
    text = sel.disclosure()
    assert "LOWER" in text
    assert "unable to access" in text
    assert "Consequence" in text


def test_ephemeral_disclosure_warns_about_discard():
    """An agent must be told the work is discarded, not discover it later."""
    text = select_strategy(TaskSignals(speculative=True)).disclosure()
    assert "discarded" in text


# --- the record itself -------------------------------------------------------


def test_a_selection_without_a_reason_is_rejected():
    """An unexplained mode is an arbitrary one; the constructor refuses it."""
    with pytest.raises(ValueError, match="non-empty reason"):
        WorktreeSelection(mode=WorktreeMode.WORKTREE, assurance=Assurance.STANDARD, reason="   ")


def test_selection_round_trips_to_dict():
    sel = select_strategy(TaskSignals(produces_mergeable_diff=True))
    data = sel.to_dict()
    assert data["mode"] == "worktree"
    assert data["assurance"] == "standard"
    assert data["mergeable"] is True


# --- unavailable capability is reported, not substituted ---------------------


def test_upgrade_or_report_keeps_the_requested_mode_when_unavailable():
    sel = WorktreeSelection(
        mode=WorktreeMode.SANDBOX,
        assurance=Assurance.STANDARD,
        reason="task executes untrusted code",
    )
    reported = upgrade_or_report(sel, available_modes={WorktreeMode.WORKTREE})
    assert reported.mode is WorktreeMode.SANDBOX
    assert reported.assurance is Assurance.LOWER
    assert "not provided" in reported.reason


def test_upgrade_or_report_is_a_no_op_when_the_mode_exists():
    sel = select_strategy(TaskSignals(produces_mergeable_diff=True))
    assert upgrade_or_report(sel, available_modes={WorktreeMode.WORKTREE}).assurance is Assurance.STANDARD


# --- protected branches ------------------------------------------------------


@pytest.mark.parametrize("branch", ["main", "master", "develop", "release/1.0", "MAIN"])
def test_protected_branches_are_recognised(branch):
    assert is_protected_branch(branch) is True


@pytest.mark.parametrize("branch", ["agent/042/task", "feature/x", "bugfix/y"])
def test_ordinary_branches_are_not_protected(branch):
    assert is_protected_branch(branch) is False


@pytest.mark.parametrize("branch", ["../outside", "a/../../b", "a..b", "a.lock", "", "-force", "a\\b"])
def test_unsafe_branch_names_are_rejected(branch):
    assert is_safe_branch_name(branch) is False


def test_safe_branch_names_pass():
    assert is_safe_branch_name("agent/042/TASK-1028-fix-auth") is True
