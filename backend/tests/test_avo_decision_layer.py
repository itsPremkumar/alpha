"""Tests for the AVO decision layer: commit rule, lineage, authority, honesty.

The properties under test are the four AVO names for the commit rule, plus the
three things AVO's gate does not have:

* AVO P1 - correctness is a hard zero, never a weighted term
* AVO P2 - comparison is against the BEST COMMITTED version, not the parent
* AVO P3 - failed attempts stay in the trajectory and out of the lineage
* AVO P4 - each committed version is versioned with its score and survives restart
* ALPHA - invariants are the second conjunct, the one neither project had
* ALPHA - the scorer and the gate are server-owned
* ALPHA - the lineage is tamper-evident

Each test that proves a guard *bites* has a sibling comment naming the guard it
would fail without, and the ``test_*_bites_*`` cases below deliberately build the
attack rather than assert the happy path only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha.avo.commit_gate import CommitGate
from alpha.avo.evidence import (
    ChangeKind,
    InvariantEvidence,
    RejectionCategory,
    RejectionReason,
    VerificationReceipt,
    classify_change,
    code_digest,
    record_score,
)
from alpha.avo.honesty import CommitStep, assert_gains_carry_denominators, build_honesty_report
from alpha.avo.lineage import AVOLineage, VersionRecord
from alpha.avo.lineage_chain import seal_entry, verify_entries
from alpha.avo.persistence import AVOPersistenceManager, LineageIntegrityError
from alpha.avo.scorer_authority import (
    GateRuleSet,
    ScorerAuthorityViolation,
    ScorerProposalLedger,
    assert_candidate_target_permitted,
    gate_fingerprint,
    is_server_owned_path,
)
from alpha.avo.scoring import EvaluationVector
from alpha.avo.trajectory import TrajectoryView
from alpha.avo.workspace_runner import WorkspaceAVORunner

# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

BASELINE = "def compute(x):\n    return x + 1\n"
FASTER = "def compute(x):\n    return x + 1.0\n"
BEHAVIOUR_BREAKING = "def compute(x):\n    return x * 2\n"  # score improves, behaviour changes


def make_candidate(*, correctness: bool, throughput: float, code: str = FASTER, **kwargs) -> tuple[VersionRecord, str]:
    """A candidate carrying a single-metric vector, so scores are comparable."""
    digest = code_digest(code)
    metadata = {"code": code}
    metadata.update(kwargs.pop("metadata", {}))
    record = VersionRecord(
        correctness=correctness,
        performance_score=min(1.0, throughput / 100.0),
        quality_score=1.0 if correctness else 0.0,
        vector=EvaluationVector(metrics={"throughput": throughput}, correctness=correctness),
        metadata=metadata,
        **kwargs,
    )
    return record, digest


def receipt(digest: str, *, passed: bool = True, exit_code: int = 0) -> VerificationReceipt:
    return VerificationReceipt(
        receipt_id=f"vr_{digest[:8]}",
        target_digest=digest,
        oracle="pytest -q",
        exit_code=exit_code,
        passed=passed,
    )


def invariants(digest: str, *, regressions: int = 0, scope: str = "measured", consistency: float = 1.0) -> InvariantEvidence:
    return InvariantEvidence(
        source="differential_invariant_fuzzer",
        target_digest=digest,
        scope=scope,
        trials=40,
        regressions=regressions,
        consistency_score=consistency,
    )


def gate(rules: GateRuleSet | None = None) -> CommitGate:
    return CommitGate(AVOLineage(), rules=rules)


# ======================================================================
# (a) correctness is a hard zero, never a weighted term
# ======================================================================


def test_a_correctness_failure_scores_zero_regardless_of_other_numbers() -> None:
    """(a) A candidate failing correctness scores ZERO however good its numbers are."""
    g = gate()
    digest = code_digest(FASTER)
    candidate, _ = make_candidate(correctness=False, throughput=9_999_999.0, code=BASELINE)

    decision = g.promote(
        candidate,
        receipt=receipt(digest, passed=False, exit_code=1),
        invariant=invariants(digest),
        target_digest=digest,
    )

    assert record_score(candidate) == 0.0, "a correctness failure must be a hard zero"
    assert decision.committed is False
    assert decision.category is RejectionCategory.CORRECTNESS
    assert decision.candidate_score == 0.0


def test_a_bites_without_the_hard_zero() -> None:
    """The guard: the same candidate commits the moment correctness is not enforced.

    Without the conjunct ordering in ``CommitGate.evaluate``, a composite built
    from a spectacular throughput number would carry a failing candidate through.
    """
    lax = GateRuleSet(require_correctness_receipt=False, require_invariants=False)
    candidate, _ = make_candidate(correctness=True, throughput=1.0, code=BASELINE)

    # The arithmetic the gate must never perform: a weighted average that lets a
    # high throughput term offset a correctness failure.
    candidate.compute_composite()
    weighted = 0.5 * 1.0 + 0.3 * min(1.0, candidate.performance_score) + 0.2 * min(1.0, candidate.quality_score)
    assert weighted > 0.5, "a naive weighted score would look like a pass here"

    decision = gate(lax).promote(candidate, target_digest=code_digest(BASELINE), evaluating_fingerprint=gate(lax).fingerprint)
    assert decision.committed is True, "with correctness unchecked the same shape does commit - that is the bug being prevented"

    strict = gate()
    assert strict.promote(candidate, receipt=None, invariant=None).committed is False


def test_a_score_is_never_computed_for_a_correctness_failure() -> None:
    candidate, _ = make_candidate(correctness=False, throughput=500.0, code=BASELINE)
    assert record_score(candidate) == 0.0
    candidate.compute_composite()
    assert candidate.composite_score == 0.0


# ======================================================================
# (b) comparison is against the BEST COMMITTED, not the parent
# ======================================================================


def test_b_regression_against_best_committed_is_rejected_even_when_it_beats_its_parent() -> None:
    """(b) The AVO rule verbatim: compare to the best committed version, not the previous one."""
    g = gate()
    best_code, best_throughput = BASELINE, 500.0
    parent_code, parent_throughput = FASTER, 100.0
    regressor_code, regressor_throughput = BASELINE, 200.0

    b, bd = make_candidate(correctness=True, throughput=best_throughput, code=best_code, hypothesis="best")
    assert g.promote(b, receipt=receipt(bd), invariant=invariants(bd), target_digest=bd).committed is True

    # A weak parent, itself a regression, so it never enters the lineage.
    p, pd = make_candidate(correctness=True, throughput=parent_throughput, code=parent_code, parent_id=b.version_id, hypothesis="parent")
    assert g.promote(p, receipt=receipt(pd), invariant=invariants(pd), target_digest=pd).committed is False

    # The case that matters: a candidate above its immediate parent but far
    # below the best committed version.
    r, rd = make_candidate(correctness=True, throughput=regressor_throughput, code=regressor_code, parent_id=p.version_id, hypothesis="regressor")
    decision = g.promote(r, receipt=receipt(rd), invariant=invariants(rd), target_digest=rd)

    assert decision.committed is False, "a regression against the best committed version must be rejected"
    assert decision.category is RejectionCategory.REGRESSION
    assert decision.best_committed_id == b.version_id
    assert decision.reason_code == RejectionReason.SCORE_REGRESSION.value
    assert "best committed" in decision.reason
    assert record_score(r) > record_score(p), "the candidate does beat its parent; that is not the comparison"


def test_b_bites_lineage_compares_against_best_not_parent() -> None:
    """The same defect in the in-memory ``commit_candidate`` path.

    ``AVOLineage.commit_candidate`` compared against ``candidate.parent_id``. Here
    the parent is deliberately WEAKER than the best committed version, so the two
    rules disagree, and the best-committed rule must win.
    """
    lineage = AVOLineage()
    v1 = VersionRecord(correctness=True, performance_score=0.5, quality_score=0.5, hypothesis="v1")
    assert lineage.commit_candidate(v1) is True
    v2 = VersionRecord(correctness=True, performance_score=0.9, quality_score=0.9, parent_id=v1.version_id, hypothesis="v2")
    assert lineage.commit_candidate(v2) is True
    assert lineage.head_id == v2.version_id

    # Parent is v1 (0.5). This beats its parent but is far below the best
    # committed (v2, 0.9). The old rule would have committed it.
    v3 = VersionRecord(correctness=True, performance_score=0.7, quality_score=0.7, parent_id=v1.version_id, hypothesis="v3")
    assert lineage.commit_candidate(v3) is False, (
        "a candidate above its parent (0.7 > 0.5) but below the best committed (0.9) must be rejected"
    )
    assert v3.metadata["compared_against"] == v2.version_id
    assert lineage.best_committed() is v2
    assert lineage.head_id == v2.version_id, "a rejection must not move the head"


# ======================================================================
# (c) THE COMPOSITION TEST: score up, invariant violated
# ======================================================================


def test_c_score_improving_invariant_violating_change_is_rejected() -> None:
    """(c) The test worth more than the rest of this file.

    AVO's gate is score-based only, so AVO would commit this change. alpha's
    composition must not.
    """
    g = gate()
    base, bd = make_candidate(correctness=True, throughput=100.0, code=BASELINE, hypothesis="baseline")
    assert g.promote(base, receipt=receipt(bd), invariant=invariants(bd), target_digest=bd).committed is True

    # Faster -- the measured score roughly doubles.
    faster, fd = make_candidate(correctness=True, throughput=200.0, code=BEHAVIOUR_BREAKING, parent_id=base.version_id, hypothesis="double the speed")
    assert record_score(faster) > record_score(base), "the change must genuinely improve the score"

    # ...and it violates a known invariant: the fuzzer sees 3 behavioural
    # regressions on paths the baseline satisfied.
    violating = invariants(fd, regressions=3, consistency=0.61)
    decision = g.promote(faster, receipt=receipt(fd), invariant=violating, target_digest=fd)

    assert decision.committed is False
    assert decision.category is RejectionCategory.INVARIANT
    assert decision.reason_code == RejectionReason.INVARIANT_VIOLATED.value
    assert decision.invariant_scope == "measured"
    assert decision.candidate_score > 0.0, "the score improvement was real; the invariant is what rejected it"

    # And the rejected candidate is NOT in the committed lineage.
    assert faster.version_id not in g.lineage.versions
    assert any(r.version_id == faster.version_id for r in g.lineage.rejected_attempts)


def test_c_bites_when_invariants_are_not_required() -> None:
    """AVO's gate, verbatim: identical candidate, invariants not checked, it commits."""
    avo_like = GateRuleSet(require_invariants=False)
    g = gate(avo_like)
    base, bd = make_candidate(correctness=True, throughput=100.0, code=BASELINE)
    g.promote(base, receipt=receipt(bd), invariant=invariants(bd), target_digest=bd, evaluating_fingerprint=g.fingerprint)

    faster, fd = make_candidate(correctness=True, throughput=200.0, code=BEHAVIOUR_BREAKING)
    avo_decision = g.promote(faster, receipt=receipt(fd), invariant=invariants(fd, regressions=3, consistency=0.61), target_digest=fd)
    assert avo_decision.committed is True, "this is exactly what AVO's score-only gate would do"

    strict = gate()
    base2, bd2 = make_candidate(correctness=True, throughput=100.0, code=BASELINE)
    strict.promote(base2, receipt=receipt(bd2), invariant=invariants(bd2), target_digest=bd2)
    faster2, fd2 = make_candidate(correctness=True, throughput=200.0, code=BEHAVIOUR_BREAKING)
    assert strict.promote(faster2, receipt=receipt(fd2), invariant=invariants(fd2, regressions=3, consistency=0.61), target_digest=fd2).committed is False


