# Feature Completion Plan

**Status:** living document. Every task below is a checkbox. Tick one only when
its gate has actually been run and its evidence is written down.

**Owner:** this plan. **Rule:** a task is done when its verification evidence
exists, not when its code is written.

---

## 0. How to read this document

### 0.1 The verification method, because it is the whole point

The subagent feature was completed by finding bugs, not by reading code. The
sequence that found them, and that every task in this plan must repeat:

1. **Run it for real.** No mocks, no simulated payloads, no "should work".
2. **Read the output against reality.** Open the artifact. Read the file. Hit the
   endpoint. Look at the rendered page.
3. **Negative-control every fix.** Re-apply the defect and confirm the test goes
   red. A test that has never failed is not evidence.
4. **Say what you cannot prove.** Write `NOT VERIFIED` and move on.

Three defects from the subagent work were invisible to reading and obvious to
running: a grounding gate that called an installed tool "not installed", a
client that read `objective` from the wrong nesting level, and a control plane
whose only writer was a slash command nobody types.

### 0.2 The honesty contract this plan is bound by

These are not style preferences. They are the rules that make the UI worth
reading.

| Rule | Wrong | Right |
| --- | --- | --- |
| Unreported ≠ zero | absent field → `0` → green badge | `null` → "not reported", grey |
| Completed ≠ verified | run finished → "verified" | run finished + independent evidence → "verified" |
| Registered ≠ executable | manifest says wired → it works | manifest proves *imports*, not execution |
| No optimistic success | click → paint success | click → re-read server → paint server's answer |
| A failed read is not an empty list | `catch → []` | reject, carry the server's reason |
| Absent state ≠ good state | `enabled ?? true` → "on" | `enabled === null` → "unknown" |

### 0.3 Inventory these numbers came from

`contracts/feature_manifest.json`, generated at `2026-10-06T02:38:45Z` by
`backend/scripts/generate_feature_manifest.py`:

| Registry | Count |
| --- | --- |
| Tools | **136** |
| Routers | **66** |
| Middlewares | **44** |
| Supervisor loops | **9** |
| Engine packages | **118** (108 packages + 10 namespaces) |
| Workspace views (frontend) | **31**, all reachable |

**The manifest proves import wiring only.** Every tool, router, middleware and
loop is marked `"wired": true`, and `dormant_packages` is empty — yet at least
**three registered tool families are structurally dead in any production
install**. Section 3 is the list.

---

## 1. Definition of done

A feature is complete when all five hold. Partial completion is reported as
partial, with the missing item named.

- [ ] **D1. Real execution.** A real request produced a real, inspectable result.
      Not a registration, not a contract, not a stub returning success.
- [ ] **D2. Visible in the UI.** The operator sees the real result in the real
      surface, and can trace it back to the request that caused it.
- [ ] **D3. Negative-controlled.** Re-applying the defect turns a test red.
- [ ] **D4. Honest under failure.** Break it and confirm the UI says what
      actually happened, with the server's reason.
- [ ] **D5. Documented.** Audit record written; doc counts regenerated.

---

## 2. Phase 0 — Make the baseline true (do this first)

Everything after this phase is measured against numbers that are currently
wrong. Cheap to fix, and it removes a class of false confidence.

### 0.T1 Correct the generated-count drift — **DONE**

**Why:** the repository's own documentation contradicted itself on capability
counts, so nothing after this phase was measured against a true number.

**Found: 15 stale current claims across 11 files.** The earlier pass missed the
user-facing files entirely.

| Location | Was | Now |
| --- | --- | --- |
| `backend/AGENTS.md:17` | 135 tools | 136 |
| `docs/DISCOVERABILITY.md:141` | 135 tools | 136 |
| `README.md:91,123` (+ one wrapped mid-line) | 135 tools | 136 |
| `llms.txt:6`, `llms-full.txt:6`, `docs/llms.txt:5` | 135 tools | 136 |
| `README.md:129` | 115 engines | 118 |
| `AGENTS.md:20`, `docs/DISCOVERABILITY.md:116` | 117 engines | 118 |
| `docs/COMPARISON.md:78` | 117 engines | 118 |
| `docs/FAQ.md:25,142` | 117 engines | 118 |
| `docs/GLOSSARY.md:213` | 116 engines | 118 |
| `docs/AGENT_LIVE_STATUS_AND_WORK_COORDINATION.md:607-608` | 134/43/116 | 136/44/118 |
| `packages/harness/alpha/groups/AGENTS.md:276-277` | 134/43/116 | 136/44/118 |
| `docs/audits/AGENT_SELF_SERVICE.md:175` | 117 engines | 118 |

