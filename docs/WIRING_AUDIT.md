# Wiring audit: features that exist but are never called

**Agent:** wiring audit. **Branch:** `agent/wiring`. **Worktree:** `alpha-wiring`.
**Date:** 2026-09-29. **Base:** `origin/main` @ `7772995`.

The brief listed eleven capabilities as implemented-but-never-called. This report
records what was independently verified, what was actually wired, what was
deliberately not wired, and — the part that matters most — **what is still inert
after this work**.

> **Read Part 7 if you only read one thing.** "Still inert" is the accurate
> remaining-work list, and it is ten items long — longer than the list of things
> that got wired. Everything above it is scaffolding.

**Summary in one line:** of eleven "implemented but never called" claims, **four
were genuinely wireable and are now wired** (items 3, 5, 6, 7), **four are
correctly inert and were deliberately left alone** (1, 2, 4, 8, with reasons and
the exact steps an owner needs), and **four of the brief's premises were wrong or
imprecise** — which is the most useful thing this audit produced, because a false
premise costs more than an unwired feature.

**Two things to know before merging:**

1. `scripts/generate_docs_index.py` **exits 2** because of this report. It needs
   one `FILE_OVERRIDES` entry that this change is not permitted to make. The exact
   snippet is in Part 5.
2. The side-effect ledger is **still unwired, on purpose**, and the reason is the
   interesting result here. See §2.7.

## Method, and one environment caveat that changes how to re-run this

`rg` is broken in this environment: its chocolatey install cannot find `rg.exe`,
so it returns **nothing** rather than failing loudly. The harness's own `grep`
and `glob` tools are backed by the same binary and return
`Invalid ripgrep JSON output`. **Two prior investigations in this project were
dispatched on that false negative.**

Every count in this report was produced by `scan.mjs`, a ~60-line
explicit-UTF-8 recursive scanner written for this task, and cross-checked
against `Select-String`. **A zero from `rg` in this environment is not evidence
of anything.** If you re-run a claim check, use
`node scan.mjs <dir> --mode count <regex>` (add `--ext .ts,.tsx` for the
frontend) or `Select-String`.

Two untracked helper files are left at the worktree root for that reason and are
**not** part of the change:

- `scan.mjs` — the scanner. Reads and writes nothing; the `--mode` flag chooses
  between a per-file count and line output.
- `mutate_for_bite.mjs` — removes the three production calls this change adds,
  so the guards in `tests/test_wired_durable_runtime_surfaces.py` can be
  demonstrated to fail. See §4.1. It uses explicit-UTF-8 `readFileSync`/
  `writeFileSync`, never a shell text pipeline.

Counts are stated as *production* (non-test) references unless marked otherwise.
Two verification passes were run in parallel against disjoint claims; findings
from both are reported, and where a sub-agent's claim was load-bearing I
re-verified it myself before using it (which is how the item 4 error in §1.4 was
caught and corrected).

---

## Part 1 — Verification of the eleven claims

### Summary table

| # | Capability | Brief's claim | **Verified** | Verdict |
|---|---|---|---|---|
| 1 | `SideEffectLedger` | zero refs under `backend/app/` | **Confirmed**, and broader than claimed | **Not wired** — §2.7 |
| 2 | `ProcessSupervisor` | no production caller | **Confirmed** | **Correctly not wireable** — §2.7 |
| 3 | `alpha.evolution.evidence` | only importer is its own config field | **Confirmed** | **Wired** — §2.6 |
| 4 | `write_safe_reasoning_payload` | zero callers **and its test proves nothing** | Callers: **confirmed**. Test claim: **FALSE** | Not wired — §2.7 |
| 5 | `/supervision/heartdog` | fleet is `{}` forever | **Effect confirmed, mechanism FALSE** — no such route exists | Partially wired, §2.5 |
| 6 | `alpha/runtime/network/` | nothing observes the four states | **Confirmed** | **Wired** — §2.4 |
| 7 | `shutdown.is_clean` | logger call and nothing else | **Confirmed** | **Wired** — §2.4 |
| 8 | `self_tuning.*` / `memory.fabric.*` | read by nothing live | **Confirmed, but misdiagnosed** — they are *constructed* by nobody, not *read* by nobody | Not wired — §2.7 |
| 9 | `verification.judge_*` | read by nothing | **Confirmed, and already a recorded repo position** | Not wired — §2.7 |
| 10 | `narrative.gates`, `subagent_batches.service` | both dead | One dead + wrong mechanism; one is a **false premise** | Reported — §1.10 |
| 11 | `workspace-view.ts` | duplicate type in `NavTabs.tsx` | **Confirmed, and they have already drifted** | Not mine — §3.2 |

**Four of the eleven premises were wrong or imprecise as written.** Those are
called out individually because a false premise costs more than an unwired
feature: it sends the next person to fix the wrong thing.

---

### 1.1 SideEffectLedger — CONFIRMED inert, and wider than the brief says

**Counts.** `side_effects|SideEffectLedger|SideEffectReclaimer` over 3236
backend `.py` files → 141 matches in 22 files. Every non-test match is inside
the implementation itself, the migration, the ORM table registration, or one
unrelated local variable in `alpha/commands/backend_handlers.py:934` (a display
string, not a call). **`backend/app/**` → 0 matches**, as claimed.

So: no `begin()` before a tool call, no `mark_in_flight()`, no
`SideEffectReclaimer` loop, no `list_unknown()`, no `reconcile()`. The table
created by `0027_side_effect_ledger` is never written to. Confirmed.

**The repository already knows, and has a guard that will bite the moment
anyone wires it.** `backend/tests/test_documented_claims.py` is not a prose
check — it reads a machine-readable `<!-- honesty-claims -->` block in
`runtime/AGENTS.md` and compares each declared value against a predicate over
the code, in both directions. Today it declares:

```
side_effect_ledger_sql_repository: exists
side_effect_ledger_production_writer: absent
parked_session_durable_registry: exists
parked_session_resume_launcher: absent
cross_process_exactly_once: absent
supervisor_in_windows_launcher: absent
```

`PRODUCTION_LEDGER_WRITER_ALLOWLIST` is **empty on purpose** (the comment says a
broad allowlist "would recreate the exact failure this file exists to prevent").
This is the highest-integrity guard in the repository and it shaped the decision
in §2.1.

**The brief's second claim is also confirmed, and it is a real defect.** See
§3.1 for the `sql.py:161` unguarded read-modify-write.

### 1.2 ProcessSupervisor — CONFIRMED inert, and correctly so

