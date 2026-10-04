# Alpha — Stability Report

**Cycle:** 3
**Date:** 2026-10-04
**Base commit:** `cc79d7c`
**Branch:** `main`

> Cycles 1 and 2 are preserved below. This cycle's report is the authority on
> the current state.

---

## Cycle 3

### What this cycle was actually about

Cycles 1 and 2 both ended with the same admission: *"no workload was run against
a live Gateway; every live row is static, not observed."* Cycle 3 closed that
gap. The keyless free-LLM path works from this environment without credentials
(`config.yaml -> free_gateways`, 10 gateways; `api.kilo.ai`, `opencode.ai`,
`vireonix.ai`, `blockrun.ai` reachable), so the agent was exercised for real:
three end-to-end runs of a hard multi-part task through the same SSE surface the
web UI uses, with a real model, real tools, real files.

Doing that immediately paid for itself. **Every bug found this cycle was
invisible to the test suite**, and each was found by one of three methods that
did not exist before:

1. **Boot the app and sweep every route** (`scripts/route_sweep.py`). The suite
   proves a route is *mounted*; nothing proved its *body ran*.
2. **Ask Alpha to do real work and read every frame** (`scripts/e2e_hard_task.py`).
3. **Static class scan** (`scripts/find_await_precedence.py`) once method 1
   produced a defect shape.

The theme is not "more bugs". It is that **Alpha's green suite was measuring the
wrong thing**, and three cheap harnesses were enough to show it.

### The most important finding

`GET /api/intelligence/inventory` — the entire self-knowledge HTTP surface —
returned **HTTP 500 on every request in a live Gateway**, and had done so
indefinitely. The cause was one misplaced pair of parentheses:

```python
return await _read(build_self_inventory, sections=wanted, detail=...).to_dict
```

`await` binds looser than attribute access, so this parses as
`await (_read(...).to_dict)` — it reads `.to_dict` off a **coroutine object**.
Fifty-odd unit tests of `build_self_inventory` passed throughout, because they
exercise the function, never the route. The existing router test drove a
**hand-picked list of seven paths**; `/inventory` was not on it.

That is the defect class worth naming: **a test that enumerates nothing cannot
detect what it does not name.** The fix is therefore not "add `/inventory` to the
list". It is:

- `tests/test_intelligence_route_reachability.py` **enumerates** the router's
  parameterless GET routes, so a newly added route is covered automatically and
  an omitted one is structurally impossible.
- `scripts/find_await_precedence.py` AST-scans for the shape itself. It is
  negative-controlled (it finds the planted bug and clears the corrected form)
  and reports **0 sites** across `app/`, `packages/` and `tests/`.

### Bugs found and fixed

Seven, each reproduced live before the fix and pinned by a test that fails
without it — plus an eighth found by *verifying my own fix*, which is the part
of the loop worth reporting separately. Full detail, including reproduction
steps, is in [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md).

| ID | Sev | Symptom | Root cause in one line |
|---|---|---|---|
| ALPHA-BUG-0016 | **P1** | `/api/intelligence/inventory` 500s for every caller | `await f(...).attr` reads an attribute off a coroutine |
| ALPHA-BUG-0017 | **P1** | Sentinel signals route: 18.4 s, 0 signals | `rglob` then filter — the skip list never pruned, so `.venv`'s 87,073 files were walked and discarded |
| ALPHA-BUG-0018 | **P1** | Same route watched a tree with no `.ps1` in it | `project_root()` answers "where Alpha writes state" (the cwd → `backend/`), not "where is the repository" |
| ALPHA-BUG-0019 | **P1** | 103 log signals, **0 real**; 89 phantom high-severity `test_failure` | Bare case-insensitive word matching: `[pending-failed]` matched `\bfailed\b` |
| ALPHA-BUG-0005 | **P2** | Capability matrix: 63.0 s first call, no client can wait | Uncached pure report whose observers import six heavy engines |
| ALPHA-BUG-0008 | **P1** | The 0005 fix turned one hang into **two** — cold 503 at 30.6 s, second call 503 at 30.7 s | The cache lock was held **across the build**; CPython cannot interrupt that thread, so every later caller burned its own deadline waiting |
| ALPHA-BUG-0006 | **P3** | `Total tools loaded: 11` when 143 were offered | Logged one component of the tool set under a total's name |
| ALPHA-BUG-0007 | **P2** | `all 1 free provider attempt(s) failed` sent the hunt after a phantom circuit bug | A **pin** (`free:<provider>:<model>`) read as a failover router that had no candidates |

