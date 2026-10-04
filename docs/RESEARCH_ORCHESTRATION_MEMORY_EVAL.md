# Research: Orchestration, Protocols, Memory, Evaluation

Date: 2026-09-29. Worktree: `alpha-r4-orchestration`, branch `research/r4-orchestration`.
Scope: orchestration frameworks, agent-interop protocols, memory SOTA, agent evaluation.
Live research is mandatory for this slice and was done today; every substantive
external claim carries a URL and the retrieval date 2026-09-29. Code citations
are against this worktree.

## 1. Method

**Fetched live (2026-09-29, via websearch):** LangGraph release state (PyPI,
GitHub releases, langchain.com blog); MCP spec revision
(modelcontextprotocol.io, MCP blog, Google/Cloudflare blogs); A2A status
(Linux Foundation press, a2aproject/A2A repo, adoption analyses); AG-UI
(ag-ui-protocol/ag-ui repo, CopilotKit docs); ACP Zed (zed.dev, agentclientprotocol.com);
framework landscape (Alice Labs Aug-2026 survey, Langfuse comparison, Braintrust,
requesty.ai, morphllm.com, Firecrawl); OpenAI/Claude/Google SDK versions;
LlamaIndex Workflows 1.0 announcement; memory SOTA (Atlan episodic-memory survey,
Letta/Mem0/Zep comparisons, memorywire arXiv 2606.01138, MPBench/arXiv 2606.04329,
MINJA, Mem0 security survey, OWASP ASI06); benchmarks (swebench.com official,
llm-stats.com, benchlm.ai, steel.dev aggregator, tbench.ai official Terminal-Bench,
GAIA HF leaderboard, taubench.com, OpenRouter τ² snapshot, LiveBench, Berkeley
"How We Broke Top AI Agent Benchmarks", Epoch AI).

**Could not reach / did not verify:** official docs pages requiring JS
(taubench.com leaderboard body, docs.ag-ui.com revision number); exact AG-UI
spec revision number (repo shows a 2026-09-23 release but no version string was
captured — marked UNVERIFIED below); Haystack and DSPy exact current versions
(only covered via secondary comparisons); WebArena/OSWorld official leaderboards
directly (used steel.dev index + official system cards as sources); any
paywalled model system cards beyond quoted excerpts.

**Alpha code read (not inferred):** `backend/packages/harness/alpha/workflow/`
(directory listing), `orchestrator/` (listing + `acp_binding.py` fully),
`runtime/checkpoint_mode.py` (fully), `runtime/checkpoint_state.py` (§1–80),
`runtime/side_effects/AGENTS.md` (fully), `agents/lead_agent/agent.py`
(grep: graph factory + middleware list), `agents/memory/recall_safety.py`
(§1–60), `memory/cognitive/AGENTS.md` (fully), `mcp/AGENTS.md` (fully),
`mcp/client.py` (grep), `benchmarks/release_gate.py` (fully),
`config/acp_config.py` (grep), `peer_network/models.py|transport.py|discovery.py|service.py`
(grep), `backend/AGENTS.md`, `backend/packages/harness/alpha/AGENTS.md`
(switched read: swarm/dynamic-workflow/bot sections), `backend/pyproject.toml`
+ `backend/packages/harness/pyproject.toml` (dependency pins),
`docs/DYNAMIC_WORKFLOWS.md`, `docs/MEMORY.md`, `docs/DEVELOPMENT.md`
(grep), `docs/PRODUCTION_READINESS_INVENTORY.md` (grep).

## 2. Orchestration landscape (live, 2026-09-29)

