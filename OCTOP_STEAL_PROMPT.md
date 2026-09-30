WORKTREE: C:/Users/PREM KUMAR/Videos/alpha-octopsteal    BRANCH: agent/octopsteal

First, create your worktree:
  cd C:/Users/PREM KUMAR/Videos/alpha
  git fetch origin
  git worktree add ../alpha-octopsteal -b agent/octopsteal origin/main

Read C:/Users/PREM KUMAR/Videos/alpha/MULTI_AGENT_PLAN.md FIRST and obey it.
Then read AGENTS.md, backend/AGENTS.md, and the full
backend/packages/harness/alpha/runtime/AGENTS.md and subagents/AGENTS.md.

Context you must read first:
  docs/RESEARCH_OCTOP.md   (a verified audit of TencentCloud/Octop)
Read its "strongest five ideas to steal" and its "five places weaker" sections.
The second list matters as much as the first: it is the list of things that
SOUNDED impressive and turned out to be a roadmap checkbox, a prompt template,
or 2.5 KB of process-local state. Do not copy anything from that list.

=====================================================================
THE TASK: PORT FIVE IDEAS FROM OCTOP THAT ALPHA GENUINELY LACKS
=====================================================================

Alpha is not behind Octop on agent capability — it is ahead. Octop is twelve
weeks old, its AgentTeams is Beta by its own admission in three places, and on
the two axes that matter most Octop plans things Alpha has already shipped
(self-evolution, project-scoped work).

It IS behind on one thing: **trust, honesty, and refusal discipline in the
team surface.** Those five ideas are what this task is about. All five are
things Alpha provably cannot do today.

---------------------------------------------------------------------
IDEA 1 — SUBTRACT THE COORDINATOR'S CAPABILITIES; DO NOT POLICE THEM
---------------------------------------------------------------------
Octop gives its team coordinator **no** filesystem, no browser, no search, no
MCP, no skills, no plugins. Only `agent_list`, `ask_agent`, memory and time.
Capabilities are removed from the construction site, so no permission document
can drift away from the enforcement.

Alpha's coordinator today has whatever it was given. Make it **structurally
incapable** of taking a side-effecting action itself: it coordinates, it does
not act. The work belongs to members.

The property to achieve and to test: **there is no configuration of a team
coordinator that lets it execute a tool that changes the world.** Not "it is
discouraged" — not constructible. Prove it by attempting it.

---------------------------------------------------------------------
IDEA 2 — THE COORDINATOR REWRITES THE TASK  (the single best idea here)
---------------------------------------------------------------------
Users always speak to the coordinator. An @-mention is a *hint*, not a routing
instruction. Members receive the **coordinator's rewritten specification**, never
the user's raw words, and the conversation transcript is demoted to system
background. The planner is the sole interpreter of intent.

Why this is worth porting: today every subagent that reads the raw conversation
re-reasons the same context, which burns tokens and produces divergent
instructions. One rewrite, many members.

Alpha's `subagents/executor.py` and `task_tool.py` are the seam. Implement it so
the task a member receives is the coordinator's brief, and prove the member
never receives the raw transcript. Be careful: a rewritten task must still be
faithful. **A rewrite that drops a requirement is worse than no rewrite**, so
test that a requirement present in the source survives into the member's brief.

---------------------------------------------------------------------
IDEA 3 — A TOOL GUARD WITH `exclude_patterns`
---------------------------------------------------------------------
Octop's shell guard is user-editable, audited, and has an **exclusion** field
alongside its allow/deny rules. `exclude_patterns` is the field almost every
guardrail system omits, and it is the one that makes guardrails usable: without
exclusions, a user spends all day adding allows for ordinary work.

Alpha has `ToolPermissionGate`, `alpha/sandbox/security`, and
`routers/policy.py`. Add the missing dimension: a way to say "these specific
known-safe patterns are excluded from the rule", with the exclusion being as
audited and as visible as the rule it overrides. **An exclusion that silently
widens a rule is worse than no exclusion** — so an exclusion must be reported
wherever the rule it overrides is reported.

