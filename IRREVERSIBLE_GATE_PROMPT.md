WORKTREE: C:/Users/PREM KUMAR/Videos/alpha-irreversible    BRANCH: agent/irreversible

First, create your worktree:
  cd C:/Users/PREM KUMAR/Videos/alpha
  git fetch origin
  git worktree add ../alpha-irreversible -b agent/irreversible origin/main

Read C:/Users/PREM KUMAR/Videos/alpha/MULTI_AGENT_PLAN.md FIRST and obey it.
Then read AGENTS.md, backend/AGENTS.md, and the `System One transport` and
`Bot self-service` sections of backend/packages/harness/alpha/models/AGENTS.md
and backend/packages/harness/alpha/AGENTS.md.

=====================================================================
THE TASK: GATE THE ONE ACTION THAT COMMITS TO THE WORLD
=====================================================================

A live-research audit of the computer-use / browser agent class concluded, of
Alpha specifically:

> Alpha has no irreversible-action gate on the browser — its strongest surface.
> The sentinel guard and blast-radius tiering cover hotkeys and shell commands.
> Nothing covers `browser_type(submit=True)`, which is the one action that can
> commit a transaction.

And, in the same report:

> Alpha already copied the right grounding idea and it's better than every CUA
> product here: `system_one_policy.py:287-317` — the model returns an index,
> never a coordinate. Every competitor passes raw pixels across the model
> boundary.

So Alpha has the best capability model in this category — a token the model
literally cannot mint — and then did not apply it to the surface where the money
and the data are. That is the task.

THE GAP, PRECISELY
------------------
- `browser_type(submit=True)`, form submission, checkout, "send", "pay",
  "confirm", "delete", account and permission changes, and any navigation that
  commits state are **not** gated.
- Shell commands and hotkeys **are** gated, by a sentinel guard and
  blast-radius tiering. Read that guard and reuse its mechanism.
- `sandbox/computer_use.py:136` already contains the approval-token pattern you
  need. Find it, read it, and build on it rather than inventing a second one.

WHAT TO BUILD
-------------
1. **Classify every browser action by reversibility.** Not by name — by
   consequence. A `GET` is reversible. A `POST` that submits a form is not.
   A navigation is not. Be rigorous and be explicit about the ambiguous middle:
   typing into a search box is harmless; typing into a field that is *about* to
   be submitted is not, and the decision has to be made before the type, not
   after.

2. **Require an explicit operator-issued token for the irreversible class.** The
   token must be something the **model cannot mint for itself**. If the model can
   obtain, guess, derive, or reuse one, the gate is decorative. This is the
   single most important property and it is the one most likely to be got wrong,
   so test it adversarially: can the model obtain a token by any route?

3. **Make the gate fail CLOSED.** If the approval state cannot be determined,
   the action is refused. An indeterminate gate must never resolve to "allow".

4. **Make the request legible.** When blocked, the operator sees what is about to
   happen, on what target, and can allow once, deny, or allow for this session.
   "Something was blocked" is not a usable interface.

5. **Keep it out of the model's reach as *content*.** Anything the model can read
   about the approval system must not be a capability. Read the existing rules on
   model-visible surfaces before you add a field.

6. **Do not create a second permission authority.** `ToolPermissionGate` and
   `routers/policy.py` already exist and are owned by others. This gate is
   specifically for irreversible *world-committing* actions and must delegate to
   or compose with the existing permission model, not fork it. If composing is
   impossible, STOP and report why.

ALSO IN SCOPE — the cheapest win on the board
---------------------------------------------
A second research finding, same audit: `browser_scroll`, `browser_key` and
`browser_select` are **already implemented and not exposed**, and the tab
management tools are already implemented and not surfaced. Expose them, with
honest empty and error states per the house rules. This is small, and it is
strictly less capability than a commit action, so it needs no gate — but confirm
that claim per tool rather than assuming it.

=====================================================================
FILES YOU OWN
=====================================================================
- `backend/packages/harness/alpha/sandbox/computer_use.py`
- `backend/packages/harness/alpha/tools/builtins/browser_*.py` (the ones you
  classify; name each in your report)
