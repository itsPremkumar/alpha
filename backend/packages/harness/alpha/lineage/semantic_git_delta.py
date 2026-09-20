"""Semantic Git Time-Machine and AST Change-Delta Analyzer.

Raw git diffs answer *which lines changed*; engineering decisions need to know
*what meaning changed*. This module converts line-level ``+``/``-`` deltas into
structured semantic AST transformations:

* function / class / method renames (detected via body-shape equality, not
  textual similarity, so a rename is distinguished from a rewrite),
* parameter additions and removals, including whether a new parameter carries a
  default (adding a defaulted parameter is backward compatible, adding a
  required one is not),
* type annotation mutations on parameters and return values,
* decorator alterations,
* exception contract changes (raised and handled),
* symbol removals and visibility changes.

Each transformation carries a breaking-change weight. The analyzer aggregates
them into a **Breaking Change Risk Score** in ``[0.0, 1.0]``, computes the
**blast radius** across dependent modules using the symbol dependency graph,
and emits **migration recommendations** naming the concrete call sites that
must be updated.

Exposed agent tool
------------------
``analyze_semantic_git_delta``.
"""

from __future__ import annotations

import ast
import contextlib
import math
import os
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

MAX_FILES_PER_ANALYSIS: int = 400
GIT_TIMEOUT_SEC: float = 60.0


class TransformationKind(str, Enum):
    """Taxonomy of semantic transformations detected between two revisions."""

    SYMBOL_ADDED = "symbol_added"
    SYMBOL_REMOVED = "symbol_removed"
    SYMBOL_RENAMED = "symbol_renamed"
    PARAMETER_ADDED = "parameter_added"
    PARAMETER_REMOVED = "parameter_removed"
    PARAMETER_RENAMED = "parameter_renamed"
    PARAMETER_DEFAULT_CHANGED = "parameter_default_changed"
    ANNOTATION_CHANGED = "annotation_changed"
    RETURN_TYPE_CHANGED = "return_type_changed"
    DECORATOR_CHANGED = "decorator_changed"
    EXCEPTION_RAISED_CHANGED = "exception_raised_changed"
    EXCEPTION_HANDLED_CHANGED = "exception_handled_changed"
    BASE_CLASS_CHANGED = "base_class_changed"
    VISIBILITY_CHANGED = "visibility_changed"
    SIGNATURE_ORDER_CHANGED = "signature_order_changed"


# Weight of each transformation class when scoring breaking-change risk.
BREAKING_WEIGHTS: dict[TransformationKind, float] = {
    TransformationKind.SYMBOL_REMOVED: 1.0,
    TransformationKind.SYMBOL_RENAMED: 0.8,
    TransformationKind.PARAMETER_REMOVED: 0.9,
    TransformationKind.PARAMETER_ADDED: 0.7,
    TransformationKind.PARAMETER_RENAMED: 0.7,
    TransformationKind.SIGNATURE_ORDER_CHANGED: 0.85,
    TransformationKind.BASE_CLASS_CHANGED: 0.8,
    TransformationKind.RETURN_TYPE_CHANGED: 0.5,
    TransformationKind.ANNOTATION_CHANGED: 0.4,
    TransformationKind.PARAMETER_DEFAULT_CHANGED: 0.3,
    TransformationKind.DECORATOR_CHANGED: 0.45,
    TransformationKind.EXCEPTION_RAISED_CHANGED: 0.55,
    TransformationKind.EXCEPTION_HANDLED_CHANGED: 0.25,
    TransformationKind.VISIBILITY_CHANGED: 0.6,
    TransformationKind.SYMBOL_ADDED: 0.0,
}


@dataclass
class SemanticTransformation:
    """One semantic change detected between two revisions of a file."""

    kind: TransformationKind
    file_path: str
    symbol: str
    detail: str
    before: str = ""
    after: str = ""
    breaking: bool = True
    weight: float = 1.0
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "file_path": self.file_path,
            "symbol": self.symbol,
            "detail": self.detail,
            "before": self.before,
            "after": self.after,
            "breaking": self.breaking,
            "weight": round(self.weight, 4),
            "line": self.line,
        }


@dataclass
class BlastRadiusEntry:
    """A dependent module affected by a transformation."""

    file_path: str
    symbol: str
    relation: str
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "symbol": self.symbol,
            "relation": self.relation,
            "line": self.line,
        }


