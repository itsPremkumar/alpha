# Alpha — Complete Feature List

> Every capability of the Alpha AI agent system, one line per feature.
> Source of truth: `README.md` feature catalog, `contracts/feature_manifest.json`,
> `ENGINE_INVENTORY.md`, and the per-subsystem `AGENTS.md` guides.

---

## 1. Autonomous Deep Research & Knowledge Synthesis

- **5-pass search pipeline** — Discovery, Specific Evidence, Adversarial Contradiction, Fact Verification, Strategic Synthesis.
- **Targeted knowledge-gap filling** — detects missing metrics, benchmarks, or architectural trade-offs and runs a bounded follow-up stage.
- **Adversarial source juxtaposition** — pairs supporting and falsification evidence for review without claiming a contradiction that was not established.
- **Explicit citation contract** — deterministic anchors (`[S1]`, `[S2]`, …) with each source marked verified / unsupported / unverified / not-checked.
- **`deep_research` tool** — callable by any agent or workflow; writes artifacts under the thread's outputs directory.
- **AgentEye live-source plane** — a curated 39-function free-source catalog (academic, developer, package, government, scientific, knowledge, media, social) with operator allowlists, bounded fan-out, SSRF-safe fetching, and honest provider failures plus DDGS fallback.
- **`deep-research` subagent preset** — direct delegation with an expanded 150-turn budget and strict citation guidelines.

## 2. Swarm Orchestration & Multi-Agent Workforce

- **Bot roster & SOUL protocol** — registered autonomous bots with distinct personalities, isolated system prompts, private inboxes.
- **Transactional bot forge** — `bot_roster(action="forge")` builds a complete bot (persona, approvals, routines, journal) as a single transaction with rollback on failure and smoke-test verification before reporting alive.
- **Work journal & blocker roll-up** — every bot keeps an append-only work journal; `bot_roster(action="waiting_on")` answers "anything waiting on me?" across the whole roster.
- **Secret-scanned bot templates** — export/import `.alphabot.json` templates through a scanner at both doors; detected secrets block the transfer and name the field, never the value.
- **Bot Forge Doctor** — `bot_roster(action="doctor")` checks open blockers, over-frequent routines, shell-capable bots on the real host, missing journals, and unusable sandbox backends.
- **Bot mode DMs** — `POST /api/bots/{name}/dm`, fire-and-forget, server-side attribution.
- **Per-bot model configuration** — each bot chooses its own primary model, fallback chain, counselling panel, and mixture panel; `config.yaml` stays the only place models are declared.
- **Multi-agent group chat & swarms** — collaborative rooms where specialized bots challenge assumptions and produce unified deliverables.
- **Swarm v2 DAG runtime** — atomic checkpoints, ordered JSONL audit events, owner-scoped admission, idempotent creation, lease-fenced task attempts, retry backoff, restart recovery.
- **Automatic topology selection** — `mode="auto"` measures the DAG and picks the shape (map-reduce, scatter/gather, hierarchical) from measured width, depth, and coupling.
- **Deliberation & failure telemetry** — sequential test that stops early once evidence is decisive; budget, rework-loop, and information-gain signals that say "unknown" when genuinely unmeasurable.
- **Bounded execution** — token / tool-call / wall-clock / task / replan budgets, adaptive provider concurrency, watchdog recovery, pause / resume / cancel.
- **Pre-run swarm projection** — each plan reports its decomposer's predicted critical path and speedup before execution; plans exceeding declared wall-clock budget are refused before registration.
- **Specification lint before registration** — empty directives or artifacts without acceptance criteria are refused; vague/duplicated/stale-path directives disclosed as warnings.
- **Model-use transparency** — Team Ops separates requested model override from model labels recorded in worker evidence; missing evidence shown as unreported.
- **Measured swarm execution signals** — peak task overlap and share of tasks with meaningful results from recorded telemetry; non-measured values stay explicit.
- **Typed communication & consensus** — bounded blackboard of untrusted observations; evidence-backed votes, leader election, acceptance verification separating execution success from verified delivery.
- **Swarm acceptance in Team Ops** — each task shows the Gateway's typed acceptance verdict; completed-without-criteria labeled unverified.
- **Opt-in rubric acceptance judge** — `acceptance_mode: "judge"` runs a bounded rubric judge; default `"evidence"` mode runs no model call (cost gate).
- **Opt-in model decomposition** — deterministic graph templates by default (no model call); `swarm.decomposition` opts into `"model"` or `"auto"` (only under declared positive token budget).
- **Specialist report citation audit** — swarm tasks cross-check cited receipt IDs against the specialist's recorded tool calls.
- **Deterministic claim grounding** — worker result summaries re-checked against the real workspace with no model call; unresolved claims fail acceptance when criteria declared.
- **Swarm operations surface** — REST + SSE for claims, leases, revisions, events, messages, replans, progress, and leader/resource telemetry; `swarm` tool exposes the same to authorized agents.
- **Multi-agent run controls** — chat composer lets users opt into specialist delegation with bounded per-run concurrency/total limits.
- **Live subagent progress cards** — task events show status, latest step/tool-call summary, reported step count, measured usage; nested delegation renders as the tree the run reported.
- **Live collaborative Kanban** — agents create, assign, transition, and audit cards on a shared board.
- **Agent-to-Agent (A2A) messaging** — structured protocol for peer requests and distributed coordination; `PeerA2ATransport` delivers real `task_request` envelopes to paired peers; fake cards replaced with real BotRegistry + peer cards; `POST /api/a2a/tasks` creates durable inbox messages.
- **Project workforce layer** — agent↔project membership, presence, resource locking, project constitutions, and ADR memory.

