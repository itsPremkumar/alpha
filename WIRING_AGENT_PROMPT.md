WORKTREE: C:/Users/PREM KUMAR/Videos/alpha-wiring    BRANCH: agent/wiring

First, create your worktree:
  cd C:/Users/PREM KUMAR/Videos/alpha
  git fetch origin
  git worktree add ../alpha-wiring -b agent/wiring origin/main

Read C:/Users/PREM KUMAR/Videos/alpha/MULTI_AGENT_PLAN.md FIRST and obey it.
Then read AGENTS.md, backend/AGENTS.md, backend/tests/AGENTS.md, and
backend/packages/harness/alpha/AGENTS.md.

=====================================================================
THE TASK: WIRE UP THE AI-AGENT FEATURES THAT EXIST BUT ARE NEVER CALLED
=====================================================================

Alpha has a documented and repeatedly-confirmed defect class: a capability is
fully implemented, has tests, has a database migration, is documented as
authoritative, and is called by nobody. It ships green. It does nothing.

Four independent audits in the last day found the same shape repeatedly. Your
job is to CONNECT what already exists, not to build anything new.

"ALREADY CONFIRMED INERT — verify each yourself, then wire it"
-----------------------------------------------------------
1. SideEffectLedger (`alpha/runtime/side_effects/` + `alpha/persistence/side_effects/`)
   Implemented in memory AND SQL, migration `0027_side_effect_ledger`, tested by
   `test_side_effect_ledger.py` and `test_side_effect_ledger_sql.py`.
   A source scan found ZERO references anywhere under `backend/app/`. No
   `begin()` before a tool call, no `mark_in_flight()`, no `SideEffectReclaimer`
   loop, no `list_unknown()` query, no `reconcile()`. The table is created and
   then never written to.
   ALSO: the ledger's own contract claims every state change is
   `UPDATE ... WHERE <key> AND status = <expected>` and that a settled entry
   cannot be re-settled. The `UNDETERMINED` reopen path at `sql.py:161` is an
   unguarded read-modify-write — demonstrated, not inferred.

2. `alpha.runtime.supervisor` / `ProcessSupervisor` (`runtime/supervisor/`)
   RESTART / SAFE_MODE / GIVE_UP under a sliding-window restart budget, with its
   own test file. No production caller. Its own guide already concedes it "is
   not yet wired into the Windows launcher".

3. `alpha.evolution.evidence`
   A whole gate/verdict subsystem. Its only importer is its own config field at
   `app_config.py` (`evolution_evidence.*`). Nothing calls it.

4. `write_safe_reasoning_payload`
   Zero production callers. Its own test passes `None`, so the test proves
   nothing about the product path.

5. `GET /supervision/heartdog` — zero production callers
   So the fleet is `{}` forever. Note there are three DIFFERENT
   `record_heartbeat` functions (`bots/health.py`, `subagents/lifecycle.py`) and
   `supervision_tool.py` holds its own separate `_GLOBAL_WATCHDOG` instance from
   the router's. The frontend was reading `{}` and calling it "watching".

6. `alpha/runtime/network/` — the service is constructed at
   `app/gateway/deps.py:669` and parked on `app.state.network_waits`, and a scan
   for readers of that attribute finds only `deps.py` itself. The four-state
   model (ONLINE / DEGRADED / UNKNOWN / OFFLINE) exists; nothing observes it.

7. `runtime/shutdown.py::is_clean` — consumed at `deps.py:781` by a `logger`
   call and nothing else. An eight-phase drain reports "clean" into a logfile
   and no operator ever sees it.

8. `self_tuning.*` (6 keys) and `memory.fabric.*`
   Read only by a subsystem production never invokes. Flipping `enabled: true`
   starts NOTHING. `backend/app` has zero `self_tuning` hits; `fabric.store`,
   `fabric.lifecycle` and `fabric.forget` are test-only imports.

9. `verification.judge_enabled` / `verification.judge_model_name`
   Declared, shipped in `config.example.yaml` AND in the Helm chart's default
   config, and read by nothing outside their own declaration module.
   `alpha.subagents.jev_acceptance` took over the job.
   IMPORTANT: per `config/AGENTS.md`, DELETING a config key is a breaking change
   for every existing install and is an OPERATOR DECISION, not a test edit. So
   do not delete it. Either wire it, or report it with evidence.

10. `alpha.memory.narrative.gates` and `app.subagent_batches.service`
    Both dead. Both pass the orphan gate only through its bare-basename suffix
    channel (`test_no_orphan_modules.py:219-244`), which is a gap in the gate
    itself. Report; do not delete.

11. `frontend/src/lib/workspace-view.ts` — read as plain text by one test, while
    `NavTabs.tsx` ships a DUPLICATE type. Two definitions of the view registry.

=====================================================================
HARD CONSTRAINTS — these have been violated; read them twice
=====================================================================
- WORK ONLY INSIDE YOUR WORKTREE. `C:/Users/PREM KUMAR/Videos/alpha` is shared
  and three agents have written into it despite explicit instructions. That cost
  a merge, a reverted in-flight edit, and a red docs-index gate. Never touch it.
- `rg` IS BROKEN IN THIS ENVIRONMENT. Its chocolatey install cannot find
  `rg.exe`, so it silently returns NOTHING. Two investigations were dispatched
  on false premises because of it. Use `Select-String` or the editor's search.
