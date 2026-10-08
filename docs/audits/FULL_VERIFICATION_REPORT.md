<<<<<<< HEAD
# Alpha — Full Verification Report (cycle 5)

**Date:** 2026-10-05
**Base commit:** `2dbfe90`
**Scope:** production-reliability audit — system map, baseline, complete bug
inventory, root-cause fixes with tests, subsystem verification, UI verification.

This report separates three things that are easy to blur: **what I proved**, **what
I fixed**, and **what I could not verify in this environment**. Nothing in §1 or
§5 is claimed without a cited artifact. Anything unexercised is written
**NOT VERIFIED**, never merged into "working".

---

## 1. EXECUTIVE SUMMARY

| Area | Verdict |
|---|---|
| **F1** Frontend suite | **1610 / 1611 pass** (baseline 1591 / 1594) — only the pre-existing, honestly-labelled `KNOWN FAILING` QR gate remains |
| **F2** `tsc --noEmit` | **0 errors** |
| **F3** Electron suite | **284 / 284 pass** |
| **F4** Backend gate (8 modules) | **277 passed / 1 failed** → after the depth fix, **124 passed** across the group-nesting pair |
| **F5** Fleet-reserved-key client bug | **FIXED** — subsystem readiness **5/7 → 6/7** measured in the live UI |
| **F6** Bot-roster legibility | **FIXED** — 8 colliding `display_name`s / 35 of 58 cards, now disambiguated |
| **F7** Brittle source-text tests | **FIXED** — 2 tests broken by commit `2dbfe90`, no defect existed |
| **F8** Group-nesting `depth_of` | **FIXED** — fan-in counted ancestors, refusing valid placements with a fabricated depth |
| **F9** Duplicate supervision stubs | **FIXED** — one shared stub + a parity tripwire, after two link-time failures |
| Manifest drift | **clean** — regeneration diff is the timestamp only |
| `docs/INDEX.md` | **check clean** — 104 documents |
| Screenshots | **CAPTURED** — `docs/audits/screenshots/2026-10-05/bots-after-fix.png` |
| Docker/nginx `:2026`, Provisioner `:8002` | **NOT VERIFIED** — no Docker daemon on this host |

**Overall verdict: PASS on the targeted gates that completed on this host, with
the reported defects fixed at root cause. The previously reported MAJOR
initialization rejection path (formerly §L3) is now caught and surfaced with
loading states cleared. The full backend suite did not complete, and the
Phase D/E/F exercises left unverified were **not** completed this cycle — see
§13 for exactly what that means.**

I am not claiming a clean bill of health. The previous cycle reported 38/38
subsystems PASS on the *read* surface; this cycle found that a subsystem the
read surface reported as healthy (the safety watchdog) was in fact reporting a
client-side parse failure over a well-formed server payload. **Read-only
verification had already "passed" that row twice.**

---

## 2. SYSTEM MAP

Delivered as a standalone artifact: **`docs/audits/SYSTEM_MAP.md`**.

Contents: service topology (nginx `:2026` loopback-by-default, Gateway `:8001`,
Frontend `:3000`, Provisioner `:8002` optional), the agent chain with
`file:line` for every stage, the state inventory marking each store
cross-process safe or process-local, the self-inventory plane, the
three-place view registry cross-check (31/31/31/31, **0 dead ids**), the
harness/app boundary, and two unresolved documentation contradictions.

---

## 3. BASELINE

Captured before any of my edits.

| Gate | Result | Evidence |
|---|---|---|
| Backend full suite (`not live`, `--ignore=tests/blocking_io`) | **DID NOT COMPLETE** — ran to 15 % in ~80 min, then thrashed the host | `logs/baseline_backend.log`; host free memory fell to **386 MB of 5996 MB** |
| Frontend `node --test src/lib/*.test.mjs` | **1594 tests, 1591 pass, 3 fail** | `logs/baseline_fe.log` |
| `tsc --noEmit` | **0 errors** | run before edits |
| Electron tests | **NOT RUN at baseline** | run after; 284/284 |
| Backend gate (manifest/orphan/boundary/inventory/groups/skills/stream) | **277 passed, 1 failed** | `logs/gate_backend.log` |
| `ruff format --check .` | **501 files would be reformatted** — pre-existing repo-wide debt | `test_web_tool_failure_honesty.py` (never touched by me) fails the check at `HEAD` |
| Manifest regeneration | clean (timestamp only) | `git diff -- contracts/feature_manifest.json` |
| Docker daemon | **absent** | `npipe:////./pipe/docker_engine` |

### The 3 baseline frontend failures

| Test | Provenance |
|---|---|
| `qr-decode.test.mjs` → `✖ KNOWN FAILING: a correctly rendered encoded code should round-trip` | **Honest, pre-existing.** `canDecodeQr()` is `false`; showing a code works, reading one is not implemented. The repo discloses this in the assertion message itself. |
| `chat-shell-dedupe.test.mjs` → `an expanded project shows its agents, and never turns an unread crew into zero` | **Regression from commit `2dbfe90`** — see F7. |
| `chat-shell-dedupe.test.mjs` → `the empty project list never claims a project is empty when its badge says otherwise` | **Regression from commit `2dbfe90`** — see F7. |

### The 1 baseline backend failure

`tests/test_group_nesting.py::test_depth_counts_the_longest_chain_and_not_the_ancestor_count`
— **F8**. This test was *already uncommitted in the working tree* when I started
(`git status` showed `M backend/tests/test_group_nesting.py`, timestamped
23:07 the previous evening, authored by a prior session). It correctly failed
against `HEAD`. **I did not write it, and I fixed the production bug it caught.**

