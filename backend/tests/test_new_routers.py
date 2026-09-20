"""Unit tests for the new skills workshop and credentials routers."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi import HTTPException

from alpha.security.credential_vault import get_credential_vault
from app.gateway.routers.credentials import (
    CredentialSubmitRequest,
    clear_credentials,
    list_pending_credentials,
    submit_credential,
)
from app.gateway.routers.skills_workshop import (
    DistillRequest,
    PublishRequest,
    distill_skill,
    publish_skill,
)


def test_credentials_router_flow():
    thread_id = "router-thread-1"
    vault = get_credential_vault()

    # Request a credential
    vault.request_credential(thread_id, "STRIPE_SECRET_KEY", "Stripe API Key")
    pending = list_pending_credentials(thread_id)
    assert len(pending) == 1
    assert pending[0]["key"] == "STRIPE_SECRET_KEY"

    # Submit via router
    res = submit_credential(
        CredentialSubmitRequest(
            thread_id=thread_id,
            key="STRIPE_SECRET_KEY",
            value="sk_test_mock_stripe_key_12345",
        )
    )
    assert res["status"] == "deposited"
    assert res["key"] == "STRIPE_SECRET_KEY"

    # Pending should now be empty
    assert len(list_pending_credentials(thread_id)) == 0
    assert vault.get_credential(thread_id, "STRIPE_SECRET_KEY") == "sk_test_mock_stripe_key_12345"

    # Clear
    clear_res = clear_credentials(thread_id)
    assert clear_res["status"] == "cleared"
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