- NEVER rewrite a file with a shell text pipeline. `Get-Content | Set-Content`
  reads UTF-8 as cp1252 and DESTROYS every non-ASCII character. This has
  corrupted files in this repository repeatedly, including one committed by
  mistake and later restored from git. Use the editor, or Node with explicit
  `encoding: "utf8"`. If you must bulk-edit, use a script that reads and writes
  UTF-8 explicitly.
- Do NOT run `make dev`, `make dev-daemon`, `scripts/serve.sh`, `uvicorn`, or
  `next dev`. Do NOT bind ports 2026, 3000 or 8001. A stack is owned by the lead.
  READ-ONLY HTTP to port 8001 is encouraged — probing a live route is how you
  find out whether wiring actually works.
- Do NOT commit to main. Do NOT push. Do NOT `git stash`. Do NOT create or
  remove any worktree other than your own.
- WRITE YOUR REPORT INCREMENTALLY to `docs/WIRING_AUDIT.md` in your worktree,
  section by section as you finish each one. Siblings in this project lost
  whole reports to upstream timeouts.

=====================================================================
FILES YOU OWN (everything else is someone else's RIGHT NOW)
=====================================================================
- `backend/app/gateway/**` — the wiring sites. This is your primary surface.
- `backend/packages/harness/alpha/runtime/side_effects/**`
- `backend/packages/harness/alpha/runtime/supervisor/**`
- `backend/packages/harness/alpha/runtime/network/**`
- `backend/packages/harness/alpha/evolution/**`
- `backend/app/gateway/routers/supervision*.py` and the system-monitor router
- NEW `backend/tests/test_wired_*.py`

DO NOT EDIT — these are owned by agents running in parallel right now:
- `backend/packages/harness/alpha/swarm/**`, `alpha/bots/**`, `alpha/subagents/**`
- `backend/packages/harness/alpha/config/**` and `config.example.yaml`
  (a `specialists:` section is being added there)
- `backend/packages/harness/alpha/runtime/journal.py`
- `backend/packages/harness/alpha/errors/**`
- `frontend/**` (a UI agent owns it)
- `contracts/**`, `config.yaml`
- `scripts/generate_docs_index.py` and `docs/INDEX.md`

If wiring genuinely requires a change in one of those, STOP and describe the
exact change in your report instead of making it. A clean report saying "this
needs the config owner" is a good outcome.

=====================================================================
THE HARD ARCHITECTURAL RULE — READ THIS BEFORE DESIGNING ANYTHING
=====================================================================
`RunManager` is the SOLE run lifecycle owner. `SafeRunRecoveryService` is the
ONLY safe-continuation authority. `subagents/AGENTS.md` explicitly forbids
broadening the delegation proxy into a generic journal facade or calling the
event store from the subagent loop.

A second lifecycle owner is WORSE than no wiring. If the only way to wire a
feature is to add a parallel write path or a second status store, STOP and
report that. Do not build it.

Also, the side-effect ledger has a documented fail-OPEN requirement that you
must honour: a ledger write failure must NOT fail the user's action. Record
that requirement explicitly in your code and prove it with a test.

=====================================================================
HOW TO DO THIS PROPERLY
=====================================================================
1. VERIFY BEFORE YOU WIRE. Do not trust the list above — verify each item with
   your own count, and report any that are actually already wired. Several
   briefs in this project contained false premises and the agents that checked
   found the real defect instead.

2. WIRE THE SMALLEST REAL SEAM. For each item, find the narrowest production
   call site where the capability belongs. Prefer one well-placed call over a
   broad refactor. "Wired" means a production code path reaches it, not that a
   test can.

3. PRODUCE THE HONEST STATE. `UNKNOWN` must be reachable in practice or the
   design is theatre — say exactly which failure produces it. If a feature
   cannot be fully wired, land the narrow part PLUS a guard that fails loudly if
   the un-wired remainder grows. An explicit small list is a ratchet; a vague
   claim is a lie.

4. NEVER SHIP A VIEW OVER AN INERT FEATURE. A permanently-empty list reads as
   "nothing is wrong", which is a false all-clear. This has already been
   caught once in this project: an agent was about to build a UI for the
   side-effect ledger, checked, found the ledger had zero callers, and
   correctly declined. Do the same.

=====================================================================
VERIFY BEFORE REPORTING — and prove every guard bites
=====================================================================
- `cd backend; $env:PYTHONPATH="."; .venv/Scripts/python.exe -m pytest -q` on
  every test you touched, PLUS `tests/test_no_orphan_modules.py`,
  `tests/test_feature_manifest_wiring.py`, `tests/test_harness_boundary.py`,
  and `tests/test_docs_index.py`.
- For EVERY piece of wiring, prove it: write a test that fails when the
  production call is removed, show it failing, restore, show it passing.
  A guard you have never seen fail is not a guard. Two agents in this project
  shipped tests that could not fail and had to be caught and rewritten.
- `cd backend; .venv/Scripts/python.exe -m ruff format --check` on files you
  changed.
- `python scripts/generate_docs_index.py` must exit 0.
- Confirm you did not weaken any existing test.

=====================================================================
DELIVERABLE — write this to docs/WIRING_AUDIT.md as you go
=====================================================================
A table: capability | verified inert? | call site you wired | how a production
path now reaches it | test that proves it | bite proof.
Then: which items you deliberately did NOT wire, and why.
Then: which items were already wired, contradicting the brief above.
Then: the new inert-surface counts, so the next person has a baseline.
Then — most important — **an explicit list of what is STILL inert after your
work.** This repository's contribution rules make claim honesty mandatory, and
the most valuable thing you can produce is an accurate remaining list rather
than a flattering summary.
Do not commit. Do not push.