---------------------------------------------------------------------
IDEA 4 — SURFACE A DELEGATED CHILD'S PERMISSION PROMPTS IN THE OPERATOR'S CHAT
---------------------------------------------------------------------
This is the capability Alpha **cannot do at all** today: Alpha speaks ACP
inbound only. Octop also delegates *outbound* to OpenCode, Claude Code, Codex
and others over stdio JSON-RPC, behind three separate gates: runners are
per-user, tools are opt-in per agent, and — the interesting part — when the child
process emits a `[permission_required]` prompt, **it surfaces in Octop's own
chat** and the operator answers it with the exact option id.

Without that last gate, outbound delegation to a coding agent **hangs** the run
the moment the child wants permission. So this is not a nicety; it is the
difference between outbound ACP working and not working.

Port the pattern, not the whole ACP stack: when Alpha delegates a bounded task to
an external agent, a permission request from that agent must become something
the operator can see and answer, and a run must not silently block on it. If the
full outbound runner is too large, land the **permission-surfacing seam** with a
test that a synthetic child prompt becomes a visible, answerable request — and be
explicit that outbound runners are not yet wired.

---------------------------------------------------------------------
IDEA 5 — IDENTITY-AWARE, PROGRESS-VISIBLE MAINTENANCE THAT REFUSES WHEN IT CANNOT
---------------------------------------------------------------------
Octop's live memory maintenance lets an operator list eligible agents, back up,
slim, and watch progress — and it **refuses maintenance requested over IM**,
because the IM identity is an agent owner rather than a verified sender. Chat
stays available until maintenance is confirmed and begins.

Alpha has memory but no user-triggered, visible-progress, refusal-aware
maintenance. Port all three properties together, because they are one idea:
**an operation that changes stored state should be (a) explicitly triggered,
(b) observable while it runs, and (c) refused when the requester's identity
does not support it.**

This is the highest-value item for a project whose contribution rules make claim
honesty mandatory, and it is where the refuse-to-fake discipline becomes a
product surface rather than a docstring.

=====================================================================
WHAT TO STEAL FROM OCTOP'S "REFUSE-TO-FAKE" SECTION
=====================================================================
Octop's single strongest quality signal is not a feature. It states, in the
product: no fabricated percentage or ETA; partial work reported as exactly the
part that is done ("dedup done but space reclamation not done"); operations
refused when identity does not support them; aborted runs recorded as aborted
rather than as failures.

Wherever you add a progress surface in this task, apply that standard: a partial
result reports the part that is done and names the part that is not. A progress
bar that reaches 100% for work that is 60% complete is the exact defect class
this repository ships repeatedly, and seven frontend surfaces were fixed today
for precisely this.

=====================================================================
HARD CONSTRAINTS — these have been violated; read them twice
=====================================================================
- WORK ONLY INSIDE YOUR WORKTREE. `C:/Users/PREM KUMAR/Videos/alpha` is shared
  and three agents wrote into it despite explicit instructions. Never touch it.
- `rg` IS BROKEN HERE. Its chocolatey install cannot find `rg.exe`, so it
  silently returns NOTHING, and two investigations were dispatched on false
  premises because of it. Use `Select-String`.
- NEVER rewrite a file with a shell text pipeline. `Get-Content | Set-Content`
  reads UTF-8 as cp1252 and destroys every non-ASCII character. Use the editor,
  or Node with explicit `encoding: "utf8"`.
- Do NOT run `make dev`, `uvicorn`, or `next dev`. Do NOT bind ports 2026, 3000
  or 8001. A stack is owned by the lead. READ-ONLY HTTP to 8001 is encouraged.
- Do NOT commit to main. Do NOT push. Do NOT `git stash`. Do NOT create or
  remove any worktree other than your own.
