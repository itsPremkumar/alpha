"""Tests for the standing-goal loop: the judge, the gates, and the boundary.

The bias in this file is adversarial. Every test asks "how could this loop
declare a goal finished when it is not?" and then tries to make it do so. A
passing suite here means the red paths actually bite, because each of them was
first written as a failing expectation and the implementation was changed until
it held (see the ``test_the_tests_bite`` cases at the bottom, which re-run the
suite's central assertions against deliberately broken doubles).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

import pytest

from alpha.mission.goalloop import (
    BUDGET_EXHAUSTED_MESSAGE,
    CONTINUATION_ROLE,
    GateReport,
    GateRunner,
    GoalJudge,
    GoalLoopEngine,
    GoalState,
    GoalStatus,
    GoalStore,
    GoalVerdict,
    QualityGate,
    TurnAction,
    build_request,
    parse_goal_text,
    parse_verdict,
)
from alpha.mission.goalloop.verdict import JUDGE_UNAVAILABLE_REASON, JUDGE_UNREADABLE_REASON

# --------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _isolated_runtime_home(tmp_path, monkeypatch):
    """Point the goal store at a throwaway directory for every test."""
    home = tmp_path / "agent-workspace"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(home))
    from alpha.mission.goalloop import state as state_module

    state_module.reset_goal_store()
    yield home
    state_module.reset_goal_store()


def _judge_returning(payload: str):
    async def _judge(_request: Any) -> str:
        return payload

    return _judge


def _judge_raising(exc: BaseException):
    async def _judge(_request: Any) -> str:
        raise exc

    return _judge


def _judge_sequence(*payloads: str):
    """A judge that returns each payload in turn, then repeats the last."""
    remaining = list(payloads)

    async def _judge(_request: Any) -> str:
        return remaining.pop(0) if len(remaining) > 1 else (remaining[0] if remaining else "{}")

    return _judge


def _done(reason: str = "pytest reported 3 passed") -> str:
    return json.dumps({"verdict": "done", "reason": reason})


def _continue(reason: str = "2 of 4 files created") -> str:
    return json.dumps({"verdict": "continue", "reason": reason})


def _run(engine: GoalLoopEngine, state: GoalState, text: str = "did some work", **kwargs: Any):
    return asyncio.run(engine.on_turn_end(state, text, **kwargs))


def _state(**kwargs: Any) -> GoalState:
    kwargs.setdefault("objective", "Create four files note_1..note_4.txt, one per turn")
    return GoalState(session_id="test-session", **kwargs)


# ------------------------------------------------- (c) strict verdict parsing


class TestVerdictGrammar:
    def test_valid_verdicts_parse(self):
        for text, expected in (
            ('{"verdict": "done", "reason": "ok"}', GoalVerdict.DONE),
            ('{"verdict": "blocked", "reason": "needs a human"}', GoalVerdict.BLOCKED),
            ('{"verdict": "continue", "reason": "1 of 4"}', GoalVerdict.CONTINUE),
            ('{"verdict": "wait", "reason": "build running"}', GoalVerdict.WAIT),
        ):
            parsed = parse_verdict(text)
            assert parsed is not None, text
            assert parsed.verdict is expected

    def test_malformed_is_never_done(self):
        for text in (
            "the goal looks done to me",
            '{"verdict": "done"',  # truncated
            '{"reason": "no verdict key"}',
            '{"verdict": "finished", "reason": "synonym"}',
            '{"verdict": null}',
            '{"verdict": 1}',
            "```json\n{\"verdict\": \"done\"}",  # unterminated fence
            "",
            None,
        ):
            assert parse_verdict(text) is None, text

    def test_legacy_done_bool_shape_is_refused(self):
        """``{"done": true}`` must not be readable as ``done``.

        Upstream Hermes still tolerates this shape. Accepting it would mean a
        response that never said the word "done" could complete a goal, which
        is exactly the "looks done" failure this loop exists to stop.
        """
        assert parse_verdict('{"done": true, "reason": "finished"}') is None
        assert parse_verdict('{"done": false}') is None

    def test_prose_around_json_is_refused(self):
        assert parse_verdict('I think so. {"verdict": "done", "reason": "x"}') is None

    def test_fenced_json_is_accepted(self):
        parsed = parse_verdict('```json\n{"verdict": "continue", "reason": "more to do"}\n```')
        assert parsed is not None
        assert parsed.verdict is GoalVerdict.CONTINUE

    def test_non_string_reason_is_a_parse_failure(self):
        assert parse_verdict('{"verdict": "done", "reason": {"nested": true}}') is None

    def test_reason_is_bounded(self):
        parsed = parse_verdict(json.dumps({"verdict": "continue", "reason": "x" * 5000}))
        assert parsed is not None
        assert len(parsed.reason) <= 400

    def test_judge_turns_unreadable_into_continue(self):
        outcome = GoalJudge.interpret("absolutely finished, trust me")
        assert outcome.verdict is GoalVerdict.CONTINUE
        assert outcome.degraded is True
        assert outcome.reason == JUDGE_UNREADABLE_REASON


# ------------------------------------------- (a) loops across turns, terminates


class TestGoalLoopsAndTerminates:
    def test_goal_loops_across_turns_then_terminates(self):
        engine = GoalLoopEngine(
            judge=GoalJudge(_judge_sequence(_continue("1 of 4"), _continue("2 of 4"), _done("all 4 exist")))
        )
        state = _state()

        first = _run(engine, state)
        assert first.action is TurnAction.CONTINUE
        assert state.turns_used == 1
        # The prompt names the turn that is about to run.
        assert "turn 2/20" in first.continuation

        second = _run(engine, state)
        assert second.action is TurnAction.CONTINUE
        assert state.turns_used == 2

        third = _run(engine, state)
        assert third.action is TurnAction.DONE
        assert state.status is GoalStatus.DONE
        assert state.turns_used == 2  # the completing turn is not a continuation

    def test_judge_is_not_consulted_once_the_goal_is_done(self):
        calls: list[Any] = []

        async def _judge(request: Any) -> str:
            calls.append(request)
            return _done()

        engine = GoalLoopEngine(judge=GoalJudge(_judge))
        state = _state()
        assert _run(engine, state).action is TurnAction.DONE
        assert _run(engine, state).action is TurnAction.IDLE
        assert len(calls) == 1

    def test_continuation_is_a_plain_user_role_message(self):
        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(_continue())))
        decision = _run(engine, _state())
        message = decision.continuation_message()
        assert message is not None
        assert message["role"] == CONTINUATION_ROLE == "user"
        assert set(message) == {"role", "content"}
        assert message["content"] == decision.continuation


# ------------------------------------------- (b) a red gate cannot reach done


class TestRedGateBlocksDone:
    def test_red_gate_blocks_done_even_when_model_insists(self):
        """THE test that proves the phase: the model says done, the gate says no.

        The judge here is maximally obedient - it returns ``done`` on every
        single call. If the loop consulted it before the gate, this would pass
        the goal immediately.
        """
        engine = GoalLoopEngine(
            judge=GoalJudge(_judge_returning(_done("I finished everything"))),
            gate_runner=GateRunner(lambda gate: (1, "FAILED tests/test_x.py::test_y - expected 3 got 2")),
        )
        state = _state()
        state.add_gate(QualityGate(command="pytest -q"))

        seen = []
        for _ in range(10):
            decision = _run(engine, state)
            seen.append(decision.action)
            if decision.action is not TurnAction.CONTINUE:
                break

        # It continues (so the agent can read the failure) and then auto-pauses
        # on the 3rd consecutive red boundary. At no point is it DONE.
        assert TurnAction.DONE not in seen
        assert seen == [TurnAction.CONTINUE, TurnAction.CONTINUE, TurnAction.PAUSED]
        assert state.status is GoalStatus.PAUSED
        assert state.status is not GoalStatus.DONE
        assert "quality gate exhausted" in _run(engine, state).reason or state.pause_reason

    def test_red_gate_output_becomes_the_continuation_prompt(self):
        """The agent iterates against the real failure, not a vibe."""
        engine = GoalLoopEngine(
            judge=GoalJudge(_judge_returning(_done())),
            gate_runner=GateRunner(lambda gate: (1, "1 failed, 42 passed\nE   assert 3 == 2")),
        )
        state = _state()
        state.add_gate(QualityGate(command="pytest -q", label="unit tests"))
        decision = _run(engine, state)
        assert decision.action is TurnAction.CONTINUE
        assert "QUALITY GATES ARE RED" in decision.continuation
        assert "exit=1" in decision.continuation
        assert "assert 3 == 2" in decision.continuation
        assert "unit tests" in decision.continuation
        assert "consecutive failures: 1" in decision.continuation

    def test_the_gate_bound_is_three_consecutive_red_boundaries(self):
        engine = GoalLoopEngine(
            judge=GoalJudge(_judge_returning(_done())),
            gate_runner=GateRunner(lambda gate: (1, "still red")),
        )
        state = _state()
        state.add_gate(QualityGate(command="false"))
        assert _run(engine, state).action is TurnAction.CONTINUE
        assert _run(engine, state).action is TurnAction.CONTINUE
        third = _run(engine, state)
        assert third.action is TurnAction.PAUSED
        assert "exhausted" in third.reason
        assert "/goal gate remove 1" in third.reason
        assert "/goal resume" in third.reason

    def test_green_gate_lets_the_judge_decide(self):
        engine = GoalLoopEngine(
            judge=GoalJudge(_judge_returning(_done("pytest -q exited 0"))),
            gate_runner=GateRunner(lambda gate: (0, "3 passed")),
        )
        state = _state()
        state.add_gate(QualityGate(command="pytest -q"))
        decision = _run(engine, state)
        assert decision.action is TurnAction.DONE
        assert state.status is GoalStatus.DONE

    def test_judge_is_never_called_while_a_gate_is_red(self):
        calls: list[Any] = []

        async def _judge(request: Any) -> str:
            calls.append(request)
            return _done()

        engine = GoalLoopEngine(judge=GoalJudge(_judge), gate_runner=GateRunner(lambda gate: (2, "boom")))
        state = _state()
        state.add_gate(QualityGate(command="false"))
        for _ in range(5):
            _run(engine, state)
        assert calls == []
        assert state.status is not GoalStatus.DONE


# ------------------------------------- gate re-runs every boundary (never stale)


class TestGatesAreNeverStale:
    def test_every_boundary_re_executes_the_command(self):
        runs = {"n": 0}

        def _runner(gate: QualityGate) -> tuple[int, str]:
            runs["n"] += 1
            return (0, "3 passed") if runs["n"] >= 3 else (1, f"attempt {runs['n']} failed")

        state = _state()
        state.add_gate(QualityGate(command="pytest -q"))
        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(_done())), gate_runner=GateRunner(_runner))

        # Boundaries 1 and 2 are red. The agent "repairs the input" between them.
        assert _run(engine, state).action is TurnAction.CONTINUE
        assert _run(engine, state).action is TurnAction.CONTINUE
        assert state.gate_failures == {1: 2}
        # Boundary 3 passes. A cached result would still be red here.
        decision = _run(engine, state)
        assert decision.action is TurnAction.DONE
        assert runs["n"] == 3
        assert state.gate_failures == {}

    def test_a_passing_gate_clears_earlier_failures(self):
        """Repair-then-regress must not inherit a dead gate's strike count."""
        outcomes = iter([1, 0, 1, 0, 0])
        engine = GoalLoopEngine(
            judge=GoalJudge(_judge_returning(_done())),
            gate_runner=GateRunner(lambda gate: (next(outcomes), "x")),
        )
        state = _state()
        state.add_gate(QualityGate(command="c"))
        assert _run(engine, state).action is TurnAction.CONTINUE  # fail -> 1
        assert _run(engine, state).action is TurnAction.DONE  # pass -> cleared
        assert state.gate_failures == {}

    def test_gate_output_tail_is_bounded_and_keeps_the_end(self):
        runner = GateRunner(lambda gate: (1, "A" * 100_000 + "THE-REAL-FAILURE"))
        result = runner.run(QualityGate(command="pytest -q"))
        assert len(result.output_tail) < 4 * 1024
        assert "THE-REAL-FAILURE" in result.output_tail
        assert "exit=1" in result.output_tail

    def test_a_gate_that_cannot_run_is_red_never_green(self):
        def _explode(gate: QualityGate) -> tuple[int, str]:
            raise OSError("no shell available")

        result = GateRunner(_explode).run(QualityGate(command="pytest -q"))
        assert result.passed is False
        assert result.status.value == "error"
        assert "OSError" in result.output_tail

    def test_empty_command_is_refused(self):
        result = GateRunner().run(QualityGate(command="   "))
        assert result.passed is False
        assert "no command" in result.reason

    def test_timeout_is_bounded_and_red(self):
        class _TimeoutRunner:
            def __call__(self, gate: QualityGate) -> tuple[int, str]:
                raise subprocess.TimeoutExpired(cmd=gate.command, timeout=gate.timeout_seconds)

        result = GateRunner(_TimeoutRunner()).run(QualityGate(command="sleep 999"))
        assert result.passed is False
        assert result.status.value == "timeout"
        assert "timed out" in result.output_tail

    def test_real_subprocess_gate_runs_and_goes_red_then_green(self):
        """Exercises the real subprocess path, not a double."""
        state = _state()
        state.add_gate(QualityGate(command=f'"{sys.executable}" -c "import sys; sys.exit(0)"'))
        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(_done())))
        assert _run(engine, state).action is TurnAction.DONE

        state2 = _state()
        state2.add_gate(QualityGate(command=f'"{sys.executable}" -c "import sys; sys.exit(3)"'))
        assert _run(engine, state2).action is TurnAction.CONTINUE
        assert "exit=3" in _run(engine, state2).continuation


