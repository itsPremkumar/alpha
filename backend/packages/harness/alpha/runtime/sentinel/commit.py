"""Scope-limited, safety-checked git commit for the Sentinel.

Nothing in the system could commit before this. The Sentinel needs to, but it is
the single most dangerous thing it does — so every rail here is deliberate.

Rails enforced in this module:
- Only explicitly listed paths are staged. Never ``git add -A``: this repo
  frequently has unrelated files dirty from parallel work, and sweeping them into
  a repair commit would be silent corruption.
- A allowlist/denylist guard refuses to commit paths that must never be
  automated (git internals, secrets, lockfiles-in-progress).
- No destructive verbs. ``reset --hard``, force push and history rewriting are
  rejected before a subprocess is ever spawned.
- Pushing is opt-in and off by default. Auto-commit is not auto-push.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Wall-clock ceiling for the local git calls below. A commit runs arbitrary
#: repository hooks and a push talks to a remote, so neither was previously
#: bounded: a hung hook or an unreachable remote parked the Sentinel loop with
#: no way to report a failure.
_COMMIT_TIMEOUT_SECONDS = 120.0

#: Grace period for reaping a git process that has just been killed. Bounds the
#: kill path so a surviving hook process holding the pipes cannot turn a bounded
#: command into a hang.
_REAP_TIMEOUT_SECONDS = 5.0

#: Never allow automated commits to touch these.
FORBIDDEN_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)\.git(/|$)"),
    re.compile(r"\.env($|\.)"),
    re.compile(r"(^|/)secrets?/"),
    # Extension-bearing key material, plus extensionless SSH keys (id_rsa has no
    # suffix, so an extension-only rule would miss it).
    re.compile(r"\.pem$|\.key$|\.p12$|\.pfx$"),
    re.compile(r"(^|/)id_(rsa|dsa|ecdsa|ed25519)$"),
    re.compile(r"(^|/)node_modules(/|$)"),
    re.compile(r"(^|/)\.venv(/|$)"),
    re.compile(r"\.lock$"),
)

#: Rejected outright, before any process is spawned.
FORBIDDEN_COMMANDS: tuple[str, ...] = (
    "reset --hard",
    "reset --hard ",
    "push --force",
    "push -f",
    "rebase",
    "filter-branch",
    "clean -fd",
    "checkout -- .",
)


@dataclass
class CommitResult:
    ok: bool
    sha: str | None = None
    message: str = ""
    staged: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "sha": self.sha,
            "message": self.message,
            "staged": list(self.staged),
            "refused": list(self.refused),
            "error": self.error,
        }


class SafetyError(Exception):
    """Raised when a commit would violate a safety rail."""


class GitCommandTimeout(TimeoutError):
    """A git command exceeded its deadline; its process tree was killed."""


def _kill_process_tree(process: subprocess.Popen[Any]) -> None:
    """Terminate *process* and anything it started, best effort and bounded."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=_REAP_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            # No ``taskkill`` (stripped image): fall back to the direct child.
            with contextlib.suppress(OSError):
                process.kill()
        return
    with contextlib.suppress(OSError):
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()


def _run_git_bounded(args: list[str], *, cwd: str, timeout: float) -> tuple[int, str]:
    """Run ``git`` under a hard deadline, returning ``(returncode, output)``.

    ``subprocess.run(timeout=...)`` kills only the direct child, and git itself
    runs repository hooks, credential helpers and GPG agents: a survivor keeps
    the inherited pipes open, so on Windows the unbounded post-kill
    ``communicate()`` CPython performs would still block. Killing the whole group
    and reaping under its own bounded grace period makes the deadline actually
    terminate.
    """
    popen_kwargs: dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(["git", *args], cwd=cwd, **popen_kwargs)  # noqa: S603 - args validated by assert_safe_command
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            process.communicate(timeout=_REAP_TIMEOUT_SECONDS)
        raise GitCommandTimeout(f"git {' '.join(args)} did not complete within {timeout:g}s and was killed") from None
    return process.returncode, (stdout or "") + (stderr or "")


def is_forbidden_path(path: str) -> bool:
    normalised = str(path).replace("\\", "/")
    return any(p.search(normalised) for p in FORBIDDEN_PATH_PATTERNS)


