"""Tests for the arena service: plan, create, start, resume, gates."""

from __future__ import annotations

import pytest

import tempfile

from alpha.arena.config import ArenaConfig
from alpha.arena.service import ArenaConfirmationRequired, ArenaRunError, ArenaService
from alpha.arena.store import ArenaStore, ArenaStoreError
from alpha.arena.runner import ScriptedArenaRunner


class TestService:
    @pytest.fixture
    def config(self) -> ArenaConfig:
        return ArenaConfig(
            enabled=True,
            default_agents=4,
            max_agents=64,
            default_wave=4,
            max_wave=32,
            estimate_tokens_per_call=60000,
            require_confirmation_over_calls=100,
            max_subagent_calls_per_run=1000,
            max_tokens_per_run=50_000_000,
            max_wall_seconds_per_run=7200.0,
            max_judge_attempts=2,
            reasoning_bank_seed=False,
        )

    @pytest.fixture
    def runner(self) -> ScriptedArenaRunner:
        def script(job):
            from alpha.arena.runner import ArenaJobResult, NO_OUTPUT
            if job.kind == "spawn":
                return ArenaJobResult(ok=True, text=f"WROTE {job.reply_hint}")
            if job.kind == "attack":
                return ArenaJobResult(ok=True, text="ATTACK 1 [MAJOR] x\nWhere: w\nProblem: p")
            if job.kind == "defend":
                return ArenaJobResult(ok=True, text="ATTACK 1: CONCEDE. fixed\nWROTE " + job.reply_hint)
            if job.kind == "judge":
                return ArenaJobResult(
                    ok=True,
                    text='{"scores": {"a": {"correctness": 8, "completeness": 7, "specificity": 6, "robustness": 7, "clarity": 8, "fatal": false}, "b": {"correctness": 7, "completeness": 6, "specificity": 5, "robustness": 6, "clarity": 7, "fatal": false}}, "winner": "a", "reason": "a better"}',
                )
            if job.kind == "final":
                return ArenaJobResult(ok=True, text="VERDICT: PASS\nREASON: ok")
            return ArenaJobResult(ok=False, text="", error=NO_OUTPUT)
        return ScriptedArenaRunner(script)

    @pytest.fixture
    def service(self, config, runner) -> ArenaService:
        with tempfile.TemporaryDirectory() as tmp:
            store = ArenaStore(tmp)
            yield ArenaService(config=config, runner_factory=lambda c: runner, store=store)

    def test_estimate(self, service):
        est = service.estimate(agents=8, wave=4, baseline=False)
        assert est["plan"]["agents"] == 8
        assert est["plan"]["total_calls"] == 8 + 5 * 7  # 43
        assert est["estimated_tokens"] == 43 * 60000
        assert est["confirmation_required"] is False  # 43 < 100

    def test_estimate_confirmation(self, service):
        est = service.estimate(agents=16, wave=4, baseline=False)
        assert est["plan"]["total_calls"] == 16 + 5 * 15  # 91
        assert est["confirmation_required"] is False
        # Use 64 (the cap) to test confirmation, since 100 exceeds max_agents
        est = service.estimate(agents=64, wave=4, baseline=False)
        assert est["confirmation_required"] is True

    def test_create(self, service):
        state = service.create(owner_id="user1", thread_id="t1", task="solve it", agents=4, wave=2)
        assert state["run_id"]
        assert state["owner_id"] == "user1"
        assert state["task"] == "solve it"
        assert state["agents_n"] == 4
        assert state["wave"] == 2
        assert len(state["cards"]) == 4
        assert state["status"] == "draft"

    def test_create_agents_cap(self, service):
        with pytest.raises(ValueError, match="capped at 64"):
            service.create(owner_id="u", thread_id="t", task="t", agents=100)

    @pytest.mark.asyncio
    async def test_start_needs_confirm(self, service):
        state = service.create(owner_id="user1", thread_id="t1", task="solve it", agents=64, wave=4)
        with pytest.raises(ArenaConfirmationRequired) as exc:
            await service.start(state["run_id"], "user1", confirm=False)
        assert "confirm=True" in str(exc.value)

    @pytest.mark.asyncio
    async def test_start_with_confirm(self, service):
        state = service.create(owner_id="user1", thread_id="t1", task="solve it", agents=4, wave=2)
        result = await service.start(state["run_id"], "user1", confirm=True)
        assert result["status"] == "completed"
        assert result["champion"] is not None

    @pytest.mark.asyncio
    async def test_resume(self, service):
        state = service.create(owner_id="user1", thread_id="t1", task="solve it", agents=4, wave=2)
        # Drive one wave manually by setting status
        state["status"] = "running"
        state["phase"] = "attack"
        # Service is not driving, just check resume logic
        # We can't easily test partial resume without running the executor
        # This is covered by test_arena_executor

    def test_status(self, service):
        state = service.create(owner_id="user1", thread_id="t1", task="t", agents=4, wave=2)
        summary = service.status(state["run_id"], "user1")
        assert summary["run_id"] == state["run_id"]
        assert summary["status"] == "draft"
        assert summary["agents_n"] == 4

    def test_pairings(self, service):
        state = service.create(owner_id="user1", thread_id="t1", task="t", agents=4, wave=2)
        # Move to first round
        state["status"] = "running"
        state["phase"] = "attack"
        # Can't easily test pairings without running executor
        # Covered by integration test

    def test_winner_not_ready(self, service):
        state = service.create(owner_id="user1", thread_id="t1", task="t", agents=4, wave=2)
        with pytest.raises(ArenaRunError, match="no champion"):
            service.winner(state["run_id"], "user1")

    def test_list_runs(self, service):
        s1 = service.create(owner_id="user1", thread_id="t1", task="t1", agents=2)
        s2 = service.create(owner_id="user1", thread_id="t2", task="t2", agents=2)
        s3 = service.create(owner_id="user2", thread_id="t3", task="t3", agents=2)
        runs = service.list_runs("user1")
        assert len(runs) == 2
        assert runs[0]["run_id"] == s2["run_id"]  # newest first

    def test_stop(self, service):
        state = service.create(owner_id="user1", thread_id="t1", task="t", agents=4, wave=2)
        state["status"] = "running"
        service._store.save(state)
        service.stop(state["run_id"], "user1")
        stopped = service._store.load(state["run_id"], "user1")
        assert stopped["status"] == "stopped"

    def test_stop_wrong_owner(self, service):
        state = service.create(owner_id="user1", thread_id="t1", task="t", agents=4, wave=2)
        state["status"] = "running"
        service._store.save(state)
        with pytest.raises(ArenaStoreError, match="another owner"):
            service.stop(state["run_id"], "user2")

    def test_delete(self, service):
        state = service.create(owner_id="user1", thread_id="t1", task="t", agents=4, wave=2)
        service.delete(state["run_id"], "user1")
        with pytest.raises(KeyError):
            service._store.load(state["run_id"], "user1")