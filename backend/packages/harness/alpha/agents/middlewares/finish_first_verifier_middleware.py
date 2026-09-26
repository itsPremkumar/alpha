"""Finish-First Evidence Verification Middleware.

Synthesized from Claude Fable 5.1 & NVIDIA AVO:
Guarantees that when code modifications occur, the agent either verifies them
empirically via test suites or explicitly reports the verification status.

This is also where the completion critics (``alpha.critic``) are wired into the
agent. That package shipped complete -- ``CriticPipeline``,
``AgentFinishedCritic``, ``EmptyPatchCritic``, ``RubricEvaluator`` -- with its
own test suite and *no caller*, so a terminal message was never checked against
the turn's own tool history: an agent whose last action failed could declare
success and the run would end. A terminal message is the one claim the agent
makes about the whole turn, so it is exactly the claim worth verifying in code
rather than asking for in a rubric.

On rejection the terminal message is withdrawn and the agent is sent back with
the critic's ``diagnostic_prompt`` (same mechanism as
``TerminalResponseMiddleware``: ``RemoveMessage`` + ``jump_to: "model"`` plus a
hidden reminder injected into the next model call). The retry is budgeted once
per run so a critic can never create a loop.

Two paths must never turn a failure into a certified success:

* the execution history handed to the critic reports each tool result's *real*
  status. ``ToolMessage.status`` is optimistic by construction -- a failure
  carried inside a ``Command`` wrapper keeps LangChain's ``"success"`` default
  and the authoritative verdict lives in the normalized result meta / tool
  receipt -- so reading ``getattr(message, "status", "success")`` certified
  failed commands as successful. An absent, blank or unrecognized status now
  resolves to ``"unknown"``, never to ``"success"``.
* a critic that raises cannot vouch for the claim it was asked to check, so it
  withholds approval instead of approving by accident. The guard is still
  budgeted like any other rejection, so a broken verifier costs at most one
  extra model turn and can never take a run down.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.runtime import Runtime

from alpha.agents.middlewares._bounded_dict import BoundedDict
from alpha.agents.middlewares.tool_receipt import TOOL_RECEIPT_KEY
from alpha.agents.middlewares.tool_result_meta import _SUBAGENT_FAILURE_STATUSES, TOOL_META_KEY
from alpha.critic import CriticPipeline

logger = logging.getLogger(__name__)


def _record_finish_first_evidence(message: object, *, code_writes: int) -> None:
    """P3: record the real Finish-First violation as observation evidence.

    The notice itself is the measured event (code writes with no verification
    step), so it is written to the previously consumer-less EvidenceStore with
    its real counts. Fail-safe by design: an evidence-store failure is logged
    and the notice is still injected — evidence bookkeeping can never break a
    turn.
    """
    try:
        from alpha.evidence.store import default_evidence_store

        record = default_evidence_store().add_evidence(
            owner_id="runtime",
            kind="observation",
            ref=f"finish-first:{getattr(message, 'id', None) or 'message'}",
            summary=f"Finish-First notice injected: {code_writes} code write(s) without a verification step",
            tags=("finish_first", "unverified_completion"),
        )
        logger.debug("Recorded finish-first evidence %s", record.id)
    except Exception:
        # Logged at warning, not debug: the notice still reaches the model, but
        # the violation is then unrecorded, and a violation nobody can find
        # afterwards is how "unverified completion" quietly becomes normal.
        logger.warning(
            "Finish-first evidence write failed for %s code write(s); the notice is still injected but the violation is unrecorded",
            code_writes,
            exc_info=True,
        )

_WRITE_TOOLS = frozenset({"write_file", "str_replace", "hashline_edit"})
_VERIFY_TOOLS = frozenset({
    "auto_test_and_repair",
    "reproduce_and_verify",
    "run_task_evaluation_benchmark",
    "audit_finish_first_evidence",
})
_TEST_KEYWORDS = ("pytest", "npm test", "pnpm test", "cargo test", "go test", "python -m unittest")

_CRITIC_REJECTION_NAME = "completion_critic_rejection"
_CRITIC_UNAVAILABLE_REJECTION = (
    "Critic rejection: the completion critic could not run, so this terminal claim is unverified. "
    "Re-read this turn's own tool results -- especially the most recent action -- diagnose the error, "
    "and only declare completion once every step has succeeded."
)
_FINISH_FIRST_NOTICE = (
    "\n\n> [!NOTE]\n"
    "> **[Finish-First Notice]**: Code modifications were performed in this session without an automated test verification step. "
    "Run `auto_test_and_repair` to verify tests before deploying."
)


def _content_text(content: Any) -> str:
    """Flatten message content to plain text for the critics' keyword checks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts)
    return ""


