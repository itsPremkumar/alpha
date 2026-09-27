"""Real domain executors for the Dynamic Workflow Engine.

This module closes the gap that kept ``acceptance_passed`` permanently false: the
executor registry shipped only ``alpha.local.digest`` (a hash) plus six bounded
local projections, so **no workflow node could ever invoke a model, a tool, or a
subagent**. A workflow could demonstrate scheduling, versioning, and evidence
plumbing, and nothing else.

Each executor here performs genuine work through the real Alpha subsystem and
reports the real measured result. The honesty rules are inherited and extended:

- A missing prerequisite (no ``config.yaml``, no configured model, no such tool)
  is a FAILURE carrying the real reason — never a synthesized success.
- ``tokens_used`` is the provider's reported usage, or ``0`` when no model was
  called. ``0`` is a real number; an invented estimate is not.
- ``evidence`` names what was actually invoked, so a reader can tell a model call
  from a projection.
- Every executor is opt-in. Nothing here is bound by
  :func:`alpha.orchestrator.executors.bind_default_executors`; a host must call
  :func:`bind_domain_executors` (or register individually) because binding a
  model-calling executor by default would silently start spending tokens for any
  workflow whose node merely declared that executor name.

The async problem is real and is handled honestly rather than hidden. The engine's
``node_runner`` seam is synchronous, while ``alpha.tools`` assembly and
``ScriptDispatcher`` are async. :func:`_run_sync` bridges them by executing the
coroutine on a dedicated single worker loop, and **refuses** when it is already
called from a thread with a running event loop — mirroring the refusal
``ExecutorRegistry`` already applies — instead of deadlocking or pretending to
have run.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

from alpha.workflow.models import WorkflowNode, WorkflowRun

# Registry keys for the real executors. Namespaced ``alpha.local.*`` alongside
# the existing projections so a node's ``executor`` field reads consistently.
MODEL_EXECUTOR = "alpha.local.model"
TOOL_EXECUTOR = "alpha.local.tool"
SUBAGENT_EXECUTOR = "alpha.local.subagent"

# Where a MODEL node's answer is published in the run state, so a later node can
# consume it with a normal expression (`state.model_output_text`).
MODEL_OUTPUT_KEY = "model_output_text"
MODEL_OUTPUT_FULL_KEY = "model_output"

# Ceiling on how long the sync bridge waits for one coroutine before declaring the
# bridge itself broken. Distinct from a node's ``timeout_seconds``, which the
# engine already enforces and which is the bound that actually matters.
_BRIDGE_JOIN_SECONDS = 3600.0


class DomainExecutorError(RuntimeError):
    """A domain executor could not perform its work; the reason is real."""


_bridge_lock = threading.Lock()
_bridge_loop: asyncio.AbstractEventLoop | None = None
_bridge_thread: threading.Thread | None = None


def _run_sync(factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Run ``factory()``'s coroutine to completion from a synchronous caller.

    The engine's node seam is synchronous but Alpha's tool assembly and
    ``ScriptDispatcher`` are async, so a private worker loop owns the bridging.
    The loop is process-wide and created lazily; it is never shut down, because a
    half-created loop that a later node tries to reuse is worse than one
    long-lived loop.

    Refuses — loudly — when the calling thread already has a running event loop.
    ``loop.run_until_complete`` on such a thread raises, and blocking that loop to
    "make it work" would deadlock the very runtime executing the workflow. The
    caller receives the real reason and the node fails, which is the contract the
    executor registry already documents for async executors.
    """
    global _bridge_loop, _bridge_thread

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise DomainExecutorError(
            "a domain executor cannot run on a thread with an active event loop; "
            "dispatch this node from the host's worker boundary instead"
        )

    with _bridge_lock:
        loop = _bridge_loop
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, name="dwe-domain-bridge", daemon=True)
            thread.start()
            _bridge_loop = loop
            _bridge_thread = thread

    future = asyncio.run_coroutine_threadsafe(factory(), loop)
    try:
        return future.result(timeout=_BRIDGE_JOIN_SECONDS)
    except TimeoutError as exc:  # pragma: no cover - the loop is process-owned
        future.cancel()
        raise DomainExecutorError(f"domain executor bridge did not return within {_BRIDGE_JOIN_SECONDS:.0f}s") from exc
    except asyncio.CancelledError as exc:
        raise DomainExecutorError("domain executor bridge was cancelled") from exc