---

## 4. COMPLETE BUG INVENTORY

Severity labels are exactly the rubric's.

### Fixed and proven

| # | Sev | Symptom | Root cause (one sentence) | Component | Evidence |
|---|---|---|---|---|---|
| **F1** | **CRITICAL** | Workspace header rendered *"The server returned an unreadable fleet payload."* over a `200` that had said, in full, *"nothing is supervised, and here is why"* | `parseFleetWorkers` required **every** key to be a worker record, so the server's four documented reserved keys (`observed`, `observed_reason`, `observed_worker_count`, `watching`) tripped the strict guard — and throwing **discarded the server's own reason** | `frontend/src/lib/supervision.ts:81`, `system.ts:120` | **B** `GET /api/supervision/fleet` → `200 {"observed":false,"observed_reason":"no_worker_has_posted_a_heartbeat_to_this_process","observed_worker_count":0,"watching":false}` · **A** 13/13 + 10/10 + 6/6 tests · **C** readiness **5/7 → 6/7**, the error string gone from the DOM |
| **F2** | **MAJOR** | 35 of 58 bot cards rendered an identical headline; `name` — the only field that separates them — appeared nowhere | `botDisplayName` returns `display_name \|\| name`, and `display_name` is not unique: **8 colliding labels over 35 of 58 cards** | `frontend/src/types/bots.ts:98`, `BotGallery.tsx` | **B** `GET /api/bots` → `count: 58`, 0 duplicate `name`, 8 duplicate `display_name` · **C** screenshot: `Data Engineer (bot_ed5fc1)`, `(bot_f62dd2)`, `(bot_66e659)` … · **A** 8/8 |
| **F3** | **CRITICAL** | Group nesting refused **valid** placements with a fabricated number: *"Room would nest 5 levels deep"* for a room three from the root | `depth_of` returned `min(len(ancestors_of(...)), MAX_DEPTH)` — the **size of the deduped ancestor set**, not the length of the longest chain; the two agree only when every ancestor lies on one path | `backend/packages/harness/alpha/groups/scope.py:193` | **A** before: `assert 4 == 2` FAIL · after: `depth_of(hub)=2`, `hub→new ALLOWED`, `hub.depth` persisted as 2 (was 4) · **A** 80 passed + 124 passed |
| **F4** | **MAJOR** | Two honesty tests failed with **no defect existing**; the failure pointed at the copy they protect rather than at the formatter that broke the grep | The pins regex-matched **raw source text**; commit `2dbfe90` re-wrapped two expressions across lines without changing a rendered byte | `frontend/src/lib/chat-shell-dedupe.test.mjs` | **A** before: 2 failures citing the honesty strings · after: **19/19** pass · `git show 2dbfe90` proves the re-wrap |
| **F5** | **MAJOR** | Two suites died with a **link-time** `does not provide an export named 'isWatching'` — a failure pointing at the test file, not at the change | `lib/supervision.ts`'s exports were hand-stubbed in **three** separate inline copies; the fleet fix made two of them stale on the same commit | `system-probe-honesty.test.mjs`, `ui-legibility.test.mjs` | **A** 2 link errors → 76/76 across all four affected suites; `supervision-stub-parity.test.mjs` now **fails if the stub and the module ever disagree** |
| **F6** | **MINOR** | The shared stub's `watchdogDetail` **returned a function object** (stringifies to `undefined`) instead of calling it | `return ${fn.toString()}` returns the function; it needed an IIFE | `test-supervision-stub.mjs` | caught by my own new pin `typeof detail === "string"` |

### Per-class sweep (Section 2.4) — including the clean answers

| Class | Answer | Evidence |
|---|---|---|
| Feature exists but unreachable | **CLEAN** for the view registry — 31 union / 31 tabs / 31 routable / 31 render branches, **0 dead ids** | `workspace-nav.test.mjs` passes both directions; `frontend/src/lib/workspace-nav.test.mjs` |
| UI shows a number the server never sent | **1 real (F2-adjacent, legibility)**. The prior cycle's **fabricated-zero** (NEW-1) is **fixed** — the strip now renders a skeleton while loading and reads 60/44/0 against a server that reports 60 | `logs/gateway` `GET /api/bots → count: 58→60`; UI `60 Total bots / 44 Active / 0 Paused` |
| UI shows a number the server never sent (honesty spot-checks) | **CLEAN, verified against the API.** `0 agents` in the vitals strip is **correct** — `total_agents` counts custom agent *profiles*, and the server genuinely reports `0`. `60/44/0` fleet strip matches `GET /api/bots` and `GET /api/bots/health/overview` | `GET /api/console/stats` → `total_agents: 0`, `total_runs: 112`, `total_threads: 109` |
| Catch-and-empty error handling | **The previously identified unexpected `ChatView.init()` rejection is handled** at the effect boundary: its reason is flashed and both primary loading states are cleared. Fetch helpers still retain their normal surface-specific fallback/error behavior. | `frontend/src/components/ChatView.tsx`; `frontend/src/lib/load-failure-honesty.test.mjs` |
| Missing structured logging | **CLEAN in changed paths.** Live errors observed carry `trace_id`: `Readiness database probe failed … [trace_id=be35adb5…]` | `logs/gateway.err.log` |
| Timeouts / unbounded loops | **CLEAN** — the only failure mode seen was host resource starvation, which the vitals strip disclosed honestly (`26 ms internet`, honest system vitals) rather than hiding | measured; §13 |
| Race conditions | **1 real (mine, self-inflicted, recorded for honesty):** I killed a watchdog-owned `next dev` while the browser was fetching `main-app.js` and the page died with `ERR_ABORTED`. I diagnosed it as my own artifact rather than reporting a product defect, then rebuilt and re-verified | `network.list` showed `failed … main-app.js :: net::ERR_ABORTED` on the run I had just interrupted |
| Persistence gaps | **NOT VERIFIED** this cycle | §13 |
| Premature stop | **NOT VERIFIED** this cycle — no live agent run was executed | §13 |
| Tool-selection failure | **NOT VERIFIED** this cycle | §13 |

