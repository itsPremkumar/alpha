"""A memory subsystem that stops learning must render as degraded, with its reason.

Regression class: DeerMem's updater is best-effort by contract, so a failed
extraction returns ``False`` and the run still reports success. That is right
for the run, but it used to leave the *only* evidence in a log line while
``GET /api/memory/status`` answered ``200`` with ``facts: []`` -- byte-identical
to a fresh agent that simply has no memories yet. These tests pin that the
status surface now distinguishes the two, and that it never claims health it
has not observed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.agents.memory.backends.deermem.deermem.core.extraction_health import (
    MemoryUpdateDisabled,
    MemoryUpdateRejected,
    describe_model,
    get_memory_update_health,
    record_attempt,
    record_failure,
    record_success,
    reset_memory_update_health,
    set_extraction_backend,
)
from app.gateway.routers import memory as memory_router


class _AuthenticationError(Exception):
    """Stands in for ``openai.AuthenticationError`` -- only the shape matters."""


@pytest.fixture(autouse=True)
def _clean_health():
    """The record is process-level; no test may inherit another's verdict."""
    reset_memory_update_health()
    yield
    reset_memory_update_health()


@pytest.fixture
def deermem_data_dir(tmp_path, monkeypatch):
    """Isolate DeerMem storage under tmp_path via $DEERMEM_DATA_DIR.

    Mirrors the fixture in ``test_deermem_self_contained.py`` rather than
    widening that file: these tests construct their own DeerMem instances and
    must not share a storage root with another module's tests.
    """
    d = tmp_path / "deermem_data"
    d.mkdir()
    monkeypatch.setenv("DEERMEM_DATA_DIR", str(d))
    yield d


def _status() -> str:
    return get_memory_update_health().status


# ── the record's vocabulary ────────────────────────────────────────────────


def test_fresh_process_is_unknown_not_ok():
    """A boot that has not attempted an update must not look healthy.

    "unknown" is a real state, and collapsing it into "ok" would recreate the
    exact lie this surface exists to prevent.
    """
    set_extraction_backend(model="free:opencode-zen:space-bunny-free", source="host_llm (app default model)")
    assert _status() == "unknown"
    assert get_memory_update_health().as_dict()["reason"] is None


def test_success_is_ok_and_failure_is_degraded_with_the_servers_own_reason():
    set_extraction_backend(model="free:opencode-zen:space-bunny-free", source="host_llm (app default model)")
    record_attempt(scope="thread=t1 user=default agent=-")
    record_success()
    assert _status() == "ok"

    # The exact shape from the live log this change answers.
    record_failure(
        _AuthenticationError("Error code: 401 - {'error': {'message': 'Failed to authenticate request with Clerk', 'code': 401}}"),
        scope="thread=t1 user=default agent=__default__",
    )
    payload = get_memory_update_health().as_dict()
    assert payload["status"] == "degraded"
    assert payload["last_error_type"] == "_AuthenticationError"
    assert "Failed to authenticate request with Clerk" in payload["last_error"]
    # The operator gets the reason, not just a boolean.
    assert "Clerk" in payload["reason"]
    assert payload["last_error_scope"] == "thread=t1 user=default agent=__default__"
    assert payload["consecutive_failures"] == 1


def test_no_model_is_disabled_and_a_later_failure_becomes_the_louder_fact():
    """Extraction with no model is a distinct state.

    While it is the only thing known it reads ``disabled``. Once an attempt has
    actually failed, ``degraded`` is the more urgent true statement -- it is a
    live observation rather than a fact about the config -- but the missing
    model stays readable instead of being hidden by that choice.
    """
    set_extraction_backend(model=None, source="none")
    assert _status() == "disabled"
    assert "no chat model resolved" in get_memory_update_health().as_dict()["reason"]

    record_attempt()
    record_failure(MemoryUpdateDisabled("no LLM is configured"))
    payload = get_memory_update_health().as_dict()
    assert payload["status"] == "degraded"
    assert payload["extraction_enabled"] is False
    assert payload["last_error_type"] == "MemoryUpdateDisabled"


def test_a_fresh_process_does_not_claim_a_verdict_before_any_backend_publishes_one():
    """``extraction_enabled`` is tri-state on purpose.

    Before a backend publishes a model nothing is known, so reporting ``false``
    would repeat the original sin one level up: a definite-looking answer to a
    question nobody has answered.
    """
    assert _status() == "unknown"
    assert get_memory_update_health().as_dict()["extraction_enabled"] is None


def test_recovery_clears_the_streak_and_the_error():
    set_extraction_backend(model="m", source="host_llm (app default model)")
    record_attempt()
    record_failure(RuntimeError("transient 503"))
    assert _status() == "degraded"

    record_attempt()
    record_success()
    payload = get_memory_update_health().as_dict()
    assert payload["status"] == "ok"
    assert payload["consecutive_failures"] == 0
    assert payload["last_error"] is None
    # A past failure stays auditable; only the "now" fields clear.
    assert payload["last_failure_at"] is not None
    assert payload["total_failures"] == 1
    assert payload["total_successes"] == 1


