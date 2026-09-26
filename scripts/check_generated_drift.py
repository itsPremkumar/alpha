#!/usr/bin/env python3
"""Fail when a committed generated artifact is not a fresh official build.

THE CONTRACT
------------
This gate executes the official generator's own ``main()`` with its output path
redirected into a throwaway directory, and compares that output with the
committed artifact.  The manual command a contributor runs to regenerate the
artifact is::

    python backend/scripts/generate_feature_manifest.py

Exactly two differences may exist without failing the gate, and the report
discloses both of them instead of tolerating them silently:

1. ``generated_at``.  The generator stamps wall-clock time into the manifest,
   so that value can never match a committed build.  The gate masks *only* the
   value of that one top-level field, on both sides, and then requires the
   remaining bytes to be identical.  A timestamp difference is reported only
   when the masked comparison is equal, the raw inputs really do differ, and
   the field was found and masked on both sides.  A difference in any other
   field is drift, and a drifted artifact is never described as a timestamp
   difference.

2. Line endings, and only in the default ``--line-endings normalized`` mode.
   The official generator writes through ``Path.write_text(...)``, which emits
   the *host* newline (CRLF on Windows, LF elsewhere), while ``.gitattributes``
   pins ``*.json`` to LF in the index.  Comparing raw bytes would therefore
   report drift that says nothing about the artifact's content whenever the
   generating host differs from the committing host.  ``normalized`` compares
   both sides after CRLF/CR -> LF and prints every line-ending-only difference
   it tolerates.  ``--line-endings exact`` compares raw bytes with no
   normalisation at all and is what CI runs, because on the Linux runner the
   generator's output and the LF-pinned index are both LF, so byte fidelity is
   unambiguous there.

Nothing else is ignored.  A missing artifact, a missing or failing generator, a
generator timeout, malformed JSON, an unrecognised generator output, and every
other byte are drift.

HERMETICITY
-----------
The official generator derives its output path from its own location on disk and
accepts no output argument, so invoking it as a plain subprocess would overwrite
``contracts/feature_manifest.json`` in the checkout.  The gate therefore runs a
generated shim (printed in the report) that loads the official generator module,
refuses to run unless the module's published ``OUT`` constant is exactly the
committed artifact path, and only then redirects ``OUT`` into the throwaway
directory.  The gate never writes into the checkout.

Exit codes: ``0`` clean, ``1`` drift or gate error, ``2`` usage error.  Every
run prints this sentence: "only the generated_at value is ignored; every other
byte is drift", preceded by the line-ending mode that produced the verdict, so a
green run can never be read as a stronger claim than it is.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_REL = Path("backend/scripts/generate_feature_manifest.py")
MANIFEST_REL = Path("contracts/feature_manifest.json")
GENERATOR_COMMAND_DOC = "python backend/scripts/generate_feature_manifest.py"
SHIM_NAME = "_alpha_feature_manifest_shim.py"
GATE_DIRNAME = "_gate"
# The shim loads the official generator, refuses to run unless the module's
# published output constant is the committed artifact, and only then redirects
# that constant into a throwaway directory.  It is printed in the gate report
# so a reviewer can see exactly what executed.
SHIM_SOURCE = """\
import importlib.util
import sys
from pathlib import Path

generator = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2])
committed = Path(sys.argv[3]).resolve()

spec = importlib.util.spec_from_file_location("alpha_official_generator", generator)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load official generator: {generator}")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

published = getattr(module, "OUT", None)
if not isinstance(published, Path):
    raise SystemExit(
        "official generator publishes no Path output constant; refusing to run "
        "it because its output location cannot be redirected out of the checkout"
    )
if published.resolve() != committed:
    raise SystemExit(
        f"official generator writes {published}, not the committed {committed}; "
        "refusing to run it because the gate cannot guarantee hermeticity"
    )