---

## 5. FIX REGISTER

### F1 — fleet reserved keys rejected as unreadable

**Root cause:** `parseFleetWorkers` mapped branch required `v && typeof v === "object" && !Array.isArray(v)` for **every** entry. `get_fleet_health` returns the worker map plus four reserved siblings whose docstring explicitly warns a consumer may read them "as a worker whose id happened to be `observed`" — and this client is exactly that consumer.

**Why the fix removes the cause, not the symptom:** the reserved key names are now pinned in one constant (`FLEET_RESERVED_KEYS`) and stripped *before* the map is read, so the parser is correct for the shape the server documents rather than correct for one deployment. The reason is read separately through `observedReason()`/`isWatching()` instead of being thrown away.

- `frontend/src/lib/supervision.ts` — `FLEET_RESERVED_KEYS`, `workerEntries()`, new `observedReason()` / `isWatching()` exports; `parseFleetWorkers` strips reserved keys and treats a reserved-keys-only payload as the empty fleet it literally is.
- `frontend/src/lib/system.ts` — the watchdog probe now reads the route **body**, not just the worker list, and appends the server's reason when `watching === false` (appended, not substituted).
- Tests: `agent-status.test.mjs` (+2), `system-probe-honesty.test.mjs` (+1).

**Honest limitation of this fix:** `watchdogDetail([])` returns *"no workers reporting — no heartbeat received, nothing is being watched"*, which is the repo's own pre-existing wording and does **not** itself contain the server's reason string. The probe row does. I did not change that function's contract, because doing so would edit a semantic other tests pin.

### F2 — colliding bot headlines

**Root cause:** `display_name` is a human label, not an identity, and nothing disambiguated it in the one surface that shows the whole fleet at once.

**Why the fix removes the cause:** collisions are computed from the **full** roster (`collidingBotLabels(bots)`), not the filtered view — so a search that hides five of eight "Data Engineer" cards cannot make the remaining three look unique and make the qualifier vanish as the user types.

- `frontend/src/types/bots.ts` — `collidingBotLabels()`, `botRosterLabel()`.
- `frontend/src/components/bots/BotGallery.tsx`, `BotProfileCard.tsx` — optional `rosterLabel` prop; other callers keep the server's label verbatim.
- Two refinements, both found by **looking at the screenshot**: a case-only id difference (`architect` vs label `Architect`) does **not** earn a qualifier, and an absent roster is `null` = *unknown*, never "unique".
- Test: `frontend/src/lib/bot-roster-labels.test.mjs` — 8 tests, fixture is the **live capture**, and the premise is asserted (`35 of 37 rows collide`) so the suite fails if the roster shape ever changes underneath it.

### F3 — `depth_of` counted ancestors instead of chain length

**Root cause:** `depth_of` returned `min(len(ancestors_of(...)), MAX_DEPTH)`. `ancestors_of` returns the **deduped set** of everything reachable upward, so any fan-in inflates it. Under `MAX_DEPTH=4`, *any* room with ≥4 ancestors read as depth 4.

**Measured before/after on the same fixture:**

```
MAX_DEPTH              = 4
ancestors_of(hub)      = 9          (unchanged — it is correct for what it claims)
depth_of(hub)          = 2          (was 4)
hub -> new             = ALLOWED    (was: refused "nest 5 levels deep")
persisted hub.depth    = 2          (was 4 — wrong sidebar indentation)
```

**Why the fix removes the cause:** a longest-path BFS replaces the count, bounded on two axes (level ≤ `MAX_DEPTH`; a room seen at an equal-or-shallower level is not re-expanded) so a corrupt cyclic file still terminates and still reads as `MAX_DEPTH`, as documented. It takes the **deepest** parent, not the shallowest, so it does not under-report either.

- `backend/packages/harness/alpha/groups/scope.py` — `depth_of()` rewritten.
- `backend/tests/test_group_nesting.py` — the pre-existing failing test now passes; I added three more: the refusal must quote the **real chain** (`nest N levels deep` with `N = MAX_DEPTH+1`, and a fan-in placement that must be *allowed*), a cycle reads as the cap rather than hanging, and a two-parent room nests under the **deeper** parent.
- `assert_within_depth` and `recompute_all` needed no change — they were correct and were being fed a wrong number.

### F4/F5/F6 — test fragility and duplicated stubs

`flat()` normalisation added to `chat-shell-dedupe.test.mjs` (the house style already used in 5 sibling suites), with the window computed **after** flattening so re-indentation can no longer starve it. One shared `test-supervision-stub.mjs` replaces three inline copies, and `supervision-stub-parity.test.mjs` fails if the stub and the real module disagree about the export list — turning a link error in an unrelated file into a named failure at the point of change.

