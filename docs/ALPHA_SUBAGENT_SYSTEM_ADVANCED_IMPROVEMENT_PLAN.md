# Alpha Subagent System — Advanced Improvement Plan

**Deep research + codebase analysis + phased implementation plan**

| Field | Value |
| --- | --- |
| Document type | Plan (design + research dossier). Changing no source by itself. |
| Created | 2026-10-17 |
| Retrieval date for every external source | 2026-10-17 unless stated inline |
| Code analysis basis | Read-only repository exploration of `main` (`backend/packages/harness/alpha/subagents/`, tool/middleware/gateway/frontend wiring), 2026-10-17 |
| Source plans this extends | `docs/architecture/multi-agent-baseline.md`, `docs/FEATURE_COMPLETION_PLAN.md`, `docs/ADVANCED_MULTI_AGENT_BUILD_PLAN.md`, `docs/ALPHA_ADVANCED_MULTI_AGENT_ARCHITECTURE_PLAN.md`, `docs/WORKFORCE.md` |
| Module authority | `backend/packages/harness/alpha/subagents/AGENTS.md` stays the owner of subagent invariants; this plan never overrides it |

> **Honesty boundary for this document.** Everything in Part A is a statement about
> *code as read today*, not about observed runtime behaviour; no stack was started
> for it. Everything in Part B is an external claim carrying its source and
> retrieval date; vendor-published numbers are labelled vendor numbers. Part C
> verdicts use exactly four labels: **EXISTS**, **PARTIAL**, **ABSENT**,
> **REFUSED** (deliberately not built). Nothing in this plan may be quoted as
> shipped capability until its Phase gate has run and its evidence is written
> down — the same rule `docs/FEATURE_COMPLETION_PLAN.md` §0.1 applies.

---

## 0. How to read this document

1. **Part A** maps what Alpha's subagent system is today — the execution kernel,
   capacity controller, lifecycle plane, batch plane, acceptance layer, event
   plane, and every engine that executes through it.
2. **Part B** is the internet research: orchestrator-worker systems, subagents in
   coding agents, agent teams, framework patterns, failure taxonomies, memory,
   verification scaling, context engineering, cost routing, and interop protocols.
3. **Part C** is the gap matrix: each state-of-the-art capability → Alpha verdict.
4. **Part D** maps the MAST failure taxonomy (the most rigorous published
   failure study) onto Alpha's existing mitigations and this plan's workstreams.
5. **Part E** is the plan itself: 13 workstreams in 5 phases, each with goals,
   file-level actions, tests, acceptance gates, and explicit refusals.
6. **Part F** defines the measurement system. **Part G** is risk. **Part H** is
   the reference list.

Nothing here proposes a second scheduler or a second run-lifecycle owner.
`RunManager` remains the sole run-lifecycle owner; `alpha.mission.acceptance`
remains the only producer of a terminal *passed*; `alpha.errors.registry`
remains the sole owner of error codes; `alpha.config.limits` remains the limit
authority. Every workstream below is additive.

---

## Part A — The subagent system as it exists today (code-evidenced)

### A1. The plane map

| Plane | Owner (paths under `backend/packages/harness/alpha/`) | Role |
| --- | --- | --- |
| Execution kernel | `subagents/executor.py` | `SubagentExecutor`, `SubagentResult`, `SubagentStatus`; process-wide background registry; one persistent isolated event loop; sandbox lease identity; step capture; terminalization; receipts harvesting |
| Admission / capacity | `subagents/capacity.py` | Process-wide `SubagentExecutionCapacity`: default 3 running, bounded queue, priority bands (`PRIORITY_BACKGROUND=0.0`, `FOREGROUND=10.0`, `OPERATOR=20.0`), FIFO tiebreak, `priority_preemptions` metric |
| Lifecycle / control plane | `subagents/lifecycle.py`, `subagents/lifecycle_runner.py` | `SubagentLifecycleManager` state machine (13 statuses), leases, heartbeats, stall detection; `run_registered_subagent(...)` drives real execution for control-plane spawns |
| Durable batches | `subagents/batch_service.py`, `batch_runtime.py`, `batch_acceptance.py`, `persistence/subagent_batches/` | Lease-based durable `batch_task` items through the same executor; per-item acceptance; owner-scoped export |
| Acceptance | `subagents/acceptance_checks.py`, `jev_acceptance.py` | Deterministic leaves (`file:`, `file_written:`, `tests_passed:`) + System One judge (narrowing-only); UNVERIFIED default |
| Definitions / registry | `subagents/config.py`, `registry.py`, `persistence/managed_subagents/` | `SubagentConfig`; resolution order built-in → `custom_agents` → managed → `subagents.agents.<name>` overrides; strict (`extra="forbid"`) managed schema |
| Categories | `subagents/categories.py` | Dispatch-time overlays (`general`, `research`, `quick`) selecting a model chain, skills, tools, budgets |
| Scenario planning | `subagents/scenarios.py`, `plan.py`, `promotion.py` | 8 delegation shapes, advisory/shadow-only `ExecutionPlan`; AST-pinned "cannot dispatch" |
| Deep specialists | `subagents/deep_runner.py`, `deep_handoff_contract.py`, `hierarchical_delegator.py` | Six `deep-*` workers mapped 1:1; unmapped type refused by name; measured-only contract fields |
| Reporting | `subagents/status_contract.py`, `step_events.py`, `report_contract.py`, `token_collector.py` | Terminal ToolMessage metadata; `[rN tool_name]` receipt contract; per-response cumulative token snapshots |
| Ledger | `agents/thread_state.py` (`delegations` + `merge_delegations`), `agents/middlewares/delegation_ledger.py` | The authoritative record of every delegation regardless of stream mode; run-tagged |
| Caps | `agents/middlewares/subagent_limit_middleware.py`, `alpha/config/limits.py` | Per-response concurrency, per-run total (default 6), APEX narrowing; global constants (`MAX_DELEGATION_DEPTH=3`, `MAX_CHILDREN_PER_PARENT=10`, `MAX_FANOUT_PER_NODE=4`, `MAX_DELEGATION_HOPS=6`) |
| Events | `runtime/runs/worker.py::_SubagentEventBuffer`, `runtime/events/catalog.py` | `subagent.start/step/end` persisted rows + `task_*` custom stream events |
| Tools | `tools/builtins/task_tool.py`, `batch_task_tool.py`, `subagent_control_tool.py`, `subagent_registry_tool.py`, `background_tasks_tool.py` | The model-facing surfaces |
| Gateway | `app/gateway/routers/subagents.py`, `subagent_control.py`, `subagent_batches.py` | Catalog + admin CRUD + `/scenarios` + `/plan-preview`; operator control plane |
| Frontend | `SubagentList.tsx`, `agent-ui/SubagentCard.tsx`, `sections/SubagentsSection.tsx`, `sections/DelegatedWorkPanel.tsx`, `lib/subagents.ts`, `lib/sse-reducer.ts` | Live cards, delegation history, control-plane panel |

### A2. What the system does today — the delegation-shape inventory

**Delegation enters through exactly five model-visible or operator-visible surfaces:**

1. **`task`** — the primary path. Enabled by run opt-in (`RunCreateRequest.autonomous`);
   spawns a one-shot isolated child with a fresh `ThreadState`, a report contract,
   optional acceptance criteria, receipts, and terminal metadata. Concurrency and
   per-run total are enforced by `SubagentLimitMiddleware`.
2. **`batch_task` / `batch_status` / `cancel_batch`** — durable, lease-based,
   explicitly-not-inferred work queue with per-item acceptance and owner-scoped export.
3. **`subagent_control` / `POST /api/subagents/control/*`** — the operator plane
   (spawn/list/cancel), executed through `lifecycle_runner` on the isolated loop.
4. **`subagent_registry`** — agent self-service CRUD over managed definitions
   (create/update/delete/list/inspect), constrained by an explicit allowlist so it
   cannot widen itself.
5. **Engine-internal instantiation** — Arena, GRO (`gro/adapters.py`),
   swarm workers, war-room participants, groups/team runner, lifecycle spawns,
   self-improvement, orchestrator domain executors, all constructing the same
   `SubagentExecutor`.

**Consumers of the same kernel** (each with its own scheduler and its own
vocabulary — the measured baseline in `docs/architecture/multi-agent-baseline.md`):

| Engine | Uses the executor for | Notes |
| --- | --- | --- |
| Arena | strategy-carded competitors through a budget-gated bracket | OPERATOR priority band |
| GRO | candidate lanes under distinct strategies, gate-first selection | OPERATOR band; separate store |
| Swarm | task nodes via `swarm/worker.py` | own leases/schedulers |
| War room | real subagent participants, quorum, taped transcript | imports deliberation router only |
| Groups / team runner | one child per room member + moderator synthesis | honors bounds + grace |
| Workforce / scheduled tasks | dispatch reuses the ordinary run path | scheduler decides *when*, never a parallel stack |
| Deep specialists | six `deep-*` workers | measured-only contract fields |
| Batch | durable items | separate budgets under `subagent_batches` |

### A3. Load-bearing invariants (must not break — from the module guide and tests)

1. **Identity split.** Provider `tool_call_id` = `external_task_id` (correlation
   key); executor-minted `execution_id` = `task_id` (registry ownership key).
   Provider IDs never become registry keys.
2. **Checkpointer isolation & lineage.** Children compile with
   `checkpointer=False`; checkpoint coordinates are inherited, never re-supplied.
3. **One capacity controller.** Only `executor.py` calls `.slot()`; every engine
   inherits the same admission control.
4. **Additive contracts.** `subagent_status_contract.json` v2 changed by adding
   optional fields, never by adding statuses; unknown statuses are never green.
5. **`null` is not `false`.** A failed read renders "unknown", never "absent";
   acceptance never implies execution success and vice versa.
6. **Measured-only fields.** Deep contract fields fill only from real
   measurements; a capped completion is `PARTIAL_PROGRESS`.
7. **Shadow-only planner.** `plan.py`/`scenarios.py` import no engine (AST-pinned).
8. **Budget authority.** `alpha.config.limits` is the ceiling; only lowering is
   allowed downstream; every cap closure is startup-only where documented.

