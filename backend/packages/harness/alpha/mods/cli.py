"""Command-line interface and offline developer tools for Alpha Mods (AMK)."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import inspect
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from alpha.mods.kernel import ModKernel, get_mod_kernel
from alpha.mods.types import (
    AlphaEvent,
    CorrelationContext,
    EventOutcome,
)

logger = logging.getLogger(__name__)


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    """Format an ASCII table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val)))
    header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_line = "-+-".join("-" * col_widths[i] for i in range(len(headers)))
    row_lines = [" | ".join(str(val).ljust(col_widths[i]) for i, val in enumerate(row)) for row in rows]
    return f"{header_line}\n{sep_line}\n" + "\n".join(row_lines)


def cmd_list(args: argparse.Namespace) -> int:
    """alpha mod list — inspect registered mods."""
    kernel = get_mod_kernel()
    descriptions = kernel.describe_mods()
    if not descriptions:
        print("No mods currently registered in Alpha Mod Kernel.")
        return 0

    headers = ["Priority", "Mod Name", "Version", "First-party", "Discrepancies", "Subscribed Events"]
    rows = []
    for d in descriptions:
        subs = d.get("subscribed_events") or []
        sub_str = ", ".join(subs)
        rows.append(
            [
                str(d.get("priority", "?")),
                str(d.get("name", "unknown")),
                str(d.get("version", "1.0.0")),
                "yes" if d.get("first_party") else "NO",
                str(len(d.get("discrepancies") or [])),
                sub_str,
            ]
        )
    print(_format_table(headers, rows))
    discrepancy_total = sum(len(d.get("discrepancies") or []) for d in descriptions)
    if discrepancy_total:
        print(f"\n{discrepancy_total} manifest discrepancy(ies) across the fleet — run 'alpha mod describe' for the names.")
    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    """alpha mod describe [name] — declared contract beside observed behaviour.

    The review-facing answer to "what does this mod claim, what is it actually
    wired to, and where do those differ?". Nothing here executes a mod.
    """
    import json

    kernel = get_mod_kernel()
    descriptions = kernel.describe_mods()
    if args.name:
        descriptions = [d for d in descriptions if d.get("name") == args.name]
        if not descriptions:
            print(f"Error: mod '{args.name}' is not registered.", file=sys.stderr)
            return 1
    for d in descriptions:
        print(json.dumps(d, indent=2, sort_keys=True, default=str))
        print()
    return 0


def cmd_chain(args: argparse.Namespace) -> int:
    """alpha mod chain — the ordered chain as dispatch runs it.

    Sorted by the same priority key dispatch uses, never by registration time:
    this is the auditable answer to "who runs first, and can anything outrank
    the safety triad?".
    """
    kernel = get_mod_kernel()
    chain = kernel.control_chain()
    if not chain:
        print("No mods currently registered in Alpha Mod Kernel.")
        return 0
    headers = ["Order", "Priority", "Mod", "Version", "First-party", "Subscribed Events"]
    rows = [
        [
            str(c["order"]),
            str(c["priority"]),
            str(c["mod"]),
            str(c["version"]),
            "yes" if c["first_party"] else "NO",
            ", ".join(c["subscribed_events"]),
        ]
        for c in chain
    ]
    print(_format_table(headers, rows))
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """alpha mod audit — recent audit records and aggregate counters.

    Admin/CI surface over the same ledger ``GET /api/mods/audit`` serves. An
    absent audit mod is an error naming it, never an empty report that reads as
    "nothing happened".
    """
    kernel = get_mod_kernel()
    audit = kernel.get_mod("audit_ledger")
    if audit is None:
        print("Error: audit_ledger mod is not registered in this kernel; no audit records could be read.", file=sys.stderr)
        return 1
    entries = audit.get_entries(limit=args.limit, event_name=args.event, outcome=args.outcome)
    stats = audit.stats()
    print(f"--- Audit ledger: {stats['retained']}/{stats['capacity']} retained, {stats['events']} distinct events ---")
    if args.jsonl:
        print(audit.export_jsonl(limit=args.limit))
        return 0
    if not entries:
        print("No audit records match the given filters.")
        return 0
    headers = ["Event", "Outcome", "Chain", "Duration (ms)"]
    rows = [
        [
            str(e.get("event", "")),
            str(e.get("outcome", "")),
            " > ".join(str(m) for m in (e.get("chain") or [])),
            f"{float(e.get('duration_ms', 0.0)):.1f}",
        ]
        for e in entries
    ]
    print(_format_table(headers, rows))
    return 0