- NEW `backend/packages/harness/alpha/browser/actions.py` (your classifier)
- NEW `backend/app/gateway/routers/browser_approval.py`
- NEW `frontend/src/lib/browser-approval.ts` and its section wiring
- NEW `backend/tests/test_irreversible_action_gate.py`
- NEW `docs/IRREVERSIBLE_ACTIONS.md`

DO NOT EDIT — owned by agents running RIGHT NOW:
- `alpha/sandbox/security/**` (another agent)
- `alpha/teams/**`, any `alpha/team/**`, `alpha/swarm/**`
- `alpha/config/app_config.py`, `config.example.yaml` (specialists agent)
- `alpha/runtime/**`, `app/gateway/deps.py`, `routers/policy.py` (wiring agent)
- `alpha/agents/middlewares/**`, `alpha/sandbox/security/**` (octop-steal agent)
- `alpha/subagents/**`, `task_tool.py`, `alpha/bots/**`
- `alpha/errors/**`, `alpha/models/**`
- `contracts/**`, `config.yaml`
- `scripts/generate_docs_index.py`, `docs/INDEX.md`

If you need a change in one of those, STOP and describe it exactly. A clean
report saying "this needs the permission-model owner" is a GOOD outcome.

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
  reads UTF-8 as cp1252 and DESTROYS every non-ASCII character — this has
  corrupted files here repeatedly, including one committed by mistake. Use the
  editor, or Node with explicit `encoding: "utf8"`.
- Do NOT run `make dev`, `uvicorn`, or `next dev`. Do NOT bind ports 2026, 3000
  or 8001. A stack is owned by the lead. READ-ONLY HTTP to 8001 is encouraged.
- Do NOT commit to main. Do NOT push. Do NOT `git stash`. Do NOT create or
  remove any worktree other than your own.
- WRITE YOUR REPORT INCREMENTALLY to `docs/IRREVERSIBLE_ACTIONS.md`.

=====================================================================
VERIFY BEFORE REPORTING — and prove every guard bites
=====================================================================
- `cd backend; $env:PYTHONPATH="."; .venv/Scripts/python.exe -m pytest -q` on
  every test you touched, plus `tests/test_harness_boundary.py`,
  `tests/test_no_orphan_modules.py`, `tests/test_tool_call_repair.py`.
- `cd frontend; node node_modules/typescript/bin/tsc --noEmit` — 0 errors.
- `cd frontend; node --test src/lib/*.test.mjs` — all green.
- Prove these four guards by breaking each and showing the test FAIL:
    * the model can obtain a token by itself        -> the token test fails
    * an indeterminate approval state resolves allow -> the fail-closed test fails
    * a commit-class action proceeds ungated         -> the classification test fails
    * a blocked request shows no target or no action -> the legibility test fails
  A guard you have never seen fail is not a guard. Two agents in this project
  shipped tests that could not fail and had to be caught and rewritten.
- `cd backend; .venv/Scripts/python.exe -m ruff format --check` on what you changed.
- `python scripts/generate_docs_index.py` must exit 0 — add the FILE_OVERRIDES
  entry for IRREVERSIBLE_ACTIONS.md.

=====================================================================
DELIVERABLE — write to docs/IRREVERSIBLE_ACTIONS.md as you go
=====================================================================
1. The full action table: every browser action you classified, its class, and
   the file:line that implements it. No action may be unclassified — an
   unclassified action defaults to irreversible, and say which actions fall there.
2. The token design, and specifically **how you proved the model cannot mint
   one**. Write the adversarial attempt you made and why it failed.
3. The fail-closed path, and the exact condition that triggers it.
4. The operator interface: what is shown, what the three choices are, and what
   "allow for this session" actually scopes to.
5. The newly exposed read-only tools, each with its honest empty and error state.
6. Every test with its bite proof, per the four mutations.
7. **What remains ungated, stated as a limitation.** This repository's rules make
   claim honesty mandatory, and a safety gate that overstates its coverage is
   more dangerous than no gate at all. Be exhaustive about what you did NOT
   gate, especially any action whose reversibility you could not determine.
Do not commit. Do not push.
