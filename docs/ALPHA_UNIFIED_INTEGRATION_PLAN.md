# Alpha — Unified Integration Plan (local-only, no auth, no remote servers)

Status: plan / not yet executed
Scope owner: lead agent integration
Constraint set (from operator):
- Everything runs on **one local machine**, in one stack, with no sign-in/sign-up,
  no OIDC, no remote control plane.
- No IM channel credentials, no cloud MCP servers, no external SaaS dependency
  required for the system to be *complete*.
- Goal: every subsystem that already exists in this repository is **reachable,
  triggered, observable and test-covered inside the running system** — not just
  importable.

---

## 1. Verified current state (measured, not assumed)

Measured on this checkout with an AST-based reference scan (module imports +
dotted-string loaders + config `use:` paths), router-mount diff, frontend
import graph, and middleware-chain diff.

| Axis | Total | Wired | Real gap |
| --- | --- | --- | --- |
| Python modules (`alpha.*`, `app.*`) | 1351 | ~all | 3 modules with no reference at all |
| Gateway routers | 54 files | 54 mounted in `app/gateway/app.py` | 0 |
| Builtin tools | 115 registered in `BUILTIN_TOOLS` (`alpha/tools/tools.py`) | all in registry | 0 |
| Middlewares (`*_middleware.py`) | 40 | 25 in lead chain + 13 via `build_lead_runtime_middlewares` / `agents/factory.py` | 2 unwired |
| Frontend modules | 82 | 81 | 1 (`src/lib/utils.ts`) |
| Background services started at lifespan | scheduler, channel service, MCP-task service, subagent-batch service, system monitor | started | sentinel loop, perpetual daemon, swarm runner, enterprise heartbeat, learning review queue, skills curator have **no automatic trigger** |

**Real, verified bugs to fix (integration debt):**
1. `alpha/agents/middlewares/metacognitive_middleware.py` — implemented, never added to any chain.
2. `alpha/agents/middlewares/continual_harness_middleware.py` — implemented, never added to any chain.
3. `app/gateway/services/system_monitor_service.py` — 0-byte duplicate of `app/gateway/system_monitor_service.py` (rename leftover).
4. `frontend/src/lib/utils.ts` — no importer (verify then either use or delete).

**Conclusion:** the repository is *not* mostly dead code. The actual problem is
**trigger/orchestration debt**: subsystems exist and are individually sound, but
several are only reachable through a tool call or an HTTP endpoint that nothing
invokes automatically. The fix is a single integration layer, not a rewrite.

---

## 2. Target architecture (single coherent local system)

```
                       ┌────────────────────────────────────────────┐
   Browser  ─────────► │ Next.js UI (nav tabs + Integration Health) │
                       └───────────────┬────────────────────────────┘
                                       │ /api/*
                       ┌───────────────▼────────────────────────────┐
                       │ Gateway (FastAPI, single worker, in-process)│
                       │  • routers: 54 mounted (unchanged)         │
                       │  • FeatureRegistry  ← manifest-driven      │
                       │  • AutonomySupervisor (one lifecycle owner)│
                       │  • EventBus (in-process, single bus)       │
                       └───────┬─────────────┬──────────────┬───────┘
                               │             │              │
                    Agent runtime      Background loops   Subsystem stores
                 (lead + subagents,   (sentinel,         (evidence, ledger,
                  middleware chain,    perpetual,        blackboard, trajectory,
                  tools, skills)       curator, swarm)   metacognition, projects)
```

Four invariants this design enforces:

1. **One lifecycle owner.** No subsystem starts itself. `AutonomySupervisor`
   starts/stops every loop; every loop is idempotent, cancellable and capped.
2. **One event bus.** Subsystems publish/subscribe on the in-process bus instead
   of polling each other, so features compose without pairwise wiring.
3. **One manifest.** `contracts/feature_manifest.json` lists every intended
   capability, its module, its wiring point and its health probe. A test fails
   when code exists but is absent from the manifest, or vice-versa.
