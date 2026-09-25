#!/usr/bin/env python3
"""Fail when committed generated artifacts differ from a fresh official build.

This gate runs the documented command::

    python backend/scripts/generate_feature_manifest.py --output-dir <temporary-directory>

The official generator reads this checkout but writes only into the temporary
output directory.  The gate compares the fresh manifest with
``contracts/feature_manifest.json`` and compares any committed files under
``docs/INDEX/`` with the corresponding generator output when that layer exists.
The manifest's wall-clock ``generated_at`` value is reported as volatile
metadata and is the only field ignored; every other byte is drift.  A missing
or failing generator, a missing output, malformed JSON, or any diff exits 1.

The optional ``--diff-output`` path is for CI artifacts and must be outside the
repository.  The gate never writes into the checkout.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_REL = Path("backend/scripts/generate_feature_manifest.py")
MANIFEST_REL = Path("contracts/feature_manifest.json")
INDEX_REL = Path("docs/INDEX")
GENERATOR_COMMAND_DOC = "python backend/scripts/generate_feature_manifest.py --output-dir <temporary-directory>"
_VOLATILE_MANIFEST_FIELD = re.compile(rb'(?m)^(?P<prefix>\s*"generated_at"\s*:\s*)"(?P<value>[^"]*)"(?P<suffix>,?)$')


@dataclass(frozen=True)
class ArtifactComparison:
    """One generated-artifact comparison and its human-readable evidence."""

    relative_path: Path
    committed_path: Path | None
    generated_path: Path | None
    diff_lines: tuple[str, ...]
    changed_lines: int
    error: str | None = None
    ignored_timestamp: bool = False

    @property
    def drifted(self) -> bool:
        return self.error is not None or self.changed_lines > 0 or self.committed_path is None or self.generated_path is None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="Checkout to inspect (default: the repository containing this script).",
    )
    parser.add_argument(
        "--diff-output",
        type=Path,
        help="Write the failure report to this path; it must be outside --repo-root.",
    )
    parser.add_argument(
        "--max-diff-lines",
        type=int,
        default=200,
        help="Maximum unified-diff lines printed per file (default: 200).",
    )
    return parser


def generator_command(repo_root: Path, output_dir: Path) -> list[str]:
    """Return the exact argv used for the documented official generator command."""
    generator = (repo_root / GENERATOR_REL).resolve()
    return [sys.executable, str(generator), "--output-dir", str(output_dir.resolve())]


def _display_command(command: Sequence[str]) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline(list(command))
    return shlex.join(command)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _read(path: Path | None) -> bytes | None:
    if path is None or not path.is_file():
        return None
    return path.read_bytes()


def _normalized_bytes(data: bytes, *, ignore_manifest_timestamp: bool) -> tuple[bytes, bool]:
    """Normalize line endings and, for the manifest only, its volatile timestamp.

    The byte-level replacement preserves formatting and ordering so a generated
    view cannot drift merely because it was reformatted by hand.
    """
    normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if not ignore_manifest_timestamp:
        return normalized, False
    replaced, count = _VOLATILE_MANIFEST_FIELD.subn(rb'\g<prefix>"<volatile>"\g<suffix>', normalized, count=1)
    return replaced, count == 1


def _validate_manifest(data: bytes | None, path: Path | None) -> str | None:
    if data is None:
        return f"missing file: {path.as_posix() if path else '<unknown>'}"
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"invalid manifest JSON: {exc}"
    if not isinstance(parsed, dict):
        return "invalid manifest JSON: top-level value is not an object"
    return None


def _unified_diff(
    relative_path: Path,
    committed: bytes | None,
    generated: bytes | None,
    *,
    ignore_manifest_timestamp: bool,
    max_diff_lines: int,
) -> ArtifactComparison:
    committed_bytes, committed_ignored = _normalized_bytes(committed, ignore_manifest_timestamp=ignore_manifest_timestamp) if committed is not None else (None, False)
    generated_bytes, generated_ignored = _normalized_bytes(generated, ignore_manifest_timestamp=ignore_manifest_timestamp) if generated is not None else (None, False)
    committed_text = committed_bytes.decode("utf-8", errors="replace") if committed_bytes is not None else ""
    generated_text = generated_bytes.decode("utf-8", errors="replace") if generated_bytes is not None else ""
    committed_label = relative_path.as_posix() if committed is not None else f"{relative_path.as_posix()} (missing committed file)"
    generated_label = f"generated:{relative_path.as_posix()}" if generated is not None else f"generated:{relative_path.as_posix()} (missing generated file)"
    diff = list(
        difflib.unified_diff(
            committed_text.splitlines(keepends=True),
            generated_text.splitlines(keepends=True),
            fromfile=committed_label,
            tofile=generated_label,
            n=3,
        )
    )
    changed_lines = sum(1 for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
    if len(diff) > max_diff_lines:
        diff = diff[:max_diff_lines] + [f"... diff truncated at {max_diff_lines} lines ...\n"]
    error = None
    if ignore_manifest_timestamp:
        error = _validate_manifest(committed, None if committed is None else Path(relative_path.as_posix()))
        if error is None:
            error = _validate_manifest(generated, Path(relative_path.as_posix()))
    return ArtifactComparison(
        relative_path=relative_path,
        committed_path=Path(relative_path.as_posix()) if committed is not None else None,
        generated_path=Path(relative_path.as_posix()) if generated is not None else None,
        diff_lines=tuple(diff),
        changed_lines=changed_lines,
        error=error,
        ignored_timestamp=committed_ignored != generated_ignored or (committed is not None and generated is not None and committed != generated and committed_bytes == generated_bytes),
    )


def _expected_index_paths(repo_root: Path) -> list[Path]:
    index_root = repo_root / INDEX_REL
    if not index_root.is_dir():
        return []
    return sorted(
        (path.relative_to(repo_root) for path in index_root.rglob("*") if path.is_file()),
        key=lambda path: path.as_posix(),
    )


def _generated_path(output_dir: Path, relative_path: Path) -> Path | None:
    """Resolve the generator's scratch layout for a committed output path."""
    if relative_path == MANIFEST_REL:
        candidates = (
            output_dir / "feature_manifest.json",
            output_dir / "contracts" / "feature_manifest.json",
        )
    elif relative_path.parts[:2] == tuple(INDEX_REL.parts):
        tail = Path(*relative_path.parts[2:])
        candidates = (output_dir / "docs" / "INDEX" / tail, output_dir / "INDEX" / tail)
    else:
        return None
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _relative_for_generated_file(output_dir: Path, path: Path) -> Path | None:
    relative = path.relative_to(output_dir)
    if relative.as_posix() in {
        "feature_manifest.json",
        "contracts/feature_manifest.json",
    }:
        return MANIFEST_REL
    if relative.parts[:2] == ("docs", "INDEX"):
        return Path("docs", "INDEX", *relative.parts[2:])
    if relative.parts and relative.parts[0] == "INDEX":
        return Path("docs", "INDEX", *relative.parts[1:])
    return None


