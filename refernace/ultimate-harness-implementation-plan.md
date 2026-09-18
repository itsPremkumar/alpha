# Plan: Ultimate Harness — Full Implementation Plan

> Sources merged: (1) local deep-dives — Hermes Agent v0.20.2 (NousResearch),
> OpenClaw 2026.9.4, Oh My OpenAgent v5.0.0-beta.18, Grok Bot (xAI docs), and
> the operator's own harness lineage (`hermes-asi-master`, `hermes-harness-repo`,
> `hermes-agi-asi-harness`); (2) `agent_workspace_ultimate_agent_harness_research_plan.docx`
> (ChatGPT deep research, ~25 references: Prime Agent RLM, Agent Prime council,
> AlphaEvolve/AVO, AI Scientist-v2, Astra, Fable/Mythos, Kimi K3, Letta, Agent Zero,
> OpenHands, Browser Use/Skyvern, Deep Agents, Goose, OpenAI SDK, Google ADK,
> Claude Code teams, SWE-agent, Aider, MiroFish). The ChatGPT share-link itself
> requires login; everything below is built from the final document.
>
> Non-negotiable invariants for every phase: harness/app boundary
> (`backend/AGENTS.md`, enforced by `tests/test_harness_boundary.py`), TDD
> (`make test` + `test-blocking-io` green), `ruff format` clean, docs updated
> (`README.md` user-facing, `AGENTS.md` dev-facing), no secrets in repo.

## 0. Foundation decision (merged verdict — both analyses agree)

Agent Workspace 2.0 stays the canonical execution kernel. Do NOT merge repositories.
Borrow patterns through adapters, plugins, skills, worker services, contracts:

| Layer | Decision | Donor pattern |
|---|---|---|
| Core runtime (runs, streams, checkpoints, sandbox) | KEEP Agent Workspace | — |
| Orchestration (planner/router/team policies) | ADD as policies | OmO categories + Claude Code teams |
| Memory/learning | ADD as pluggable subsystem | Hermes loop + Letta layers |
| Gateway/channels | EXTEND (pairing, scoping) | OpenClaw + Hermes adapters |
| Browser/computer/media | ADD as worker services | Browser Use/Skyvern concepts, Agent Zero isolation |
| Coding execution | ADD backends + gates | OpenHands/SWE-agent/OMO worktrees |
| Evolution/optimization | ADD bounded engine | AlphaEvolve/AVO + operator's GEPA |
| Science mode | ADD as task mode | AI Scientist-v2 tree search |
| Eval/benchmarks | PROMOTE scripts → product | Operator's v3 `benchmarks/` + Hermes runners |
| Model routing | ADD chains + classes | OmO fallback chains, doc §9 classes |
| Security/governance | EXTEND (policy engine, approvals, council) | OpenClaw layers, Agent Prime gates, doc §10 |
| Interop | ADD contracts only | MCP (have) + A2A/capability registry |

Reconciliation with the research doc: its Postgres/pgvector/Redis/Temporal/S3
stack is adopted as **optional backends only** (Postgres already supported;
Redis is an existing extra; S3 artifacts new and optional). SQLite-first local
operation is preserved. Its 13 phases are compressed below into an executable
sequence ordered by dependency, risk, and visible value.

## 1. Target architecture (Agent Workspace-grounded)

```
USER / EVENTS / CHANNELS (existing app/channels + pairing/scope extensions)
  → FastAPI Gateway (existing routers + missions/runs/council/benchmarks/evolution routers)
  → MISSION MANAGER (new, durable — above threads, below UI)
  → MASTER ORCHESTRATOR = lead agent (existing) + router policies (new)
      ├── Planner / Reviewer / Researcher / Executor roster (subagents/builtins)
      ├── Model router + fallback chains (models/ + config)
      ├── Capability registry (new runtime service + console surface)
      ├── Risk/policy engine + approval cards (new tool + UI + channels)
      └── Council/verifiers (new managed runs + evidence receipts)
  → DOMAIN WORKERS = existing sandbox/mcp/browser/community providers
      + new computer/media/science/evolution workers (provider pattern)
  → MEMORY PLANE L0–L8 (extend DeerMem: curated files, FTS session recall,
      semantic facts, user model, skills ledger, eval memory)
  → LEARNING LOOP (post-turn fork → proposals → SkillScan gate → curator →
      ledger/rollback; human approval where configured)
  → ARTIFACT / RESULT BUS (existing artifacts + manifests + delivery receipts)
  → POST-RUN LEARNING (existing run_events + workspace_changes as the substrate)
```

Security kernel stays immutable: owner isolation, fail-closed auth, sandbox
boundary, secret handling. Self-improvement may touch versioned skills,
memories, prompts, routing rules — never permission policy, secrets, or the
run-admission core.

## 2. Phase A — Mission kernel + capability registry + policy engine (foundation)

