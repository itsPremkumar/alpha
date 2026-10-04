"""Every GET route on the intelligence router must have a reachable body.

The bug this pins
-----------------
``tests/blocking_io/test_intelligence_router.py`` drives a **hand-picked list of
seven routes** and asserts ``status in {200, 404}``. ``GET /inventory`` was not
on that list, so its body could be broken and the suite stayed green.

It was broken. ``intelligence_inventory`` ended in::

    return await _read(build_self_inventory, sections=wanted, detail=...).to_dict

``await`` binds looser than attribute access, so that parses as
``await (_read(...).to_dict)`` -- it reads ``.to_dict`` off a **coroutine
object**, and every request raised ``AttributeError``. The entire self-knowledge
HTTP surface answered HTTP 500 in a live Gateway while 50+ unit tests of
``build_self_inventory`` passed.

The lesson is not "add /inventory to the list". It is that a hand-curated list
cannot prove the absence of this defect class: a route that is mounted, and
absent from the list, is untested by construction. So this file **enumerates**
the router's routes instead of naming them, which makes a newly added route
covered automatically and an omitted one impossible.

``scripts/route_sweep.py`` does the same across the whole app against a booted
Gateway; this is the offline, CI-runnable version scoped to one router.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers.intelligence import router as intelligence_router

#: Routes whose answer legitimately depends on ids this offline environment
#: cannot mint. Every entry names the reason; the set must stay small, because
#: its growth is exactly the blind spot this file exists to remove.
#:
#: ``/experts/{expert_id}`` and every other parameterised route is excluded by
#: construction (see ``_route_paths``), not by being listed here.
_SKIP_EXACT: frozenset[str] = frozenset()


def _route_paths() -> list[str]:
    """Every parameterless GET path the router exposes, sorted."""
    paths = set()
    for route in intelligence_router.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if "GET" not in methods or not path or "{" in path:
            continue
        if path in _SKIP_EXACT:
            continue
        paths.add(path)
    return sorted(paths)


def test_the_router_has_routes_to_enumerate() -> None:
    """Guard against the sweep silently passing because it found nothing."""
    paths = _route_paths()
    assert len(paths) >= 15, f"expected the router to expose its read surface, found {paths}"


@pytest.mark.parametrize("path", _route_paths())
def test_every_inventory_route_body_runs(path: str, tmp_path: object) -> None:
    """Each parameterless GET route must answer, not raise.

    ``assert status < 500`` rather than ``== 200``: a 404 for an absent expert or
    a 422 for a validation boundary is a real answer, and the point of the test
    is that the handler *body executed at all*. The route that motivated this
    file answered 500 for every caller.
    """
    import shutil
    from pathlib import Path

    import yaml

    home = Path(str(tmp_path))
    config_path = home / "config.yaml"
    source = Path(__file__).resolve().parents[2] / "config.example.yaml"
    config_path.write_text(
        yaml.safe_dump(yaml.safe_load(source.read_text(encoding="utf-8")), allow_unicode=True),
        encoding="utf-8",
    )
    del shutil

    import os

    previous = {k: os.environ.get(k) for k in ("ALPHA_CONFIG_PATH", "ALPHA_HOME")}
    os.environ["ALPHA_CONFIG_PATH"] = str(config_path)
    os.environ["ALPHA_HOME"] = str(home / "alpha-home")
    try:
        app = FastAPI()
        app.include_router(intelligence_router)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(path)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert response.status_code < 500, f"{path} -> HTTP {response.status_code}: {response.text[:400]}"


def test_the_inventory_route_returns_its_document_not_a_traceback() -> None:
    """The specific regression, asserted on the payload shape.

    ``/inventory`` is the one route whose contract is a *document*: a schema
    version plus per-section status. A 500, or a payload missing
    ``schema_version``, is the bug this file exists to catch.
    """
    import os
    from pathlib import Path

    import yaml
    from fastapi.testclient import TestClient

    home = Path(os.environ.get("TMP") or ".") / "alpha-inventory-probe"
    home.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[2] / "config.example.yaml"
    config_path = home / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(yaml.safe_load(source.read_text(encoding="utf-8")), allow_unicode=True),
        encoding="utf-8",
    )
    previous = {k: os.environ.get(k) for k in ("ALPHA_CONFIG_PATH", "ALPHA_HOME")}
    os.environ["ALPHA_CONFIG_PATH"] = str(config_path)
    os.environ["ALPHA_HOME"] = str(home / "alpha-home")
    try:
        app = FastAPI()
        app.include_router(intelligence_router)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/intelligence/inventory")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert response.status_code == 200, f"HTTP {response.status_code}: {response.text[:400]}"
    payload = response.json()
    assert payload.get("schema_version") == "alpha.self-inventory.v1", payload.get("schema_version")
    assert isinstance(payload.get("sections"), (dict, list))
