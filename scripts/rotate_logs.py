#!/usr/bin/env python3
"""Rotate Alpha launcher log files so no single log grows without bound.

Why this exists
---------------
``start.ps1`` grew a ``Rotate-LogIfLarge`` helper for the Windows launcher, and
nothing else grew one: ``start.sh``, ``start.bat``, ``watchdog.bat`` and the
Docker compose files all append to ``logs/*.log`` forever. This module is the
portable, dependency-free rotation those launchers were missing, so the root
``Makefile`` can call it once before ``make dev`` / ``make start`` on every
platform instead of duplicating the logic in four shell scripts.

It is a **pre-launch** rotator, not a live handler. A process holds its stdout
open on an inherited file descriptor, so renaming the file underneath a running
process is exactly the wrong thing to do while it is running; the safe moment is
before the process starts. That is also why ``start.ps1`` rotates at startup
only. Live rotation of a *Python-owned* sink is
:class:`logging.handlers.RotatingFileHandler`'s job, and that is what
``backend/debug.py`` now uses.

Naming
------
Backups are ``<name>.1``, ``<name>.2``, ... -- the layout
:class:`logging.handlers.RotatingFileHandler` produces, so a log rotated by this
script and one rotated by a Python handler in the same directory agree on where
generations live. (``start.ps1`` used ``<stem>.1.<ext>``; that shape keeps the
``.log`` suffix, so a *previous generation* would itself look like a rotation
candidate and the second pass would re-rotate it. This layout does not have that
problem: ``gateway.log.1`` does not end in ``.log``.) The oldest backup is dropped
when the budget is exhausted -- a rotation that can still fail to free space is
not a bound.

Usage
-----
::

    python scripts/rotate_logs.py                       # rotate ./logs and ./backend/logs
    python scripts/rotate_logs.py --logs-dir D --max-bytes 1048576 --backups 3
    python scripts/rotate_logs.py --json                # machine-readable result

Exit codes
----------
0  every candidate directory was processed (including "absent" and "nothing to do")
1  at least one file could not be rotated; the reason is reported per file
2  bad arguments
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

#: 5 MiB, matching the bound ``start.ps1`` applies today, so the two launchers
#: converge on one number instead of two.
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUPS = 3
#: Only these suffixes are considered. Rotating arbitrary files a user dropped
#: into ``logs/`` would be destructive, and the launcher only ever writes these.
#: A rotated generation (``gateway.log.1``) does not end in either, so it is
#: excluded without a special case.
LOG_SUFFIXES = (".log", ".txt")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


@dataclass(frozen=True)
class RotationResult:
    """The outcome for one log file."""

    path: str
    rotated: bool
    reason: str
    size_bytes: int | None = None
    backups: tuple[str, ...] = ()


def is_log_candidate(path: Path) -> bool:
    """Whether *path* is a launcher log this module may rotate."""
    if not path.is_file():
        return False
    lowered = path.name.lower()
    return any(lowered.endswith(suffix) for suffix in LOG_SUFFIXES)


def backup_path(path: Path, index: int) -> Path:
    """Return the *index*-th backup name for *path* (``gateway.log`` -> ``gateway.log.1``)."""
    return path.with_name(f"{path.name}.{index}")


def _shift_backups(path: Path, backups: int) -> list[str]:
    """Move ``<name>.N.<ext>`` up by one so slot 1 is free. Oldest is dropped."""
    shifted: list[str] = []
    for index in range(backups, 1, -1):
        source = backup_path(path, index - 1)
        if not source.exists():
            continue
        target = backup_path(path, index)
        if target.exists():
            target.unlink()
        source.replace(target)
        shifted.append(str(target))
    return shifted


def rotate_file(
    path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES, backups: int = DEFAULT_BACKUPS
) -> RotationResult:
    """Rotate *path* if it exceeds *max_bytes*, keeping at most *backups* generations.

    A file at or below the threshold is left untouched and reported as such: the
    caller gets a per-file reason instead of having to infer silence.
    """
    if backups < 1:
        raise ValueError("backups must be at least 1")
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")
    try:
        size = path.stat().st_size
    except OSError as exc:
        return RotationResult(
            path=str(path),
            rotated=False,
            reason=f"stat failed: {type(exc).__name__}: {exc}",
        )
    if size <= max_bytes:
        return RotationResult(
            path=str(path),
            rotated=False,
            reason=f"within budget ({size} <= {max_bytes} bytes)",
            size_bytes=size,
        )
    try:
        shifted = _shift_backups(path, backups)
        path.replace(backup_path(path, 1))
    except OSError as exc:
        return RotationResult(
            path=str(path),
            rotated=False,
            reason=f"rotate failed: {type(exc).__name__}: {exc}",
            size_bytes=size,
        )
    kept = (str(backup_path(path, 1)), *shifted)
    return RotationResult(
        path=str(path),
        rotated=True,
        reason=f"rotated {size} bytes",
        size_bytes=size,
        backups=kept,
    )


def rotate_directory(
    directory: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backups: int = DEFAULT_BACKUPS,
) -> list[RotationResult]:
    """Rotate every launcher log in *directory*. A missing directory is not an error."""
    if not directory.is_dir():
        return [
            RotationResult(
                path=str(directory), rotated=False, reason="directory absent"
            )
        ]
    results: list[RotationResult] = []
    candidates = 0
    for entry in sorted(directory.iterdir()):
        if is_log_candidate(entry):
            candidates += 1
            results.append(rotate_file(entry, max_bytes=max_bytes, backups=backups))
    if not results:
        # Distinguish "the directory is empty" from "only rotated generations are
        # present". The second case is the normal state right after a rotation, and
        # reporting it as an empty directory reads as "the logs vanished".
        generations = sorted(
            entry.name for entry in directory.iterdir() if entry.is_file()
        )
        reason = (
            "no log files present"
            if not generations
            else f"nothing to rotate; {len(generations)} rotated generation(s) present"
        )
        results.append(
            RotationResult(path=str(directory), rotated=False, reason=reason)
        )
    return results


def default_log_dirs(project_root: Path) -> list[Path]:
    """The log directories every Alpha launcher writes into.

    ``<root>/logs`` is what ``start.ps1``/``start.sh``/``start.bat`` and the
    Make targets use; ``<root>/backend/logs`` and ``<root>/backend/debug.log``'s
    directory cover a backend-local run started from ``backend/``.
    """
    return [project_root / "logs", project_root / "backend" / "logs"]


def rotate_logs(
    project_root: Path,
    *,
    log_dirs: list[Path] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backups: int = DEFAULT_BACKUPS,
) -> dict[str, list[RotationResult]]:
    """Rotate every default (or supplied) log directory under *project_root*."""
    directories = log_dirs if log_dirs is not None else default_log_dirs(project_root)
    return {
        str(directory): rotate_directory(
            directory, max_bytes=max_bytes, backups=backups
        )
        for directory in directories
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rotate Alpha launcher log files so no single log grows without bound."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Alpha project root (default: the checkout this script lives in)",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        action="append",
        default=None,
        dest="log_dirs",
        help="Log directory to rotate; repeatable. Defaults to <root>/logs and <root>/backend/logs.",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"Rotate a log larger than this (default: {DEFAULT_MAX_BYTES})",
    )
    parser.add_argument(
        "--backups",
        type=int,
        default=DEFAULT_BACKUPS,
        help=f"Generations of rotated logs to keep (default: {DEFAULT_BACKUPS})",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit the result as JSON instead of lines"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_bytes < 1:
        print("error: --max-bytes must be >= 1", file=sys.stderr)
        return EXIT_USAGE
    if args.backups < 1:
        print("error: --backups must be >= 1", file=sys.stderr)
        return EXIT_USAGE

    report = rotate_logs(
        args.project_root.resolve(),
        log_dirs=[path.resolve() for path in args.log_dirs] if args.log_dirs else None,
        max_bytes=args.max_bytes,
        backups=args.backups,
    )
    flat = [result for results in report.values() for result in results]
    failures = [
        result
        for result in flat
        if result.reason.startswith(("stat failed", "rotate failed"))
    ]

    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "max_bytes": args.max_bytes,
                    "backups": args.backups,
                    "directories": {
                        directory: [asdict(result) for result in results]
                        for directory, results in report.items()
                    },
                    "rotated": sum(1 for result in flat if result.rotated),
                    "failed": len(failures),
                },
                indent=2,
            )
        )
    else:
        for directory, results in report.items():
            for result in results:
                marker = "rotated" if result.rotated else "kept"
                suffix = (
                    f" -> {', '.join(os.path.basename(item) for item in result.backups)}"
                    if result.backups
                    else ""
                )
                print(f"[{marker}] {result.path}: {result.reason}{suffix}")
        if not any(result.rotated for result in flat):
            print("No log file exceeded the size budget; nothing rotated.")
    return EXIT_FAILED if failures else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
