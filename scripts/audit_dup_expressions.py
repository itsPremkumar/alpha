#!/usr/bin/env python3
"""Duplicate-expression gate: the mangled-rename bug family, found by AST.

Renames in this repo once collapsed ``X or Y`` into ``X or X`` (ops/version),
``a || b`` into ``a || a`` (electron, next.config) and left duplicate object
keys behind. Those were JavaScript; this is the Python half of the same gate.
Pure syntax analysis — no imports — so it is safe to run while the test suite
occupies the venv.

Flags (file:line, in non-test source and tests alike):
  DUP-BOOL    ``X or X`` / ``X and X`` (same operand twice, incl. chained)
  DUP-CMP     ``x == 'a' or x == 'a'`` — identical comparison repeated in one BoolOp
  DUP-TAUT    complementary operator pair on identical operands in one chain:
              ``A != B or A == B`` (always True) / ``A < B and A >= B`` (always
              False) — assertions and guards that can never fail (the
              test_skill_request_scoped_secrets tautology family)
  DUP-DICTKEY repeated literal key in a dict/display literal (last-wins clobber)
Exit 1 on any hit. Test fixtures that intentionally build duplicate-key dicts
are reported too — silence must be earned with a comment, not assumed.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "node_modules", ".venv", ".next", "__pycache__", "logs", "dist", "build", "coverage", ".pytest_cache", ".mypy_cache", "sandbox"}


def iter_py():
    for p in ROOT.rglob("*.py"):
        if any(s in p.parts for s in SKIP_DIRS):
            continue
        # Copy-in templates carry ``<placeholder>`` identifiers that are not
        # valid Python by design (e.g. anchor.template.py) — exclude by path,
        # never by suppressing parse errors from real source.
        if p.name.endswith(".template.py") or "templates" in p.parts:
            continue
        if p.stat().st_size > 2_000_000:
            continue
        yield p


def key(node: ast.AST) -> str | None:
    """Stable structural fingerprint for comparing operand nodes."""
    try:
        return ast.dump(node, include_attributes=False)
    except Exception:
        return None


class DupFinder(ast.NodeVisitor):
    def __init__(self, path: str):
        self.path = path
        self.hits: list[str] = []

    def _hit(self, line: int, kind: str, detail: str) -> None:
        self.hits.append(f"{self.path}:{line}  {kind}  {detail}")

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        seen: dict[str, int] = {}
        for v in node.values:
            k = key(v)
            if k is None:
                continue
            seen[k] = seen.get(k, 0) + 1
        for k, n in seen.items():
            if n > 1:
                op = "or" if isinstance(node.op, ast.Or) else "and" if isinstance(node.op, ast.And) else "?"
                label = k.split(" ", 1)[0]
                self._hit(node.lineno, "DUP-BOOL", f"operand x{n} in `{op}` chain: {label} ...")
        # identical comparisons (same operands AND same operator): (a == b) or (a == b)
        # fingerprint includes the operator so complementary pairs fall to DUP-TAUT
        cmp_keys = [f"{key(c.left)}|{','.join(key(x) for x in c.comparators)}|{','.join(type(o).__name__ for o in c.ops)}" for v in node.values if isinstance(v, ast.Compare) for c in [v]]
        seen_c: dict[str, int] = {}
        for ck in cmp_keys:
            seen_c[ck] = seen_c.get(ck, 0) + 1
        for ck, n in seen_c.items():
            if n > 1:
                self._hit(node.lineno, "DUP-CMP", f"identical compare x{n}: {ck.split('|', 1)[0]}")
        # complementary pairs on identical operands: tautology / contradiction
        pairs = {frozenset({"Eq", "NotEq"}), frozenset({"Lt", "GtE"}), frozenset({"LtE", "Gt"}), frozenset({"Is", "IsNot"}), frozenset({"In", "NotIn"})}
        grouped: dict[str, list[ast.Compare]] = {}
        for v in node.values:
            if isinstance(v, ast.Compare) and len(v.ops) == 1:
                fp = f"{key(v.left)}|{','.join(key(x) for x in v.comparators)}"
                grouped.setdefault(fp, []).append(v)
        for fp, cmps in grouped.items():
            ops = {type(c.ops[0]).__name__ for c in cmps}
            for pair in pairs:
                if pair <= ops:
                    chain = "or" if isinstance(node.op, ast.Or) else "and"
                    verdict = "always True" if isinstance(node.op, ast.Or) else "always False"
                    self._hit(node.lineno, "DUP-TAUT", f"`{chain}` chain of {sorted(pair)} on same operands -> {verdict}: {fp.split('|', 1)[0]}")
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        seen: dict[str, int] = {}
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                seen[k.value] = seen.get(k.value, 0) + 1
        for name, n in seen.items():
            if n > 1:
                self._hit(node.lineno, "DUP-DICTKEY", f"key '{name}' appears x{n}")
        self.generic_visit(node)


def main() -> int:
    total = 0
    for f in sorted(iter_py()):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=str(f))
        except SyntaxError as exc:
            print(f"{f.relative_to(ROOT).as_posix()}:{exc.lineno}  PARSE-ERROR  {exc.msg}")
            total += 1
            continue
        finder = DupFinder(f.relative_to(ROOT).as_posix())
        finder.visit(tree)
        for h in finder.hits:
            print(h)
        total += len(finder.hits)
    print("-" * 72)
    print(f"duplicate-expression hits: {total}")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
