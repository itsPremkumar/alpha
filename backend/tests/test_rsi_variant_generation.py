"""WP-B2 tests: candidate factory — evidence-backed populations + dedup (feature #9).

Honesty pins (plan §3 WP-B2 test plan, §5 guardrails):

- zero evidence refs → ``ValueError`` matching "requires at least one
  evidence reference" (fail-closed, imported ``_normalize_evidence``
  semantics — this test pins the message the imported function raises);
- >50 refs / >2000-char ref → ``ValueError`` (bounded input, same source);
- ``surface="secrets"``/``"auth"`` → ``ValueError`` "not evolvable" —
  asserted against ``alpha.evolution.engine.FORBIDDEN_SURFACES`` itself so
  the test proves the reused constant, not a copy;
- population of 3 → 3 DISTINCT ``payload_hash``es, each a real
  ``sha256(canonical json)`` of its payload; a duplicate payload → skipped
  with the ``duplicate`` reason — no fabricated diversity score anywhere;
- every spec appears in ``lineage.get(variant_id)`` with correct
  ``parent_id`` (and the durable JSONL survives a fresh store);
- **honesty:** ``VariantSpec.to_dict()`` contains no ``score``,
  ``confidence``, or ``improved`` field — asserted over the full
  serialization, payload included;
- strategy-memory prior is consumed as a *disclosed heuristic* only:
  ``basis == "heuristic"``, explicit "not a measurement" note, neutral 0.5
  for a ``None`` prior, and exploration never disabled (≥1 non-dominant
  operator always emitted with ``population >= 2``).

Every test points ``AGENT_WORKSPACE_HOME`` at a temp dir (the environment
does not isolate it).
"""

import json

import pytest

from alpha.evolution.engine import FORBIDDEN_SURFACES
from alpha.rsi.generator import (
    TEMPLATE_OPERATORS,
    CandidateFactory,
    VariantSpec,
    op_alternative,
    op_conservative,
    op_perf_oriented,
    op_resilience,
)
from alpha.rsi.lineage import RsiLineageStore, _canonical_payload_hash
from alpha.rsi.strategy_memory import StrategyStat

VALID_EVIDENCE = [{"ref": "evidence://benchmark/run-1"}]


