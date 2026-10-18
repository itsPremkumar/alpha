"""Bot routines (2.4): one scheduled task bound to the bot, its chat, its name.

Pinned here:

1. ``POST /api/bots/{name}/routines`` writes a scheduled task with
   ``assistant_id=<bot>`` and ``thread_id=canonical_bot_chat_id(bot)`` (opening
   that shared thread record on demand), then records the ``task_id`` on the
   profile routine — so ``GET /api/bots/{name}/chat`` reports the very thread
   the routine's result lands on.
2. A duplicate routine name is REFUSED (409) rather than silently replaced: a
   name never points at two schedules, and the previous task is never
   orphaned behind a quiet replace.
3. ``DELETE`` retires the task first and the record second; a task that is
   launching or running refuses (409) and the routine stays whole, while an
   already-gone task still cleans the dangling record and says so.
4. Schedule validation stays owned by the scheduled-task surface (reached
   through its own route function): a bad schedule is refused there, with no
   task and no routine written.
5. The resolver accepts roster bot names, and the scheduler's launch path
   stamps ``botName`` into the run metadata — the identity a fired routine
   actually runs as.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore
from starlette.middleware.base import BaseHTTPMiddleware

import alpha.bots.registry as bot_reg
from alpha.bots.dm import canonical_bot_chat_id
from alpha.bots.registry import BotRegistry
from alpha.config.app_config import AppConfig, reset_app_config, set_app_config
from alpha.persistence.thread_meta.memory import MemoryThreadMetaStore
from app.gateway import services
from app.gateway.auth_disabled import AUTH_SOURCE_SESSION
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


@pytest.fixture(autouse=True)
def _stub_app_config():
    """Pin config so a developer-local ``config.yaml`` cannot move the
    interval bounds under the schedule-validation tests."""
    set_app_config(AppConfig.model_validate({"sandbox": {"use": "alpha.sandbox.local:LocalSandboxProvider"}}))
    yield
    reset_app_config()


class _AuthSourceStamp(BaseHTTPMiddleware):
    """Stamp the auth source the real ``AuthMiddleware`` sets.

    ``make_authed_test_app``'s stub sets ``state.user``/``state.auth`` only;
    ``get_current_user_from_request`` needs a known ``auth_source`` to accept
    the state user, and the delegated scheduled-task route resolves the caller
    through it. Added last so it runs outermost, before the stub.
    """

    async def dispatch(self, request, call_next):
        request.state.auth_source = AUTH_SOURCE_SESSION
        return await call_next(request)


class FakeScheduledTaskRepo:
    """The subset of ``ScheduledTaskRepository`` the routines flow uses.

    Deliberately signature-faithful to the SQL repo (``create``/``get``/
    ``delete_with_queue_cancellation``), because the routes call it with
    keyword arguments and through the real scheduled-task route function: a
    drift in the real contract fails here instead of hiding behind a
    permissive mock. Records carry the row fields the UI reads.
    """

    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.created: list[str] = []
        self.deleted: list[str] = []

    async def create(
        self,
        *,
        task_id: str,
        user_id: str,
        thread_id: str | None,
        context_mode: str,
        assistant_id: str | None,
        title: str,
        prompt: str,
        schedule_type: str,
        schedule_spec: dict,
        timezone: str,
        next_run_at,
    ) -> dict:
        record = {
            "id": task_id,
            "user_id": user_id,
            "thread_id": thread_id,
            "context_mode": context_mode,
            "assistant_id": assistant_id,
            "title": title,
            "prompt": prompt,
            "schedule_type": schedule_type,
            "schedule_spec": dict(schedule_spec),
            "timezone": timezone,
            "status": "enabled",
            "next_run_at": next_run_at.isoformat() if next_run_at is not None else None,
            "last_run_at": None,
            "last_run_id": None,
            "last_error": None,
            "run_count": 0,
        }
        self.tasks[task_id] = record
        self.created.append(task_id)
        return dict(record)

    async def get(self, task_id: str, *, user_id: str) -> dict | None:
        task = self.tasks.get(task_id)
        if task is None or task["user_id"] != user_id:
            return None
        return dict(task)

    async def delete_with_queue_cancellation(self, task_id: str, *, user_id: str, error: str, now) -> str:
        task = self.tasks.get(task_id)
        if task is None or task["user_id"] != user_id:
            return "not_found"
        if task.get("_executing"):
            return "executing"
        del self.tasks[task_id]
        self.deleted.append(task_id)
        return "deleted"

    async def list_by_user(self, user_id: str) -> list[dict]:
        return [dict(t) for t in self.tasks.values() if t["user_id"] == user_id]


async def _noop_admin(*_args, **_kwargs):
    return None


@pytest.fixture()
def registry(_isolated_home) -> BotRegistry:
    """The process-wide roster, isolated to ``tmp_path`` by ``ALPHA_HOME``.

    One registry, not two: the bots routes and the run-identity resolver
    (``resolve_roster_bot``) must read the same roster, exactly as in
    production — a bot the route can see but the resolver cannot would be a
    wiring bug, not a test artifact.
    """
    from alpha.bots.registry import get_bot_registry

    return get_bot_registry()


def _seed(registry: BotRegistry, name: str = "quill", **kwargs):
    return registry.get_or_create(name, display_name="Quill", role="Write scheduled social posts", **kwargs)


@pytest.fixture()
def repo() -> FakeScheduledTaskRepo:
    return FakeScheduledTaskRepo()


@pytest.fixture()
def thread_store() -> MemoryThreadMetaStore:
    return MemoryThreadMetaStore(InMemoryStore())


def _make_client(monkeypatch, registry, repo, thread_store, *, patch_admin: bool = True) -> TestClient:
    if patch_admin:
        monkeypatch.setattr(bots_router, "require_admin_user", _noop_admin)
    monkeypatch.setattr(bots_router, "_registry", lambda: registry)
    app = make_authed_test_app()
    app.add_middleware(_AuthSourceStamp)
    app.state.thread_store = thread_store
    app.state.scheduled_task_repo = repo
    app.include_router(bots_router.router)
    return TestClient(app)


@pytest.fixture()
def client(monkeypatch, registry, repo, thread_store) -> TestClient:
    return _make_client(monkeypatch, registry, repo, thread_store)


def _routine_body(**overrides) -> dict:
    body = {
        "name": "standup",
        "action": "Post the standup summary into this chat.",
        "schedule_type": "interval",
        "schedule_spec": {"every_seconds": 600},
        "timezone": "UTC",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Create — one task, bound to the bot and its own chat
# ---------------------------------------------------------------------------


def test_create_binds_the_task_to_the_bot_and_its_chat(client, registry, repo):
    _seed(registry)

    response = client.post("/api/bots/quill/routines", json=_routine_body())

    assert response.status_code == 200, response.text
    body = response.json()
    routine = body["routine"]
    task = body["task"]
    assert task["assistant_id"] == "quill"
    assert task["thread_id"] == canonical_bot_chat_id("quill") == "bot-chat-quill"
    assert task["context_mode"] == "reuse_thread"
    assert task["user_id"] == routine["owner_user_id"]
    for key in ("next_run_at", "last_run_at", "last_error", "run_count", "status"):
        assert key in task, f"the task record must carry {key!r} for the UI"
    assert routine["task_id"] == task["id"]
    assert routine["schedule"] == "every 600s"
    assert routine["enabled"] is True
    assert routine["schedule_type"] == "interval"
    assert routine["timezone"] == "UTC"

    # The profile is the record of truth for the routine side of the pair.
    stored = registry.get_bot("quill").routines
    assert [r["name"] for r in stored] == ["standup"]
    assert stored[0]["task_id"] == task["id"]


def test_create_opens_the_shared_chat_thread_without_an_owner(client, registry, repo, thread_store):
    _seed(registry)

    client.post("/api/bots/quill/routines", json=_routine_body())

    thread_id = canonical_bot_chat_id("quill")
    row = asyncio.run(thread_store.get(thread_id, user_id=None))
    assert row is not None, "the canonical thread record must exist for reuse_thread tasks"
    assert row["user_id"] is None, "the canonical chat is one shared conversation, not a personal thread"
    assert row["metadata"].get("botName") == "quill"


def test_the_acceptance_thread_is_the_one_the_chat_route_reports(client, registry, repo):
    """Acceptance, in its honest form: the routine's next result lands on the
    thread the bot's chat surface reports — not merely on ``assistant_id``."""
    _seed(registry)

    task = client.post("/api/bots/quill/routines", json=_routine_body()).json()["task"]
    chat = client.get("/api/bots/quill/chat").json()

    assert task["thread_id"] == chat["canonical_thread_id"]


