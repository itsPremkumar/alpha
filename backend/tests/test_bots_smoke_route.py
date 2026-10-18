"""The bot smoke probe (2.2): a real bot-scoped run, observed and recorded.

Pinned here:

1. ``POST /api/bots/{name}/smoke`` answers with ONE observed terminal result
   of a real bot-scoped run — ``alive`` is true only when that run reached
   ``success``, never inferred and never fabricated.
2. The outcome (with the run id) is persisted on the profile, so the badge
   progresses ``created · unverified`` (no smoke) → ``smoke passed`` on a
   plain GET, and the route is admin-gated like every other bot mutation.
3. The runner binds the bot through ``assistant_id`` on the bot's canonical
   chat thread and goes through the scheduler's internal launcher — the same
   admission path scheduled work uses.
4. A stuck run is interrupted and reported ``timeout``; an unobservable run is
   reported ``failed``. Neither can read as passed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

import alpha.bots.registry as bot_reg
from alpha.bots.dm import canonical_bot_chat_id
from alpha.bots.registry import BotRegistry
from alpha.runtime.runs.schemas import RunStatus
from app.gateway.routers import bots as bots_router


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """No test in this file may touch the real ALPHA_HOME roster."""

    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None
    yield
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None


async def _noop_admin(*_args, **_kwargs):
    return None


@pytest.fixture()
def registry(tmp_path) -> BotRegistry:
    return BotRegistry(tmp_path / "roster.json")


@pytest.fixture()
def client(monkeypatch, registry) -> TestClient:
    monkeypatch.setattr(bots_router, "require_admin_user", _noop_admin)
    monkeypatch.setattr(bots_router, "_registry", lambda: registry)
    app = make_authed_test_app()
    app.include_router(bots_router.router)
    return TestClient(app)


def _seed(registry: BotRegistry, name: str = "quill"):
    return registry.get_or_create(name, display_name="Quill", role="Write scheduled social posts")


def _stub_smoke(monkeypatch, **overrides) -> None:
    payload = {
        "bot_name": "quill",
        "run_id": "r-smoke-1",
        "thread_id": "bot-chat-quill",
        "status": "passed",
        "observed_status": "success",
        "detail": None,
        "answered": True,
        "reply": "Quill standing by.",
        "message_count": 3,
    }
    payload.update(overrides)

    async def _stub(name, request):
        return dict(payload)

    monkeypatch.setattr(bots_router, "_run_bot_smoke", _stub)


class _FakeRunManager:
    def __init__(self, record):
        self._record = record
        self.cancelled: list[tuple[str, str]] = []

    async def get(self, run_id: str):
        return self._record

    async def cancel(self, run_id: str, *, action: str = "interrupt"):
        self.cancelled.append((run_id, action))
        return None


def _fake_request(run_mgr) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(run_manager=run_mgr)),
        state=SimpleNamespace(user=SimpleNamespace(id="u-1")),
    )


def _record(**overrides) -> SimpleNamespace:
    payload = {
        "status": RunStatus.success,
        "error": None,
        "stop_reason": None,
        "last_ai_message": "Quill standing by.",
        "message_count": 3,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_the_route_runs_and_persists_a_passing_smoke(client, monkeypatch, registry):
    """The full badge progression: unverified at creation → passed after a smoke."""

    _seed(registry)
    before = client.get("/api/bots/quill").json()
    assert before["smoke"] is None and before["alive"] is False, "a fresh bot is created · unverified"

    _stub_smoke(monkeypatch)
    response = client.post("/api/bots/quill/smoke")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["alive"] is True
    assert body["smoke"]["status"] == "passed"
    assert body["smoke"]["run_id"] == "r-smoke-1"
    assert body["reply"] == "Quill standing by."

    persisted = registry.get_bot("quill").metadata["smoke"]
    assert persisted["run_id"] == "r-smoke-1", "the outcome survives on the profile"

    after = client.get("/api/bots/quill").json()
    assert after["alive"] is True
    assert after["smoke"]["run_id"] == "r-smoke-1", "the badge follows a plain GET"


def test_a_failed_smoke_is_recorded_but_never_alive(client, monkeypatch, registry):
    _seed(registry)
    _stub_smoke(monkeypatch, status="failed", observed_status="error", detail="model refused")

    body = client.post("/api/bots/quill/smoke").json()
    assert body["ok"] is True, "a terminal verdict WAS observed, even though it failed"
    assert body["alive"] is False
    assert body["smoke"]["status"] == "failed"
    assert body["smoke"]["detail"] == "model refused"
    assert registry.get_bot("quill").metadata["smoke"]["status"] == "failed"
    assert client.get("/api/bots/quill").json()["alive"] is False


def test_a_timed_out_smoke_answers_ok_false_and_never_alive(client, monkeypatch, registry):
    _seed(registry)
    _stub_smoke(monkeypatch, status="timeout", observed_status="running", detail="still running after 180s")

    body = client.post("/api/bots/quill/smoke").json()
    assert body["ok"] is False, "a run that never reached a terminal state is not an observed result"
    assert body["alive"] is False
    assert body["smoke"]["status"] == "timeout"


def test_the_smoke_route_404s_for_an_unknown_bot(client):
    assert client.post("/api/bots/ghost/smoke").status_code == 404


def test_the_smoke_route_is_admin_gated(registry, monkeypatch):
    monkeypatch.setattr(bots_router, "_registry", lambda: registry)
    app = make_authed_test_app()
    app.include_router(bots_router.router)
    client = TestClient(app)
    assert client.post("/api/bots/quill/smoke").status_code == 403


@pytest.mark.asyncio
async def test_the_runner_binds_the_bot_on_its_canonical_thread(monkeypatch):
    """The launch call shape: internal launcher, assistant_id=bot, canonical thread."""

    captured: dict = {}

    async def _fake_launch(**kwargs):
        captured.update(kwargs)
        return {"run_id": "r-1", "thread_id": kwargs["thread_id"]}

    from app.gateway import services as gateway_services

    monkeypatch.setattr(gateway_services, "launch_scheduled_thread_run", _fake_launch)
    run_mgr = _FakeRunManager(_record())

    result = await bots_router._run_bot_smoke("quill", _fake_request(run_mgr))

    assert captured["assistant_id"] == "quill"
    assert captured["thread_id"] == canonical_bot_chat_id("quill") == "bot-chat-quill"
    assert "Smoke test" in captured["prompt"]
    assert captured["owner_user_id"] == "u-1"
    assert captured["metadata"] == {"bot_mode_smoke": True}
    assert result["status"] == "passed"
    assert result["answered"] is True
    assert result["reply"] == "Quill standing by."


@pytest.mark.asyncio
async def test_a_stuck_run_is_timed_out_and_interrupted(monkeypatch):
    async def _fake_launch(**kwargs):
        return {"run_id": "r-9", "thread_id": kwargs["thread_id"]}

    from app.gateway import services as gateway_services

    monkeypatch.setattr(gateway_services, "launch_scheduled_thread_run", _fake_launch)
    monkeypatch.setattr(bots_router, "_SMOKE_TIMEOUT_SECONDS", 0.0)
    run_mgr = _FakeRunManager(_record(status=RunStatus.running, last_ai_message=None, message_count=0))

    result = await bots_router._run_bot_smoke("quill", _fake_request(run_mgr))

    assert result["status"] == "timeout"
    assert result["observed_status"] == "running"
    assert "interrupted" in result["detail"]
    assert run_mgr.cancelled == [("r-9", "interrupt")], "a stuck probe is interrupted, not leaked"


@pytest.mark.asyncio
async def test_an_unobservable_run_is_a_failed_probe(monkeypatch):
    async def _fake_launch(**kwargs):
        return {"run_id": "r-none", "thread_id": kwargs["thread_id"]}

    from app.gateway import services as gateway_services

    monkeypatch.setattr(gateway_services, "launch_scheduled_thread_run", _fake_launch)

    class _EmptyManager:
        async def get(self, run_id: str):
            return None

    result = await bots_router._run_bot_smoke("quill", _fake_request(_EmptyManager()))

    assert result["status"] == "failed"
    assert result["observed_status"] == "absent"
    assert result["answered"] is False
