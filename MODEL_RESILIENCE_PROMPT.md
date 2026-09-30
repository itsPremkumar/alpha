WORKTREE: C:/Users/PREM KUMAR/Videos/alpha-modelrel    BRANCH: agent/modelrel

First, create your worktree:
  cd C:/Users/PREM KUMAR/Videos/alpha
  git fetch origin
  git worktree add ../alpha-modelrel -b agent/modelrel origin/main

Read C:/Users/PREM KUMAR/Videos/alpha/MULTI_AGENT_PLAN.md FIRST and obey it.
Then read AGENTS.md, backend/AGENTS.md, and in full
backend/packages/harness/alpha/models/AGENTS.md — especially the sections on
model routing failing closed, the reasoning-effort ladder, and the
"no cross-namespace drift" rule.

=====================================================================
THE TASK: MAKE THE MODEL LAYER SURVIVE A MODEL DISAPPEARING
=====================================================================

Alpha's default model can be withdrawn without notice, and there is no mechanism
that notices. This task builds one, and removes three pieces of dead
configuration that are actively misleading.

THE PROBLEM, MEASURED
---------------------
Alpha's shipped default is pinned to a **keyless, anonymous, stealth model of
unknown provenance**. It works today. It can stop working at any moment, with no
notice, no deprecation window, and no announcement — and when it does, every
fresh install is dead on first run.

Worse: the machinery that would survive this is **built, tested, and switched
off.**

1. `model_routing:` is COMMENTED OUT in `config.example.yaml` (approximately
   lines 236-244). So every fresh install routes everything to one model,
   despite complete, fail-closed routing machinery that validates every declared
   name against `models[]` at load and refuses to invent a route.

2. The deployed OpenRouter key has **no credits** and answers **HTTP 402**. It
   cannot be the fallback.

3. Direct measurement this session: of the free endpoints available to this
   project, exactly ONE answered. `space-bunny-free` returned 200 with correct
   output and working tool calls. `nemotron-3-ultra-free`,
   `muse-spark-1.3-contributor-free` and `mimo-v2.6-flash-free` all returned 500
   on every attempt, interleaved with the working model to rule out throttling.
   **A single-model configuration is one provider incident from total outage.**

WHAT TO BUILD
-------------
1. **Turn on `model_routing:` properly, and prove it earns its place.** Not just
   uncomment it — populate it with real, validated categories and tiers so a
   "quick" task actually runs on a cheap model and an escalation actually
   escalates. The repo's rule is that the built-in tables are advisory
   *suggestions* for operators who declared nothing, and that a chain which
   filters to nothing must return an empty chain plus a **reason** rather than
   inventing a route. Preserve that.

2. **Make the default model's withdrawal survivable.** This is the actual goal.
   Concretely, the system must be able to notice that the configured default is
   not answering and fall back **within the same run**, not on the next start.
   The existing failover machinery (`alpha/models/fallback.py`,
   `CreditExhaustedError`, `FailoverEvent`, `get_last_failover_events`) is built
   for exactly this and should be used rather than replaced.

3. **Distinguish "the model is gone" from "the model said no".** A 402 is
   billing. A 429 is a throttle. An auth failure is a credential. A 5xx from one
   model while another answers is a model outage. These must not collapse into
   one "provider error" state, and a throttle must never be read as an outage.
   `models/fallback.py` already excludes rate-limit phrasing from billing for
   this reason — follow it.

4. **Remove the dead configuration.** Three specific findings:
   - `persorai` answers **HTTP 404** live, and ships with a hardcoded
     `Bearer alpha-public-anonymous` header. A template key that reads as
     authoritative while doing nothing.
   - `llm7.documented_models` documents `gpt-oss` as free; that model returns
     **HTTP 400**. And `_pick_model` **prefers documented ids**, so a dead entry
     is selected first.
   - A shared-pool gateway answers **403**. Documented, shipped, unusable.
   **IMPORTANT:** per `config/AGENTS.md`, removing a key is a breaking change for
   every existing install and is an OPERATOR DECISION. So: do not silently
   delete. Either wire it, or remove it with a startup warning and a named
   rationale, or report it with evidence and let the operator decide. Say which
   you did for each.

5. **A guard that catches the class, not the instance.** Add a check that every
   entry in `free_gateways:` and `model_catalog:` is **plausibly reachable** and
   that its documented models actually respond — as a test that can run offline
   against recorded evidence, plus a documented way to refresh the evidence. A
   test that requires the network is not a CI test, and a test that requires a
   credit is not a test at all.

6. **Report the route taken.** When a run is served by a model other than the one
   requested, that must be visible and attributable. A research agent found the
   run inspector was showing the *configured alias* under a label reading "model
   that served the run" — a confident false fact. Do not reproduce that.