### A4. Measured gaps and open items (carried forward from audits + code reading)

| # | Gap | Evidence |
| --- | --- | --- |
| G1 | **Two execution-record planes remain disjoint.** `task` writes `ThreadState.delegations` + events; the control plane registers only operator spawns. `SubagentsSection` "Running now" reads the control plane, so ordinary delegation history is invisible there. | `docs/audits/SUBAGENT_VISIBILITY.md` (Option 1 open); `SubagentsSection.tsx` reads control plane |
| G2 | **A single 3-slot global pool is the throughput ceiling** for every engine simultaneously (task, batch, swarm, war room, GRO, arena, teams). Priority reorders admission; nothing preempts; `priority_preemptions` is the only contention signal. | `capacity.py` defaults; `alpha/config/limits.py` |
| G3 | **`SubagentResult` carries no artifact manifest.** Terminal metadata carries receipts + usage + brief, but no verified list of produced files with hashes. | `docs/FEATURE_COMPLETION_PLAN.md` §3.T2 note "`artifacts` left unset" |
| G4 | **No structured-output contract for children.** Results are prose + acceptance verdict; there is no optional typed result schema the parent can rely on. | `status_contract.py`, `task_tool.py` |
| G5 | **No mid-flight interaction.** Once dispatched, a child cannot receive a follow-up message or steering; only cancel/timeout exist. `subagents/messaging.py` and `yield_handoff.py` exist as primitives without a production tool surface. | `background_tasks_tool.py` (await/cancel only); module inventory |
| G6 | **Queue-wait visibility is minimal.** No wait-time distribution; an operator cannot see why a delegation queued for 4 minutes. | `capacity.py` snapshot fields |
| G7 | **No cost-aware model policy for delegation.** `model` per definition + category chains + fallback exist; there is no policy that starts cheap and escalates on measured failure, and no per-delegation cost reporting joined to acceptance. | `categories.py`, `models/factory.py`, `models/cost_governor.py` |
| G8 | **Verification is single-layer for judgments.** Deterministic leaves are strong; the judge path is "System One narrowing-only". No aspect-verifier layer (multiple independent checkers) exists for open-ended outputs. | `acceptance_checks.py`, `jev_acceptance.py` |
| G9 | **No delegation-catalog progressive disclosure.** The whole enabled catalog is rendered to the lead prompt; there is no deferred-discovery mode like `skills.deferred_discovery`. | `registry.py`, lead prompt assembly |
| G10 | **No learning loop over delegation outcomes.** Failures inform the ledger and stop reasons, but nothing distills recurring failure patterns into definition proposals. | `delegation_ledger.py`; no consumer |
| G11 | **Nested delegation is deliberately single-level.** Default tool allowlist denies `task`; `MAX_DELEGATION_DEPTH=3` exists in the limits authority but the ordinary path never uses it. | `config.py` defaults; `limits.py` |
| G12 | **Interop is scoped but not wired for delegation.** A2A v1.0 exists as a declared scope in `docs/PROTOCOLS.md`; the peer network is `alpha-a2a`-shaped but wire-incompatible. Remote subagents (Alpha as A2A client/server for the subagent plane) do not exist. | `docs/PROTOCOLS.md` §1, §7 |

Each gap becomes a workstream in Part E (mapping in Part C).

---

## Part B — State of the art, researched 2026-10-17

### B1. Orchestrator-worker (Anthropic's multi-agent research system)

The reference implementation of lead+subagent delegation for open-ended work.
Architectural facts and lessons:

- A lead agent plans and spawns **3–5 subagents in parallel**, each with a
  self-contained objective, an output format, a tool list, and a clear boundary
  ("don't research X — that's another subagent's job").
- Lead (Opus 4) + Sonnet 4 subagents **outperformed single-agent Opus 4 by 90.2%**
  on Anthropic's internal research eval (vendor-published).
- **Cost is the honest headline: orchestration consumes roughly 15× the tokens of
  a single chat turn** — the plan, every worker's context, and synthesis all bill
  separately. Token usage alone explained ~80% of performance variance in their
  analysis of BrowseComp.
- The failure they hit first was **vague delegation prompts**: subagents duplicated
  searches or left gaps. The fix was prompt engineering of the *lead's delegation
  behavior*, evaluated with an LLM-as-judge over hundreds of traces.
