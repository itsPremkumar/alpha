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

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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
        proc = subprocess.run(  # noqa: S603 - args validated above
            ["git", *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

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
        failed commit is data the loop must act on, not an exception.
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