=====================================================================
FILES YOU OWN
=====================================================================
- `backend/packages/harness/alpha/models/**`
- `backend/app/gateway/routers/console.py` — ONLY the model/routing/cost
  functions, nothing else in that file
- `config.example.yaml` — ONLY the `model_routing:`, `free_gateways:`,
  `model_catalog:` and `models[]` blocks
- NEW `backend/tests/test_model_routing_survives_withdrawal.py`
- NEW `backend/tests/test_model_gateway_reachability.py`
- NEW `docs/MODEL_RESILIENCE.md`

DO NOT EDIT — owned by agents running RIGHT NOW:
- `alpha/config/app_config.py` and `alpha/config/model_config.py`
  (specialists agent) — if you need a config schema change there, report it
- `alpha/runtime/**`, `app/gateway/deps.py`, most of `routers/**` (wiring agent)
- `alpha/teams/**`, `alpha/swarm/**` (team-runtime agent)
- `alpha/agents/middlewares/**`, `alpha/sandbox/security/**` (octop-steal agent)
- `alpha/subagents/**`, `alpha/bots/**`
- `alpha/errors/**`
- `frontend/**`, `contracts/**`, `config.yaml` (the operator's live gitignored
  file — never touch it)
- `scripts/generate_docs_index.py`, `docs/INDEX.md`

=====================================================================
HARD CONSTRAINTS — these have been violated; read them twice
=====================================================================
- WORK ONLY INSIDE YOUR WORKTREE. `C:/Users/PREM KUMAR/Videos/alpha` is shared
  and three agents wrote into it despite explicit instructions. Never touch it.
- `rg` IS BROKEN HERE. Its chocolatey install cannot find `rg.exe`, so it
  silently returns NOTHING. Two investigations were dispatched on false premises
  because of it. Use `Select-String`.
- NEVER rewrite a file with a shell text pipeline. `Get-Content | Set-Content`
  reads UTF-8 as cp1252 and DESTROYS every non-ASCII character. Use the editor,
  or Node with explicit `encoding: "utf8"`.
- Do NOT run `make dev`, `uvicorn`, or `next dev`. Do NOT bind ports 2026, 3000
  or 8001. A stack is owned by the lead.
- **Do NOT add a paid dependency.** The configured OpenRouter key has no credits
  and answers HTTP 402. A recommendation that needs a key is worthless here
  unless you mark it explicitly as requiring one.
- Do NOT commit to main. Do NOT push. Do NOT `git stash`. Do NOT create or
  remove any worktree other than your own.
- WRITE YOUR REPORT INCREMENTALLY to `docs/MODEL_RESILIENCE.md`.

=====================================================================
VERIFY BEFORE REPORTING — and prove every guard bites
=====================================================================
- `cd backend; $env:PYTHONPATH="."; .venv/Scripts/python.exe -m pytest -q` on
  every test you touched, plus `tests/test_model_config.py`,
  `tests/test_model_catalog_consistency.py`,
  `tests/test_credit_exhaustion_failover.py`,
  `tests/test_reasoning_effort.py`,
  `tests/test_single_model_config_file.py`,
  `tests/test_harness_boundary.py`.
- Prove these four by breaking each and showing the test FAIL:
    * a 429 treated as an outage        -> the classification test fails
    * a routing chain that filters to nothing silently falls back to a default
                                      -> the no-invented-route test fails
    * a dead gateway left in the config -> the reachability test fails
    * an unfallbackable default model   -> the withdrawal test fails
  A guard you have never seen fail is not a guard. Two agents in this project
  shipped tests that could not fail and had to be caught and rewritten.
- `cd backend; .venv/Scripts/python.exe -m ruff format --check` on what you changed.
- **Prove `config.example.yaml` still loads** through the real loader, with
  `ALPHA_CONFIG_PATH` pointed at a copy of it. A template that fails its own
  schema is a disaster on first run.
- `python scripts/generate_docs_index.py` must exit 0 — add the FILE_OVERRIDES
  entry for MODEL_RESILIENCE.md.

=====================================================================
DELIVERABLE — write to docs/MODEL_RESILIENCE.md as you go
=====================================================================
1. The routing table you shipped, and what each category actually buys.
2. The withdrawal-survival path, traced end to end, with the file:line of every
   step.
3. Your error classification: the distinct conditions and how each is detected.
4. Per dead entry (persorai, llm7 gpt-oss, the 403 gateway): what you did and
   why, and which of the three options you chose.
5. Every test with its bite proof, per the four mutations.
6. **What is still a single point of failure.** State it plainly. If the
   remaining fallback set is one keyless anonymous model, then that is the honest
   answer and the document must say so rather than implying a resilient chain.
Do not commit. Do not push.
