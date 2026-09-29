# Alpha self-audit (R5): internal inventory of what exists, what is inert, and what is claimed

**Scope.** One repository, one worktree, one question: *what does this codebase actually
contain, judged only from code and running verification configuration — never from
documentation about itself?*

| | |
| --- | --- |
| Worktree | `C:\Users\PREM KUMAR\Videos\alpha-r5-selfaudit` |
| Branch / HEAD | `research/r5-selfaudit` @ `53457d9` ("docs: the deep improvement plan, and the pattern under the shipped bugs") |
| Date of measurement | 2026-09-29 |
| Deliverable | this file only — no product code, no tests, no config changed; `git status --porcelain` is empty apart from this document |
| Not run | `make dev`, `make dev-daemon`, `scripts/serve.sh`, `uvicorn`, `next dev`; no ports 2026/3000/8001 opened; no commits, no push, no stash, no worktree create/remove |
| Not consulted | external/web sources (four sibling agents own the external landscape) |

---

## 1. Method

### 1.1 What was read first (orientation, not evidence)

`AGENTS.md` (root), `backend/AGENTS.md`,
`backend/packages/harness/alpha/AGENTS.md`,
`backend/packages/harness/alpha/runtime/AGENTS.md` (the honesty boundary, lines 56–61),
`docs/architecture/durable-runtime.md` (including its own "Not yet implemented",
lines 419–455), `docs/PRODUCTION_READINESS_INVENTORY.md`, `.github/workflows/*`,
`scripts/ruff_scope.py`, `scripts/check_changed_python_lint.py`,
`backend/tests/test_no_orphan_modules.py`, `backend/tests/test_discovery_plane_parity.py`.

These were treated as *claims to be tested*, per the rule "never infer capability from a
doc". Where a doc and the code disagree, §6 records both and names the authority.

### 1.2 Three independent oracles, cross-checked

1. **The generated oracle** — `contracts/feature_manifest.json` and the collector that
   produces it (`backend/scripts/generate_feature_manifest.py`). Re-running its
   `collect_*` functions against the live tree reproduces every published number.
2. **Static code measurement** — file/AST/text scans over the tree, written independently
   of the manifest so agreement is meaningful (§2.2).
3. **Verification configuration** — `.github/workflows/*.yml`, `Makefile`, test globs,
   and the gates' own assertions, because "exists" and "is verified" are different
   questions.

### 1.3 Tooling (environment caveats that shaped the method)

- The `rg`/`grep`/`glob` tools are **broken in this environment** (the Chocolatey `rg`
  shim is missing). All search was done with PowerShell `Select-String` and purpose-built
  Python scripts. This is a *search recall* risk, mitigated by writing whole-tree Python
  scans (byte-exact, `encoding="utf-8"`) rather than trusting a single tool.
- Python came from `uv run --no-project python` (CPython 3.14.7); `--with pyyaml` for
  YAML; `--with "ruff==0.14.11"` for formatting (matches the `ruff>=0.14.11` floor in
  `backend/pyproject.toml`).
