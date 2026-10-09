"""Canonical thread ID contract: every HTTP route and embedded-client entry
point that takes a ``thread_id`` must enforce ``ThreadId`` validation.

Two complementary guards:

1. A static sweep (AST over ``app/gateway/routers/*.py``) asserting every
   route handler that declares a ``thread_id`` parameter either annotates it
   ``ThreadId`` (``ThreadId | None`` for an optional one) or, when it is an
   optional *filter* that must default to ``None``, calls
   ``validate_thread_id(thread_id)`` in its own body. This is what prevents
   new routes from silently landing with an unvalidated raw ``str`` again
   (the suggestions/thread_runs/threads gaps, then the three filter handlers
   that answered an empty page for a malformed id).
2. A runtime sweep hitting every ``{thread_id}`` route with a non-canonical
   ID and asserting a 422 whose error location names ``thread_id``.

Deliberate exceptions (RFC #4588):
- ``DELETE /api/threads/{thread_id}`` keeps ``thread_id: str`` as the
  legacy-cleanup escape hatch.
- The browser websocket stream validates on upgrade; covered separately.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

ROUTERS_DIR = Path(__file__).resolve().parent.parent / "app" / "gateway" / "routers"

# (handler name) route handlers deliberately allowed to keep ``thread_id: str``.
STATIC_WHITELIST = {"delete_thread_data"}

#: Spellings whose own type enforces the canonical contract.
CANONICAL_ANNOTATIONS = frozenset({"ThreadId", "ThreadId | None"})

#: The optional **filter** spelling. It cannot be a bare ``ThreadId``: omitting
#: a filter must resolve to ``None`` rather than fail validation, so the
#: guarantee comes from the handler calling ``validate_thread_id(thread_id)``
#: itself. That is a real check rather than a loophole — declaring ``str |
#: None`` *without* validating still fails this guard, which is exactly the
#: shape these three routes used to ship: a malformed id matched nothing and
#: answered the same empty page as a real thread with no work.
OPTIONAL_FILTER_ANNOTATION = "str | None"

# (method, path) routes deliberately excluded from the runtime 422 sweep.
RUNTIME_WHITELIST = {
    ("DELETE", "/api/threads/{thread_id}"),  # legacy-cleanup escape hatch
}

BAD_THREAD_ID = "bad.thread.id"

_ROUTE_DECORATOR_RE = re.compile(r"router\.(get|post|delete|put|patch|websocket)")


def _handler_validates_thread_id(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Whether the handler itself calls ``validate_thread_id(thread_id)``.

    This is what turns ``str | None`` from a bare ``str`` into a real boundary:
    absence means "no filter", defiance means the canonical contract is
    enforced before the value ever reaches a store. A handler that merely
    declares ``str | None`` without validating still fails the guard.
    """
    wanted: set[str] = set()
    for stmt in node.body:
        for child in ast.walk(stmt):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name == "validate_thread_id":
                wanted.add(ast.unparse(child))
    if not wanted:
        return False
    return any(call == "validate_thread_id(thread_id)" for call in wanted)


