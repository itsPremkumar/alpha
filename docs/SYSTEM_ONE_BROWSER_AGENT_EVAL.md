# Evaluation: `browser-use/jev-ultrafast`

**Repo:** https://github.com/browser-use/jev-ultrafast · by **gregpr07** (Browser Use), Sep 2026.
**Verdict: not drop-in usable as-is, but the core design is genuinely valuable and worth porting into Alpha.** The novel part is ~150 lines, not the whole repo.

---

## What it is

A browser agent where **Jev picks the operation and the element**, and a small LLM writes
text *only* when the operation is `TYPE_TEXT`.

> Zürich → London on Google Flights in **7.1 seconds** — one natural-language goal,
> actual text generation and loading waits included.

Operations: `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, `BLOCKED`.

## The design that matters

### 1. Dynamic, indexed action space

Every observation produces an element table:

```
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
```

### 2. Speculative fan-out with per-operation target heads — **the key trick**

```
                      one TypeSafe request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
```

All target heads are asked in **one** request. Only the head matching the chosen operation
can execute — *"unused target heads cannot cause an action."* Two decisions, one network
round trip.

**This is also how they solve the 255-option limit.** Rather than one choice over every
element, `action_space()` partitions elements by compatible operation, and each head only
offers elements valid for that operation. Native dropdown options get a composite
`element:option` index. This is the concrete answer to the cardinality problem I flagged
in the use-case research.

### 3. State is structured, never a screenshot

```python
"state": {
    "page": {"url": ..., "title": ..., "text": ...},
    "elements": [{"index": "1", "role": "button", "label": ..., "operations": [...]}],
    "recent_actions": [...last 10...],
}
```

No screenshots in the default loop — Jev is text-only, so this is a requirement, not an
optimisation. The inspector opts into screenshots for humans only.

### 4. Defensive validation

```python
def validate_choice(answer, ids):
    probabilities = answer["probabilities"]
    valid = (
        answer["choice"] in ids
        and set(probabilities) == set(ids)
        and all(0 <= n <= 1 for n in [*probabilities.values(), answer["confidence"]])
        and abs(sum(probabilities.values()) - 1) < 0.02
        and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
    )
