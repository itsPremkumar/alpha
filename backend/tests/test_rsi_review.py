"""Tests for the RSI human review gate (plan WP-C2 slice C2b, feature #4).

Pins (plan §3 WP-C2 + §5 guardrails):

- honest decision states: exactly pending/approved/rejected; a failed lookup
  (record, queue request, corrupt/unsafe record) or missing evidence lands
  ``pending`` — never auto-approved;
- no self-approval: the module never resolves its own approval requests
  (AST pin) and exposes no approve/reject function — approval comes only from
  the human channel (``ApprovalQueue.resolve_request`` called here as the
  human operator, never from module code);
- no fabricated reviewer identity: an absent human stays ``None``, and an
  "approved" queue record without a reviewer identity fails closed;
- approval without complete evidence is refused with the real reason — both
  before enqueueing and re-checked at decision time;
- WP-B3 protected-path deny still fails closed even when this gate reports
  ``approved``/``reviewed=True`` (the gate never weakens B3);
- every recorded decision carries provenance (who/what/when) via the
  injectable clock — deterministic;
- serialization contains no invented confidence/score fields;
- declared ``simulated``/``unverified`` evidence never passes (§5.6).
"""

import ast
import json
from pathlib import Path

import pytest

from alpha.config.runtime_paths import runtime_home
from alpha.projects.approval_queue import ApprovalQueue
from alpha.rsi import review
from alpha.rsi.lineage import RsiLineageStore
from alpha.rsi.protected_paths import assert_candidate_path_allowed

MODULE_PATH = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "rsi" / "review.py"