@pytest.fixture(autouse=True)
def rsi_home(tmp_path, monkeypatch):
    """Run every test against a temp AGENT_WORKSPACE_HOME (env is global)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    return tmp_path


def _stat(strategy, problem_class, *, attempts, promotions, rate):
    return StrategyStat(
        strategy=strategy,
        problem_class=problem_class,
        attempts=attempts,
        promotions=promotions,
        rollbacks=0,
        rejections=attempts - promotions,
        promotion_rate=rate,
        rollback_rate=0.0,
        updated_at=1.0,
    )


# --- fail-closed evidence (imported _normalize_evidence semantics) ---


def test_zero_evidence_refs_fail_closed(rsi_home):
    factory = CandidateFactory()
    with pytest.raises(ValueError, match="requires at least one evidence reference"):
        factory.generate("hyp-1", target="system_prompt", surface="prompt", evidence_refs=[])
    with pytest.raises(ValueError, match="requires at least one evidence reference"):
        factory.generate("hyp-1", target="system_prompt", surface="prompt", evidence_refs=None)
    # a failed call writes nothing: no lineage event, no spec file
    assert not (rsi_home / "rsi" / "lineage.jsonl").exists()
    assert not (rsi_home / "rsi" / "variants").exists()


def test_evidence_bounds_fail_closed(rsi_home):
    factory = CandidateFactory()
    with pytest.raises(ValueError, match="at most 50"):
        factory.generate(
            "hyp-1",
            target="system_prompt",
            surface="prompt",
            evidence_refs=[{"ref": f"evidence://run-{index}"} for index in range(51)],
        )
    with pytest.raises(ValueError, match="2000-char cap"):
        factory.generate("hyp-1", target="system_prompt", surface="prompt", evidence_refs=[{"ref": "x" * 2001}])
    assert not (rsi_home / "rsi" / "lineage.jsonl").exists()


# --- forbidden surfaces (reused constant, never a second list) ---


def test_forbidden_surfaces_are_not_evolvable(rsi_home):
    factory = CandidateFactory()
    # iterate the REAL alpha.evolution.engine constant — the plan's pins included
    for surface in sorted(FORBIDDEN_SURFACES):
        with pytest.raises(ValueError, match="not evolvable"):
            factory.generate("hyp-1", target="t", surface=surface, evidence_refs=list(VALID_EVIDENCE))
    # explicit plan pins
    for surface in ("secrets", "auth"):
        with pytest.raises(ValueError, match="not evolvable"):
            factory.generate("hyp-1", target="t", surface=surface, evidence_refs=list(VALID_EVIDENCE))
    # outside the plan's evolvable Literal: also fail-closed with "not evolvable"
    with pytest.raises(ValueError, match="not evolvable"):
        factory.generate("hyp-1", target="t", surface="banana", evidence_refs=list(VALID_EVIDENCE))
    assert not (rsi_home / "rsi" / "lineage.jsonl").exists()


# --- population distinctness + real hashes ---


def test_population_three_yields_three_distinct_payload_hashes(rsi_home, monkeypatch):
    monkeypatch.setattr("alpha.rsi.generator.prior_for", lambda strategy, problem_class: None)
    factory = CandidateFactory()
    specs = factory.generate("hyp-1", target="system_prompt", surface="prompt", evidence_refs=list(VALID_EVIDENCE))
    assert len(specs) == 3
    hashes = {spec.payload_hash for spec in specs}
    assert len(hashes) == 3
    for spec in specs:
        # a real sha256 over canonical json, recomputed independently by lineage's helper
        assert spec.payload_hash == _canonical_payload_hash(spec.payload)
        assert spec.evidence_refs == VALID_EVIDENCE
        assert spec.parent_id == "hyp-1"
    # no prior recorded → honest neutral default keeps the template order
    assert [spec.mutation_operator for spec in specs] == ["conservative", "alternative", "perf_oriented"]
    assert factory.skipped == []


def test_duplicate_payload_skipped_with_duplicate_reason(rsi_home):
    factory = CandidateFactory()
    specs = factory.generate(
        "hyp-1",
        target="system_prompt",
        surface="prompt",
        evidence_refs=list(VALID_EVIDENCE),
        operators=["resilience", "resilience"],
        population=2,
    )
    assert len(specs) == 1
    # exact report shape from the plan: a string reason + the real hash, nothing else
    assert factory.skipped == [{"skipped": "duplicate", "payload_hash": specs[0].payload_hash}]
    assert set(factory.skipped[0]) == {"skipped", "payload_hash"}
    # one variant → one lineage event (the duplicate was never recorded)
    lines = (rsi_home / "rsi" / "lineage.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


# --- lineage completeness ---


def test_every_spec_is_in_lineage_with_correct_parent(rsi_home):
    factory = CandidateFactory()
    specs = factory.generate(
        "hyp-42",
        target="tool_router",
        surface="routing",
        evidence_refs=["evidence://a", "evidence://b"],  # plain-string refs normalize too
    )
    assert len(specs) == 3
    for spec in specs:
        record = factory.lineage.get(spec.variant_id)
        assert record is not None
        assert record.parent_id == spec.parent_id == "hyp-42"
        assert record.mutation_operator == spec.mutation_operator
        assert record.payload_hash == spec.payload_hash
        assert record.surface == spec.surface
        assert record.target == spec.target
        assert record.status == "candidate"
        # nothing about this variant is verified at generation time — honest label
        assert record.evidence_kind == "unverified"
    # durable: a fresh store over the same storage sees every variant
    fresh = RsiLineageStore()
    for spec in specs:
        assert fresh.get(spec.variant_id) is not None
        assert fresh.get(spec.variant_id).parent_id == "hyp-42"


# --- honesty: generation never claims improvement ---


def test_to_dict_never_claims_improvement(rsi_home):
    factory = CandidateFactory()
    spec = factory.generate("hyp-1", target="system_prompt", surface="prompt", evidence_refs=list(VALID_EVIDENCE))[0]
    data = spec.to_dict()
    assert set(data) == {
        "variant_id",
        "cycle_id",
        "parent_id",
        "surface",
        "target",
        "mutation_operator",
        "payload",
        "payload_hash",
        "evidence_refs",
        "created_at",
    }
    serialized = json.dumps(data)
    for banned in ("score", "confidence", "improved", "diversity"):
        assert banned not in serialized


# --- persistence + variants_for ---


def test_variants_persisted_atomically_and_variants_for_filters_by_hypothesis(rsi_home):
    factory = CandidateFactory()
    specs = factory.generate("hyp-p", target="system_prompt", surface="skill", evidence_refs=list(VALID_EVIDENCE))
    root = rsi_home / "rsi" / "variants"
    for spec in specs:
        path = root / f"{spec.variant_id}.json"
        assert path.is_file()
        assert json.loads(path.read_text(encoding="utf-8")) == spec.to_dict()
        # atomic_write_json leaves no staging file behind
    assert not list(root.glob("*.tmp"))

    other = factory.generate("hyp-other", target="tool_router", surface="routing", evidence_refs=list(VALID_EVIDENCE))
    found = factory.variants_for("hyp-p")
    assert {spec.variant_id for spec in found} == {spec.variant_id for spec in specs}
    found_other = factory.variants_for("hyp-other")
    assert {spec.variant_id for spec in found_other} == {spec.variant_id for spec in other}
    assert factory.variants_for("hyp-nope") == []

    # honest reads: a corrupt spec file is skipped with a counted warning, never repaired
    (root / "broken.json").write_text("{not json", encoding="utf-8")
    assert {spec.variant_id for spec in factory.variants_for("hyp-p")} == {spec.variant_id for spec in specs}


# --- strategy-memory prior: disclosed heuristic, exploration never disabled ---


def test_prior_is_disclosed_heuristic_and_exploration_never_disabled(rsi_home, monkeypatch):
    def fake_prior(strategy, problem_class):
        if strategy == "perf_oriented":
            return _stat(strategy, problem_class, attempts=10, promotions=8, rate=0.8)
        return _stat(strategy, problem_class, attempts=4, promotions=1, rate=0.25)

    monkeypatch.setattr("alpha.rsi.generator.prior_for", fake_prior)
    factory = CandidateFactory()

    specs = factory.generate("hyp-1", target="system_prompt", surface="prompt", evidence_refs=list(VALID_EVIDENCE))
    operators = [spec.mutation_operator for spec in specs]
    assert "perf_oriented" in operators  # the dominant prior influences operator choice
    assert any(operator != "perf_oriented" for operator in operators)  # exploration: >=1 non-dominant ALWAYS emitted
    assert operators[0] == "perf_oriented"  # dominant first

    # a population of 2 keeps both guarantees: dominant + non-dominant
    pair = factory.generate("hyp-2", target="t", surface="prompt", evidence_refs=list(VALID_EVIDENCE), population=2)
    assert [spec.mutation_operator for spec in pair] == ["perf_oriented", "conservative"]

    for spec in specs:
        choice = spec.payload["operator_choice"]
        assert choice["basis"] == "heuristic"  # never claimed as a measurement
        assert choice["source"] == "alpha.rsi.strategy_memory.prior_for"
        assert choice["status"] == "recorded_prior"
        assert "not a measurement of this variant" in choice["note"]
        expected = 0.8 if spec.mutation_operator == "perf_oriented" else 0.25
        assert choice["heuristic_value"] == expected
        assert choice["recorded_attempts"] == (10 if spec.mutation_operator == "perf_oriented" else 4)
        # stat_note's concrete counts are disclosed inside the note
        assert "promotion_rate = promotions/attempts" in choice["note"]


def test_missing_prior_uses_disclosed_neutral_default(rsi_home, monkeypatch):
    monkeypatch.setattr("alpha.rsi.generator.prior_for", lambda strategy, problem_class: None)
    factory = CandidateFactory()
    specs = factory.generate("hyp-1", target="system_prompt", surface="prompt", evidence_refs=list(VALID_EVIDENCE))
    # all-tied (no prior) → template order preserved
    assert [spec.mutation_operator for spec in specs] == ["conservative", "alternative", "perf_oriented"]
    for spec in specs:
        choice = spec.payload["operator_choice"]
        assert choice["basis"] == "heuristic"
        assert choice["status"] == "no_recorded_prior"
        assert choice["heuristic_value"] == 0.5  # disclosed neutral, never a fabricated 0.0/1.0
        assert choice["recorded_attempts"] is None
        assert "neutral" in choice["note"]
        assert "not a measurement of this variant" in choice["note"]


# --- remaining argument validation + template purity ---


def test_bad_arguments_fail_closed(rsi_home):
    factory = CandidateFactory()
    with pytest.raises(ValueError, match="unknown template operator"):
        factory.generate("hyp-1", target="t", surface="prompt", evidence_refs=list(VALID_EVIDENCE), operators=["banana"])
    with pytest.raises(ValueError, match="operators"):
        factory.generate("hyp-1", target="t", surface="prompt", evidence_refs=list(VALID_EVIDENCE), operators=[])
    with pytest.raises(ValueError, match="population"):
        factory.generate("hyp-1", target="t", surface="prompt", evidence_refs=list(VALID_EVIDENCE), population=0)
    with pytest.raises(ValueError, match="hypothesis_id"):
        factory.generate("", target="t", surface="prompt", evidence_refs=list(VALID_EVIDENCE))
    with pytest.raises(ValueError, match="target"):
        factory.generate("hyp-1", target="", surface="prompt", evidence_refs=list(VALID_EVIDENCE))
    assert not (rsi_home / "rsi" / "lineage.jsonl").exists()


def test_template_operators_are_pure_and_distinct():
    base = {"hypothesis_id": "h", "surface": "prompt", "target": "t"}
    frozen = json.dumps(base, sort_keys=True)
    outputs = [op(base) for op in (op_conservative, op_alternative, op_perf_oriented, op_resilience)]
    # pure: input payload untouched by every operator
    assert json.dumps(base, sort_keys=True) == frozen
    # distinct: four templates produce four different payloads (no dedup collapse at source)
    assert len({json.dumps(output, sort_keys=True) for output in outputs}) == 4
    for output in outputs:
        assert output["operator"] in TEMPLATE_OPERATORS
    assert set(TEMPLATE_OPERATORS) == {"conservative", "alternative", "perf_oriented", "resilience"}


def test_spec_roundtrip_from_dict(rsi_home):
    factory = CandidateFactory()
    spec = factory.generate("hyp-1", target="t", surface="prompt", evidence_refs=list(VALID_EVIDENCE))[0]
    rebuilt = VariantSpec.from_dict(spec.to_dict())
    assert rebuilt == spec
