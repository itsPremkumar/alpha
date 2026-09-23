"""Tests for RSI strategy memory / reflection stats (WP-D3, feature #23).

Honesty pins (plan §3 WP-D3 + §5.6):
* empty history -> honest empty/neutral state: no stats, rates ``None``/"unverified",
  never fabricated 0.0/1.0 improvement claims;
* synthetic events -> rates equal hand-computable recorded counts;
* rejected / rolled-back events counted distinctly;
* ``lesson_for`` on an unknown failure -> ``{"status": "unresolved"}`` with no
  invented root cause;
* persistence atomic (tmp + ``os.replace``); corrupt or honesty-violating
  files load as honest empty memory;
* determinism: two rebuilds of the same input are identical.
"""

import json

import pytest

from alpha.evolution.retrospective_engine import PromptEvolutionProposal
from alpha.rsi import strategy_memory as sm


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    """Pin AGENT_WORKSPACE_HOME to a per-test temp dir (the env does not isolate it)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    sm.clear_memory_cache()
    yield
    sm.clear_memory_cache()


def _synthetic_events():
    """Seven recorded events over four distinct units (hand math below).

    Hand computation (one unit = one attempt):
      units      = c1, c2, c3, c4                     -> attempts = 4
      promotions = c1, c2                             -> 2/4 = 0.5
      rollbacks  = c1 (promoted, then rolled back)    -> 1/4 = 0.25
      rejections = c3                                 -> 1/4 = 0.25
      updated_at = max(10,20,30,40,50,60,70)          -> 70.0
    """
    return [
        {"event": "proposed", "candidate_id": "c1", "strategy": "refactor", "problem_class": "perf", "at": 10.0},
        {"event": "promoted", "candidate_id": "c1", "strategy": "refactor", "problem_class": "perf", "at": 20.0},
        {"event": "rolled_back", "candidate_id": "c1", "strategy": "refactor", "problem_class": "perf", "at": 30.0},
        {"event": "proposed", "candidate_id": "c2", "strategy": "refactor", "problem_class": "perf", "at": 40.0},
        {"event": "promoted", "candidate_id": "c2", "strategy": "refactor", "problem_class": "perf", "at": 50.0},
        {"event": "rejected", "candidate_id": "c3", "strategy": "refactor", "problem_class": "perf", "at": 60.0},
        {"event": "proposed", "candidate_id": "c4", "strategy": "refactor", "problem_class": "perf", "at": 70.0},
    ]


def _proposal(trigger_pattern, instruction="Always verify imported packages exist."):
    return PromptEvolutionProposal(
        proposal_id="evo-test0001",
        project_id="default",
        bot_name="coder",
        trigger_pattern=trigger_pattern,
        heuristic_summary="Incident: recorded postmortem symptom",
        proposed_instruction=instruction,
        target_prompt_section="coding_guidelines",
    )


class _StubRetrospective:
    """Injected stand-in for RetrospectiveEngine; pins queue_for_approval=False."""

    def __init__(self, proposals):
        self._proposals = list(proposals)
        self.calls = 0

    def analyze_recent_learnings(self, bot_name="coder", queue_for_approval=True):
        self.calls += 1
        assert queue_for_approval is False, "strategy memory must never enqueue approvals"
        return list(self._proposals)


class _ExplodingRetrospective:
    """Engine stand-in that fails, to pin fail-closed unresolved behavior."""

    def analyze_recent_learnings(self, bot_name="coder", queue_for_approval=True):
        raise RuntimeError("retrospective unavailable")


# ---------------------------------------------------------------------------
# Honesty pin: empty history -> attempts 0 + rates None, never 0.0/1.0 defaults
# ---------------------------------------------------------------------------


def test_empty_ledger_honesty_pin():
    assert sm.rebuild_from_ledger([]) == {}
    assert sm.prior_for("anything", "any", memory={}) is None
    stat = sm.empty_stat("s", "p")
    assert stat.attempts == 0
    assert stat.promotion_rate is None
    assert stat.rollback_rate is None
    assert "unverified" in sm.stat_note(stat)
    # Unknown event kinds never fabricate a bucket out of nothing.
    assert sm.rebuild_from_ledger([{"event": "mystery", "candidate_id": "x"}]) == {}
    assert sm.rebuild_from_ledger([{"candidate_id": "no-kind"}, "not-a-dict"]) == {}


# ---------------------------------------------------------------------------
# Synthetic events -> rates match the hand computation above
# ---------------------------------------------------------------------------


def test_synthetic_events_rates_match_hand_computation():
    stats = sm.rebuild_from_ledger(_synthetic_events())
    stat = stats[sm.memory_key("refactor", "perf")]
    assert stat.strategy == "refactor"
    assert stat.problem_class == "perf"
    assert stat.attempts == 4
    assert stat.promotions == 2
    assert stat.rollbacks == 1
    assert stat.rejections == 1
    assert stat.promotion_rate == 0.5  # 2/4
    assert stat.rollback_rate == 0.25  # 1/4
    assert stat.updated_at == 70.0


def test_stat_note_writes_formula_and_concrete_counts():
    stat = sm.rebuild_from_ledger(_synthetic_events())[sm.memory_key("refactor", "perf")]
    note = sm.stat_note(stat)
    assert "promotion_rate = promotions/attempts = 2/4 = 0.5" in note
    assert "rollback_rate = rollbacks/attempts = 1/4 = 0.25" in note
    assert "no Laplace smoothing" in note


def test_rejected_and_rolled_back_counted_distinctly():
    events = [
        {"event": "promoted", "candidate_id": "k1", "strategy": "s", "problem_class": "p", "at": 1.0},
        {"event": "rolled_back", "candidate_id": "k1", "strategy": "s", "problem_class": "p", "at": 2.0},
        {"event": "rejected", "candidate_id": "k2", "strategy": "s", "problem_class": "p", "at": 3.0},
    ]
    stat = sm.rebuild_from_ledger(events)[sm.memory_key("s", "p")]
    assert stat.attempts == 2  # k1 promoted->rolled_back is ONE attempt
    assert stat.promotions == 1
    assert stat.rollbacks == 1
    assert stat.rejections == 1  # a rejection is not lumped into rollbacks
    assert stat.promotion_rate == 0.5
    assert stat.rollback_rate == 0.5


def test_unattributed_events_use_honest_sentinel_buckets():
    events = [
        # Existing evolution-ledger shape: no strategy/problem_class recorded.
        {"event": "promoted", "candidate_id": "ev-1", "at": 5.0},
        # Aliases: surface -> strategy, problem -> problem_class.
        {"event": "promoted", "candidate_id": "x1", "surface": "prompt", "problem": "flaky-test", "at": 6.0},
    ]
    stats = sm.rebuild_from_ledger(events)
    anon = stats[sm.memory_key(sm.UNATTRIBUTED_STRATEGY, sm.UNKNOWN_PROBLEM_CLASS)]
    assert (anon.attempts, anon.promotions, anon.promotion_rate) == (1, 1, 1.0)
    aliased = stats[sm.memory_key("prompt", "flaky-test")]
    assert (aliased.attempts, aliased.promotions) == (1, 1)


def test_rebuild_is_deterministic_and_pure():
    events = _synthetic_events()
    first = sm.rebuild_from_ledger(events)
    second = sm.rebuild_from_ledger([dict(event) for event in events])
    assert first == second
    assert list(first) == list(second)
    assert events == _synthetic_events()  # input not mutated


# ---------------------------------------------------------------------------
# Persistence: atomic (tmp + os.replace); corrupt/forged -> honest empty
# ---------------------------------------------------------------------------


def test_save_memory_atomic_round_trip(tmp_path):
    stats = sm.rebuild_from_ledger(_synthetic_events())
    target = sm.save_memory(stats, path=tmp_path / "nested" / "mem.json")
    assert target.exists()
    assert not (tmp_path / "nested" / "mem.json.tmp").exists()  # tmp consumed by os.replace
    assert sm.load_memory(path=target) == stats


def test_corrupt_or_forged_memory_loads_as_honest_empty(tmp_path):
    assert sm.load_memory(path=tmp_path / "missing.json") == {}

    garbage = tmp_path / "garbage.json"
    garbage.write_text("{not json", encoding="utf-8")
    assert sm.load_memory(path=garbage) == {}

    wrong_version = tmp_path / "v99.json"
    wrong_version.write_text(json.dumps({"version": 99, "stats": []}), encoding="utf-8")
    assert sm.load_memory(path=wrong_version) == {}

    # Zero-attempt stat carrying rates would read as a measurement -> reject.
    forged_zero = tmp_path / "forged_zero.json"
    forged_zero.write_text(
        json.dumps(
            {
                "version": 1,
                "stats": [
                    {
                        "strategy": "s",
                        "problem_class": "p",
                        "attempts": 0,
                        "promotions": 0,
                        "rollbacks": 0,
                        "rejections": 0,
                        "promotion_rate": 0.0,
                        "rollback_rate": 1.0,
                        "updated_at": 0.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert sm.load_memory(path=forged_zero) == {}

    # Rate not equal to recorded counts (1.0 != 1/2) -> fabricated -> reject.
    forged_rate = tmp_path / "forged_rate.json"
    forged_rate.write_text(
        json.dumps(
            {
                "version": 1,
                "stats": [
                    {
                        "strategy": "s",
                        "problem_class": "p",
                        "attempts": 2,
                        "promotions": 1,
                        "rollbacks": 0,
                        "rejections": 1,
                        "promotion_rate": 1.0,
                        "rollback_rate": 0.0,
                        "updated_at": 1.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert sm.load_memory(path=forged_rate) == {}
    # Cache now holds the honest empty state: no fabricated prior.
    assert sm.prior_for("s", "p") is None


# ---------------------------------------------------------------------------
# prior_for: recorded stat or honest None
# ---------------------------------------------------------------------------


def test_prior_for_returns_recorded_stat_and_none_for_unknown():
    stats = sm.rebuild_from_ledger(_synthetic_events())
    stat = sm.prior_for("refactor", "perf", memory=stats)
    assert stat == stats[sm.memory_key("refactor", "perf")]
    assert stat is not None and stat.attempts == 4
    assert sm.prior_for("refactor", "memory", memory=stats) is None
    assert sm.prior_for("unknown-strategy", "perf", memory=stats) is None
    assert sm.prior_for("refactor", "perf", memory={}) is None


def test_prior_for_lazy_cache_round_trip(tmp_path):
    assert sm.prior_for("refactor", "perf") is None  # nothing recorded yet
    stats = sm.rebuild_from_ledger(_synthetic_events())
    target = sm.save_memory(stats, path=tmp_path / "mem.json")
    assert sm.load_memory(path=target) == stats
    assert sm.prior_for("refactor", "perf") is not None  # served from cache
    sm.clear_memory_cache()
    # Default location (runtime_home()/rsi/strategy_memory.json) is absent.
    assert sm.prior_for("refactor", "perf") is None


def test_refresh_memory_records_real_ledger_counts(tmp_path):
    evolution_dir = tmp_path / "evolution"
    evolution_dir.mkdir(parents=True)
    raw_lines = [
        json.dumps({"event": "promoted", "candidate_id": "ev-1", "at": 100.0}),
        "{corrupt json",
        json.dumps({"event": "rejected", "candidate_id": "ev-2", "at": 200.0}),
        json.dumps({"event": "mystery", "candidate_id": "ev-3", "at": 300.0}),
    ]
    (evolution_dir / "ledger.jsonl").write_text("\n".join(raw_lines) + "\n", encoding="utf-8")
    # rsi/lineage.jsonl (WP-A1) does not exist yet -> skipped silently.
    stats = sm.refresh_memory()
    stat = stats[sm.memory_key(sm.UNATTRIBUTED_STRATEGY, sm.UNKNOWN_PROBLEM_CLASS)]
    assert stat.attempts == 2
    assert stat.promotions == 1
    assert stat.rejections == 1
    assert stat.rollbacks == 0
    assert stat.promotion_rate == 0.5
    assert stat.rollback_rate == 0.0  # measured zero over real attempts, not a default
    assert stat.updated_at == 200.0  # the unknown "mystery" event was fully ignored
    assert sm.memory_path().exists()  # persisted under the isolated workspace
    assert sm.load_memory() == stats
    assert sm.prior_for(sm.UNATTRIBUTED_STRATEGY, sm.UNKNOWN_PROBLEM_CLASS) == stat


# ---------------------------------------------------------------------------
# lesson_for: matched patterns disclosed, unknown failures honestly unresolved
# ---------------------------------------------------------------------------


def test_lesson_for_no_failure_returns_none():
    assert sm.lesson_for(None) is None


def test_lesson_for_empty_symptom_is_unresolved():
    out = sm.lesson_for({})
    assert out["status"] == "unresolved"
    assert out["evidence_kind"] == "unverified"
    assert "proposed_instruction" not in out
    assert "no root cause claimed" in out["note"]


def test_lesson_for_unknown_failure_with_real_engine_is_unresolved():
    # Real RetrospectiveEngine in an empty isolated workspace: with no recorded
    # postmortems it can only emit its generic fallback, which is never a root
    # cause for a specific failure -> honest unresolved, no fabrication.
    out = sm.lesson_for({"error": "KaboomError: the frobnicator exploded"})
    assert out["status"] == "unresolved"
    assert out["evidence_kind"] == "unverified"
    assert "proposed_instruction" not in out
    assert "no root cause claimed" in out["note"]
    assert "proposals_emitted=" in out["note"]


def test_lesson_for_matched_import_pattern_is_disclosed_heuristic():
    stub = _StubRetrospective([_proposal("Recurring Import / Module Resolution Failure")])
    out = sm.lesson_for({"error": "ModuleNotFoundError: No module named 'requests'"}, retrospective=stub)
    assert stub.calls == 1
    assert out["status"] == "matched"
    assert out["evidence_kind"] == "heuristic"
    assert out["trigger_pattern"] == "Recurring Import / Module Resolution Failure"
    assert out["proposed_instruction"]
    assert "proposals_emitted=1, matched=1" in out["note"]
    assert "heuristic" in out["note"] and "not a measured root cause" in out["note"]


def test_lesson_for_matched_type_pattern():
    stub = _StubRetrospective([_proposal("Runtime Type / Attribute Error")])
    out = sm.lesson_for("AttributeError: 'NoneType' object has no attribute 'x'", retrospective=stub)
    assert out["status"] == "matched"
    assert out["evidence_kind"] == "heuristic"
    assert out["trigger_pattern"] == "Runtime Type / Attribute Error"


def test_lesson_generic_fallback_never_becomes_root_cause():
    stub = _StubRetrospective([_proposal("Proactive Quality Hardening", "Verify DoD criteria.")])
    out = sm.lesson_for("ImportError: import of module beta failed", retrospective=stub)
    assert out["status"] == "unresolved"
    assert out["evidence_kind"] == "unverified"
    assert "proposed_instruction" not in out


def test_lesson_engine_error_fails_closed_with_real_error_text():
    out = sm.lesson_for("some failure", retrospective=_ExplodingRetrospective())
    assert out["status"] == "unresolved"
    assert out["evidence_kind"] == "unverified"
    assert "failed" in out["note"]
    assert "retrospective unavailable" in out["note"]
