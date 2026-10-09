# Alpha reliability verification report

## Conclusion

This is an evidence-backed partial reliability audit, **not** a production
readiness certification. Offline workflow regression coverage passed and the
local Gateway and frontend served HTTP 200 during the final probe. The full
offline backend suite did not finish within this audit; backend lint/format
gates report substantial repository-wide baseline failures; the frontend suite
has a known failing QR-decoder round-trip; and the live `union-alpha` provider
probe failed for insufficient credits. A real end-to-end user task, browser
screenshot, scheduler firing, T1–T5 task matrix, and production chaos drill
were not verified.

The companion [system map](SYSTEM_MAP.md) records architecture and persistence
boundaries. In particular, the built-in dynamic workflow digest executor is a
graph projection, not domain work, and does not satisfy acceptance. No result
below should be interpreted as evidence that it does.

## Environment and live probes

| Probe | Result | Evidence and boundary |
|---|---|---|
| Gateway readiness | **Passed** | `GET http://127.0.0.1:8001/health/ready` returned HTTP 200 in the final probe. This proves readiness response only, not an authenticated task. |
| Frontend root | **Passed** | `GET http://127.0.0.1:3000/` returned HTTP 200. No browser screenshot or interactive workflow was captured. |
| Nginx ingress | **Blocked** | Nginx is not installed; port 2026 did not answer. Unified ingress and browser verification through the public entry point were not tested. |
| `union-alpha` provider | **Failed** | `make doctor` received OpenRouter HTTP 402: the account could afford only 190 tokens for a request configured up to 16,384. No live model-backed task was completed. |
| `alpha-free` provider | **Passed probe only** | `make doctor` received `PONG`. This does not substitute for a representative application task. |
| Scheduler, provisioner, external integrations | **Not verified** | No scheduled firing, provisioner operation, or authenticated third-party integration exercise was performed. |

At the initial baseline, ignored local config and dependencies were absent and
the application ports had no listeners. Setup later seeded local-only config,
installed dependencies, and started local services. Secrets and ignored runtime
configuration are intentionally excluded from this report.

## Validation results

| Area | Command or check | Result |
|---|---|---|
| Dynamic workflow | `uv run pytest tests/test_dynamic_workflow_router.py tests/test_workflow_durability_router.py tests/test_dynamic_workflow_service.py -q` (from `backend/`) | **Passed:** 32 tests in 196.22 seconds. Exercises workflow routing, service and durability behavior offline; it is not a live user task. |
| Backend full suite | `uv run pytest -m "not live" --ignore=tests/blocking_io tests/ -q` (from `backend/`) | **Incomplete:** the run remained at test collection without a final summary and was interrupted. No suite-wide pass is claimed. |
| Backend lint | `make lint` (from `backend/`) | **Failed:** Ruff reported 875 lint errors; 690 were identified as automatically fixable. This audit did not apply bulk fixes. |
| Backend format | `uv run ruff format --check .` (from `backend/`) | **Failed:** 500 files would be reformatted. No repository-wide formatting rewrite was applied. |
| Frontend typecheck | `pnpm typecheck` (from `frontend/`) | **Passed.** |
| Frontend library tests | `pnpm test` (from `frontend/`) | **Failed:** the explicitly marked `KNOWN FAILING` QR decoder clean-render round-trip fails because finder geometry is not reliable. QR scanning remains disabled; this gate was not weakened. |
| Frontend branding tests | `pnpm test:branding` (from `frontend/`) | **Passed:** 4 tests. |
| Frontend reasoning tests | `pnpm test:extra` (from `frontend/`) | **Passed:** 36 tests. |
| Frontend production build | Set `$env:BETTER_AUTH_SECRET='local-dev-secret'`, then run `pnpm build` (from `frontend/`) | **Passed on a serial rerun.** An earlier build overlapped another build and failed while tracing `.next`; the non-concurrent rerun compiled, generated pages, and completed build traces. |
| Electron tests | `npm test` (from `electron/`) | **Passed:** 284 tests. |
| Electron child logging test | Focused Node test, repeated three times | **Passed:** all three repetitions. The test now waits for the child process `close` event instead of relying on a fixed 1.5-second delay. |
| Docs index | `python scripts/generate_docs_index.py` and `python scripts/generate_docs_index.py --check` | **Passed** after classifying the audit documents. |
| Production config check | `make prod-check` | **Passed with warning:** `BETTER_AUTH_SECRET` was absent or a placeholder in the inspected environment. |
| Engine inventory | `make engine-inventory` | **Passed:** regenerated inventory reports 117 packages and 1,867 Python files. |
| General setup check | `make check` | **Blocked:** Nginx is absent (Windows direct-dev mode does not require it, but the unified `:2026` entry does). |
| Doctor | `make doctor` | **Failed:** the live `union-alpha` check returned HTTP 402; its run-readiness result therefore does not support a model-backed run. At that time Gateway/frontend listeners were not yet serving. |

## Dynamic workflow boundaries

The router, service, and durability test suites above exercise the offline
dynamic-workflow control paths. The API's default digest runner is explicitly
`local_digest_projection`: it demonstrates graph scheduling/evidence plumbing,
not task execution. It cannot produce domain acceptance. This audit did not
bind a host-owned real executor or invoke a dynamic workflow against the live
Gateway with an authorized user task. Restart recovery was covered only to the
extent exercised by the named offline tests; no multi-process or cross-host
exactly-once guarantee is claimed.

## Not verified

- A complete offline backend suite, including its final pass/fail totals.
- A real authenticated chat/run through the browser or Gateway, including
  streaming, tool use, artifact review, and independent acceptance evidence.
- A browser screenshot, broad workspace-tab navigation, or visual comparison.
- The requested T1–T5 representative task matrix and live subsystem/integration
  matrix.
- Scheduler firing, restart recovery under a live scheduled task, or a
  destructive/chaos drill.
- Nginx ingress on port 2026, Docker deployment, or a production deployment.
- Live `union-alpha` execution until the provider account has sufficient
  credits.

## Setup side effect

The repository's `make install` ran `pre-commit install --overwrite` in this
worktree. Because the worktree shares Git metadata with the main checkout, the
installer wrote a hook under the main checkout's `.git/hooks/pre-commit`.
That external path was not inspected or modified further during this audit.