# ----------------------------- (d) judge error fails open to continue, no wedge


class TestJudgeFailsOpen:
    def test_judge_exception_becomes_continue_not_done_and_not_wedge(self):
        engine = GoalLoopEngine(judge=GoalJudge(_judge_raising(ConnectionError("aux model down"))))
        state = _state()
        decision = _run(engine, state)
        assert decision.action is TurnAction.CONTINUE
        assert decision.should_continue is True
        assert state.status is GoalStatus.ACTIVE
        assert state.status is not GoalStatus.DONE
        assert "degraded" in decision.reason

    def test_no_judge_configured_at_all_still_continues(self):
        """A deployment with no aux model must keep working, not stop and not lie."""
        state = _state()
        decision = _run(GoalLoopEngine(), state)
        assert decision.action is TurnAction.CONTINUE
        assert state.status is GoalStatus.ACTIVE
        assert JUDGE_UNAVAILABLE_REASON in decision.reason

    def test_malformed_judge_output_continues(self):
        state = _state()
        decision = _run(GoalLoopEngine(judge=GoalJudge(_judge_returning("Looks good to me!"))), state)
        assert decision.action is TurnAction.CONTINUE
        assert state.status is not GoalStatus.DONE
        assert JUDGE_UNREADABLE_REASON in decision.reason

    def test_a_forever_broken_judge_still_hits_the_budget_rather_than_looping_forever(self):
        engine = GoalLoopEngine(judge=GoalJudge(_judge_raising(RuntimeError("down"))))
        state = _state(max_turns=3)
        actions = [_run(engine, state).action for _ in range(5)]
        assert actions[:3] == [TurnAction.CONTINUE] * 3
        assert actions[3] is TurnAction.PAUSED
        assert state.status is GoalStatus.PAUSED