@dataclass
class SemanticDeltaReport:
    """Structured result of analyzing a revision delta."""

    base_ref: str
    head_ref: str
    files_changed: int = 0
    transformations: list[SemanticTransformation] = field(default_factory=list)
    risk_score: float = 0.0
    risk_band: str = "none"
    blast_radius: list[BlastRadiusEntry] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    breaking_count: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_ref": self.base_ref,
            "head_ref": self.head_ref,
            "files_changed": self.files_changed,
            "transformation_count": len(self.transformations),
            "breaking_count": self.breaking_count,
            "risk_score": round(self.risk_score, 4),
            "risk_band": self.risk_band,
            "transformations": [item.to_dict() for item in self.transformations],
            "blast_radius": [item.to_dict() for item in self.blast_radius],
            "recommendations": list(self.recommendations),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Signature extraction helpers
# ---------------------------------------------------------------------------


@dataclass
class SymbolSignature:
    """Normalized signature extracted from a function or class node."""

    name: str
    qualified_name: str
    kind: str
    parameters: list[str]
    defaults: dict[str, Optional[str]]
    annotations: dict[str, str]
    return_type: str
    decorators: list[str]
    raised: list[str]
    handled: list[str]
    bases: list[str]
    body_shape: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "parameters": self.parameters,
            "defaults": self.defaults,
            "annotations": self.annotations,
            "return_type": self.return_type,
            "decorators": self.decorators,
            "raised": self.raised,
            "handled": self.handled,
            "bases": self.bases,
            "line": self.line,
        }


def _unparse(node: Optional[ast.AST]) -> str:
    """Safely render an AST node back to source text."""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive
        return ""


