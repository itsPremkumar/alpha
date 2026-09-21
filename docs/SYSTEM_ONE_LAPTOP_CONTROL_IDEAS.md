# Reading the laptop-control guide against Alpha

Source: `JEV_Full_Laptop_Control_Architecture_Guide.md` (2026-09-21), a research
guide for a Windows-first autonomous agent with JEV as the decision layer.

This note maps its recommendations onto what Alpha already has, records the three
things I implemented after reading it, and lists what is worth doing next. The
short version: **Alpha already implements the guide's central architecture**, and
reading it surfaced one genuine bug and one genuine gap.

---

## 1. What Alpha already does

The guide's core claim is:

> **JEV decides; deterministic software acts; verification proves the result.**

That is already Alpha's shape, site by site.

| Guide | Alpha |
|---|---|
| §1 JEV as a fast decision layer, not the whole system | `models/system_one.py` + 16 call sites, all with LLM/heuristic fallback |
| §2 `Choice` / `Score` / `Noul` primitives | `ChoiceQuestion` / `ScoreQuestion` / `BooleanQuestion` |
| §3 Excellent use cases: target selection, completion detection, stuck detection, routing, candidate selection | browser targets, `acceptance`, `jev_agent` loop guards, `deliberation/router`, `tools/selection`, `memory/rerank` |
| §3 Poor use cases: long-form planning, code generation, raw screenshots | never used for these; those stay with the main model |
| §28 Hierarchical decisions, not hundreds of options in one `Choice` | operation head → per-operation target heads; single-candidate heads auto-decided |
| §29 `next_action` / `target_control` / `goal_completed` | `operation` / `{op}_target` / `DONE` |
| §31 Typed actions, never prose the executor interprets | the policy returns an **index**; only `executor.py` resolves it |
| §33 "GUI click → expected UI state changed?" | `browser/freshness.py` + `ExecutedStep.changed` + `ExecutedStep.evidence` (the ordered chain) |
| §36 Capability classes / §72 decision policy by action class | `RiskTier` read 0.60 / write 0.75 / destructive 0.90 |
| §41 Stuck detection: same action repeated, no state change, same error repeated | repeat limit, A/B oscillation, no-progress counter |
| §41 "Do not rely exclusively on JEV — deterministic heuristics should detect obvious loops" | all three loop guards are deterministic, not model calls |
| §43 Three-part division: model plans, JEV decides, code executes | exactly this; see `docs/SYSTEM_ONE.md` |
| §66 Human-in-the-loop approval by action class | `RiskTier` + narrowing-only gating |
| §67–70 Unit / integration / recovery tests, simulation, golden tests | `ScriptedExecutor`, 7 System One suites, `--simulate` demo |
| §71 "Implement restrictions in code, not merely in prompts" | `Answer.validate`, index-only execution, narrowing-only |

Two things Alpha has that the guide does not mention: **shadow mode** and
**calibration** (`docs/SYSTEM_ONE_CALIBRATION.md`). The guide says "JEV decides";
it never says "measure whether JEV's confidence is real". Alpha does.

---

## 2. The bug the guide surfaced

§30 *State Normalization* lists what the decision state must carry:

```text
previousAction?: ActionResult;
failures?: FailureRecord[];
```

Checking that against `browser/jev_policy.py` found the state builder doing this:

```python
"recent_actions": [
    {k: h.get(k) for k in ("action", "operation", "text") if h.get(k)}
    for h in (history or [])[-10:]
],
```

The agent writes history entries with keys `step`, `operation`, `target`, `text`,
`element`, `ok`, `changed`, `detail`. There is no `action` key — so the filter
kept only `operation` and `text`, and **`ok` and `changed` were dropped entirely**.

The consequence is not cosmetic. The rubric the model is given includes
"do not repeat satisfied steps" and "recent WAIT actions are not evidence of
loading" — but the model could not see which steps had been satisfied, or which
had failed. It was being told not to repeat something it had no way to recognise.

Fixed: `recent_actions` now carries `operation`, `target`, `text`, `element`,
`detail`, `ok`, and an **explicit** `changed` (a `null` there is information —
"cannot tell" is not "nothing changed"). A separate `failures` list holds only the
failed entries, so the decision can change strategy rather than repeat itself.
`history` in `jev_agent.py` gained `detail` so a failure carries its reason.

Tests: `test_policy_is_told_which_recent_actions_failed`,
`test_policy_omits_failures_when_nothing_failed`.

---

## 3. The gap the guide surfaced

§40 *Action Budgets*:

```text
maximum actions      → Alpha had max_steps
maximum runtime      → MISSING
maximum retries      → stale_retries, ineffective_limit
maximum destructive  → RiskTier gates confidence, not count
```

`BrowserAgent` had a **step** budget but no **wall-clock** budget. A step budget
is not enough on its own: each step can be slow — a text-model round trip, a page
load, a stalled network — so twelve steps can still run for minutes with nothing
bounding the total.

