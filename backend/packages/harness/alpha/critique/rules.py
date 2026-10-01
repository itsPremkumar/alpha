"""Deterministic critique rules (AST-based).

Each rule inspects a parsed module and returns findings. Rules are pure and
side-effect free so the critic is reproducible and needs no dependencies beyond
the standard library.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """One critique finding (rule, severity, location, message, remediation)."""

    rule: str
    severity: str  # "high" | "medium" | "low"
    path: str
    line: int
    message: str
    remediation: str


def _f(rule: str, severity: str, path: str, line: int, message: str, remediation: str) -> Finding:
    return Finding(rule, severity, path, line, message, remediation)


def rule_bare_except(path: str, tree: ast.AST, src: str) -> list[Finding]:
    """Flag broad except clauses that silently swallow errors."""
    out: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            name = "<bare>" if node.type is None else (node.type.id if isinstance(node.type, ast.Name) else None)
            if name in {"<bare>", "Exception", "BaseException"}:
                swallowed = all(isinstance(s, (ast.Pass, ast.Continue)) for s in node.body)
                if swallowed:
                    out.append(_f("bare-except", "medium", path, node.lineno,
                                  f"broad 'except {name}' swallows the error",
                                  "Catch a specific exception; log and re-raise unexpected ones."))
    return out


def rule_open_encoding(path: str, tree: ast.AST, src: str) -> list[Finding]:
    """Flag text-mode open() without an explicit encoding."""
    out: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            keywords = {k.arg for k in node.keywords}
            mode = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for k in node.keywords:
                if k.arg == "mode" and isinstance(k.value, ast.Constant):
                    mode = k.value.value
            if mode and "b" in str(mode):
                continue
            if "encoding" not in keywords:
                out.append(_f("open-encoding", "medium", path, node.lineno,
                              "open() without an explicit encoding",
                              "Pass encoding='utf-8' so behaviour is locale-independent."))
    return out


def rule_print_in_library(path: str, tree: ast.AST, src: str) -> list[Finding]:
    """Flag print() calls in library modules (should use logging)."""
    out: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            out.append(_f("print-in-library", "low", path, node.lineno,
                          "print() in library code", "Use the logging module instead."))
    return out


def rule_todo_marker(path: str, tree: ast.AST, src: str) -> list[Finding]:
    """Flag TODO/FIXME/XXX/HACK markers left in comments (not code strings)."""
    out: list[Finding] = []
    for i, line in enumerate(src.splitlines(), 1):
        hash_idx = line.find("#")
        if hash_idx == -1:
            continue
        comment = line[hash_idx:].upper()
        for marker in ("TODO", "FIXME", "XXX", "HACK"):
            if marker in comment:
                out.append(_f("todo-marker", "low", path, i, f"{marker} marker left in code", "Resolve or track the item."))
                break
    return out


def rule_mutable_default(path: str, tree: ast.AST, src: str) -> list[Finding]:
    """Flag mutable default arguments on functions."""
    out: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defaults = list(node.args.defaults) + [k for k in node.args.kw_defaults if k is not None]
            for d in defaults:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    out.append(_f("mutable-default", "high", path, node.lineno,
                                  f"{node.name}() has a mutable default argument",
                                  "Use None and assign inside, or dataclasses.field(default_factory=...)."))
    return out


def rule_from_dict_unknown_check(path: str, tree: ast.AST, src: str) -> list[Finding]:
    """Flag from_dict classmethods that do not reject unknown keys."""
    out: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "from_dict":
            uses_unknown = any(isinstance(n, ast.Name) and n.id == "unknown" for n in ast.walk(node))
            if not uses_unknown:
                out.append(_f("from-dict-no-unknown-check", "medium", path, node.lineno,
                              "from_dict does not reject unknown keys",
                              "Compute unknown = sorted(set(data) - known) and raise on a non-empty set."))
    return out


def rule_save_without_fsync(path: str, tree: ast.AST, src: str) -> list[Finding]:
    """Flag durable _save helpers that never fsync before the atomic replace."""
    out: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_save":
            has_fsync = any(isinstance(n, ast.Attribute) and n.attr == "fsync" for n in ast.walk(node))
            if not has_fsync:
                out.append(_f("save-no-fsync", "medium", path, node.lineno,
                              "durable write has no fsync before replace",
                              "flush + os.fsync(fd) before os.replace so a crash cannot lose the write."))
    return out


def rule_missing_docstring(path: str, tree: ast.AST, src: str) -> list[Finding]:
    """Flag a module or public class/function without a docstring."""
    out: list[Finding] = []
    if ast.get_docstring(tree) is None:
        out.append(_f("module-docstring", "low", path, 1, "module has no docstring", "Add a one-line module docstring."))
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            if ast.get_docstring(node) is None:
                out.append(_f("docstring", "low", path, node.lineno, f"{node.name} has no docstring", "Add a docstring."))
    return out


RULES = (
    rule_bare_except,
    rule_open_encoding,
    rule_print_in_library,
    rule_todo_marker,
    rule_mutable_default,
    rule_from_dict_unknown_check,
    rule_save_without_fsync,
    rule_missing_docstring,
)

__all__ = ["Finding", "RULES"]
