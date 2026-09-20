"""Command-line entry point for the Sentinel repair loop.

This is what turns the loop into something a scheduler, cron job or human can
actually invoke:

    python -m agent_workspace.runtime.sentinel.cli --once
    python -m agent_workspace.runtime.sentinel.cli --interval 300
    python -m agent_workspace.runtime.sentinel.cli --once --dry-run

``--dry-run`` is important: it reports what *would* be fixed without touching the
working tree or creating commits. That is the safe way to point this at a repo
for the first time.

Push is never performed from the CLI. The loop commits locally; pushing stays a
deliberate, separately-enabled act.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _default_repo_root() -> Path:
    """Best-effort repo root: three levels up from this file's package dir."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return here.parents[4]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sentinel",
        description="Autonomous monitor / diagnose / fix / verify / commit loop.",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true",
                      help="Run a single pass and exit (default).")
    mode.add_argument("--interval", type=float, metavar="SECONDS",
                      help="Run forever, sleeping SECONDS between passes.")
    p.add_argument("--repo-root", type=Path, default=None,
                   help="Repository to operate on (default: nearest .git ancestor).")
    p.add_argument("--max-fixes", type=int, default=5,
                   help="Maximum repairs per pass (default 5).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report only; do not modify files or commit.")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="Emit the run report as JSON.")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return p


def run(args: argparse.Namespace) -> int:
    from agent_workspace.runtime.sentinel.commit import Committer
    from agent_workspace.runtime.sentinel.runner import (
        SentinelRunner,
        make_default_fix_fns,
    )

    repo_root = args.repo_root or _default_repo_root()
    repo_root = Path(repo_root).resolve()

    # No verification commands are wired by default: verification must be
    # supplied deliberately, and an empty check set fails closed (the loop
    # reverts rather than commits). Running with none configured therefore
    # escalates every fault instead of fixing it, which is the safe default.
    verification_commands: dict[str, list[str]] = {}

    committer = Committer(repo_root, dry_run=args.dry_run, allow_push=False)
    runner = SentinelRunner(
        repo_root,
        fix_fns=make_default_fix_fns(repo_root),
        verification_commands=verification_commands,
        committer=committer,
        max_fixes_per_run=args.max_fixes,
    )

    if args.interval:
        logger.info("Sentinel starting in daemon mode (interval=%ss)", args.interval)
        while True:
            report = runner.run_once()
            _emit(report.to_dict(), args.as_json)
            time.sleep(args.interval)
    else:
        report = runner.run_once()
        _emit(report.to_dict(), args.as_json)
        # Exit non-zero whenever the pass did not end cleanly, so a scheduler or
        # CI job notices. A "reverted" means a fix was attempted and failed
        # verification — that is news, not success, and must not be reported as
        # a green run.
        if report.escalated or report.reverted or report.errors:
            return 1
        return 0


def _emit(report: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2))
        return
    print(report.get("summary", ""))
    for outcome in report.get("outcomes", []):
        print(f"  - [{outcome['status']}] {outcome['kind']}: {outcome['detail']}")
    for err in report.get("errors", []):
        print(f"  ! {err}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
