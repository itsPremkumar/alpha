import json

from agent_workspace.tools.builtins import enterprise_security_manage


def test_enterprise_security_tool_actions():
    # 1. Action: check_path
    res1 = json.loads(enterprise_security_manage.invoke({
        "action": "check_path",
        "path": "backend/tests",
    }))
    assert res1["status"] == "allowed"

    # 2. Action: check_network (metadata IP blocked)
    res2 = json.loads(enterprise_security_manage.invoke({
        "action": "check_network",
        "url_or_host": "http://169.254.169.254",
    }))
    assert res2["status"] == "blocked"

    # 3. Action: encrypt_checkpoint & decrypt_checkpoint
    state_payload = {"phase": "wave5", "score": 99.5}
    enc_res = json.loads(enterprise_security_manage.invoke({
        "action": "encrypt_checkpoint",
        "payload_json": json.dumps(state_payload),
    }))
    assert enc_res["status"] == "encrypted"
    cipher = enc_res["cipher_b64"]

    dec_res = json.loads(enterprise_security_manage.invoke({
        "action": "decrypt_checkpoint",
        "encrypted_blob": cipher,
    }))
    assert dec_res["status"] == "decrypted"
    assert dec_res["data"]["phase"] == "wave5"

    # 4. Action: log_telemetry & verify_telemetry
    log_res = json.loads(enterprise_security_manage.invoke({
        "action": "log_telemetry",
        "actor": "lead_agent",
        "telemetry_action": "start_run",
        "payload_json": json.dumps({"task": "build"}),
    }))
    assert log_res["status"] == "logged"

    ver_res = json.loads(enterprise_security_manage.invoke({
        "action": "verify_telemetry",
    }))
    assert ver_res["chain_valid"] is True

    # 5. Action: issue_credential
    cred_res = json.loads(enterprise_security_manage.invoke({
        "action": "issue_credential",
        "service_name": "npm_registry",
        "scopes_csv": "read,publish",
    }))
    assert cred_res["status"] == "issued"
    assert cred_res["lease"]["service_name"] == "npm_registry"

    # 6. Action: redact_text
    red_res = json.loads(enterprise_security_manage.invoke({
        "action": "redact_text",
        "raw_text": "Authorization: Bearer sk-ant-api03-secretkey12345678901234567890",
    }))
    assert "[REDACTED_SECRET]" in red_res["redacted_text"]

    # 7. Action: audit_deception
    dec_audit = json.loads(enterprise_security_manage.invoke({
        "action": "audit_deception",
        "agent_statement": "All tests passed successfully!",
        "tool_output": "FAILED tests/test_core.py",
        "exit_code": 1,
    }))
    assert dec_audit["is_deceptive"] is True