**A1. Mission entity (durable, above threads).** New `missions` store
(SQLAlchemy model + alembic revision via `make migrate-rev`; reuse
`agent_workspace/persistence` engine patterns): id, owner, objective, constraints
JSON, budget, schedule, status, linked thread_ids, artifact manifest.
Gateway router `app/gateway/routers/missions.py` (CRUD + `POST /launch` →
`launch_scheduled_thread_run` path reuse). Frontend: workspace missions page
(`src/app/workspace/missions/page.tsx`, `src/core/missions/`), sidebar entry.
Tests: `backend/tests/test_missions_*.py` (ownership isolation first).
Acceptance: mission survives restart; runs launched from it journal back to it.

**A2. Capability registry.** Static + measured capabilities: agent/subagent
specs advertise `{name, domain, risk_class, models, tools, cost_class, envs}`.
Fit: new `harness/agent_workspace/capabilities/` (registry + Pydantic schema) fed by
`subagents/registry.py` + `extensions_config` tool surface; Gateway
`GET /api/capabilities`; console UI badges (extend `routers/console.py`).
Reuse operator's `capability_registry.py` shape (domains × criticality).
Acceptance: orchestrator selects worker by capability query, never by
hard-coded name, in at least one path (researcher selection).

**A3. Policy/risk engine + adaptive autonomy.** Per-call `allow|deny|approval`
from risk class × impact (new `harness/agent_workspace/policy/`); config
`policy:` section (default fail-closed, matching current posture).
Acceptance: low-risk reads auto-pass, deletes/shell prompt approval cards.

## 3. Phase B — Memory/learning plane (Hermes + Letta patterns)

**B1. Post-turn learning fork.** Opt-in middleware (`agents/middlewares/
learning_fork.py`): after run end, cheap model proposes memory writes/skill
patches via whitelisted tools only; writes land in DeerMem queue + skill
proposal store, never directly in prompts. Config `learning_fork: {enabled,
model_name, max_proposals_per_run}`. Tests: cache-safety (no prompt churn),
whitelist enforcement. Acceptance: no measurable prompt-cache regression.

**B2. FTS session recall.** New `session_search` builtin over `run_events` +
thread metadata (SQLite FTS5, WAL-safe under existing engine): modes
discover/scroll/read. Gateway endpoint + frontend history search hook.
Acceptance: cross-thread recall with zero embedding calls.

**B3. Layered memory (L0–L8).** Extend DeerMem: curated `MEMORY.md`/`USER.md`
bounds + char budgets (Hermes `memory_tool` pattern), semantic facts table,
user-model provider ABC (Honcho-compatible first), project memory scope.
Files: `agents/memory/*`, `config` memory section (hot-reloadable fields
stay hot-reloadable per `reload_boundary.py`). Acceptance: per-layer tests +
`make test-blocking-io` clean.

**B4. Skill proposal queue + curator.** Agent proposals → SkillScan gate →
human approve/reject (Settings UI) → `skills/custom/`; idle curator archives
stale agent skills with JSONL ledger + `rollback` endpoint (reuse scheduler
for the cadence). Wires existing `review_skill_package`, install/export.
Acceptance: end-to-end propose→scan→approve→install→rollback in tests.

## 4. Phase C — Orchestration (OmO/Claude patterns)

**C1. Per-role model fallback chains.** `ModelConfig.fallbacks[]`
(provider-ordered); factory retries 429/5xx/timeout down the chain; per-call
override stays. Files: `models/*`, `config/model_config.py`. Acceptance:
fault-injection tests (first provider 500s → second serves).

**C2. Specialist roster.** Builtins `planner`, `researcher`, `reviewer`,
`executor` in `subagents/builtins/` with registry metadata + category intent
routing in lead prompt (no hard-coded model names in prompts). Acceptance:
planner→reviewer→executor chain on a fixture task.

**C3. `/plan` interview mode.** Composer command (precedent: `/goal` in
`input-box.tsx`): Metis-style gap questions → plan artifact → Momus-style
review rounds (cap 3) → approval → execution pins plan hash. E2E in
`frontend/tests/e2e/` (mock API pattern). Acceptance: plan artifact +
approval audit in run events.

**C4. Never-stop goal follow-ups.** `runtime/goal.py` extension: unsatisfied
goal + `keep_going` → Gateway chains bounded follow-up runs (per-run cap of 8
unchanged; audit links runs). Reuses run idempotency + orphan recovery.
Acceptance: multi-run goal completes across a process restart in tests.

**C5. Hash-anchored concurrent edits.** `read` emits `LINE#ID` tags;
`str_replace` rejects stale hashes (extends artifact SHA-256 pattern to
sandbox files, `sandbox/tools.py`). Acceptance: racing edits test.

