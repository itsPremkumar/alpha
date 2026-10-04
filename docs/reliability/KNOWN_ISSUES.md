# Alpha — Known Issues

Every entry is either **FIXED** (test-proven, commit named), **WIRED**
(implemented and attached to the Gateway lifespan), or **SPECIFIED** (root-caused
and designed, not yet implemented). This file is the authority; where it
conflicts with aspirational prose elsewhere, this file wins.

## Open

### P1 — Idempotent HTTP retry (frontend)

`api-client.ts` and `http.ts` are single-shot. No `Idempotency-Key` is sent.
Retrying `POST /runs/stream` without a plumbed idempotency key is exactly the
double-charging failure the side-effect ledger exists to catch, so this needs
end-to-end work, not a blind flip. See [RELIABILITY_ROADMAP.md](../RELIABILITY_ROADMAP.md).

### P1 — Failure visibility queue (frontend)

`flash()` in `ChatView.tsx` is one `string | null` slot with a 4.5 s auto-clear
and ~60 call sites, duplicated per-section. Concurrent failures overwrite each
other. A bounded list with a persistent error badge is the fix; it is a broad
`ChatView` refactor across ~15 components.

### P2 — Unified offline signals (frontend)

`gatewayOk` and `serverHistoryError` render two independent banners with
independent dismissal that can visibly disagree. The honesty machinery
(`connectivityView()`) is built and tested in `lib/network.ts`; it is simply not
applied to these two signals.

### P2 — Crash-loop supervisor wiring (Windows launcher)

`ProcessSupervisor` + `RestartLedger` are built but not connected to
`start.ps1` / `watchdog.ps1`. A crash-looping service is not yet quarantined
after N restarts.

### P2 — Coded nginx 502

Both `docker/nginx/nginx.conf` and `nginx.local.conf` return a bare 502 when the
Gateway is down. `proxy_intercept_errors on` + `error_page 502 503 54` returning
a JSON body would let `api-client.ts` classify a real payload. Never intercept
`text/event-stream`.

### P2 — Error reporter SSE leg

No production `configure_error_reporter` caller exists. Run-scoped errors still
reach clients via `gateway_terminal_error_payload`, but the four-legged
fan-out's SSE leg never fires.

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

## Fixed (this cycle)

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 7 | Every failed run rendered one indistinguishable sentence | The Gateway sent a coded `event: error`, the frontend parsed it, a test pinned the parse — and nothing read the result | `StreamRunFailure` + `chat-support-id.ts`; code + correlation id now reach the UI |
| 8 | A dropped stream with no `retry:` frame re-dialed with zero delay | `let retryDelay = 0` and the only fallback was the post-increment cap | 5-attempt ladder with equal jitter (guaranteed floor) |
| 9 | A corrupt `goals.json` / harness state file reported "no goals" / injected no reminder | Three JSON stores under `alpha/harness/` wrapped `json.load` in `except Exception: pass` with no logger | `is_degraded` / `is_durable` disclosure + staged load |
| 10 | `test_harness_refine_tool` failed on `main` | The test invoked the tool with no `runtime`, which pydantic rejects | Repaired at the call site; reproduced on clean `main` first |

## Fixed (previous cycles, recorded in the roadmap)

| # | Symptom | Fix |
|---|---|---|
| 1 | Checkpointer `database is locked` | 30 s busy timeout on every connection (`6e49de3`) |
| 2 | Run hung 20+ min with no heartbeat | Stall watchdog 900 s → terminal `error` + `stop_reason="stalled"`; tool-call budget 600 s (`3d4795b`) |
| 3 | Zombie run's SSE showed zero events for 877 s | Named `event: heartbeat` every 15 s |
| 4 | Idle Gateway burned 1442 s CPU | Not reproduced; 0.91 s over 60 s measured; stays open above the <1 % target |
| 5 | Error reporter SSE leg never fires | Honesty fixed; binding still SPECIFIED |
| 6 | `e2e_real_task.py` both tasks green | Evidence the happy path works after Fixes 1–4 |