def test_c_invariant_evidence_for_another_revision_is_not_evidence() -> None:
    g = gate()
    candidate, digest = make_candidate(correctness=True, throughput=500.0, code=FASTER)
    decision = g.promote(
        candidate,
        receipt=receipt(digest),
        invariant=invariants(code_digest(BASELINE)),  # evidence for a DIFFERENT revision
        target_digest=digest,
    )
    assert decision.committed is False
    assert decision.reason_code == RejectionReason.INVARIANT_EVIDENCE_STALE.value


def test_c_missing_invariant_evidence_is_a_rejection_not_a_pass() -> None:
    g = gate()
    candidate, digest = make_candidate(correctness=True, throughput=500.0, code=FASTER)
    decision = g.promote(candidate, receipt=receipt(digest), invariant=None, target_digest=digest)
    assert decision.committed is False
    assert decision.reason_code == RejectionReason.INVARIANT_EVIDENCE_MISSING.value


# ======================================================================
# (d) trajectory keeps failures, lineage keeps commits, reason is recorded
# ======================================================================


def test_d_failed_attempts_stay_in_the_trajectory_and_out_of_the_lineage_with_distinct_reasons() -> None:
    """(d) Four distinct rejection categories, all recorded, none in the lineage."""
    g = gate()
    base, bd = make_candidate(correctness=True, throughput=400.0, code=BASELINE)
    g.promote(base, receipt=receipt(bd), invariant=invariants(bd), target_digest=bd)

    # correctness failure
    broken, bkd = make_candidate(correctness=False, throughput=9999.0, code=BASELINE)
    g.promote(broken, receipt=receipt(bkd, passed=False, exit_code=1), invariant=invariants(bkd), target_digest=bkd)

    # invariant violation
    violating, vd = make_candidate(correctness=True, throughput=900.0, code=BEHAVIOUR_BREAKING)
    g.promote(violating, receipt=receipt(vd), invariant=invariants(vd, regressions=2, consistency=0.7), target_digest=vd)

    # score regression
    regressor, rd = make_candidate(correctness=True, throughput=10.0, code=BASELINE)
    g.promote(regressor, receipt=receipt(rd), invariant=invariants(rd), target_digest=rd)

    # abandoned: walked away from before evaluation completed
    abandoned, ad = make_candidate(correctness=True, throughput=1.0, code=FASTER)
    g.abandon(abandoned, author="agent", detail="profiling showed no headroom; abandoning this line")

    categories = {r.metadata.get("rejection_category") for r in g.lineage.rejected_attempts}
    assert categories == {"correctness", "invariant", "regression", "abandoned"}, f"categories not distinguished: {categories}"

    assert {base.version_id} == set(g.lineage.versions), "only the accepted version may be committed"
    rejected_ids = {r.version_id for r in g.lineage.rejected_attempts}
    assert rejected_ids == {broken.version_id, violating.version_id, regressor.version_id, abandoned.version_id}
    for record in g.lineage.rejected_attempts:
        assert record.rejection_reason, "a rejected candidate with no reason is not a record"

    # The archive merges them for the agent, but the two sets stay separate.
    archive_ids = {r.version_id for r in g.lineage.get_trajectory_archive()}
    assert archive_ids == rejected_ids | {base.version_id}
    assert rejected_ids & set(g.lineage.versions) == set(), "trajectory and lineage must not overlap"


