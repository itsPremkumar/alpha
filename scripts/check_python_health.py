#!/usr/bin/env python3
"""Run repository-wide Python compile, lint, type, and runtime diagnostics.

The default run is fast and read-only: it compiles every tracked Python
source without importing or executing it, then runs Ruff lint and format
checks. ``--runtime-smoke`` additionally asks pytest to collect tests, which
loads test modules and exposes import/collection-time failures without running
the test bodies. ``--full`` adds that smoke stage, mypy, and both runtime test
suites. Each stage gets a separate UTF-8 log and machine-readable summary under
``.alpha/reports/`` so a large failure is easy to locate and share.

Compilation catches syntax/compiler errors only. Collection catches import and
test-discovery failures on the paths exercised by pytest, not every possible
module import. Runtime behavior is checked by pytest; mypy and Ruff are static
checks and cannot prove runtime correctness. Runtime pytest stages stream progress
while also writing the full output to their stage logs.
"""

from __future__ import annotations

import argparse
import codecs
import html
import json
import os
import re
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
    diagnostics: list[dict[str, Any]]


_MYPY_DIAGNOSTIC = re.compile(r"^(.*?):(\d+):(?:(\d+):)? (error|warning|note): (.*)$")
_MYPY_FILE_DIAGNOSTIC = re.compile(r"^(.*?): (error|warning|note): (.*)$")
_COMPILE_DIAGNOSTIC = re.compile(r"^(.*?):(\d+):(\d+): (.+)$")
_COMPILE_FILE_DIAGNOSTIC = re.compile(r"^(.+?): (OSError|UnicodeError|ValueError|MemoryError|RecursionError): (.+)$")
_RUFF_FORMAT_DIAGNOSTIC = re.compile(r"^Would reformat: (.+)$")
_PYTEST_FAILURE = re.compile(r"^(FAILED|ERROR) (.+?)(?: - (.*))?$")


def _parse_mypy_diagnostics(output: str) -> list[dict[str, Any]]:
    """Extract every location-bearing mypy diagnostic for the JSON report."""
    diagnostics: list[dict[str, Any]] = []
    for line in output.splitlines():
        match = _MYPY_DIAGNOSTIC.match(line)
        if match:
            path, row, column, severity, message = match.groups()
        else:
            file_match = _MYPY_FILE_DIAGNOSTIC.match(line)
            if not file_match:
                continue
            path, severity, message = file_match.groups()
            row = column = None
        code_match = re.search(r" \[([^\]]+)\]$", message)
        code = code_match.group(1) if code_match else None
        if code_match:
            message = message[: code_match.start()]
        diagnostics.append(
            {
                "path": path,
                "line": int(row) if row else None,
                "column": int(column) if column else None,
                "severity": severity,
                "message": message,
                "code": code,
            }
        )
    return diagnostics


def _parse_diagnostics(name: str, output: str) -> list[dict[str, Any]]:
    """Parse located findings into one schema across compiler and runtime stages."""
    if name == "mypy":
        return _parse_mypy_diagnostics(output)
    if name == "compile":
        diagnostics = []
        for line in output.splitlines():
            match = _COMPILE_DIAGNOSTIC.match(line)
            if match:
                path, row, column, message = match.groups()
                diagnostics.append(
                    {
                        "path": path,
                        "line": int(row),
                        "column": int(column),
                        "severity": "error",
                        "message": message,
                        "code": "syntax-error",
                    }
                )
                continue
            file_match = _COMPILE_FILE_DIAGNOSTIC.match(line)
            if file_match:
                path, error_type, message = file_match.groups()
                diagnostics.append(
                    {
                        "path": path,
                        "line": None,
                        "column": None,
                        "severity": "error",
                        "message": f"{error_type}: {message}",
                        "code": "compile-input-error",
                    }
                )
        return diagnostics
    if name.startswith("ruff-lint-"):
        try:
            findings = json.loads(output)
        except json.JSONDecodeError:
            return []
        return [
            {
                "path": finding.get("filename"),
                "line": (finding.get("location") or {}).get("row"),
                "column": (finding.get("location") or {}).get("column"),
                "severity": "error",
                "message": finding.get("message", ""),
                "code": finding.get("code"),
            }
            for finding in findings
        ]
    if name.startswith("ruff-format-"):
        return [
            {
                "path": match.group(1),
                "line": None,
                "column": None,
                "severity": "error",
                "message": "File is not formatted; run `ruff format` on this file.",
                "code": "format-needed",
            }
            for line in output.splitlines()
            if (match := _RUFF_FORMAT_DIAGNOSTIC.match(line.strip()))
        ]
    if name.startswith("pytest-"):
        diagnostics = []
        for line in output.splitlines():
            match = _PYTEST_FAILURE.match(line.strip())
            if match:
                kind, target, message = match.groups()
                diagnostics.append(
                    {
                        "path": target,
                        "line": None,
                        "column": None,
                        "severity": "error",
                        "message": message or kind,
                        "code": "pytest-failure" if kind == "FAILED" else "pytest-error",
                    }
                )
        return diagnostics
    return []


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
        if name == "mypy":
            diagnostics = _parse_mypy_diagnostics(output)
            if diagnostics:
                errors = sum(item["severity"] == "error" for item in diagnostics)
                codes: dict[str, int] = {}
                for item in diagnostics:
                    code = item["code"] or "uncoded"
                    codes[code] = codes.get(code, 0) + 1
                preview_lines = []
                for item in diagnostics[:40]:
                    location = f":{item['line']}:{item['column'] or 1}" if item["line"] else ""
                    preview_lines.append(f"{item['path']}{location}: {item['severity']}: {item['message']}")
                preview = "\n".join(preview_lines)
                remaining = len(diagnostics) - min(len(diagnostics), 40)
                suffix = f"\n... {remaining} more; all findings are in the JSON report and full log" if remaining else ""
                return f"mypy diagnostics: {errors} errors across {len(diagnostics)} findings; codes={codes}\n{preview}{suffix}"
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