def _write_report(path: Path, lines: Sequence[str], repo_root: Path) -> str | None:
    resolved = path.resolve()
    if _inside(resolved, repo_root):
        return "--diff-output must be outside --repo-root; the gate never writes into the repository"
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        return f"could not write diff report {resolved}: {exc}"
    return None


def _format_comparison(comparison: ArtifactComparison) -> list[str]:
    relative = comparison.relative_path.as_posix()
    if comparison.error:
        return [f"ERROR {relative}: {comparison.error}"]
    if not comparison.drifted:
        return [f"OK {relative}"]
    lines = [f"DRIFT {relative}: {comparison.changed_lines} changed line(s)"]
    if comparison.ignored_timestamp:
        lines.append("  generated_at differed only as volatile wall-clock metadata")
    if comparison.committed_path is None:
        lines.append("  committed file is missing")
    if comparison.generated_path is None:
        lines.append("  generator output is missing")
    lines.extend(f"  {line.rstrip()}" for line in comparison.diff_lines)
    return lines


def run_gate(repo_root: Path, *, max_diff_lines: int = 200, diff_output: Path | None = None) -> int:
    """Run the generator and return a process-style exit code."""
    repo_root = repo_root.resolve()
    report: list[str] = []
    generator = repo_root / GENERATOR_REL
    if not generator.is_file():
        report.append(f"ERROR generator missing: {generator}")
        _finish_report(report, diff_output, repo_root)
        return 1

    with tempfile.TemporaryDirectory(prefix="alpha-generated-drift-") as temporary:
        output_dir = Path(temporary)
        command = generator_command(repo_root, output_dir)
        report.append(f"generator command: {_display_command(command)}")
        try:
            result = subprocess.run(
                command,
                cwd=repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            report.append(f"ERROR generator could not start: {exc}")
            _finish_report(report, diff_output, repo_root)
            return 1
        if result.stdout:
            report.extend(f"generator stdout: {line}" for line in result.stdout.splitlines())
        if result.stderr:
            report.extend(f"generator stderr: {line}" for line in result.stderr.splitlines())
        if result.returncode != 0:
            report.append(f"ERROR generator exited with code {result.returncode}")
            _finish_report(report, diff_output, repo_root)
            return 1

        expected = [MANIFEST_REL, *_expected_index_paths(repo_root)]
        generated_files = sorted(
            (path for path in output_dir.rglob("*") if path.is_file()),
            key=lambda path: path.as_posix(),
        )
        comparisons: list[ArtifactComparison] = []
        seen: set[Path] = set()
        for relative_path in expected:
            committed_path = repo_root / relative_path
            generated_path = _generated_path(output_dir, relative_path)
            comparison = _unified_diff(
                relative_path,
                _read(committed_path),
                _read(generated_path),
                ignore_manifest_timestamp=relative_path == MANIFEST_REL,
                max_diff_lines=max_diff_lines,
            )
            comparisons.append(comparison)
            if generated_path is not None:
                seen.add(generated_path)

        unrecognized_outputs: list[str] = []
        for generated_path in generated_files:
            relative_path = _relative_for_generated_file(output_dir, generated_path)
            if relative_path is None:
                unrecognized_outputs.append(generated_path.relative_to(output_dir).as_posix())
                report.append(f"DRIFT {generated_path.relative_to(output_dir).as_posix()}: generator produced an unrecognized output")
                continue
            if relative_path in seen or relative_path in {item.relative_path for item in comparisons}:
                continue
            comparisons.append(
                _unified_diff(
                    relative_path,
                    _read(repo_root / relative_path),
                    _read(generated_path),
                    ignore_manifest_timestamp=relative_path == MANIFEST_REL,
                    max_diff_lines=max_diff_lines,
                )
            )

        report.append(f"checked generated artifacts: {len(comparisons)}")
        if not _expected_index_paths(repo_root):
            report.append("  docs/INDEX: no committed generated files are present")
        for comparison in comparisons:
            report.extend(_format_comparison(comparison))
        drift = [comparison for comparison in comparisons if comparison.drifted]
        report.append(f"generated artifact drift: {len(drift) + len(unrecognized_outputs)} file(s)")
        _finish_report(report, diff_output, repo_root)
        return 1 if drift or unrecognized_outputs else 0


def _finish_report(lines: list[str], diff_output: Path | None, repo_root: Path) -> None:
    for line in lines:
        print(line)
    if diff_output is None:
        return
    error = _write_report(diff_output, lines, repo_root)
    if error:
        print(f"ERROR {error}", file=sys.stderr)
        raise RuntimeError(error)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_diff_lines < 1:
        print("ERROR --max-diff-lines must be positive", file=sys.stderr)
        return 2
    try:
        return run_gate(
            args.repo_root,
            max_diff_lines=args.max_diff_lines,
            diff_output=args.diff_output,
        )
    except RuntimeError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