def test_d_gate_records_every_decision() -> None:
    g = gate()
    candidate, digest = make_candidate(correctness=True, throughput=10.0, code=FASTER)
    g.promote(candidate, receipt=receipt(digest), invariant=invariants(digest), target_digest=digest)
    assert g.stats()["decisions"] == 1
    assert g.stats()["committed"] == 1
    assert g.history[0].to_dict()["category"] is None


# ======================================================================
# (e) the lineage is tamper-evident
# ======================================================================


def test_e_mutating_a_historical_committed_version_is_detected() -> None:
    """(e) Edit a committed version's score in memory; the chain notices."""
    lineage = AVOLineage()
    first = VersionRecord(correctness=True, performance_score=0.5, quality_score=0.5, hypothesis="v1")
    lineage.commit_candidate(first)
    second = VersionRecord(correctness=True, performance_score=0.8, quality_score=0.8, parent_id=first.version_id, hypothesis="v2")
    lineage.commit_candidate(second)

    assert lineage.verify_chain().ok is True
    assert len(lineage.chain) == 2

    # Rewrite history: inflate the first version's recorded score.
    lineage.chain[0]["payload"]["composite_score"] = 0.99
    verdict = lineage.verify_chain()
    assert verdict.ok is False
    assert verdict.broken_at_seq == 1
    assert "does not match its contents" in (verdict.defect or "")


