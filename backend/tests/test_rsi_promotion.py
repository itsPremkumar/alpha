"""WP-C2/C2c tests: RSI promotion decision composer + evolution routing wiring.

Honesty pins (plan §3 WP-C2 C2c assignment + §5 guardrails):

- every failure injection — missing bundle, corrupt bundle index, unverified
  holdout, pending review, rejected review, corrupt/missing lineage — lands
  ``promoted is False`` with the REAL reason substring, carried verbatim from
  the landed gate module into ``decision.reason`` (failing entries join as
  ``<gate>: <reason>``) and into the matching per-gate entry;
- the gate logic itself always runs the REAL landed modules
  (``verify_bundle`` / ``Bundle.meets_evidence_standard`` /
  ``holdout_gate`` / ``review_decision``+``human_reviewed`` /
  ``RsiLineageStore`` / ``EvolutionEngine.gate``). The ONLY stub point is the
  module-level invoke seam ``alpha.rsi.promotion.route_promotion_decision``,
  used solely to pin honest routing: a stubbed failure reason flows through
  verbatim, a stubbed exception fails closed AND is logged with the real
  text, and a failed composition never invokes the seam at all;
- routing uses the REAL evolution dispatch API: the landed ``GateRequest``
  contract (``human_approved``/``autonomous_mode`` default False) and the
  exact ``EvolutionEngine.gate(candidate_id, baseline, ...)``
  ``-> (promoted, reason)`` signature the router handler calls are pinned;
- ``simulated`` never gates and ``unverified`` is a disclosed 0.5-neutral,
  never a pass — on the lineage record and in the bundle evidence kinds;
- the positive path composes REAL modules end-to-end under a tmp
  ``AGENT_WORKSPACE_HOME``: real measured holdout execution, real bundle
  finalization + sha256 verification, real approval-queue human approval,
  real engine promotion (ledger event asserted);
- generated decision output contains no invented confidence/score/rating
  fields (literal forbidden-key set) and no fabricated defaults (0.89/96.4);
- deterministic: ``tmp_path`` + injected clocks (C2a/C2b), no network, no
  subprocess. The evolution engine's process-global singleton is reset per
  test (``_engine = None``) — a state reset so each test constructs a REAL
  ``EvolutionEngine`` bound to its own tmp home, NOT a stub of any gate.
"""

import inspect
import json
import logging
import shutil

import pytest

from alpha.config.runtime_paths import runtime_home
from alpha.evolution import engine as evolution_engine_module
from alpha.evolution.engine import EvolutionEngine, get_evolution_engine
from alpha.evolution.promotion_route import decide_promotion, route_evolution_gate
from alpha.projects.approval_queue import ApprovalQueue
from alpha.rsi import promotion, review
from alpha.rsi.evidence_bundle import begin_bundle, verify_bundle
from alpha.rsi.holdout import holdout_gate, holdout_not_run, register_hidden_suite, run_holdout
from alpha.rsi.lineage import RsiLineageStore
from app.gateway.routers.evolution import GateRequest

#: The plan §5.6 honesty whitelist, written LITERALLY here so drift in the
#: implementation constant fails the test instead of following it.
WHITELIST = {"measured", "simulated", "heuristic", "unverified"}

#: Forbidden metric keys in decision-GENERATED structures (normalized: lower
#: case, underscores stripped). Upstream payloads may carry labeled metrics
#: verbatim; a promotion decision must never originate one.
FORBIDDEN_GENERATED_KEYS = {
    "confidence",
    "score",
    "passrate",
    "promotionrate",
    "accuracy",
    "winrate",
    "successrate",
    "improved",
    "rating",
}

#: Fabricated defaults from the surrounding codebase (RSI preview 0.89,
#: enterprise holdout 96.4/0.964) that must never appear in decision output.
FORBIDDEN_FABRICATED_NUMBERS = ("0.89", "0.964", "96.4")

#: Bare metric keys as key-form text, banned in decision output.
FORBIDDEN_GENERATED_KEY_FORMS = ('"confidence"', '"score"', '"pass_rate"', '"improved": true')

#: The closed gate order, written LITERALLY so implementation drift fails here.
EXPECTED_GATES = ["lineage", "bundle_integrity", "evidence_standard", "holdout", "human_review", "cooldown", "evolution_route"]

FIXED_CLOCK = 1_700_000_000.0
BASELINE = {"passed": 5, "failed": 0}


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    """Temp AGENT_WORKSPACE_HOME + a fresh REAL evolution engine per test.

    The ``_engine = None`` reset is singleton STATE hygiene (so each test's
    ``get_evolution_engine()`` constructs a real engine bound to its own tmp
    home), restored automatically by monkeypatch — no gate is ever stubbed.
    """
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    monkeypatch.setattr(evolution_engine_module, "_engine", None)
    return tmp_path