`ProcessSupervisor|runtime\.supervisor` → 61 matches in 6 files: its own two
modules, and three test files. No production caller. Confirmed.

`supervisor.py:441` is `subprocess.Popen(...)` of an operator-supplied argv.
This class supervises a **child OS process**. See §2.2 for why wiring it inside
the Gateway is the wrong move rather than a missed one.

### 1.3 `alpha.evolution.evidence` — CONFIRMED inert (and its own docstring admits it)

`evolution_evidence|evolution\.evidence` → 115 matches in 14 files. The only
**production** importer is the config model itself: `config/app_config.py:72`
(`from alpha.evolution.evidence.config import EvolutionEvidenceConfig`) and the
field at `app_config.py:358-366`. The 51 exported names — `evaluate_proposal`,
`decide_evidence_verdict`, `run_required_gates`, `check_evaluator_integrity` — are
imported by **no** production module. The package's own docstring
(`evidence/__init__.py:11-13`) says so: *"is not wired into any promotion path
yet"*. `service.py:3-4` names the intended owner: *"`evaluate_proposal` is what the
central owner wires into `alpha/evolution` promotion"*. It is absent from
`alpha/capabilities/catalog.py` and from `test_no_orphan_modules.py`'s
allowlists. **Confirmed — and this one was wireable. See §2.6.**

### 1.4 `write_safe_reasoning_payload` — the brief's second claim is FALSE

Production references: **0**. Only `reasoning/summary.py:432` (the definition)
and two test files. It is also deliberately *not* re-exported from the
`alpha.reasoning` package root, so nothing reaches it by wildcard either.

**But "its own test passes `None`, so the test proves nothing" is a misreading.**
`write_safe_reasoning_payload(path, payload, *, config=None)` — `None` is the
first parameter, **not** the payload:

```python
# summary.py:452
destination = Path(path) if path is not None else resolved_config.storage_path
```

`tests/test_reasoning_models_config.py:221` is
`assert write_safe_reasoning_payload(None, state, config=config) == storage.resolve()`
followed by `assert storage.is_file()`. That is the **most valuable** of the two
call shapes: it exercises the `storage_path` fallback, asserts the resolved path
is the *configured* one, and asserts the file really exists. The other test
(`test_reasoning_uncertainty_summary.py:259-280`) covers the explicit-path branch
and asserts a rejected payload leaves neither the destination nor its parent
behind. Both branches of `path` and both outcomes are covered.

**The real defect is different in kind.** `reasoning/config.py:3-4` states the
plane *"deliberately does not import `alpha.config` or register an `AppConfig`
field"*, so `storage_path` defaults to `None` in every deployment and the function
would raise `UnsafePersistencePayload` even if called. **Not wired — this plane
has no configuration surface at all.** See §2.7.

### 1.5 `/supervision/heartdog` — the route does not exist

**The brief's literal claim is false.** A whole-repo scan for `heartdog`
(`.py,.ts,.tsx,.mjs,.md,.json`, 3878 files) returns **0 matches**. There is no
`GET /supervision/heartdog`, no `heartdog` symbol, and no such route in
`contracts/feature_manifest.json`.

The *effect* the brief describes is real, but the mechanism is different, and
the difference matters because it changes the fix:

- The real routes are `POST /api/supervision/heartbeat` (ingest) and
  `GET /api/supervision/fleet` (read), in
  `backend/app/gateway/routers/supervision.py`.
- A whole-repo scan for `supervision/heartbeat|supervision/fleet` finds **no
  caller of the POST**. So `_GLOBAL_WATCHDOG._heartbeats` is empty, and
  `evaluate_fleet()` returns `{}` forever. The conclusion the brief reached is
  correct; the route name is not.
