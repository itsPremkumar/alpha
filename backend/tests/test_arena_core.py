"""Tests for the arena engine core: cards, rubric, bracket, prompts."""

from __future__ import annotations

import pytest

from alpha.arena import (
    CALLS_PER_MATCH,
    AttackRecord,
    AttackSeverity,
    ArenaBudget,
    ArenaPlan,
    ArenaStatus,
    DefenseRecord,
    DefenseVerdict,
    StrategyCard,
    VerdictScores,
    WEIGHTS,
    deal,
    deal_cards,
    decide,
    load_deck,
    validate_deck,
    weighted_total,
    bracket_sizes,
    new_run,
    start_run,
    spawn_complete,
    start_round,
    next_actions,
    record_attack,
    record_defense,
    record_verdict,
    bracket,
    plan,
)
from alpha.arena.models import parse_attacks, parse_defenses
from alpha.arena.rubric import RUBRIC_TEXT
from alpha.arena.prompts import card_values, render


class TestDeck:
    def test_load_deck(self):
        deck = load_deck()
        assert "reasoning" in deck and "workflows" in deck and "strategies" in deck
        assert len(deck["reasoning"]) == 15
        assert len(deck["workflows"]) == 12
        assert len(deck["strategies"]) == 12

    def test_validate_deck_ok(self):
        deck = load_deck()
        validate_deck(deck)  # no error

    def test_validate_deck_errors(self):
        bad = {"reasoning": [{"id": "x", "name": "X", "how": "h"}], "workflows": [], "strategies": []}
        with pytest.raises(Exception) as exc:
            validate_deck(bad)
        assert "no 'workflows' entries" in str(exc.value)

    def test_combo_count(self):
        deck = load_deck()
        assert deal_cards(1, "seed", deck) is not None


class TestDeal:
    def test_deal_basic(self):
        deck = load_deck()
        cards = deal_cards(8, "test-seed", deck)
        assert len(cards) == 8
        # All distinct
        triples = [(c.reasoning.id, c.workflow.id, c.strategy.id) for c in cards]
        assert len(set(triples)) == 8
        # Every card has all three parts with names
        for c in cards:
            assert c.reasoning.name and c.workflow.name and c.strategy.name

    def test_deal_deterministic(self):
        deck = load_deck()
        a = deal_cards(10, "same", deck)
        b = deal_cards(10, "same", deck)
        assert [(c.reasoning.id, c.workflow.id, c.strategy.id) for c in a] == [
            (c.reasoning.id, c.workflow.id, c.strategy.id) for c in b
        ]

    def test_deal_different_seeds(self):
        deck = load_deck()
        a = deal_cards(10, "seed1", deck)
        b = deal_cards(10, "seed2", deck)
        assert [(c.reasoning.id, c.workflow.id, c.strategy.id) for c in a] != [
            (c.reasoning.id, c.workflow.id, c.strategy.id) for c in b
        ]

    def test_deal_max(self):
        deck = load_deck()
        cards = deal_cards(2160, 0, deck)
        assert len(cards) == 2160
        triples = [(c.reasoning.id, c.workflow.id, c.strategy.id) for c in cards]
        assert len(set(triples)) == 2160

    def test_deal_bounds(self):
        deck = load_deck()
        with pytest.raises(Exception) as exc:
            deal_cards(0, "seed", deck)
        assert "between 1 and" in str(exc.value)
        with pytest.raises(Exception) as exc:
            deal_cards(2161, "seed", deck)
        assert "between 1 and" in str(exc.value)

    def test_144_boundary(self):
        """Up to 144 agents, no two share two parts (pairwise unique)."""
        deck = load_deck()
        cards = deal_cards(144, "boundary", deck)
        pairs_rs = set()
        pairs_ws = set()
        pairs_rw = set()
        for c in cards:
            pairs_rs.add((c.reasoning.id, c.strategy.id))
            pairs_ws.add((c.workflow.id, c.strategy.id))
            pairs_rw.add((c.reasoning.id, c.workflow.id))
        # Every pair is unique
        assert len(pairs_rs) == 144
        assert len(pairs_ws) == 144
        assert len(pairs_rw) == 144