| Framework | Current version (retrieved 2026-09-29) | Distinguishing idea | vs LangGraph | License |
|---|---|---|---|---|
| LangGraph (+ Platform/Deployment) | **1.2.12** (PyPI/GitHub releases; 1.0 milestone announced on langchain.com blog; checkpoint 4.2.0, checkpoint-postgres 3.1.2). Alpha pins `langgraph>=1.2.9,<1.3` (`backend/packages/harness/pyproject.toml:30`). | Explicit state-machine graphs: nodes/edges/reducers, durable checkpoints, interrupts, time-travel, subgraph streaming. The control-plane framework. | Baseline. | MIT |
| Microsoft Agent Framework 1.0 | **1.0 GA 2026-04-03** (devblogs.microsoft.com). Direct successor merging **Semantic Kernel + AutoGen**; C#+Python parity; native MCP + A2A. | Enterprise consolidation play: one SDK, declarative YAML agents, sequential/concurrent/handoff/group-chat/Magentic-One patterns. | Native protocol cards; LangGraph has the deeper checkpoint/time-travel story. | MIT (approx; verify per repo) |
| AutoGen / AG2 | Microsoft AutoGen in **maintenance since Oct 2025**; community fork **AG2 0.12.2**, pre-1.0. (Alice Labs, retrieved 2026-09-29.) | Conversational multi-agent (speaker selection, group chat). Now a lineage, not a product. | Do not start new work on it; MAF is Microsoft's answer. | Apache 2.0 |
| CrewAI | **1.14.7** (2026-06-11): pluggable memory/knowledge/RAG/flow backends, Chat API, Snowflake Cortex provider. Native MCP + A2A per uvik.net. | Role-based crews (role/goal/backstory) + event Flows. Lowest time-to-first-multi-agent-demo. | Ergonomics over control; no checkpoint/time-travel equivalent. | MIT |
| LlamaIndex Workflows | **1.0 stable** (llamaindex.ai blog, "announcing-workflows-1-0"). | Event-driven steps + native deep-retrieval/RAG layer; dedicated multi-agent construct with human-in-the-loop. | Retrieval-first; graph control thinner than LangGraph. | MIT |
| Pydantic AI | **V2 stable 2026-06-23** ("capabilities" primitive unifying tools/hooks/instructions/settings; harness with 40+ features; V1 Sep 2025 supported). | Type-safe, DI-driven, validated structured output with auto-retry; FastAPI feeling. | DX/types over graph explicitness; durable execution now built in. | MIT |
| Mastra | **1.x, `@mastra/core` ~1.35**; 1.0 Jan 2026; observational memory, enterprise RBAC. Users incl. Replit, PayPal, Sanity. | Full-stack TypeScript: agents + workflows + memory + RAG + evals + tracing in one package. Fastest median latency in one test survey. | TS-only; docs lag the framework. No Python story. | Apache 2.0 |
| OpenAI Agents SDK | **v0.15.1** (requesty.ai, Jun 2026). Python+TS, 27k+ stars. | Lightweight agent loop + sandbox execution, voice support, swappable providers. | Runtime-first, not graph-first; multi-agent basic. | MIT |
| Claude Agent SDK | Evolved from Claude Code; Python+TS. (No exact version captured — UNVERIFIED.) | Tool-rich loop, deep OS/file/shell access, subagents with isolation, "give the agent a computer". | Best coding-agent substrate; Anthropic-native. | UNVERIFIED (check repo) |
| Google ADK | **2.0** (graph workflows, agent teams); Java 1.0 + Go 1.0 early 2026; 4 languages (Py/TS/Go/Java/Kotlin); native A2A. | Hierarchical agent trees, software-dev idioms, widest language support, Vertex integration. | Closest LangGraph rival on orchestration + broader language reach. | Apache 2.0 |
| Haystack | Current version UNVERIFIED (not reached directly). Coordinator/specialist via `ComponentTool`; deterministic pipelines heritage. | Search-pipeline determinism; self-hosted + Enterprise. | Pipelines over free-form graphs. | Apache 2.0 |
| DSPy | Current version UNVERIFIED. | Prompt optimization as compilation (assertions, optimizers). Complementary, not rival. | Compiles prompts; doesn't run graphs. | MIT (verify) |
| 2026 entrants (from surveys) | Strands Agents (AWS), Agno (ex-Phidata, high-throughput swarms), Smolagents, Vercel AI SDK, mcp-agent (MCP-native), Letta (memory runtime). | Each owns one axis (cloud, throughput, minimalism, TS, MCP-native, memory). | Point solutions; none matches LangGraph's control + ecosystem breadth. | Mixed |

Alpha's position: it builds on `langchain.agents.create_agent`
(`backend/packages/harness/alpha/agents/lead_agent/agent.py:33`) with a large
middleware stack (clarification, memory, summarization, todo, token-usage,
subagent-limit, view-image, loop-detection, title, safety/finish-reason —
`agent.py:38-53`), i.e. LangChain 1.x idioms on LangGraph 1.2.x — plus its own
DAG layers on top (Swarm v2, dynamic-workflow engine, ExecutionKernel). That is
a defensible "LangGraph for control, custom DAGs for autonomy" split, but it
means Alpha inherits LangGraph's process-local checkpoint limits (see §8).

## 3. Protocol status (live, 2026-09-29)

### MCP (Model Context Protocol)
- **Current revision: 2026-07-28** (modelcontextprotocol.io/specification/2026-07-28;
  MCP blog "The 2026-07-28 Specification"). **Moving, not stable**: the headline
  change is dropping session state/handshake for a **stateless core**
  (`MCP-Protocol-Version` + `Mcp-Method` headers, client info in `_meta`),
  aimed at horizontal scaling of remote servers. Prior dated revisions
  (2024-11-05, 2025-03-26, 2025-06-18) are superseded.