### The loop caught my own fix

`ALPHA-BUG-0005` is the one defect this cycle introduced and then repaired, and
the sequence is the argument for the debug → fix → **verify** → retry cycle.

1. Measured: cold capability probe 63.0 s, warm 0.33 s.
2. Fixed with a TTL cache, holding the lock across the build so one thread paid
   it. Route bounded at 30 s with a 503 naming the bound.
3. **Verified live** — and the warm call *also* 503'd, at 30.7 s.

The bound worked (an honest, actionable 503); the concurrency was wrong. CPython
cannot interrupt a thread already inside the build, so `asyncio.wait_for`
cancelled the await while the worker kept running, and the next caller blocked on
that lock until its own deadline expired — seconds before the lock holder would
have published the answer. One 63 s hang had become two 30 s failures.

The lock now guards the **slot, not the work**: one thread builds, every other
caller returns immediately with the previous answer and `building: true`, or an
honest "still loading" disclosure when there is none. Re-measured cold on a fresh
Gateway: **cold 503 at 30.5 s, second call HTTP 200 in 1.5 s.**

Had I trusted the passing unit tests — which were green for the broken version —
this would have shipped. Three of the eight bugs this cycle were found the same
way: by measuring something the suite already claimed was fine.

### Two of these deserve comment because the *behaviour* was correct and only the
### **reporting** was wrong

- **ALPHA-BUG-0019.** The sentinel's job is to make an operator act. 89 phantom
  incidents aimed at the auto-repair path are worse than silence. The classifier
  now accepts only *structural* fault reports — a traceback header, an
  `ExceptionClass:` with its colon, Alpha's own `- ERROR -` record, or the test
  runner's own `FAILED `/`E `/`=== FAILURES ===`/`N failed` forms — plus an
  explicit rule that **a pytest node id is a name, not a result**. 103 → 3
  signals, all 3 real, and the log leg got 7.8× faster.
- **ALPHA-BUG-0007.** `free:opencode-zen:space-bunny-free` genuinely is a single
  endpoint, and one 429 legitimately ends the run. What was wrong was that the
  picker advertises the same name as *"Dynamic auto-routing across verified
  keyless providers with failover"* while 45 healthy models across 8 providers go
  unconsulted. The message now names the pin and the alternative. The candidate
  set is unchanged — a message claiming failover while quietly trying 45 others
  would be a different lie.

### Evidence the agent really works

Not "the code exists". Measured, from `logs/e2e_run3.log`:

| Capability | Evidence |
|---|---|
| Real LLM, no credentials | `opencode-zen:space-bunny-free`; observed latencies 59.4 s and 6.2 s per call |
| Real tool dispatch | **12 distinct tools**, 31 tool frames: `web_search`×8, `hashline_edit`×8, `web_fetch`×6, `hashline_read`×6, `write_file`×4, `read_file`×3, `keyless_web_search`, `code_mode`, `catalog_tool_search`, `list_dynamic_tools`, `verify_command_approval`, `glob` |
| Real artifacts | both output files created, confirmed by `GET /runs/{id}/workspace-changes` |
| Real research | U+2019 → `Pf` (Final_Punctuation), Unicode 1.1 (June 1993), agreeing across three independent sources |
| **Self-correction** | found **three** arithmetic errors in its own working and replaced them with a stated derivation (35 letters + 7 spaces + 1 period = 43) |
| **Honest failure** | disclosed that all four direct page fetches returned HTTP 401, that its conclusion therefore rested on search snippets rather than page bodies, and recorded that caveat *inside the artifact* |
| Guard rails hold and recover | `str_replace` correctly refused a stale write ("you have not read its current version… Call read_file"); the agent then called `read_file` with the exact range the error named |
| Provenance on every result | `alpha_tool_receipt` with `args_sha256`/`output_sha256`/`output_bytes`, `alpha_tool_meta` status + `recoverable_by_model` + `recommended_next_action` |
| Liveness | 21 named `event: heartbeat` frames across the run |

