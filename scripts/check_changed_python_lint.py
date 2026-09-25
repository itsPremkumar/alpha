#!/usr/bin/env python3
"""Run Ruff only on Python files changed by the current CI revision.

The repository has a disclosed, pre-existing Ruff debt backlog.  This gate is
intentionally incremental: a pull request or push must not introduce a new
lint or format finding in a file it owns, while the separate full-repository
report makes the remaining debt visible without pretending it is clean.

The file list comes from Git's trusted base/head comparison.  Deleted files are
not linted because there is no content to check.  No files are written.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--backend-root", type=Path)
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref")
    parser.add_argument("--before")
    parser.add_argument("--after")
    return parser


def _git(repo_root: Path, args: Sequence[str]) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo_root, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def _changed_python_paths(
    repo_root: Path,
    base_ref: str | None,
    head_ref: str | None,
    before: str | None,
    after: str | None,
) -> list[Path]:
    if base_ref and head_ref:
        if set(base_ref) == {"0"}:
            raw = _git(repo_root, ["ls-tree", "-r", "--name-only", "-z", head_ref])
        else:
            raw = _git(
                repo_root,
                [
                    "diff",
                    "--name-only",
                    "--diff-filter=ACMR",
                    "-z",
                    f"{base_ref}...{head_ref}",
                    "--",
                ],
            )
    elif before and after:
        if set(before) == {"0"}:
            raw = _git(repo_root, ["ls-tree", "-r", "--name-only", "-z", after])
        else:
            raw = _git(
                repo_root,
                [
                    "diff",
                    "--name-only",
                    "--diff-filter=ACMR",
                    "-z",
                    f"{before}..{after}",
                    "--",
                ],
            )
    else:
        raw = _git(repo_root, ["diff", "--name-only", "--diff-filter=ACMR", "-z", "HEAD", "--"])
        raw += _git(
            repo_root,
            ["ls-files", "--others", "--exclude-standard", "-z", "--", "*.py"],
        )
    paths: list[Path] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        relative = Path(item.decode("utf-8", errors="surrogateescape"))
        if relative.suffix == ".py" and (repo_root / relative).is_file():
            paths.append(relative)
    return sorted(set(paths), key=lambda path: path.as_posix())


def _ruff_command(backend_root: Path, subcommand: str, paths: Sequence[Path]) -> list[str]:
    uv = shutil.which("uv")
    if uv:
        return [
            uv,
            "run",
            "--no-sync",
            "ruff",
            subcommand,
            *[str(path) for path in paths],
        ]
    return [sys.executable, "-m", "ruff", subcommand, *[str(path) for path in paths]]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if bool(args.base_ref) != bool(args.head_ref):
        print("ERROR --base-ref and --head-ref must be provided together", file=sys.stderr)
        return 2
    if bool(args.before) != bool(args.after):
        print("ERROR --before and --after must be provided together", file=sys.stderr)
        return 2
    if args.base_ref and args.before:
        print(
            "ERROR choose either --base-ref/--head-ref or --before/--after",
            file=sys.stderr,
        )
        return 2

    repo_root = args.repo_root.resolve()
    backend_root = (args.backend_root or repo_root / "backend").resolve()
    try:
        paths = _changed_python_paths(repo_root, args.base_ref, args.head_ref, args.before, args.after)
    except (OSError, RuntimeError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1

    print(f"changed Python files: {len(paths)}")
    if not paths:
        print("incremental ruff gate: 0 (no changed Python files)")
        return 0
    for path in paths:
        print(f"  {path.as_posix()}")

    ruff_paths = [(repo_root / path).resolve() for path in paths]
    commands = (
        _ruff_command(backend_root, "check", ruff_paths),
        _ruff_command(backend_root, "format", ["--check", *ruff_paths]),
    )
    for command in commands:
        print("running:", " ".join(command))
        result = subprocess.run(command, cwd=backend_root, check=False)
        if result.returncode != 0:
            print(
                f"incremental ruff gate: FAILED (exit {result.returncode})",
                file=sys.stderr,
            )
            return result.returncode
    print("incremental ruff gate: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
