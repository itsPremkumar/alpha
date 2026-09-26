"""Round-trip verification of every memory path hop (Part A).

Proves write -> persist (real disk bytes) -> retrieve (fresh reader) ->
``<memory>`` prompt injection -> HTTP API using a real DeerMem store; the
storage layer is never mocked. It also exercises the failure hops:

* a corrupt on-disk store raises the typed manager error, maps to HTTP 500,
  and its bytes are never repaired or rewritten;
* a degraded read is disclosed (structured log) and returns no
  success-shaped ``<memory>`` block, while a fail-closed config re-raises.
"""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from alpha.agents.lead_agent import prompt as lead_prompt
from alpha.agents.memory import MemoryCorruptionError
from alpha.agents.memory.backends.deermem.deer_mem import DeerMem
from alpha.agents.memory.tools import memory_add_tool, memory_search_tool
from app.gateway.routers import memory as memory_router
from app.gateway.routers.memory import MemoryResponse, _get_memory_or_501

FACT_TEXT = "User prefers pytest with -q for targeted test runs."
FACT_QUERY = "pytest"
AGENT = "alpha-agent"


@pytest.fixture
def make_manager(tmp_path):
    """Real DeerMem managers sharing one on-disk store under tmp_path."""
    managers = []

    def factory(*, mode: str = "middleware", storage_name: str = "memstore"):
        manager = DeerMem(
            backend_config={
                "storage_path": str(tmp_path / storage_name),
                "token_counting": "char",
                "retrieval_adapter": "",
            },
            mode=mode,
        )
        managers.append(manager)
        return manager

    yield factory
    for manager in managers:
        manager.shutdown_flush(5)


def _memory_config(*, enabled: bool = True, injection_enabled: bool = True, backend_config=None):
    return SimpleNamespace(
        enabled=enabled,
        injection_enabled=injection_enabled,
        backend_config=backend_config if backend_config is not None else {},
    )


def _app_config(*, enabled: bool = True, injection_enabled: bool = True, backend_config=None):
    """Minimal AppConfig shape: _get_memory_context reads app_config.memory."""
    return SimpleNamespace(
        memory=_memory_config(
            enabled=enabled,
            injection_enabled=injection_enabled,
            backend_config=backend_config,
        )
    )


def test_create_fact_persists_disk_bytes_and_fresh_reader_retrieves(make_manager, tmp_path):
    manager = make_manager()
    memory_data, fact_id = manager.create_fact(FACT_TEXT, "preference", 0.9, agent_name=AGENT, user_id="user-rt")
    assert fact_id, "create_fact must return a durable fact id"
    assert any(fact.get("content") == FACT_TEXT for fact in memory_data["facts"])

    # Persist hop: the fact exists as real bytes on disk, not only in memory.
    user_root = tmp_path / "memstore" / "users" / "user-rt"
    fact_files = list(user_root.rglob("*.md"))
    assert fact_files, "fact markdown file was not persisted to disk"
    assert any(FACT_TEXT.encode("utf-8") in path.read_bytes() for path in fact_files)
    memory_json = user_root / "memory.json"
    assert memory_json.is_file()
    on_disk_doc = json.loads(memory_json.read_text(encoding="utf-8"))
    assert isinstance(on_disk_doc, dict) and on_disk_doc

    # Retrieve hop: a brand-new manager (fresh process simulation) reads the
    # fact back through all three read surfaces.
    fresh = make_manager()
    doc = fresh.get_memory(user_id="user-rt", agent_name=AGENT)
    persisted = [fact for fact in doc["facts"] if fact.get("content") == FACT_TEXT]
    assert persisted, "fresh get_memory did not return the persisted fact"
    assert persisted[0].get("confidence") == pytest.approx(0.9)

    hits = fresh.search(FACT_QUERY, top_k=5, user_id="user-rt", agent_name=AGENT)
    assert any(hit.get("content") == FACT_TEXT for hit in hits), "search did not recall the persisted fact"

    context = fresh.get_context(user_id="user-rt", agent_name=AGENT)
    assert FACT_TEXT in context, "injection context does not contain the persisted fact"


def test_memory_tools_roundtrip_through_real_store(make_manager, monkeypatch):
    manager = make_manager()
    monkeypatch.setattr("alpha.agents.memory.tools.get_memory_manager", lambda: manager)
    monkeypatch.setattr("alpha.agents.memory.tools.resolve_runtime_user_id", lambda runtime: "user-tools")
    runtime = SimpleNamespace(context={"agent_name": AGENT})

    added = json.loads(memory_add_tool.func(runtime, FACT_TEXT, category="preference", confidence=0.9))
    assert added.get("status") == "added"
    assert added.get("fact_id"), "memory_add did not return a durable fact id"

    searched = json.loads(memory_search_tool.func(runtime, FACT_QUERY))
    assert searched.get("count", 0) >= 1
    assert any(result.get("content") == FACT_TEXT for result in searched["results"])

    # The duplicate rejection is real (checked against the store), not cosmetic.
    duplicate = json.loads(memory_add_tool.func(runtime, FACT_TEXT, category="preference", confidence=0.9))
    assert duplicate.get("error") == "Duplicate fact"


def test_get_memory_context_injects_persisted_fact_in_memory_block(make_manager, monkeypatch):
    manager = make_manager()
    manager.create_fact(FACT_TEXT, "preference", 0.9, agent_name=AGENT, user_id="user-inj")
    monkeypatch.setattr("alpha.agents.memory.get_memory_manager", lambda: manager)

    block = lead_prompt._get_memory_context(AGENT, app_config=_app_config(), user_id="user-inj")

    assert block.startswith("<memory>\n")
    assert block.rstrip().endswith("</memory>")
    assert FACT_TEXT in block