4. **One runtime home.** Every store resolves paths through `Paths` /
   `runtime_home()` so state is inspectable, backup-able and never scattered.

---

## 3. The anti-drift mechanism (fixes the root cause)

Multiple AI passes created orphans because nothing recorded "what must be wired".
Add, permanently:

- `contracts/feature_manifest.json` — one entry per capability:
  ```json
  {
    "id": "metacognition.self_audit",
    "kind": "middleware",
    "module": "alpha.agents.middlewares.metacognitive_middleware",
    "symbol": "MetacognitiveMiddleware",
    "wiring_point": "alpha.agents.lead_agent.agent:middlewares",
    "order_after": ["token_usage_middleware"],
    "order_before": ["terminal_response_middleware"],
    "enabled_by": "config.autonomy.metacognition",
    "probe": "GET /api/ops/integration-health",
    "tests": ["backend/tests/test_feature_manifest_wiring.py"]
  }
  ```
- `backend/tests/test_feature_manifest_wiring.py` (new, TDD) asserts, for every
  manifest entry: module imports, symbol exists, the declared wiring point
  actually references it, and `enabled_by` resolves. Missing wiring ⇒ red test.
- `backend/tests/test_no_unmanifested_modules.py` (new) re-runs the AST
  reference scan and fails on any module that is neither referenced in code nor
  listed in the manifest under `"intentionally_standalone": [...]`.
- `GET /api/ops/integration-health` + a frontend **Integration Health** panel that
  renders manifest coverage live (wired / degraded / unwired counts per
  subsystem). This turns integration from a one-time cleanup into a visible,
  continuously checked property.


---

## 4. Wiring map — correct location for every capability class

This table is the "what goes where" contract. Each row names the single correct
hook point, so future changes cannot scatter.

| Capability class | Correct wiring point | Why there | Examples in this repo |
| --- | --- | --- | --- |
| Prompt/context shaping | `alpha/agents/lead_agent/agent.py` → `middlewares` (ordered) | must run before the model call, after memory injection | `dynamic_context`, `durable_context`, `hooks_bridge` |
| Safety / sanitization | same list, **outermost** ring | must see raw input and final tool output | `input_sanitization`, `tool_result_sanitization`, `llm_error_handling`, `review_guard`, `read_before_write` |
| Tool lifecycle (budget/progress/receipt) | same list, adjacent to the tool-call middleware | needs both request and result | `tool_output_budget`, `tool_progress`, `tool_receipt`, `sandbox_audit` |
| Self-observation of the agent | same list, read-mostly, fail-open | must never change the user's answer | `metacognitive`, `continual_harness` (**currently unwired**) |
| Model-facing tools | `alpha/tools/tools.py` → `BUILTIN_TOOLS` (+ `SUBAGENT_TOOLS`) | one registry de-duplicates and binds | 115 builtin tools (already correct) |
| HTTP surface | `app/gateway/app.py` → `include_router` | one mount table, auth-free locally | 54 routers (already correct) |
| Background loops | `app/gateway/autonomy/supervisor.py` (new), started from the `app.py` lifespan | one owner, one stop path, capped concurrency | sentinel, perpetual daemon, curator, review queue |
| Periodic/business schedules | `alpha/scheduler/{blueprints,cron_manager}.py` + `app/scheduler` service | durable, user-visible, already wired to the UI | daily-report, nightly-backup, weekly-audit, skill-curator |
| Cross-subsystem signals | `alpha/events/bus.py` (new single bus) | avoids O(n^2) direct imports | incidents -> curator -> review queue |
| Long-lived subsystem state | `Paths` / `runtime_home()` only | one backup/inspect story | evidence, ledger, blackboard, trajectory, projects |

---

## 5. Phased execution plan

Each phase is independently shippable, test-first and reversible. No phase
depends on authentication or on any remote service.