## 3. Continuous Execution Harness, Planners & Loops

- **Autonomous one-prompt planner** — turns an unformatted, complex prompt into a structured multi-step plan.
- **Lifecycle directives** — request intent evaluated once per user turn; incidental mentions don't dispatch verification.
- **8-dimension plan mode** — clarity, safety, feasibility, reversibility, resource intensity, architectural impact, empirical evidence, mission alignment.
- **Mission hierarchy & work-queue DAG** — macro goals decomposed into dependency-ordered work queues.
- **Continuous goal engine & integrity gate** — enforces verifiable completion criteria and blocks premature or hallucinated exits.
- **Run acceptance evidence** — Run Inspector shows measured exact-text file readback, artifact digest, test results; recheckable with append-only measurement history.
- **Ralph loop** — test-driven iterative self-healing until the suite passes and architectural invariants hold.
- **Boulder checkpointing & safe durable replay** — multi-session snapshots; disconnects never cancel work; resumes after crash, expired lease, restart, or recoverable model failure.
- **Effect journal & reconciliation console** — side-effect queue with counts by status/level, oldest unaccounted effect age, `POST /api/side-effects/{id}/reconcile` for verdict recording.
- **Mod kernel console** — reads Alpha's ordered policy chain end to end; commands, audit ledger, durable hold queue, preview endpoint.
- **Multimodal** — vision support (ViewImageMiddleware, view_image_tool), image processing, multimodal chain for T1→T2→T3 capability routing.
- **Subagents** — `task` delegation tool, `batch_task`/`batch_status`/`cancel_batch` durable batch submission, subagent catalog, capacity management, read-only worker tool isolation.
- **File uploads** — all-or-nothing uploads to thread-isolated user buckets, staging through hidden `.upload-*.part` files, atomic replace, sandbox sync.
- **Thread management** — thread branching, regeneration, edit replay, archive/search, owner isolation, activity-time preservation, thread runs with SSE streaming.
- **Peer network** — cross-installation identity, discovery, pairing, transport, delivery receipts, DM bridging; invites as QR codes; libp2p reports unavailable.
- **Plan mode** — TodoList middleware for complex multi-step tasks with `write_todos` tool.
- **Scheduled tasks** — cron schedules, pre-flight wake gates, incident tracking, auto-pause; scheduled-task runtime sets `non_interactive=true`.
- **Side effects** — effect journal and reconciliation console; per-effect `UNKNOWN` + reconciliation ledger; admin-only verdict recording.
- **System monitor** — host CPU/RAM/disk/network/process state with best-effort timeout-bounded cached samplers.
- **War rooms** — staged group deliberation with quorum policy; 20-subsystem aggregate; coordination policy, decision log, event feed, locks, handoffs, approvals, checkpoints.
- **Teams** — team composition, roster management, team operations surface.
- **Companies** — company OS with durable multi-tenant organizations indexed over real subsystems.
- **Skills workshop** — skill authoring, validation, static scan, SkillProposal queuing, admin approve gate.
- **Subagent batches** — durable batch submission/progress/cancellation with acceptance criteria and verdict tracking.
- **Input polish** — `POST /api/input-polish` rewrites composer drafts before sending.
- **Suggestions** — `POST /api/threads/{id}/suggestions` generates follow-up questions.
- **Ops integration** — deployment metadata, version, status, resources, advice, runtime, network connectivity.
- **Deliveries** — run delivery receipts; idempotent persistence of run-scoped delivery receipts.
- **GitHub webhooks** — agent invocations from pull requests, issues, comments, releases; HMAC verification.
- **MCP tasks** — durable task runtime with lease-based recovery for long-running MCP work.
- **Agent messages** — structured agent-to-agent messaging with inbox.
- **Assistants compat** — OpenAI-compatible assistant API for drop-in client compatibility.
- **Goal contracts** — goal contract management for thread-level goals.
- **Goal integrity** — goal integrity verification and enforcement.
- **Group coordination** — group coordination panel, group activity, group profile management.
- **Jobs** — job management and execution tracking.
- **Features** — feature manifest and capability discovery endpoint.
- **Predictive RSI** — advisory forecast service with calibration metrics (Brier, calibration error, coverage).
- **Capabilities** — capability mesh with evidence-backed records and learned instruments.
- **Sentinel repair loop** — autonomous repair loop with per-fault-kind verdict, human handoff queue, checkpoint-before-edit, revert on failure.
- **Kibitzer metacognitive supervision** — background supervision detecting loops, thrashing, and prompt drift.
- **Dynamic workflow plane** — intent perception, capability discovery, task decomposition, bounded DAG waves, typed graph patches, approval gates, retry/replan, evidence-gated replay, saga compensation.
- **Workflow fork, dry run & templates** — fork from any event history point, dry-run on throwaway engine (zero side effects, zero tokens), templates gated through `draft → verified → promoted`.
- **Execution modes** — frontend ExecutionModesSection for selecting and configuring execution paradigms (seven declared by the backend).
- **Thread management** — thread branching, regeneration, edit replay, archive/search, owner isolation, activity-time preservation.
- **Failure classification** — 19 deterministic `NodeFailureClass` values (auth, rate limit, security block, worker lost, …) with the matching rule disclosed.
- **Company OS** — durable multi-tenant organizations indexed over real subsystems; every figure carries a `MeasurementBasis`.
- **Autonomous AI Software Enterprise** — C-Suite hierarchy, mission-to-sprint DAG, RFC/blackboard consensus, token treasury with circuit breakers, multi-sig release attestations.
- **Nested groups** — visibility may fan out to many parents; authority is at most ONE; relay with `forwarded_from` provenance and hop guard.
- **Alpha-to-Alpha peer network** — cross-installation identity, discovery, pairing, transport, delivery receipts, DM bridging; invites as QR codes; libp2p reports unavailable until real adapter wired.
- **Free model gateways & fail-closed routing** — keyless `alpha-free` routing over daily-refreshed free catalogs; automatic failover; bounded concurrent health probes; async cancellation closes local provider requests.
- **System One computer control** — opt-in Windows desktop control where the model returns only semantic element indexes (never coordinates, selectors, keys, typed text); re-observed and locally resolved before sentinel-guarded dispatch; disabled by default.