module.OUT = output
raise SystemExit(module.main())
"""
GENERATOR_TIMEOUT_SECONDS = 600
MAX_DIFF_LINES = 200
LINE_ENDINGS_NORMALIZED = "normalized"
LINE_ENDINGS_EXACT = "exact"
LINE_ENDINGS_CHOICES = (LINE_ENDINGS_NORMALIZED, LINE_ENDINGS_EXACT)
VOLATILE_SENTINEL = b"<volatile-generated-at>"
# The value of one top-level "generated_at" field.  A value that itself needs
# JSON escaping cannot be a wall-clock stamp, so it is not masked.
_GENERATED_AT_FIELD = re.compile(
    rb'(?m)^(?P<prefix>[ \t]*"generated_at"[ \t]*:[ \t]*)'
    rb'"(?P<value>[^"\\\r\n]*)"'
)


class GateError(RuntimeError):
    """A gate-level failure that must fail closed rather than pass silently."""


class CommandTimeout(GateError):
    """A child process outlived its budget and was terminated."""


@dataclass(frozen=True)
class CommandResult:
    """The bounded result of one child process."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ArtifactComparison:
    """One generated-artifact comparison and its human-readable evidence."""

    relative_path: Path
    line_endings: str
    committed_bytes: bytes | None
    generated_bytes: bytes | None
    diff_lines: tuple[str, ...]
    changed_lines: int
    error: str | None = None
    ignored_timestamp: bool = False
    committed_newlines: str = "absent"
    generated_newlines: str = "absent"
    newline_difference: bool = False

    @property
    def drifted(self) -> bool:
        """True unless the two sides are byte-identical after the contract."""
        if self.error is not None:
            return True
        if self.committed_bytes is None or self.generated_bytes is None:
            return True
        return self.changed_lines > 0

    @property
    def contract(self) -> str:
        if self.line_endings == LINE_ENDINGS_EXACT:
            return "raw bytes; only the generated_at value is ignored"
        return "raw bytes after CRLF/CR -> LF normalisation; only the generated_at value is ignored"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="Checkout to inspect (default: the repository containing this script).",
    )
    parser.add_argument(
        "--line-endings",
        choices=LINE_ENDINGS_CHOICES,
        default=LINE_ENDINGS_NORMALIZED,
        help=(
            "How to compare line endings. 'normalized' (default) compares raw "
            "bytes after CRLF/CR -> LF and discloses every line-ending-only "
            "difference it tolerates; 'exact' compares raw bytes with no "
            "normalisation, so a line-ending-only change is drift."
        ),
    )
    parser.add_argument(
        "--diff-output",
        type=Path,
        help="Write the report to this path; it must be outside --repo-root.",
    )
    parser.add_argument(
        "--max-diff-lines",
        type=int,
        default=MAX_DIFF_LINES,
        help=f"Maximum unified-diff lines printed per file (default: {MAX_DIFF_LINES}).",
    )
    parser.add_argument(
        "--print-shim",
        action="store_true",
        help="Print the hermetic generator shim and exit without running the gate.",
    )
    parser.add_argument(
        "--generator-timeout",
        type=float,
        default=GENERATOR_TIMEOUT_SECONDS,
        help=(f"Seconds the official generator may run before it is killed and the gate fails (default: {GENERATOR_TIMEOUT_SECONDS})."),
    )
    return parser


def generator_command(repo_root: Path, output_dir: Path) -> list[str]:
    """Return the argv the gate uses to run the official generator hermetically.

    The official generator has no output argument, so the gate runs a generated
    shim that redirects the generator's own output constant.  ``output_dir`` is
    the throwaway directory the generated artifact must land in; the shim
    itself is written under ``output_dir/_gate`` so it is never mistaken for a
    generator output.
    """
    generator = (repo_root / GENERATOR_REL).resolve()
    shim = output_dir / GATE_DIRNAME / SHIM_NAME
    return [
        sys.executable,
        str(shim),
        str(generator),
        str((output_dir / MANIFEST_REL.name).resolve()),
        str((repo_root / MANIFEST_REL).resolve()),
    ]


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