def _exit_code(content: Any) -> int | None:
    """Best-effort non-zero exit code from a JSON tool result.

    ``AgentFinishedCritic`` reads ``exit_code`` off each history entry, but a
    tool that shells out reports the code inside a JSON payload rather than in
    ``ToolMessage.status``, so read it from there when it is present.
    """
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("exit_code")
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def _stamp_status(stamp: Any) -> str | None:
    """Read a ``status`` field off a message-carried verdict stamp, if usable."""
    if isinstance(stamp, dict):
        value = stamp.get("status")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


#: Statuses a tool result can carry and have them mean what they say. Anything
#: outside this set is reported verbatim to the critic rather than coerced.
_KNOWN_TOOL_STATUSES = frozenset({"success", "partial_success", "error", "failed"})

#: Statuses that cannot certify a terminal claim: an observed failure, or a
#: status nobody could determine. ``"unknown"`` is deliberately *not* mapped to
#: ``"success"`` -- an absent verdict is unverified, not a passing one.
_UNCERTIFIABLE_TOOL_STATUSES = frozenset({"error", "failed", "unknown"})


def _honest_tool_status(message: ToolMessage) -> str:
    """Resolve a tool result's real status without ever inventing ``success``.

    ``ToolMessage.status`` is optimistic by construction (langchain defaults it
    to ``"success"`` and a ``Command``-wrapped failure keeps that default -- see
    ``test_command_tool_result_semantics``), while the authoritative verdict
    lives in the normalized result meta stamped by
    ``ToolErrorHandlingMiddleware`` / ``normalize_tool_result`` or in the tool
    receipt. Precedence therefore runs: LangChain's own failure marker, then
    the meta stamp, then the receipt, then the message field. A status that is
    absent, ``None``, blank or unrecognized resolves to ``"unknown"``.
    """
    raw_status = getattr(message, "status", None)
    if raw_status == "error":
        return "error"

    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    for stamp_key in (TOOL_META_KEY, TOOL_RECEIPT_KEY):
        stamped = _stamp_status(additional_kwargs.get(stamp_key))
        if stamped is not None:
            return stamped

    # Structured subagent verdicts (subagents/status_contract.py): producers put
    # the truth here while leaving ToolMessage.status at the default. Failures
    # are errors; any other subagent state falls through to the fields below.
    subagent_status = additional_kwargs.get("subagent_status")
    if isinstance(subagent_status, str) and subagent_status.strip() and subagent_status in _SUBAGENT_FAILURE_STATUSES:
        return "error"

    if isinstance(raw_status, str) and raw_status in _KNOWN_TOOL_STATUSES:
        return raw_status
    return "unknown"


def _execution_history(turn_messages: list[Any]) -> list[dict[str, Any]]:
    """Shape the turn's tool results the way ``BaseCritic`` subclasses read them."""
    history: list[dict[str, Any]] = []
    for message in turn_messages:
        if not isinstance(message, ToolMessage):
            continue
        text = _content_text(message.content)
        status = _honest_tool_status(message)
        entry: dict[str, Any] = {
            "tool_name": getattr(message, "name", "") or "",
            "status": status,
            "content": text,
            "exit_code": _exit_code(text),
        }
        if status in {"error", "failed"}:
            entry["error"] = text or "tool call failed"
        history.append(entry)
    return history


