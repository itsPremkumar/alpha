# Deep improvement plan

Companion to [MULTI_AGENT_PLAN.md](MULTI_AGENT_PLAN.md), which is the *how* (worktrees,
assignment template, merge order). This is the *what* — the defects measured on `main` at
`4584cd6`, why each one matters, and who owns it.

Every entry below was found by running or reading the code, not by inference. Where a
finding is a claim I could not fully verify, it says so.

## The pattern underneath all of it

The bugs that got shipped here share one shape: **a claim that is more specific than the
evidence behind it.**

| Shipped claim | What was actually true |
| --- | --- |
| "a failing tool call is recorded as its result" | `status: "success"` beside `Error: File not found` |
| "the configured price table is authoritative" | eight entries, read by nobody |
| "the default model works out of the box" | a slug the provider had retired, 404 on first run |
| "the event feed shows when things happened" | a raw float where every sibling route sent ISO |
| "the cost of this run" | `null`, always |
| "this run hit a budget" | it hit a **recursion limit**; no budget existed |
| "this run delegated to a subagent" | the contract declares the events; nothing emits them |
| "this tool call's status is honest" | the guard reads a field that is always empty |

Two of these — the price table and the retired slug — were *configuration*, and the tests
that should have caught them asserted that the config parsed. They did. Parsing is not
reading, and declaring is not emitting. A test that checks a value was accepted proves the
loader works; it says nothing about whether anything consumes it.

So the standing rule for this wave: **for every fix, pin the consumer, not the
declaration.** If nothing reads it, the test must fail when the reader is removed.

## Wave 1 — dispatched, in flight

| Worktree | Owns | Model / effort |
| --- | --- | --- |
| `alpha-iso` | raw float epochs on the wire, 5 confirmed instances + a sweep | `space-bunny-free` / high |
| `alpha-ui` | run inspector: what tests cannot see, then legibility | `space-bunny-free` / xhigh |
| `alpha-security` | deployment-surface drift; the asymmetric nginx guard | `space-bunny-free` / high |
| `alpha-e2e` | a committed, CI-runnable honesty suite for these four classes | `space-bunny-free` / high |

## Wave 2 — the measured findings

### 1. The error vocabulary tells the operator something untrue

`backend/packages/harness/alpha/errors/registry.py:474`

```python
exception_types=("BudgetExceeded", "TokenBudgetExceeded", "RecursionLimit"),
```

`RecursionLimit` is folded into `RUN_QUOTA_EXCEEDED`, whose message is *"A run or token
budget for this thread is exhausted."* A recursion limit is the graph being too deep. No
budget was consumed, none is exhausted, and the remediation is completely different —
raise the limit or shorten the chain, not buy tokens. An operator reading this is told to
do the wrong thing.

This is the highest-leverage item in the plan because `registry.py` is the vocabulary
*every* surface reports through. A wrong code here is wrong in the API, the UI, the run
record, and the logs simultaneously.

**Owner:** `alpha-errors`. Task: audit the whole registry for codes whose message asserts
something unprovable, and codes that conflate genuinely distinct causes the way this one
does. Not just this line — the class.

### 2. Delegation is declared in the contract and emitted nowhere

`contracts/run_event_stream_contract.json` declares `subagent.start`, `subagent.step`,
`subagent.end`. A search for those strings in `backend/` returns hits in
`test_run_events_endpoint.py` and `test_run_event_store_filter.py` — **tests only**. There
is no production emitter.

A delegation that genuinely happened (task called, result recorded, subagent tokens
attributed) produced zero of these events. So the run inspector, and anything else that
reads the feed, sees an empty delegation on a run that really delegated. The delegation
*is* recorded — in `ThreadState.delegations` — so the data exists and is simply not
published where the contract says it is.

This is a contract that lies by omission, which is worse than a missing one, because the
contract is what tells a reader the events should be there.

**Owner:** `alpha-delegation`. Task: emit them at the real lifecycle points, or, if the
honest answer is that the feed will never carry them, delete them from the contract and
say which surface is authoritative. Either is acceptable. Silence is not.