**C6. Tool lifecycle hooks API.** Extend extensions contract:
`pre_tool_call/post_tool_call/session_idle/on_error` plugin hooks
(precedent: middleware chain). Migrate at least one middleware. Acceptance:
sample extension + boundary test still green.

## 5. Phase D — Trust plane (approvals, council, evidence, recovery)

**D1. Approval cards.** Generalize `ask_clarification` → approval requests
with Approve/Deny in web UI (`human-input-card` precedent), IM buttons,
allowlist auto-approve policy. Acceptance: E2E approve + deny paths.

**D2. Verifier council.** Managed runs for critic/factual/security/quality +
judge quorum for tier-1 artifacts; evidence receipts (claim→evidence→
uncertainty) persisted like delivery receipts (`runtime/` journal precedent).
Acceptance: high-risk artifact blocked without quorum in tests.

**D3. Recovery policies.** State-machine retries for model timeout, tool
failure, context overflow (existing summarization), container crash —
bounded, observable via run events. Acceptance: injected-failure suite.

## 6. Phase E — Worker planes (browser/computer/media/coding/science)

**E1. Browser worker hardening.** Multi-tab sessions, download/upload,
stateful login contexts, DOM→vision fallback ordering, Skyvern-style action
validation. Fit: `community/browser_automation/*`. Acceptance: scripted
login + extraction fixture.

**E2. Computer worker (isolated).** New sandbox provider: Linux desktop/VM
screenshots + GUI actions behind `Sandbox` interface; never host desktop.
Acceptance: provider conformance tests, strict isolation tests.

**E3. Media worker.** `image_gen`/`video_gen` community tools → artifact
delivery (receipts cover provenance). Acceptance: offline-capable tests with
recorded fixtures.

**E4. Coding plane.** Worktree-per-agent execution, test/review/release gates
(OMO `/ulw-execute` semantics), SWE-agent-style evaluators as the gate
contract. Fit: `subagents/` + sandbox + new `verification` helpers.
Acceptance: fixture repo → green-gated merge in tests.

**E5. Science mode (task mode, not default).** Hypothesis→experiment→analysis
tree with lineage + executable evaluator + budget (AI Scientist-v2 pattern on
top of C4/E4). Acceptance: fixture hypothesis converges with bounded spend.

## 7. Phase F — Evaluation + evolution + scale

**F1. Benchmark plane as product.** Promote `backend/scripts/benchmark/` to
`POST /api/benchmarks/run` + console dashboard (suites, trajectory export,
cost). Operator's v3 `benchmarks/` drops in here. Acceptance: one suite
runs end-to-end from UI.

**F2. Evolution engine (bounded).** AlphaEvolve/AVO + operator GEPA:
objective + evaluator + isolated candidates + lineage + promote gates; learns
only versioned surfaces (skills, prompts, routing). Human approval for
promotion. Acceptance: skill-reliability improves on a fixture benchmark.

**F3. A2A + external workers.** Capability discovery + delegation/status/
artifact contract; external agents as workers without orchestrator changes.
Acceptance: mock external worker completes a delegated task in tests.

**F4. Scale (last).** Redis-backed bridges (already optional), quotas,
multi-tenancy only if demanded. Explicitly deferred until F1–F3 prove
reliability — matches doc §18 P3.

## 8. Test + rollout rules per phase

- Backend: `tests/test_<area>_<behavior>.py`, offline by default, live-marked
  only for real APIs; update `backend/AGENTS.md` + root `AGENTS.md` map.
- Frontend: `tests/unit/` mirror + E2E for user-visible flows (mock-API pattern).
- Each phase ends with: `make test`, `test-blocking-io`, `ruff`, `pnpm check`,
  version bump via `scripts/bump_version.sh` + `verify_versions.sh`.
- Docs: user-facing → `README.md`/docs; dev-facing → nearest `AGENTS.md`.
- Never break: owner isolation, fail-closed auth, checkpoint/delta modes,
  idempotent run admission, multi-worker heartbeat contract
  (`tests/test_run_*` pin these — run them every phase).

## 9. Explicit non-goals (protect the core)

Shared multi-user cloud sessions, YOLO-by-default exec, host-desktop control,
cloud credential custody, deleting auth, TUI duplication, mandatory
Postgres/Redis/S3 (stay optional), merging any donor repo wholesale.

## 10. Suggested start: Milestone 1 (2–3 weeks, independently shippable)

C1 (fallback chains) → B2 (FTS recall) → B1 (learning fork, default off) →
C3 (`/plan` mode). Each lands behind config flags; together they demo a
self-improving, never-lost, plan-disciplined agent on the existing kernel.

## 11. Reference code map (local clones, latest as of 2026-09-13)