def test_e_deleting_a_historical_entry_is_detected() -> None:
    lineage = AVOLineage()
    for perf in (0.4, 0.5, 0.6):
        assert lineage.commit_candidate(VersionRecord(correctness=True, performance_score=perf, quality_score=perf))
    assert lineage.verify_chain().ok is True
    del lineage.chain[1]
    verdict = lineage.verify_chain()
    assert verdict.ok is False
    assert any("prev_hash" in d or "sequence" in d for d in verdict.defects)


def test_e_reordering_is_detected() -> None:
    lineage = AVOLineage()
    for perf in (0.4, 0.5, 0.6):
        lineage.commit_candidate(VersionRecord(correctness=True, performance_score=perf, quality_score=perf))
    lineage.chain[1], lineage.chain[2] = lineage.chain[2], lineage.chain[1]
    assert lineage.verify_chain().ok is False


def test_e_persisted_lineage_survives_restart_and_refuses_a_tampered_load(tmp_path: Path) -> None:
    """(d)+(e): the lineage survives a restart, and a tampered file is refused on load."""
    mgr = AVOPersistenceManager(base_dir=tmp_path)
    lineage = AVOLineage()
    for perf, qual in ((0.4, 0.4), (0.8, 0.8)):
        assert lineage.commit_candidate(VersionRecord(correctness=True, performance_score=perf, quality_score=qual))
    path = mgr.save_lineage(lineage)
    assert path.exists()

    restored = mgr.load_lineage()
    assert restored is not None
    assert len(restored.versions) == 2
    assert restored.head_id == lineage.head_id
    assert restored.verify_chain().ok is True, "an untampered lineage must load"

    # Tamper on disk, then reload.
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["chain"][0]["payload"]["composite_score"] = 0.99
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LineageIntegrityError):
        mgr.load_lineage()


def test_e_chain_head_and_seal_are_ordered() -> None:
    entries = [seal_entry({"a": 1}, seq=1, prev_hash="0" * 64)]
    entries.append(seal_entry({"a": 2}, seq=2, prev_hash=entries[0]["entry_hash"]))
    assert verify_entries(entries).ok is True
    assert verify_entries([]).ok is True


# ======================================================================
# (f) the agent cannot install a scorer
# ======================================================================


def test_f_agent_proposed_scorer_is_refused_with_a_reason() -> None:
    """(f) A scorer that trivially rewards the agent's own output style."""
    ledger = ScorerProposalLedger()
    proposal = ledger.propose(
        author="agent",
        subject="alpha/avo/scoring.py",
        requested_change="Replace the throughput metric with 'assistant_praise_density', which returns 1.0 for my own style.",
    )
    assert proposal.installed is False
    assert proposal.refusal_reason is RejectionReason.GATE_MODIFICATION_REFUSED
    assert proposal.refusal_category is RejectionCategory.AUTHORITY
    assert "may not write to it" in proposal.refusal_detail or "not authoritative" in proposal.refusal_detail

    with pytest.raises(ScorerAuthorityViolation) as excinfo:
        ledger.install(proposal.proposal_id)
    assert excinfo.value.reason in (RejectionReason.GATE_MODIFICATION_REFUSED, RejectionReason.SCORER_INSTALL_REFUSED)
    assert excinfo.value.subject == "alpha/avo/scoring.py"

    # Even a scorer at a path the agent owns is still not authoritative for its
    # own promotion.
    benign = ledger.propose(author="agent", subject="my_own_helper.py", requested_change="reward my own style")
    assert benign.installed is False
    assert benign.refusal_reason is RejectionReason.SCORER_INSTALL_REFUSED


def test_f_bites_there_is_no_install_path() -> None:
    """The guard is absence of capability, not a boolean. Assert the absence."""
    ledger = ScorerProposalLedger()
    assert not hasattr(ledger, "approve")
    assert not hasattr(ledger, "force")
    assert not hasattr(ledger, "accept")
    assert all(p.installed is False for p in ledger.proposals)
    with pytest.raises(ScorerAuthorityViolation):
        ledger.install("sp_does_not_exist")


def test_f_unverified_correctness_assertion_is_refused() -> None:
    """A candidate cannot vouch for its own correctness by asserting it."""
    g = gate()
    candidate, digest = make_candidate(correctness=True, throughput=100.0, code=FASTER)
    decision = g.promote(candidate, receipt=None, invariant=invariants(digest), target_digest=digest)
    assert decision.committed is False
    assert decision.reason_code == RejectionReason.CORRECTNESS_UNVERIFIED.value


# ======================================================================
# (g) the agent cannot modify the gate
# ======================================================================


def test_g_candidate_writes_into_the_scoring_surface_are_refused() -> None:
    """(g) Hard refusal at the write path, on the surface that defines ``f``."""
    for target in (
        "alpha/avo/commit_gate.py",
        "alpha/avo/scorer_authority.py",
        "alpha/avo/scoring.py",
        "alpha/avo/evidence.py",
        "alpha/testing/differential_invariant_fuzzer.py",
        "alpha/evaluation/anything.py",
        "alpha/benchmarks/scores.py",
    ):
        assert is_server_owned_path(target) is not None, f"{target} should be server-owned"
        with pytest.raises(Exception) as excinfo:
            assert_candidate_target_permitted(target)
        assert "server-owned scoring surface" in str(excinfo.value)

    # A normal project file is untouched.
    assert is_server_owned_path("src/my_module.py") is None
    assert_candidate_target_permitted("src/my_module.py")