def _fixed_clock():
    return FIXED_CLOCK


def _candidate_view() -> dict:
    """A view that genuinely satisfies the five hidden holdout ranges (real executed run)."""
    return {
        "compaction": {"max_budget_chars": 100_000, "keep_last_observations": 5},
        "tool_router": {"retry_limit": 3, "timeout_seconds": 60},
        "context_pruner": {"strip_threshold": 50},
    }


def _measured_holdout() -> dict:
    register_hidden_suite()  # idempotent; real hidden suite on the module-owned runner
    result = run_holdout(_candidate_view())
    assert result["evidence_kind"] == "measured" and result["failed"] == 0, f"fixture must produce a real measured run, got {result}"
    return result


def _review_evidence() -> dict:
    return {
        "manifest": {"state": "complete", "files": ["alpha/rsi/engine.py"]},
        "baseline_metrics": {"evidence_kind": "measured", "fixtures": 5},
        "candidate_metrics": {"evidence_kind": "measured", "fixtures": 5},
        "holdout": {"evidence_kind": "measured", "gate": True},
    }


def _setup_green(candidate_id=None, *, approve_review=True, record_lineage=True, holdout_payload=None) -> dict:
    """Full green composition through the REAL modules; returns handles for one-at-a-time tampering.

    Creates the evolution candidate (its generated id becomes THE candidate
    id everywhere), records measured lineage, finalizes a verified bundle
    with real measured holdout execution, opens the human review and — by
    default — approves it through the real ApprovalQueue.
    """
    engine = get_evolution_engine()
    if candidate_id is None:
        evolution_candidate = engine.propose("skill", "compaction.budget", {"note": "promotion wiring test candidate"})
        candidate_id = evolution_candidate.candidate_id
    engine.record_benchmark(candidate_id, {"passed": 10, "failed": 0, "fixtures": 10})
    if record_lineage:
        RsiLineageStore().record(
            {"candidate_id": candidate_id, "payload": {"note": "promotion test candidate"}, "evidence_kind": "measured"},
            parent_id=None,
            mutation_operator="test_operator",
        )
    bundle = begin_bundle(candidate_id, clock=_fixed_clock)
    bundle.add("holdout.json", _measured_holdout() if holdout_payload is None else holdout_payload)
    bundle.add("shadow.json", {"evidence_kind": "measured", "state": "improved", "channel": "shadow", "note": "recorded comparison evidence"})
    bundle.add("reviews.json", [{"reviewer": "reviewer_alice", "state": "approved", "source": "alpha.rsi.review"}], evidence_kind="measured")
    bundle.finalize()
    record = review.open_review(candidate_id, _review_evidence(), risk="R2", clock=_fixed_clock)
    if approve_review and record.request_id is not None:
        ApprovalQueue(review.DEFAULT_PROJECT_ID).resolve_request(record.request_id, approved=True, resolved_by="reviewer_alice", comment="measured evidence checks out")
    return {"candidate_id": candidate_id, "bundle_dir": bundle.bundle_dir, "request_id": record.request_id}


def _gate_map(decision) -> dict:
    return {entry["gate"]: entry for entry in decision.gates}


def _assert_no_invented_fields(payload: dict) -> None:
    """Recursive forbidden-key scan + fabricated-number/key-form scans over generated decision output."""

    def _normalize(key):
        return str(key).lower().replace("_", "")

    def _walk(node, trail=()):
        if isinstance(node, dict):
            for key, value in node.items():
                assert _normalize(key) not in FORBIDDEN_GENERATED_KEYS, f"fabricated metric key {key!r} at {trail}"
                _walk(value, trail + (key,))
        elif isinstance(node, list):
            for position, value in enumerate(node):
                _walk(value, trail + (str(position),))

    _walk(payload, ("decision",))
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    for forbidden in FORBIDDEN_FABRICATED_NUMBERS:
        assert forbidden not in serialized, f"fabricated default {forbidden!r} found in decision output"
    for forbidden in FORBIDDEN_GENERATED_KEY_FORMS:
        assert forbidden not in serialized, f"bare metric key {forbidden!r} originated by the decision"


# ---------------------------------------------------------------------------
# Closed gate set + missing-everything baseline (fail-closed, never silent)
# ---------------------------------------------------------------------------