class FinishFirstVerifierMiddleware(AgentMiddleware[AgentState]):
    """Middleware that audits the evidence matrix for code modifications."""

    def __init__(self, enabled: bool = True, critic_pipeline: CriticPipeline | None = None) -> None:
        super().__init__()
        self.enabled = enabled
        self.critic_pipeline = critic_pipeline
        self._lock = threading.Lock()
        self._critic_retries: BoundedDict[tuple[str, str], int] = BoundedDict(1000)
        self._pending_prompts: BoundedDict[tuple[str, str], str] = BoundedDict(1000)

    @staticmethod
    def _key(runtime: Runtime) -> tuple[str, str]:
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            thread_id = str(context.get("thread_id") or "unknown-thread")
            run_id = str(context.get("run_id") or context.get("run_attempt_id") or id(runtime))
            return thread_id, run_id
        # Defensive fallback for tests/custom embeddings. Production Gateway runs
        # always provide thread_id and run_id in Runtime.context.
        return "unknown-thread", str(id(runtime))

    def _reset(self, runtime: Runtime) -> None:
        key = self._key(runtime)
        with self._lock:
            self._critic_retries.pop(key, None)
            self._pending_prompts.pop(key, None)

    def _clear_other_runs(self, runtime: Runtime) -> None:
        thread_id, run_id = self._key(runtime)
        with self._lock:
            stale = [key for key in self._critic_retries if key[0] == thread_id and key[1] != run_id]
            for key in stale:
                self._critic_retries.pop(key, None)
                self._pending_prompts.pop(key, None)

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        self._clear_other_runs(runtime)
        self._reset(runtime)
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        self._clear_other_runs(runtime)
        self._reset(runtime)
        return None

    @hook_config(can_jump_to=["model"])
    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Inspect the model response. If terminal, verify evidence integrity."""
        if not self.enabled:
            return None

        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None

        last_ai = messages[-1]
        # If the model is continuing tool execution, do not intercept
        if last_ai.tool_calls or getattr(last_ai, "invalid_tool_calls", None):
            return None

        # Find the latest user message boundary
        latest_user_idx = -1
        for i, m in enumerate(messages):
            if isinstance(m, HumanMessage):
                latest_user_idx = i

        turn_messages = messages[latest_user_idx + 1 :] if latest_user_idx >= 0 else messages

        # Scan for code write operations and verification operations in this turn
        code_write_count = 0
        had_verification = False

        for m in turn_messages:
            if isinstance(m, ToolMessage):
                tool_name = getattr(m, "name", "")
                if tool_name in _WRITE_TOOLS:
                    code_write_count += 1
                elif tool_name in _VERIFY_TOOLS:
                    had_verification = True
                elif tool_name == "bash":
                    content_str = str(m.content).lower()
                    if any(kw in content_str for kw in _TEST_KEYWORDS):
                        had_verification = True

        # Verify the terminal claim against the turn's own tool history first: a
        # rejected claim is withdrawn and retried, so the notice below would be
        # annotating a message that is about to be discarded.
        rejection = self._rejection(messages, turn_messages, runtime, latest_user_idx)
        if rejection is not None:
            return rejection

        # If code was modified and no verification tool was executed, stamp an evidence notice
        if code_write_count and not had_verification:
            content = last_ai.content
            if isinstance(content, str) and "[Finish-First Notice]" not in content:
                updated_ai = AIMessage(
                    content=content + _FINISH_FIRST_NOTICE,
                    id=last_ai.id,
                    additional_kwargs=last_ai.additional_kwargs,
                    response_metadata=last_ai.response_metadata,
                )
                _record_finish_first_evidence(last_ai, code_writes=code_write_count)
                return {"messages": [updated_ai]}

        return None

    @hook_config(can_jump_to=["model"])
    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self.after_model(state, runtime)

    def _rejection(
        self,
        messages: list[Any],
        turn_messages: list[Any],
        runtime: Runtime,
        latest_user_idx: int,
    ) -> dict[str, Any] | None:
        """Return the retry state update when a critic rejects the terminal claim."""
        if self.critic_pipeline is None:
            return None

        last_ai = messages[-1]
        # ``RemoveMessage`` needs the id to withdraw the claim. Without one the
        # retry would re-read the very claim it is meant to discard, so let the
        # completion stand rather than spin.
        if not last_ai.id:
            logger.warning("FinishFirstVerifier: terminal message has no id; skipping critic check")
            return None

        key = self._key(runtime)
        with self._lock:
            attempts = self._critic_retries.get(key, 0)
        if attempts >= 1:
            # One retry per run. A second rejection would mean the agent cannot
            # satisfy the critic, and looping is worse than an unverified finish.
            return None

        task_description = _content_text(messages[latest_user_idx].content) if latest_user_idx >= 0 else ""
        context = getattr(runtime, "context", None)
        workspace_dir = context.get("workspace_dir") if isinstance(context, dict) else None

        history = _execution_history(turn_messages)
        try:
            result = self.critic_pipeline.evaluate(
                task_description=task_description,
                execution_history=history,
                workspace_dir=str(workspace_dir) if workspace_dir else None,
            )
        except Exception:
            # A crashing verifier has not verified anything: it must not
            # approve the claim it failed to check. Withhold approval exactly
            # like a rejection (same one-retry budget), so a broken guard costs
            # one model turn and can never take the run down -- but a failed
            # command still cannot be certified success on its way past.
            logger.exception("FinishFirstVerifier: critic pipeline raised; withholding completion approval")
            return self._reject(last_ai, key=key, attempts=attempts, prompt=_CRITIC_UNAVAILABLE_REJECTION)

        if result.is_rejected:
            logger.warning("FinishFirstVerifier: completion rejected: %s", result.reason)
            return self._reject(last_ai, key=key, attempts=attempts, prompt=result.diagnostic_prompt or result.reason)

        if result.verdict.value == "warning":
            logger.info("FinishFirstVerifier: completion critic warning: %s", result.reason)

        # Backstop: an approved verdict must still rest on an observable
        # outcome. The last tool result of the turn either failed or could not
        # be classified at all -- neither certifies completion, whatever the
        # pipeline concluded (a custom pipeline may not read the history at all).
        if history:
            last_entry = history[-1]
            if last_entry["status"] in _UNCERTIFIABLE_TOOL_STATUSES:
                tool_name = last_entry.get("tool_name") or "unknown tool"
                prompt = (
                    f"Critic rejection: the most recent action ({tool_name}) reported status "
                    f"'{last_entry['status']}', so completion cannot be certified. "
                    "Diagnose the error, repair or re-run that step, and verify it succeeds "
                    "before declaring completion."
                )
                logger.warning(
                    "FinishFirstVerifier: completion not certified; last tool result status is %s (%s)",
                    last_entry["status"],
                    tool_name,
                )
                return self._reject(last_ai, key=key, attempts=attempts, prompt=prompt)

        return None

    def _reject(self, last_ai: Any, *, key: tuple[str, str], attempts: int, prompt: str) -> dict[str, Any]:
        """Withdraw the terminal claim and send the agent back with *prompt*.

        Consumes the per-run retry budget, so every rejection path (critic
        verdict, crashing critic, uncertifiable last action) shares the same
        single-retry ceiling.
        """
        with self._lock:
            self._critic_retries[key] = attempts + 1
            self._pending_prompts[key] = prompt
        return {"messages": [RemoveMessage(id=last_ai.id)], "jump_to": "model"}

    def _augment_request(self, request: ModelRequest) -> ModelRequest:
        key = self._key(request.runtime)
        with self._lock:
            prompt = self._pending_prompts.pop(key, None)
        if not prompt:
            return request
        reminder = HumanMessage(
            content=f"<system_reminder>\n{prompt}\n</system_reminder>",
            name=_CRITIC_REJECTION_NAME,
            additional_kwargs={"hide_from_ui": True},
        )
        return request.override(messages=[*request.messages, reminder])

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._augment_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._augment_request(request))