def test_duplicate_routine_names_are_refused_not_replaced(client, registry, repo):
    _seed(registry)
    first = client.post("/api/bots/quill/routines", json=_routine_body())
    assert first.status_code == 200, first.text
    original_task = first.json()["task"]["id"]

    response = client.post("/api/bots/quill/routines", json=_routine_body(action="Replaced body"))

    assert response.status_code == 409, response.text
    assert "Delete it first" in response.json()["detail"]
    # No second task, no orphan behind a quiet replace, and the record intact.
    assert list(repo.tasks) == [original_task]
    assert len(registry.get_bot("quill").routines) == 1
    assert registry.get_bot("quill").routines[0]["action"] == "Post the standup summary into this chat."


def test_cron_schedules_normalize_and_label_the_routine(client, registry, repo):
    _seed(registry)

    body = client.post(
        "/api/bots/quill/routines",
        json=_routine_body(name="digest", schedule_type="cron", schedule_spec={"cron": "*/5 * * * *"}),
    ).json()

    assert body["task"]["schedule_spec"]["cron"] == "*/5 * * * *"
    assert body["routine"]["schedule"] == "cron: */5 * * * *"


def test_a_bad_schedule_is_refused_by_the_one_owner_with_no_task_or_routine(client, registry, repo):
    """The scheduled-task surface owns schedule validation, so a bad schedule
    is refused there — through the same route function — and no task and no
    routine are written. (The bot's own chat thread may be opened first: it is
    the shared conversation, and opening the bot page creates it anyway.)"""
    _seed(registry)

    unsupported = client.post("/api/bots/quill/routines", json=_routine_body(schedule_type="hourly"))
    assert unsupported.status_code == 422, unsupported.text
    too_fast = client.post("/api/bots/quill/routines", json=_routine_body(schedule_spec={"every_seconds": 1}))
    assert too_fast.status_code == 422, too_fast.text

    assert repo.tasks == {}
    assert registry.get_bot("quill").routines == []


