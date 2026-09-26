"""The completion critics are wired into the terminal-claim path.

``alpha.critic`` was a complete, fully tested package with no caller: a terminal
message was never checked against the turn's own tool history, so an agent whose
last action failed could declare success and the run would end. These tests pin
the wiring, the retry, and the two ways a failure must never become a certified
success -- a tool result whose real status is hidden behind LangChain's
optimistic ``"success"`` default, and a critic that raises instead of ruling.
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from alpha.agents.middlewares.finish_first_verifier_middleware import (
    FinishFirstVerifierMiddleware,
    _execution_history,
    _honest_tool_status,
)
from alpha.agents.middlewares.tool_receipt import TOOL_RECEIPT_KEY
from alpha.agents.middlewares.tool_result_meta import TOOL_META_KEY
from alpha.config.app_config import AppConfig
from alpha.config.sandbox_config import SandboxConfig
from alpha.config.verification_config import VerificationConfig
from alpha.critic import AgentFinishedCritic, CriticPipeline
from alpha.critic.base import BaseCritic, CriticResult, CriticVerdict
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


class _ExplodingCritic(BaseCritic):
    """A verifier that crashes instead of ruling -- it verified nothing."""

    name = "exploding_critic"

    def evaluate(self, task_description, execution_history=None, workspace_dir=None, **kwargs):
        raise RuntimeError("critic blew up")


class _AlwaysApprovesCritic(BaseCritic):
    """A verifier that approves unconditionally, to isolate the backstop."""

    name = "always_approves_critic"

    def evaluate(self, task_description, execution_history=None, workspace_dir=None, **kwargs):
        return CriticResult(
            verdict=CriticVerdict.APPROVED,
            reason="approved without reading the history",
            critic_name=self.name,
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


def test_a_raising_critic_withholds_approval_instead_of_granting_it():
    """A crashing verifier has not verified anything, so it must not approve.

    The old behavior treated the exception as "no objection" and let the claim
    through, which is exactly how a failed command ended up certified successful.
    """
    middleware = FinishFirstVerifierMiddleware(critic_pipeline=CriticPipeline([_ExplodingCritic()]))
    runtime = _Runtime(thread_id="t", run_id="r")

    update = middleware.after_model(_failed_then_terminal(), runtime)

    assert update is not None, "a raising critic must not approve the completion"
    assert update["jump_to"] == "model"
    assert update["messages"] == [RemoveMessage(id="final")]

    # The retry is budgeted like any other rejection, so a broken guard costs
    # exactly one extra model turn and can never take the run down.
    assert middleware.after_model(_failed_then_terminal(), runtime) is None


def test_a_raising_critic_withholds_approval_even_for_an_otherwise_clean_turn():
    """The refusal is about the missing verdict, not about the turn's content."""
    state = {
        "messages": [
            HumanMessage(content="Refactor the auth handler"),
            ToolMessage(content="File updated", name="write_file", tool_call_id="c1"),
            AIMessage(content="I have refactored the auth handler successfully!", id="final"),
        ]
    }
    middleware = FinishFirstVerifierMiddleware(critic_pipeline=CriticPipeline([_ExplodingCritic()]))

    update = middleware.after_model(state, _Runtime(thread_id="t", run_id="r"))

    assert update is not None
    assert update["jump_to"] == "model"
    assert update["messages"] == [RemoveMessage(id="final")]


def test_a_raising_critic_prompt_names_the_missing_verification():
    middleware = FinishFirstVerifierMiddleware(critic_pipeline=CriticPipeline([_ExplodingCritic()]))
    runtime = _Runtime(thread_id="t", run_id="r")
    middleware.after_model(_failed_then_terminal(), runtime)

    calls: list[list] = []
    request = _ModelRequest(runtime, messages=[HumanMessage(content="go")], recorded=calls)
    injected = middleware.wrap_model_call(request, lambda current: current).messages[-1]

    assert injected.name == "completion_critic_rejection"
    assert "could not run" in injected.content
    assert "unverified" in injected.content


# ---------------------------------------------------------------------------
# A failed tool result must never read as "success" to the critic
# ---------------------------------------------------------------------------


def _command_wrapped_failure() -> ToolMessage:
    """A ``Command``-wrapped failure: LangChain's default status says "success".

    This is the exact shape produced when a tool raises and the error-handling
    middleware wraps the result in a ``Command`` (``test_command_tool_result_
    semantics`` pins that ``message.status`` stays ``"success"``). The real
    verdict only exists in the normalized meta / tool receipt.
    """
    message = ToolMessage(
        content="Error: soul content is empty; refusing to create agent",
        name="setup_agent",
        tool_call_id="c1",
    )
    assert message.status == "success", "precondition: langchain defaults to the optimistic status"
    return message.model_copy(
        update={
            "additional_kwargs": {
                TOOL_META_KEY: {
                    "status": "error",
                    "error_type": "ValueError",
                    "recoverable_by_model": True,
                    "recommended_next_action": "continue",
                    "source": "content_analysis",
                },
                TOOL_RECEIPT_KEY: {"status": "error", "tool_name": "setup_agent"},
            }
        }
    )