- Adoption: 200+ server implementations (morphllm.com survey); native in
  Microsoft Agent Framework, CrewAI, Google ADK; Cloudflare `agents/mcp`
  handler; Python/TypeScript SDKs (`@modelcontextprotocol/server` v2-style API).
- **Alpha implements MCP as a client**: `backend/packages/harness/alpha/mcp/`
  via `langchain-mcp-adapters` `MultiServerMCPClient`
  (`mcp/AGENTS.md:3`), transports stdio/SSE/HTTP (`mcp/AGENTS.md:11`),
  pinned `langchain-mcp-adapters>=0.2.2`
  (`backend/packages/harness/pyproject.toml:26`). Strengths beyond a thin
  client: durable long-running task runtime with lease-fenced polling
  (`mcp/AGENTS.md:4-7`, `mcp/AGENTS.md:38-41`), per-user/per-request credential
  interceptors with fail-closed header validation (`mcp/AGENTS.md:13-16`),
  stdio allowlist launch policy (`mcp/AGENTS.md:30-36`).
- **Gap**: no MCP *server* surface found (Alpha does not expose its own tools
  over MCP — UNVERIFIED exhaustively, but no server module exists under `mcp/`);
  protocol-version negotiation against 2026-07-28 stateless servers not
  confirmed — `mcp/client.py` grep for `protocolVersion|2026` returned **no
  matches**, so version handling rides entirely on `langchain-mcp-adapters`.
  Effort M to track the stateless revision explicitly.

### A2A (Agent2Agent)
- **Status: v1.0 stable, first stable spec; Linux Foundation project
  (donated by Google); 150+ orgs, Apr 2026 press.** Concepts: Agent Card
  (`.well-known/agent-card.json`), Message, Task. Multi-protocol support added
  in 1.0. License Apache 2.0 (`github.com/a2aproject/A2A`).
- **Alpha: PARTIAL, deliberately A2A-like but not A2A.** `peer_network/models.py:5-6`
  states alignment "with the A2A concepts (Agent Card, Message, Task-like
  request/result kinds) without pretending to implement every A2A SDK feature";
  own envelope `application/alpha-a2a+json`, `PROTOCOL = "alpha-a2a"`
  (`models.py:18`), own version `"1.0"` (`service.py:741`), own discovery token
  `b"protocol": b"alpha-a2a"` (`discovery.py:289`), Agent-Card-shaped fetch at
  `.well-known/agent-card.json` (`transport.py:125`). No A2A SDK dependency,
  no cross-vendor interop. Honest naming, but an external A2A agent cannot talk
  to an Alpha peer today. Effort M–L for a real A2A adapter.

### AG-UI (Agent–User Interaction Protocol, CopilotKit)
- **Status: moving fast; GitHub `ag-ui-protocol/ag-ui` shows a release dated
  2026-09-23; .NET SDK 1.0 on NuGet; Microsoft Agent Framework docs list AG-UI
  as a supported UI integration.** Event-based agent↔frontend protocol;
  CopilotKit first-party client. Exact spec revision number UNVERIFIED
  (docs.ag-ui.com needs JS; release tag text not captured).
- **Alpha: ABSENT.** No `ag_ui`/AG-UI module, event vocabulary, or frontend
  client binding found (grep for `ag_ui` returned only unrelated ACP hits).
  Alpha streams via its own Gateway SSE framing + `StreamBridge`
  (`backend/AGENTS.md` runtime section). Effort M to adopt; risk is tracking a
  moving spec.

### ACP — two different protocols share the name; Alpha implements the Zed one
- **Zed Agent Client Protocol** (editor↔coding-agent, JSON-RPC over stdio;
  zed.dev/acp, agentclientprotocol.com; ACP Registry live; JetBrains
  co-building): **Alpha PRESENT as a consumer.** `config/acp_config.py:1`
  ("ACP (Agent Client Protocol) agent configuration"), `ACPAgentConfig`
  subprocess launch (`acp_config.py:11-33`), `invoke_acp_agent` tool + prompt
  section (`agents/lead_agent/prompt.py:1238-1258`), `/mnt/acp-workspace`
  result handoff (`prompt.py:1257-1258`), thread→agent binding registry
  (`orchestrator/acp_binding.py:14-31`).
- **"Agent Client Protocol" as generic agent-interop standard**: Alpha has no
  other ACP interop surface; the A2A/AG-UI rows above are the relevant ones.
  No confusion found in code, but docs should disambiguate the two ACPs.

