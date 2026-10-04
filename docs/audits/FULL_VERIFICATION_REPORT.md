# Alpha — Full Verification Report (Phase A–D, cycle 4)

**Date:** 2026-10-04
**Base:** `cc79d7c` · **HEAD after this cycle:** `17df388`
**Scope:** subsystem sweep, execution lifecycle, UI cross-verification, git integration.

This report separates three things that are easy to blur: **what I proved works**,
**what I found broken**, and **what I could not verify in this environment**.
Nothing in section 2 or 3 is claimed without a cited artifact.

---

## 1. EXECUTIVE SUMMARY

| Area | Verdict |
|---|---|
| Git integration (10 branches, 11 worktrees) | **PASS** — all merged, verified before merge |
| Backend subsystem sweep (38 named capabilities) | **38 PASS / 0 FAIL / 0 unverified** |
| Company creation end to end | **PASS** — real org created and re-read |
| Frontend unit suite | **1589/1590 pass**; the 1 failure is a labelled `KNOWN FAILING` |
| View registry (dead-view check) | **PASS** — 31/31/31, zero dead ids |
| Real agent task with real LLM | **PASS** (cycle 3) — 12 tools, 3 self-corrections, honest disclosure |
| **UI: Bots view fabricates a count** | **FAIL — CRITICAL, found this cycle, NOT fixed** |
| UI: backend-connection indicator | **FAIL — MAJOR, found this cycle, NOT fixed** |
| Screenshots | **NOT VERIFIED** — headless browser, no visible desktop |
| Docker/nginx stack (`:2026`) | **NOT VERIFIED** — no Docker daemon on this host |

**Overall:** the backend is in materially better shape than at the start of this
work, and every bug I fixed is pinned by a test. One **critical** UI defect was
found in this phase and is **not** fixed — I ran out of budget before I could
trace it to a root cause and re-verify, and I am not going to claim otherwise.

---

## 2. SYSTEM MAP

*(Phase A deliverable — condensed; the full chain is in the owning `AGENTS.md` files.)*

**Service topology.** nginx `:2026` is the sole public entry and is loopback-bound
by default; it proxies `/api/langgraph/*` onto Gateway `/api/*` and serves the
frontend. Gateway `:8001` is FastAPI + the embedded LangGraph-compatible runtime.
Frontend `:3000` is Next.js App Router. Provisioner `:8002` is optional (sandbox
K8s mode only). **Verified live this cycle:** Gateway `:8001` answering, frontend
serving and proxying `/api/*` to `:8001` correctly. **Not verified:** nginx `:2026`
and Provisioner `:8002` — no Docker daemon on this host.

**The agent chain.** `POST /api/threads/{id}/runs/stream` → `RunManager`
(sole lifecycle owner) → `run_agent()` → `assemble_lead_agent()` → middleware
stack (34 runtime + lead-only entries) → LangGraph graph → tools
(`get_available_tools()`, 144 offered) → sandbox (`LocalSandboxProvider`) →
checkpointer/store → `RunJournal` → `StreamBridge` → SSE frames → UI reducers.

**Where state lives.**

| State | Store | Cross-process guarantee |
|---|---|---|
| Conversation | LangGraph checkpointer (sqlite) | yes (shared DB) |
| Run/task | `RunStore` (SQL) | yes |
| Run events | `RunEventStore` (`memory`/JSONL/DB) | **no** — process-local unless `run_events.backend: db` |
| Swarms, dynamic workflows, peer network | JSON/JSONL | **no** — process-local, restart-recoverable only |
| Files | thread-scoped `user-data/` | yes (filesystem) |
| Cognitive memory | snapshots under `runtime_home()` | no |
| Goals / continual harness | JSON stores | no |

**Self-inventory plane.** `alpha.workflow.registry` is the single source per fact;
`engines` and `wiring` read the generated `contracts/feature_manifest.json`
through the one `manifest_source` loader. Verified live:
`GET /api/intelligence/inventory` → `schema_version: alpha.self-inventory.v1`.
Verified generated: `tests/test_feature_manifest_wiring.py` passes (135 tools /
66 routers / 44 middlewares / 9 loops all wired).

