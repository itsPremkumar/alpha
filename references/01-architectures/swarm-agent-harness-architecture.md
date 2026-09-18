# Swarm Agent Harness — Architecture

## 1. Overview

This document describes a high-level architecture for a swarm-intelligent AI agent harness: a system where one **orchestrator** agent decomposes incoming tasks, dispatches work to a pool of parallel **subagents**, coordinates them through **shared memory**, and routes their actions through a common **tool/execution layer** before aggregating a final result.

This pattern is the same one used by modern agentic systems (e.g. Moonshot's Kimi K3, which fans out 20+ concurrent subagents for deep-research tasks) and by Claude's own multi-agent research mode: a single lead agent that plans, spawns workers, and synthesizes.

```
Task → Orchestrator → Agent Swarm → Shared Memory ⇄ Tool Layer → Orchestrator → Result
```

## 2. System Layers

| Layer | Responsibility |
|---|---|
| Orchestrator | Task decomposition, routing, aggregation, stop condition |
| Agent swarm | Specialized subagents executing subtasks in parallel |
| Shared memory / blackboard | Common read/write context store across all agents |
| Tool & execution layer | Sandboxed code exec, terminal, browser, external APIs |
| Observability | Tracing, logging, cost/token accounting |

## 3. Control Loop

Every orchestrator run follows a **plan → act → observe → reflect → replan** loop:

1. **Plan** — break the task into a directed graph of subtasks (some parallelizable, some sequential/dependent)
2. **Act** — dispatch subtasks to available or newly spawned subagents
3. **Observe** — collect subagent outputs from shared memory as they complete
4. **Reflect** — check outputs against the goal; detect conflicts, gaps, or failures
5. **Replan** — either finalize and aggregate, or issue a new round of subtasks

Stop conditions: goal satisfied, max iterations reached, token/cost budget exhausted, or no progress detected across N rounds (to avoid infinite loops).

## 4. Agent Roles & Specialization

Define a small set of agent archetypes rather than one generic agent. Typical roles:

- **Planner** — lives inside the orchestrator, turns a goal into a task graph
- **Researcher** — web/document search, summarization, fact-gathering
- **Coder** — writes/edits/tests code in a sandbox
- **Verifier / critic** — checks another agent's output for correctness before it's accepted
- **Executor** — runs terminal commands, calls APIs, drives a browser
- **Synthesizer** — merges multiple agents' partial outputs into one coherent artifact

Each subagent should get a **narrow system prompt**, a **limited toolset** (only what its role needs), and its **own context window** — don't let every agent see everything, or you lose the efficiency benefit of specialization.

## 5. Communication & Coordination

Two common patterns — pick one, don't mix without a reason:

- **Message passing (queue-based)**: orchestrator publishes tasks to a queue (Redis Streams / Kafka / RabbitMQ); subagents pull, process, and publish results back. Good for async, high-throughput, decoupled scaling.
- **Direct orchestration (function-call based)**: orchestrator calls subagents as functions/sub-processes and awaits results directly. Simpler to reason about and debug; harder to scale to very large swarms.

### Message schema (example)

```json
{
  "task_id": "t_8f21",
  "parent_task_id": "root_001",
  "role": "researcher",
  "instructions": "Find pricing for competitor products X, Y, Z",
  "context_refs": ["mem:task_root_001:goal", "mem:task_root_001:constraints"],
  "tool_allowlist": ["web_search", "web_fetch"],
  "budget": { "max_tokens": 40000, "max_tool_calls": 15 },
  "status": "pending"
}
```

## 6. Memory Architecture

Three tiers, not one:

- **Working memory** — per-agent scratchpad, exists only for the lifetime of its subtask
- **Shared/blackboard memory** — a key-value or document store all active agents can read/write during a run (e.g. `task:{id}:findings`, `task:{id}:draft`)
- **Long-term memory** — persists across runs/sessions; typically a vector store (pgvector, Pinecone, Weaviate) for semantic recall plus a structured store for facts/entities

Write conflicts matter once agents run in parallel — use append-only logs per key, or optimistic locking with versioned writes, rather than last-write-wins on shared documents.

## 7. Tool & Execution Sandbox

- Every subagent calls tools through **one shared execution layer**, not its own copy — this is where you enforce permissions, rate limits, and logging in one place
- Code execution runs in isolated containers (Docker / gVisor / Firecracker) with no network access unless explicitly granted per task
- External API calls go through an allowlist per agent role (a researcher gets `web_search`, a coder gets `bash` + `file` tools, etc.)
- Every tool call is logged with the task_id that triggered it, for traceability

## 8. Result Aggregation & Consensus

When multiple subagents produce overlapping or conflicting outputs, the orchestrator needs an explicit merge strategy:

- **Voting** — for factual/classification tasks, take majority answer across N independent agents
- **Verifier gate** — a dedicated critic agent scores/approves each output before it's accepted into shared memory
- **Confidence-weighted merge** — each agent reports a confidence score; orchestrator weights accordingly
- **Synthesizer pass** — for open-ended outputs (reports, code), one agent explicitly merges partial contributions into a single coherent artifact rather than concatenating them

## 9. Failure Handling & Reliability

- **Timeouts** per subtask, with automatic retry (capped, e.g. 2 retries with backoff)
- **Budget caps** per subagent (tokens, tool calls, wall-clock time) so one runaway agent can't consume the whole run's budget
- **Circuit breaker** at the orchestrator level: if N subtasks fail in a row, halt and surface to the caller instead of looping
- **Idempotent tasks** where possible, so retries don't duplicate side effects (e.g. don't re-send an email on retry)