## 4. Memory state of the art (live, 2026-09-29)

**Decomposition (settled):** episodic (instance-specific events) / semantic
(facts, relations) / procedural (workflows, skills, prompt updates) + working
memory, per Tulving/Squire via memorywire (arXiv 2606.01138, Jun 2026).
**Consolidation** (episodic→semantic reflection, cf. Generative Agents
recency/relevance/salience) is "the single most impactful and least-implemented
stage" (Atlan survey). Timing norm: background consolidation every ~50–200
episodes; fidelity loss of naive summarization is unquantified in public.
Lineage: **MemGPT paper → Letta** (self-editing memory blocks, archival/recall;
"MemGPT" now names the pattern, "Letta" the framework — evermind.ai comparison);
competitors **Mem0** (add/update/delete/expire write path, dedup only),
**Zep/Graphiti** (temporal knowledge graph, best invalidation story),
**LangMem** (episodic few-shots + procedural prompt optimizer),
**Cognee, MemoryOS, MemTensor**; proposed standard **memorywire**
(RRF + four-type taxonomy + STM/LTM lifecycle ops).
**Context-window management:** summarization/compaction near limits (industry
norm); retrieval-vs-recurrence is settled as hybrid (arXiv:2404.00573:
hybrid beats single-type, esp. with pre-trained semantic).
**Production pattern:** per-user namespaced stores, vector + graph retrieval,
rerank, TTLs/expiry bounding the blast radius.

**Failure modes (active research area):**
- **Memory poisoning** — the headline risk: MINJA (Dong et al. 2025, NeurIPS:
  query-only injection, >95% injection / ~70% attack success); AgentPoison
  (NeurIPS 2024, trigger-token backdoors); MemoryGraft (Dec 2025, poisoned
  "successful experiences"); MPBench/arXiv 2606.04329 (Jun 2026: avg 50.46%
  ASR, aggressive write/retrieval = more exploitable — capability/security
  tension); Unit42 Oct 2025 (Bedrock); OWASP **ASI06** memory/context
  poisoning (2026 top-10). Defenses: provenance, trust-aware retrieval +
  temporal decay, expiry, audit, quarantine — but A-MemGuard finds LLM
  detectors miss 66% of poisoned entries.
- **Stale memory** (Zep's temporal invalidation is the best published answer;
  most stores lack it), **over-retrieval** (precision collapse, prompt
  flooding), catastrophic forgetting via dedup-without-care.
- **Memory-scoped ACLs**: production norm is owner-namespaced isolation; true
  per-record ACLs remain rare (UNVERIFIED as a productized standard).

**Alpha's memory (code-read):** multi-store (`agents/memory/`:
manager/tools/recall/summarization-hook/user-model; `memory/` subpackages incl.
`cognitive/` with episodic/semantic-graph/procedural/working/consolidation/
spatio-temporal modules). Notable: **`recall_safety.py:1-32`** treats recalled
memory as data-not-instructions with explicit in-prompt marking
(`RECALL_DATA_NOTICE`, `recall_safety.py:49-53`), structural containment of
`</memory>` (`:58-60`), per-item 500-char and per-block 8k caps
(`:42-45`) — a real, code-level poisoning mitigation most frameworks lack.
`memory/cognitive/AGENTS.md` owns server-resolved owner scoping
(`engine.py:41`), atomic fsync snapshots (`engine.py:117`), fail-closed corrupt
state, and states the single-process limit explicitly (`:21-24`). MEMORY.md
documents episodic consolidation as idle-time pattern extraction
(`docs/MEMORY.md:373`).

## 5. Evaluation landscape (live, 2026-09-29)

Scoring key used below: **A** = fully automated (tests/checkers), **J** =
model-judged, **H** = human. Trust key: **T** = trustworthy for decisions
(reproducible, independent), **C** = caution (self-reported/vendor-run or
contaminated), **U** = unusable as cited.

