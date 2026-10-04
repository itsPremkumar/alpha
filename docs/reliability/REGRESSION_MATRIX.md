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

## Coverage gaps (no regression test yet)

| Area | Why | Risk |
|---|---|---|
| Idempotent HTTP retry | Not implemented | Retrying without idempotency could double-charge |
| Failure visibility queue | Not implemented | Concurrent failures overwrite each other |
| Unified offline signals | Not implemented | Two banners can disagree |
| Crash-loop supervisor wiring | Built, unwired | Crash-looping service not quarantined |
| Coded nginx 502 | Not implemented | `api-client.ts` cannot classify proxy failure |
| Error reporter SSE leg | Not bound | Run-scoped errors missing from SSE fan-out |
| Idle-Gateway CPU burn | Not reproduced | 5-min sample + loop-counter guard still open |
| Frontend client telemetry | Not implemented | Client failures visible only via `console` |
