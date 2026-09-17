import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.gateway.auth import jwt
from app.gateway.auth.models import User
from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.routers import jobs
from agent_workspace.config.authorization_config import AuthorizationConfig
from agent_workspace.jobs import ExternalJobRunner, JobResult, JobSpec, JobStatus, PersistentJobQueue

pytestmark = [pytest.mark.asyncio, pytest.mark.no_auto_user]


@pytest.fixture
def jobs_app(monkeypatch):
    users = {name: User(email=f"{name}@example.com", system_role=role) for name, role in (("alice", "admin"), ("bob", "admin"), ("reader", "user"))}
    provider = SimpleNamespace(get_user=AsyncMock(side_effect=lambda user_id: next((user for user in users.values() if str(user.id) == user_id), None)))
    monkeypatch.setattr("app.gateway.deps.get_local_provider", lambda: provider)
    monkeypatch.setattr(jwt, "get_auth_config", lambda: SimpleNamespace(jwt_secret="jobs-test-signing-secret-not-for-production", token_expiry_days=1))
    monkeypatch.setattr("app.gateway.authz._get_route_authorization_config", lambda: AuthorizationConfig())
    monkeypatch.setattr("app.gateway.auth_middleware.is_auth_disabled", lambda: False)
    monkeypatch.setattr("app.gateway.auth_middleware.is_valid_internal_auth_token", lambda token: token == "test-internal")
    config = SimpleNamespace(sandbox=SimpleNamespace(use="agent_workspace.sandbox.local:LocalSandboxProvider", allow_host_bash=True))
    monkeypatch.setattr("agent_workspace.jobs.runner.get_app_config", lambda: config)
    queue = PersistentJobQueue()
    runner = ExternalJobRunner(queue)
    monkeypatch.setattr(jobs, "_GLOBAL_QUEUE", queue)
    monkeypatch.setattr(jobs, "_GLOBAL_RUNNER", runner)
    app = FastAPI()
    app.include_router(jobs.router)
    app.add_middleware(AuthMiddleware)
    return SimpleNamespace(app=app, users=users, queue=queue, runner=runner, config=config)


def session_headers(state, name):
    return {"cookie": f"access_token={jwt.create_access_token(str(state.users[name].id))}"}


@pytest.mark.parametrize("method,path", [("POST", ""), ("GET", ""), ("GET", "/unknown"), ("GET", "/unknown/logs"), ("POST", "/unknown/cancel")])
async def test_jobs_require_authentication(jobs_app, method, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=jobs_app.app), base_url="http://test") as client:
        response = await client.request(method, f"/api/jobs{path}", json={"command": ["must-not-run"]})
    assert response.status_code == 401
    assert jobs_app.queue.list_jobs() == []


async def test_non_operator_cannot_submit_even_with_host_gate(jobs_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=jobs_app.app), base_url="http://test") as client:
        response = await client.post("/api/jobs", headers=session_headers(jobs_app, "reader"), json={"command": ["must-not-run"], "owner_id": str(jobs_app.users["alice"].id)})
    assert response.status_code == 403
    assert jobs_app.queue.list_jobs() == []


@pytest.mark.parametrize("provider", ["agent_workspace.sandbox.local:LocalSandboxProvider", "agent_workspace.community.aio_sandbox:AioSandboxProvider"])
@pytest.mark.parametrize("gate", [False, None, "true"])
async def test_host_gate_is_explicit_for_every_provider(jobs_app, provider, gate):
    jobs_app.config.sandbox.use = provider
    jobs_app.config.sandbox.allow_host_bash = gate
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=jobs_app.app), base_url="http://test") as client:
        response = await client.post("/api/jobs", headers=session_headers(jobs_app, "alice"), json={"command": ["must-not-run"]})
    assert response.status_code == 403
    assert jobs_app.queue.list_jobs() == []


async def test_config_failure_denies_submission(jobs_app, monkeypatch):
    def fail_config():
        raise RuntimeError("private configuration detail")

    monkeypatch.setattr("agent_workspace.jobs.runner.get_app_config", fail_config)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=jobs_app.app), base_url="http://test") as client:
        response = await client.post("/api/jobs", headers=session_headers(jobs_app, "alice"), json={"command": ["must-not-run"]})
    assert response.status_code == 403
    assert "private configuration" not in response.text
    assert jobs_app.queue.list_jobs() == []


async def test_synthetic_admin_and_internal_cannot_submit(jobs_app, monkeypatch):
    monkeypatch.setattr("app.gateway.auth_middleware.is_auth_disabled", lambda: True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=jobs_app.app), base_url="http://test") as client:
        for headers in ({}, {"X-DeerFlow-Internal-Token": "test-internal"}):
            response = await client.post("/api/jobs", headers=headers, json={"command": ["must-not-run"]})
            assert response.status_code == 403
    assert jobs_app.queue.list_jobs() == []