def test_g_bites_the_write_path_actually_calls_the_guard(tmp_path: Path) -> None:
    """Reachable from the runner, not just the helper.

    A candidate confined to its own project root cannot name a scorer path at
    all, so this asserts the guard is *consulted* on the real write path by
    pointing a runner at a workspace whose root IS a server-owned subtree.
    """
    root = tmp_path / "alpha" / "avo"
    root.mkdir(parents=True)
    target = root / "scoring.py"
    target.write_text("x = 1\n", encoding="utf-8")
    runner = WorkspaceAVORunner(root_path=root)
    result = runner.run_workspace_variation(
        target_file_path="scoring.py",
        candidate_code="x = 2\n",
        hypothesis="raise my own score",
        modification="x = 1 -> x = 2",
    )
    assert result["success"] is False
    assert "server-owned scoring surface" in result["error"]
    assert target.read_text(encoding="utf-8") == "x = 1\n", "the refused write must not have landed"


def test_gate_fingerprint_changes_when_a_rule_changes() -> None:
    v1 = GateRuleSet(version=1)
    v2 = GateRuleSet(version=1, require_invariants=False)
    assert gate_fingerprint(v1.to_dict()) != gate_fingerprint(v2.to_dict())
    assert v1.fingerprint == GateRuleSet(version=1).fingerprint


# ======================================================================
# (h) a change to the scorer is judged by the PREVIOUS gate
# ======================================================================


def test_h_scorer_change_is_evaluated_by_the_previous_gate_not_the_proposed_one() -> None:
    """(h) The gate cannot be rewritten and used in the same step."""
    lineage = AVOLineage()
    previous_gate = CommitGate(lineage, rules=GateRuleSet(version=1))
    proposed_gate = CommitGate(lineage, rules=GateRuleSet(version=2, require_invariants=False))
    assert previous_gate.fingerprint != proposed_gate.fingerprint

    base, bd = make_candidate(correctness=True, throughput=100.0, code=BASELINE)
    previous_gate.promote(base, receipt=receipt(bd), invariant=invariants(bd), target_digest=bd)

    # The candidate is a scorer change: it carries the PROPOSED fingerprint and
    # it also breaks an invariant.
    attack, ad = make_candidate(correctness=True, throughput=900.0, code=BEHAVIOUR_BREAKING)
    decision = previous_gate.promote(
        attack,
        receipt=receipt(ad),
        invariant=invariants(ad, regressions=4, consistency=0.5),
        target_digest=ad,
        evaluating_fingerprint=proposed_gate.fingerprint,
    )

    assert decision.committed is False
    assert decision.evaluated_under_previous_gate is True
    assert decision.gate_version == 1, "the decision must be stamped with the gate that made it"
    assert decision.gate_fingerprint == previous_gate.fingerprint
    assert decision.reason_code == RejectionReason.INVARIANT_VIOLATED.value, (
        "the attack was caught by the PREVIOUS gate's invariant rule, not waved through by the proposed weaker one"
    )
    # And the proposed weaker gate, if consulted, would have allowed it -- which
    # is exactly why it must not be the one consulted.
    assert proposed_gate.evaluate(
        attack,
        receipt=receipt(ad),
        invariant=invariants(ad, regressions=4, consistency=0.5),
        target_digest=ad,
        evaluating_fingerprint=proposed_gate.fingerprint,
    ).committed is True


# ======================================================================
# (i) the agent chooses what to vary, and a confident variation still passes
# ======================================================================


def test_i_agent_chooses_the_variation_and_a_confident_one_commits() -> None:
    """(i) The agent picks the edit; the gate still decides. Both halves verified."""
    from alpha.avo.engine import AVOEngine

    engine = AVOEngine()
    seen_contexts: list[dict] = []

    def agent_edit(code: str, context: dict) -> tuple[str, str]:
        # The agent's decision is driven by the lineage context it was handed.
        seen_contexts.append(context)
        return "def compute(x):\n    return x + 2\n", "agent-chosen: add two"

    def evaluate(code: str) -> EvaluationVector:
        return EvaluationVector(metrics={"throughput": 900.0}, correctness=True)

    result = engine.run_agentic_variation(
        base_hypothesis="make compute faster",
        edit_fn=agent_edit,
        evaluate_fn=evaluate,
    )
    assert seen_contexts, "the agent's variation step must run"
    assert "lineage" in seen_contexts[0], "the agent must receive the lineage as context"
    assert seen_contexts[0]["lineage"]["explored_count"] == 0, "an empty lineage is still shown, not skipped"
    assert result["success"] is True, "a variation the agent is confident about must still pass the gate"
    assert result["committed_version_id"] is not None
    assert "lineage_context" in result
    assert "best_committed_id" in result["lineage_context"]