def _iter_route_handlers(path: Path):
    """Yield (handler_name, annotation, validates_thread_id) for route handlers."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and isinstance(dec.func.value, ast.Name) and _ROUTE_DECORATOR_RE.fullmatch(f"{dec.func.value.id}.{dec.func.attr}") for dec in node.decorator_list):
            continue
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if arg.arg == "thread_id":
                annotation = ast.unparse(arg.annotation) if arg.annotation else None
                yield node.name, annotation, _handler_validates_thread_id(node)


def test_every_thread_id_route_handler_uses_canonical_type():
    """Static guard: no route handler may leave a ``str`` thread_id unvalidated.

    ``ThreadId`` (optionally ``ThreadId | None``) is canonical because the
    annotation itself enforces `^[A-Za-z0-9_-]{1,64}$`. An optional **filter**
    cannot be a bare ``ThreadId`` — omitting it must resolve to ``None``
    rather than fail validation — so it is accepted *only* when the handler
    body calls ``validate_thread_id(thread_id)``. A raw ``str`` that never
    reaches the canonical contract stays a violation: that was the shape the
    three ledger-side-effect filters used to ship, where a malformed id
    matched nothing and answered the same empty page as a real thread with no
    work.
    """
    violations = []
    for path in sorted(ROUTERS_DIR.glob("*.py")):
        for handler, annotation, validated in _iter_route_handlers(path):
            if handler in STATIC_WHITELIST:
                continue
            if annotation in CANONICAL_ANNOTATIONS:
                continue
            if annotation == OPTIONAL_FILTER_ANNOTATION and validated:
                continue
            violations.append(f"{path.name}:{handler} -> {annotation!r}")
    assert not violations, "route handlers with non-canonical thread_id:\n" + "\n".join(violations)


def _collect_thread_id_routes():
    """Import every gateway router and collect (method, full_path) with {thread_id}."""
    from app.gateway.routers import (
        agent_messages,
        artifacts,
        browser,
        feedback,
        mcp_tasks,
        runs,
        scheduled_tasks,
        skills,
        subagent_batches,
        suggestions,
        thread_runs,
        threads,
        uploads,
    )

    routers = [
        agent_messages,
        artifacts,
        browser,
        feedback,
        mcp_tasks,
        runs,
        scheduled_tasks,
        skills,
        subagent_batches,
        suggestions,
        thread_runs,
        threads,
        uploads,
    ]
    cases = []
    for module in routers:
        for route in module.router.routes:
            path = getattr(route, "path", "")
            if "{thread_id}" not in path:
                continue
            methods = getattr(route, "methods", None)
            if methods is None:
                continue  # websocket routes — covered by the dedicated test below
            for method in sorted(methods):
                if (method, path) in RUNTIME_WHITELIST:
                    continue
                cases.append((module.__name__.rsplit(".", 1)[-1], method, path))
    return cases


_THREAD_ID_ROUTES = _collect_thread_id_routes()


def test_browser_websocket_rejects_noncanonical_thread_id():
    """The browser stream websocket validates thread_id on upgrade."""
    from starlette.websockets import WebSocketDisconnect

    from app.gateway.routers import browser

    app = make_authed_test_app()
    app.include_router(browser.router)

    with TestClient(app, raise_server_exceptions=False) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/threads/{BAD_THREAD_ID}/browser/stream"):
                pass


def test_sweep_covers_expected_surface():
    """Sanity: the sweep must actually see the known thread_id routes."""
    assert len(_THREAD_ID_ROUTES) >= 30
    assert any("suggestions" in name for name, _, _ in _THREAD_ID_ROUTES)
    assert any("mcp_tasks" in name for name, _, _ in _THREAD_ID_ROUTES)
    assert any("subagent_batches" in name for name, _, _ in _THREAD_ID_ROUTES)


def test_sweep_covers_every_router_module_with_thread_id_routes():
    """No router module declaring ``{thread_id}`` route paths may fall out of
    the runtime sweep (a file merely mentioning thread_id in a body field
    does not count)."""
    import importlib

    swept = {name for name, _, _ in _THREAD_ID_ROUTES}
    missing = []
    for path in sorted(ROUTERS_DIR.glob("*.py")):
        module = importlib.import_module(f"app.gateway.routers.{path.stem}")
        routes = getattr(getattr(module, "router", None), "routes", None) or []
        if not any("{thread_id}" in getattr(route, "path", "") for route in routes):
            continue
        if path.stem not in swept:
            missing.append(path.stem)
    assert not missing, f"routers with thread_id routes missing from the runtime sweep: {missing}"


@pytest.mark.parametrize(
    ("router_name", "method", "path"),
    _THREAD_ID_ROUTES,
    ids=[f"{name}:{method}:{path}" for name, method, path in _THREAD_ID_ROUTES],
)
def test_noncanonical_thread_id_gets_422(router_name, method, path):
    """Runtime guard: a non-canonical thread_id yields 422 naming thread_id."""
    import importlib
    from unittest.mock import MagicMock

    from app.gateway.deps import get_config

    module = importlib.import_module(f"app.gateway.routers.{router_name}")
    app = make_authed_test_app()
    app.include_router(module.router)
    # get_config 503s when no config.yaml exists (CI), and dependency solving
    # precedes path-param validation — override it so the 422 contract is
    # exercised regardless of the environment.
    app.dependency_overrides[get_config] = MagicMock()

    url = path.replace("{thread_id}", BAD_THREAD_ID)
    # Other path params get a harmless canonical placeholder.
    url = re.sub(r"\{(\w+)(?::path)?\}", "x", url)

    with TestClient(app, raise_server_exceptions=False) as client:
        if method == "GET":
            response = client.get(url)
        elif method == "DELETE":
            response = client.delete(url)
        elif method in {"POST", "PUT", "PATCH"}:
            response = client.request(method, url, json={})
        else:  # WEBSOCKET etc. — not expected in the sweep
            pytest.skip(f"unsupported method {method}")

    assert response.status_code == 422, f"{method} {path} -> {response.status_code}: {response.text[:300]}"
    detail = response.json()["detail"]
    assert any("thread_id" in str(err.get("loc", ())) for err in detail), f"422 did not name thread_id: {detail}"
