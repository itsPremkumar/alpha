#!/usr/bin/env python3
"""Measure the repository's Ruff debt over the same file set the gate lints.

This is a measurement, not a gate.  It exits 0 whenever the measurement ran,
however much debt it found, and exits 1 only when the measurement itself could
not be produced - a broken report is a failure, a large backlog is not.

WHY THIS IS A SCRIPT AND NOT A STEP IN THE WORKFLOW
---------------------------------------------------
The file set has to be the same one ``check_changed_python_lint.py`` uses, and
that set is defined in ``ruff_scope.py``.  A gate and a debt report that
describe different file sets cannot detect their own disagreement, and 108
findings in first-party non-backend code were invisible for exactly that
reason: the gate linted those files under ruff's defaults (no E501, no I001, no
UP) and this report only walked ``backend/``.  One definition, two callers:

    $ python scripts/ruff_debt_report.py --out /tmp/ruff-debt.json
    policy config: ruff.toml
    scope: 3101 python files (backend=3025 other=76) sha256=<digest>
    excluded from ruff scope: <one template, with its reason>
    RULES   findings=...  backend=...  other=...
    FORMAT  files needing `ruff format`=...

The digest is over the sorted scope list, so two reports can be compared
byte-for-byte to prove they measured the same files.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ruff_scope  # noqa: E402 - the shared file set, deliberately next door

ROOT = Path(__file__).resolve().parents[1]
RUFF_TIMEOUT_SECONDS = 900


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument(
        "--backend-root",
        type=Path,
        help=("Directory whose locked environment provides ruff (default: REPO/backend). Not a lint scope: the policy is resolved per file."),
    )
    parser.add_argument("--out", type=Path, help="Write the raw findings JSON here.")
    parser.add_argument(
        "--ruff-timeout",
        type=float,
        default=RUFF_TIMEOUT_SECONDS,
        help="Seconds each ruff invocation may run before the report fails.",
    )
    return parser


def _ruff_command(ruff_cwd: Path, repo_root: Path, subcommand: str, arguments: list[str]) -> list[str]:
    uv = shutil.which("uv")
    if uv:
        command = [uv, "run", "--project", str(ruff_cwd), "--no-sync", "ruff", subcommand]
    else:
        command = [sys.executable, "-m", "ruff", subcommand]
    command.extend(arguments)
    return command


def _run(command: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(  # noqa: S603 - argv is built, never shell=True
            command,
            cwd=str(cwd),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"ERROR the debt measurement could not run ruff: {exc}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    ruff_cwd = (args.backend_root or repo_root / "backend").resolve()
    if not (repo_root / ruff_scope.POLICY_CONFIG).is_file():
        raise SystemExit(f"ERROR the repository-wide ruff policy is missing: {repo_root / ruff_scope.POLICY_CONFIG}. A debt report that cannot name the policy it measures is not a measurement.")

    scope = ruff_scope.lintable_python_files(repo_root)
    if not scope:
        raise SystemExit("ERROR the lint scope is empty; the measurement is broken")
    backend, other = ruff_scope.split_by_scope(repo_root, scope)
    digest = hashlib.sha256("\n".join(scope).encode("utf-8")).hexdigest()
    print("FULL-REPOSITORY RUFF DEBT REPORT (non-gating)")
    print(f"  policy config: {ruff_scope.POLICY_CONFIG}")
    print(f"  scope: {len(scope)} python files (backend={len(backend)} other={len(other)})")
    print(f"  scope sha256: {digest}")
    ruff_scope.report_exclusions()

    missing = [path for path in scope if not (repo_root / path).is_file()]
    if missing:
        raise SystemExit(f"ERROR the scope lists {len(missing)} file(s) that do not exist, starting with {missing[0]}")

    targets = [str((repo_root / path).resolve()) for path in scope]
    chunks = ruff_scope.chunk_paths(targets)
    print(f"  running: ruff check --no-cache over {len(targets)} files in {len(chunks)} invocation(s)")
    findings: list[dict[str, object]] = []
    worst_exit = 0
    for index, chunk in enumerate(chunks, start=1):
        command = _ruff_command(
            ruff_cwd,
            repo_root,
            "check",
            ["--no-cache", "--output-format=json", *chunk],
        )
        result = _run(command, repo_root, args.ruff_timeout)
        # ruff exits 1 when it reported findings and 2 on an error, and the scope
        # is split across invocations, so the reported status is the worst one.
        worst_exit = max(worst_exit, result.returncode)
        if not result.stdout:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise SystemExit(f"ERROR the Ruff debt report was not produced (exit {result.returncode}, invocation {index}/{len(chunks)})" + (f": {detail}" if detail else ""))
        try:
            batch = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SystemExit(f"ERROR the Ruff debt report is unreadable: {exc}") from exc
        if not isinstance(batch, list):
            raise SystemExit("ERROR the Ruff debt report is not a JSON finding list")
        findings.extend(batch)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(findings, indent=1, sort_keys=True), encoding="utf-8")

    # ruff echoes the absolute path it was given, so the backend split is a
    # prefix test on the file, not a guess from the finding's own text.
    backend_prefix = str(repo_root / "backend").replace("\\", "/").rstrip("/") + "/"

    def scope_of(finding: dict[str, object]) -> str:
        filename = str(finding["filename"]).replace("\\", "/")
        return "backend" if filename.startswith(backend_prefix) else "other"

    per_rule = collections.Counter()
    per_scope = collections.Counter()
    for finding in findings:
        per_rule[str(finding["code"])] += 1
        per_scope[scope_of(finding)] += 1
    print(f"  RULES   findings={len(findings)} backend={per_scope['backend']} other={per_scope['other']} (ruff exit {worst_exit}, {len(chunks)} invocation(s))")
    for code, count in sorted(per_rule.items(), key=lambda item: (-item[1], item[0])):
        print(f"    {code:<16}{count}")

    # The gate also enforces `ruff format --check`, so the other half of its
    # verdict is measured here too rather than left unstated.
    unformatted: list[str] = []
    for chunk in chunks:
        format_command = _ruff_command(ruff_cwd, repo_root, "format", ["--check", "--no-cache", *chunk])
        format_result = _run(format_command, repo_root, args.ruff_timeout)
        unformatted.extend(line.split(": ", 1)[1] for line in format_result.stdout.decode("utf-8", errors="replace").splitlines() if line.startswith("Would reformat: "))
    print(f"  FORMAT  files needing `ruff format`={len(unformatted)} (same scope)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
