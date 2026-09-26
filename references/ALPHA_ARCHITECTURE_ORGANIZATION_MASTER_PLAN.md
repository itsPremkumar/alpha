# ALPHA — Architecture Organization & Advanced-Feature Master Plan (v1)

**Date:** 2026-09-24 · **Status:** PLAN (implementation waves P1–P7 queued) · **Grounded at:** HEAD `b331c04`
**User mandate:** organize every advanced agentic feature with complete automation, clear separation, easy navigation, single-file indexes for the agent, full capability/limiter/parallel maps — **delete or remove NOTHING** (additive canon: new files, re-exports, generated views, wiring; never removal).

---

## 0. Executive answer — "am I correct that this code is well planned and organized?"

**Mostly yes, with one precise correction.**

- ✅ **The ENGINE layer is genuinely well-organized.** 89+ single-responsibility engines each own a directory (`backend/packages/harness/alpha/<engine>/`), one shared honesty canon (`evidence_kind`, measured-or-None, fail-closed, "LLMs propose, deterministic runtime decides"), generated ground truth (`contracts/feature_manifest.json` — tools 127 · routers 57 · middlewares 41 · loops 6 · skills 24, official generator, never hand-edited), a real capability catalog, and now (W-N3 `b331c04`) a runtime **discovery plane** (`alpha/workflow/registry/` — capabilities/tools/skills/mcp/memory behind one `list/describe/health` facade with honesty invariants enforced by test).
- ✅ **Execution is genuinely dynamic and automated**: mode-mapped orchestration (7 paradigms), durable event log with kill-and-resume, approvals, sandbox tiers, RSI gates with cooldown + immutable releases, watchdog self-healing, CI-built installer.
- ❌ **What is NOT yet organized is the CONNECTIVE TISSUE — the knowledge layer.** The lists a *reader* (human or agent) needs are scattered across code + README + manifest: slash commands live in `commands/catalog.py` + handlers + docs; tools in `BUILTIN_TOOLS` + README table + manifest; bot profiles, plugins, loops, modes, limiters have no single generated view each. There is **no map** of which features are parallel-safe, which are unused between each other, or which trigger starts what. Dormant modules exist (`alpha.ledger`, `alpha.evidence`), 5 spec commands honestly report `registered=False`, two memory trees coexist without a documented boundary, and duplicate memory tools exist.

**Verdict:** no re-architecture needed; the plan below completes the **organization, discoverability, and automation** of what already exists — exactly what you asked for — without deleting a single feature.

---

## 1. Ground-truth inventory (measured 2026-09-24, not guessed)

| Domain | Count / location | Source of truth |
|---|---|---|
| Engine subpackages | 97 top-level dirs (89 curated engines + new waves + cache dirs) | `backend/packages/harness/alpha/` |
| Native tools | **127** | `contracts/feature_manifest.json` (generated) |
| API routers | **57** | same |
| Middlewares | **41** | same |
| Loops | **6** | same |
| Public skills | **24** dirs | `skills/public/` |
| Slash-command strings | **125 distinct** (`about … world`, incl. namespaced `goal:*`, `skill:*`, `loop:*`, `subagent:*`) | scan of `alpha/commands/*.py` (indicative; authoritative count = `commands/catalog.py`, Wave P1 generator) |
| Capability catalog | live dict, +1 `workflow_registry` entry (`b331c04`) | `alpha/capabilities/catalog.py` |
| Plugins/extensions | dedicated engine with loader/manager/registry/policy/stack/isolation | `alpha/extensions/` |
| Discovery plane (runtime) | 5 registries + facade + singleton | `alpha/workflow/registry/` |
| Memory trees | **two**: `alpha/memory/*` (cognitive engines: active_memory, cognitive, session_search, kibitzer, dreaming, wiki_vault — proven importable by W-N3 tests) AND `alpha/agents/memory/` (agent-facing memory; currently under audit/improvement) | both exist; boundary undocumented (Wave P4) |
| Manifest disclosures | 1 intentionally-unwired shim · 2 dormant packages · 6 operator-constrained exclusions | `contracts/feature_manifest.json` |

