"""Unit tests for the new skills workshop and credentials routers."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi import HTTPException

from alpha.security.credential_vault import get_credential_vault
from app.gateway.routers.skills_workshop import (
    DistillRequest,
    PublishRequest,
    distill_skill,
    publish_skill,
)


def test_credentials_router_flow():
    """The vault flow, driven through HTTP with an authenticated owner.

    The credential routes now resolve the caller and enforce thread ownership
    before touching the process-global vault, so they can no longer be invoked
    as bare functions. This exercises the same sequence end to end (request ->
    list pending -> submit -> pending empty -> clear) through the real router.
    Cross-user refusal lives in test_authz_surface_audit.py.
    """
    from _router_auth_helpers import make_authed_test_app
    from fastapi.testclient import TestClient

    from app.gateway.routers import credentials as credentials_router

    thread_id = "router-thread-1"
    vault = get_credential_vault()
    vault.clear_thread(thread_id=thread_id)

    app = make_authed_test_app()
    app.include_router(credentials_router.router)
    client = TestClient(app)

    # Request a credential
    vault.request_credential(thread_id, "STRIPE_SECRET_KEY", "Stripe API Key")
    pending = client.get("/api/credentials/pending", params={"thread_id": thread_id})
    assert pending.status_code == 200, pending.text
    assert len(pending.json()) == 1
    assert pending.json()[0]["key"] == "STRIPE_SECRET_KEY"

    # Submit via router
    res = client.post(
        "/api/credentials/submit",
        json={
            "thread_id": thread_id,
            "key": "STRIPE_SECRET_KEY",
            "value": "sk_test_mock_stripe_key_12345",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "deposited"
    assert res.json()["key"] == "STRIPE_SECRET_KEY"

    # Pending should now be empty
    assert client.get("/api/credentials/pending", params={"thread_id": thread_id}).json() == []
    assert vault.get_credential(thread_id, "STRIPE_SECRET_KEY") == "sk_test_mock_stripe_key_12345"

    # Clear
    clear_res = client.delete("/api/credentials/clear", params={"thread_id": thread_id})
    assert clear_res.status_code == 200, clear_res.text
    assert clear_res.json()["status"] == "cleared"
    assert vault.get_credential(thread_id, "STRIPE_SECRET_KEY") is None


def test_skills_workshop_router_distill_and_publish():
    req = DistillRequest(
        name="pytest-runner-workflow",
        description="Executes python unit test suites with pytest.",
        steps=[{"tool": "exec", "action": "Run pytest", "command": "pytest -q"}],
        verification_command="pytest -q",
    )
    draft_dict = distill_skill(req)

    assert draft_dict["name"] == "pytest-runner-workflow"
    assert draft_dict["is_valid"] is True
    assert "pytest -q" in draft_dict["markdown_content"]

    # Invalid publish should raise 422
    with pytest.raises(HTTPException) as exc_info:
        publish_skill(
            PublishRequest(
                name="INVALID NAME WITH SPACES",
                description="Valid short description.",
                markdown_content="missing sections",
            )
        )
    assert exc_info.value.status_code == 422


def test_skills_workshop_router_publish_valid(monkeypatch):
    with TemporaryDirectory() as tmp_dir:
        monkeypatch.setenv("AGENT_WORKSPACE_PROJECT_ROOT", tmp_dir)
        req = DistillRequest(
            name="valid-router-skill",
            description="Valid skill created via router distill.",
            steps=[{"tool": "exec", "action": "Run check", "command": "echo check"}],
            verification_command="echo check",
        )
        draft = distill_skill(req)
        assert draft["is_valid"] is True

        res = publish_skill(
            PublishRequest(
                name=draft["name"],
                description=draft["description"],
                markdown_content=draft["markdown_content"],
                overwrite=True,
            )
        )
        assert res["status"] == "published"
        assert res["name"] == "valid-router-skill"
        assert (Path(tmp_dir) / "skills" / "custom" / "valid-router-skill" / "SKILL.md").exists()