### Phase 0 — Baseline lock (0.5 day)
- Snapshot the four measured tables in §1 into `docs/` as the baseline.
- Add `backend/tests/test_module_reference_scan.py` (the AST scan as a test; an
  xfail list holds the 3 currently-unreferenced modules) so the number can only
  improve.
- Acceptance: `cd backend && python -m pytest tests/test_module_reference_scan.py -q` passes.

### Phase 1 — Close the 4 verified gaps (1 day)
1. Wire `MetacognitiveMiddleware` and `ContinualHarnessMiddleware` into the lead
   chain at the slots declared in the manifest (after `TokenUsageMiddleware`,
   before `TerminalResponseMiddleware`); both **fail-open** — an internal error
   logs and passes the request through unchanged.
2. Delete the 0-byte `app/gateway/services/system_monitor_service.py` leftover;
   confirm `app/gateway/system_monitor_service.py` stays the only import path.
3. Resolve `frontend/src/lib/utils.ts`: import it where styles are needed, or
   delete it (`pnpm typecheck` must stay clean).
- Tests: `test_metacognitive_middleware_wiring.py`,
  `test_continual_harness_middleware_wiring.py`, plus one live turn asserting the
  reply is identical with the middlewares enabled vs disabled while idle.
- Acceptance: those tests green; `ruff check` and `pnpm typecheck` clean.

### Phase 2 — Manifest + integration health (2 days)
- Add `contracts/feature_manifest.json`; backfill **all** 115 tools, 54 routers,
  40 middlewares and the background services.
- Add the two guard tests from §3.
- Add `app/gateway/routers/ops_integration.py` exposing
  `GET /api/ops/integration-health` (wired/total per class + the unwired id list).
- Frontend: an `IntegrationHealthSection` inside the existing `SystemSection` tab
  (no new nav item, no auth).
- Acceptance: guard tests green; endpoint reports non-zero coverage in every
  class; the UI renders it.

### Phase 3 — AutonomySupervisor + event bus (3 days)
- `alpha/events/bus.py`: async in-process pub/sub, bounded queues, drop-oldest,
  per-subscriber isolation so one slow consumer cannot stall a run.
- `app/gateway/autonomy/supervisor.py`: registers loops from the manifest with
  `{enabled_by, interval_seconds, max_concurrent, jitter, backoff, stop_timeout}`;
  exposes `start()/stop()/status()`; a **single** stop path on lifespan shutdown.
- Migrate loops onto it in this order, each behind its own flag and default
  **off** until its tests pass: sentinel loop → perpetual daemon (discovery,
  memory consolidation, stagnation) → learning review queue → skills curator →
  swarm runner → enterprise heartbeat.
- Hard rules: a loop is a no-op when its flag is off; a crashed loop restarts
  with exponential backoff under a restart budget; model-dependent loops are
  excluded from the "system healthy" criterion so a machine without an API key
  still reports a complete, healthy system.
- Acceptance: flags off ⇒ zero background activity (asserted); flags on ⇒ each
  loop's `status()` shows `runs > 0` and no task leaks after shutdown
  (`asyncio.all_tasks()` assertion).


### Phase 4 — Subsystem state unification (2 days)
- Route every store through `Paths`/`runtime_home()`; add
  `alpha/runtime/home_layout.py` documenting the on-disk contract.
- Emit lifecycle events (`evidence.recorded`, `ledger.entry`, `blackboard.post`,
  `review.queued`, `incident.opened`, `skill.proposed`) on the bus and subscribe
  the consumers that today import each other directly: curator ← trace,
  review queue ← learning fork, incidents ← scheduler guards.
- Acceptance: one scripted scenario (a single run that records evidence, opens an
  incident and proposes a skill) completes with every store present under the
  documented layout and no new cross-import between subsystems.

### Phase 5 — UI/UX completion of the unified surface (2 days)
- Give every wired backend capability a visible, non-auth surface: workforce
  tabs, projects, kanban, scheduled tasks, integration health.