def test_unknown_bot_is_404_on_create(client, repo):
    response = client.post("/api/bots/ghost/routines", json=_routine_body())

    assert response.status_code == 404
    assert repo.tasks == {}


# ---------------------------------------------------------------------------
# The joined read — profile routine + live task state
# ---------------------------------------------------------------------------


def test_the_read_joins_live_task_state(client, registry, repo):
    _seed(registry)
    created = client.post("/api/bots/quill/routines", json=_routine_body()).json()
    task_id = created["task"]["id"]
    repo.tasks[task_id]["status"] = "paused"
    repo.tasks[task_id]["last_run_at"] = "2026-10-18T06:00:00+00:00"
    repo.tasks[task_id]["last_error"] = "model call failed"

    body = client.get("/api/bots/quill/routines").json()

    assert body["bot_name"] == "quill"
    entry = body["routines"][0]
    assert entry["task"]["status"] == "paused"
    assert entry["task"]["last_error"] == "model call failed"


def test_a_routine_without_a_task_reports_that_instead_of_inventing_one(client, registry):
    _seed(registry)
    registry.add_routine("quill", "legacy", "cron: 0 * * * *", "Hand-written routine")

    body = client.get("/api/bots/quill/routines").json()

    assert body["routines"][0]["task"] is None
    assert "task_id" not in body["routines"][0]


def test_unknown_bot_is_404_on_the_read(client):
    assert client.get("/api/bots/ghost/routines").status_code == 404


# ---------------------------------------------------------------------------
# Delete — task first, record second
# ---------------------------------------------------------------------------


def test_delete_retires_the_task_and_the_record(client, registry, repo):
    _seed(registry)
    task_id = client.post("/api/bots/quill/routines", json=_routine_body()).json()["task"]["id"]

    response = client.delete("/api/bots/quill/routines/standup")

    assert response.status_code == 200, response.text
    assert response.json() == {"bot_name": "quill", "routine": "standup", "removed": True, "task_deletion": "deleted"}
    assert repo.tasks == {}
    assert repo.deleted == [task_id]
    assert registry.get_bot("quill").routines == []


