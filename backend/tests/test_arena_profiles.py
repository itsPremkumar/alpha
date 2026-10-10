"""Profiles, modes, judge bias controls, hard gates and repair records.

These are the advanced-plan features wired into the running engine:

* ``ArenaService.resolve_profile`` - explicit profile > router > config;
* mode honesty - ``plan`` refuses ``start``, and the modes the executor
  cannot run are refused by name rather than degraded into ``decide``;
* the judge's position/identity bias controls (neutral paths + swap);
* hard gates from the judge's JSON applied to the right solution;
* repair dispositions parsed from the defender's reply and recorded.
"""

from __future__ import annotations

import pytest

from alpha.arena import bracket
from alpha.arena.cards import deal_cards, load_deck
from alpha.arena.config import ArenaConfig
from alpha.arena.executor import ArenaExecutor
from alpha.arena.modes import Profile, spec_for
from alpha.arena.runner import ArenaJob, ArenaJobResult, ScriptedArenaRunner
from alpha.arena.service import ArenaConfirmationRequired, ArenaRunError, ArenaService
from alpha.arena.task_rubrics import pair_order


def _match_number(match_id: str) -> int:
    digits = "".join(c for c in str(match_id) if c.isdigit())
    return int(digits) if digits else 0


@pytest.fixture
def store():
    import tempfile

    from alpha.arena.store import ArenaStore

    with tempfile.TemporaryDirectory() as tmp:
        yield ArenaStore(tmp)


@pytest.fixture
def config() -> ArenaConfig:
    return ArenaConfig(
        enabled=True,
        default_agents=4,
        max_agents=64,
        default_wave=4,
        max_wave=32,
        estimate_tokens_per_call=1000,
        require_confirmation_over_calls=10000,
        max_judge_attempts=2,
        reasoning_bank_seed=False,
    )


# --------------------------------------------------------------- profile resolution


class TestProfileResolution:
    def test_defaults_when_nothing_is_asked_for(self, config) -> None:
        import tempfile

        from alpha.arena.store import ArenaStore

        with tempfile.TemporaryDirectory() as tmp:
            service = ArenaService(store=ArenaStore(tmp), config=config)
            resolved = service.resolve_profile()
            assert resolved["agents"] == config.default_agents
            assert resolved["wave"] == config.default_wave
            assert resolved["profile"] == "custom"
            assert resolved["reason"] == "configured defaults"

    def test_named_profile_supplies_agents_and_wave(self, store, config) -> None:
        service = ArenaService(store=store, config=config)
        resolved = service.resolve_profile(profile="quick")
        assert resolved["agents"] == spec_for(Profile.QUICK).agents
        assert resolved["wave"] == spec_for(Profile.QUICK).wave
        assert resolved["profile"] == "quick"
        assert resolved["reason"] == "explicit profile"

    def test_auto_routes_from_the_task_and_reports_why(self, store, config) -> None:
        service = ArenaService(store=store, config=config)
        short = service.resolve_profile(profile="auto", task="fix the typo")
        long = service.resolve_profile(profile="auto", task="x" * 5000)
        assert short["profile"] == Profile.QUICK.value
        assert long["profile"] == Profile.DEEP.value
        assert short["reason"] and long["reason"]
        assert short["agents"] < long["agents"]

    def test_explicit_agents_beat_the_profile(self, store, config) -> None:
        service = ArenaService(store=store, config=config)
        resolved = service.resolve_profile(profile="deep", agents=2, wave=1)
        assert resolved["agents"] == 2
        assert resolved["wave"] == 1

    def test_unknown_profile_is_refused_by_name(self, store, config) -> None:
        service = ArenaService(store=store, config=config)
        with pytest.raises(ValueError):
            service.resolve_profile(profile="enormous")

    def test_declared_profiles_travel_with_the_resolution(self, store, config) -> None:
        service = ArenaService(store=store, config=config)
        resolved = service.resolve_profile(profile="standard")
        assert resolved["declared"]["profile"] == "standard"
        assert {entry["profile"] for entry in resolved["all_profiles"]} == {"quick", "standard", "deep"}