FIXED_CLOCK = 1_700_000_000.0


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    """Keep runtime_home() inside a temp dir (the env does not isolate it)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def _fixed_clock() -> float:
    return FIXED_CLOCK


def _complete_evidence() -> dict:
    return {
        "manifest": {"state": "complete", "files": ["alpha/rsi/engine.py"]},
        "baseline_metrics": {"evidence_kind": "measured", "pass_rate": 0.92},
        "candidate_metrics": {"evidence_kind": "measured", "pass_rate": 0.95},
        "holdout": {"evidence_kind": "measured", "holdout_gate": True},
    }


def _record_candidate(candidate_id: str) -> None:
    store = RsiLineageStore()
    if store.get(candidate_id) is None:
        store.record({"candidate_id": candidate_id, "payload": {"note": "review-gate test candidate"}}, parent_id=None, mutation_operator="test_operator")


def _open(candidate_id: str = "cand-review-1", evidence: dict | None = None, risk: str = "R2") -> review.ReviewRecord:
    _record_candidate(candidate_id)
    return review.open_review(candidate_id, _complete_evidence() if evidence is None else evidence, risk=risk, clock=_fixed_clock)


def _queue() -> ApprovalQueue:
    return ApprovalQueue(review.DEFAULT_PROJECT_ID)


def _queue_path() -> Path:
    return runtime_home() / "projects" / review.DEFAULT_PROJECT_ID / "approvals" / "queue.json"


def _read_queue() -> dict:
    return json.loads(_queue_path().read_text(encoding="utf-8"))


def _write_queue(payload: dict) -> None:
    _queue_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Honest decision states: open lands pending, lookups fail closed
# ---------------------------------------------------------------------------


def test_decision_states_constant_is_honest():
    assert review.DECISION_STATES == ("pending", "approved", "rejected")


def test_open_review_lands_pending_with_honest_provenance():
    record = _open()
    assert record.state == "pending"
    assert record.opened_at == FIXED_CLOCK
    assert record.request_id is not None and record.request_id.startswith("APPR-")
    assert record.lineage_status == "candidate"  # what the review records against
    assert record.lineage_payload_hash

    decision = review.review_decision(record.candidate_id)
    assert decision.state == "pending"
    assert decision.reviewer is None
    assert decision.decided_at is None
    assert decision.opened_at == FIXED_CLOCK
    assert decision.provenance["who"] is None
    assert decision.provenance["what"] == f"rsi_review:{record.candidate_id}:risk=R2"
    assert decision.provenance["when_opened"] == FIXED_CLOCK
    assert decision.provenance["when_decided"] is None
    assert "awaiting human review" in decision.reason
    assert review.human_reviewed(record.candidate_id) is False

    pending = _queue().list_pending()
    assert [req.request_id for req in pending] == [record.request_id]
    assert pending[0].action_type == "rsi_review"
    assert pending[0].bot_name == "rsi_review"
    assert pending[0].risk_level == "medium"  # R2 -> medium (real ApprovalQueue literal)


def test_unknown_candidate_lookup_fails_closed_pending():
    decision = review.review_decision("cand-never-opened")
    assert decision.state == "pending"
    assert "no review record" in decision.reason
    assert decision.reviewer is None
    assert review.load_review("cand-never-opened") is None
    # Unsafe ids fail closed too (no traversal, no phantom record).
    assert review.load_review("bad/id") is None
    assert review.review_decision("bad/id").state == "pending"


def test_open_without_lineage_lookup_fails_closed_no_request():
    record = review.open_review("cand-ghost-1", _complete_evidence(), risk="R2", clock=_fixed_clock)
    assert record.state == "pending"
    assert record.request_id is None
    assert "no RSI lineage record" in record.reason
    assert review.review_decision("cand-ghost-1").state == "pending"
    assert _queue().list_pending() == []


def test_missing_queue_request_fails_closed_pending():
    record = _open()
    _queue_path().unlink()
    decision = review.review_decision(record.candidate_id)
    assert decision.state == "pending"
    assert "not found" in decision.reason


def test_corrupt_or_approved_claiming_record_fails_closed():
    record = _open()
    path = review.review_record_path(record.candidate_id)
    original = json.loads(path.read_text(encoding="utf-8"))

    path.write_text("{not json", encoding="utf-8")
    assert review.load_review(record.candidate_id) is None
    assert review.review_decision(record.candidate_id).state == "pending"

    # A record file *claiming* "approved" is corrupt by definition (this gate
    # only ever persists "pending") and must never be trusted.
    original["state"] = "approved"
    path.write_text(json.dumps(original), encoding="utf-8")
    assert review.load_review(record.candidate_id) is None
    assert review.review_decision(record.candidate_id).state == "pending"


# ---------------------------------------------------------------------------
# Evidence completeness: refused with the real reason, before and after approval
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("defect", ["missing", "null", "empty"])
def test_incomplete_evidence_refused_with_real_reason_no_request(defect):
    candidate_id = f"cand-evidence-{defect}"
    _record_candidate(candidate_id)
    evidence = _complete_evidence()
    if defect == "missing":
        evidence.pop("holdout")
    elif defect == "null":
        evidence["holdout"] = None
    else:
        evidence["holdout"] = {}
    record = review.open_review(candidate_id, evidence, risk="R2", clock=_fixed_clock)
    assert record.state == "pending"
    assert record.request_id is None
    assert "holdout" in record.reason
    assert "no approval request enqueued" in record.reason
    decision = review.review_decision(candidate_id)
    assert decision.state == "pending"
    assert any("holdout" in defect_text for defect_text in decision.evidence_defects)
    assert _queue().list_pending() == []


@pytest.mark.parametrize(
    ("kind", "needle"),
    [("simulated", "simulated"), ("unverified", "unverified"), ("made_up_kind", "whitelist")],
)
def test_non_gating_evidence_kinds_refused_at_open(kind, needle):
    candidate_id = f"cand-kind-{kind}"
    _record_candidate(candidate_id)
    evidence = _complete_evidence()
    evidence["holdout"] = {"evidence_kind": kind, "value": 0.5}
    record = review.open_review(candidate_id, evidence, risk="R2", clock=_fixed_clock)
    assert record.state == "pending"
    assert record.request_id is None
    assert needle in record.reason
    assert review.review_decision(candidate_id).state == "pending"
    assert _queue().list_pending() == []


def test_approval_without_complete_evidence_is_refused_at_decision_time():
    record = _open()
    # Evidence goes missing after the request opened (bundle tamper/loss).
    path = review.review_record_path(record.candidate_id)
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["evidence"].pop("holdout")
    path.write_text(json.dumps(stored), encoding="utf-8")
    # A human approves — approval must still not stand on incomplete evidence.
    _queue().resolve_request(record.request_id, approved=True, resolved_by="reviewer_alice", comment="looks fine to me")
    decision = review.review_decision(record.candidate_id)
    assert decision.state == "pending"
    assert "incomplete evidence" in decision.reason
    assert "holdout" in decision.reason
    assert review.human_reviewed(record.candidate_id) is False


def test_entry_validation_fails_closed_with_real_text():
    _record_candidate("cand-entry-1")
    with pytest.raises(ValueError, match="candidate_id"):
        review.open_review("bad/id", _complete_evidence(), risk="R2", clock=_fixed_clock)
    with pytest.raises(ValueError, match="mapping"):
        review.open_review("cand-entry-1", ["not", "a", "mapping"], risk="R2", clock=_fixed_clock)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="required_keys"):
        review.open_review("cand-entry-1", _complete_evidence(), risk="R2", required_keys=(), clock=_fixed_clock)
    with pytest.raises(ValueError, match="clock"):
        review.open_review("cand-entry-1", _complete_evidence(), risk="R2", clock=lambda: "not-a-time")  # type: ignore[arg-type,return-value]


def test_risk_class_validated_and_mapped_to_queue_risk_level():
    _record_candidate("cand-risk-1")
    with pytest.raises(ValueError, match="risk"):
        review.open_review("cand-risk-1", _complete_evidence(), risk="R9", clock=_fixed_clock)
    record = _open(candidate_id="cand-risk-5", risk="R5")
    queued = _queue().get_request(record.request_id)
    assert queued is not None
    assert queued.risk_level == "critical"
    assert queued.details["risk"] == "R5"


# ---------------------------------------------------------------------------
# Human channel: approval / rejection, verbatim identity, provenance
# ---------------------------------------------------------------------------


def test_human_approval_produces_approved_decision_with_verbatim_identity():
    record = _open()
    resolved = _queue().resolve_request(record.request_id, approved=True, resolved_by="reviewer_alice", comment="measured evidence checks out")
    decision = review.review_decision(record.candidate_id)
    assert decision.state == "approved"
    assert decision.reviewer == "reviewer_alice"
    assert decision.decided_at == resolved.resolved_at  # verbatim provenance, never invented
    assert decision.provenance["who"] == "reviewer_alice"
    assert decision.provenance["when_decided"] == resolved.resolved_at
    assert "reviewer_alice" in decision.reason
    assert list(decision.evidence_defects) == []
    assert review.human_reviewed(record.candidate_id) is True


def test_human_rejection_records_rejected_state():
    record = _open()
    _queue().resolve_request(record.request_id, approved=False, resolved_by="reviewer_bob", comment="holdout delta regressed")
    decision = review.review_decision(record.candidate_id)
    assert decision.state == "rejected"
    assert decision.reviewer == "reviewer_bob"
    assert "holdout delta regressed" in decision.reason
    assert review.human_reviewed(record.candidate_id) is False


def test_absent_human_stays_absent_no_fabricated_reviewer():
    record = _open()
    decision = review.review_decision(record.candidate_id)
    assert decision.state == "pending"
    assert decision.reviewer is None
    assert decision.provenance["who"] is None
    assert json.loads(json.dumps(decision.to_dict()))["reviewer"] is None

    # An "approved" queue record with NO reviewer identity fails closed.
    payload = _read_queue()
    assert payload["requests"][0]["request_id"] == record.request_id
    payload["requests"][0]["status"] = "approved"
    payload["requests"][0]["resolved_by"] = None
    payload["requests"][0]["resolved_at"] = "2026-01-01T00:00:00+00:00"
    _write_queue(payload)
    decision = review.review_decision(record.candidate_id)
    assert decision.state == "pending"
    assert "no reviewer identity" in decision.reason
    assert decision.reviewer is None
    assert review.human_reviewed(record.candidate_id) is False


def test_request_channel_mismatch_fails_closed():
    record = _open()
    payload = _read_queue()
    payload["requests"][0]["action_type"] = "deploy_production"
    _write_queue(payload)
    decision = review.review_decision(record.candidate_id)
    assert decision.state == "pending"
    assert "does not match this review channel" in decision.reason


def test_timed_out_request_fails_closed_pending():
    record = _open()
    payload = _read_queue()
    payload["requests"][0]["status"] = "timed_out"
    _write_queue(payload)
    decision = review.review_decision(record.candidate_id)
    assert decision.state == "pending"
    assert "timed out" in decision.reason
    assert decision.reviewer is None


def test_pending_request_blocks_reopen_rejection_allows_new_review():
    first = _open()
    with pytest.raises(ValueError, match="already open"):
        review.open_review(first.candidate_id, _complete_evidence(), risk="R2", clock=_fixed_clock)
    _queue().resolve_request(first.request_id, approved=False, resolved_by="reviewer_bob", comment="needs work")
    assert review.review_decision(first.candidate_id).state == "rejected"
    second = review.open_review(first.candidate_id, _complete_evidence(), risk="R2", clock=_fixed_clock)
    assert second.request_id is not None and second.request_id != first.request_id
    assert review.review_decision(first.candidate_id).state == "pending"  # fresh ticket, awaiting human


# ---------------------------------------------------------------------------
# No self-approval (AST pin) + provenance + serialization honesty
# ---------------------------------------------------------------------------


def test_module_never_resolves_requests_no_self_approval():
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE_PATH))
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.add(func.id)
            elif isinstance(func, ast.Attribute):
                calls.add(func.attr)
    # No code path in this gate resolves (approves/rejects) its own requests:
    # candidates can never approve their own changes (plan §5.3).
    assert "resolve_request" not in calls, "review.py must never resolve its own approval requests (human-only channel)"
    assert "request_approval" in calls, "open_review must open the channel via the existing human-approval precedent"
    top_functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    forbidden = {"approve", "approve_review", "reject", "reject_review", "resolve_review", "self_approve"}
    assert top_functions.isdisjoint(forbidden), f"gate exposes approve/reject functions: {top_functions & forbidden}"


def test_every_decision_carries_provenance_deterministically():
    first = _open(candidate_id="cand-prov-1")
    second = _open(candidate_id="cand-prov-2")
    assert first.opened_at == second.opened_at == FIXED_CLOCK  # injected clock, deterministic
    reloaded = review.load_review(first.candidate_id)
    assert reloaded is not None and reloaded.opened_at == FIXED_CLOCK
    _queue().resolve_request(first.request_id, approved=True, resolved_by="reviewer_carol", comment="ok")
    decided = review.review_decision(first.candidate_id)
    pending = review.review_decision(second.candidate_id)
    for decision in (decided, pending):
        provenance = decision.provenance
        assert {"who", "what", "when_opened", "when_decided"} <= set(provenance)
        assert provenance["what"].startswith("rsi_review:")
        assert provenance["when_opened"] == FIXED_CLOCK
    assert decided.provenance["who"] == "reviewer_carol"
    assert pending.provenance["who"] is None
    round_tripped = json.loads(json.dumps(decided.to_dict()))
    assert round_tripped["provenance"]["when_opened"] == FIXED_CLOCK
    assert round_tripped["provenance"]["who"] == "reviewer_carol"


def test_serialization_contains_no_invented_confidence_scores():
    record = _open(candidate_id="cand-serial-1")
    _queue().resolve_request(record.request_id, approved=True, resolved_by="reviewer_alice", comment="ok")
    payloads = [
        record.to_dict(),
        review.review_decision(record.candidate_id).to_dict(),
        review.review_decision("cand-serial-missing").to_dict(),
        review.load_review(record.candidate_id).to_dict(),  # type: ignore[union-attr]
    ]
    forbidden_keys = {"confidence", "confidence_kind", "score", "scores", "rating"}
    for payload in payloads:
        text = json.dumps(payload)
        assert "confidence" not in text, f"invented confidence score serialized: {text}"

        def walk(node, found: set) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    found.add(str(key))
                    walk(value, found)
            elif isinstance(node, list | tuple):
                for item in node:
                    walk(item, found)

        found: set = set()
        walk(payload, found)
        assert found.isdisjoint(forbidden_keys)


# ---------------------------------------------------------------------------
# WP-B3 regression pin: the review gate never weakens protected-path deny
# ---------------------------------------------------------------------------


def test_protected_path_deny_fails_closed_even_with_approved_review():
    record = _open()
    _queue().resolve_request(record.request_id, approved=True, resolved_by="reviewer_alice", comment="approved for review_required surfaces only")
    flag = review.human_reviewed(record.candidate_id)
    assert flag is True
    # The human channel flag satisfies review_required paths…
    assert_candidate_path_allowed(".github/workflows/release.yml", reviewed=flag)
    # …but a DENY keeps failing closed even when the review record says approved.
    for path in (".env", "backend/tests/test_rsi_review.py", "backend/app/gateway/auth/dependencies.py", "contracts/feature_manifest.json", "main"):
        with pytest.raises(PermissionError):
            assert_candidate_path_allowed(path, reviewed=flag)
    # The gate module itself never touches the protected-path policy at all.
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "protected_paths" not in source
    assert "PROTECTED_RULES" not in source