## 10. Observability & Tracing

Non-negotiable once you have more than a couple of agents running concurrently:

- Distributed tracing (OpenTelemetry) with one trace per top-level task, spans per subagent/tool call
- Structured logs keyed by `task_id` / `agent_id` for replay and debugging
- Real-time dashboard: active agents, queue depth, token spend, failure rate
- Full transcript storage per run — you will need this for debugging emergent (and sometimes wrong) swarm behavior

## 11. Scaling & Dynamic Spawning

- Start with the orchestrator deciding agent count/roles upfront from the task plan
- More advanced: let subagents themselves request additional subagents (bounded by a max-depth and max-total-agents limit to prevent runaway recursion)
- Horizontal scaling: subagents as stateless workers behind the message queue, autoscaled by queue depth
- Cap total concurrent agents per run based on cost/latency targets, not just infrastructure limits

## 12. Security Considerations

- Sandbox all code execution; never let a subagent's generated code run with host-level permissions
- Scope tool access per role (least privilege) — a researcher agent should not have filesystem write access
- Treat any content pulled from the web/tools as untrusted input — don't let it silently alter another agent's instructions (prompt-injection risk across shared memory)
- Rate-limit and audit external API/tool calls per task and per user

## 13. Suggested Tech Stack

| Component | Options |
|---|---|
| Orchestration/state machine | LangGraph, custom FSM, Temporal |
| Message queue | Redis Streams, Kafka, RabbitMQ |
| Shared/long-term memory | pgvector, Pinecone, Weaviate + Postgres for structured data |
| Sandboxed execution | Docker, gVisor, Firecracker microVMs |
| Observability | OpenTelemetry, Grafana, Langfuse/Helicone for LLM-specific tracing |
| Model layer | Claude API / OpenAI API / self-hosted open weights, behind a common adapter interface |

## 14. Example End-to-End Flow

1. User submits task → Orchestrator receives it
2. Orchestrator plans: 3 research subtasks (parallel) → 1 synthesis subtask (depends on all 3)
3. 3 researcher agents spawned, each writes findings to `task:{id}:findings:{n}`
4. Orchestrator observes all 3 complete, checks for conflicts
5. Synthesizer agent reads all findings from shared memory, produces draft
6. Verifier agent checks draft against original goal, flags 1 gap
7. Orchestrator dispatches 1 more researcher subtask to fill the gap
8. Synthesizer updates draft, verifier approves
9. Orchestrator returns final result, closes trace

## 15. Suggested Repo Structure

```
/harness
  /orchestrator        # planning, routing, aggregation logic
  /agents
    /researcher
    /coder
    /verifier
    /synthesizer
  /memory               # shared memory client, vector store adapters
  /tools                # tool implementations + sandbox execution
  /queue                # message bus client/config
  /observability         # tracing, logging config
  /schemas              # task/message JSON schemas
  config.yaml
```

## 16. Build Phases (Roadmap)

1. **Phase 1** — single orchestrator + 1 hardcoded subagent type, direct function calls, no queue (prove the control loop works)
2. **Phase 2** — add shared memory layer, 2–3 agent roles, sequential dispatch
3. **Phase 3** — parallel dispatch via message queue, add verifier/consensus step
4. **Phase 4** — add observability, budget caps, retry/failure handling
5. **Phase 5** — dynamic agent spawning, autoscaling, full sandboxed tool layer
