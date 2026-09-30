"""Single-segment literal routes must be registered ABOVE same-prefix catch-alls.

Starlette matches in registration order and takes the first full match, so a
``/{name}`` catch-all declared above a sibling literal swallows it. The symptom
is a 404 whose body is indistinguishable from "that resource does not exist" -
which is why both of these shipped unnoticed:

  GET /api/models/providers
      -> 404 {"detail":"Model 'providers' not found"}
      declared AFTER /api/models/{model_name} inside routers/models.py
      called by frontend/src/lib/api.ts `fetchProvidersCatalog`

  GET /api/scheduled-tasks/blueprints
      -> 404 {"detail":"Scheduled task not found"}
      routers/deliveries.py was include_router'd AFTER routers/scheduled_tasks.py,
      so the cross-file `/scheduled-tasks/{task_id}` catch-all won
      called by frontend/src/lib/protocols.ts `listScheduledBlueprints` and
      frontend/src/lib/workforce.ts `fetchBlueprints`

These are **behavioural** pins, not source-text pins: they build the real
application and read its compiled route table, so a router that is written but
never included fails here. List membership is not reachability - the pre-existing
`tests/test_workforce_platform.py` asserted
``"/api/scheduled-tasks/blueprints" in paths``, which was TRUE while the endpoint
404ed, because the route was registered but unreachable.

The companion structural pin in the same file asserts the declaration order, so
the failure mode is diagnosed rather than merely detected.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from app.gateway.app import create_app


@pytest.fixture(scope="module")
def route_table() -> list[tuple[int, str, str]]:
    """(registration index, methods, path) for every route the app really has."""

    app: FastAPI = create_app()
    rows: list[tuple[int, str, str]] = []
    for index, route in enumerate(app.routes):
        path = getattr(route, "path", None)
        if not path:
            continue
        rows.append((index, ",".join(sorted(getattr(route, "methods", []) or [])), path))
    return rows


def _index_of(rows: list[tuple[int, str, str]], path: str) -> int:
    matches = [index for index, _methods, candidate in rows if candidate == path]
    assert matches, f"{path} is not registered at all - this is dead code, not a shadow"
    return matches[0]


def test_models_providers_is_registered_above_the_model_name_catch_all(route_table) -> None:
    literal = _index_of(route_table, "/api/models/providers")
    catch_all = _index_of(route_table, "/api/models/{model_name}")
    assert literal < catch_all, '/api/models/providers is declared after /api/models/{model_name}, so the catch-all answers it with 404 {"detail": "Model \'providers\' not found"}'


def test_scheduled_task_blueprints_is_registered_above_the_task_id_catch_all(route_table) -> None:
    literal = _index_of(route_table, "/api/scheduled-tasks/blueprints")
    catch_all = _index_of(route_table, "/api/scheduled-tasks/{task_id}")
    assert literal < catch_all, '/api/scheduled-tasks/blueprints is registered after /api/scheduled-tasks/{task_id}, so the catch-all answers it with 404 {"detail":"Scheduled task not found"}'


def test_the_two_shadowed_endpoints_keep_their_own_methods(route_table) -> None:
    """A literal path must resolve to its own verb, not merely exist in the table."""

    for path, expected in (("/api/models/providers", "GET"), ("/api/scheduled-tasks/blueprints", "GET")):
        rows = [(index, methods) for index, methods, candidate in route_table if candidate == path]
        assert rows, f"{path} missing"
        for index, methods in rows:
            assert expected in methods, f"{path} at index {index} does not accept {expected}: {methods}"


def test_sibling_literals_are_still_not_shadowed(route_table) -> None:
    """Guard the neighbours of both catch-alls.

    Moving a literal above a catch-all is only safe if the other literals on the
    same prefix are still reachable. These are the paths that answered 200 before
    the reorder; a regression here would trade one 404 for another.
    """

    for path in (
        "/api/models/free/catalog",
        "/api/models/providers/credentials-storage",
        "/api/scheduled-tasks/queue-health",
        "/api/scheduled-tasks",
    ):
        assert _index_of(route_table, path) >= 0, f"{path} disappeared from the route table"


def test_declaration_order_is_pinned_in_source(route_table) -> None:
    """Structural companion: diagnose the failure, don't only detect it.

    Weaker than the compiled-table assertions above - it proves the declaration
    order in the two source files, not that the running app honours it. It is
    here so a future reorder names the cause immediately.
    """

    from pathlib import Path

    routers = Path(__file__).resolve().parents[1] / "app" / "gateway" / "routers"
    app_py = Path(__file__).resolve().parents[1] / "app" / "gateway" / "app.py"

    models_src = (routers / "models.py").read_text(encoding="utf-8")
    assert models_src.index('"/models/providers"') < models_src.index('"/models/{model_name}"'), "routers/models.py must declare /models/providers before /models/{model_name}"

    app_src = app_py.read_text(encoding="utf-8")
    assert app_src.index("include_router(deliveries.router)") < app_src.index("include_router(scheduled_tasks.router)"), (
        "app.py must include deliveries.router before scheduled_tasks.router, or the /scheduled-tasks/{task_id} catch-all swallows /scheduled-tasks/blueprints"
    )