---

## 2. Honest verdict: well-organized vs gaps (no feature is ever deleted; gaps are wired or documented)

**Already well-organized (keep as-is):**
1. One engine = one directory = one responsibility; routers thin over engines; middlewares registered in a manifest.
2. Generated truth (manifest) with a single official generator and a no-hand-edit rule.
3. Discovery plane with fail-closed, honest descriptors (`health="unverified"`, `version=None`, `unavailable ⇒ reason`, `RegistryUnavailable`, never-raising aggregate health) — invariants *pinned by test*.
4. Orchestration shares ONE execution kernel + durable event log (workflow/loop/subagent/swarm/bot all compose through it — no parallel invented runtimes).
5. Wave discipline: disjoint fences, central review, targeted gates (ruff / AST-dup / targeted pytest), Conventional Commits, ledgers (`docs/TASK_LIST.md`, `docs/IMPLEMENTATION_MATRIX.md`).

**Gaps this plan closes:**
| # | Gap | Wave |
|---|---|---|
| G1 | No single generated one-file index per domain (tools/commands/plugins/bots/loops/modes/limiters/skills…) for humans **and the agent** | P1 |
| G2 | No trigger map (slash vs tool vs event vs schedule vs UI vs A2A vs webhook) | P2 |
| G3 | No parallel/async/serialization map (what may run concurrently, what is locked and why) | P2 |
| G4 | No limiter catalog (every budget/cap/approval/TTL/cooldown/estop in one view) | P1–P2 |
| G5 | Dormant modules with no consumer: `alpha.ledger` (ActionLedger/ActionReceipt, tested but zero call sites), `alpha.evidence` (EvidenceStore, no consumer, **no tests**) | P3 |
| G6 | 5 spec commands honestly `registered=False` (`/boost`, `/schedule`, `/grill-me`, `/teamwork-preview`, `/self-heal`) — need real handlers | P5 |
| G7 | Two memory trees, boundary undocumented; duplicate bash-based memory tools (alias, never delete) | P4 |
| G8 | Under-connected features: kanban↔workflow, evidence↔finish-first/RSI, ledger↔action run-path, forge↔traces (each = additive defaults-off wiring) | P6 |
| G9 | W-N3 blueprint leftover: explicit **command⊆catalog⊆capability parity test** not among its 14 tests | P1 |
| G10 | Index/manifest drift not CI-gated (someone must remember to regen) | P7 |
| G11 | Repo-wide ruff debt ~1,143 in unowned files (disclosed; incremental, never a mass reflow) | P7 |

---

## 3. Feature taxonomy — "are dynamic workflow, subagent and swarm the same thing?"

**No. They share ONE substrate and have DISTINCT contracts.** The confusion is exactly the missing map this plan adds. (All rows compose through `alpha/orchestrator` kernel + `alpha/workflow` durable log where marked ✓ — that is shared *infrastructure*, not duplicated features.)

| Axis | **Dynamic Workflow** | **Loop (×6 + ralph)** | **Subagent** | **Swarm** | **Bot mode** | **Company** | **Kanban/Projects** | **Plan/Think modes** |
|---|---|---|---|---|---|---|---|---|
| Purpose | durable multi-step DAG of work | iterate-until-condition | bounded delegated task | peer parallelism toward shared goal | standing persona worker | org hierarchy & governance | human+agent coordination board | pre-execution evaluation |
| Topology | DAG nodes/edges | cycle in-run | one isolated child | peers + leader election | peers w/ mailboxes | tree (depts) | board/columns | none (analysis) |
| Identity | run id | iteration count | agent preset (general/research/quick/deep-research) | swarm member | SOUL persona | role/dept | card assignee | same agent |
| Durability | ✓ event log, kill-and-resume, replay | in-run (+ journal events) | via jobs/batches | in-run | mailbox + ledger | heartbeat/KPI cadence | persistent cards | ephemeral |
| Trigger | REST/UI/turns | slash/tool/step | slash/batch/tool | slash/tool | messages/DM | heartbeat/discovery | UI | slash `/plan`, `/mode`, `cognitive_plan` |
| Parallelism | node-parallel within run rules | serial iteration | ✓ batch async (capacity-limited) | ✓ in-process peers | ✓ async DM/fire-forget | serial cadence | n/a | n/a |
| Approval/risk | per-node HITL approvals | loop pause/resume | turn/budget/subagent_control limits | partition limits | lease TTL, clone modes | governance council | resource locks | 8-dim evaluation gates |
| Failure model | fail-closed → FAILED + honest event | budget_exhausted honest stop | per-task honest failure | member failure disclosed | lease loss raises | incident auto-pause | explicit states | abstain≠approve |
| **Use when** | state must survive restarts; approvals; replay | retry/self-heal/verify loops | delegate a bounded unit, need isolation | explore/consensus across peers | durable role w/ memory & reputation | structure, KPIs, dispatch | visible coordination | decide BEFORE executing |

