"""The `bash` tool must refuse irreversible git operations.

`classify_git_push` is unit-tested on its own; this file proves the guard is
actually *reached* from the tool, which is the part that was missing entirely.
`autonomy_guard.IRREVERSIBLE_ACTIONS` listed `force_push` and
`git_push_protected`, and `assert_requires_approval` had no production caller,
so nothing stopped an agent with a `bash` tool from force-pushing over `main`.

The guard runs as the first statement of `bash_tool`, before
`ensure_sandbox_initialized`, so a refusal is proven with no sandbox, no
provider, and no container. The pass-through cases are asserted against the
guard helper rather than the whole tool, because continuing past the guard needs
a real sandbox and that is a different test's job.
"""

from __future__ import annotations

import pytest

from alpha.sandbox.git_push_guard import GIT_PUSH_GUARD_MESSAGE, classify_git_push
from alpha.sandbox.tools import _check_protected_git_push, bash_tool


class _Runtime:
    """Placeholder runtime: a blocked command never reaches anything that uses it."""

    context: dict = {}


def _always_block_protected_pushes(command: str):
    """Stand in for the helper, forcing a verdict regardless of the config file."""
    return classify_git_push(command, current_branch="main")


@pytest.mark.parametrize(
    "command",
    [
        "git push --force origin main",
        "git push -f",
        "git push origin HEAD:refs/heads/main",
        "cd /tmp && git push --force origin main",
        "git -C /repo push --force",
    ],
)
def test_bash_refuses_irreversible_git(monkeypatch, command: str):
    monkeypatch.setattr("alpha.sandbox.tools._check_protected_git_push", _always_block_protected_pushes)
    out = bash_tool.func(command=command, description="test", runtime=_Runtime())
    assert out.startswith("Error:"), out
    assert GIT_PUSH_GUARD_MESSAGE.split(".")[0] in out


def test_bash_refusal_names_the_operator_opt_in(monkeypatch):
    """A refusal that leaves the agent guessing whether the command is broken is bad."""
    monkeypatch.setattr("alpha.sandbox.tools._check_protected_git_push", _always_block_protected_pushes)
    out = bash_tool.func(command="git push --force origin main", description="t", runtime=_Runtime())
    assert "Reason:" in out
    assert "irreversible" in out
    assert "allow_protected_git_push" in out


def test_guard_declines_an_ordinary_feature_branch_push(monkeypatch):
    """The guard must not stand in the way of normal work."""
    monkeypatch.setattr("alpha.sandbox.tools._check_protected_git_push", _check_protected_git_push)
    assert _check_protected_git_push("git push origin feature/my-work") is None
    assert _check_protected_git_push("git status") is None
    assert _check_protected_git_push("git commit -m 'wip'") is None


def test_guard_blocks_a_protected_push_through_the_real_helper(monkeypatch):
    """Exercise the real helper (config read included), not a stub."""
    monkeypatch.setattr("alpha.sandbox.tools._check_protected_git_push", _check_protected_git_push)
    # The helper resolves the current branch itself; pin it so the test does not
    # depend on the checkout it runs in.
    monkeypatch.setattr("alpha.sandbox.tools._current_git_branch", lambda: "main")
    verdict = _check_protected_git_push("git push")
    assert verdict is not None and verdict.blocked is True
    assert verdict.target_branch == "main"


def test_guard_respects_the_operator_opt_in(monkeypatch):
    """An operator who set `allow_protected_git_push: true` gets their push."""
    monkeypatch.setattr("alpha.sandbox.tools._check_protected_git_push", _check_protected_git_push)
    monkeypatch.setattr("alpha.sandbox.tools._current_git_branch", lambda: "main")

    class _Cfg:
        sandbox = type("_S", (), {"allow_protected_git_push": True})()

    class _App:
        pass

    monkeypatch.setattr("alpha.config.app_config.get_app_config", lambda: _App())
    monkeypatch.setattr(_App, "sandbox", property(lambda self: _Cfg.sandbox), raising=False)
    assert _check_protected_git_push("git push --force origin main") is None


def test_unreadable_config_does_not_silently_disable_the_guard(monkeypatch):
    """A config that cannot be read must fail toward enforcement, not away from it."""
    monkeypatch.setattr("alpha.sandbox.tools._check_protected_git_push", _check_protected_git_push)
    monkeypatch.setattr("alpha.sandbox.tools._current_git_branch", lambda: "main")

    def _boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("alpha.config.app_config.get_app_config", _boom)
    verdict = _check_protected_git_push("git push --force origin main")
    assert verdict is not None and verdict.blocked is True
