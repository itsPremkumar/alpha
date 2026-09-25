# Dynamic Workflows

Alpha's Dynamic Workflow Engine (DWE) is a typed, evidence-gated graph runtime.
A workflow definition is a template; each run owns its state, node statuses,
budgets, approvals, patches, and terminal outcome. The Gateway remains the owner
of ordinary Agent runs, and the DWE is a correlated child orchestration plane for
hosts that explicitly use it.

## Choosing an entry point

### Full dynamic loop (opt in)

`POST /api/workflows/dynamic/perceive` previews the deterministic intent/domain
classifier, task decomposition, execution waves, and resource preview without
starting a run:

```http
POST /api/workflows/dynamic/perceive
Content-Type: application/json

{"prompt":"implement and verify a feature","context":{}}
```

`POST /api/workflows/dynamic/execute` performs the same perception and
compilation, registers the graph, optionally starts it, and returns the measured
run result:

```http
POST /api/workflows/dynamic/execute
Content-Type: application/json

{
  "prompt":"implement and verify a feature",
  "mode":"normal",
  "auto_execute":true,
  "max_steps":40
}
```

The response includes the selected decisions, discovered registries, resource
provenance, graph/run identifiers, node outputs, replans, compensation attempts,
and an explicit acceptance label. When a host supplies the shared
`ExecutionKernel`, the dynamic bridge starts the run and dispatches every wave
(and applies runtime replan patches) through that kernel's per-run claim. An
explicitly unbound bridge does not fall back to the live executor registry, so
an installed digest executor cannot silently turn a missing host binding into
claimed work. The default `alpha.local.digest` executor is only a recomputable
**graph projection**: it can demonstrate scheduling, versioning, and evidence
plumbing, but it cannot claim that a domain task was performed. Such responses
carry `execution_label: "local_digest_projection"` and
`acceptance_passed: false`. A host must bind a real executor before domain
acceptance can be true.

Recurring automation is deliberately not converted into an in-process cron
loop. The service reports the missing scheduler handoff and leaves recurring
execution to the existing scheduler/host lifecycle.

### Paradigm turns and bot mode

`POST /api/workflows/turns` remains the low-latency compatibility seam. By
default it maps a known paradigm (`direct_agent`, `subagent`, `bot_profile`,
`moa`, `deep_research`, or `deep_think`) to a bounded graph and returns a
`TurnOutcome`. Unknown paradigms and the dynamic swarm paradigm fail before a
run is created. Set `dynamic: true` to use the full perception/discovery service
for that turn instead.

`POST /api/bots/{name}/workflow` runs the same service in `bot` mode after the
server validates the bot and authorizes the caller. It is not a shortcut that
turns an inbox message or bot profile into evidence of work; the same executor
and acceptance rules apply.

## Run control and mutation

Definitions and runs are created through:

- `POST /api/workflows` and `GET /api/workflows`
- `POST /api/workflows/{workflow_id}/runs`
- `GET /api/workflows/runs` and `GET /api/workflows/runs/{run_id}`

A run can be advanced one scheduling wave with
`POST /api/workflows/runs/{run_id}/step`, stopped with
`POST /api/workflows/runs/{run_id}/cancel`, and inspected through its event
stream. Approval-gated nodes pause in `waiting_approval`; resolve the exact
request with `POST /api/workflows/runs/{run_id}/approvals/{node_id}`. Approval
IDs are checked against the active request so a stale client decision cannot
resume a different gate.

Graph changes use optimistic concurrency. `POST .../patch` requires the current
`base_graph_version`, validates typed operations and structural invariants, and
journals the committed revision. `POST .../replan` creates a bounded repair
patch and reports `committed_resume_failed` when the repaired graph cannot be
reopened or dispatched. `POST .../compensate` invokes only a real, dedicated
compensation executor; a missing callback or missing evidence is a disclosed
failure, never a fabricated rollback receipt.

`GET /api/workflows/system/registries` is a bounded, read-only view of the
capability, tool, skill, MCP, subagent, and bot registries used by planning.
Registry health and unavailable entries are returned as data; discovery does
not imply that a provider is connected or authorized.

## Evidence, replay, and durability

Every workflow event is appended to the durable JSONL sink before listeners are
notified. The Gateway sink fails closed when persistence fails; a run is not
advertised as durably executable when the store is unwritable. The following
surfaces expose the actual journal rather than an in-memory success shape:

- `GET /api/workflows/system/durability`
- `GET /api/workflows/runs/{run_id}/events/durable`
- `POST /api/workflows/runs/{run_id}/project`
- `POST /api/workflows/hydrate`
- `GET|POST /api/workflows/{workflow_id}/plans`

Projection and hydration report corrupt tails, stale projections, missing
graphs/definitions, and skipped owner-scoped resources. Replay folds the
validated event log into a fresh projection and reports covered-field
mismatches; it never emits new replay events. The engine keeps the authored
base graph separately from its compatibility projection, so replay can reapply
patch revisions in order and restore journaled node outputs without treating a
latest projected graph as the original template.

Workflow plan revisions are append-only. Ordinary registration does not create
an implicit revision; the explicit plan endpoint records the current graph, and
the first patch seeds its real base revision when necessary before advancing it
with compare-and-set. A same-version different graph is rejected rather than
overwritten.

## Ownership and safety

For real HTTP requests, workflow definitions and runs carry a server-resolved
owner. Reads and mutations enforce that owner; request bodies cannot self-assert
ownership. Event payloads are redacted before persistence. Model/user text is
data, not a system instruction. The workflow plane does not replace
`RunManager`, the group/bot lifecycle owner, or the scheduler, and it does not
create a second parent-run stream.

## Current boundaries

The orchestration graph, scheduling, retries, approvals, conditional routing,
bounded loops, patch OCC, replay, and compensation plumbing are implemented.
Production domain work still requires a host-bound executor for the relevant
node kind (model, tool, MCP, sandbox, bot, or external service). The default
digest executor is intentionally projection-only. The current scheduler remains
process-local; a multi-worker deployment must provide shared lease/coordination
before claiming cross-process exactly-once execution or true concurrent wave
parallelism.

## Regression coverage

The implementation is covered by `backend/tests/test_dynamic_workflow_service.py`,
`test_dynamic_workflow_router.py`, `test_dynamic_workflow_engine.py`,
`test_workflow_dag_edges.py`, `test_workflow_durability_router.py`,
`test_orchestrator_kernel.py`, `test_orchestrator_mode_mapper.py`, and
`test_bot_dynamic_workflow.py`, plus the frontend `workflows.test.mjs` client
contract tests.