| Benchmark | What it measures | Scoring | Contamination / integrity notes | Top entries (retrieved 2026-09-29) |
|---|---|---|---|---|
| SWE-bench Verified (500 Python issue-fix tasks) | Real-world GitHub issue resolution | **A** (FAIL_TO_PASS/PASS_TO_PASS tests) | Saturated (>95%); training-on-test risk high; vendor self-reports dominate aggregators | Official swebench.com (mini-SWE-agent harness, Feb 2026): Claude 4.5 Opus high 76.8%, Gemini 3 Flash 75.8%, MiniMax M2.5 75.8% (**T** within one harness). Aggregators claim Claude Fable 5 95% / Opus 5 96–97% — **C**: self-reported, cross-harness, treat as marketing until reproduced |
| SWE-bench Pro (731 tasks, harder) | Harder/larger issue-fix set | **A** | Berkeley RDI 2026: exploit agent hit **100%** via in-container parser overwrite — sandbox-trust flaw, not model skill | Vendor-reported: Claude Opus 5.5 89.9%, Fable 5 80% (**C**). Official independent board preferred; none captured — UNVERIFIED |
| Terminal-Bench 1.x/2.x (89+ terminal tasks) | Shell-first task completion in containers | **A** | Version confusion (1.0 vs 2.0 vs 2.1 vs 4.0/Hard); aggregator numbers conflict with official | Official tbench.ai: GPT-6 Astra 58.2%, Opus 5 53.9%, Fable 5 44.5% (**T**). TB-2.0 aggregators claim GPT-5.5 82–82.7% (**C**: different task set/version — never compare across versions) |
| GAIA (general assistant, L1–L3) | Web browsing + tools + reasoning, exact-match answers | **A** (quasi-exact match; some J assistance historically) | Official HF board dominated by multi-model ensembles at 90–93% (saturated, ensemble-harness effects); question leakage into training discussed publicly | Official HF: CustomGPT.ai v44 93.36%, Co-Sight Pro 93.02% (Jun 2026, **T** as harness numbers, **U** as model numbers). Bare-model boards: GPT-5 Mini 44.8% |
| τ-bench (retail/airline tool-user) | Tool-call policy compliance with simulated user | **A** (DB state check) + simulated user (**J**) | User-sim model choice (gpt-5.2) moves scores; τ³-bench adds voice/knowledge | taubench.com: GPT-5.5 44.6%, Opus 4.7 40.2% (Apr 2026, **T**). τ² telecom/airline: OpenRouter snapshot exists; top-model numbers UNVERIFIED here |
| τ²-bench | Dual-control agent+user shared-state convo | **A** + **J** | New; leaderboard submission bar low | See taubench.com; no trusted top number captured — UNVERIFIED |
| WebArena (812 web tasks) | End-to-end web navigation | **A** (state/URL match) | Berkeley RDI: **~100%** via config leakage + DOM/prompt injection; self-hosted variance | steel.dev index: WebTactix 74.3% (Feb 2026), OpAgent 71.6% (**C**: self-reported). Assume compromised-by-default without pinned harness |
| OSWorld (369 desktop tasks; 2.0 exists) | OS GUI control, screenshots | **A** (VM state scripts) | Berkeley RDI: 73% via VM-state manipulation + public gold files; v1 vs 2.0 incomparable | OSWorld 2.0: Claude Fable 5.1 77.9% (system card, Sep 2026, **C–T**); OSWorld 1.0: Qwen3.8-Max 86.1% (**C**) |
| AgentBench (multi-env incl. OS/KG) | Cross-environment agent generality | **A** | Aging; recent top rows are RL-finetune submissions (AgentRL 60–70%, Oct 2025) | AgentRL/Qwen2.5-32B 70.4% (**T** as published result, narrow meaning) |
| LiveBench (monthly-refresh LLM eval) | General LLM skill, contamination-resistant by design | **A** | Refresh cycle is the defense; agent-specific signal weak | livebench.ai: GPT-5.6 Luna 73.6, GLM-5.2 73.2 (**T** for models, **U** for agent decisions) |

**Which numbers to trust for a decision:** official-harness, pinned-version,
independently reproduced A-scored results (swebench.com mini-SWE-agent rows,
tbench.ai rows, taubench.com rows, HF GAIA agent rows as *system* numbers).
**Which not to trust:** vendor system-card figures, cross-aggregator mixes,
any number without a benchmark version + harness + date, anything ≥90% on a
2023–2024-era static set (assume saturation/contamination), and all WebArena /
SWE-bench-Pro figures post-Berkeley-RDI without exploit-mitigation notes.
Alpha's own `release_gate.py` defaults (0.80 task success; 1.00 authorization
isolation/recovery/injection resistance) are gates, not measurements — see §6.

## 6. Alpha gap analysis

