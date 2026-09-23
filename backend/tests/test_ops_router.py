"""Unit tests for the production operations router (app.gateway.routers.ops)."""

from datetime import datetime
from importlib import metadata
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.gateway.routers.ops as ops_module
from app.gateway.routers import ops


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ops.router)
    return TestClient(app)


def test_ops_version_returns_service_and_version(monkeypatch) -> None:
    monkeypatch.setattr(ops_module, "_resolve_gateway_version", lambda: "2.1.0")

    with _client() as client:
        response = client.get("/api/ops/version")

    assert response.status_code == 200
    assert response.json() == {"service": "agent-workspace-gateway", "version": "2.1.0"}


def test_ops_version_falls_back_to_unknown_without_package_metadata(monkeypatch) -> None:
    def _missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(ops_module.metadata, "version", _missing)

    assert ops_module._resolve_gateway_version() == "unknown"


def test_ops_version_resolves_installed_harness_then_legacy_names(monkeypatch) -> None:
    """Pin the lookup chain: agent-workspace-harness first (it carries the
    release version), then legacy names. A duplicated name tuple previously
    made every packaged deployment report "unknown"."""
    looked_up: list[str] = []

    def _resolver(installed: dict[str, str]):
        def _version(name: str) -> str:
            looked_up.append(name)
            if name in installed:
                return installed[name]
            raise metadata.PackageNotFoundError(name)

        return _version

    monkeypatch.setattr(ops_module.metadata, "version", _resolver({"agent-workspace-harness": "2.1.0"}))
    assert ops_module._resolve_gateway_version() == "2.1.0"
    assert looked_up == ["agent-workspace-harness"]

    looked_up.clear()
    monkeypatch.setattr(ops_module.metadata, "version", _resolver({"agent-workspace": "9.9.9"}))
    assert ops_module._resolve_gateway_version() == "9.9.9"
    assert looked_up == ["agent-workspace-harness", "alpha", "agent-workspace"]


def test_ops_status_returns_runtime_health(monkeypatch) -> None:
    monkeypatch.setattr(ops_module, "get_gateway_config", lambda: SimpleNamespace(enable_docs=False))

    with _client() as client:
        response = client.get("/api/ops/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "agent-workspace-gateway"
    assert payload["status"] == "ok"
    assert isinstance(payload["uptime_seconds"], int)
    assert payload["uptime_seconds"] >= 0
    # Must be a parseable UTC timestamp.
    assert datetime.fromisoformat(payload["time_utc"]).tzinfo is not None
    assert payload["docs_enabled"] is False


def test_ops_status_reports_docs_enabled_when_docs_are_on(monkeypatch) -> None:
    monkeypatch.setattr(ops_module, "get_gateway_config", lambda: SimpleNamespace(enable_docs=True))

    with _client() as client:
        response = client.get("/api/ops/status")

    assert response.status_code == 200
    assert response.json()["docs_enabled"] is True
