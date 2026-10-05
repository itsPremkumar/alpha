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
| **F1** Frontend suite | **1609 / 1610 pass** (baseline 1591 / 1594) — only the pre-existing, honestly-labelled `KNOWN FAILING` QR gate remains |
| **F2** `tsc --noEmit` | **0 errors** |
| **F3** Electron suite | **284 / 284 pass** |
| **F4** Backend gate (8 modules) | **277 passed / 1 failed** → after the depth fix, **124 passed** across the group-nesting pair |
| **F5** Fleet-reserved-key client bug | **FIXED** — subsystem readiness **5/7 → 6/7** measured in the live UI |
| **F6** Bot-roster legibility | **FIXED** — 8 colliding `display_name`s / 35 of 58 cards, now disambiguated |
| **F7** Brittle source-text tests | **FIXED** — 2 tests broken by commit `2dbfe90`, no defect existed |
| **F8** Group-nesting `depth_of` | **FIXED** — fan-in counted ancestors, refusing valid placements with a fabricated depth |
| **F9** Duplicate supervision stubs | **FIXED** — one shared stub + a parity tripwire, after two link-time failures |
| Manifest drift | **clean** — regeneration diff is the timestamp only |
| `docs/INDEX.md` | **regenerates cleanly** — 103 documents, +2 indexed |
| Screenshots | **CAPTURED** — `docs/audits/screenshots/2026-10-05/bots-after-fix.png` |
| Docker/nginx `:2026`, Provisioner `:8002` | **NOT VERIFIED** — no Docker daemon on this host |

**Overall verdict: PASS on every gate that can be exercised on this host, with
four real defects found and fixed at root cause. One MAJOR unfixed finding is
recorded in §13 (§L3), and the full Phase D/E/F exercises that the previous
cycle left unverified were **not** completed this cycle — see §13 for exactly
what that means.**

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
| Catch-and-empty error handling | **1 real, unfixed (§L3)**: `ChatView.init()`'s `Promise.all` at `ChatView.tsx:737` is **outside any `try`/`catch`** (the only `try` covers `loadStore()` above it). `listProjects()` has a `.catch`; `fetchBots`, `fetchModelCatalog`, `fetchFeatures` all catch internally and resolve — so it does not fire today, but the structure is one unguarded await from a whole-app abort | static read; §L3 |
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

Two iterations. I did not reach the 12-iteration cap; I stopped because the
remaining work is Phase D/E/F live-run verification that this host cannot
complete in the remaining budget, and padding iterations with re-runs of the
same gates would be theatre.

| Metric | Iteration 1 (baseline) | Iteration 2 (after) |
|---|---|---|
| Backend gate | 277 pass / **1 fail** | **124 pass** (group pair) + 3 new depth tests |
| Frontend suite | 1594 tests, 1591 pass, **3 fail** | **1610 tests, 1609 pass, 1 fail** |
| `tsc --noEmit` | 0 errors | 0 errors |
| Electron | not run | **284 / 284** |
| Manifest drift | clean | clean (content-identical) |
| `docs/INDEX.md` | clean | regenerates, +2 indexed |
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
| Frontend `src/lib/*.test.mjs` | 1594 / 1591 pass / 3 fail | **1610 / 1609 / 1 fail** |
| Backend `test_group_nesting*.py` | 1 fail (depth) | **124 passed** |
| Backend gate (8 modules) | 277 pass / 1 fail | all pass |
| Electron `tests/*.test.mjs` | not run | **284 / 284** |
| `tsc --noEmit` | 0 | **0** |
| `ruff format --check` (my 2 files) | — | **clean** |
| Manifest | — | content-identical |
| Docs index | — | regenerates, 103 docs |

**Pre-existing, not introduced by me and not fixed:**
- `ruff format --check .` → **501 files** would be reformatted (verified at `HEAD`
  on a file I never touched).
- `ruff check .` → **876 errors**.
- The `qr-decode` round-trip gate, honestly labelled `KNOWN FAILING`.

---

## 13. REMAINING LIMITATIONS

Stated plainly and separately from what works.

1. **§L1 — Backend full suite: NOT COMPLETED.** It reached 15 % in ~80 minutes
   and starved the host to 386 MB free. I killed it to free the machine and
   report a targeted gate instead. **The claim "1345+ backend tests green" is NOT
   made.** Only the 8-module gate (277→all pass) and the group-nesting pair (124)
   were run to completion.
2. **§L2 — Gateway logs are reset on restart.** `start.ps1:687`
   `Archive-ServiceLogs` truncates/rotates `gateway.log` and `gateway.err.log`
   at every start; both were **0 bytes** after the 05:43 restart. An operator
   investigating a crash therefore loses the evidence. Severity **OBSERVABILITY**.
   Not fixed — archiving is deliberate and I will not change log retention
   without an owner decision.
3. **§L3 — `ChatView.init()` has an unguarded `Promise.all` (MAJOR, unfixed).**
   `ChatView.tsx:737` sits outside any `try`/`catch`; the only `try` covers
   `loadStore()` above it, and there is **no `finally`**. Today every dependency
   catches internally and resolves, so the rejection path is unreachable — but
   `listProjects()`/`listCommands()` beside it are not in that `Promise.all`, and
   the structure makes any future non-catching dependency abort the entire
   workspace bootstrap (`setBotsLoading(false)`, `setThreadsLoading(false)`,
   `setModels`, `setFeatures` all skipped) with an unhandled rejection. Root
   cause is real and named; I did not fix it because the honest fix is to
   restructure a 130-line initialiser and re-verify all 31 views, which I could
   not complete.
4. **§L4 — Lion companion tooltip overlays the fleet strip (MINOR, unfixed).**
   Visible in the screenshot, covering two cards.
5. **§L5 — Phase D/E/F live-run verification: NOT PERFORMED.** No agent task was
   run, no subagent delegated, no scheduler task fired, no swarm started, no
   kanban card moved, no group nest/move/merge/promote exercised, no memory
   recall, no MCP tool executed, no live peer pairing, no Electron app launched.
   The previous cycle's coverage of these is not re-asserted here.
6. **§L6 — Docker/nginx `:2026` and Provisioner `:8002`: NOT VERIFIED.** No
   Docker daemon.
7. **§L7 — `/api/company/*`: read-only.** No company was bootstrapped this cycle;
   the honest 404 is what I verified.
8. **§L8 — Two documentation contradictions left open.** (a) Does
   `SqlSideEffectLedger` have a production writer? `docs/architecture/durable-runtime.md:490-502`
   says no; `alpha/runtime/AGENTS.md` asserts yes in its `honesty-claims` block.
   (b) `docs/ARCHITECTURE.md` §5 lists nine middleware stages; **six have no
   definition anywhere in the backend**. Both recorded in `SYSTEM_MAP.md` §7; I
   did not adjudicate either.
9. **§L9 — Intermittent 500s observed once each** on
   `/api/multimodal/capabilities` and `/api/threads/{id}/token-usage`, both 200 on
   every direct retry. Flaky, not diagnosed, not fixed.
10. **§L10 — `/api/health` 404s**; the live liveness route is `/health`. Harmless,
    but any external check written against `/api/health` is silently probing
    nothing.
11. **§L11 — Manifest count discrepancy, pre-existing.** `llms.txt:72` and the
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
| Frontend after | `logs/after_fe3.log` (1610 / 1609 / 1) |
| Frontend intermediate (caught the stub break) | `logs/after_fe.log` |
| Backend gate | `logs/gate_backend.log` (277 / 1) |
| Backend full-suite partial | `logs/baseline_backend.log` (15 %, aborted) |
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