def test_b_best_committed_is_scoped_to_one_benchmark() -> None:
    """Scores from different benchmarks are not commensurable.

    Found by the Phase 8 A/B: a shared lineage compared task B's score against
    task A's best-committed score and rejected B as a regression, so the A/B was
    measuring the gate instead of the agents. The fix is ``task_id``.
    """
    g = gate()
    a, ad = make_candidate(correctness=True, throughput=900.0, code=BASELINE, task_id="A")
    assert g.promote(a, receipt=receipt(ad), invariant=invariants(ad), target_digest=ad).committed is True

    # B's score is far below A's, but that is not a regression: different
    # benchmark.
    b, bd = make_candidate(correctness=True, throughput=2.0, code=FASTER, task_id="B")
    assert g.promote(b, receipt=receipt(bd), invariant=invariants(bd), target_digest=bd).committed is True

    # Within B, the bar is B's own best.
    b_worse, bwd = make_candidate(correctness=True, throughput=1.0, code=FASTER, task_id="B")
    decision = g.promote(b_worse, receipt=receipt(bwd), invariant=invariants(bwd), target_digest=bwd)
    assert decision.committed is False
    assert decision.best_committed_id == b.version_id
    assert decision.detail["task_id"] == "B"

    assert g.lineage.best_committed("A") is a
    assert g.lineage.best_committed("B") is b
    assert g.lineage.best_committed() is a  # unscoped: the global best


def test_i_lineage_context_includes_rejected_attempts_and_reasons() -> None:
    g = gate()
    ok, okd = make_candidate(correctness=True, throughput=500.0, code=BASELINE, modification="seed the baseline")
    g.promote(ok, receipt=receipt(okd), invariant=invariants(okd), target_digest=okd)
    bad, badd = make_candidate(correctness=True, throughput=1.0, code=FASTER, modification="agent-chosen: add two")
    g.promote(bad, receipt=receipt(badd), invariant=invariants(badd), target_digest=badd)

    view = TrajectoryView.from_lineage(g.lineage)
    ctx = view.to_agent_context()
    assert ctx["committed_count"] == 1
    assert ctx["rejected_count"] == 1
    assert ctx["failure_reasons"] == {"regression": 1}
    assert ctx["best_committed_id"] == ok.version_id
    assert "agent-chosen: add two" in ctx["tried_modifications"], ctx["tried_modifications"]
    assert ctx["recent_rejected"][0]["modification"] == "agent-chosen: add two"


# ======================================================================
# (j) a plateaued agent is redirected toward MULTIPLE directions, and recorded
# ======================================================================


def test_j_plateau_produces_a_recorded_multi_direction_redirect() -> None:
    """(j) A redirect, not a kill, and not a single forced successor."""
    from alpha.avo.supervisor import AVOSupervisor

    lineage = AVOLineage()
    good, gd = make_candidate(correctness=True, throughput=500.0, code=BASELINE)
    lineage.commit_candidate(good)
    for i in range(3):
        lineage.commit_candidate(VersionRecord(correctness=True, performance_score=0.1, quality_score=0.1, hypothesis=f"fail-{i}"))

    supervisor = AVOSupervisor(max_no_improve=4)
    for i in range(4):
        stagnated, directive, _ = supervisor.observe_step(improved=False, signature=f"edit-{i}", lineage=lineage)

    assert stagnated is True
    assert directive is not None
    assert len(directive.recommended_directions) >= 2, "a redirect must re-open the fan-out, not pick one successor"
    assert len(directive.recommended_directions) != 1

    assert len(supervisor.redirects) == 1, "the redirect must be recorded"
    record = supervisor.redirects[0].to_dict()
    assert record["trigger"] == "STAGNATION_PIVOT"
    assert record["direction_count"] >= 2
    assert record["sequence"] == 1

    # The redirect was acted on and the action recorded -- otherwise the
    # supervisor is a detector, not a supervisor.
    ack = supervisor.acknowledge_redirect(record["sequence"], directive.recommended_directions[0], "branched from best committed")
    assert ack["recorded"] is True
    assert supervisor.acknowledge_redirect(99, "x", "y")["recorded"] is False

    stats = supervisor.stats()
    assert stats["intervention_mode"] == "redirect"
    assert "FALSE POSITIVES" in stats["detector_bias"]


def test_j_redirect_directions_are_derived_from_the_trajectory() -> None:
    from alpha.avo.supervisor import AVOSupervisor

    lineage = AVOLineage()
    base, bd = make_candidate(correctness=True, throughput=500.0, code=BASELINE)
    lineage.commit_candidate(base)
    bad, badd = make_candidate(correctness=True, throughput=1.0, code=FASTER, modification="the cache-warm attempt")
    lineage.commit_candidate(bad)  # rejected -> lands in rejected_attempts

    supervisor = AVOSupervisor(max_no_improve=2)
    for i in range(2):
        _, directive, _ = supervisor.observe_step(improved=False, signature=f"s{i}", lineage=lineage)
    assert directive is not None
    assert any("cache-warm" in d for d in directive.recommended_directions), (
        f"directions must reference what the lineage actually rejected; got {directive.recommended_directions}"
    )


# ======================================================================
# (k) honesty metrics
# ======================================================================


