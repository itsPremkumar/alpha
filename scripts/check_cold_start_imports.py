#!/usr/bin/env python3
"""Cold-start import gate: no new *eager* import on the startup path.

Why a second gate
-----------------
``check_cold_start_budget.py`` enforces cold start in the only unit that
matters -- wall-clock milliseconds -- and a wall-clock median measured on a
shared runner is a flake source. This gate enforces the same property in a
unit that cannot flake at all: **structure**. It parses the startup-path
packages and counts the imports that run when the module is *imported*, which
is exactly the quantity cold start scales with (``cold_start_probe.py``: cold
cost "scales with how many modules and how much source you import"). It needs
no subprocess, no wall clock, no installed dependencies beyond the standard
library, and it produces the same answer on a laptop, on a Windows host under
Defender, and on a 4-core Linux runner.

The regression class it catches
-------------------------------
A newly added **eager** import on the startup path: a module-scope
``import torch`` in a package ``__init__``, a ``from alpha.memory import x``
moved up out of a function body, a ``from app.gateway.app import app`` that
makes a router drag the whole ASGI app in. Wall-clock budgets catch those only
after they are big enough to matter and only on a quiet host; this catches them
in the diff, in seconds, every time.

What counts as eager
--------------------
* Any ``import``/``from ... import`` reachable at module scope, **including**
  inside ``try``/``except ImportError`` and ``if`` blocks, because those still
  execute during the import.
* Class bodies: they run at import time too.
* **Not** function/async-function/lambda bodies, and **not** anything inside a
  positive ``if TYPE_CHECKING:`` block. Both of those are the *supported* way
  to keep a dependency off the startup path -- ``alpha/config/__init__.py``
  keeps ~50 sibling config modules behind ``TYPE_CHECKING`` plus a
  :pep:`562` ``__getattr__``, and that shape must stay free.

The two budgets
---------------
``third_party_roots``
    Every third-party top-level package that any startup-path module imports
    at module scope. A *new* one is a new eager dependency on the import path,
    which is a review decision, not an accident.
``eager_first_party_by_package``
    The count of module-scope first-party imports per package. This is the
    eager-``__init__`` problem measured directly: the budget cannot tell you
    *which* import is the expensive one, so it counts them, and every addition
    has to be acknowledged in the same commit that adds it.

**Improvements are never auto-applied.** A package under budget is reported as
an opportunity; the file only moves when a human runs ``--write-budget`` and
commits the result.

What this gate does not claim
-----------------------------
It is a static upper bound over the whole scanned tree, so a module that is
*not* actually reachable from a cold start can still trip it. That is
deliberate -- the price of an instant deterministic gate -- and the remedy is
a one-line budget bump in the same diff, reviewed on its merits. It also does
not measure time; that stays with ``check_cold_start_budget.py``, which runs
nightly for exactly that reason.

Usage::

    python scripts/check_cold_start_imports.py --json
    python scripts/check_cold_start_imports.py --write-budget   # human, then commit
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BUDGET = REPO_ROOT / "backend" / "benchmarks" / "cold_start" / "import_budget.json"

#: Schema this gate understands. A budget written by a different version of the
#: collector is a hard failure, not something to guess at.
BUDGET_SCHEMA = 1

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_ERROR = 2

#: Packages that are this repository's own code even though they are not under
#: an ``alpha``/``app`` directory (the extension API ships as an installed
#: workspace distribution). Overriding the default keeps them out of the
#: third-party allowlist, where they would be indistinguishable from ``numpy``.
DEFAULT_FIRST_PARTY_ROOTS = ("alpha", "app", "agent_workspace_extension_api")


class GateError(RuntimeError):
    """Anything that stops the gate from producing a verdict."""


@dataclass(frozen=True)
class EagerImport:
    """One module-scope import statement, resolved to an absolute dotted name."""

    line: int
    target: str
    root: str


@dataclass(frozen=True)
class ScanRoot:
    """A directory to scan, and the dotted module root its files belong to.

    The two are stated rather than inferred: ``backend/app/gateway`` is the
    directory but ``app.gateway`` is the package name, and the mapping is a
    convention that would silently rot if it were recomputed from the path.
    """

    path: str
    module_root: str


#: The startup path, in the order the probe's default targets walk it. Every
#: default target -- ``alpha``, ``alpha.config.memory_config``, ``alpha.memory``,
#: ``alpha.agents.lead_agent.prompt``, ``alpha.tools.builtins`` and
#: ``app.gateway.app`` -- imports through one of these two roots.
DEFAULT_SCAN_ROOTS = (
    ScanRoot("backend/packages/harness/alpha", "alpha"),
    ScanRoot("backend/app/gateway", "app.gateway"),
)


@dataclass(frozen=True)
class FileImports:
    """What one source file imports eagerly."""

    path: Path
    package: str  # dotted package the file belongs to ("alpha.config")
    first_party: tuple[EagerImport, ...]
    stdlib: tuple[EagerImport, ...]
    third_party: tuple[EagerImport, ...]


# --------------------------------------------------------------------------
# AST collection
# --------------------------------------------------------------------------


def _is_positive_type_checking(test: ast.expr) -> bool:
    """True for a bare ``if TYPE_CHECKING:`` (or ``typing.TYPE_CHECKING``).

    ``if not TYPE_CHECKING:`` is the *opposite* -- its body is the eager path
    -- so a negation must not be treated as free.
    """
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    if isinstance(test, ast.BoolOp) and test.values:
        # `if TYPE_CHECKING and something:` -- still guarded.
        return _is_positive_type_checking(test.values[0])
    return False


def _resolve_relative(package: str, level: int, module: str | None) -> str:
    """Resolve a relative import against the importing file's package.

    ``package`` is the dotted package the *file* lives in, so a module
    ``alpha/config/app_config.py`` has package ``alpha.config`` and
    ``from .paths import x`` resolves to ``alpha.config.paths``. ``__init__.py``
    passes its own directory's package, which is what makes ``from . import x``
    inside it resolve to ``<dir>.x`` instead of the parent.
    """
    base = package.split(".") if package else []
    keep = len(base) - (level - 1)
    trimmed = ".".join(base[: max(keep, 0)])
    return f"{trimmed}.{module}" if module else trimmed


def _statements(body: list[ast.stmt]) -> list[ast.stmt]:
    """Flatten module-scope statements, dropping function bodies and TYPE_CHECKING.

    Recursing into a ``ClassDef``, a ``try``/``except`` (including the handler
    bodies, which run on the fallback path) and a module-level ``if`` is
    deliberate: all of that executes during the import. Recursing into a
    def/lambda is not: that code runs later.
    """
    out: list[ast.stmt] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.If) and _is_positive_type_checking(node.test):
            continue
        if isinstance(node, ast.Try):
            nested: list[ast.stmt] = [*node.body, *node.orelse, *node.finalbody]
            for handler in node.handlers:
                nested.extend(handler.body)
            out.extend(_statements(nested))
            continue
        if isinstance(node, (ast.If, ast.With, ast.For, ast.While)):
            nested = [child for child in ast.iter_child_nodes(node) if isinstance(child, ast.stmt)]
            out.extend(_statements(nested))
            continue
        if isinstance(node, ast.ClassDef):
            out.extend(_statements(node.body))
            continue
        out.append(node)
    return out


def _absolute_targets(node: ast.stmt, package: str) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level:
            base = _resolve_relative(package, node.level, node.module)
        else:
            base = node.module or ""
        if not base:
            return []
        if not node.names:
            return [base]
        return [f"{base}.{alias.name}" for alias in node.names]
    return []


def _root_of(target: str) -> str:
    return target.lstrip(".").split(".", 1)[0]


def classify(root: str, first_party_roots: frozenset[str], stdlib: frozenset[str]) -> str:
    if root in first_party_roots:
        return "first_party"
    if root in stdlib or root == "__future__":
        return "stdlib"
    return "third_party"


def collect_file(path: Path, package: str, first_party_roots: frozenset[str]) -> FileImports:
    """Every module-scope import in one file, bucketed by kind."""
    stdlib = frozenset(getattr(sys, "stdlib_module_names", ()))
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GateError(f"cannot read {path.as_posix()} as UTF-8: {exc}") from exc
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        # A file the collector cannot parse is a file whose eager imports are
        # invisible, which is precisely what this gate exists to count. Refuse
        # to produce a verdict rather than produce a partial one.
        raise GateError(f"cannot parse {path.as_posix()}: line {exc.lineno}: {exc.msg}\n  The eager-import budget is an AST scan, so an unparseable file hides\n  exactly the imports this gate counts. The gate fails closed.") from exc
    buckets: dict[str, list[EagerImport]] = {
        "first_party": [],
        "stdlib": [],
        "third_party": [],
    }
    for node in _statements(tree.body):
        for target in _absolute_targets(node, package):
            root = _root_of(target)
            if not root:
                continue
            kind = classify(root, first_party_roots, stdlib)
            buckets[kind].append(EagerImport(node.lineno, target, root))
    return FileImports(
        path=path,
        package=package,
        first_party=tuple(buckets["first_party"]),
        stdlib=tuple(buckets["stdlib"]),
        third_party=tuple(buckets["third_party"]),
    )


def package_of(path: Path, root: Path, module_root: str) -> str | None:
    """Dotted *package* the file lives in -- its containing directory.

    ``alpha/config/app_config.py`` is in ``alpha.config``, not
    ``alpha.config.app_config``: the budget is per package, so a module and its
    directory are one budget key, and adding a file to a package does not
    silently create a second key. A module directly under the root
    (``alpha/memory.py``) belongs to the root package itself, ``alpha``.

    This is also exactly the value a relative import resolves against, which is
    why the collector takes one ``package`` argument rather than two.
    """
    try:
        relative = path.relative_to(root)
    except ValueError:  # pragma: no cover - callers pass files under root
        return None
    parts = list(relative.parts)[:-1]  # drop the file name
    return ".".join([module_root, *parts])


def scan(
    repo_root: Path,
    roots: list[ScanRoot],
    first_party_roots: tuple[str, ...],
) -> tuple[list[FileImports], list[str]]:
    """Scan every ``.py`` under each committed scan root.

    Returns the per-file records and the list of scan roots that matched no
    files, which the caller treats as a failure: a moved directory that leaves
    a gate scanning nothing is a green light wired to nothing.
    """
    records: list[FileImports] = []
    empty: list[str] = []
    for entry in roots:
        directory = (repo_root / entry.path).resolve()
        if not directory.is_dir():
            empty.append(entry.path)
            continue
        found = sorted(directory.rglob("*.py"))
        if not found:
            empty.append(entry.path)
            continue
        for path in found:
            package = package_of(path, directory, entry.module_root)
            if package is None:  # pragma: no cover - path is under directory
                continue
            records.append(collect_file(path, package, frozenset(first_party_roots)))
    return records, empty


# --------------------------------------------------------------------------
# budget
# --------------------------------------------------------------------------


def load_budget(path: Path) -> dict[str, Any]:
    """Read the budget or fail loudly. Never returns a default."""
    if not path.exists():
        raise GateError(
            f"import budget not found: {path}\n"
            "  Eager startup imports are an enforced property, so an absent budget is a\n"
            "  failure, not a skip. Create it deliberately and commit it:\n"
            f"    python scripts/check_cold_start_imports.py --write-budget --budget {path}"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"import budget unreadable: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GateError(f"import budget is not a JSON object: {path}")
    if data.get("schema_version") != BUDGET_SCHEMA:
        raise GateError(f"import budget schema_version {data.get('schema_version')!r} != {BUDGET_SCHEMA!r}: {path}\n  Regenerate it with --write-budget; the collector's shape changed.")
    for key, kind in (
        ("scan_roots", list),
        ("third_party_roots", list),
        ("first_party_roots", list),
        ("eager_first_party_by_package", dict),
    ):
        if not isinstance(data.get(key), kind):
            raise GateError(f"import budget has no {key!r} {kind.__name__}: {path}")
    for entry in data["scan_roots"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not isinstance(entry.get("module_root"), str):
            raise GateError(f"import budget scan_roots entry is not {{'path': str, 'module_root': str}}: {path}")
    return data


def scan_roots_of(budget: dict[str, Any]) -> list[ScanRoot]:
    return [ScanRoot(entry["path"], entry["module_root"]) for entry in budget["scan_roots"]]


def build_budget(
    records: list[FileImports],
    roots: list[ScanRoot],
    first_party_roots: tuple[str, ...],
) -> dict[str, Any]:
    """Derive a budget from the tree. Only ever called by --write-budget."""
    third_party: set[str] = set()
    per_package: dict[str, int] = {}
    for record in records:
        for item in record.third_party:
            third_party.add(item.root)
        count = per_package.get(record.package, 0) + len(record.first_party)
        per_package[record.package] = count
    # Only packages that carry at least one eager first-party import get an
    # entry. An absent package is budgeted at zero, so a package that has *no*
    # eager import today and gains one tomorrow is a regression -- and a
    # package that loses all of them reports as a stale entry to delete.
    return {
        "schema_version": BUDGET_SCHEMA,
        "tool": "check_cold_start_imports",
        "description": ("Eager (module-scope) imports on the cold-start path. A new entry is a new eager dependency at import time; regenerate with --write-budget only after reviewing the diff, never to silence a failure."),
        "scan_roots": [{"path": root.path, "module_root": root.module_root} for root in roots],
        "first_party_roots": list(first_party_roots),
        "third_party_roots": sorted(third_party),
        "eager_first_party_by_package": {key: per_package[key] for key in sorted(per_package) if per_package[key] > 0},
    }


# --------------------------------------------------------------------------
# decision
# --------------------------------------------------------------------------


def _judge(records: list[FileImports], budget: dict[str, Any]) -> dict[str, Any]:
    """Full verdict for a scan against a budget."""
    allowed_roots = set(budget["third_party_roots"])
    package_budget = {key: int(value) for key, value in budget["eager_first_party_by_package"].items()}

    new_roots: dict[str, list[str]] = {}
    for record in records:
        for item in record.third_party:
            if item.root in allowed_roots:
                continue
            new_roots.setdefault(item.root, []).append(f"{record.path.as_posix()}:{item.line} -> {item.target}")

    observed: dict[str, int] = {}
    contributors: dict[str, list[str]] = {}
    for record in records:
        if not record.first_party:
            continue
        observed[record.package] = observed.get(record.package, 0) + len(record.first_party)
        contributors.setdefault(record.package, []).extend(f"{record.path.as_posix()}:{item.line} -> {item.target}" for item in record.first_party)

    grown = {package: count for package, count in sorted(observed.items()) if count > package_budget.get(package, 0)}
    # A budgeted package that no longer exists in the tree has shrunk, not
    # regressed; that is a stale entry to delete, reported, never a failure.
    vanished = sorted(set(package_budget) - set(observed))
    improved = sorted(package for package in observed if package in package_budget and observed[package] < package_budget[package])
    return {
        "status": "fail" if new_roots or grown else "pass",
        "third_party_root_regressions": new_roots,
        "package_regressions": {
            package: {
                "budget": package_budget.get(package, 0),
                "observed": count,
                "imports": sorted(contributors.get(package, ())),
            }
            for package, count in grown.items()
        },
        "improvements": improved,
        "stale_entries": vanished,
        "files_scanned": len(records),
        "third_party_roots_allowed": len(allowed_roots),
        "packages_budgeted": len(package_budget),
    }


def print_report(result: dict[str, Any]) -> int:
    print("cold-start eager imports")
    print(f"  scanned {result['files_scanned']} files, {result['third_party_roots_allowed']} third-party roots allowed, {result['packages_budgeted']} packages budgeted")
    print()

    roots = result["third_party_root_regressions"]
    if roots:
        print(f"NEW EAGER THIRD-PARTY DEPENDENCY ({len(roots)}):")
        for root in sorted(roots):
            print(f"  {root}")
            for site in roots[root]:
                print(f"      {site}")
        print("    Defer the import into the function that needs it, or record it")
        print("    deliberately with --write-budget and say why in the diff.")
        print()

    packages = result["package_regressions"]
    if packages:
        print(f"EAGER FIRST-PARTY IMPORTS OVER BUDGET ({len(packages)}):")
        for package, detail in packages.items():
            print(f"  {package}: {detail['observed']} eager imports, budget {detail['budget']} (+{detail['observed'] - detail['budget']})")
            for site in detail["imports"]:
                print(f"      {site}")
        print("    An eager package __init__ is how startup grows without anyone")
        print("    deciding it should. Defer, or acknowledge the new count.")
        print()

    if result["stale_entries"]:
        print(f"STALE BUDGET ENTRIES ({len(result['stale_entries'])}) -- packages that no longer carry eager imports (informational; the entries are safe to delete):")
        print("  " + ", ".join(result["stale_entries"]))
        print()

    if result["improvements"]:
        print(f"UNDER BUDGET ({len(result['improvements'])}) -- deferring worked; the gate never lowers a bar on its own:")
        print("  " + ", ".join(result["improvements"]))
        print()

    return EXIT_REGRESSION if result["status"] == "fail" else EXIT_OK


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gate eager (module-scope) imports on the cold-start path.")
    parser.add_argument("--budget", type=Path, default=BUDGET)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--write-budget",
        action="store_true",
        help="replace the budget with this scan (human decision; then commit it)",
    )
    parser.add_argument("--json", action="store_true", help="also print the result as JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    try:
        return _run(args, repo_root)
    except GateError as exc:
        print(f"cold-start import gate FAILED CLOSED: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _run(args: argparse.Namespace, repo_root: Path) -> int:
    if args.write_budget:
        # A scan of the real tree is the only thing that can seed a budget, so
        # the scan roots come from the existing budget when there is one and
        # from the documented default otherwise.
        try:
            existing = load_budget(args.budget)
            roots = scan_roots_of(existing)
            first_party_roots = tuple(existing["first_party_roots"])
        except GateError:
            roots = list(DEFAULT_SCAN_ROOTS)
            first_party_roots = DEFAULT_FIRST_PARTY_ROOTS
        records, empty = scan(repo_root, roots, first_party_roots)
        if empty:
            raise GateError(f"refusing to write a budget: no Python files under {', '.join(empty)}")
        budget = build_budget(records, roots, first_party_roots)
        args.budget.parent.mkdir(parents=True, exist_ok=True)
        args.budget.write_text(json.dumps(budget, indent=2) + "\n", encoding="utf-8")
        print(f"wrote budget {args.budget} ({len(budget['third_party_roots'])} third-party roots, {len(budget['eager_first_party_by_package'])} packages) -- review and commit it")
        return EXIT_OK

    budget = load_budget(args.budget)
    first_party_roots = tuple(budget["first_party_roots"])
    records, empty = scan(repo_root, scan_roots_of(budget), first_party_roots)
    if empty:
        raise GateError("no Python files under " + ", ".join(empty) + "\n  A scan root that moved leaves a gate that inspects nothing, which reads\n  as green. Fix the path in the budget, or re-seed it with --write-budget.")
    if not records:  # pragma: no cover - covered by the empty-scan check above
        raise GateError("scanned zero files")

    result = _judge(records, budget)
    if args.json:
        print(json.dumps({"baseline": str(args.budget), **result}, indent=2))
    return print_report(result)


if __name__ == "__main__":
    raise SystemExit(main())