## 4. Frontier Cognitive Intelligence & Optimization

- **Agentic Variation Operators (AVO)** — evolutionary prompt and strategy mutation driven by compiler-grounded feedback.
- **Mixture of Agents (MoA)** — fans a question out to several models in parallel, redacts PII/secrets, aggregates independent answers; unresolved models reported as failed perspectives.
- **Epistemic belief evaluation** — checks claims against an empirical ground-truth base.
- **Dreaming consolidation** — folds ephemeral episodic traces into a semantic knowledge graph during idle periods; honest consolidation with provenance (`alpha/memory/consolidation.py`) writes `{fact, source_trace_ids, extractor_model, confidence, created_at}`, dedupes, appends dream journal JSONL.
- **Strategic discipline council** — multi-perspective invariant verification, gap analysis, policy compliance audits.
- **Quality council & evidence matrix** — deliberates artifact quality and validates finish-first empirical evidence.
- **Consequence simulation** — models negative outcomes, side effects, and blast radius before an irreversible action runs.
- **System One decision models** — provider-neutral typed `choice` / `score` / `noul` decisions (hosted Jev or self-hosted Laya) with confidence gating and deterministic fallback.
- **Staged group deliberation (War Room)** — bounded room runs several agents through a staged sequence under quorum policy (`all`/`any`/`majority`/`supermajority`); agreement measured over claims, minority view preserved, correlated voters collapse to single voice.
- **Cross-agent taint screening** — every contribution screened for instruction-override and exfiltration patterns; detected payloads redacted before re-entering prompts; unaddressed matches downgrade the run.
- **Intelligence control plane** — **Intelligence** view reads loop health, evidence ledger, journal integrity, replay reservoir, goal counts; every metric carries its basis (`measured`/`unmeasured`/`unavailable`/`unowned`).

## 5. Code Agentic Core & Developer Tooling

- **Pre-commit AST guardrail** — intercepts `write_file`, `str_replace`, `hashline_edit` before disk commit, statically verifying Python/JSON/YAML syntax.
- **Git shadow checkpoints & 1-click rollback** — `refs/alpha-checkpoints/<cid>` before risky mutations; `GET/POST /api/checkpoints`, rollback, diff.
- **Automated test & repair** — runs test commands, parses tracebacks, isolates root cause, applies verified fixes.
- **Repository map AST generator** — whole-codebase structural dependency maps.
- **AST-grep search and rewrite** — structural search/rewrite for TypeScript, Python, Go, Rust, C++.
- **Repo twin inspection** — shadow sandbox twin previews changes before they touch the working tree.
- **Hashline line-level editing** — deterministic line-based read/edit avoiding multi-line merge drift.
- **Sandboxed Python & Bash REPL** — secure execution for analysis, scripts, system commands.
- **Visual artifact & UI verification** — renders and inspects generated widgets, canvases, media artifacts.

## 6. Security Enclave & Governance

- **Astra security enclave & scoped credential vault** — credentials scoped per tool, never handed to the model in the clear.
- **Smart command approval gate** — risk scoring with explicit operator verification for high-impact terminal commands.
- **Emergency stop (Estop)** — global pause sentinel (gates recursive-self-improvement cycle).
- **Trajectory flight recorder** — lead-agent turns recorded to local SQLite trajectory store; model text and tool payloads credential-redacted and bounded.
- **Universal artifact lineage** — hash-linked provenance of every generated file/code/document (SHA-256 content digests).
- **Deterministic token budgeting & cost telemetry** — per-run ceilings and real-time spend with cache-aware pricing.
- **Deterministic security audit** — eight local offline checks (skill Python AST, transcript credential leakage, tool-allowlist drift, bot capability ceiling, channel webhook auth, swarm worktree path escapes, MCP trust) with severity/evidence findings; no aggregate score; `not_checked` for unrunnable checks.

## 7. Omnichannel Messaging Hub & Integrations

- **Nine channels** — Telegram, Slack, Feishu/Lark, WeChat, WeCom, DingTalk, Discord, Buzz, Signal — bidirectional, driving the same agent runtime.
- **Scheduled automations & blueprints** — cron schedules, pre-flight wake gates, incident tracking, auto-pause.
- **OpenAI-compatible chat gateway** — `POST /api/compat/openai/chat/completions` for drop-in client compatibility.
- **GitHub webhook automations** — agent invocations from pull requests, issues, comments, releases.

