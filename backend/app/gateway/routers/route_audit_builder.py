"""AST inventory of Gateway HTTP routes: one JSON document, no Gateway import.

Walks every router module under ``app/gateway/routers`` and reports each
decorator-declared route (``@router.get(...)`` on a module function or a
class method, and any other ``<object>.<method>(...)`` HTTP decorator) as
``{file, count, routes: [{name, method, sig, decos}]}``. Parsing with the
``ast`` module keeps the answer read-only: nothing imports the Gateway, so
the audit cannot bind a port, read a secret or start a lifespan.

Improvements over the original scratch version, which ran at import time
and only worked from one directory:

- the routers directory derives from ``__file__`` (the old ``"backend/app/
  gateway/routers"`` relative path broke whenever the CWD was not the repo
  root), an optional argv[1] may override it;
- one AST walk instead of two: the old first walk (class bodies) plus
  second walk (any ``router.X(...)`` call) counted class-method routes
  twice and labelled module-level routes ``"<module>"`` instead of the
  decorated function's real name; the no-op ``Assign``/``AnnAssign``
  branches are gone;
- ``main()`` plus an ``if __name__ == "__main__"`` guard, so importing this
  module never prints or scans anything.

Runnable standalone::

    python -m app.gateway.routers.route_audit_builder   # from backend/, PYTHONPATH=.
    python route_audit_builder.py                       # from anywhere
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})
_DEFAULT_ROUTERS_DIR = Path(__file__).resolve().parent


def _http_method(decorator: ast.expr) -> str | None:
    """The HTTP method if ``decorator`` is a ``<obj>.<method>(...)`` HTTP call."""
    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr in _HTTP_METHODS:
        return decorator.func.attr
    return None


def audit_file(path: Path) -> dict[str, Any] | None:
    """One file's route inventory, or ``None`` when it declares no routes."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    routes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        decos = [ast.unparse(d) for d in node.decorator_list]
        for decorator in node.decorator_list:
            method = _http_method(decorator)
            if method is None:
                continue
            sig = ast.unparse(decorator)
            key = (node.name, method, sig)
            if key in seen:
                continue
            seen.add(key)
            routes.append({"name": node.name, "method": method, "sig": sig, "decos": decos})

    if not routes:
        return None
    return {"file": path.name, "count": len(routes), "routes": routes}


def build_route_audit(routers_dir: Path = _DEFAULT_ROUTERS_DIR) -> list[dict[str, Any]]:
    """Inventory every router file that declares at least one route."""
    report: list[dict[str, Any]] = []
    for path in sorted(routers_dir.iterdir()):
        if path.suffix != ".py" or path.name == "__init__.py":
            continue
        entry = audit_file(path)
        if entry is not None:
            report.append(entry)
    return report


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:]) if argv is None else list(argv)
    routers_dir = Path(args[0]) if args else _DEFAULT_ROUTERS_DIR
    if not routers_dir.is_dir():
        print(f"not a directory: {routers_dir}", file=sys.stderr)
        return 2
    print(json.dumps(build_route_audit(routers_dir), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