def cmd_holds(args: argparse.Namespace) -> int:
    """alpha mod holds — held actions awaiting (or carrying) an operator decision.

    The durable approval queue behind a mod ``DEFER``. Approving or rejecting is
    an authenticated Gateway/API act (``POST /api/mods/holds/{id}/approve``);
    this command only reads, so it can never release an action by itself.
    """
    from alpha.mods.approvals import get_hold_store

    store = get_hold_store()
    records = store.list_holds(decision=args.decision, limit=args.limit)
    print(f"--- Mod hold store: {store.store_file} ---")
    if not records:
        print("No held actions recorded.")
        return 0
    headers = ["Hold ID", "Tool", "Risk", "Decision", "Reason", "Expires (unix)"]
    rows = [
        [
            r.hold_id,
            r.tool_name,
            r.risk_level,
            r.decision + (" (EXPIRED)" if r.is_expired() and not r.is_terminal() else ""),
            (r.reason[:60] + "…") if len(r.reason) > 60 else r.reason,
            f"{r.expires_at:.0f}" if r.expires_at else "—",
        ]
        for r in records
    ]
    print(_format_table(headers, rows))
    return 0


class ModAstValidator(ast.NodeVisitor):
    """AST analyzer checking declared vs accessed capabilities and forbidden patterns."""

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.declared_capabilities: set[str] = set()
        self.accessed_namespaces: set[str] = set()
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.has_mod_class = False

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Check if class implements handle or has name/priority attributes
        has_handle = any(isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)) and b.name == "handle" for b in node.body)
        if has_handle:
            self.has_mod_class = True
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and target.id == "required_capabilities":
                            if isinstance(item.value, (ast.Set, ast.List, ast.Tuple)):
                                for el in item.value.elts:
                                    if isinstance(el, ast.Constant) and isinstance(el.value, str):
                                        self.declared_capabilities.add(el.value)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Check ctx.<namespace> access
        if isinstance(node.value, ast.Name) and node.value.id in {"ctx", "$"}:
            self.accessed_namespaces.add(node.attr)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in {"ctypes"}:
                self.errors.append(f"Forbidden direct import '{alias.name}' detected in mod code.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in {"ctypes"}:
            self.errors.append(f"Forbidden import from '{node.module}' detected in mod code.")
        self.generic_visit(node)


def cmd_validate(args: argparse.Namespace) -> int:
    """alpha mod validate <path> — perform static AST validation."""
    target_path = Path(args.path).resolve()
    if not target_path.exists():
        print(f"Error: Path '{target_path}' does not exist.", file=sys.stderr)
        return 1

    py_files = [target_path] if target_path.is_file() else list(target_path.rglob("*.py"))
    if not py_files:
        print(f"Error: No Python files found under '{target_path}'.", file=sys.stderr)
        return 1

    namespace_cap_map = {
        "tools": "tools:read",
        "models": "models:complete",
        "evidence": "evidence:record",
        "estop": "estop:control",
        "clock": "clock:schedule",
        "storage": "storage:write",
        "ui": "ui:render",
    }

    all_errors: list[str] = []
    all_warnings: list[str] = []

    for pf in py_files:
        try:
            tree = ast.parse(pf.read_text(encoding="utf-8"))
            validator = ModAstValidator(pf)
            validator.visit(tree)

            all_errors.extend(f"{pf.name}: {err}" for err in validator.errors)
            all_warnings.extend(f"{pf.name}: {warn}" for warn in validator.warnings)

            # Check undeclared capability access
            if validator.has_mod_class:
                for ns in validator.accessed_namespaces:
                    required_cap = namespace_cap_map.get(ns)
                    if required_cap:
                        # Allow read or write prefix variants
                        cap_prefix = required_cap.split(":")[0]
                        has_match = any(c == required_cap or c.startswith(f"{cap_prefix}:") for c in validator.declared_capabilities)
                        if not has_match:
                            all_errors.append(f"{pf.name}: Mod accesses 'ctx.{ns}' but does not declare capability '{required_cap}' in required_capabilities.")
        except SyntaxError as exc:
            all_errors.append(f"{pf.name}: Syntax error parsing file: {exc}")

    print(f"--- Static Mod Validation Report: {target_path} ---")
    if all_warnings:
        print(f"Warnings ({len(all_warnings)}):")
        for w in all_warnings:
            print(f"  [WARN] {w}")

    if all_errors:
        print(f"Errors ({len(all_errors)}):")
        for err in all_errors:
            print(f"  [FAIL] {err}", file=sys.stderr)
        print("\nResult: VALIDATION FAILED")
        return 1

    print("\nResult: VALIDATION PASSED (All AST checks clean)")
    return 0


def _load_mod_classes_from_path(target_path: Path) -> list[type]:
    """Dynamically load classes implementing AlphaMod protocol from path."""
    py_files = [target_path] if target_path.is_file() else list(target_path.rglob("*.py"))
    mod_classes: list[type] = []
    seen: set[tuple[str, str]] = set()
    for pf in py_files:
        if pf.name.startswith("test_") or pf.name.startswith("."):
            continue
        if pf.name == "__init__.py":
            continue
        spec = importlib.util.spec_from_file_location(f"mod_under_test_{pf.stem}", pf)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if hasattr(obj, "handle") and hasattr(obj, "priority") and hasattr(obj, "name"):
                        # Only count classes defined in this file, not re-exported
                        # imports (e.g. enforcers/__init__.py re-exports the triad).
                        if getattr(obj, "__module__", "") != module.__name__:
                            continue
                        key = (getattr(obj, "__name__", ""), getattr(obj, "__module__", ""))
                        if key in seen:
                            continue
                        seen.add(key)
                        mod_classes.append(obj)
            except Exception as exc:
                logger.debug("Failed loading module %s: %s", pf, exc)
    return mod_classes


async def _run_synthetic_test_suite(mod_cls: type) -> tuple[int, int, list[str]]:
    """Execute synthetic offline tests against a mod class."""
    passed = 0
    failed = 0
    logs: list[str] = []

    mod_instance = mod_cls()
    kernel = ModKernel()
    kernel.register_mod(mod_instance, granted_capabilities=set(getattr(mod_instance, "required_capabilities", set())))

    # Test 1: Safe read event dispatch
    try:
        corr = CorrelationContext.create()
        ev_safe = AlphaEvent(
            name="tool.requested",
            payload={"tool_name": "view_file", "tool_args": {"path": "README.md"}},
            correlation=corr,
        )
        res = await kernel.dispatch(ev_safe)
        if res.outcome in (EventOutcome.CONTINUE, EventOutcome.ANSWER):
            passed += 1
            logs.append(f"PASS: Safe read event allowed ({res.outcome.value})")
        elif res.outcome == EventOutcome.DENY:
            failed += 1
            logs.append(f"FAIL: Safe read event unexpectedly denied: {res.reason}")
        else:
            passed += 1
            logs.append(f"PASS: Handled event with outcome {res.outcome.value}")
    except Exception as exc:
        failed += 1
        logs.append(f"FAIL: Exception in safe event dispatch: {exc}")

    # Test 2: Destructive event dispatch (for security/blast radius mods)
    if "blast_radius" in getattr(mod_instance, "name", ""):
        try:
            ev_destructive = AlphaEvent(
                name="tool.requested",
                payload={"tool_name": "run_command", "tool_args": {"CommandLine": "rm -rf /"}},
                correlation=corr,
            )
            res = await kernel.dispatch(ev_destructive)
            if res.outcome == EventOutcome.DEFER:
                passed += 1
                logs.append("PASS: Destructive command intercepted with DEFER hold")
            else:
                failed += 1
                logs.append(f"FAIL: Destructive command was not deferred; outcome was {res.outcome.value}")
        except Exception as exc:
            failed += 1
            logs.append(f"FAIL: Destructive event test raised: {exc}")

    # Test 3: ESTOP behavior (for fleet_estop)
    if "estop" in getattr(mod_instance, "name", ""):
        try:
            # Trip mock estop
            ctx = kernel._create_context(mod_instance)
            ctx.estop.engage("Synthetic test stop")
            ev_estop = AlphaEvent(
                name="tool.requested",
                payload={"tool_name": "view_file", "tool_args": {"path": "foo"}},
                correlation=corr,
            )
            res = await kernel.dispatch(ev_estop)
            if res.outcome == EventOutcome.DENY:
                passed += 1
                logs.append("PASS: ESTOP engaged correctly halts event with DENY")
            else:
                failed += 1
                logs.append(f"FAIL: ESTOP was active but event outcome was {res.outcome.value}")
            ctx.estop.disengage()
        except Exception as exc:
            failed += 1
            logs.append(f"FAIL: ESTOP event test raised: {exc}")

    # Test 4: Completion honesty (for verification gate)
    if "verification" in getattr(mod_instance, "name", ""):
        try:
            ev_claim = AlphaEvent(
                name="turn.complete",
                payload={"status": "success", "message": "I have fixed everything and all tests pass"},
                correlation=corr,
            )
            res = await kernel.dispatch(ev_claim)
            # Kernel folds REWRITE through downstream and returns CONTINUE with
            # mod_rewrites evidence (see test_verification_gate_rejects_unverified_completion).
            rewrites = res.metadata.get("mod_rewrites", []) if isinstance(res.metadata, dict) else []
            if res.outcome == EventOutcome.CONTINUE and rewrites and any("EVIDENCE_GATE_DENIAL" in str(r.get("reason", "")) for r in rewrites if isinstance(r, dict)):
                passed += 1
                logs.append("PASS: Unverified success claim rewritten into remediation directive")
            else:
                failed += 1
                logs.append(f"FAIL: Unverified claim was not rewritten; outcome was {res.outcome.value}")
        except Exception as exc:
            failed += 1
            logs.append(f"FAIL: Verification gate test raised: {exc}")

    return passed, failed, logs


def cmd_test(args: argparse.Namespace) -> int:
    """alpha mod test <path> — run synthetic offline event runner."""
    import asyncio

    target_path = Path(args.path).resolve()
    if not target_path.exists():
        print(f"Error: Path '{target_path}' does not exist.", file=sys.stderr)
        return 1

    mod_classes = _load_mod_classes_from_path(target_path)
    if not mod_classes:
        # Check if the path targets builtins
        if "enforcer" in str(target_path).lower() or target_path.name == "mods":
            from alpha.mods.enforcers.blast_radius_mod import BlastRadiusGuardMod
            from alpha.mods.enforcers.estop_mod import FleetEstopMod
            from alpha.mods.enforcers.verification_gate_mod import VerificationEvidenceGateMod

            mod_classes = [FleetEstopMod, BlastRadiusGuardMod, VerificationEvidenceGateMod]
        else:
            print(f"Error: No AlphaMod classes found in '{target_path}'.", file=sys.stderr)
            return 1

    print("=== Synthetic Offline Mod Test Harness ===")
    print(f"Target: {target_path}")
    print(f"Loaded Mods: {[cls.__name__ for cls in mod_classes]}\n")

    total_passed = 0
    total_failed = 0

    for cls in mod_classes:
        print(f"Testing mod: {cls.__name__} ...")
        passed, failed, logs = asyncio.run(_run_synthetic_test_suite(cls))
        for line in logs:
            print(f"  {line}")
        total_passed += passed
        total_failed += failed
        print()

    print(f"--- Summary: {total_passed} Passed, {total_failed} Failed ---")
    if total_failed > 0:
        print("Result: TESTS FAILED", file=sys.stderr)
        return 1
    print("Result: ALL TESTS PASSED")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alpha mod",
        description="Alpha Mod Kernel (AMK) management and offline development tools.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # list
    sub.add_parser("list", help="list registered mods and their priority tiers")

    # describe
    desc_p = sub.add_parser("describe", help="declared manifest beside observed behaviour, with discrepancies")
    desc_p.add_argument("name", nargs="?", default=None, help="restrict to one mod (default: every registered mod)")

    # chain
    sub.add_parser("chain", help="the ordered control chain as dispatch runs it")

    # audit
    audit_p = sub.add_parser("audit", help="recent audit records and aggregate counters")
    audit_p.add_argument("--limit", type=int, default=50, help="maximum records to show (default 50)")
    audit_p.add_argument("--event", default=None, help="filter by event name")
    audit_p.add_argument("--outcome", default=None, help="filter by outcome (continue/deny/defer/...)")
    audit_p.add_argument("--jsonl", action="store_true", help="emit the records as JSONL instead of a table")

    # holds
    holds_p = sub.add_parser("holds", help="held actions awaiting an operator decision (read-only)")
    holds_p.add_argument("--limit", type=int, default=50, help="maximum holds to show (default 50)")
    holds_p.add_argument("--decision", default=None, help="filter by decision (pending/approved/rejected/expired)")

    # validate
    val_p = sub.add_parser("validate", help="static AST analysis of mod capabilities")
    val_p.add_argument("path", help="path to mod file or directory")

    # test
    test_p = sub.add_parser("test", help="synthetic offline event test runner")
    test_p.add_argument("path", help="path to mod file or directory")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.subcommand == "list":
        return cmd_list(args)
    elif args.subcommand == "describe":
        return cmd_describe(args)
    elif args.subcommand == "chain":
        return cmd_chain(args)
    elif args.subcommand == "audit":
        return cmd_audit(args)
    elif args.subcommand == "holds":
        return cmd_holds(args)
    elif args.subcommand == "validate":
        return cmd_validate(args)
    elif args.subcommand == "test":
        return cmd_test(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