Added `max_seconds` (default 120, `0` disables). Checked before each decision, so
it bounds the whole run rather than only the actions inside it, and a step that
began inside the budget is allowed to finish. New status `timeout`.

Config: `system_one.browser_max_seconds`, read through
`_browser_guard_options()` alongside the other three guards.

Tests: `test_run_stops_when_the_wall_clock_budget_is_exhausted`,
`test_zero_disables_the_wall_clock_budget`,
`test_a_generous_budget_does_not_interfere`.

> The clock is swapped out by replacing the module's `time` reference, **not** by
> patching `time.monotonic` globally — asyncio schedules with the real clock, and
> patching it would break the event loop the test runs on.

---

## 4. Worth doing next, in order

Ideas from the guide that Alpha does **not** have. Ordered by value against
effort, and each one is bounded.

### 4.1 A deterministic recovery ladder — §34 — **done**

A failed browser step used to end the run with `status="blocked"`. One transient
click failure — an element mid-render, a page still settling — killed the whole
run. For a Playwright-backed agent, where transient failures are routine, that is
the wrong default.

The guide's ladder is deterministic-first, and the browser subset is the first two
rungs:

| Rung | What it does |
|---|---|
| 1 | **Retry the action verbatim.** The same operation, target and value, re-run without asking. Most browser failures are transient. |
| 2 | **Re-observe, then re-decide.** The element may simply have moved, so refresh the page state and let the decision run against it. |

Only after both does the run report `blocked` and hand back. The guide is explicit
that asking the model for a new strategy is *last*, and here it is deliberately
**not** part of the loop at all: replanning belongs to the caller, which is the
layer that can do it.

Implemented as `recoveries_limit` (default 2, `0` restores the old behaviour).
The run records `recoveries` so a caller can see the failure was survived rather
than infer it from the step list.

Two details worth knowing:

- **A deliberate retry is not counted by the loop detector.** `repeat_limit`
  bounds *loops*; `recoveries_limit` bounds *recovery*. Counting a single
  intentional retry towards the repeat limit would let a low `repeat_limit`
  (1 is valid) abort a legitimate recovery.
- **Stage 1 does not regenerate the value.** The page is re-observed immediately
  after the failure, so the decision is still made against fresh state, and asking
  a model for the same text again would be a wasted round trip.

`ScriptedExecutor` gained `failures` (per-action flags, last entry repeating) so
the ladder is testable without a real browser — `failures=[True, False]` fails
once then succeeds; `failures=[True]` never recovers.

Tests: `test_a_failed_step_is_retried_once`,
`test_stage_one_reuses_the_decision_and_stage_two_re_decides` (proves the retry
does *not* re-decide and the recovery *does*, via a client that changes its answer
per call), `test_recovery_gives_up_after_the_limit`,
`test_recovery_can_be_switched_off`,
`test_a_deliberate_retry_does_not_trip_the_repeat_detector`.

### 4.2 An evidence chain instead of a bare `changed` — §33, §73 — **done**

§73 states the principle Alpha already follows:

```text
Action + Evidence = Verified action
```

Alpha recorded evidence as one tri-state bool. The guide's verification examples
are **ordered ladders**, cheapest check first:

```text
copy file → destination exists? → size matches? → hash if required
```

The browser equivalent is a chain — url changed → element count changed → target
signature changed → visible text changed — and `describe_change()` already
computed exactly these reasons but only used them for logging. A run could say
"the page changed" but not "it navigated".

Implemented as `freshness.change_evidence(observed, current, target_index=None)`,
ordered by *significance, not cost* — a navigation also alters text and element
counts, but knowing it navigated is the fact that matters. Five labels:

| Label | Fires when |
|---|---|
| `url` | the URL changed |
| `element_count` | the table gained or lost elements |
| `target` | the specific element a decision names was replaced |
| `text` | visible text changed |
| `content` | the catch-all — a change no named check saw (a field's value, a scroll) |

`content` is deliberately a *fallback*, not an addition: it fires only when
nothing more specific did, so the list stays a ladder rather than a set.

**Where it is consumed** — the wiring lesson from §3 applied to itself:

- `ExecutedStep.evidence` → `to_dict()` → `_run_goal`'s JSON → the calling agent.
- `history[].evidence` → the policy's `recent_actions`, so the next decision sees
  *what* changed and not just *that* something did. Two rubric lines were added
  for this: "a step that changed nothing did not advance the goal; repeating it
  will not either", and the `DONE` line now says "not merely that the page
  changed" — the terminal claim is the one place nothing downstream re-checks it.
- `describe_change(..., target_index)` now takes the index, so a replaced target
  reads as "the target element changed" instead of the misleading "page content
  changed". `jev_agent` passes `decision.target` on the text-generation path.

**A bug this surfaced.** Writing the evidence path exposed that when the
post-action re-observation *failed*, the loop substituted the previous observation
and then compared it to itself — reporting `changed=False` for what was really
"we could not look". A run of those trips the no-progress detector on a page that
may be moving perfectly well. `changed` now stays `None` when the re-observation
did not happen, and the evidence list is empty rather than invented. Test:
`test_a_failed_re_observation_is_not_reported_as_no_change`.

Tests: 16 in `test_system_one_freshness.py` (ordering, each label, the
fallback-is-not-an-addition property, the empty cases, and the two agent-level
assertions), 2 in `test_system_one_wiring.py` (the real stub backend produces no
evidence, and evidence survives the tool's JSON round trip).

**And the claim is now enforced, not just asked for.** §71 says restrictions
belong in code rather than prompts — so the `DONE` requirement stopped being a
rubric line only. A terminal `DONE` now has to rest on something observable: a
page with no elements and no text cannot support "all requirements are
satisfied", and the run reports `unverified` with `fallback=True` instead of
accepting it.

This was not hypothetical. The first end-to-end test of the HTTP executor — run
against a page that could not be fetched — came back `status="done"`. The
freshness guard cannot catch that case: two identical *empty* pages compare
equal, so they are trivially "fresh". A run whose page was never read passed the
one check standing between it and a success claim.

Gated by the same `require_fresh` switch as the rest of the terminal-claim
verification, so it is one switch for "verify the terminal claim".

### 4.3 A destructive-action budget — §40

The guide budgets "maximum destructive operations". For a desktop control plane
the action type is explicit (`file.delete`, `registry.write`). For a browser it is
**not**: a `CLICK` may be "Next page" or "Delete account", and inferring which is
exactly the kind of guessing the rest of this design avoids. Worth doing only if
the risk class comes from somewhere honest — e.g. the element's own label/role
matching a known-dangerous set, or the caller declaring the goal destructive. Not
worth doing as a guess.

### 4.4 Structured `previousAction` — §30

`recent_actions` is now a flat list. The guide models `previousAction` as a single
typed record with its `ActionResult`. Marginal for the browser loop, which already
sees the last 10 entries; more valuable if the browser agent is ever embedded in a
larger multi-domain loop where the previous action may not have been a browser
action at all.

### 4.5 Out of scope for Alpha

The guide's §4–27 control surface (UI Automation, registry, services, scheduled
tasks, packages, printers, USB, Bluetooth, DPI, audio) and §35 self-healing worker
watchdogs describe a **Windows desktop control plane**. Alpha has a browser tool
and a sandbox, not a desktop agent. Adopting those means building a new subsystem,
not extending this one — and §53 is honest that "full laptop control" spans three
distinct levels (user-level, administrative, complete physical), of which only the
first is reachable without privilege escalation the guide itself forbids in §71.

---

## 5. Verification

- `tests/test_system_one_freshness.py` — 62 passed (3 for the wall-clock budget,
  5 for the recovery ladder, 16 for the evidence chain, 5 for `href` as part of a
  link's identity)
- `tests/test_system_one_wiring.py` — 57 passed (`browser_max_seconds` and
  `browser_recoveries_limit` added to the guard-config assertions; two asserting
  the stub backend produces no evidence and that evidence survives the tool's
  JSON round trip; six for the `fetch_run` entry point; eight for the `values`
  path that makes form filling reachable)
- `tests/test_system_one_http_executor.py` — 47 passed (new file; see
  `docs/SYSTEM_ONE_BROWSER_AGENT_EVAL.md`, Updates 4 and 5 — the HTTP backend,
  then form filling and submission, with the `needs_text` refusals)
- `tests/test_system_one_wave2.py` — 123 passed (2 for the policy state, 6 for
  `href` surviving the snapshot-to-table path, 26 for nested-label correctness,
  control state, and form extraction, 3 for both kinds of table incompleteness
  reaching the policy)
- Full fourteen-file regression — see the run recorded in
  `docs/SYSTEM_ONE_BROWSER_AGENT_EVAL.md`
- `ruff` clean on every touched file
- Config verified end-to-end by script: `config.yaml` → `_browser_guard_options()`
  → `BrowserAgent` (`require_fresh`, `stale_retries`, `ineffective_limit`,
  `max_seconds`, `recoveries_limit`)

## 6. The six browser guards, in one place

| Guard | Bounds | Default | Config |
|---|---|---|---|
| `max_steps` | total actions | 12 | caller (`steps=`) |
| `max_seconds` | wall-clock | 120 s | `browser_max_seconds` |
| `repeat_limit` | identical actions | 3 | caller (`repeat_limit=`) |
| `ineffective_limit` | actions that changed nothing | 3 | `browser_ineffective_limit` |
| `stale_retries` | re-decisions after the page moved | 2 | `browser_stale_retries` |
| `recoveries_limit` | attempts after a failure | 2 | `browser_recoveries_limit` |

Plus `require_fresh` (`browser_require_fresh`), which gates the freshness guard
rather than bounding anything. Each one exists because of a specific way a browser
agent fails *while appearing to succeed* — which is the theme running through the
whole port.

Two things in the same spirit that are **not** budgets, and so are not in the
table: the evidence chain (§4.2), which says *what* changed rather than only
whether; and the `DONE` evidence requirement, which refuses a success claim the
page cannot support.
