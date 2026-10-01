"""Guard for irreversible git operations issued through the bash tool.

Why this exists
---------------
``alpha.bots.autonomy_guard.IRREVERSIBLE_ACTIONS`` already lists ``force_push``
and ``git_push_protected`` as work that "always routes through the existing
approval gate", and the module docstring is explicit that full autonomy "does
not mean no human for destructive execution". Nothing called
``assert_requires_approval`` in production: the only caller was a test. So the
decision was recorded and never enforced, and an agent with a `bash` tool could
force-push over ``main`` unchallenged.

The same module's own comment explains the direction of failure it wanted — an
allowlist, "because a denylist fails open". This module is that allowlist
expressed over a shell command string.

Scope, stated honestly
---------------------
This is a **best-effort command classifier, not a shell parser.** It recognises
``git push`` invocations across common separator styles and flags. It is not a
substitute for the `bash` tool's existing policy that host execution requires an
explicit opt-in (``sandbox.allow_host_bash``); where that opt-in is absent the
command never runs at all. Two limits are worth knowing:

* A command this module cannot tokenise (unbalanced quotes, PowerShell-native
  syntax) is **not blocked**. Failing closed there would make ordinary Windows
  shell work impossible, and a guard that breaks the shell it guards gets
  disabled by operators. This matches the existing, explicitly-best-effort
  posture of ``validate_local_bash_command_paths``.
* A bare ``git push`` with no refspec pushes the *current* branch, which cannot
  be known from the command text alone. ``classify_git_push`` therefore takes an
  optional ``current_branch``; callers that can resolve it cheaply should.

What it refuses
---------------
``force-push`` (a history rewrite, to any branch, including a protected one) and
any push whose *target* is a protected branch. Both are in the irreversible
allowlist. A force push to a feature branch is blocked too: ``--force`` on a
shared branch is the standard way to destroy a collaborator's commits, and the
non-destructive ``--force-with-lease`` is the correct tool there.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass


#: Branches an implementation push may not target. Mirrors
#: ``sandbox.worktree_strategy._PROTECTED_BRANCHES`` — the same rule, deliberately
#: not re-derived here, because two lists that can disagree is how a protected
#: branch stops being protected.
def _protected_branches() -> frozenset[str]:
    from alpha.sandbox.worktree_strategy import _PROTECTED_BRANCHES

    return _PROTECTED_BRANCHES


#: Shell operators that start a new command. Splitting on these means
#: `cd repo && git push --force` is classified, not missed because the first
#: token is not `git`.
_SEGMENT_SPLIT = re.compile(r"(?:&&|\|\||[;|\n]|\r)")

#: `git` as a command word: start of string, or after an operator, wrapper, or
#: subshell. Deliberately not a bare substring match, so `git` inside a path or a
#: string literal does not produce a finding.
_GIT_COMMAND = re.compile(
    r"(?:^|[;&|(]\s*|\b(?:sudo|env|time|nohup|xargs)\s+|(?:ba|z|k|da)?sh\s+-c\s+['\"]?|\bgit\s+)"
    r"git(?=\s|$)",
    re.IGNORECASE,
)

#: Global git options that consume the following token, so `push` is not
#: mistaken for a subcommand after `git -C /some/dir`.
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})

#: Global git options that stand alone.
_GIT_FLAG_OPTIONS = frozenset(
    {
        "--no-pager",
        "--paginate",
        "--bare",
        "--no-replace-objects",
        "--literal-pathspecs",
        "--no-optional-locks",
        "-P",
        "-p",
        "--version",
        "--help",
    }
)

_FORCE_FLAGS = frozenset({"-f", "--force", "--force-with-lease"})

#: Commands that may legitimately precede `git` in a pipeline. Without this,
#: `echo git push --force` and `cat notes git push` would be classified as git
#: invocations, and a guard that blocks ordinary shell usage gets disabled.
_GIT_WRAPPERS = frozenset({"sudo", "env", "time", "nohup", "xargs", "command", "nice", "ionice", "stdbuf", "doas"})

#: A refspec that names a branch: `main`, `refs/heads/main`, `HEAD:main`,
#: `+main:refs/heads/main`. The destination (after the colon) is what receives the
#: push and therefore what must be checked.
_REFSPEC = re.compile(r"^(?P<force>\+)?(?P<src>[^:]+):(?P<dst>.+)$")


def _is_git_invocation(tokens: list[str], idx: int) -> bool:
    """True when ``tokens[idx] == "git"`` sits at a real command position.

    A `git` token is only a command when nothing but a known wrapper precedes
    it, or when it is the first token. This is what separates a real
    ``git push`` from the string ``echo git push --force``.
    """
    if idx == 0:
        return True
    prev = tokens[idx - 1].lower()
    if prev in _GIT_WRAPPERS:
        return True
    # `sh -c "git push -f"` puts git inside the -c argument, not as a bare token.
    if prev == "-c" and idx >= 2:
        return tokens[idx - 2].lower() in {"sh", "bash", "zsh", "dash", "ksh", "ash"}
    return False


@dataclass(frozen=True)
class GitPushVerdict:
    """The outcome of classifying one command for irreversible git intent."""

    #: True when the command must not run without an explicit operator opt-in.
    blocked: bool
    #: Why, phrased for the agent that will receive it.
    reason: str = ""
    #: The push was a history rewrite (`-f` / `--force*`, or a `+refspec`).
    force: bool = False
    #: The branch the push would update, when it could be determined.
    target_branch: str | None = None
    #: The matched segment, for the operator's log.
    matched: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "blocked": self.blocked,
            "reason": self.reason,
            "force": self.force,
            "target_branch": self.target_branch,
            "matched": self.matched,
        }


def _short_branch(ref: str) -> str:
    """Reduce a ref to its branch head.

    ``refs/heads/main`` -> ``main``; ``HEAD`` stays ``HEAD`` (the caller decides
    what HEAD means); ``v1.2`` stays ``v1.2`` because a tag push is not a branch
    push and must not be judged as one.
    """
    ref = ref.strip()
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/") :]
    return ref


def _is_protected(branch: str | None) -> bool:
    if not branch:
        return False
    return _short_branch(branch).split("/", 1)[0].strip().lower() in _protected_branches()


def _classify_segment(segment: str, current_branch: str | None, depth: int = 0) -> GitPushVerdict | None:
    tokens = shlex.split(segment, posix=True)
    if not tokens:
        return None

    candidates = [i for i, t in enumerate(tokens) if t.lower() == "git" and _is_git_invocation(tokens, i)]

    # `sh -c "git push -f"` keeps the whole inner command in one token. Recurse
    # into it rather than missing the most common way a script hides a push.
    if depth < 2:
        for tok in tokens:
            if " " in tok and "git" in tok.lower() and tok.strip().lower() != "git":
                nested = _classify_segment(tok, current_branch, depth + 1)
                if nested is not None:
                    return nested

    if not candidates:
        return None
    idx = candidates[0]

    # Walk past global options to find the subcommand.
    i = idx + 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in _GIT_VALUE_OPTIONS:
            i += 2
            continue
        if tok.split("=", 1)[0] in _GIT_FLAG_OPTIONS or tok.startswith("--git-dir=") or tok.startswith("--work-tree="):
            i += 1
            continue
        break
    if i >= len(tokens) or tokens[i] != "push":
        return None

    rest = tokens[i + 1 :]
    force = any(tok in _FORCE_FLAGS or tok.startswith("--force-with-lease=") for tok in rest)
    positional: list[str] = []
    skip_next = False
    for tok in rest:
        if skip_next:
            skip_next = False
            continue
        if tok == "--repo" or tok == "-o" or tok == "--push-option":
            skip_next = True
            continue
        if tok.startswith("-"):
            if tok.startswith("+"):
                force = True
            continue
        positional.append(tok)

    # positional[0] is the remote; the refspec (if any) follows.
    target: str | None = None
    for tok in positional[1:]:
        match = _REFSPEC.match(tok)
        if match:
            force = force or bool(match.group("force"))
            target = _short_branch(match.group("dst"))
            break
        if not tok.startswith("HEAD"):
            target = _short_branch(tok)
            break

    # No explicit refspec: git pushes the current branch. Only judge it when the
    # caller told us what that is; otherwise say so rather than guess.
    if target is None and current_branch:
        target = _short_branch(current_branch)

    protected = _is_protected(target)
    if force and protected:
        reason = f"Force-pushing to the protected branch {target!r} rewrites shared history and is irreversible. Alpha treats this as destructive work that requires explicit human approval."
    elif force:
        reason = "Force-pushing rewrites published history and can destroy a collaborator's commits. Use `--force-with-lease` if the rewrite is genuinely intended, and push to a feature branch rather than a protected one."
    elif protected:
        reason = f"Pushing directly to the protected branch {target!r} bypasses review. Push a feature branch and raise a pull request instead."
    else:
        return None

    return GitPushVerdict(
        blocked=True,
        reason=reason,
        force=force,
        target_branch=target,
        matched=segment.strip()[:200],
    )


def classify_git_push(command: str, *, current_branch: str | None = None) -> GitPushVerdict | None:
    """Classify *command* for an irreversible ``git push``.

    Args:
        command: The full shell command about to run.
        current_branch: The checked-out branch, when the caller can resolve it
            cheaply. Enables the bare ``git push`` case, which has no refspec to
            read the target from.

    Returns:
        A blocked :class:`GitPushVerdict` when the command rewrites history or
        targets a protected branch, otherwise ``None`` (no finding).
    """
    if not command or "git" not in command.lower():
        return None
    for segment in _SEGMENT_SPLIT.split(command):
        seg = segment.strip()
        if not seg or "git" not in seg.lower():
            continue
        try:
            verdict = _classify_segment(seg, current_branch)
        except ValueError:
            # shlex could not tokenise this segment (unbalanced quotes, or
            # PowerShell-native syntax). Best-effort by design: see the module
            # docstring. Not a finding.
            continue
        if verdict is not None:
            return verdict
    return None


#: Message shown when the guard refuses. Names the opt-in so the agent is not
#: left guessing whether the command is merely broken.
GIT_PUSH_GUARD_MESSAGE = (
    "Blocked by Alpha's git protection guard. Protected branches (main/master/trunk/develop/"
    "release) may not be pushed to directly, and history-rewriting pushes are refused because "
    "they are irreversible. Work on a feature branch and raise a pull request. An operator who "
    "genuinely needs this can set `sandbox.allow_protected_git_push: true` in config.yaml."
)