def test_gate_order_is_pinned_and_every_reason_is_real():
    decision = promotion.decide("cand-never-existed")
    assert [entry["gate"] for entry in decision.gates] == EXPECTED_GATES
    assert EXPECTED_GATES == list(promotion.GATE_ORDER)  # implementation constant == literal order
    assert decision.candidate_id == "cand-never-existed"
    assert decision.promoted is False
    for entry in decision.gates:
        assert set(entry) == {"gate", "ok", "reason"}
        assert isinstance(entry["ok"], bool)
        assert entry["reason"].strip(), f"gate {entry['gate']} disclosed an empty reason"
    gates = _gate_map(decision)
    assert gates["lineage"]["ok"] is False and "no RSI lineage record" in gates["lineage"]["reason"]
    assert gates["bundle_integrity"]["ok"] is False and "no bundle directory" in gates["bundle_integrity"]["reason"]
    assert gates["evolution_route"]["ok"] is False
    assert gates["evolution_route"]["reason"].startswith("not routed:")  # the engine was never invoked
    assert "lineage: no RSI lineage record" in decision.reason  # joined as <gate>: <reason>
    assert "bundle_integrity: no bundle directory" in decision.reason
    _assert_no_invented_fields(decision.to_dict())


# ---------------------------------------------------------------------------
# Failure injections: promoted is False AND the real reason substring
# ---------------------------------------------------------------------------


def test_missing_bundle_fails_closed_with_real_reason():
    handles = _setup_green()
    shutil.rmtree(handles["bundle_dir"])
    decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert decision.promoted is False
    assert "no bundle directory" in decision.reason
    gates = _gate_map(decision)
    assert gates["bundle_integrity"]["ok"] is False and "no bundle directory" in gates["bundle_integrity"]["reason"]
    assert gates["lineage"]["ok"] is True  # the real lineage gate still passes — injection is isolated
    assert gates["evolution_route"]["ok"] is False
    assert gates["evolution_route"]["reason"].startswith("not routed:")


def test_corrupt_bundle_index_fails_closed_with_real_reason():
    handles = _setup_green()
    (handles["bundle_dir"] / "bundle_index.json").write_text("{not json", encoding="utf-8")
    decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert decision.promoted is False
    assert "corrupt bundle index" in decision.reason
    assert _gate_map(decision)["bundle_integrity"]["ok"] is False
    assert _gate_map(decision)["lineage"]["ok"] is True
    ok, _ = verify_bundle(handles["candidate_id"])
    assert ok is False  # the REAL C2a verifier agrees, independently


def test_unverified_holdout_fails_closed_with_real_reason():
    handles = _setup_green(holdout_payload=holdout_not_run("holdout not run: candidate_view was not provided"))
    decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert decision.promoted is False
    assert "holdout unverified — cannot gate on unverified evidence" in decision.reason  # the landed holdout_gate text
    assert "0.5-neutral" in decision.reason  # unverified disclosed as neutral, never a pass (C2a standard text)
    gates = _gate_map(decision)
    # verbatim passthrough: the real landed holdout_gate's own tuple lands in the gate entry
    assert gates["holdout"]["ok"] is False
    assert gates["holdout"]["reason"] == holdout_gate(holdout_not_run("holdout not run: candidate_view was not provided"))[1]
    assert gates["bundle_integrity"]["ok"] is True  # index is consistent — integrity is green, kind is not
    assert gates["evolution_route"]["reason"].startswith("not routed:")


def test_simulated_evidence_never_gates_anywhere():
    handles = _setup_green(holdout_payload={"evidence_kind": "simulated", "note": "RSI preview constant"})
    decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert decision.promoted is False
    gates = _gate_map(decision)
    assert "evidence_kind='simulated'" in gates["evidence_standard"]["reason"]
    assert "simulated evidence may exist but never gates" in gates["evidence_standard"]["reason"]  # REAL C2a text
    assert gates["evolution_route"]["reason"].startswith("not routed:")  # simulated evidence never reaches routing

    # a simulated LINEAGE record can never gate either
    simulated_id = "cand-simulated-lineage"
    RsiLineageStore().record({"candidate_id": simulated_id, "payload": {"note": "sim"}, "evidence_kind": "simulated"}, parent_id=None, mutation_operator="t")
    sim_decision = promotion.decide(simulated_id)
    assert sim_decision.promoted is False
    sim_gates = _gate_map(sim_decision)
    assert sim_gates["lineage"]["ok"] is False
    assert "simulated evidence can never gate" in sim_gates["lineage"]["reason"]