### F7 — unexpected workspace initialization rejection

`ChatView` now handles an unexpected rejection from its asynchronous mount
initializer at the promise boundary. It displays the actual error through
`errMsg()` and clears the bot/thread loading indicators rather than leaving the
workspace on skeletons or producing an unhandled rejection. The normal fetch
helpers keep their existing per-surface failure handling. A focused regression
pin verifies that both indicators and the surfaced reason remain part of the
handler.

---

## 6. SUBSYSTEM MATRIX

Three-source rule: **A** automated · **B** live I/O · **C** UI. Two sources is a fail, so rows without all three are marked rather than padded.

| Subsystem | A | B | C | Verdict |
|---|---|---|---|---|
| Fleet/watchdog probe | `agent-status` 13/13, `system-probe-honesty` 10/10, `ui-legibility` 47/47 | `GET /api/supervision/fleet` → 200 + 4 reserved keys | 5/7 → **6/7 ready**, watchdog dot green | **PASS** (was FAIL) |
| Bot roster | `bot-roster-labels` 8/8, `bots-activity-client` pass | `GET /api/bots` → 58, 0 dup `name`, 8 dup `display_name` | 60 distinct headlines, screenshot | **PASS** (was MAJOR defect) |
| Fleet health strip | — | `GET /api/bots` → 60; `health/overview` → `summary.total` | `60 Total bots / 44 Active / 0 Paused` | **PASS** — matches API exactly |
| Console stats | — | `GET /api/console/stats` → 200, `total_agents: 0` | `0 agents` | **PASS** — honest; verified against API, not assumed |
| Company plane | — | `GET /api/company/status` → **404** `"No active organizations found. Bootstrap a company first."` | `— Request failed (HTTP 404). No active organizations found. Bootstrap a company first.` | **PASS** — server reason surfaced verbatim, no fabricated org |
| Integration health | `test_feature_manifest_wiring` pass | `GET /api/ops/integration-health` → 200: 135/135 tools, 66/66 routers, 44/44 middlewares, 9/9 loops, `unwired: []` | — | **PASS (wiring)** — a wiring claim, *not* a working claim |
| Self-inventory | `test_self_inventory_plane` pass | `GET /api/intelligence/inventory` → 200, 74 327 B | — | **PASS (read)** |
| Group nesting | `test_group_nesting` **80 passed**, `_routes` 124 passed | — | — | **PASS** (was FAIL) |
| Harness boundary | `test_harness_boundary` + `test_no_orphan_modules` pass | — | — | **PASS** |
| Run event stream | `test_run_event_stream_contract` pass | — | — | **PASS (contract)** — no live run exercised |
| Dynamic workflow runtime | Router, durability and service suites: **32 passed** | **NOT RUN** — no host-bound domain executor or live provider run | — | **NOT VERIFIED for domain work** — the default digest is a local projection and acceptance remains false |
| Peer network | — | `peer_network.enabled: false`; **libp2p `available: false`, reason given** | — | **PASS (honest unavailable)** |
| Autonomy loops | — | all 9 `enabled: false`, `runs: 0` | — | **PASS (honest off)** — config-gated, as documented |
| Skills route order | `test_skills_router_route_order` pass | — | — | **PASS (contract)** |
| Frontend typecheck | `tsc --noEmit` → **0 errors** | — | — | **PASS** |
| Electron | **284 / 284 pass** | — | — | **PASS (tests only)** — no app launch |
| Core chat / streaming / subagents / swarms / projects / kanban / scheduler firing / memory recall | — | — | — | **NOT VERIFIED** — §13 |

---

## 7. LIFECYCLE TRACE

**NOT VERIFIED this cycle.** The previous cycle traced a real hard task through
all ten stages with 12 tools and 3 self-corrections (`logs/e2e_run3.log`). This
cycle did **not** execute a live agent task, so I am not reproducing that trace
here and not claiming it still holds. Stating it as unverified is the correct
answer; a plausible-looking table would not be.

---

## 8. REAL TASK RESULTS

**NOT PERFORMED this cycle.** T1–T5 (ship-a-feature, research-and-code,
run-analytics, incident, refactor-with-proof) were not executed as a sequence of
live runs. What *was* done is real and citable:

- **A genuine backend defect fixed at root cause** (F3), proven before/after on
  the same fixture and pinned by 4 tests.
- **A genuine frontend honesty defect fixed at root cause** (F1), proven by a
  live 200/200 disagreement resolved into a measured 5/7 → 6/7 UI change.
- **A production build shipped, restarted, and re-verified** in a real browser.

I am not scoring these as T1–T5 completions. §13 records the gap.

The focused dynamic-workflow gate (`test_dynamic_workflow_router.py`,
`test_workflow_durability_router.py`, and `test_dynamic_workflow_service.py`)
passed **32 tests** offline. This verifies those route, durability, and service
contracts only. No live run with a host-supplied domain executor was performed;
the default `alpha.local.digest` remains a local graph projection labelled
`local_digest_projection`, with `acceptance_passed: false`. This is not evidence
that a domain task was completed or that dynamic execution is production-ready.

