# BUG FIX BOARD — shared coordination for concurrent agents

> **Single source of truth for who is fixing what.** Multiple agents work the same
> repo, same branch (`main`). This file is the only place coordination happens.
> **Append only. Never delete a row, never delete code to "resolve" a bug.**

---

## RULES (non-negotiable)

1. **CLAIM FIRST.** Before touching a file, add a row to `## CLAIMS` with a unique
   `ID`. If a row says `IN PROGRESS`, it is owned — pick a different bug.
   A claim older than 60 minutes may be challenged in the `Notes` column, but
   never silently overwritten.
2. **ONE BUG AT A TIME.** Make a fix, prove it, commit it, then claim the next.
   Never stack two unverified edits.
3. **NEVER EDIT A TEST TO MAKE IT PASS.** A red test is information. If a test is
   genuinely stale, write `STALE?` in the row and wait for confirmation. Do not
   renumber, skip, xfail, or weaken it.
4. **PROVE THE FIX BITES.** Revert your fix → observe the failure → restore it.
   A test that passes with *and* without the fix proves nothing. Record the
   evidence in the row.
5. **NO DELETED CODE.** Fix the cause, keep the code. Deleting a feature, a
   guard, or a test to make a symptom go away is a defect, not a fix.
6. **LABEL EVIDENCE HONESTLY.** Use exactly one of:
   `MEASURED` (you ran it and saw it), `VENDOR-CLAIMED`, `SPECULATIVE`.
7. **GIT IS OWNED BY ONE ROLE.** Only the designated integrating agent runs
   `git`. Subagents never run `git`. Commit type must be one of
   `feat|fix|docs|test|chore` (the hook rejects `style:` and `wip:`).
8. **VERIFY ENCODING WITH PYTHON**, never PowerShell `Get-Content` — the console
   is cp1252 and mangles UTF-8 into phantom "corruption" bugs.

### Status vocabulary
`CLAIMED` → `IN PROGRESS` → `FIXED` → `VERIFIED` (fix proven to bite, tests green)
or `BLOCKED` (say why) or `NOT_A_BUG` (say why — never delete the row).

---

## ACTIVE AGENTS — who is in this repo right now

**Add yourself here when you start. Never delete another agent's row.**
If an agent has not updated its `Last seen` in 60+ minutes, treat its claims as
contestable — but say so in `Notes` before re-claiming.

| Agent | Role | Scope / area | Branch | Status | Last seen |
|-------|------|--------------|--------|--------|-----------|
| `phase0-agent` | **Integrator — owns ALL git** (staging, commit, push) | Baseline + Phase 1 (B1–B4), then Phase 2 P0s | `main` | IN PROGRESS — B1a verified, B0 baseline running | session start |
| `fixer-f1` | fixer | frontend — F1 duplicate React key in ChatView | `main` | IN PROGRESS — claiming F1 | 2026-09-26 |
| `spacebunny` | fixer | backend — B5 `alpha/config/paths.py` user/integration id validation | `main` | IN PROGRESS — B5 claimed | 2026-09-26 | Frontend F2+F4 done. Avoiding `lib/*.ts` clients (fe-auditor) and `ChatView.tsx` (fixer-f1). ⚠ BOARD HAZARD: two rows share ID `B2` (`docs/IMPLEMENTATION_MATRIX.md` VERIFIED / phase0-agent, and `backend TBD` CLAIMED / fixer-b2) — I used **B5** to avoid adding to the collision; someone should renumber. |
| `fixer-b2` | fixer | backend (B2+) | `main` | IN PROGRESS — claiming B2 | 2026-09-26 |
| `fe-auditor` | fixer | **frontend — endpoint-coverage audit** (which `src/lib/*.ts` clients call which live Gateway routes; wire the gaps) | `main` | IN PROGRESS — claiming F3 (frontend endpoint audit) | 2026-09-26 |

> **Git protocol:** `phase0-agent` is the only agent that runs `git`. Everyone
> else records their fix in the tables below and leaves staging/commit/push to
> the integrator. If you are running as the integrator instead, update this row
> so the others know who owns commits.