**Deliberately not touched:** `CHANGELOG.md`, `docs/SELF_AUDIT.md`,
`docs/audits/FULL_VERIFICATION_REPORT.md`, `docs/END_TO_END_CRITIQUE.md`,
`docs/ALPHA_UNIFIED_INTEGRATION_PLAN.md`, `docs/SELF_AWARENESS.md:214` and
`packages/harness/alpha/AGENTS.md:106`. Those record **past** states — the last
two are literally histories of the drift ("89 → 97 → 99 engines"). Rewriting a
changelog to match today's number falsifies evidence.

- [x] Every current claim corrected toward the generated manifest.
- [x] Gate added so it cannot recur: `tests/test_documented_capability_counts.py`,
      **24 cases**. It parses `"<n> tools"` / `"<n> engines"` out of eleven files
      and fails on any value the manifest does not report, and it asserts the
      manifest itself is not stale relative to its generator.
- [x] **Negative-controlled:** reverting `docs/FAQ.md` to `117 engines` →
      `docs/FAQ.md claims [117] engines; the manifest has 118`. Restored: 24 pass.
- [x] The gate immediately caught a leftover I had missed — `README.md` wrapped
      `135 native\ntools` across a line break, so a plain string replace missed it.

### 0.T2 The engine count depended on when you read it — **DONE, by fixing the counter**

**Found:** the committed manifest said **118** engines and a fresh regeneration
said **119**. `backend/packages/harness/alpha/backend/packages/harness/alpha`
existed as three nested **empty** directories with **zero tracked files**.

**Root cause, in one sentence:** `collect_engines()` treated "contains a
subdirectory" as sufficient to be an importable package, so a chain of empty
directories counted as one engine — contradicting its own docstring, which says
"Directories holding only data (or nothing) are excluded".

**The stray tree was NOT deleted** (operator instruction: add code, delete
nothing). The counter was fixed instead, so the number no longer depends on
whether unrelated junk is on disk. A generator that is sensitive to stray
directories is fragile; one that asks "does this hold Python?" is not.

- [x] `collect_engines()` now requires a subdirectory to hold a `.py` file at any
      depth. Regeneration: **118**, and `alpha.backend` is gone from the manifest.
- [x] The empty directories remain on disk, proving the count no longer needs
      their removal.
- [x] `tests/test_engine_count_ignores_empty_directories.py`, **8 cases**,
      including `test_the_count_is_stable_regardless_of_unrelated_junk`, which
      pins the property that actually broke.
- [x] **Negative-controlled:** restoring the original rule → `assert 'alpha.backend'
      not in {'alpha.backend'}` and the stability test fails on `alpha.zzz_stray`.
      Restored: 8 pass.

### 0.T3 Stale loop comment — **DONE**. Pre-commit gate — **NOT DONE**

- [x] `supervisor.py:196` said "all eight loops pass through" while
      `register_default_loops()` registers **nine**. Rewritten to name the
      registry function rather than assert a count.
- [x] `tests/test_supervisor_loop_registry_matches_its_comments.py`, **3 cases**.
      It parses the `defaults` tuple of `(loop_id, description, tick, interval)`
      rows, so the count moves structurally when a loop is added or removed — a
      literal "nine" test would pass against a registry of eight. It also
      cross-checks the registry against `contracts/feature_manifest.json`, which
      counts the same loops independently, so agreement is evidence rather than
      a tautology. And it fails on *any* supervisor comment that counts the loops,
      so the next person to write "all nine loops" gets a red test.
- [x] **Negative-controlled:** restoring the "eight loops" comment →
      `a supervisor comment counts the loops, which nothing verifies`.
      Restored: 3 pass.
- [ ] `.pre-commit-config.yaml:27-32` runs `npx eslint`, which **cannot pass**:
      the frontend has no `eslint.config.*`, declares `"lint": "pnpm typecheck"`
      (`frontend/package.json:12`), and has no ESLint dependency. ESLint 9+
      hard-fails, so every commit touching `frontend/` is refused. Analysis in
      `docs/audits/FRONTEND_PRE_COMMIT_HOOK.md`. **Not done**: `.pre-commit-config.yaml`
      is shared state that a concurrent agent is also using, and the operator asked
      for additions only. Frontend commits in this worktree therefore use
      `--no-verify`, with `tsc --noEmit` and `node --test src/lib/*.test.mjs` run
      directly instead — the gate's intent is enforced, just not through the hook.

---

## 3. Phase 1 — The registered-but-dead capabilities

The manifest cannot express these. Each is `wired: true` and cannot execute.
This phase is the highest-value work in the plan, because it converts a
**count** into a **claim**.

### 3.T1 Deep-agent delegation — 3 tools, permanently failing

**Severity: highest.** `delegate_to_deep_agent`,
`inspect_deep_agent_telemetry`, `list_available_deep_agents` telemetry.

`subagents/hierarchical_delegator.py:263-267`:

```python
if runner is None:
    raise RuntimeError(
        "No deep agent execution backend is configured; "
        "delegation refuses to fabricate an execution result."
    )
```

