"""Command-line entry point for the authority auditor.

Examples (run from the repository root)::

    python -m alpha.safety.authority scan --root . --json
    python -m alpha.safety.authority scan --root . --markdown --output docs/AUTHORITY_MODEL.md
    python -m alpha.safety.authority diff --root . --baseline .authority/baseline.json
    python -m alpha.safety.authority baseline --root . --output .authority/baseline.json

Exit codes are part of the contract: ``0`` means no triage finding, ``1``
means the report contains an unknown, an ungated destructive capability, an
ineffective gate, or a baseline change that needs review, and ``2`` means a
usage/configuration error.  The tool is static and offline; it never imports
Alpha, starts a process, or reaches the network.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .baseline import BaselineDiff, diff_reports, load_baseline, write_baseline
from .census import scan
from .config import AuthorityAuditConfig
from .report import render_json, render_markdown

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2


def _add_format_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--json", action="store_true", help="Emit the deterministic JSON census.")
    group.add_argument("--markdown", action="store_true", help="Emit the deterministic Markdown census.")


def build_parser() -> argparse.ArgumentParser:
    """Build the parser without reading configuration or touching the tree."""

    parser = argparse.ArgumentParser(prog="alpha.safety.authority", description="Static, read-only authority census")
    parser.add_argument("--version", action="version", version="authority-audit/1")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan a repository")
    scan_parser.add_argument("--root", required=True, help="Repository root")
    _add_format_flags(scan_parser)
    scan_parser.add_argument("--output", help="Write the report to exactly this path")
    scan_parser.add_argument("--revision", default="unrevised", help="Injected commit/revision string")
    scan_parser.add_argument("--config", help="JSON AuthorityAuditConfig path")
    scan_parser.add_argument("--strict", action="store_true", help="Treat every unknown as a failure")

    diff_parser = subparsers.add_parser("diff", help="Compare a scan with a stored baseline")
    diff_parser.add_argument("--root", required=True, help="Repository root")
    diff_parser.add_argument("--baseline", help="Baseline JSON path (defaults to config.baseline_path)")
    _add_format_flags(diff_parser)
    diff_parser.add_argument("--output", help="Write the diff to exactly this path")
    diff_parser.add_argument("--revision", default="unrevised", help="Injected current commit/revision string")
    diff_parser.add_argument("--config", help="JSON AuthorityAuditConfig path")
    diff_parser.add_argument("--strict", action="store_true", help="Treat every unknown as a failure")

    for name in ("baseline", "write-baseline"):
        baseline_parser = subparsers.add_parser(name, help="Explicitly write a census baseline")
        baseline_parser.add_argument("--root", required=True, help="Repository root")
        baseline_parser.add_argument("--output", help="Baseline path (defaults to config.baseline_path)")
        baseline_parser.add_argument("--revision", default="unrevised", help="Injected commit/revision string")
        baseline_parser.add_argument("--config", help="JSON AuthorityAuditConfig path")
    return parser


def _settings(config_path: str | None) -> AuthorityAuditConfig:
    if config_path:
        return AuthorityAuditConfig.from_file(config_path)
    return AuthorityAuditConfig()


def _output_format(args: argparse.Namespace) -> str:
    return "markdown" if getattr(args, "markdown", False) else "json"


def _resolve_output(args: argparse.Namespace, settings: AuthorityAuditConfig, root: Path) -> Path | None:
    explicit = getattr(args, "output", None)
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = root / path
        return path.resolve()
    return settings.resolve_output_path(root)


def _strict(args: argparse.Namespace, settings: AuthorityAuditConfig) -> bool:
    return bool(getattr(args, "strict", False) or settings.unknown_is_failure())


def _print_or_write(text: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _run_scan(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.exists() or not root.is_dir():
        sys.stderr.write(f"root is not a directory: {root}\n")
        return EXIT_USAGE
    settings = _settings(args.config)
    settings.should_run(explicitly_requested=True)
    report = scan(root, config=settings, revision=args.revision)
    output = _resolve_output(args, settings, root)
    text = render_markdown(report) if _output_format(args) == "markdown" else render_json(report)
    _print_or_write(text, output)
    if output is not None:
        sys.stdout.write(f"authority audit: {len(report.records)} capabilities, {len(report.unknowns)} unknowns, coverage {report.coverage.describe()}\n")
    return EXIT_FINDINGS if report.has_triage_findings(strict=_strict(args, settings)) else EXIT_CLEAN


def _render_diff_markdown(diff: BaselineDiff) -> str:
    lines = [
        "# Authority Baseline Diff",
        "",
        f"- Baseline revision: `{diff.baseline_revision}`",
        f"- Current revision: `{diff.current_revision}`",
        "",
    ]
    if diff.posture_flips:
        lines.extend(["## CRITICAL: posture flips", ""])
        for flip in diff.posture_flips:
            lines.append(f"- `{flip.gate_id}` at `{flip.source}` changed `{flip.before.value}` → `{flip.after.value}`: {flip.reason}")
        lines.append("")
    for title, values in (
        ("New destructive capabilities", diff.new_destructive_capabilities),
        ("New capabilities", diff.new_capabilities),
        ("Removed gates", diff.removed_gates),
        ("New gates", diff.new_gates),
        ("New unknowns", diff.new_unknowns),
        ("Resolved unknowns", diff.resolved_unknowns),
    ):
        lines.extend([f"## {title}", ""])
        lines.extend([f"- `{value}`" for value in values] or ["- None"])
        lines.append("")
    return "\n".join(lines) + "\n"


def _run_diff(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.exists() or not root.is_dir():
        sys.stderr.write(f"root is not a directory: {root}\n")
        return EXIT_USAGE
    settings = _settings(args.config)
    settings.should_run(explicitly_requested=True)
    baseline_path = Path(args.baseline) if args.baseline else settings.resolve_baseline_path(root)
    if baseline_path is None:
        sys.stderr.write("baseline path is required (use --baseline or config.baseline_path)\n")
        return EXIT_USAGE
    if not baseline_path.is_absolute():
        baseline_path = root / baseline_path
    try:
        baseline = load_baseline(baseline_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"cannot load baseline {baseline_path}: {exc}\n")
        return EXIT_USAGE
    current = scan(root, config=settings, revision=args.revision)
    diff = diff_reports(baseline, current)
    output = _resolve_output(args, settings, root)
    text = _render_diff_markdown(diff) if _output_format(args) == "markdown" else json.dumps(diff.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _print_or_write(text, output)
    if output is not None:
        sys.stdout.write(f"authority baseline diff: {'changed' if diff.has_changes else 'unchanged'}\n")
    if diff.posture_flips or diff.has_changes:
        return EXIT_FINDINGS
    if _strict(args, settings) and current.has_unknown:
        return EXIT_FINDINGS
    return EXIT_CLEAN


def _run_baseline(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.exists() or not root.is_dir():
        sys.stderr.write(f"root is not a directory: {root}\n")
        return EXIT_USAGE
    settings = _settings(args.config)
    destination = _resolve_output(args, settings, root)
    if destination is None:
        sys.stderr.write("baseline output is required (use --output or config.baseline_path)\n")
        return EXIT_USAGE
    report = scan(root, config=settings, revision=args.revision)
    try:
        written = write_baseline(destination, report, revision=args.revision)
    except (OSError, ValueError, TypeError) as exc:
        sys.stderr.write(f"cannot write baseline {destination}: {exc}\n")
        return EXIT_USAGE
    sys.stdout.write(f"authority baseline written: {written.as_posix()}\n")
    return EXIT_CLEAN


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the documented exit code."""

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else EXIT_USAGE
        return code
    try:
        if args.command == "scan":
            return _run_scan(args)
        if args.command == "diff":
            return _run_diff(args)
        if args.command in {"baseline", "write-baseline"}:
            return _run_baseline(args)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"authority audit error: {exc}\n")
        return EXIT_USAGE
    parser.error(f"unknown command: {args.command}")
    return EXIT_USAGE


__all__ = [
    "EXIT_CLEAN",
    "EXIT_FINDINGS",
    "EXIT_USAGE",
    "build_parser",
    "main",
]
