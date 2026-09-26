"""``alpha --json --recursion-limit N "<task>"`` must actually run the task.

Why this has its own file
-------------------------
This is a *pure planning* test: no client, no provider, no money, no network.
That matters because the only other coverage of this argv form is the live
integration test, which costs a real provider call to check something a
``plan_launch`` call decides in microseconds. A regression here would otherwise
be caught only by a paid run.

The bug
-------
``--print``/``--json`` take their message as an **optional** value
(``nargs="?"``). So in the documented long-task form

    alpha --json --recursion-limit 250 "do the thing"

argparse sees an option following ``--json``, binds the flag to its ``const``
(None), and leaves the task stranded in the *positional* list -- which
``plan_launch`` then discarded. The command exited **2** with
``--json needs a MESSAGE argument or piped stdin.`, and the bare form
``alpha --json "task"`` was the only one that worked.

The failure mode worth naming: when the task is dropped rather than the flag
being misread, a caller can get a clean exit having answered nothing. So the
assertion is that the task survives into the plan, not merely that the mode is
right.
"""

from __future__ import annotations

import pytest

from alpha.tui.cli import plan_launch

_TASK = "summarise the repository README"


def _plan(argv, *, stdin_tty: bool = True, stdout_tty: bool = False):
    return plan_launch(argv, stdin_isatty=stdin_tty, stdout_isatty=stdout_tty, env={})


@pytest.mark.parametrize("flag", ["--json", "--print"])
def test_bare_word_task_is_not_dropped_when_an_option_follows_the_flag(flag):
    """The documented acceptance form, resolved offline.

    The exact form the acceptance criteria use for ``--json``; parametrised over
    ``--print`` because it shares the same optional-value parser and therefore
    the same bug.
    """
    plan = _plan([flag, "--recursion-limit", "250", _TASK])
    assert plan.mode == flag.lstrip("-")
    assert plan.message == _TASK, "the task was dropped; the run would answer nothing"
    assert plan.recursion_limit == 250
    assert plan.read_stdin is False


def test_explicit_message_still_wins_over_the_positional():
    """``--json "message"`` is unchanged: an explicit value is not overridden."""
    plan = _plan(["--json", "explicit", _TASK])
    assert plan.mode == "json"
    assert plan.message == "explicit"


def test_positional_preserves_its_spaces():
    """The task is re-joined from the positional list, not taken from argv[0].

    Guards the join: taking only the first word would silently truncate a long
    task, which looks identical to "worked" for a one-word prompt.
    """
    plan = _plan(["--json", "--recursion-limit", "250", "explain", "the", "codebase"])
    assert plan.message == "explain the codebase"


def test_flag_with_no_task_anywhere_still_falls_back_to_stdin():
    """The fix must not turn "no message" into an empty-message run.

    Piped stdin is still how an answer is supplied when no task is given at
    all, and that behaviour is load-bearing for ``echo ... | alpha --print``.
    """
    plan = _plan(["--print"], stdin_tty=False)
    assert plan.mode == "print"
    assert plan.message is None
    assert plan.read_stdin is True


def test_no_task_on_a_tty_is_still_a_usage_error():
    """On a TTY with nothing to read, it must stay a usage error (exit 2)."""
    plan = _plan(["--json"], stdin_tty=True)
    assert plan.mode == "headless-help"
    assert plan.reason