# ------------------------------------- (e) unachievable goal blocks, not done


class TestBlockedIsNotDone:
    def test_blocked_pauses_with_a_reason_and_never_completes(self):
        payload = json.dumps({"verdict": "blocked", "reason": "requires a production database that does not exist"})
        state = _state()
        decision = _run(GoalLoopEngine(judge=GoalJudge(_judge_returning(payload))), state)
        assert decision.action is TurnAction.BLOCKED
        assert state.status is GoalStatus.BLOCKED
        assert state.status is not GoalStatus.DONE
        assert state.status is not GoalStatus.CLEARED
        assert "production database" in state.pause_reason
        assert state.turns_used == 0  # a blocked goal burns no budget

    def test_a_blocked_goal_does_not_continue_looping(self):
        payload = json.dumps({"verdict": "blocked", "reason": "needs a human decision"})
        state = _state()
        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(payload)))
        assert _run(engine, state).action is TurnAction.BLOCKED
        assert _run(engine, state).action is TurnAction.IDLE


# ------------------------------------------- (f) turn budget stops the loop


class TestTurnBudget:
    def test_budget_exhaustion_pauses_and_explains_how_to_continue(self):
        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(_continue("still going"))))
        state = _state(max_turns=3)
        for _ in range(3):
            assert _run(engine, state).action is TurnAction.CONTINUE
        decision = _run(engine, state)
        assert decision.action is TurnAction.PAUSED
        assert state.status is GoalStatus.PAUSED
        assert decision.reason == BUDGET_EXHAUSTED_MESSAGE.format(used=3, max_turns=3)
        assert "/goal resume" in decision.reason
        assert "/goal clear" in decision.reason
        assert decision.continuation == ""  # nothing more is fed in

    def test_resume_resets_the_counter_and_continues(self):
        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(_continue())))
        state = _state(max_turns=2)
        _run(engine, state)
        _run(engine, state)
        assert _run(engine, state).action is TurnAction.PAUSED
        state.resume()
        assert state.status is GoalStatus.ACTIVE
        assert state.turns_used == 0
        assert _run(engine, state).action is TurnAction.CONTINUE

    def test_default_budget_is_twenty(self):
        assert _state().max_turns == 20