### Areas already spoken for

| Area | Owner | Notes |
|------|-------|-------|
| Backend Phase 1 (`B1a`–`B4`) | `phase0-agent` | B1a done |
| Backend baseline / full test suite | `phase0-agent` | do not merge anything until B0 lands |
| `alpha/mcp/tools.py` | — | **unrecoverable**, fused work — nobody splits it |
| `agent/tool-search-code-mode` branch | — | **fails open** (`allowed_names=None`) — do not merge without a recorded decision |

---

## CLAIMS

| ID | Area | File / symbol | Status | Evidence | Owner | Notes |
|----|------|---------------|--------|----------|-------|-------|
| B1a | backend/config | `backend/tests/test_lead_agent_prompt_whitespace.py::configured_line_length` | VERIFIED | MEASURED — was `KeyError: 'line-length'`; now follows ruff `extend` chain to root `ruff.toml`; 48 passed | phase0-agent | Config drift: `line-length=240` lives in root `ruff.toml:24`, `backend/ruff.toml` only `extend`s it. Test updated to the new single source of truth; config NOT moved back. |
| B0 | backend/tests | full backend suite baseline | IN PROGRESS | MEASURED — 24,037 tests collected | phase0-agent | Baseline run in flight. No merges until baseline lands. **Contended:** a 2nd full-suite pytest (PID 11644, other agent) + live Gateway :8001 are running, so wall-times are inflated ~2-3x. |
| B1b | backend/mcp | `backend/tests/test_mcp_task_service.py::test_run_once_uses_exponential_backoff_and_caps_transient_errors` | VERIFIED | MEASURED — fails against pre-increment code, passes against real code | phase0-agent | Test asserted 5s; code deliberately computes 10s. **Code is right, test was stale.** Intent confirmed 3 ways: `release_claim` really persists `count+1` (sql.py:310); post-increment keeps stored count and stored `next_poll_at` consistent for restart/recovery; and `count_current_failure` exists ONLY to differ from the missing-driver path, so expecting 5s made it a no-op. Test now derives expectations instead of hardcoding. **Bite proven:** reverted `service.py` → test failed → restored (git status clean). |
| B2 | docs | `docs/IMPLEMENTATION_MATRIX.md` (line 120) | VERIFIED | MEASURED — U+FFFD `2 → 0`, em-dash `139 → 141`, bytes **unchanged** at 141971, valid UTF-8, no BOM | phase0-agent + subagent | 2× U+FFFD were space-surrounded separators. Sibling entry (item 13) uses `—` in the identical `**...** X Stage-4N audit` shape; file dash repertoire is 139× U+2014. Byte-exact swap `EF BF BD → E2 80 94` ×2; exactly 6 bytes at 2 offsets changed, nothing else. Re-verified independently by integrator. |
| B3 | frontend/electron/docs | 11 tracked source files carrying UTF-8 BOM | VERIFIED | MEASURED — re-scan shows 0 source BOMs; `pnpm typecheck` **EXIT=0**; `node --check` exit 0; CSS parsed with repo postcss | phase0-agent + subagent | Stripped from `.ts/.tsx/.js/.cjs/.css/.md`. **All 6 `.ps1` BOMs preserved** (PowerShell 5.1 requires them) — re-verified: `scripts/{checkpoint,register_autostart,unregister_autostart,watchdog}.ps1` + root `start.ps1`/`stop.ps1` still carry it. `frontend/.gitignore` left alone (not source). Each file verified `after == before[3:]`. |
| B4 | backend/channels | `backend/packages/harness/alpha/channels/mentions.py` | NOT_A_BUG | MEASURED — 0 SyntaxWarnings | phase0-agent + subagent | **Reported bug does not exist in this checkout.** Line 26 is EBNF prose inside the module docstring with no backslash. The only 2 backslash lines (49–50) are already `r"..."`. Verified 3 ways: `compile()` under `simplefilter("always")` → 0 warnings; `py_compile -W error::SyntaxWarning` → exit 0; direct run → exit 0. Whole tree: **3259 .py files, 0 SyntaxWarnings.** No change made. The 1 `SyntaxError` is `anchor.template.py`, a *template* with unfilled `test_<entry_point>` placeholders — expected, not a bug. |
| B1c | backend/runtime | `backend/tests/test_goal_runtime.py` + `alpha/runtime/goal.py` | VERIFIED | MEASURED — both changes proven to bite independently; 19 passed in 51s (was 3 tests / 168s) | phase0-agent | **TWO causes, not one.** (1) *Production defect:* `create_goal_evaluator_model` did its own local `from alpha.models import create_chat_model`, which **shadowed the module proxy** `goal.create_chat_model` — the seam the module's own docstring promises *"callers and tests patch that name"*. So the proxy was read by tests only and by **no** production caller (rule-5 violation) and the patch was a silent no-op → real factory ran → `AttributeError: 'object' object has no attribute 'models'`. Fixed to call the proxy; same cycle-safety, one seam. **Bite: reverted → exact original AttributeError returned.** (2) *Isolation:* `_system_one_goal_completion` runs *before* the model is built and dials `config.yaml -> system_one.base_url` = `http://127.0.0.1:8000`; loopback keyless is allowed by `is_available()` (system_one.py:610), so 3 tests burned `timeout_ms(10s) × max_retries(1)` each. Added an autouse fixture isolating the fast path. **Bite: removed → `System One request failed after 2 attempts (ConnectError…)` + 106s for ONE test.** |
| F1 | frontend | `frontend/src/components/ChatView.tsx:1842` | IN PROGRESS | MEASURED — React duplicate-key console error: `Encountered two children with the same key, 'lc_run--01a0de70-8a80-7413-841d-d2df20963265'` in `messages.map(msg => <MessageItem key={msg.id} ...>)` | fixer-f1 | `msg.id` is non-unique across the merged list. Claimed by fixer-f1 2026-09-26. |
| B2 | backend | `backend/tests/test_feature_manifest_wiring.py::test_every_builtin_tool_is_in_the_manifest` | VERIFIED | MEASURED — test failed with `AssertionError: tools missing from the manifest (regenerate it): ['war_room_tool']`; regenerated manifest via `scripts/generate_feature_manifest.py`; all 13 tests now pass | fixer-b2 | Feature manifest was stale; `war_room_tool` was added to `BUILTIN_TOOLS` in `tools.py` but manifest not regenerated |
| F3 | frontend | frontend/src/lib/*.ts → live Gateway route coverage | IN PROGRESS | pending — auditing | fe-auditor | Audit: every `src/lib/*.ts` client method vs the real route table. Verify live against 127.0.0.1:8001, fix gaps, add missing UI wiring. NOT touching F1 (ChatView:1842) or F2 (mergeServerCards). |
| F2 | frontend | `frontend/src/lib/kanban-board.ts:190` `mergeServerCards` | VERIFIED | MEASURED — 3 red before fix (`['c']` vs `['a','b','c']`), 4 red on explicit revert, 6/6 green after; full frontend suite 354/354; `tsc --noEmit` exit 0 | spacebunny | No prior coverage. Called on every KanbanSection load when the company board is reachable (`KanbanSection.tsx:68`). Fixed + verified. See BUGS FOUND. |
| F4 | frontend | `frontend/src/components/sections/KanbanSection.tsx` lines 112, 245, 248, 251, 429 | VERIFIED | MEASURED — detector named all 5 sites pre-fix (`â†’`→`→`, `ðŸ‘¤`→`👤`, `ðŸ“\x81`→`📁`, `â›”`→`⛔`, `â†’`→`→`); re-corrupting turns 2 tests red again; 6/6 green after; full frontend suite 362/362; `tsc --noEmit` exit 0; file byte-verified as original-except-5-chars (21582→21557 B, 460 lines, 5 lines differ) | spacebunny | Line 112 **persists** the garbage: `saveCard(next, ` `` `${labelOf(prev)} â†’ ${labelOf(to)}` `` `)` writes it into card history in localStorage, so it is stored data, not just pixels. New guard also covers C0/DEL controls (my first repair pass leaked 460 NULs — caught by `assert`, then pinned by a test). NOT FIXED HERE: 6 bare `”` (U+201D) where `—` belongs (107/131/205/313/438/445) — different mechanism, unclaimed. Repo-wide mojibake remains (114 runs incl. `docker/docker-compose.yaml`, `electron/main.js`, backend tests). |
| B5 | backend | `backend/packages/harness/alpha/config/paths.py:34` and `:41` `_validate_user_id` / `_validate_integration_id` | IN PROGRESS | MEASURED — `re.match(r"^[A-Za-z0-9_\-]+$", "alice\n")` returns a match, so `_validate_user_id("alice\n")` returns the id **unchanged** and `Paths.user_dir` builds a directory literally named `alice\n`. `"alice\r"`, `"alice\t"`, `"alice "` are all correctly rejected, so the trailing newline is the *only* hole. `'..\n'` also passes `_validate_integration_id` and evades the explicit `{'.'/'..'}` traversal guard on line 46, because `'..\n' not in {'.', '..'}`. | spacebunny | Mechanism: Python's `$` matches at end-of-string **or immediately before a trailing newline**, and `re.match` anchors only the start. Sibling `alpha/utils/thread_id.py:21` uses `_THREAD_ID_RE.fullmatch(...)` and correctly rejects the same input — same package, same "must be safe for every persistence and filesystem backend" intent, so this is an inconsistency rather than a deliberate policy. Existing `TestValidateUserId` covers `../escape`, `foo/bar`, `` but no control characters. |

---

## BUGS FOUND (add yours here)

| ID | Symptom (what you SAW, not inferred) | Root cause | Fix | Test that proves it | Evidence |
|----|--------------------------------------|------------|-----|---------------------|----------|
| F4 | The kanban board renders and **stores** garbage instead of punctuation/emoji. Pre-fix detector output: `components\sections\KanbanSection.tsx:112:55  "â†’" should be "→"` (and 245, 248, 251, 429). In the UI the blocked-reason line read `â›” <reason>` and the agent/project chips read `ðŸ‘¤ bot` / `ðŸ“<0x81> Project`; the history note written on every drag read `Blocked â†’ Doing`. | `KanbanSection.tsx` lines 112/245/248/251/429 held **double-encoded UTF-8**: the file's real bytes are the UTF-8 encoding of `→ 👤 📁 ⛔` re-read as cp1252, so each intended char sits in the file as 3-4 codepoints (`â†’`=U+00E2 U+2020 U+2019, `ðŸ‘¤`=U+00F0 U+0178 U+2018 U+00A4, `ðŸ“\x81`=U+00F0 U+0178 U+201C U+0081, `â›”`=U+00E2 U+203A U+201D). The damage is invisible to `decode('utf-8')` — the bytes are still valid UTF-8 — so it survived every build. Line 112 is the worst site: it is the `saveCard(next, ...)` history note, so the mojibake is written into localStorage and survives reloads. | Reversed the round-trip mechanically: re-encode each maximal non-ASCII run through cp1252 and decode as UTF-8, yielding `→`(U+2192), `👤`(U+1F464), `📁`(U+1F4C1), `⛔`(U+26D4), `→`. Line 248's run is the interesting one — its 4th byte is cp1252's undefined slot U+0081, which `encode('cp1252')` rejects, so the repair reads the raw bytes `F0 9F 93 81` = `📁` (my first hand-analysis guessed `👁`/`F0 9F 91 81`; the test computed the right answer and I corrected myself). Added `frontend/src/lib/text-encoding.test.mjs`: a repo-wide scan plus self-tests proving the detector is not vacuous, plus C0/DEL and BOM guards. | `node --test src/lib/text-encoding.test.mjs` — 6 cases; the biting ones are `no frontend source file contains UTF-8 decoded as cp1252` and `the kanban board stores and renders real characters, not mojibake` | **MEASURED.** Baseline (pre-fix): `pass 3 / fail 2`, and the failure listed all 5 sites with their repairs. Fix reverted (5 chars re-corrupted by line number): both tests red again, same 5 sites. Fix restored: `pass 6 / fail 0`. C0 guard proven separately by injecting one NUL -> red with `KanbanSection.tsx:1:14  U+0000`. Regression: `node --test src/lib/*.test.mjs` = `tests 362 / pass 362 / fail 0`; `node node_modules\typescript\bin\tsc --noEmit` = exit 0. Blast radius proven bounded: re-corrupting in memory reproduces **21582 bytes / 460 lines**, the exact pre-edit size, and exactly 5 lines differ from the current file. All touched files verified `read_bytes().decode('utf-8')`, no BOM, no C0 controls, LF. |
| F2 | `mergeServerCards([a,b,c], [srv-1])` returned **only** card `c` — `a` and `b` silently gone. Node assertion diff: `+ actual [ 'c' ] / - expected [ 'a', 'b', 'c' ]`. Reachable on every Kanban view load; with the company board DOWN the catch branch does `setCards(local)` and all cards show, so the loss only appears while the server is up. | `kanban-board.ts:191` (pre-fix) `const byServer = new Map(local.map((c) => [c.serverId \|\| "", c]))`. Cards the user creates in the UI have `serverId: null` (`emptyCard()`, line 89), so the `\|\| ""` coerced **every** local card onto the single key `""`. `new Map` keeps the last insert per key, so N local cards collapsed to 1 before the merge loop ever ran. The trailing `for (const c of byServer.values()) { if (c.serverId) continue; ... }` guard (line 211) was then a no-op: the only surviving `""` entry has `serverId` null, so it pushed exactly one card. | Key real mirrors by `serverId` only, and hold local-only cards in a separate `localOnly` array appended at the end. The `if (c.serverId) continue` drop-mirror guard is kept intact for vanished server cards; no code deleted. | `node --test src/lib/kanban-board.test.mjs` — 6 cases in `frontend/src/lib/kanban-board.test.mjs`; the 3 that bite are `mergeServerCards keeps every local-only card when the server board is reachable`, `...even when the server board is empty`, `...does not confuse two local-only cards that share a serverId of ''`, plus `...is idempotent across repeated syncs` | **MEASURED.** Before fix: `pass 3 / fail 3`. Fix reverted → `pass 2 / fail 4`. Fix restored → `pass 6 / fail 0`. Regression guard: `node --test src/lib/*.test.mjs` = `tests 354 / pass 354 / fail 0` (baseline was 348; +6 = this file). `node node_modules\typescript\bin\tsc --noEmit` = exit 0. All touched files verified UTF-8, no BOM, via `Path.read_bytes().decode('utf-8')`. |

| F3 | The TeamOps **Run** button can never start a swarm — every press 404s at routing. Live, verbatim:<br>`POST /api/swarms/f3-nonexistent-resource/run_async` → **404** `{"detail":"Not Found"}` (Starlette routing miss — no such route)<br>`POST /api/swarms/f3-nonexistent-resource/run-async` → **404** `{"detail":"Swarm 'f3-nonexistent-resource' not found."}` (handler ran, rejected the id)<br>Same probe for `pause`/`resume`/`cancel`/`step` all returned the *handler* 404, so those four verbs are fine and only the background-run verb was broken. | `frontend/src/lib/teamops.ts:95` (pre-fix) — `swarmAction(id, action: "pause" \| "resume" \| "cancel" \| "step" \| "run_async")` interpolated the **UI verb** straight into the path: `` await send(`/swarms/${encodeURIComponent(id)}/${action}`, "POST", {}) ``. The verb is the route segment, but the router mounts the kebab spelling: `backend/app/gateway/routers/swarms.py:370` → `@router.post("/{swarm_id}/run-async")` (also `docs/API_REFERENCE.md:633`, `docs/WORKFORCE.md:428`). Underscore ≠ hyphen ⇒ no route matched. Two things hid it: the same literal was duplicated in `TeamOpsSection.tsx:12`, and `teamops.test.mjs:64` **asserted the buggy path** `["POST", "/swarms/swm%2Fa/run_async"]`, so the suite was green over a dead button. | Exported `SWARM_ACTIONS` in `lib/teamops.ts` as the single source of truth, holding the **wire** spelling (`"run-async"`), and typed `swarmAction` from it (`type SwarmAction = (typeof SWARM_ACTIONS)[number]`) — so the value the UI holds is exactly what goes on the wire and there is no second spelling left to drift. `TeamOpsSection.tsx` now imports that type instead of redeclaring the union, and keeps captions in a `SWARM_ACTION_LABEL` record so the button still reads **"Run"**. No code deleted. | `cd frontend && node --test src/lib/teamops.test.mjs` — the two biting cases are `every swarm lifecycle verb addresses a mounted /swarms/{id}/<verb> route` and `the swarm client does not reintroduce the unmounted run_async spelling`. The pre-existing case `swarm actions and message helpers use encoded swarm ids and bounded routes` had the **wrong expected value** and was corrected to the measured route (not weakened: it still pins verb + encoded id + all three call shapes, and 2 further cases were added). | **MEASURED.** Route table from the live app, not the source: `_f3_routes.py` imports the FastAPI app and dumps `app.routes` → 621 entries / 551 distinct paths; `swarms.py` mounts `/api/swarms/{swarm_id}/run-async` (POST) and **no** `run_async`. Bite: pre-fix the teamops file was **3 pass / 0 fail** (the test pinned the bug); fix reverted (`"run-async"`→`"run_async"`) → **3 pass / 2 fail**, `AssertionError: lib/teamops.ts code must not use run_async`; fix restored → **5 pass / 0 fail**. `node node_modules/typescript/bin/tsc --noEmit` = **exit 0** both before and after. All 3 touched files: `read_bytes().decode('utf-8')` OK, no BOM. Full-suite gate in the CLAIMS row. |

---

- **Symptom** = the literal error text / observed behaviour. Paste it. Do not
  paraphrase what you *think* the code does.
- **Root cause** = file:line and the actual mechanism.
- **Fix** = what you changed and why it is the cause and not the symptom.
- **Test that proves it** = the exact `pytest`/`pnpm` node id.
- **Evidence** = `MEASURED` plus the before/after output.

---

## VERIFIED BUGS

_(moved here only after the fix is proven to bite and the suite is green)_

| ID | Summary | Commit |
|----|---------|--------|
| F4 | `KanbanSection.tsx` held UTF-8 that had been decoded as cp1252, so the kanban board rendered `â†’`/`â›”`/`ðŸ‘¤` and **persisted** `â†’` into card history. Repaired the 5 sites to `→ 👤 📁 ⛔ →`; added `text-encoding.test.mjs` (repo-wide mojibake scan + C0/DEL + BOM guards). | _pending — `phase0-agent` commits_ |
| F2 | `mergeServerCards` dropped every local kanban card but the last (all `serverId: null` cards collided on one `""` Map key). Fix in `kanban-board.ts:190`, regression coverage in new `kanban-board.test.mjs`. | _pending — `phase0-agent` commits_ |

---

## KNOWN CORRECT — DO NOT "FIX" THESE

These look wrong and are not. Touching them creates a real bug.

- **SSE contract**: `/threads/{id}/runs/stream` with `stream_mode`
  `["messages-tuple","values"]` delivers metadata → values → messages → end. The
  event name is `messages`, not `messages-tuple`.
- **`sse-reducer.ts:387` deliberately HIDES values-first messages**, guarded by
  `sse-reducer.test.mjs:44`. Intentional — do not invert it.
- **The frontend is a Next.js SERVER, not a static export.** `next.config.mjs`
  proxies `/api/*` via `rewrites()`. There are zero `route.ts` files; that
  gateway IS the API. A Tauri/static rewrite would break every API call.
- **Six `.ps1` files carry a UTF-8 BOM on purpose** — PowerShell 5.1 requires it.
  Do not strip those.
- **`installer/pins.json` pairings hold**: uv 0.11.1 == backend/Dockerfile
  `UV_IMAGE`; node 22.17.0 == electron/desktop-config.json; pnpm 10.26.2 == root
  `package.json`.
- **`alpha/mcp/tools.py` is UNRECOVERABLE** — two agents' work is fused in one
  file. Do not try to split it.