- They explicitly advise: use multi-agent **only when** the task decomposes into
  independent threads; shared-state tasks break under isolation.
  ([Anthropic engineering post](https://www.anthropic.com/engineering/multi-agent-research-system);
  analysis: [The AI Engineer](https://theaiengineer.substack.com/p/how-anthropic-built-multi-agent-deep))

**Alpha relevance:** Alpha already has the shape (lead + `task`), the caps, and
an evaluated routing prompt (`test_subagent_routing_prompt.py`). What is missing
is the *measurement loop* around delegation quality (W1) and cost-aware routing
(W9).

### B2. Claude Code: subagents and Agent Teams

Claude Code's subagent feature (a markdown-defined specialist with `description`,
`prompt`, `tools`, and per-subagent `model`) is the most widely adopted subagent
surface in the coding domain. In 2026 Anthropic layered **Agent Teams** above it
(research preview, disabled by default behind
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`): multiple sessions that *coordinate
autonomously*, communicate peer-to-peer, and share a task list — positioned for
work needing inter-agent coordination, while "lightweight delegation: subagents
spawn helper agents for research or verification within your session".
([Claude Code agent teams docs](https://code.claude.com/docs/en/agent-teams);
[SDK subagent docs](https://code.claude.com/docs/en/agent-sdk/subagents))

**Alpha relevance:** Alpha's hierarchy is subagents (task) → groups/war-room
(teams). The team features exist but are scheduler-owned elsewhere; the plan's
W13 proposes a shared task list on existing primitives rather than a new team
runtime.

### B3. VS Code multi-agent surface (parallel subagents + visibility)

VS Code's 2026 multi-agent release made "multiple subagents in parallel" a
first-class editor feature and — the part worth copying — invested in **better
visibility into what subagents are doing**, under one "Agent Sessions" view.
([VS Code blog, 2026-02-05](https://code.visualstudio.com/blogs/2026/02/05/multi-agent-development))

**Alpha relevance:** W2 is exactly this: one read model for delegation, no
disjoint planes.

### B4. LangChain Deep Agents: subagents as middleware

Deep Agents ships subagents as a **middleware** (`middleware/subagents.py`) that
adds a `task` tool; dynamic subagents entered via a code-interpreter/REPL
middleware; v0.6 added **subagent-aware streaming** (`stream.subagents` yielding
per-subagent status and message deltas — the same problem Alpha solves with
`task_*`/`subagent.*` events). Their "subagents pattern" guidance names the
tradeoff Alpha already encodes: centralized orchestration, strong context
isolation, **one extra model call per interaction**, and subagents stateless.
([Dynamic subagents](https://www.langchain.com/blog/introducing-dynamic-subagents-in-deep-agents);
[Deep Agents v0.6](https://www.langchain.com/blog/deep-agents-0-6);
[Choosing the right multi-agent architecture](https://www.langchain.com/blog/choosing-the-right-multi-agent-architecture))

**Alpha relevance:** Alpha's `deepagent` working plane already ports their state
backend. W5 extends that parity to child context engineering; W13's team ideas
stay subordinate to the existing engines.

### B5. OpenAI Agents SDK: handoffs vs agents-as-tools

The SDK's core primitives are Agent, Runner, tools, **handoffs**, **guardrails**,
**sessions**, tracing. The documented distinction matters: a *handoff* transfers
control of the conversation to another agent (the specialist owns the turn); an
*agent-as-tool* returns control to the caller. Sessions persist conversation
state across turns. ([SDK docs](https://openai.github.io/openai-agents-python/);
[framework analysis](https://turion.ai/blog/framework-deep-dive-openai-agents-sdk/))

**Alpha relevance:** Alpha's `task` is strictly agent-as-tool. `yield_handoff.py`
and `messaging.py` are the seams where a handoff-style mode could live without
touching the task contract (W6).

### B6. Microsoft Magentic-One: ledgers, stall detection, re-planning

Magentic-One's Orchestrator keeps two ledgers: an outer **Task Ledger** (facts,
guesses, plan) and an inner **Progress Ledger** (self-reflection per step:
"Task complete? Progress being made?"). **If no progress for enough steps (stall
count > 2), it updates the Task Ledger and re-plans.** Agents are model-agnostic
and heterogeneous (strong reasoning model for the orchestrator, cheaper for
others). ([Microsoft Research](https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/);
[AutoGen docs](https://microsoft.github.io/autogen/stable//user-guide/agentchat-user-guide/magentic-one.html))

**Alpha relevance:** Alpha's lead has a delegation ledger and `LoopDetectionMiddleware`,
but no *progress-ledger* notion over delegations (stall → re-plan). W1's telemetry
and W10's lessons loop are the prerequisites; a lead-side progress ledger is a
Phase 3 candidate (W13-adjacent, measured-only).

### B7. AWS Strands: agents-as-tools, swarm, graph

Strands 1.0 ships three first-class multi-agent patterns: **agents-as-tools**,
**Swarm** (handoff-based with `max_handoffs`, `max_iterations`, `execution_timeout`,
`node_timeout`), and **Graph** (typed nodes/edges/topologies, including mesh, via
`agent_graph`). Everything is bounded: handoff counts and timeouts are parameters,
not vibes. ([Strands 1.0](https://aws.amazon.com/blogs/opensource/introducing-strands-agents-1-0-production-ready-multi-agent-orchestration-made-simple/);
[technical deep dive](https://aws.amazon.com/blogs/machine-learning/strands-agents-sdk-a-technical-deep-dive-into-agent-architectures-and-observability/))

**Alpha relevance:** Alpha's swarm/graph planes already exist; the lesson is that
**every coordination bound travels as a parameter with a refusal on exhaustion** —
which Alpha's limits authority already enforces. W6/W13 reuse that discipline.

### B8. The MAST failure taxonomy — why multi-agent systems fail

The first empirically grounded taxonomy: 14 failure modes in 3 categories —
(i) **system design issues** (specification ambiguity, role disobedience, step
repetition), (ii) **inter-agent misalignment** (information withholding,
ignored input, reasoning-action mismatch), (iii) **task verification** (premature
termination, incomplete verification, incorrect verification). Built from 1,600+
annotated traces across 7 MAS frameworks; inter-annotator κ = 0.88; LLM annotator
agreement κ = 0.77. Headline numbers: **41%–86.7% failure rates** on the studied
MAS; workflow/prompt interventions improved results by at most **+15.6%**,
which the authors read as evidence that most failures need structural fixes, not
better prompts. They also released `agentdash` (pip) for annotating your own
traces. ([arXiv 2503.13657](https://arxiv.org/abs/2503.13657);
[project page](https://sky.cs.berkeley.edu/project/mast/);
[GitHub](https://github.com/multi-agent-systems-failure-taxonomy/MAST))

**Alpha relevance:** this is the single most useful external artifact for this
plan. Part D maps it directly.

### B9. Verification and test-time scaling

- **BoN-MAV (Multi-Agent Verification):** scale the *number and type of verifier
  models* ("aspect verifiers") rather than only samples; combining weak verifiers
  improves even strong models; self-improvement possible with one base model
  generating and verifying. ([OpenReview](https://openreview.net/forum?id=LriQ3NY9uL);
  [paper list](https://github.com/ThreeSR/Awesome-Inference-Time-Scaling))
- **Verifier-augmented best-of-N in deep research:** verifier scaling produced
  8–11% accuracy gains on GAIA/XBench-DeepResearch subsets with capable
  models. ([arXiv 2601.15808](https://arxiv.org/html/2601.15808v1))
- **Structured test-time scaling theory:** topology compression, scope isolation,
  and **decoupled verification** as the three levers of hierarchical MAS —
  the theoretical case for exactly what Alpha's acceptance layer does.
  ([summary](http://xinmingtu.cn/blog/2026/hierarchical-mas-theory/))
- **Inference-scaling caveat:** resampling with imperfect verifiers can fail to
  help ([Inference Scaling Flaws](https://arxiv.org/abs/2411.17501)) — so a
  verifier-only workstream needs measured gates, never assumed wins.

**Alpha relevance:** W8 is this, constrained to Alpha's honesty rules: aspect
verifiers may only *narrow* (never upgrade), cite receipts, and default off.

### B10. Context engineering (Manus) and compaction

The Manus lessons are now canonical:

- **Design around the KV-cache**: append-only context, stable serialization,
  no dynamic timestamps mid-context; cache hits cut cost ~10× in their report.
- **Mask, don't remove**: mutating tool definitions invalidates the cache for
  everything after; they constrain action space by logit masking instead.
- **Filesystem as context**: externalize memory to files with *restorable*
  compression (keep the URL, drop the page) and let the agent rewrite a todo file
  for recitation.
  ([Manus post](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus))
- **Compaction**: Anthropic's cookbook quantifies it — peak context 335k tokens
  baseline vs 169k with compaction on the same task, at the cost of a summary
  destruction event. ([Cookbook](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools))
- **Context rot**: performance degrades with context length across models, so
  "bigger window" is not a strategy. ([Chroma research](https://research.trychroma.com/context-rot))

**Alpha relevance:** subagents are Alpha's primary context-isolation tool; W5
makes each child's context discipline explicit (stable prefixes, one-shot date
injection already; working-plane memory inheritance already exists via
`DeepAgentContextMiddleware`; per-category compaction policy is new).

### B11. Memory architectures

- **MemGPT/Letta**: OS-style tiering (core/recall/archival), agent-managed paging.
- **Mem0**: extract → consolidate → retrieve; hybrid vector+graph; vendor-reported
  91% lower latency and ~90% token savings vs full-context on LoCoMo.
  ([Mem0 state of memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026))
- **Zep/Graphiti**: temporal knowledge graph — every fact has validity windows
  and supersession. ([paper](https://arxiv.org/abs/2501.13956))
- **LongMemEval-V2**: extends memory benchmarks to *agentic* abilities —
  workflow knowledge, environment gotchas, premise awareness — and finds current
  systems fail to internalize operational experience. This is the strongest
  published case for per-*specialist* procedural memory.
  ([LongMemEval-V2 overview](https://agentic-ai.readthedocs.io/en/latest/AgentMemory/research-papers/))
- **Infini Memory (2026)**: topic-document memory with buffered writes + periodic
  consolidation; names four failure modes of append-only memory (conflict,
  drift, etc.). ([arXiv 2606.10677](https://arxiv.org/html/2606.10677v1))

**Alpha relevance:** subagents intentionally do **not** write the parent thread's
memory (`skip_memory_flush=True`), and the cognitive-memory guide owns semantics.
W10 is the careful inverse: delegation *lessons* distilled into reviewer-gated
proposals, not silent memory writes.

### B12. Self-improvement loops

- **Reflexion**: per-attempt natural-language reflection buffer feeding retries.
- **ExpeL**: extract insights from success/failure trajectories; reuse as
  in-context examples.
- **Darwin Gödel Machine**: agents edit their own scaffolds; cross-benchmark
  transfer reported (28.9% on Polyglot vs 14.2% starting agent — paper claim).
- **PILOT (2026)**: live, in-run self-improvement for long-horizon agents.
- **Counterpoint**: single-agent self-reflection can anchor on its own local
  optimum; multi-perspective reflection helps.
  ([survey](https://furong-huang.com/blog/self-improving-agents-learning-how-to-work/);
  [o-mega 2026 guide](https://o-mega.ai/articles/self-improving-ai-agents-the-2026-guide);
  [PILOT](https://arxiv.org/html/2608.26530v1))

**Alpha relevance:** Alpha's stance is stronger than most vendors': self-repair is
refusal-only until independent post-action verifiers exist; skills already have a
proposal→approve pipeline. W10 reuses that pipeline for subagent definitions.

### B13. Parallel coding agents and workspace isolation

The engineering community converged on **git worktrees + one container per
agent** as the isolation primitive for parallel coding agents (Simon Willison's
"parallel coding agent lifestyle" and the Rover/Shards tooling that automates
it). A widely read experiment orchestrated **200+ parallel Claude Code
instances** in isolated worktrees with DAG task decomposition and three-loop
failure recovery ("queen/drone" pattern).
([Simon Willison](https://simonwillison.net/2025/Oct/5/parallel-coding-agents/);
[HN thread](https://news.ycombinator.com/item?id=45489884);
[200-agents write-up](https://bhavishyapandit9.substack.com/p/ai-code-generation-claude-thinking))

**Alpha relevance:** Alpha already has `sandbox/worktrees.py::WorktreeManager`
and `merge_simulation.py`; W7/W8 connect them to delegation outcomes (artifact
manifests; optional non-conflict checks for parallel writers).

### B14. Model routing and the economics of delegation

- **Orchestrator-worker is a quality pattern, not automatically a cost pattern.**
  Vendor-published BrowseComp result: a strong lead with Sonnet workers reached
  **96% of the score at 46% of the cost** (86.8% @ $18.53 vs 90.8% @ $40.56).
  A counter-datapoint from an Anthropic staffer's July 2026 experiments: mixing
  models added a **60% markup for no measured benefit** on his workload.
  ([cost analysis](https://claudefa.st/blog/guide/development/multi-agent-orchestration-cost))
- **Routing** (one model per request by difficulty) is the cost lever:
  RouteLLM measured **3.66× cheaper at 95% of GPT-4 quality** on MT-Bench (2024
  pricing); FrugalGPT's cascades reached up to **98% lower cost** matching GPT-4
  on its benchmarks. ([RouteLLM](https://arxiv.org/html/2406.18665v4);
  [FrugalGPT](https://arxiv.org/abs/2305.05176))
- **AOrchestra (2026)**: automating sub-agent creation with a *learnable*
  orchestrator — SFT for orchestration +11.51%; cost-aware in-context routing
  −18.5% average cost at +3.03% GAIA pass@1.
  ([arXiv 2602.03786](https://arxiv.org/html/2602.03786v2))
- **Break-even rule of thumb**: a delegation only pays when the worker absorbs
  enough context to beat the handoff overhead; short tasks live below break-even.

**Alpha relevance:** W9 implements this as a bounded, measured policy: cheap-tier
first for bounded subtasks, escalation on *measured* failure, every escalation
counted against existing caps; zero-cost constraints respected; no claim of
savings without the per-task join of cost and acceptance (W1).

### B15. Interop: MCP, A2A, Agent Skills

- **MCP** is at spec revision `2026-07-28` (stateless core, no handshake).
  Alpha speaks `2025-11-25` — measured and recorded in `docs/PROTOCOLS.md`.
- **A2A** reached v1.0 on 2026-03-12 (v1.0.1 May 2026), hosted by the Linux
  Foundation, 150+ supporting organisations, in production across Google/Microsoft/
  AWS surfaces; LangGraph and CrewAI both ship integrations. "MCP for tools,
  A2A for agents" is the converged mental model.
  ([LF press](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year);
  [protocol guide](https://www.turingpost.com/p/a2a))
- **Agent Skills** became an open standard (`SKILL.md`, published 2025-12-18 at
  agentskills.io): three-stage progressive disclosure (name+description →
  SKILL.md body → bundled files), now the package format across vendors.
  ([Anthropic](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills);
  [ecosystem report](https://agentman.ai/blog/agent-skills-ecosystem-report-2026))

**Alpha relevance:** W4 applies the skills' progressive-disclosure pattern to the
subagent catalog (Alpha already has `skills.deferred_discovery`); W12 scopes A2A
for the subagent plane without claiming wire compatibility before conformance.

---

## Part C — Gap matrix (SOTA → Alpha verdict)

Verdicts: **EXISTS** (built and reachable), **PARTIAL**, **ABSENT**,
**REFUSED** (deliberately not built; keep it that way unless the named
precondition changes).

| # | SOTA capability | Source | Alpha today | Verdict | Workstream |
| --- | --- | --- | --- | --- | --- |
| C1 | Lead delegates 3–5 parallel isolated children with explicit boundaries | Anthropic | `task` + parallel fan-out + routing prompt + caps | **EXISTS** | — |
| C2 | Delegation quality measured on real traces (LLM judge) | Anthropic, MAST | Acceptance leaves + ledger; no trace-level delegation eval harness | **PARTIAL** | W1 |
| C3 | Token/context cost accounting per delegated task | Anthropic | `SubagentTokenCollector` → terminal metadata + events | **PARTIAL** (no per-task $ join) | W1, W9 |
| C4 | One visibility plane for "what are my agents doing" | VS Code, Claude teams | Two disjoint planes (G1) | **PARTIAL** | W2 |
| C5 | Queue/latency observability under contention | Strands bounds, Magentic stall | `priority_preemptions` only (G6) | **PARTIAL** | W3 |
| C6 | Per-subagent model choice | Claude Code, Magentic-One, all | `SubagentConfig.model`, category chains, fallbacks | **EXISTS** | — |
| C7 | Cost-aware routing / cascades with escalation | RouteLLM, FrugalGPT, AOrchestra | None (G7) | **ABSENT** | W9 |
| C8 | Progressive disclosure of the agent/skill catalog | Agent Skills | Skills yes; subagent catalog always full (G9) | **PARTIAL** | W4 |
| C9 | Mid-run interaction with a child (follow-ups, steering) | OpenAI handoffs, Claude teams | cancel/timeout only (G5) | **ABSENT** | W6 |
| C10 | Handoff-style control transfer | OpenAI SDK | `yield_handoff.py` primitive, no tool surface | **PARTIAL** | W6 |
| C11 | Artifact manifests with verified hashes from children | Coding-agent practice | Receipts at runtime, no terminal artifact list (G3) | **ABSENT** | W7 |
| C12 | Typed structured outputs for delegation | SDKs/pydantic practice | Prose + acceptance only (G4) | **ABSENT** | W7 |
| C13 | Multi-verifier / aspect-verifier scaling | BoN-MAV, arXiv 2601.15808 | Deterministic leaves + narrowing judge (G8) | **PARTIAL** | W8 |
| C14 | Decoupled verification of delegated work | Structured TTS theory | Acceptance layer, demoted-never-promoted | **EXISTS** | W8 (extend) |
| C15 | KV-cache-aware context discipline | Manus | Static prefix + one-shot date; no cache-stability test | **PARTIAL** | W5 |
| C16 | File-system-as-context for children | Manus, Deep Agents | `deepagent` working plane inherited by children | **EXISTS** | W5 (parity) |
| C17 | Compaction policy per work class | Anthropic cookbook | One global `summarization` switch for both chains | **PARTIAL** | W5 |
| C18 | Per-specialist operational memory (workflow knowledge) | LongMemEval-V2, Infini | None (children are stateless by design) | **ABSENT** | W10 (proposals only) |
| C19 | Experience distillation into improved agent configs | ExpeL, DGM | Skill proposals pipeline exists; unused for subagents (G10) | **PARTIAL** | W10 |
| C20 | Stall → re-plan ledger on the orchestrator | Magentic-One | Loop detection + run stall watchdog; no progress ledger | **PARTIAL** | W13 (stretch) |
| C21 | Bounded swarm/handoff primitives with explicit timeouts | Strands | Swarm/groups/war-room own theirs | **EXISTS** | — |
| C22 | Worktree isolation for parallel writers | Community practice | `WorktreeManager` + `MergeSimulation` exist | **PARTIAL** (not wired to `task`) | W7, W8 |
| C23 | Remote agents over an open protocol (A2A) | A2A v1.0 | Declared scope only; peer net wire-incompatible (G12) | **ABSENT** | W12 |
| C24 | Skills delivered over MCP / portable skill packs | agentskills.io, Skills-over-MCP | Skills Hub + installer exist; standard alignment partial | **PARTIAL** | W12 |
| C25 | Nested subagents (child spawns child) | Claude Code (enforced single-level), DGM | Deliberately denied; depth constant exists unused (G11) | **REFUSED** → optional W11 | W11 |
| C26 | Shared task list for a team working one objective | Claude teams | `todos` (lead-only) + groups/war-room transcripts | **PARTIAL** | W13 (stretch) |
| C27 | Batched durable delegation queue | Batch APIs (OpenAI/Anthropic) | `batch_task` + leases + acceptance | **EXISTS** | — |
| C28 | Security: prompt-injection-resistant delegation | OWASP LLM01 practice | Input sanitization + untrusted-channel acceptance + report contract | **EXISTS** | — |

---

## Part D — MAST mapping: failure modes → Alpha today → this plan

MAST's three categories map cleanly onto Alpha's three strongest invariants and
its three weakest measurement points. (Modes quoted from the taxonomy; the
"Alpha today" column cites the owning mechanism.)

| MAST category | Representative failure modes | Alpha mitigation today | Residual risk → workstream |
| --- | --- | --- | --- |
| **FC1 System design issues** | Ambiguous specifications; disobey role spec; step repetition | `SubagentConfig` (tools/skills/model bounded); `SubagentLimitMiddleware`; `LoopDetectionMiddleware` hard-stop (`loop_capped`); strict managed schema | Ambiguity of *delegation prompts* is unmeasured → W1 evaluates delegation phrasing; W4 capability digests; W10 lessons |
| **FC2 Inter-agent misalignment** | Information withholding; ignored input; reasoning–action mismatch | Fresh child state; explicit upload boundary; ledger as authoritative record; report contract with receipts; `[rN]` citations | No mid-flight correction channel (G5) → W6; no progress ledger → W13 |
| **FC3 Task verification** | Premature termination; incomplete verification; incorrect verification | Acceptance leaves (deterministic), UNVERIFIED default, demote-never-promote, `stop_reason` honesty, deep-runner `PARTIAL_PROGRESS` | Single verifier layer for judgments (G8) → W8 aspect verifiers |
| **Cross-cutting cost/latency** | (Not a MAST mode; vendor-documented) | Caps + priority bands + token accounting | No cost-aware routing (G7) → W9; queue visibility (G6) → W3 |

**Doctrine extracted from MAST for every workstream below:** a structural fix
beats a prompt fix; an unmapped state is a refusal, never a default; and
verification weakness is the most fixable category — which is why W8 is
sequenced early despite being "advanced".

---

## Part E — The plan

### E0. Design principles (apply to every workstream)

1. **Measurement precedes capability.** No workstream ships without a W1-style
   measured baseline and a negative-controlled gate.
2. **Additive contracts only.** New statuses are new optional fields, never new
   enum members (`subagent_status_contract.json` discipline).
3. **Refusal over defaults.** Unknown scenario, unknown tier, unknown band,
   unmapped status → named refusal.
4. **One owner per fact.** Ledger owns delegation history; capacity owns
   slots; limits own ceilings; acceptance owns verdicts. New projections read,
   never fork.
5. **Default off for behavior-changing cognitive features** (aspect verifiers,
   routing policy, proposal learning), default unchanged for everything else.
6. **The engine never fabricates.** Anything unmeasured is `null`/UNVERIFIED.
7. **New capabilities ride existing tools.** Extend `task`,
   `background_tasks_tool`, `subagent_control`, `subagent_registry` with actions —
   no tool-count churn unless the manifest and every count consumer update in the
   same change (root `AGENTS.md` rule).

### Phase overview

| Phase | Workstreams | Exit criteria (all with evidence) |
| --- | --- | --- |
| **P0 — Measure** | W1 (telemetry/eval harness), W3.a (queue metrics) | A single command produces a delegation report; every field is measured or explicit `null`; report schema pinned by tests |
| **P1 — Visibility & throughput** | W2 (one read model), W3 (scheduling), W7 (artifacts) | Panel shows both planes with honest failure states; queue wait p50/p95 visible; artifact manifests verified (existence + hash) |
| **P2 — Context & interaction** | W4 (catalog disclosure), W5 (context policy), W6 (follow-ups) | Catalog digest mode tested; prefix-stability test passes; follow-up delivery reaches a running child at a safe point and is ledgered |
| **P3 — Intelligence** | W8 (aspect verifiers), W9 (routing), W10 (lessons), W11 (nested, gated) | Aspect verifiers narrow-only with receipts; routing policy refuses unknown tiers; lessons land as proposals; nested delegation off by default and gated by depth tests when on |
| **P4 — Teams & interop** | W12 (A2A/MCP scope), W13 (team task list, progress ledger) | A2A adapter passes protocol conformance or stays labelled non-conformant; shared task list is read-model first, never a second scheduler |

---

### W1 — Delegation telemetry and evaluation harness (Phase 0)

**Goal.** One reproducible command that dispatches a catalogue of real tasks
through each delegation surface (`task`, `batch_task`, control spawn) and emits a
report joining: outcome, `stop_reason`, tokens (per model), wall time, queue wait,
acceptance verdicts, receipts, artifacts, and event persistence — each field
measured or `null`.

**Research anchors.** Anthropic's LLM-judge loop (B1); MAST's annotator pipeline
(B8); `docs/FEATURE_COMPLETION_PLAN.md` §0.1 method.

**Current state.** `test_subagent_events_e2e.py` proves event precondition;
`token_collector.py` publishes usage; `acceptance_checks.py` produces verdicts.
Nothing joins them into one artifact.

**Changes.**
- New `backend/scripts/subagent_delegation_probe.py` (pattern:
  `backend/scripts/realtime_gateway_check.py`): boots a real Gateway OR runs an
  embedded `AlphaClient` with real `config.yaml`; runs a small pinned scenario
  catalogue; writes JSON + markdown under `.alpha/subagent_probe/<run-id>/`.
- Scenario catalogue v1 (each with a declared expectation): direct answer,
  single delegation, 3-way parallel fan-out, quick-category capped run,
  turn-capped recovery, acceptance `tests_passed:` pass and fail,
  batch flush + cancel, control-plane spawn visible.
- A pure report-schema module under `backend/tests/support/` (or
  `scripts/support/`) with a dataclass + tests; the probe fails on schema drift.
- Optional: `pip install agentdash` adoption is **out of scope** (third-party
  dependency decision belongs to its own review).

**Tests.** `backend/tests/test_subagent_delegation_probe_schema.py` (offline:
report schema, null-honesty — an unmeasured field must serialize as `null`, not
`0`/`""`); a negative control that deletes a captured field and fails.

**Acceptance gates.** D1 real execution (live probe run recorded); D3 negative
control; D4 break the Gateway mid-probe and confirm the report says "incomplete
with reason", not success.

**Refusals.** No assertions about model quality inside the probe; it measures
*plumbing*, not intelligence. No new runtime dependency.

#### W1 implementation record (measured 2026-10-17)

**Status: implemented; live evidence recorded; all 11 catalogue scenarios
measured at least once.** Six live runs produced reports — two full-catalogue
attempts were interrupted mid-flight by host events (below), and the remaining
scenarios were measured in chunks. Reports are written under
`backend/.alpha/subagent_probe/<run-id>/` (gitignored); run inventory, all
times UTC:

| tag | run id | note |
| --- | --- | --- |
| R-pre | `20261017T141735Z-18e92662` | before the `autonomous` opt-in (verdict `complete`) |
| R-sub | `20261017T142149Z-007ab1a7` | post-opt-in subset, 2 scenarios (verdict `complete`) |
| R-a | `20261017T150649Z-78928464` | full catalogue attempt 1 — killed at scenario 8 (verdict `incomplete`) |
| R-b | `20261017T152159Z-060ca2ad` | full catalogue attempt 2 — killed at scenario 7 (verdict `incomplete`) |
| R-c | `20261017T160443Z-dab4acc7` | light chunk (verdict `incomplete`) |
| R-d | `20261017T160558Z-a2ecfe6d` | tail chunk, 3 scenarios (verdict `complete`, exit 0) |

**Artifacts.** Probe `backend/scripts/subagent_delegation_probe.py` (attach
mode: `--base-url`, default `http://127.0.0.1:8001`); pure report schema
`backend/scripts/support/subagent_probe_report.py`; offline suite
`backend/tests/test_subagent_delegation_probe_schema.py` (35 tests); operator
guide `backend/scripts/AGENTS.md`. One command from `backend/`:
`uv run --no-sync python scripts/subagent_delegation_probe.py`;
`--scenarios <keys>` runs a chunk; `--list` prints the catalogue. Exit codes:
`0` complete, `1` incomplete, `2` schema violations. `queue_wait_seconds` stays
`null` everywhere (`UNINSTRUMENTED_FIELDS`) until W3.a instruments it.

**The `autonomous` opt-in is load-bearing.** R-pre measured **0 delegations**
on `single_delegation`: without `autonomous=true` the server never applies
`subagent_enabled` (it is sanitised out of ordinary client context), so the
`task` tool is absent and a zero-delegation measurement is a property of the
request, not of the model. Every later run opts in; R-sub then met both
`direct_answer` (0 delegations) and `single_delegation` (1 delegation,
`alpha-free`, 22 089 tokens, 157.8 s).

**Measured per scenario** (best verdict across runs; a finding, not an
endorsement):

| scenario | verdict | measured notes |
| --- | --- | --- |
| `direct_answer` | met / unmet | met with 0 delegations (R-sub); unmet with exactly 1 delegation in both full attempts (R-a, R-b) — over-delegation on an explicitly direct prompt is model behaviour, not plumbing |
| `single_delegation` | met | 1 delegation; the child reported provider usage; model `alpha-free` |
| `parallel_fanout_3` | met | 3 delegations completed; measured overlap: 3 task pairs with intersecting event windows |
| `quick_category` | met | quick-category delegation completed via `alpha-free` |
| `turn_capped` | met | `stop_reasons=['turn_capped']` with run status `error` — the cap fired and is attributable |
| `acceptance_tests_pass` | unmet | `tests_passed` landed UNVERIFIED (`checked=false`): "no matching bash execution recorded"; R-b also hit the 420 s stream/poll deadline with the run possibly non-terminal |
| `acceptance_tests_fail` | unmet | the delegation ledger carried no acceptance verdict (2 delegations, R-a) |
| `acceptance_file_pass` | unmet | artifact `probe_accept_pass.txt` was **created** (artifacts row) yet `file_written` landed UNVERIFIED: "file size could not be established by a bounded probe; content not read" (R-d) |
| `acceptance_file_fail` | unmet | same UNVERIFIED shape for the negative case (R-d) |
| `batch_flush_cancel` | refused | batches disabled on this deployment (`{"enabled": false, "repository_available": true, "worker_running": false, "max_running": 3}`); `refused` is recorded and never changes the exit code (R-c) |
| `control_plane_spawn` | met | terminal control record (`completed`, result summary `CONTROL-PROBE`), visible in the control listing (R-d) |

**D1** — live runs recorded (R-sub, R-a, R-b, R-c, R-d above). **D3** —
offline: 35 tests pass (`uv run --no-sync pytest
tests/test_subagent_delegation_probe_schema.py -q`), covering the schema
round-trip, `null`-vs-`0` honesty, outcome/expectation consistency, exit-code
mapping, the delete-a-captured-field negative control, the evaluators, and the
crash path; `ruff check` / `ruff format --check` clean.

**D4 — target death mid-probe (satisfied by live kills).** Three unplanned
kills hit the probe mid-run; each produced a written `incomplete` report naming
a transport reason with exit `1` — never success, never a probe-bug `error`:

1. the self-healing watchdog killing the port-8001 tree (20:47:35 local) —
   R-a `acceptance_file_pass`: "event stream … broke mid-run: `ReadError`";
2. a host network event plus stack restart (21:12) — R-b
   `acceptance_tests_fail`: "POST /api/threads transport failure: `ReadError`";
3. a launcher/watchdog port crossfire (21:34) — R-c `control_plane_spawn`:
   "`RemoteProtocolError`: Server disconnected without sending a response".

The first full-catalogue attempt died differently, and that is the finding that
mattered: the trailing `GET /api/ops/version` was not transport-guarded, so a
dead target turned the run into a traceback with nothing on disk (host TCP
ephemeral-port exhaustion; Windows Tcpip event 4231 at 20:00:34). The version
read is now best-effort and a dead target always yields a written report —
pinned by `test_d4_a_target_that_dies_mid_probe_still_yields_a_written_report`
— and every later kill produced one. The synthetic variant (an isolated second
Gateway `taskkill`ed mid-stream) was prepared but not run: the organic kills
are the stronger evidence, and a second Gateway on this host would have
duplicated the operator's IM channel connections for the duration.

**Handed to the next workstreams.** (a) Over-delegation on a direct prompt is
measurable and belongs in the W5/W9 evidence base. (b) The acceptance plane's
evidence gap is symmetric: positive and negative `file_written` and
`tests_passed` criteria all land UNVERIFIED — in one case the artifact exists
while the verifier cannot bound-check it — which is W8's mandate. (c)
Free-router provider failures surface inside scenarios as
`model_failure_recovery` / `delivery_incomplete` SSE error frames and can push
a run past the probe's stream/poll deadline; scenario verdicts must keep
distinguishing that from plumbing failure.

---

### W2 — One delegation read model (Phase 1)

**Goal.** The UI shows ordinary `task` delegations, batch items, and operator
spawns through one honest read model; "Running now" stops being control-plane-only.

**Research anchors.** VS Code multi-agent visibility (B3); Claude teams
visibility (B2).

**Current state.** G1: two disjoint planes; `SubagentsSection.tsx` reads the
control plane; `DelegatedWorkPanel.tsx` reads thread events; `SubagentList.tsx`
reads live tasks.

**Changes.**
- New projection module `app/gateway/subagent_read_model.py` (Gateway-level; the
  harness cannot import `app.*`, and the reverse would be a boundary violation
  if it read control-plane storage): merges (a) the control plane
  (`persistence/managed_subagents` + lifecycle store), (b) the thread ledger
  (`ThreadState.delegations` via the existing checkpoint reader), (c) persisted
  `subagent.*` events. Every row carries `source` and per-field provenance.
- `GET /api/subagents/control/...` (or a sibling route) gains `?read_model=1`
  returning the merged view; **additive**, old shape unchanged.
- Frontend: `DelegatedWorkPanel` and `SubagentsSection` consume the merged route
  through `lib/subagents.ts`; failure states follow the existing honesty rules
  (reject, carry reason; never `[]`).
- Ledger rows missing from events render "details unavailable (stream not
  enabled)" — the documented precondition in the run-event contract stays true.

**Tests.** Gateway: `test_subagent_read_model.py` (merge precedence: same
execution present in ledger and events → one row, newest wins; contradictory
statuses → panel shows ledger status + event status, never silently picking);
`test_subagents_router.py` extended with the additive query. Frontend:
`lib/subagent-events.test.mjs` + `delegated-work-panel.test.mjs` extended
(negative control: a rejected fetch must not render an empty success panel).

**Acceptance gates.** D2 visible: screenshot-free evidence = a real API response
paste + rendered panel check in the audit record; D4: kill the events store and
confirm the panel says why.

**Refusals.** No new status vocabulary; no writes from the read model.

---

### W3 — Scheduling and throughput evolution (Phase 1)

**Goal.** Operators can see and shape contention without changing defaults;
the pool remains one controller.

**Research anchors.** Anthropic 3–5 parallel guidance (B1); Strands' explicit
bounds (B7); Magentic-One stall detection (B6).

**Current state.** G2/G6: 3 slots, priority bands, `priority_preemptions` only.

**Changes (each config-gated, startup-only where the pool is startup-frozen).**
- **a. Metrics (P0).** Extend `SubagentCapacitySnapshot` with
  `queue_wait_seconds` percentiles (bounded ring, monotonic clock, measured only;
  nulls when empty) + per-band running counts. Surfaced on the existing
  `/api/subagents` catalog payload and the panel. Pinned by
  `test_subagent_capacity.py` additions.
- **b. Reserved foreground slots.** Optional
  `subagent_runtime.foreground_reserved` (int, default 0): when > 0, background
  waiters cannot consume the last N slots unless no foreground waiter exists.
  Intersection rule documented: `min(per-run request, max_running, hard
  ceiling)` unchanged; the reserve narrows only background admission.
  Negative control: reserve=3 with max_running=3 refuses all background work
  until foreground arrives — test pins the starvation refusal message.
- **c. Backpressure disclosure.** `queue_timeout` rejects and timeout responses
  carry `capacity_refusal_retry_after_seconds`-style hints (reuse the existing
  pattern from `lane_scheduler.py`).

**Refusals.** No preemption of running children (a running thread cannot be
safely stopped; documented in the plan and the module guide). No per-engine
pools in this plan — the shared controller is a feature, not a bug; per-engine
reservation requests remain operator configuration of the single pool.

---

### W4 — Definition discovery and capability digests (Phase 2)

**Goal.** The lead prompt does not pay for the full catalog by default; every
definition is attributable by digest.

**Research anchors.** Agent Skills progressive disclosure (B15); Claude Code
subagent `description`-driven selection (B2).

**Current state.** G9: full catalog rendered; managed schema strict; no digest
travel.

**Changes.**
- `subagents.deferred_discovery` (bool, default **false** to preserve behavior):
  when true, the lead prompt receives `name + description` rows only, and the
  existing `subagent_registry` tool's `inspect` action returns full detail on
  demand. Mirrors `skills.deferred_discovery`; refusal mode on unknown name.
- `definition_sha256` stamped into: run metadata, `subagent.start` event
  metadata, and the terminal ToolMessage `additional_kwargs` (additive key in
  `status_contract.py`). Digest covers the effective resolved config after
  overlays (builtin→custom→managed→`subagents.agents` overrides) so a result is
  attributable to the *resolved* definition, not the stored one.
- Golden test: same definition → same digest across runs; any field change →
  digest change (negative control).

**Tests.** `test_subagent_routing_prompt.py` extended for deferred mode
(prompt excludes full prompts but includes names+descriptions; unknown inspect
refuses); `test_subagent_registry_tool.py` inspect path.

---

### W5 — Context engineering policy for children (Phase 2)

**Goal.** Each child's context discipline is explicit, tested, and documented —
prefix stability, working-plane parity, compaction policy per category.

**Research anchors.** Manus KV-cache discipline (B10); Deep Agents working plane
(B4) — already ported as `alpha/deepagent/`; Anthropic compaction numbers (B10).

**Current state.** Static system prompt + one-shot `SubagentDateContextMiddleware`
(good); working plane projected into every child (`DeepAgentContextMiddleware`);
global summarization switch; no prefix-stability test (G-C15).

**Changes.**
- **Prefix-stability test** (the honest proxy for KV-cache friendliness, since
  provider cache hits are not measurable from here):
  `test_subagent_prefix_stability.py` — for a fixed definition and task, the
  assembled child request's leading system message lives at index 0, byte-stable
  across two runs (excluding declared volatile fields); tool schema ordering
  stable; a negative control introducing a timestamp into the system message
  fails the test. Document in the module guide that Alpha measures *stability*,
  never claims *hit rate*.
- **Per-category compaction**: `subagents.categories.<name>.summarization`
  (optional bool; absent = inherit global). `quick` defaults to off (15-turn cap
  makes compaction pointless); validated against `summarization.enabled=false`
  (cannot enable what the operator disabled globally — one-way narrowing).
- **Working-plane handoff convention**: the task report contract gains one line
  telling children to externalize intermediate findings into the working plane
  under their execution id; the parent's compaction therefore preserves pointers,
  not payloads. No new tool (the plane tool already exists).
- **Tool schema stability note**: `tool_search` promotion mutates the visible
  schema set mid-run by design (deferred discovery). Document the masked-schema
  alternative (Manus) as **refused** for now with the reason: promotion already
  budgets top-k and the operator can set `tool_search.enabled=false` for a
  cache-stable profile.

**Tests.** New suites above; `test_subagent_executor.py` untouched paths stay
green (regression).

---

### W6 — Mid-flight interaction: follow-ups, steering, handoff (Phase 2)

**Goal.** A parent (or operator) can send a bounded message to a running child
at a safe point; children can hand off control in a declared mode.

**Research anchors.** OpenAI handoffs vs agents-as-tools (B5); Claude teams
peer messaging (B2); `subagents/messaging.py` + `yield_handoff.py` primitives.

**Current state.** G5: only cancel/timeout. The primitives exist without a
production tool surface.

**Changes.**
- **Follow-up channel.** New actions on `background_tasks_tool` (no new tool):
  `send_message(execution_id, text)` — bounded queue per execution (e.g. 8
  messages × 2,000 chars, neutralized like acceptance criteria), delivered by
  `SubagentExecutor` at the next safe point: after the current model response,
  before the next tool batch's model call (mirrors how LangGraph merge points
  work; never mid-tool, never mid-stream). Delivery is recorded as a hidden
  human message with the existing untrusted-channel framing and appears in
  `subagent.step` events and the ledger.
- **Gating.** `subagents.followups.enabled` (default false); delivery only for
  executions registered in the running state; unknown/terminal id → named
  refusal. A message that could not be delivered before terminalization is
  reported as `undelivered` (never silently dropped).
- **Handoff mode (declared, bounded).** A task may declare
  `mode="handoff"` in its config extension: on completion the child's final
  message becomes the parent's *current turn content* via the existing
  `yield_handoff` primitive, with the parent resuming after. Version 1 ships
  **read-only**: the handoff returns text + receipts; tool side effects never
  transfer ownership. Refusals: nested handoff chains > 2, handoff during
  acceptance checking, handoff from a batch item.

**Tests.** `test_subagent_messaging.py` (new): queue bounds, safe-point
delivery ordering, undelivered honesty, refusal on terminal id, negative
control removing the safe-point check (message lands mid-tool → test red).

---

### W7 — Artifact manifests and structured results (Phase 1–2)

**Goal.** A completed child reports *what it produced* with verified existence
and hashes; optional typed result schema for machine consumers.

**Research anchors.** Coding-agent practice (B13); `run.delivery` receipts
already exist for the lead; `FEATURE_COMPLETION_PLAN.md` G3.

**Current state.** Receipts exist at runtime (`ToolReceiptMiddleware`), terminal
metadata carries receipts + brief; `SubagentResult` has no artifact list.

**Changes.**
- **Artifact harvest.** Extend the executor's terminalization (before
  `try_set_terminal`, under the same state lock, matching the receipts rule) to
  build `artifacts: [{path|url, sha256|null, size|null, source_tool, receipt_id}]`
  from write receipts whose target is inside the thread workspace. Verification
  policy: hash + size computed by re-reading the file at terminalization; a file
  removed before readback → row kept with `sha256:null` and `status:"missing"`
  (measured, honest). Unsupported targets (URLs, remote sandbox paths without
  readback) → `sha256:null`, `basis:"unverified"`.