- WRITE YOUR REPORT INCREMENTALLY to `docs/OCTOP_PORT.md` as you go. Siblings in
  this project lost whole reports to upstream timeouts.

=====================================================================
OWNERSHIP — three agents are working in parallel RIGHT NOW
=====================================================================
YOU OWN:
- `backend/packages/harness/alpha/sandbox/security/**` (idea 3)
- `backend/packages/harness/alpha/agents/middlewares/**` (idea 1)
- NEW `backend/packages/harness/alpha/memory/maintenance/**` (idea 5)
- NEW `backend/app/gateway/routers/maintenance.py` (idea 5)
- NEW `backend/tests/test_octop_*.py`
- NEW `docs/OCTOP_PORT.md`

DO NOT EDIT — owned by agents running now:
- `alpha/swarm/**` and any new `alpha/team/**` (a team-runtime agent is building
  the coordination loop). Ideas 1 and 2 depend on its output: define a STABLE
  INTERFACE you need, implement against it, and say in your report exactly what
  you expect from it. Do not build the coordinator yourself.
- `alpha/subagents/executor.py` and `task_tool.py` (idea 2 touches this seam —
  if you need a change there, STOP and describe it exactly)
- `alpha/config/**` and `config.example.yaml` (a `specialists:` section is landing)
- `alpha/runtime/**`, `alpha/errors/**`, `alpha/bots/**`
- `frontend/**`, `contracts/**`, `config.yaml`
- `scripts/generate_docs_index.py`, `docs/INDEX.md`

A clean report saying "this needs the team-runtime owner" is a GOOD outcome.
Do not take ownership of another agent's file to unblock yourself.

=====================================================================
ARCHITECTURAL RULES THAT ARE NOT NEGOTIABLE
=====================================================================
- `RunManager` is the SOLE run lifecycle owner. `SafeRunRecoveryService` is the
  ONLY safe-continuation authority. A second lifecycle owner is worse than no
  feature. Stop and report rather than adding one.
- The side-effect ledger has a fail-OPEN requirement: a ledger write failure
  must NOT fail the user's action. Honour and test it.
- `RunManager` retention and `StreamBridge` late-subscriber windows are
  process-local. Octop's own weakest finding was that its TeamJobTracker is
  2,553 bytes of in-process state, so a restart mid-run loses in-flight work and
  the callback never fires. **Do not copy that mistake.** If your work introduces
  durable in-flight state, it must be durable.
- Never ship a view over an inert feature. A permanently-empty list reads as
  "nothing is wrong", which is a false all-clear.

=====================================================================
VERIFY BEFORE REPORTING — and prove every guard bites
=====================================================================
- `cd backend; $env:PYTHONPATH="."; .venv/Scripts/python.exe -m pytest -q` on
  every test you touched, plus `tests/test_harness_boundary.py`,
  `tests/test_no_orphan_modules.py`, `tests/test_feature_manifest_wiring.py`.
- For EVERY property above, prove it: write the test, remove the production
  behaviour, show the test FAIL, restore, show it pass. Two agents in this
  project shipped tests that could not fail and had to be caught and rewritten.
  A guard you have never seen fail is not a guard.
- `cd backend; .venv/Scripts/python.exe -m ruff format --check` on what you changed.
- `python scripts/generate_docs_index.py` must exit 0 — add the FILE_OVERRIDES
  entry for OCTOP_PORT.md.
- Confirm you did not weaken any existing test.

=====================================================================
DELIVERABLE — write to docs/OCTOP_PORT.md as you go
=====================================================================
Per idea: what you ported, the file:line, the property achieved, the test, and
the bite proof.
Then: which ideas you deliberately did NOT port, and why.
Then: which of your five landed only partially, stated as partial.
Then — most important — **an explicit list of what is still missing for Alpha to
have Octop's team-trust properties in full.** This repository's rules make claim
honesty mandatory, and an accurate remaining list is worth more than a
flattering summary.
Do not commit. Do not push.