def _body_shape(node: ast.AST) -> str:
    """Render a normalized body fingerprint used to detect renames.

    The fingerprint ignores the symbol name and its docstring so that a pure
    rename of a function yields an identical shape, while a behavioural rewrite
    does not.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        body = node.body[1:] if _is_docstring(node.body[0:1]) else node.body
        return "||".join(_unparse(statement) for statement in body)
    if isinstance(node, ast.ClassDef):
        body = node.body[1:] if _is_docstring(node.body[0:1]) else node.body
        return "||".join(_unparse(statement) for statement in body)
    return _unparse(node)


def _is_docstring(statements: list[ast.stmt]) -> bool:
    """Return True when ``statements`` begins with a docstring expression."""
    if not statements:
        return False
    first = statements[0]
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )


def _is_privacy_variant(before: str, after: str) -> bool:
    """Return True when two symbol names differ only by underscore prefixes.

    ``helper`` and ``_helper`` denote the same identifier whose privacy
    convention changed, which is materially different from a genuine rename
    such as ``helper`` to ``compute_total``.

    Args:
        before: Symbol name as it appeared in the base revision.
        after: Symbol name as it appears in the head revision.

    Returns:
        True when the names are distinct but share a stripped core.
    """
    return before != after and before.lstrip("_") == after.lstrip("_")


def _raised_exceptions(node: ast.AST) -> list[str]:
    """Collect exception type names raised anywhere inside ``node``."""
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Raise) and child.exc is not None:
            exc = child.exc
            if isinstance(exc, ast.Call):
                exc = exc.func
            if isinstance(exc, ast.Name):
                names.append(exc.id)
            elif isinstance(exc, ast.Attribute):
                names.append(exc.attr)
            else:
                rendered = _unparse(exc)
                if rendered:
                    names.append(rendered)
    return sorted(set(names))


def _handled_exceptions(node: ast.AST) -> list[str]:
    """Collect exception type names caught anywhere inside ``node``."""
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.ExceptHandler) and child.type is not None:
            handler_type = child.type
            if isinstance(handler_type, ast.Tuple):
                for element in handler_type.elts:
                    if isinstance(element, ast.Name):
                        names.append(element.id)
            elif isinstance(handler_type, ast.Name):
                names.append(handler_type.id)
            elif isinstance(handler_type, ast.Attribute):
                names.append(handler_type.attr)
    return sorted(set(names))


class SymbolExtractor(ast.NodeVisitor):
    """Collects normalized signatures from a Python module."""

    def __init__(self) -> None:
        self.signatures: list[SymbolSignature] = []
        self._stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Record a class definition and recurse into its body."""
        qualified = ".".join([*self._stack, node.name])
        self.signatures.append(
            SymbolSignature(
                name=node.name,
                qualified_name=qualified,
                kind="class",
                parameters=[],
                defaults={},
                annotations={},
                return_type="",
                decorators=[_unparse(d) for d in node.decorator_list],
                raised=_raised_exceptions(node),
                handled=_handled_exceptions(node),
                bases=[_unparse(base) for base in node.bases],
                body_shape=_body_shape(node),
                line=node.lineno,
            )
        )
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Record a function definition and recurse into its body."""
        self._record_function(node, "function")
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Record an async function definition and recurse into its body."""
        self._record_function(node, "async_function")
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def _record_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str
    ) -> None:
        """Build a :class:`SymbolSignature` for a function node."""
        arguments = node.args
        positional = list(getattr(arguments, "posonlyargs", [])) + list(arguments.args)
        parameters = [arg.arg for arg in positional]
        if arguments.vararg is not None:
            parameters.append(f"*{arguments.vararg.arg}")
        parameters.extend(arg.arg for arg in arguments.kwonlyargs)
        if arguments.kwarg is not None:
            parameters.append(f"**{arguments.kwarg.arg}")

        defaults: dict[str, Optional[str]] = {}
        default_values = list(arguments.defaults)
        if default_values:
            for arg, value in zip(positional[-len(default_values) :], default_values):
                defaults[arg.arg] = _unparse(value)
        for arg, value in zip(arguments.kwonlyargs, arguments.kw_defaults):
            defaults[arg.arg] = _unparse(value) if value is not None else None

        annotations: dict[str, str] = {}
        for arg in list(positional) + list(arguments.kwonlyargs):
            if arg.annotation is not None:
                annotations[arg.arg] = _unparse(arg.annotation)
        if arguments.vararg is not None and arguments.vararg.annotation is not None:
            annotations[arguments.vararg.arg] = _unparse(arguments.vararg.annotation)
        if arguments.kwarg is not None and arguments.kwarg.annotation is not None:
            annotations[arguments.kwarg.arg] = _unparse(arguments.kwarg.annotation)

        self.signatures.append(
            SymbolSignature(
                name=node.name,
                qualified_name=".".join([*self._stack, node.name]),
                kind=kind,
                parameters=parameters,
                defaults=defaults,
                annotations=annotations,
                return_type=_unparse(node.returns),
                decorators=[_unparse(d) for d in node.decorator_list],
                raised=_raised_exceptions(node),
                handled=_handled_exceptions(node),
                bases=[],
                body_shape=_body_shape(node),
                line=node.lineno,
            )
        )