## 8. Presentation Layer: Desktop & Web UI

- **One-click Windows desktop app** — Electron shell with bundled Node.js and `uv`; launches straight into chat.
- **Next.js 15 web workspace** — Chat, Overview, Workforce, Projects, Kanban, Skills, Peers, Settings views.
- **Agent response workspace** — readable terminal, file-diff, search, browser, artifact, subagent cards; transcript search and Markdown export; side pane for files/commands/pages/artifacts.
- **Workspace-aware lion companion** — movable companion clears navigation, chat answers, project inspector controls; hit area and status bubble scale for narrow screens.
- **Complete on-device chat history** — uncapped IndexedDB archive, full server history pagination, migration from old capped cache, honest degraded states, JSON backup/restore.
- **Bot-led multi-bot projects** — every bot profile can create a project with itself as lead; other bots added, assigned a role, managed together.
- **Bot-scoped chats** — selecting a specialist carries through a new conversation and its first turn; active chats restore their bot from thread metadata.
- **Projects tab** — lists every project; `View more` reads one project in full (lifecycle, crew, room, coordination policy, conversations, memory, constitution, decision log, events, locks, handoffs, approvals, checkpoints, War Room aggregate, autonomy subsystems); each subsystem fails independently.
- **Persistent project instructions** — loaded for each assigned thread through owner-scoped reads; applied as hidden, request-only user-level guidance; do not grant tools or permissions.
- **Bot detail page** — `/bots/[name]` reads everything the Gateway reports about one bot (profile, SOUL, capabilities, skills, toolsets, model config, performance, liveness, working state, project memberships, group rooms, company employment, board tasks, canonical chat thread, inbox, routines, org chart, fleet context, org events); twelve reads fail independently.
- **Free local real-time voice** — browser mic streaming, local Whisper interim + final transcription, VAD turn endpointing, normal SSE agent streaming, sentence-level local Piper playback, optional Kokoro voice, on-device openWakeWord wake word, hands-free resume, no paid speech API.
- **Milo the lion companion** — local inline-SVG companion with articulated animation, bounded roaming, petting, optional always-on-top desktop window forwarding no prompt or conversation data.
- **Self-healing Windows startup** — watchdog, autostart, tray icon, and recovery checks under `recovery/`; self-healing chain restarts the port-8001 tree mid-probe.
- **Guarded GitHub source auto-update** — published-release discovery, clean worktree + fast-forward enforcement, backup refs, detached restart, health verification, automatic rollback; disabled by default.
- **Interactive canvas & generative UI** — inline interactive HTML/React widgets, live SVG diagrams, KaTeX math, data tables.

## 9. Self-Knowledge, Durability & Autonomous Operations