# ------------------------------------- (g) a user message preempts the loop


class TestUserMessagePreempts:
    def test_user_turn_never_produces_a_continuation(self):
        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(_continue("keep going"))))
        state = _state()
        decision = _run(engine, state, "user said something", user_initiated=True)
        assert decision.action is TurnAction.IDLE
        assert decision.continuation == ""
        assert decision.continuation_message() is None
        assert "precedence" in decision.reason

    def test_user_turn_does_not_burn_continuation_budget(self):
        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(_continue())))
        state = _state(max_turns=2)
        for _ in range(5):
            _run(engine, state, "user", user_initiated=True)
        assert state.turns_used == 0
        assert state.status is GoalStatus.ACTIVE

    def test_a_user_turn_can_still_complete_the_goal(self):
        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(_done("user pasted the passing output"))))
        state = _state()
        decision = _run(engine, state, "here it is", user_initiated=True)
        assert decision.action is TurnAction.DONE
        assert state.status is GoalStatus.DONE

    def test_user_turn_preempts_even_with_a_red_gate(self):
        engine = GoalLoopEngine(
            judge=GoalJudge(_judge_returning(_done())),
            gate_runner=GateRunner(lambda gate: (1, "nope")),
        )
        state = _state()
        state.add_gate(QualityGate(command="false"))
        decision = _run(engine, state, "user", user_initiated=True)
        assert decision.action is TurnAction.IDLE
        assert decision.continuation == ""
        assert state.turns_used == 0
        assert state.status is not GoalStatus.DONE