def validate_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    """Split paths into (allowed, refused)."""
    allowed: list[str] = []
    refused: list[str] = []
    for p in paths:
        if is_forbidden_path(p):
            refused.append(p)
        else:
            allowed.append(p)
    return allowed, refused


def assert_safe_command(args: list[str]) -> None:
    """Reject destructive git invocations before spawning anything."""
    joined = " ".join(args).lower()
    for bad in FORBIDDEN_COMMANDS:
        if bad in joined:
            raise SafetyError(f"refusing destructive git command: {' '.join(args)!r}")


def build_commit_message(
    subject: str,
    *,
    why: str = "",
    what: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
) -> str:
    """Structured, evidence-bearing message so `git log` reads as repair history."""
    lines = [subject.strip()]
    if why:
        lines += ["", why.strip()]
    if what:
        lines += ["", "What changed:"] + [f"- {w}" for w in what]
    if evidence:
        lines += ["", "Evidence:"]
        for k, v in evidence.items():
            lines.append(f"- {k}: {v}")
    return "\n".join(lines)


class Committer:
    """Runs git commit with the Sentinel's rails applied."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        allow_push: bool = False,
        remote: str = "origin",
        dry_run: bool = False,
    ) -> None:
        self.repo_root = Path(repo_root)
        # Off by default and deliberately so: auto-commit is one decision,
        # auto-push is another.
        self.allow_push = allow_push
        self.remote = remote
        self.dry_run = dry_run

    def _run(self, args: list[str]) -> tuple[int, str]:
        assert_safe_command(args)
        return _run_git_bounded(args, cwd=str(self.repo_root), timeout=_COMMIT_TIMEOUT_SECONDS)

    def commit(
        self,
        paths: list[str],
        message: str,
        *,
        push: bool = False,
        branch: str | None = None,
    ) -> CommitResult:
        """Commit exactly ``paths``.

        Returns a CommitResult. It does not raise for ordinary failures — a
        failed commit is data the loop must act on, not an exception. A git
        command that had to be killed at its deadline is one of those failures,
        not a silent success: the paths are already staged, so the loop has to
        learn about it.
        """
        if not paths:
            return CommitResult(ok=False, error="no paths provided")

        allowed, refused = validate_paths(paths)
        if not allowed:
            return CommitResult(
                ok=False, refused=refused, error="every path was refused by the safety filter"
            )

        if self.dry_run:
            return CommitResult(ok=True, message=message, staged=allowed, refused=refused)

        try:
            return self._commit(allowed, refused, message, push=push, branch=branch)
        except GitCommandTimeout as exc:
            logger.error("Sentinel git command timed out: %s", exc)
            return CommitResult(ok=False, staged=allowed, refused=refused, error=str(exc))

    def _commit(
        self,
        allowed: list[str],
        refused: list[str],
        message: str,
        *,
        push: bool,
        branch: str | None,
    ) -> CommitResult:
        code, out = self._run(["add", "--", *allowed])
        if code != 0:
            return CommitResult(ok=False, staged=allowed, refused=refused, error=f"git add failed: {out.strip()}")

        from alpha.runtime.sentinel import checkpoint as cp

        msg_path = None
        try:
            # -F avoids shell quoting entirely, which matters on Windows where
            # commit messages routinely contain backslashes and quotes.
            msg_path = cp.write_temp_message(message)
            code, out = self._run(["commit", "-F", str(msg_path)])
            if code != 0:
                return CommitResult(ok=False, staged=allowed, refused=refused, error=f"git commit failed: {out.strip()}")
        finally:
            if msg_path is not None:
                cp.cleanup_temp_message(msg_path)

        code, sha_out = self._run(["rev-parse", "HEAD"])
        sha = sha_out.strip() if code == 0 else None

        if push:
            if not self.allow_push:
                return CommitResult(
                    ok=True, sha=sha, message=message, staged=allowed, refused=refused,
                    error="push requested but allow_push is False; commit landed locally only",
                )
            target = branch or "HEAD"
            code, out = self._run(["push", self.remote, target])
            if code != 0:
                return CommitResult(
                    ok=False, sha=sha, message=message, staged=allowed, refused=refused,
                    error=f"git push failed: {out.strip()}",
                )

        return CommitResult(ok=True, sha=sha, message=message, staged=allowed, refused=refused)