def test_pending_review_fails_closed_with_real_reason():
    handles = _setup_green(approve_review=False)
    decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert decision.promoted is False
    assert "awaiting human review" in decision.reason
    gates = _gate_map(decision)
    assert gates["human_review"]["ok"] is False
    assert gates["human_review"]["reason"].startswith("awaiting human review: approval request")
    # verbatim passthrough: the real review module's own reason lands in the gate entry
    assert gates["human_review"]["reason"] == review.review_decision(handles["candidate_id"]).reason
    for green_gate in ("lineage", "bundle_integrity", "evidence_standard", "holdout"):
        assert gates[green_gate]["ok"] is True
    assert gates["evolution_route"]["reason"].startswith("not routed:")


def test_rejected_review_fails_closed_with_real_reason():
    handles = _setup_green(approve_review=False)
    comment = "held: upstream benchmark audit incomplete"
    ApprovalQueue(review.DEFAULT_PROJECT_ID).resolve_request(handles["request_id"], approved=False, resolved_by="reviewer_alice", comment=comment)
    decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert decision.promoted is False
    assert comment in decision.reason  # the human's verbatim rejection comment is the real reason
    gates = _gate_map(decision)
    assert gates["human_review"]["ok"] is False
    assert review.human_reviewed(handles["candidate_id"]) is False  # the REAL C2b gate agrees
    assert gates["evolution_route"]["reason"].startswith("not routed:")


def test_missing_lineage_fails_closed_with_real_reason():
    handles = _setup_green(record_lineage=False)
    decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert decision.promoted is False
    assert "no RSI lineage record" in decision.reason
    gates = _gate_map(decision)
    assert gates["lineage"]["ok"] is False
    assert gates["lineage"]["reason"].startswith("no RSI lineage record")
    assert gates["bundle_integrity"]["ok"] is True  # injection isolated to lineage (+ its honest review fallout)
    assert gates["evolution_route"]["reason"].startswith("not routed:")


def test_corrupt_lineage_fails_closed_with_real_reason():
    handles = _setup_green()
    lineage_path = runtime_home() / "rsi" / "lineage.jsonl"
    lineage_path.write_text("{not json — corrupt line\n", encoding="utf-8")
    decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert decision.promoted is False
    assert "no RSI lineage record" in decision.reason
    assert "corrupt/partial lineage line(s) skipped" in decision.reason  # corruption disclosed, never repaired
    gates = _gate_map(decision)
    assert gates["lineage"]["ok"] is False
    for green_gate in ("bundle_integrity", "evidence_standard", "holdout", "human_review"):
        assert gates[green_gate]["ok"] is True  # injection isolated: the approved review and bundle still stand
    assert gates["evolution_route"]["reason"].startswith("not routed:")


# ---------------------------------------------------------------------------
# The seam: honest routing in both directions (only stub point in this file)
# ---------------------------------------------------------------------------


def test_seam_failure_reason_flows_into_the_decision(monkeypatch):
    handles = _setup_green()
    recorded = {}

    def _stub(candidate_id, baseline, *, human_approved):
        recorded.update({"candidate_id": candidate_id, "baseline": baseline, "human_approved": human_approved})
        return False, "awaiting human approval"

    monkeypatch.setattr(promotion, "route_promotion_decision", _stub)  # the ONE allowed stub point
    decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert all(entry["ok"] for entry in decision.gates[:-1])  # every REAL gate ran green
    assert decision.promoted is False
    assert decision.gates[-1] == {"gate": "evolution_route", "ok": False, "reason": "awaiting human approval"}
    assert "evolution_route: awaiting human approval" in decision.reason
    assert recorded == {"candidate_id": handles["candidate_id"], "baseline": BASELINE, "human_approved": True}


def test_seam_exception_fails_closed_and_is_logged(monkeypatch, caplog):
    handles = _setup_green()

    def _boom(candidate_id, baseline, *, human_approved):
        raise RuntimeError("routing transport down")

    monkeypatch.setattr(promotion, "route_promotion_decision", _boom)
    with caplog.at_level(logging.WARNING, logger="alpha.rsi.promotion"):
        decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert decision.promoted is False
    assert "RuntimeError: routing transport down" in decision.reason  # real text, no bare except
    entry = decision.gates[-1]
    assert entry["gate"] == "evolution_route" and entry["ok"] is False
    assert "failed closed: RuntimeError: routing transport down" in entry["reason"]
    assert any("routing transport down" in record.getMessage() for record in caplog.records)  # logged, not swallowed