### 3. A correctness guard that cannot fire

`backend/packages/harness/alpha/runtime/journal.py:160`

```python
meta = (payload.get("additional_kwargs") or {}).get("alpha_tool_meta")
```

`_with_honest_tool_status` is a 30-line function with a careful docstring explaining that
it reads the verdict `ToolErrorHandlingMiddleware` already stamped, so there is "one rule,
and the one the rest of the run acted on." The reported reason for the dead `model_pricing`
key was that *nothing read it*. This is the same failure wearing a docstring: if the event
rows carry `additional_kwargs: {}` — which is what the earlier investigation found — the
function returns `payload` unchanged on every single call, and the event feed reports
`success` where the checkpoint says `error`.

Note the sibling: `subagents/executor.py:489` and `:548` both special-case
`alpha_tool_meta`, so the key *is* stamped somewhere. The question is whether it survives
into the journal's event payload, and that is a measurement, not a guess.

**Owner:** `alpha-journal`. Task: **prove first, fix second.** Write the test that
demonstrates whether the guard fires. If it does not, find where the stamp is lost and fix
the loss — not the reader.

### 4. `pricing` has no schema, so a typo is silently free

`backend/packages/harness/alpha/models/factory.py:228-233`

```python
_EXTRA_NON_CONSTRUCTOR_MODEL_KEYS = frozenset({"pricing"})
```

`ModelConfig` is `extra="allow"` because operators legitimately pass provider kwargs
(`api_key`, `base_url`, `max_tokens`). That is correct and must stay. But it means
`pricing: {inpt_per_million: 1.0}` — one typo — parses cleanly, reaches
`console._build_pricing_map`, matches nothing, and yields `total_cost: null`.

This is the `model_pricing` dead-key bug one level down. We made the *fallback table*
readable; the *inline per-model* table is still unvalidated. The fix is a declared schema
for the known key inside a free-form extras map, which is the standard way to keep both
properties.

**Owner:** `alpha-pricing`. Task: declare it, fail loudly on an unrecognised shape, and
prove the typo now fails at load rather than at report time.

### 5. 562 files do not satisfy the formatter the CI enforces

```
562 files would be reformatted, 2768 files already formatted
```

334 of those are under `backend/packages/harness/alpha/` — the core, not the edges. The CI
gate is scoped (`scripts/ruff_scope.py`), so this does not fail the build, which is
precisely why it accumulated: the scope is honest about what it lints and silent about the
rest.

Worth doing for its own sake, and worth doing as the control experiment for the model
table — it is genuinely mechanical work, so it should go to the cheapest model that can do
it, and it should need no test suite at all beyond the formatter's own exit code.

**Owner:** `alpha-format`. Task: reduce the count in coherent batches, re-verify, and
report the number before and after. Formatting is not a behaviour change, so the gate is
the formatter plus the suite for the touched packages.

## The suite itself

The full backend suite — 1207 test files, serial, no `pytest-xdist` in the venv — is
running now. Its result is the single most important unknown in this document. Every claim
above is scoped and targeted; nothing here establishes that `main` is green end to end, and
until that run reports, nobody should say it is.

## Honest limits of this plan

- **None of wave 2 has been merged.** All five are in separate worktrees, unverified
  against the full suite.
- **The screenshot sweep is still not done.** It needs a running stack, and the stack has
  one owner — me. No agent starts it. This is the largest remaining gap between "the
  operator can see everything" and "I have confirmed the operator can see everything," and
  it is a gap in *evidence*, not in code.
- **"562 files" is a count, not a plan.** The format owner decides the batching; a
  562-file diff in one change is unreviewable and should not happen.
- **`ruff format` is not `ruff check`.** Formatting a file proves nothing about whether it
  is correct, and the debt report is explicitly non-gating. Passing the formatter is not
  passing a test.
- The findings above are what I measured. I have not read all 1207 test files, and a
  defect of this class in a surface I did not touch is entirely possible.
