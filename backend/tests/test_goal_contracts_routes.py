from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.auth import jwt
from app.gateway.auth.models import User
from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.routers import goal_contracts
from alpha.config.authorization_config import AuthorizationConfig
from alpha.config.paths import Paths
from alpha.goals import GoalStore

pytestmark = pytest.mark.no_auto_user
BASE = "/api/goals/contracts"
ROUTES = [
    ("GET", "", None),
    ("POST", "", {"objective": "Ship a release"}),
    ("GET", "/unknown", None),
    ("POST", "/unknown/plans", {"content": {"steps": ["test"]}}),
    ("POST", "/unknown/plans/1/approve", None),
    ("POST", "/unknown/attempts", {"plan_id": "unknown", "intent": "test"}),
    ("POST", "/unknown/attempts/unknown/transition", {"status": "running"}),
]


@pytest.fixture
def goals_app(monkeypatch, tmp_path):
    users = {name: User(email=f"{name}@example.com", system_role="user") for name in ("alice", "bob")}
    provider = SimpleNamespace(get_user=AsyncMock(side_effect=lambda user_id: next((user for user in users.values() if str(user.id) == user_id), None)))
    monkeypatch.setattr("app.gateway.deps.get_local_provider", lambda: provider)
    monkeypatch.setattr(jwt, "get_auth_config", lambda: SimpleNamespace(jwt_secret="goals-test-signing-secret-not-for-production", token_expiry_days=1))
    monkeypatch.setattr("app.gateway.authz._get_route_authorization_config", lambda: AuthorizationConfig())
    monkeypatch.setattr("app.gateway.auth_middleware.is_auth_disabled", lambda: False)
    monkeypatch.setattr("app.gateway.auth_middleware.is_valid_internal_auth_token", lambda token: False)
    paths = Paths(base_dir=tmp_path)
    monkeypatch.setattr(goal_contracts, "get_paths", lambda: paths)
    monkeypatch.setattr(goal_contracts, "_stores", {})
    app = FastAPI()
    app.include_router(goal_contracts.router)
    app.add_middleware(AuthMiddleware)
    with TestClient(app) as client:
        yield SimpleNamespace(app=app, client=client, users=users, paths=paths)


def session_headers(state, name="alice"):
    return {"cookie": f"access_token={jwt.create_access_token(str(state.users[name].id))}"}


@pytest.mark.parametrize("method,path,payload", ROUTES)
def test_requires_authentication(goals_app, method, path, payload):
    response = goals_app.client.request(method, BASE + path, json=payload)
    assert response.status_code == 401
    assert goal_contracts._stores == {}


@pytest.mark.parametrize("method,path,payload", ROUTES)
def test_pat_is_forbidden(goals_app, monkeypatch, method, path, payload):
    monkeypatch.setattr("app.gateway.auth.pat.authenticate_pat", AsyncMock(return_value=(goals_app.users["alice"], frozenset({"runs:create", "runs:read", "runs:cancel"}))))
    response = goals_app.client.request(method, BASE + path, headers={"Authorization": "Bearer test-pat"}, json=payload)
    assert response.status_code == 403
    assert goal_contracts._stores == {}


@pytest.mark.parametrize("method,path,payload", ROUTES)
def test_permission_denial(goals_app, monkeypatch, method, path, payload):
    monkeypatch.setattr("app.gateway.auth_middleware.resolve_route_permissions", AsyncMock(return_value=[]))
    response = goals_app.client.request(method, BASE + path, headers=session_headers(goals_app), json=payload)
    assert response.status_code == 403
    assert goal_contracts._stores == {}


@pytest.mark.parametrize("method,path,payload", ROUTES)
def test_router_without_middleware_denies_anonymous(goals_app, method, path, payload):
    app = FastAPI()
    app.include_router(goal_contracts.router)
    with TestClient(app) as client:
        assert client.request(method, BASE + path, json=payload).status_code == 401
    assert goal_contracts._stores == {}


def create_contract(state, name="alice"):
    response = state.client.post(BASE, headers=session_headers(state, name), json={"objective": "Ship a release"})
    assert response.status_code == 201
    return response.json()


def create_plan(state, contract_id, name="alice"):
    response = state.client.post(f"{BASE}/{contract_id}/plans", headers=session_headers(state, name), json={"content": {"steps": ["test", "ship"]}})
    assert response.status_code == 201
    return response.json()