- **Self-tuning** — bounded observation window, canary clock, max step ratio, cooldown; protected paths refused even with operator token; unavailable canary/health/audit fails closed.
- **Self-repair verification** — `selfrepair/engine.py` provides symptom-based repair; `refuse_weakened_tests` is not a field; a self-repair loop never weakens tests.
- **Arena tournaments** — runs one task through N strategy-carded sub-agents with single-elimination bracket (attack, defend, revise, judge); durable budget-gated orchestration; judge quality engineered with blinded neutral copies, deterministic position swapping, per-task hard gates, bounded repair loop; `compare`/`synthesize` refused by name; task-contract/candidate-coverage/evidence-ledger/synthesis wired into executor.
- **War Room** — staged group deliberation with quorum policy; open the **War Room** view for the 20-subsystem aggregate, coordination policy, decision log, event feed, locks, handoffs, approvals, checkpoints.
- **External Alpha** — cross-installation peer network view; transcript read scope; bounded reports with truncation; `hide_from_ui` unchanged.
- **Dashboard** — overview dashboard with system health, run summaries, and quick actions. learned instruments (cross-run memory, calibration, drift, decision-policy table, routing, team selection, composition, independence, shadow); read/preview-only, no executor wiring; `enabled: false` by default.
- **Vitals & reliability view** — frontend VitalsSection and ReliabilitySection for system health monitoring and reliability metrics.
- **Self-knowledge (self-inventory plane)** — eleven read-only registries behind one `list`/`describe`/`health` protocol; `alpha_capability` tool and `GET /api/intelligence/inventory`. do?": eleven read-only registries (tools, skills, MCP, models, bots, commands, capabilities, engines, wiring, memory) behind the `alpha_capability` tool and `GET /api/intelligence/inventory`; unreadable sources report `count: null`, never `0`.
- **Code index** — bounded, query-driven symbol lookup returning names, signatures, `path:line`, one-line docstrings; never a function body; TypeScript rows labelled `extraction="regex"`.
- **Cognitive memory** — owner-server-resolved storage under `users/{owner}/cognitive_memory`, per-owner/per-directory process cache, atomic fsync-backed snapshots; unreadable store fails closed.
- **L1 capture observability** — Memory overview shows latest owner-scoped typed-memory capture outcome from a bounded seven-day provenance scan; surface status and counts; no remembered content exposed.
- **Memory extraction health** — Memory overview shows whether automatic fact extraction is healthy, degraded, disabled, or unknown, with model/source, recent timestamps, aggregate counters; resets on Gateway restart.
- **Coding-task repository memory** — when `memory.codebase.enabled` opted in and a run supplies an explicit repository workspace, Alpha refreshes a bounded AST/structure snapshot off the event loop and retrieves only query-matched modules, symbols, dependency summaries; no execution/import of project files.
- **L1 secret protection** — recognized credential patterns redacted from record content and metadata before content-derived IDs assigned; durable store refuses records bypassing the sanitizing constructor.
- **Prospective reminders** — captures clear "remind me to …" requests with owner/agent scope and idempotent source IDs; stamps bounded due time; scheduled delivery and notifications remain unimplemented.
- **ReasoningBank** — durable, evidence-gated procedure memory: strategies that worked stored with verdict and measured wins/attempts, recalled deterministically for similar tasks; `ralph_loop` is its production consumer.
- **APEX executive control plane** — one autonomy contract, one bounded decision cycle, one read-only status projection and one control surface over engines that already exist; emergency stop, terminal-state and approval-gate refusals enforced.
- **Durable runtime** — session lifecycle, connectivity as first-class state, per-effect `UNKNOWN` + reconciliation ledger, ordered honestly reported shutdown; crash-loop backoff policy ships but is not yet wired into the process launcher.
- **Workflow fork, dry run & templates** — fork from any event history point, dry-run on throwaway engine (zero side effects, zero tokens, labelled `dry_run_simulation`), templates gated through `draft → verified → promoted`.
- **Failure classification** — 19 deterministic `NodeFailureClass` values (auth, rate limit, security block, worker lost, …) disclosing the matching rule.
- **Company OS** — durable multi-tenant organizations indexed over real subsystems; every figure carries a `MeasurementBasis`; a proposal is never an action.
- **Autonomous AI Software Enterprise** — C-Suite hierarchy, mission-to-sprint DAG, RFC/blackboard consensus, token treasury with circuit breakers, multi-sig release attestations.
- **Swarm modes** — ten execution topologies: `auto`, `parallel`, `map_reduce`, `scatter_gather`, `hierarchical`, `debate`, `ensemble`, `coding_worktree`, `read_fanout`, `critical_path_split`.
- **Swarm worker types** — `SpecialistSubagentWorker`, `SpecialistBotWorker`, `EphemeralSubagentWorker`, `CodingWorktreeWorker` (isolated git worktree per task).
- **Subagent types** — `general-purpose`, `bash`, `deep-architect`, `deep-code-reviewer`, `deep-debugger`, `deep-performance`, `deep-security`, `deep-test-synthesizer`.
- **Memory subsystems** — 15 memory engines: affective (mood tracking), codebase (AST/structure snapshots), cognitive (belief graph, episodic, semantic, procedural, working), dreaming (consolidation), entities (entity extraction), evaluation (memory quality metrics), fabric (memory fabric), fusion (multi-source fusion), health (extraction health), narrative (narrative memory), policy (memory policy), prospective (reminders/commitments), scenarios (scenario memory), social (social memory), utility (utility scoring), wiki_vault (wiki-style knowledge vault).
- **Alpha-to-Alpha peer network** — cross-installation identity, discovery, pairing, transport, delivery receipts, DM bridging; invites as QR codes; libp2p reports unavailable until real adapter wired.
- **Free model gateways & fail-closed routing** — keyless `alpha-free` routing over daily-refreshed free catalogs; automatic failover; bounded concurrent health probes; async cancellation closes local provider requests; routing table resolving to nothing returns empty chain + reason.
- **System One computer control** — opt-in Windows desktop control where the model returns only semantic element indexes (never coordinates, selectors, keys, typed text); re-observed and locally resolved before sentinel-guarded dispatch; disabled by default.

## 10. Harness² (Recursive Harness Improvement)

- **Immutable harness bundles** — content-addressed `HarnessBundle` with hashes, parent lineage, active/draft status, rollback.
- **Two bounded proposals** — general (invariant) and task/domain scopes, each with explicit hypothesis and falsification condition.
- **Isolated probing** — each candidate probed in its own workspace with environment fingerprint; confounders surfaced, never averaged away.
- **Eight-question contrast** — deterministic validators first, `CONFOUNDED` outranks every verdict, single-run results flagged.
- **Composition with baseline retention** — adopts only supported benefit; "no change" is a valid composition; every edit travels with evidence refs.
- **Evidence-gated experience library** — separate from conversation/semantic memory; no evidence, no lesson; scope travels with the lesson; staleness computed; negative findings recorded.
- **Promotion ladder** — `draft → statically_validated → probed → regression_tested → shadow → canary → active`; tri-state gates; approval is substantive.
- **Atomic activation & one-step rollback** — `ActivePointer.previous_bundle_id` records predecessor.
- **Budget enforcement** — enforced/exhausted/unenforceable ceilings; deliverable reserve withheld; measured stage charges.
- **Production posture** — no executor/editor/baseline wired; `GET /api/harness/status` carries `harness_capability` row (`state: absent`, `smart: false`); `POST /runs` refuses with `409 harness_baseline_absent`.
- **Kill switch** — `harness_improvement.enabled` splits run/advance writes (refuse) from stop-or-restore writes (reachable).
- **Harness Lab UI** — live stage timeline, candidate diffs, trace comparison, budget meter, run history, cancel/resume, approval/rejection, rollback.
- **Experience library maintenance** — whole-library consolidation: supersedes duplicates, marks expired entries stale, reports conflicts; additive only; periodic tick in autonomy supervisor.
- **Adaptive recursion** — `start()` accepts `base_bundle_id` override; `marginal_improvement` signal; complexity estimation; adaptive stop decision.
- **Bounded parallel exploration** — `parallel` mode runs proposal scopes and candidate probes concurrently with isolated workspaces.
- **Cross-domain/cross-model transfer tests** — domain isolation, runtime isolation, tool-schema isolation, expiry exclusion, held-out task separation.