def _kill_tree(process: subprocess.Popen[str]) -> None:
    """Terminate a child and everything it spawned; never leave an orphan.

    ``taskkill /T`` walks the tree before killing, so it can itself return a
    non-zero code when a child exits mid-walk.  A non-zero code therefore falls
    back to killing the direct child rather than being treated as success.
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is None or result.returncode != 0:
            process.kill()
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError, PermissionError):
            process.kill()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - kill(9) did not land
        pass


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float,
    capture: bool = True,
) -> CommandResult:
    """Run a child process under a hard timeout and fail closed on expiry.

    A gate that hangs is not a gate, so no child may outlive ``timeout``: the
    process tree is killed and :class:`CommandTimeout` is raised instead of
    waiting for the platform default.
    """
    spawn: dict[str, object] = {}
    if os.name == "nt":
        spawn["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        spawn["start_new_session"] = True
    process = subprocess.Popen(  # noqa: S603 - argv is built, never shell=True
        list(argv),
        cwd=None if cwd is None else str(cwd),
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        **spawn,  # type: ignore[arg-type]
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            process.communicate(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - pipes never closed
            pass
        raise CommandTimeout(f"command exceeded its {timeout:g}s budget and was killed: {_display_command(argv)}") from None
    return CommandResult(
        argv=tuple(argv),
        returncode=process.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
    )


def _normalize_newlines(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _newline_style(data: bytes | None) -> str:
    if data is None:
        return "absent"
    crlf = data.count(b"\r\n")
    cr = data.count(b"\r") - crlf
    lf = data.count(b"\n") - crlf
    parts = []
    if crlf:
        parts.append(f"CRLF x{crlf}")
    if lf:
        parts.append(f"LF x{lf}")
    if cr:
        parts.append(f"CR x{cr}")
    return ", ".join(parts) if parts else "none"


def _parse_manifest(data: bytes) -> dict[str, object] | list[object] | None:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _mask_generated_at(data: bytes, parsed: dict[str, object] | list[object] | None) -> tuple[bytes, bool]:
    """Mask the value of the one top-level ``generated_at`` field.

    Returns the masked bytes and whether masking actually happened.  Refusing
    to mask an unexpected shape is deliberate: a manifest the gate cannot
    recognise must be compared in full, so an unknown field is drift rather
    than a silently ignored difference.
    """
    if not isinstance(parsed, dict) or not isinstance(parsed.get("generated_at"), str):
        return data, False
    matches = list(_GENERATED_AT_FIELD.finditer(data))
    if len(matches) != 1:
        return data, False
    match = matches[0]
    masked = data[: match.start()] + match.group("prefix") + b'"' + VOLATILE_SENTINEL + b'"' + data[match.end() :]
    return masked, True


def _visible(line: str) -> str:
    """Render a diff line without hiding the carriage return of a CRLF diff."""
    return line.rstrip("\n").replace("\r", "<CR>")


def compare_artifact(
    relative_path: Path,
    committed: bytes | None,
    generated: bytes | None,
    *,
    line_endings: str = LINE_ENDINGS_NORMALIZED,
    ignore_manifest_timestamp: bool = False,
    max_diff_lines: int = MAX_DIFF_LINES,
) -> ArtifactComparison:
    """Compare one committed artifact with freshly generated bytes.

    The verdict is decided on bytes.  ``ignored_timestamp`` is set only when
    the timestamp is provably the *sole* difference between the two sides, so
    it can never describe a difference that is not a timestamp.
    """
    if line_endings not in LINE_ENDINGS_CHOICES:
        raise ValueError(f"unknown line-ending mode: {line_endings!r}")

    error: str | None = None
    committed_parsed = generated_parsed = None
    for label, payload in (("committed", committed), ("generated", generated)):
        if payload is None:
            continue
        parsed = _parse_manifest(payload)
        if parsed is None:
            error = f"{label} side is not valid UTF-8 JSON"
        else:
            if label == "committed":
                committed_parsed = parsed
            else:
                generated_parsed = parsed

    def _prepare(payload: bytes | None, parsed: object) -> tuple[bytes, bool]:
        if payload is None:
            return b"", False
        if line_endings == LINE_ENDINGS_EXACT:
            comparable = payload
        else:
            comparable = _normalize_newlines(payload)
        if not ignore_manifest_timestamp:
            return comparable, False
        return _mask_generated_at(comparable, parsed)  # type: ignore[arg-type]

    committed_comparable, committed_masked = _prepare(committed, committed_parsed)
    generated_comparable, generated_masked = _prepare(generated, generated_parsed)

    committed_label = relative_path.as_posix() if committed is not None else f"{relative_path.as_posix()} (missing committed file)"
    generated_label = f"generated:{relative_path.as_posix()}" if generated is not None else f"generated:{relative_path.as_posix()} (missing generated file)"
    diff = list(
        difflib.unified_diff(
            committed_comparable.decode("utf-8", errors="replace").splitlines(keepends=True),
            generated_comparable.decode("utf-8", errors="replace").splitlines(keepends=True),
            fromfile=committed_label,
            tofile=generated_label,
            n=3,
        )
    )
    changed_lines = sum(1 for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
    if len(diff) > max_diff_lines:
        diff = diff[:max_diff_lines] + [f"... diff truncated at {max_diff_lines} lines ...\n"]

    raw_equal = committed is not None and committed == generated
    # "The two sides do not use the same line endings", which is independent of
    # whether anything else differs, so the disclosure is always honest.
    newline_difference = committed is not None and generated is not None and _newline_style(committed) != _newline_style(generated)
    # A timestamp difference is only claimable when nothing else differs.
    ignored_timestamp = error is None and changed_lines == 0 and not raw_equal and committed is not None and generated is not None and committed_masked and generated_masked
    return ArtifactComparison(
        relative_path=relative_path,
        line_endings=line_endings,
        committed_bytes=committed,
        generated_bytes=generated,
        diff_lines=tuple(_visible(line) for line in diff),
        changed_lines=changed_lines,
        error=error,
        ignored_timestamp=ignored_timestamp,
        committed_newlines=_newline_style(committed),
        generated_newlines=_newline_style(generated),
        newline_difference=newline_difference,
    )


def _generated_path(output_dir: Path, relative_path: Path) -> Path | None:
    """Resolve the generator's scratch layout for a committed output path."""
    if relative_path == MANIFEST_REL:
        candidates = (
            output_dir / MANIFEST_REL.name,
            output_dir / MANIFEST_REL,
        )
    else:
        return None
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _relative_for_generated_file(output_dir: Path, path: Path) -> Path | None:
    relative = path.relative_to(output_dir)
    if relative.as_posix() in {
        MANIFEST_REL.as_posix(),
        MANIFEST_REL.name,
    }:
        return MANIFEST_REL
    return None


