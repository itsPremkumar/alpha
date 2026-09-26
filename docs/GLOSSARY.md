# Alpha glossary

Every Alpha-specific term in one place, in plain language. Terms marked ⚠️ have a
boundary or caveat that matters — see the linked document before relying on them.

---

## A

**Acceptance criteria** — bounded, machine-checkable conditions a run declares up
front. Alpha persists them with the run record so a verification collector can later
attach independently checked evidence. Declaring criteria does not make a run
verified. → [PRODUCTION_READINESS_INVENTORY.md](PRODUCTION_READINESS_INVENTORY.md)

**Acceptance-sensitive aggregation** — swarm result aggregation that depends on
verified acceptance, not merely on task completion, so "it ran" and "it was
delivered and verified" stay distinct. → [WORKFORCE.md](WORKFORCE.md)

**Action** — a transactional operation with rollback and atomic side-effect
guarantees (`alpha/action`).

**Adaptive provider concurrency** — swarm worker concurrency that adjusts to
provider behaviour instead of using a fixed number.

**AEO (Answer Engine Optimization)** — structuring content so answer engines
(Google AI Overviews, Bing Copilot, Perplexity) can extract and present it.
→ [DISCOVERABILITY.md](DISCOVERABILITY.md)

**AgentEye** — the pinned live-source research plane: a curated 39-function
free-source catalog with operator allowlists, bounded fan-out, SSRF-safe fetching,
and DDGS fallback. → [DEEP_RESEARCH.md](DEEP_RESEARCH.md)

**Agent roster** — the registered set of autonomous bots, each with its own
personality, isolated system prompt, and private inbox. → [WORKFORCE.md](WORKFORCE.md)

**Approval gate** — the risk-scoring command gate that requires explicit operator
verification before a high-impact terminal command runs.

**Artifact lineage** — end-to-end cryptographic provenance of every generated file,
code, and document, from the originating prompt.

**Astra enclave** — the security plane providing credential scoping and process
isolation, so the model never receives raw secrets. → [SECURITY.md](SECURITY.md)

**Autonomy loop** — a background loop owned by the autonomy supervisor (sentinel,
perpetual, review_queue, skill_curator, enterprise_heartbeat). Gated by
`config.yaml -> autonomy.loops`; an absent id means disabled. → [ARCHITECTURE.md](ARCHITECTURE.md)

**AVO (Agentic Variation Operators)** — evolutionary mutation operators for
prompts and strategies, driven by compiler-grounded feedback.
→ [COGNITIVE_ENGINES.md](COGNITIVE_ENGINES.md)

---

## B

**Blackboard** — the bounded shared surface where agents post untrusted
observations and task results, and query each other's findings.
→ [WORKFORCE.md](WORKFORCE.md)

**Blast radius** — the set of systems a given action can affect; modelled by
consequence simulation before an irreversible action runs.

**Boulder checkpoint** — a durable, multi-session execution snapshot that lets a
run survive a crash, an expired worker lease, a restart, or a recoverable model
failure. Browser and network disconnects never cancel a run. → [RUN_RECOVERY.md](RUN_RECOVERY.md)

**Budget** — a hard ceiling on tokens, tool calls, wall-clock time, tasks, or
replans for a single run. Exceeding it produces an explicit `budget_exhausted`
state rather than an unbounded run. → [WORKFORCE.md](WORKFORCE.md)

---

## C

**Canary sandbox** — an isolated environment used to trial a change before it is
allowed to affect the real workspace.

**Citation contract** — the research output rule: deterministic `[S1]`, `[S2]`
anchors, and every source reported as verified, unsupported, unverified, or not
checked. → [DEEP_RESEARCH.md](DEEP_RESEARCH.md)

**Cognitive memory** — the long-term memory plane scoped to a server-resolved
owner, stored under that owner's directory. Missing owners and corrupt-state loads
fail closed. → [MEMORY.md](MEMORY.md)