# --------------------------------- (h) subgoals must be satisfied before done


class TestSubgoals:
    def test_subgoal_is_included_in_the_continuation_and_the_judge_request(self):
        seen: list[Any] = []

        async def _judge(request: Any) -> str:
            seen.append(request)
            return _continue()

        state = _state()
        state.add_subgoal("add a regression test for the bug you patched")
        engine = GoalLoopEngine(judge=GoalJudge(_judge))
        decision = _run(engine, state)
        assert "add a regression test" in decision.continuation
        assert "ADDITIONAL CRITERIA" in decision.continuation
        assert seen[0].subgoals == ("add a regression test for the bug you patched",)

    def test_adding_a_subgoal_does_not_reset_the_loop(self):
        state = _state()
        state.consume_turn()
        state.consume_turn()
        state.add_subgoal("also update the docs")
        assert state.turns_used == 2
        assert state.status is GoalStatus.ACTIVE
        assert state.subgoals == ["also update the docs"]

    def test_removing_and_clearing_subgoals(self):
        state = _state()
        state.add_subgoal("one")
        state.add_subgoal("two")
        assert state.remove_subgoal(1) == "one"
        assert state.subgoals == ["two"]
        with pytest.raises(IndexError):
            state.remove_subgoal(9)
        assert state.clear_subgoals() == 1
        assert state.subgoals == []

    def test_a_subgoal_alone_is_not_a_verdict_criterion_for_completion(self):
        """The judge sees subgoals, but the *goal* cannot be done without them.

        The engine has no way to evaluate a subgoal deterministically, so the
        enforcement is the judge's - and the judge is told so explicitly in the
        request it receives.
        """
        state = _state()
        state.add_subgoal("regression test added")
        request = build_request(state.objective, "done!", subgoals=state.subgoals)
        assert "all must hold" in request.render().lower()