class TestModes:
    def test_estimate_accepts_plan_and_reports_it(self, store, config) -> None:
        service = ArenaService(store=store, config=config)
        estimate = service.estimate(task="anything", mode="plan")
        assert estimate["mode"] == "plan"
        assert estimate["estimated_calls"] > 0

    def test_plan_run_refuses_to_start(self, store, config) -> None:
        service = ArenaService(store=store, config=config)
        state = service.create(owner_id="u", thread_id="t", task="solve it", mode="plan")
        assert state["mode"] == "plan"
        with pytest.raises(ArenaRunError, match="plan"):
            # Drive would need a runner; the mode gate fires first.
            import asyncio

            asyncio.run(service.start(state["run_id"], "u"))

    def test_unimplemented_modes_are_refused_by_name(self, store, config) -> None:
        service = ArenaService(store=store, config=config)
        for mode in ("compare", "synthesize"):
            with pytest.raises(ValueError, match=mode):
                service.create(owner_id="u", thread_id="t", task="solve it", mode=mode)

    def test_unknown_mode_is_refused(self, store, config) -> None:
        service = ArenaService(store=store, config=config)
        with pytest.raises(ValueError, match="unknown arena mode"):
            service.create(owner_id="u", thread_id="t", task="solve it", mode="speedrun")

    def test_profiles_listing_is_honest_about_what_runs(self, store, config) -> None:
        service = ArenaService(store=store, config=config)
        listing = service.profiles()
        assert {entry["profile"] for entry in listing["profiles"]} == {"quick", "standard", "deep"}
        assert set(listing["implemented_modes"]) == {"decide", "plan"}
        declared = {entry["mode"] for entry in listing["modes"]}
        assert declared == {"decide", "compare", "synthesize", "plan"}
        assert declared > set(listing["implemented_modes"])

    def test_create_records_profile_and_route_reason(self, store, config) -> None:
        service = ArenaService(store=store, config=config)
        state = service.create(owner_id="u", thread_id="t", task="short task", profile="quick")
        assert state["agents_n"] == spec_for(Profile.QUICK).agents
        assert state["profile"] == "quick"
        assert state["route_reason"]
        summary = service.status(state["run_id"], "u")
        assert summary["profile"] == "quick"
        assert summary["mode"] == "decide"


# ------------------------------------------------------------- executor wiring


def _deal(store, agents: int = 2, seed: int = 7, task: str = "solve it"):
    state = bracket.new_run(owner_id="test", task=task, seed=seed, agents_n=agents, wave=2)
    deck = load_deck()
    cards = deal_cards(agents, str(seed), deck)
    bracket.set_cards(state, {a: c for a, c in zip(bracket.agent_ids(agents), cards)})
    store.save(state)
    return state


def _scripted(judge_text: str | None = None) -> ScriptedArenaRunner:
    def script(job: ArenaJob) -> ArenaJobResult:
        if job.kind == "spawn":
            return ArenaJobResult(ok=True, text=f"WROTE {job.reply_hint}")
        if job.kind == "attack":
            return ArenaJobResult(ok=True, text="ATTACK 1 [MAJOR] flaw\nWhere: here\nProblem: it fails")
        if job.kind == "defend":
            return ArenaJobResult(ok=True, text="ATTACK 1: CONCEDE. fixed the flaw\nWROTE " + job.reply_hint)
        if job.kind == "judge":
            text = judge_text or (
                '{"scores": {"a": {"correctness": 9, "completeness": 8, "specificity": 7, '
                '"robustness": 8, "clarity": 9, "fatal": false}, '
                '"b": {"correctness": 5, "completeness": 5, "specificity": 5, '
                '"robustness": 5, "clarity": 5, "fatal": false}}, '
                '"winner": "a", "reason": "a stronger"}'
            )
            return ArenaJobResult(ok=True, text=text)
        if job.kind == "final":
            return ArenaJobResult(ok=True, text="VERDICT: PASS\nREASON: passes")
        return ArenaJobResult(ok=False, text="", error="unexpected job")

    return ScriptedArenaRunner(script)


class TestJudgeBiasControls:
    @pytest.mark.asyncio
    async def test_judge_reads_neutral_paths(self, store) -> None:
        state = _deal(store)
        executor = ArenaExecutor(store, _scripted(), wave=2, on_event=lambda n, p: None)
        final = await executor.execute(state)
        match = final["rounds"][0]["matches"][0]
        presentation = match["judge_presentation"]
        assert presentation["blind"] is True
        for label in ("a", "b"):
            path = presentation[f"{label}_path"]
            agent = presentation[f"{label}_agent"]
            assert f"judge-{match['id']}-{label}.md" in path
            assert f"{agent}-solution" not in path, "the judge must not see the author's name"
        assert presentation["a_agent"] in (match["a"], match["b"])
        assert presentation["b_agent"] in (match["a"], match["b"])
        assert presentation["a_agent"] != presentation["b_agent"]

    @pytest.mark.asyncio
    async def test_presentation_is_deterministic_and_maps_the_winner_back(self, store) -> None:
        state = _deal(store, seed=11)
        executor = ArenaExecutor(store, _scripted(), wave=2, on_event=lambda n, p: None)
        final = await executor.execute(state)
        match = final["rounds"][0]["matches"][0]
        presentation = match["judge_presentation"]
        expected_swap = not pair_order(final["seed"], 1, _match_number(match["id"]))
        assert presentation["swapped"] == expected_swap
        # The scripted judge always prefers its label A, so the winner is
        # whichever physical side label A was carrying.
        assert match["winner"] == presentation["a_agent"]

    @pytest.mark.asyncio
    async def test_swapped_presentation_is_exposed_on_pairings(self, store) -> None:
        from alpha.arena.service import ArenaService

        state = _deal(store, seed=5)
        executor = ArenaExecutor(store, _scripted(), wave=2, on_event=lambda n, p: None)
        await executor.execute(state)
        service = ArenaService(store=store, config=ArenaConfig(reasoning_bank_seed=False))
        pairings = service.pairings(state["run_id"], "test")
        match = pairings["matches"][0]
        assert match["judge_blind"] is True
        assert isinstance(match["judge_swapped"], bool)


