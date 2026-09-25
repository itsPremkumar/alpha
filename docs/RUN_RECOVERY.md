# Safe Run Recovery

Alpha treats a run as **durable work**, not as a browser connection. Conversation
state, goals, artifacts, tool results, and graph progress are checkpointed, while
the run row and event journal provide ownership and an audit trail.

## Default behavior

When enabled, Alpha automatically continues a recoverable run from its latest
safe checkpoint after:

- the browser or Gateway network connection drops;
- the Gateway is restarted;
- a worker dies and its run lease expires;
- a model/provider fails after its normal retry/fallback chain; or
- the configured model changes before the continuation is launched.

A dropped SSE connection does **not** mean cancellation. The client may reload
from durable thread/run events after reconnecting. The explicit Stop/cancel API
remains the user-controlled termination path.

The continuation is a new, idempotently admitted run linked through
`metadata.resumed_from_run_id`. It does not edit or erase the failed run's audit
record. Conversation, goal, todo, sandbox, artifact, and other checkpoint state
continue from the selected checkpoint. An active durable goal also re-enters
the bounded goal-continuation loop even when the graph itself has no pending
node at the instant of recovery.

## Safety rule: no blind side-effect replay

No distributed system can guarantee exactly-once effects for an arbitrary
external tool. A provider may accept a request and the process may crash before
the result reaches the checkpoint.

Alpha therefore auto-resumes only when every pending graph node is a known
model/agent node. Recovery requires a real compiled graph because degraded raw
full-mode blobs cannot reconstruct `next`/`tasks`; unknown scheduling state is
never treated as “nothing to do.” It stops with
`stop_reason=recovery_confirmation_required` when the next node is a tool, MCP
call, shell/browser action, write/delete,
payment, custom middleware, or unknown node. Review whether the action already
took effect, then use the existing manual Resume action or regenerate the turn.

A transient model-provider fallback is the one bounded rewind case: Alpha may
select the immediate parent checkpoint only when the head's last assistant
message is explicitly marked `agent_workspace_error_fallback` with a recoverable
reason (`transient`, `busy`, `burst_rate`, or `circuit_open`). Authentication,
quota, configuration, and generic deterministic failures are not retried.
Generic crashes never rewind visible work.

## Recovery outcomes

| Stop reason | Meaning |
|---|---|
| `orphan_recovered` | The owning worker disappeared; ownership was atomically reclaimed. |
| `gateway_shutdown` | A graceful Gateway shutdown interrupted the run. |
| `model_failure` | The provider retry/fallback chain ended in a marked model error. |
| `recovery_confirmation_required` | A pending external action may already have taken effect. |
| `recovery_exhausted` | The bounded automatic-attempt limit was reached. |
| `recovery_superseded` | A newer run already advanced the thread. |
| `recovery_no_work` | The selected checkpoint has no safe pending graph work. |
| `recovery_owner_missing` | Ownership could not be proved for an internal continuation. |
| `recovery_blocked` | Checkpoint/model/admission validation could not establish a safe replay. |

Terminal recovery dispositions use a compare-and-set update on
`(status, stop_reason)`. A stale worker cannot overwrite a newer worker's
confirmation or exhaustion decision.

## Configuration

`run_ownership` is startup-only:

```yaml
run_ownership:
  lease_seconds: 30
  grace_seconds: 10
  heartbeat_enabled: false
  auto_resume: true
  resume_poll_interval_seconds: 5.0
  max_resume_attempts: 3
  resume_backoff_seconds: 5.0
  max_concurrent_resumes: 2
```

Use `heartbeat_enabled: true` for `GATEWAY_WORKERS > 1`. Cross-worker durable
recovery also requires the existing PostgreSQL and shared run-event deployment
contract.

Each continuation stores a durable `recovery_attempt` and uses
`auto-recovery:<source_run_id>` as its admission idempotency key. If the Gateway
crashes between inspection and launch, the next worker reuses the same admission
instead of creating a second execution. Chat auto-resume deliberately excludes
scheduled-task and durable MCP-notification runs: their queue/dispatcher owns
occurrence identity, retry accounting, and completion projection.

## Model switching

A continuation intentionally does not pin the failed run's model name. The new
graph is assembled through the normal `start_run` path using current model
configuration, while the checkpoint retains conversation and work state. Normal
authorization, tool allowlists, sandbox policy, token budgets, and user context
are re-evaluated; recovery never bypasses them.

## Operational notes

- Run history remains the source of audit truth; a recovery does not relabel the
  failed run as successful.
- A durable cancellation request is an explicit stop fence: recovery candidate
  scans exclude it even if shutdown or orphan reconciliation writes a
  recoverable-looking terminal reason.
- Stream history is bounded. After a sufficiently old stream gap, clients must
  reload durable thread/run state and history.
- Files or remote effects completed immediately before a crash may exist outside
  the checkpoint. The confirmation gate is the protection; no generic recovery
  mechanism can infer an arbitrary provider's side-effect outcome.
- A model failure with no addressable parent model checkpoint is left for manual
  review rather than replaying the whole conversation.