# ------------------------------------------------ (i) state survives restart


class TestPersistence:
    def test_state_survives_a_fresh_store_and_fresh_process_state(self, tmp_path):
        store = GoalStore(directory=tmp_path / "goals")
        state = _state()
        state.add_subgoal("criterion one")
        state.add_gate(QualityGate(command="pytest -q", label="tests"))
        state.consume_turn()
        state.consume_turn()
        store.save(state)

        # A brand new store object with an empty cache: the restart case.
        reborn = GoalStore(directory=tmp_path / "goals")
        loaded = reborn.load("test-session")
        assert loaded is not None
        assert loaded.goal_id == state.goal_id
        assert loaded.objective == state.objective
        assert loaded.subgoals == ["criterion one"]
        assert [g.command for g in loaded.gates] == ["pytest -q"]
        assert loaded.turns_used == 2
        assert loaded.status is GoalStatus.ACTIVE

    def test_paused_state_and_its_reason_survive(self, tmp_path):
        store = GoalStore(directory=tmp_path / "goals")
        state = _state(max_turns=1)
        state.pause("paused for a reason")
        store.save(state)
        loaded = GoalStore(directory=tmp_path / "goals").load("test-session")
        assert loaded is not None
        assert loaded.status is GoalStatus.PAUSED
        assert loaded.pause_reason == "paused for a reason"

    def test_two_sessions_do_not_share_a_goal(self, tmp_path):
        store = GoalStore(directory=tmp_path / "goals")
        first = GoalState(session_id="alpha", objective="goal one")
        second = GoalState(session_id="beta", objective="goal two")
        store.save(first)
        store.save(second)
        fresh = GoalStore(directory=tmp_path / "goals")
        assert fresh.load("alpha").objective == "goal one"
        assert fresh.load("beta").objective == "goal two"

    def test_missing_session_loads_as_none(self, tmp_path):
        assert GoalStore(directory=tmp_path / "goals").load("nope") is None

    def test_engine_persists_through_its_saver(self, tmp_path):
        store = GoalStore(directory=tmp_path / "goals")
        engine = GoalLoopEngine(
            judge=GoalJudge(_judge_returning(_continue())),
            save=store.save,
        )
        state = _state()
        _run(engine, state)
        reloaded = GoalStore(directory=tmp_path / "goals").load("test-session")
        assert reloaded is not None
        assert reloaded.turns_used == 1

    def test_a_broken_saver_cannot_wedge_the_loop(self, tmp_path):
        def _boom(_state: GoalState) -> None:
            raise OSError("disk full")

        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(_continue())), save=_boom)
        decision = _run(engine, _state())
        assert decision.action is TurnAction.CONTINUE


# ------------------------ (j) a continuation does not touch prompt or toolset