class TestRubric:
    def test_weights_sum(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

    def test_weighted_total(self):
        scores = VerdictScores(correctness=10, completeness=8, specificity=6, robustness=4, clarity=2)
        total = weighted_total(scores)
        expected = 10 * 0.30 + 8 * 0.25 + 6 * 0.15 + 4 * 0.20 + 2 * 0.10
        assert abs(total - expected) < 1e-9

    def test_decide_fatal_beats(self):
        a = VerdictScores(correctness=10, completeness=10, specificity=10, robustness=10, clarity=10)
        b = VerdictScores(correctness=10, completeness=10, specificity=10, robustness=10, clarity=10, fatal=True)
        result = decide(a, b)
        assert result["winner"] == "a"
        assert "fatal" in result["reason"].lower()

    def test_decide_higher_total(self):
        a = VerdictScores(correctness=10, completeness=10, specificity=10, robustness=10, clarity=10)
        b = VerdictScores(correctness=5, completeness=5, specificity=5, robustness=5, clarity=5)
        result = decide(a, b)
        assert result["winner"] == "a"

    def test_decide_tie_break_correctness(self):
        a = VerdictScores(correctness=8, completeness=5, specificity=5, robustness=5, clarity=5)
        b = VerdictScores(correctness=7, completeness=6, specificity=5, robustness=5, clarity=5)
        # Total: a=7.5, b=7.5 - tie, correctness breaks
        result = decide(a, b)
        assert result["winner"] == "a"

    def test_decide_tie_break_robustness(self):
        # a has robustness=5, b has robustness=4 -> a should win on robustness tie-break
        # Need to ensure total and correctness tie, so robustness is the first differentiator
        a = VerdictScores(correctness=8, completeness=5, specificity=5, robustness=5, clarity=5)
        b = VerdictScores(correctness=8, completeness=5, specificity=5, robustness=4, clarity=6)
        # Total: a=7.5, b=7.5 - tie, correctness tie (both 8), completeness tie (both 5), robustness breaks (a=5 > b=4)
        result = decide(a, b)
        assert result["winner"] == "a"

    def test_decide_judge_override(self):
        a = VerdictScores(correctness=10, completeness=10, specificity=10, robustness=10, clarity=10)
        b = VerdictScores(correctness=5, completeness=5, specificity=5, robustness=5, clarity=5)
        result = decide(a, b, judge_pick="b")
        assert result["winner"] == "a"
        assert result["judge_overridden"] is True

    def test_decide_surviving_fatal(self):
        a = VerdictScores(correctness=10, completeness=10, specificity=10, robustness=10, clarity=10)
        b = VerdictScores(correctness=10, completeness=10, specificity=10, robustness=10, clarity=10)
        a_attacks = [AttackRecord(index=1, title="t", severity=AttackSeverity.FATAL, where="w", problem="p")]
        a_defenses = []  # Conceded by default
        result = decide(a, b, a_attacks=a_attacks, a_defenses=a_defenses)
        assert result["winner"] == "b"
        assert result["fatal"]["a"] is True

    def test_surviving_attacks(self):
        attacks = [
            AttackRecord(index=1, title="t1", severity=AttackSeverity.FATAL, where="w", problem="p"),
            AttackRecord(index=2, title="t2", severity=AttackSeverity.MAJOR, where="w", problem="p"),
        ]
        defenses = [
            DefenseRecord(attack_index=1, verdict=DefenseVerdict.CONCEDE, note=""),
            DefenseRecord(attack_index=2, verdict=DefenseVerdict.REBUT, note="this is a real answer that is long enough"),
        ]
        from alpha.arena.rubric import _surviving_attacks
        survived = _surviving_attacks(attacks, defenses)
        assert len(survived) == 1
        assert survived[0].index == 1


class TestBracket:
    def test_bracket_sizes(self):
        assert bracket_sizes(1) == []
        assert bracket_sizes(2) == [(2, 1, False)]
        assert bracket_sizes(3) == [(3, 1, True), (2, 1, False)]
        assert bracket_sizes(8) == [(8, 4, False), (4, 2, False), (2, 1, False)]

    def test_plan(self):
        from alpha.arena import plan as plan_fn
        plan = plan_fn(8, 4, baseline=False)
        assert plan.agents == 8
        assert plan.rounds == 3
        assert plan.total_calls == 8 + 5 * 7  # spawn + 7 matches * 5
        assert plan.total_waves == 3

    def test_plan_wave_size(self):
        from alpha.arena import plan as plan_fn
        plan = plan_fn(8, 2, baseline=False)
        # wave=2: round 1 (4 matches) -> 2 waves, round 2 (2 matches) -> 1 wave, round 3 (1 match) -> 1 wave = 4
        assert plan.total_waves == 4

    def test_new_run(self):
        from alpha.arena.bracket import new_run
        state = new_run(owner_id="u1", task="test", seed=42, agents_n=4, wave=2, baseline=None)
        assert state["run_id"]
        assert state["status"] == ArenaStatus.DRAFT.value
        assert state["agents_n"] == 4
        assert state["wave"] == 2
        assert len(state["cards"]) == 0

    def test_start_run(self):
        from alpha.arena import bracket, CardPart, StrategyCard
        state = bracket.new_run(owner_id="u1", task="test", seed=42, agents_n=4, wave=2)
        card = StrategyCard(
            reasoning=CardPart(id="r", name="R", how="h"),
            workflow=CardPart(id="w", name="W", how="h"),
            strategy=CardPart(id="s", name="S", how="h"),
        )
        bracket.set_cards(state, {f"agent-{i}": card for i in range(1, 5)})
        ids = start_run(state)
        assert ids == ["agent-1", "agent-2", "agent-3", "agent-4"]
        assert state["status"] == ArenaStatus.RUNNING.value

    def test_spawn_complete(self):
        from alpha.arena import bracket, CardPart, StrategyCard
        state = bracket.new_run(owner_id="u1", task="test", seed=42, agents_n=4, wave=2)
        card = StrategyCard(
            reasoning=CardPart(id="r", name="R", how="h"),
            workflow=CardPart(id="w", name="W", how="h"),
            strategy=CardPart(id="s", name="S", how="h"),
        )
        bracket.set_cards(state, {f"agent-{i}": card for i in range(1, 5)})
        start_run(state)
        assert not spawn_complete(state)
        bracket.record_solution(state, "agent-1", "path1")
        assert not spawn_complete(state)
        bracket.record_solution(state, "agent-2", "path2")
        bracket.record_solution(state, "agent-3", "path3")
        bracket.record_solution(state, "agent-4", "path4")
        assert spawn_complete(state)

    def test_start_round(self):
        from alpha.arena import bracket, CardPart, StrategyCard
        state = bracket.new_run(owner_id="u1", task="test", seed=42, agents_n=4, wave=2)
        card = StrategyCard(
            reasoning=CardPart(id="r", name="R", how="h"),
            workflow=CardPart(id="w", name="W", how="h"),
            strategy=CardPart(id="s", name="S", how="h"),
        )
        bracket.set_cards(state, {f"agent-{i}": card for i in range(1, 5)})
        start_run(state)
        for agent in ["agent-1", "agent-2", "agent-3", "agent-4"]:
            bracket.record_solution(state, agent, f"path-{agent}")
        round_record = start_round(state)
        assert round_record["round"] == 1
        assert len(round_record["matches"]) == 2
        assert round_record["matches"][0]["a"] == "agent-1"
        assert round_record["matches"][0]["b"] == "agent-2"

    def test_next_actions_wave_bound(self):
        from alpha.arena import bracket, CardPart, StrategyCard
        state = bracket.new_run(owner_id="u1", task="test", seed=42, agents_n=4, wave=2)
        card = StrategyCard(
            reasoning=CardPart(id="r", name="R", how="h"),
            workflow=CardPart(id="w", name="W", how="h"),
            strategy=CardPart(id="s", name="S", how="h"),
        )
        bracket.set_cards(state, {f"agent-{i}": card for i in range(1, 5)})
        start_run(state)
        for agent in ["agent-1", "agent-2", "agent-3", "agent-4"]:
            bracket.record_solution(state, agent, f"path-{agent}")
        actions = next_actions(state, limit=2)
        assert len(actions) == 2
        actions = next_actions(state, limit=10)
        assert len(actions) >= 4  # 4 attacks (2 matches * 2 sides)

    def test_attack_defend_judge_flow(self):
        from alpha.arena import bracket, CardPart, StrategyCard
        state = bracket.new_run(owner_id="u1", task="test", seed=42, agents_n=2, wave=2)
        card = StrategyCard(
            reasoning=CardPart(id="r", name="R", how="h"),
            workflow=CardPart(id="w", name="W", how="h"),
            strategy=CardPart(id="s", name="S", how="h"),
        )
        bracket.set_cards(state, {f"agent-{i}": card for i in range(1, 3)})
        start_run(state)
        bracket.record_solution(state, "agent-1", "path1")
        bracket.record_solution(state, "agent-2", "path2")
        start_round(state)
        # Attacks
        round_rec = state["rounds"][0]
        match = round_rec["matches"][0]
        record_attack(state, match["id"], "agent-1", "ATTACK 1 [MAJOR] bad\nWhere: here\nProblem: it breaks")
        record_attack(state, match["id"], "agent-2", "NO ATTACKS")
        # Defenses
        record_defense(state, match["id"], "agent-1", "ATTACK 1: CONCEDE. fixed", "rev1")
        record_defense(state, match["id"], "agent-2", "ATTACK 1: REBUT. already handled", "rev2")
        # Judge
        verdict = record_verdict(state, match["id"], {"correctness": 8, "completeness": 8, "specificity": 7, "robustness": 7, "clarity": 8},
                                  {"correctness": 7, "completeness": 7, "specificity": 6, "robustness": 6, "clarity": 7})
        assert verdict["winner"] in ("a", "b")
        assert match["winner"] in ("agent-1", "agent-2")


class TestPrompts:
    def test_render_competitor(self):
        from alpha.arena.prompts import render, card_values
        card = StrategyCard(
            reasoning=type("R", (), {"id": "r", "name": "R", "how": "h"})(),
            workflow=type("W", (), {"id": "w", "name": "W", "how": "h"})(),
            strategy=type("S", (), {"id": "s", "name": "S", "how": "h"})(),
        )
        prompt = render("competitor", {"task": "solve it", **card_values(card), "solution_path": "/tmp/sol.md"})
        assert "solve it" in prompt
        assert "R" in prompt and "W" in prompt and "S" in prompt
        assert "WROTE /tmp/sol.md" in prompt

    def test_render_judge_contains_rubric(self):
        from alpha.arena.prompts import render
        from alpha.arena.rubric import RUBRIC_TEXT
        prompt = render("judge", {"task": "t", "a_path": "/a", "b_path": "/b", "rubric_text": RUBRIC_TEXT})
        assert "correctness" in prompt
        assert "robustness" in prompt
        assert "{rubric_text}" not in prompt  # substituted


class TestModels:
    def test_attack_record_parsing(self):
        text = "ATTACK 1 [FATAL] title\nWhere: w\nProblem: p\nATTACK 2 [MINOR] t2\nWhere: w2\nProblem: p2"
        attacks = parse_attacks(text)
        assert len(attacks) == 2
        assert attacks[0].severity == AttackSeverity.FATAL
        assert attacks[1].severity == AttackSeverity.MINOR

    def test_defense_record_parsing(self):
        text = "ATTACK 1: CONCEDE. ok\nATTACK 2: REBUT. already handled"
        defenses = parse_defenses(text)
        assert len(defenses) == 2
        assert defenses[0].verdict == DefenseVerdict.CONCEDE
        assert defenses[1].verdict == DefenseVerdict.REBUT

    def test_budget_charge(self):
        budget = ArenaBudget(max_subagent_calls=10, max_tokens=1000)
        budget.charge(calls=3, tokens=500)
        assert budget.measured_calls == 3
        assert budget.measured_tokens == 500
        assert budget.is_exhausted() is None
        budget.charge(calls=8, tokens=600)
        assert budget.is_exhausted() == "subagent_calls"

    def test_budget_exhausted_axis(self):
        budget = ArenaBudget(max_subagent_calls=5, max_tokens=5000)
        budget.charge(calls=3, tokens=2000)
        budget.charge(calls=3, tokens=2000)
        assert budget.is_exhausted() == "subagent_calls"

    def test_arena_statuses(self):
        assert ArenaStatus.DRAFT.value == "draft"
        assert ArenaStatus.COMPLETED.value == "completed"
        assert ArenaStatus.BUDGET_EXHAUSTED.value == "budget_exhausted"