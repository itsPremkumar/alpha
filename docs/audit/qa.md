# QA Audit — Test-Suite Truth

All findings MEASURED unless labelled otherwise. No git commands run. No test edited.

## 1. Coverage configuration: **NO**
- No `.coveragerc` anywhere (`search_files` for `.coveragerc` → total_count 0).
- No `tox.ini`, `setup.cfg`, `pytest.ini`, root `pyproject.toml`. Only `backend/pyproject.toml`
  and `examples/alpha-extension-example/pyproject.toml` exist.
- `backend/pyproject.toml:102` `[tool.pytest.ini_options]` declares **markers only**
  (`no_auto_user`, `allow_blocking_io`, `integration`, `live`) — no `addopts`, no `--cov`.
- `coverage` and `pytest_cov` are **not installed**: `find_spec` → `coverage MISSING`,
  `pytest_cov MISSING`. No `*cov*` dir in `backend/.venv/Lib/site-packages`.
- CI: `grep -niE 'coverage|--cov' .github/workflows/*.yml` returns only two *prose* hits
  (`e2e-tests.yml:14` "…are the real coverage", `sandbox-image-smoke.yml:4`). No cov invocation.
- `backend/Makefile:45-52` — `test`, `test-live`, `test-blocking-io`, `test-shard` all plain `pytest`.

Conclusion: line/branch coverage is structurally impossible on this repo today.

## 2. Counts
| root | test files | test defs |
|---|---|---|
| backend/tests | 1399 | 24074 (`def test_`/`test(`/`it(`) |
| backend/packages/harness/tests | 3 | 75 |
| frontend/src | 107 | 1597 |
AST parse of backend/tests: **24062** test functions (the 12-file delta = JS-style or helper indirection).

## 3. Weakest tests — the grep signal is mostly FALSE POSITIVE
- `assert True` bare: **1**, and it is a *fixture string* inside a mutation-fixture, not an assertion:
  `backend/tests/test_apr_sbfl_and_immutability.py:139` (inside `tampered_code = """..."""`, asserting the
  guard *rejects* it). Legitimate.
- xfail: **3 total, 0 without reason, all `strict=True`** —
  `test_concurrency_cross_process_guards.py:526`, `test_e2e_honesty_event_timestamps.py:275`, `:305`.
  Each names a real unfixed defect with file:line. These are honest defect markers.
- skip/skipif: 170 occurrences, **0 without a reason string** (spot-checked `skipif` all carry `reason=`).
- `assert X is not None`: 1964 occurrences, but almost all are one assertion *inside* a longer test.
  **70 test functions (0.29%)** consist *solely* of `is not None`/`True` assertions. Weakest of those:
  - `test_company_os_core.py:323` `assert kr.target is not None`
  - `test_grounding_layer.py:139` `assert ledger.get(live.claim_id) is not None`
  - `test_alpha_leader_capability_dispatch.py:1021` `assert reloaded.get_bot(ALPHA_LEADER_NAME) is not None`
  - `test_console_model_pricing_fallback.py:116` `assert console._lookup_pricing(pricing, "gpt-4o") is not None`
- AST scan found 316 functions with no bare `assert` **and** no `pytest.raises`. Manually read the
  top ~30: nearly all assert through a helper or `try/except → raise AssertionError`
  (`test_autonomous_planner.py:61`, `test_browser_router.py:49` via `_expect_ws_close`,
  `test_agent_storage_backend.py:58` "no raise" idiom, `test_aio_sandbox.py:33` via
  `assert_called_once_with`). 604 `assert_called_once_with`-family mock assertions exist.
  **No genuinely vacuous test found.** This is a materially stronger suite than the raw counts suggest.

## 4. Installed pytest plugins (site-packages, MEASURED)
`pytest 9.0.3`, `pytest-asyncio 1.3.0`, `pytest-split 0.11.0`, `hypothesis 6.156.6`,
`anyio 4.13.0`, `importlib_metadata 8.7.1`.
**Absent:** pytest-cov, xdist, pytest-mock, rerunfailures, timeout, order, benchmark, forked,
repeat, socket, mutmut. Sharding is file-count-based (pytest-split) — no duration data claimed.

## 5. Not verified
Collected test count (collection >7 min, not run). Coverage reality per module (no tool exists).
`backend/tests/blocking_io/` gate efficacy and mutation-fuzzer output — out of scope this pass.