def _write_report(path: Path, lines: Sequence[str], repo_root: Path) -> None:
    resolved = path.resolve()
    if _inside(resolved, repo_root):
        raise GateError("--diff-output must be outside --repo-root; the gate never writes into the repository")
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise GateError(f"could not write diff report {resolved}: {exc}") from exc


def _format_comparison(comparison: ArtifactComparison) -> list[str]:
    relative = comparison.relative_path.as_posix()
    if comparison.error:
        return [f"DRIFT {relative}: {comparison.error}"]
    if not comparison.drifted:
        lines = [f"OK {relative} ({comparison.contract})"]
        if comparison.ignored_timestamp:
            lines.append(f"  disclosed: generated_at is the only content difference (committed newlines {comparison.committed_newlines}; generated newlines {comparison.generated_newlines})")
        if comparison.newline_difference:
            lines.append(f"  disclosed: line endings differ (committed {comparison.committed_newlines}; generated {comparison.generated_newlines}); allowed by --line-endings normalized")
        return lines
    lines = [f"DRIFT {relative}: {comparison.changed_lines} changed line(s)"]
    if comparison.committed_bytes is None:
        lines.append("  committed file is missing")
    if comparison.generated_bytes is None:
        lines.append("  generator output is missing")
    if comparison.newline_difference:
        lines.append(f"  line endings also differ (committed {comparison.committed_newlines}; generated {comparison.generated_newlines})")
    if comparison.ignored_timestamp:
        lines.append("  generated_at is the only content difference that was masked")
    lines.extend(f"  {line}" for line in comparison.diff_lines)
    return lines