- **The brief's "three different `record_heartbeat` functions" is really four.**
  `bots/health.py:44`, `subagents/lifecycle.py:367`, `routers/bots.py:505`
  (an HTTP handler), and `alpha/supervision/watchdog.py:36` (the watchdog's own).
- **The brief's "`supervision_tool.py` holds its own separate `_GLOBAL_WATCHDOG`
  from the router's" is CONFIRMED and is the more serious finding.**
  `alpha/tools/builtins/supervision_tool.py:17` and
  `routers/supervision.py:21` each construct their own
  `DeterministicWatchdog(freeze_threshold_beats=3)`. The model-facing tool and
  the REST route therefore report **two different, independently empty fleets**.
  A "fleet" that is per-module rather than per-process cannot be made correct by
  wiring a producer later, which is why this is reported as a structural defect
  rather than a missing call.

**The frontend is already honest about the empty fleet** and needed no change:
`frontend/src/lib/supervision.ts` → `watchdogDetail([])` returns
`"no workers reporting — no heartbeat received, nothing is being watched"`, and
`frontend/src/lib/runtime-visibility.test.mjs:357-363` pins that the probe does
*not* say "watching". A previous agent fixed the false all-clear on the client
side. The server side still under-discloses; see §2.3.

### 1.6 `alpha/runtime/network/` — CONFIRMED inert

`state\.network_monitor|state\.network_waits` over the whole repo (3523 files) →
**14 matches, and every production one is `deps.py` itself** (lines 654, 655,
659, 671, 738, 743 — all assignment or reset, never a read). The only other
matches are two test files, a frontend test *comment* that describes the same
finding, and this report. Confirmed: the four-state model exists, the poll loop
runs, and nothing observes it.

`NetworkWaitService.status()` — whose docstring literally says "for an ops
endpoint" (`wait_registry.py:183`) — has **no production caller**. That is the
seam used in §2.4.

### 1.7 `shutdown.is_clean` — CONFIRMED inert

`deps.py:780-784` is the only consumer: `drain_report = await drain.shutdown()`
then `if not drain_report.is_clean: logger.warning(...)`. An eight-phase drain
reports "clean" or "incomplete" into a logfile, and the process then exits. No
operator surface, and nothing survives the restart that would need it. Confirmed.

### 1.8 `self_tuning.*` and `memory.fabric.*` — CONFIRMED, but the defect is *construction*, not *reads*

`self_tuning` → 32 matches in 12 files. The six keys are `enabled`,
`canary_window_seconds`, `max_step_ratio`, `cooldown_seconds`, `bounds_source`,
`protected_paths` (plus a nested `verification_policy`), declared at
`config/self_tuning/config.py:125-158` and on `AppConfig` at
`app_config.py:367-375`. `memory.fabric` has nine keys at
`memory/fabric/config.py:20-63`.

**"Read by nothing" is the wrong diagnosis, and the difference changes the
work.** Both are read — by their own config schemas, and by
`self_tuning/targets.py:228-241`, which introspects `MemoryConfig` and will emit
`memory.fabric.enabled` (self-tuning reading fabric, which is inert only
transitively). The actual gap is that **nothing constructs either subsystem**:

- `SelfConfigurationProtocol.__init__` (`config/self_tuning/protocol.py:44-68`)
  requires **eight injected collaborators** (`registry`, `validator`, `canary`,
  `applier`, `health_check`, `rollback_manager`, `ledger`, `clock`). There is no
  zero-arg constructor or factory anywhere, so `protocol.py:99-100`
  (`if not self.config.enabled`) is unreachable in production: flipping
  `enabled: true` starts nothing.
- `memory.fabric`'s only production importer is `memory_config.py:31`
  (`from alpha.memory.fabric.config import FabricConfig`) — **config only, never
  `store` / `lifecycle` / `forget`**. It is also absent from `SURFACE_ORDER` in
  `recall_composition.py:68-74`, so it has no recall seam either.
- Neither is in `alpha/capabilities/catalog.py`, and
  `AutonomySupervisor.register_default_loops()` (`autonomy/supervisor.py:98-109`)
  registers exactly eight loops — `sentinel`, `perpetual`, `review_queue`,
  `skill_curator`, `enterprise_heartbeat`, `swarm_status`, `free_models_sync`,
  `self_update` — with **no self-tuning loop and no fabric loop**.

So these are two *complete, coherent, fail-closed subsystems* each missing
exactly one composition root. **Not wired — see §2.7 for why, and for the exact
one-line fix each needs.**

### 1.9 `verification.judge_enabled` / `judge_model_name` — CONFIRMED inert, and already a recorded repository position

Declared at `config/verification_config.py:19-26`, shipped in
`config.example.yaml:2646-2647` **and** in the Helm chart's embedded config
block at `deploy/helm/alpha/values.yaml:307-308`. Production readers: **0**.

**The repository already knows and has recorded it.**
`backend/tests/test_deploy_surface_parity.py:705-709` carries a `known_unread`
allowlist whose stated purpose is *"to stop the list growing"*, and it names
both keys with the reason:
`"verification.judge_enabled": "a specced selective judge that was never wired;
alpha.subagents.jev_acceptance is the implementation that took its place"`. The
test that enforces it (`:672-726`) walks every `AppConfig` leaf and fails if a
shipped key has no reader outside its declaration module. **Leaving the keys
while removing the allowlist entries is a build failure.**

What actually gates acceptance today is `subagents/jev_acceptance.py:98-101`,
which reads `system_one.enabled` and `system_one.enable_acceptance` — not
`verification.judge_*`.

**Not wired — see §2.7.** Wiring it as a gate would silently switch off a live
feature for any operator who already set `judge_enabled: true` and currently
gets judging with zero effect from that key.

### 1.10 `narrative.gates` and `subagent_batches.service` — one dead shim, one false premise

**`alpha.memory.narrative.gates` — CONFIRMED dead, but it is not what the brief
says.** `gates.py` is 11 lines total and **defines nothing**: it is a pure alias
shim re-exporting `NarrativeConfig` and `narrative_enabled` from
`narrative/config.py`. Every sibling imports `.config` directly
(`memory.py:14`, `recall.py:8`, `store.py:35`, `synthesis.py:21`,
`timeline.py:8`), and the package `_EXPORTS` maps both names to `"config"`, not
`"gates"`.

**The brief's mechanism is wrong.** `test_no_orphan_modules.py:219-244` is
`_is_referenced`, the *reference-resolution heuristic* — not an allowlist. The
allowlist is `ALLOWED_ORPHANS` at `:26-77`, and `alpha.memory.narrative.gates`
is **not in it**. It passes because of the heuristic's three evidence channels:
channel 1 tests every dotted suffix of the module path (`:234-236`) and
channel 2 accepts a bare basename when any ancestor package is referenced
(`:240-243`). `"gates"` is a near-universally reused basename in this tree, so
that channel is very permissive — and the gap is **structural, applying to every
module under `alpha/` and `app/`**, not specific to this file. The number of
modules that pass *only* this way was not computed; see §3.3.

**`app.subagent_batches.service` — the brief's "dead" is only true of a 5-line
shim.** `backend/app/subagent_batches/service.py` is a compatibility re-export,
and it is genuinely dead: the only production import of the package is
`app/gateway/app.py:578` (`from app.subagent_batches import SubagentBatchService`)
— the `__init__`, which bypasses `service.py` entirely.

**But the *capability* is very much alive.** The class is reached by a real
path: `routers/subagent_batches.py:89` → `deps.get_subagent_batch_service`
(`deps.py:869`) → `app.state.subagent_batch_service`, constructed and started at
`app.py:584-593` behind a fail-closed `subagent_batches.enabled` gate
(`app.py:408-409`) and stopped at `app.py:760-777`. So "dead" is the right word
for the module and the wrong word for the feature. **Reported, not touched** —
`app/subagent_batches/**` is not in this change's owned files.

### 1.11 `frontend/src/lib/workspace-view.ts` — CONFIRMED duplicate, and it has already drifted

`workspace-view.ts` defines and exports `WORKSPACE_VIEW_IDS`,
`WorkspaceView`, `isWorkspaceView`, `workspaceViewFromSearch`,
`workspaceViewUrl` (7 self-references). `NavTabs.tsx:34` declares its **own**
`export type WorkspaceView = ...`, and `ChatView.tsx:11` imports the type from
`@/components/NavTabs` — **not** from `@/lib/workspace-view`. The only consumer
of `workspace-view.ts` is `frontend/src/lib/war-room.test.mjs:59,63`, which
reads it with `readFileSync(..., "utf8")` and checks the text.

So the brief is right: the registry exists once, is used once (by a test reading
it as text), and the shipped component tree uses the duplicate. `frontend/**`
is owned by another agent — **reported, not touched.** See §3.2.

**The duplicate has already drifted, which makes it a live bug rather than
tidiness.** `workspace-view.ts:1-28` lists **26** ids; `NavTabs.tsx:34-61`
declares **27**. The extra is `"run-inspector"` — present in the type
(`NavTabs.tsx:43`) and in `WORKSPACE_TABS` (`NavTabs.tsx:93`), rendered by
`ChatView.tsx:1867-1870`, and **absent from `WORKSPACE_VIEW_IDS`**. So
`isWorkspaceView("run-inspector")` returns `false` for a view the UI navigates to
happily. `frontend/src/lib/workspace-view.ts` has **zero TypeScript importers**;
the only reference anywhere is `war-room.test.mjs:59`, which
`readFileSync`s it and asserts `source.includes('"deliberation"')` in all three
files. That test is substring-only: it cannot detect the drift above, because
`"deliberation"` is present in *both* definitions. The substring technique is
justified for `war-room.ts` (it imports `@/lib/http`, which plain Node cannot
resolve) but **not** for `workspace-view.ts`, which is dependency-free pure
TypeScript and could be imported directly.

---

## Part 2 — What was actually wired

| # | Capability | Call site wired | How a production path reaches it | Test that proves it | Bite proof |
|---|---|---|---|---|---|
| 6 | Network four-state model + parked-session registry | new `GET /api/ops/runtime` (`routers/ops.py`) reading `app.state.network_monitor` / `app.state.network_waits` | Gateway lifespan installs both at `deps.py:659`/`:671`; any authenticated ops read now observes them | `test_the_route_reports_a_live_measurement_end_to_end`, `test_wiring_the_route_reads_app_state_not_a_module_global` | with the call removed the record is absent and the route's guard fires (same test, steps 1–3) |
| 7 | `ShutdownReport.is_clean` | `deps.py`, immediately after `drain.shutdown()` → `record_drain_report` → `ALPHA_HOME/runtime/last_drain.json`, read by the same new route | the drain runs on every Gateway teardown; the next boot reads the record | `test_an_unclean_drain_is_recorded_as_unclean_with_the_failing_phase`, `test_the_route_reports_an_unclean_previous_drain` | the same bite test, steps 2–3 |
| 5 | Empty supervision fleet | `routers/supervision.py` `/fleet` and new `/observation` | any supervisor read; the fleet is now self-describing | `test_an_empty_fleet_does_not_read_as_a_healthy_fleet`, `test_a_reported_worker_makes_the_fleet_observed` | `test_bite_a_hard_coded_observed_flag_breaks_the_empty_fleet_guard` executes a mutated **copy of the real module** and shows the guard reject it |
| 3 | `alpha.evolution.evidence` | `alpha/evolution/promotion_route.py::route_evolution_gate` | `alpha/rsi/promotion.py:167` imports this function **at module scope**, so every RSI promotion decision goes through it | `test_the_evidence_gate_actually_runs_and_blocks_when_enabled`, `test_wiring_the_evidence_gate_is_called_from_the_production_promotion_seam` | `test_bite_neutralising_the_evidence_gate_call_restores_the_promotion`: with the call neutered, the engine's `True` is returned |

Diffstat: **4 files changed, 399 insertions, 6 deletions**, plus two new files
(`backend/app/gateway/ops_runtime.py`,
`backend/tests/test_wired_durable_runtime_surfaces.py` — 32 tests) and this
report. No test file was modified.

### 2.4 Items 6 + 7 — one seam, two inert facts

`NetworkWaitService.status()` carries the docstring *"What the service is doing,
for an ops endpoint"* (`runtime/network/wait_registry.py:183`) and had no caller.
`is_clean` was consumed by a `logger.warning` at `deps.py:781` and nothing else.
Both are now reachable from `GET /api/ops/runtime`, the natural home: it is
already the authenticated operator surface (same default auth as `/api/features`)
and it is inside this change's owned files.

The design rule is **absence is never a value**, enforced with an explicit
`reported` flag plus a machine-readable `reason` on both blocks:

| situation | `reported` | `reason` | the trap avoided |
|---|---|---|---|
| first boot, no drain record | `false` | `no_drain_has_been_recorded_yet` | reading as "the last shutdown was clean" |
| record present but corrupt | `false` | `the recorded drain could not be read` | reading as *no shutdown happened* |
| record from an unknown version | `false` | same | half-trusting a future shape |
| `network.enabled: false` | `false` | `network monitoring is disabled by configuration` | **reading as "connectivity is fine"** |
| enabled but monitor absent | `false` | `the network monitor is not running in this process` | a config claim masquerading as a network claim |
| monitor live, probe broke | `true`, `state: "unknown"` | — | **rounding `UNKNOWN` to `OFFLINE`** |

`UNKNOWN` is genuinely reachable: a probe that raises, or returns no results,
produces it — and per `runtime/network/AGENTS.md` rule 2 it **still permits a
network attempt**, so it parks nothing.
`test_unknown_is_reported_as_unknown_not_offline` pins that it is never rounded.

**What makes the drain record worth having** is that it survives the process.
`is_clean` only ever exists during teardown; before this change the answer to
"did the last shutdown finish?" was unrecoverable outside a logfile, and the next
boot is exactly when somebody asks. The file is written `mkstemp` + `fsync` +
`os.replace`, so a crash mid-write leaves the *previous* record rather than a
truncated file that would read as "unreadable" and hide the one prior incomplete
drain.

**The fail-OPEN requirement is honoured and stated in code.** The brief raised it
for the ledger; the same rule is load-bearing here, because the caller is a
teardown path. `record_drain_report` returns `{"recorded": False, "reason": ...}`
and never raises: turning "I could not record that the shutdown was incomplete"
into "the shutdown did not finish" is strictly worse.
`test_a_failed_record_write_returns_instead_of_raising` uses a real unwritable
location (a *file* where a directory must go). `deps.py` logs the failure rather
than swallowing it.

**Durability boundary, stated rather than glossed:** `last_drain.json` is a
single-process, atomic-replace file under `ALPHA_HOME`. It is restart-recoverable
for one Gateway. Two Gateway instances sharing an `ALPHA_HOME` would overwrite
each other's record, so it is an operator hint about *this* installation, not a
coordination primitive, and no cross-process exactly-once claim is made.

### 2.5 Item 5 — an unobserved watchdog, stated as such

`GET /api/supervision/fleet` keeps its flat `worker_id -> status` map so existing
consumers keep working, and gains four sibling keys: `observed`, `watching`,
`observed_worker_count`, `observed_reason`. They are **siblings, not members of
the map**, because the per-worker values are consumed as a map and a reserved key
inside it would read as a worker whose id happened to be `observed`.
`GET /api/supervision/observation` answers the same yes/no question on its own.

The frontend needed no change and got none: `watchdogDetail([])` already returns
`"no workers reporting — no heartbeat received, nothing is being watched"`,
pinned by `runtime-visibility.test.mjs:357-363`. A previous agent fixed the false
all-clear on the client; this fixes the server's under-disclosure.

**A structural gap is disclosed, not papered over.**
`alpha/tools/builtins/supervision_tool.py:17` constructs its **own**
`DeterministicWatchdog`, so the model-facing tool and this REST route report two
independent, independently-empty fleets, and a heartbeat ingested here is
invisible to the tool. The single-instance fix belongs in `alpha/supervision/`
(both callers import it from there), which this change does not own. Making this
router delegate to the tool's instance would invert the dependency (app → a model
tool module) and leave the tool's lifetime wrong, so the duplication is recorded
in the module docstring and pinned by a test rather than half-fixed.

