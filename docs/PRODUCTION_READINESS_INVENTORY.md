# Alpha Production-Readiness Inventory

Status meanings: **implemented** has an identified production owner and test or
contract; **partial** has a foundation but needs an end-to-end gate; **unverified**
requires a deliberate audit before it can be claimed in a production release.

| Capability | Status | Evidence / owner | Production gap and next gate |
| --- | --- | --- | --- |
| Durable chat run lifecycle | implemented | `alpha.runtime.runs.RunManager`; run store and Gateway run routes | Preserve this as the sole lifecycle owner; test migrations against supported DBs. |
| Lease ownership and orphan recovery | implemented | `runtime/runs/manager.py`; `run_ownership` config | Exercise multi-worker failure recovery in release drills. |
| Scheduled execution queue | implemented | Scheduler contract in `backend/AGENTS.md` | Add operational SLO and queue-depth alert thresholds. |
| Durable MCP tasks | implemented | `alpha.mcp.tasks` contract | Audit every integration driver for idempotent external delivery. |
| Authentication and thread ownership | implemented | Gateway auth/authz and thread admission checks | Run an API-wide authorization matrix in CI. |
| Internal-context trust boundary | implemented | Gateway run-context stripping contract | Add fuzz/property tests for every newly reserved key. |
| Run event history | implemented | Run events store/journal | Define retention, redaction, and audit export policy. |
| Health and readiness | implemented | `/health`, `/health/ready` | Deploy synthetic probes and alerting. |
| Integration wiring checks | implemented | Feature manifest and no-orphan tests | Regenerate manifest after registry changes. |
| Acceptance criteria | partial | `app.gateway.run_models.AcceptanceCriterion` | Expose evidence collection and verifier result through run APIs/UI. |
| APEX goal-driven control plane | partial | `alpha.apex.executive`, Gateway RunManager dispatcher, acceptance report gate; `tests/test_apex_executive.py`, `test_apex_dispatcher.py`, `test_apex_control.py`, `test_apex_api.py` | Goal/session/mode JSON snapshots use same-host local locks only; event journal is a separate write; no automatic trusted collector verifies arbitrary natural-language criteria; background APEX loop is opt-in. Complete multi-process Windows restart drills and a bounded real-task acceptance run before raising status. |
| Evidence verification | partial | `alpha.runtime.runs.verification` | Add trusted collectors for tests, artifacts, HTTP checks, and reviewer approvals. |
| Centralized policy decision point | partial | Existing authz/approval facilities are distributed | Define one policy grant contract at every consequential tool boundary. |
| Secrets lifecycle | partial | Existing secret redaction in run metadata | Add rotation/revocation inventory and secret-scanning regression tests. |
| Tool reliability contracts | partial | Sandboxing and task systems exist | Standardize pre/postconditions, timeout, retry safety, and cancellation on every tool. |
| Cost budget enforcement | partial | Token meter and console cost reporting exist | Enforce per-run/user/workspace hard ceilings before model and tool calls. |
| Benchmark release gate | partial | `backend/scripts/benchmark/` | Create versioned task corpus and block releases on scored regressions. |
| Prompt-injection security suite | unverified | Guardrails are documented | Add fixture corpus covering tool output, uploads, web content, and MCP events. |
| Backup and restore | unverified | Persistence is documented | Automate backup, restoration into a clean environment, and recovery-time measurement. |
| Multi-tenant isolation | partial | Per-owner stores and route checks exist | Test every database query and artifact path with cross-owner attempts. |
| Observability and incident response | partial | Trace middleware and health endpoints exist | Publish dashboards, alerts, runbooks, and an on-call ownership model. |
| Self-improvement governance | partial | Learning/review components are present | Require opt-in, redaction, offline evaluation, canary, provenance, rollback. |
| Autonomous side effects | partial | Scheduler and run idempotency exist | Require expiry, budget, policy grant, approval, and evidence before default enablement. |
| Alpha Mod Kernel | partial | `alpha.mods.kernel`; wired into lead-agent tool/model lifecycle and run admission; `tests/test_mod_kernel.py`, `test_mod_middleware.py`, `test_mod_enforcers.py`, plus the advanced suites (`test_mod_approvals.py`, `test_mods_router.py`, `test_mod_manifest.py`, …) | Approval holds are durable single-process JSON under `runtime_home()` with an admin approve/reject flow (`POST /api/mods/holds/{id}/approve|reject`), so deferred actions now have an operator-resume path for one Gateway — not cross-worker. Kernel journal (audit ledger), capability storage, timers, and UI-card store remain process-local, and kernel event-journal replay/dead-letter processing is not implemented. |
| Dynamic workflow runtime | implemented | `alpha.workflow.runtime`, `alpha.workflow.execution`, `alpha.orchestrator.loop`; `test_workflow_runtime_correctness.py` | Wave concurrency and the durable store are process-local: a multi-worker deployment needs shared lease coordination before claiming cross-process exactly-once execution. |
| Dynamic workflow observability | implemented | `alpha.workflow.observability`, journalled `node_timed`/`wave_dispatched` events; `test_workflow_observability_router.py` | Reports execution only. Add a dashboard and alert thresholds on the critical path and timed-out nodes. |
| Dynamic workflow forking and dry run | implemented | `alpha.workflow.time_travel`; `test_workflow_time_travel.py` | Dry runs are throwaway projections and prove nothing about acceptance; add a re-measurement gate before any optimization is trusted. |
| Dynamic workflow domain executors | partial | `alpha.orchestrator.domain_executors` (`alpha.local.model` / `.tool` / `.subagent`); opt-in via `bind_domain_executors()` | Exercised only in refusal paths by the offline suite. Needs live-provider contract tests, per-node cost ceilings, and a cancellation seam for the fenced in-flight call. |
| Workflow template promotion | partial | `alpha.workflow.templates`; `test_workflow_templates_and_improvement.py` | Promotion is evidence-gated by a completed run, but there is no cross-revision regression corpus, so a promoted template can still drift as the engine changes. |
| Workflow improvement proposals | partial | `alpha.workflow.self_improvement`; proposes only, never mutates | Nothing is shown to work until a re-run is measured. Add an offline evaluation harness and canary before allowing any automated application. |

## Immediate implementation queue

1. Persist and retrieve verified/unverified evidence results for run details.
2. Add server-side policy grants for consequential tools; no tool should trust a
   client-provided approval flag.
3. Add adversarial regression tests and a release scorecard before enabling more
   autonomy by default.
4. Run a backup/restore drill and publish an operator runbook.

The inventory is intentionally conservative: a marketing claim or an existing
module is not proof of production readiness. Update status only with a concrete
owner, exercised path, and automated acceptance test.
