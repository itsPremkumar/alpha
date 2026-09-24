"""REST contract + honesty tests for the Mixture-of-Agents (MoA) surface.

Covers the two endpoints added to ``app/gateway/routers/models.py``:

* ``GET  /api/models/moa``      — real engine/command/limit/tool/redaction state
  with PER-FIELD error disclosures (a broken capability registry must degrade
  one field, never the whole answer, and never invent defaults).
* ``POST /api/models/moa/run``  — a real MoA round whose ``evidence_kind``
  honestly distinguishes ``real`` (production client ran), ``failed``
  (production client ran, every candidate failed) and ``simulated`` (the
  model client is a stub — never presented as model output).

Also pinned: per-candidate ``model:use`` authorization reuse, prompt
redaction by the engine, secret redaction inside advisor error text, and the
validation contract (400/422/404) — no request reaches the engine invalid.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.deps import get_config
from app.gateway.routers import models as models_router

KNOWN_MODELS = {
    name: SimpleNamespace(
        name=name,
        model=f"vendor/{name}",
        display_name=name.title(),
        description=f"{name} test model",
        supports_thinking=False,
        supports_reasoning_effort=False,
    )
    for name in ("alpha-1", "alpha-2", "alpha-3")
}


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        get_model_config=lambda name: KNOWN_MODELS.get(name),
        authorization=SimpleNamespace(fail_closed=True),
    )


def _app() -> FastAPI:
    app = make_authed_test_app(
        user_factory=lambda: User(
            email="moa-test@example.com", password_hash="x", system_role="user", id=uuid4()
        )
    )
    app.state.config = _config()
    app.dependency_overrides[get_config] = _config
    app.include_router(models_router.router)
    return app


# ── status ────────────────────────────────────────────────────────────


def test_moa_status_reports_real_registry_state():
    with TestClient(_app()) as client:
        resp = client.get("/api/models/moa")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["capability_id"] == "moa_engine"

    # Every optional slot is either a real value or a real error string —
    # never a silent default.
    for slot in ("engine", "engine_error", "command", "command_error", "limits", "limits_error"):
        value = body[slot]
        assert value is not None or slot.endswith("_error")
    for error_slot in ("engine_error", "command_error", "limits_error"):
        if body[error_slot] is not None:
            assert isinstance(body[error_slot], str) and body[error_slot]

    if body["command"] is not None:
        assert body["command"]["command"] == "/moa"
        # Handler availability is a measured fact, not an assumption.
        assert isinstance(body["command"]["handler_available"], bool)
    if body["limits"] and "max_advisors" in body["limits"]:
        assert int(body["limits"]["max_advisors"]) >= 1
    if "error" not in body["redaction"]:
        assert body["redaction"]["email_masked"] is True
        assert body["redaction"]["phone_masked"] is True
        assert body["redaction"]["secret_masked"] is True
    assert body["tool"]["name"] == "moa_multi_model_reasoning"


def test_moa_status_isolates_a_broken_capability_registry(monkeypatch):
    """One broken source degrades its own field; the rest stays real."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("capability registry offline")

    import alpha.capabilities as capabilities

    monkeypatch.setattr(capabilities, "status", boom)
    with TestClient(_app()) as client:
        resp = client.get("/api/models/moa")
    assert resp.status_code == 200
    body = resp.json()
    assert body["engine"] is None
    assert "RuntimeError" in (body["engine_error"] or "")
    # Isolated failure: the tool/redaction probes still answered for real.
    assert body["tool"]["name"] == "moa_multi_model_reasoning"
    if "error" not in body["redaction"]:
        assert body["redaction"]["secret_masked"] is True


# ── run ───────────────────────────────────────────────────────────────


def _patch_oneshot(monkeypatch, impl):
    import alpha.utils.oneshot_llm as oneshot

    monkeypatch.setattr(oneshot, "run_oneshot_llm", impl)


