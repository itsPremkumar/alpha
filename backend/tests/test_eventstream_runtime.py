"""Tests for Typed EventStream and Action-Observation Audit Ledger."""

from agent_workspace.events.stream.actions import (
    ActionType,
    AgentFinishAction,
    CmdRunAction,
    CriticAction,
    FileEditAction,
)
from agent_workspace.events.stream.ledger import EventStreamLedger
from agent_workspace.events.stream.observations import (
    CmdOutputObservation,
    CriticObservation,
    ErrorObservation,
    FileEditObservation,
    ObservationType,
)


def test_typed_actions_and_observations():
    # 1. CmdRunAction & Output
    cmd_act = CmdRunAction(command="git status", thought="Checking workspace state")
    assert cmd_act.action_type == ActionType.CMD_RUN
    assert cmd_act.command == "git status"

    cmd_obs = CmdOutputObservation(exit_code=0, stdout="clean", action_id=cmd_act.action_id)
    assert cmd_obs.observation_type == ObservationType.CMD_OUTPUT
    assert cmd_obs.action_id == cmd_act.action_id

    # 2. FileEditAction & Observation
    edit_act = FileEditAction(path="main.py", content="print('hello')", mode="write")
    edit_obs = FileEditObservation(path="main.py", success=True, lines_added=1, action_id=edit_act.action_id)
    assert edit_obs.lines_added == 1

    # 3. CriticAction & CriticObservation
    critic_act = CriticAction(critic_name="EmptyPatchCritic")
    critic_obs = CriticObservation(critic_name="EmptyPatchCritic", verdict="approved", reason="Patch verified")
    assert critic_obs.verdict == "approved"

    # 4. ErrorObservation
    err_obs = ErrorObservation(error_type="FileNotFoundError", message="No such file config.json")
    assert err_obs.error_type == "FileNotFoundError"


def test_event_stream_ledger_workflow(tmp_path):
    ledger = EventStreamLedger(session_id="test_session_100")

    # Append Action 1
    act1 = CmdRunAction(command="pytest tests/")
    ledger.append_action(act1)

    # Append Observation 1
    obs1 = CmdOutputObservation(exit_code=0, stdout="3 passed in 0.2s", action_id=act1.action_id)
    ledger.append_observation(obs1)

    # Append Action 2 (Finish)
    act2 = AgentFinishAction(final_thought="All tests green, task complete", deliverables=["tests/"])
    ledger.append_action(act2)

    # Assert trajectory pairing
    traj = ledger.get_trajectory()
    assert len(traj) == 2
    assert traj[0][0].action_id == act1.action_id
    assert len(traj[0][1]) == 1
    assert traj[0][1][0].content == "3 passed in 0.2s"

    # Assert replay session
    replay_steps = list(ledger.replay_session())
    assert len(replay_steps) == 3
    assert replay_steps[0]["event_category"] == "action"
    assert replay_steps[1]["event_category"] == "observation"

    # Assert export JSONL
    export_file = tmp_path / "events.jsonl"
    ledger.export_jsonl(export_file)
    assert export_file.exists()
    lines = export_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3

    # Assert stats
    stats = ledger.summary_stats()
    assert stats["total_events"] == 3
    assert stats["total_actions"] == 2
    assert stats["total_observations"] == 1
    assert stats["total_errors"] == 0