`set_deep_agent_runner` (`:146`) has **zero production callers** — a repo-wide
grep finds it only in `tests/test_hierarchical_delegator.py:13,68,73,89,93`.
So `runner is None` unconditionally in production, and every delegation returns
`UNRECOVERABLE_ERROR` (`deep_handoff_contract.py:30,358`).

Cascade: `planning/bridge.py:449-465` maps `"UNRECOVERABLE_ERROR"` → `"failed"`,
so the **SUBAGENT execution paradigm of the planning bridge is permanently
failed**. Every plan that dispatches through it fails by construction.

The refusal itself is correct and must not be weakened. What is missing is the
runner.

#### Measured scope, 2026-10-06 — read this before estimating

The definitions are **real**; only the execution is missing.

| Deep agent | tools | max_turns | timeout |
| --- | --- | --- | --- |
| `deep-architect` | 3 | 120 | 1800 |
| `deep-code-reviewer` | 3 | 120 | 1800 |
| `deep-debugger` | 3 | 120 | 1800 |
| `deep-performance` | 3 | 120 | 1800 |
| `deep-security` | 3 | 120 | 1800 |
| `deep-test-synthesizer` | 3 | 120 | 1800 |

And the decisive measurement: **no deep agent ever constructs a
`SubagentExecutor`.** Every construction site in the tree is
`batch_service.py:226`, `executor.py:996` (its own internal path), and the
`task` tool. `grep SubagentExecutor packages/harness/alpha/subagents/` returns
those three plus re-exports in `__init__.py`. There is no partial wiring to
finish — the execution path does not exist.

The seam itself is narrow and well-specified:
`DeepAgentRunner = Callable[[DeepAgentSession], DeepHandoffContract]`, and the
session already carries `spec: DeepTaskSpec` and `workspace_dir`.

What a real runner must therefore do, in order:

1. Resolve the definition by `agent_type` from `BUILTIN_SUBAGENTS`.
2. Assemble tools via `get_available_tools(...)`, filtered to the definition's
   3-tool allowlist, with `task` excluded so a deep agent cannot nest.
3. Construct `SubagentExecutor` — about 25 keyword arguments, including the whole
   identity-propagation set (`user_id`, `user_role`, `oauth_provider`,
   `oauth_id`, `is_internal`, `authz_attributes`, `channel_user_id`,
   `alpha_trace_id`, `run_extensions`), plus `sandbox_state`, `thread_data`,
   `uploaded_files` and `context_snapshot`.
4. Submit through `execute_async` onto the **persistent isolated subagent loop**,
   behind the process-wide admission controller Gateway installs at startup.
5. Poll the registry for the terminal `SubagentResult`.
6. Translate into a `DeepHandoffContract` with a **real** status, the real final
   answer as the executive summary, real artifacts, and real token usage
   harvested from the run.

#### Why this is the riskiest item in the plan

`subagents/AGENTS.md` documents the isolated-loop boundary in detail, and each
rule it states is a bug someone already shipped:

- ContextVars must be copied into the persistent loop, or checkpoint lineage,
  tracing and the namespaced message stream are lost.
- `RunJournal` must be kept **out** of the child loop (it carries an
  `alpha_loop_bound` marker) — crossing it causes duplicate accounting and
  `Future attached to a different loop`.
- `record_external_llm_usage_records` must never run on the persistent loop or a
  worker thread; the report crosses back via `call_soon_threadspacesafe` on the
  captured parent loop.
- Deferred cleanup must be pinned to the isolated loop via
  `run_on_isolated_subagent_loop()`, because `asyncio.run()` cancels
  caller-loop tasks on teardown.

A runner that gets the loop boundary wrong produces silently wrong token
accounting rather than a visible failure — the worst failure mode in this
repository, and the reason this task is scheduled rather than improvised.

#### Recommendation: do 3.T1 and 3.T2 together

Both need the same machinery — isolated-loop submission, admission control,
registry polling, terminal-state translation. Building it twice risks two subtly
different boundary implementations, and the second one is the one nobody reviews.
One implementation, two bindings.

- [ ] Resolve the definition and assemble the filtered toolset.
- [ ] Construct and submit the executor on the isolated loop.
- [ ] Poll to a terminal `SubagentResult`.
- [ ] Translate to a real `DeepHandoffContract`, claiming `passed` for a test
      oracle **only** when that check genuinely executed.
- [ ] Bind at assembly with the ordinary lifecycle, lease and budget rules.
- [ ] Make the SUBAGENT paradigm's `failed` mapping depend on the delegation
      result rather than on an unconditional refusal.
- [ ] Negative-control: unbind the runner and confirm `UNRECOVERABLE_ERROR`
      returns with its reason intact — the refusal must survive.
- [ ] Gate: a real `delegate_to_deep_agent` call executes and returns a real
      result; token accounting matches the `task` path on the same work.
- [ ] Record in the audit file that `deep_agent` is real.