- **Wire.** Additive `subagent_artifacts` key in `status_contract.py` +
  `contracts/subagent_status_contract.json` (additive optional field; contract
  test extended). Frontend `SubagentCard` renders artifacts with per-field
  honesty (no hash → no checkmark).
- **Structured result (opt-in).** `task(output_schema=<small JSON schema>)`
  where the schema is validated by a bounded pydantic model list (start with
  `{summary: str, items: list[str], artifacts: list[str]}`-shaped presets, not
  arbitrary schema hosting — arbitrary schemas are a prompt-injection and
  denial-of-service surface; arbitrary-schema support is refused in v1).
  Validation failure → `subagent_status=completed` + `output_schema_status="failed"`
  additive field; never a retry by itself; never an acceptance verdict.

**Tests.** `test_subagent_artifacts.py`, contract tests, frontend
`subagent-events.test.mjs` extension; negative controls: delete file post-write →
`missing`; hash mismatch → `mismatch` rendered, not hidden.

---

### W8 — Verification upgrade: aspect verifiers (Phase 3)

**Goal.** Optional, bounded, multi-verifier layer for open-ended outputs —
narrow-only, receipt-citing, default off.

**Research anchors.** BoN-MAV (B9); verifier-scaling gains (B9);
Inference-Scaling-Flaws caution (B9).

