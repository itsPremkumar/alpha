"""Runtime tests for the self-inventory HTTP surface.

Route-level, because the two things most likely to regress here are transport
concerns the library tests cannot see: whether the routes are actually mounted and
in the right order, and whether the async handlers actually keep their blocking
registry reads off the event loop.

The route-order assertion is not defensive boilerplate. Starlette matches in
registration order, so ``/inventory/status`` declared after a single-segment
catch-all would answer ``"Expert 'status' not found"`` — the identical trap the
skills and dynamic-workflow routers already document.
"""

from __future__ import annotations

import pytest

#: Applied per-class rather than module-wide: the two async tests need it, and a
#: module-level mark would warn on every synchronous test in the file.
pytestmark: list = []


def _routes() -> dict[str, list[str]]:
    from app.gateway.app import create_app

    app = create_app()
    collected: dict[str, list[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        collected.setdefault(path, []).extend(getattr(route, "methods", []) or [])
    return collected


class TestInventoryRoutesMounted:
    def test_inventory_and_status_are_mounted(self):
        routes = _routes()
        assert "GET" in routes.get("/api/intelligence/inventory", [])
        assert "GET" in routes.get("/api/intelligence/inventory/status", [])

    def test_status_is_declared_after_inventory(self):
        """Declaration order, not just presence.

        ``/inventory/status`` only resolves because it is a distinct two-segment
        path, but the ordering rule is what keeps a future single-segment route in
        this router from swallowing it.
        """
        from app.gateway.app import create_app

        app = create_app()
        order = [getattr(route, "path", "") for route in app.routes]
        assert order.index("/api/intelligence/inventory") < order.index("/api/intelligence/inventory/status")

    def test_experts_catch_all_does_not_shadow_inventory(self):
        routes = _routes()
        # The catch-all exists and is declared after both inventory routes.
        assert "/api/intelligence/experts/{expert_id}" in routes


class TestRegistriesRouteDisclosesTruncation:
    def test_registries_route_is_present(self):
        assert "GET" in _routes().get("/api/workflows/system/registries", [])

    def test_registry_cap_is_a_named_constant_not_a_bare_slice(self):
        """The old ``[:100]`` silently dropped rows with nothing saying so."""
        from app.gateway.routers import workflows

        assert workflows._MAX_REGISTRY_DESCRIPTORS >= 500
        source = open(workflows.__file__, encoding="utf-8").read()
        assert '["truncated"]' not in source.split("def get_workflow_registries")[1][:2000] or '"truncated":' in source
        assert '"returned"' in source


@pytest.mark.asyncio
class TestBlockingIOIsOffTheLoop:
    async def test_inventory_runs_in_a_worker_thread(self, monkeypatch):
        """A registry read touches disk, ``git`` and the config file.

        Running it inline on the event loop would stall every other run in the
        process — the exact failure the strict Blockbuster gate exists for.
        """
        import asyncio

        from app.gateway.routers import intelligence

        observed: list[str] = []

        original = asyncio.to_thread

        async def spy(fn, *args, **kwargs):
            observed.append(getattr(fn, "__module__", "") or "")
            return await original(fn, *args, **kwargs)

        monkeypatch.setattr(intelligence.asyncio, "to_thread", spy)
        await intelligence.intelligence_inventory_status()
        assert observed, "the handler bypassed asyncio.to_thread"

    async def test_search_path_also_offloads(self, monkeypatch):
        import asyncio

        from app.gateway.routers import intelligence

        observed: list[str] = []
        original = asyncio.to_thread

        async def spy(fn, *args, **kwargs):
            observed.append(getattr(fn, "__module__", "") or "")
            return await original(fn, *args, **kwargs)

        monkeypatch.setattr(intelligence.asyncio, "to_thread", spy)
        await intelligence.intelligence_inventory(query="memory", limit=2)
        assert observed