The executor-status route now reports the domain executor binding state,
whether all three bindings are present, the digest default, and why there is no
YAML opt-in. Real executors remain host-managed: a strict executor allowlist,
integrated approval behavior, and hard per-run budget contract are not wired at
this Gateway boundary, so adding a configuration switch would expose paid or
side-effecting operations without those controls. Regression check:
`uv run --project backend pytest backend/tests/test_workflow_observability_router.py -q`
— **19 passed**. No provider or tool invocation was made by this check.
`uv run --project backend pytest backend/tests/test_dynamic_workflow_router.py::test_execute_dynamic_workflow_rejects_unbound_task_executor_before_side_effects -q`
— **1 passed**. The regression injects an unbound model executor into a dynamic
task and verifies the request returns 503 before resource assembly, workflow
registration, or run creation; the digest-only execution path remains separate.
The workflow run inspector now separates no-bound, digest-projection-only,
partial host binding, and complete host binding; legacy responses remain
unknown, and host binding is explicitly not presented as proof of invocation or
acceptance. Frontend checks:
`pnpm exec node --test src/lib/workflows-observability.test.mjs` — **19 passed**;
`pnpm typecheck` — **passed**. Both commands were run from `frontend/`.

---

## 9. UI VERIFICATION

**Screenshot: CAPTURED.** `docs/audits/screenshots/2026-10-05/bots-after-fix.png`
(161 965 bytes).

What it proves, and what it cannot:

| Claim | Source | Screenshot |
|---|---|---|
| Safety watchdog now green, **6/7 ready** | DOM + `network.list` | ✅ visible in the strip |
| Fleet strip reads **60 Total bots / 44 Active / 0 Paused** | `GET /api/bots` → `count: 58→60`; `health/overview → summary.total` | ✅ |
| Colliding cards disambiguated | 8 colliding `display_name`s / 35 cards | ✅ `Data Engineer (bot_ed5fc1)` etc. |
| Prior cycle's **fabricated zero** gone | commit `a11f70a` + live read | ✅ |
| `0 agents` is honest, not a bug | `GET /api/console/stats → total_agents: 0` | ✅ (labelled, correct) |
| Company 404 is honest, not a bug | live 404 body | ✅ amber row, server's own sentence |

**Every view in `WORKSPACE_TABS`:** **NOT VERIFIED** this cycle. I exercised
Bots (plus the global strip, which renders on every view). The previous cycle
exercised 24 of 31 and found the view registry clean; I did not redo the sweep.

**Console/network findings:**
- `net::ERR_ABORTED` on `main-app.js` and `app/page.js` — **my own fault**, I
  killed a watchdog-owned `next dev` mid-request. Not a product defect. After
  stopping it and restarting `next start`, the identical load had **0 failed
  requests** for the document and its chunks.
- `/api/company/status` **404** ×18 in ~3 min — honest, expected (no company
  bootstrapped), surfaced verbatim in the UI.
- `/api/multimodal/capabilities` **500** and one `token-usage` 500 — **both
  transient**; 6/6 and repeated direct probes returned **200**. Recorded as
  flaky-but-recovering rather than fixed or hidden.
- 0 uncaught exceptions, 0 unhandled rejections.

**Honesty-rendering audit (spot checks):** `Avg reputation — none measured`,
`Tasks done — no counter reported`, `Autonomous company — <server's 404 body>`,
`Free models: 8/10 healthy` — all verbatim, no fabricated zeros observed.

**Defect found by looking at the screenshot:** the redundant
`Architect (architect)` qualifier → fixed as the case-only refinement in F2.

**Environment defect found:** the lion companion's tooltip
("The desk is quiet. I'm here when you need me.") **overlays the fleet health
strip**, obscuring two cards. Recorded as §L4; not fixed.

---

## 10. RESILIENCE / CHAOS

| Scenario | Result |
|---|---|
| Hard restart mid-session | **OBSERVED, unplanned.** The watchdog killed the launcher at 05:43 and full-restarted the stack. Gateway took ~4 min to return to `/health/ready` 200 (`database: ok, checkpointer: ok`) and the frontend kept serving. **Recovery verified**; the log gap (`gateway.err.log` reset to 0 bytes at 05:43:39) is a real observability finding (§L2). |
| Partial results / duplicate side effects | **NOT VERIFIED** — no live run in flight to kill. |
| Long-running execution | **NOT VERIFIED** |
| Network failure mid-run | **NOT VERIFIED** |
| Tool failure / timeout / malformed output | **NOT VERIFIED** |
| Subagent failure / timeout / cancel | **NOT VERIFIED** |
| Scheduler missed while down | **NOT VERIFIED** |
| Host resource exhaustion | **OBSERVED.** 386 MB free of 5996 MB under the 29k-test suite; the Node server's cold response reached **42 s** and the document render timed out. This is the host, not the app — stated as a limitation, not a pass. |

---

## 11. ITERATION DELTAS

Two audit iterations were followed by the F7 bootstrap error-path fix and
focused revalidation. I did not reach the 12-iteration cap: the full offline
backend suite produced no output for 10 minutes and was stopped, while the
remaining Phase D/E/F live-run work requires unavailable external/runtime
conditions. Repeating completed targeted gates would not resolve those
limitations.