def extract_symbols(source: str) -> list[SymbolSignature]:
    """Extract every symbol signature from Python ``source``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    extractor = SymbolExtractor()
    extractor.visit(tree)
    return extractor.signatures


# ---------------------------------------------------------------------------
# Git plumbing
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    """Run a git command and return ``(returncode, output)``."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SEC,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"git {args[0] if args else ''} failed: {exc}"
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def changed_files(repo_path: Path, base_ref: str, head_ref: str) -> list[tuple[str, str]]:
    """Return ``(status, path)`` pairs changed between two refs."""
    code, output = _run_git(
        ["diff", "--name-status", f"{base_ref}..{head_ref}"], cwd=repo_path
    )
    if code != 0:
        return []
    entries: list[tuple[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            entries.append((parts[0][:1], parts[-1]))
    return entries


def file_at_ref(repo_path: Path, ref: str, relative_path: str) -> str:
    """Return the content of ``relative_path`` at ``ref`` (empty when absent)."""
    code, output = _run_git(["show", f"{ref}:{relative_path}"], cwd=repo_path)
    if code != 0:
        return ""
    return output


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------


def risk_band(score: float) -> str:
    """Map a risk score onto a human readable band."""
    if score <= 0.0:
        return "none"
    if score < 0.25:
        return "low"
    if score < 0.5:
        return "moderate"
    if score < 0.75:
        return "elevated"
    return "critical"


def compute_risk_score(transformations: list[SemanticTransformation]) -> float:
    """Aggregate transformation weights into a risk score in ``[0, 1]``.

    The score saturates rather than accumulates linearly: ``1 - exp(-sum)``
    keeps a single catastrophic change near ``1.0`` while letting many small
    changes approach it asymptotically.
    """
    total = sum(item.weight for item in transformations if item.breaking)
    if total <= 0:
        return 0.0
    return round(1.0 - math.exp(-total), 4)


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class SemanticGitDeltaAnalyzer:
    """Converts git revision deltas into structured semantic transformations."""

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).resolve()
        self._dependency_graph: Any = None

    # -- dependency graph --------------------------------------------------

    def _load_dependency_graph(self) -> Optional[Any]:
        """Lazily build the repository-wide symbol dependency graph."""
        if self._dependency_graph is not None:
            return self._dependency_graph
        try:
            from alpha.coding.structural_intelligence.symbol_dependency_graph import (
                SymbolDependencyGraph,
            )

            graph = SymbolDependencyGraph()
            graph.parse_directory(self.repo_path, max_files=MAX_FILES_PER_ANALYSIS)
            self._dependency_graph = graph
            return graph
        except Exception:  # pragma: no cover - optional dependency
            return None

    def _callers_of(self, symbol_name: str) -> list[BlastRadiusEntry]:
        """Locate call sites of ``symbol_name`` via the dependency graph."""
        graph = self._load_dependency_graph()
        entries: list[BlastRadiusEntry] = []
        if graph is not None:
            with contextlib.suppress(Exception):
                for node in graph.find_symbol_by_name(symbol_name):
                    for caller_id in graph.get_callers(node.id):
                        caller = graph.nodes.get(caller_id)
                        if caller is None:
                            continue
                        entries.append(
                            BlastRadiusEntry(
                                file_path=caller.file_path,
                                symbol=caller.name,
                                relation="calls",
                                line=caller.start_line,
                            )
                        )
        if entries:
            return entries
        return self._textual_callers(symbol_name)

    def _textual_callers(self, symbol_name: str) -> list[BlastRadiusEntry]:
        """Fallback caller scan used when the dependency graph is unavailable."""
        pattern = re.compile(rf"\b{re.escape(symbol_name)}\s*\(")
        entries: list[BlastRadiusEntry] = []
        for file_path in self._iter_python_files():
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    entries.append(
                        BlastRadiusEntry(
                            file_path=_display_path(file_path, self.repo_path),
                            symbol=symbol_name,
                            relation="reference",
                            line=line_no,
                        )
                    )
            if len(entries) >= 50:
                break
        return entries

    def _iter_python_files(self) -> list[Path]:
        """Yield Python files beneath the repository root."""
        skip = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".mypy_cache"}
        files: list[Path] = []
        for root, dirs, names in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if d not in skip]
            for name in names:
                if name.endswith(".py"):
                    files.append(Path(root) / name)
            if len(files) >= MAX_FILES_PER_ANALYSIS:
                break
        return files

    # -- comparison --------------------------------------------------------

    def compare_sources(
        self, file_path: str, before_source: str, after_source: str
    ) -> list[SemanticTransformation]:
        """Diff two revisions of one Python file into transformations.

        Args:
            file_path: Repository-relative path used for reporting.
            before_source: Source text of the file at the base revision.
            after_source: Source text of the file at the head revision.

        Returns:
            The list of detected :class:`SemanticTransformation` records.
        """
        before = {item.qualified_name: item for item in extract_symbols(before_source)}
        after = {item.qualified_name: item for item in extract_symbols(after_source)}

        transformations: list[SemanticTransformation] = []

        before_by_shape: dict[str, SymbolSignature] = {}
        for signature in before.values():
            before_by_shape.setdefault(f"{signature.kind}:{signature.body_shape}", signature)

        added = [name for name in after if name not in before]
        removed = [name for name in before if name not in after]

        removal_by_shape = {
            f"{before[name].kind}:{before[name].body_shape}": name for name in removed
        }
        # Qualified names already explained by a rename must not also be
        # reported as removals.
        renamed_sources: set[str] = set()

        for name in added:
            candidate = after[name]
            shape_key = f"{candidate.kind}:{candidate.body_shape}"
            matched_removal = removal_by_shape.get(shape_key)
            if matched_removal and _is_privacy_variant(
                before[matched_removal].name, candidate.name
            ):
                # A rename that only toggles the leading-underscore privacy
                # marker is a visibility change, not a rename: the identifier
                # keeps its meaning and callers still resolve the same symbol
                # name modulo convention.
                previous = before[matched_removal]
                transformations.append(
                    SemanticTransformation(
                        kind=TransformationKind.VISIBILITY_CHANGED,
                        file_path=file_path,
                        symbol=candidate.qualified_name,
                        detail=(
                            f"visibility changed from "
                            f"{'private' if previous.name.startswith('_') else 'public'} to "
                            f"{'private' if candidate.name.startswith('_') else 'public'}"
                        ),
                        before=previous.name,
                        after=candidate.name,
                        breaking=True,
                        weight=BREAKING_WEIGHTS[TransformationKind.VISIBILITY_CHANGED],
                        line=candidate.line,
                    )
                )
                removal_by_shape.pop(shape_key, None)
                renamed_sources.add(previous.qualified_name)
                continue
            if matched_removal and before[matched_removal].name != candidate.name:
                transformations.append(
                    SemanticTransformation(
                        kind=TransformationKind.SYMBOL_RENAMED,
                        file_path=file_path,
                        symbol=candidate.qualified_name,
                        detail=(
                            f"{before[matched_removal].qualified_name} renamed to "
                            f"{candidate.qualified_name} (identical body shape)"
                        ),
                        before=before[matched_removal].name,
                        after=candidate.name,
                        breaking=True,
                        weight=BREAKING_WEIGHTS[TransformationKind.SYMBOL_RENAMED],
                        line=candidate.line,
                    )
                )
                removal_by_shape.pop(shape_key, None)
                renamed_sources.add(before[matched_removal].qualified_name)
                continue
            transformations.append(
                SemanticTransformation(
                    kind=TransformationKind.SYMBOL_ADDED,
                    file_path=file_path,
                    symbol=candidate.qualified_name,
                    detail=f"{candidate.kind} {candidate.name} added",
                    after=candidate.name,
                    breaking=False,
                    weight=BREAKING_WEIGHTS[TransformationKind.SYMBOL_ADDED],
                    line=candidate.line,
                )
            )

        for name in removed:
            if before[name].qualified_name in renamed_sources:
                continue
            transformations.append(
                SemanticTransformation(
                    kind=TransformationKind.SYMBOL_REMOVED,
                    file_path=file_path,
                    symbol=before[name].qualified_name,
                    detail=f"{before[name].kind} {before[name].name} removed",
                    before=before[name].name,
                    breaking=True,
                    weight=BREAKING_WEIGHTS[TransformationKind.SYMBOL_REMOVED],
                    line=before[name].line,
                )
            )

        for name in sorted(set(before) & set(after)):
            transformations.extend(
                self._compare_signature(file_path, before[name], after[name])
            )

        return transformations

    def _compare_signature(
        self, file_path: str, before: SymbolSignature, after: SymbolSignature
    ) -> list[SemanticTransformation]:
        """Compare two versions of the same symbol signature."""
        results: list[SemanticTransformation] = []
        line = after.line

        if before.parameters != after.parameters:
            removed_params = [p for p in before.parameters if p not in after.parameters]
            added_params = [p for p in after.parameters if p not in before.parameters]

            if (
                len(removed_params) == 1
                and len(added_params) == 1
                and before.parameters.index(removed_params[0])
                == after.parameters.index(added_params[0])
            ):
                results.append(
                    SemanticTransformation(
                        kind=TransformationKind.PARAMETER_RENAMED,
                        file_path=file_path,
                        symbol=after.qualified_name,
                        detail=(
                            f"parameter '{removed_params[0]}' renamed to "
                            f"'{added_params[0]}'"
                        ),
                        before=removed_params[0],
                        after=added_params[0],
                        breaking=True,
                        weight=BREAKING_WEIGHTS[TransformationKind.PARAMETER_RENAMED],
                        line=line,
                    )
                )
                removed_params = []
                added_params = []

            for param in removed_params:
                results.append(
                    SemanticTransformation(
                        kind=TransformationKind.PARAMETER_REMOVED,
                        file_path=file_path,
                        symbol=after.qualified_name,
                        detail=f"parameter '{param}' removed",
                        before=param,
                        breaking=True,
                        weight=BREAKING_WEIGHTS[TransformationKind.PARAMETER_REMOVED],
                        line=line,
                    )
                )

            for param in added_params:
                has_default = after.defaults.get(param) is not None
                results.append(
                    SemanticTransformation(
                        kind=TransformationKind.PARAMETER_ADDED,
                        file_path=file_path,
                        symbol=after.qualified_name,
                        detail=(
                            f"parameter '{param}' added"
                            + (" with a default value" if has_default else " as required")
                        ),
                        after=param,
                        breaking=not has_default,
                        weight=(
                            BREAKING_WEIGHTS[TransformationKind.PARAMETER_ADDED]
                            if not has_default
                            else 0.15
                        ),
                        line=line,
                    )
                )

        if before.annotations != after.annotations:
            for param in sorted(set(before.annotations) | set(after.annotations)):
                old_value = before.annotations.get(param, "")
                new_value = after.annotations.get(param, "")
                if old_value != new_value:
                    results.append(
                        SemanticTransformation(
                            kind=TransformationKind.ANNOTATION_CHANGED,
                            file_path=file_path,
                            symbol=after.qualified_name,
                            detail=(
                                f"annotation of '{param}' changed from "
                                f"'{old_value or 'untyped'}' to '{new_value or 'untyped'}'"
                            ),
                            before=old_value,
                            after=new_value,
                            breaking=True,
                            weight=BREAKING_WEIGHTS[TransformationKind.ANNOTATION_CHANGED],
                            line=line,
                        )
                    )

        if before.return_type != after.return_type:
            results.append(
                SemanticTransformation(
                    kind=TransformationKind.RETURN_TYPE_CHANGED,
                    file_path=file_path,
                    symbol=after.qualified_name,
                    detail=(
                        f"return type changed from '{before.return_type or 'untyped'}' "
                        f"to '{after.return_type or 'untyped'}'"
                    ),
                    before=before.return_type,
                    after=after.return_type,
                    breaking=True,
                    weight=BREAKING_WEIGHTS[TransformationKind.RETURN_TYPE_CHANGED],
                    line=line,
                )
            )

        if before.decorators != after.decorators:
            results.append(
                SemanticTransformation(
                    kind=TransformationKind.DECORATOR_CHANGED,
                    file_path=file_path,
                    symbol=after.qualified_name,
                    detail=(
                        f"decorators changed from {before.decorators or '[]'} to "
                        f"{after.decorators or '[]'}"
                    ),
                    before=", ".join(before.decorators),
                    after=", ".join(after.decorators),
                    breaking=True,
                    weight=BREAKING_WEIGHTS[TransformationKind.DECORATOR_CHANGED],
                    line=line,
                )
            )

        if before.bases != after.bases:
            results.append(
                SemanticTransformation(
                    kind=TransformationKind.BASE_CLASS_CHANGED,
                    file_path=file_path,
                    symbol=after.qualified_name,
                    detail=(
                        f"base classes changed from {before.bases or '[]'} to "
                        f"{after.bases or '[]'}"
                    ),
                    before=", ".join(before.bases),
                    after=", ".join(after.bases),
                    breaking=True,
                    weight=BREAKING_WEIGHTS[TransformationKind.BASE_CLASS_CHANGED],
                    line=line,
                )
            )

        if before.raised != after.raised:
            results.append(
                SemanticTransformation(
                    kind=TransformationKind.EXCEPTION_RAISED_CHANGED,
                    file_path=file_path,
                    symbol=after.qualified_name,
                    detail=(
                        f"raised exceptions changed from {before.raised or '[]'} to "
                        f"{after.raised or '[]'}"
                    ),
                    before=", ".join(before.raised),
                    after=", ".join(after.raised),
                    breaking=True,
                    weight=BREAKING_WEIGHTS[TransformationKind.EXCEPTION_RAISED_CHANGED],
                    line=line,
                )
            )

        if before.handled != after.handled:
            results.append(
                SemanticTransformation(
                    kind=TransformationKind.EXCEPTION_HANDLED_CHANGED,
                    file_path=file_path,
                    symbol=after.qualified_name,
                    detail=(
                        f"handled exceptions changed from {before.handled or '[]'} to "
                        f"{after.handled or '[]'}"
                    ),
                    before=", ".join(before.handled),
                    after=", ".join(after.handled),
                    breaking=False,
                    weight=BREAKING_WEIGHTS[TransformationKind.EXCEPTION_HANDLED_CHANGED],
                    line=line,
                )
            )

        before_private = before.name.startswith("_")
        after_private = after.name.startswith("_")
        if before_private != after_private:
            results.append(
                SemanticTransformation(
                    kind=TransformationKind.VISIBILITY_CHANGED,
                    file_path=file_path,
                    symbol=after.qualified_name,
                    detail=(
                        f"visibility changed from "
                        f"{'private' if before_private else 'public'} to "
                        f"{'private' if after_private else 'public'}"
                    ),
                    before=before.name,
                    after=after.name,
                    breaking=True,
                    weight=BREAKING_WEIGHTS[TransformationKind.VISIBILITY_CHANGED],
                    line=line,
                )
            )

        return results

    # -- orchestration -----------------------------------------------------

    def analyze(
        self,
        base_ref: str = "HEAD~1",
        head_ref: str = "HEAD",
        max_blast_radius: int = 40,
    ) -> SemanticDeltaReport:
        """Analyze the delta between two git revisions.

        Args:
            base_ref: Base revision-ish (commit, tag or branch).
            head_ref: Head revision-ish (commit, tag or branch).
            max_blast_radius: Maximum number of dependent call sites reported.

        Returns:
            A :class:`SemanticDeltaReport`. ``error`` is populated when the
            repository or revision range cannot be resolved.
        """
        report = SemanticDeltaReport(base_ref=base_ref, head_ref=head_ref)
        if not self.repo_path.exists():
            report.error = f"repository path '{self.repo_path}' does not exist"
            return report

        entries = changed_files(self.repo_path, base_ref, head_ref)
        if not entries:
            code, output = _run_git(["rev-parse", "--verify", base_ref], cwd=self.repo_path)
            if code != 0:
                report.error = (
                    f"no changes found between {base_ref}..{head_ref}; "
                    f"git rev-parse failed: {output.strip()[:200]}"
                )
            return report

        report.files_changed = len(entries)
        transformations: list[SemanticTransformation] = []
        for status, relative_path in entries[:MAX_FILES_PER_ANALYSIS]:
            if not relative_path.endswith(".py"):
                continue
            before_source = "" if status in {"A"} else file_at_ref(self.repo_path, base_ref, relative_path)
            after_source = "" if status in {"D"} else file_at_ref(self.repo_path, head_ref, relative_path)
            transformations.extend(
                self.compare_sources(relative_path, before_source, after_source)
            )

        report.transformations = transformations
        report.breaking_count = sum(1 for item in transformations if item.breaking)
        report.risk_score = compute_risk_score(transformations)
        report.risk_band = risk_band(report.risk_score)
        report.blast_radius = self._compute_blast_radius(transformations, max_blast_radius)
        report.recommendations = self._recommendations(transformations, report.blast_radius)
        return report

    def analyze_sources(
        self,
        before_sources: dict[str, str],
        after_sources: dict[str, str],
        base_ref: str = "before",
        head_ref: str = "after",
        max_blast_radius: int = 40,
    ) -> SemanticDeltaReport:
        """Analyze an in-memory file overlay pair instead of a git range.

        Args:
            before_sources: Mapping of path to source at the base revision.
            after_sources: Mapping of path to source at the head revision.
            base_ref: Label used for the base revision in the report.
            head_ref: Label used for the head revision in the report.
            max_blast_radius: Maximum number of dependent call sites reported.

        Returns:
            A :class:`SemanticDeltaReport`.
        """
        report = SemanticDeltaReport(base_ref=base_ref, head_ref=head_ref)
        transformations: list[SemanticTransformation] = []
        for relative_path in sorted(set(before_sources) | set(after_sources)):
            if not relative_path.endswith(".py"):
                continue
            transformations.extend(
                self.compare_sources(
                    relative_path,
                    before_sources.get(relative_path, ""),
                    after_sources.get(relative_path, ""),
                )
            )
        report.files_changed = len(set(before_sources) | set(after_sources))
        report.transformations = transformations
        report.breaking_count = sum(1 for item in transformations if item.breaking)
        report.risk_score = compute_risk_score(transformations)
        report.risk_band = risk_band(report.risk_score)
        report.blast_radius = self._compute_blast_radius(transformations, max_blast_radius)
        report.recommendations = self._recommendations(transformations, report.blast_radius)
        return report

    # -- blast radius ------------------------------------------------------

    def _compute_blast_radius(
        self, transformations: list[SemanticTransformation], max_entries: int
    ) -> list[BlastRadiusEntry]:
        """Resolve dependent call sites for every breaking transformation."""
        entries: list[BlastRadiusEntry] = []
        seen: set[tuple[str, str, int]] = set()
        for transformation in transformations:
            if not transformation.breaking:
                continue
            symbol_name = transformation.symbol.split(".")[-1]
            for entry in self._callers_of(symbol_name):
                key = (entry.file_path, entry.symbol, entry.line)
                if key in seen:
                    continue
                seen.add(key)
                entries.append(entry)
                if len(entries) >= max_entries:
                    return entries
        return entries

    @staticmethod
    def _recommendations(
        transformations: list[SemanticTransformation],
        blast_radius: list[BlastRadiusEntry],
    ) -> list[str]:
        """Generate migration guidance for breaking transformations."""
        recommendations: list[str] = []
        for transformation in transformations:
            if not transformation.breaking:
                continue
            if transformation.kind is TransformationKind.SYMBOL_RENAMED:
                recommendations.append(
                    f"Rename call sites of '{transformation.before}' to "
                    f"'{transformation.after}' in dependents of "
                    f"{transformation.file_path}."
                )
            elif transformation.kind is TransformationKind.SYMBOL_REMOVED:
                recommendations.append(
                    f"Remove or replace usages of removed symbol "
                    f"'{transformation.symbol}' before merging."
                )
            elif transformation.kind is TransformationKind.PARAMETER_REMOVED:
                recommendations.append(
                    f"Drop argument '{transformation.before}' from calls to "
                    f"'{transformation.symbol}'."
                )
            elif transformation.kind is TransformationKind.PARAMETER_ADDED:
                recommendations.append(
                    f"Supply '{transformation.after}' when calling "
                    f"'{transformation.symbol}', or give it a default value."
                )
            elif transformation.kind is TransformationKind.PARAMETER_RENAMED:
                recommendations.append(
                    f"Update keyword argument '{transformation.before}' to "
                    f"'{transformation.after}' at call sites of "
                    f"'{transformation.symbol}'."
                )
            elif transformation.kind is TransformationKind.ANNOTATION_CHANGED:
                recommendations.append(
                    f"Re-check typed call sites of '{transformation.symbol}' "
                    f"after the annotation change ({transformation.detail})."
                )
            elif transformation.kind is TransformationKind.RETURN_TYPE_CHANGED:
                recommendations.append(
                    f"Audit consumers of '{transformation.symbol}' for the new "
                    f"return type '{transformation.after}'."
                )
            elif transformation.kind is TransformationKind.EXCEPTION_RAISED_CHANGED:
                recommendations.append(
                    f"Update exception handling around '{transformation.symbol}': "
                    f"{transformation.detail}."
                )
            elif transformation.kind is TransformationKind.DECORATOR_CHANGED:
                recommendations.append(
                    f"Verify decorator side effects for '{transformation.symbol}' "
                    f"({transformation.detail})."
                )
            elif transformation.kind is TransformationKind.BASE_CLASS_CHANGED:
                recommendations.append(
                    f"Re-validate subclasses of '{transformation.symbol}' after "
                    f"the base class change."
                )
            elif transformation.kind is TransformationKind.VISIBILITY_CHANGED:
                recommendations.append(
                    f"'{transformation.symbol}' changed visibility; update imports "
                    f"and public API documentation."
                )
            elif transformation.kind is TransformationKind.SIGNATURE_ORDER_CHANGED:
                recommendations.append(
                    f"Positional call sites of '{transformation.symbol}' must be "
                    f"updated: parameter order changed."
                )

        if blast_radius:
            affected = sorted({entry.file_path for entry in blast_radius})
            recommendations.append(
                "Affected dependent modules: " + ", ".join(affected[:20])
            )
        return recommendations


def _display_path(file_path: Path, root: Path) -> str:
    """Render ``file_path`` relative to ``root`` when possible."""
    try:
        return str(file_path.resolve().relative_to(root)).replace("\\", "/")
    except (ValueError, OSError):
        return str(file_path)