def create_attempt(state, contract_id, plan_id):
    response = state.client.post(f"{BASE}/{contract_id}/attempts", headers=session_headers(state), json={"plan_id": plan_id, "intent": "Run tests"})
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
def test_lifecycle_and_persistence(goals_app, terminal):
    client = goals_app.client
    headers = session_headers(goals_app)
    contract = create_contract(goals_app)
    path = f"{BASE}/{contract['id']}"
    owner_id = str(goals_app.users["alice"].id)
    assert contract["owner_id"] == owner_id
    assert client.get(path, headers=headers).json() == contract
    assert client.get(BASE, headers=headers).json() == [contract]
    plan = create_plan(goals_app, contract["id"])
    assert plan["version"] == 1
    assert plan["status"] == "draft"
    assert client.post(path + "/attempts", headers=headers, json={"plan_id": plan["id"], "intent": "test"}).status_code == 409
    approved = client.post(path + "/plans/1/approve", headers=headers)
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    attempt = create_attempt(goals_app, contract["id"], plan["id"])
    assert attempt["status"] == "pending"
    transition = f"{path}/attempts/{attempt['id']}/transition"
    assert client.post(transition, headers=headers, json={"status": "succeeded"}).status_code == 409
    for status in ("running", terminal):
        response = client.post(transition, headers=headers, json={"status": status})
        assert response.status_code == 200
        assert response.json()["status"] == status
    assert client.post(transition, headers=headers, json={"status": "running"}).status_code == 409
    second_plan = create_plan(goals_app, contract["id"])
    assert second_plan["version"] == 2
    assert client.post(path + "/plans/2/approve", headers=headers).status_code == 200
    storage_dir = goals_app.paths.user_dir(owner_id) / "goal_contracts"
    persisted = GoalStore(storage_dir)
    assert persisted.get_contract(contract["id"], owner_id=owner_id).objective == contract["objective"]
    assert persisted.get_plan(plan["id"], owner_id=owner_id).status == "superseded"
    assert persisted.get_attempt(attempt["id"], owner_id=owner_id).status == terminal
    assert len(goal_contracts._stores) == 1
    goal_contracts._stores.clear()
    assert client.get(path, headers=headers).json() == contract


def test_cross_owner_and_cross_contract_records_are_not_found(goals_app):
    client = goals_app.client
    alice = session_headers(goals_app)
    bob = session_headers(goals_app, "bob")
    contract = create_contract(goals_app)
    path = f"{BASE}/{contract['id']}"
    plan = create_plan(goals_app, contract["id"])
    assert client.post(path + "/plans/1/approve", headers=alice).status_code == 200
    attempt = create_attempt(goals_app, contract["id"], plan["id"])
    routes = [
        ("GET", "", None),
        ("POST", "/plans", {"content": {}}),
        ("POST", "/plans/1/approve", None),
        ("POST", "/attempts", {"plan_id": plan["id"], "intent": "test"}),
        ("POST", f"/attempts/{attempt['id']}/transition", {"status": "running"}),
    ]
    for method, suffix, payload in routes:
        foreign = client.request(method, path + suffix, headers=bob, json=payload)
        missing = client.request(method, BASE + "/unknown" + suffix, headers=bob, json=payload)
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()
    assert client.get(BASE, headers=bob, params={"owner_id": contract["owner_id"]}).json() == []
    for name in ("alice", "bob"):
        other = create_contract(goals_app, name)
        other_path = f"{BASE}/{other['id']}"
        headers = session_headers(goals_app, name)
        assert client.post(other_path + "/attempts", headers=headers, json={"plan_id": plan["id"], "intent": "test"}).status_code == 404
        assert client.post(other_path + f"/attempts/{attempt['id']}/transition", headers=headers, json={"status": "running"}).status_code == 404
    assert client.post(path + "/plans/999/approve", headers=alice).status_code == 404
    assert client.post(path + "/attempts/unknown/transition", headers=alice, json={"status": "running"}).status_code == 404


@pytest.mark.parametrize("field", ["owner_id", "user_id", "storage_dir"])
def test_body_cannot_supply_owner_or_storage(goals_app, field):
    response = goals_app.client.post(BASE, headers=session_headers(goals_app), json={"objective": "test", field: str(goals_app.users["bob"].id)})
    assert response.status_code == 422
    assert goal_contracts._stores == {}


@pytest.mark.parametrize("objective", ["", "   ", 42, None])
def test_invalid_objective(goals_app, objective):
    assert goals_app.client.post(BASE, headers=session_headers(goals_app), json={"objective": objective}).status_code == 422


def test_storage_failure_is_not_reported_as_success(goals_app, monkeypatch):
    def fail_save(*args):
        raise OSError("private storage path")

    monkeypatch.setattr(GoalStore, "_save", fail_save)
    response = goals_app.client.post(BASE, headers=session_headers(goals_app), json={"objective": "test"})
    assert response.status_code == 503
    assert "private storage path" not in response.text
    assert goals_app.client.get(BASE, headers=session_headers(goals_app)).json() == []
