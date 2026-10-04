# Alpha — Error Catalog

The authoritative source is `backend/packages/harness/alpha/errors/registry.py`.
This document is a **rendering** of that registry for humans; if the two ever
disagree, the registry wins and this file is wrong.

## The contract

- **One code, one meaning.** A code is added to the registry, never invented at a
  raise site.
- **The user-facing message lives with the code**, not at the call site. Messages
  never interpolate exception text — the caller already has it.
- **Correlation is a property of the code.** Every occurrence reports under the
  same `correlation_id`. Per-occurrence identity (request/run/thread) rides the
  `alpha.trace_context` id and is carried separately in `ReportedError.context`.
- **Classification is total.** Any exception maps to exactly one code: by
  `CodedError` first, then by exception type, then by provider status, then
  `INTERNAL_ERROR`. A run can never end with an uncoded failure.
- **Codes are append-only.** Never repurpose or renumber an existing code, and
  never widen a code's severity in place.

## Severity ladder

`ErrorSeverity` is the single taxonomy. `observability/contract.py` restates it
as data (`SEVERITIES = {"info", "warning", "error", "critical"}`) and
`test_behaviour_trace_contract.py` asserts the two sets are equal — which is what
stops the restatement from silently forking.

## Recovery actions

`RecoveryAction` is the authority on *what to do* about a failure. It is
distinct from `retryable`, which only says whether retrying is *safe*.

## The codes

Grouped by the category the prompt requires. Each row: code · severity ·
retryable · recovery.

### Generic fallbacks

| Code | Sev | Retry | Recovery | Meaning |
|---|---|---|---|---|
| `INTERNAL_ERROR` | ERROR | no | INVESTIGATE | Total fallback so a run can never end uncoded |
| `NOT_IMPLEMENTED` | ERROR | no | NONE | Capability absent in this deployment |
| `DEPENDENCY_UNAVAILABLE` | ERROR | yes | RETRY | A required dependency is down |
| `TIMEOUT` | ERROR | yes | RETRY | Operation exceeded its budget |
| `CANCELLED` | INFO | no | NONE | Cancelled; not a failure of the system |
| `DEGRADED_MODE` | WARN | no | NONE | Running with reduced capability, disclosed |

### Network & provider

| Code | Sev | Retry | Recovery |
|---|---|---|---|
| `NETWORK_UNAVAILABLE` | ERROR | yes | RETRY |
| `UPSTREAM_UNAVAILABLE` | ERROR | yes | RETRY |
| `MODEL_PROVIDER_UNAVAILABLE` | ERROR | yes | FALLBACK |
| `MODEL_PROVIDER_AUTH` | ERROR | no | ESCALATE |
| `MODEL_PROVIDER_QUOTA` | ERROR | no | ESCALATE |
| `MODEL_PROVIDER_RATE_LIMITED` | WARN | yes | BACKOFF |
| `MODEL_RESPONSE_INVALID` | ERROR | yes | RETRY |

### Auth

| Code | Sev | Retry | Recovery |
|---|---|---|---|
| `AUTH_REQUIRED` | ERROR | no | ESCALATE |
| `AUTH_INVALID_CREDENTIALS` | ERROR | no | ESCALATE |
| `AUTH_FORBIDDEN` | ERROR | no | ESCALATE |
| `AUTH_QUOTA_EXCEEDED` | ERROR | no | ESCALATE |
| `AUTH_SSO_FAILED` | ERROR | no | ESCALATE |

### Run execution

| Code | Sev | Retry | Recovery |
|---|---|---|---|
| `RUN_EXECUTION_FAILED` | ERROR | no | INVESTIGATE |
| `RUN_CANCELLED` | INFO | no | NONE |
| `RUN_NOT_FOUND` | ERROR | no | NONE |
| `RUN_ADMISSION_CONFLICT` | WARN | yes | RETRY |
| `RUN_OWNERSHIP_LOST` | ERROR | no | HANDOFF |
| `RUN_QUOTA_EXCEEDED` | ERROR | no | ESCALATE |
| `RUN_RECURSION_LIMIT` | ERROR | no | INVESTIGATE |
| `RSI_BUDGET_EXCEEDED` | ERROR | no | ESCALATE |
| `RUN_DELIVERY_UNVERIFIED` | ERROR | no | INVESTIGATE |

### Checkpoint & state

| Code | Sev | Retry | Recovery |
|---|---|---|---|
| `CHECKPOINT_INCOMPATIBLE` | ERROR | no | INVESTIGATE |
| `CHECKPOINT_WRITE_FAILED` | ERROR | yes | RETRY |
| `STATE_ACCESS_FAILED` | ERROR | no | INVESTIGATE |

### Tools & sandbox

| Code | Sev | Retry | Recovery |
|---|---|---|---|
| `TOOL_EXECUTION_FAILED` | ERROR | yes | RETRY |
| `TOOL_NOT_FOUND` | ERROR | no | NONE |
| `TOOL_SCHEMA_INVALID` | ERROR | no | REPAIR_ARGS |
| `SANDBOX_UNAVAILABLE` | ERROR | yes | RETRY |
| `SANDBOX_TIMEOUT` | ERROR | yes | RETRY |
| `SANDBOX_POLICY_DENIED` | ERROR | no | ESCALATE |

### Persistence

| Code | Sev | Retry | Recovery |
|---|---|---|---|
| `PERSISTENCE_WRITE_FAILED` | ERROR | yes | RETRY |
| `PERSISTENCE_CONFLICT` | WARN | yes | RETRY |
| `PERSISTENCE_UNAVAILABLE` | ERROR | yes | RETRY |
| `PERSISTENCE_CORRUPT` | ERROR | no | QUARANTINE |

### Event stream

| Code | Sev | Retry | Recovery |
|---|---|---|---|
| `EVENT_STREAM_WRITE_FAILED` | ERROR | yes | RETRY |
| `EVENT_STREAM_UNAVAILABLE` | ERROR | yes | RETRY |

### MCP

| Code | Sev | Retry | Recovery |
|---|---|---|---|
| `MCP_CONNECTION_FAILED` | ERROR | yes | RETRY |
| `MCP_TOOL_FAILED` | ERROR | yes | RETRY |
| `MCP_PROTOCOL_ERROR` | ERROR | no | INVESTIGATE |

### Storage

| Code | Sev | Retry | Recovery |
|---|---|---|---|
| `STORAGE_READ_FAILED` | ERROR | yes | RETRY |
| `STORAGE_WRITE_FAILED` | ERROR | yes | RETRY |
| `STORAGE_NOT_FOUND` | ERROR | no | NONE |

### Extensions & security

| Code | Sev | Retry | Recovery |
|---|---|---|---|
| `EXTENSION_LOAD_FAILED` | ERROR | no | INVESTIGATE |
| `SECURITY_POLICY_VIOLATION` | ERROR | no | ESCALATE |
| `INVALID_INPUT` | ERROR | no | REPAIR_ARGS |

## Frontend error identity

The frontend has a parallel, narrower concept: the **support identity** of a
failed run (`frontend/src/lib/chat-support-id.ts`). Only the server's `code` and
`correlationId` are rendered. The server's failure `message` is deliberately
**not** rendered — it is derived from tool output and provider text, so it is
untrusted content, and `chat-request-error.test.mjs` pins that such a body must
never reach the transcript.

## What is deliberately not here

- **No per-occurrence codes.** A run id, thread id, user id or trace id is never
  a code and never a metric label. Label cardinality is a hard cap.
- **No free-form messages.** A message invented at a call site is a bug; the
  registry is the only source.