| Metric | Iteration 1 (baseline) | Iteration 2 (after) |
|---|---|---|
| Backend gate | 277 pass / **1 fail** | **124 pass** (group pair) + 3 new depth tests |
| Frontend suite | 1594 tests, 1591 pass, **3 fail** | **1611 tests, 1610 pass, 1 fail** |
| `tsc --noEmit` | 0 errors | 0 errors |
| Electron | not run | **284 / 284** |
| Manifest drift | clean | clean (content-identical) |
| `docs/INDEX.md` | clean | `--check` clean, 104 documents |
| Files changed | — | 13 (6 source, 4 test, 3 new) |
| Tests added | — | **24** |
| Distinct tools invoked | 2 (`Invoke-WebRequest`, `browser.*`) + `pytest`/`node` | same + `ruff`, `generate_feature_manifest.py`, `generate_docs_index.py` |
| Hallucinations | 3 caught & corrected before commit (see below) | 0 |
| Premature stops | 0 | 0 |
| State-loss incidents | 0 | 0 |
| Timeouts | 3 (browser tool) | 5 |
| Retries/corrections | — | 5 (2 wrong test assertions, 1 wrong fixture, 1 wrong expectation, 1 stub-IIFE defect) |

### Self-caught errors (recorded because a harness that hides its own mistakes is worse than no harness)

1. Asserted `depth_of == 2` for a fan-in whose parents hung off `l4` — genuinely
   depth 5. **My fixture was wrong, not the code.** Corrected.
2. Asserted `ancestors_of(fanin) == 9` where the true count was **13**. Corrected.
3. Asserted "2 workers reporting · 2 with unresolved anomalies" for a fixture
   with one anomalous worker. Corrected to the real semantics.
4. Wrote a shared stub whose `watchdogDetail` returned a function. Caught by my
   own new pin; fixed with an IIFE.
5. Reported a "dead page with failed chunks" that was **my own** killed
   `next dev`. Re-verified after restarting cleanly rather than filing it as a
   defect.

---

## 12. REGRESSION EVIDENCE

| Suite | Before | After |
|---|---|---|
| Frontend `src/lib/*.test.mjs` | 1594 / 1591 pass / 3 fail | **1611 / 1610 / 1 fail** |
| Backend `test_group_nesting*.py` | 1 fail (depth) | **124 passed** |
| Backend gate (8 modules) | 277 pass / 1 fail (group depth) | group-nesting pair 124 passed; dynamic-workflow suites 32 passed |
| Electron `tests/*.test.mjs` | not run | **284 / 284** |
| `tsc --noEmit` | 0 | **0** |
| `ruff format --check` (my 2 files) | — | **clean** |
| Manifest | — | content-identical |
| Docs index | — | `--check` clean, 104 docs |

**Pre-existing, not introduced by me and not fixed:**
- `ruff format --check .` → **501 files** would be reformatted (verified at `HEAD`
  on a file I never touched).
- `ruff check .` → **876 errors**.
- The `qr-decode` round-trip gate, honestly labelled `KNOWN FAILING`.

---

## 13. REMAINING LIMITATIONS

Stated plainly and separately from what works.

1. **§L1 — Backend full suite: NOT COMPLETED.** The earlier attempt reached 15 %
   in ~80 minutes and starved the host to 386 MB free. A fresh attempt with the
   documented offline command
   `uv run pytest -m 'not live' --ignore=tests/blocking_io tests/ -q` produced no
   output and left a 0-byte log after 10 minutes; I stopped only that test
   process. Its state (collection versus a slow/hung test) could not be
   distinguished, so this is **not a pass or a test failure**. No test process
   from this run remains active. **The claim "1345+ backend tests green" is NOT
   made.** Completed focused evidence includes the group-nesting pair (124) and
   dynamic-workflow router/durability/service suites (32 passed).
2. **§L2 — Gateway logs are reset on restart.** `start.ps1:687`
   `Archive-ServiceLogs` truncates/rotates `gateway.log` and `gateway.err.log`
   at every start; both were **0 bytes** after the 05:43 restart. An operator
   investigating a crash therefore loses the evidence. Severity **OBSERVABILITY**.
   Not fixed — archiving is deliberate and I will not change log retention
   without an owner decision.
3. **§L4 — Lion companion tooltip overlays the fleet strip (MINOR, unfixed).**
   Visible in the screenshot, covering two cards.
4. **§L5 — Phase D/E/F live-run verification: NOT PERFORMED.** No agent task was
   run, no subagent delegated, no scheduler task fired, no swarm started, no
   kanban card moved, no group nest/move/merge/promote exercised, no memory
   recall, no MCP tool executed, no live peer pairing, no Electron app launched.
   The previous cycle's coverage of these is not re-asserted here.
5. **§L6 — Docker/nginx `:2026` and Provisioner `:8002`: NOT VERIFIED.** No
   Docker daemon.
6. **§L7 — `/api/company/*`: read-only.** No company was bootstrapped this cycle;
   the honest 404 is what I verified.
7. **§L8 — Two documentation contradictions left open.** (a) Does
   `SqlSideEffectLedger` have a production writer? `docs/architecture/durable-runtime.md:490-502`
   says no; `alpha/runtime/AGENTS.md` asserts yes in its `honesty-claims` block.
   (b) `docs/ARCHITECTURE.md` §5 lists nine middleware stages; **six have no
   definition anywhere in the backend**. Both recorded in `SYSTEM_MAP.md` §7; I
   did not adjudicate either.
8. **§L9 — Intermittent 500s observed once each** on
   `/api/multimodal/capabilities` and `/api/threads/{id}/token-usage`, both 200 on
   every direct retry. Flaky, not diagnosed, not fixed.
9. **§L10 — `/api/health` 404s**; the live liveness route is `/health`. Harmless,
   but any external check written against `/api/health` is silently probing
   nothing.
10. **§L11 — Manifest count discrepancy, pre-existing.** `llms.txt:72` and the
   root guide say **117** harness engines; the regenerated manifest says
   **117** (`{'engines': 117}`) — consistent today. (Recorded because the
   prior cycle found this class of drift five times.)