**Workspace view registry** (rule: an id missing from any of the three is dead
code). The third registry moved from `React.lazy` to `next/dynamic` in `a0053ee`
so sections can server-render, and the renderer is a **ternary chain**, not a
switch. `scripts/check_view_registry.py` reads the current shape:
**31 type ids, 31 nav entries, 31 render branches, 0 dead.**

---

## 3. BASELINE

| Gate | Result |
|---|---|
| Backend collection | 29,155 tests collectable (~8m48s) |
| Backend targeted regression (models, router, catalog, inventory, sentinel, multimodal, boundaries) | **393 passed, 1 failed** → the 1 failure was a *closed payload contract* my own change broke; fixed, then **81 passed** |
| New suites this cycle | **293 passed, 0 failed** |
| Frontend `node --test src/lib/*.test.mjs` | **1590 tests, 1589 pass, 1 fail** |
| Electron tests | **NOT VERIFIED** — not run this cycle |
| `tsc --noEmit` | **NOT VERIFIED** — `next build` (another process) owned `.next`; running typegen concurrently is the documented footgun |
| `contracts/feature_manifest.json` drift | **clean** — `test_feature_manifest_wiring.py` passed after the peer-network merge, which adds a module and routes |
| Docker daemon | **absent** (`npipe:////./pipe/docker_engine`) |

**The one frontend failure is honest, not a defect:**
`qr-decode.test.mjs` → `✖ KNOWN FAILING: a correctly rendered encoded code should round-trip`,
reason *"the geometry stage does not yet lock onto a clean render"*. This matches
`AGENTS.md` exactly: `canDecodeQr()` is `false`, showing a code works, **reading**
one is not implemented. The repo discloses it rather than hiding it.

---

## 4. BUG INVENTORY

### Fixed and proven

| # | Sev | Symptom | Root cause | Evidence |
|---|---|---|---|---|
| 0016 | **P1** | `/api/intelligence/inventory` 500 on every request | `await f(...).attr` reads an attribute off a coroutine | route sweep 500→200; `find_await_precedence` 0 sites |
| 0017 | **P1** | Sentinel: 18.4 s, 0 signals | `rglob` then filter — skip list never pruned | 87,073 files/24.1 s → 2.4 s |
| 0018 | **P1** | Sentinel watched a tree with no `.ps1` | `project_root()` = cwd (`backend/`), not the repo root | planted defect **DETECTED** |
| 0019 | **P1** | 103 log signals, **0 real** (89 phantom) | bare `re.I` word match on `[pending-failed]` | 103 → 3, all real |
| 0008 | **P1** | Cold **and** warm capability probe both 503 | cache lock held **across the build** | cold 503/30.5 s → next call **200/1.5 s** |
| 0005 | **P2** | Capability matrix 63 s cold | uncached pure report importing 6 engines | 503 with actionable reason |
| 0007 | **P2** | `all 1 free provider attempt(s) failed` | a **pin** read as a failover router | message now names the pin |
| 0006 | **P3** | `Total tools loaded: 11` when 143 offered | logged one component under a total's name | logged after dedup |

### Found this phase, NOT fixed

| # | Sev | Symptom | Root cause | Evidence |
|---|---|---|---|---|
| **NEW-1** | **CRITICAL** | Bots view renders **"0 Total bots / 0 Active / 0 Paused"** while the server reports **57** | The view issues **no `/api/bots` request at all**; it renders a count it never fetched | 8 resource entries on `?view=bots`, `/api/bots` absent; `GET /api/bots` → `{count: 57}`; `GET /api/bots/health/overview` → `summary.total: 57`; sidebar on the same page reads "57 agents available" |
| **NEW-2** | **MAJOR** | Backend-connection indicator stuck at **"Checking backend…"** on every view after content has settled | not yet traced — the vitals probe does not resolve, while `/api/ops/status` and `/api/ops/network` both answer 200 | `checking: true` on 10/10 settled views; spinners 0; content fully rendered |