```

Even though Jev cannot type-error, they still verify the distribution is well-formed and
the reported choice is really the argmax. **Worth copying into Alpha's client.**

### 5. Text generation is quarantined

The LLM only ever produces one thing: a JSON object `{"text": "..."}` for the selected
field, validated before typing. Everything else is the model choosing among typed options.
*"Model output never becomes selectors, coordinates, shell commands, or executable JavaScript."*

## Measured results

| Metric | Before | After |
|---|---|---|
| Median task time (Google Flights) | 9.450 s | **7.092 s** (−25%) |
| Median browser protocol calls | 1,092 | **101** (~10× fewer) |
| Wikipedia article task | — | **2.798 s** |
| Hotel search + filter | — | **1.896 s** |
| Recorded Flights run | — | **7,073 ms** |

6 alternating runs, identical models and settings, both versions **3/3**. Their own caveat:
*"This is three repeats of one task on one browser profile, not a general reliability
benchmark."* Treat the 25% as directional, not a benchmark.

## Why it moves (from their README)

- One request per decision cycle; operation and target heads share the same observed state.
- One browser call per snapshot — read visible controls, names, values, text atomically.
- Wait for useful state (suggestions capped at 200 ms; else ≤ 2 frames or 50 ms).
- Send visible text only — offscreen bodies and footers don't fill context.
- Keep hidden tabs rendering; focus emulation avoids background throttling.
- Executor rechecks page freshness and click occlusion before acting.

## Can Alpha use it? — portability

| Aspect | jev-ultrafast | Alpha | Portable? |
|---|---|---|---|
| Model provider | `api.typesafe.ai/v1/systemone`, `jev-latest`, `TYPESAFE_API_KEY` | `alpha/models/system_one.py`, Vercel gateway default | ✅ Yes — our client already speaks both. Only `choice` is used, so the `noul`/`boolean` difference is irrelevant. Set `provider: typesafe` + a native key, or point at the gateway. |
| Browser layer | Chrome + [Browser Harness](https://github.com/browser-use/browser-harness) via CDP | Playwright (`browser` extra) | ⚠️ Different layer — needs a port |
| Async | sync `httpx.Client` | async throughout | ⚠️ Needs an async port |
| Text model | `TEXT_MODEL_API_KEY` (OpenRouter / DeepSeek) | Alpha has its own LLM stack | ✅ Use Alpha's own |
| Question construction | `questions.py` + `model.py` (~150 lines of the good part) | — | ✅ Copy the pattern |

### Alpha's current browser capability is much weaker

`alpha/tools/builtins/browser_supervisor_tool.py` exposes only
`navigate` / `dom` / `click(x, y)` / `screenshot` / `close` — **coordinate-based clicking**,
one tool round trip per action, with the LLM picking coordinates. The element-index
approach is a clear upgrade in both speed and reliability.

### Blockers for using it as-is

1. **Needs a native `TYPESAFE_API_KEY`** (early access). Our gateway key is a different
   auth/endpoint. Adaptable, but not zero-work.
2. **Browser Harness + Chrome remote debugging**, not Playwright.
3. **Sync**, whereas Alpha is async.
4. **MVP gaps:** no shadow roots, frames, canvas, uploads, popup tabs, nested scrolling, or
   arbitrary keyboard widgets. DOM reader covers common HTML + ARIA, not the full
   accessible-name spec.

## Recommendation

**Don't vendor the repo. Port the pattern.** The valuable, portable part is:

1. **Indexed element table** built from a single atomic DOM snapshot.
2. **Per-operation target heads** — solves the 255-cardinality limit by partitioning.
3. **Speculative fan-out** — operation + all targets in one request; only the matching head executes.
4. **`validate_choice`** — defend against malformed distributions anyway.

Concrete shape inside Alpha:

- New `alpha/browser/element_table.py` — atomic DOM snapshot → indexed elements with
  per-operation compatibility (Playwright instead of `snapshot.js`).
- New `alpha/browser/jev_policy.py` — builds the fan-out request via the existing
  `SystemOneClient` and returns `{operation, target_index}`.
- Wire into `browser_supervisor_tool.py` as a new action, replacing coordinate clicking.
- Add `validate_choice` to `alpha/models/system_one.py` as `Answer.validate(allowed_ids)`.

Estimated: a focused port, not a rewrite — the policy is ~150 lines.

**Sequencing note:** this needs a *working* System One key. Ours is still blocked by the
Vercel credit-card gate, so it would be built against the simulator and validated live
later. Given that, finishing the already-started sites (tool-trace verification,
injection detection on fetched content) is better value first — this browser work is the
right *third* step.

## Sources

- [jev-ultrafast](https://github.com/browser-use/jev-ultrafast) · [performance.md](https://github.com/browser-use/jev-ultrafast/blob/main/docs/performance.md)
- [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out) — the pattern this is built on
- [Browser Harness](https://github.com/browser-use/browser-harness)

---

## Update: the port is done

The recommendation above has been implemented. What landed:

### `alpha/browser/element_table.py`
Atomic DOM snapshot → indexed elements, partitioned by which operation they
support. `select` elements contribute composite `element:option` indices. Heads
are only truncated when one operation genuinely exceeds 255 candidates, and the
`truncated` flag is set so the caller can scroll instead of silently losing options.

### `alpha/browser/jev_policy.py`
One request carrying the operation head plus every per-operation target head.
Two details worth knowing:

- **Single-candidate heads are decided without asking.** A one-option choice is
  degenerate — it came back with probability 0.95, which fails a sum-to-one check.
  There is a regression test.
- **Abstention is not an action.** Unusable operation *or* target answers return
  `None`, and `None` never reaches the executor.

### `alpha/browser/dom_snapshot.py`
Three adapters, so the element table is source-agnostic in practice and not just
in theory:

1. Alpha's `get_dom_summary` shape.
2. **Raw HTML via `html.parser`** — no dependency, no browser process. This is the
   one that matters: it means the whole fast path runs on content Alpha already
   fetched. Handles void tags, unclosed tags, hidden inputs, `<script>` exclusion,
   and native `<select>` options.
3. An optional Playwright/CDP page, duck-typed so the import is never required.

### `alpha/browser/executor.py`
`SupervisorExecutor` (built-in supervisor), `PlaywrightExecutor` (live page),
`ScriptedExecutor` (tests). The built-in one is honest about its limits: it
reports TYPE_TEXT / SELECT / SCROLL as unsupported rather than pretending.

### `alpha/browser/jev_agent.py`
The loop: observe → **screen the page text for injection** → decide → execute.

- Step budget, identical-step detection, and A/B oscillation detection.
- `status="no_signal"`, `fallback=True`, **zero steps executed** when System One
  abstains — tested.
- Stops before acting when injection risk ≥ 0.6.
- Text values are never guessed: the run stops with `needs_text` and reports the
  field so the calling agent supplies it, unless a `text_provider` is given.
  `llm_text_provider` wraps an LLM and explicitly tells it the page text is data.

### Tool surface
`browser_navigate_and_inspect` gained `table` (indexed action space), `run`
(drive toward a goal), and `parse` (index elements from raw HTML).

### Why the HTML adapter changes the calculus
The original note sequenced this work third, after tool-trace verification and
injection detection, partly because it seemed to need a real browser. It does not.
Everything except actual clicking works on fetched HTML, so the value is
available now, and the Playwright path is a drop-in upgrade later.

---

## Update 2: the loop guards, ported

Reading their `agent.py` against Alpha's first port surfaced two things Alpha did
**not** have, both of which fail *silently* — the run reports success while doing
nothing, or doing the wrong thing. Both are now in.

### `alpha/browser/freshness.py` — the page-freshness guard

A decision is made against an observed page and executed a moment later. In that
window the page can navigate, re-render, or have the target replaced. Executing
anyway means clicking whatever now occupies that index — and the click still
reports success.

Two levels, because they catch different things:

| Function | Catches |
|---|---|
| `page_fingerprint` | the whole page moved — navigation, re-render, new results |
| `element_signature` | the *specific target* moved — a list re-ordered, a button disabled |

Only the second catches a re-rendered list, which is the common case on real
sites. `is_fresh(observed, current, target_index)` runs the cheap target-scoped
check; without a target it compares whole-page fingerprints.

Dropdown options are guarded as well, under the `"<element>:<offset>"` keys the
action space offers them as, and scoped to their parent element. Without that,
`SELECT` is the one targeted operation with no guard: it fails open, and the
agent picks whatever option now occupies that offset. The parent scoping matters
because the same label legitimately appears in two different dropdowns.

**Geometry is deliberately excluded from the signature.** Elements move as the
page scrolls and reflows; treating a scroll as a stale decision would re-decide
constantly for no reason. Coordinates are re-resolved by the executor, which is
the layer that can actually hit-test.

`is_fresh` **fails open** on missing data (no elements, unknown index). A guard
that fails closed turns a data-shape problem into a stuck agent, and the executor
already re-resolves geometry at input time.

> **A real bug this caught.** The first version keyed `element_guards` on an
> `index` *field* in the element dict. But `dom_snapshot` never emits one —
> `build_action_space` derives the index from **list position**. So the guard
> returned `{}` for every real page, `is_fresh` fell through to its fail-open
> branch, and the whole target guard was dead code in production. It is now keyed
> by position, with an explicit `index` winning when a richer source supplies one.
> The unit tests passed throughout, because the test fixture supplied the field
> the real adapters do not.

### `alpha/browser/jev_agent.py` — staleness and no-progress

Four additions to the loop:

1. **`DONE` is a claim about a page.** If the page moved while deciding, the claim
   is about a page that no longer exists. The agent re-observes, and re-decides
   up to `stale_retries` times before returning `status="stale_page"`.
2. **The text-generation window is the dangerous one.** Asking a model for a value
   is a network round trip; the page can move underneath it. Before typing, the
   target guard is re-checked. This is the case their `agent.py` guards and the
   one most likely to fill the wrong field.
3. **No-progress detection.** A run of actions that all *succeeded* and all
   changed nothing is a loop, even when the actions differ. This catches what
   "same step repeated" misses — the agent trying three different things that each
   do nothing. `WAIT` is exempt: waiting is *supposed* to change nothing.
4. **A deterministic recovery ladder.** A failed step used to end the run. Now it
   retries the action once (transient failures are the common case), then
   re-observes and re-decides, and only then reports `blocked`. See
   `SYSTEM_ONE_LAPTOP_CONTROL_IDEAS.md` §4.1.

`changed` is tri-state — `True` / `False` / `None` — and `None` means "cannot
tell", deliberately distinct from `False`. A scripted or stub backend has no way
to know, and treating its silence as "nothing happened" would let the agent
declare a no-progress loop on a page it simply cannot observe. Backends advertise
this via `tracks_changes`; `PlaywrightExecutor` sets it, `SupervisorExecutor` does
not.

A **deliberate** recovery retry is not counted by the loop detector:
`repeat_limit` bounds loops, `recoveries_limit` bounds recovery. Counting one
intentional retry towards the repeat limit would let a low `repeat_limit` abort a
legitimate recovery.

### Outcome recording now prefers real change

The calibration log (see `SYSTEM_ONE_CALIBRATION.md`) previously recorded
`step.ok` as the outcome of a browser decision. That is a weak signal: a click can
report success and accomplish nothing. It now records `changed` when the backend
can observe it, falling back to `ok` only when it cannot.

### Config

```yaml
system_one:
  browser_require_fresh: true      # re-verify the page before acting / before DONE
  browser_stale_retries: 2         # re-decisions allowed when the page moved (0-10)
  browser_ineffective_limit: 3     # consecutive no-op actions before "stuck" (1-20)