**Cognitive memory owner** — the server-resolved identity that scopes production
cognitive memory. HTTP-supplied and model-supplied owners are deliberately not
trusted. → [MEMORY.md](MEMORY.md)

**Companion (Milo)** — the local inline-SVG lion with articulated animation,
bounded roaming, petting, and an optional always-on-top desktop window that
forwards no prompt or conversation data. → [LION_COMPANION.md](LION_COMPANION.md)

**Consequence simulation** — counterfactual analysis of side effects and negative
outcomes before a tool executes.

**Constitution (project)** — the governing document for a project in the workforce
layer, alongside its ADRs and resource locks. → [WORKFORCE.md](WORKFORCE.md)

**Context compaction** — sliding-window compression of context-as-data, with
prompt-injection filters. → [ARCHITECTURE.md](ARCHITECTURE.md)

**Crew / Flow** — CrewAI's two abstractions (autonomous collaboration, deterministic
control). Alpha's swarm layer plays a similar role. → [COMPARISON.md](COMPARISON.md)

---

## D

**DAG (directed acyclic graph)** — the dependency-ordered shape used for both
mission work queues and swarm task plans.

**Deep research** — the five-pass autonomous search pipeline: discovery, specific
evidence, adversarial contradiction, fact verification, strategic synthesis, plus
bounded knowledge-gap filling. → [DEEP_RESEARCH.md](DEEP_RESEARCH.md)

**Deep Agents** — LangChain's higher-level package on top of LangGraph for agents
that plan, use subagents, and use a filesystem. → [COMPARISON.md](COMPARISON.md)

**Delta** — a recorded state change, so a snapshot can be reconstructed without
storing the whole state. → [ARCHITECTURE.md](ARCHITECTURE.md)

**Digest executor** — ⚠️ the built-in dynamic-workflow executor, which is a **local
graph projection**, not proof a domain task ran. It reports
`execution_label="local_digest_projection"` and `acceptance_passed=false` until a
real executor is bound. → [DYNAMIC_WORKFLOWS.md](DYNAMIC_WORKFLOWS.md)

**Dreaming** — idle-time consolidation that folds ephemeral episodic memory traces
into higher-level semantic knowledge in a knowledge graph.
→ [MEMORY.md](MEMORY.md)

**Dynamic workflow** — the typed, evidence-gated workflow runtime: intent
perception, capability discovery, decomposition, bounded DAG waves, graph patches,
approval gates, retry/replan, evidence-gated replay, and saga compensation.
→ [DYNAMIC_WORKFLOWS.md](DYNAMIC_WORKFLOWS.md)

---

## E

**Episodic memory** — a trace of what happened, replayable later.
→ [MEMORY.md](MEMORY.md)

**Epistemic belief tracking** — separating proven empirical facts from
assumptions, so unverified claims are flagged rather than presented as fact.

**Estop (emergency stop)** — the hard stop that terminates runaway loops,
subagents, and background processes. → [SECURITY.md](SECURITY.md)

**Evidence matrix** — the finish-first audit that checks claims about completed
work against empirical evidence before completion is declared.

**Exactly-once (⚠️)** — Alpha's swarm and dynamic-workflow state is atomic JSON
checkpoints plus append-only JSONL events: **restart-recoverable for one Gateway
process.** A multi-worker deployment must provide a shared SQL lease repository
before cross-process exactly-once execution can be claimed. → [WORKFORCE.md](WORKFORCE.md)

**Execution label** — the honest disclosure attached to a dynamic-workflow
response describing what actually ran. → [DYNAMIC_WORKFLOWS.md](DYNAMIC_WORKFLOWS.md)

**Extension package** — a Python package that contributes middleware, task
lifecycle hooks, system-model observers, Gateway services, or FastAPI routers.
Declared in `config.yaml -> plugins:`; requires a Gateway restart and runs with
Gateway privileges. → [EXTENSIONS.md](EXTENSIONS.md)