def test_failed_composition_never_invokes_the_routing_seam(monkeypatch):
    calls = []

    def _stub(candidate_id, baseline, *, human_approved):
        calls.append(candidate_id)
        return True, "should never be reached"

    monkeypatch.setattr(promotion, "route_promotion_decision", _stub)
    decision = promotion.decide("cand-nothing-on-disk")
    assert calls == []  # a failed composition is never routed
    assert decision.promoted is False
    assert decision.gates[-1]["reason"].startswith("not routed:")
    assert "the evolution engine gate was not invoked" in decision.gates[-1]["reason"]


# ---------------------------------------------------------------------------
# Evolution wiring: the REAL landed dispatch API, pinned
# ---------------------------------------------------------------------------


def test_routing_matches_the_landed_gate_request_contract():
    # The landed router contract this wiring mirrors (real API, pinned; omission never grants autonomy)
    request = GateRequest()
    assert request.human_approved is False
    assert request.autonomous_mode is False

    # The exact engine.gate signature the landed GateRequest handler calls
    assert list(inspect.signature(EvolutionEngine.gate).parameters) == ["self", "candidate_id", "baseline", "human_approved", "autonomous_mode"]

    # route_evolution_gate answers with the engine's real verdict, verbatim
    assert route_evolution_gate("cand-not-in-engine", {"passed": 1, "failed": 0}, human_approved=True) == (False, "missing candidate or benchmark")


def test_decide_promotion_entry_composes_through_the_evolution_surface():
    handles = _setup_green()
    decision = decide_promotion(handles["candidate_id"], baseline=BASELINE)
    assert isinstance(decision, promotion.PromotionDecision)
    assert decision.promoted is True and decision.reason == "promoted"

    # an unbundled candidate fails honestly through the same entry point
    broken = decide_promotion("cand-never-existed")
    assert broken.promoted is False
    assert "no bundle directory" in broken.reason


# ---------------------------------------------------------------------------
# Positive path: REAL modules end-to-end under a tmp AGENT_WORKSPACE_HOME
# ---------------------------------------------------------------------------


def test_positive_path_promotes_through_real_modules():
    handles = _setup_green()
    candidate_id = handles["candidate_id"]

    # preconditions are real, not assumed: the bundle verifies and the human approved
    ok, verify_reason = verify_bundle(candidate_id)
    assert ok is True and "3 file(s)" in verify_reason
    assert review.human_reviewed(candidate_id) is True

    decision = promotion.decide(candidate_id, baseline=BASELINE)
    assert decision.promoted is True
    assert decision.reason == "promoted"  # the evolution engine's own verdict, verbatim
    gates = _gate_map(decision)
    assert [entry["gate"] for entry in decision.gates] == EXPECTED_GATES
    assert all(entry["ok"] for entry in decision.gates)
    assert "evidence_kind='measured'" in gates["lineage"]["reason"]
    assert "verified" in gates["bundle_integrity"]["reason"]  # real sha256 verification text
    assert "evidence_kind='measured'" in gates["evidence_standard"]["reason"]
    assert gates["holdout"]["reason"] == "holdout passed"  # REAL executed holdout gate
    assert "reviewer_alice" in gates["human_review"]["reason"]  # REAL approved review, verbatim identity
    assert gates["evolution_route"]["reason"] == "promoted"  # routed through the REAL engine

    # the routing really happened on the landed dispatch surface: its own ledger says so
    events = [event for event in get_evolution_engine().ledger() if event["candidate_id"] == candidate_id]
    assert any(event["event"] == "promoted" for event in events)

    payload = decision.to_dict()
    assert set(payload) == {"candidate_id", "promoted", "reason", "gates"}  # exact honest keys, no growth
    _assert_no_invented_fields(payload)
    assert all(entry["evidence_kind"] in WHITELIST for entry in json.loads((handles["bundle_dir"] / "bundle_index.json").read_text(encoding="utf-8"))["files"].values())


def test_routing_with_an_undisclosed_reason_fails_closed(monkeypatch, caplog):
    """A routing verdict without its real text can never promote (the module's no-empty-reason invariant, applied to routing too)."""
    handles = _setup_green()

    def _empty_reason(candidate_id, baseline, *, human_approved):
        return True, ""

    monkeypatch.setattr(promotion, "route_promotion_decision", _empty_reason)
    with caplog.at_level(logging.WARNING, logger="alpha.rsi.promotion"):
        decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert decision.promoted is False  # True-with-no-reason is not a promotable verdict
    entry = decision.gates[-1]
    assert entry["gate"] == "evolution_route" and entry["ok"] is False
    assert "empty reason" in entry["reason"]
    assert "evolution_route: " in decision.reason  # joined like every other failing gate
    assert any("empty reason" in record.getMessage() for record in caplog.records)  # logged, not silent
