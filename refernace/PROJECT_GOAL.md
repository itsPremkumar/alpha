# Project Goal — Autonomous AI Execution Platform for Windows

> **Give it a goal. It figures out how to accomplish it.**

Build a persistent, autonomous, goal-driven AI execution platform for Windows
that plans, executes, verifies, recovers, self-heals, and keeps working toward
a user's objective with minimal human intervention. The user should never need
to understand agents, models, APIs, terminals, Python environments, Docker,
dependencies, configuration, MCP servers, subagents, or infrastructure.

## The UX contract

```text
Download → Install → Enter a goal → The AI takes care of everything else.
```

The system owns planning, execution, tool selection, subagent coordination,
error recovery, verification, optimization, and long-running execution. Human
intervention is required only when genuinely necessary (approval-gated,
blocked, or resource-exhausted — never for routine execution).

## The ultimate execution loop

```text
                 HUMAN
                   │
                   ▼
                 GOAL
                   │
                   ▼
              GOAL ANALYSIS
                   │
                   ▼
              PLAN GENERATION
                   │
                   ▼
            TASK DECOMPOSITION
                   │
                   ▼
          AGENT / TOOL SELECTION
                   │
                   ▼
              EXECUTION
                   │
                   ▼
              OBSERVATION
                   │
                   ▼
             VERIFICATION
                   │
             ┌─────┴─────┐
             │           │
          SUCCESS      FAILURE
             │           │
             │           ▼
             │       DIAGNOSIS
             │           │
             │           ▼
             │      RECOVERY
             │           │
             │      ┌────┴────┐
             │      │         │
             │   RECOVER    REPLAN
             │      │         │
             │      └────┬────┘
             │           │
             └───────────┘
                   │
                   ▼
              FINAL VERIFY
                   │
                   ▼
             GOAL COMPLETE
```

Completion rule: **no evidence = no completion.** Failure of an approach is
never treated as failure of the goal — the system finds another path until
the goal is completed, explicitly blocked, or needs human authorization.

## Capability map — verified status

Status meanings: **Done** = implemented and verified by tests/builds;
**Partial** = works end-to-end with known gaps listed; **Planned** = not built.

