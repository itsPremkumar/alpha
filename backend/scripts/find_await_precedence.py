"""AST scan: find `await f(...).attr` -- attribute access on a coroutine.

`await x.y(...).z` parses as `await (x.y(...).z)`, because attribute access binds
tighter than `await`. That is only correct when `.z` is itself a coroutine
function being called. When `.z` is a plain attribute -- `.to_dict`,
`.model_dump`, `.json`, `.value` -- the await applies to a `coroutine` object and
the handler raises `AttributeError` on *every* call.

`GET /api/intelligence/inventory` shipped exactly that: the whole
self-knowledge HTTP surface answered 500 while its unit tests stayed green,
because they exercise `build_self_inventory` directly and never the route.

This scanner is the class-level guard. Run standalone::

    python scripts/find_await_precedence.py [roots...]
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

#: Attribute names that are genuinely awaited properties on some objects.
#: Everything else that is not a call is reported.
_AWAITABLE_ATTRS = frozenset({"__await__"})


class AwaitAttributeVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.findings: list[tuple[int, str]] = []

    def visit_Await(self, node: ast.Await) -> None:
        value = node.value
        # `await f(...).attr` -> value is Attribute whose value is a Call.
        if isinstance(value, ast.Attribute) and not isinstance(value.ctx, ast.Store):
            if value.attr in _AWAITABLE_ATTRS:
                return
            if isinstance(value.value, ast.Call):
                try:
                    rendered = ast.unparse(node)
                except Exception:  # pragma: no cover - unparse is total in 3.12
                    return
                self.findings.append((node.lineno, rendered))
        self.generic_visit(node)


def scan_file(path: Path) -> list[tuple[int, str]]:
    # `utf-8-sig`, not `utf-8`. A BOM-prefixed file makes `ast.parse` raise
    # SyntaxError on the leading U+FEFF, and a bare `except: return []` would
    # then report the file as clean. That is the same blindness as any other
    # "I could not look" that reads as "nothing there" -- and it is how a
    # PowerShell-authored file slipped past this scanner's own negative
    # control until the control was written.
    try:
        source = path.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    visitor = AwaitAttributeVisitor(path)
    visitor.visit(tree)
    return visitor.findings


def scan(roots: list[Path]) -> list[tuple[Path, int, str]]:
    out: list[tuple[Path, int, str]] = []
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for f in files:
            for lineno, rendered in scan_file(f):
                out.append((f, lineno, rendered))
    return out


def _display(path: Path, repo: Path) -> str:
    """Repo-relative when possible; absolute for a root outside the repo.

    The scanner takes arbitrary roots so its negative control can point at a
    temp file, and `relative_to` raises for anything outside the repository.
    """
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def main(argv: list[str]) -> int:
    repo = Path(__file__).resolve().parents[2]
    roots = [Path(a) if Path(a).is_absolute() else repo / a for a in (argv[1:] or ["app", "packages"])]
    findings = scan(roots)
    for path, lineno, rendered in findings:
        print(f"{_display(path, repo)}:{lineno}: {rendered}")
    print(f"\n{len(findings)} suspicious `await f(...).attr` site(s)", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