def _html_report(report: dict[str, Any]) -> str:
    """Build a self-contained searchable summary without trusting diagnostic text as HTML."""
    rows: list[str] = []
    cards: list[str] = []
    for stage in report["stages"]:
        state = "PASS" if stage["ok"] else "FAIL"
        cards.append(
            f'<section class="stage {"pass" if stage["ok"] else "fail"}">'
            f"<h2>{html.escape(stage['name'])}: {state} ({len(stage['diagnostics'])} findings)</h2>"
            f'<a href="{html.escape(Path(stage["log_path"]).name, quote=True)}">Full stage log</a>'
            f"<pre>{html.escape(stage['output_tail'])}</pre></section>"
        )
        for finding in stage["diagnostics"]:
            path = str(finding.get("path") or "")
            if finding.get("line") is not None:
                path += f":{finding['line']}"
                if finding.get("column") is not None:
                    path += f":{finding['column']}"
            values = (stage["name"], path, finding.get("code") or "", finding.get("severity") or "error", finding.get("message") or "")
            escaped = [html.escape(str(value)) for value in values]
            rows.append("<tr>" + "".join(f"<td>{value}</td>" for value in escaped) + "</tr>")
    if not rows:
        rows.append('<tr><td colspan="5">No parsed diagnostics.</td></tr>')
    overall = "PASS" if report["ok"] else "FAIL"
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width"><title>Alpha Python diagnostics</title>'
        "<style>body{font:15px system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem;"
        "background:#10141b;color:#e8edf5}a{color:#75b5ff}.stage{border:1px solid #394456;"
        "border-radius:10px;padding:1rem;margin:1rem 0}.pass{border-left:4px solid #36b37e}"
        ".fail{border-left:4px solid #ff646e}pre{white-space:pre-wrap;overflow-wrap:anywhere}"
        "table{border-collapse:collapse;width:100%;margin-top:1rem}th,td{padding:.65rem;text-align:left;"
        "border-bottom:1px solid #394456;vertical-align:top}td:last-child{overflow-wrap:anywhere}"
        "input{width:min(100%,32rem);padding:.7rem}</style>"
        f"<body><h1>Python diagnostics: {overall}</h1><p>{report['python_files_compiled']} "
        "tracked Python files compiled. Syntax compilation does not import modules or prove runtime behavior.</p>"
        "<h2>Stages</h2>"
        + "\n".join(cards)
        + '<h2>Searchable findings</h2><label for="filter">Filter by stage, file, code, or message</label><br>'
        + '<input id="filter" type="search" placeholder="e.g. mypy, backend/foo.py, F401">'
        + "<table><thead><tr><th>Stage</th><th>Location</th><th>Code</th><th>Severity</th><th>Finding</th>"
        + '</tr></thead><tbody id="findings">'
        + "\n".join(rows)
        + "</tbody></table>"
        + '<script>const q=document.querySelector("#filter");q.addEventListener("input",()=>{'
        + 'const s=q.value.toLowerCase();for(const r of document.querySelectorAll("#findings tr"))'
        + "r.hidden=!r.innerText.toLowerCase().includes(s)});</script></body></html>"
    )


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
    stream_output: bool = False,
) -> StageResult:
    started = time.monotonic()
    log_path = reports_dir / f"{name}.log"
    output_streamed_to_log = False
    try:
        if stream_output:
            output, exit_code = _run_streaming_process(
                command,
                cwd=cwd,
                environment=environment,
                log_path=log_path,
            )
            output_streamed_to_log = True
        else:
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
    if not output_streamed_to_log:
        log_path.write_text(output, encoding="utf-8")
    diagnostic_output = log_path.read_text(encoding="utf-8", errors="replace") if output_streamed_to_log else output
    return StageResult(
        name=name,
        ok=exit_code == 0,
        exit_code=exit_code,
        elapsed_seconds=round(elapsed, 3),
        log_path=_display_path(log_path),
        output_tail=_output_summary(name, output),
        diagnostics=_parse_diagnostics(name, diagnostic_output),
    )