## 12. Slash Command System (454 commands)

Alpha exposes 454 slash commands across 15 categories, each binding to a real backend subsystem:

- **Core / Help** — `/help`, `/commands`, `/version`, `/about`, `/status`, `/doctor`, `/diagnostics`, `/whoami`, `/capabilities`, `/tools`, `/skills`.
- **Mission / Goal** — `/goal` (create, start, pause, resume, edit, status, complete), `/mission`, `/goal decompose`, `/goal expand`, `/goal verify`.
- **Agent Management** — `/agent` (create, clone, delegate, evaluate, inspect, list, pause, recruit, replace, reputation, restart, resume, spawn, specialize, stop, tree), `/ask-agent`.
- **Swarm** — `/swarm` (start, stop, pause, resume, kill, status, inspect, topology, scale, merge, optimize, research, design, hierarchy, debate, consensus, compete, coding, review, mapreduce, pipeline, parallel, rebalance), `/swarm coding`, `/swarm compete`.
- **Research** — `/research` (deep, quick, exhaustive, sources, citations, claims, counter, gaps, map, synthesize, compare, timeline, verify, frontier).
- **Memory** — `/memory` (add, search, forget, consolidate, experience, failure, inspect, prune, repair, summarize, promote, demote, project, user).
- **Security** — `/security` (audit, scan, review, redteam, permissions, policy, credentials, secrets, inject, lockdown, approve, deny, revoke, sandbox).
- **Planning** — `/plan` (deep, show, approve, reject, revise, compare, dependencies, risk, simulate, optimize, parallelize, freeze, unfreeze, explain, alternative).
- **Verification** — `/verify` (final, deep, evidence, sources, tests, security, completeness, requirements, regression, runtime, artifact, visual, adversarial, independent).
- **Context** — `/context` (compact, detail, export, focus, map, optimize, preserve, prune, reset, restore).
- **Runtime** — `/runtime` (health, inspect, logs, processes, queues, reload, resources, restart, shutdown, workers, events).
- **Code / Git** — `/code`, `/commit`, `/diff`, `/patch`, `/merge`, `/pr`, `/refactor`, `/undo`, `/branch`, `/worktree`.
- **Model** — `/model` (list, auto, best, cheap, coding, compare, fallback, fast, local, research, route, vision, benchmark).
- **Workflow** — `/workflow` (create, run, optimize), `/trigger`, `/task` (cancel, fork, logs, pause, result, resume, retry).
- **Autonomy / APEX** — `/apex` (on, off, status, policy, approve, reject, pause, resume, stop, take-over, replan, steer, verify), `/avo` (start, stop, status, generate, mutate, evaluate, promote, rollback, candidates, compare, shadow, canary, benchmark, adversarial, archive, lineage), `/bg`, `/boost`, `/monitor`, `/performance`, `/telemetry`, `/usage`, `/metrics`, `/latency`.
- **Browser** — `/browser` (open, search, click, fill, extract, screenshot, tabs, network, inspect, verify).
- **Computer** — `/computer` (act, inspect, screenshot, verify).
- **Knowledge** — `/knowledge` (search, graph, update), `/learn` (failure, trajectory).
- **Experience** — `/experience` (similar).
- **Consensus / Negotiation** — `/consensus`, `/negotiate`, `/vote`, `/debate`, `/consult`.
- **Teamwork** — `/teamwork-preview`, `/handoff`, `/broadcast`, `/msg`.
- **Session** — `/session` (clone, fork, search), `/sessions`, `/new`, `/newtask`, `/resume`, `/resume-hitl`, `/pause`, `/interrupt`, `/retry`, `/run`, `/watch`.
- **Skills** — `/skill` (create, list, test), `/skills` (list, install, uninstall, update, enable, disable, inspect, audit, benchmark, improve, create, propose, approve, rollback, search, test).
- **MCP** — `/mcp` (list, add, remove, enable, disable, health, inspect, auth, reload).
- **Self-healing** — `/self-heal`.
- **World** — `/world` (changes, inspect, refresh).

## 13. Memory Systems (Advanced)

- **DeerMem (L1)** — file-based memory with FTS5 retrieval, staleness propagation, conflict expansion, as-of recall, signal boost.
- **Cognitive memory** — owner-scoped storage under `users/{owner}/cognitive_memory`, per-owner/per-directory process cache, atomic fsync-backed snapshots.
- **Episodic memory** — turn-by-turn trace recording with salience scoring, outcome tracking, parent episode linking.
- **Semantic graph** — belief crystallization, conflict reconciliation, Ebbinghaus forgetting curve decay, associative clustering.
- **Procedural skill memory** — skill registration, lifecycle management (proposed → verified → promoted), strength-based ranking, capacity enforcement.
- **Working memory** — ephemeral, owner-scoped working memory with salience promotion.
- **Affective memory** — mood tracking, affective extraction, provenance.
- **Codebase memory** — bounded AST/structure snapshot for coding tasks; query-matched modules, symbols, dependency summaries.
- **L1 capture observability** — latest owner-scoped typed-memory capture outcome from bounded seven-day provenance scan.
- **Memory extraction health** — healthy/degraded/disabled/unknown status with model/source, timestamps, aggregate counters.
- **L1 secret protection** — credential patterns redacted before content-derived IDs assigned.
- **Prospective reminders** — captures "remind me to …" requests with owner/agent scope and idempotent source IDs.
- **ReasoningBank** — durable, evidence-gated procedure memory with measured wins/attempts.
- **Dreaming consolidation** — folds ephemeral episodic traces into semantic knowledge graph; honest consolidation with provenance.
- **Memory consolidation (lossy)** — fact consolidation with `consolidation_enabled` flag; min facts threshold, max groups per cycle.
- **Honest memory consolidation** — `alpha/memory/consolidation.py` with `{fact, source_trace_ids, extractor_model, confidence, created_at}`, dedupe, dream journal JSONL.