| Capability | Status | file:line | Effort | Real gap or deliberate choice |
|---|---|---|---|---|
| Graph orchestration (LangGraph) | PRESENT | `agents/lead_agent/agent.py:33` (`create_agent`); harness pins `langgraph>=1.2.9,<1.3` (`backend/packages/harness/pyproject.toml:30`) | — | Choice; 1.2.12 current, pin lags one minor |
| Multi-node DAG + waves/retry/budgets/approvals/compensation | PRESENT | `alpha/AGENTS.md:266-354` (DWE + ExecutionKernel contract); `workflow/` (24 modules); `orchestrator/loop.py`, `domain_executors.py` | — | Real, ahead on honesty plumbing |
| Delta-channel checkpointing + mutation graphs | PRESENT | `runtime/checkpoint_mode.py:1-142`; `runtime/checkpoint_state.py:1-66` | — | Real, ahead (see §8) |
| Side-effect ledger w/ first-class UNKNOWN | PRESENT | `runtime/side_effects/AGENTS.md:1-83`; `side_effects/ledger.py`, `statuses.py` | — | Real, ahead (see §8) |
| MCP client (stdio/SSE/HTTP) + durable tasks | PRESENT | `mcp/AGENTS.md:3-41`; `mcp/client.py`; `mcp/tasks/` | — | Real; best-in-class task runtime |
| MCP server (expose Alpha tools via MCP) | ABSENT | no server module under `mcp/` (UNVERIFIED exhaustively) | M | **Real gap** — frameworks (MAF, ADK, CrewAI) are bidirectional |
| MCP 2026-07-28 stateless tracking | PARTIAL | version handling delegated to `langchain-mcp-adapters>=0.2.2`; no `protocolVersion` reference in `mcp/client.py` (grep: no match) | M | Real gap (spec moved under us) |
| A2A interop | PARTIAL (A2A-shaped, proprietary) | `peer_network/models.py:5-6,18`; `transport.py:125`; `service.py:741`; `discovery.py:289` | M–L | Deliberate (LAN-first, pairing model) but a **real interop gap** |
| AG-UI streaming interop | ABSENT | no `ag_ui` surface (grep: no match) | M | Real gap; spec still moving, so waiting is defensible |
| ACP (Zed) consume | PRESENT | `config/acp_config.py:1-47`; `agents/lead_agent/prompt.py:1238-1258`; `orchestrator/acp_binding.py:14-41` | — | Real |
| Episodic/semantic/procedural/working split | PRESENT | `memory/cognitive/` (`episodic_memory.py`, `semantic_graph.py`, `procedural_memory.py`, `working_memory.py`, `consolidation.py`) | — | Real |
| Recall-time injection containment | PRESENT | `agents/memory/recall_safety.py:1-60` | — | Ahead of most frameworks |
| Consolidation fidelity measurement | PARTIAL | idle-time extraction (`docs/MEMORY.md:373`); no published loss metric | S–M | Real gap (field-wide, not just Alpha) |
| Per-record memory ACLs | PARTIAL | owner-scoped isolation (`memory/cognitive/AGENTS.md:3-10`); record-level ACLs not found | M | Real gap; owner-scoping is the honest subset |
| Stale-memory invalidation (temporal) | UNVERIFIED | not confirmed in reads; Zep-style temporal invalidation not referenced | S–M | Suspected gap |
| External benchmark harness (SWE/Terminal/GAIA/τ) | ABSENT | `benchmarks/` = `arena.py`, `release_gate.py`, `runner.py`, `suites.py` only; `backend/scripts/benchmark/` = context_snapshot/deermem_eviction/concurrency (internal micro-benchmarks per `docs/DEVELOPMENT.md:888-943`) | M–L | **Deliberate** (no external harness) but leaves all external claims ungrounded |
| Release gate | PRESENT (as gate, not evidence) | `benchmarks/release_gate.py:31-53` (fail-closed on missing metrics, `:40-47`) | — | Honest mechanism; defaults must not be read as results |
| Cross-process exactly-once | ABSENT (declared) | `docs/PRODUCTION_READINESS_INVENTORY.md:31`; swarm AGENTS (`alpha/AGENTS.md:166-175`) | L | Deliberate, honestly declared |
| Digest executor = projection, not work | PRESENT (declared) | `docs/DYNAMIC_WORKFLOWS.md:45-50`; `alpha/AGENTS.md:290-299` | — | Honest; the claim to verify, verified |

## 7. Documented-vs-actual divergence (most valuable section)

**Verdict first: no material dishonesty found in this slice. The honesty
machinery the root AGENTS.md demands is present in the code I read.** The
following are the closest things to divergences, graded:

1. **No divergence — digest executor honesty holds.** Root AGENTS.md requires
   the digest executor always be called a local graph projection. Code/AGENTS
   agree: `alpha/AGENTS.md:290-299` (`local_digest_projection`,
   `acceptance_passed=false`, never domain-task acceptance) and
   `docs/DYNAMIC_WORKFLOWS.md:45-50` (`execution_label:
   "local_digest_projection"`). PRESENT on both sides.
