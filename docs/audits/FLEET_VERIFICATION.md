# Fleet Verification — five real tasks, five bot profiles

**Date:** 2026-10-05
**Gateway:** `http://127.0.0.1:8001` (live, `GET /health` → `{"status":"healthy"}`)
**Driver:** `backend/scripts/fleet_task_assign.py`
**Verdict:** **5/5 runs succeeded, 6/6 artifacts on disk, 1 real product bug found and fixed.**

---

## 1. What was run

Five genuinely different engineering tasks, addressed to five *named roster
bots* through `assistant_id`, executed **concurrently** so thread, bot and state
isolation were exercised rather than asserted.

| Bot | Role | Task | Duration | Tools | Artifacts |
|---|---|---|---|---|---|
| `coder` | Software Engineer | Implement a text-statistics library + pytest suite | 249 s | 3 | 2 files, 9.3 KB |
| `researcher` | Deep Researcher | Research Unicode categories from the live web, cite sources | 273 s | 8 | 1 file, 8.0 KB |
| `reviewer` | Code & Quality Reviewer | Review `alpha/utils/time.py`, write findings with line numbers | ~1400 s | 5 | 1 file, 15.1 KB |
| `data-analyst` | Data Engineer | Count real test files/dirs in this repo, report observed counts | 586 s | 7 | 1 file, 6.2 KB |
| `architect` | System Architect | Design a tool-result cache grounded in real files read | 724 s | 5 | 1 file, 16.4 KB |

Distinct tool surface used across the fleet: `write_file`, `read_file`,
`present_files`, `web_search`, `web_fetch`, `keyless_web_search`,
`deep_web_search`, `agent_eye_search`, `browser_navigate_and_inspect`, `ls`,
`glob`, `grep`, `hashline_read`, `code_mode`, `process_handle` — **15 distinct
tools**, chosen per task rather than uniformly.

Isolation held: five separate threads, five separate `user-data/outputs`
directories, no cross-contamination in any artifact.

---

## 2. Output quality — checked, not assumed

Every artifact was opened and read.

**`coder` → `fleet_textstats.py` (3.8 KB).** Genuinely good. Documents its two
non-obvious conventions rather than leaving them implicit — `splitlines()`
semantics for `lines` (`"a\n"` is 1 line, not 2) and first-occurrence
tie-breaking for `longest_word`. States that empty input never raises and never
divides by zero. `fleet_test_textstats.py` (5.5 KB) has exact-key-set
assertion, empty string, single word, **parameterized** whitespace-only cases,
trailing-newline, multiline-with-punctuation and duplicate-word cases.

**`reviewer` → `fleet_code_review.md` (15.1 KB).** The highest-value output in
the fleet. Two findings with specific line numbers, mechanism explanations,
**explicit confidence levels**, and a "Method and evidence honesty" section
stating that shell/execution were gated so it could not run anything. It did not
claim a passing test run it never performed.

**`researcher` → `fleet_unicode_research.md` (8.0 KB).** Multi-query research
with per-claim source URLs and an explicit unverified marking where a fetch
failed.

**`data-analyst` / `architect`** produced measured-count and grounded-design
documents respectively.

---

## 3. The bug this found: `coerce_iso` raised `OverflowError`

**Severity: CRITICAL** — an unhandled exception on the rendering path of three
routers.

The `reviewer` bot's Finding 1 claimed `coerce_iso` can raise `OverflowError`
from its own finiteness guard. **Verified by hand against the shipped code
before acting on it:**

```
coerce_iso(10**400) -> OverflowError: int too large to convert to float
coerce_iso(10**309) -> OverflowError: int too large to convert to float
coerce_iso(float('nan')) -> ''          (already fine)
```

**Root cause (one sentence).** The guard `if not isfinite(value):` sat
*outside* the `try` that absorbs `OverflowError`, and `math.isfinite` coerces
its argument to a float — so an int too large for one raises **from the guard
itself**, the exact exception the `except` two lines below exists to catch.

**Impact.** `routers/projects.py`, `routers/threads.py` and
`routers/evidence.py` all call `coerce_iso` while rendering, so a corrupt stored
value became an unhandled **500**. The guard had been added to stop a corrupt
numeric producing a *plausible-looking timestamp*; instead it raised on the very
inputs it existed to defend against.

**Fix** (`backend/packages/harness/alpha/utils/time.py`): move the guard inside
the `try`, so an overflow in either the test or the conversion takes the
documented "stringify as a last resort" path. No caller patched, no exception
list widened — the function now honours its own contract for every input.

**Post-fix, verified on the live module:**