- Scratch measurement scripts live **outside** the worktree, in
  `%TEMP%\opencode\`: `r5_inventory.py`, `r5_markers.py` / `r5_markers.json`,
  `r5_show_nie.py`, `r5_show_rest.py`, `r5_config_keys.py`, `r5_config_keys2.py`,
  `r5_orphans.py`, `r5_orphans2.py`, `r5_orphans3.py`, `r5_orphanbucket.py`, `r5_fe_dead.py`,
  `r5_pkgscan.py`, `r5_testrefs.py`, `r5_format.txt`, `r5_format_01512.txt`, `r5_caps.py`,
  `r5_cmdrows.py`, `r5_fmtbreak.py`, `r5_fmtscope.py`, `r5_fmtver.py`, `r5_nie.py`.
  Every number in §2 and §3 can be reproduced from them.

### 1.4 The five states used for every judgement

| State | Meaning |
| --- | --- |
| **implemented + tested** | code path exists *and* a test exercises it (test named with file:line) |
| **implemented + untested** | code path exists, no test found |
| **declared-but-not-implemented** | a name, key, route, or catalog row is declared; the code behind it does not exist |
| **documented-but-not-implemented** | a document asserts it; the code does not exist |
| **documented-as-limitation** | the absence is disclosed on purpose, by the code's own docs — a design boundary, not a bug |

"No test found" is weaker evidence than "no code found": a test could hide behind
dynamic generation. Where the difference matters, §10 says so.

---

## 2. Measured inventory

### 2.1 The generated oracle — reproduced with zero drift

`contracts/feature_manifest.json` (`generated_at 2026-09-29T01:00:16Z`):

| Claim | Manifest | Independent check | Drift |
| --- | --- | --- | --- |
| Built-in tools | 134 | `BUILTIN_TOOLS` length = 134 | 0 |
| Routers | 61 | 61 files define an `APIRouter`; `app.include_router` = 64 call sites over 61 router imports in `app.py` | 0 |
| Middlewares | 42 | 42 `*_middleware.py` files | 0 |
| Supervisor loops | 8 | 8 loop ids: `enterprise_heartbeat`, `free_models_sync`, `perpetual`, `review_queue`, `self_update`, `sentinel`, `skill_curator`, `swarm_status` | 0 |
| Engines | 102 | 102 engine directories; set-diff vs manifest = ∅ | 0 |

Command: `uv run --no-project python backend/scripts/generate_feature_manifest.py` (collector
functions only, dry — no write) compared against the committed JSON.

**This is a genuine positive**: the headline counts published in `README.md` and
`llms.txt` are generated, and they were not stale at HEAD.

### 2.2 Independent code measurements

| Quantity | Value | Command / basis |
| --- | --- | --- |
| `@tool`-decorated functions | 203 in 141 files | AST scan; the manifest's 134 counts only `BUILTIN_TOOLS`, so the 69-function difference is **declared elsewhere, not drift** |
| Route decorators under `backend/app/` | 603 | `Select-String -Pattern '@(get\|post\|put\|patch\|delete)\('` over `backend/app/**/*.py` |
| Alembic migrations | 28 files / 28 unique revision ids | `persistence/migrations/versions/`; chain head `0027_side_effect_ledger.py` follows `0026_network_waits.py` |
| Slash-command catalog rows | 428 rows, **426 unique** | parsed `get_default_catalog_entries()` (`commands/catalog.py:6`) |
| Handler-backed catalog rows | **16** | pinned by `backend/tests/test_discovery_plane_parity.py:43-67` |
| Capability catalog entries | 40, every entry import-verified | `capabilities/catalog.py:38`; asserted by `test_discovery_plane_parity.py:241-244` |
| Config surface | 47 top-level sections, 1,057 key occurrences, 473 unique key names | `config.example.yaml` parse |
| Lint scope | 3,330 files, 1 excluded with a written reason | `scripts/ruff_scope.py:26-90` (`EXCLUDED_FILES`) |

### 2.3 Test inventory

| Surface | Files | Test functions | Wired into CI? |
| --- | --- | --- | --- |
| `backend/tests/` | 1,273 `.py` | 21,156 `def test_` (regex) | yes — `.github/workflows/backend-unit-tests.yml`: collect-only job + 4 shards, `pytest -m "not live" --ignore=tests/blocking_io tests/`, postgres + redis services |
| `tests/blocking_io/` | (included in tree) | — | yes, separate path-filtered workflow |
| `frontend/src/lib/*.test.mjs` | 54 | — | yes — `pnpm test` |
| `frontend/tests/` | 2 (`branding.test.mjs`, `reasoning-effort.test.mjs`) | — | yes — `test:branding`, `test:extra` |
| `electron/tests/` | 4 | — | yes — `windows-installer.yml` |
| **`tests/skills/` (root)** | **4 + 1 helper** | — | **NO runner anywhere** — zero hits for `tests/skills` across `.github/workflows/*`, `Makefile`, `scripts/*` |

21 files in `backend/tests/` contain no `def test_`; all 21 are helpers/conftest, which is
normal.

### 2.4 Size

| Language | Files | Lines |
| --- | --- | --- |
| Python | 3,247 | 982,632 — **product 504,820 / tests 477,659 (≈0.95:1)** |
| TS/TSX | 133 | 45,727 (0 of them tests — frontend tests are `.mjs` by design) |
| Markdown | 345 | 174,692 — `references/` 114,470; `docs/` 21,088; `backend/` 19,281; `skills/` 8,223; root 6,799 |

Largest files — tests: `test_channels.py` 11,018; `test_e2b_sandbox_provider.py` 5,416;
`test_subagent_executor.py` 5,140. Product: `runtime/runs/worker.py` 3,339;
`app/channels/manager.py` 2,903; community e2b provider 2,892;
`integrations/lark_cli.py` 2,846; `sandbox/tools.py` 2,756; `workflow/runtime.py` 2,642;
`app/gateway/routers/thread_runs.py` 2,539; `runtime/runs/manager.py` 2,521.
Frontend: `ChatView.tsx` 2,293; `WorkforceSection.tsx` 1,915; `WorkflowsSection.tsx` 1,644.

### 2.5 Format debt (measured three ways — the number depends on the ruff version)

```
# pyproject floor (backend/pyproject.toml:84, ruff>=0.14.11):
uv run --no-project --with "ruff==0.14.11" ruff format --check .
→ 566 would reformat / 2,762 already formatted      (3,328 discovered)

# the exact lint scope from scripts/ruff_scope.py (3,330 files), same ruff:
→ 566 would reformat / 2,764 already formatted

# the version locked for CI and dev envs (backend/uv.lock → ruff 0.15.12,
# what `uv sync --group dev` in lint-check.yml:121,175 installs):
uv run --no-project --with "ruff==0.15.12" ruff format --check .
→ 507 would reformat / 2,827 already formatted      (3,334 discovered)
```

Buckets under 0.14.11: `backend/packages/harness` **338**, `backend/tests` **198**,
`backend/app` **14**, `backend/scripts` + root probes **6**, root `scripts/` **9**,
`installer/tests` **1**. Under 0.15.12: harness **282**, tests **196**, app **14** —
and the 0.15.12 set is a **strict subset** of the 0.14.11 set (59 files differ, all
0.14.11-only), i.e. newer ruff formats strictly more of this tree correctly.

**The CI-relevant backlog is 507 (harness 282)**, because the gate and the debt report
both run the locked ruff. Raw outputs: `%TEMP%\opencode\r5_format.txt` (0.14.11) and
`r5_format_01512.txt` (0.15.12); bucket script `r5_fmtver.py`, scope script
`r5_fmtscope.py`.

Notably, `scripts/generate_docs_index.py` — the tool behind the docs-index gate — is in
the unformatted set under **both** versions.

---

## 3. Not-yet-implemented inventory

### 3.1 Marker census (whole tree, 3,598 files scanned)

| Marker | Hits | Verdict after reading the hits |
| --- | --- | --- |
| `NotImplementedError` | 181 mentions / **82 actual `raise` statements** | split measured by `r5_nie.py`: 50 raises sit in files containing `@abstractmethod` (designed capability tiers: `runtime/runs/store/base.py`, `app/gateway/auth/repositories/base.py`, `auth/providers.py`, …); the other 32 are 16 test doubles, 1 docstring mention (`agents/memory/tools.py:14`), 1 template (`.agent/skills/…/anchor.template.py:32`), and **14 product sites, every one an honest tier refusal** — `computer_use/dispatcher.py:105-123` (7 capability tiers), `community/warm_pool_lifecycle.py:33,37`, `alpha/client.py:1788` (caught and converted to a JSON error), `models/fallback.py:517`, `persistence/json_compat.py:233` (unsupported dialect). **No unfinished-feature stub found among them** |
| `stub` | 439 | mostly `script_bridge`/`stubgen` machinery and `noop_manager` — implementation artifacts, not debt |
| `no-op`/`noop` | 385 | mostly deliberate null implementations |
| `placeholder` | 362 | UI placeholders and docstrings |
| `not yet` | 56 | mixed — the durable-runtime list is authoritative and accurate (§3.2) |
| `temporarily` | 48 | mostly comments about ordering |
| `TODO` | **52** | **all** fixtures/templates/domain enum `TaskStatus.TODO` — no real comment debt |
| `not implemented` | 22 | 15 product-side hits + 7 test assertions; all inspected ones are honest refusal strings, most with a pinning test (§3.2 #1–#3) |
| `not wired`/`not yet wired` | 12 | see §3.2 |
| `FIXME` | 2 | both in fixtures |
| `XXX` | 1 | an ADR filename |
| `HACK` | 0 | — |
| `for now` | 2 | wizard UI copy |

**Positive:** conventional comment-debt markers are effectively zero. The repository does
not hide unfinished work in `TODO`s; it hides it in *declared names* (§4).

### 3.2 Substantive entries

| # | Marker / claim | Location | Judgement |
| --- | --- | --- | --- |
| 1 | Runtime verifiers for self-repair | `alpha/selfrepair/engine.py:4` (docstring), `:72` ("verification not implemented for repair kind …"), `:89` ("repair action and verification not implemented …") | **documented-as-limitation** — refusal is the feature; asserted by `backend/tests/test_workforce_intelligence.py:103,114,126` |
| 2 | Worker hot-replace | `alpha/supervision/recovery.py:136` | **implemented + tested** as a *bounded* refusal (`backend/tests/test_supervision_watchdog.py:116` pins the message) — the missing capability (hot replacement) itself is **declared-but-not-implemented** |
| 3 | `/compact` via slash handler | `alpha/commands/backend_handlers.py:764` | **documented-as-limitation** — points at `POST /api/threads/{thread_id}/compact`; asserted in `backend/tests/test_backend_handlers.py:255` |
| 4 | `runtime/supervisor/` not wired into the Windows launcher | `docs/architecture/durable-runtime.md:424-427`; `runtime/AGENTS.md:58` | **documented-as-limitation, still true** — `alpha.runtime.supervisor` is imported only by tests (`r5_pkgscan.py`) |
| 5 | No per-tool-call reconciliation API or UI | `durable-runtime.md:428-430` | **documented-as-limitation, still true** — no `SideEffect*` reference exists anywhere under `backend/app/` |
| 6 | No `replay(session_id)` over the thread run-event log | `durable-runtime.md:431-435` | **documented-as-limitation, still true** |
| 7 | Parked-session registry has no per-thread resume launcher | `durable-runtime.md:436-444` | **documented-as-limitation, still true** |
| 8 | `memory` DB backend gets no parked-session registry | `durable-runtime.md:445-447` | **documented-as-limitation** |
| 9 | `AWAITING` subagent reconciliation has no dedicated ledger | `durable-runtime.md:448-450` | **documented-as-limitation** |
| 10 | No filesystem snapshots for undo/redo | `durable-runtime.md:451-455` | **documented-as-limitation** |
| 11 | Docker dev entrypoint comment "not implemented" | `docker/dev-entrypoint.sh` (1 hit) | **implemented + tested** modulo the comment — comment is stale wording, not a gap |
| 12 | Playwright e2e suite | `.github/workflows/e2e-tests.yml:3-7` | **documented-but-not-implemented, honestly banner-documented** — no `@playwright/*` dependency, no `playwright.config.ts`, no `*.spec.ts`; workflow is `if: false` |

**Reading:** the durable-runtime "Not yet implemented" list (§3.2 #4–#10) is *accurate
today* — I re-checked each item against the tree. That list is the best-quality
self-disclosure in the repository.

---

## 4. Declared-but-inert

This is the audit's central finding class: **names that answer for themselves but have
nothing behind them.**

### 4.1 Config keys: three with no production reader, seven whose subsystem is never invoked

Method: parse every key path declared under `alpha/config/**`, then search every
`.py`/`.ts`/`.tsx`/`.mjs` file for a reader, classifying readers as *declaration*,
*own-package*, *other production*, or *test*. A first pass that counted "readers outside
`alpha/config/`" produced a 10-key list; follow-up reads (below) split it into two very
different findings, which is why they are not reported as one number.

**Group A — no production reader exists at all (3 of the 10 entries):**

| Key | Declared at | Read at | State |
| --- | --- | --- | --- |
| `evolution_evidence.*` (whole section) | `alpha/config/app_config.py:358` | tests only: `backend/tests/test_appconfig_autonomy_promotion.py:120` | declared-but-not-implemented |
| `verification.judge_enabled` | `alpha/config/verification_config.py:19` | tests only: `test_verification_config.py:14,23`, `test_config_version.py:207` | declared-but-not-implemented |
| `verification.judge_model_name` | `alpha/config/verification_config.py:23` | tests only: `test_verification_config.py:15`, `test_config_version.py:208` | declared-but-not-implemented |

(The `/judge` catalog row, §4.4, is the third leg of the same triangle: row, key, and
description all describe a judge the product never runs.)

**Group B — read only by a subsystem that production code never invokes (7 of the 10):**

| Key(s) | Declared at | Read at | The catch |
| --- | --- | --- | --- |
| `self_tuning.bounds_source` `:147`, `canary_window_seconds` `:129`, `max_step_ratio` `:135`, `relative_tolerance` `:87`, `required_metrics` `:74`, `verification_policy` `:155` | `alpha/config/self_tuning/config.py` | yes — real logic reads them: `self_tuning/dynamics.py:95`, `self_tuning/canary.py:107`, `self_tuning/dynamics.py:122`, `self_tuning/verify.py:97,109` | the protocol package's only non-test importer is `alpha/config/app_config.py:51` — the schema type. `Select-String 'self_tuning\|SelfTuning'` over `backend/app/**` returns **zero** hits; `from alpha.config.self_tuning` appears only in `app_config.py:51` and `backend/tests/test_config_self_tuning.py:21,61` |
| `memory.fabric.*` (`enabled: false` at `config.example.yaml:3390`) | `alpha/config/memory_config.py:237` | `fabric_enabled()`/`FabricConfig` have readers inside `alpha/memory/fabric/*` | outside that package, the only production import is `memory_config.py:31` (the config model). `fabric.store`/`lifecycle`/`forget` are imported only by `backend/tests/test_memory_fabric.py:13-47` and `test_store_format_gate.py:320-321` |

**The operational consequence:** flipping `self_tuning.enabled: true` or
`memory.fabric.enabled: true` in `config.example.yaml` starts nothing — the enable flag
itself has no consumer on the runtime path. Both subsystems are **implemented + tested +
reachable only from tests**, and both ship `enabled: false` (self-tuning at
`config.example.yaml:4482`).

**Fixed since the known bug:** `model_pricing` *is* consumed —
`app/gateway/routers/console.py:222` (declared `app_config.py:338`). The historical
price-config read failure no longer reproduces.

**False positives excluded from the table** (so the finding is not over-counted):
`bash_output_max_chars` and siblings looked unread in a naive scan but are read in
`alpha/sandbox/tools.py:2102,2126,2193,2474`; and `self_tuning.*` keys themselves would
have been over-counted as "unread" had the reader search stopped at the package boundary,
which is the correction applied above.

### 4.2 The side-effect ledger: implemented, tested, and wired to nothing

This is the largest inert subsystem in the repository.

| Layer | Evidence |
| --- | --- |
| Runtime API | `alpha/runtime/side_effects/` (incl. `InMemorySideEffectLedger`, `SideEffectReclaimer`) |
| SQL repository | `alpha/persistence/side_effects/sql.py:74` — `class SqlSideEffectLedger` |
| Migration | `persistence/migrations/versions/0027_side_effect_ledger.py` (schema head) |
| Tests | `backend/tests/test_side_effect_ledger_sql.py` (+ ledger unit tests) |
| Docs | `docs/architecture/durable-runtime.md:252` "The side-effect ledger in SQL" |
| **Production wiring** | **none** — zero `SideEffect*` identifiers under `backend/app/`; `InMemorySideEffectLedger`/`SideEffectReclaimer` referenced only by their own module and tests |

A search for `_begin_side_effect` finds only `alpha/tools/os_computer_tool.py:114`, an
unrelated sentinel/`reason` parameter — not the ledger.

State: **implemented + tested, not wired.** The durability layer therefore *records
nothing in production*, because no production effect ever enters it. The honest
"unknown + reconciliation" design (`durable-runtime.md:428-430`) is accurate about the
missing API/UI but the deeper gap is upstream: nothing calls `begin/complete/fail`.

### 4.3 `alpha.runtime.supervisor` and 26 test-only packages

A package-level import scan (`r5_pkgscan.py`) of all 238 packages found: **0 packages
with zero references**, but **26 imported only by the test tree**:

```
agents.memory.backends.{fullmemory,honcho,mem0,noop,openviking}
community.{boxlite,browserless,e2b_sandbox,lightrag,opensandbox,ragflow,searxng,serper,tencent_wsa,tenki}
context.condenser · deepagent · rules · runtime.supervisor · security.shell_ast
security.vault · skills.triggers · streamjson · tools.repair · tui · wire_contracts
```

Judgement, with the two that matter called out:

- `alpha.runtime.supervisor` — **implemented + tested, not wired** (matches the
  disclosed limitation, `durable-runtime.md:424`).
- `alpha.tui` — not dead: it is a CLI entry point (`python -m alpha.tui`), invisible to an
  import scan. Excluded from the finding.
- The 11 `alpha.community.*` packages are **conditionally loaded** by `use:` entries in
  `config.example.yaml` — they are *declared*, and become live only when an operator
  enables them. State: implemented + tested, gated off by default (legitimate).
- The remaining ~13 (`agents.memory.backends.*`, `rules`, `security.*`, `skills.triggers`,
  `streamjson`, `tools.repair`, `wire_contracts`, `context.condenser`, `deepagent`) have
  no production importer and no config switch found: **implemented + tested, unreachable
  from a running Gateway** unless something imports them dynamically at a site my scan
  could not see (§10 flags this as the audit's main recall risk).

**Known blind spot of this scan (found by it, then corrected for):** a package counts as
production-referenced if *any* production module imports *any* submodule — including a
pure config model. `alpha.memory.fabric` passes the scan on the strength of
`alpha/config/memory_config.py:31` importing `fabric.config`, while its runtime modules
(`store`, `lifecycle`, `forget`) are test-only (§4.1 Group B). The 26 is therefore a
floor, not the full population.

### 4.4 Slash-command catalog: 410 of 426 rows have no handler

| Fact | Evidence |
| --- | --- |
| 428 rows / 426 unique | parsed `commands/catalog.py:6` |
| 16 handler-backed | `backend/tests/test_discovery_plane_parity.py:43-67` (`IMPLEMENTED_COMMANDS`) |
| **410 placeholder rows** | pinned at `test_discovery_plane_parity.py:67` (`PLACEHOLDER_ROW_COUNT = 410`) |
| Placeholder behaviour | `alpha/commands/registry.py:361-373` returns `status="success"` with `Directive /x accepted [category]. …` |
| Nuance | 4 of the 410 (`/plan deep`, `/run autonomous`, `/swarm`, `/evolve`) are autonomous triggers: `registry.py:357-359` emits an `autonomous_directives` string consumed by `commands/autonomous_engine.py:197` → model execution. The other 406 are pure no-ops. (Measured by `r5_cmdrows.py`.) |
| Two pinned catalog defects | 21 handler bindings unpublished in the catalog (`test_discovery_plane_parity.py:77-101`); 2 duplicate rows `/learn`, `/usage` silently discarded (`:103-106`) |

State: **declared-but-not-implemented — and honestly disclosed by a test that refuses to
let it grow.** The `/judge` row (`commands/catalog.py:434`, "Runs independent judge over
competing answers") is one of them: there is no `/judge` handler anywhere in
`alpha/commands/`, and `verification.judge_enabled` (§4.1) is read by nothing. The
catalog, the config key, and the config description all describe a judge that the product
does not run.

### 4.5 Frontend: a file that exists only for its test

- `frontend/src/lib/workspace-view.ts` is **never imported by product code** — its only
  reader is `frontend/src/lib/war-room.test.mjs:59`, which reads it *as text*.
- `frontend/src/components/NavTabs.tsx:34` declares its own duplicate `WorkspaceView`
  type instead of importing the shared one.
- The test asserts the same strings appear in both files — so it verifies two independent
  copies agree, rather than enforcing a single source of truth.

State: **declared-but-not-implemented as a shared module** (the duplicated type in
`NavTabs.tsx` is what actually ships).

### 4.6 `write_safe_reasoning_payload` — a tested function with no caller

`alpha/reasoning/summary.py:432`, exported in `__all__`, has **zero production callers**.
`backend/tests/test_reasoning_models_config.py:221` calls it with `None` and only asserts
path resolution — it proves nothing about any path a product call would take.

State: **implemented + weakly tested + unreachable** (see §8, vacuous-test candidate).

### 4.7 `alpha.evolution.evidence`: a whole subsystem imported only by its own config

Production importers of `alpha.evolution.evidence.*` outside the package itself: exactly
one — `alpha/config/app_config.py:72` (the config *model*). `evaluate_proposal`,
`run_required_gates`, and `service.py` are reached only from
`backend/tests/test_evolution_evidence.py`. The `evolution_evidence` settings (§4.1) are
the knobs for a gate pipeline that no runtime path turns.

State: **implemented + tested, not wired** (the package self-imports via
`evolution/evidence/__init__.py:106-151`, which is why a naive scan reports it as
referenced).

---

## 5. Dead / unreachable code

### 5.1 Orphan-module scan (`r5_orphans.py` → `r5_orphans2.py` → `r5_orphans3.py`)

Exact relative-import resolution over 1,629 leaf modules:

| Bucket | Count | Notes |
| --- | --- | --- |
| Resolved to a real production import | 1,518 | |
| Referenced only as text/dynamic | 58 | overwhelmingly legitimate: `alpha.community.*.tools` loaded via `use:` in `config.example.yaml`, `app.channels.*`, `alpha.models.*` providers, `capabilities/catalog.py:40,46,76,82,100` |
| Referenced only by tests | 2 | `app.gateway.langgraph_auth`, `app.gateway.langgraph_studio` — **actually referenced by file path in `backend/langgraph.json`**; a scan artifact, not dead |
| **Genuinely dead, yet passing the gate** | **2** | see §5.2 |

Reconciliation (so the table is auditable): 1,629 enumerated leaves − 32
`persistence.migrations` modules (the gate also skips these) − 14 `GATE_ALLOW` entries −
3 CLI `__main__` entries = **1,580 classified** = 1,518 + 58 + 2 + 2.

**Universe caveat:** the enumeration skips any path containing a directory named
`sandbox`, which excludes 26 product files under `alpha/sandbox/` from this scan (they
were not audited for orphan-hood; `alpha/sandbox/tools.py` is demonstrably wired, so no
finding is believed lost). `__init__.py` is also excluded as a *module* — by design, to
match the gate.

### 5.2 Two dead modules the orphan gate accepts

| Module | Why it is dead | Why the gate misses it |
| --- | --- | --- |
| `alpha.memory.narrative.gates` | re-exports from `.config`; no importer anywhere | `backend/tests/test_no_orphan_modules.py:219-244` `_is_referenced` channel 1 matches on **bare basename suffix**: some unrelated relative import resolves to a module ending in `gates` |
| `app.subagent_batches.service` | a 173-byte compat shim whose own package `__init__.py` imports `alpha.subagents.batch_service` instead | same bare-basename collision via `service` |

Verified empirically by importing the gate's own functions and feeding it the two
modules: `_is_referenced` returns `True`.

Gate blind spots found (all demonstrated, none theoretical):

1. **Bare-basename suffix channel** accepts a match from *any* module whose final name
   segment equals the target's (`test_no_orphan_modules.py:219-244`).
2. **Text/comment evidence counts as a reference** — a mention in a docstring satisfies
   the scan.
3. **`__init__.py` files are never checked as modules** — only leaves are scanned, so a
   dead `__init__` export is invisible.
4. **`SCAN_CODE_BASES` excludes `backend/scripts/`, root `scripts/`, and `docker/`** —
   code that only those entry points use would be mis-flagged, and code living there is
   never audited for orphan-hood.

The gate is still a net positive: it passes with 0 unexplained modules, it fails closed
on real orphans (`test_no_orphan_modules.py:295`), and `ALLOWED_ORPHANS` requires a
written reason per entry (`:26`, asserted at `:298`). The finding is that *its two
happiest paths are the two that let §5.2 through*.

### 5.3 Other dead-ish surfaces (already classified in §4)

`workspace-view.ts` (§4.5), `write_safe_reasoning_payload` (§4.6), 26 test-only packages
(§4.3), and **root `tests/skills/` — 4 test files with no runner in any workflow, the
`Makefile`, or `scripts/`** (§2.3), i.e. tests that are written but can never fail a build.

---

## 6. Documented-vs-actual divergences

### 6.1 The runtime honesty boundary is half stale

`backend/packages/harness/alpha/runtime/AGENTS.md:56-61` states four boundaries. Two are
true, two are now **out of date in the repo's own favour-of-humility direction** — i.e.
the code is *better* than the boundary says:

| # | Claim (`runtime/AGENTS.md`) | Actual | Verdict |
| --- | --- | --- | --- |
| 1 | "there is no cross-process exactly-once here" (:57) | design boundary; single-process JSON/JSONL state throughout | **true — documented-as-limitation** |
| 2 | "the side-effect ledger has no SQL repository yet" (:58) | `persistence/side_effects/sql.py:74` `SqlSideEffectLedger`, migration `0027_side_effect_ledger.py`, `backend/tests/test_side_effect_ledger_sql.py`; documented at `durable-runtime.md:252` | **STALE** — the real gap is §4.2 (no production wiring), which the sentence does not convey |
| 3 | "the supervisor is not yet wired into the Windows launcher" (:58) | `start.ps1` owns startup; `alpha.runtime.supervisor` test-only | **true** |
| 4 | "a session's parked-across-restart state is re-derived rather than kept in a dedicated durable registry" (:59-60) | `runtime/network/wait_registry.py` + `alpha.persistence.network_waits.NetworkWaitRepository`, imported `app/gateway/deps.py:35`, built in lifespan (`app.state.network_waits`, `deps.py:655`/`:671`), migration `0026_network_waits.py`, tests `test_network_wait_registry.py` / `test_network_wiring.py` | **STALE** — a dedicated durable registry exists |

The consequence is not cosmetic: an agent reading `runtime/AGENTS.md` would under-credit
two implemented subsystems *and* would still miss the actual gap (§4.2), because
`durable-runtime.md` and `runtime/AGENTS.md` now disagree with each other. The repo's
documented rule is that guides own their depth — here two guides own one fact and say
different things.

### 6.2 Numeric claims vs measurement

| Documented | Measured | Delta |
| --- | --- | --- |
| `IMPROVEMENT_PLAN.md:132-138`: "562 files would be reformatted, 2768 files already formatted"; "334 of those are under `backend/packages/harness/alpha/`" | **neither figure reproduces at HEAD**: ruff 0.14.11 → 566 / 2,764 (scope), harness 338; ruff 0.15.12 (locked) → 507 / 2,827, harness 282 | **unreproducible** — the plan's number sits between the two versions' results and matches no tested invocation; format debt is ruff-version-sensitive (59-file spread), so a backlog number without a pinned version is not a checkable claim |
| `PLACEHOLDER_ROW_COUNT = 410`, 426 unique rows (`test_discovery_plane_parity.py:64-67`) | 410, 426 | **0 — pinned correctly** |
| 134 tools / 61 routers / 42 middlewares / 8 loops / 102 engines | identical | **0** |
| manifest `generated_at 2026-09-29T01:00:16Z` | regenerated match | **0** |

### 6.3 Dangling test references in documentation (19 references, ~13 unique targets)

Markdown under the repo references `tests/*.py` paths that do not exist:

| Document | Missing targets |
| --- | --- |
| `AGENT_TRANSFER_GUIDE.md` | `test_learning_fork`, `test_user_model` |
| `docs/ALPHA_UNIFIED_INTEGRATION_PLAN.md` | `test_module_reference_scan` |
| `docs/DEVELOPMENT.md` | `test_api_threads`, `test_api_thread_branching`, `test_my_skill`, `test_perf` |
| `docs/EXTENSIONS.md` | `test_extension` |
| `docs/INSTALLER.md` | `test_installer_contract` — **exists, but at `installer/tests/`, not `backend/tests/`** (wrong path, not a missing test) |
| `backend/docs/rfc-grep-glob-tools.md` | `test_app`, `test_config` |

No gate checks that a documented test path resolves — this class of rot has no owner.

### 6.4 Honest disclosures that hold up (positives)

- `.github/workflows/e2e-tests.yml:3-7` says the Playwright suite is not in the
  repository and disables itself with `if: false` — verified: no `@playwright/*`
  dependency, no `playwright.config.ts`, no `*.spec.ts`.
- **No `continue-on-error` anywhere** in `.github/workflows/` (comments explicitly
  forbid it).
- `test_discovery_plane_parity.py` refuses to let unclassified catalog rows or duplicate
  rows grow — debt pinned *and* labelled "known defect, not endorsement" (`:73`, `:103`).
- `docs/PRODUCTION_READINESS_INVENTORY.md` row "Autonomous side effects | partial"
  matches §4.2's measurement.
- `docs/architecture/durable-runtime.md` pairs its "Not yet implemented" (`:419`) with
  "What is deliberately not claimed" (`:320`) and even publishes
  "Two real bugs this found" (`:396`) — disclosure that includes its own defects found.
- `docs/MEMORY_FABRIC_PLAN.md:33` states auto-tuning is *deferred* on principle
  ("silent self-tuning of an honesty-critical system is the wrong default") — consistent
  with §4.1 Group B: the code exists, ships off, and the deferral is on the record.
- `model_pricing` read path works (§4.1), i.e. a previously shipped bug stayed fixed.

---

## 7. Structural risk

### 7.1 Import cycles are load-bearing

- `backend/tests/conftest.py:41` installs a `sys.modules["alpha.subagents.executor"]`
  mock (block begins ~line 22) to break a documented circular chain:
  `alpha.subagents → executor → thread_state → agents → lead_agent →
  subagent_limit_middleware → executor`.
- ~20 cycle-avoidance comments exist in product code; **1,394 function-body imports**
  were counted — the "import inside the function to avoid a cycle" pattern is pervasive.
- `alpha/config/network_resilience_config.py` imports `alpha.runtime` only inside
  function bodies for this reason (documented in `runtime/network/AGENTS.md`).
- 311 `lru_cache` / `cached_property` / `_instance` / module-global markers —
  process-wide singletons are the default wiring style.

**Risk:** the cycle structure is invisible to the type checker and to any test that does
not go through `conftest.py`. A refactor that merely moves an import can break *only the
test suite* (because the suite runs with a mock that production does not have) or *only
production* (because production imports eagerly). The mock is a divergence between the
test process and the Gateway process by construction.

### 7.2 State and shape

- **Test:product LOC ≈ 0.95:1** — unusually high, and a large share of it is
  *characterisation* of very large modules (§2.4). Modules over ~2,500 lines
  (`channels/manager.py` 2,903, `sandbox/tools.py` 2,756, `thread_runs.py` 2,539) have
  tests whose size tracks their subject's complexity rather than its contract.
- **Single-process state**: the honesty boundary (§6.1 #1) is real — all local state is
  JSON/JSONL under one process. Every durability feature (ledger, wait registry) is
  therefore only as true as its wiring (§4.2).
- **Two documentation authorities disagree** on the same subsystem (§6.1), which is a
  structural documentation risk, not a one-off typo: `runtime/AGENTS.md` vs
  `docs/architecture/durable-runtime.md`.
- **The orphan gate's four blind spots** (§5.2) mean the "no dead code" property is
  *approximately* true and provably not exactly true.
- **410/426 catalog rows are placeholders** (§4.4): the command *discovery plane*
  overstates the product by an order of magnitude, mitigated by an honest test but still
  user-visible as `status="success"`.

---

## 8. Verification quality

### 8.1 What is actually gated

| Gate | Workflow / file | Character |
| --- | --- | --- |
| Backend unit tests | `backend-unit-tests.yml` | collect-only job + 4 shards `make test-shard SPLITS=4 GROUP=n`, `pytest -m "not live" --ignore=tests/blocking_io tests/`, postgres + redis services |
| Blocking IO | separate, path-filtered workflow | honest isolation |
| Python lint | `lint-check.yml:123-141` → `scripts/check_changed_python_lint.py` | **incremental**: only changed files; runs **both** `ruff check` and `ruff format --check` (`check_changed_python_lint.py:515`) |
| Format/complexity debt | `ruff-debt-report` (`lint-check.yml:143+`) | **non-gating**, disclosed as "pre-existing Ruff debt backlog" (`:91`) |
| Agent guidance | `agent-guidance` (gating) + `agent-guidance-debt-report` (non-gating) | two-tier by design |
| Frontend | `lint-frontend`: typecheck + build | **no formatter** — root `AGENTS.md` states there is no `format` script and no Prettier dependency |
| Docs | `docs-index`, `duplicate-expressions` | fails closed on unclassified Markdown |
| Path-filtered | generated-drift gate, cold-start, replay-e2e, skill-review, sandbox-smoke | only when relevant paths change |
| Nightly | `nightly.yaml` | scheduled |
| Windows | `windows-installer.yml` | 4 electron shell tests + installer tests |
| E2E | `e2e-tests.yml` | **`if: false`** — suite absent, honestly documented (§3.2 #12) |

**Positives worth stating plainly:** every declared frontend test suite runs somewhere
(54 + 2 + 4 files); no `continue-on-error`; generated counts are gated against drift;
the command/capability discovery plane is gated in *both* directions
(`test_discovery_plane_parity.py:1-26`); `test_check_no_silent_failures.py` exists and
asserts raised-and-caught structure, not swallowed exceptions.

### 8.2 What is not gated

1. **Full-repository format is never a gate** — only changed files are checked
   (`lint-check.yml:123-141`), so the backlog — 507 files under the locked ruff, 566
   under the pyproject floor (§2.5) — can only shrink by touching each file.
2. **Root `tests/skills/` has no runner at all** (§2.3) — those tests cannot fail CI.
3. **No doc-link/reference gate** — 19 dangling `tests/*.py` references survive (§6.3).
4. **No "a declared key must have a reader" gate** — Group A's 3 keys pass every check
   because schema-only tests *are* their readers, and Group B's 7 pass because their own
   unwired subsystem reads them (§4.1).
5. **No frontend e2e** — UI regressions are covered only by unit tests of `.mjs` helpers;
   no component renders in CI.
6. **Vacuous-test candidate:** `backend/tests/test_reasoning_models_config.py:221` calls
   `write_safe_reasoning_payload(None)` and asserts path resolution for a function with no
   production caller (§4.6) — green coverage over an unreachable path.

### 8.3 Net assessment

Backend verification is genuinely strong in breadth (21,156 test functions, sharded with
services, fail-closed gates) and honest in its disclosures. Its two weaknesses are
*precision* (gates that can be satisfied by declaring things — schema tests, catalog
placeholders, orphan-gate suffix matching) and *reach* (no e2e, no doc-reference gate, a
test directory with no runner).

---

## 9. Top-10 ranked recommendations

Ranked by (user-visible blast radius) × (how much the repo already believes it is safe).

1. **Wire the side-effect ledger into production effects** — nothing under `backend/app/`
   ever calls `begin/complete/fail`, so the `UNKNOWN` + reconciliation design
   (`durable-runtime.md:428-430`) is unreachable and every remote side effect is still
   unrecorded (§4.2). Start with the two highest-consequence effect classes (process
   spawn, external HTTP) and expose `list_unknown()` as an ops route.
2. **Reconcile `runtime/AGENTS.md:56-61` with reality** — items 2 and 4 are stale and now
   *contradict* `durable-runtime.md:252` and `:194-266` (§6.1). Rewrite the boundary to
   name the true gap ("ledger has SQL storage but no production writer") in both guides,
   same change set.
3. **Make inert declarations a gate** — two tests: (a) every key declared in
   `alpha/config/**` has ≥1 reader outside its own declaring package *or* an explicit
   `INERT_KEYS` allowlist with written reasons — catches Group A §4.1 (`judge_enabled`,
   `evolution_evidence`); (b) every `*.enabled: true` flag in `config.example.yaml` maps
   to a production entry point that reads it — catches Group B (flipping
   `self_tuning.enabled` / `memory.fabric.enabled` currently starts nothing).
4. **Classify the 410 placeholder command rows** — either give the high-intent rows
   (`/judge`, `/swarm`, `/evolve`, `/run autonomous`) handlers or publish them as
   "model-interpreted" in the catalog, and stop returning `status="success"` from
   `registry.py:361-373` for rows that did nothing.
5. **Fix the two demonstrable orphan-gate holes** — require an *import* edge (not a
   bare-basename suffix, not text) in `_is_referenced`
   (`test_no_orphan_modules.py:219-244`), then delete or wire
   `alpha.memory.narrative.gates` and `app.subagent_batches.service` (§5.2).
6. **Turn on some e2e** — even one Playwright path (login → send → stream → stop) would
   convert `e2e-tests.yml:3-7` from an honest banner into a gate (§8.2 #5).
7. **Run root `tests/skills/` somewhere** — add a workflow/`make` target or delete the
   directory; tests with no runner are Worse Than No Tests (§2.3, §5.3).
8. **Gate documentation references** — a script that resolves every `tests/*.py`
   reference in Markdown would clear the 19 dangling paths in §6.3 and keep them clear;
   note `docs/INSTALLER.md` needs a path fix, not a new test.
9. **Shrink the format backlog deliberately and pin the ruler** — 507 files under the
   locked ruff 0.15.12 (566 under the pyproject floor 0.14.11; §2.5), ~196 of them
   tests: run `ruff format` per package with review using the *locked* version, record
   the version next to any backlog number (the 562 claim in `IMPROVEMENT_PLAN.md:132`
   reproduces under neither version, §6.2), then make full-repo format a gate so the
   number cannot regrow; include `scripts/generate_docs_index.py` itself.
10. **Give the frontend one source of truth for `WorkspaceView`** — import
    `workspace-view.ts` from `NavTabs.tsx:34` (or delete the file) and change
    `war-room.test.mjs:59` from text-comparison to an import assertion (§4.5).

---

## 10. Confidence and gaps

### 10.1 High confidence (two independent methods agree)

- All §2.1 counts: manifest regeneration + independent structural scans agree exactly.
- §2.3 test/CI mapping, §2.5 format counts (measured under both ruff versions), §4.4
  command-row counts: direct measurement, reproducible from the scratch scripts listed
  in §1.3.
- §4.2 (ledger unwired), §4.3 (supervisor unwired), §6.1 (stale honesty items): confirmed
  by *both* absence-of-reference scans and presence-of-implementation reads.
- §3.2 #4–#10: each durable-runtime "not yet implemented" bullet was checked
  individually against the tree.

### 10.2 Medium confidence

- **Config-key readership (§4.1):** the search is textual (`.py/.ts/.tsx/.mjs` for the
  literal key name), so a reader via a computed key (`cfg[section][name]`, `getattr` with
  a computed string) would be missed. Group A's 3 entries were each inspected by hand to
  make that unlikely; Group B's verdict rests on a second, independent signal — *import*
  statements (`from alpha.config.self_tuning`, `fabric.store|lifecycle|forget`) — which is
  a stronger shape of evidence than name matching alone, but still textual.
- **The ~13 "no production importer" packages (§4.3):** a dynamic
  `importlib.import_module(name_from_config)` site would evade the scan. The community
  packages are already explained by `use:` config; the others were not.
- **"21,156 test functions":** a `def test_` regex, so a nested or generated test could
  be double/under counted. Order of magnitude and file counts are safe.

### 10.3 Low confidence / explicitly unverified

- **No test suite was executed.** Nothing here claims the tests pass at HEAD; only that
  they exist and are wired (§2.3, §8.1). The sharded backend suite, the frontend suites,
  and the format gate were read, not run.
- **No runtime behaviour** was observed — no Gateway, no ports, by instruction.
- **Marker census recall** (§3.1): single-tree text search with a broken `rg`; a marker
  written in an unusual phrasing (not `TODO`/`FIXME`/`not implemented`/…) would be
  missed. `HACK = 0` and `XXX = 1` are plausible but rest on that same recall limit.
- **Dead-code negatives**: "no importer found" is the weakest state in §1.4 and is the
  entire basis for §4.3/§4.6; the two §5.2 positives are strong precisely because they
  were reproduced by running the gate's own logic.

### 10.4 Known consequences of this deliverable

- `docs/SELF_AUDIT.md` is a new file under `docs/`, and the docs index fails closed on
  unclassified Markdown: `scripts/generate_docs_index.py` needs a `FILE_OVERRIDES` entry
  (`:148`) and `docs/INDEX.md` needs regeneration. That is a *second* file change and was
  deliberately not made under the one-file constraint — the `docs-index` gate will flag
  this document until an owner adds the entry. Flagged rather than worked around.
- No findings were manufactured to fill sections: §5.2's two dead modules are the complete
  list the method could substantiate, and §3.1 records that comment-debt markers are
  essentially absent.
