import asyncio
import json
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.agents.memory import MemoryConflictError, MemoryCorruptionError
from alpha.agents.memory.backends.deermem.deer_mem import DeerMem
from alpha.agents.memory.l1.paths import generation_log_path
from alpha.agents.memory.l1.provenance import read_recent_capture_entry
from app.gateway.routers import memory


def _sample_memory(facts: list[dict] | None = None) -> dict:
    return {
        "version": "1.0",
        "lastUpdated": "2026-03-26T12:00:00Z",
        "user": {
            "workContext": {"summary": "", "updatedAt": ""},
            "personalContext": {"summary": "", "updatedAt": ""},
            "topOfMind": {"summary": "", "updatedAt": ""},
        },
        "history": {
            "recentMonths": {"summary": "", "updatedAt": ""},
            "earlierContext": {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": facts or [],
    }


# ── export ─────────────────────────────────────────────────────────────────


def test_export_memory_route_returns_current_memory() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    exported_memory = _sample_memory(facts=[{"id": "fact_export", "content": "User prefers concise responses.", "category": "preference", "confidence": 0.9, "createdAt": "2026-03-20T00:00:00Z", "source": "thread-1"}])

    mock_mgr = MagicMock()
    mock_mgr.get_memory.return_value = exported_memory
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.get("/api/memory/export")
    assert response.status_code == 200
    assert response.json()["facts"] == exported_memory["facts"]


def test_get_memory_route_offloads_manager_call_from_event_loop() -> None:
    event_loop_thread = threading.get_ident()
    called_from: list[int] = []
    manager = MagicMock()

    def get_memory(*, user_id: str) -> dict:
        called_from.append(threading.get_ident())
        return _sample_memory()

    manager.get_memory.side_effect = get_memory
    request = SimpleNamespace()
    with (
        patch("app.gateway.routers.memory.get_memory_manager", return_value=manager),
        patch("app.gateway.routers.memory._resolve_memory_user_id", return_value="user-1"),
    ):
        response = asyncio.run(memory.get_memory(request))

    assert response.facts == []
    assert called_from and called_from[0] != event_loop_thread


def test_export_memory_route_preserves_source_error() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    exported_memory = _sample_memory(
        facts=[
            {
                "id": "fact_correction",
                "content": "Use make dev for local development.",
                "category": "correction",
                "confidence": 0.95,
                "createdAt": "2026-03-20T00:00:00Z",
                "source": "thread-1",
                "sourceError": "The agent previously suggested npm start.",
            }
        ]
    )

    mock_mgr = MagicMock()
    mock_mgr.get_memory.return_value = exported_memory
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.get("/api/memory/export")
    assert response.status_code == 200
    assert response.json()["facts"][0]["sourceError"] == "The agent previously suggested npm start."


# ── import ─────────────────────────────────────────────────────────────────


def test_import_memory_route_returns_imported_memory() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    imported_memory = _sample_memory(facts=[{"id": "fact_import", "content": "User works on Alpha.", "category": "context", "confidence": 0.87, "createdAt": "2026-03-20T00:00:00Z", "source": "manual"}])

    mock_mgr = MagicMock()
    mock_mgr.import_memory.return_value = imported_memory
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.post("/api/memory/import", json=imported_memory)
    assert response.status_code == 200
    assert response.json()["facts"] == imported_memory["facts"]


def test_import_route_without_agent_name_persists_default_bucket_markdown(tmp_path) -> None:
    app = FastAPI()
    app.include_router(memory.router)
    manager = DeerMem(backend_config={"storage_path": str(tmp_path)})
    imported_memory = _sample_memory(
        facts=[
            {
                "id": "fact_gateway_import",
                "content": "Gateway imports use the default agent bucket.",
                "category": "context",
                "confidence": 0.9,
                "createdAt": "2026-07-21T00:00:00Z",
                "source": "import",
            }
        ]
    )

    with (
        patch("app.gateway.routers.memory.get_memory_manager", return_value=manager),
        patch("app.gateway.routers.memory.get_effective_user_id", return_value="alice"),
        TestClient(app) as client,
    ):
        response = client.post("/api/memory/import", json=imported_memory)

    assert response.status_code == 200
    assert [fact["id"] for fact in response.json()["facts"]] == ["fact_gateway_import"]
    facts_root = tmp_path / "users" / "alice" / "agents" / "__default__" / "facts"
    assert [path.stem for path in facts_root.glob("**/*.md")] == ["fact_gateway_import"]


def test_import_memory_route_preserves_source_error() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    imported_memory = _sample_memory(
        facts=[
            {
                "id": "fact_correction",
                "content": "Use make dev for local development.",
                "category": "correction",
                "confidence": 0.95,
                "createdAt": "2026-03-20T00:00:00Z",
                "source": "thread-1",
                "sourceError": "The agent previously suggested npm start.",
            }
        ]
    )

    mock_mgr = MagicMock()
    mock_mgr.import_memory.return_value = imported_memory
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.post("/api/memory/import", json=imported_memory)
    assert response.status_code == 200
    assert response.json()["facts"][0]["sourceError"] == "The agent previously suggested npm start."


# ── clear ──────────────────────────────────────────────────────────────────


def test_clear_memory_route_returns_cleared_memory() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    mock_mgr = MagicMock()
    mock_mgr.clear_memory.return_value = _sample_memory()
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.delete("/api/memory")
    assert response.status_code == 200
    assert response.json()["facts"] == []


# ── fact CRUD (normal / error) ─────────────────────────────────────────────


def test_create_memory_fact_route_returns_updated_memory() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    updated_memory = _sample_memory(facts=[{"id": "fact_new", "content": "User prefers concise code reviews.", "category": "preference", "confidence": 0.88, "createdAt": "2026-03-20T00:00:00Z", "source": "manual"}])

    mock_mgr = MagicMock()
    mock_mgr.create_fact.return_value = (updated_memory, "fact_new")
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.post("/api/memory/facts", json={"content": "User prefers concise code reviews.", "category": "preference", "confidence": 0.88})
    assert response.status_code == 200
    assert response.json()["facts"] == updated_memory["facts"]


def test_create_memory_fact_route_maps_conflict_to_409() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    mock_mgr = MagicMock()
    mock_mgr.create_fact.side_effect = MemoryConflictError("stale write")

    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.post("/api/memory/facts", json={"content": "fact"})

    assert response.status_code == 409
    assert response.json()["detail"] == "Memory changed concurrently; reload and retry."


def test_create_memory_fact_route_maps_duplicate_to_409() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    mock_mgr = MagicMock()
    mock_mgr.create_fact.side_effect = ValueError("Duplicate fact")

    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.post("/api/memory/facts", json={"content": "fact"})

    assert response.status_code == 409
    assert response.json()["detail"] == "A fact with the same content already exists."


def test_get_memory_route_maps_corruption_to_stable_500() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    mock_mgr = MagicMock()
    mock_mgr.get_memory.side_effect = MemoryCorruptionError("private path and parser detail")

    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.get("/api/memory")

    assert response.status_code == 500
    assert response.json()["detail"] == "Stored memory data is corrupted."


def test_delete_memory_fact_route_returns_updated_memory() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    updated_memory = _sample_memory(facts=[{"id": "fact_keep", "content": "User likes Python", "category": "preference", "confidence": 0.9, "createdAt": "2026-03-20T00:00:00Z", "source": "thread-1"}])

    mock_mgr = MagicMock()
    mock_mgr.delete_fact.return_value = updated_memory
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.delete("/api/memory/facts/fact_delete")
    assert response.status_code == 200
    assert response.json()["facts"] == updated_memory["facts"]


def test_delete_memory_fact_route_returns_404_for_missing_fact() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    mock_mgr = MagicMock()
    mock_mgr.delete_fact.side_effect = KeyError("fact_missing")
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.delete("/api/memory/facts/fact_missing")
    assert response.status_code == 404
    assert response.json()["detail"] == "Memory fact 'fact_missing' not found."


def test_update_memory_fact_route_returns_updated_memory() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    updated_memory = _sample_memory(facts=[{"id": "fact_edit", "content": "User prefers spaces", "category": "workflow", "confidence": 0.91, "createdAt": "2026-03-20T00:00:00Z", "source": "manual"}])

    mock_mgr = MagicMock()
    mock_mgr.update_fact.return_value = updated_memory
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.patch("/api/memory/facts/fact_edit", json={"content": "User prefers spaces", "category": "workflow", "confidence": 0.91})
    assert response.status_code == 200
    assert response.json()["facts"] == updated_memory["facts"]


def test_settings_fact_crud_without_agent_name_uses_default_agent(tmp_path) -> None:
    """The current Settings API sends no agent_name; it must remain usable."""
    app = FastAPI()
    app.include_router(memory.router)
    memory_path = tmp_path / "users" / "alice" / "memory.json"
    memory_path.parent.mkdir(parents=True)
    legacy = _sample_memory(
        facts=[
            {
                "id": "fact_legacy",
                "content": "Legacy global fact",
                "category": "context",
                "confidence": 0.9,
                "createdAt": "2026-03-20T00:00:00Z",
                "source": "manual",
            }
        ]
    )
    memory_path.write_text(json.dumps(legacy), encoding="utf-8")
    manager = DeerMem(backend_config={"storage_path": str(tmp_path)})

    with (
        patch("app.gateway.routers.memory.get_memory_manager", return_value=manager),
        patch("app.gateway.routers.memory.get_effective_user_id", return_value="alice"),
        TestClient(app) as client,
    ):
        fetched = client.get("/api/memory")
        assert fetched.status_code == 200
        assert [fact["content"] for fact in fetched.json()["facts"]] == ["Legacy global fact"]

        exported = client.get("/api/memory/export")
        assert exported.status_code == 200
        assert [fact["content"] for fact in exported.json()["facts"]] == ["Legacy global fact"]

        created = client.post("/api/memory/facts", json={"content": "Project uses Python", "category": "context", "confidence": 0.8})
        assert created.status_code == 200
        assert all(isinstance(fact["source"], str) for fact in created.json()["facts"])
        fact_id = next(fact["id"] for fact in created.json()["facts"] if fact["content"] == "Project uses Python")

        updated = client.patch(f"/api/memory/facts/{fact_id}", json={"content": "Project uses Python 3.12"})
        assert updated.status_code == 200
        assert updated.json()["facts"][0]["content"] == "Project uses Python 3.12"

        deleted = client.delete(f"/api/memory/facts/{fact_id}")
        assert deleted.status_code == 200
        assert [fact["id"] for fact in deleted.json()["facts"]] == ["fact_legacy"]

        deleted_legacy = client.delete("/api/memory/facts/fact_legacy")
        assert deleted_legacy.status_code == 200
        assert deleted_legacy.json()["facts"] == []

    facts_root = tmp_path / "users" / "alice" / "agents" / "__default__" / "facts"
    assert facts_root.exists()
    assert not list(facts_root.glob("**/*.md"))
    assert "facts" not in json.loads(memory_path.read_text(encoding="utf-8"))


def test_update_memory_fact_route_preserves_omitted_fields() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    updated_memory = _sample_memory(facts=[{"id": "fact_edit", "content": "User prefers spaces", "category": "preference", "confidence": 0.8, "createdAt": "2026-03-20T00:00:00Z", "source": "manual"}])

    mock_mgr = MagicMock()
    mock_mgr.update_fact.return_value = updated_memory
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.patch("/api/memory/facts/fact_edit", json={"content": "User prefers spaces"})
    assert response.status_code == 200
    # The router calls _require_capability("update_fact") -> getattr(mgr, "update_fact")
    # which returns mock_mgr.update_fact (a MagicMock).  Then the call is
    #   update_fact(fact_id=..., content=..., category=..., confidence=..., user_id=...)
    mock_mgr.update_fact.assert_called_once()
    call_kwargs = mock_mgr.update_fact.call_args.kwargs
    assert call_kwargs.get("fact_id") == "fact_edit"
    assert call_kwargs.get("content") == "User prefers spaces"
    assert call_kwargs.get("category") is None
    assert call_kwargs.get("confidence") is None
    assert "user_id" in call_kwargs
    assert response.json()["facts"] == updated_memory["facts"]


def test_update_memory_fact_route_returns_404_for_missing_fact() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    mock_mgr = MagicMock()
    mock_mgr.update_fact.side_effect = KeyError("fact_missing")
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.patch("/api/memory/facts/fact_missing", json={"content": "User prefers spaces", "category": "workflow", "confidence": 0.91})
    assert response.status_code == 404
    assert response.json()["detail"] == "Memory fact 'fact_missing' not found."


def test_update_memory_fact_route_returns_specific_error_for_invalid_confidence() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    mock_mgr = MagicMock()
    mock_mgr.update_fact.side_effect = ValueError("confidence")
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with TestClient(app) as client:
            response = client.patch("/api/memory/facts/fact_edit", json={"content": "User prefers spaces", "confidence": 0.91})
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid confidence value; must be between 0 and 1."


# ── bound-owner (internal caller) ──────────────────────────────────────────


def _internal_owner_request(owner_user_id: str) -> SimpleNamespace:
    from alpha.runtime.user_context import DEFAULT_USER_ID
    from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE

    return SimpleNamespace(
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: owner_user_id},
        state=SimpleNamespace(user=SimpleNamespace(id=DEFAULT_USER_ID, system_role=INTERNAL_SYSTEM_ROLE)),
    )


def test_get_memory_honors_bound_owner_header() -> None:
    seen: dict[str, str] = {}

    def fake_get_memory(*, user_id: str) -> dict:
        seen["user_id"] = user_id
        return _sample_memory(facts=[{"id": "f", "content": "owner fact", "category": "context", "confidence": 0.9, "createdAt": "", "source": "owner"}])

    mock_mgr = MagicMock()
    mock_mgr.get_memory.side_effect = fake_get_memory
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        response = asyncio.run(memory.get_memory(_internal_owner_request("owner-1")))
    assert seen["user_id"] == "owner-1"
    assert response.facts[0].content == "owner fact"


def test_get_memory_sanitizes_unsafe_owner_header() -> None:
    from alpha.config.paths import make_safe_user_id

    raw_owner = "feishu|ou_AbC/123"
    seen: dict[str, str] = {}

    def fake_get_memory(*, user_id: str) -> dict:
        seen["user_id"] = user_id
        return _sample_memory()

    mock_mgr = MagicMock()
    mock_mgr.get_memory.side_effect = fake_get_memory
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        asyncio.run(memory.get_memory(_internal_owner_request(raw_owner)))
    expected = make_safe_user_id(raw_owner)
    assert seen["user_id"] == expected
    assert seen["user_id"] != raw_owner


def test_get_memory_falls_back_to_effective_user_for_browser_requests() -> None:
    from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME

    seen: dict[str, str] = {}

    def fake_get_memory(*, user_id: str) -> dict:
        seen["user_id"] = user_id
        return _sample_memory()

    browser_request = SimpleNamespace(
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-1"},
        state=SimpleNamespace(user=SimpleNamespace(id="real-user", system_role="user")),
    )

    mock_mgr = MagicMock()
    mock_mgr.get_memory.side_effect = fake_get_memory
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        with patch("app.gateway.routers.memory.get_effective_user_id", return_value="real-user"):
            asyncio.run(memory.get_memory(browser_request))
    assert seen["user_id"] == "real-user"


def _browser_request_with_spoofed_owner_header() -> SimpleNamespace:
    from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME

    return SimpleNamespace(
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-1"},
        state=SimpleNamespace(user=SimpleNamespace(id="real-user", system_role="user")),
    )


def test_clear_memory_scopes_destructive_write_to_bound_owner() -> None:
    seen: dict[str, str] = {}

    def fake_clear(*, user_id: str) -> dict:
        seen["user_id"] = user_id
        return _sample_memory()

    mock_mgr = MagicMock()
    mock_mgr.clear_memory.side_effect = fake_clear
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        asyncio.run(memory.clear_memory(_internal_owner_request("owner-1")))
        assert seen["user_id"] == "owner-1"

        with patch("app.gateway.routers.memory.get_effective_user_id", return_value="real-user"):
            asyncio.run(memory.clear_memory(_browser_request_with_spoofed_owner_header()))
        assert seen["user_id"] == "real-user"


def test_import_memory_scopes_overwrite_to_bound_owner() -> None:
    seen: dict[str, str] = {}
    payload = memory.MemoryResponse(**_sample_memory())

    def fake_import(_data: dict, *, user_id: str) -> dict:
        seen["user_id"] = user_id
        return _sample_memory()

    mock_mgr = MagicMock()
    mock_mgr.import_memory.side_effect = fake_import
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mock_mgr):
        asyncio.run(memory.import_memory(payload, _internal_owner_request("owner-1")))
        assert seen["user_id"] == "owner-1"

        with patch("app.gateway.routers.memory.get_effective_user_id", return_value="real-user"):
            asyncio.run(memory.import_memory(payload, _browser_request_with_spoofed_owner_header()))
        assert seen["user_id"] == "real-user"


