"""The completion critics are wired into the terminal-claim path.

``alpha.critic`` was a complete, fully tested package with no caller: a terminal
message was never checked against the turn's own tool history, so an agent whose
last action failed could declare success and the run would end. These tests pin
the wiring, the retry, and the two ways it must stay harmless -- a budget that
prevents looping, and a critic that raises not taking down the run.
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from alpha.agents.middlewares.finish_first_verifier_middleware import FinishFirstVerifierMiddleware
from alpha.config.app_config import AppConfig
from alpha.config.sandbox_config import SandboxConfig
from alpha.config.verification_config import VerificationConfig
from alpha.critic import AgentFinishedCritic, CriticPipeline
from alpha.critic.empty_patch import EmptyPatchCritic


class _Runtime:
    """Minimal Runtime stand-in: only ``context`` is read."""

    def __init__(self, **context):
        self.context = context


class _ModelRequest:
    """Minimal ``ModelRequest`` stand-in: only ``runtime``, ``messages`` and ``override`` are read."""

    def __init__(self, runtime, *, messages, recorded):
        self.runtime = runtime
        self.messages = messages
        self._recorded = recorded

    def override(self, *, messages):
        self._recorded.append(messages)
        return _ModelRequest(self.runtime, messages=messages, recorded=self._recorded)


def _app_config(**verification) -> AppConfig:
    return AppConfig(
        sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
        verification=VerificationConfig(**verification),
    )


def _failed_then_terminal() -> dict:
    """A turn whose last action failed, followed by a claim of success."""
    return {
        "messages": [
            HumanMessage(content="Fix the failing auth test and make sure it passes"),
            AIMessage(content="Running the suite.", tool_calls=[{"name": "bash", "args": {}, "id": "c1"}]),
            ToolMessage(content="1 failed, 4 passed", name="bash", tool_call_id="c1", status="error"),
            AIMessage(content="Done - the auth test is fixed and passing!", id="final"),
        ]
    }


def _failed_then_recovered() -> dict:
    """The same failure, followed by a real recovery step."""
    return {
        "messages": [
            HumanMessage(content="Fix the failing auth test and make sure it passes"),
            ToolMessage(content="1 failed, 4 passed", name="bash", tool_call_id="c1", status="error"),
            ToolMessage(content="5 passed", name="bash", tool_call_id="c2"),
            AIMessage(content="Done - the auth test is fixed and passing!", id="final"),
        ]
    }


def test_critic_pipeline_is_built_from_config():
    from alpha.agents.lead_agent.agent import _build_completion_critic_pipeline

    pipeline = _build_completion_critic_pipeline(_app_config())
    assert pipeline is not None
    assert [type(critic).__name__ for critic in pipeline.critics] == ["AgentFinishedCritic"]

    with_patch = _build_completion_critic_pipeline(_app_config(completion_critics_require_patch=True))
    assert [type(critic).__name__ for critic in with_patch.critics] == [
        "AgentFinishedCritic",
        "EmptyPatchCritic",
    ]

    assert _build_completion_critic_pipeline(_app_config(completion_critics_enabled=False)) is None


def test_disabled_critics_leave_the_terminal_message_alone():
    middleware = FinishFirstVerifierMiddleware(critic_pipeline=None)

    assert middleware.after_model(_failed_then_terminal(), _Runtime(thread_id="t", run_id="r")) is None


def test_rejected_claim_is_withdrawn_and_retried_once():
    middleware = FinishFirstVerifierMiddleware(critic_pipeline=CriticPipeline([AgentFinishedCritic()]))
    runtime = _Runtime(thread_id="t", run_id="r")

    update = middleware.after_model(_failed_then_terminal(), runtime)

    assert update is not None
    assert update["jump_to"] == "model"
    assert [type(m).__name__ for m in update["messages"]] == ["RemoveMessage"]
    assert update["messages"][0] == RemoveMessage(id="final")

    # The retry is budgeted once per run, so the second terminal message stands
    # even though the critic would still reject it.
    assert middleware.after_model(_failed_then_terminal(), runtime) is None


def test_recovered_turn_is_accepted():
    middleware = FinishFirstVerifierMiddleware(critic_pipeline=CriticPipeline([AgentFinishedCritic()]))

    assert middleware.after_model(_failed_then_recovered(), _Runtime(thread_id="t", run_id="r")) is None


def test_rejection_prompt_reaches_the_next_model_call_once():
    middleware = FinishFirstVerifierMiddleware(critic_pipeline=CriticPipeline([AgentFinishedCritic()]))
    runtime = _Runtime(thread_id="t", run_id="r")
    middleware.after_model(_failed_then_terminal(), runtime)

    calls: list[list] = []

    def _request() -> _ModelRequest:
        return _ModelRequest(
            runtime,
            messages=[HumanMessage(content="Fix the failing auth test")],
            recorded=calls,
        )

    first = middleware.wrap_model_call(_request(), lambda request: request)

    assert len(calls) == 1
    injected = first.messages[-1]
    assert injected.name == "completion_critic_rejection"
    assert injected.additional_kwargs["hide_from_ui"] is True
    assert "diagnose the error" in injected.content

    # Consumed, not repeated: the next model call is untouched.
    second = middleware.wrap_model_call(_request(), lambda request: request)

    assert len(calls) == 1
    assert len(second.messages) == 1


def test_a_raising_critic_does_not_break_the_run():
    class ExplodingCritic(AgentFinishedCritic):
        def evaluate(self, *args, **kwargs):
            raise RuntimeError("critic blew up")

    middleware = FinishFirstVerifierMiddleware(critic_pipeline=CriticPipeline([ExplodingCritic()]))

    assert middleware.after_model(_failed_then_terminal(), _Runtime(thread_id="t", run_id="r")) is None


def test_nonzero_exit_code_in_a_json_result_is_seen():
    """A shell tool reports the code in its payload, not in ``ToolMessage.status``."""
    state = {
        "messages": [
            HumanMessage(content="Fix the failing auth test and make sure it passes"),
            ToolMessage(content=json.dumps({"exit_code": 1, "stdout": "1 failed"}), name="bash", tool_call_id="c1"),
            AIMessage(content="Done - the auth test is fixed and passing!", id="final"),
        ]
    }
    middleware = FinishFirstVerifierMiddleware(critic_pipeline=CriticPipeline([AgentFinishedCritic()]))

    update = middleware.after_model(state, _Runtime(thread_id="t", run_id="r"))

    assert update is not None
    assert update["jump_to"] == "model"


def test_finish_first_notice_still_fires_when_the_critic_approves():
    """The pre-existing evidence notice must survive the new critic path."""
    state = {
        "messages": [
            HumanMessage(content="Refactor the auth handler"),
            ToolMessage(content="File updated", name="write_file", tool_call_id="c1"),
            AIMessage(content="I have refactored the auth handler successfully!", id="final"),
        ]
    }
    middleware = FinishFirstVerifierMiddleware(critic_pipeline=CriticPipeline([AgentFinishedCritic()]))

    update = middleware.after_model(state, _Runtime(thread_id="t", run_id="r"))

    assert update is not None
    assert "[Finish-First Notice]" in update["messages"][0].content


def test_empty_patch_critic_is_consulted_when_enabled():
    """Opt-in, and it must actually be reached through the pipeline."""
    critic = EmptyPatchCritic(force_check=True)

    assert critic.requires_patch("anything") is True

    result = CriticPipeline([critic]).evaluate(
        task_description="anything",
        execution_history=[],
        workspace_dir=None,
    )

    assert result.critic_name == "CriticPipeline"
    assert result.verdict.value in {"approved", "rejected", "warning"}
