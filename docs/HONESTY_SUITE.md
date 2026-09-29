# The honesty suite

Six pytest files under `backend/tests/`, named `test_e2e_honesty_*.py`, that
exist to catch one class of defect: **the durable record disagreesing with the
answer the product gave.** No live server, no provider, no network, no sleeps,
no fixed ports. They run in CI on a clean checkout.

```
cd backend
$env:PYTHONPATH="."; .venv/Scripts/python.exe -m pytest tests/test_e2e_honesty_*.py -q
53 passed, 2 xfailed
```

The two `xfail`s are not aspirational. They are real defects this agent found
and was not allowed to fix (it does not own `backend/packages/`). They are
`strict=True`, so fixing either one turns them into a **failure** that names the
change and asks for the marker to be removed. See
[Known defects](#known-defects-reported-not-fixed).

## Why these exist

Four of the worst bugs found in this repository passed every test that existed:

| Bug | Why no test caught it |
| --- | --- |
| A failing `python_repl` call recorded as `status: "success"` | Every test fed the classifier `"Error: ..."`. The REPL emits `"Error (Name): value"`. |
| A documented cost fallback read by nobody | The test used a synthetic config, so it could not see a renamed or un-loaded key. |
| A retired model slug, in two namespaces, with no check comparing them | The only cross-namespace check compares *capabilities by name*, never the slug. |
| `GET /projects/{id}/events` emitted a raw float | The event contract is checked for field *existence*, not for the *type a reader receives*. |
| A completed delegation produced zero `subagent.*` events | The only writer is fed exclusively by one stream mode, which is not the default. |

Common shape: **each test asserted the shape the author imagined, not the shape
the product produces.** Every file below closes that gap by driving the real
producer and reading the durable record back.

## Design rules this suite holds itself to

1. **Import the real module.** No test asserts against a copy of the code, a
   re-implementation, or a string the author typed. Where a premise is needed
   (e.g. "the REPL really emits `Error (Name): value`"), it is asserted by
   *executing* the producer, and labelled as a premise.
2. **Read the durable record, not the emitter's intent.** Tests drive the real
   tool / journal / graph / store and then read back through the same read path
   the Gateway serves. A stubbed writer would defeat the purpose.
3. **Every test names the defect it pins and how that defect was found.** In the
   module docstring and, where it matters, again at the assertion.
4. **A control beside every claim.** Most files carry a test that proves the
   guard is measuring the thing it claims and not its own arithmetic.
5. **No test may pass for the wrong reason.** Where a refactor could silently
   stop covering a case, the premise is asserted explicitly.
6. **Deterministic.** Wall-clock values are computed, not hard-coded where
   avoidable; the one place a real clock is read is asserted to be
   timezone-aware rather than to a literal.

## What each file pins

### 1. `test_e2e_honesty_tool_failure.py` — a failing tool call is never a success

**Claim:** `alpha_tool_meta` on a tool result describes what actually happened.
**Durable record:** the stamp the classifier wrote, read back by
`ToolProgressMiddleware`.

The sandbox REPL builds its error body as
`f"Error ({error_name}): {error_value})"` (`sandbox/repl/protocol.py:36`), which
does not `startswith("Error:")`. The classifier used to match only the literal
prefix, so a raised `ModuleNotFoundError` fell through every branch to the
success default.

That is worse than a wrong label, because `ToolProgressMiddleware` reads the
**stamp**, not the text: its ACTIVE → WARNED → BLOCKED anti-thrash state machine
counts `status in ("error", "partial_success")`. A `success`/`continue` stamp
resets that counter to zero on every call, blinding the guard to the whole
`python_repl` family.

- Drives the **real** `python_repl_tool` (a real `ReplSession` executing real
  code) and the real classifier.
- Pins **both** conventions through the single `split_error_prefix` seam.
- Asserts the recommended action differs from the success case.
- Drives the **real** `ToolProgressMiddleware.wrap_tool_call` five times and
  requires WARNED (recoverable) and BLOCKED (stop-grade) respectively.
- **Control:** replays the exact historical `success`/`continue` stamp and
  requires the guard to stay at `active` with `consecutive_problems == 0`. That
  is what makes the passing tests mean something.

### 2. `test_e2e_honesty_error_fingerprint.py` — a stack fingerprint that survives redaction

**Claim:** a terminal run error carries a fingerprint that groups occurrences.
**Durable record:** the `err.raised` row in the real `RunEventStore`, drained
through the real `RunEventStoreSink`.

`TraceEnvelope.build` scrubs the payload through the strict `Redactor` at write
time, and that redactor erases high-entropy strings — a 64-char lowercase-hex
value is exactly a SHA-256 digest *and* some credentials. A digest in `payload`
is therefore replaced with `[REDACTED:high_entropy_blob]` before the row is
written: the event survives, says "something was here", and identifies nothing.
`TraceEnvelope.digests` exists for this and is sealed by a separate 64-hex gate.

The second half is a cross-worker property, and it is the half every existing
test was structurally incapable of making:

- Asserts the digest is in `digests` and **absent** from `payload`.
- Asserts against the **real redactor** that a digest in a payload is destroyed,
  so "it is in `digests`" is not a storage preference.
- Asserts no traceback text and no exception message reaches the row.
- Asserts the digest equals `sha256(formatted traceback)`, computed by the test
  from the same exception object using stdlib only.
- Asserts stability across **two independently built writers**, and across
  **two real OS processes** running the same generated worker file (a traceback
  records file paths and line numbers, so a shared file is the only way "the same
  failure" means the same bytes on both sides). A per-process salt is constant
  within one process and differs across two — invisible to every in-process
  test, which is why both assertions exist.
- Asserts a *different* failure gets a *different* digest, so the value is not a
  constant that satisfies "it is 64 hex characters".

### 3. `test_e2e_honesty_pricing.py` — the configured price table is reachable

**Claim:** a model priced in `config_pricing:` produces spend.
**Durable record:** a real `runs` row, aggregated by the real `/api/console/*`
routes on a real sqlite engine.

`config.example.yaml` shipped `model_pricing:` with eight entries,
`AppConfig` declared it, `ModelPriceEntry` validated it, and the config guide
documented it — and nothing read it. An operator who configured the documented
key got `total_cost: null` forever, with no error and no warning. A shipped key
that silently does nothing is worse than no key, because it looks
authoritative.

- Loads the **real** `config.example.yaml` through the production resolution
  chain (`ALPHA_CONFIG_PATH` → `AppConfig.resolve_config_path`), and asserts the
  Gateway really resolved that file. No `SimpleNamespace` config anywhere.
- Asserts a model priced **only** in the fallback table produces a non-null
  cost through `_build_pricing_map` / `_run_cost`, and through the HTTP routes.
- Asserts `models[].pricing` still wins, using a real `ModelConfig` carrying a
  real inline price against a **competing** fallback entry of the same name.
- Asserts **every** declared fallback entry reaches the pricing map, so one
  dropped key is caught.
- **Controls:** an unpriced model still reports `null` (the fix did not turn
  "unpriced" into "free"); the shipped models carry no inline price at all, so
  the whole cost surface really does depend on the fallback.
- Also pins that the template embeds **no credential literal**, on the raw file
  (see the env-interpolation note below).

> **Env-interpolation trap.** `AppConfig` interpolates `$VAR` against the
> process environment at load time. On a machine with `OPENROUTER_API_KEY`
> exported, the loaded `api_key` is a literal secret; on CI it is `""`.
> Asserting on the loaded value makes a test's verdict depend on whose laptop ran
> it — and prints a real key into the failure message. This suite therefore
> asserts environment-dependent fields against the **raw template text**.

### 4. `test_e2e_honesty_model_slug.py` — a slug in two namespaces is compared across them

**Claim:** a model name that appears in both `models[]` and `model_catalog`
resolves to the same provider-side identifier.
**Durable record:** the file the Gateway actually loads.

`models[].model` is the slug the runtime sends. `model_catalog.models[].model_id`
is the slug the Settings picker synthesises a `ModelConfig` from
(`AppConfig.get_model_by_name`). OpenRouter retired `stealth/union-alpha`; one
namespace was moved and the other was not, so a fresh install 404'd on a model
the picker still advertised. `catalog_consistency` cannot catch it: its
`COMPARED_FIELDS` are capabilities, compared **by name**, and the merge is
first-wins by name, so the stale value simply loses and survives unnoticed.

- Checks **every** overlapping model, not just the baseline, so the next
  rename-shaped drift on a new entry is caught.
- Reads the file via the production resolution chain and asserts it resolved
  that file.
- Asserts the baseline slug as a **value**, so a provider retirement is a named
  test failure rather than a 404 on a fresh install.
- **States the gap as a test:** asserts `catalog_consistency` does not compare a
  slug, *and* proves behaviourally that a deliberately stale slug leaves the
  boot check green. If the slug is ever added to that check, these fail and the
  explicit comparison above may be redundant.
- **Controls:** a model declared in only one namespace is not an error, and the
  test asserts the asymmetry it tolerates actually exists, so the comparison is
  never vacuous.

**Not covered, deliberately:** whether a slug still exists at the provider. That
needs a network call and a provider account. A string comparison cannot prove
liveness and this file does not pretend otherwise.

### 5. `test_e2e_honesty_event_timestamps.py` — every run-event `created_at` is a string

**Claim:** `created_at` is an ISO 8601 string on the way to a reader.
**Durable record:** the store's own `list_events` read path, per backend.

`GET /projects/{id}/events` emitted a raw epoch float where every sibling route
emitted ISO, so the client rendered "time not reported" for every row. That was
fixed at that one boundary. The same shape can occur anywhere a value crosses
from a store into a response, and the run-event feed has three backends with
three write paths and three read paths.
`contracts/run_event_stream_contract.json` declares `created_at` as
`{"type": "string", "format": "date-time"}`, and the existing contract test
validates the *schema* — but only against records the producer happened to build
correctly. Nothing checked the type a reader actually receives.

- Drives the **real** `RunJournal` callbacks (`on_chain_start`,
  `on_chat_model_start`, `on_llm_end`, `on_tool_end`, `on_chain_end`) into each
  of the memory, db and jsonl backends, and asserts every row is a timezone-aware
  ISO string.
- Asserts the same for what the **real** `list_run_events` handler returns —
  the last hop, i.e. what a client receives.
- Reads the contract from the shipped JSON, so loosening the type has to be
  acknowledged.
- Asserts a float **cannot be written at all** (the abstract interface types it
  `str | None`; no backend widens it) and that the db backend **rejects** one.
- Two `xfail(strict=True)` tests pin the defect the other two backends have.

### 6. `test_e2e_honesty_delegation.py` — a completed delegation is visible somewhere

**Claim:** the agent delegated work, so an operator can see that it did.
**Durable record:** the checkpointed `ThreadState.delegations` channel, read back
through a real `InMemorySaver` checkpoint and the real
`serialize_channel_values_for_api`.

A genuinely successful delegation — `task` called, subagent executed, result
recorded, tokens attributed — produced **zero** `subagent.start` /
`subagent.step` / `subagent.end` events on the parent run. Nothing errored; the
run finished `success`. Every surface an operator could read said the agent did
nothing.

**Authoritative surface today: `ThreadState.delegations`.** Written by
`DurableContextMiddleware` on every model call, with no dependency on stream
mode, provider, worker count or persistence backend, and it survives
summarization because it is a separate state channel rather than message text.

**Declared but conditionally emitted: the `subagent.*` run events.** The catalog
declares all three, the contract documents them, and `backend/docs/RUN_EVENT_STREAM.md`
lists them as the subtask-card backfill path. But the only writer is
`_SubagentEventBuffer` (`runtime/runs/worker.py`), fed from exactly one place:
`_publish_stream_item(..., mode == "custom")`. A run that does not request the
`custom` stream mode — **the default** in
`alpha.runtime.stream_modes.normalize_stream_modes`, and what the IM channels and
the LangGraph SDK default to — never feeds it. That is the mechanism behind
"zero events on the parent run".

- Builds a **real** `create_agent` graph with the real `ThreadState` schema, the
  real `merge_delegations` reducer, the real `DurableContextMiddleware` and a
  real `@tool`-decorated `task` driven through a real `ToolNode`.
- Asserts the completed entry survives the merger, is not downgraded by a later
  non-terminal observation, and is reachable through a real checkpoint.
- Asserts the `subagent.*` events are declared in the runtime catalog **and** in
  the shipped contract (a declared event nothing can emit is a documentation
  lie; an emitted event nothing declares is an undeclared contract).
- Pins the `custom`-mode condition by driving `_publish_stream_item` both ways,
  so removing the condition fails a **named** test that says the header is out of
  date.
- **Control:** a subagent step in category `subagent` must stay out of
  `list_messages` — otherwise a subagent's internal tool output would start
  appearing as chat messages.

## Bite proofs

Every guard was verified by breaking the code it guards, observing the failure,
and restoring. The mutation was applied in this worktree only and is not part of
the commit.

| File | Mutation | Result |
| --- | --- | --- |
| 1 | `_ERROR_BODY_RE` reverted to `^Error:` (the historical bug) | **8 of 10 failed**, incl. `consecutive_problems == 0 == 3` — the guard went blind |
| 2 | `stack_sha256=_sha256_text(_PROCESS_SALT + stack_text)` with a module-level `os.urandom` salt | **2 failed**: the pure-function test *and* the two-process test; the two-fresh-writers test **passed**, proving it is necessary but not sufficient |
| 2 | `emit_error` puts the digest in `payload` instead of `digests` | **6 failed** |
| 3 | `_build_pricing_map` iterates `...items()[:0]` (table ignored) | **5 failed** — the original "every cost number is null" defect |
| 4 | `model_catalog` `model_id: stealth/union-alpha` (the original drift) | **1 failed** with a message naming both fields and both namespaces |
| 5 | `DbRunEventStore._row_to_dict` emits `val.timestamp()` (the projects bug) | **2 failed**: `created_at is float (1790650078.56), not a string`, at the store *and* at the API boundary |
| 6 | `DurableContextMiddleware._capture_delegations` returns `None` | **4 failed** — no delegation ledger anywhere |
| 6 | `_publish_stream_item` gate widened to `("custom", "values")` | **1 failed** — "a non-custom stream now persists subagent events; the header of this file is out of date" |

## Known defects (reported, not fixed)

This agent does not own `backend/packages/`, so both are reported rather than
fixed, and pinned as `xfail(strict=True)` so CI stays green while the guard is
armed.

**A legacy float `created_at` reaches readers on the memory and jsonl
backends.** `MemoryRunEventStore._put_one`
(`packages/harness/alpha/runtime/events/store/memory.py:71`) and
`JsonlRunEventStore` (`.../store/jsonl.py:162, 219, 238`) store `created_at`
verbatim on write and apply no coercion on read. The db backend rejects the same
write (`datetime.fromisoformat` raises `TypeError` on a float), so the three
backends disagree about a value the contract declares as a string. An event log
written by an older release keeps handing floats to every reader of
`GET /runs/{id}/events` — the same shape as the projects-events bug, which was
fixed at that one boundary.

*Suggested fix:* coerce on the read path with `alpha.utils.time.coerce_iso`, as
`app/gateway/routers/projects.py:773` already does for project events. No
migration needed: the on-disk value can stay as it is.

**Pre-existing, unrelated to this suite:** two existing tests
(`test_console_model_pricing_fallback.py::test_the_shipped_template_table_is_reachable_by_the_console`
and `test_durable_context_middleware.py::TestMiddlewareRegistration::test_registered_before_summarization`)
raise `FileNotFoundError: config.yaml file not found` on a clean checkout,
because they call `get_app_config()` without pointing `ALPHA_CONFIG_PATH` at
anything and `config.yaml` is gitignored. Verified by copying
`config.example.yaml` to `config.yaml` in the worktree — both then passed. The
files in this suite do not have that dependency: they copy the template into
`tmp_path` and set `ALPHA_CONFIG_PATH`, so they pass on a clean checkout with no
operator action.

## What this suite can and cannot catch

### Can

- **Disagreement between a claim and its durable record**, when both sides can be
  produced offline: a classifier stamp vs. the tool body, a configured price vs.
  a reported cost, a written event vs. a served one, a fingerprint vs. the stack
  it summarises, a declared namespace vs. the other namespace's value.
- **A shipped, documented, validated configuration key that nothing reads** — by
  loading the real template through the real loader rather than a synthetic one.
- **A declared-but-unemitted surface**, asserted as such, with the condition
  named so removing it fails a test that explains itself.
- **Values destroyed by a defence in the wrong layer** (a digest erased by
  redaction), because the payload and the sealed field are both asserted.
- **Cross-process properties**, by actually starting a second interpreter.
- **Vacuous tests**, via per-file controls that re-run the same path with the
  discriminating input changed.

### Cannot

- **Anything requiring a provider, a network, or a live stack.** Whether a slug
  still exists at OpenRouter; whether a price is *current*; whether a run really
  404s on a fresh install; whether the frontend renders the fields correctly.
  These are `live` tests or manual verification, and this suite does not mock the
  thing it is meant to check in order to claim coverage.
- **Cross-worker behaviour of the durable runtime.** `RunStore` is
  single-process; a lease, a heartbeat, or a takeover race cannot be exercised
  from one interpreter. The suite asserts the *digest* is stable across
  processes; it does not claim the store is.
- **Real concurrency or timing.** No sleeps, no clock races, no
  load. A guard that is blind only under contention is out of reach here.
- **Genuine provider behaviour of a subagent.** The delegation test replaces the
  subagent with a real tool that returns a real terminal `ToolMessage` with the
  real structured metadata. It tests the *recording* of a delegation, not the
  execution of one.
- **UI rendering.** "The client showed *time not reported*" is not reproducible
  here; only the server-side value that causes it is.
- **That the defects in this table are the *only* ones of their kind.** Each
  file pins a named instance. A second instance of the same shape in a module
  nobody thought to look at is still uncaught — that is what a class of bug is.

## Maintenance

- When a guard here fails, the failure message is written to be actionable on its
  own: it names the field, the two disagreeing values, the namespace, and why
  nothing else would have caught it. Please keep that property.
- When a fix lands for an `xfail`, remove the marker in the same change. CI will
  tell you if you forget.
- New honesty files follow the naming `test_e2e_honesty_<subject>.py` so a single
  glob runs the whole suite. Do not add a fixture to `tests/conftest.py` for
  them; each file is self-contained and adds nothing to the shared surface.