All shallow clones under `C:\Users\PREM KUMAR\`. Borrow patterns only —
never merge repos, never import `src/**` across boundaries.

| Clone | Use for | Sharpest steal (deep-dive verified) |
|---|---|---|
| `hermes-agent-repo` (NousResearch) | Learning loop, FTS recall, cron, 7 terminal backends, 35 providers, trajectory pipeline | `agent/background_review.py` fork; `agent/curator.py` ledger+rollback; `hermes_state.py` FTS5 session search; `tools/environments/` backend ABC |
| `openclaw-ref` | Gateway multiplexing, skill workshop, approvals, scheduler+wake, bindings | Skill Workshop propose→review→apply; `notifyOnExit` heartbeat wake; pairing/mention-gating; harness registry `auto` selection |
| `oh-my-openagent-ref` | Roster, never-stop, plan gates, hash edits, hooks, DAG runs | Category→model-chain routing; Boulder/Goal idle re-inject; `/ulw-plan`→`/ulw-execute`; hashline `LINE#ID`; `mass-ulw` WAL ledger |
| `agent-prime-ref` (+ external `Avyayalaya/agent-council`) | Council mechanics | Score-aware quorum (`SHIP iff 0 blocks & min≥3; HOLD iff irreducible/blocks≥3`), R1→R2→Adjudicator with recompute, tier-1 glob gating, `council_log.jsonl` priors |
| `prime-agent-ref` | RLM, goals, scheduler, refinement | Admission-handle recursion (handle now, answer via inbox/files); per-name dill checkpoints + manifest; goal-as-transcript-entry; claim-before-deliver scheduler; plan/apply-split refinement with baseline guard |
| `openhands-ref` | Coding plane | Action/Observation event join (`action_id↔tool_call_id`) + risk-scored confirmation gate; ACP stdio split (`agent_kind+command+model+LookupSecret`); conversation-scoped working dirs; budget caps |
| `letta-ref` | Memory layers | NOTE: upstream `main` currently ships docs only — use documented MemGPT pattern (core/recall/archival blocks + memory tools) via the provider ABC, not code |
| `agent-zero-ref` | Computer worker | Dockerized Linux desktop + DOM-annotated browser behind one tool contract; isolated worker pattern |
| `browser-use-ref` | Browser primitive | DOM-action agent primitive + Playwright integration shape |
| `skyvern-ref` | Browser resilience | Action/extraction/validation split; `evaluation/` as gate fixtures |
| `deepagents-ref` | Long-horizon patterns | Filesystem context management + HITL reference implementation |
| `goose-ref` | Desktop/CLI/API parity | `workflow_recipes/`, MCP extension ecosystem shape, cross-surface session model |
| `swe-agent-ref` | Coding evaluators | Issue→patch→test loop + `trajectories/` as gate fixtures |
| `aider-ref` | Repo intelligence | Repo map + git-native editing conventions |
| `mirofish-ref` | Simulation mode | Persona-swarm scenario simulation harness shape |
| `ai-scientist-v2-ref` | Science mode | Hypothesis→experiment→analysis tree + lineage |
| `kimi-code-ref` | CLI/session patterns | Long focused sessions, ACP integration, subagent shape |
| `openai-agents-sdk-ref` | Interop contracts | Agents/handoffs/guardrails/tracing minimal primitives |
| `adk-python-ref` | Orchestration alt | Graph workflows + eval/deployment patterns |
| Operator's `hermes-asi-master` | Skills, GEPA, verification | `skills/07-search-optimized`, `09-github-advanced` → `skills/public/`; GEPA loop → Phase F2; 5-gate pipeline → Phase D |
| Operator's `hermes-harness-repo` / `hermes-agi-asi-harness` | Registry, benchmarks, control plane | `capability_registry.py` → console; `benchmarks/` → Phase F1; `continuous_dev.py` → nightly CI job, not runtime |

## 2026-09-17 — Docs re-apply merge entry (ULT + REBRAND into main, docs only)

Docs-only re-application after prior docs edits were wiped (LICENSE restoration
verified intact via `git hash-object LICENSE` = `git rev-parse 52229a6c^:LICENSE`;
no code, frontend, config, skills, or env changes in this pass; no test runs; no commits).

Ported scopes (docs only, incl. recovery): Union Alpha configuration baseline +
cognitive-memory owner contract (`AGENTS.md` before Cross-Cutting Conventions),
Union Alpha model configuration (`README.md`), Display-Identity contract
(`backend/AGENTS.md`), owner-scoped goals contract
(`backend/packages/harness/agent_workspace/goals/AGENTS.md`, verified identical to ULT
source so no copy was needed), root `verify` script (`package.json`), and
recovery-policy scope notes. Sources: `agent-workspace-ultimate-harness` (ULT) and
`rebrand-wt` (REBRAND Display-Identity hunk).

Deferred (not ported in this docs pass): profile.py / templates.py /
full-skills-pack, durable queue, sandbox-backed jobs, multi-process coherence,
live inference, browser E2E.