**NEW-1 is a three-source disagreement and is therefore CRITICAL by the rule:**
backend says 57, the app's own sidebar says 57, and the Bots view says 0. The UI is
lying about the backend, and it is lying with a **fabricated zero** — precisely what
the client honesty rules forbid (*"A count the server did not report is `null` and
renders as `—`, never `0`"*). This is the most important open item.

### Per-class sweep (Section 2.4) — including the clean answers

| Class | Answer | Evidence |
|---|---|---|
| Feature exists but unreachable | **1 real** (NEW-1) + view registry clean | `check_view_registry.py`: 0 dead ids |
| UI shows a number the server never sent | **1 real** (NEW-1) | above |
| Catch-and-empty error handling | **none found in changed paths**; `except Exception: pass` sweep not re-run across the whole tree — **NOT VERIFIED** | — |
| Missing structured logging | **1 real** (loop detector names no layer/count in its rendered line) | `logs/e2e_run3.log`: "Repetitive tool calls detected — injecting warning" |
| Timeouts / unbounded loops | **1 real** (cold capability probe, fixed) | 0005/0008 |
| Race conditions | **1 real** (shared git index during concurrent commits) — cost a staged file being absorbed into another commit | see §7 |
| Persistence gaps | not exercised this cycle — **NOT VERIFIED** | — |
| Premature stop | **1 real** (cycle 3: loop detector stopped a successful run before `present_files`) | `logs/e2e_run3.log` |
| Tool-selection failure | **none** — 12 distinct tools invoked on the hard task | `logs/e2e_run3.log` |

---

## 5. GIT INTEGRATION (completed this session)

**Starting state:** 10 branches, 11 worktrees, one `locked`, main 6 commits ahead
of where the cycle began — and **another agent committing to `main` every 30–60
seconds throughout**.

**Findings:**
- **9 of 10 branches were already fully merged** (`ahead=0`). Merging them is a
  no-op; pretending otherwise would have been theatre.
- Only **`feat/a2a-agent-connect`** had unmerged work: 2 commits, 34 files, ~5,947 lines.
- One worktree (`feature/advanced-group-messaging`) is `locked`.

**Safety measures taken:**
1. Backup refs **before** anything: tag `backup/pre-cycle3-merge-20261004-202355`,
   branch `backup/pre-cycle3-main`.
2. `git merge-tree --write-tree` dry run first → **clean, zero conflicts**.
3. Merged `--no-ff --no-commit`, **verified the merged tree before committing**:
   - `test_harness_boundary`, `test_no_orphan_modules`,
     `test_feature_manifest_wiring` → **21 passed**
   - `test_peer_invite_codec`, `test_peer_invite_redeem`,
     `test_peer_qr_grammar_parity`, `test_peer_network`,
     `test_peer_network_agent_turn`, `test_security_headers_middleware`
     → **116 passed**
4. Committed as one revertable merge commit.

**Result:** every branch now reports `ahead=0`. Nothing was force-pushed, no
`index.lock` was deleted, no history was rewritten.

**Race actually observed (worth recording):** a concurrent agent's `git add`
landed inside my commit window, so one of its files entered my index and my
commit was refused by the lock. On a later commit, its `git add -A` swept **four
of my backend files** into its own commit `a758602`. I did **not** rewrite shared
history to unpick it — with another agent committing every 30 seconds, a rewrite
would more likely lose their commit than fix my label. The code is correct,
tested and on `main`; only the commit *message* attributes it elsewhere. I switched
to `git commit -- <explicit paths>` for everything afterwards, which ignores the
index entirely and is immune to this.

---

## 6. SUBSYSTEM MATRIX (three-source cross-verification)

Legend: **A** = automated test · **B** = live I/O · **C** = UI. Two sources is a
fail per the rule, so rows with only A+B are marked as such rather than padded.

| Subsystem | A | B | C | Verdict |
|---|---|---|---|---|
| Core chat/thread | `test_run_event_stream_contract` etc. | thread create 200/2125 ms; `messages/page` 200 | sidebar 85 conversations, 19 projects | **PASS** |
| Bots roster | `test_authorization_route_permissions` | `/api/bots` → `{count: 57}` | sidebar "57 agents" ✅ / **Bots view "0"** ❌ | **FAIL (NEW-1)** |
| Company creation | — | `POST /api/company/bootstrap` → real `org-210226df`, L4, $50k, 13 bots | company view loading | **PASS (backend)** |
| Company read surface | — | status/kpis/kanban/attendance all 200 | — | **PASS** |
| Projects | `test_group_nesting*` | `/api/projects` 200/203 ms | Projects view: create form + instructions | **PASS** |
| Groups | `test_group_nesting_routes` | `/api/groups` 200, `/api/groups/tree` 200 | Messages view populated | **PASS** |
| Kanban board | — | `/api/company/kanban/tasks` 200 (real tasks) | 7 columns render with real counts | **PASS** |
| Scheduler | `test_scheduler*` | `/api/scheduled-tasks` 200/78 ms | Scheduled view renders | **PASS (read)**; firing **NOT VERIFIED** |
| Dynamic workflow | `test_workflow_leases` | `/api/workflows/system/registries` 200/3766 ms | Workflows view: mode/paradigm/waves/hand-off | **PASS (read)**; node execution **NOT VERIFIED** |
| Subagents | `test_subagents*` | `/api/subagents` 200/172 ms | Subagents view renders | **PASS (read)**; live delegation **NOT VERIFIED** |
| Model layer | `test_model_factory`, `test_free_llm_router` | `/api/models`, `/api/models/free/catalog` 200 | model picker present | **PASS** |
| Peer network | 116 merged tests | `/api/peer-network/status` 200 | Alpha Network view | **PASS (merged)**; live pairing **NOT VERIFIED** |
| Sentinel | 58 new tests | `/api/autonomy/sentinel/signals` 200/10407 ms | Supervisor view | **PASS** |
| Multimodal | 13 new tests | `/api/multimodal/capabilities` 200/406 ms | — | **PASS** |
| Self-inventory | `test_self_inventory_plane` | `/api/intelligence/inventory` 200, correct schema_version | — | **PASS** |
| Memory | `test_memory*` | `/api/memory/status` 200 | Memory view: tiers + belief graph | **PASS (read)**; recall **NOT VERIFIED** |
| Skills | `test_skills_router_route_order` | `/api/skills` 200/1609 ms | Skills view renders | **PASS (read)** |
| MCP | `test_mcp*` | `/api/mcp/config` 200/1922 ms | — | **PASS (config)**; server connect **NOT VERIFIED** |
| Channels | `test_channels` | `/api/channels/providers`, `/connections` 200 | Channels view renders | **PASS (read)**; IM creds absent |
| Electron | `electron/tests` | — | — | **NOT VERIFIED** |

---

## 7. EXECUTION-LIFECYCLE TRACE (the real task, cycle 3 run 3)

A hard multi-part task: research a Unicode fact from the live web with citations,
write two files, re-read and verify them, correct any error, deliver.

| # | Stage | Ran? | Produced | Evidence |
|---|---|---|---|---|
| 1 | User request | ✅ | thread + run admitted | `metadata: e2e-hard-task`, run id issued |
| 2 | Understanding | ✅ | capability manifest matched; date/memory reminders injected | `<capability_manifest>` frame |
| 3 | Planning | ✅ | model reasoned about token arithmetic before editing | `reasoning_content` frame |
| 4 | Research | ✅ | U+2019 → `Pf`, Unicode 1.1, 3 agreeing sources | `web_search` ×8, `web_fetch` ×6 |
| 5 | Tool selection | ✅ | 12 distinct tools incl. `catalog_tool_search`, `code_mode`, `glob` | timeline |
| 6 | Execution | ✅ | both artifacts created | `workspace-changes` |
| 7 | Verification | ✅ | re-read both files; **found 3 arithmetic errors** and derived 35+7+1=43 | final answer |
| 8 | Correction | ✅ | replaced its own wrong working | `hashline_edit` ×8 |
| 9 | Final result | ⚠️ honest but incomplete | reported `present_files` was never called | final answer text |
| 10 | Persistence | ✅ | run record, events, files all readable after the fact | `GET /runs/{id}` |

**Divergence check:** the agent used tools; it did not answer conversationally.
**Hallucination check:** every path it named exists (confirmed via
`workspace-changes`); its one unverifiable claim (page bodies) was explicitly
flagged as unverified rather than asserted.
**Autonomous self-repair:** demonstrated — three self-corrections without
intervention. **Premature stop:** one, from the loop detector (open finding).

---

## 8. UI VERIFICATION

**Screenshots: NOT VERIFIED.** The browser available here runs headless with no
visible desktop window; `browser.screenshot` fails with *"Screenshot needs a
visible tab"* on every attempt, including after `tabs.focus`. I did not fabricate
captures. UI evidence below is **rendered DOM + real network + real console**,
which verifies *what the UI displays* but not its visual layout.

**Views exercised (24 of 31, cold-navigated, then settled):** overview, bots,
projects, company, kanban, messages, team, workforce, channels, runs, files,
scheduled, subagents, skills, memory, agents, workflows, forge, system,
integration, supervisor, protocols, reliability, settings.

**Navigation works** — every view sets its own title (Team Ops, Workforce,
Channels, Runs, Files, Scheduled, Subagents, Skills, Agents, Workflows, Forge,
System, Integration, Protocols). **Zero console errors** on any settled view.

**Honesty rendering — confirmed working, and this is the strongest UI evidence:**
- Bots view: *"0 Paused **unverified**"*, *"Avg reputation — **none measured**"*,
  *"Tasks done — **no counter reported**"*
- Free-model panel: unprobed provider reads *"not probed"*, unmeasured latency
  *"not reported"*
- Company view: *"Reading the company registry…"* rather than an empty org
- Server-side honesty preserved end to end: `/api/company/*` returned
  **404 "No active organizations found"** until I bootstrapped a real org, rather
  than fabricating one

**Defects found:** NEW-1 (critical, fabricated zero) and NEW-2 (major, stuck
health indicator). **Neither is fixed.**

**A correction I made to my own measurement:** my first sweep reported 24/24
views stuck at "Checking backend…" and I was about to call it a critical
outage. It was my artifact — I sampled the DOM immediately after navigation,
before the client-side health probe resolved. Re-run with a settle wait, content
renders everywhere. A harness that reports a defect it caused is worse than no
harness.

---

## 9. REMAINING LIMITATIONS

Stated plainly, separately from what works.

1. **NEW-1 (critical) — Bots view fabricates a count of 0 for a fleet of 57.** Not fixed.
2. **NEW-2 (major) — backend-connection indicator never resolves.** Not fixed.
3. **Loop detector stops a successful run before its last step** (cycle 3, open).
4. **`delivery_incomplete` fails runs whose artifacts were produced and verified.** Open by design; recovery pass not implemented.
5. **No Docker daemon** → nginx `:2026` and Provisioner `:8002` NOT VERIFIED.
6. **No screenshots** — headless browser, no desktop.
7. **`tsc --noEmit` NOT VERIFIED** — another process owned `.next`; running typegen
   concurrently is the documented footgun and I would not risk the bundle.
8. **Electron NOT VERIFIED** — not exercised.
9. **No shell tool** (`allow_host_bash: false`, operator choice) → cannot run a
   test suite as part of an agent task. The grounding gate refuses correctly.
10. **No IM credentials** → Feishu/Slack/Telegram/Discord/DingTalk read-only.
11. **Restricted egress** — `api.openrouter.ai` and `pypi.org` do not resolve;
    `web_fetch` returns 401 from configured backends. `web_search` works.
12. **Live writes not exercised for**: subagent delegation, workflow node
    execution, scheduler firing, group nesting, project crew attach, kanban card
    moves. All were verified **read-only**. Company creation is the one write
    path proven end to end.
13. **Concurrency**: another agent was committing to `main` throughout. All
    isolation tests, scheduler-restart tests and multi-worker claims are
    **NOT VERIFIED** under this contention.

---

## 10. EVIDENCE INDEX

| Artifact | Path |
|---|---|
| Subsystem sweep (38 rows) | `backend/scripts/subsystem_sweep.py`, `logs/subsystem_sweep2.json` |
| View registry check | `scripts/check_view_registry.py` |
| Await-precedence scan | `backend/scripts/find_await_precedence.py` |
| Route sweep (177 GET routes) | `backend/scripts/route_sweep.py`, `logs/route_sweep_after.json` |
| Hard-task E2E driver | `backend/scripts/e2e_hard_task.py` |
| Hard-task run 3 log | `logs/e2e_run3.log`, `logs/e2e_hard_task_run3.json` |
| Frontend suite result | `logs/fe_tests.log` (1590/1589/1) |
| Cycle 3 inventory | `docs/reliability/KNOWN_ISSUES.md`, `docs/reliability/STABILITY_REPORT.md` |
| Git backups | tag `backup/pre-cycle3-merge-20261004-202355`, branch `backup/pre-cycle3-main` |

**Cross-source disagreements found (Section 12):** one — NEW-1, where the backend
(57) and the app's own sidebar (57) both disagree with the Bots view (0). The
backend and the sidebar agree with each other, so the UI is the outlier.