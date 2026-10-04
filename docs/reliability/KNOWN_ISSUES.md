# Alpha — Known Issues

Every entry is either **FIXED** (test-proven, commit named), **WIRED**
(implemented and attached to the Gateway lifespan), or **SPECIFIED** (root-caused
and designed, not yet implemented). This file is the authority; where it
conflicts with aspirational prose elsewhere, this file wins.

## Open

### P1 — Failure visibility queue (frontend)

`flash()` in `ChatView.tsx` is one `string | null` slot with a 4.5 s auto-clear
and ~60 call sites, duplicated per-section. Concurrent failures overwrite each
other. A bounded list with a persistent error badge is the fix; it is a broad
`ChatView` refactor across ~15 components. Not started.

### P2 — Crash-loop supervisor wiring (Windows launcher)

`ProcessSupervisor` + `RestartLedger` are built but not connected to
`start.ps1` / `watchdog.ps1`. A crash-looping service is not yet quarantined
after N restarts.

### P2 — Coded nginx 502/504 (live acceptance owed)

The configuration is done and pinned in all three shipped configs
(`test_nginx_coded_upstream_failures.py`), including the decision *not* to
intercept 503. What is not yet done is the live proof: with the compose stack up
and the Gateway stopped, `GET /api/features` must return the coded JSON body and
an SSE request must fail with its own client-side error rather than a rewritten
body. The Docker daemon was not running when this was written, so the claim is
static, not observed. See `RELIABILITY_ROADMAP.md` §3.2.

### P2 — Error reporter SSE leg: operator surface

The leg is bound and publishing (§3.1 of the roadmap), but no `GET /api/ops`
route yet reads `app.state.error_reporter_sse` or
`alpha_errors_sse_unbound_total`. An operator can currently see the counter only
through the metrics endpoint.

### P2 — Idle-Gateway CPU burn (incident #4)

Did not reproduce across two e2e sessions (0.91 s over 60 s wall, ≈1.5 %), but
stays above the <1 % target. The 5-min sample and the loop-counter regression
guard are the remaining acceptance steps.

### P3 — Behaviour-trace spine is default-off

`ObservabilityConfig.enabled` defaults `False`. The absence of a trace file is
not currently distinguishable from a disabled recorder at the operator surface.

### P3 — No frontend client telemetry

The browser has one error boundary and console writes; no `sendBeacon` /
analytics path. Client-side failures are visible only where the code already
writes to `console`.

## Fixed (this cycle — cycle 2)

| # | Severity | Symptom | Root cause | Fix | Tests |
|---|---|---|---|---|---|
| ALPHA-BUG-0011 | P1 | A run admission whose response was lost was never re-sent, and a blind retry of `POST /runs/stream` would have admitted a **second run** | The Gateway already scoped `Idempotency-Key` on thread-scoped admission and deduped a reuse, but the frontend sent no key and `apiFetch` is single-shot — the safe-retry precondition existed and was never used | `lib/idempotency.ts`: per-send key + bounded equal-jitter retry of **transport failures only** (no response received), same key/path/body, opt-in per call site; `sendMessage` uses it for the stream POST only | 9 + 1 |
| ALPHA-BUG-0012 | P2 | Every failed run had to be re-sent by hand, and a laptop that slept mid-send lost the turn | Same as above: with no retry there was no recovery path at all for a transport drop on the initial POST | Idem | idem |
| ALPHA-BUG-0013 | P2 | A Gateway outage showed **two** disagreeing banners with two independent dismiss buttons for one cause | `gatewayOk` (probe failure) and `serverHistoryError` (history read failure) are the same outage — while the Gateway is down the history read cannot succeed — but each rendered its own alert; dismissing one implied a second, partial problem | The amber history banner now speaks only when the Gateway answered (`gatewayOk !== false`); nothing is cleared, and the outage banner carries the local-copy disclosure | 1 |
| ALPHA-BUG-0014 | P2 | A coded run failure reached the log, the metric and the recovery ledger but never the **client** watching the stream | No production `configure_error_reporter` caller existed, so the four-legged fan-out's SSE leg was unbound and counted into a process-global nobody read | `app/gateway/error_sse.py` binds the sink in the lifespan; publishes in the frame shape clients parse (`code`/`correlation_id`, not the nested `error_*` keys); unaddressable reports count `unbound_sse` | 3 |
| ALPHA-BUG-0015 | P2 | A proxy failure reached the client as an HTML page, so `api-client.ts` could report only "HTTP 502" | Both nginx configs had no `proxy_intercept_errors` / `error_page`; the Helm ConfigMap was a third copy that two earlier fixes would have missed | Coded 502/504 JSON bodies in all three configs; frontend/provisioner opt out; 503 deliberately **not** intercepted (it carries the Gateway's own reason) | 25 |

## Fixed (cycle 1, recorded in the roadmap)

| # | Severity | Symptom | Root cause | Fix |
|---|---|---|---|---|
| ALPHA-BUG-0007 | P1 | Every failed run rendered one indistinguishable sentence | The Gateway sent a coded `event: error`, the frontend parsed it, a test pinned the parse — and nothing read the result | `StreamRunFailure` + `chat-support-id.ts`; code + correlation id now reach the UI |
| ALPHA-BUG-0008 | P1 | A dropped stream re-dialed with zero delay | `let retryDelay = 0` and the only fallback was the post-increment cap | 5-attempt ladder with equal jitter (guaranteed floor) |
| ALPHA-BUG-0009 | P1 | Corrupt JSON store read as empty state | Three JSON stores under `alpha/harness/` wrapped `json.load` in `except Exception: pass` with no logger | `is_degraded` / `is_durable` disclosure + staged load |
| ALPHA-BUG-0010 | P2 | `test_harness_refine_tool` failed on `main` | The test invoked the tool with no `runtime`, which pydantic rejects | Repaired at the call site; reproduced on clean `main` first |

## Fixed (earlier cycles, recorded in the roadmap)

| # | Symptom | Fix |
|---|---|---|
| 1 | Checkpointer `database is locked` | 30 s busy timeout on every connection (`6e49de3`) |
| 2 | Run hung 20+ min with no heartbeat | Stall watchdog 900 s → terminal `error` + `stop_reason="stalled"`; tool-call budget 600 s (`3d4795b`) |
| 3 | Zombie run's SSE showed zero events for 877 s | Named `event: heartbeat` every 15 s |
| 4 | Idle Gateway burned 1442 s CPU | Not reproduced; 0.91 s over 60 s measured; stays open above the <1 % target |
| 5 | Error reporter SSE leg never fires | Honesty fixed in cycle 1; **binding FIXED in cycle 2** (`app/gateway/error_sse.py`) |
| 6 | `e2e_real_task.py` both tasks green | Evidence the happy path works after Fixes 1–4 |