def test_a_answered_but_fully_rejected_update_is_not_healthy():
    """A 200 from the model that stores nothing is still a broken memory.

    This is the same bug class from the other direction: reporting success
    because the call did not raise would be the false "ok" this module exists
    to prevent.
    """
    set_extraction_backend(model="m", source="host_llm (app default model)")
    record_attempt()
    record_failure(MemoryUpdateRejected("every proposal was rejected by a write gate"))
    assert _status() == "degraded"
    assert "rejected by a write gate" in get_memory_update_health().as_dict()["reason"]


# ── bounds and masking ─────────────────────────────────────────────────────


def test_provider_error_bodies_are_masked_and_clipped():
    """A rejected Authorization header is echoed by several gateways.

    The health record is operator-facing, so a provider body must not be able to
    print a key into an API response or grow without bound.
    """
    set_extraction_backend(model="m", source="host_llm")
    record_failure(RuntimeError("401 for Authorization: Bearer sk-or-v1-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH"))
    assert "sk-or-v1" not in get_memory_update_health().last_error
    assert "[redacted]" in get_memory_update_health().last_error

    record_failure(RuntimeError("x" * 50_000))
    assert len(get_memory_update_health().last_error) <= 500


def test_reset_mutates_in_place_so_holders_stay_live():
    """``reset_memory_update_health`` must not detach existing references.

    The backend reads the record through a held object on every status call; a
    rebind would leave it reporting a detached snapshot forever.
    """
    held = get_memory_update_health()
    set_extraction_backend(model="m", source="host_llm")
    record_attempt()
    record_failure(RuntimeError("boom"))
    assert held.status == "degraded"

    reset_memory_update_health()
    assert get_memory_update_health() is held
    assert held.status == "unknown"
    assert held.last_error is None


def test_describe_model_reads_the_shapes_the_real_factories_set():
    """The label must come from a real model object, not a hardcoded class check.

    ``host_llm`` is whatever ``create_chat_model`` returned, so this has to cope
    with both the keyless router and an openai-SDK client.
    """

    class _FreeRouter:
        model = "free:opencode-zen:space-bunny-free"

    class _OpenAI:
        model_name = "unbiased/pareto"

    class _Unlabelled:
        pass

    assert describe_model(_FreeRouter()) == "free:opencode-zen:space-bunny-free"
    assert describe_model(_OpenAI()) == "unbiased/pareto"
    assert describe_model(_Unlabelled()) == "_Unlabelled"
    assert describe_model(None) is None


# ── the updater actually reports ───────────────────────────────────────────


def test_updater_records_a_provider_auth_failure_instead_of_only_logging(deermem_data_dir):
    """The reported bug, end to end through the real updater.

    Before this change the 401 produced one ``logger.exception`` line and
    nothing else: ``update_memory`` returned ``False``, the queue moved on, and
    no surface anywhere carried the reason.
    """
    from alpha.agents.memory.backends.deermem.deer_mem import DeerMem

    boom = _AuthenticationError("Error code: 401 - {'error': {'message': 'Failed to authenticate request with Clerk', 'code': 401}}")
    model = MagicMock()
    model.model = "unbiased/pareto"
    model.invoke.side_effect = boom
    # Injected the way the alpha factory does it, so the health record's
    # "which model answered" answer is produced by real wiring, not by poking
    # private attributes after construction.
    dm = DeerMem(backend_config={"storage_path": str(deermem_data_dir), "host_llm": model})

    messages = [MagicMock(type="human", content="I prefer Python"), MagicMock(type="ai", content="Noted", tool_calls=[])]
    assert dm._updater.update_memory(messages, thread_id="t-1", user_id="default", agent_name="__default__") is False

    payload = get_memory_update_health().as_dict()
    assert payload["status"] == "degraded"
    assert payload["last_error_type"] == "_AuthenticationError"
    assert "Clerk" in payload["last_error"]
    assert payload["model"] == "unbiased/pareto"
    # The failure still never propagates: memory observes a run, it does not own it.
    assert payload["last_error_scope"].startswith("thread=t-1")


def test_updater_records_success_after_a_real_persisted_update(deermem_data_dir):
    from alpha.agents.memory.backends.deermem.deer_mem import DeerMem

    payload = '{"user": {}, "history": {}, "newFacts": [{"content": "User prefers Python.", "category": "preference"}], "factsToRemove": []}'
    model = MagicMock()
    model.model = "free:opencode-zen:space-bunny-free"
    model.invoke.return_value = type("R", (), {"content": payload})()
    dm = DeerMem(backend_config={"storage_path": str(deermem_data_dir), "host_llm": model})

    messages = [MagicMock(type="human", content="I prefer Python"), MagicMock(type="ai", content="Noted", tool_calls=[])]
    assert dm._updater.update_memory(messages, thread_id="t-2", user_id="default") is True
    assert _status() == "ok"
    assert get_memory_update_health().total_successes == 1