class TestPromptCacheStability:
    def test_continuation_carries_no_system_message_and_no_tools(self):
        """Structural check: the decision object has nowhere to put either."""
        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(_continue())))
        decision = _run(engine, _state())
        fields = set(decision.__dataclass_fields__)
        assert "continuation" in fields
        assert not fields & {"system_prompt", "tools", "toolset", "messages", "toolsets"}

    def test_a_full_continue_cycle_leaves_system_prompt_and_toolset_untouched(self):
        """Simulates a host runtime and fingerprints both across a boundary."""

        @dataclass
        class _FakeRuntime:
            system_prompt: str
            toolset: list[str]
            history: list[dict[str, str]]

        runtime = _FakeRuntime(
            system_prompt="You are alpha. <static prefix that must stay cacheable>",
            toolset=["read_file", "patch", "bash"],
            history=[{"role": "user", "content": "hello"}],
        )
        system_before = runtime.system_prompt
        tools_before = list(runtime.toolset)
        history_before = list(runtime.history)

        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(_continue())))
        decision = _run(engine, _state())
        assert decision.should_continue

        # Exactly what a host does: append one plain user message.
        message = decision.continuation_message()
        assert message is not None
        runtime.history.append(message)

        assert runtime.system_prompt == system_before
        assert runtime.toolset == tools_before
        # History grew by exactly one appended message; nothing was rewritten.
        assert len(runtime.history) == len(history_before) + 1
        assert runtime.history[: len(history_before)] == history_before

    def test_engine_signature_exposes_no_system_prompt_or_toolset(self):
        import inspect

        params = set(inspect.signature(GoalLoopEngine.on_turn_end).parameters)
        assert not params & {"system_prompt", "tools", "toolset", "toolsets"}


# ------------------------------------------------- completion contracts


class TestCompletionContracts:
    def test_inline_fields_are_parsed_into_the_contract(self):
        parsed = parse_goal_text(
            "Migrate auth to JWT\n"
            "verify: pytest tests/auth passes\n"
            "constraints: keep the /login response shape unchanged\n"
            "boundaries: only touch services/auth and its tests\n"
            "stop when: a DB schema migration is required"
        )
        assert parsed.objective == "Migrate auth to JWT"
        assert parsed.contract.verification == "pytest tests/auth passes"
        assert parsed.contract.constraints == "keep the /login response shape unchanged"
        assert parsed.contract.boundaries == "only touch services/auth and its tests"
        assert parsed.contract.stop_when == "a DB schema migration is required"

    def test_an_incidental_colon_is_not_mangled(self):
        """A plain goal with a colon keeps the colon and stays the objective."""
        parsed = parse_goal_text("Fix bug: the parser drops commas")
        assert parsed.objective == "Fix bug: the parser drops commas"
        assert parsed.contract.is_empty

    def test_unknown_prefixes_stay_in_the_objective(self):
        parsed = parse_goal_text("Investigate: why do session ids drift\nNote: it is mid-compression")
        assert parsed.objective == "Investigate: why do session ids drift\nNote: it is mid-compression"
        assert parsed.contract.is_empty

    def test_aliases_map_to_canonical_fields(self):
        parsed = parse_goal_text("Do the thing\nverified by: CI green\nscope: repo only\nstop when: needs prod access")
        assert parsed.contract.verification == "CI green"
        assert parsed.contract.boundaries == "repo only"
        assert parsed.contract.stop_when == "needs prod access"

    def test_empty_contract_is_the_default(self):
        parsed = parse_goal_text("just do the thing")
        assert parsed.contract.is_empty
        assert parsed.has_contract is False

    def test_a_contract_raises_the_bar_in_the_judge_request(self):
        state = _state()
        parsed = parse_goal_text("Ship it\nverification: ruff check exits 0")
        state.contract = parsed.contract
        request = build_request(state.objective, "done", contract=state.contract)
        rendered = request.render()
        assert "COMPLETION CONTRACT" in rendered
        assert "ruff check exits 0" in rendered

    def test_drafting_failure_still_yields_a_usable_goal(self):
        from alpha.mission.goalloop import draft_contract

        async def _boom(_objective: str) -> Any:
            raise RuntimeError("aux model unavailable")

        contract, error = asyncio.run(draft_contract("do the thing", _boom))
        assert contract is None
        assert "aux model unavailable" in error

    def test_drafting_from_a_model_reply(self):
        from alpha.mission.goalloop import draft_contract

        async def _ok(_objective: str) -> Any:
            return json.dumps({"outcome": "auth uses JWT", "verification": "pytest tests/auth"})

        contract, error = asyncio.run(draft_contract("do the thing", _ok))
        assert error is None
        assert contract is not None
        assert contract.verification == "pytest tests/auth"