---

## F

**Feature manifest** — the generated, CI-enforced registry
(`contracts/feature_manifest.json`) pinning every tool, router, middleware, and
supervisor loop. Regenerate after any registry change.

**Finish-first verification** — requiring empirical evidence before a task is
declared complete, so "done" cannot mean "I think it's done."

**Flight recorder (trajectory)** — the cryptographic log of every reasoning step,
tool call, and state transition, for forensic audit.
→ [SECURITY.md](SECURITY.md)

**Flow (LangGraph)** — a stateful, resumable execution unit in LangGraph.

---

## G

**GEO (Generative Engine Optimization)** — optimizing content so LLM answer engines
cite it. → [DISCOVERABILITY.md](DISCOVERABILITY.md)

**Gap filling** — a bounded follow-up research stage targeting evidence that is
still missing. → [DEEP_RESEARCH.md](DEEP_RESEARCH.md)

**Git shadow checkpoint** — a lightweight git reference at
`refs/alpha-checkpoints/<cid>` captured before a risky mutation, with REST
endpoints for diff and one-click rollback.

**Goal engine** — the component that tracks active objectives, enforces verifiable
completion criteria, and blocks premature or hallucinated task exits.

**Group chat** — a multi-agent collaborative room where specialised bots
brainstorm, challenge assumptions, and produce a unified deliverable.

---

## H

**Hashline editing** — deterministic line-level reading and editing that prevents
multi-line edit drift and merge conflicts.

**Harness (agent-workspace-harness)** — the importable agent framework package
(import name `alpha.*`) containing 89 engine modules. Alpha the product is built on
top of it. → [backend/AGENTS.md](../backend/AGENTS.md)

**Handoff** — a recorded transfer of work between agents, part of the workforce
layer's project memory.

**HTTP/SSE MCP transport** — Model Context Protocol over HTTP Server-Sent Events,
one of three supported transports (stdio, HTTP, SSE). → [EXTENSIONS.md](EXTENSIONS.md)

---

## I

**Idempotent creation** — swarm task creation that can be retried safely without
duplicating work.