**Current state.** G8: deterministic leaves + narrowing judge.

**Changes.**
- `acceptance_checks.py` gains `aspect:<name>=<criterion>` leaves, enabled by
  `subagents.aspect_verifiers.enabled` (default false). Each aspect verifier:
  runs off-loop, failure-isolated; receives **only** the child's terminal text +
  receipts + artifact manifest; must return per-criterion `pass|fail|unverified`
  **with at least one `rN` citation per `pass`**; a `pass` without citation
  demotes to `unverified`.
- Composition (the load-bearing rule): the final verdict is the **minimum** of
  (deterministic leaves, aspect verifiers) — deterministic `fail` cannot be
  upgraded; aspect `pass` never upgrades a deterministic `unverified`; all
  aspect machinery runs on a completed branch only. Cost guard: at most N=3
  aspects per task, each capped by the existing token budgets, model resolved
  from a dedicated `subagents.aspect_verifiers.model` (validated against
  `models[]`; zero-cost constraint respected).
- MAST alignment test: the suite includes one deliberately premature-termination
  trace and one incorrect-verification trace; both must stay `unverified`/`fail`.

**Tests.** `test_subagent_aspect_verifiers.py` (negative controls: citation
missing → demotion; verifier error → `unverified`; try to upgrade a `FAIL` →
red), `test_task_tool_acceptance_checklist.py` extended (composition matrix).