def _app_config(node: WorkflowNode) -> Any:
    """Resolve the AppConfig snapshot this node should use.

    A node may pin a specific config path via ``config.config_path``; otherwise
    the process snapshot is used. A missing ``config.yaml`` raises
    ``FileNotFoundError`` from the real loader and is reported as-is — the whole
    point of these executors is that the reason is genuine.
    """
    from alpha.config import get_app_config

    override = node.config.get("config_path")
    if isinstance(override, str) and override.strip():
        from alpha.config import reload_app_config

        return reload_app_config(override.strip())
    return get_app_config()


def _node_prompt(node: WorkflowNode, *, required: bool = True) -> str:
    """The node's instruction text, with an explicit config fallback."""
    prompt = node.prompt if isinstance(node.prompt, str) else ""
    if prompt.strip():
        return prompt
    configured = node.config.get("prompt") or node.config.get("input") or node.config.get("query")
    if isinstance(configured, str) and configured.strip():
        return configured
    if required:
        raise DomainExecutorError(
            f"node '{node.id}' declares no prompt; a model call with no instruction cannot be made up"
        )
    return ""


def _usage_tokens(response: Any) -> int:
    """Extract real token usage from a provider response, or 0 when absent.

    Providers disagree on the field, so several shapes are checked. Returning
    ``0`` for a response that genuinely reported no usage is honest; guessing a
    number from text length is not, so nothing is estimated here.
    """
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict):
        for key in ("total_tokens", "total_token_count"):
            value = usage.get(key)
            if isinstance(value, int) and value >= 0:
                return value
        return 0
    for attr in ("total_tokens",):
        value = getattr(response, attr, None)
        if isinstance(value, int) and value >= 0:
            return value
    metadata = getattr(response, "response_metadata", None)
    if isinstance(metadata, dict):
        token_usage = metadata.get("token_usage")
        if isinstance(token_usage, dict):
            total = token_usage.get("total_tokens")
            if isinstance(total, int) and total >= 0:
                return total
    return 0


def _content_to_text(content: Any) -> str:
    """Flatten a provider's content into plain text without losing the payload.

    Multimodal responses are lists of typed blocks; the text blocks are joined so
    a workflow node's output is usable text rather than an opaque list. No
    content is invented: an empty response yields an empty string, and the caller
    decides whether that is a failure.
    """
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
        return "".join(parts)
    return "" if content is None else str(content)


# --------------------------------------------------------------- model executor


