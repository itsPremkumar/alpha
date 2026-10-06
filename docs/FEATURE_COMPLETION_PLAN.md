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

### 0.T1 Correct the generated-count drift

**Why:** the repository's own documentation contradicts itself on capability
counts, and `docs/DISCOVERABILITY.md` makes finding an unclassified document fail
closed — while several documents carry stale numbers.

Measured drift against the manifest:

| Location | Claims | Actual |
| --- | --- | --- |
| `backend/AGENTS.md:31` | 135 tools | 136 |
| `docs/DISCOVERABILITY.md:141` | 135 tools | 136 |
| `docs/audits/FULL_VERIFICATION_REPORT.md:198,481` | 135/135 tools | 136 |
| root `AGENTS.md:20`, `docs/DISCOVERABILITY.md:116` | 117 engines | 118 |
| `packages/harness/alpha/groups/AGENTS.md:277` | 116 engines | 118 |
| `README.md`, `llms.txt`, `llms-full.txt`, `docs/COMPARISON.md` | 136 / 117 | correct |

- [ ] Update every stale count above.
- [ ] Gate: `python scripts/generate_docs_index.py` writes no diff;
      `backend/tests/test_feature_manifest_wiring.py` and
      `test_no_orphan_modules.py` pass.

### 0.T2 Remove the stray directory that inflates the engine count

**Why:** `backend/packages/harness/alpha/backend/packages/` is an **empty stray
directory tree**. `collect_engines()` counts any directory with submodules, so a
regeneration today emits **119** engines, not 118. The count is wrong twice
depending on when you look.

Also stray: `find_re.sh`, `parse_scan2.py`, `url_probe2.py` at the `alpha/` root.

- [ ] Delete the empty tree and the three stray scripts.
- [ ] Regenerate the manifest; confirm the engine count.
- [ ] Gate: manifest regenerated and committed; counts in 0.T1 match it.

### 0.T3 Fix the stale comment and the broken gate

- [ ] `app/gateway/autonomy/supervisor.py:198` says "all eight loops" while nine
      are registered in `register_default_loops()` (`supervisor.py:126-142`).
- [ ] `.pre-commit-config.yaml:27-32` runs `npx eslint`, which **cannot pass**:
      the frontend has no `eslint.config.*`, declares `"lint": "pnpm typecheck"`
      (`frontend/package.json:12`), and has no ESLint dependency. ESLint 9+
      hard-fails. Every commit touching `frontend/` is refused. Full analysis in
      `docs/audits/FRONTEND_PRE_COMMIT_HOOK.md`.
- [ ] Gate: a frontend commit passes the hook, and `tsc --noEmit` is still the
      gate it enforces.

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

- [ ] Decide the executor: reuse the ordinary `task` executor, or a distinct deep
      one. Record the decision and why.
- [ ] Bind a real runner at assembly, with the same lifecycle, lease and budget
      rules the ordinary path uses.
- [ ] Make the SUBAGENT paradigm's `failed` mapping depend on the delegation
      result rather than on an unconditional refusal.
- [ ] Negative-control: unbind the runner and confirm `UNRECOVERABLE_ERROR`
      returns with its reason intact.
- [ ] Gate: a real `delegate_to_deep_agent` call executes and returns a real
      result; the planning bridge's SUBAGENT paradigm stops failing by
      construction.
- [ ] Record in the audit file that `deep_agent` is real.

### 3.T2 The subagent control plane has no runner

**This is the user's original request, and it is the reason the Subagents panel
says `ready` forever.** Full analysis: `docs/audits/SUBAGENT_VISIBILITY.md`.

Measured: `SubagentLifecycleManager.start_subagent` has **no production caller**.
The only production lifecycle call is `spawn_subagent` from the `/subagent:spawn`
slash command. So a spawned record is created at `ready` and nothing transitions,
heartbeats, or completes it.

Two paths, wired to unrelated surfaces:

| Path | Really executes? | Reaches the UI's live plane? |
| --- | --- | --- |
| `task` delegation | **Yes** — 3 artifacts on disk | **No** — never registers |
| `/api/subagents/control/spawn` | **No** | Yes, registers at `ready` |

- [ ] Implement the runner: `start_subagent` on dispatch, heartbeat on the lease,
      complete with the real result, mark failure with the real reason.
- [ ] Have `task` register each dispatch **and maintain its heartbeat**.
- [ ] Reject the naive version explicitly: registering without heartbeats makes
      every row decay to `stalled` without ever having been stalled, which is a
      fabricated measurement.
- [ ] Gate: a delegated subagent appears in `/api/subagents/control` as `running`,
      transitions to `completed` with a result, and the UI shows all three states
      from real records.

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
- [ ] 0.T1 Correct generated-count drift
- [ ] 0.T2 Remove the stray directory inflating engine count
- [ ] 0.T3 Fix the stale loop comment and the broken pre-commit gate

### Phase 1 — registered-but-dead
- [ ] 3.T1 Deep-agent delegation: bind a real runner
- [ ] 3.T2 Control-plane runner: start, heartbeat, complete
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
| 2026-10-06 | Deep-agent runner | — | **not started** |
| 2026-10-06 | Prompt-only assignment | — | **not started** |