class TestHardGatesInTheJudge:
    @pytest.mark.asyncio
    async def test_a_tripped_gate_fails_the_side_it_belongs_to(self, store) -> None:
        gated = (
            '{"scores": '
            '{"a": {"correctness": 9, "completeness": 9, "specificity": 9, "robustness": 9, "clarity": 9, "fatal": false}, '
            '"b": {"correctness": 9, "completeness": 9, "specificity": 9, "robustness": 9, "clarity": 9, "fatal": false}}, '
            '"gates": {"a": [{"id": "tests", "tripped": true, "detail": "2 failing"}], "b": []}, '
            '"winner": "a", "reason": "a"}'
        )
        state = _deal(store, seed=3)
        executor = ArenaExecutor(store, _scripted(gated), wave=2, on_event=lambda n, p: None)
        final = await executor.execute(state)
        match = final["rounds"][0]["matches"][0]
        presentation = match["judge_presentation"]
        # The gate landed on the side carrying label A, and that side lost.
        gated_agent = presentation["a_agent"]
        other = presentation["b_agent"]
        assert match["winner"] == other
        report = match["gates"][gated_agent]
        assert report[0]["id"] == "tests"
        assert report[0]["tripped"] is True

    @pytest.mark.asyncio
    async def test_a_healthy_gate_changes_nothing(self, store) -> None:
        clean = (
            '{"scores": '
            '{"a": {"correctness": 9, "completeness": 9, "specificity": 9, "robustness": 9, "clarity": 9, "fatal": false}, '
            '"b": {"correctness": 2, "completeness": 2, "specificity": 2, "robustness": 2, "clarity": 2, "fatal": false}}, '
            '"gates": {"a": [{"id": "tests", "tripped": false}], "b": []}, '
            '"winner": "a", "reason": "a"}'
        )
        state = _deal(store, seed=3)
        executor = ArenaExecutor(store, _scripted(clean), wave=2, on_event=lambda n, p: None)
        final = await executor.execute(state)
        match = final["rounds"][0]["matches"][0]
        assert match["winner"] == match["judge_presentation"]["a_agent"]
        assert match["gates"][match["judge_presentation"]["a_agent"]][0]["tripped"] is False


class TestRepairRecord:
    @pytest.mark.asyncio
    async def test_defender_dispositions_are_recorded(self, store) -> None:
        state = _deal(store, agents=4, seed=13)
        executor = ArenaExecutor(store, _scripted(), wave=2, on_event=lambda n, p: None)
        final = await executor.execute(state)
        match = final["rounds"][0]["matches"][0]
        assert match["repairs"], "the defender's dispositions must be captured"
        assert match["repairs"][0]["disposition"] == "CONCEDED"
        assert match["repairs"][0]["attack_index"] == 1
        # Every recorded attack was answered, so nothing is left unanswered.
        assert all(indexes == [] for indexes in match["repairs_unresolved"].values())

    @pytest.mark.asyncio
    async def test_a_silent_defender_leaves_attacks_unanswered(self, store) -> None:
        state = _deal(store)
        executor = ArenaExecutor(store, _scripted(), wave=2, on_event=lambda n, p: None)
        await executor.execute(state)
        match = state["rounds"][0]["matches"][0]
        defender = match["a"]
        before = len(match.get("repairs", []))
        # A reply that answers nothing records nothing and reports the gap.
        executor._record_repairs(state, {"match": match["id"], "defender": defender}, "no dispositions here")
        assert len(match.get("repairs", [])) == before
        executor._record_repairs(state, {"match": match["id"], "defender": defender}, "ATTACK 1: REBUT. handled")
        assert len(match.get("repairs", [])) == before + 1
        assert match["repairs_unresolved"][defender] == []


class TestConfirmationStillApplies:
    def test_profiles_do_not_bypass_the_confirmation_gate(self, store) -> None:
        import tempfile

        from alpha.arena.store import ArenaStore

        with tempfile.TemporaryDirectory() as tmp:
            service = ArenaService(
                store=ArenaStore(tmp),
                config=ArenaConfig(
                    enabled=True,
                    default_agents=4,
                    max_agents=64,
                    default_wave=4,
                    max_wave=32,
                    estimate_tokens_per_call=1000,
                    require_confirmation_over_calls=1,
                    reasoning_bank_seed=False,
                ),
            )
            estimate = service.estimate(profile="quick", task="t")
            assert estimate["confirmation_required"] is True
            state = service.create(owner_id="u", thread_id="t", task="t", profile="quick")
            with pytest.raises(ArenaConfirmationRequired):
                import asyncio

                asyncio.run(service.start(state["run_id"], "u"))