# ---------------------------------------------------------- wait / parking


class TestWaitParking:
    def test_wait_parks_without_consuming_a_turn(self):
        payload = json.dumps({"verdict": "wait", "reason": "CI is still running"})
        state = _state()
        decision = _run(GoalLoopEngine(judge=GoalJudge(_judge_returning(payload))), state)
        assert decision.action is TurnAction.WAIT
        assert decision.wait_until > 0
        assert state.turns_used == 0
        assert decision.continuation == ""

    def test_an_expired_park_cannot_wedge_the_loop(self):
        payload = json.dumps({"verdict": "wait", "reason": "watcher"})
        state = _state()
        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(payload)))
        _run(engine, state)
        state.wait_until = 1.0  # a deadline already in the past
        decision = _run(engine, state)
        assert decision.action is TurnAction.CONTINUE


# ------------------------------------------ the tests themselves must bite


class TestTheTestsBite:
    """Prove the central assertions fail when the invariant is removed.

    Each case re-runs one of the suite's core scenarios against a deliberately
    broken double and asserts the loop does the WRONG thing - i.e. that the
    passing test above it is not vacuous. Nothing here mutates a tracked file.
    """

    def test_without_the_gate_first_rule_a_red_gate_would_reach_done(self):
        """Counterfactual: judge-before-gate reaches ``done`` on a red suite."""
        judge = GoalJudge(_judge_returning(_done("trust me")))
        # Judge consulted directly, gates never consulted.
        outcome = asyncio.run(judge.judge(build_request("anything", "I finished")))
        assert outcome.verdict is GoalVerdict.DONE  # the model alone completes it

        # With the gate actually in front, the same judge cannot complete it.
        engine = GoalLoopEngine(judge=judge, gate_runner=GateRunner(lambda gate: (1, "1 failed")))
        state = _state()
        state.add_gate(QualityGate(command="pytest -q", max_attempts=1))
        assert _run(engine, state).action is not TurnAction.DONE

    def test_a_permissive_parser_would_treat_a_malformed_verdict_as_done(self):
        permissive = '{"verdict": "finishing", "reason": "x"}'
        # The strict parser refuses it...
        assert parse_verdict(permissive) is None
        # ...and a naive substring check, which is what a lax implementation
        # would do, happily matches "done"-shaped text.
        assert "done" in '{"verdict": "done"}'

    def test_without_a_budget_the_loop_would_never_stop(self):
        """Counterfactual: a judge that never says done terminates ONLY on budget.

        This is the whole reason fail-open is safe. If the judge is broken and
        the goal is a real one, the loop still stops - on the turn budget, with
        an explanation - rather than spinning.
        """
        state = _state(max_turns=4)
        engine = GoalLoopEngine(judge=GoalJudge(_judge_returning(_continue("never done"))))
        actions = [_run(engine, state).action for _ in range(4)]
        assert actions == [TurnAction.CONTINUE] * 4
        assert state.turns_used == 4
        final = _run(engine, state)
        assert final.action is TurnAction.PAUSED
        assert state.status is GoalStatus.PAUSED

    def test_a_red_gate_still_reaches_a_pause_eventually(self):
        report = GateReport(results=GateRunner(lambda gate: (1, "x")).run_all([QualityGate(command="c")]))
        assert report.all_passed is False
        assert len(report.red) == 1
        assert report.red[0].status.value == "fail"

    def test_without_the_fence_rule_a_truncated_response_would_parse(self):
        """Counterfactual for the closed-fence rule.

        Without it, a response truncated mid-stream after a well-formed object
        would parse as a clean verdict. The truncation is exactly when the
        judge is least trustworthy.
        """
        truncated = '```json\n{"verdict": "done", "reason": "all four fi'
        assert parse_verdict(truncated) is None
        # The same object, unterminated string, is also refused.
        assert parse_verdict('{"verdict": "done", "reason": "all four fi') is None