---

### W9 — Cost-aware model routing (Phase 3)

**Goal.** A measured, bounded policy that starts bounded subtasks on the cheapest
adequate tier and escalates on *measured* failure — never on vibes; savings
claimed only via the W1 join of cost and acceptance.

**Research anchors.** B14 in full.

**Current state.** G7. `categories.py` chains + `models/factory.py` fallbacks +
`resilience.py` escalation exist as pieces.

**Changes.**
- New pure module `subagents/routing.py`: `resolve_model_plan(category,
  definition, measured_tier?) → plan` producing `(primary, chain, escalation_ladder)`
  where every name is validated against `config.yaml models[]` (same load-time
  discipline as `model_routing`) and each tier carries a `cost_class` derived
  from `model_pricing`. Unknown tier → refusal listing available tiers.
- Config: `subagents.routing.policy: {enabled: false, tiers:
  {economy: [...], standard: [...], frontier: [...]}, escalation: {on:
  [turn_capped, loop_capped, failed], max_attempts: 1}}`. Escalation counts
  against the same per-run caps and APEX/token budgets — **the routing layer
  has no budget of its own**.
- Escalation evidence: the delegation ledger entry gains `model_attempts:
  [{model, stop_reason, tokens}]`; a child that escalates is never reported as a
  single-model success.