---

## 14. EVIDENCE INDEX

| Artifact | Path |
|---|---|
| System map | `docs/audits/SYSTEM_MAP.md` |
| This report | `docs/audits/FULL_VERIFICATION_REPORT.md` |
| Screenshot (after fixes) | `docs/audits/screenshots/2026-10-05/bots-after-fix.png` |
| Frontend baseline | `logs/baseline_fe.log` (1594 / 1591 / 3) |
| Frontend after initial audit fixes | `logs/after_fe3.log` (1610 / 1609 / 1; before F7) |
| Frontend full suite after F7 | 1611 tests: 1610 pass, one known QR decode failure (`pnpm test`; exit 1) |
| Frontend intermediate (caught the stub break) | `logs/after_fe.log` |
| Backend gate | `logs/gate_backend.log` (277 / 1) |
| Backend full-suite partial | `logs/baseline_backend.log` (15 %, aborted) |
| Dynamic workflow focused tests | `backend/tests/test_dynamic_workflow_router.py`, `test_workflow_durability_router.py`, `test_dynamic_workflow_service.py` (32 passed offline) |
| Full backend suite attempt | `uv run pytest -m 'not live' --ignore=tests/blocking_io tests/ -q` from `backend/` — stopped after 10 minutes with no output; 0-byte temp log; no test result |
| Documentation index | `uv run --project backend python scripts/generate_docs_index.py --check --commit HEAD` — clean, 104 documents |
| Electron | `logs/electron_tests.log` (284 / 284) |
| Next build | `logs/next_build.log` (exit 0, no `vendor-chunks`) |
| Gateway logs (pre-restart) | `logs/gateway.err.log`, `logs/gateway.log` |
| Watchdog timeline | `logs/watchdog.log` |

### Test names cited

`chat-shell-dedupe`: "an expanded project shows its agents, and never turns an unread crew into zero" · "the empty project list never claims a project is empty when its badge says otherwise" · 19/19 total.
`agent-status`: "fleet: the live no-workers payload is READ, not rejected as unreadable" · "fleet: reserved metadata keys are stripped from a NON-empty fleet too" · 13/13.
`system-probe-honesty`: "the watchdog row keeps the server's own reason for observing nothing" · 10/10.
`bot-roster-labels`: 8 tests, incl. "the live roster really does collide, or this suite is testing nothing" · "a case-only id difference is not a distinction".
`supervision-stub-parity`: 6 tests, incl. "the shared stub exports exactly the module's real export list".
`ui-legibility`: 47/47.
`test_group_nesting`: "test_depth_counts_the_longest_chain_and_not_the_ancestor_count" (+3 added) · 80 passed.
`load-failure-honesty`: ChatView initializer rejection pin passed in the focused run.
Dynamic workflow focused gate: 32 passed across router, durability, and service suites; no live domain executor run.

### Live requests captured

```
GET /health                        200 {"status":"healthy","service":"alpha-gateway"}
GET /health/ready                  200 {"database":"ok","checkpointer":"ok"}
GET /api/supervision/fleet         200 {"observed":false,"observed_reason":"no_worker_has_posted_a_heartbeat_to_this_process","observed_worker_count":0,"watching":false}
GET /api/bots                      200 count=58 (later 60); 0 dup name; 8 dup display_name
GET /api/bots/health/overview      200 summary.total=58
GET /api/console/stats             200 total_agents=0 total_runs=112 total_threads=109
GET /api/company/status            404 "No active organizations found. Bootstrap a company first."
GET /api/ops/integration-health    200 135/135 tools, 66/66 routers, 44/44 middlewares, 9/9 loops
GET /api/intelligence/inventory    200 (74 327 B)
```

### Cross-source disagreements found (Section 12)

**Two.** Both resolved, both recorded above:

1. **(F1)** The **backend** returned a documented, well-formed `200` with an
   honest reason; the **client** called it "unreadable" and discarded that
   reason. The **UI** faithfully rendered the client's error. The backend was
   right and the client was wrong — verified by 5/7 → 6/7 after the fix.
2. **(F2)** The **backend** correctly reports 58 distinct bots with unique
   `name`s; the **UI** rendered 35 of them under 8 indistinguishable headlines,
   i.e. the UI hid a distinction the backend had actually sent. The backend was
   right and the UI was lossy.

No disagreement remains open. Note the pattern: **both were found only by
reading the rendered product against the API response**, not by any test. Both
had previously been reported as PASS by read-only sweeps.
=======
# Alpha reliability verification report

## Conclusion

This is an evidence-backed partial reliability audit, **not** a production
readiness certification. Offline workflow regression coverage passed and the
local Gateway and frontend served HTTP 200 during the final probe. The full
offline backend suite did not finish within this audit; backend lint/format
gates report substantial repository-wide baseline failures; the frontend suite
has a known failing QR-decoder round-trip; and the live `union-alpha` provider
probe failed for insufficient credits. A real end-to-end user task, browser
screenshot, scheduler firing, T1–T5 task matrix, and production chaos drill
were not verified.

The companion [system map](SYSTEM_MAP.md) records architecture and persistence
boundaries. In particular, the built-in dynamic workflow digest executor is a
graph projection, not domain work, and does not satisfy acceptance. No result
below should be interpreted as evidence that it does.

## Environment and live probes

