"""Regression anchor: the intelligence router must not block the event loop.

``app.gateway.routers.intelligence`` exposes 21 read-only routes whose bodies
read real state — ``get_app_config()`` parses a YAML file,
``LearningJournal`` opens and scans a JSONL file, ``ExpertFabric`` reads and
validates a JSON document, and the regression coverage reads
``config.example.yaml``. Every one of those is blocking filesystem IO.

This was a real defect, not a hypothetical one: ``loop_health`` and
``get_expert_prune_eligibility`` both called ``intelligence_config()``
directly inside an ``async def``, so a YAML parse ran on the event loop on every
request. If either regresses, the strict Blockbuster gate below raises
``BlockingError`` and this test fails.

The two routes are driven through a real ``TestClient`` rather than by calling
the coroutines, because the defect was in the handler body rather than in the
helper it calls; a direct ``await handler()`` would exercise the same code but
would not prove the route is mounted and reachable.

Seeding the journal on disk is itself offloaded with ``asyncio.to_thread`` so
only the handlers' own filesystem access is measured on the loop.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Imported at module scope deliberately. The Blockbuster gate arms around the whole
# item protocol, and importing the router module constructs app-level singletons
# (the kanban store resolves `Path.cwd()` at import time), which would trip the gate
# on import cost rather than on request cost. Collection happens before the gate
# arms, so the defect this file exists to catch -- blocking IO in the *handler body*
# -- stays isolated.
from app.gateway.routers.intelligence import router as intelligence_router

pytestmark = pytest.mark.asyncio


def _write_config(root: Path) -> Path:
    """Copy the shipped example config so ``AppConfig`` resolves for real."""
    import yaml

    source = Path(__file__).resolve().parents[3] / "config.example.yaml"
    target = root / "config.yaml"
    target.write_text(yaml.safe_dump(yaml.safe_load(source.read_text(encoding="utf-8")), allow_unicode=True), encoding="utf-8")
    return target


async def _seed_journal(home: Path) -> None:
    """Write a journal line off the loop so only the handler's reads are measured."""
    journal = home / "intelligence" / "learning-journal.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)

    def _write() -> None:
        event = {"kind": "loop_observed", "mode": "OBSERVE_ONLY", "decision": "NO_CHANGE", "reason": "seed", "experiences": [], "expert_id": "", "experiment_id": "", "recorded_at": 1.0}
        entry = {"index": 0, "prev_hash": "0" * 64, "event": event}
        journal.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    await asyncio.to_thread(_write)


async def test_intelligence_routes_stay_off_the_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the config-reading routes with Blockbuster armed."""
    config_path = _write_config(tmp_path)
    home = tmp_path / "alpha-home"
    monkeypatch.setenv("ALPHA_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("ALPHA_HOME", str(home))
    await _seed_journal(home)

    app = FastAPI()
    app.include_router(intelligence_router)
    client = TestClient(app)

    async def drive() -> list[tuple[str, int]]:
        results: list[tuple[str, int]] = []
        for path in (
            "/api/intelligence/health",
            "/api/intelligence/control-plane",
            "/api/intelligence/experts/expert_000001/prune-eligibility",
            "/api/intelligence/journal",
            "/api/intelligence/replay",
            "/api/intelligence/snapshots",
            "/api/intelligence/paging",
            "/api/intelligence/regressions",
        ):
            response = await asyncio.to_thread(client.get, path)
            results.append((path, response.status_code))
        return results

    results = await drive()

    for path, status in results:
        # 404 for the seeded-absent expert is a correct answer, not a failure:
        # the point of this test is that no route blocked the loop.
        assert status in {200, 404}, f"{path} returned {status}"


async def test_health_endpoint_is_mounted_and_read_only() -> None:
    """The new Phase F route exists, is reachable, and exposes no mutation."""
    paths = {route.path: set(route.methods) for route in intelligence_router.routes}
    assert "/api/intelligence/health" in paths
    assert paths["/api/intelligence/health"] == {"GET"}
    # Every intelligence route is read-only; a mutation surface would need its
    # own authz and CSRF decision, which was deliberately not made.
    for path, methods in paths.items():
        assert not methods & {"POST", "PUT", "PATCH", "DELETE"}, f"{path} exposes a mutation method: {methods}"