2. **No divergence — no cross-process exactly-once anywhere checked.**
   Inventory states process-local wave concurrency + durable store and names
   shared lease coordination as a precondition
   (`docs/PRODUCTION_READINESS_INVENTORY.md:31`); swarm persistence is "atomic
   and restart-recoverable for a single Gateway, but not a shared SQL lease
   repository" (`alpha/AGENTS.md:166-175`); cognitive memory is "single-process
   contract only" (`memory/cognitive/AGENTS.md:21-24`). All three layers agree.
3. **No divergence — release gate is evidence-demanding, not evidence-claiming.**
   `benchmarks/release_gate.py:40-47` fails closed on missing/non-numeric
   metrics; `backend/AGENTS.md:46-52` says CI must supply real values rather
   than treating defaults as evidence. A reader could still misread the 0.80 /
   1.00 defaults as "Alpha achieves…", but the code refuses that reading.
4. **Minor gap (doc precision, not dishonesty): MEMORY.md's transmission
   encoding.** `docs/MEMORY.md` contains non-UTF8/mojibake bytes around
   lines 25–27 (visible as `U+FFFD` in grep output). Cosmetic; S effort. Also the
   repo's UTF-8 fragility history (MULTI_AGENT_PLAN.md:17-20) makes this worth
   a check pass.
5. **Minor gap: ACP naming collision unflagged.** Two industry protocols share
   "ACP" and Alpha implements Zed's; no doc line disambiguates them. A reader
   mapping "ACP: implemented" onto agent-interop ACP would be misled. S effort:
   one clarifying paragraph.
6. **Minor gap: A2A-adjacent naming.** `PROTOCOL = "alpha-a2a"`
   (`peer_network/models.py:18`) and `.well-known/agent-card.json`
   (`transport.py:125`) borrow A2A's distinctive names for a proprietary
   envelope. The models.py disclaimer (`:5-6`) is honest, but the wire names
   invite interop assumptions. S effort: document "not wire-compatible with
   A2A v1.0" at the router + doc level.
7. **Watch item (UNVERIFIED, needs a protocol owner): MCP spec drift.**
   No `protocolVersion`/2026-07-28 handling in Alpha's own code (grep
   `mcp/client.py`: no match); correctness rides on
   `langchain-mcp-adapters>=0.2.2` (unbounded above, so drift arrives
   silently). Not a divergence today — but the stateless revision is exactly
   the kind of upstream move that creates one. Recommend pinning + a
   version-negotiation test (M effort).
8. **Temporal/staleness story UNVERIFIED.** Consolidation-at-idle is documented
   (`docs/MEMORY.md:373`); whether retrieval discounts stale memories
   (Zep-style temporal invalidation) was not confirmed in the files read. If
   docs elsewhere claim it, that claim needs a file:line. Flagged for the
   memory owner, not asserted.

## 8. What Alpha already does better than the frameworks (verified, with file:line)

1. **Dual-mode checkpoint channels with fail-closed migration.**
   `runtime/checkpoint_mode.py:1-142`: process-frozen mode, per-checkpoint
   metadata markers, full→delta transparent reads, full-mode-opening-delta
   raises `CheckpointModeMismatchError` instead of materializing empty state
   (`:114-135`), hot-switch reconfiguration refused (`:39-45`). LangGraph
   ships the checkpoint primitives; Alpha ships the *migration-safety
   discipline* around them. Verified as claimed.
2. **Side-effect ledger with first-class UNKNOWN.**
   `runtime/side_effects/AGENTS.md:21-50`: three honest outcomes, lease-bound
   entries, `reclaim_expired()` → UNKNOWN, exactly-one-exit reconciliation,
   UNDETERMINED reopens rather than resolving, `UNKNOWN→COMPLETED` absent from
   the transition table by design. No surveyed framework documents an
   equivalent per-effect unknown-state machine; this is genuinely ahead.
3. **Recall-time injection containment as shared infrastructure.**
   `agents/memory/recall_safety.py:1-60`: every memory surface gets data
   marking + `</memory>` neutralization + size caps as pure transforms. The
   MPBench finding (aggressive retrieval = more exploitable) makes this the
   right layer; most frameworks leave it to each integration.
4. **Durable MCP task runtime outside the agent loop.**
   `mcp/AGENTS.md:4-7,38-41`: lease-claimed polling, cancellation fencing,
   idempotent delivery, dead-lettering, 503-when-no-worker honesty. This is a
   production operations story LangGraph/MCP SDKs don't bundle.
