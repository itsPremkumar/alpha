WORKTREE: C:/Users/PREM KUMAR/Videos/alpha-verifyloop    BRANCH: agent/verifyloop

First, create your worktree:
  cd C:/Users/PREM KUMAR/Videos/alpha
  git fetch origin
  git worktree add ../alpha-verifyloop -b agent/verifyloop origin/main

Read C:/Users/PREM KUMAR/Videos/alpha/MULTI_AGENT_PLAN.md FIRST and obey it.
Then read AGENTS.md, backend/AGENTS.md, backend/tests/AGENTS.md, and the full
backend/packages/harness/alpha/runtime/AGENTS.md.

Read the relevant sections of docs/RESEARCH_CODING_AGENTS.md — specifically the
finding that Alpha "is not behind on agent design; it is behind on operating the
verification it already built." Everything below is built on that.

=====================================================================
THE TASK: CLOSE THE VERIFICATION LOOP
=====================================================================

Alpha already owns every component of an honest self-verification loop. Nothing
connects them. This task builds the controller that does.

WHAT ALREADY EXISTS — read all of it before designing anything
---------------------------------------------------------------
1. `agents/middlewares/finish_first_verifier_middleware.py`
   `_VERIFY_TOOLS` at approximately lines 89-96 names `auto_test_and_repair` and
   `reproduce_and_verify`. The middleware already performs `RemoveMessage` plus
   `jump_to: "model"` with a BUDGETED RETRY, and already interrupts when it
   cannot verify. It **notices and asks. It never drives.**

2. `subagents/acceptance_checks.py:32-45` — this is the best piece of
   verification design in the field, and a research audit judged no competitor
   close to it. A self-report about a test result is INADMISSIBLE:
   `tests_passed:<command>` must anchor to a specific recorded bash execution
   with a recognised summary shape, with shell-structure-aware matching so
   `echo "npm test"` cannot anchor it, and `_TEST_ZERO_SHAPE_RE` vetoes
   "0 passed". Undecidable renders UNVERIFIED, never a silent pass. Lines 47-50
   add a vocabulary rule: leaf booleans are `checked`/`holds`, never
   `satisfied`/`verified`/`passed`, so the model cannot conflate evidence with
   acceptance.

3. `LoopDetectionMiddleware` — anti-thrash, already present.

4. The evidence binder and the side-effect ledger (`runtime/side_effects/`),
   whose `UNKNOWN` is first-class. NOTE: an audit found the ledger has ZERO
   production callers. Do NOT depend on it. If your work needs it, say so —
   another agent is wiring it and that is not yours.

THE GAP
-------
Nothing closes the loop. Today the agent may call a verify tool; a middleware
notices a missing result and asks for one with a budgeted retry. But there is no
component that, on its own initiative:

  runs the tests -> reads the actual failure -> dispatches a bounded fix ->
  re-runs -> decides whether to stop, escalate, or report honestly

A research agent concluded this single change "is worth more than the other nine
recommendations combined." It is the gap between a capability the project
documented and a capability the project operates.

WHAT TO BUILD
-------------
A verification controller that drives the loop. It must:

1. **Execute the verification for real.** Through the real tool path, producing
   the real recorded execution the acceptance checker anchors to. It must NOT
   synthesise a transcript.

2. **Feed the ACTUAL failure back.** The controller reads the recorded failure
   output and hands that to the fix attempt — not a summary of it, not a
   restatement. The whole value of the loop is that the model sees what actually
   happened.

3. **Be bounded.** A fixed attempt budget, decremented per attempt, with the
   remainder visible. Exhaustion is a real, reportable outcome — never an
   infinite loop and never a silent give-up.

4. **Refuse to accept a weakened test.** THIS IS THE CRITICAL REQUIREMENT.
   The known failure mode of every self-repairing agent is "fixing" a failing
   test by weakening it — deleting an assertion, loosening a matcher, marking it
   skip. Alpha is unusually well-placed to catch this, because it already has
   evidence binding. **The controller must compare the test surface before and
   after the fix attempt and refuse an attempt that reduced coverage.** A
   self-repair loop that can be satisfied by deleting the assertion is worse
   than no loop at all, because it manufactures a green result.

5. **Report honestly at the end.** Three distinct outcomes, never conflated:
   VERIFIED (the evidence binder accepted it), FAILED (it ran and did not pass),
   UNVERIFIED (it could not be determined — including "budget exhausted",
   "no recorded execution to anchor to", "the fix attempt weakened the test").
   Undecidable is UNVERIFIED. It is never a pass.

6. **Never create a second lifecycle owner.** `RunManager` is the SOLE run
   lifecycle owner and `SafeRunRecoveryService` the ONLY safe-continuation
   authority. The controller drives a loop WITHIN a run; it does not start,
   adopt, or terminate runs. If the only way to do this is to add a parallel run
   path, STOP and report that.

7. **A completed run is NEVER a verified run.** The project guide is explicit on
   this and the distinction is the entire point of your work. A run that finished
   with UNVERIFIED is not a failure, and it is not a success — say which.

=====================================================================
THE INTEGRATION PROBLEM — READ THIS, IT IS A REAL COLLISION
=====================================================================
`backend/packages/harness/alpha/agents/middlewares/**` is **currently claimed by
another agent working in parallel**. Before you edit anything there:

  git -C C:/Users/PREM KUMAR/Videos/alpha fetch origin
  git log --all --oneline | findstr verifyloop

