import json

from alpha.tools.builtins import (
    audit_finish_first_evidence,
    check_or_set_autonomy_profile,
    deliberate_artifact_quality,
    manage_context_data,
)


def test_wave4_quality_council_tool():
    res_str = deliberate_artifact_quality.invoke({
        "artifact_name": "secure_module.py",
        "content": 'def safe_func():\n    """Safe function."""\n    return True\n',
        "risk_tier": "tier_2_standard",
        "test_passed": True,
        "exit_code": 0,
    })
    data = json.loads(res_str)
    assert data["passed"] is True
    assert data["approvals_count"] >= 3
    # Tool output discloses that the aggregate confidence is a heuristic.
    assert data["confidence_baseline_disclosed"] is True
    assert data["weighted_score_method"] == "heuristic"
    assert data["weighted_score_formula"]


def test_wave4_context_as_data_tool():
    # 1. Load context
    load_res = json.loads(manage_context_data.invoke({
        "action": "load",
        "content": "line 1\nkeyword target\nline 3\nkeyword target 2",
        "label": "test_text",
    }))
    assert load_res["action"] == "load"
    handle_id = load_res["handle"]["handle_id"]

    # 2. Grep
    grep_res = json.loads(manage_context_data.invoke({
        "action": "grep",
        "handle_id": handle_id,
        "query": "keyword",
    }))
    assert grep_res["action"] == "grep"
    assert len(grep_res["matches_preview"]) == 2

    # 3. Stats
    stats_res = json.loads(manage_context_data.invoke({"action": "stats"}))
    assert stats_res["total_handles"] >= 1


def test_wave4_adaptive_autonomy_tool():
    # 1. Get profile
    res1 = json.loads(check_or_set_autonomy_profile.invoke({"action": "get_profile"}))
    assert "current_profile" in res1

    # 2. Set profile
    res2 = json.loads(check_or_set_autonomy_profile.invoke({
        "action": "set_profile",
        "profile_name": "operator",
    }))
    assert res2["active_profile"] == "operator"

    # 3. Evaluate safe action
    res3 = json.loads(check_or_set_autonomy_profile.invoke({
        "action": "evaluate_action",
        "proposed_command": "git status",
    }))
    assert res3["decision"] == "allow"

    # 4. Evaluate destructive action
    res4 = json.loads(check_or_set_autonomy_profile.invoke({
        "action": "evaluate_action",
        "proposed_command": "rm -rf /root",
    }))
    assert res4["decision"] == "require_approval"


def test_wave4_evidence_matrix_tool():
    # 1. Record claim
    res1 = json.loads(audit_finish_first_evidence.invoke({
        "action": "record_claim",
        "claim_statement": "Endpoint responds with 200 OK",
        "target_path": "api/v1/health",
    }))
    assert res1["action"] == "record_claim"
    claim_id = res1["claim"]["claim_id"]

    # 2. Attach physical proof
    res2 = json.loads(audit_finish_first_evidence.invoke({
        "action": "attach_proof",
        "claim_id": claim_id,
        "proof_type": "exit_code_zero",
        "command_run": "curl -I http://localhost:8000/health",
        "exit_code": 0,
        "output_snippet": "HTTP/1.1 200 OK",
        "verified": True,
    }))
    assert res2["entry"]["certified"] is True

    # 3. Audit finalization
    res3 = json.loads(audit_finish_first_evidence.invoke({
        "action": "audit_finalization",
    }))
    assert res3["can_finalize"] is True
    assert "AUDIT_PASSED" in res3["status_message"]
