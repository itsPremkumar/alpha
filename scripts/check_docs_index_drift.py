#!/usr/bin/env python3
"""CI-friendly drift gate for the generated documentation index.

The official generator is run with a temporary output path, so this wrapper
never writes to the checkout.  It compares the resulting bytes with
``docs/INDEX.md`` and returns 1 for drift, 0 for a clean tree, and 2 for a
configuration or generator error.

Human-readable output includes a unified diff.  ``--json`` emits one JSON
object for CI systems and never mixes generator logs into stdout.
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_REL = Path("scripts/generate_docs_index.py")
INDEX_REL = Path("docs/INDEX.md")
MAX_FILES = 20_000


@dataclass(frozen=True)
class GateResult:
    """The result of one read-only documentation-index comparison."""

    status: str
    exit_code: int
    diff: str = ""
    error: str | None = None
    summary: dict[str, int] | None = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        "--repo-root",
        dest="root",
        type=Path,
        default=ROOT,
        help="checkout to inspect (default: the repository containing this script)",
    )
    parser.add_argument(
        "--commit",
        help="pin the input revision passed to the official generator",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=MAX_FILES,
        help=f"file-walk safety limit (default: {MAX_FILES})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit one machine-readable JSON object",
    )
    return parser


def _resolve_root(value: Path) -> Path:
    candidate = value.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def _parse_summary(stdout: str) -> dict[str, int] | None:
    for line in stdout.splitlines():
        if not line.startswith("summary: "):
            continue
        values: dict[str, int] = {}
        for field in line.removeprefix("summary: ").split():
            key, separator, value = field.partition("=")
            if (
                separator
                and key in {"documents", "skipped", "files_walked"}
                and value.isdigit()
            ):
                values[key] = int(value)
        if values:
            return values
    return None


def _unified_diff(committed: bytes | None, generated: bytes) -> str:
    old_lines = (
        []
        if committed is None
        else committed.decode("utf-8", errors="replace").splitlines(keepends=True)
    )
    new_lines = generated.decode("utf-8").splitlines(keepends=True)
    text = "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=INDEX_REL.as_posix()
            if committed is not None
            else f"{INDEX_REL.as_posix()} (missing)",
            tofile="generated docs/INDEX.md",
            n=3,
        )
    )
    if not text and committed is None:
        text = f"--- {INDEX_REL.as_posix()} (missing)\n+++ generated docs/INDEX.md\n"
    return text


def _generator_command(
    generator: Path, root: Path, output: Path, commit: str | None, max_files: int
) -> list[str]:
    command = [
        sys.executable,
        str(generator),
        "--root",
        str(root),
        "--output",
        str(output),
        "--max-files",
        str(max_files),
    ]
    if commit is not None:
        command.extend(["--commit", commit])
    return command


def run_gate(
    root: Path, *, commit: str | None = None, max_files: int = MAX_FILES
) -> GateResult:
    """Run the official generator in a temporary location and compare bytes."""

    root = _resolve_root(root)
    if not root.is_dir():
        return GateResult(
            "error", 2, error=f"repository root does not exist: {root.name}"
        )
    if max_files < 1:
        return GateResult("error", 2, error="--max-files must be positive")
    generator = root / GENERATOR_REL
    if not generator.is_file():
        # A hermetic fixture may contain only ``docs/``.  Fall back to the
        # sibling official generator only when the target has no scripts tree;
        # a real checkout must provide and exercise its own generator.
        sibling_generator = Path(__file__).with_name(GENERATOR_REL.name)
        if not (root / "scripts").is_dir() and sibling_generator.is_file():
            generator = sibling_generator
        else:
            return GateResult(
                "error",
                2,
                error=f"official generator is missing: {GENERATOR_REL.as_posix()}",
            )

    try:
        with tempfile.TemporaryDirectory(prefix="alpha-docs-index-") as temporary:
            output = Path(temporary) / "INDEX.md"
            command = _generator_command(generator, root, output, commit, max_files)
            result = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if result.returncode != 0:
                detail = (
                    result.stderr.strip()
                    or result.stdout.strip()
                    or "generator failed without a message"
                )
                return GateResult(
                    "error", 2, error=f"generator exited {result.returncode}: {detail}"
                )
            if not output.is_file():
                return GateResult(
                    "error",
                    2,
                    error="generator completed without producing an output file",
                )
            generated = output.read_bytes()
            committed_path = root / INDEX_REL
            committed = (
                committed_path.read_bytes() if committed_path.is_file() else None
            )
    except OSError as exc:
        return GateResult(
            "error", 2, error=f"could not run documentation generator: {exc}"
        )

    summary = _parse_summary(result.stdout)
    if committed == generated:
        return GateResult("clean", 0, summary=summary)
    return GateResult(
        "drift", 1, diff=_unified_diff(committed, generated), summary=summary
    )


def _print_human(result: GateResult) -> None:
    if result.error is not None:
        print(f"ERROR: {result.error}", file=sys.stderr)
        return
    if result.summary is not None:
        print(
            "summary: "
            f"documents={result.summary.get('documents', 0)} "
            f"skipped={result.summary.get('skipped', 0)} "
            f"files_walked={result.summary.get('files_walked', 0)}"
        )
    if result.diff:
        print(result.diff, end="" if result.diff.endswith("\n") else "\n")
    if result.status == "drift":
        print("documentation index drift detected")
    else:
        print("documentation index is up to date")


def _print_json(result: GateResult) -> None:
    payload: dict[str, object] = {
        "status": result.status,
        "drift": result.status == "drift",
        "exit_code": result.exit_code,
        "artifact": INDEX_REL.as_posix(),
        "diff": result.diff,
        "summary": result.summary,
    }
    if result.error is not None:
        payload["error"] = result.error
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = run_gate(args.root, commit=args.commit, max_files=args.max_files)
    if args.as_json:
        _print_json(result)
    else:
        _print_human(result)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