def test_deermem_publishes_the_resolved_model_and_its_source(deermem_data_dir):
    """``model: null`` means "inherit the app default", not "no model".

    The status surface has to name the model that actually ran, or the operator
    cannot connect a memory failure to the credential that caused it.
    """
    from alpha.agents.memory.backends.deermem.deer_mem import DeerMem

    inherited = DeerMem(backend_config={"storage_path": str(deermem_data_dir), "host_llm": MagicMock(model="free:opencode-zen:space-bunny-free")})
    health = inherited.memory_health()
    assert health["model"] == "free:opencode-zen:space-bunny-free"
    assert "host_llm" in health["model_source"]

    reset_memory_update_health()
    explicit = DeerMem(backend_config={"storage_path": str(deermem_data_dir)})
    assert explicit.memory_health()["extraction_enabled"] is False
    assert explicit.memory_health()["status"] == "disabled"
    assert explicit.memory_health()["model_source"] == "none"


def test_memory_health_never_raises(deermem_data_dir):
    """A health read must not be able to break the status route it serves."""
    from alpha.agents.memory.backends.deermem.deer_mem import DeerMem

    dm = DeerMem(backend_config={"storage_path": str(deermem_data_dir)})
    with patch("alpha.agents.memory.backends.deermem.deer_mem.get_memory_update_health", side_effect=RuntimeError("broken")):
        assert dm.memory_health() is None


# ── the Gateway surface ────────────────────────────────────────────────────


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(memory_router.router)
    return app


def _manager(memory_data: dict, health_payload: dict | None) -> MagicMock:
    mgr = MagicMock()
    mgr.get_memory.return_value = memory_data
    if health_payload is None:
        mgr.memory_health.return_value = None
    else:
        mgr.memory_health.return_value = health_payload
    return mgr


_EMPTY = {
    "version": "1.0",
    "lastUpdated": "2026-03-26T12:00:00Z",
    "user": {"workContext": {"summary": "", "updatedAt": ""}, "personalContext": {"summary": "", "updatedAt": ""}, "topOfMind": {"summary": "", "updatedAt": ""}},
    "history": {"recentMonths": {"summary": "", "updatedAt": ""}, "earlierContext": {"summary": "", "updatedAt": ""}, "longTermBackground": {"summary": "", "updatedAt": ""}},
    "facts": [],
}


def test_status_endpoint_surfaces_a_degraded_memory_with_its_reason():
    """The surface that was silent: identical data, now with the reason attached."""
    degraded = {
        "status": "degraded",
        "reason": "the last memory update failed: AuthenticationError: Error code: 401",
        "extraction_enabled": True,
        "model": "unbiased/pareto",
        "model_source": "host_llm (app default model)",
        "last_attempt_at": "2026-09-29T01:00:00+00:00",
        "last_success_at": None,
        "last_failure_at": "2026-09-29T01:02:00+00:00",
        "last_error_type": "AuthenticationError",
        "last_error": "Error code: 401",
        "last_error_scope": "thread=t1 user=default agent=__default__",
        "consecutive_failures": 7,
        "total_successes": 0,
        "total_failures": 7,
    }
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=_manager(_EMPTY, degraded)):
        with TestClient(_app()) as client:
            response = client.get("/api/memory/status")

    assert response.status_code == 200
    body = response.json()
    assert body["health"]["status"] == "degraded"
    assert body["health"]["last_error_type"] == "AuthenticationError"
    assert body["health"]["consecutive_failures"] == 7
    # The pre-existing fields are untouched by this addition.
    assert body["data"]["facts"] == []
    assert body["config"]["manager_class"] == "deermem"


def test_status_health_is_null_for_a_backend_with_no_pipeline_not_ok():
    """Unavailable is rendered by omission (the route excludes None) and is
    explicitly *not* a claim of health."""
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=_manager(_EMPTY, None)):
        with TestClient(_app()) as client:
            response = client.get("/api/memory/status")

    assert response.status_code == 200
    assert response.json().get("health") is None


def test_status_endpoint_survives_a_backend_without_the_hook():
    class _NoHealthBackend:
        """A backend predating the hook: memory-readable, health-silent."""

        def get_memory(self, *, user_id=None, **kwargs):
            return _EMPTY

    with patch("app.gateway.routers.memory.get_memory_manager", return_value=_NoHealthBackend()):
        with TestClient(_app()) as client:
            response = client.get("/api/memory/status")

    assert response.status_code == 200
    assert response.json().get("health") is None


def test_an_unusable_health_record_degrades_to_null_instead_of_500():
    """A third-party backend returning junk must not take down a status read."""
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=_manager(_EMPTY, {"status": "invented-state", "nonsense": object()})):
        with TestClient(_app()) as client:
            response = client.get("/api/memory/status")

    assert response.status_code == 200
    assert response.json().get("health") is None


def test_a_raising_health_reporter_does_not_break_the_status_route():
    mgr = _manager(_EMPTY, None)
    mgr.memory_health.side_effect = RuntimeError("health exploded")
    with patch("app.gateway.routers.memory.get_memory_manager", return_value=mgr):
        with TestClient(_app()) as client:
            response = client.get("/api/memory/status")

    assert response.status_code == 200
    assert response.json().get("health") is None