def test_get_memory_context_disabled_gate_returns_empty(make_manager, monkeypatch):
    manager = make_manager()
    manager.create_fact(FACT_TEXT, "preference", 0.9, agent_name=AGENT, user_id="user-gate")
    monkeypatch.setattr("alpha.agents.memory.get_memory_manager", lambda: manager)

    block = lead_prompt._get_memory_context(
        AGENT,
        app_config=_app_config(injection_enabled=False),
        user_id="user-gate",
    )

    assert block == ""


def test_api_get_memory_serves_fact_written_to_disk(make_manager):
    manager = make_manager()
    # agent_name=None resolves to the DEFAULT bucket, which is exactly the
    # scope GET /api/memory reads (get_memory without an agent scope).
    manager.create_fact(FACT_TEXT, "preference", 0.9, agent_name=None, user_id="user-api")

    # Direct seam hop: _get_memory_or_501 returns the raw doc ...
    raw_doc = asyncio.run(_get_memory_or_501(manager, "user-api", "get memory"))
    assert any(fact.get("content") == FACT_TEXT for fact in raw_doc["facts"])
    # ... and the doc validates as the documented API response model.
    MemoryResponse.model_validate(raw_doc)

    # ... and the full HTTP hop serves the on-disk fact to the UI.
    app = FastAPI()
    app.include_router(memory_router.router)
    with (
        patch("app.gateway.routers.memory.get_memory_manager", return_value=manager),
        patch("app.gateway.routers.memory._resolve_memory_user_id", return_value="user-api"),
    ):
        with TestClient(app) as client:
            response = client.get("/api/memory")
    assert response.status_code == 200
    payload = MemoryResponse.model_validate(response.json())
    assert any(fact.content == FACT_TEXT for fact in payload.facts)


def test_corrupt_store_maps_to_http_500_without_repairing_bytes(make_manager, tmp_path):
    manager = make_manager()
    manager.create_fact(FACT_TEXT, "preference", 0.9, agent_name=None, user_id="user-corrupt")

    memory_json = tmp_path / "memstore" / "users" / "user-corrupt" / "memory.json"
    corrupt_bytes = b"{ truncated garbage"
    memory_json.write_bytes(corrupt_bytes)

    # Fresh reader (post-restart semantics) must surface the typed error.
    fresh = make_manager()
    with pytest.raises(MemoryCorruptionError):
        fresh.get_memory(user_id="user-corrupt")

    # Direct seam: the typed error maps to the stable 500 contract.
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(_get_memory_or_501(fresh, "user-corrupt", "get memory"))
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == "Stored memory data is corrupted."

    # Full HTTP hop.
    app = FastAPI()
    app.include_router(memory_router.router)
    with (
        patch("app.gateway.routers.memory.get_memory_manager", return_value=fresh),
        patch("app.gateway.routers.memory._resolve_memory_user_id", return_value="user-corrupt"),
    ):
        with TestClient(app) as client:
            response = client.get("/api/memory")
    assert response.status_code == 500
    assert response.json()["detail"] == "Stored memory data is corrupted."

    # Fail closed, no silent repair: the corrupt bytes are untouched after
    # the failed manager read and the failed HTTP read.
    assert memory_json.read_bytes() == corrupt_bytes


def test_degraded_read_is_disclosed_and_not_dressed_as_success(make_manager, monkeypatch, caplog):
    manager = make_manager()

    def exploding_get_context(self, user_id=None, **_kwargs):
        raise RuntimeError("simulated memory backend outage")

    monkeypatch.setattr(DeerMem, "get_context", exploding_get_context)
    monkeypatch.setattr("alpha.agents.memory.get_memory_manager", lambda: manager)

    with caplog.at_level(logging.ERROR):
        block = lead_prompt._get_memory_context(AGENT, app_config=_app_config(), user_id="user-degraded")

    # Permissive policy: the degraded read returns no success-shaped block,
    # and the failure is disclosed via a structured exception log.
    assert block == ""
    disclosure = [record for record in caplog.records if record.message == "Failed to load memory context"]
    assert disclosure, "degraded memory read was not disclosed in the logs"
    assert disclosure[0].exc_info is not None, "disclosure log must carry the original exception"
    # The disclosed cause is the injected backend outage itself, not some
    # incidental configuration error on the way to the read.
    disclosed = disclosure[0].exc_info[1]
    assert isinstance(disclosed, RuntimeError)
    assert "simulated memory backend outage" in str(disclosed)


def test_fail_closed_policy_reraises_memory_error(make_manager, monkeypatch):
    manager = make_manager()

    def corrupting_get_context(self, user_id=None, **_kwargs):
        raise MemoryCorruptionError("Stored memory data is corrupted.")

    monkeypatch.setattr(DeerMem, "get_context", corrupting_get_context)
    monkeypatch.setattr("alpha.agents.memory.get_memory_manager", lambda: manager)

    config = _app_config(backend_config={"failure_policy": {"read": "fail_closed"}})
    with pytest.raises(MemoryCorruptionError):
        lead_prompt._get_memory_context(AGENT, app_config=config, user_id="user-fail-closed")


def test_get_memory_or_501_maps_unsupported_backend():
    class MinimalBackend:
        def get_memory(self, *, user_id=None, agent_name=None):
            raise NotImplementedError("get_memory not supported by MinimalBackend")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(_get_memory_or_501(MinimalBackend(), "user-1", "get memory"))
    assert excinfo.value.status_code == 501
    assert "MinimalBackend" in excinfo.value.detail