### 2.6 Item 3 — the evidence gate, fail-closed and default-off

`alpha.evolution.evidence` was the one item on the list that was both genuinely
inert **and** wireable inside this change's owned files. It is wired at
`route_evolution_gate`, which `alpha/rsi/promotion.py:167` imports at module
scope — so it sits on the real promotion path, not a side channel.

Verified by count after the change: `evolution_evidence|evolution\.evidence`
over `backend/packages` now resolves in **12** files, of which exactly one sits
outside the package's own ten modules and its config model —
`promotion_route.py`, with 6 matches. Before the change that list had 11 files
and no such caller at all.

Four properties, each pinned:

1. **Default-OFF and behaviour-preserving.** `EvolutionEvidenceConfig.enabled`
   defaults to `False` and `AppConfig` never sets it, so
   `test_the_evidence_gate_is_off_by_default_and_changes_nothing` proves the gate
   does not run and the engine's tuple is untouched. **No existing install sees
   any behaviour change.**
2. **It can only block.** Nothing in the wiring turns a `False` into a `True`.
   `test_an_accepted_evidence_verdict_cannot_promote_what_the_engine_refused`
   drives a *stubbed accepted* verdict against an engine that refuses, and
   asserts the refusal survives verbatim.
3. **Enabling it fails CLOSED, with the real reason.** With the flag on and no
   measurement source injected, `evaluate_proposal` returns
   `insufficient_evidence` (the `reproducibility` gate reports `unavailable`
   without a probe). That blocks promotion. **This is the honest state and it is a
   consequence of the flag, not of the wiring:** the flag means *"promotion
   requires measured evidence"*, and **no deployment can currently supply
   measurements through config alone**. The reason string names the specific gate
   that could not be evaluated.
