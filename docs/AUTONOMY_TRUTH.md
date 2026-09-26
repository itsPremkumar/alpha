# Autonomy Truth & Recovery

Alpha's execution engines can plan, delegate, supervise, repair, and verify
work. The `autonomy_control` built-in adds the missing truth layer around those
engines: it makes capability uncertainty, failure recovery, and activity
visible without requiring a model, provider, network, or paid service.

## Readiness is fail-closed

```json
{
  "action": "readiness"
}
```

The tool ignores model-authored readiness claims. Its hidden runtime argument
and server configuration establish the evidence. The result is `ready`,
`degraded`, or `blocked`. Missing required observations are reported as
`unknown`; they are never converted into a healthy assumption. Each decision
includes a reason, evidence labels, confidence, and a next action.

The required core loop is:

- current model execution;
- tool execution;
- offline project documentation.

The report also covers optional capabilities and limitations:

- sandbox execution;
- model vision support;
- network policy;
- enabled MCP connectors;
- public skills.

Optional absences do not block a valid offline agent loop. They are returned in
`optional_limitations` so the planner can select a supported alternative.

`build_autonomous_plan` runs the same server-owned probe automatically. Every
one-prompt plan embeds `autonomy_readiness`; a degraded or blocked core forces
`autonomy: gated`, and an unready plan cannot persist a Kanban board or install
profiles.

## Capability boundaries

```json
{
  "action": "capability",
  "capability": "external_side_effect"
}
```

Capability inputs come from the same server-owned probe. Supplying
`approval_granted: true` or a writable-context claim cannot authorize an
external side effect or mutation; those remain owned by the project approval
and execution-mode middleware. The decision distinguishes:

- `available`;
- `degraded` — usable only with a constraint or approval;
- `unavailable` — cannot be performed safely;
- `unknown` — evidence is insufficient.

The registry also exposes explicit non-capabilities such as
`unbounded_autonomy`, `credential_inference`, `universal_os_control`, and
`guaranteed_success`. These are deliberate refusals, not missing features to
work around.

## Failure recovery

```json
{
  "action": "failure",
  "error": "HTTP 429 rate limit"
}
```

Failures are classified as permission, sandbox, dependency, provider, network,
resource, input, or unknown. The result says whether a bounded retry is safe,
which next steps to take, and when to escalate. Error summaries are redacted and
capped at 500 characters; credentials, bearer tokens, and API keys are removed
before the result reaches the agent.

## Activity digest

```json
{
  "action": "activity",
  "events_json": "[{\"type\":\"tool_call\",\"tool\":\"bash\",\"status\":\"success\"}]"
}
```

The digest summarizes existing evidence without exposing raw event payloads:

- step/success/failure/retry/approval counts;
- bounded tool-type counts;
- edited-file count and short SHA-256 path references;
- input/output token totals;
- estimated spend;
- optional duration when timestamps are present;
- safe next recommendations.

Malformed records are ignored, event count and output length are bounded, and
raw errors, thoughts, tool output, file paths, and secrets are never copied into
the summary.

## Recovery brief

```json
{"action": "recovery"}
```

`recovery` reads the current thread's bounded continuity state and returns a
compact resume brief: redacted task notes, tool names/statuses, history
availability, counts, and safe next steps. It never returns raw messages, tool
arguments/output, file paths, or credentials. A missing or disabled continuity
store reports `unavailable`; it does not pretend that history is complete.

This is an opt-in continuity feature at the storage layer, so enabling it is an
operator configuration decision. The recovery action itself is always safe and
available for inspection.

## Safety and cost boundary

This layer is pure local computation. It does not:

- invoke a model or external provider;
- access the network;
- read credentials or runtime secrets;
- persist context, events, or errors;
- execute repairs or approve side effects.

That separation makes it safe to call before planning, after a failure, or when
presenting a run to a human. Actual mutation and external communication remain
owned by the existing policy, approval, sandbox, and action-ledger systems.

## Tests

```bash
cd backend
uv run pytest tests/test_autonomy_truth.py -q
```