- UX fixes: consistent empty/loading/error states per section (reuse
  `lib/http.ts:errMsg`), one global toast for background-loop failures, and a
  "what ran while you were away" digest fed by the bus.
- Acceptance: `pnpm typecheck` + `pnpm test` clean, plus a manual per-tab
  checklist.

### Phase 6 — Verification, docs, hardening (1.5 days)
- Full backend suite: `cd backend && make test` (default) — green.
- Strict blocking-I/O suite: `make test-blocking-io`.
- `scripts/verify_unified_system.py`: boots the stack headless, calls every
  router's health probe, asserts manifest coverage, runs one real agent turn,
  tears down. Exit code 0 = unified system proven.
- Update `AGENTS.md`, `backend/AGENTS.md`, `frontend/AGENTS.md` with the §4
  wiring map so nobody (human or AI) can re-create orphans.
- Acceptance: `verify_unified_system.py` exits 0 on a clean checkout.

---

## 6. Explicit exclusions (per operator constraint)

Marked `"excluded": true, "reason": "local-only"` in the manifest so they cannot
silently return:

- sign-in / sign-up, admin setup, OIDC, PATs, RBAC enforcement paths;
- IM channel credentials and bridges (Feishu, Slack, Telegram, Discord,
  DingTalk, Signal, WeCom, Buzz);
- remote MCP/ACP servers, cloud sandboxes (E2B, OpenSandbox, Tenki, Boxlite),
  Kubernetes provisioner;
- remote tracing exporters (LangSmith) and remote model endpoints beyond the
  local `config.yaml` model list;
- GitHub webhook ingress.

Auth-disabled local mode stays the default (`AGENT_WORKSPACE_AUTH_DISABLED=1`)
and loopback-only binding is preserved.

---

## 7. Risk register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Middleware ordering changes replies | high | insert only at declared slots; order-assertion test; A/B a real turn |
| Loops multiply write load / spin CPU | medium | default-off flags, interval + jitter, max_concurrent, restart budget, per-loop cap |
| Event bus hides failures | medium | per-subscriber isolation, drop-oldest with counter metric, failures surface in integration health |
| Manifest goes stale | medium | two guard tests fail the build; live coverage shown in the UI |
| Renamed-package leftovers recur | medium | Phase 0 scan test + `use:` path validation test on `config.yaml` |
| Blast radius too large at once | high | phased, each phase shippable and revertible; no phase touches auth or channels |

---

## 8. Definition of done

1. Backend default + blocking-I/O suites green; `ruff check` /
   `ruff format --check` clean on touched files.
2. Frontend `pnpm typecheck` + `pnpm test` green.
3. Integration-health coverage: 100% of registered tools/routers/middlewares in
   the manifest; zero unmanifested modules.
4. One real agent turn succeeds end to end locally with every integration layer
   enabled.
5. Every background loop is reachable from `AutonomySupervisor.status()` with
   `runs > 0` when enabled, and there is zero activity when disabled.
6. `scripts/verify_unified_system.py` exits 0 from a clean checkout.

---

## 9. Estimated total effort

| Phase | Days | Deliverable |
| --- | --- | --- |
| 0 Baseline | 0.5 | scan test + baseline docs |
| 1 Gaps | 1.0 | 2 middlewares wired, leftovers removed |
| 2 Manifest | 2.0 | manifest + 2 guard tests + health endpoint/UI |
| 3 Supervisor | 3.0 | event bus + supervisor + 6 loops migrated |
| 4 State | 2.0 | unified stores + bus events |
| 5 UI/UX | 2.0 | complete visible surface |
| 6 Verify | 1.5 | full suites + verify script + docs |
| **Total** | **12.0** | one coherent, continuously-checked system |

| UI surfaces | `frontend/src/components/sections/*` + `NavTabs` + `workforce.ts` | already wired; only flag-gated | workforce tabs, integration health (new) |