Check whether that directory is already being worked on. If it is, **do not edit
it.** Instead:

- Put your controller in a NEW package you own (below).
- Define the integration point as a small, explicit, documented INTERFACE — a
  callable the middleware can invoke, or a hook you register — and implement
  against that.
- State in your report exactly what one-line change the middleware owner must
  make to wire it, and why it is theirs to make.

A clean report that says "this needs a one-line call from the middleware owner"
is a GOOD outcome. Silently editing another agent's files is not.

=====================================================================
FILES YOU OWN
=====================================================================
- NEW `backend/packages/harness/alpha/verification/**` (your controller)
- `backend/packages/harness/alpha/subagents/acceptance_checks.py` — ONLY to
  extend the evidence vocabulary if genuinely required; do not weaken it
- `backend/app/gateway/routers/verification.py` (new route, if one is needed)
- NEW `backend/tests/test_verification_loop.py` and `test_verification_honesty.py`
- NEW `docs/VERIFICATION_LOOP.md`
- `backend/packages/harness/alpha/config/verification_loop_config.py` (new) —
  NOTE: `config/app_config.py` and `config.example.yaml` are owned by another
  agent right now. If you need a config key, define the model in your own module,
  report the one-line `AppConfig` change needed, and do not edit those files.

DO NOT EDIT — owned by agents running RIGHT NOW:
- `agents/middlewares/**` (see above — likely not yours)
- `alpha/swarm/**`, any `alpha/team/**` (team-runtime agent)
- `alpha/config/app_config.py`, `config.example.yaml` (specialists agent)
- `alpha/runtime/side_effects/**`, `runtime/supervisor/**`, `runtime/network/**`,
  `alpha/evolution/**`, `app/gateway/**` generally (wiring agent)
- `alpha/bots/**`, `alpha/subagents/executor.py`, `task_tool.py`
- `alpha/errors/**`, `alpha/models/**`
- `frontend/**`, `contracts/**`, `config.yaml`
- `scripts/generate_docs_index.py`, `docs/INDEX.md`

=====================================================================
HARD CONSTRAINTS — these have been violated; read them twice
=====================================================================
- WORK ONLY INSIDE YOUR WORKTREE. `C:/Users/PREM KUMAR/Videos/alpha` is shared
  and three agents wrote into it despite explicit instructions, costing a merge,
  a reverted in-flight edit, and a red docs-index gate. Never touch it.
- `rg` IS BROKEN HERE. Its chocolatey install cannot find `rg.exe`, so it
  silently returns NOTHING. Two investigations were dispatched on false premises
  because of it. Use `Select-String`.
- NEVER rewrite a file with a shell text pipeline. `Get-Content | Set-Content`
  reads UTF-8 as cp1252 and DESTROYS every non-ASCII character. This has
  corrupted files here repeatedly, including one committed by mistake. Use the
  editor, or Node with explicit `encoding: "utf8"`.
- Do NOT run `make dev`, `uvicorn`, or `next dev`. Do NOT bind ports 2026, 3000
  or 8001. A stack is owned by the lead. READ-ONLY HTTP to 8001 is encouraged.
- Do NOT commit to main. Do NOT push. Do NOT `git stash`. Do NOT create or
  remove any worktree other than your own.
- WRITE YOUR REPORT INCREMENTALLY to `docs/VERIFICATION_LOOP.md`. Siblings in
  this project lost whole reports to upstream timeouts.

=====================================================================
VERIFY BEFORE REPORTING — and prove every guard bites
=====================================================================
- `cd backend; $env:PYTHONPATH="."; .venv/Scripts/python.exe -m pytest -q` on
  every test you touched, plus `tests/test_subagent_acceptance*.py`,
  `tests/test_harness_boundary.py`, `tests/test_no_orphan_modules.py`.
- For EVERY property, prove it by breaking it and showing the test FAIL:
    * a controller that loops forever -> the budget test fails
    * a controller that accepts a deleted assertion -> the weakening test fails
    * a controller that reports UNVERIFIED as pass -> the honesty test fails
    * a controller fed a SYNTHESISED transcript rather than a recorded execution
      -> the real-execution test fails
  A guard you have never seen fail is not a guard. Two agents in this project
  shipped tests that could not fail and had to be caught and rewritten.
- `cd backend; .venv/Scripts/python.exe -m ruff format --check` on what you changed.
- `python scripts/generate_docs_index.py` must exit 0 — add the FILE_OVERRIDES
  entry for VERIFICATION_LOOP.md.
- Confirm you did not weaken `acceptance_checks.py`. A test that passes because
  you loosened the checker is the exact defect you are here to prevent.

=====================================================================
DELIVERABLE — write to docs/VERIFICATION_LOOP.md as you go
=====================================================================
1. What you read in the existing pieces, and the precise seam you built on.
2. The controller's design: state machine, where the attempt budget lives, and
   how exhaustion is represented.
3. How a weakened test is detected, and the exact comparison you perform.
4. The three outcomes (VERIFIED / FAILED / UNVERIFIED) and how each is reached.
5. Every test, with its bite proof, per the four mutations above.
6. The integration point the middleware owner must wire, as a diff-shaped
   instruction.
7. **What your loop still cannot do, stated as a limitation.** This repository's
   contribution rules make claim honesty mandatory; a flattering summary is worth
   less than an accurate gap list. Specifically address: what happens when the
   failure is non-deterministic, when the fix is correct but the test is wrong,
   and what your controller does when it cannot tell the difference.
Do not commit. Do not push.
