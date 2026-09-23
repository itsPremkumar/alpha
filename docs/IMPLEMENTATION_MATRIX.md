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
| `GET /api/ops/version` | 🟡 | **bug found + fixed**: resolver looked up a duplicated, non-existent dist tuple `("agent-workspace", "agent-workspace")` → always `"unknown"`. Fixed to `agent-workspace-harness → alpha → agent-workspace` with a chain-order regression test; **unit ✅ 16/16** (`test_ops_router.py` + docs-toggle); **deployment path ✅** — image runs `uv sync --locked` over the workspace that ships the harness dist, so metadata resolves in-container. **Live probe pending the controlled restart below** |
| `/docs` `/redoc` `/openapi.json` in production | 🟡 | template/env now default off; the **running** gateway started before the `.env` change (`docs_enabled: true` on live `/api/ops/status`) → enforce on next controlled restart |

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
| Controlled gateway restart | ⏳ | deferred until the full backend suite completes; one restart picks up **both** `docs_enabled=false` and the `/api/ops/version` fix — then re-probe both endpoints |

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