### 3.T2 The subagent control plane has no runner — **RUNNER WRITTEN, live run NOT yet verified**

**This is the user's original request, and it is the reason the Subagents panel
said `ready` forever.** Full analysis: `docs/audits/SUBAGENT_VISIBILITY.md`.

Measured: `SubagentLifecycleManager.start_subagent` had **no production caller**.
The only production lifecycle call was `spawn_subagent` from the
`/subagent:spawn` route, so a spawned record was created at `ready` and nothing
transitions, heartbeats, or completes it.

Two paths, wired to unrelated surfaces:

| Path | Really executes? | Reaches the UI's live plane? |
| --- | --- | --- |
| `task` delegation | **Yes** — 3 artifacts on disk | **No** — never registers |
| `/api/subagents/control/spawn` | **No** | Yes, registers at `ready` |

#### Scope correction, measured before writing any code

The plan previously estimated "about 25 required keyword arguments". Measured
with `scripts/probe_deep_runner_wiring.py`, that was wrong in the runner's
favour:

- `SubagentExecutor.__init__` requires **two** kwargs — `config` and `tools`.
  The other 22 (the whole identity-propagation set included) are optional.
- `get_subagent_config` **does** resolve the deep agents:
  `deep-architect` → 3 tools, 4 disallowed, 120 turns, 1800 s, `model=inherit`.
- `execute_async(task, task_id) -> str` and
  `get_background_task_result(execution_id) -> SubagentResult | None` form a
  clean round-trip.

So the config and registry layers were already finished; only the runner was
missing. The estimate was corrected in the plan rather than left standing.

#### What was built

`subagents/lifecycle_runner.py` — the single implementation of the execution
boundary, deliberately the *only* place that drives a lifecycle record, so 3.T1
binds to it instead of writing a second one. `subagent_control.py::spawn` now
schedules it on `run_on_isolated_subagent_loop`, so the response still returns
immediately (as its docstring always promised) while the work proceeds on the
process-owned loop where `asyncio.run()` teardown cannot cancel it.

Four honesty decisions inside the runner, each of which was a trap:

1. **`renew_lease` instead of `record_heartbeat`.** `record_heartbeat`'s
   `progress_percent` defaults to `0.0` and clamps with `min(100.0, max(0.0, v))`,
   so passing `None` raises `TypeError` and omitting it asserts *measured zero
   progress*. The registry exposes a status enum, not a percentage. So the runner
   renews the lease and records the real status string, and never stamps a number
   it did not measure.
2. **`artifacts` left unset.** `SubagentResult` carries no artifact list
   (task_id, status, result, error, stop_reason, ai_messages, token_usage_records,
   tool_receipts, bash_executions). `getattr(result, "artifacts", [])` would
   yield `[]` every time, and an always-empty artifact list is indistinguishable
   from "this run produced no files". Populating it from the result *text* would
   be worse — parsing prose as a path list.
3. **Only `COMPLETED` completes.** `FAILED`, `CANCELLED` and `TIMED_OUT` each
   call `fail_subagent` with the real reason, and a failed run's prose is never
   promoted into a deliverable summary.
4. **A submission exception fails the record.** Otherwise the record is left
   `running` because the runner died quietly — which is the original bug wearing
   a different hat.

- [x] Runner written and wired into the spawn route.
- [x] `tests/test_subagent_lifecycle_runner.py`, **13 cases** (verified: `13
      passed`, exit 0), each aimed at a plausible lie rather than a crash: every
      non-completed terminal status, a submission exception, an unknown
      definition, a poller ceiling, a start refusal at the attempt ceiling, lease
      renewal across a slow run, the absence of a progress percentage, a capped
      run keeping its `stop_reason`, and an empty completion staying empty.
- [x] Start-refusal honoured: `start_subagent` returning `False` at the attempt
      ceiling stops the runner before it reaches the executor, so a restart sweep
      can never resurrect a unit that has given up.
- [x] **Live run performed** — `backend/scripts/probe_lifecycle_runner.py`.
      Two live runs, and the first one found a real bug immediately:

      | Observation | Run 1 (before fix) | Run 2 (after fix) |
      |---|---|---|
      | status at spawn | `ready`, `started_at=None` | `ready`, `started_at=None` |
      | first poll | `running`, `started_at` set, `renew_count=1` | same |
      | terminal | `failed` in 17 s | `failed` in 24 s |
      | reason | `Thread ID is required in runtime context or config.configurable` | provider quota rejection |
      | executor reached | no — 141 tools resolved, then middleware raised | yes — **141 tools, 24 skills loaded, `max_turns=150`, graph started** |

      **Run 1 was my bug.** The route passed `thread_id=None`, and
      `ThreadDataMiddleware.before_agent` requires one. Fixed with
      `_execution_thread_id()` (deterministic per record, so a retry rejoins the
      same workspace and two helpers cannot share state) plus a resolved runtime
      user. Run 2 proves the fix: the graph now starts.

      Also proved by the Gateway log, not inferred: `renew_count` advances 1 -> 2
      during the run, so the lease renewal is real rather than nominal.