## 14. APEX, Autonomy & Self-Improvement

- **APEX executive control plane** — one autonomy contract, one bounded decision cycle, one read-only status projection and one control surface.
- **APEX agent roles** — `ApexAgentRole` with specialization assessment, delegation limits, isolated workspace requirement, reviewer detection.
- **APEX agent factory** — spawn agents with lifecycle and ephemeral resolution; `AgentSpawnResult` with refusal tracking.
- **APEX auto-acceptance** — automatic acceptance gate with contract validation.
- **APEX locking** — distributed locking for executive decisions.
- **APEX strategy** — strategy resolution and execution planning.
- **Autonomous upkeep** — `run_upkeep_pass()` driver: ingest, reconcile skill lifecycle, replay, verify; degraded verification blocks action.
- **APEX invariants** — fail-loud runtime self-checks; `InvariantRegistry` with named `InvariantCheck`s.
- **RSI (Recursive Self-Improvement)** — predictive RSI with advisory forecasts, calibration metrics (Brier, calibration error, coverage).
- **Self-improvement verification** — four gates (target, repetition_order, held_out, regression) with tri-state pass/fail.
- **Self-tuning** — bounded observation window, canary clock, max step ratio, cooldown; protected paths refused even with operator token.
- **Self-repair verification** — `selfrepair/engine.py` provides symptom-based repair; refuses weakened tests.
- **Sentinel repair loop** — autonomous repair loop with per-fault-kind verdict, human handoff queue, checkpoint-before-edit, revert on failure.
- **Kibitzer metacognitive supervision** — background supervision detecting loops, thrashing, prompt drift.

## 15. Additional Subsystems

- **Arena tournaments** — single-elimination bracket (attack, defend, revise, judge) with durable budget-gated orchestration; judge quality engineered with blinded neutral copies, deterministic position swapping, per-task hard gates.
- **AVO (Agentic Variation Operators)** — evolutionary prompt and strategy mutation driven by compiler-grounded feedback.
- **Capability mesh** — evidence-backed capability records; learned instruments (cross-run memory, calibration, drift, decision-policy table, routing, team selection, composition, independence, shadow); read/preview-only.
- **Company OS** — durable multi-tenant organizations indexed over real subsystems; employees are bot profiles, projects are project rows, work items are Kanban cards.
- **Autonomous AI Software Enterprise** — C-Suite hierarchy, mission-to-sprint DAG, RFC/blackboard consensus, token treasury with circuit breakers, multi-sig release attestations.
- **Mod kernel** — ordered policy chain with mod-contributed commands, admin-gated audit ledger, durable hold queue, preview endpoint.
- **Multimodal** — vision support (ViewImageMiddleware, view_image_tool), image processing, multimodal chain for T1→T2→T3 capability routing.
- **Execution modes** — seven execution paradigms declared by the backend.
- **War rooms** — staged group deliberation with quorum policy; 20-subsystem aggregate.
- **Teams** — team composition, roster management, team operations surface.
- **Companies** — company OS with durable multi-tenant organizations.
- **Skills workshop** — skill authoring, validation, static scan, SkillProposal queuing, admin approve gate.
- **Subagent batches** — durable batch submission/progress/cancellation with acceptance criteria and verdict tracking.
- **Input polish** — `POST /api/input-polish` rewrites composer drafts.
- **Suggestions** — `POST /api/threads/{id}/suggestions` generates follow-up questions.
- **Ops integration** — deployment metadata, version, status, resources, advice, runtime, network connectivity.
- **Deliveries** — run delivery receipts; idempotent persistence.
- **GitHub webhooks** — agent invocations from PRs, issues, comments, releases; HMAC verification.
- **MCP tasks** — durable task runtime with lease-based recovery for long-running MCP work.
- **Agent messages** — structured agent-to-agent messaging with inbox.
- **Assistants compat** — OpenAI-compatible assistant API.
- **Goal contracts** — goal contract management for thread-level goals.
- **Goal integrity** — goal integrity verification and enforcement.
- **Group coordination** — group coordination panel, group activity, group profile management.
- **Jobs** — job management and execution tracking.
- **Features** — feature manifest and capability discovery endpoint.
- **Predictive RSI** — advisory forecast service with calibration metrics (Brier, calibration error, coverage).
- **Capabilities** — capability mesh with evidence-backed records.
- **Dashboard** — overview dashboard with system health, run summaries, quick actions.
- **Vitals & reliability** — system health monitoring and reliability metrics.
- **File uploads** — all-or-nothing uploads to thread-isolated user buckets.
- **Thread management** — thread branching, regeneration, edit replay, archive/search, owner isolation.
- **Plan mode** — TodoList middleware for complex multi-step tasks.
- **Scheduled tasks** — cron schedules, pre-flight wake gates, incident tracking, auto-pause.
- **Side effects** — effect journal and reconciliation console.
- **System monitor** — host CPU/RAM/disk/network/process state.
- **Peer network** — cross-installation identity, discovery, pairing, transport, delivery receipts.
- **Self-healing Windows startup** — watchdog, autostart, tray icon, recovery checks.
- **Guarded GitHub source auto-update** — published-release discovery, clean worktree + fast-forward enforcement, backup refs, detached restart, health verification, automatic rollback.

