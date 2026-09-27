"""Phase 1: the war room takes its shape from a deliberation strategy.

Before this, ``build_default_config()`` always produced the same three stages
(``positions -> cross_exam -> synthesis``) and the 11-value
``DeliberationStrategy`` enum was unreachable from the war room. These tests pin
the strategy-driven plan and - just as importantly - pin that a room opened
*without* a strategy behaves exactly as it did before.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from alpha.bots.events import OrgEventStore
from alpha.deliberation.models import DeliberationStrategy
from alpha.groups.war_room import (
    DEFAULT_STAGE_NAMES,
    STRATEGY_STAGE_PLANS,
    StageBudget,
    StageSpec,
    WarRoom,
    WarRoomConfig,
    build_default_config,
    plan_stages,
    resolve_strategy,
)

ALL_STRATEGIES = [strategy for strategy in DeliberationStrategy if strategy is not DeliberationStrategy.AUTO]


@pytest.fixture
def env(tmp_path):
    return {"root": tmp_path, "store": OrgEventStore(tmp_path / "events.jsonl")}


def make_room(env, config, impls, *, room="wr", moderator=None):
    return WarRoom(
        config,
        participants=impls,
        moderator=moderator,
        room=room,
        root=env["root"],
        ledger_store=env["store"],
        clock=time.monotonic,
    )


def eager(prefix):
    async def participant(ctx):
        return f"{prefix} on {ctx.stage.name}\nSTATED CLAIMS: adopt postgres"

    return participant


# ---------------------------------------------------------------------------
# backwards compatibility: no strategy means the room that always existed
# ---------------------------------------------------------------------------
def test_no_strategy_reproduces_the_original_three_stage_room():
    config = build_default_config("topic", ["a", "b"])
    assert [stage.name for stage in config.stages] == list(DEFAULT_STAGE_NAMES)
    assert config.strategy is None
    # The run records the absence honestly rather than borrowing a name.
    assert config.to_dict()["strategy"] == "legacy_fixed"
    # And the original gating is untouched: every collecting stage still ends
    # the run when it fails quorum.
    assert all(stage.requires_previous_quorum for stage in config.stages)


def test_a_legacy_run_still_reports_legacy_fixed(env):
    config = build_default_config("topic", ["alice", "bob"], stage_timeout_seconds=1.0, stage_grace_seconds=0.3)
    room = make_room(env, config, {"alice": eager("alice"), "bob": eager("bob")})
    run = asyncio.run(room.execute())
    assert run.strategy == "legacy_fixed"
    assert run.strategy_rationale == ""
    assert run.to_dict()["strategy"] == "legacy_fixed"


# ---------------------------------------------------------------------------
# every strategy produces a real, distinct plan
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda strategy: strategy.value)
def test_every_strategy_has_a_plan(strategy):
    plan = plan_stages(strategy)
    assert plan, f"{strategy.value} has an empty plan"
    assert plan[-1].collects is False, f"{strategy.value} must end in a non-collecting verdict stage"
    assert any(stage.collects for stage in plan), f"{strategy.value} collects nothing at all"
    names = [stage.name for stage in plan]
    assert len(names) == len(set(names)), f"{strategy.value} repeats a stage name: {names}"
    assert all(stage.prompt.strip() for stage in plan), f"{strategy.value} has an empty prompt"


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=lambda strategy: strategy.value)
def test_only_the_deciding_stage_can_end_a_strategy_room(strategy):
    """Exploratory stages must not terminate the room on failing quorum.

    In a debate nobody is expected to agree after one round, and a red-team
    attack is expected to find something. If every collecting stage could end the
    run, those strategies would be unusable.
    """
    plan = plan_stages(strategy)
    collecting = [stage for stage in plan if stage.collects]
    gating = [stage for stage in collecting if stage.requires_previous_quorum]
    assert len(gating) == 1, f"{strategy.value} has {len(gating)} gating stages, expected exactly 1"
    assert gating[0] is collecting[-1], f"{strategy.value} gates on {gating[0].name} rather than its last collecting stage"


def test_the_plans_actually_differ_from_each_other():
    shapes = {strategy.value: tuple(plan.name for plan in plan_stages(strategy)) for strategy in ALL_STRATEGIES}
    assert len(set(shapes.values())) >= len(ALL_STRATEGIES) - 1, shapes
    assert shapes["red_team"] == ("proposal", "attack", "defense", "verdict")
    assert shapes["council"] == ("draft", "blind_review", "chairman")
    assert shapes["expert_panel"] == ("expertise", "panel", "synthesis")


def test_every_strategy_in_the_enum_has_a_plan_or_is_auto():
    """A new enum value must not silently fall through to a KeyError."""
    for strategy in DeliberationStrategy:
        if strategy is DeliberationStrategy.AUTO:
            continue
        assert strategy in STRATEGY_STAGE_PLANS, f"{strategy.value} has no entry in STRATEGY_STAGE_PLANS"


# ---------------------------------------------------------------------------
# DEBATE is the one plan whose length varies
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rounds", [1, 2, 3, 5])
def test_debate_rounds_interleave_between_one_opening_and_one_judgment(rounds):
    plan = plan_stages(DeliberationStrategy.DEBATE, debate_rounds=rounds)
    assert plan[0].name == "opening"
    assert plan[-1].name == "judge"
    middle = plan[1:-1]
    assert len(middle) == rounds
    assert all(stage.collects for stage in middle)
    assert all(stage.name.startswith("cross_exam") for stage in middle)
    # Only the LAST round gates; earlier rounds are exploratory.
    assert [stage.requires_previous_quorum for stage in middle] == [False] * (rounds - 1) + [True]


def test_debate_rounds_are_floored_at_one():
    plan = plan_stages(DeliberationStrategy.DEBATE, debate_rounds=0)
    assert len(plan) == 3
    assert plan[1].requires_previous_quorum is True


# ---------------------------------------------------------------------------
# resolution: explicit, AUTO, unknown
# ---------------------------------------------------------------------------
def test_an_explicit_strategy_resolves_to_itself_with_no_rationale():
    strategy, rationale = resolve_strategy("red_team", "anything")
    assert strategy is DeliberationStrategy.RED_TEAM
    assert rationale == ""


def test_none_resolves_to_no_strategy():
    assert resolve_strategy(None, "topic") == (None, "")
    assert resolve_strategy("", "topic") == (None, "")
    assert resolve_strategy("   ", "topic") == (None, "")


def test_an_unknown_strategy_falls_back_and_says_why():
    strategy, rationale = resolve_strategy("not_a_strategy", "topic")
    assert strategy is None
    assert "not_a_strategy" in rationale
    assert "legacy fixed plan" in rationale


def test_auto_resolves_through_the_deliberation_router_and_keeps_its_words(monkeypatch):
    from alpha.deliberation.router import DeliberationRouter, RouterEvaluation, TaskDifficulty, TaskRisk

    monkeypatch.setattr(
        DeliberationRouter,
        "classify_smart",
        staticmethod(
            lambda *args, **kwargs: RouterEvaluation(
                difficulty=TaskDifficulty.HIGH_RISK,
                risk=TaskRisk.CRITICAL,
                strategy=DeliberationStrategy.COUNCIL,
                roster_models=["a", "b"],
                rationale="destructive operation detected",
                worthwhile=True,
            )
        ),
    )

    strategy, rationale = resolve_strategy("auto", "drop table")
    assert strategy is DeliberationStrategy.COUNCIL
    assert rationale == "destructive operation detected"


def test_a_router_fault_falls_back_to_council_and_discloses_it(monkeypatch):
    from alpha.deliberation.router import DeliberationRouter

    def boom(*args, **kwargs):
        raise RuntimeError("router offline")

    monkeypatch.setattr(DeliberationRouter, "classify_smart", staticmethod(boom))

    strategy, rationale = resolve_strategy("auto", "topic")
    assert strategy is DeliberationStrategy.COUNCIL
    assert "router unavailable" in rationale
    assert "RuntimeError" in rationale


# ---------------------------------------------------------------------------
# a strategy-shaped room actually runs
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("strategy", [DeliberationStrategy.COUNCIL, DeliberationStrategy.RED_TEAM], ids=lambda strategy: strategy.value)
def test_a_strategy_room_runs_to_a_real_synthesis(env, strategy):
    config = build_default_config(
        "should we adopt strategy rooms",
        ["alice", "bob"],
        stage_timeout_seconds=2.0,
        stage_grace_seconds=0.5,
        strategy=strategy,
    )
    assert config.strategy is strategy

    seen: list[str] = []

    async def participant(ctx):
        seen.append(ctx.stage.name)
        return f"{ctx.participant} on {ctx.stage.name}\nSTATED CLAIMS: adopt strategy rooms"

    room = make_room(env, config, {"alice": participant, "bob": participant})
    run = asyncio.run(room.execute())

    assert run.strategy == strategy.value
    assert [stage.name for stage in run.stages] == [plan.name for plan in plan_stages(strategy)]
    assert seen, "no participant was ever dispatched"
    assert run.status in {"succeeded", "partial"}
    assert run.synthesis, "a completed strategy room must produce a synthesis"


def test_a_debate_room_does_not_die_early_when_round_one_fails_quorum(env):
    """The central point of exploratory gating.

    Under the old fixed plan, a room whose first stage did not reach quorum ended
    immediately. A debate whose opening round disagrees is working as intended,
    so it must continue to the next round.
    """
    config = build_default_config(
        "contested trade-off",
        ["alice", "bob"],
        stage_timeout_seconds=2.0,
        stage_grace_seconds=0.5,
        strategy=DeliberationStrategy.DEBATE,
        debate_rounds=2,
    )

    async def participant(ctx):
        # Deliberately unhelpful: this stage will NOT reach quorum.
        if ctx.stage.name.startswith("cross_exam"):
            return ""
        return f"{ctx.participant} on {ctx.stage.name}\nSTATED CLAIMS: keep postgres"

    room = make_room(env, config, {"alice": participant, "bob": participant})
    run = asyncio.run(room.execute())

    assert run.status != "timeout", "an empty round should not be reported as a clock problem"
    ran = [stage.name for stage in run.stages]
    assert "cross_exam" in ran
    # The first cross_exam is exploratory, so the room reached the second one.
    assert "cross_exam_2" in ran, f"the room stopped after the first round: {ran}"


def test_the_config_serialises_its_strategy():
    config = build_default_config("topic", ["a", "b"], strategy="expert_panel")
    payload = config.to_dict()
    assert payload["strategy"] == "expert_panel"
    assert [stage["name"] for stage in payload["stages"]] == ["expertise", "panel", "synthesis"]


def test_a_config_can_still_be_built_by_hand_without_a_strategy():
    """Hand-built configs keep working; strategy is additive, not required."""
    stages = (
        StageSpec(
            name="positions",
            prompt="say something",
            budget=StageBudget(name="positions", timeout_seconds=1.0, grace_seconds=0.1),
        ),
    )
    config = WarRoomConfig(topic="t", participants=("a", "b"), stages=stages)
    assert config.strategy is None
    assert config.to_dict()["strategy"] == "legacy_fixed"
    # The failure for an empty stage list is still about stages, not strategy.
    with pytest.raises(ValueError, match="stage"):
        WarRoomConfig(topic="t", participants=("a", "b"), stages=())