**IndexedDB chat archive** — the uncapped on-device conversation store in the
browser, with full server-history pagination, honest degraded states, and JSON
backup/restore. → [README.md](../README.md#8-presentation-layer-desktop--web-ui)

**Intent category preset** — a named subagent configuration such as `general`,
`research`, `quick`, or `deep-research` with its own toolset and turn budget.

**Interrupt** — a LangGraph mechanism that pauses execution for human input or
approval and resumes from the same point.

---

## J

**Jev / Laya** — the System One decision models: a hosted fast structured-decision
model and a self-hosted open-weights alternative. → [SYSTEM_ONE.md](SYSTEM_ONE.md)

---

## K

**Kanban** — the real-time collaborative board where agents create, assign,
transition, and audit cards. → [WORKFORCE.md](WORKFORCE.md)

**Knowledge graph** — the semantic store that dreaming consolidates into and that
associative cross-session search queries.

---

## L

**LangGraph** — the low-level orchestration framework for long-running, stateful
agents that Alpha's runtime is built on. → [COMPARISON.md](COMPARISON.md)

**Lease fencing** — the mechanism that makes a task attempt safe against a
duplicated or zombie worker: only the current lease holder may commit.
→ [WORKFORCE.md](WORKFORCE.md)

**Leader election** — how a swarm picks a coordinator, kept separate from voting
on results.

**llms.txt** — the compact, LLM-readable overview of a project.
[`/llms.txt`](../llms.txt) · [`/llms-full.txt`](../llms-full.txt)

**Local digest projection** — see *Digest executor*.

**Loop stabilization** — the harness logic that keeps a long-running agent from
thrashing.

---

## M

**Manifest (feature)** — see *Feature manifest*.

**MCP (Model Context Protocol)** — the open protocol for exposing tools to LLM
applications. Alpha is an MCP client over stdio, HTTP, and SSE.
→ [EXTENSIONS.md](EXTENSIONS.md)

**Memory tiers** — working memory (ephemeral), episodic memory (traces), semantic
memory (knowledge graph), and cognitive memory (owner-scoped, durable).
→ [MEMORY_TYPES.md](MEMORY_TYPES.md)

**Mixture of Agents (MoA)** — querying multiple heterogeneous LLMs in parallel and
synthesizing their diverse perspectives into a high-confidence conclusion.
→ [COGNITIVE_ENGINES.md](COGNITIVE_ENGINES.md)

**Mission hierarchy** — macro goals decomposed into a tree of dependency-ordered
work queues.

**Metacognition / Kibitzer** — background supervision that detects agent loops,
thrashing, fatigue, and prompt drift.

**Middleware** — one of the 42 layers that wrap agent execution (auth, policy,
memory, budgets, guardrails, and more). → [ARCHITECTURE.md](ARCHITECTURE.md)

**Model Context Protocol** — see *MCP*.

---

## N

**Nginx** — the reverse proxy on port `2026` that is the only public entry point:
it proxies `/api/*` to the Gateway (rewriting `/api/langgraph/*` onto native
routes) and serves the frontend. → [DEPLOYMENT.md](DEPLOYMENT.md)

**Noul** — a System One typed decision meaning *no useful label / abstain*.
→ [SYSTEM_ONE.md](SYSTEM_ONE.md)

---

## O

**Orphan module** — a module with no import, no dotted-string loader path, no
config `use:` entry, and no allowlist reason. `tests/test_no_orphan_modules.py`
fails the build on any orphan.

---

## P

**Peer network** — Alpha-to-Alpha discovery, explicit pairing, and direct
delivery. Discovery is untrusted and never grants access; pairing uses a
high-entropy out-of-band code. ⚠️ Its SQLite storage is installation-scoped, not
cross-process exactly-once. → [ALPHA_PEER_NETWORK.md](ALPHA_PEER_NETWORK.md)

**Plan mode (8 dimensions)** — evaluating a task across clarity, safety,
feasibility, reversibility, resource intensity, architectural impact, empirical
evidence, and mission alignment before executing it.

**Plugin (operator-controlled)** — the top-level `plugins:` list in `config.yaml`
that causes Python code to be imported. Deliberately kept out of the API-writable
`extensions_config.json`. → [EXTENSIONS.md](EXTENSIONS.md)

**Provisioner** — the optional service on port `8002` that manages Kubernetes
sandboxes. Only present in provisioner/K8s sandbox mode.

---

## Q

**Quality council** — a deliberating body that reviews artifact quality and
validates finish-first evidence before work is declared complete.

---

## R

**Ralph loop** — a recursive self-improvement loop that runs test-driven iterative
self-healing until the suite passes and architectural invariants hold.

**Repo twin** — a shadow sandbox copy of the target repository used to preview
changes before they touch the working tree.

**Run** — one execution of a task through the durable lifecycle, with a
checkpointed state machine, budgets, and a recorded event stream.

**Run journal / event stream** — the ordered record of a run's events, persisted
for recovery and audit.

**Runtime: Runtime** — the bare, required, first parameter on any tool that needs
runtime access. Writing `Runtime | None = None` makes pydantic schema-generate
`ToolRuntime`'s `Callable` fields and breaks the entire tool list for the model.
→ [AGENTS.md](../AGENTS.md)

---

## S

**Saga compensation** — the undo path that runs when a multi-step workflow fails
partway, so partial effects are rolled back deliberately rather than abandoned.

**Sandbox tiers** — local subprocess, Docker container, and Kubernetes provisioner.
→ [SECURITY.md](SECURITY.md)

**Semantic memory** — the knowledge-graph layer. → [MEMORY.md](MEMORY.md)

**Skill** — a packaged capability: a `SKILL.md` plus optional resources, listed in
`extensions_config.json`. → [SKILLS.md](SKILLS.md)

**Skill curator** — the component that moves skills through active, stale, and
archived states, with quarantine trust tiers. → [SKILLS.md](SKILLS.md)

**Skill review** — the read-only security and compliance audit of a skill package,
using the harness-layer `review_skill_package` tool. → [SKILLS.md](SKILLS.md)

**Sleep / idle consolidation** — see *Dreaming*.

**SOUL protocol** — the per-bot state machine that defines a bot's persistent
identity, inbox, and behaviour. → [WORKFORCE.md](WORKFORCE.md)

**Stalled** — an explicit swarm state meaning progress stopped without the budget
being the cause; distinct from `budget_exhausted`.

**Subagent** — a delegated agent with its own system prompt, toolset, and turn
budget, spawned by a lead orchestrator.

**System One** — the fast, structured, provider-neutral decision layer returning
typed `choice`, `score`, and `noul` results, with confidence gating and a
deterministic fallback. → [SYSTEM_ONE.md](SYSTEM_ONE.md)

---

## T

**TOCTOU-safe state file** — a state file written atomically (temp file plus
rename) so a crash mid-write cannot corrupt it.

**Token budget** — a deterministic per-run ceiling on token consumption, with
cache-aware cost telemetry. → [SECURITY.md](SECURITY.md)

**Trajectory flight recorder** — see *Flight recorder*.

**Trigger / wake gate** — a pre-flight check that decides whether a scheduled
occurrence should run at all. → [PRODUCTION.md](PRODUCTION.md)

**Theory of Mind (ToM)** — simulating user mental models, stakeholder
expectations, and downstream receiver perspectives before acting.
→ [COGNITIVE_ENGINES.md](COGNITIVE_ENGINES.md)

---

## U

**Untrusted observation** — content posted to a swarm blackboard that is treated as
data, never as instruction. → [WORKFORCE.md](WORKFORCE.md)

**Use-case page** — [USE_CASES.md](USE_CASES.md), the map from goal to subsystem.

---

## V

**Vault** — the scoped credential store; secrets are handed to tools, never
printed into prompts. → [SECURITY.md](SECURITY.md)

**Verify (run)** — ⚠️ independent confirmation that a completed run actually
achieved its acceptance criteria. A run can finish successfully and remain
unverified. → [PRODUCTION_READINESS_INVENTORY.md](PRODUCTION_READINESS_INVENTORY.md)

**Visual verification** — rendering a generated UI artifact and asserting against
the DOM plus a screenshot comparison.

---

## W

**Wake gate** — see *Trigger / wake gate*.

**Watchdog** — the process that detects a hung or dead component and restarts it,
distinguishing *holding a port but not answering HTTP* from *not running*.
→ [PRODUCTION.md](PRODUCTION.md)

**Working memory** — ephemeral per-turn context. Deliberately **not** persisted
into memory snapshots. → [MEMORY.md](MEMORY.md)

**Work queue** — the dependency-ordered task list derived from a mission hierarchy.

**Workforce layer** — bots, DMs, inboxes, swarms, group chat, projects,
constitutions, ADRs, handoffs, goals, conflicts, and Kanban.
→ [WORKFORCE.md](WORKFORCE.md)

---

## Related documents

- [README.md](../README.md) — overview, comparison summary, and quickstart
- [FAQ.md](FAQ.md) — short answers
- [COMPARISON.md](COMPARISON.md) — Alpha vs. other frameworks
- [USE_CASES.md](USE_CASES.md) — goals mapped to subsystems
- [ARCHITECTURE.md](ARCHITECTURE.md) — the planes, in depth
- [MEMORY_TYPES.md](MEMORY_TYPES.md) — the canonical memory taxonomy
- [DISCOVERABILITY.md](DISCOVERABILITY.md) — how this project is optimized for SEO, GEO, and AEO
