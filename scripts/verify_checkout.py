"""Verify a checkout / worktree is COMPLETE before you trust or commit it.

Why this exists
---------------
`git worktree add` on this large repository (Windows, long paths, ~12k files) has
been observed to produce an INCOMPLETE working tree *with no error*: files that
are present in the index/HEAD are simply missing from disk. Committing from such
a tree is dangerous — ``git add <dir>`` then stages MASS DELETIONS and can create
a commit that removes thousands of files.

This script fails loudly when a checkout is short, so the failure is caught
before anyone stages or commits. It is intentionally dependency-free.

Usage:
    python scripts/verify_checkout.py [--repo PATH] [--max-missing N] [--json]

Exit codes:
    0  checkout is complete
    1  checkout is incomplete (files missing) or a required path is absent
    2  not a git repository / git unavailable
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: Paths that must exist for the checkout to be usable.
REQUIRED_PATHS = (
    "Makefile",
    "AGENTS.md",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/tests",
    "frontend/package.json",
)


@dataclass
class CheckoutReport:
    """Result of a checkout-completeness check."""

    repo: str
    tracked: int
    missing: list[str] = field(default_factory=list)
    required_missing: list[str] = field(default_factory=list)
    max_missing: int = 0

    @property
    def ok(self) -> bool:
        return not self.required_missing and len(self.missing) <= self.max_missing

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "tracked": self.tracked,
            "missing": len(self.missing),
            "required_missing": self.required_missing,
            "max_missing": self.max_missing,
            "ok": self.ok,
        }

    def render(self) -> str:
        lines = [
            f"Checkout: {self.repo}",
            f"  tracked files:      {self.tracked}",
            f"  missing from disk:  {len(self.missing)} (allowed <= {self.max_missing})",
        ]
        if self.required_missing:
            lines.append(f"  required paths MISSING: {self.required_missing}")
        if not self.ok:
            lines.append("")
            lines.append("RESULT: INCOMPLETE checkout — do NOT stage or commit from here.")
            lines.append("  Fix: re-run `git checkout -- .` in this worktree (or recreate it),")
            lines.append("       then verify: git ls-files --deleted | wc -l   # must be 0")
            for path in self.missing[:20]:
                lines.append(f"    missing: {path}")
            if len(self.missing) > 20:
                lines.append(f"    ... and {len(self.missing) - 20} more")
        else:
            lines.append("RESULT: checkout looks complete.")
        return "\n".join(lines)


def evaluate(repo: str, tracked: int, missing: list[str], required_missing: list[str], *, max_missing: int = 0) -> CheckoutReport:
    """Pure check used by both the CLI and the tests."""
    return CheckoutReport(repo=repo, tracked=tracked, missing=list(missing), required_missing=list(required_missing), max_missing=max_missing)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def check_repo(repo: Path, *, max_missing: int = 0) -> CheckoutReport:
    """Gather the facts for a real repository and evaluate them."""
    tracked = [line for line in _git(repo, "ls-files").splitlines() if line]
    missing = [line for line in _git(repo, "ls-files", "--deleted").splitlines() if line]
    required_missing = [p for p in REQUIRED_PATHS if not (repo / p).exists()]
    return evaluate(str(repo), len(tracked), missing, required_missing, max_missing=max_missing)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a git checkout is complete.")
    parser.add_argument("--repo", default=".", help="repository / worktree root (default: cwd)")
    parser.add_argument("--max-missing", type=int, default=0, help="tolerated number of missing tracked files")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    try:
        report = check_repo(repo, max_missing=args.max_missing)
    except subprocess.CalledProcessError as exc:
        print(f"not a git repository (or git failed): {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print("git is not installed or not on PATH", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