def test_moa_run_reports_real_evidence_and_engine_redacted_prompt(monkeypatch):
    async def fake_oneshot(*, system_instruction, user_content, run_name, app_config, model_name):
        assert system_instruction  # advisors get their own system prompt
        assert user_content
        assert run_name == "moa_run"
        return f"answer from {model_name}"

    _patch_oneshot(monkeypatch, fake_oneshot)
    with TestClient(_app()) as client:
        resp = client.post(
            "/api/models/moa/run",
            json={
                "prompt": "reach me at probe@example.com and summarize this",
                "candidate_models": ["alpha-1", "alpha-2"],
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["evidence_kind"] == "real"
    assert "2/2" in body["evidence_note"]
    assert "probe@example.com" not in body["prompt"]
    assert "[redacted email]" in body["prompt"]
    assert len(body["candidates"]) == 2
    assert {c["model_name"] for c in body["candidates"]} == {"alpha-1", "alpha-2"}
    assert all(c["success"] for c in body["candidates"])
    assert all(c["duration_ms"] >= 0 for c in body["candidates"])
    assert isinstance(body["consensus_response"], str)
    assert body["total_duration_ms"] >= 0


def test_moa_run_reports_failed_when_every_candidate_fails(monkeypatch):
    async def exploding_oneshot(**_kwargs):
        raise RuntimeError("provider unavailable")

    _patch_oneshot(monkeypatch, exploding_oneshot)
    with TestClient(_app()) as client:
        resp = client.post(
            "/api/models/moa/run",
            json={"prompt": "anything", "candidate_models": ["alpha-1"]},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["evidence_kind"] == "failed"
    assert "no model output" in body["evidence_note"]
    assert all(not c["success"] for c in body["candidates"])
    assert all(c["error"] for c in body["candidates"])


def test_moa_run_marks_a_stubbed_client_as_simulated(monkeypatch):
    async def stub_client(_model_name, _system_instruction, _user_content, _app_config):
        return "stub answer"

    # Replacing the module-level client is exactly what a test/substitute
    # injection does; the route must disclose it instead of passing it off
    # as production output.
    monkeypatch.setattr(models_router, "_call_moa_model", stub_client)
    with TestClient(_app()) as client:
        resp = client.post(
            "/api/models/moa/run",
            json={"prompt": "anything", "candidate_models": ["alpha-1"]},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["evidence_kind"] == "simulated"
    assert "simulated" in body["evidence_note"]


def test_moa_run_redacts_secrets_leaking_through_advisor_errors(monkeypatch):
    secret = "ghp_abcdef1234567890abcdef1234567890abcd"

    async def leaky_oneshot(**_kwargs):
        raise RuntimeError(f"auth failed for key {secret}")

    _patch_oneshot(monkeypatch, leaky_oneshot)
    with TestClient(_app()) as client:
        resp = client.post(
            "/api/models/moa/run",
            json={"prompt": "anything", "candidate_models": ["alpha-1"]},
        )
    assert resp.status_code == 200, resp.text
    for candidate in resp.json()["candidates"]:
        assert secret not in (candidate["error"] or "")
        assert "RuntimeError" in (candidate["error"] or "")


def test_moa_run_rejects_invalid_requests_before_the_engine(monkeypatch):
    from alpha.deliberation.moa import MAX_ADVISORS

    async def never_called(**_kwargs):  # pragma: no cover - must not run
        raise AssertionError("the engine must not be reached for an invalid request")

    _patch_oneshot(monkeypatch, never_called)
    with TestClient(_app()) as client:
        blank = client.post("/api/models/moa/run", json={"prompt": "   ", "candidate_models": ["alpha-1"]})
        assert blank.status_code == 400

        duplicate = client.post(
            "/api/models/moa/run", json={"prompt": "x", "candidate_models": ["alpha-1", "alpha-1"]}
        )
        assert duplicate.status_code == 422

        empty_name = client.post(
            "/api/models/moa/run", json={"prompt": "x", "candidate_models": ["alpha-1", " "]}
        )
        assert empty_name.status_code == 422

        too_many = client.post(
            "/api/models/moa/run",
            json={"prompt": "x", "candidate_models": [f"m{i}" for i in range(MAX_ADVISORS + 1)]},
        )
        assert too_many.status_code == 422
        assert "MAX_ADVISORS" in (too_many.json().get("detail") or "")

        unknown = client.post(
            "/api/models/moa/run", json={"prompt": "x", "candidate_models": ["not-a-model"]}
        )
        assert unknown.status_code == 404
        assert "not found" in (unknown.json().get("detail") or "").lower()

        missing_prompt = client.post("/api/models/moa/run", json={"candidate_models": ["alpha-1"]})
        assert missing_prompt.status_code == 422