---

*Generated from the Alpha repository at commit `7eef241`. See `README.md` for the full feature catalog with links, and `docs/INDEX.md` for the documentation index.*


- **140 native tools** — deep research, code execution, file operations, web search, browser automation, and more.
- **MCP extensions** — Model Context Protocol server integration for extensible tooling.
- **24 public skills** — academic-paper-review, bootstrap, chart-visualization, claude-to-alpha, code-documentation, consulting-analysis, data-analysis, deep-research, find-skills, frontend-design, github-deep-research, image-generation, music-generation, newsletter-generation, podcast-generation, ppt-generation, project-cartographer, skill-creator, skill-reviewer, surprise-me, systematic-literature-review, vercel-deploy-claimable, video-generation, web-design-guidelines.
- **Custom skills** — operator-authored skills in `skills/custom/`.
- **Plugin system** — top-level `plugins:` list in `config.yaml` for third-party extensions (middleware, task lifecycle, system-model observers, Gateway services, FastAPI routers).
- **CLI** — `alpha extensions install/upgrade/list/enable/disable/remove`, `make update-status|check|apply|recover`.
- **Guarded source auto-update** — published-release discovery, clean worktree + fast-forward enforcement, backup refs, detached restart, health verification, automatic rollback; disabled by default.
- **Docker Compose** — `make up` / `make down` for reproducible team/server deploys.
- **Windows desktop app** — Electron shell with bundled Node.js and `uv`.
- **Bare metal** — `make dev` for active development with hot reload.
- **Kubernetes** — `deploy/helm/alpha` for cluster deployment.
- **Single Nginx entry point** — port 2026, loopback by default.
- **Health checks** — `GET /health`, `GET /health/ready` with concurrent ORM engine + checkpointer probes.
- **CI/CD** — backend unit tests, lint, frontend unit tests, replay E2E, drift gate, Windows installer, release.


## 16. Additional Engine Subsystems (Deep Dive)

- **Experts** � expert panel and expert management for specialized task routing.
- **Guardrails** � governance guardrail provider with risk class enforcement, ask fail-closed, malformed declaration fails agent assembly.
- **Learning** � learning review queue, deferred learning reviews, learning fork reviews.
- **Metacognition** � kibitzer metacognitive supervision; detects loops, thrashing, prompt drift.
- **Metacompiler** � metacompiler for self-modifying code generation.
- **Orchestrator** � swarm orchestrator with adaptive mode selection, deliberation, failure-aware telemetry.
- **Perpetual daemon** � perpetual daemon heartbeat: discovery, consolidation, stagnation.
- **Protocols** � A2A protocol adapter, MCP tasks protocol.
- **Reflection** � reflection engine for post-execution analysis.
- **Reproduction** � reproduction gates and reproducibility verification.
- **Rules** � rules engine for policy enforcement.
- **Scheduler** � swarm scheduler with DAG validation, lease assignment, backoff, stale result fencing.
- **Skills market** � skills marketplace with supply-chain controls.
- **Stream JSON** � streaming JSON utilities for real-time data.
- **System One** � System One decision models (hosted Jev or self-hosted Laya) with confidence gating.
- **Testing** � automated test & repair, test command execution, traceback parsing.
- **Tracing** � trajectory flight recorder, lead-agent turn recording.
- **TUI** � terminal user interface for Alpha.
- **Wire contracts** � cross-component JSON contracts (subagent status, skill review).
- **Workspace changes** � workspace change review with pre-run/post-run snapshots, text diffs.
- **Agency** � agency subsystem for autonomous agent behavior.
- **Authz** � authorization facade with sandbox execution authorization.
- **Autoconfig** � automatic configuration management.
- **Community** � 28 community integrations (agent_eye, aio_sandbox, boxlite, brave, browser_automation, browser_use, browserless, crawl4ai, ddg_search, e2b_sandbox, exa, fastcrw, firecrawl, groundroute, image_search, infoquest, jina_ai, lightrag, opensandbox, ragflow, search_federation, searxng, serper, serply, sofya, tavily, tencent_wsa, tenki).
- **Company OS** � durable multi-tenant organizations indexed over real subsystems.
- **Computer use** � System One indexed computer control with UI Automation.
- **Critique** � critique engine for code and output review.
- **Debugging** � debugging subsystem for error analysis.
- **Deepagent** � deep-agent working plane with bounded thread-scoped virtual filesystem.
- **Epistemics** � epistemic belief evaluation against ground-truth base.
- **Errors** � error registry and error code management.
- **Experts** � expert panel and expert management for specialized task routing.

---

*Generated from the Alpha repository at commit 73ed133. See README.md for the full feature catalog with links, and docs/INDEX.md for the documentation index.*