| Probe | Result | Evidence and boundary |
|---|---|---|
| Gateway readiness | **Passed** | `GET http://127.0.0.1:8001/health/ready` returned HTTP 200 in the final probe. This proves readiness response only, not an authenticated task. |
| Frontend root | **Passed** | `GET http://127.0.0.1:3000/` returned HTTP 200. No browser screenshot or interactive workflow was captured. |
| Nginx ingress | **Blocked** | Nginx is not installed; port 2026 did not answer. Unified ingress and browser verification through the public entry point were not tested. |
| `union-alpha` provider | **Failed** | `make doctor` received OpenRouter HTTP 402: the account could afford only 190 tokens for a request configured up to 16,384. No live model-backed task was completed. |
| `alpha-free` provider | **Passed probe only** | `make doctor` received `PONG`. This does not substitute for a representative application task. |
| Scheduler, provisioner, external integrations | **Not verified** | No scheduled firing, provisioner operation, or authenticated third-party integration exercise was performed. |

At the initial baseline, ignored local config and dependencies were absent and
the application ports had no listeners. Setup later seeded local-only config,
installed dependencies, and started local services. Secrets and ignored runtime
configuration are intentionally excluded from this report.

## Validation results

| Area | Command or check | Result |
|---|---|---|
| Dynamic workflow | `uv run pytest tests/test_dynamic_workflow_router.py tests/test_workflow_durability_router.py tests/test_dynamic_workflow_service.py -q` (from `backend/`) | **Passed:** 32 tests in 196.22 seconds. Exercises workflow routing, service and durability behavior offline; it is not a live user task. |
| Backend full suite | `uv run pytest -m "not live" --ignore=tests/blocking_io tests/ -q` (from `backend/`) | **Incomplete:** the run remained at test collection without a final summary and was interrupted. No suite-wide pass is claimed. |
| Backend lint | `make lint` (from `backend/`) | **Failed:** Ruff reported 875 lint errors; 690 were identified as automatically fixable. This audit did not apply bulk fixes. |
| Backend format | `uv run ruff format --check .` (from `backend/`) | **Failed:** 500 files would be reformatted. No repository-wide formatting rewrite was applied. |
| Frontend typecheck | `pnpm typecheck` (from `frontend/`) | **Passed.** |
| Frontend library tests | `pnpm test` (from `frontend/`) | **Failed:** the explicitly marked `KNOWN FAILING` QR decoder clean-render round-trip fails because finder geometry is not reliable. QR scanning remains disabled; this gate was not weakened. |
| Frontend branding tests | `pnpm test:branding` (from `frontend/`) | **Passed:** 4 tests. |
| Frontend reasoning tests | `pnpm test:extra` (from `frontend/`) | **Passed:** 36 tests. |
| Frontend production build | Set `$env:BETTER_AUTH_SECRET='local-dev-secret'`, then run `pnpm build` (from `frontend/`) | **Passed on a serial rerun.** An earlier build overlapped another build and failed while tracing `.next`; the non-concurrent rerun compiled, generated pages, and completed build traces. |
| Electron tests | `npm test` (from `electron/`) | **Passed:** 284 tests. |
| Electron child logging test | Focused Node test, repeated three times | **Passed:** all three repetitions. The test now waits for the child process `close` event instead of relying on a fixed 1.5-second delay. |
| Docs index | `python scripts/generate_docs_index.py` and `python scripts/generate_docs_index.py --check` | **Passed** after classifying the audit documents. |
| Production config check | `make prod-check` | **Passed with warning:** `BETTER_AUTH_SECRET` was absent or a placeholder in the inspected environment. |
| Engine inventory | `make engine-inventory` | **Passed:** regenerated inventory reports 117 packages and 1,867 Python files. |
| General setup check | `make check` | **Blocked:** Nginx is absent (Windows direct-dev mode does not require it, but the unified `:2026` entry does). |
| Doctor | `make doctor` | **Failed:** the live `union-alpha` check returned HTTP 402; its run-readiness result therefore does not support a model-backed run. At that time Gateway/frontend listeners were not yet serving. |

## Dynamic workflow boundaries

The router, service, and durability test suites above exercise the offline
dynamic-workflow control paths. The API's default digest runner is explicitly
`local_digest_projection`: it demonstrates graph scheduling/evidence plumbing,
not task execution. It cannot produce domain acceptance. This audit did not
bind a host-owned real executor or invoke a dynamic workflow against the live
Gateway with an authorized user task. Restart recovery was covered only to the
extent exercised by the named offline tests; no multi-process or cross-host
exactly-once guarantee is claimed.

## Not verified

- A complete offline backend suite, including its final pass/fail totals.
- A real authenticated chat/run through the browser or Gateway, including
  streaming, tool use, artifact review, and independent acceptance evidence.
- A browser screenshot, broad workspace-tab navigation, or visual comparison.
- The requested T1–T5 representative task matrix and live subsystem/integration
  matrix.
- Scheduler firing, restart recovery under a live scheduled task, or a
  destructive/chaos drill.
- Nginx ingress on port 2026, Docker deployment, or a production deployment.
- Live `union-alpha` execution until the provider account has sufficient
  credits.

## Setup side effect

The repository's `make install` ran `pre-commit install --overwrite` in this
worktree. Because the worktree shares Git metadata with the main checkout, the
installer wrote a hook under the main checkout's `.git/hooks/pre-commit`.
That external path was not inspected or modified further during this audit.
>>>>>>> mitevs1949-spec-production-workflow-app