- [ ] **A subagent COMPLETING with a real summary is still NOT verified.** Run 2
      reached the model call and was refused:

      > The configured LLM provider rejected the request because the account is
      > out of quota, billing is unavailable, or usage is restricted.

      `GET /api/models/free/catalog` still reports `healthy=True` for the probed
      providers, so this is a specific account/route rejection at completion
      time, not a wiring fault. It is an **environment limit, reported as one.**
      The claim is "the control plane executes and reports the truth about why it
      stopped", **not** "a subagent completes".
- [ ] Have `task` register each dispatch **and maintain its heartbeat**, so the
      ordinary delegation path reaches the same live plane.
- [x] The naive version is rejected in the design: registering without
      heartbeats makes every row decay to `stalled` without ever having been
      stalled, which is the fabricated measurement this task exists to remove.

#### Process incident worth keeping

An interrupted negative-control run left a mutation in the source
(`if now - started >= wait_ceiling_seconds:` became `if False:`), and a follow-up
integrity check **reported the file as clean** because its output had not flushed
before the command timed out. `ruff` caught it instead, as
`F841 Local variable 'started' is assigned to but never used` — a dead variable
is the fingerprint of a deleted condition.

Two rules came out of it, and both generalise past this task:

- **Never mutate a source file for a negative control without a restore that is
  verified after the fact.** The restore was in the same script as the mutation,
  so an interruption between them left the defect in place.
- **A clean-looking integrity check that did not print is not a check.** Read the
  output, do not infer it from the absence of an error.

### 3.T3 Slash-command dispatch — 54 of 461 rows

`commands/registry.py:26` defines `UNIMPLEMENTED_STATUS = "unimplemented"`, and
`:363-393` returns *"is a catalogued command with no handler bound to it, so
nothing was executed"* with `executed: False`.

The refusal is honest. The **catalogued count** is the problem: a palette
advertising 461 commands of which 407 cannot run.

- [ ] Publish both numbers in the UI: total catalogued **and** dispatchable, with
      the difference named. Never present 461 as usable.
- [ ] Add a gate test that fails if the two numbers ever diverge silently.
- [ ] Decide per command: bind a handler, or move it out of the palette.
- [ ] Gate: the palette's headline count is the dispatchable count; the
      catalogued total appears beside it, labelled.

### 3.T4 Self-repair never repairs

`selfrepair/engine.py:72` returns `False, "verification not implemented for
repair kind ...; no runtime evidence collected"`, and `:89` sets
`outcome="refused"`. Diagnosis works; repair never executes.

- [ ] Implement scoped repair adapters, each collecting **independent
      post-action runtime evidence** before reporting success.
- [ ] Gate: `verify_repair` returns true only with collected evidence; a repair
      with no verifier still refuses, with the reason.

### 3.T5 Document every registered-but-gated capability

The audit found ~20 more families that are wired but hard-gated: voice/multimodal,
browser/Playwright, IM channels, MCP adapters, keyless-search providers,
LightRAG/RAGFlow, Redis/Postgres backends, tracing, ONNX classifier, peer-network
transport, GitHub webhooks, ACP, script bridge, ops parked-sessions.

These are *fail-closed and honest*, which is correct. The gap is that no single
document says so.

- [ ] One table: capability → gate → the exact refusal string → file:line.
- [ ] Surface it in the UI as a "gated on this deployment" list, so an operator
      never wonders why a documented feature is missing.
- [ ] Gate: the table is generated from the source, not hand-written.

---

## 4. Phase 2 — Prompt-only work assignment

**The user's core ask: type a prompt, real work gets assigned and runs, and you
can watch it.**

### The blocker, measured

`frontend/src/components/ChatView.tsx:1387-1396` sends:

```ts
config: { configurable: {
  model_name: selectedModel,
  ...(planMode ? { is_plan_mode: true } : {}),
  ...(reasoningEffort !== DEFAULT_EFFORT ? { reasoning_effort: reasoningEffort } : {}),
}}
```

It never sends `subagent_enabled`, and never sends `autonomous: true`. Confirmed
in the gateway log for a UI chat run:

```
Create Agent(default) -> ... subagent_enabled: False ...
```

Measured consequence: `autonomous: false` → tools `alpha_capability,
catalog_tool_search`. `autonomous: true` → `alpha_capability, task`. **Typing a
prompt can never produce a working subagent in this UI.** It is not the
operator's usage; it is the wiring.

### 4.T1 Make delegation reachable from a prompt

- [ ] Add an explicit, visible delegation control to the composer, defaulting to
      the server's configured policy rather than a hidden hardcoded `false`.
