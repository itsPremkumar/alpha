"""``POST /api/bots`` — the transactional forge as a Gateway route.

The route exists so the bot-creation dialog (frontend
``components/bots/BotCreateDialog.tsx``) hits the REAL forge instead of a
second, optimistic create path. What is pinned here:

1. a create forges through ``forge_bot`` over the live registry and answers the
   structured result (steps, reply, elapsed);
2. a successful build with no smoke probe is ``ok: true`` but ``alive: false``
   — "created · unverified", never "alive";
3. a taken name is a *result* (``ok: false`` + reason), not an HTTP error: the
   forge's contract is that a failure is a value the caller can read, and
   translating it into an exception would drop ``steps``/``rollback_report``;
4. the request contract is validated at the edge — handle shape and "role or
   template present" are 422s raised BEFORE anything is created.
"""

from __future__ import annotations

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

import alpha.bots.registry as bot_reg
from alpha.bots.registry import BotRegistry
from alpha.bots.survey import clear_survey_cache
from app.gateway.routers import bots


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Same isolation discipline as ``test_bots_forge``: no real ALPHA_HOME."""

    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None
    clear_survey_cache()
    yield
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None
    clear_survey_cache()


async def _noop_admin(*_args, **_kwargs):
    return None


@pytest.fixture()
def registry(tmp_path) -> BotRegistry:
    return BotRegistry(tmp_path / "roster.json")


@pytest.fixture()
def client(monkeypatch, registry) -> TestClient:
    monkeypatch.setattr(bots, "require_admin_user", _noop_admin)
    monkeypatch.setattr(bots, "_registry", lambda: registry)
    app = make_authed_test_app()
    app.include_router(bots.router)
    return TestClient(app)


def _create(client: TestClient, **overrides):
    payload = {"name": "quill", "role": "write scheduled social posts", "workspace_survey": False}
    payload.update(overrides)
    return client.post("/api/bots", json=payload)


def test_a_create_forges_through_the_registry_and_answers_the_result(client, registry):
    response = _create(client)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True, body
    assert body["name"] == "quill"
    assert "profile" in body["steps"]
    assert body["rolled_back"] is False
    assert registry.get_bot("quill") is not None, "the forge really claimed a roster entry"
    assert body["elapsed_seconds"] >= 0.0


def test_a_successful_build_is_reported_unverified_not_alive(client):
    """The honesty pin: no probe ran, so `alive` must be false and `reply` says so."""

    body = _create(client).json()
    assert body["ok"] is True
    assert body["smoke_tested"] is False
    assert body["alive"] is False, "a build with no smoke probe is NEVER reported alive"
    assert "unverified" in body["reply"].lower()


def test_a_taken_name_is_a_result_not_an_http_error(client, registry):
    assert _create(client).json()["ok"] is True
    second = _create(client)
    assert second.status_code == 200, "a forge refusal is a RESULT, not a transport error"
    body = second.json()
    assert body["ok"] is False
    assert "already exists" in body["reason"]
    assert body["rolled_back"] is False
    assert registry.get_bot("quill") is not None, "the original bot survives the refused second create"


def test_an_unknown_template_is_refused_by_the_forge(client, registry):
    body = _create(client, name="mystery", template="not-a-template").json()
    assert body["ok"] is False
    assert "template" in body["reason"].lower()
    assert registry.get_bot("mystery") is None, "a refused forge leaves nothing behind"


def test_an_invalid_handle_is_rejected_at_the_edge(client):
    response = _create(client, name="Not A Handle!")
    assert response.status_code == 422
    assert "must be 1-64 chars" in response.json()["detail"]


def test_a_create_without_role_or_template_is_rejected_at_the_edge(client):
    response = _create(client, name="roleless", role="")
    assert response.status_code == 422
    assert "role" in response.json()["detail"].lower()