4. **A gate that cannot run blocks; an unreadable config does not.** An exception
   inside `evaluate_proposal` becomes `status="gate_error"` with the real
   exception. A config that raises on attribute access is a *third* state —
   neither the operator's "no" nor a verdict — reported as
   `ran=False, blocking=False`, because a config the process cannot read is not
   evidence about the proposal.

**A real bug this wiring surfaced, found by a test and fixed in production
code:** a baseline carrying no `declared_intent` made `Proposal` raise, and my
first version turned that into a `gate_error` block — so enabling the gate would
have blocked *every* promotion with a confusing
`proposal.declared_intent must be a non-empty string`. The fix states the absence
rather than inventing a claim
(`f"no declared intent was supplied for promotion candidate {candidate_id}"`),
because an empty string is not a disclosure and a fabricated intent is exactly
what this gate exists to distrust.

This is **not** a second lifecycle owner: it gates a *change promotion*, never a
run, and touches no run state. `RunManager` remains the sole run lifecycle owner
and `SafeRunRecoveryService` the only safe-continuation authority.

### 2.7 What was deliberately NOT wired, and why

**Item 1 — `SideEffectLedger`. Not wired, for two independent reasons, either of
which is sufficient.**

*There is no legitimate call site in the owned files.* The ledger's key is
`tool_call_id` — its own guide says it "is the identifier the model, the journal,
and the stream all agree on". Tool calls are executed in the harness tool path
(`alpha/tools/**`); the other external-effect producers are the IM channels
(`app/channels/**`). **Neither is in this change's owned files.** A Gateway
router does not perform the effect it would record, so a write placed there would
be bookkeeping with no referent — a ledger row naming a tool call the Gateway
never made.

*And the repository's own guard makes the honest wiring unreachable from here.*
`test_documented_claims.py` derives `cross_process_exactly_once` from
`side_effect_ledger_has_production_writer` (`_cross_process_exactly_once_available`
simply `return`s the writer predicate). Adding a production writer flips **both**
claims, so the test can only pass by declaring `cross_process_exactly_once:
exists` — a **false claim**. A ledger can deduplicate an *announcement*; it
cannot make a charge that already happened happen once, and it would cover only
the instrumented effects anyway. That is precisely the over-claim the root guide
forbids and that `test_documented_claims.py` was written to prevent.

**The exact change an owner needs, in order:**

1. In the harness tool path, record `begin()` before a side-effecting call and
   `mark_in_flight()` immediately before it — the seam the ledger's own
   `AGENTS.md` describes.
2. Add that module to `PRODUCTION_LEDGER_WRITER_ALLOWLIST` in
   `backend/tests/test_documented_claims.py` (it is empty *on purpose*).
3. **Un-derive `cross_process_exactly_once`** from the writer predicate into its
   own predicate. The derivation is correct only while the writer is absent: with
   a writer, "every effect is durably announced" and "cross-process exactly-once
   is available" stop being the same fact. This is a **strengthening**, not a
   weakening — it keeps the conclusion (`absent`) and makes the claim
   independently checkable.
4. Update the `<!-- honesty-claims -->` block in `runtime/AGENTS.md`
   (`side_effect_ledger_production_writer: exists`) and rewrite the
   "never writes a side-effect ledger row" sentence in the same commit.
5. Only then add a view. **Until step 1 lands, any ledger UI is a permanently
   empty list that reads as "nothing is uncertain"** — which is what a previous
   agent in this project correctly declined to build.