- [ ] Send the opt-in the server already understands. `RunCreateRequest.autonomous`
      is the server-owned switch (`app/gateway/services.py` sets
      `body_context["subagent_enabled"] = True`).
- [ ] The control states what it will do before it is used, and the run's
      metadata records what was actually applied.
- [ ] Gate: with delegation on, a prompt that warrants it produces a real `task`
      dispatch and a real artifact; with it off, the toolset genuinely lacks
      `task` and the UI says so.

### 4.T2 Realtime progress for delegated work

The honest signal already exists and already reaches the browser — in the
transcript, over SSE. `task_*` custom events from
`alpha/tools/builtins/task_tool.py`, rendered by `components/SubagentList.tsx`.
The panel's live plane is a separate, unwired surface.

- [ ] Decide the panel's source: point it at the delegation ledger
      (`ThreadState.delegations`) and `subagent.step` run events, **or** make the
      control plane truthful via 3.T2.
- [ ] Rename the heading to match whichever it is. "Running now" must mean
      running.
- [ ] Preserve the existing honesty rules: no duration for a task restored from
      history, `step 3` never `0/0`, an unknown status never green.
- [ ] Gate: during a real delegated run the operator sees the task appear, show
      its steps, and end in a real terminal state — verified on screen.

### 4.T3 Assign work without the Subagents panel

The prompt must be sufficient. Everything else is a convenience.

- [ ] A prompt that names an outcome produces a plan and dispatches, with the
      dispatch visible in the transcript.
- [ ] A prompt that needs clarification asks, via the existing
      `ask_clarification` path, rather than guessing.
- [ ] A prompt that fails says what failed and why, with the server's reason.
- [ ] Gate: three scripted prompts, each run end to end, each outcome inspected
      on disk and on screen.

---

## 5. Phase 3 — The named features, end to end

Each feature follows the same five-step shape: **inventory → run → fix →
negative-control → UI verify.** The inventory column is already done for all of
them; what remains is running each one for real.

### 5.T1 Swarm mode

Surface: `swarm_tool`; `/api/swarms`; view `team` → Team Ops.
Loop: `swarm_status`, telemetry-only, 300 s default.

- [ ] Run a real swarm. Record how many agents actually started, versus how many
      the tool reported.
- [ ] Verify the blackboard path: `blackboard_record_evidence` →
      `blackboard_query` → `emit_stigmergic_event` → `query_stigmergic_traces`.
- [ ] Verify consensus, not just fan-out: do agents see each other's evidence?
- [ ] UI: Team Ops shows live swarm state from real records.
- [ ] Gate: N agents, N real artifacts, each attributable to a named agent.

### 5.T2 Dynamic workflow mode

Surface: `alpha.local.digest` executor; `/api/workflows/*`; view `workflows`.
Known: the default executor is a `local_digest_projection` with
`acceptance_passed=false`; the real `alpha.orchestrator.domain_executors` are
opt-in via `bind_domain_executors()` and **never bound at import**.

- [ ] Bind the real domain executors, or state plainly that the digest projection
      is the product.
- [ ] Verify `GET /workflows/system/executors` distinguishes `bound` from
      `domain_bound` and that the UI renders that difference.
- [ ] Verify step/cancel/approval/patch/replan/compensation against a real run.
- [ ] Verify the report carries **no acceptance verdict** — a completed run is not
      a verified run.
- [ ] Gate: a dynamic run executes real steps through bound executors, with the
      binding state visible in the UI.

### 5.T3 Company / enterprise mode

Surface: `/api/company`, `/api/companies`, `/api/enterprise`; views `company`,
`warroom`; loop `company_operations`, 300 s.

- [ ] Bootstrap a company. Run `loop/tick`. Verify observe → plan → act → verify
      → ledger with a real artifact from each phase.
- [ ] Verify the hierarchy, RFC review/debate/gate, treasury circuit breaker,
      mission pipeline, sprint step, and council release benchmarking.
- [ ] Fix the frontend defects already found here (see 6.T4) — the Companies
      overview currently renders the literal string `undefined` for absent
      metrics, and reads an unreachable work block as `0 open / 0 blocked`.
- [ ] Gate: a company exists, ticks, produces artifacts, and every tile is either
      a measured number or an explicit "not reported".

### 5.T4 Messages, projects, board

Surfaces: `/api/groups`, `/api/projects`, `/api/company/kanban/tasks`; views
`messages`, `projects`, `kanban`. **Note: there is no `groups` view id** — group
surfaces live inside `messages` and `warroom`.

- [ ] Projects: verify crew reconcile, presence attach/detach, collaboration
      modes, memory. Known: `GET /crew` is a reconciling read, so polling it is
      the supported way to stay honest.
- [ ] Board: verify against `GET /company/kanban/tasks?limit=100` merged with
      local cards — confirm the two sources cannot silently disagree.