# ── unsupported-backend 501s ────────────────────────────────────────────────
# A minimal backend (only add + get_context) inherits the tier-2/tier-3 default
# raise for get_memory / clear_memory / import_memory / reload_memory. Before
# the contract change these were @abstractmethod (every backend implemented
# them, so the endpoints could never raise); now the endpoints catch
# NotImplementedError -> 501 so an unsupported backend gets a clean "not
# supported" instead of a raw 500 (there is no global NotImplementedError
# handler, so an uncaught raise is a 500).


def _unsupported_manager() -> MagicMock:
    """Mock a minimal backend: read/manage ops raise NotImplementedError."""
    mock_mgr = MagicMock()
    mock_mgr.get_memory.side_effect = NotImplementedError("get_memory not supported")
    mock_mgr.clear_memory.side_effect = NotImplementedError("clear_memory not supported")
    mock_mgr.import_memory.side_effect = NotImplementedError("import_memory not supported")
    mock_mgr.reload_memory.side_effect = NotImplementedError("reload_memory not supported")
    return mock_mgr


def test_get_memory_route_returns_501_for_unsupported_backend() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=_unsupported_manager()):
        with TestClient(app) as client:
            response = client.get("/api/memory")
    assert response.status_code == 501
    assert "not supported" in response.json()["detail"]