- Zero-cost constraints: an `economy` model that is zero-cost may not escalate
  to a paid tier (`alpha.budgets.spend` rules reused); `subagents.token_budget`
  remains the backstop.
- Honesty: savings dashboards are out of scope; W1's report is the only place a
  cost claim may be computed, and it must carry per-task acceptance alongside.

**Tests.** `test_subagent_routing_policy.py` (unknown tier refuses; policy can
only narrow category chains; escalation ladder bounded; zero-cost refusal),
`test_subagent_model_resolution.py` extended.

---

### W10 — Delegation lessons loop (Phase 3, default off)

**Goal.** Recurring delegation failures are distilled into *reviewer-gated
proposals* against subagent definitions — never auto-applied, never silent
memory writes.

**Research anchors.** ExpeL/DGM/PILOT (B12); LongMemEval-V2's finding that
operational experience is what current memory misses (B11); Alpha's existing
proposal→approve pipeline for skills.

**Current state.** G10. Ledger + stop reasons exist; no consumer.

**Changes.**
- New action on `subagent_registry`: `analyze` (read-only) — given a time window
  and optional definition name, aggregates the ledger + terminal metadata into a
  report: failure-mode counts by `stop_reason`, acceptance verdict mix, median
  token/turn usage, repeated task-description patterns. Every number measured;
  small samples render "insufficient evidence" instead of percentages.
- `subagents.lessons.enabled` (default **false**): when on, a bounded analyzer
  (category-configurable model) may emit `ManagedSubagentProposal` rows through
  the existing proposals store pattern (`skills/packages/alpha/skills/proposals.py`
  is the model to mirror for subagents): proposed description/prompt/tool-allowlist
  edits with the evidence that motivated them. Approval stays an admin route;
  the proposal never mutates the definition.
- Refusals: no proposal for `REQUIRED_DISALLOWED_TOOLS` widening; no proposal
  may add tools that widen authority (`task` stays denied); a proposal touching
  a protected path surfaces the same refusal vocabulary as `self_tuning`
  protected paths.

**Tests.** `test_subagent_lessons.py` — analysis numbers computed from a fixed
ledger fixture; insufficient-sample honesty; proposal validation (schema strict,
`extra="forbid"`); negative control: analyzer output cannot include `task` in a
tool list.

---

### W11 — Nested delegation, gated (Phase 3, default off)

**Goal.** Give the operator the **option** of multi-level delegation under the
existing ceilings — with every failure mode from MAST FC1 explicitly fenced.

