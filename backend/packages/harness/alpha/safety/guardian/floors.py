"""Permanent allowlist rules and floor matching for Smart Approvals."""

from __future__ import annotations

import fnmatch


class PermanentAllowlist:
    """Stores permanent regex and glob patterns that automatically bypass interactive prompts."""

    DEFAULT_PATTERNS = [
        "git status*",
        "git log*",
        "git diff*",
        "git branch*",
        "pytest*",
        "python -m pytest*",
        "npm test*",
        "cargo test*",
        "ls*",
        "dir*",
        "pwd",
        "echo *",
        "cat *",
        "type *",
    ]

    def __init__(self, patterns: list[str] | None = None):
        self._patterns: set[str] = set(patterns or self.DEFAULT_PATTERNS)

    def add_pattern(self, pattern: str) -> None:
        self._patterns.add(pattern.strip())

    def is_allowlisted(self, command: str) -> bool:
        cmd = command.strip()
        for pat in self._patterns:
            if fnmatch.fnmatch(cmd, pat):
                return True
        return False


_global_allowlist = PermanentAllowlist()


def get_permanent_allowlist() -> PermanentAllowlist:
    return _global_allowlist
