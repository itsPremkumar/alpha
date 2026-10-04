# Alpha — Regression Matrix

Every confirmed bug that has a regression test, so a fix can never silently
revert to the old behaviour.

| Bug | Symptom | Regression test | Status |
|---|---|---|---|
| Checkpointer `database is locked` | Run died mid-flight with `sqlite3.OperationalError` | `test_checkpointer_busy_timeout.py` | PASS |
| Run hung 20+ min, no heartbeat | `status=running` forever, no SSE frames | `test_run_stall_watchdog.py` (12) | PASS |
| Zombie run's SSE showed zero events for 877 s | Heartbeat was a spec-comment, invisible to JS | `test_sse_heartbeat.py` (10) | PASS |
| Hung tool call zombie'd a run | `tool.ainvoke → run_in_executor → self.invoke` blocked forever | `test_tool_timeout_guard.py` (4) | PASS |
| Every failed run rendered one sentence | Parsed `code`/`correlationId` was discarded at every throw site | `chat-support-id.test.mjs` (13), `chat-stream.test.mjs` (+4) | PASS |
| Dropped stream re-dialed with zero delay | `retryDelay = 0` meant no wait at all | `chat-stream.test.mjs` (ladder tests) | PASS |
| Corrupt `goals.json` read as "no goals" | `except Exception: pass` in `_load` | `test_harness_state_durability.py` (12) | PASS |
| Corrupt harness state injected no reminder | `except Exception: pass` in `load` | `test_harness_state_durability.py` | PASS |
| Partial parse destroyed good entries | `self.entries` reset before parsing | `test_harness_state_durability.py` | PASS |
| Unreadable snapshot manifest read as "no snapshots" | `except Exception: return []` | `test_harness_state_durability.py` | PASS |
| `test_harness_refine_tool` failed on `main` | Tool invoked with no `runtime` | `test_continual_harness.py` (5) | PASS |
| Manifest drift | Generated artifact disagrees with committed | `scripts/check_generated_drift.py` | PASS (0 drift) |
| Orphan module | Module exists with no import/loader/config entry | `test_no_orphan_modules.py` | PASS |
| Feature manifest wiring | Registry entry not pinned to a module | `test_feature_manifest_wiring.py` | PASS |
| Harness boundary | `app.*` import inside the harness | `test_harness_boundary.py` | PASS |
| Assembly invariants | Duplicate tool names / non-terminal ClarificationMiddleware | `test_invariants.py` | PASS |
| Lost run admission was never re-sent | Frontend sent no `Idempotency-Key`, so a transport drop ended the turn | `idempotency.test.mjs` (9), `chat-request-error.test.mjs` (+1 end-to-end) | PASS |
| A blind retry of run admission would double-admit | Retry had no idempotency precondition; server-side dedupe existed but unused | `idempotency.test.mjs` (server answer + abort are never retried; same key/path/body across attempts) | PASS |
| Retried admission double-charged on a server answer | Would re-send a request the Gateway already decided | `idempotency.test.mjs` (5xx is never retried) | PASS |
| One outage showed two disagreeing banners | `gatewayOk` and `serverHistoryError` rendered as independent alerts | `history-store.test.mjs` (1) | PASS |
| Coded run error never reached the watching client | No production `configure_error_reporter` caller; SSE leg unbound | `test_run_error_coded.py::TestErrorReporterSseLeg` (2 behavioural) | PASS |
| Error frame's identity fields nobody parsed | Reporter nests `error_code`/`error_correlation_id`; the SSE consumer reads `code`/`correlation_id` | `test_run_error_coded.py` (frame shape) | PASS |
| An unaddressable report could vanish | No count for a report naming no run | `test_run_error_coded.py` (unbound metric + other legs still ran) | PASS |
| Proxy failure reached the client as HTML | No `proxy_intercept_errors`/`error_page` in any config | `test_nginx_coded_upstream_failures.py` (25, three configs) | PASS |
| Gateway 503 payload clobbered by a proxy message | Intercepting 503 would delete the server's own reason | `test_nginx_coded_upstream_failures.py` (503 never intercepted) | PASS |
| A body reported the wrong status | One shared body cannot state 502 vs 504 | `test_nginx_coded_upstream_failures.py` (each body matches its return) |
| Duplicated `Connection` header from a pooling edit | Two `proxy_set_header Connection` lines in one location | `test_nginx_coded_upstream_failures.py` (at most one per location) |
| WebSocket upgrade broken by Connection pooling | An upgrade location must keep `upgrade` | `test_nginx_coded_upstream_failures.py` (upgrades untouched) |
| Keepalive pool that can never engage | Static `upstream` would defeat the Docker config's request-time DNS | `test_nginx_coded_upstream_failures.py` (pooling split) |
| A UTF-8 BOM made nginx reject its own first directive | Invisible in every diff | `test_nginx_coded_upstream_failures.py` (BOM) |

## Coverage gaps (no regression test yet)

| Area | Why | Risk |
|---|---|---|
| Failure visibility queue | Not implemented | Concurrent failures overwrite each other |
| Crash-loop supervisor wiring | Built, unwired | Crash-looping service not quarantined |
| Coded nginx 502 — **live** acceptance | Config pinned statically; the Docker daemon was not running to stop a Gateway for real | A syntax/semantic mistake in the coded path that only a live proxy reveals |
| Error reporter SSE leg — operator surface | Counters exist, no `/api/ops` route reads them | The leg's health is not visible to an operator |
| Idle-Gateway CPU burn | Not reproduced | 5-min sample + loop-counter guard still open |
| Frontend client telemetry | Not implemented | Client failures visible only via `console` |
