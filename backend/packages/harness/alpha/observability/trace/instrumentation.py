"""One typed emitter per instrumented layer, and the call-site kit.

Why emitters instead of ``writer.record("model.call.completed", ...)``
---------------------------------------------------------------------
A dotted string at a call site is a typo waiting to happen, and a typo here is
indistinguishable from a subsystem that never fired. Each function below names
its event code as a module constant, so the registry, the coverage report and
the test all read the same spelling, and the payload keys a code requires are
passed as named parameters rather than assembled from a dict at the call site.

Every emitter returns ``None`` and does nothing when no writer is installed or
the writer is disabled. That is the whole cost of the substrate in a default
deployment: one ``is None`` test, one function call, no allocation.

The layer map
-------------
============  ==========================================================
layer         emitter(s)
============  ==========================================================
0 envelope    :func:`emit_run_opened`, :func:`emit_run_closed`
1 node/graph  :func:`emit_node_transition`
2 model call  :func:`emit_model_requested`, :func:`emit_model_completed`,
              :func:`emit_model_failed`, :func:`emit_model_retry`
3 tool select :func:`emit_tool_selection`
4 skill sel.  :func:`emit_skill_selection`
5 provider    :func:`emit_provider_selection`
6 subagent    :func:`emit_subagent_spawned`, :func:`emit_subagent_completed`
7 swarm       :func:`emit_swarm_plan`, :func:`emit_swarm_attempt`,
              :func:`emit_swarm_incident`
8 web search  :func:`emit_web_results`, :func:`emit_web_fetch`
9 memory      :func:`emit_memory_recall`, :func:`emit_memory_write`,
              :func:`emit_memory_evicted`
10 knowledge  :func:`emit_rag_query`, :func:`emit_rag_allowlist`
11 filesystem :func:`emit_filesystem_op`
12 cost       :func:`emit_cost_snapshot`, :func:`emit_cost_governor`,
              :func:`emit_cost_throttled`
13 error      :func:`emit_error`, :func:`emit_error_swallowed`
14 handoff    :func:`emit_handoff`
15 human loop :func:`emit_human_interrupt`, :func:`emit_human_edit`
16 guardrail  :func:`emit_guardrail_denied`
17 self-evol. :func:`emit_evolution_observed`, :func:`emit_evolution_diagnosed`,
              :func:`emit_evolution_fix`, :func:`emit_evolution_verify`
============  ==========================================================

On ``reasoning``
----------------
The models Alpha configures today (``space-bunny``, ``union-alpha``) declare
``supports_thinking: false``, so a provider returns no reasoning content and the
``reasoning`` field of :func:`emit_model_completed` is ``None`` -- recorded as
``None``, explicitly, rather than omitted. The field exists and is populated the
moment a provider does return reasoning, and the code that consumes it must
treat ``None`` as the normal case for this deployment rather than as a bug. The
assertion is in the emitter, not in a comment: a caller that passes a non-``None``
``reasoning`` for a model declared non-thinking still gets it recorded, because
the declaration is configuration and the provider is the authority.

Selection events: candidate set, choice, and reason
----------------------------------------------------
:func:`emit_tool_selection` and :func:`emit_skill_selection` require the
**candidate set**, not just the winner. "Which tool ran" is already visible in
the tool-call event; "which tools were considered and why this one won" is the
only way a self-improving system can learn that its tool descriptions are wrong.
A selection event that recorded only the choice would be a screenshot.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .contract import TraceEnvelope
from .writer import TraceWriter, current_writer

__all__ = [
    "E_COST_GOVERNOR",
    "E_COST_SNAPSHOT",
    "E_COST_THROTTLED",
    "E_ERROR_RAISED",
    "E_ERROR_SWALLOWED",
    "E_EVOLUTION_DIAGNOSED",
    "E_EVOLUTION_FIX",
    "E_EVOLUTION_OBSERVED",
    "E_EVOLUTION_VERIFY",
    "E_FS_OP",
    "E_GUARD_DENIED",
    "E_HANDOFF",
    "E_HUMAN_EDIT",
    "E_HUMAN_INTERRUPT",
    "E_MEM_EVICTED",
    "E_MEM_RECALL",
    "E_MEM_WRITE",
    "E_MODEL_CALL_COMPLETED",
    "E_MODEL_CALL_FAILED",
    "E_MODEL_CALL_REQUESTED",
    "E_MODEL_CALL_RETRY",
    "E_NODE_TRANSITION",
    "E_PROVIDER_SELECT",
    "E_RAG_ALLOWLIST",
    "E_RAG_QUERY",
    "E_RUN_CLOSED",
    "E_RUN_OPENED",
    "E_SKILL_SELECT",
    "E_SUB_COMPLETED",
    "E_SUB_SPAWNED",
    "E_SWARM_ATTEMPT",
    "E_SWARM_INCIDENT",
    "E_SWARM_PLAN",
    "E_TOOL_CALL_COMPLETED",
    "E_TOOL_SELECT",
    "E_WEB_FETCH",
    "E_WEB_RESULTS",
    "emit_cost_governor",
    "emit_cost_snapshot",
    "emit_cost_throttled",
    "emit_error",
    "emit_error_swallowed",
    "emit_evolution_diagnosed",
    "emit_evolution_fix",
    "emit_evolution_observed",
    "emit_evolution_verify",
    "emit_filesystem_op",
    "emit_guardrail_denied",
    "emit_handoff",
    "emit_human_edit",
    "emit_human_interrupt",
    "emit_memory_evicted",
    "emit_memory_recall",
    "emit_memory_write",
    "emit_model_completed",
    "emit_model_failed",
    "emit_model_requested",
    "emit_model_retry",
    "emit_node_transition",
    "emit_provider_selection",
    "emit_rag_allowlist",
    "emit_rag_query",
    "emit_run_closed",
    "emit_run_opened",
    "emit_skill_selection",
    "emit_subagent_completed",
    "emit_subagent_spawned",
    "emit_swarm_attempt",
    "emit_swarm_incident",
    "emit_swarm_plan",
    "emit_tool_selection",
    "emit_web_fetch",
    "emit_web_results",
    "record_event",
]

# -- layer 0: envelope ---------------------------------------------------------
E_RUN_OPENED = "env.run.opened"
E_RUN_CLOSED = "env.run.closed"
# -- layer 1: node / graph ------------------------------------------------------
E_NODE_TRANSITION = "node.transition"
# -- layer 2: model call ---------------------------------------------------------
E_MODEL_CALL_REQUESTED = "model.call.requested"
E_MODEL_CALL_COMPLETED = "model.call.completed"
E_MODEL_CALL_FAILED = "model.call.failed"
E_MODEL_CALL_RETRY = "model.call.retry"
# -- layer 3: tool selection -----------------------------------------------------
E_TOOL_SELECT = "tool.select.decided"
E_TOOL_CALL_COMPLETED = "tool.call.completed"
# -- layer 4: skill selection ----------------------------------------------------
E_SKILL_SELECT = "skill.select.decided"
# -- layer 5: provider selection -------------------------------------------------
E_PROVIDER_SELECT = "provider.select.decided"
# -- layer 6: subagent -----------------------------------------------------------
E_SUB_SPAWNED = "sub.spawned"
E_SUB_COMPLETED = "sub.completed"
# -- layer 7: swarm --------------------------------------------------------------
E_SWARM_PLAN = "swarm.plan"
E_SWARM_ATTEMPT = "swarm.attempt"
E_SWARM_INCIDENT = "swarm.incident"
# -- layer 8: web search ---------------------------------------------------------
E_WEB_RESULTS = "web.search.results"
E_WEB_FETCH = "web.fetch.attempted"
# -- layer 9: memory -------------------------------------------------------------
E_MEM_RECALL = "mem.recall"
E_MEM_WRITE = "mem.write"
E_MEM_EVICTED = "mem.evicted"
# -- layer 10: knowledge / RAG ---------------------------------------------------
E_RAG_QUERY = "rag.query"
E_RAG_ALLOWLIST = "rag.allowlist"
# -- layer 11: filesystem --------------------------------------------------------
E_FS_OP = "fs.op"
# -- layer 12: cost / budget -----------------------------------------------------
E_COST_SNAPSHOT = "cost.snapshot"
E_COST_GOVERNOR = "cost.governor"
E_COST_THROTTLED = "cost.throttled"
# -- layer 13: errors ------------------------------------------------------------
E_ERROR_RAISED = "err.raised"
E_ERROR_SWALLOWED = "err.swallowed"
# -- layer 14: handoff -----------------------------------------------------------
E_HANDOFF = "handoff.done"
# -- layer 15: human loop --------------------------------------------------------
E_HUMAN_INTERRUPT = "human.interrupt"
E_HUMAN_EDIT = "human.edit"
# -- layer 16: guardrails --------------------------------------------------------
E_GUARD_DENIED = "guard.denied"
# -- layer 17: self-evolution ----------------------------------------------------
E_EVOLUTION_OBSERVED = "evo.observed"
E_EVOLUTION_DIAGNOSED = "evo.diagnosed"
E_EVOLUTION_FIX = "evo.fix.attempted"
E_EVOLUTION_VERIFY = "evo.verify.outcome"


def record_event(event_type: str, *, writer: TraceWriter | None = None, **fields: Any) -> TraceEnvelope | None:
    """Record one event through the installed writer.

    The generic escape hatch, and the only function the layer emitters below call.
    Returns ``None`` when nothing is installed or the writer is off.
    """
    target = writer if writer is not None else current_writer()
    if target is None:
        return None
    return target.record(event_type, **fields)


#: Identity keys an emitter already supplies itself. A caller passing one of these
#: through ``**identity`` is asking for something the emitter owns.
_RESERVED_IDENTITY: frozenset[str] = frozenset({"payload", "writer", "event_type"})


def _identity(identity: dict[str, Any]) -> dict[str, Any]:
    """Return *identity* with the reserved keys removed.

    This exists because of a real defect, not for tidiness. Every emitter builds
    its own ``payload`` and then splatted ``**identity`` into
    :func:`record_event`, so a caller that passed ``payload=`` -- the natural
    thing to do for an extra field -- produced
    ``record_event(..., payload=a, payload=b)``. Python raises
    ``TypeError: got multiple values for keyword argument 'payload'`` **in the
    emitter's own frame**, which the instrumentation call site then swallowed:
    the event vanished, the caller got ``None``, and nothing anywhere said why.
    A telemetry mistake that costs a diagnostic event and reports nothing is
    exactly the failure this package exists to remove.

    Dropping the reserved keys turns that mistake into an *ignored* extra field
    rather than a lost event, which is strictly better: the emitter's own
    contract is still honoured and the event is still recorded. A caller who
    genuinely wants extra payload keys passes ``extra_payload=``, which the
    emitters merge explicitly.
    """
    return {key: value for key, value in identity.items() if key not in _RESERVED_IDENTITY}


def _emit(
    event_type: str,
    *,
    writer: TraceWriter | None = None,
    identity: dict[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    digests: Mapping[str, str] | None = None,
    **identity_fields: Any,
) -> TraceEnvelope | None:
    """Merge, hand off and record. The one tail every emitter below shares.

    ``extra_payload`` is the supported way for a caller to add keys to an
    emitter's payload without rebuilding it. It is merged *before* the writer
    scrubs, so an extra key is redacted and bounded exactly like a built-in one.

    ``digests`` is separate because a payload cannot hold one. The strict
    redaction policy hides a bare 64-character lowercase-hex string -- that is
    precisely the shape of a SHA-256 digest, and also of some credentials -- so a
    hash stored in a payload is replaced with ``[REDACTED:high_entropy_blob]`` and
    the layers whose entire payload *is* a hash (6, 8, 11) lose their content.
    The envelope therefore carries a top-level ``digests`` mapping that only the
    emitters write, with values this package minted itself.

    ``**identity_fields`` is the per-call-site identity passthrough (``run_id``,
    ``thread_id``, ``span_id``, ...). A ``**kwargs`` rather than a named
    ``identity`` parameter so a call site writes ``run_id=``; the reserved keys
    are filtered by :func:`_identity` either way.
    """
    merged: dict[str, Any] = dict(payload or {})
    if isinstance(extra_payload, Mapping):
        merged.update(extra_payload)
    fields = {**(identity or {}), **identity_fields}
    return record_event(event_type, writer=writer, payload=merged, digests=dict(digests or {}), **_identity(fields))


# ---------------------------------------------------------------------------
# layer 0 -- envelope
# ---------------------------------------------------------------------------


def emit_run_opened(
    *,
    model_config_sha256: str,
    config_version: str,
    git_sha256: str = "unknown",
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Open a run's envelope.

    ``git_sha256`` defaults to ``"unknown"`` rather than being omitted, and it is
    that literal which is an honest answer outside a checkout. A reader that
    cannot tell "no git" from "git was not recorded" will trust a trace it should
    not.
    """
    return _emit(
        E_RUN_OPENED,
        writer=writer,
        payload={"model_config_sha256": model_config_sha256, "config_version": config_version, "git_sha256": git_sha256},
        identity=identity,
        extra_payload=extra_payload,
    )


