"""Host resource probe: best-effort snapshot that never fails the request."""

from __future__ import annotations

import pytest

from app.gateway.app import create_app
from app.gateway.routers import ops

pytestmark = pytest.mark.asyncio


async def test_resources_shape_and_units() -> None:
    response = await ops.ops_resources()
    assert response.platform
    assert response.cpu_count is None or response.cpu_count >= 1
    if response.memory.total_mb is not None:
        assert response.memory.total_mb > 0
    if response.memory.available_mb is not None:
        assert 0 <= response.memory.available_mb <= (response.memory.total_mb or response.memory.available_mb)
    if response.disk is not None:
        assert response.disk.total_mb > 0
        assert 0 <= response.disk.free_mb <= response.disk.total_mb
        assert response.disk.path
    if response.load_average is not None:
        assert len(response.load_average) == 3


async def test_memory_probe_degrades_to_nulls() -> None:
    snapshot = ops._memory_mb()
    # Must always return a snapshot, never raise — nulls are the contract.
    assert snapshot.total_mb is None or snapshot.total_mb > 0


async def test_gateway_mounts_resources_route() -> None:
    paths = {route.path for route in create_app().routes}
    assert "/api/ops/resources" in paths