async def test_owner_scoping_and_authorized_submission(jobs_app, monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(jobs_app.runner, "execute_spec", execute)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=jobs_app.app), base_url="http://test") as client:
        alice = session_headers(jobs_app, "alice")
        bob = session_headers(jobs_app, "bob")
        response = await client.post("/api/jobs", headers=alice, json={"command": ["test-command"], "priority": "critical", "owner_id": str(jobs_app.users["bob"].id), "user_id": str(jobs_app.users["bob"].id)})
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        assert jobs_app.queue.get_spec(job_id).owner_id == str(jobs_app.users["alice"].id)
        jobs_app.queue.complete_job(JobResult(job_id=job_id, status=JobStatus.RUNNING, stdout="private output"))
        jobs_app.queue.enqueue(JobSpec(command=["legacy-unowned"]))
        jobs_app.queue.enqueue(JobSpec(command=["other-owner"], owner_id=str(jobs_app.users["bob"].id)))
        own = await client.get("/api/jobs?limit=1&owner_id=spoof", headers=alice)
        assert [row["job_id"] for row in own.json()] == [job_id]
        assert (await client.get(f"/api/jobs/{job_id}", headers=alice)).status_code == 200
        assert (await client.get(f"/api/jobs/{job_id}/logs", headers=alice)).json()["stdout"] == "private output"
        for method, suffix in (("GET", ""), ("GET", "/logs"), ("POST", "/cancel")):
            denied = await client.request(method, f"/api/jobs/{job_id}{suffix}", headers=bob)
            missing = await client.request(method, f"/api/jobs/unknown{suffix}", headers=bob)
            assert denied.status_code == missing.status_code == 404
        assert not jobs_app.queue.is_cancelled(job_id)
        assert (await client.post(f"/api/jobs/{job_id}/cancel", headers=alice)).status_code == 200
    await asyncio.gather(*jobs_app.runner._running_tasks.values(), return_exceptions=True)


async def test_authorized_asgi_job_completes(jobs_app):
    async def exercise():
        finished = asyncio.Event()
        jobs_app.queue.add_listener(lambda job_id, status: finished.set() if status == JobStatus.COMPLETED else None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=jobs_app.app), base_url="http://test") as client:
            headers = session_headers(jobs_app, "alice")
            response = await client.post("/api/jobs", headers=headers, json={"command": [sys.executable, "-c", "print('gateway job')"]})
            assert response.status_code == 200
            job_id = response.json()["job_id"]
            await asyncio.wait_for(finished.wait(), timeout=10)
            logs = await client.get(f"/api/jobs/{job_id}/logs", headers=headers)
            assert logs.json()["stdout"].strip() == "gateway job"
            assert (await client.post(f"/api/jobs/{job_id}/cancel", headers=headers)).status_code == 404

    def run():
        factory = asyncio.ProactorEventLoop if os.name == "nt" else asyncio.new_event_loop
        with asyncio.Runner(loop_factory=factory) as runner:
            runner.run(exercise())

    await asyncio.to_thread(run)


@pytest.mark.parametrize("method,path", [("POST", ""), ("GET", ""), ("GET", "/unknown"), ("GET", "/unknown/logs"), ("POST", "/unknown/cancel")])
async def test_pat_cannot_access_jobs(jobs_app, monkeypatch, method, path):
    monkeypatch.setattr("app.gateway.auth.pat.authenticate_pat", AsyncMock(return_value=(jobs_app.users["alice"], frozenset({"runs:create", "runs:read", "runs:cancel"}))))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=jobs_app.app), base_url="http://test") as client:
        response = await client.request(method, f"/api/jobs{path}", headers={"Authorization": "Bearer test-pat"}, json={"command": ["must-not-run"]})
        assert response.status_code == 403
    assert jobs_app.queue.list_jobs() == []


async def test_operator_route_permission_denial(jobs_app, monkeypatch):
    monkeypatch.setattr("app.gateway.auth_middleware.resolve_route_permissions", AsyncMock(return_value=[]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=jobs_app.app), base_url="http://test") as client:
        response = await client.post("/api/jobs", headers=session_headers(jobs_app, "alice"), json={"command": ["must-not-run"]})
    assert response.status_code == 403
    assert jobs_app.queue.list_jobs() == []


async def test_router_without_middleware_still_denies_anonymous(jobs_app):
    app = FastAPI()
    app.include_router(jobs.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/jobs", json={"command": ["must-not-run"]})).status_code == 401
        assert (await client.get("/api/jobs")).status_code == 401
