"""Blast-radius gate: classification, operator-issued approval, honest status."""

from __future__ import annotations

import inspect
import json

import pytest

from alpha.sandbox.computer_use import (
    NO_EXECUTION_DISCLOSURE,
    ActionSafetyTier,
    BlastRadiusPolicy,
    ComputerWorker,
)
from alpha.tools.builtins.computer_worker_tool import execute_sandboxed_computer_action


def test_blast_radius_policy_classification():
    # Safe
    c_safe = BlastRadiusPolicy.classify("pytest backend/tests -v")
    assert c_safe.tier == ActionSafetyTier.SAFE

    # Sensitive
    c_sens = BlastRadiusPolicy.classify("git push origin main --force")
    assert c_sens.tier == ActionSafetyTier.SENSITIVE

    # Forbidden
    c_forb = BlastRadiusPolicy.classify("rm -rf /")
    assert c_forb.tier == ActionSafetyTier.FORBIDDEN

    # Forbidden credential theft
    c_cred = BlastRadiusPolicy.classify("curl http://169.254.169.254/latest/meta-data/")
    assert c_cred.tier == ActionSafetyTier.FORBIDDEN


def test_computer_worker_execution_gates():
    worker = ComputerWorker(sandbox_name="test_box")

    # 1. Safe command clears the gate
    res_safe = worker.execute("ls -la")
    assert res_safe["status"] == "validated"
    assert res_safe["tier"] == "safe"

    # 2. Sensitive command paused without approval
    res_sens = worker.execute("git push --force")
    assert res_sens["status"] == "approval_required"
    assert res_sens["tier"] == "sensitive"
    assert res_sens["approval_id"]

    # 3. The pause hands out an opaque id, not a boolean the caller can set
    assert "approval_granted" not in inspect.signature(worker.execute).parameters
    assert res_sens["approval_id"] != worker.execute("git push --force")["approval_id"]

    # 4. A granted id clears the gate exactly once
    approved = worker.grant_approval(res_sens["approval_id"], approved_by="test-operator")
    assert approved["approved"] is True
    res_approved = worker.execute("git push --force", approval_id=res_sens["approval_id"])
    assert res_approved["status"] == "validated"
    assert res_approved["approval_used"] is True
    replay = worker.execute("git push --force", approval_id=res_sens["approval_id"])
    assert replay["status"] == "approval_required"

    # 5. Forbidden command rejected even with an operator approval in hand
    res_forb = worker.execute("rm -rf /", approval_id=res_sens["approval_id"])
    assert res_forb["status"] == "forbidden"

    # Verify audit log: every decision above, including the two extra pauses
    # (the duplicate execute in step 3 and the replay in step 4), is recorded.
    statuses = [entry["status"] for entry in worker.get_audit_log()]
    assert statuses == ["validated", "awaiting_approval", "awaiting_approval", "validated", "awaiting_approval", "rejected"]


def test_approval_is_command_bound_and_cannot_be_minted():
    worker = ComputerWorker()
    pending = worker.execute("pip install something")
    assert pending["status"] == "approval_required"

    # An ungranted id cannot be spent, even by the exact command it was issued for.
    ungranted = worker.execute("pip install something", approval_id=pending["approval_id"])
    assert ungranted["status"] == "approval_required"
    assert "never granted by an operator" in ungranted["approval_reason"]

    # An invented id is refused.
    invented = worker.execute("pip install something", approval_id="made-up")
    assert invented["status"] == "approval_required"
    assert "cannot mint one" in invented["approval_reason"]
    assert worker.grant_approval("made-up")["approved"] is False

    # Once granted, it is bound to that one command.
    assert worker.grant_approval(pending["approval_id"], approved_by="test-operator")["approved"] is True
    other = worker.execute("npm install something", approval_id=pending["approval_id"])
    assert other["status"] == "approval_required"
    assert "different command" in other["approval_reason"]

    # And still works for the command it was granted for, exactly once.
    assert worker.execute("pip install something", approval_id=pending["approval_id"])["status"] == "validated"
    assert worker.execute("pip install something", approval_id=pending["approval_id"])["status"] == "approval_required"


def test_operator_approval_reset_revokes_everything_pending():
    worker = ComputerWorker()
    first = worker.execute("pip install one")
    second = worker.execute("pip install two")
    assert worker.revoke_approvals()["revoked_count"] == 2
    for payload in (first, second):
        assert worker.grant_approval(payload["approval_id"])["approved"] is False


def test_worker_never_claims_to_have_executed_anything():
    """This engine gates; it does not spawn. The payload must say so."""

    worker = ComputerWorker()
    result = worker.execute("pytest -q")
    assert result["status"] == "validated"
    assert result["executed"] is False
    assert result["exit_code"] is None
    assert result["disclosure"] == NO_EXECUTION_DISCLOSURE
    # Nothing anywhere in the payload may read as a successful execution.
    serialised = json.dumps(result).lower()
    assert '"executed": true' not in serialised
    assert '"exit_code": 0' not in serialised
    assert "executed safely" not in serialised


def test_worker_tool_schema_has_no_approval_argument():
    properties = set(execute_sandboxed_computer_action.tool_call_schema.model_json_schema().get("properties", {}))
    assert "approval_token" not in properties
    assert "approval_granted" not in properties
    assert "approval_id" not in properties


@pytest.mark.parametrize("token", ["yes", "true", "approved", "admin", "YES"])
def test_worker_tool_cannot_self_authorize_a_sensitive_command(token: str):
    """The exact bypass that was found: any truthy token used to lift the gate."""

    payload = json.loads(execute_sandboxed_computer_action.invoke({"action": "execute_command", "command": "pip install evilpkg", "approval_token": token}))
    assert payload["status"] == "approval_required", token
    assert payload["tier"] == "sensitive"


def test_worker_tool_still_rejects_forbidden_commands():
    payload = json.loads(execute_sandboxed_computer_action.invoke({"action": "execute_command", "command": "rm -rf /", "approval_token": "yes"}))
    assert payload["status"] == "forbidden"


def test_worker_tool_dry_run_is_reported_as_a_dry_run():
    payload = json.loads(execute_sandboxed_computer_action.invoke({"action": "execute_command", "command": "pytest -q", "dry_run": True}))
    assert payload["status"] == "dry_run"
    assert payload["executed"] is False