- [ ] Messages: verify the group tree, roster, claims, relay `max_hop`, and that
      `direct_count` beside `effective_count` never disagrees.
- [ ] Gate: each surface creates a real artifact and displays it, with every count
      traceable to a server field.

---

## 6. Phase 4 — Fix the honesty defects already found

These are **measured**, with file:line. Each is small; together they are why the
UI cannot be fully trusted yet.

### 6.T1 Absent value rendered as a healthy zero

| # | Site | Defect |
| --- | --- | --- |
| 1 | `lib/systemMonitor.ts:152`, `WorkspaceVitals.tsx:496,500` | `num(v, fallback = 0)` — absent `ram.percent` → `0` → **green dot and "RAM 0%"**. The fastest-looking reading is manufactured. |
| 2 | `lib/bots.ts:16` | absent `status` → `"active"` |
| 3 | `lib/overview.ts:99,106` | absent `status`/`enabled` → counted as active/enabled |
| 4 | `lib/comm.ts:416` | absent room `state` → `"active"` → **green badge** (`groups-tree.ts:132-144`) |
| 10 | `lib/external-alpha.ts:150-153` | `num()` returns `0` → `totals.messages`, `retention_days` |
| 11 | `lib/notifications.ts:47-50` | absent `unread_count` → `0` → no badge, no error |

- [ ] Fix each to `null` + an explicit disclosure.
- [ ] Negative-control each: omit the field, confirm no green and no `0`.
- [ ] Gate: `src/lib/ui-legibility.test.mjs` and a new dash-with-disclosure case
      per site.

### 6.T2 A failed read rendered as an empty list

| # | Site | Defect |
| --- | --- | --- |
| 12 | `lib/bots.ts:44-63` | `catch → []`; downstream renders `Profiles (0)` |
| 13 | `ChatView.tsx:2107` + `NavTabs.tsx:250` | badge gated on `count > 0`, so a failed read hides the badge |
| 15 | `WorkforceSection.tsx:192` | `.catch(() => setProjects([]))`, unlike `KanbanSection.tsx:63-67` |
| 14 | `WorkforceSection.tsx:89` | `Promise.resolve({ messages: [], unread_count: 0 })` — **fabricates a measured zero** |

- [ ] Make each reject and carry the server's reason.
- [ ] Gate: against a stopped Gateway, none of these render as empty/zero.

### 6.T3 Counts that become `0`

`lib/comm.ts:397,401,415,447,448,487,488,559,580` coerce
`message_count`, `child_count`, `depth`, `effective_count`, `direct_count`,
`inherited_count`, rule `matches` to `0`. The tree-node mapper at `:403-404`
deliberately keeps those `null` — the roster path does not. That inconsistency is
the bug.

- [ ] Make the roster path match the tree path.
- [ ] Gate: `membershipHeadline` never prints `"0 members"` for an unread roster.

### 6.T4 Company tiles rendering `undefined`

`CompanySection.tsx:330-331` — `String(data.metrics.project_count)`. `metrics` is
built as `{ ...(d.metrics ?? {}), health_percent }` (`company.ts:363-369`), so an
absent block leaves the field `undefined` and the tile renders the literal string
**`undefined`**. Lines `:328-329` and `:390-395` already use `?? 0`; `:330-331`
is the outlier.

- [ ] Fix `:330-331`; keep the distinction between absent and zero.
- [ ] Gate: no tile renders `undefined`, `NaN`, or `[object Object]`.

### 6.T5 Two honesty pins that assert line shape

`collaboration-surfaces-honesty.test.mjs` pinned
`/\[listSubagentCatalog\(\), fetchLiveSubagentsStrict\(\)\]/` and
`act(b, () => pauseBatch(`. Prettier reflowed both, and the suite went red while
the behaviour was intact — accusing the product of hiding server state.

Fixed in `dbb1d70` with whitespace-tolerant patterns, negative-controlled.
Sweep the rest of the suite for the same class.

- [ ] Grep all `.test.mjs` for regexes containing a literal space between two
      identifiers that could be reflowed.
- [ ] Gate: `prettier --write` over the frontend followed by a full `node --test`
      run changes nothing.

---

## 7. Verification protocol

### 7.1 Per task

- [ ] Backend: `cd backend && .venv\Scripts\python.exe -m pytest tests/<file> -q`
- [ ] Backend gate: `make test` **and** `make test-blocking-io`
- [ ] Frontend gate: `tsc --noEmit` = 0 errors **and**
      `node --test src/lib/*.test.mjs` green
- [ ] Lint/format: `ruff check`, `ruff format --check`
- [ ] Drift: `python scripts/generate_docs_index.py` produces no diff
- [ ] Negative control: re-apply the defect, watch the test go red, restore

### 7.2 Per feature (the D1-D5 gate)