**Same-substrate rule (keep):** every path that executes work goes through the one kernel seam (single `node_runner`/claim lock/OCC) — "LLMs propose, deterministic runtime decides". Plan mode and think mode are **modes of the same runtime, not separate executors** (8-dim `planning` + `deliberation`/`council` engines); System-1 reflex (`85b7629`) is a **fast-path proposal layer** that the runtime accepts only on evidence agreement. RSI/self-evolve is a **meta-loop** outside the user task loop (own workspace, 9-gate promotion, cooldown, immutable releases, `auto_promote=False`). Skill forge/learning converts **traces → skills** (input: any successful run; output: skill registry).

---

## 4. Terminology reconciliation (your words → Alpha's real features)

| You said | Maps to (existing) | Note |
|---|---|---|
| plan mode | `plan_mode` router + `planning` engine (8-dim) + `build_autonomous_plan` | exists |
| think mode | no feature literally named "think" — nearest: `deliberation`, `council`, `metacognition`, `reasoning/MoA` + `cognitive_plan` | P2 doc must say this plainly |
| war room | no feature literally named "war room" — nearest: `groups` multi-agent rooms + `swarm` + emergency `safety/estop` | alias documented in P2 |
| git worktree tools | `manage_code_checkpoint`, repo twin, `workspace_changes`, `hashline_*` | literal per-feature git *worktrees* = candidate gap → P6 additive (never replaces checkpoints) |
| ASI | ambiguous — treated as **A2A protocol** (`protocols`, `routers/a2a.py`) and/or **RSI** (self-improvement); both documented | clarify in P2 |
| self evolve / avo | `rsi/*`, `avo`, `evolution`, `reflection`, `learning` | exists, gated |
| self-repair / auto recovery | `selfrepair`, `recovery`, watchdog, `harness` | exists |
| parallel process | `jobs`, `subagent_batches`, `perpetual`, scheduler | exists |
| skill forge/create | `learning` forge + `skill-creator`/`skill-reviewer` skills + curator lifecycle | exists |
| bot command / company / swarm / kanban / memory / project / dynamic workflow / subagent / slash command / loop | exact engines as named | exists |

---

## 5. The canonical Discovery Plane — "everything important in ONE file" (user's core ask)

**Principle: the CODE is the source of truth; every "one file" is a GENERATED VIEW — never hand-edited** (extends the proven `feature_manifest.json` pattern; avoids drift, which is the classic failure of hand-maintained indexes).

### 5.1 Runtime registries (agent-facing API — extend W-N3's plane)
`alpha/workflow/registry/` already serves 5 kinds (`capabilities, tools, skills, mcp, memory`) with the honest descriptor shape (`id, kind, availability, source, version, health, authority, evidence_kind, reason`). **Add 7 kinds, same protocol, same honesty invariants:**

