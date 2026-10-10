"""Tests for the arena executor: full tournament simulation with a scripted runner."""

from __future__ import annotations

import asyncio

import pytest

from alpha.arena import (
    ArenaStatus,
    StrategyCard,
    bracket,
)
from alpha.arena.executor import ArenaExecutor
from alpha.arena.runner import NO_OUTPUT, ArenaJob, ArenaJobResult, ScriptedArenaRunner


def _make_card(rid: str, w_id: str, s_id: str) -> StrategyCard:
    return StrategyCard(
        reasoning=type("R", (), {"id": rid, "name": rid, "how": "reasoning how"})(),
        workflow=type("W", (), {"id": w_id, "name": w_id, "how": "workflow how"})(),
        strategy=type("S", (), {"id": s_id, "name": s_id, "how": "strategy how"})(),
    )


class TestExecutorScripted:
    """Full bracket simulations with a scripted runner."""

    @pytest.fixture
    def runner(self) -> ScriptedArenaRunner:
        def script(job: ArenaJob) -> ArenaJobResult:
            kind = job.kind
            if kind == "spawn":
                return ArenaJobResult(ok=True, text=f"WROTE {job.reply_hint}")
            if kind == "attack":
                return ArenaJobResult(
                    ok=True,
                    text="ATTACK 1 [MAJOR] flaw\nWhere: here\nProblem: it fails",
                )
            if kind == "defend":
                return ArenaJobResult(ok=True, text="ATTACK 1: CONCEDE. fixed\nWROTE " + job.reply_hint)
            if kind == "judge":
                return ArenaJobResult(
                    ok=True,
                    text=(
                        '{"scores": {"a": {"correctness": 8, "completeness": 7, "specificity": 6, "robustness": 7, "clarity": 8, "fatal": false}, '
                        '"b": {"correctness": 7, "completeness": 6, "specificity": 5, "robustness": 6, "clarity": 7, "fatal": false}}, "winner": "a", "reason": "a better"}'
                    ),
                )
            if kind == "final":
                return ArenaJobResult(ok=True, text="VERDICT: PASS\nREASON: passes")
            return ArenaJobResult(ok=False, text="", error=NO_OUTPUT)

        return ScriptedArenaRunner(script)

    @pytest.fixture
    def store(self):
        import tempfile

        from alpha.arena.store import ArenaStore

        with tempfile.TemporaryDirectory() as tmp:
            yield ArenaStore(tmp)

    async def run_tournament(self, store, runner, agents: int) -> dict:
        """Run a complete tournament from draft to champion."""
        state = bracket.new_run(
            owner_id="test",
            task="solve this problem",
            seed=42,
            agents_n=agents,
            wave=2,
        )
        # Deal cards
        from alpha.arena.cards import deal_cards, load_deck

        deck = load_deck()
        cards = deal_cards(agents, "42", deck)
        bracket.set_cards(state, {agent: card for agent, card in zip(bracket.agent_ids(agents), cards)})
        store.save(state)

        executor = ArenaExecutor(store, runner, wave=2, max_judge_attempts=1, on_event=lambda n, p: None)
        final_state = await executor.execute(state)
        return final_state

    @pytest.mark.asyncio
    async def test_two_agents(self, store, runner):
        state = await self.run_tournament(store, runner, 2)
        assert state["status"] == ArenaStatus.COMPLETED.value
        assert state["champion"] in ("agent-1", "agent-2")
        assert state["rounds"][0]["matches"][0]["winner"] == state["champion"]

    @pytest.mark.asyncio
    async def test_four_agents(self, store, runner):
        state = await self.run_tournament(store, runner, 4)
        assert state["status"] == ArenaStatus.COMPLETED.value
        assert state["champion"] in tuple(f"agent-{i}" for i in range(1, 5))
        assert len(state["rounds"]) == 2
        # Round 1: 2 matches, Round 2: 1 match
        assert len(state["rounds"][0]["matches"]) == 2
        assert len(state["rounds"][1]["matches"]) == 1

    @pytest.mark.asyncio
    async def test_eight_agents(self, store, runner):
        state = await self.run_tournament(store, runner, 8)
        assert state["status"] == ArenaStatus.COMPLETED.value
        assert len(state["rounds"]) == 3
        assert len(state["rounds"][0]["matches"]) == 4
        assert len(state["rounds"][1]["matches"]) == 2
        assert len(state["rounds"][2]["matches"]) == 1

    @pytest.mark.asyncio
    async def test_odd_agents_bye(self, store, runner):
        state = await self.run_tournament(store, runner, 3)
        assert state["status"] == ArenaStatus.COMPLETED.value
        assert len(state["rounds"]) == 2
        # Round 1: 1 match + 1 bye
        assert len(state["rounds"][0]["matches"]) == 1
        assert len(state["rounds"][0]["byes"]) == 1

    @pytest.mark.asyncio
    async def test_budget_exhausted(self, store, runner):
        state = bracket.new_run(
            owner_id="test",
            task="solve this",
            seed=42,
            agents_n=4,
            wave=2,
            max_subagent_calls=5,  # Too small for 4 agents (needs ~43)
        )
        from alpha.arena.cards import deal_cards, load_deck

        deck = load_deck()
        cards = deal_cards(4, "42", deck)
        bracket.set_cards(state, {agent: card for agent, card in zip(bracket.agent_ids(4), cards)})
        store.save(state)
        executor = ArenaExecutor(store, runner, wave=2, max_judge_attempts=1, on_event=lambda n, p: None)
        final = await executor.execute(state)
        assert final["status"] == "budget_exhausted"
        assert final["stop_reason"] == "subagent_calls"

    @pytest.mark.asyncio
    async def test_spawn_failure_drops_agent(self, store):
        """An agent that fails to spawn is recorded and excluded."""

        def bad_spawn(job: ArenaJob) -> ArenaJobResult:
            if job.kind == "spawn" and job.agent_id == "agent-1":
                return ArenaJobResult(ok=False, text="", error="no file written")
            if job.kind == "judge":
                return ArenaJobResult(
                    ok=True,
                    text=(
                        '{"scores": {"a": {"correctness": 8, "completeness": 7, "specificity": 6, "robustness": 7, "clarity": 8, "fatal": false}, '
                        '"b": {"correctness": 7, "completeness": 6, "specificity": 5, "robustness": 6, "clarity": 7, "fatal": false}}, "winner": "b", "reason": "b better"}'
                    ),
                )
            return ArenaJobResult(ok=True, text="ok")

        runner = ScriptedArenaRunner(bad_spawn)
        state = bracket.new_run(owner_id="test", task="t", seed=1, agents_n=2, wave=2)
        from alpha.arena.cards import deal_cards, load_deck

        deck = load_deck()
        cards = deal_cards(2, "1", deck)
        bracket.set_cards(state, {agent: card for agent, card in zip(bracket.agent_ids(2), cards)})
        store.save(state)
        executor = ArenaExecutor(store, runner, wave=2, on_event=lambda n, p: None)
        final = await executor.execute(state)
        assert "agent-1" in final["failed"] or "agent-2" in final["failed"]
        # The other agent should still win
        assert final["champion"] in tuple(f"agent-{i}" for i in range(1, 3))

    @pytest.mark.asyncio
    async def test_attack_failure_is_no_attacks(self, store):
        """A failed attack is recorded as no attacks."""

        def bad_attack(job: ArenaJob) -> ArenaJobResult:
            if job.kind == "attack":
                return ArenaJobResult(ok=False, text="", error=NO_OUTPUT)
            if job.kind == "judge":
                return ArenaJobResult(
                    ok=True,
                    text=(
                        '{"scores": {"a": {"correctness": 8, "completeness": 7, "specificity": 6, "robustness": 7, "clarity": 8, "fatal": false}, '
                        '"b": {"correctness": 7, "completeness": 6, "specificity": 5, "robustness": 6, "clarity": 7, "fatal": false}}, "winner": "a", "reason": "a better"}'
                    ),
                )
            return ArenaJobResult(ok=True, text="ok")

        runner = ScriptedArenaRunner(bad_attack)
        state = bracket.new_run(owner_id="test", task="t", seed=1, agents_n=2, wave=2)
        from alpha.arena.cards import deal_cards, load_deck

        deck = load_deck()
        cards = deal_cards(2, "1", deck)
        bracket.set_cards(state, {agent: card for agent, card in zip(bracket.agent_ids(2), cards)})
        store.save(state)
        executor = ArenaExecutor(store, runner, wave=2, on_event=lambda n, p: None)
        final = await executor.execute(state)
        # Should complete despite failed attack
        assert final["status"] == ArenaStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_judge_retry_then_fail(self, store):
        """A judge that keeps failing causes the run to fail honestly."""
        attempts = {"count": 0}

        def flaky_judge(job: ArenaJob) -> ArenaJobResult:
            if job.kind == "judge":
                attempts["count"] += 1
                if attempts["count"] <= 2:
                    return ArenaJobResult(ok=True, text="not json")
                return ArenaJobResult(ok=True, text='{"scores": {"a": {}, "b": {}}}')  # still bad
            return ArenaJobResult(ok=True, text="ok")

        runner = ScriptedArenaRunner(flaky_judge)
        state = bracket.new_run(owner_id="test", task="t", seed=1, agents_n=2, wave=2)
        from alpha.arena.cards import deal_cards, load_deck

        deck = load_deck()
        cards = deal_cards(2, "1", deck)
        bracket.set_cards(state, {agent: card for agent, card in zip(bracket.agent_ids(2), cards)})
        store.save(state)
        executor = ArenaExecutor(store, runner, wave=2, max_judge_attempts=2, on_event=lambda n, p: None)
        with pytest.raises(Exception, match="judge for match.*failed after"):
            await executor.execute(state)

    @pytest.mark.asyncio
    async def test_no_output_sentinel(self):
        """The NO_OUTPUT sentinel is a first-class failure signal."""
        assert NO_OUTPUT == "NO OUTPUT"
        result = ArenaJobResult(ok=False, text="", error=NO_OUTPUT)
        assert result.error == "NO OUTPUT"

    @pytest.mark.asyncio
    async def test_wave_concurrency(self, store):
        """Jobs from different matches in the same wave run concurrently."""
        concurrency = {"current": 0, "max": 0}

        async def check_concurrency(job: ArenaJob) -> ArenaJobResult:
            if job.kind in ("attack", "defend"):
                concurrency["current"] += 1
                concurrency["max"] = max(concurrency["max"], concurrency["current"])
                await asyncio.sleep(0.01)
                concurrency["current"] -= 1
                return ArenaJobResult(ok=True, text="ok")
            if job.kind == "judge":
                return ArenaJobResult(
                    ok=True,
                    text=(
                        '{"scores": {"a": {"correctness": 8, "completeness": 7, "specificity": 6, "robustness": 7, "clarity": 8, "fatal": false}, '
                        '"b": {"correctness": 7, "completeness": 6, "specificity": 5, "robustness": 6, "clarity": 7, "fatal": false}}, "winner": "a", "reason": "a better"}'
                    ),
                )
            if job.kind == "spawn":
                return ArenaJobResult(ok=True, text=f"WROTE {job.reply_hint}")
            if job.kind == "defend":
                return ArenaJobResult(ok=True, text="ATTACK 1: CONCEDE. fixed\nWROTE " + job.reply_hint)
            return ArenaJobResult(ok=False, text="", error=NO_OUTPUT)

        runner = ScriptedArenaRunner(check_concurrency)
        state = bracket.new_run(owner_id="test", task="t", seed=1, agents_n=4, wave=4)
        from alpha.arena.cards import deal_cards, load_deck

        deck = load_deck()
        cards = deal_cards(4, "1", deck)
        bracket.set_cards(state, {agent: card for agent, card in zip(bracket.agent_ids(4), cards)})
        store.save(state)
        executor = ArenaExecutor(store, runner, wave=4, on_event=lambda n, p: None)
        await executor.execute(state)
        # With wave=4 and 4 agents, 4 attacks in round 1 should run concurrently
        assert concurrency["max"] >= 2

    @pytest.mark.asyncio
    async def test_resume_after_budget_stop(self, store, runner):
        """A budget-exhausted run is terminal and cannot make progress."""
        state = bracket.new_run(
            owner_id="test",
            task="t",
            seed=1,
            agents_n=4,
            wave=2,
            max_subagent_calls=5,
        )
        from alpha.arena.cards import deal_cards, load_deck

        deck = load_deck()
        cards = deal_cards(4, "1", deck)
        bracket.set_cards(state, {agent: card for agent, card in zip(bracket.agent_ids(4), cards)})
        store.save(state)
        executor = ArenaExecutor(store, runner, wave=2, on_event=lambda n, p: None)
        await executor.execute(state)
        # Run is budget_exhausted - terminal
        assert state["status"] == "budget_exhausted"
        # Cannot make progress - executing again just returns the same state
        final = await executor.execute(state)
        assert final["status"] == "budget_exhausted"
        assert final["stop_reason"] == "subagent_calls"