5. **Claim-hygiene as architecture.** The digest-projection labeling, the
   no-exactly-once preconditions, the fail-closed release gate, and the
   per-module "deliberately absent / not yet implemented" lists (e.g.
   `runtime/side_effects/AGENTS.md:51-61`) form a system most frameworks lack:
   the docs cannot drift because the code refuses the dishonest state.

## 9. Ranked recommendations (top ten)

1. **Add an MCP *server* surface (expose Alpha tools over MCP).** Why: every
   major framework is now bidirectional; Alpha can consume 200+ servers but
   cannot serve. Effort M. Risk: auth/ACL design on the serving path. Caveat:
   the 2026-07-28 stateless spec is the right target, and it is still bedding
   down — build behind a flag.
2. **Pin and test MCP protocol-version negotiation (2026-07-28).** Why: silent
   drift via unbounded `langchain-mcp-adapters` dependency. Effort M. Risk
   low. Caveat: may require upstream adapter changes first; the test is the
   deliverable even if the fix waits.
3. **Ship a real A2A v1.0 adapter (inbound Agent Card + Task).** Why: MAF and
   ADK speak it natively; Alpha's LAN peer net is honest but isolated. Effort
   M–L. Risk: pairing-token model vs A2A auth must be reconciled, not
   bypassed. Caveat: keep `alpha-a2a` for LAN; A2A is the WAN/interop face.
4. **Run one external benchmark harness end-to-end (τ-bench first).**
   Why: cheapest A-scored agent benchmark that exercises Alpha's actual
   strengths (tool policy, multi-turn state); grounds every future claim.
   Effort M. Risk: score will be modest initially — that is the point.
   Caveat: never tune to the set; pin version + harness + date per §5 rules.
5. **Add temporal/staleness handling to memory retrieval (or document its
   absence).** Why: the one SOTA axis where Alpha's story is thinnest vs
   Zep/Graphiti. Effort S–M. Caveat: UNVERIFIED whether partially present —
   audit first.
6. **Add per-record memory provenance + trust tiers.** Why: MPBench/MINJA show
   provenance is the durable defense; recall_safety is containment, not
   provenance. Effort M. Risk: schema migration on memory stores. Caveat:
   detectors miss 66% (A-MemGuard) — provenance bounds, doesn't solve.
7. **Adopt AG-UI event vocabulary for the web stream (behind compat seam).**
   Why: CopilotKit clients + MAF interop for free. Effort M. Risk: spec still
   moving (2026-09-23 release); track, don't marry. Caveat: keep native SSE
   framing as canonical until AG-UI stabilises.
8. **Disambiguate the two ACPs + A2A-wire-compat in docs.** Why: cheapest
   honesty fix in the report (§7.5, §7.6). Effort S. No risk. Caveat: none.
9. **Fix MEMORY.md encoding + audit memory-doc claims to file:line.** Why:
   mojibake at `docs/MEMORY.md:25-27`; every memory claim needs a code
   anchor per §7.8. Effort S. Caveat: respect UTF-8 handling rules
   (MULTI_AGENT_PLAN.md:17-20) — use the edit tool, not shell round-trips.
10. **Evaluate consolidation fidelity (measure, don't just run).** Why: the
    field's least-measured stage; whoever measures it first gets a real
    differentiator. Effort M (needs `deermem_eviction`-style harness +
    LongMemEval-style oracle, cf. `docs/DEVELOPMENT.md:913-922`). Caveat:
    result may show real loss — publish it anyway; the honesty brand survives
    bad numbers, not hidden ones.

## 10. Confidence and gaps

- **High confidence:** MCP revision (official spec site + blog), A2A v1.0 +
  Linux Foundation status, LangGraph 1.2.12, MAF 1.0 GA, CrewAI 1.14.7,
  Pydantic V2, LlamaIndex Workflows 1.0, ADK 2.0 shape, memory failure-mode
  literature (multiple primary sources agree), Alpha code findings in files
  fully read (checkpoint_mode, side_effects, recall_safety, release_gate,
  acp_binding, cognitive AGENTS).
- **Medium confidence:** aggregator leaderboard numbers (reported with trust
  grades, not as facts); framework license details; exact Alpha lead-graph
  runtime behavior (factory + middleware list confirmed, full assembly flow
  not traced); MCP server absence (no module found, not exhaustively proven).
- **UNVERIFIED / needs follow-up:** AG-UI spec revision number; Haystack/DSPy
  current versions; Claude Agent SDK version; τ² top numbers; official
  SWE-bench-Pro independent board; stale-memory handling in Alpha retrieval;
  `docs/INDEX.md` registration for this file (the generator fails closed on
  unclassified `docs/` Markdown — the merge owner must add the FILE_OVERRIDES
  entry; deliberately not touched: report-only mandate).