def _run_streaming_process(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None,
    log_path: Path,
) -> tuple[str, int]:
    """Run a noisy stage while teeing output to the terminal and its log."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    output_tail = ""
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=8192,
        )
        try:
            if process.stdout is None:  # pragma: no cover - stdout is piped above
                raise OSError("subprocess output pipe could not be opened")
            while chunk := process.stdout.read1(8192):
                text = decoder.decode(chunk)
                if text:
                    log_file.write(text)
                    log_file.flush()
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    output_tail = (output_tail + text)[-3000:]
            final_text = decoder.decode(b"", final=True)
            if final_text:
                log_file.write(final_text)
                log_file.flush()
                sys.stdout.write(final_text)
                sys.stdout.flush()
                output_tail = (output_tail + final_text)[-3000:]
            exit_code = process.wait()
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            raise
        finally:
            if process.stdout is not None:
                process.stdout.close()
    return output_tail, exit_code


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="also run mypy, the complete offline pytest suite, and blocking-I/O tests",
    )
    parser.add_argument("--compile-only", action="store_true", help="only compile-check Python source; suitable for CI")
    parser.add_argument(
        "--diagnostics-only",
        action="store_true",
        help="compile Python and collect backend tests, without mixing in lint/type-check debt",
    )
    parser.add_argument("--typecheck", action="store_true", help="also run mypy (known debt may fail)")
    parser.add_argument(
        "--runtime-smoke",
        action="store_true",
        help="collect backend tests to detect test-module import and collection errors without running tests",
    )
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
            diagnostics=_parse_diagnostics("compile", compile_output),
        )
    )

    python = sys.executable
    if not (args.compile_only or args.diagnostics_only):
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

    run_runtime_smoke = args.diagnostics_only or (not args.compile_only and (args.full or args.runtime_smoke))
    run_typecheck = not (args.compile_only or args.diagnostics_only) and (args.full or args.typecheck)
    run_tests = not (args.compile_only or args.diagnostics_only) and (args.full or args.tests)
    run_blocking_io = not (args.compile_only or args.diagnostics_only) and (args.full or args.blocking_io)
    if run_runtime_smoke:
        environment = os.environ.copy()
        environment.update({"PYTHONPATH": ".", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        stages.append(
            _run_process_stage(
                "pytest-collect",
                [python, "-m", "pytest", "--collect-only", "-q", "tests/"],
                cwd=REPO_ROOT / "backend",
                reports_dir=reports_dir,
                environment=environment,
            )
        )
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
                stream_output=True,
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
                stream_output=True,
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
    html_path = reports_dir / "python-health-latest.html"
    html_path.write_text(_html_report(report), encoding="utf-8")

    print(f"Python health: {'PASS' if report['ok'] else 'FAIL'} ({len(python_files)} Python files compiled)")
    for stage in stages:
        state = "PASS" if stage.ok else f"FAIL (exit {stage.exit_code})"
        print(f"  {stage.name}: {state} in {stage.elapsed_seconds}s - {stage.log_path}")
        if not stage.ok:
            print(stage.output_tail)
    print(f"JSON summary: {_display_path(summary_path)}")
    print(f"HTML report: {_display_path(html_path)}")
    if exclusions:
        print("Scoped exclusions:")
        for exclusion in exclusions:
            print(f"  {exclusion}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