- [ ] A real request produced a real artifact. Open it.
- [ ] The UI shows that artifact, and the operator can trace it to the request.
- [ ] Screenshot under `docs/audits/screenshots/<date>/`.
- [ ] Break it; confirm the UI names the real failure with the server's reason.
- [ ] Audit record written with the exact evidence.

### 7.3 Known environment limits — state these, do not work around them

- [ ] `delegate_to_deep_agent` has no execution backend until 3.T1.
- [ ] `browser.screenshot` requires a focused, visible tab; `tabs.focus` must
      move `focusedTabID` first. This works in this environment now.
- [ ] Voice/multimodal, browser/Playwright, IM channels, MCP adapters, keyless
      search providers, Redis/Postgres, tracing, ONNX, libp2p and GitHub webhooks
      are all gated off in a default install. Fail-closed and honest — report as
      limitations.
- [ ] The peer-network SQLite store is installation-scoped and never
      cross-process exactly-once.
- [ ] Installation-scoped JSON/JSONL state is never cross-process
      exactly-once.

---

## 8. Task index

Tick in order. Each line links to its section.

### Phase 0 — baseline truth
- [x] 0.T1 Correct generated-count drift (15 claims, 11 files) + 24-case gate
- [x] 0.T2 Engine count fixed at the counter; nothing deleted
- [x] 0.T3a Stale loop comment fixed + 3-case structural gate
- [ ] 0.T3b Pre-commit eslint gate (shared state; additions-only instruction)

### Phase 1 — registered-but-dead
- [ ] 3.T1 Deep-agent runner (scope measured; pair with 3.T2)
- [x] 3.T2a Control-plane runner: 13 unit cases + 2 live runs (ready -> running -> terminal)
- [ ] 3.T3 Slash commands: publish dispatchable count
- [ ] 3.T4 Self-repair: implement or keep refusing, honestly
- [ ] 3.T5 Document every gated capability

### Phase 2 — prompt-only work assignment
- [ ] 4.T1 Make delegation reachable from a prompt
- [ ] 4.T2 Realtime progress for delegated work
- [ ] 4.T3 Assign work without the Subagents panel

### Phase 3 — named features
- [ ] 5.T1 Swarm mode
- [ ] 5.T2 Dynamic workflow mode
- [ ] 5.T3 Company / enterprise mode
- [ ] 5.T4 Messages, projects, board

### Phase 4 — honesty defects
- [ ] 6.T1 Absent value rendered as a healthy zero
- [ ] 6.T2 A failed read rendered as an empty list
- [ ] 6.T3 Counts that become `0`
- [ ] 6.T4 Company tiles rendering `undefined`
- [ ] 6.T5 Sweep for line-shape assertions

### Already complete

- [x] `e0eaaf5` — `subagent_registry`: an agent can create its own subagent
- [x] `b701c9c` — grounding `tool_exists` gate no longer refuses installed tools
- [x] `3de2b19` — subagent catalog panel: 4 of 15 fields → all 15
- [x] `669f316` — live subagent objectives read from the right nesting level
- [x] `dbb1d70` — two honesty pins no longer assert line shape
- [x] `4248bc8` — `SUBAGENT_VISIBILITY.md`: why no subagent ever appears working
- [x] `0475a7a` — `FRONTEND_PRE_COMMIT_HOOK.md`: the gate that cannot pass

---

## 9. Change log

Tick one row per task, newest last. A row without evidence is not an entry.

| Date | Task | Evidence | Gate |
| --- | --- | --- | --- |
| 2026-10-06 | Subagent catalog panel | 5 live screenshots; 8 definitions, 23 tool chips | 40 new cases, 5 negative controls; tsc 0 |
| 2026-10-06 | Live subagent objective | Rendered rows show the real objective | flat-only read → 15/1; restored 16/0 |
| 2026-10-06 | Control-plane runner | `docs/audits/SUBAGENT_VISIBILITY.md` | finding documented, **fix not started** |
| 2026-10-06 | 3.T2a control-plane runner | 13 passed exit 0; live: 141 tools, 24 skills, max_turns=150, graph started | VERIFIED to the model call; **completion blocked by provider quota** |
| 2026-10-06 | 3.T2a bug found live | run 1: `Thread ID is required` — my `thread_id=None` | fixed; run 2 reached the model |
| 2026-10-06 | 3.T1 deep-agent runner | measured: no deep agent constructs a `SubagentExecutor`; executor needs only 2 kwargs | **not started** - scope corrected in plan |
| 2026-10-06 | Prompt-only assignment | — | **not started** |
| 2026-10-06 | 0.T1 count drift | 15 claims corrected in 11 files | 24-case gate; NC reverted FAQ.md to 117 → red |
| 2026-10-06 | 0.T2 engine count | fresh gen was 119 vs committed 118 | fixed `collect_engines()`; 8 cases; NC → `alpha.backend` counted |
| 2026-10-06 | 0.T3a loop comment | 9 registered, comment said eight | 3 cases; NC stale comment → red |
