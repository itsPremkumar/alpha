#!/usr/bin/env python3
"""Run repository-wide Python compile, lint, type, and runtime diagnostics.

The default run is fast and read-only: it compiles every tracked Python
source without importing or executing it, then runs Ruff lint and format
checks. ``--full`` adds mypy and both runtime test suites. Each stage gets a
separate UTF-8 log and machine-readable summary under ``.alpha/reports/`` so a
large failure is easy to locate and share.

Compilation catches syntax/compiler errors only. Runtime behavior is checked by
pytest; mypy and Ruff are static checks and cannot prove runtime correctness.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import tokenize
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.ruff_scope import EXCLUDED_FILES, chunk_paths, is_lintable  # noqa: E402


@dataclass
class StageResult:
    name: str
    ok: bool
    exit_code: int
    elapsed_seconds: float
    log_path: str
    output_tail: str


def _python_files() -> tuple[list[str], list[str]]:
    """Return tracked Python files plus this new script before it is committed."""
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    files = sorted(path.decode("utf-8") for path in completed.stdout.split(b"\0") if path and is_lintable(path.decode("utf-8")))
    script = _display_path(Path(__file__).resolve())
    if script not in files:
        files.append(script)
        files.sort()
    excluded = [f"{item.path}: {item.reason}" for item in EXCLUDED_FILES]
    return files, excluded


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _output_summary(name: str, output: str) -> str:
    """Render concise actionable findings while retaining the full stage log."""
    if not name.startswith("ruff-lint-"):
        return output[-3000:]
    try:
        findings = json.loads(output)
    except json.JSONDecodeError:
        return output[-3000:]
    if not findings:
        return "Ruff reported no lint findings."
    lines = []
    for finding in findings:
        filename = Path(finding.get("filename", "<unknown>"))
        try:
            filename = filename.relative_to(REPO_ROOT)
        except ValueError:
            pass
        location = finding.get("location") or {}
        lines.append(f"{filename}:{location.get('row', 1)}:{location.get('column', 1)} {finding.get('code', 'RUFF')}: {finding.get('message', '')}")
    limit = 40
    visible = lines[:limit]
    if len(lines) > limit:
        visible.append(f"... {len(lines) - limit} more; see the full log")
    return "\n".join(visible)


def _compile_stage(files: list[str]) -> tuple[int, str]:
    """Compile source strings without importing modules or writing bytecode."""
    failures: list[str] = []
    for relative in files:
        path = REPO_ROOT.joinpath(*PurePosixPath(relative).parts)
        try:
            with tokenize.open(path) as source_file:
                source = source_file.read()
            compile(source, str(path), "exec", dont_inherit=True)
        except SyntaxError as exc:
            failures.append(f"{relative}:{exc.lineno or 1}:{exc.offset or 1}: {exc.msg}")
        except (OSError, UnicodeError, ValueError, MemoryError, RecursionError) as exc:
            failures.append(f"{relative}: {type(exc).__name__}: {exc}")
    if failures:
        return (
            1,
            f"Compilation failed for {len(failures)} of {len(files)} Python files:\n" + "\n".join(failures),
        )
    return 0, f"Compiled {len(files)} tracked Python files successfully; modules were not imported or executed."


def _run_process_stage(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    reports_dir: Path,
    environment: dict[str, str] | None = None,
) -> StageResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        output = completed.stdout + completed.stderr
        exit_code = completed.returncode
    except OSError as exc:
        output = f"Could not start {name}: {type(exc).__name__}: {exc}\n"
        exit_code = 127
    elapsed = time.monotonic() - started
    log_path = reports_dir / f"{name}.log"
    log_path.write_text(output, encoding="utf-8")
    return StageResult(
        name=name,
        ok=exit_code == 0,
        exit_code=exit_code,
        elapsed_seconds=round(elapsed, 3),
        log_path=_display_path(log_path),
        output_tail=_output_summary(name, output),
    )


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="also run mypy, the complete offline pytest suite, and blocking-I/O tests",
    )
    parser.add_argument("--compile-only", action="store_true", help="only compile-check Python source; suitable for CI")
    parser.add_argument("--typecheck", action="store_true", help="also run mypy (known debt may fail)")
    parser.add_argument("--tests", action="store_true", help="also execute the offline backend pytest suite")
    parser.add_argument("--blocking-io", action="store_true", help="also execute backend blocking-I/O tests")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path(".alpha/reports"),
        help="directory for diagnostic logs and JSON summary (default: .alpha/reports)",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    reports_dir = (REPO_ROOT / args.report_dir).resolve()
    reports_dir.mkdir(parents=True, exist_ok=True)
    python_files, exclusions = _python_files()
    stages: list[StageResult] = []

    started = time.monotonic()
    compile_code, compile_output = _compile_stage(python_files)
    compile_log = reports_dir / "python-compile.log"
    compile_log.write_text(compile_output + "\n", encoding="utf-8")
    stages.append(
        StageResult(
            name="compile",
            ok=compile_code == 0,
            exit_code=compile_code,
            elapsed_seconds=round(time.monotonic() - started, 3),
            log_path=_display_path(compile_log),
            output_tail=compile_output[-3000:],
        )
    )

    python = sys.executable
    if not args.compile_only:
        for chunk_index, chunk in enumerate(chunk_paths(python_files), start=1):
            stages.append(
                _run_process_stage(
                    f"ruff-lint-{chunk_index:02d}",
                    [python, "-m", "ruff", "check", "--output-format", "json", *chunk],
                    cwd=REPO_ROOT,
                    reports_dir=reports_dir,
                )
            )
            stages.append(
                _run_process_stage(
                    f"ruff-format-{chunk_index:02d}",
                    [python, "-m", "ruff", "format", "--check", *chunk],
                    cwd=REPO_ROOT,
                    reports_dir=reports_dir,
                )
            )

    run_typecheck = not args.compile_only and (args.full or args.typecheck)
    run_tests = not args.compile_only and (args.full or args.tests)
    run_blocking_io = not args.compile_only and (args.full or args.blocking_io)
    if run_typecheck:
        stages.append(
            _run_process_stage(
                "mypy",
                [python, "-m", "mypy", ".", "--ignore-missing-imports"],
                cwd=REPO_ROOT / "backend",
                reports_dir=reports_dir,
            )
        )
    if run_tests:
        junit = reports_dir / "pytest-offline.xml"
        environment = os.environ.copy()
        environment.update({"PYTHONPATH": ".", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        stages.append(
            _run_process_stage(
                "pytest-offline",
                [
                    python,
                    "-m",
                    "pytest",
                    "-m",
                    "not live",
                    "--ignore=tests/blocking_io",
                    "tests/",
                    "-q",
                    "--tb=short",
                    f"--junitxml={junit}",
                ],
                cwd=REPO_ROOT / "backend",
                reports_dir=reports_dir,
                environment=environment,
            )
        )
    if run_blocking_io:
        junit = reports_dir / "pytest-blocking-io.xml"
        environment = os.environ.copy()
        environment.update({"PYTHONPATH": ".", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        stages.append(
            _run_process_stage(
                "pytest-blocking-io",
                [
                    python,
                    "-m",
                    "pytest",
                    "tests/blocking_io",
                    "-q",
                    "--tb=short",
                    f"--junitxml={junit}",
                ],
                cwd=REPO_ROOT / "backend",
                reports_dir=reports_dir,
                environment=environment,
            )
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "python_version": sys.version,
        "python_files_compiled": len(python_files),
        "scope_exclusions": exclusions,
        "ok": all(stage.ok for stage in stages),
        "stages": [asdict(stage) for stage in stages],
    }
    summary_path = reports_dir / "python-health-latest.json"
    summary_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"Python health: {'PASS' if report['ok'] else 'FAIL'} ({len(python_files)} Python files compiled)")
    for stage in stages:
        state = "PASS" if stage.ok else f"FAIL (exit {stage.exit_code})"
        print(f"  {stage.name}: {state} in {stage.elapsed_seconds}s - {stage.log_path}")
        if not stage.ok:
            print(stage.output_tail)
    print(f"JSON summary: {_display_path(summary_path)}")
    if exclusions:
        print("Scoped exclusions:")
        for exclusion in exclusions:
            print(f"  {exclusion}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