def test_k_failed_to_committed_ratio_and_change_kinds_are_reported_with_denominators() -> None:
    """(k) No metric reports improvement without its denominator beside it."""
    g = gate()
    steps: list[CommitStep] = []
    prev = 0.0
    # A refinement, a structural change, then another refinement. The middle one
    # moves the module boundary, which is what an AVO inflection point is.
    variants = (
        (FASTER, 100.0, "refinement"),
        ("def compute(x):\n    return x + 1.0\n\n\ndef helper(x):\n    return x\n", 300.0, "structural"),
        ("def compute(x):\n    return x + 1.0\n\n\ndef helper(x):\n    return x + 1\n", 500.0, "refinement"),
    )
    for code, thr, expected_kind in variants:
        c, d = make_candidate(correctness=True, throughput=thr, code=code, modification=f"step {thr}")
        g.promote(c, receipt=receipt(d), invariant=invariants(d), target_digest=d)
        assert c.version_id in g.lineage.versions, f"step at {thr} should have committed"
        steps.append(
            CommitStep(
                version_id=c.version_id,
                kind=expected_kind,
                score=record_score(c),
                previous_best=prev,
                best_committed_id=g.lineage.head_id,
            )
        )
        prev = record_score(c)
    # 4 rejected attempts that never commit.
    for i in range(4):
        c, d = make_candidate(correctness=False, throughput=9999.0, code=BASELINE)
        g.promote(c, receipt=receipt(d, passed=False, exit_code=1), invariant=invariants(d), target_digest=d)

    report = build_honesty_report(
        total_explored=len(g.lineage.versions) + len(g.lineage.rejected_attempts),
        committed_steps=steps,
        rejection_counts=g.lineage.rejection_breakdown(),
    )
    payload = report.to_dict()
    assert payload["denominators"]["committed"] == 3
    assert payload["denominators"]["failed"] == 4
    assert payload["denominators"]["failed_to_committed"] == pytest.approx(4 / 3, abs=1e-3)
    assert payload["denominators"]["avo_reference_failed_to_committed"] == 12.0

    kinds = payload["change_kinds"]
    assert kinds["structural"] == 1, "the module-boundary change is structural, not a refinement"
    assert kinds["refinement"] == 2

    for gain in payload["gains"]:
        assert "absolute" in gain
        assert gain["denominator_commits"] == 3
        assert gain["denominator_explored"] == 7
        assert gain["failed_attempts"] == 4

    assert_gains_carry_denominators(report)


def test_k_bites_bare_improvement_is_rejected() -> None:
    """The guard: a report cannot express a gain without its denominator."""
    report = build_honesty_report(
        total_explored=2,
        committed_steps=[CommitStep(version_id="v1", kind="refinement", score=0.5, previous_best=0.0, best_committed_id="v1")],
    )
    # The normal path passes.
    assert_gains_carry_denominators(report)

    # Corrupt the report the way a future "just show me the delta" change would.
    report.total_explored = 0
    with pytest.raises(AssertionError, match="inconsistent"):
        assert_gains_carry_denominators(report)

    empty = build_honesty_report(total_explored=0, committed_steps=[])
    with pytest.raises(AssertionError, match="nothing to say"):
        assert_gains_carry_denominators(empty)


def test_k_plateau_is_stated_not_hidden_behind_a_small_last_delta() -> None:
    def steps(gains: list[float]) -> list[CommitStep]:
        out, score = [], 0.0
        for i, gain in enumerate(gains):
            out.append(CommitStep(f"v{i}", "structural" if i < 2 else "refinement", score=score + gain, previous_best=score, best_committed_id=f"v{i}"))
            score += gain
        return out

    # Gains collapse to nothing: a real plateau, and it must be said plainly.
    report = build_honesty_report(total_explored=60, committed_steps=steps([10.0, 10.0, 0.0, 0.0, 0.0, 0.0]))
    dim = report.to_dict()["diminishing_returns"]
    assert dim["plateaued"] is True
    assert dim["diminishing"] is False
    assert "PLATEAUED" in dim["plateau_reason"]
    assert report.to_dict()["denominators"]["failed"] == 54

    # Gains shrink but stay positive: AVO's v21-v40 shape. Not a plateau.
    report = build_honesty_report(total_explored=60, committed_steps=steps([10.0, 10.0, 0.5, 0.1, 0.05, 0.01]))
    dim = report.to_dict()["diminishing_returns"]
    assert dim["plateaued"] is False
    assert dim["diminishing"] is True
    assert "DIMINISHING" in dim["plateau_reason"]

    # Constant positive gain: steady progress. Calling this a plateau is its own
    # kind of dishonesty, so it must not be.
    report = build_honesty_report(total_explored=12, committed_steps=steps([10.0] * 6))
    dim = report.to_dict()["diminishing_returns"]
    assert dim["plateaued"] is False
    assert dim["diminishing"] is False
    assert "STEADY" in dim["plateau_reason"]

    # Too few commits to claim anything.
    short = build_honesty_report(total_explored=2, committed_steps=steps([1.0, 1.0])).to_dict()["diminishing_returns"]
    assert short["plateaued"] is None
    assert "not enough committed versions" in short["plateau_reason"]


def test_k_change_kind_classification_distinguishes_structure_from_refinement() -> None:
    assert classify_change(BASELINE, BASELINE) is ChangeKind.NO_OP
    assert classify_change(BASELINE, FASTER) is ChangeKind.REFINEMENT
    assert classify_change(BASELINE, "def other(x):\n    return x\n") is ChangeKind.STRUCTURAL
    assert classify_change("def a():\n    pass\n", "not python at all !!!") is ChangeKind.REFINEMENT


# ======================================================================
# reachability: the gate is on a real runtime path, not only a test
# ======================================================================