def test_command_wrapped_failure_is_never_reported_to_the_critic_as_success():
    history = _execution_history([_command_wrapped_failure()])

    assert history[0]["status"] == "error"
    assert history[0]["status"] != "success"
    assert "soul content is empty" in history[0]["error"]


def test_a_command_wrapped_failure_cannot_be_certified_as_a_successful_completion():
    """End-to-end: the failed command is withdrawn, not certified."""
    state = {
        "messages": [
            HumanMessage(content="Create a demo agent with a SOUL.md"),
            _command_wrapped_failure(),
            AIMessage(content="The agent was created successfully!", id="final"),
        ]
    }
    middleware = FinishFirstVerifierMiddleware(critic_pipeline=CriticPipeline([_AlwaysApprovesCritic()]))

    update = middleware.after_model(state, _Runtime(thread_id="t", run_id="r"))

    assert update is not None, "an approving critic must not override an observed failure"
    assert update["jump_to"] == "model"
    assert update["messages"] == [RemoveMessage(id="final")]


def test_a_subagent_failure_status_is_never_reported_as_success():
    message = ToolMessage(
        content="subagent did not finish",
        name="task",
        tool_call_id="c1",
    ).model_copy(update={"additional_kwargs": {"subagent_status": "failed"}})

    assert _honest_tool_status(message) == "error"


def test_a_missing_status_attribute_resolves_to_unknown_and_never_to_success():
    """The literal defect: ``getattr(message, "status", "success")``.

    A message object that carries no ``status`` at all used to hand the critic
    ``"success"`` purely because the field was absent. Absence is not a verdict.
    """
    class StatuslessMessage:
        content = "ok"
        name = "bash"
        additional_kwargs: dict = {}

    assert not hasattr(StatuslessMessage(), "status")
    assert _honest_tool_status(StatuslessMessage()) == "unknown"


def test_an_unreadable_status_resolves_to_unknown_and_never_to_success():
    """Blank, ``None`` and out-of-vocabulary values are unverified, not passing."""
    for blank in (None, "", "   "):
        message = ToolMessage(content="ok", name="bash", tool_call_id="c1")
        object.__setattr__(message, "status", blank)
        assert _honest_tool_status(message) == "unknown"

    # An unrecognized vocabulary entry is reported verbatim, never upgraded.
    message = ToolMessage(content="ok", name="bash", tool_call_id="c1").model_copy(
        update={"additional_kwargs": {TOOL_RECEIPT_KEY: {"status": "probably_fine"}}}
    )
    assert _honest_tool_status(message) == "probably_fine"


def test_an_authoritative_error_stamp_beats_the_optimistic_default_status():
    """``ToolMessage.status`` is populated with ``"success"`` by LangChain itself.

    The message field therefore can never be the tie-breaker: only an explicit
    error marker or a stamped verdict is allowed to contradict the default.
    """
    bare = ToolMessage(content="ok", name="bash", tool_call_id="c1")
    assert bare.status == "success"
    assert _honest_tool_status(bare) == "success"

    assert _honest_tool_status(bare.model_copy(update={"status": "error"})) == "error"

    stamped = bare.model_copy(
        update={"additional_kwargs": {TOOL_META_KEY: {"status": "partial_success"}}}
    )
    assert _honest_tool_status(stamped) == "partial_success"


def test_an_unknown_last_action_cannot_be_certified_even_when_the_critic_approves():
    """No verdict at all is not a passing one."""
    message = ToolMessage(content="wrote the file", name="write_file", tool_call_id="c1")
    object.__setattr__(message, "status", "")
    state = {
        "messages": [
            HumanMessage(content="Refactor the auth handler"),
            message,
            AIMessage(content="I have refactored the auth handler successfully!", id="final"),
        ]
    }
    middleware = FinishFirstVerifierMiddleware(critic_pipeline=CriticPipeline([_AlwaysApprovesCritic()]))

    update = middleware.after_model(state, _Runtime(thread_id="t", run_id="r"))

    assert update is not None
    assert update["jump_to"] == "model"
    assert update["messages"] == [RemoveMessage(id="final")]


def test_a_certifiable_last_action_still_completes_normally():
    """The backstop must not turn every turn into a rejection."""
    state = {
        "messages": [
            HumanMessage(content="Refactor the auth handler"),
            ToolMessage(content="5 passed", name="bash", tool_call_id="c1", status="success"),
            AIMessage(content="Done - the auth handler is refactored.", id="final"),
        ]
    }
    middleware = FinishFirstVerifierMiddleware(critic_pipeline=CriticPipeline([_AlwaysApprovesCritic()]))

    assert middleware.after_model(state, _Runtime(thread_id="t", run_id="r")) is None


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