```

> **These were decorative until they were wired.** The three keys were declared on
> the config model and present in both YAML files, but nothing read them:
> production constructs `BrowserAgent` in exactly one place
> (`browser_supervisor_tool._run_goal`) and passed only `max_steps`. Setting
> `browser_require_fresh: false` would have changed nothing, silently.
>
> `_run_goal` now reads them through `_browser_guard_options()`, which degrades to
> `{}` — the agent's own defaults — if the config cannot be read, so a config
> error can never break the tool. Covered by tests in
> `test_system_one_wiring.py`, including one that asserts a broken config still
> runs and does **not** override the defaults.
>
> The same gap applied to `omitted`: it was populated inside the page state but
> never surfaced. It is now reported by `_element_table`, `_parse_html`, and
> `_suggest_next_action`, so the caller can see the table is incomplete.

### `alpha/browser/dom_snapshot.py` — truncation is reported, not silent

All three adapters cap the element table at `MAX_ELEMENTS = 200`. That cap used
to be invisible: a caller got a list and could not tell a page with 12 controls
from one where 12 of 300 survived.

A policy that does not know elements are missing will keep re-deciding against a
table that cannot contain the answer — and the run reads as a *policy* failure
when it is a *data limit*. The page state now carries `omitted`:

```python
elements, omitted = extract_elements(html)          # -> (list, dropped_count)
state = page_state_from_html(html)                  # -> state["omitted"] when > 0
```

`extract_elements_from_playwright` reads the injected script's `{items, total}`
and derives the same count (and still accepts a bare list, so an older injected
script degrades to "no count" rather than breaking). `elements_from_html` keeps
returning a plain list — the tuple helper is additive.

The check sits **after** the interactivity filters, so `omitted` counts real
targets rather than every tag on a long page. The count is then passed to the
policy as `state["table"] = {"complete": False, "omitted": N}`, with a matching
rubric line telling the model to `SCROLL` rather than repeat a failed search.

### What the guards do on Alpha's *actual* backend today

Worth stating plainly, because it bounds what this port delivers right now.

Production drives `SupervisorExecutor`, and `BrowserSupervisor.get_dom_summary()`
is a **stub**: it returns the same three hardcoded elements
(`#btn-submit`, `#input-search`, a Documentation link) for every page, and
`click_coordinate` returns a canned `"clicked"` status. Playwright is not
installed. So the loop currently runs against a fake page.

