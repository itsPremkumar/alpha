# Alpha — Known Issues

Every entry is either **FIXED** (test-proven, evidence named), **WIRED**
(implemented and attached to the Gateway lifespan), or **SPECIFIED** (root-caused
and designed, not yet implemented). This file is the authority; where it
conflicts with aspirational prose elsewhere, this file wins.

Cycle 3's finding was structural rather than another missing feature: **the suite
proved routes were *mounted*, never that their bodies *ran*.** A handler whose
statement is mis-parsed stayed green until a human called it. That class is now
closed by enumeration, not by a longer list — see `ALPHA-BUG-0016`.

## Open

### P1 — A productive run is stopped by the loop detector before its last required step

Observed live in `logs/e2e_run3.log`. The agent researched U+2019 (three
independent sources agreeing, `Pf`, Unicode 1.1), wrote both output files,
**found and corrected three of its own arithmetic errors** with a documented
derivation, and disclosed that every direct page fetch had failed with HTTP 401
so the claim rested on snippets rather than page bodies. It was then cut off by
`LoopDetectionMiddleware` ("Repetitive tool calls detected - injecting warning")
with `present_files` never called, so the run failed `delivery_incomplete` and
the artifacts were never delivered.

`config.yaml -> loop_detection` is `warn_threshold: 3`, `hard_limit: 5`,
`window_size: 20`. The call-signature histogram of that run shows no signature
repeating more than twice, so the warning plausibly fired on the *tool-frequency*
layer (`tool_freq_warn: 30` inside a 20-call window) rather than the
identical-call-set layer. Not yet root-caused: the warning's `extra` payload
carries `call_hash`/`count`/`tools` but the **rendered** log line names none of
them, so an operator cannot tell which layer fired or why. Decide first whether
the frequency window should count *productive* repeated calls (reading two
different ranges of one file it is actively editing is not a loop), then add the
threshold and layer to the message.

### P2 — `delivery_incomplete` fails a run whose artifacts were correctly produced