**Research anchors.** The enforcement-not-prompting principle
([AI Engineer analysis](https://theaiengineer.substack.com/p/how-anthropic-built-multi-agent-deep)):
"enforce it in the orchestration layer, not beg for it in a prompt";
`MAX_DELEGATION_DEPTH=3`, `MAX_CHILDREN_PER_PARENT=10`, `MAX_FANOUT_PER_NODE=4`,
`MAX_DELEGATION_HOPS=6` already exist.

**Current state.** G11: single-level by default; constants unused by the
ordinary path.

**Changes.**
- `subagents.nested.enabled` (default **false**) + `subagents.nested.max_depth`
  (default 2; hard-clamped to `alpha.config.limits.MAX_DELEGATION_DEPTH`).
- When enabled: `task` joins the child's effective tool allowlist (never when
  disabled; `REQUIRED_DISALLOWED_TOOLS` semantics preserved for managed
  definitions that opt out). Depth travels in the child runtime context;
  depth-exhausted `task` calls are refused with a typed error naming the limit
  (extension of the `SubagentLimitMiddleware` pattern), not silently dropped.
- Fan-out/children bounds reuse `MAX_CHILDREN_PER_PARENT` /
  `MAX_FANOUT_PER_NODE`; the per-run total cap applies **per root run** across
  the whole tree (the ledger is the counting surface; it already carries
  execution ids).
- Mandatory tests (this is the price of admission): depth-2 chain executes,
  depth-3 refuses; a child cannot raise its own depth; the tree's total child
  count cannot exceed the per-run cap via sibling batches (the existing bypass
  test extended to two levels); cancellation of a root cancels the tree
  (recursion exists in `cancel_subagent`; extend to executor-registry children);
  APEX `max_delegation_depth=0` refuses at level one.

**Refusals.** Default stays off; no "auto-nesting" heuristic. This workstream is
deliberately last in P3 because it multiplies every other failure mode.

---

### W12 — Interop: remote subagents and standard alignment (Phase 4)

**Goal.** A scoped bridge: consume a remote A2A agent as a subagent definition;
optionally expose managed subagents as A2A agents — without claiming
conformance before it is tested.

**Research anchors.** A2A v1.0 + LF adoption (B15); `docs/PROTOCOLS.md`
(verified 2026-09-29) — Alpha's peer network is `alpha-a2a`-shaped but
wire-incompatible; AG-UI stable at 1.0 (a separately tracked surface).

**Changes.**
- `subagents.remote.enabled` (default false). A remote definition is a
  `SubagentConfig` variant with `transport: a2a|alpha-peer` where:
  - `alpha-peer` reuses the existing pair-token-authenticated transport
    (blast radius minimized; this is the only transport available without new
    dependencies).
  - `a2a` is **spec'd, not shipped**, in this plan: implement server-side Agent
    Card + `message/send` subset, then validate against the A2A conformance
    tooling before flipping any claim; until then every surface labels it
    `conformance: unverified`.
- Execution: remote tasks run through the same executor lifecycle *as a
  transport adapter* (submission, polling, cancellation, timeout) so caps,
  ledger, events, and terminal metadata are identical. A remote child is never
  trusted for receipts: its claims arrive as untrusted data, its artifacts are
  `basis:"remote-unverified"`.
- Skills sales-channel note: keep `SKILL.md` packs standard-compliant
  (agentskills.io) — already largely true; record the standard version in
  `docs/SKILLS.md` when touched (separate workstream, owner `docs/SKILLS.md`).
- `docs/PROTOCOLS.md` updated with the delegation bridge's exact posture.

**Tests.** `test_subagent_remote_transport.py` (fake A2A server: success,
timeout, cancellation fencing, malformed payload refusal), plus an explicit
test that no code path reports "A2A compliant" while conformance is unverified.

---

### W13 — Team task list and progress ledger (Phase 4, stretch)

**Goal.** Two coordination features built **on existing primitives**, no new
scheduler: (a) a shared task list a small set of children can read/report
against; (b) a lead-side progress ledger that detects stalled delegations and
re-plans.

**Research anchors.** Claude Agent Teams (B2); Magentic-One ledgers (B6).

**Changes.**
- **(a)** Reuse `ThreadState.todos` + `todos_updated` events: extend the subagent
  tool policy so a task child in a team-scoped run may *read* the shared todo
  list (read-only; writes remain lead-only — the existing one-in-progress
  invariant stays). Team scope = the existing groups room or a run-scoped
  `team_id` in runtime context; no new store.
- **(b)** Lead-side only: `SubagentProgressLedger` (new small module under
  `subagents/`) summarizing per-delegation progress deltas from existing
  `subagent.step` events (turn count + tool-call count + time since last step).
  A single bounded "no progress for K steps" signal is surfaced to the lead as a
  hidden reminder (like `DynamicContextMiddleware` does for dates), never as an
  automatic re-dispatch. Automatic re-planning stays refused until W1 has
  measured that it helps.

**Tests.** `test_subagent_progress_ledger.py`: fixture event streams produce
stall signals; no signal when steps continue; reminder is framework-owned and
one-per-stall-window (no spam); negative control: deleting the progress source
yields `unknown`, not "not stalled".

---

### E-end. What this plan deliberately refuses (and why)

| Refusal | Reason |
| --- | --- |
| Preempting/killing running child threads | CPython cannot stop a thread; a "killed" child could still write. Timeouts + fencing already exist. |
| A second scheduler or team runtime | Seven schedulers already exist; the baseline document warns against exactly this. Teams ride groups/war-room/todos. |
| Auto-applying learned prompts/definitions | Proposal→approval is the existing control; silent self-modification would violate the operator gate. |
| Claiming KV-cache hit rates | Not measurable from here; we measure prefix stability instead and say so. |
| Arbitrary output schemas | Prompt-injection and DoS surface; v1 ships bounded presets only. |
| Auto re-planning on stall | Unmeasured intervention; reminder-only until W1 evidence exists. |
| "A2A compliant" claims before conformance testing | `docs/PROTOCOLS.md` discipline: a claim without a test is not a claim. |
| Auto-nesting | Default off; opt-in only with the W11 gate suite. |

---

## Part F — Metrics and evaluation

Every metric below is computable from W1's report; none requires new storage.

| Metric | Definition | Honesty rule |
| --- | --- | --- |
| Delegation Success Rate (DSR) | completed ∧ (acceptance `pass` ∨ no criteria) / dispatched | `unverified` never counts as success; both numerators reported |
| Acceptance mix | pass / fail / unverified per leaf type | unverified is a first-class outcome in every table |
| Cost per accepted task | tokens × `model_pricing` (USD) | `null` when any model is unpriced; never estimated |
| Queue wait p50/p95 | monotonic timestamps around `.slot()` | `null` under 5 samples |
| Visibility coverage | dispatched executions with ledger row ∧ start+end events | stream-mode precondition stated in every report |
| Failure profile (MAST) | counts by the 14 modes over sampled traces, human-checked | sampled + dated; never extrapolated to "all delegations" |
| Prefix stability | byte-stable leading SystemMessage hash across identical runs | labelled stability, not cache hit rate |
| Routing savings | Σ cost(attempts) vs counterfactual single-tier run, both measured | claimed only at n ≥ 30 graded tasks; else "insufficient evidence" |

**Cadence.** W1 runs at every Phase gate; MAST annotation on ≥ 20 sampled traces
per phase; the probe report is committed under `.alpha/` (disposable) with the
phase audit record referencing it.

---

## Part G — Risks

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| W9 savings are negative on short tasks (break-even math) | Medium | Medium | Measured-only claims; escalation bounded; policy default off |
| W8 verifiers add cost without quality (imperfect-verifier dynamics) | Medium | Medium | Default off; composition is narrow-only; W1 gate decides adoption |
| W6 follow-ups create a new injection channel into children | Medium | High | Untrusted-channel framing; bounded queue; neutralization; never system-channel |
| W11 nested delegation explodes token spend | Medium | High | Default off; depth/fan-out/children caps; per-run total across tree |
| W2 read model disagrees with ledger (two owners of a fact) | Low | High | Ledger is the single owner; projection is read-only; contradiction renders both |
| W12 remote children launder unreceipted claims | Medium | High | Receipts from remote are `remote-unverified`; acceptance never runs on remote claims alone |
| Scope creep into the seven-scheduler consolidation | Medium | High | This plan touches no scheduler; teams are read-models on existing planes |

---

## Part H — References (all fetched/retrieved 2026-10-17)

**Multi-agent systems & delegation**
1. Anthropic — How we built our multi-agent research system — https://www.anthropic.com/engineering/multi-agent-research-system
2. The AI Engineer — How Anthropic Built Multi-Agent Deep Research — https://theaiengineer.substack.com/p/how-anthropic-built-multi-agent-deep
3. LangChain — Choosing the Right Multi-Agent Architecture — https://www.langchain.com/blog/choosing-the-right-multi-agent-architecture
4. Claude Code — Agent Teams — https://code.claude.com/docs/en/agent-teams
5. Claude Code — Subagents (SDK) — https://code.claude.com/docs/en/agent-sdk/subagents
6. VS Code — Your Home for Multi-Agent Development (2026-02-05) — https://code.visualstudio.com/blogs/2026/02/05/multi-agent-development
7. LangChain — Introducing Dynamic Subagents in Deep Agents — https://www.langchain.com/blog/introducing-dynamic-subagents-in-deep-agents
8. LangChain — New in Deep Agents v0.6 — https://www.langchain.com/blog/deep-agents-0-6
9. LangChain — Subagents docs — https://docs.langchain.com/oss/python/deepagents/subagents
10. OpenAI — Agents SDK — https://openai.github.io/openai-agents-python/
11. Turion — OpenAI Agents SDK deep dive — https://turion.ai/blog/framework-deep-dive-openai-agents-sdk/
12. Microsoft Research — Magentic-One — https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/
13. AutoGen — Magentic-One docs — https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/magentic-one.html
14. AWS — Strands Agents 1.0 — https://aws.amazon.com/blogs/opensource/introducing-strands-agents-1-0-production-ready-multi-agent-orchestration-made-simple/
15. AWS — Strands technical deep dive — https://aws.amazon.com/blogs/machine-learning/strands-agents-sdk-a-technical-deep-dive-into-agent-architectures-and-observability/

**Failure taxonomy & verification**
16. MAST — Why Do Multi-Agent LLM Systems Fail? — https://arxiv.org/abs/2503.13657
17. MAST project — https://sky.cs.berkeley.edu/project/mast/
18. MAST GitHub + agentdash — https://github.com/multi-agent-systems-failure-taxonomy/MAST
19. BoN-MAV — Multi-Agent Verification — https://openreview.net/forum?id=LriQ3NY9uL
20. Inference-Time Scaling of Verification (deep research) — https://arxiv.org/html/2601.15808v1
21. Structured Test-Time Scaling (hierarchical MAS theory) — http://xinmingtu.cn/blog/2026/hierarchical-mas-theory/
22. Inference Scaling Flaws — https://arxiv.org/abs/2411.17501
23. Awesome Inference-Time Scaling — https://github.com/ThreeSR/Awesome-Inference-Time-Scaling

**Context, memory, self-improvement**
24. Manus — Context Engineering for AI Agents — https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus
25. Anthropic Cookbook — Context engineering: memory, compaction, tool clearing — https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools
26. Chroma — Context Rot — https://research.trychroma.com/context-rot
27. Mem0 — State of AI Agent Memory 2026 — https://mem0.ai/blog/state-of-ai-agent-memory-2026
28. Zep/Graphiti — temporal knowledge graph paper — https://arxiv.org/abs/2501.13956
29. Agent memory research papers (LongMemEval, LongMemEval-V2, MemGPT) — https://agentic-ai.readthedocs.io/en/latest/AgentMemory/research-papers/
30. Infini Memory — https://arxiv.org/html/2606.10677v1
31. Self-improving agents survey — https://furong-huang.com/blog/self-improving-agents-learning-how-to-work/
32. o-mega — Self-Improving AI Agents: The 2026 Guide — https://o-mega.ai/articles/self-improving-ai-agents-the-2026-guide
33. PILOT — Live self-improvement for long-horizon agents — https://arxiv.org/html/2608.26530v1

**Parallel agents, routing, protocols, skills**
34. Simon Willison — Embracing the parallel coding agent lifestyle — https://simonwillison.net/2025/Oct/5/parallel-coding-agents/
35. HN — parallel agents thread (Rover) — https://news.ycombinator.com/item?id=45489884
36. 200 Agents, One Brain (queen/drone orchestration) — https://bhavishyapandit9.substack.com/p/ai-code-generation-claude-thinking
37. Multi-agent orchestration cost math — https://claudefa.st/blog/guide/development/multi-agent-orchestration-cost
38. RouteLLM — https://arxiv.org/html/2406.18665v4
39. FrugalGPT — https://arxiv.org/abs/2305.05176
40. AOrchestra — automating sub-agent creation — https://arxiv.org/html/2602.03786v2
41. Linux Foundation — A2A one-year milestone — https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year
42. Turing Post — A2A v1.0 guide — https://www.turingpost.com/p/a2a
43. Anthropic — Equipping agents with Agent Skills — https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
44. Agent Skills ecosystem report 2026 — https://agentman.ai/blog/agent-skills-ecosystem-report-2026

**In-repo authorities**
45. `backend/packages/harness/alpha/subagents/AGENTS.md` — subagent invariants
46. `docs/architecture/multi-agent-baseline.md` — measured multi-engine baseline
47. `docs/FEATURE_COMPLETION_PLAN.md` — verification method + open items
48. `docs/PROTOCOLS.md` — MCP/A2A/ACP/AG-UI posture
49. `docs/WORKFORCE.md` — scheduled tasks and engine sizing
50. `contracts/subagent_status_contract.json`, `contracts/run_event_stream_contract.json` — frozen wire contracts

---

## Appendix A — New config keys (sketch; names final at implementation review)

| Key | Default | Owner | Semantics |
| --- | --- | --- | --- |
| `subagent_runtime.foreground_reserved` | `0` | startup-only | Slots background waiters cannot consume while foreground waiters exist |
| `subagents.deferred_discovery` | `false` | hot-reload | Name+description-only catalog in the lead prompt |
| `subagents.categories.<name>.summarization` | inherit | hot-reload | Per-category compaction narrowing only |
| `subagents.followups.enabled` | `false` | hot-reload | Enable the bounded follow-up channel |
| `subagents.aspect_verifiers.enabled` / `.model` | `false` / `default_model` | hot-reload | Bounded narrowing verifiers |
| `subagents.routing.policy` | disabled | hot-reload | Tiers + bounded escalation |
| `subagents.lessons.enabled` | `false` | hot-reload | Proposal-generating analyzer |
| `subagents.nested.enabled` / `.max_depth` | `false` / `2` | hot-reload, clamped by `limits` | Opt-in nested delegation |
| `subagents.remote.enabled` | `false` | startup-only | Remote subagent transport |

Every key is validated at load; an unknown key is a startup error (config
`extra` discipline), and every cognitive feature defaults off.

## Appendix B — Contract additions (all additive)

`contracts/subagent_status_contract.json`:
- `subagent_definition_sha256` (string, optional)
- `subagent_artifacts` (array, optional; per-item `path|url`, `sha256|null`,
  `size|null`, `status: verified|missing|mismatch|unverified`)
- `subagent_output_schema_status` (`passed|failed|null`)
- `subagent_model_attempts` (array, optional; only when routing escalated)

`contracts/run_event_stream_contract.json`: `subagent.step` may carry
`kind: message_delivery` rows for W6; the "absence is not evidence" warning
stays unchanged.

## Appendix C — Scenario catalogue growth (advisory catalogue only)

Existing (8): `direct`, `single_delegate`, `parallel_fan_out`, `tournament`,
`advisory_council`, `staged_pipeline`, `background_delegate`, plus the
perception tiers. Proposed additions (each requires measured `Requirement`s
before any promotion beyond shadow):

1. `best_of_n_verified` — N independent children + W8 verifiers; requires
   W8, a measured verifier, and a budget that can only lower N.
2. `map_reduce_files` — one child per file group + synthesis; requires a
   measured file inventory and non-overlapping scopes.
3. `pair_review` — writer child + reader child with receipts-only review;
   requires W7 artifacts.
4. `staged_continuation` — long child with W6 follow-ups across stages;
   requires follow-ups + progress ledger.

Unchanged rules: shadow by default, AST-pinned cannot-dispatch, unknown id
refuses, a budget may only lower.

## Appendix D — Open questions for the operator

1. Should `subagents.max_total_per_run` default rise from 6 (single-level)
   when W11 is enabled, or stay 6 per root run across the tree? (Recommendation:
   stay 6 until W1 measures typical trees.)
2. Is the global 3-slot pool the intended operational ceiling, or should the
   reserved-foreground knob (W3.b) be the sanctioned answer? (Recommendation:
   knob first, then re-measure.)
3. Which A2A transport lands first if W12 proceeds: the existing
   `alpha-peer` bridge (zero new dependencies) or an `a2a-sdk` dependency?
   (Recommendation: `alpha-peer` first, `a2a-sdk` only with a conformance
   harness.)
4. Should W8's aspect verifiers run on the same model family as the child or a
   different vendor? (Recommendation: operator-selected; default = the
   configured `default_model`.)