```
coerce_iso(10**400)              -> '1000...000'   (was: OverflowError)
coerce_iso(float('nan'))         -> ''
coerce_iso(float('inf'))         -> ''
coerce_iso(0)                    -> '1970-01-01T00:00:00+00:00'
coerce_iso(1764123456.789)       -> '2025-11-26T02:17:36.789000+00:00'
coerce_iso(None)                 -> ''
is_lease_expired(<corrupt>, 30)  -> True
```

**Test:** `backend/tests/test_coerce_iso_never_raises.py` — 12 cases.
**Negative-controlled:** with the guard moved back out, **6 of 12 fail**; with
the fix, **12 pass**. Commit `86aae4e`.

---

## 4. Findings about Alpha itself

| # | Sev | Finding | Evidence |
|---|---|---|---|
| F1 | CRITICAL | `coerce_iso` raised `OverflowError` → 500 in 3 routers | reproduced, fixed `86aae4e`, 12 tests negative-controlled |
| F2 | MINOR | A test only passes on a clean checkout. `test_projects_events_timestamp_dialect.py::test_the_event_bus_reads_a_legacy_log` asserts 1 legacy event reads back as 1 row and gets **8** — the project event log is not isolated between runs | reproduced with my change stashed, so **pre-existing and unrelated** |
| F3 | — | **My harness, not Alpha — FIXED.** The driver reported `reviewer` as failed (`stream exceeded 1200s`, `produced no workspace file changes`) when the run actually succeeded ~1400 s later and wrote a 15.1 KB artifact. The real verdict is 5/5. A timeout in my own instrument must not be reported as a product failure | driver log vs `GET /api/console/runs` |
| F4 | — | **My harness had a verification hole — FIXED.** `_host_path` failed to resolve the thread outputs directory, so `verified_bytes` was empty and the size floor was silently skipped — reporting `problems: none` for artifacts it had never actually measured. A verifier that cannot check must say so, not pass | driver output showed empty byte counts with `problems: none` |

F3 and F4 are recorded because a harness that flatters itself is worse than no
harness: F3 would have become a false bug report and F4 a false pass.

**Both were fixed rather than annotated.**

- **F3** — the fixed 40-iteration status poll was replaced with polling until a
  terminal status, bounded by the same `--timeout` as the stream. A run that
  never terminates is now recorded as `stream_timed_out` **and** gets an
  explicit problem, so "the stream was cut off" and "the run failed" stay
  separate facts. `TERMINAL` gained `failed`, so an errored run stops the poll
  instead of being misreported as a timeout. The default budget moved from 900 s
  to `max(2400, SLOWEST_OBSERVED_RUN_SECONDS * 1.7)` — the previous default was
  itself a defect, since the documented command produced a false failure by
  default rather than only by choice.
- **F4** — the size floor was extracted into `check_artifacts()`, which reports
  any artifact it could not measure and returns without applying the floor. A
  partially-readable run is reported rather than averaged over the readable
  subset. `_host_path` now searches every user directory instead of assuming a
  layout, and returns a concrete path for a missing file so the error can name
  where it looked.

Regression coverage: `backend/tests/test_fleet_task_assign_verifier.py`,
**18 cases**, testing the verifier rather than Alpha. Negative-controlled: with
the report removed, 2 of the 18 fail; with it, 18 pass.

---

## 5. Honest limitations of this run

- **No code was executed by any bot.** `sandbox.allow_host_bash: false`, so
  `coder` wrote a pytest suite it could not run. It was told to say so, and the
  suite has therefore never been executed — the tests are reviewed, not proven.
- **`reviewer`'s Finding 2** (`is_lease_expired` does not use `coerce_iso`) was
  **NOT VERIFIED** — I confirmed Finding 1 only. It is reported here as an open
  claim, not as a defect.
- Durations are wall-clock including a ~59 s first-token latency on the keyless
  free gateway, which rate-limits.
- Web research was partial: `web_search` worked, `web_fetch` returned HTTP 401
  from the configured backends, so several claims rest on snippets.
- Single run per bot. No repeat runs, so no flakiness data.

---

## 6. Evidence index

| Artifact | Path |
|---|---|
| Driver | `backend/scripts/fleet_task_assign.py` |
| Verifier regression tests | `backend/tests/test_fleet_task_assign_verifier.py` (18 cases) |
| Run log (driver's own verdict, pre-fix) | `logs/fleet_run1.log` |
| Machine-readable results (pre-fix) | `logs/fleet_runs.json` |
| Artifacts (6 files) | `backend/.alpha/users/default/threads/*/user-data/outputs/fleet_*` |
| Bug fix | `backend/packages/harness/alpha/utils/time.py`, commit `86aae4e` |
| Regression test | `backend/tests/test_coerce_iso_never_raises.py` (12 cases) |
| Surrounding suites | 182 passed, 2 xfailed, 1 pre-existing failure (F2) |