def run_gate(
    repo_root: Path,
    *,
    line_endings: str = LINE_ENDINGS_NORMALIZED,
    max_diff_lines: int = MAX_DIFF_LINES,
    diff_output: Path | None = None,
    generator_timeout: float = GENERATOR_TIMEOUT_SECONDS,
) -> int:
    """Run the official generator and return a process-style exit code."""
    repo_root = repo_root.resolve()
    if line_endings not in LINE_ENDINGS_CHOICES:
        raise ValueError(f"unknown line-ending mode: {line_endings!r}")
    report: list[str] = [f"contract: compared on {line_endings} bytes; only the generated_at value is ignored; every other byte is drift"]
    exit_code = 0
    try:
        generator = repo_root / GENERATOR_REL
        if not generator.is_file():
            raise GateError(f"official generator is missing: {GENERATOR_REL.as_posix()}")

        with tempfile.TemporaryDirectory(prefix="alpha-generated-drift-") as temporary:
            output_dir = Path(temporary)
            shim_path = output_dir / GATE_DIRNAME / SHIM_NAME
            shim_path.parent.mkdir(parents=True, exist_ok=True)
            shim_path.write_text(SHIM_SOURCE, encoding="utf-8", newline="\n")
            command = generator_command(repo_root, output_dir)
            report.append(f"official generator: {GENERATOR_COMMAND_DOC} (run through a generated shim that redirects its output constant; see --print-shim)")
            report.append(f"shim command: {_display_command(command)}")
            result = run_command(
                command,
                cwd=repo_root,
                timeout=generator_timeout,
            )
            if result.stdout:
                report.extend(f"generator stdout: {line}" for line in result.stdout.splitlines())
            if result.stderr:
                report.extend(f"generator stderr: {line}" for line in result.stderr.splitlines())
            if result.returncode != 0:
                raise GateError(f"generator exited with code {result.returncode}")

            comparisons: list[ArtifactComparison] = []
            tracked: set[Path] = set()
            # A generated artifact is always compared, even when it is absent
            # from the checkout: a deleted committed artifact is drift, and a
            # clean checkout cannot discover a path that is no longer on disk.
            for relative_path in (MANIFEST_REL,):
                generated_path = _generated_path(output_dir, relative_path)
                comparisons.append(
                    compare_artifact(
                        relative_path,
                        _read(repo_root / relative_path),
                        _read(generated_path),
                        line_endings=line_endings,
                        ignore_manifest_timestamp=True,
                        max_diff_lines=max_diff_lines,
                    )
                )
                if generated_path is not None:
                    tracked.add(generated_path)

            untracked: list[str] = []
            for generated_path in sorted(output_dir.rglob("*")):
                if not generated_path.is_file() or generated_path in tracked:
                    continue
                if GATE_DIRNAME in generated_path.relative_to(output_dir).parts:
                    continue  # written by the gate, not produced by the generator
                name = generated_path.relative_to(output_dir).as_posix()
                if _relative_for_generated_file(output_dir, generated_path) is None:
                    untracked.append(name)

        report.append(f"checked generated artifacts: {len(comparisons)}")
        for comparison in comparisons:
            report.extend(_format_comparison(comparison))
        for name in untracked:
            report.append(f"DRIFT {name}: generator produced an output this gate does not compare; declare it as a tracked artifact")
        drift = [item for item in comparisons if item.drifted]
        report.append(f"generated artifact drift: {len(drift) + len(untracked)} file(s)")
        exit_code = 1 if drift or untracked else 0
    except GateError as exc:
        report.append(f"ERROR {exc}")
        exit_code = 1
    except OSError as exc:
        report.append(f"ERROR gate could not run: {exc}")
        exit_code = 1

    for line in report:
        print(line)
    if diff_output is not None:
        _write_report(diff_output, report, repo_root)
    return exit_code


def _read(path: Path | None) -> bytes | None:
    if path is None or not path.is_file():
        return None
    return path.read_bytes()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.print_shim:
        print(SHIM_SOURCE, end="")
        return 0
    if args.max_diff_lines < 1:
        print("ERROR --max-diff-lines must be positive", file=sys.stderr)
        return 2
    if args.generator_timeout <= 0:
        print("ERROR --generator-timeout must be positive", file=sys.stderr)
        return 2
    try:
        return run_gate(
            args.repo_root,
            line_endings=args.line_endings,
            max_diff_lines=args.max_diff_lines,
            diff_output=args.diff_output,
            generator_timeout=args.generator_timeout,
        )
    except GateError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