| # | Capability | Status | Evidence / gap |
|---|------------|--------|----------------|
| 1 | One-click Windows install | Partial | `electron/` bundles Node/uv runtimes, auto-seeds config, opens straight into the workspace (`main.js`); unattended setup via `AGENT_WORKSPACE_SETUP_*` (`scripts/wizard/noninteractive.py`, verified exit 0); boot self-diagnostic fails loud on low disk with a versions summary. Gap: model key still needs the user (or a running Ollama daemon); no hardware-adaptive install strategy yet. |
| 2 | Goal-driven execution (goal → plan → tasks → verify) | Partial | Goal evaluator + hidden continuation loop, per-run acceptance criteria, plan-mode todos, kanban board (`runtime/goal.py`, `runtime/runs/worker.py`, `subagents/report_contract.py`). Gap: no single persistent goal-tree artifact spanning months. |
| 3 | Autonomous planning + dynamic subagents | Done | Lead orchestrator, subagent registry (builtin/config/managed), durable batches, benefit-based routing policy, intent categories; replanning via the goal loop. Pinned by `test_subagent_routing_prompt.py`, batch suites. |
| 4 | Agent failure recovery (agents replaceable, goals persistent) | Done | Guardrail caps surfaced as additive `stop_reason`, acceptance-check verdicts, tool receipts, run cancel/takeover with lease fencing, durable retries. Pinned by status-contract tests. |
| 5 | Goal integrity across crashes/restarts | Done | Durable scheduler queue, MCP tasks, and batches recover via leases + orphan reconciliation; group runs persist and mark `interrupted`. Interrupted chat runs resume from the head checkpoint via `POST /api/threads/{id}/runs/{rid}/resume` (409 unless terminal, resumable, and still the latest turn) with a Resume button on the Overview Activity card; pinned by `test_run_resume.py`. Auto-execution on boot is deliberately explicit, not implicit — resuming paid model work without consent would violate the approval principles. |
| 6 | Persistent state + checkpointing | Done | LangGraph checkpoints, run journal/event store, goal state, artifacts with revisions, thread branches. A crash never means starting from zero for durable work. |
| 7 | Verification-driven completion (multi-level) | Done | Task level: acceptance leaves (`file:`, `tests_passed:`) + receipts. Subgoal/goal level: evaluator + stand-down reasons. Real-world level stays human-confirmed by design. |
| 8 | Autonomous task queue + scheduling | Done | `ScheduledTaskService`, `McpTaskService`, `SubagentBatchService` with lease recovery; scheduled-tasks UI; `test_scheduled_task_service.py` et al. pass. |
| 9 | Team execution from one objective | Done | `POST /api/groups/{name}/runs`: parallel member subagents (no clarification blocking) + moderator synthesis, room-log receipts, cancel; Team page run panel. Pinned by `test_group_runs.py`. |
| 10 | Agent-to-agent coordination | Done | Thread-scoped roster/inbox/send (`/api/threads/{id}/agent-messages`), group rooms with 5 orchestration modes, bot registry with epoch fingerprints. Pinned by `test_bots_groups_a2a.py`. |
| 11 | Plugin-first + fault isolation | Done | Versioned extension API, placement/isolation, harness→app import firewall (`test_harness_boundary.py`); broken extensions fail closed without taking the Gateway down. |
| 12 | Security + approvals by default | Partial | Sandboxed execution, PAT scopes, CSRF double-submit, secret redaction, role-aware guardrails. Approval surfaces exist; expanding approval policy UI is follow-up. |
| 13 | Observability (goal dashboard) | Done | Overview page (`/workspace/overview`, sidebar entry): runtime, resources, models, console activity, scheduled tasks, team rooms; gateway + network offline banners. Console stats degrade gracefully on non-SQL backends. |
| 14 | Self-healing infrastructure | Partial | `/health` + `/health/ready` probes, bounded shutdown hooks, extension isolation, `make doctor` / `prod-check` / support-bundle. Gap: no continuous resource monitor or supervisor daemon that restarts failed components on its own. |
| 15 | Network failure as pause, not death | Done | Browser-offline banner (`NetworkOfflineBanner`) plus `offlineFirst` query/mutation pausing with reconnect resume; SSE gap recovery with durable reload; bounded retries/backoff; durable tasks outlive outages server-side. Pinned by `tests/unit/core/network/`. |
| 16 | 24/7 + Windows always-on | Done | "Start with Windows" login item with persisted preference + boot-time self-heal (`electron/main.js`), user toggle in Settings → Desktop (`core/desktop`, `desktop-settings-page.tsx`, pinned by `tests/unit/core/desktop/`). Durable scheduler/services then run from logon with no manual launch. |
| 17 | Local + cloud intelligence | Partial | Ollama (keyless) + 15+ cloud providers configurable; setup auto-detects keys/Ollama. Gap: model choice is operator-configured, not automatic per-task. |
| 18 | Resource-aware autonomy | Partial | `GET /api/ops/resources`: stdlib-only CPU/memory/disk/load probe (null-tolerant, never fails), surfaced on the Overview page; pinned by `test_ops_resources.py`. Gap: dynamic concurrency and model sizing from the readings. |
| 19 | Agents repairing their own environment | Planned | Diagnostics exist; agents cannot yet inspect, repair, and verify their own runtime. This is the flagship follow-up once 14 + 18 land. |

## Operating principles

1. User gives goals, not procedures; complexity lives inside the platform.
2. Agents are replaceable. Goals are persistent.
3. A failed strategy triggers replanning, never silent abandonment.
4. Temporary infrastructure failure triggers recovery, not task death.
5. Completed work is checkpointed; important results carry evidence.
6. Components are replaceable and isolated; one failure must not cascade.
7. The system monitors itself and reports only what needs a human.
8. Reliability before raw autonomy — never trade correctness for speed.

## Definition of done

This project is world-class when a normal Windows user can install it,
type one goal, and watch it plan, execute, verify, recover, and complete —
across restarts and failures — needing them only for approvals, genuine
blockers, or exhausted resources. Every row above reads **Done**, verified by
automated tests, `prod-check`, and production builds.

## Out of scope (explicitly unwanted)

- Autonomous financial transactions, irreversible destructive actions, or
  privacy-sensitive operations **without human approval**.
- Multi-tenant SaaS hosting — this is a single-user local platform.
- Silent infrastructure changes outside the app sandbox without consent.
- Claiming autonomy without evidence: every automation claim in the table
  above must stay pinned by a test or a reproducible check.