def test_workspace_runner_promotes_through_the_gate_and_records_the_chain(tmp_path: Path) -> None:
    """The gate is wired into ``run_workspace_variation`` -- the real promotion path.

    The candidate here is deliberately **behaviour-preserving**. That is not a
    convenience: the invariant half of this gate is a differential oracle, so a
    candidate that changes a caller-visible result is a candidate that violates
    an invariant. See ``test_workspace_runner_rolls_back_an_invariant_violation``
    for the other half of that pair.
    """
    import sys

    target = tmp_path / "calc.py"
    target.write_text(BASELINE, encoding="utf-8")
    runner = WorkspaceAVORunner(root_path=tmp_path, actor="agent")

    # `1 + x` is a real edit and produces identical results for every input.
    preserving = "def compute(x):\n    return 1 + x\n"
    cmd = f'"{sys.executable}" -c "import calc; assert calc.compute(5) == 6"'
    result = runner.run_workspace_variation(
        target_file_path="calc.py",
        candidate_code=preserving,
        hypothesis="reassociate the addition",
        modification="x + 1 -> 1 + x",
        test_command=cmd,
    )
    assert result["success"] is True, result
    assert result["invariant_scope"] == "measured", "a Python target must get measured invariants"
    assert result["invariant_regressions"] == 0
    assert result["chain_intact"] is True
    assert result["gate_fingerprint"] == runner.gate.fingerprint
    assert len(runner.lineage.chain) == 1
    assert runner.lineage.versions
    assert target.read_text(encoding="utf-8") == preserving


def test_workspace_runner_records_a_behaviour_change_as_unverified_not_as_verified(tmp_path: Path) -> None:
    """The composition at the runner, stated honestly.

    This surface's landed contract is that a candidate may change behaviour when
    the supplied oracle certifies the new behaviour. That contract is preserved --
    but the commit is stamped ``behaviour_changed_by_candidate`` and counted in
    the honesty caveats, so it can never be read as invariant-verified.

    The strict policy is a one-flag change and is tested separately below.
    """
    import sys

    target = tmp_path / "calc.py"
    target.write_text(BASELINE, encoding="utf-8")
    runner = WorkspaceAVORunner(root_path=tmp_path, actor="agent")

    # Tests pass (so correctness holds) and behaviour changed on every input.
    breaking = "def compute(x):\n    return x * 2\n"
    cmd = f'"{sys.executable}" -c "import calc; assert calc.compute(2) == 4"'
    result = runner.run_workspace_variation(
        target_file_path="calc.py",
        candidate_code=breaking,
        hypothesis="double",
        modification="x + 1 -> x * 2",
        test_command=cmd,
    )
    assert result["correctness"] is True
    assert result["committed"] is True, "the landed contract for this surface is preserved"
    assert result["invariant_regressions"] > 0, "the divergence was measured, not assumed"
    assert result["invariant_scope"] == "behaviour_changed_by_candidate", (
        "a behaviour change must never be recorded as invariant-verified"
    )
    caveats = runner.honesty().to_dict()["caveats"]
    assert caveats["commits_with_declared_behaviour_change"] == 1
    assert caveats["invariant_verified_commits"] == 0


def test_workspace_runner_strict_policy_rejects_the_behaviour_change(tmp_path: Path) -> None:
    """The same candidate, same gate, strict policy: rejected as an invariant violation.

    This is the composition test on the real promotion path, and it is the reason
    :class:`CommitGate` defaults to ``allow_declared_behaviour_change=False``.
    """
    import sys

    target = tmp_path / "calc.py"
    target.write_text(BASELINE, encoding="utf-8")
    runner = WorkspaceAVORunner(root_path=tmp_path, actor="agent", allow_declared_behaviour_change=False)

    breaking = "def compute(x):\n    return x * 2\n"
    cmd = f'"{sys.executable}" -c "import calc; assert calc.compute(2) == 4"'
    result = runner.run_workspace_variation(
        target_file_path="calc.py",
        candidate_code=breaking,
        hypothesis="double",
        modification="x + 1 -> x * 2",
        test_command=cmd,
    )
    assert result["correctness"] is True, "the supplied test passes, which is the point"
    assert result["success"] is False
    assert result["rejection_category"] == "invariant"
    assert result["invariant_regressions"] > 0
    assert result["rolled_back"] is True
    assert target.read_text(encoding="utf-8") == BASELINE, "an invariant violation must not be retained in the workspace"
    assert runner.lineage.versions == {}, "an invariant violation must not enter the committed lineage"
    assert any(r.metadata.get("rejection_category") == "invariant" for r in runner.lineage.rejected_attempts)


def test_workspace_runner_records_non_python_targets_as_unmeasured_not_as_verified(tmp_path: Path) -> None:
    """Honesty: a target the invariant suite cannot exercise is reported as such."""
    import sys

    target = tmp_path / "notes.txt"
    target.write_text("baseline\n", encoding="utf-8")
    runner = WorkspaceAVORunner(root_path=tmp_path, actor="agent")
    cmd = f'"{sys.executable}" -c "print(1)"'
    result = runner.run_workspace_variation(
        target_file_path="notes.txt",
        candidate_code="candidate\n",
        hypothesis="edit",
        modification="baseline -> candidate",
        test_command=cmd,
    )
    assert result["invariant_scope"] == "not_applicable"
    report = runner.honesty().to_dict()
    assert report["caveats"]["commits_with_unmeasured_invariants"] >= 1
    assert report["denominators"]["committed"] == 1