The guards are correctly **inert** there, and that is the point of the tri-state
`changed`:

| Guard | On the stub |
|---|---|
| `changed` | always `None` — `tracks_changes` is False, so nothing is inferred |
| no-progress | cannot trip; `None` is not `False` |
| staleness | skipped; the stub re-reports identical elements, so `is_fresh` is trivially true |
| `omitted` | always 0; the stub summary has no cap |
| outcome recording | falls back to `step.ok` — the only signal available |

Verified by `test_the_stub_backend_never_reports_no_change` and
`test_the_stub_backend_is_not_called_stale`, which run the loop against the real
`SupervisorExecutor` and a live session. **Without the tri-state design, a stub
backend would make the agent declare a no-progress loop on a page it cannot see** —
that is the failure these two tests exist to prevent.

What was *not* available when this section was written: **form filling**. It was
closed in Update 5 — `HtmlExecutor` now records a field value and submits the
form, and `fetch_run` takes a `values` object to supply what to type. See
"Update 5: forms, and the state the model could not see" below.

### Still not ported

Honest list of what remains from their implementation, and why it is not done:

| Their feature | Status |
|---|---|
| `elementFromPoint` occlusion hit-test before input | Not ported. `PlaywrightExecutor` clicks by selector, so Playwright's own actionability checks already cover this; it would only matter for the coordinate-clicking supervisor, which is a stub. |
| Focus emulation for background tabs | Not ported. Needs a live CDP session to be meaningful or testable. |
| `omitted_actions` count on truncation | **Ported** — see above. |
| Text-helper output validated against `{"text"}` | Not applicable — Alpha has no separate text-helper process; `llm_text_provider` returns a plain string. |

### Verification

- `tests/test_system_one_freshness.py` — 57 tests (new file): fingerprints,
  signatures, option guards, `is_fresh` including the fail-open cases, staleness
  re-decision, no-progress blocking, the `WAIT` exemption, the wall-clock budget,
  the recovery ladder, the outcome-preference change, and the evidence chain.
- `tests/test_system_one_wave2.py` — 87 tests (was 75), including 10 for the cap
  (the count, the filter ordering, the back-compatible list signature, both
  Playwright response shapes, and the policy being told) and 2 for the policy
  state carrying `ok` / `changed` / `failures`.