The same run. Both files existed and were verified by the agent, which *itself*
reported that presentation was missing — and the run still terminalised as
`error`. This is the documented contract ("missing a matching presentation
becomes a run error") and it is the right default; the open question is whether
the gate should attempt a **recovery** pass that runs when the graph reaches its
end with produced-but-unpresented outputs, instead of only failing. The
requirement is already stated twice in the system prompt
(`agents/lead_agent/prompt.py:690,834`) — the gap is enforcement timing, not
documentation.

### P2 — Failure visibility queue (frontend)

Unchanged from cycle 2. `flash()` in `ChatView.tsx` is one `string | null` slot
with a 4.5 s auto-clear and ~60 call sites, duplicated per-section. Concurrent
failures overwrite each other.

### P2 — Crash-loop supervisor wiring (Windows launcher)

`ProcessSupervisor` + `RestartLedger` are built but not connected to
`start.ps1` / `watchdog.ps1`. A crash-looping service is not yet quarantined
after N restarts.

### P2 — Error reporter SSE leg: operator surface

The leg is bound and publishing, but no `GET /api/ops` route reads
`app.state.error_reporter_sse` or `alpha_errors_sse_unbound_total`.

### P2 — Coded nginx 502/504 (live acceptance owed)

Configuration is done and pinned in all three shipped configs. The live proof —
compose stack up, Gateway stopped, `GET /api/features` returning the coded JSON
body — is still owed; there is no Docker daemon in the development environment.

### P2 — Idle-Gateway CPU burn (incident #4)

Did not reproduce. Stays above the <1 % target; the 5-min sample and the
loop-counter regression guard are the remaining acceptance steps.

### P3 — Behaviour-trace spine is default-off

`ObservabilityConfig.enabled` defaults `False`. The absence of a trace file is
not distinguishable from a disabled recorder at the operator surface.

### P3 — No frontend client telemetry

One error boundary and console writes; no `sendBeacon` / analytics path.

## Environment limits observed (not Alpha defects)

Recorded so a later cycle does not re-investigate them as bugs.

- **`bash` and `python_repl` are unbound by operator choice.**
  `config.yaml -> sandbox` sets `allow_host_bash: false` and
  `allow_in_process_repl: false`; `is_host_bash_allowed()` is `False` and
  `get_available_tools()` returns **144** tools, none of them a shell. The
  grounding gate refused the agent's `bash` call correctly and named the reason.
  A deployment with no code-execution capability cannot be asked to run a test
  suite; that tests the operator's security decision, not the agent.
- **Existing `config.yaml` files may pin `alpha-free` to one endpoint.** The shipped
  `config.example.yaml` now uses `model: auto` for provider failover; a pin remains
  an operator choice and retains the single-endpoint behavior documented by
  `ALPHA-BUG-0007`.
- **Outbound network is restricted.** `api.openrouter.ai` and `pypi.org` do not
  resolve; `api.kilo.ai`, `opencode.ai`, `vireonix.ai`, `blockrun.ai` do. The
  keyless free LLM path works end to end, and `web_search` works; `web_fetch`
  returns HTTP 401 from the configured backends.
- **`SERPER_API_KEY` / `TAVILY_API_KEY` are `your-…` placeholders** and
  `OPENROUTER_API_KEY` is empty.

## Fixed (cycle 3)

Every fix below was reproduced live first, then pinned by a test that fails
without it.

| # | Severity | Symptom | Root cause | Fix | Tests |
|---|---|---|---|---|---|
| ALPHA-BUG-0016 | **P1** | `GET /api/intelligence/inventory` returned **HTTP 500 for every caller** while 50+ unit tests of the code it calls stayed green | `return await _read(build_self_inventory, ...).to_dict` — `await` binds looser than attribute access, so this parses as `await (_read(...).to_dict)` and reads `.to_dict` off a **coroutine**. Found by booting a live Gateway and sweeping routes, not by any test | Parenthesise it. Close the class: `scripts/find_await_precedence.py` (AST scan, negative-controlled, `0` sites remain in `app/`, `packages/`, `tests/`), and `tests/test_intelligence_route_reachability.py` **enumerates** the router's routes instead of naming seven of them, so an added route is covered and an omitted one is impossible | 19 + scanner |
| ALPHA-BUG-0017 | **P1** | `GET /api/autonomy/sentinel/signals` took **18.4 s** and returned **0 signals** | `scan_directory` did `root.rglob("*.ps1")` then filtered on `SKIP_DIR_NAMES`. `rglob` has no prune hook, so the traversal cost was paid in full and the result discarded: measured **87,073 files in 24.1 s**, and the only three matches were all inside `.venv`, which the filter then removed. The correct pattern already existed in `knowledge/code_index.py` with a comment saying exactly this | `os.walk` with `dirnames` mutated in place (the house pattern), plus `.worktrees`/`.alpha`/`site-packages` added to the skip set. **24.1 s → 2.4 s, 10.1×** | 12 |
| ALPHA-BUG-0018 | **P1** | The same endpoint returned 0 signals because **it watched a tree with no PowerShell in it** | `_resolve_project_root()` called `runtime_paths.project_root()`, which resolves the **process working directory** — and the documented launch command is `cd backend && uvicorn app.gateway.app:app`. Every `.ps1` in this repo is at the root (`start.ps1`, `installer/`, `recovery/`, `scripts/`); none under `backend/`. One function answered "where does Alpha write its own state", the other asked "where is the repository" | New structural `runtime_paths.repository_root()` (marker walk, `ALPHA_REPOSITORY_ROOT` override); `capabilities.honesty.repo_root()` delegates to it instead of duplicating the walk. Sentinel now reports **real** signals and a planted negative control is **DETECTED** | 4 |
| ALPHA-BUG-0019 | **P1** | The log source produced **103 signals of which 0 were real** — 89 high-severity `test_failure` from pytest *parametrization ids* like `[pending-failed]` | Prefilter and classifier both matched **bare words** case-insensitively (`\bfailed\b`, `\bERROR\b`). A line that mentions a word is not a line that reports a fault. `test_failure` routes to the test-repair strategy, so the sentinel was manufacturing 89 phantom incidents aimed at auto-repair | Structural report forms only: traceback header, `ExceptionClass:` + colon, Alpha's own `- ERROR -` log record, and the runner's `FAILED `/`ERROR `/`E `/`=== FAILURES ===`/`N failed` forms. Plus an explicit **node-id is a name, not a result** rule. **103 → 3 signals, all 3 real**; log leg 7.33 s → 0.94 s | 58 |
| ALPHA-BUG-0005 | **P2** | `GET /api/multimodal/capabilities` could not answer inside a 30 s budget — first call measured **63.0 s**, second 0.33 s | The route called the pure-but-uncached report on every request; the observers *import* kokoro/piper/faster-whisper/rapidocr/openwakeword/webrtcvad. 63 s exceeds a default 60 s client timeout, so an operator on a fresh Gateway saw **nothing at all**. The WebSocket leg of the same report already had an honest try/except; the HTTP leg had neither a bound nor the disclosure | Process-local TTL cache behind `capabilities_report_cached()` with a **disclosed** `cache` block; route bounded by `CAPABILITIES_PROBE_TIMEOUT_SECONDS` with a 503 naming the bound, plus the WS-leg disclosure on failure | 13 + 1 updated |
| ALPHA-BUG-0008 | **P1** | The first cache fix turned one hang into **two**: cold 503 at 30.6 s, then an immediate second call *also* 503 at 30.7 s | The cache lock was held **across the whole build**. CPython cannot interrupt a thread already inside it, so `asyncio.wait_for` cancelled the *await* while the worker kept going, and the next caller then blocked on that lock until its own 30 s deadline expired — even though the answer was seconds away and the lock holder was about to publish it | The lock guards the **slot, not the work**. One thread builds; every other caller returns immediately — the previous answer with `building: true`, or an honest "still loading" disclosure when there is none. Verified live: cold 503 at 30.5 s → **second call HTTP 200 in 1.5 s** | 3 |
| ALPHA-BUG-0006 | **P3** | Tool-usage logs reported `Total tools loaded: 11` on a run whose agent received **143** | The line logged `len(loaded_tools)`, which is only the `tools:` block from `config.yaml`, under a name that reads as the total. An operator using it to work out why a tool was never called was reading the wrong number | Logged after dedup, with the real total and the per-source breakdown | — |
| ALPHA-BUG-0007 | **P2** | A provider exhaustion read `all 1 free provider attempt(s) failed (1 now cooling down)` and sent the investigation hunting a circuit-breaker bug | `free:<provider>:<model>` is a **pin**: every other provider is dropped because it does not offer that model id, so the chain has exactly one member and one HTTP 429 ends the run. Defensible behaviour, dishonest message — the picker advertises the same name as "Dynamic auto-routing … with failover" while 45 healthy models across 8 providers go unconsulted | `_no_failover_reason()` names the pin and the alternative (`models[].model: auto`) whenever exactly one candidate was attempted, and says the real cause when the single candidate was not a pin. A genuine multi-candidate outage gets no pin advice, and the candidate set is **unchanged** | 8 |

### Cycle-3 verification evidence

| Claim | Measurement |
|---|---|
| A live Gateway boots and answers | `scripts/route_sweep.py` over **177** parameterless GET routes: before **175 answered / 2 timed out**; `/api/intelligence/inventory` **500 → 200** |
| The sentinel sees its subject | planted BOM-less non-ASCII `.ps1` → **DETECTED**, `critical`/`missing_bom`; live repo scan 0 → 3 real signals |
| A real agent does real work | `scripts/e2e_hard_task.py`, three live runs against a keyless free gateway, **144 tools offered** |
| Tools are actually dispatched | run 3: **12 distinct tools**, 31 tool frames — `web_search`×8, `hashline_edit`×8, `web_fetch`×6, `hashline_read`×6, `write_file`×4, `read_file`×3, `keyless_web_search`, `code_mode`, `catalog_tool_search`, `list_dynamic_tools`, `verify_command_approval`, `glob` |
| Self-correction is real | the agent found **three** arithmetic errors in its own working and replaced them with a stated derivation (35 letters + 7 spaces + 1 period = 43) |
| Failure is reported honestly | it disclosed that all four direct page fetches returned HTTP 401 and that its Unicode conclusion therefore rested on search snippets, and recorded that caveat inside the artifact |
| Write guards work and recover | `str_replace` correctly blocked with `read_before_write` ("you have not read its current version… Call read_file"); the agent then called `read_file` with the exact suggested range |
| Regression | 393 passed / 1 pre-existing assertion updated, then **49 passed** on the touched multimodal suites; **66 passed** after `ruff format` |

## Fixed (cycle 2 — recorded in the roadmap)

| # | Severity | Symptom | Root cause | Fix |
|---|---|---|---|---|
| ALPHA-BUG-0011 | P1 | A lost run-admission response was never re-sent; a blind retry would have admitted a **second run** | The Gateway already scoped `Idempotency-Key`; the frontend sent no key and `apiFetch` is single-shot | `lib/idempotency.ts` |
| ALPHA-BUG-0012 | P2 | A transport drop on the initial POST ended the turn | Same gap | Idem |
| ALPHA-BUG-0013 | P2 | One outage rendered two disagreeing banners | `gatewayOk` and `serverHistoryError` are the same outage | Unified signal |
| ALPHA-BUG-0014 | P2 | A coded run failure never reached the client | No production `configure_error_reporter` caller | `app/gateway/error_sse.py` |
| ALPHA-BUG-0015 | P2 | A proxy failure reached the client as an HTML page | No `proxy_intercept_errors`/`error_page` in any of the three configs | Coded 502/504 JSON |

## Fixed (cycle 1 — recorded in the roadmap)

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| ALPHA-BUG-0007 → see cycle 3 | Every failed run rendered one indistinguishable sentence | Coded `event: error` was sent, parsed and pinned — and nothing read it | `StreamRunFailure` + `chat-support-id.ts` |
| ALPHA-BUG-0008 | A dropped stream re-dialed with zero delay | `retry_delay = 0` | 5-attempt equal-jitter ladder |
| ALPHA-BUG-0009 | Corrupt JSON store read as empty state | `except Exception: pass` in three stores | `is_degraded` / `is_durable` disclosure |
| ALPHA-BUG-0010 | `test_harness_refine_tool` failed on `main` | Tool invoked with no `runtime` | Repaired at the call site, after reproducing on clean `main` |

> **ID reuse note:** the cycle-1 numbering above is the roadmap's, kept verbatim so
> the history stays readable. Cycle 3's `ALPHA-BUG-0005/0006/0007` are **new** ids
> in a range the roadmap left free; they do not overwrite cycle 1's
> `ALPHA-BUG-0007`.

## Fixed (earlier cycles, recorded in the roadmap)

| # | Symptom | Fix |
|---|---|---|
| 1 | Checkpointer `database is locked` | 30 s busy timeout on every connection (`6e49de3`) |
| 2 | Run hung 20+ min with no heartbeat | Stall watchdog 900 s → terminal `error` + `stop_reason="stalled"`; tool-call budget 600 s (`3d4795b`) |
| 3 | Zombie run's SSE showed zero events for 877 s | Named `event: heartbeat` every 15 s |
| 4 | Idle Gateway burned 1442 s CPU | Not reproduced; stays open above the <1 % target |
| 5 | Error reporter SSE leg never fires | Honesty fixed in cycle 1; binding FIXED in cycle 2 |
| 6 | `e2e_real_task.py` both tasks green | Happy path works after Fixes 1–4 |