**Item 2 — `ProcessSupervisor`. Not wired, and wiring it would be the wrong fix.**
`supervisor.py:441` is `subprocess.Popen(argv)` of an operator-supplied command:
it supervises a **child OS process**. The Gateway *is* the process, not a host
for one, and Alpha ships no out-of-band worker host that would be the supervised
child. The two real process owners are `start.ps1` / `recovery/watchdog.ps1` (not
in this change's owned files) and the container/orchestrator. Wiring it
in-process would mean supervising the Gateway's own background loops — which
`AutonomySupervisor` already owns, with its own restart budget and `park_reason`.
**That is a second restart-budget owner against the declared single owner**,
which the brief and `backend/AGENTS.md` both forbid. The honest state is the one
`runtime/AGENTS.md` already states: *"The supervisor is not yet wired into the
Windows launcher."* Wiring it means changing `start.ps1`, which is not mine.

**Item 9 — `verification.judge_*`. Not wired: it would silently disable a live
feature.** `jev_acceptance.py:98-101` gates on `system_one.enabled` and
`system_one.enable_acceptance`. Making `judge_enabled` an additional AND-gate
changes nothing for an install that never touched the key (it ships `false`), but
for an operator who *already set it* it would turn a working feature **off** —
their key currently has zero effect, and afterwards it would suppress judging
they can see working. That is the "a key whose meaning changes underneath an
operator who set it" failure `config/AGENTS.md` warns about. Per the brief,
deleting the keys is an **operator decision**, not a test edit — and they are
pinned in three places (`test_deploy_surface_parity.py:705`,
`test_config_version.py:205-208`, `test_verification_config.py:34-41`).
**The repository has already made this call and recorded it** in `known_unread`;
the brief explicitly allowed "report it with evidence".

**Item 4 — `write_safe_reasoning_payload`. Not wired: the plane has no
configuration surface.** `reasoning/config.py:3-4` states it deliberately does not
register an `AppConfig` field, so `storage_path` is `None` in every deployment
and the function would raise `UnsafePersistencePayload` even if called. Wiring a
call that always throws is not wiring. It needs a config field first, which
belongs to the config owner.

**Item 8 — `self_tuning` / `memory.fabric`. Not wired: each is one missing
composition root, and building the root is new code, not wiring.**
`SelfConfigurationProtocol` needs eight injected collaborators constructed and
held; `memory.fabric` needs a store constructed and a `SURFACE_ORDER` entry. Both
are properly reachable only through `alpha/capabilities/catalog.py` — the
repository's own designated mechanism for "optional subsystems earn a production
reference without being on by default" (`catalog.py:1-17`), which simultaneously
satisfies `test_no_orphan_modules.py`. **That file is not mine.** Building an
eighth autonomy loop or a new composition root inside a wiring change is exactly
the "new code disguised as wiring" the brief rules out.

**Items 10, 11 — reported, not touched.** `narrative/gates.py` and
`app/subagent_batches/service.py` are outside this change's owned files, and
`frontend/**` belongs to another agent.

---

## Part 3 — Defects found that the brief did not ask about

### 3.1 The ledger's `UNDETERMINED` reopen path is an unguarded read-modify-write

The brief flagged this ("demonstrated, not inferred") and it is confirmed by
`backend/tests/test_concurrency_cross_process_guards.py:521-534`, which names it
in its own docstring:

> *"KNOWN DEFECT in `persistence/side_effects/sql.py::reconcile`. The settled path
> is …"*

while `persistence/side_effects/AGENTS.md` claims *"Every state change is an
`UPDATE ... WHERE <key> AND status = <expected>`"* and that a settled entry cannot
be re-settled. `sql.py:161` breaks both: the `UNDETERMINED` reopen reads the entry,
decides, and writes it back without a conditional guard, so two processes
reconciling the same entry concurrently can interleave and leave a state neither
computed.

**Reported, not fixed** — `alpha/persistence/side_effects/**` is not in this
change's owned files. It is also the reason wiring the ledger is not merely a
cross-ownership question (§2.7): shipping a production writer on top of an
unguarded reopen would make the concurrency defect reachable instead of
theoretical.

### 3.2 `frontend`: the two `WorkspaceView` definitions have already drifted

Detailed in §1.11. The short form for the frontend owner: `"run-inspector"`
exists in `NavTabs.tsx:43` and `WORKSPACE_TABS` but not in
`WORKSPACE_VIEW_IDS`, so `isWorkspaceView("run-inspector")` is `false` for a view
the UI navigates to. The fix is to make `NavTabs.tsx` derive its type and tab
list from `@/lib/workspace-view` (the direction the module already provides via
`WORKSPACE_VIEW_IDS`), which also makes the `war-room.test.mjs` substring check
unnecessary. Not touched: `frontend/**` is another agent's.

### 3.3 The orphan gate's suffix heuristic is a structural gap, not one file

`test_no_orphan_modules.py::_is_referenced` accepts a module through three
evidence channels, two of which match on a **bare basename**
(`:234-236` dotted suffixes, `:240-243` bare basename under any referenced
ancestor). `"gates"`, `"config"`, `"store"`, `"engine"`, `"models"`,
`"registry"` are near-universally reused basenames under `alpha/` and `app/`, so
the channel is very permissive. `alpha.memory.narrative.gates` is the instance
found here; **the count of modules that pass only this way was not computed**
(that needs a diff of strict-equality against `_is_referenced` over
`_referenced_modules()`, which is a mechanical follow-up).

The suggested ratchet, and it is small: assert that every module passing
`_is_referenced` **also** passes `module in referenced`, and report the delta.
That turns an invisible gap into a number without changing what currently
passes. It belongs with whoever owns `test_no_orphan_modules.py`.

### 3.4 Two defects my own change introduced and then fixed

Recorded because a report that only lists other people's defects is not an audit.

1. `ParkedSessionsResponse` was read with `.to_dict()`, which Pydantic v2 has no
   method for — so the parked-session branch raised `AttributeError` on the real
   request path. Caught by `test_a_parked_session_store_outage_does_not_fail_the_request`.
2. `Proposal(declared_intent="")` was refused by the model, and my first
   handling turned that into a blocking `gate_error`, which would have blocked
   every promotion once the gate was enabled. Caught by
   `test_the_evidence_gate_actually_runs_and_blocks_when_enabled`; fixed in
   `promotion_route.py` by stating the absence instead of inventing a claim
   (§2.6).

---

## Part 4 — Verification actually run

**Everything below was executed in this worktree and is reported as measured,
including the two checks that do not pass.** No result is projected.

All commands run from `backend/` with `PYTHONPATH=.` and this worktree's own
`.venv`, built by `uv sync`; `alpha` resolves to `alpha-wiring`, not the shared
tree (`alpha.__file__` was checked). Nothing here binds a port, starts a server,
or touches the stack the lead owns.

| Command | Result |
|---|---|
| `pytest -q tests/test_wired_durable_runtime_surfaces.py` | **32 passed** |
| the same, with all three production calls removed from the real files | **5 failed, 2 passed** — §4.1 |
| `pytest -q` over every suite this change can affect (`test_supervision_watchdog`, `test_planned_shutdown`, `test_evolution_evidence`, `test_rsi_promotion`, `test_network_wiring`, `test_network_wait_registry`, `test_durable_runtime_realtime`) | **221 passed**, 2 pre-existing warnings, 5m46s |
| the four required gates (`test_no_orphan_modules`, `test_feature_manifest_wiring`, `test_harness_boundary`, `test_documented_claims`) plus `test_deploy_surface_parity` | **54 passed** |
| `ruff format --check` on all 6 changed/added files | **exit 0**, "6 files already formatted" |
| `ruff check` on the same 6 | **exit 0**, "All checks passed!" (one `I001` import-order fix applied to the new test) |
| `python scripts/generate_docs_index.py` | **exit 2** — fails closed on this report; §5 |
| `git status` in the shared tree `alpha/` | **untouched by this change** — no wiring file, no commit, no branch |

`test_durable_runtime_realtime.py` is in that list deliberately: it boots a
**real** lifespan, kills nothing, but does start real background tasks and does
take real ports inside its own fixtures, so it is the closest thing in the suite
to an end-to-end check of the drain I touched. It passes, which means the
`deps.py` change does not break the real startup/shutdown sequence.

Note on runtime: this box is slow enough that `test_no_orphan_modules` alone
takes several minutes, which is why the gates were run in the background rather
than serially. The suites above are the ones whose assertions this change could
plausibly contradict; the full `make test` suite is the lead's to run at merge
time.

### 4.4 One failure that is the worktree, not the change

`tests/test_advanced_agentic_os_wiring.py` fails in **this worktree only**, with
`FileNotFoundError: config.yaml file not found in the project root or legacy
backend/repository root locations` (raised from `app_config.py:559`).
`config.yaml` is gitignored and each worktree needs its own — this one was never
generated, and the brief correctly forbids me running `make config` against a
shared tree. **Confirmed environmental, not a regression:** the identical
invocation against the shared tree's own venv and its `config.yaml` is
**5 passed**. Two of its five tests read real tool metadata, which is why it
appears here at all: it asserts `/api/supervision/fleet` and
`/api/supervision/anomalies` are registered, which is the one supervision route
this change touches. Anyone re-running it in a fresh worktree needs
`make config` first.

### 4.3 The one pre-existing warning that is *not* mine

`test_durable_runtime_realtime.py:51` emits
`PytestUnknownMarkWarning: Unknown pytest.mark.timeout`. It is pre-existing, in
a file I did not touch, and harmless (the suite guards it with
`hasattr(pytest.mark, "timeout")`). Recorded for completeness because a reader
comparing logs will see it.

### 4.1 The bite proofs, exactly

`node mutate_for_bite.mjs` removes three real production lines with explicit
UTF-8 read/write (never a shell text pipeline, which is what corrupted files in
this repository before). Against that mutated tree:

| guard | what was removed | result |
|---|---|---|
| `test_wiring_the_teardown_records_the_drain_it_just_ran` | the `record_drain_report` import + call in `deps.py` | **FAILED** |
| `test_wiring_the_evidence_gate_is_called_from_the_production_promotion_seam` | the `evidence_verdict_for(...)` call in `promotion_route.py` | **FAILED** |
| `test_bite_removing_the_drain_recorder_call_stops_the_record_from_being_written` | same | **FAILED** |
| `test_bite_neutralising_the_evidence_gate_call_restores_the_promotion` | same | **FAILED** |
| `test_bite_a_hard_coded_observed_flag_breaks_the_empty_fleet_guard` | the empty-fleet disclosure in `supervision.py`, hard-coded to `observed: True` | **FAILED** |

Two guards that pass under mutation and are honest about it:
`test_wiring_the_route_reads_app_state_not_a_module_global` and
`test_wiring_the_route_is_declared_on_the_ops_router` — those two pin the new
route's own shape, which this mutation script does not remove, so they are
expected to hold. They are shape guards, not reachability guards; the reachability
of the route is proven by the four behavioural tests above them.

All three files were restored from byte-exact backups and re-verified green
before this section was written.

### 4.2 No existing test was weakened

`git diff --stat` on this branch touches **no** file under `backend/tests/` except
the new `test_wired_durable_runtime_surfaces.py`. Nothing was edited, skipped,
`xfail`-ed, or loosened.

Two existing suites were read closely because they *constrain* this change rather
than being run past it, and both shaped it:

- **`test_documented_claims.py`** is the reason §2.7 does not wire the ledger. Its
  `PREDICATES` table derives `cross_process_exactly_once` from the writer
  predicate, so a production writer forces a false claim. Working *with* that
  guard rather than around it is the single most important judgement in this
  report.
- **`test_network_wiring.py`** pins that `deps.py` still contains
  `"app.state.network_monitor = monitor"` and
  `"app.state.network_waits = wait_service"` as literal source strings. My `deps.py`
  edit is below those lines and leaves both intact; the suite passes.

`test_no_orphan_modules.py` had to be run rather than assumed, because
`app/gateway/ops_runtime.py` is a brand-new module and could trip the orphan gate.
It is imported by `deps.py` and `routers/ops.py`, so it is reachable.

---

## Part 5 — One gate this change breaks, and it is not mine to fix

`python scripts/generate_docs_index.py` **fails closed on this report**:

```
ERROR: unclassified document(s); add a directory rule or file override:
- WIRING_AUDIT.md
```

That is the gate working as designed: it refuses to let a new document appear
unclassified. The fix is one entry in `scripts/generate_docs_index.py`'s
`FILE_OVERRIDES`, which the brief puts **out of bounds** for this change, so it
is not made here. It was verified by measurement, not assumed: a probe file
(`docs/__probe_tmp.md`) reproduced exit code 2 and its removal restored exit 0.

The entry needed, modelled on the existing `SELF_AUDIT.md` entry at
`scripts/generate_docs_index.py:292-295`:

```python
    "WIRING_AUDIT.md": DocumentSpec(
        "plans",
        "Measured audit of implemented-but-uncalled capabilities: what was wired, what was not, and the remaining inert surface.",
    ),
```

Until that lands, `docs/INDEX.md` cannot be regenerated, and the docs-index gate
is red **because this report exists**. That is the correct trade: an unindexed
document is a visible problem, a silently-missing document is not.

---

## Part 6 — Inert-surface counts, as a baseline for the next person

Measured on `origin/main` @ `7772995` before this change, over the whole repo
(3523 files with `.py,.ts,.tsx,.mjs`; 3878 with `.md,.json` added). Production =
non-test.

| surface | before | after | how the "after" was measured |
|---|---|---|---|
| `SideEffectLedger` production writers | 0 | **0** | `SqlSideEffectLedger` → 6 files, all impl/re-export/tests; `SideEffectReclaimer|list_unknown|reconcile(|mark_in_flight` over `backend/app` → **0 in 160 files** |
| `ProcessSupervisor` production callers | 0 | **0** | correctly not wireable in-process (§2.7) |
| `alpha.evolution.evidence` promotion-path callers | 0 | **1** | `promotion_route.route_evolution_gate` |
| `write_safe_reasoning_payload` production callers | 0 | **0** | no config surface exists (§2.7) |
| heartbeat producers into the supervision watchdog | 0 | **0** | Alpha ships no out-of-band worker host (§2.5) |
| distinct `DeterministicWatchdog` instances in production | 2 | 2 | router + model tool; fix belongs in `alpha/supervision/` |
| `record_heartbeat` definitions | 4 | 4 | `bots/health.py`, `subagents/lifecycle.py`, `routers/bots.py`, `watchdog.py` |
| readers of `app.state.network_monitor` | **0** | **1** | `GET /api/ops/runtime` |
| readers of `app.state.network_waits` | **0** | **1** | `GET /api/ops/runtime` |
| consumers of `ShutdownReport.is_clean` | 1 (a logger) | **2** (logger + durable record) | survives restart |
| `self_tuning` production composition roots | 0 | 0 | needs 8 injected collaborators (§2.7) |
| `memory.fabric` production composition roots | 0 | 0 | needs a store + a `SURFACE_ORDER` entry |
| `verification.judge_*` production readers | 0 | 0 | already in `known_unread`; wiring would disable a live feature |
| `alpha.memory.narrative.gates` importers | 0 | 0 | 11-line alias shim; passes the orphan gate heuristically (§3.3) |
| `app.subagent_batches.service` importers | 0 | 0 | 5-line shim; the *class* is reached via the package `__init__` |
| `WorkspaceView` TypeScript importers of `lib/workspace-view` | 0 | 0 | `NavTabs.tsx` ships the duplicate; drifted (§3.2) |

---

## Part 7 — What is STILL INERT after this work

This is the deliverable. It is deliberately specific: each line is a fact about
this tree that a reader can check, not a hedge.

**1. The side-effect ledger has no production writer, and its table is empty.**
No `begin()`, no `mark_in_flight()`, no `SideEffectReclaimer` loop, no
`list_unknown()`, no `reconcile()` — anywhere, including `backend/app/`. So
`UNKNOWN` is *durable and enumerable in the schema* and *absent from a real
run*. Until §2.7's five steps land, **no view over it is honest**, because a
permanently-empty UNKNOWN list reads as "nothing is uncertain" when the truth is
that nothing records effects.

**2. `SqlSideEffectLedger.reconcile` still has an unguarded `UNDETERMINED`
reopen** (`persistence/side_effects/sql.py:161`), contradicting the module's own
`AGENTS.md` claim that every state change is a conditional `UPDATE`. Latent today
because nothing calls it; it would become live the moment a writer is added.

**3. `ProcessSupervisor` is still not wired into the Windows launcher**, and now
also has no in-Gateway seam. `start.ps1` / `recovery/watchdog.ps1` remain the
process owners. On Windows, **nothing restarts the backend automatically.**

**4. `write_safe_reasoning_payload` is still unreachable and would throw if
called** — the reasoning plane registers no `AppConfig` field, so `storage_path`
is always `None`.

**5. `self_tuning.enabled: true` and `memory.fabric.enabled: true` still start
nothing.** Both subsystems are complete and neither is constructed. An operator
who sets either flag gets silence. This is the most likely of these to be hit by
a real user, because it is the one that is a plain config key with a boolean.

**6. `verification.judge_enabled` / `judge_model_name` are still read by nothing.**
Recorded in `known_unread`, kept deliberately, and deleting them is an operator
decision.

**7. Nothing anywhere posts a heartbeat to the supervision watchdog, so
`GET /api/supervision/fleet` is still empty.** It is now *labelled* empty, which
is strictly better and still not a fleet. And the two `DeterministicWatchdog`
instances still disagree by construction: the model-facing `supervision` tool
will keep reporting an empty fleet even after somebody wires a producer into the
REST route. **Fixing that needs `alpha/supervision/` to own the singleton.**

**8. `alpha.memory.narrative.gates` and `app/subagent_batches/service.py` are
still dead modules**, and `lib/workspace-view.ts` still has zero TypeScript
importers with a drifted duplicate in `NavTabs.tsx`.

**9. The orphan-module gate's bare-basename channel is still permissive**, and
the number of modules relying on it is still unknown (§3.3).

**10. No cross-process exactly-once side effects.** Unchanged and unchanged for
the same reason as before: it needs a production writer, and the writer's
concurrency bug is unfixed.

### What is now genuinely NOT inert

- The four network states and the durable parked-session registry are observable
  by an operator, with `UNKNOWN` reachable and never rounded.
- "Did the last shutdown finish?" survives the process and is answerable over
  HTTP, with the failing phase and its reason.
- The evolution evidence gate is on a real promotion decision, default-off,
  fail-closed, and can only block.
- An empty supervision fleet says so instead of looking like a healthy one.

### One thing I did not do that a reader should know about

I did not add a `SideEffectReclaimer` loop, a `list_unknown()` query, or a
`reconcile()` call site anywhere. With no writer they would each be a loop over
an empty table — the same permanently-empty-view failure in a different costume —
and `runtime/side_effects/**` was in this change's owned files, so adding them
was available. It would have made the ledger *look* wired while all three of its
core operations stayed unreachable.

Verified rather than asserted: `SideEffectReclaimer|list_unknown|reconcile(|
mark_in_flight` over `backend/app` returns **0 matches in 160 files**, and
`SqlSideEffectLedger` over the whole backend resolves in 6 files — the
implementation, its re-export, and four test files, unchanged from `origin/main`.
The honest sequence is §2.7's, and it starts with a writer.

### If you only read one paragraph

The ledger is the headline item and it is still inert, deliberately, because
wiring it honestly is blocked by another agent's file and by the repository's own
best guard. The four things that are genuinely wired now are small, but each one
turns a fact the process already measured into something a person can read, and
each has a guard that was demonstrated to fail when the production call was
removed. Nothing in this report should be read as "the dead code is fixed" —
Part 7 is the accurate remaining list, and it is ten items long.
