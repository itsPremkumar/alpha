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
    ModPriority,
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
    mods = kernel.list_mods()
    if not mods:
        print("No mods currently registered in Alpha Mod Kernel.")
        return 0

    headers = ["Priority", "Tier", "Mod Name", "Version", "Capabilities", "Subscribed Events"]
    rows = []
    for m in mods:
        priority_val = getattr(m, "priority", 2000)
        try:
            tier_name = ModPriority(priority_val).name
        except Exception:
            tier_name = "CUSTOM"
        caps = getattr(m, "required_capabilities", set())
        subs = getattr(m, "subscribed_events", None)
        sub_str = "*" if subs is None else ", ".join(sorted(subs))
        rows.append(
            [
                str(priority_val),
                tier_name,
                getattr(m, "name", "unknown"),
                getattr(m, "version", "1.0.0"),
                ", ".join(sorted(caps)) or "none",
                sub_str,
            ]
        )
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
    elif args.subcommand == "validate":
        return cmd_validate(args)
    elif args.subcommand == "test":
        return cmd_test(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