def local_model_executor(node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
    """Invoke a REAL chat model and report its real output and token usage.

    The model is resolved from the node's ``config.model``, else the configured
    default. An unknown name is the factory's own ``ValueError`` and is surfaced
    verbatim rather than silently falling back to a different model — a silent
    fallback would make the run's evidence describe a model that was never asked.

    The answer is published to ``run.state`` under :data:`MODEL_OUTPUT_KEY` (plain
    text) and :data:`MODEL_OUTPUT_FULL_KEY` (the full response dict) so a later
    node can consume it with an ordinary routing expression.
    """
    from alpha.models import create_chat_model

    prompt = _node_prompt(node)
    app_config = _app_config(node)
    requested = node.config.get("model")
    model_name = requested if isinstance(requested, str) and requested.strip() else app_config.default_model_name

    try:
        model = create_chat_model(
            name=model_name,
            thinking_enabled=bool(node.config.get("thinking_enabled", False)),
            app_config=app_config,
            # No outer layer retries this call, so the provider SDK's own retry
            # behaviour stays active.
            retries_orchestrated=False,
        )
    except Exception as exc:  # noqa: BLE001 - the factory's reason is authoritative
        raise DomainExecutorError(f"could not resolve model {model_name!r}: {type(exc).__name__}: {exc}") from exc

    system_prompt = node.config.get("system_prompt")
    messages: list[tuple[str, str]] = []
    if isinstance(system_prompt, str) and system_prompt.strip():
        messages.append(("system", system_prompt))
    messages.append(("human", prompt))

    try:
        response = model.invoke(messages)
    except Exception as exc:  # noqa: BLE001 - a provider failure is real work failing
        raise DomainExecutorError(f"model {model_name!r} call failed: {type(exc).__name__}: {exc}") from exc

    text = _content_to_text(getattr(response, "content", None))
    if not text.strip():
        raise DomainExecutorError(
            f"model {model_name!r} returned an empty response; an empty answer is not a completed task"
        )

    tokens = _usage_tokens(response)
    run.state[MODEL_OUTPUT_KEY] = text
    run.state[MODEL_OUTPUT_FULL_KEY] = {
        "model": model_name,
        "node_id": node.id,
        "chars": len(text),
        "tokens": tokens,
    }
    return {
        "status": "completed",
        "output": {"model": model_name, "text": text, "chars": len(text)},
        "evidence": (
            f"model {model_name!r} returned {len(text)} characters "
            f"({tokens} tokens reported by the provider) for node '{node.id}'"
        ),
        "tokens_used": tokens,
    }


# ---------------------------------------------------------------- tool executor


def _tool_arguments(node: WorkflowNode) -> dict[str, Any]:
    """The arguments for a TOOL node.

    ``config.arguments`` is the explicit mapping. A bare ``config.input`` is
    accepted for single-argument tools, because requiring every author to learn
    the arguments-wrapper for a one-field tool is a needless trap — but the
    mapping is built explicitly either way, never merged from arbitrary node
    fields.
    """
    arguments = node.config.get("arguments")
    if isinstance(arguments, dict):
        return dict(arguments)
    if isinstance(arguments, list):
        raise DomainExecutorError(
            f"tool node '{node.id}' config.arguments must be a mapping of argument name to value, got a list"
        )
    single = node.config.get("input")
    if single is not None:
        return {"input": single}
    return {}


def local_tool_executor(node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
    """Invoke a REAL builtin tool through the real assembly + guardrail path.

    Dispatch deliberately goes through ``ScriptDispatcher``, which is the same
    ``get_available_tools`` list the lead agent and the subagent executor use and
    the same ``GuardrailMiddleware`` decision a model-issued call would get. A
    workflow node therefore cannot reach a tool the agent itself could not, and
    cannot skip a guardrail by going around the registry.

    A tool that needs populated ``runtime.state`` (sandbox paths, thread outputs)
    has none here, because this seam is not inside a LangGraph run. Rather than
    fabricate that context, the tool's own real error is surfaced; tools whose
    behaviour depends only on their arguments work normally.
    """
    tool_name = node.config.get("tool") or node.config.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise DomainExecutorError(f"tool node '{node.id}' declares no tool name in config.tool")
    tool_name = tool_name.strip()
    arguments = _tool_arguments(node)

    allowed = node.config.get("allowed_tools")
    allowed_names = tuple(str(item) for item in allowed) if isinstance(allowed, list) and allowed else (tool_name,)

    async def _dispatch() -> Any:
        from alpha.tools.script_bridge.dispatcher import RuntimeCarrier, ScriptDispatcher
        from alpha.tools.script_bridge.policy import ScriptBridgePolicy

        app_config = _app_config(node)
        carrier = RuntimeCarrier(
            context={
                "thread_id": str(node.config.get("thread_id") or run.run_id),
                "user_id": run.owner_id,
                "run_id": run.run_id,
            },
            thread_id=str(node.config.get("thread_id") or run.run_id),
            run_id=run.run_id,
            user_id=run.owner_id,
            app_config=app_config,
        )
        import tempfile
        from pathlib import Path

        dispatcher = ScriptDispatcher(
            policy=ScriptBridgePolicy(allowed_tool_names=allowed_names),
            carrier=carrier,
            cache_dir=Path(tempfile.gettempdir()) / "dwe-tool-bridge",
        )
        return await dispatcher.dispatch(tool_name, arguments)

    try:
        result = _run_sync(_dispatch)
    except DomainExecutorError:
        raise
    except Exception as exc:  # noqa: BLE001 - the tool's real failure is authoritative
        raise DomainExecutorError(f"tool {tool_name!r} failed: {type(exc).__name__}: {exc}") from exc

    return {
        "status": "completed",
        "output": {"tool": tool_name, "result": result},
        "evidence": f"tool {tool_name!r} was dispatched with {len(arguments)} argument(s) through the guarded tool bridge",
        # A builtin tool call is not a model call; the provider reported no model
        # usage, so the honest number is 0.
        "tokens_used": 0,
    }


# ------------------------------------------------------------ subagent executor


def local_subagent_executor(node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
    """Delegate to a REAL subagent and adopt its real result.

    Mirrors the construction in ``alpha.tools.builtins.task_tool`` so the
    subagent sees the same tools, guardrails, sandbox policy and capacity
    accounting a model-issued delegation would get. The synchronous
    ``SubagentExecutor.execute`` is used rather than the background
    ``execute_async`` polling loop, because the engine's node seam is synchronous
    and a poll loop here would need its own bounded termination rule.

    ``status`` is mapped from the subagent's own ``SubagentStatus``; a subagent
    that did not succeed fails this node with its real stop reason rather than
    contributing empty output to a run that would then report success.
    """
    from alpha.subagents import SubagentExecutor, get_subagent_config
    from alpha.subagents.runtime import SubagentRuntime
    from alpha.tools import get_available_tools
    from alpha.utils.assembly_io import run_assembly

    agent_name = node.config.get("subagent") or node.config.get("agent")
    if not isinstance(agent_name, str) or not agent_name.strip():
        raise DomainExecutorError(f"subagent node '{node.id}' declares no subagent name in config.subagent")
    agent_name = agent_name.strip()
    task = _node_prompt(node)

    app_config = _app_config(node)
    subagent_config = get_subagent_config(agent_name, app_config=app_config)
    if subagent_config is None:
        available = sorted(node.config.get("_known_subagents") or [])
        raise DomainExecutorError(f"unknown subagent {agent_name!r}" + (f"; configured: {available}" if available else ""))

    tools = _run_sync(
        lambda: run_assembly(
            get_available_tools,
            model_name=node.config.get("model") or app_config.default_model_name,
            subagent_enabled=False,  # no nested delegation out of a workflow node
            app_config=app_config,
        )
    )
    if not tools:
        raise DomainExecutorError(
            f"subagent {agent_name!r} was resolved but no tools were available to it; refusing to run an agent that cannot act"
        )

    runtime = SubagentRuntime.from_app_config(app_config)
    executor = SubagentExecutor(
        config=subagent_config,
        tools=tools,
        app_config=app_config,
        parent_model=node.config.get("model") or app_config.default_model_name,
        thread_id=str(node.config.get("thread_id") or run.run_id),
        user_id=run.owner_id,
        run_id=run.run_id,
        execution_capacity=runtime.execution_capacity,
    )

    try:
        result = executor.execute(task)
    except Exception as exc:  # noqa: BLE001 - a subagent fault is real work failing
        raise DomainExecutorError(f"subagent {agent_name!r} faulted: {type(exc).__name__}: {exc}") from exc

    status = str(getattr(result, "status", "") or "")
    text = getattr(result, "result", None)
    error = getattr(result, "error", None)
    stop_reason = getattr(result, "stop_reason", None)

    if status != "completed" or not isinstance(text, str) or not text.strip():
        detail = error or stop_reason or f"status={status or 'unknown'}"
        raise DomainExecutorError(f"subagent {agent_name!r} did not complete: {detail}")

    tokens = 0
    for record in getattr(result, "token_usage_records", None) or []:
        total = getattr(record, "total_tokens", None)
        if isinstance(total, int) and total > 0:
            tokens += total

    return {
        "status": "completed",
        "output": {"subagent": agent_name, "text": text, "external_task_id": getattr(result, "external_task_id", None)},
        "evidence": (
            f"subagent {agent_name!r} completed task '{task[:120]}' returning {len(text)} characters "
            f"({tokens} tokens across {len(getattr(result, 'tool_receipts', None) or [])} tool receipt(s))"
        ),
        "tokens_used": tokens,
    }


DOMAIN_EXECUTORS: dict[str, Callable[[WorkflowNode, WorkflowRun], dict[str, Any]]] = {
    MODEL_EXECUTOR: local_model_executor,
    TOOL_EXECUTOR: local_tool_executor,
    SUBAGENT_EXECUTOR: local_subagent_executor,
}


def bind_domain_executors(registry: Any | None = None) -> Any:
    """Bind the real domain executors. Explicit opt-in, never automatic.

    Deliberately NOT called by ``bind_default_executors``: these executors spend
    money and reach the network, so a workflow that merely names one must not
    start calling models because a module happened to be imported.
    """
    if registry is None:
        from alpha.orchestrator.executors import get_executor_registry

        registry = get_executor_registry()
    for name, executor in DOMAIN_EXECUTORS.items():
        registry.register(name, executor)
    return registry


def unbind_domain_executors(registry: Any | None = None) -> list[str]:
    """Remove the domain executors again, returning the names actually removed."""
    if registry is None:
        from alpha.orchestrator.executors import get_executor_registry

        registry = get_executor_registry()
    return [name for name in DOMAIN_EXECUTORS if registry.unregister(name)]


__all__ = [
    "DOMAIN_EXECUTORS",
    "MODEL_EXECUTOR",
    "MODEL_OUTPUT_FULL_KEY",
    "MODEL_OUTPUT_KEY",
    "SUBAGENT_EXECUTOR",
    "TOOL_EXECUTOR",
    "DomainExecutorError",
    "bind_domain_executors",
    "local_model_executor",
    "local_subagent_executor",
    "local_tool_executor",
    "unbind_domain_executors",
]