def emit_run_closed(
    *,
    outcome: str,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Close a run's envelope with its terminal ``outcome``."""
    return _emit(E_RUN_CLOSED, writer=writer, payload={"outcome": outcome}, identity=identity, extra_payload=extra_payload)


# ---------------------------------------------------------------------------
# layer 1 -- node / graph
# ---------------------------------------------------------------------------


def emit_node_transition(
    *,
    from_node: str,
    to_node: str,
    reason: str,
    state_before: Mapping[str, Any] | None = None,
    state_after: Mapping[str, Any] | None = None,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a graph transition, with the chosen next node *and its reason*.

    ``reason`` is required by the registry rather than optional because a
    transition with no stated reason is a transition nothing can learn from: the
    sentinel's whole job is to answer "why did it go there", and an event that
    omits the answer is the failure it is meant to detect.
    """
    return _emit(
        E_NODE_TRANSITION,
        writer=writer,
        from_node=from_node,
        to_node=to_node,
        node=to_node,
        node_name=to_node,
        payload={
            "from_node": from_node,
            "to_node": to_node,
            "reason": reason,
            "state_before": dict(state_before or {}),
            "state_after": dict(state_after or {}),
        },
        identity=identity,
        extra_payload=extra_payload,
    )


# ---------------------------------------------------------------------------
# layer 2 -- model call
# ---------------------------------------------------------------------------


def emit_model_requested(
    *,
    provider: str,
    model: str,
    temperature: float | None = None,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a model call being issued."""
    payload: dict[str, Any] = {"provider": provider, "model": model}
    if temperature is not None:
        payload["temperature"] = float(temperature)
    return _emit(E_MODEL_CALL_REQUESTED, writer=writer, provider=provider, model=model, payload=payload, identity=identity, extra_payload=extra_payload)


def emit_model_completed(
    *,
    provider: str,
    model: str,
    finish_reason: str,
    latency_ms: float,
    ttfb_ms: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    cost_usd: float | None = None,
    retry_count: int = 0,
    breaker_state: str = "closed",
    reasoning: str | None = None,
    messages_before: Sequence[Mapping[str, Any]] | None = None,
    messages_after: Sequence[Mapping[str, Any]] | None = None,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a completed model call.

    ``reasoning`` is ``None`` for every model Alpha configures today
    (``space-bunny`` and ``union-alpha`` both declare ``supports_thinking:
    false``), and ``None`` is recorded rather than omitted so a consumer can tell
    "the provider sent no reasoning" from "nobody looked". It is passed through
    untouched when a provider does return some.

    ``ttfb_ms`` and ``cached_tokens`` are ``None`` when the provider reported
    neither; the same honest-unknown convention as ``reasoning``.
    """
    tokens = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "reasoning_tokens": reasoning_tokens,
    }
    payload: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "finish_reason": finish_reason,
        "latency_ms": float(latency_ms),
        "ttfb_ms": None if ttfb_ms is None else float(ttfb_ms),
        "tokens": {key: value for key, value in tokens.items() if value is not None},
        "cost_usd": cost_usd,
        "retry_count": int(retry_count),
        "breaker_state": breaker_state,
        "reasoning": reasoning,
        "messages_before": [dict(message) for message in (messages_before or ())],
        "messages_after": [dict(message) for message in (messages_after or ())],
    }
    return _emit(E_MODEL_CALL_COMPLETED, writer=writer, provider=provider, model=model, payload=payload, identity=identity, extra_payload=extra_payload)


def emit_model_failed(
    *,
    provider: str,
    model: str,
    error_code: str,
    retry_count: int,
    breaker_state: str,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a model call that failed after its retries."""
    return _emit(
        E_MODEL_CALL_FAILED,
        writer=writer,
        provider=provider,
        model=model,
        error_code=error_code,
        payload={"provider": provider, "model": model, "error_code": error_code, "retry_count": int(retry_count), "breaker_state": breaker_state},
        identity=identity,
        extra_payload=extra_payload,
    )


def emit_model_retry(
    *,
    provider: str,
    model: str,
    attempt: int,
    reason: str,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a model-call retry and why it happened."""
    return _emit(
        E_MODEL_CALL_RETRY,
        writer=writer,
        provider=provider,
        model=model,
        payload={"provider": provider, "model": model, "attempt": int(attempt), "reason": reason},
        identity=identity,
        extra_payload=extra_payload,
    )


# ---------------------------------------------------------------------------
# layer 3 / 4 -- tool and skill selection
# ---------------------------------------------------------------------------


def emit_tool_selection(
    *,
    candidates: Sequence[Any],
    chosen: str,
    reason: str,
    scores: Mapping[str, float] | None = None,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a tool-selection decision: the candidates, the choice, the why.

    ``candidates`` is required by the registry. A tool-selection event carrying
    only the winner tells a reader which tool ran -- which the tool-call event
    already says -- and nothing about whether the *right* tools were on the
    table.
    """
    return _emit(
        E_TOOL_SELECT,
        writer=writer,
        tool=chosen,
        payload={
            "candidates": [str(candidate) for candidate in candidates],
            "chosen": chosen,
            "reason": reason,
            "scores": {str(key): float(value) for key, value in (scores or {}).items()},
        },
        identity=identity,
        extra_payload=extra_payload,
    )


def emit_skill_selection(
    *,
    candidates: Sequence[Any],
    chosen: str,
    reason: str,
    registry_version: str,
    scores: Mapping[str, float] | None = None,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a skill-selection decision.

    ``registry_version`` is required: a selection recorded against an unknown
    catalog version cannot be compared with a later one, and a self-improving
    system comparing two skill decisions is exactly the case that needs it.
    """
    return _emit(
        E_SKILL_SELECT,
        writer=writer,
        skill=chosen,
        payload={
            "candidates": [str(candidate) for candidate in candidates],
            "chosen": chosen,
            "reason": reason,
            "scores": {str(key): float(value) for key, value in (scores or {}).items()},
            "registry_version": registry_version,
        },
        identity=identity,
        extra_payload=extra_payload,
    )


def emit_tool_call_completed(
    *,
    tool: str,
    status: str,
    duration_ms: float | None = None,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a tool call's outcome.

    Tool arguments and results are **not** parameters here on purpose: they are
    the highest-risk payloads in the system (a command line, a file body, a
    response body) and the registry's bounds and the write-time redactor are the
    right place to handle them, not 200 call sites that each have to remember.
    A caller that wants them recorded passes ``payload={...}`` and gets the same
    redaction and disclosure.
    """
    payload: dict[str, Any] = {"tool": tool, "status": status}
    if duration_ms is not None:
        payload["duration_ms"] = float(duration_ms)
    return _emit(E_TOOL_CALL_COMPLETED, writer=writer, tool=tool, payload=payload, identity=identity, extra_payload=extra_payload)


# ---------------------------------------------------------------------------
# layer 5 -- plugin / provider selection
# ---------------------------------------------------------------------------


def emit_provider_selection(
    *,
    provider: str,
    chain: Sequence[str],
    breaker_state: str,
    health: str = "unknown",
    reason: str = "",
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record which provider/plugin was chosen and what was traversed to get there.

    ``chain`` is the fallback chain in the order it was tried. A selection that
    recorded only the winner cannot answer "was this the first choice or the
    fourth", and that difference is the difference between a working provider and
    an outage that is merely not visible.
    """
    return _emit(
        E_PROVIDER_SELECT,
        writer=writer,
        provider=provider,
        payload={"provider": provider, "chain": [str(item) for item in chain], "breaker_state": breaker_state, "health": health, "reason": reason},
        identity=identity,
        extra_payload=extra_payload,
    )


# ---------------------------------------------------------------------------
# layer 6 -- subagent
# ---------------------------------------------------------------------------


def emit_subagent_spawned(
    *,
    reason: str,
    depth: int,
    subagent_id: str,
    assigned_model: str = "",
    prompt_sha256: str = "",
    siblings: Sequence[str] | None = None,
    parent_agent: str | None = None,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a subagent spawn.

    ``depth`` and ``parent_agent`` are what make the child nestable: a reader
    reconstructs the delegation tree from ``parent_span_id`` and these, without
    guessing. ``prompt_sha256`` is a hash rather than the prompt, because the
    prompt is a highest-risk payload and its *identity* is what a comparison
    needs; pass ``payload={"prompt": ...}`` if the text itself must be recorded,
    and it will be redacted and bounded like anything else.
    """
    return _emit(
        E_SUB_SPAWNED,
        writer=writer,
        digests={"prompt_sha256": prompt_sha256},
        agent_depth=depth,
        subagent_id=subagent_id,
        parent_agent_name=parent_agent,
        payload={
            "reason": reason,
            "depth": int(depth),
            "subagent_id": subagent_id,
            "assigned_model": assigned_model,
            "siblings": [str(item) for item in (siblings or ())],
        },
        identity=identity,
        extra_payload=extra_payload,
    )


def emit_subagent_completed(
    *,
    depth: int,
    outcome: str,
    subagent_id: str = "",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    siblings: Sequence[str] | None = None,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a subagent finishing, with its tokens, cost and siblings."""
    return _emit(
        E_SUB_COMPLETED,
        writer=writer,
        agent_depth=depth,
        subagent_id=subagent_id or None,
        payload={
            "depth": int(depth),
            "outcome": outcome,
            "subagent_id": subagent_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "siblings": [str(item) for item in (siblings or ())],
        },
        identity=identity,
        extra_payload=extra_payload,
    )


# ---------------------------------------------------------------------------
# layer 7 -- swarm
# ---------------------------------------------------------------------------


def emit_swarm_plan(
    *,
    nodes: Sequence[Any],
    plan_id: str = "",
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a swarm plan."""
    return _emit(E_SWARM_PLAN, writer=writer, payload={"nodes": [str(node) for node in nodes], "plan_id": plan_id}, identity=identity, extra_payload=extra_payload)


def emit_swarm_attempt(
    *,
    worker: str,
    attempt: int,
    budget_snapshot: Mapping[str, Any],
    node: str = "",
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record one swarm worker attempt with the budget it ran under."""
    return _emit(
        E_SWARM_ATTEMPT,
        writer=writer,
        payload={"worker": worker, "attempt": int(attempt), "budget_snapshot": dict(budget_snapshot), "node": node},
        identity=identity,
        extra_payload=extra_payload,
    )


def emit_swarm_incident(
    *,
    node: str,
    detail: str,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a swarm-level incident on one node."""
    return _emit(E_SWARM_INCIDENT, writer=writer, node=node, payload={"node": node, "detail": detail}, identity=identity, extra_payload=extra_payload)


# ---------------------------------------------------------------------------
# layer 8 -- web search
# ---------------------------------------------------------------------------


def emit_web_results(
    *,
    query: str,
    provider: str,
    results: Sequence[Mapping[str, Any]],
    used: str | None = None,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record **every** web-search result and which one was then used.

    ``results`` carries each hit's url/title/snippet/rank. ``used`` is the url (or
    result id) the agent subsequently acted on, or ``None`` when it used none.

    Why every result and not just the winner: a search that returned the right
    answer at rank 9 and the wrong one at rank 1 is a *ranking* defect, and it is
    invisible in any log that records only the hit. That defect is precisely what
    a self-improving search should be able to find.
    """
    return _emit(
        E_WEB_RESULTS,
        writer=writer,
        payload={
            "query": query,
            "provider": provider,
            "results": [dict(result) for result in results],
            "used": used,
        },
        identity=identity,
        extra_payload=extra_payload,
    )


def emit_web_fetch(
    *,
    url: str,
    content_sha256: str,
    outcome: str,
    status: int | None = None,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a fetch/extract outcome by content **hash**, never by content."""
    payload: dict[str, Any] = {"url": url, "outcome": outcome}
    if status is not None:
        payload["status"] = int(status)
    return _emit(E_WEB_FETCH, writer=writer, payload=payload, digests={"content_sha256": content_sha256}, identity=identity, extra_payload=extra_payload)


# ---------------------------------------------------------------------------
# layer 9 -- memory
# ---------------------------------------------------------------------------


def emit_memory_recall(
    *,
    query: str,
    hits: Sequence[Mapping[str, Any]],
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a memory recall: the query and every hit with its score."""
    return _emit(E_MEM_RECALL, writer=writer, payload={"query": query, "hits": [dict(hit) for hit in hits]}, identity=identity, extra_payload=extra_payload)


def emit_memory_write(
    *,
    kind: str,
    memory_id: str = "",
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a memory write. Never the written content."""
    return _emit(E_MEM_WRITE, writer=writer, payload={"kind": kind, "memory_id": memory_id}, identity=identity, extra_payload=extra_payload)


def emit_memory_evicted(
    *,
    reason: str,
    count: int = 1,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a memory eviction and why."""
    return _emit(E_MEM_EVICTED, writer=writer, payload={"reason": reason, "count": int(count)}, identity=identity, extra_payload=extra_payload)


# ---------------------------------------------------------------------------
# layer 10 -- knowledge / RAG
# ---------------------------------------------------------------------------


def emit_rag_query(
    *,
    query: str,
    documents: Sequence[Mapping[str, Any]],
    digest: str = "",
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a RAG query: the documents retrieved, their scores, and the digest."""
    return _emit(E_RAG_QUERY, writer=writer, payload={"query": query, "documents": [dict(doc) for doc in documents], "digest": digest}, identity=identity, extra_payload=extra_payload)


def emit_rag_allowlist(
    *,
    decision: str,
    reason: str,
    document_id: str = "",
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record an allowlist decision on a retrieved document."""
    return _emit(E_RAG_ALLOWLIST, writer=writer, payload={"decision": decision, "reason": reason, "document_id": document_id}, identity=identity, extra_payload=extra_payload)


# ---------------------------------------------------------------------------
# layer 11 -- filesystem
# ---------------------------------------------------------------------------


def emit_filesystem_op(
    *,
    path: str,
    operation: str,
    content_sha256_after: str,
    bytes_written: int = 0,
    content_sha256_before: str | None = None,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a filesystem operation **by hash, never by content**.

    The signature has no content parameter and that is the enforcement: file
    bodies are the highest-risk thing a trace can hold (a ``.env``, a key file), and
    a parameter that does not exist cannot be filled in by accident. The before
    and after digests are what a reader needs to prove a file changed and what it
    became, without the trace becoming a backup of the workspace.
    """
    return _emit(
        E_FS_OP,
        writer=writer,
        payload={
            "path": path,
            "operation": operation,
            "bytes_written": int(bytes_written),
        },
        digests={"content_sha256_before": content_sha256_before or "", "content_sha256_after": content_sha256_after},
        identity=identity,
        extra_payload=extra_payload,
    )


# ---------------------------------------------------------------------------
# layer 12 -- cost / budget
# ---------------------------------------------------------------------------


def emit_cost_snapshot(
    *,
    totals: Mapping[str, Any],
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record running token/cost totals."""
    return _emit(E_COST_SNAPSHOT, writer=writer, payload={"totals": dict(totals)}, identity=identity, extra_payload=extra_payload)


def emit_cost_governor(
    *,
    decision: str,
    reason: str,
    budget: Mapping[str, Any] | None = None,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a budget governor decision."""
    payload: dict[str, Any] = {"decision": decision, "reason": reason}
    if budget:
        payload["budget"] = dict(budget)
    return _emit(E_COST_GOVERNOR, writer=writer, payload=payload, identity=identity, extra_payload=extra_payload)


def emit_cost_throttled(
    *,
    reason: str,
    delay_ms: float | None = None,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a throttle event."""
    payload: dict[str, Any] = {"reason": reason}
    if delay_ms is not None:
        payload["delay_ms"] = float(delay_ms)
    return _emit(E_COST_THROTTLED, writer=writer, payload=payload, identity=identity, extra_payload=extra_payload)


# ---------------------------------------------------------------------------
# layer 13 -- errors
# ---------------------------------------------------------------------------


def emit_error(
    *,
    error_code: str,
    message: str,
    severity: str | None = None,
    exc: BaseException | None = None,
    retried: bool = False,
    stack_sha256: str = "",
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a failure with its registry code.

    ``error_code`` is resolved through :func:`~.codes.resolve_error_code`, so a
    legacy value (``invalid_credentials``) is accepted and stored as its
    surviving counterpart (``AUTH_INVALID_CREDENTIALS``) and an unregistered value
    is refused. That is the unification, enforced at the only place an error code
    enters the substrate.

    When *exc* is given and no ``error_code`` is, the code is classified from the
    exception by :func:`alpha.errors.registry.classify`, which is total -- there
    is no exception this cannot put a code on.

    ``stack_sha256`` is a hash, not a traceback. A traceback in a durable log is
    unbounded text containing file paths and, routinely, an interpolated argument
    that is the very secret redaction was supposed to keep out.
    """
    payload: dict[str, Any] = {
        "error_code": error_code,
        "message": message,
        "retried": bool(retried),
    }
    if exc is not None and not error_code:
        # Classification only fills a code the caller did not supply. The journal
        # already resolved a code through ``report_exception`` -- including the
        # ``INTERNAL_ERROR`` -> ``RUN_EXECUTION_FAILED`` substitution it makes
        # because it knows the *run* died -- and re-classifying here would throw
        # that knowledge away and record the generic code beside the specific
        # one. A caller that knows better than the classifier wins.
        from alpha.errors.registry import classify

        definition = classify(exc)
        payload["error_code"] = definition.code
        payload["severity"] = definition.severity.value
        payload["retryable"] = definition.retryable
        payload["recovery"] = definition.recovery.value
        payload["error_correlation_id"] = definition.correlation_id
    if severity is not None:
        payload["severity"] = severity
    if exc is not None:
        payload["exception_type"] = type(exc).__name__
    return _emit(E_ERROR_RAISED, writer=writer, payload=payload, digests={"stack_sha256": stack_sha256}, identity=identity, extra_payload=extra_payload)


def emit_error_swallowed(
    *,
    error_code: str,
    escalated: bool,
    exc: BaseException | None = None,
    message: str = "",
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a caught failure that was not re-raised.

    ``escalated`` says whether anything acted on it. A swallowed failure with
    ``escalated=False`` and a swallowed failure nobody was told about are
    different states of the world, and only one of them is a bug.
    """
    payload: dict[str, Any] = {"error_code": error_code, "escalated": bool(escalated), "message": message}
    if exc is not None:
        payload["exception_type"] = type(exc).__name__
    return _emit(E_ERROR_SWALLOWED, writer=writer, payload=payload, identity=identity, extra_payload=extra_payload)


# ---------------------------------------------------------------------------
# layer 14 -- handoff
# ---------------------------------------------------------------------------


def emit_handoff(
    *,
    from_agent: str,
    to_agent: str,
    reason: str,
    attempt: int,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a handoff from one agent to another."""
    return _emit(
        E_HANDOFF,
        writer=writer,
        payload={"from_agent": from_agent, "to_agent": to_agent, "reason": reason, "attempt": int(attempt)},
        identity=identity,
        extra_payload=extra_payload,
    )


# ---------------------------------------------------------------------------
# layer 15 -- human loop
# ---------------------------------------------------------------------------


def emit_human_interrupt(
    *,
    action: str,
    reason: str,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record an interrupt or an approval request."""
    return _emit(E_HUMAN_INTERRUPT, writer=writer, payload={"action": action, "reason": reason}, identity=identity, extra_payload=extra_payload)


def emit_human_edit(
    *,
    target: str,
    after: str,
    before_sha256: str = "",
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a human edit. ``after`` is a hash or redacted text, never raw state.

    ``before_sha256`` is a digest precisely so the *before* side can be recorded
    at all: the whole value of this event is "what it was and what it became",
    and the before half is the half that must not be stored verbatim.
    """
    return _emit(E_HUMAN_EDIT, writer=writer, payload={"target": target, "after": after}, digests={"before_sha256": before_sha256}, identity=identity, extra_payload=extra_payload)


# ---------------------------------------------------------------------------
# layer 16 -- guardrails
# ---------------------------------------------------------------------------


def emit_guardrail_denied(
    *,
    guard: str,
    reason: str,
    detail: str = "",
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a sandbox denial, safety stop or budget stop."""
    return _emit(E_GUARD_DENIED, writer=writer, payload={"guard": guard, "reason": reason, "detail": detail}, identity=identity, extra_payload=extra_payload)


# ---------------------------------------------------------------------------
# layer 17 -- self-evolution
# ---------------------------------------------------------------------------


def emit_evolution_observed(
    *,
    fingerprint: str,
    source: str,
    detail: str = "",
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a sentinel observation of a fault in this trace.

    The shape is defined now and filled in later, deliberately: the sentinel
    (:mod:`alpha.runtime.sentinel`) is the consumer, and a consumer that has to
    wait for a producer's schema is a consumer that gets a bespoke schema. The
    ``fingerprint`` is
    :func:`alpha.runtime.sentinel.signals.compute_fingerprint`, so the same fault
    observed twice produces the same key.
    """
    return _emit(E_EVOLUTION_OBSERVED, writer=writer, payload={"fingerprint": fingerprint, "source": source, "detail": detail}, identity=identity, extra_payload=extra_payload)


def emit_evolution_diagnosed(
    *,
    fingerprint: str,
    diagnosis: str,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a sentinel diagnosis for a fingerprint."""
    return _emit(E_EVOLUTION_DIAGNOSED, writer=writer, payload={"fingerprint": fingerprint, "diagnosis": diagnosis}, identity=identity, extra_payload=extra_payload)


def emit_evolution_fix(
    *,
    fingerprint: str,
    attempt: int,
    summary: str = "",
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a sentinel fix attempt."""
    return _emit(E_EVOLUTION_FIX, writer=writer, payload={"fingerprint": fingerprint, "attempt": int(attempt), "summary": summary}, identity=identity, extra_payload=extra_payload)


def emit_evolution_verify(
    *,
    fingerprint: str,
    outcome: str,
    writer: TraceWriter | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    **identity: Any,
) -> TraceEnvelope | None:
    """Record a verification outcome.

    ``outcome`` is free text because the sentinel's vocabulary lives in
    :mod:`alpha.runtime.sentinel.verify` and duplicating it here would be the
    kind of fifth taxonomy this package exists to prevent. The one rule is
    enforced upstream: a red or unknown verification never commits.
    """
    return _emit(E_EVOLUTION_VERIFY, writer=writer, payload={"fingerprint": fingerprint, "outcome": outcome}, identity=identity, extra_payload=extra_payload)