### What still fails, and why it is not fixed here

**1. The loop detector stopped a successful run before its last required step
(P1).** In run 3 the agent had finished the research, written both files,
corrected its arithmetic, and was honest about what it could not confirm — then
`LoopDetectionMiddleware` cut it off with `present_files` never called, so the
run failed `delivery_incomplete`.

The signature histogram of that run shows no call signature repeating more than
twice, so the warning most plausibly came from the **tool-frequency** layer
(`tool_freq_warn: 30` within a 20-call window) rather than identical-call-set.
Reading two different ranges of a file the agent is actively editing is
productive work, not a loop, and the frequency layer appears to count it the same
way. I did not change the thresholds: doing so without first establishing which
layer fired would be guessing at a safety guard, and the honest next step is to
put the layer and the measured count in the rendered log line — the `extra`
payload already carries `call_hash`/`count`/`tools`, but the message an operator
sees names none of them, which is exactly why this cost a full investigation.

**2. `delivery_incomplete` fails a run whose artifacts were correctly produced
(P2).** Documented and intended ("missing a matching presentation becomes a run
error"), and the requirement is stated twice in the system prompt
(`agents/lead_agent/prompt.py:690,834`). The open design question is whether the
gate should *attempt delivery* when the graph ends with produced-but-unpresented
outputs, rather than only failing. Not attempted here.

**3. Environment limits, recorded so they are not re-investigated as bugs.**
`bash` and `python_repl` are unbound because the operator set
`sandbox.allow_host_bash: false` / `allow_in_process_repl: false` — verified
(`is_host_bash_allowed() is False`; 144 tools offered, none a shell). The
grounding gate refused the agent's `bash` call **correctly** and named the
reason; an earlier task in this cycle failed at step 3 because it asked for a
capability this deployment deliberately does not have. That is the security
choice working, and the task was retargeted rather than the gate loosened.

### Test counts

| Suite | Result |
|---|---|
| New: `test_sentinel_log_classifier.py` | **58 passed** (23 real-detection cases, 21 phantom cases, 4 contract cases) |
| New: `test_sentinel_scan_coverage.py` | **12 passed** |
| New: `test_intelligence_route_reachability.py` | **19 passed** (17 enumerated routes + the specific regression) |
| New: `test_multimodal_capabilities_cache.py` | **13 passed** |
| New: `test_free_router_exhaustion_honesty.py` | **8 passed** |
| Targeted regression (models, free router, catalog, self-inventory, sentinel, multimodal) | **393 passed, 1 failed** |
| The one failure | `test_capabilities_returns_probe_matrix_and_voice_block` pinned a **closed** payload key set; the required `cache` disclosure made it fail. Test updated to encode the new contract explicitly, then **81 passed** across the multimodal suites |
| After the 0008 concurrency fix + `ruff format`/`ruff check` on changed files | **68 passed**, `find_await_precedence` **0 sites** |
| Boundaries | `test_no_orphan_modules.py` + `test_harness_boundary.py` green; consolidated run of all new suites + the existing intelligence router test: **121 passed** |
| `scripts/route_sweep.py` (live, 177 GET routes) | before: 175 answered / 2 timed out, `/inventory` **500** → after: `/inventory` **200**, 176 answered, and the one remaining timeout is `/api/multimodal/capabilities` on a *cold* process, which now returns a bounded **503 with an actionable reason** instead of hanging |

`ruff format --check` still reports pre-existing failures under
`packages/harness/alpha/runtime/sentinel/` (`checkpoint.py`, `cli.py`,
`commit.py`, `loop.py`, `report_store.py`, `runner.py`, `scheduler.py`,
`signals.py`, `verify.py`) — confirmed untouched by this cycle
(`git diff --name-only` is empty for them). Not reformatted, to keep the diff
about this cycle's changes.

### Changes

| # | File | Change |
|---|---|---|
| 1 | `app/gateway/routers/intelligence.py` | parenthesise the `await`; comment names the precedence rule |
| 2 | `backend/scripts/find_await_precedence.py` | **new** — AST scan for the defect class, negative-controlled |
| 3 | `backend/scripts/route_sweep.py` | **new** — boot the real app, drive every parameterless GET route, fail on 5xx |
| 4 | `backend/scripts/e2e_hard_task.py` | **new** — boot the real app, assign a hard task, record every frame |
| 5 | `tests/test_intelligence_route_reachability.py` | **new** — enumerates routes instead of naming them |
| 6 | `packages/harness/alpha/runtime/sentinel/sources/scripts.py` | `os.walk` with in-place pruning; `iter_candidate_files`; skip set extended |
| 7 | `packages/harness/alpha/config/runtime_paths.py` | **new** `repository_root()` (structural marker walk); `project_root()` docstring states what it is *not* |
| 8 | `app/gateway/autonomy/loops.py` | `_resolve_project_root()` uses the structural resolver |
| 9 | `packages/harness/alpha/capabilities/honesty.py` | `repo_root()` delegates instead of duplicating the walk |
| 10 | `packages/harness/alpha/runtime/sentinel/sources/logs.py` | structural fault-report gate; class→kind/severity mapping; node-id rule |
| 11 | `packages/harness/alpha/multimodal/chain.py` | `capabilities_report_cached()` + `invalidate_capabilities_cache()`, disclosed; **the lock guards the slot, not the build** |
| 12 | `app/gateway/routers/multimodal.py` | bounded probe, 503 naming the bound, WS-leg disclosure on the HTTP leg |
| 13 | `packages/harness/alpha/tools/tools.py` | tool-count log moved after dedup and reports the real total |
| 14 | `packages/harness/alpha/models/free_router/catalog.py` | `_no_failover_reason()` names a single-endpoint pin |
| 15 | `tests/test_sentinel_log_classifier.py`, `test_sentinel_scan_coverage.py`, `test_multimodal_capabilities_cache.py`, `test_free_router_exhaustion_honesty.py` | **new** |
| 16 | `tests/test_multimodal_router.py` | closed payload set updated for the `cache` disclosure |
| 17 | `docs/reliability/KNOWN_ISSUES.md` | rewritten as the authority for this cycle |

### What I would do next, in order

1. **Name the loop-detector layer in its log line** (`extra` already has it),
   then decide whether the tool-frequency window should exclude reads that
   advance the work. This is the only P1 that is still open and it is the one
   that cost a successful run.
2. **Make the delivery gate recover rather than only fail** — attempt
   `present_files` for produced-but-unpresented outputs when the graph ends.
3. Extend `route_sweep.py` from an operator script into a **slow** CI job. It is
   the cheapest available detector for "mounted but broken", and it found two
   defects a green suite could not.
4. The P1 frontend failure-visibility queue, unchanged since cycle 1.

---

# Cycle 2

*(preserved; see `KNOWN_ISSUES.md` for the full cycle-2 table)*

Base `fefa49a`. Frontend **1488 passed**, backend targeted gates green. Fixed
ALPHA-BUG-0011…0015: idempotent run admission, transport-failure retry, unified
outage signal, the error-reporter SSE leg bound to the lifespan, and coded
502/504 bodies in all three nginx configs. Its own verdict was that
**live acceptance was still the gap**, gating six gates D–K.

# Cycle 1

*(preserved)*

Base `eaf1b32`. Fixed ALPHA-BUG-0007…0010: coded run errors reaching the client,
a zero-delay stream re-dial, corrupt JSON stores reading as empty, and a
pre-existing test failure. Its worst bug was invisible because the first link
worked — the Gateway sent a coded `event: error`, the frontend parsed it, a test
pinned the parse, and nothing read the result.

> **Cycle 3 confirms that lesson at a larger scale.** Cycle 1's bug was a parsed
> value nobody consumed. Cycle 3's was a mounted route nobody executed. Both are
> the same shape: *the chain was verified link by link and nobody verified the
> whole.* The three new harnesses — route sweep, hard-task E2E, static class scan
> — exist because a per-link test suite structurally cannot see it.