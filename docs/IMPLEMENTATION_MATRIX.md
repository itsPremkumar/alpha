# Alpha Implementation Matrix (P1)

Living status matrix for the production-grade implementation plan. One row per
subsystem, each linked to evidence. Status values:

- **✅ verified** — tested with cited evidence (file/command + result)
- **🟡 partial** — works, with a stated caveat or pending confirmation
- **⏳ in progress** — work running or applied, verification not yet reported
- **⬜ blocked** — waiting on an external action

Updated: 2026-09-23 · cycle base `3910838` · remote `main` @ `https://github.com/itsPremkumar/alpha.git`

---

## A. Verification gates

| Gate | Status | Evidence |
|---|---|---|
| Recovery suite (30 scenarios: gateway, frontend, launcher kill→watchdog replace, Layer-4 `-Once` restore, double-kill, maintenance + resume, provider independence) | ✅ | `logs/verify_recovery_last.json` — run #2: **30 PASS / 0 FAIL**, exit 0 |
| Backend unit/integration suite (full `pytest tests`) | ⏳ | background run, log target `logs/pytest_last.log`; triage of any failure is the next fix-queue item |
| Frontend typecheck (`tsc --noEmit`) | ✅ | exit 0 (run after BOM strip, 2026-09-23) |
| Frontend production build (`next build`) | ✅ | exit 0, 89 s, 4/4 static pages, First Load JS 197 kB |
| Electron unit tests | ✅ | 8/8 pass (`node --test electron/tests/desktop-utils.test.mjs`) |
| Title-middleware tests (incl. new real-runtime + concurrency tests) | ✅ | `test_title_generation.py` 9/9 + `test_title_middleware_core_logic.py` 62/62 = **71 passed** |
| PowerShell parse (19 `.ps1` files) | ✅ | 0 errors |
| Python `compileall` | ✅ | exit 0 |
| YAML parse (20 files) | ✅ | 20/20 valid |
| JSON parse (33 tracked `*.json`, `utf-8-sig`-tolerant) | ✅ | **33/33 valid** after tsconfig BOM fix |
| `scripts/prod_check.py --strict` | ✅ | **0 failures / 0 warnings** — “production ready” (now also compares harness pyproject) |
| `scripts/verify_versions.sh` (all version sources incl. harness) | ✅ | exit 0 in both modes via Git-bash login shell (prints all 5 figures incl. harness 2.1.0); `bash -n` exit 0 on bump+verify; note: `verify-versions.yml` gates v* tags only, so this local run is the push-cycle gate |
| Zero-unwired-features wiring audit (`scripts/audit_frontend_wiring.py`) | ✅ | **216/216 frontend call sites → real gateway routes (454), exit 0**; found + fixed 3 real defects (see fix-log #5) |
| Env config wiring audit (`scripts/audit_env_wiring.py`) | ✅ | **DEAD-SET 0 / SET-ONLY 0, exit 0** after two pattern fixes; no launcher sets a rename-family variable nothing reads; 57 READ-ONLY entries are optional code-defaulted knobs (incl. `AGENT_WORKSPACE_ENV` prod marker — label-only, unset by design in every shipped config) |
| Docker compose config (prod + dev) | ✅ | docker 29.8.0 / compose v5.5.0: both files validate (exit 0; unset-secret warnings are pre-`.env` interpolation only); no host-local absolute paths; images pinned `redis:7|8-alpine`, `nginx:alpine` (no `:latest`) |

## B. Self-healing runtime (four layers)

| Subsystem | Status | Evidence |
|---|---|---|
| Launcher + 2 s monitor (gateway/frontend supervision) | ✅ | recovery suite: launcher kill → watchdog replaced it (new PID observed) |
| Watchdog loop, kill → restore (Layer 4) | ✅ | recovery suite: watchdog kill → `-Once` task restored; double-kill scenario 166 s PASS |
| Maintenance mode (stays stopped) + `start.ps1` resume | ✅ | recovery suite: maintenance PASS + resume PASS (127 s) |
| Provider independence (empty `OPENROUTER` key) | ✅ | recovery suite PASS |
| Scheduled tasks: `Alpha_Autostart`, `Alpha_Watchdog` (-Once/5 min), `Alpha_TrayStatus` | ✅ | all `Ready`, `StartWhenAvailable` + `IgnoreNew` |
| Watchdog behaviour under load | ✅ | watchdog.log: readiness flaps to `starting` under CPU load are **deferred** (no restart loop), gateway uptime continuous (no restart observed across probes) |
| Live stack heartbeat | ✅ | `GET :8001/health` 200 `healthy`; `GET :8001/health/ready` 200 `database:ok checkpointer:ok`; frontend `:3000` listening; heartbeat files fresh |
| Tray state display (state-colored icon behind `^` chevron) | 🟡 | tray process + `logs/tray.log` verified running; **visual confirmation from user pending** |
| Unplanned full-stack recovery — **2 real events** | ✅ | OpenCode server restarts at **07:44** and **08:15** killed launcher+gateway+frontend (harness children); `watchdog.log` shows **Layer-4 `loop_recreate` + `full_restart` escalation** both times (`pid=13192 alive=False → repairing`, `escalation after 1 failing checks: gateway=down frontend=down launcher=dead`); stack healthy again within seconds, all tasks `Ready` — and the restarted gateway came up with the new config (`docs_enabled: false`, `version: 2.1.0` live) |

## C. Configuration & worldwide portability

| Item | Status | Evidence |
|---|---|---|
| `GATEWAY_ENABLE_DOCS=false` production default | ✅ | active in `.env` and now in `.env.example` (fresh installs via `install.ps1`/`install.sh`/`start.ps1` inherit it) — commit `3999abd`; `prod_check --strict` passes |
| `.env.production.example` template | ✅ | strict-UTF-8, no BOM (console mojibake was PowerShell-5.1 display only) |
| `frontend/tsconfig.json` UTF-8 BOM | ✅ | stripped (bytes now `123,10,32`) — commit `84e5a1d`; `tsc` exit 0 after |
| Version sources in lockstep (backend, **harness**, frontend, Chart) | ✅ | `bump_version.sh`, `verify_versions.sh`, `prod_check.py` all extended to `backend/packages/harness/pyproject.toml`; `prod_check --strict` prints 4× 2.1.0 exit 0; `verify_versions.sh` exit 0 both modes; `bash -n` clean; harness is a workspace dependency (`backend/pyproject.toml:113`) present in `uv.lock` |
| No hardcoded secrets / auth untouched | ✅ | auth not wired, modified, or redesigned this cycle; `.env` gitignored; `BETTER_AUTH_SECRET` present but never printed |

## D. Ops & API endpoints

| Endpoint | Status | Evidence |
|---|---|---|
| `GET /health`, `GET /health/ready` | ✅ | 200 / 200 with DB + checkpointer probes OK |
| `GET /api/ops/status` | ✅ | 200: `status ok`, `uptime_seconds`, `time_utc`, `docs_enabled` flag present |
| `GET /api/ops/version` | ✅ | **bug found + fixed**: resolver looked up a duplicated, non-existent dist tuple `("agent-workspace", "agent-workspace")` → always `"unknown"`. Fixed to `agent-workspace-harness → alpha → agent-workspace` with a chain-order regression test; **unit ✅ 16/16**; **deployment path ✅** (image ships harness dist via `uv sync --locked` workspace). **Live ✅**: `GET /api/ops/version` → `"version":"2.1.0"` after the watchdog-driven restart |
| `/docs` `/redoc` `/openapi.json` in production | ✅ | template/env default off; **live ✅**: `/api/ops/status` → `"docs_enabled":false` confirmed on the running gateway (restart picked up `.env`) |

## E. Test debt & honesty items

| Item | Status | Evidence |
|---|---|---|
| Stale TODO placeholders in `test_title_generation.py` | ✅ | replaced by real tests: real compiled graph + `InMemorySaver` persistence (invoke & ainvoke), thread-concurrency, interleaved async concurrency — commit `3910838`. Coverage map comment documents where each former TODO bullet is covered |
| Immediate fix queue #1 `GATEWAY_ENABLE_DOCS` warning | ✅ | closed (see C) |
| Immediate fix queue #2 tsconfig BOM | ✅ | closed (see C) |
| Immediate fix queue #3 title TODOs | ✅ | closed (see E) |
| Immediate fix queue #4 `channels/manager.py:119`, `memory/manager.py:216` notes | ✅ | audited: both are **accurate documentation strings, not bugs** — channel dedupe-TTL boundary (documented design note referencing upstream issue #4121) and the `supports_search` invariant error message. No code change; recorded as documented limitations |
| Immediate fix queue #5 P2 baseline triage | ⏳ | frontend/electron portions green; backend full-suite result pending |
| Debt scan (TODO/placeholder/`@ts-ignore`/`console.log`) | ✅ | 0 `@ts-ignore`, 0 frontend `console.log`; fresh repo-wide sweep: **58 TODO/FIXME/XXX/HACK hits, 0 genuine unfinished items** (all are kanban `TaskStatus.TODO`, `TodoMiddleware` names, LLM `{TODO: …}` prompt fill-ins, test data, `ADR-XXX` slug templates) |

## F. Blocked / user actions

| Item | Status | Action |
|---|---|---|
| Reboot verification | ⬜ | after reboot run: `powershell -ExecutionPolicy Bypass -File scripts\verify_reboot.ps1` |
| Tray icon visual check | ⬜ | confirm state-colored circle behind the `^` chevron (hover/click/menu) |
| Controlled gateway restart | ✅ | completed via the 07:44/08:15 watchdog escalations (gateway relaunched with current `.env`); live probes green: `docs_enabled: false`, `version: "2.1.0"` |

---

## Session fix log (root cause → test)

1. **Fresh installs shipped with Swagger docs enabled** — `.env.example` had `GATEWAY_ENABLE_DOCS=false` commented out and every installer copies `.env.example` → `.env`. Root cause: production-hostile template default. Fix: active `false` in template + local `.env`; evidence `prod_check --strict` 0/0. Commit `3999abd`.
2. **`frontend/tsconfig.json` carried a UTF-8 BOM** — flagged by JSON gate. Fix: stripped; evidence `tsc --noEmit` exit 0. Commit `84e5a1d`.
3. **Stale TODO “add tests” block** — three of four promised areas were already covered elsewhere; concurrency + real-runtime persistence were genuinely missing. Fix: added them (compiled `create_agent` + `InMemorySaver` rebound-graph read; 24-thread sync isolation; scrambled-delay async binding) — 71/71 green. Commit `3910838`.
4. **`/api/ops/version` always `"unknown"`** — `_resolve_gateway_version()` iterated a duplicated tuple of dist names that never exist; also the harness pyproject (the installed carrier of the release version) was missing from `bump_version.sh`/`verify_versions.sh`/`prod_check.py`, so any correct lookup would have drifted at the next release. Fix: lookup chain `agent-workspace-harness → alpha → agent-workspace`, harness added to all three gates, docs (`ops.py`, gateway `AGENTS.md`) updated, regression test pins the chain order. Verification ✅: 16/16 tests, `prod_check --strict` 0/0, `verify_versions.sh` exit 0 both modes, `bash -n` clean; live probe after the controlled restart.
5. **Three unwired/shadowed frontend routes** — found by the new `scripts/audit_frontend_wiring.py` (every frontend call site vs. the imported FastAPI route table):
   (a) **channel status panel permanently empty**: backend declared the list route as `@router.get("/")` (canonical `/api/channels/`) while the UI requests `/api/channels` → Starlette 307 → `apiFetch` sets `redirect: "error"` → fetch throws → `channelStatus()` swallowed it as `[]`. Root cause: lone slash-spelling among `""` siblings (swarms, subagents). Fix: register both spellings in `channels.py`.
   (b) **dead `/subagents/live` fallback**: only possible match was `GET /api/subagents/{name}` (a single-bot lookup), never a registry; primary `GET /api/subagents/control` (`subagent_control.py:224`) exists. Fix: removed the fallback; unreachable gateway honestly yields `[]`.
   (c) **`/api/swarms/{id}/${action}` false positive**: `${action}` is a closed TS union; all four values (`pause|resume|cancel|step`) have real routes (`swarms.py:94–125`). Fix: documented enum-exception in the audit that re-expands and re-verifies every value each run.
   Verification ✅: audit **216/216 exit 0**; backend **97/97** (`test_channels_router` + `test_auth_middleware` + `test_csrf_middleware` + `test_feature_manifest_wiring`); frontend `tsc` exit 0 + **51/51** tests; `prod_check --strict` 0/0.
6. **Seven mangled-rename duplicate expressions + wrong default-port doc in the desktop launcher** — same family as fix #4 (a rename collapsed `X || Y` pairs into `X || X`): `electron/main.js` set duplicate object keys (`AGENT_WORKSPACE_PROJECT_ROOT`/`CONFIG_PATH`/`HOME` in `spawnBackend`, `AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL` in both frontend spawns, `AGENT_WORKSPACE_AUTH_DISABLED` twice in `applyDesktopAuthMode`), read `process.env.AGENT_WORKSPACE_ENV` twice in `isExplicitProdEnv`, had a tautological `data.service === 'x' || data.service === 'x'` in `isAgentWorkspaceGateway`, and documented `--gateway-port` default as 8001 while the desktop actually owns **8201** (`desktop-config.json`, contradicting its own header); `frontend/next.config.mjs:9` also read the same env var twice. The duplicates were behavior-neutral (identical duplicate keys, last-wins) but are the exact pattern that caused bug #4, and the port doc was simply wrong. Fix: all seven deduped, doc corrected to 8201. Verification ✅: `node --check` on both files exit 0, electron **8/8**, adjacent-duplicate scan empty, assignment re-greps show singles, `next build` exit 0 (4/4 pages, First Load JS 197 kB) against the edited `next.config.mjs`.
7. **Docs promised env knobs/names that nothing reads** — found by the new `scripts/audit_env_wiring.py` (SET-vs-READ sweep of `AGENT_WORKSPACE_*`/`ALPHA_*` across launchers, `.env*`, compose, docs) plus manual adjudication of its SET-ONLY list:
   (a) **README headless/CI setup was dead**: `ALPHA_SETUP_PROVIDER`/`ALPHA_SETUP_API_KEY` (README:536) — the wizard reads `AGENT_WORKSPACE_SETUP_PROVIDER`/`AGENT_WORKSPACE_SETUP_API_KEY` (`scripts/wizard/noninteractive.py:95,152`) and nothing translates between them, so documented non-interactive setup silently ignored the provider and key. Fix: README corrected to the real names.
   (b) **`AGENT_WORKSPACE_DEV_BUNDLER` never had a reader** (`git log -S` empty across all paths): claimed by `docs/CONFIGURATION.md`, `AGENTS.md`, `docs/DEVELOPMENT.md`. Real mechanism: `pnpm dev --turbopack`. Fix: all three docs now state the flag.
   (c) **`AGENT_WORKSPACE_LOG_LEVEL` never had a reader**: real mechanism is `log_level:` in `config.yaml` (`AppConfig` field, `app_config.py:212`, applied by `apply_logging_level`, restart-required per `reload_boundary.py`). Fix: CONFIGURATION.md points at the real knob.
   (d) **`SKIP_FRONTEND_BUILD` works but is not an `.env` key** (bash never reads `.env`): consumed by `Makefile:159` and `serve.sh` flag `-skip-frontend-build`; Windows `start.ps1` already reuses an existing `.next` build (`start.ps1:539`). Fix: CONFIGURATION.md states the real invocation forms.
   Verification ✅: env audit **DEAD-SET 0 / SET-ONLY 0 exit 0** (after tightening: JS-only env-object sets, shell/make bare-`$` reads, `env.get(...)` reads); frontend wiring regression **216/216 exit 0**; `prod_check --strict` **0/0**. `AGENT_WORKSPACE_ENV` reviewed as label-only (SDK environment tag + monitor display), unset by design everywhere — recorded informational, no change.
8. **Swarm runs could never fail honestly + group members never retried** — deep swarm audit found a 6-defect cluster, each verified failing before the fix:
   (a) terminal-state overwrite: `mark_completed`/`mark_failed` let a late result rewrite an already-terminal task, and PENDING/QUEUED tasks whose dependencies could never run kept `finished` false forever → first-terminal-result-wins guards + `fail_unrunnable_tasks()` emitting `SWARM_STRANDED_TASKS_FAILED` (coordinator `step()` also reconciles the ready==0/running==0/finished==false deadlock before every dispatch);
   (b) runner exited on pause (killing the whole run) and only checked the watchdog once per wave → pause now parks the runner in a resume loop and the watchdog ticks every poll; `SPECULATIVE_BACKUP_LAUNCHED` emitted a success event **without launching anything**, and a backup failure demoted the original run → a real backup race now launches, and `SPECULATIVE_BACKUP_FAILED` never demotes the original;
   (c) aggregator reported `partial_success` even with **zero** completed tasks and router `/run-async` returned fake `started_async` on terminal plans → `failed` when 0 completed + ≥1 failed, three-way final event (`SWARM_COMPLETED`/`SWARM_FAILED`/`SWARM_PARTIAL`), HTTP 409 on terminal plans; duplicate `start_background_swarm` spawned a second run → module-level dedup registry;
   (d) groups runner retried **only infrastructure exceptions** — a FAILED/TIMED_OUT/empty-output member result (the commonest failure: the model call itself) was recorded first-hit despite `max_retries=2` → results now raise into the retry machinery with backoff; CANCELLED still never retries.
   Verification ✅: 4 new swarm regressions (stranded-dep terminal status, pause/resume liveness, terminal-state immutability, idempotent background start) + groups retry regression (attempt1=FAILED → attempt2=COMPLETED; asserts `retry_counts==1` + final output = retry's output) — batches **43/43** (swarm engine/advanced/company/enterprise/groups/kanban) + **31/31** (group chat/bots/attendance/discipline/mesh) + **7/7** (`test_group_runs`). Commits `dc1d977`, `8ac5850`.
9. **The self-learning fork was dead five ways (and its tests had never passed)** — `learning_fork_middleware._execute_fork`: (a) `from alpha.tools.builtins import add_memory, recall_memory` raised ImportError on every execution — neither tool exists anywhere in the repo (real names: `memory_add`/`memory_search` in `alpha.agents.memory.tools`); (b) the handler awaited the **sync** `manager.add(...)` passing `user_id`/`trace_id` positionally where the signature is keyword-only — TypeError on every memory write; (c) the read branch called `manager.recall(...)` — no such method (real API `asearch`/`search`); (d) `from alpha.skills.proposals import get_skill_proposal_store` — no such function (the store is built as `SkillProposalStore(proposals_root())`); (e) handlers resolved managers via **local** imports, bypassing the module-level names the integration tests patch. This suite lives in `packages/harness/tests`, which no gate runs — 5 tests sat red invisibly; they additionally patched `langgraph.config.get_config` instead of the middleware's own binding, let `AsyncMock` make the synchronous `bind_tools` return a coroutine (the fork died on `'coroutine' has no attribute 'ainvoke'`), and asserted *generic* warnings that matched unrelated isolated failures. Fix: real tool names through whitelist/prompt/import/handler, `await manager.aadd(..., user_id=, trace_id=)` / `await manager.asearch(...)`, module-level patchable accessors, tests repaired with specific warning assertions. Verification ✅: `test_learning_fork` **20/20** (was 5 failed). Commit `f04099a`.
10. **user_model observed five tool names that do not exist** — `handle_tool_call` tracked `{read_file, str_replace, write_file, add_memory, propose_skill}`; only `propose_skill` is a registered `@tool` here (full inventory collected), so file/skill/memory observations were **never captured** — the personalization provider learned from proposals alone. Fix: `OBSERVED_TOOL_NAMES` = real `@tool` names (`memory_add`, `memory_search`, `session_search`, `propose_skill`, `skill_manage`, `invoke_python_skill`, `code_mode`, `python_repl`, `present_files`); tests renamed to a tracked real name. Verification ✅: `test_user_model` green in the 5-file batch. Commit `f04099a`.
11. **Cold `import alpha.subagents.executor` circular-crashed** — `alpha.authz.tool_filter` imported `alpha.tools.mcp_metadata` at module level: executor → authz → tools → builtins → `task_tool`/`self_improvement_tool` → `alpha.subagents.__getattr__` → partially-initialized executor (the whole-suite mock in `tests/conftest.py:29-41` exists only to dodge this edge; a clean `python -c "import alpha.subagents.executor"` reproduced the crash). Single edge: the import's only user is `_filter_mcp_tools_by_server` → moved inside the function. Verification ✅: cold probes `alpha.subagents.executor` / `alpha.authz` / `alpha.tools` all exit 0 (executor-first crashed before); new `tests/test_cold_imports.py` pins 5 module cold-imports in fresh subprocesses. Commit `f04099a`.