- `tests/test_system_one_wiring.py` — 43 tests (was 31), including 8 for the
  guard-config seam (values read from config; a broken config degrades to the
  agent's defaults without stopping the run; `omitted` reaches the tool output),
  2 that run the loop against the real `SupervisorExecutor` to prove the guards
  stay inert on a backend that cannot observe the page, and 2 for the evidence
  chain reaching the caller.
- Full seven-suite System One set — **342 passed** (325 before this change).
- Twelve-file regression (the seven System One suites plus `browser_automation`,
  `browser_router`, `browser_supervisor`, `browserless_client`,
  `cdp_browser_bridge`) — **463 passed, 1 skipped**.
- `ruff` clean and `compileall` OK on every touched file.

## Update 3: the evidence chain

From the laptop-control guide (§33, §73):

```text
Action + Evidence = Verified action
```

Alpha already recorded evidence, as one tri-state bool: `ExecutedStep.changed`.
That answers *whether* the page moved. It cannot answer *what* moved — and "it
changed" is not evidence of what changed. A click that navigated and a scroll
that merely re-rendered both report `changed=True`; only one of them means the
goal advanced. A run could therefore be described but not diagnosed, and the only
way to find out what happened was to run it again.

`describe_change()` already computed the individual reasons — it just logged them
and threw the structure away. That computation is now
`freshness.change_evidence()`.

### The ladder

Ordered by **significance, not cost**. A navigation also alters text and element
counts, so the cheap-check-first ordering of a file copy does not transfer; what
transfers is the *ladder* idea — return the strongest fact, not every fact.

| Order | Label | Fires when |
|---|---|---|
| 1 | `url` | the URL changed |
| 2 | `element_count` | the table gained or lost elements |
| 3 | `target` | the specific element the decision names was replaced |
| 4 | `text` | visible text changed |
| 5 | `content` | catch-all: a change no named check saw — a field's value, a scroll |

`content` is a **fallback, not an addition** — it fires only when nothing more
specific did, so the result stays a ladder rather than collapsing into a set.
Returning `[]` means "nothing can be shown to have changed", which includes the
missing-observation case: that is `cannot tell`, and it is deliberately not the
same as `False`.

### Wiring it, not just writing it

A helper nothing calls is decoration. This was the specific lesson from the
config-keys gap earlier in this port, so it was applied to the new code on the
way in — three consumers, each with a test:

| Consumer | Why |
|---|---|
| `ExecutedStep.evidence` → `to_dict()` → `_run_goal`'s JSON | The calling agent sees what changed, so a failure is diagnosable without a replay |
| `history[].evidence` → the policy's `recent_actions` | The next decision sees *what* changed, not just that something did |
| `describe_change(..., target_index)` | A replaced target no longer reads as a whole-page change |

Two rubric lines were added to `NEXT_ACTION` for the policy:

- `"A step that changed nothing did not advance the goal; repeating it will not either."`
  — the existing "do not repeat satisfied steps" does not cover this: an
  ineffective step is not a satisfied one.
- `"DONE requires visible evidence that ALL requirements are satisfied, not merely
  that the page changed."` — the terminal claim is the one place nothing
  downstream re-checks the evidence, so the rubric has to.

`describe_change()` now takes the target index, because `jev_agent` calls it on
the text-generation stale path where the check that failed was the *target* check.
Without the index it reported "page content changed", sending the reader to the
wrong place.

### The bug this surfaced

Writing the evidence path exposed a real defect in the re-observation step:

```python
before = page_state
page_state = await self._observe_or_none() or before   # falls back to `before`
changed = result.get("changed")
if changed is None and self._tracks_changes:
    changed = page_fingerprint(before) != page_fingerprint(page_state)
```

When the re-observation **failed**, the loop substituted the previous observation
and then compared it to itself — reporting `changed=False` for what was really
*"we could not look"*. A run of those trips the no-progress detector and kills a
run that may have been progressing perfectly well. The tri-state `changed` existed
precisely to prevent this class of error, and this path was quietly defeating it.

Fixed: `changed` stays `None` when the re-observation did not happen, and the
evidence list is empty rather than invented. Verified by
`test_a_failed_re_observation_is_not_reported_as_no_change`, which drives an
executor whose `observe()` succeeds once and then raises.

### On the stub backend

Evidence is computed by comparing two real observations, and the stub supervisor
re-reports an identical page — so the field is present and empty, never absent and
never invented. `test_the_stub_backend_produces_no_evidence` asserts exactly that
against the real `SupervisorExecutor`.

## Update 4: acting on a real page

Everything above made the loop *safe*. None of it made it *useful*: production
drives `SupervisorExecutor`, whose `get_dom_summary()` returns the same three
hardcoded elements for every page, and Playwright is not installed. So `run` had
no backend that could act on a real page at all — the guards were all correctly
inert because there was never a real page to guard.

This update closes that, for the goals that are navigation.

### The bug found on the way in

Before any executor could follow a link, one had to be able to *see* the
destination. It could not:

| Layer | `href` |
|---|---|
| `dom_snapshot` — HTML adapter | emitted |
| `dom_snapshot` — DOM-summary adapter | emitted |
| `dom_snapshot` — Playwright adapter | emitted |
| `element_table.Element` | **no such field** |
| `build_action_space` | **never read** |

Every producer emitted it; the boundary silently dropped it. This is the same
shape as the config keys that nothing read and the `omitted` count that nothing
surfaced — a value that exists, is correct, and is unreachable. `Element` gained
`href`, `build_action_space` reads `href`/`url`, and `to_dict()` carries it.

`href` also went into `freshness._SIGNATURE_FIELDS`, because it is part of a
link's identity: the same label pointing somewhere else is a different target.
That is exactly the re-rendered-list case the freshness module exists for — a
paginated list where "Next" now goes to page 3 while the label is unchanged — and
it was invisible without this. The cost of the extra sensitivity is bounded and
cheap (a site that rewrites hrefs with session tokens costs one extra re-decide,
capped by `stale_retries`); the cost of missing it is a click that silently goes
somewhere else.

### `HtmlExecutor`

Fetches a URL over HTTP, indexes the interactive elements from the HTML, and acts
by following a link's `href`. `tracks_changes = True`, because the page can
genuinely be re-fetched and compared — so the guards from Update 2 stop being
inert the moment this backend is used.

Honest about its limits, the same way `SupervisorExecutor` is:

| Operation | Behaviour |
|---|---|
| `CLICK` on an element with an `href` | follows it |
| `CLICK` on anything else | refused — "has no href to follow" |
| `TYPE_TEXT`, `SELECT`, `SCROLL_*` | refused — "an HTTP page cannot …; needs a real browser" |
| `DONE`, `WAIT`, `BLOCKED` | no-op, as for every backend |

A pretending backend is worse than a limited one: the run records progress it
never made, and every guard downstream is built to trust that record.

The cost is real and worth knowing: `observe()` re-fetches, because a cached page
cannot tell you whether the last action did anything. **One step is two
requests**, and the tool reports `fetches` so a caller can see it rather than
infer it.

### The security boundary

This executor turns a model-chosen index into a network request, so the scheme
and the host are a boundary, not a detail:

| Rule | Why |
|---|---|
| Only `http`/`https` | `javascript:`, `data:`, `file:`, `mailto:` are refused |
| Literal local hosts refused | `127.0.0.1`, `169.254.169.254` (cloud metadata), `localhost`, `[::1]`, private ranges |
| Redirects validated **per hop** | an allowed host that 302s to a metadata endpoint must not be followed |
| Allowed hosts default to the start host | an unattended agent that follows any host is an SSRF engine |
| Not exposed as a tool parameter | a boundary the caller can widen on request is not one |

The local-host check is deliberately **partial and says so**: it does not resolve
DNS, so a hostname that resolves to a private address still gets through. A
complete guard has to resolve and then check the address at connect time, which
belongs in the HTTP client rather than here.

### The bug the tests found

The first end-to-end test of `fetch_run` — against a page that could not be
fetched — came back `status="done"`.

The freshness guard cannot catch that: two identical *empty* pages compare equal,
so they are trivially "fresh". A run whose page was never read passed the one
check standing between it and a success claim. The rubric already says "DONE
requires visible evidence that ALL requirements are satisfied", but a rubric is a
prompt, and the guide's §71 is explicit that restrictions belong in code.

So a terminal `DONE` now has to rest on something observable. A page with no
elements and no text cannot support "all requirements are satisfied" — there is
nothing visible to be satisfied by — and the run reports the new status
`unverified` with `fallback=True`, handing the unsupported claim back to the
caller. Gated by the same `require_fresh` switch that gates the rest of the
terminal-claim verification.

### Reaching it

A capability no caller can invoke is the same decoration as a config key nothing
reads, so the tool gained a `fetch_run` action. It goes through the same
`_browser_guard_options()` seam as `run`, so it cannot bypass the loop guards, and
it deliberately does **not** accept an allowed-hosts parameter — widening that
boundary stays a decision a human made, not one the model can request.

### Verification

- `tests/test_system_one_http_executor.py` — **31 tests** (new file): fetching and
  indexing, `href` capture, the last-known-state fallback, following a link, each
  refusal, every scheme and local-host rejection, redirect validation including
  the SSRF-via-redirect case, the body cap, the redirect loop, and four end-to-end
  runs of the agent against a fetched page.
- `tests/test_system_one_wave2.py` — 93 tests (was 87): 6 for `href` surviving
  from each adapter through to a resolved target.
- `tests/test_system_one_freshness.py` — 62 tests (was 57): 5 for `href` as part
  of a link's identity.
- `tests/test_system_one_wiring.py` — 49 tests (was 43): 6 for `fetch_run` being
  routed, guarded, and unable to widen its own host boundary.
- Full regression across the thirteen browser/System One files plus
  `test_no_orphan_modules.py` — **516 passed, 1 skipped** (was 463/1 before this
  update, over twelve files).
- `ruff` clean on every touched file.

For the reading of the external laptop-control architecture guide against this
code — what Alpha already implements, and what is worth doing next — see
`docs/SYSTEM_ONE_LAPTOP_CONTROL_IDEAS.md`.

## Update 5: forms, and the state the model could not see

Update 4 gave the loop a way to *reach* a real page. It could follow a link and
nothing else: any page whose content lives behind a search box, a login, or a
filter was still unreachable, because `HtmlExecutor` had no way to fill a field
or submit a form. That is most of the useful web.

Closing it turned up two more instances of the recurring bug class, and one
long-standing parser bug.

### Dead signature fields, again

`checked`, `selected` and `expanded` were listed in `_SIGNATURE_FIELDS` — so
they affected freshness — but **no adapter emitted any of them**. `disabled` was
emitted nowhere either; it was only ever *read*, to drop an element from the
action space.

The signature test passed anyway, because the fixture injected the field by
hand. This is the same trap as `element_guards` keying by `index`: a test that
supplies its own input cannot discover that the producer never sends it. The
check that catches it is asking *which producer emits this key* — and for these
four the answer was none.

The consequence was not theoretical. The rubric says "do not toggle a checkbox
that is already in the requested state", and that instruction was unenforceable:
the model could not see a checkbox's state, so it could not tell an already-set
box from an unset one, and the freshness check could not tell a toggle that
worked from a toggle that was never observed.

All four are now emitted from all three adapters (DOM summary, HTML parser,
Playwright JS):

- `checked` — **always** emitted for a checkbox or radio, never only when true.
  An unchecked box and a box nobody reported must not share a signature.
- `selected` — per option, and a `<select>`'s own `value` is now taken from its
  selected option. Without that every dropdown read as empty.
- `expanded` — from `aria-expanded`, absent when the attribute is absent.
- `disabled` — carried as tri-state state by the Playwright and DOM-summary
  adapters. The HTML parser deliberately **drops** such controls from the action
  space instead (a control that cannot be acted on does not belong there), so on
  an HTML-parsed page "disabled" reads as "absent" — the trade-off is documented
  in `dom_snapshot._emit`, and the rubric's WAIT rule covers both the same way.

### Forms: hidden inputs are data, not actions

A CSRF token is exactly the value that makes a POST work, and it is invisible.
It must not become an action — but dropping it makes every protected form
unsubmittable. So `dom_snapshot` now collects forms separately from the action
space:

```python
extract_forms(html)
# [{"index": 1, "action": "/search", "method": "get", "enctype": "",
#   "fields": [{"name": "csrf", "value": "tok"}]}]
```

Each interactive element inside a form carries its `form` index, and the
`page_state` gains a `forms` key (omitted when there are none, like `omitted`).
`action=""` means "submit to the current URL" — the HTML spec, and what every
browser does.

### Filling and submitting, in `HtmlExecutor`

`act()` now has three real outcomes instead of one:

| what was clicked | what happens |
|---|---|
| a link | follow its `href` |
| a submit control | build and send the form's request |
| a field (`TYPE_TEXT` / `SELECT`) | record the value against its form |

A recorded fill survives the next `observe()`, which is the non-obvious part:
`observe()` rebuilds the whole page state from HTML, so a value held only in the
element table would vanish on every step. Pending values are held against the
form index and cleared on navigation and after a submit.

A recorded fill also reports `changed=True`. The page genuinely does not change
server-side when you type into it — so the honest answer is "nothing changed",
which would trip the no-progress detector and kill a run that is legitimately
filling in a form. This is the one place where the truthful value and the useful
value disagree, and the useful one wins, deliberately and with a comment.

The request is built the way a browser builds it: hidden fields first, then the
recorded values, so a typed value overrides a default. `GET` becomes a query
string, `POST` a form-encoded body. A `multipart` form, a field outside a form,
a nameless field, and a non-submit button are each refused with a reason rather
than guessed at.

Two details that only matter once you have a session:

- **Post/redirect/get.** A 301/302/303 after a POST is re-issued as a `GET` with
  no body — what browsers do, and what stops a refresh from double-submitting.
  307/308 preserve the method, as the spec requires.
- **One persistent client.** A login's session cookie has to survive to the next
  request, so the executor holds a single `httpx.AsyncClient` rather than
  creating one per fetch. It is closed in `aclose()`.

### The nested-label bug

Found while testing form labels, and pre-existing: `handle_data` appends text to
*every* open frame, so a child's text already reaches its ancestors as it
arrives. `handle_endtag` then re-appended it, so
`<button><span>Go</span></button>` read as `"GoGo"` — and the duplication
compounded with nesting depth. The label is what the model chooses between, so a
doubled one is a wrong target, not a cosmetic flaw. Removed the re-append.

### The sixth instance, found on the way out

Having built form filling into the executor, the obvious question was whether any
caller could reach it. None could.

`fetch_run` constructs the agent with a URL and a goal — and no `text_provider`.
The agent's `_value_for` returns `None` without one, which becomes
`STATUS_NEEDS_TEXT` and ends the run. So the executor could fill a field and
nobody could ever ask it to: the same shape as `href` and the dead signature
fields, one layer further out.

`fetch_run` now takes `values`, a JSON object keyed by field name, and turns it
into a text provider:

```
fetch_run(url="https://example.com/search", goal="find the python docs",
          values='{"q": "python"}')
```

The provider matches on `name`, then on the visible label, and **returns `None`
for anything it has no entry for** — which stops the run with `needs_text` and
names the target. It deliberately does not fall back to "the only value I have",
because typing the wrong text into the right box is harder to notice than a run
that stops and says which field it wanted. Malformed `values` is reported before
the executor is even constructed, so a typo costs no network round trip.

### Verification

- `tests/test_system_one_http_executor.py` — **46 tests** (was 43): three
  end-to-end runs proving the form flow works and, just as importantly, that it
  *stops* when no value is available — `TYPE_TEXT` → the form is submitted at the
  expected URL → `DONE`, and the two `needs_text` cases.
- `tests/test_system_one_wiring.py` — **57 tests** (was 49): eight for the values
  path — the provider is passed only when values are given, it matches by name
  then label, it refuses a field it has no value for, malformed JSON is rejected
  before the agent is built, and `values` did not widen the host boundary.
- `tests/test_system_one_wave2.py` — **119 tests** (was 93): 26 for nested-label
  correctness, control state (`checked` / `selected` / `expanded`) from each
  adapter into the action space, form extraction with hidden fields, form
  indexing, and the two end-to-end "HTML in, usable table out" paths.
- `ruff` clean on every touched file (one real find: a loop variable named
  `field` shadowing the `dataclasses.field` import).

The remaining gap is honest and documented: `HtmlExecutor` cannot do anything a
plain HTTP request cannot — no JavaScript, no canvas, no client-side state. For
those pages Alpha still needs the Playwright path, and the two backends now
share the same guard seam, so switching between them does not change the safety
properties.

## Update 6: an audit for the same bug class

Six instances of "exists but nothing reads" had been found one at a time, each
while doing something else. That is not a way to find bugs — it is a way to
notice them. So this is the same class hunted deliberately, with the two
questions from the skill written out as a check:

> For every key you read, **which producer emits it** — a concrete function, not
> "the fixture"? For every capability you build, **which caller can invoke it**?

Applied across the browser stack, that turned up two more.

### A dropdown demanded a value and then discarded it

`BrowserDecision.needs_text` is `TYPE_TEXT`-only, but the agent's guard read:

```python
if decision.needs_text or decision.operation == SELECT:
    text = await self._value_for(...)
    if text is None:
        run.status = STATUS_NEEDS_TEXT   # <- a SELECT could never get past here
```

`_record_value` then took the value for a `SELECT` from the **target**, not from
`text` — the action space addresses an option as `"<element>:<offset>"`, so the
decision already names which one to choose.

So `SELECT` required a value it never used. Two consequences, both bad:

- **Every dropdown was unusable without a text provider.** No provider → no
  value → `needs_text`, every time, for a control the policy had already decided
  correctly.
- **A supplied value was silently ignored**, and the provider call was wasted.

Found by writing the end-to-end test before the fix, which is what turned
"this looks redundant" into a failure message:

```
AssertionError: got needs_text: no value available for SELECT on 1:1
assert 'needs_text' == 'done'
```

The fix is one condition — `if decision.needs_text:` — and `ruff` confirmed it
immediately by flagging the now-unused `SELECT` import in `jev_agent.py`: the
agent had no other reason to know what a `SELECT` was. The test now runs
`SELECT → CLICK → DONE` with no provider at all and asserts the form was
submitted at `…/pick?country=us`.

Two smaller things fell out of the same thread:

- `_record_value`'s docstring now says the target is authoritative for a
  `SELECT`, so the ignore is documented rather than accidental.
- The `TARGET` rubric said "Choose only an offered element index", but a dropdown
  offers `<element>:<option>`. It now says so, since that is the one place the
  model has to name half an index.

### A truncated head never reached the policy

The policy was told the table was incomplete only when elements had been dropped
by `MAX_ELEMENTS`:

```python
omitted = int(page_state.get("omitted") or 0)
if omitted > 0:
    state["table"] = {"complete": False, "omitted": omitted}
```

But there are **two** limits, and they are independent:

| Limit | What it drops |
|---|---|
| `MAX_ELEMENTS` → `omitted` | elements past the whole-table cap |
| `MAX_CHOICE_OPTIONS` → `space.truncated` | the tail of a *single* operation head |

`space.truncated` was computed in `build_action_space`, carried onto
`BrowserDecision`, and reported in the tool JSON — and never read on the decision
path. A head can be capped on a page with nothing omitted at all, so the model
could be choosing from a short list without knowing it was short. The rubric line
meant to cover this ("If the element table is incomplete, SCROLL") only ever
fired on `omitted`.

Both now reach the policy, and the rubric line names both ways a table can be
short. The existing test that asserts `"table" not in state` for a complete page
is what keeps this honest in the other direction — reporting incompleteness that
is not there would send the model scrolling for elements that do not exist.

### What the same audit found *clean*

Worth recording, because a sweep that only lists problems gives no sense of
coverage:

| Chain | Producer → carrier → consumer |
|---|---|
| `coords` | `dom_snapshot` (both adapters) → `Element.coords` → `SupervisorExecutor` clicks by coordinate |
| `history` keys | agent writes `operation` / `target` / `text` / `element` / `ok` / `changed` / `evidence` / `detail`; the policy reads exactly those eight |
| `target_confidence` | set by the policy, **gated** (`target_answer.meets(threshold)`), and reported — not merely recorded |
| `ExecutedStep` | all ten fields appear in `to_dict()` |
| `omitted` | `dom_snapshot` → `page_state` → `state["table"]` for the policy, and the tool payload |
| `selector` | `dom_snapshot` → `Element` → `PlaywrightExecutor` |

### The one gap left open on purpose

`PlaywrightExecutor` is complete and has no tool entry point — which, by this
audit's own logic, is a capability nothing can invoke. It stays that way
deliberately: **Playwright is not installed** in this environment, so a tool
action reaching it could not be tested, and an untested entry point is worse than
a documented gap. The two checks that would apply are ready when it matters:
`PlaywrightExecutor` already reports `tracks_changes = True`, and it is driven
through the same `_browser_guard_options()` seam, so wiring it later cannot
bypass the loop guards.

What it would add over `HtmlExecutor` is exactly what HTTP cannot do: JavaScript,
client-side state, and canvas. Those pages are unreachable today, and the run
says so rather than reporting success.

### Verification

- `tests/test_system_one_http_executor.py` — **47 tests** (was 46): the SELECT
  run, end to end with no text provider.
- `tests/test_system_one_wave2.py` — **123 tests** (was 119): a truncated head is
  reported; a truncated head *and* omitted elements are reported together; the
  existing "complete table says nothing" case still holds; and the HTML parser's
  deliberate drop of a disabled control is pinned as a decision rather than left
  as an accident.
- The seven System One suites — **393 passed** with the SELECT fix and the rubric
  change in place, so nothing depended on the old behaviour.
- Full fourteen-file regression — **571 passed, 1 skipped**.
- `ruff` clean; the unused `SELECT` import it flagged was the proof the fix was
  complete.