def test_delete_refuses_while_the_task_is_executing_and_keeps_the_record(client, registry, repo):
    _seed(registry)
    task_id = client.post("/api/bots/quill/routines", json=_routine_body()).json()["task"]["id"]
    repo.tasks[task_id]["_executing"] = True

    response = client.delete("/api/bots/quill/routines/standup")

    assert response.status_code == 409, response.text
    # The routine stays whole — a half-deleted routine would keep firing with
    # no record of why.
    assert len(registry.get_bot("quill").routines) == 1
    assert task_id in repo.tasks


def test_delete_cleans_a_record_whose_task_is_already_gone(client, registry, repo):
    _seed(registry)
    task_id = client.post("/api/bots/quill/routines", json=_routine_body()).json()["task"]["id"]
    del repo.tasks[task_id]  # deleted through the scheduled-tasks surface

    response = client.delete("/api/bots/quill/routines/standup")

    assert response.status_code == 200, response.text
    assert response.json()["task_deletion"] == "not_found"
    assert response.json()["removed"] is True
    assert registry.get_bot("quill").routines == []


def test_unknown_routine_or_bot_is_404_on_delete(client, registry):
    _seed(registry)

    assert client.delete("/api/bots/quill/routines/nope").status_code == 404
    assert client.delete("/api/bots/ghost/routines/standup").status_code == 404


# ---------------------------------------------------------------------------
# Admin gate
# ---------------------------------------------------------------------------


def test_routine_writes_are_admin_gated(registry, repo, thread_store, monkeypatch):
    _seed(registry)
    client = _make_client(monkeypatch, registry, repo, thread_store, patch_admin=False)

    assert client.post("/api/bots/quill/routines", json=_routine_body()).status_code == 403
    assert client.delete("/api/bots/quill/routines/standup").status_code == 403
    assert repo.tasks == {}


# ---------------------------------------------------------------------------
# Identity plumbing: resolver + launch metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_resolver_accepts_roster_bots_and_still_refuses_unknown_names(registry):
    from app.gateway.routers.scheduled_tasks import resolve_scheduled_task_assistant_id

    _seed(registry)

    assert await resolve_scheduled_task_assistant_id("Quill", user_id="u-1") == "quill"
    assert await resolve_scheduled_task_assistant_id("lead_agent", user_id="u-1") == "lead_agent"
    assert await resolve_scheduled_task_assistant_id(None, user_id="u-1") == "lead_agent"
    with pytest.raises(HTTPException) as excinfo:
        await resolve_scheduled_task_assistant_id("no-such-agent-or-bot", user_id="u-1")
    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_scheduled_launch_stamps_the_bot_identity_in_run_metadata(registry, monkeypatch):
    _seed(registry)
    captured: dict = {}

    async def fake_start_run(body, thread_id, request, *, idempotency_key=None):
        captured["body"] = body
        captured["idempotency_key"] = idempotency_key
        return SimpleNamespace(run_id="run-1", thread_id=thread_id)

    monkeypatch.setattr(services, "start_run", fake_start_run)

    result = await services.launch_scheduled_thread_run(
        thread_id=canonical_bot_chat_id("quill"),
        assistant_id="quill",
        prompt="Post the standup summary.",
        app=SimpleNamespace(state=SimpleNamespace()),
    )

    assert result == {"run_id": "run-1", "thread_id": "bot-chat-quill"}
    assert captured["body"].metadata["botName"] == "quill"
    assert captured["idempotency_key"] is None


@pytest.mark.asyncio
async def test_a_non_bot_assistant_id_carries_no_bot_identity(monkeypatch):
    captured: dict = {}

    async def fake_start_run(body, thread_id, request, *, idempotency_key=None):
        captured["body"] = body
        return SimpleNamespace(run_id="run-2", thread_id=thread_id)

    monkeypatch.setattr(services, "start_run", fake_start_run)

    await services.launch_scheduled_thread_run(
        thread_id="thread-plain",
        assistant_id="lead_agent",
        prompt="hello",
        app=SimpleNamespace(state=SimpleNamespace()),
    )

    assert "botName" not in captured["body"].metadata