| New kind | Source of truth it reads | Solves |
|---|---|---|
| `commands` | `alpha/commands/catalog.py` + handler registry | all 125 slash commands in ONE view incl. honest `registered=False` for the 5 spec commands |
| `bots` | bot roster/SOUL profiles store | all bot profiles in one list |
| `plugins` | `alpha/extensions` registry + `extensions_config.json` mcpServers | all plugins/servers in one list |
| `loops` | loops registration (manifest's 6 + ralph/harness entries) | loop inventory + pause/resume surface |
| `modes` | `orchestrator/mode_mapper.py` paradigms + execution_mode | plan/think/normal/bot/… in one list |
| `limiters` | `policy`, budgets, `RISK_POLICY`, estop, capacities, TTLs, cooldowns | everything that limits anything, in one list |
| `workflows` | workflow definitions + plan graphs | available flows in one list |

### 5.2 Generated human/agent files (P1 deliverables, produced ONLY by the official generator)
- `contracts/feature_index.json` — machine-readable combined index.
- `docs/INDEX/`: **`TOOLS.md`, `COMMANDS.md`, `PLUGINS.md`, `BOTS.md`, `LOOPS.md`, `MODES.md`, `SKILLS.md`, `CAPABILITIES.md`, `TRIGGERS.md`, `LIMITERS.md`, `PARALLEL.md`, `FEATURES.md`** — one line per entry (name · one-line purpose · source path · trigger · parallel-safe? · limiter · status).
- Agent navigation flow: prompt carries a **compact summary** (counts + the 12 INDEX names); the agent reads one INDEX file or calls `get_workflow_registry().list(kind)` / `describe(kind, id)` on demand → **progressive disclosure, zero prompt bloat**.

### 5.3 Parity tests (fail the build on drift — the automation of "organized")
1. **command⊆catalog⊆capability** (W-N3 blueprint leftover → P1): every non-deprecated command name appears in `commands/catalog.py` and resolves to a capability/handler or is honestly `registered=False`.
2. **regen == committed**: generator run in CI; any diff in `feature_manifest.json` or `docs/INDEX/*` fails (P7).
3. Descriptor honesty invariants (already exist for 5 kinds → extend to all 12).

---

## 6. Trigger & activation map (what starts what — P2 `TRIGGERS.md`)

| Trigger path | Entry seam | Families it starts | Limiter at entry |
|---|---|---|---|
| Slash command | `commands/registry` → handler | planning, loops, skills, swarm, subagents, doctor/security/compact, goals, workflow | registered-flag honesty; approval for risky |
| Tool call | tool registry (`BUILTIN_TOOLS` + catalog dynamic) | research, coding, cognition, governance, security, automation | AST/pre-commit guard, risk tier, token budget |
| UI / REST | 57 routers → engines | everything user-visible | auth posture, per-route validation |
| Schedule/cron | `scheduler` (+ P1 job memory) | automations, blueprints, perpetual daemon | claims/locks, incident auto-pause, TTL |
| Event | `events` bus subscribers | supervision, telemetry, kibitzer, kanban sync | handler isolation (one failure never hides rest) |
| Inter-agent msg | A2A / bot DM / group rooms | swarm, company dispatch, teammate mesh | attribution, mailbox scope |
| Workflow node | executor registry (`build_runner`, fail-closed if unbound) | node executors, HITL approval gates | per-run claim lock, approvals, budgets |
| Webhook (operator-excluded by default) | `github_webhooks` env-gated (404 unless configured) | — | env gate, secret |
| Self/meta | RSI cycle, watchdog, recovery, reflex | self-evolve, self-heal, estop | isolated workspace, 9 gates, cooldown |

---

## 7. Parallel / async capability map (P2 `PARALLEL.md` — every claim pinned by test)

| Feature | Parallel? | Async? | Serialized/limited by |
|---|---|---|---|
| Workflow nodes within a run | per DAG rules | ✓ step streaming | per-run `threading.Lock` claim; OCC patches; single event-log writer |
| Subagent batches | ✓ | ✓ jobs | capacity/turn/budget (`subagent_control`), depth |
| Swarm peers | ✓ in-process | fire-and-forget | leader election, partition limits |
| MoA multi-model queries | ✓ | ✓ | provider rate limits, token budget |
| Scheduler/cron tasks | ✓ across jobs | ✓ | per-task claims + race tests; incident auto-pause |
| Background jobs | ✓ | ✓ | queue, cancel signals, process handles |
| RSI experiments | isolated only | ✓ | isolated workspace + regression suite + cooldown (never parallel-promote) |
| Bot DM / channels | ✓ | fire-and-forget | mailbox scope, attribution |
| Dreaming consolidation | idle-only | ✓ | idle trigger |
| Heartbeat/KPI writes | **no** | — | single writer per cycle; measured-only fields |
| Skill storage writes | **no** | — | scan-before-activation quarantine; 422 on block |
| Catalog appends | shared-append protocol | — | read-verify-append; no reformat |
| Estop | interrupts everything | — | highest priority, safety engine |

---

## 8. Limiter catalog (P1 `LIMITERS.md` — "map every limiter feature")

Risk tiers/approval policy (`policy`, `authz`, smart command approval) · token budgets + cost telemetry (deterministic) · concurrency caps (subagents, swarm, batches) · turn/time budgets (loops, subagent presets, RSI timebox) · OCC + idempotency keys (durability) · estop + rate limiting (`safety`) · RSI cooldown + immutable releases + `auto_promote=False` · lease TTL + archive-on-expiry (ephemeral bots) · resource locks (projects) · sandbox tiers (local/Docker/K8s) · skill quarantine + security scan · memory retention/TTL + secret redaction pre-memory · evidence standards (measured > simulated; simulated never gates; unverified = 0.5-neutral disclosed) · incident auto-pause (scheduler) · disk-space gate (desktop) · queue health + event-loop sampler (ops).

---

## 9. Unused & under-connected map (wire, never delete)

- **Dormant → wire (P3):** `alpha.ledger` ActionLedger/ActionReceipt → real receipt-writing call site in the action run path; `alpha.evidence` EvidenceStore → real consumer (finish-first/RSI evidence) **plus its missing test suite**.
- **Unregistered → implement (P5):** the 5 spec commands with real handlers (DY-R3 no-phantom-command canon: keep `registered=False` honesty until each handler lands).
- **Duplicate → alias (P4):** duplicate bash-based memory tools → one canonical implementation + thin wrapper of the old name (zero deletion); openviking fail-open residue → fail-closed root fix.
- **Two memory trees (P4):** document `alpha.memory` (cognitive engines) vs `alpha.agents.memory` (agent-facing) boundary in INDEX + docstrings; unify only views, never remove either.
- **Connections (P6, each additive + defaults-off):** kanban cards ↔ workflow nodes (state mirror); evidence store ↔ rubric/finish-first; action ledger ↔ receipts in REST responses; forge ↔ trace store (already adjacent — expose); worktree isolation (new capability alongside checkpoints); swarm metrics ↔ company KPI (measured-only).
- **Stay excluded (operator constraint, NOT gaps):** sign-in/OIDC, IM bridges, remote MCP/ACP, cloud sandboxes, remote tracing, GitHub webhook ingress — each already disclosed in the manifest; README/manifest nuance already recorded in `docs/TASK_LIST.md` §9.

---

## 10. Navigation architecture — "where do I add X?"

**Layer cake (unchanged, now written down):**
```
TRIGGERS (slash · tool · REST/UI · schedule · event · A2A · webhook · meta)
    ↓
DISCOVERY PLANE (workflow/registry — 12 generated kinds; feature_index.json; docs/INDEX/*)
    ↓
ORCHESTRATION (kernel · mode_mapper · loops · subagent batches · swarm · company cadence)
    ↓
RUNTIME (LangGraph agent loop · 41 middlewares · System-1 reflex · approvals · estop)
    ↓
ENGINES (97 dirs: cognition · research · coding · memory×2 · skills · bots · rsi · …)
    ↓
PERSISTENCE (checkpointer · durable event log · plan graph · artifacts · ledger/evidence after P3)
```
**Recipe "adding a new feature" (P2 `docs/ARCHITECTURE.md`):** 1 engine dir → 2 register in `capabilities/catalog.py` (shared append) → 3 expose via tool and/or router (thin) → 4 **it appears automatically in the generated INDEX + registry** (generator reads code) → 5 tests: unit + wiring + orphan-gate reachability + honesty invariants → 6 manifest regen (official script) → 7 parity tests green. No hand-editing any index, ever.

---

## 11. End-to-end workflow (one picture, every stage limited and logged)

`user intent → trigger (§6) → command/catalog honesty check → plan/think evaluation (8-dim, abstain≠approve) → mission/DAG build → capability discovery (registry plane; unavailable ⇒ surfaced verbatim, fail-closed) → orchestration choice (§3 table) → kernel claim (serialized) → tool call (risk tier → approval → AST guard → sandbox) → events (bus + fsync'd log, idempotency keys) → persistence (checkpointer/projections; kill-and-resume) → verification (rubric/evidence matrix; measured-only) → memory write (scoped, secret-filtered) → learning (skill forge/retrospective; RSI behind gates) → UI/logs (honest states; every failure visible)` — with the §8 limiter applying at each arrow.

---

## 12. Automation gaps → full-automation plan

Manual today → automated by: manifest regen (P7 CI drift gate) · INDEX drift (P7 same gate) · command registration honesty (P1 parity test) · dormant-module detection (P7: manifest `dormant_packages` must shrink or justify — never silent) · docs/README cross-checks (P7 workflow) · tests already automated (gates on every landing).

---

## 13. Phased implementation waves (fenced, gated, NO deletions)

| Wave | Scope | Fences/gates |
|---|---|---|
| **P0** | this plan registered in ledgers | lead (docs commit) |
| **P1** | Discovery-plane completion: 7 new registry kinds + `contracts/feature_index.json` + `docs/INDEX/*` generated + generator extension + **command⊆catalog⊆capability test** + honesty invariants for new kinds | owns `workflow/registry/**`, `commands/read-view`, generator script; targeted pytest + ruff 0 + dup 0 + regen==committed |
| **P2** | Navigation docs: `docs/ARCHITECTURE.md` (layer cake + add-recipe + terminology table), `TRIGGERS.md`, `PARALLEL.md`, `LIMITERS.md` (generated views) | docs/**; doc-accuracy test (counts match manifest) |
| **P3** | Dormant wiring: `alpha.evidence` consumer + tests, `alpha.ledger` action-path receipts | new consumers only; both modules' existing APIs untouched |
| **P4** | Memory clarity: boundary docs + duplicate-tool canonical+alias + openviking fail-closed | `agents/memory/**`, `alpha/memory/**` read-mostly; round-trip tests |
| **P5** | Module A: real handlers for `/boost /schedule /grill-me /teamwork-preview /self-heal` → `registered=True` one at a time | command handler files; per-command tests; no phantom registration |
| **P6** | Connection waves (defaults-off): kanban↔workflow, ledger↔responses, worktree capability, forge exposure, swarm↔KPI | additive flags only; no config_version bump |
| **P7** | Automation: CI drift gates (manifest+INDEX regen==committed), dormant-shrink check, incremental ruff burn-down (never mass reflow) | CI workflow edits; no test weakening |

**Standing gates for every wave:** targeted pytest only while suites run · ruff 0 on touched files · `ast_dup_gate_dups=0` · orphan-gate reachability (re-export seams) · honesty invariants · centralized commits · ledgers updated · **zero features removed**.

---

## 14. Definition of done (organization edition)

The agent (and any human) can answer, **from one read**: (1) what features exist? (2) how is each triggered? (3) which run in parallel/async and what serializes them? (4) what limits each? (5) what is dormant/uncertain and why? (6) where do I add a new one? — with **zero drift** (parity tests), all 12 INDEX kinds green, 5 spec commands registered with real handlers, 2 dormant modules wired+tested, and every claim measured (never asserted).

---

## 15. Risks & disclosures

- Indexes are generated views: a bug in the generator = wrong index → mitigated by regen==committed CI gate + unit tests on the generator.
- Registry probes measure *importability/registration*, never runtime health — `health` stays `unverified` unless a real probe exists (already pinned by test; do not regress).
- Two memory trees: P4 documents first; any future unification is a separate reviewed decision — **not** part of this plan's execution.
- Parallelism claims in §7 must gain test pins during P2 (documentation without pins is not accepted as done).
- Operator-excluded features (§9) remain excluded by user constraint; do not "helpfully" re-enable.