def test_export_memory_route_returns_501_for_unsupported_backend() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=_unsupported_manager()):
        with TestClient(app) as client:
            response = client.get("/api/memory/export")
    assert response.status_code == 501


def test_memory_status_route_returns_501_for_unsupported_backend() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    cfg = SimpleNamespace(
        enabled=True,
        mode="middleware",
        injection_enabled=True,
        shutdown_flush_timeout_seconds=30.0,
        manager_class="minimal",
        backend_config={},
    )
    with (
        patch("app.gateway.routers.memory.get_memory_manager", return_value=_unsupported_manager()),
        patch("app.gateway.routers.memory.get_memory_config", return_value=cfg),
    ):
        with TestClient(app) as client:
            response = client.get("/api/memory/status")
    assert response.status_code == 501


def test_memory_status_projects_latest_owner_scoped_l1_capture_without_content(tmp_path) -> None:
    app = FastAPI()
    app.include_router(memory.router)
    now = datetime(2026, 10, 16, 12, tzinfo=UTC).timestamp()
    day = datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%d")
    log = generation_log_path(tmp_path, "owner-a", day)
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps(
            {
                "ts": "2026-10-16T11:59:00+00:00",
                "status": "succeeded",
                "content": "secret memory text must not be projected",
                "capture_outcome": {"surfaces": [{"surface": "entities", "status": "written", "written": 2, "considered": 3, "rejected": 1, "detail": "private detail"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    config = SimpleNamespace(
        enabled=True,
        mode="middleware",
        injection_enabled=True,
        shutdown_flush_timeout_seconds=30.0,
        manager_class="deermem",
        backend_config={},
        l1=SimpleNamespace(enabled=True, provenance_enabled=True, storage_path=str(tmp_path)),
    )
    manager = MagicMock()
    manager.get_memory.return_value = _sample_memory()
    with (
        patch("app.gateway.routers.memory.get_memory_manager", return_value=manager),
        patch("app.gateway.routers.memory.get_memory_config", return_value=config),
        patch("app.gateway.routers.memory._resolve_memory_user_id", return_value="owner-a"),
    ):
        with TestClient(app) as client:
            response = client.get("/api/memory/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["l1_capture"] == {
        "status": "available",
        "window_days": 7,
        "latest": {
            "timestamp": "2026-10-16T11:59:00+00:00",
            "run_status": "succeeded",
            "surfaces": [{"surface": "entities", "status": "written", "written": 2, "considered": 3, "rejected": 1}],
        },
    }
    serialized = json.dumps(payload)
    assert "secret memory text" not in serialized
    assert "private detail" not in serialized


def test_memory_status_projects_prospective_health_counts_without_content(tmp_path) -> None:
    from alpha.memory.prospective.config import ProspectiveConfig
    from alpha.memory.prospective.store import ProspectiveStore, prospective_items_path

    app = FastAPI()
    app.include_router(memory.router)
    prospective_config = ProspectiveConfig(enabled=True, storage_path=str(tmp_path))
    store = ProspectiveStore(prospective_config)
    store.create(user_id="owner-a", content="private reminder text")
    store.create(user_id="owner-b", content="another owner's reminder")
    config = SimpleNamespace(
        enabled=True,
        mode="middleware",
        injection_enabled=True,
        shutdown_flush_timeout_seconds=30.0,
        manager_class="deermem",
        backend_config={},
        l1=SimpleNamespace(enabled=False, provenance_enabled=False),
        prospective=prospective_config,
    )
    manager = MagicMock()
    manager.get_memory.return_value = _sample_memory()
    with (
        patch("app.gateway.routers.memory.get_memory_manager", return_value=manager),
        patch("app.gateway.routers.memory.get_memory_config", return_value=config),
        patch("app.gateway.routers.memory._resolve_memory_user_id", return_value="owner-a"),
        TestClient(app) as client,
    ):
        response = client.get("/api/memory/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["prospective"] == {
        "status": "available",
        "pending": 1,
        "fired": 0,
        "done": 0,
        "cancelled": 0,
        "expired": 0,
    }
    assert "private reminder text" not in json.dumps(payload)
    assert "another owner's reminder" not in json.dumps(payload)
    assert prospective_items_path(tmp_path, "owner-b").exists()


def test_memory_status_discloses_unsupported_prospective_store_format(tmp_path) -> None:
    from alpha.memory.prospective.config import ProspectiveConfig
    from alpha.memory.prospective.store import prospective_items_path

    app = FastAPI()
    app.include_router(memory.router)
    prospective_config = ProspectiveConfig(enabled=True, storage_path=str(tmp_path))
    path = prospective_items_path(tmp_path, "owner-a")
    path.parent.mkdir(parents=True)
    original = '{"schema":2,"items":[],"future":true}'
    path.write_text(original, encoding="utf-8")
    config = SimpleNamespace(
        enabled=True,
        mode="middleware",
        injection_enabled=True,
        shutdown_flush_timeout_seconds=30.0,
        manager_class="deermem",
        backend_config={},
        l1=SimpleNamespace(enabled=False, provenance_enabled=False),
        prospective=prospective_config,
    )
    manager = MagicMock()
    manager.get_memory.return_value = _sample_memory()
    with (
        patch("app.gateway.routers.memory.get_memory_manager", return_value=manager),
        patch("app.gateway.routers.memory.get_memory_config", return_value=config),
        patch("app.gateway.routers.memory._resolve_memory_user_id", return_value="owner-a"),
        TestClient(app) as client,
    ):
        response = client.get("/api/memory/status")

    assert response.status_code == 200
    assert response.json()["prospective"] == {"status": "degraded", "reason": "unsupported_store_format", "pending": 0, "fired": 0, "done": 0, "cancelled": 0, "expired": 0}
    assert path.read_text(encoding="utf-8") == original


def test_prospective_memory_routes_list_and_transition_only_the_authenticated_owner(tmp_path) -> None:
    from alpha.memory.prospective.config import ProspectiveConfig
    from alpha.memory.prospective.store import ProspectiveStore

    app = FastAPI()
    app.include_router(memory.router)
    prospective_config = ProspectiveConfig(enabled=True, storage_path=str(tmp_path))
    store = ProspectiveStore(prospective_config)
    owned = store.create(user_id="owner-a", content="Review the migration checklist")
    foreign = store.create(user_id="owner-b", content="Private note from another account")
    config = SimpleNamespace(enabled=True, prospective=prospective_config)

    with (
        patch("app.gateway.routers.memory.get_memory_config", return_value=config),
        patch("app.gateway.routers.memory._resolve_memory_user_id", return_value="owner-a"),
        TestClient(app) as client,
    ):
        listed = client.get("/api/memory/prospective")
        completed = client.post(f"/api/memory/prospective/{owned.item.id}/complete", json={"reason": "verified in UI"})
        foreign_cancel = client.post(f"/api/memory/prospective/{foreign.item.id}/cancel", json={"reason": "should not cross owners"})

    assert listed.status_code == 200
    assert listed.json()["status"] == "succeeded"
    assert [item["id"] for item in listed.json()["items"]] == [owned.item.id]
    assert "Private note from another account" not in listed.text
    assert completed.status_code == 200
    assert completed.json()["item"]["status"] == "done"
    assert foreign_cancel.status_code == 404
    assert store.get(owned.item.id, user_id="owner-a").status.value == "done"
    assert store.get(foreign.item.id, user_id="owner-b").status.value == "pending"


def test_prospective_memory_routes_disclose_unsupported_format_and_refuse_mutation(tmp_path) -> None:
    from alpha.memory.prospective.config import ProspectiveConfig
    from alpha.memory.prospective.store import prospective_items_path

    app = FastAPI()
    app.include_router(memory.router)
    prospective_config = ProspectiveConfig(enabled=True, storage_path=str(tmp_path))
    path = prospective_items_path(tmp_path, "owner-a")
    path.parent.mkdir(parents=True)
    original = '{"schema":2,"items":[],"future":true}'
    path.write_text(original, encoding="utf-8")
    config = SimpleNamespace(enabled=True, prospective=prospective_config)

    with (
        patch("app.gateway.routers.memory.get_memory_config", return_value=config),
        patch("app.gateway.routers.memory._resolve_memory_user_id", return_value="owner-a"),
        TestClient(app) as client,
    ):
        listed = client.get("/api/memory/prospective")
        mutation = client.post("/api/memory/prospective/unknown/complete", json={})

    assert listed.status_code == 503
    assert "unsupported_store_format" in listed.json()["detail"]
    assert mutation.status_code == 503
    assert path.read_text(encoding="utf-8") == original


def test_memory_status_reports_no_recent_l1_runs_for_another_owner(tmp_path) -> None:
    now = datetime(2026, 10, 16, 12, tzinfo=UTC).timestamp()
    day = datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%d")
    log = generation_log_path(tmp_path, "owner-a", day)
    log.parent.mkdir(parents=True)
    log.write_text('{"capture_outcome":{"surfaces":[]}}\n', encoding="utf-8")

    result = read_recent_capture_entry(user_id="owner-b", storage_path=str(tmp_path), now=now)
    assert result["status"] == "no_recent_runs"
    assert result["entry"] is None


def test_read_recent_l1_capture_distinguishes_corrupt_from_missing(tmp_path) -> None:
    now = datetime(2026, 10, 16, 12, tzinfo=UTC).timestamp()
    day = datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%d")
    log = generation_log_path(tmp_path, "owner-a", day)
    log.parent.mkdir(parents=True)
    log.write_text("{not-json}\n", encoding="utf-8")

    result = read_recent_capture_entry(user_id="owner-a", storage_path=str(tmp_path), now=now)
    assert result["status"] == "unreadable"
    assert result["reason"] == "generation_log_unreadable"


def test_read_recent_l1_capture_uses_a_bounded_tail(tmp_path) -> None:
    now = datetime(2026, 10, 16, 12, tzinfo=UTC).timestamp()
    day = datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%d")
    log = generation_log_path(tmp_path, "owner-a", day)
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps({"ts": "old", "capture_outcome": {"surfaces": []}}) + "\n" + "x" * 5000 + "\n" + json.dumps({"ts": "latest", "capture_outcome": {"surfaces": []}}) + "\n",
        encoding="utf-8",
    )

    result = read_recent_capture_entry(
        user_id="owner-a",
        storage_path=str(tmp_path),
        now=now,
        max_bytes_per_day=1024,
    )
    assert result["status"] == "available"
    assert result["entry"]["ts"] == "latest"


def test_clear_memory_route_returns_501_for_unsupported_backend() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=_unsupported_manager()):
        with TestClient(app) as client:
            response = client.delete("/api/memory")
    assert response.status_code == 501


def test_import_memory_route_returns_501_for_unsupported_backend() -> None:
    app = FastAPI()
    app.include_router(memory.router)
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=_unsupported_manager()):
        with TestClient(app) as client:
            response = client.post("/api/memory/import", json=_sample_memory())
    assert response.status_code == 501


def test_reload_memory_route_returns_501_when_read_also_unsupported() -> None:
    """reload falls back to get_memory; if both raise (minimal backend), the
    fallback surfaces 501 instead of a raw 500 from the uncaught raise."""
    app = FastAPI()
    app.include_router(memory.router)
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=_unsupported_manager()):
        with TestClient(app) as client:
            response = client.post("/api/memory/reload")
    assert response.status_code == 501
