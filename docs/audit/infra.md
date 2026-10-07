# Infra Audit — Startup & CI Truth

Date: 2026-10-05. No git commands run. Gateway/frontend not started.

## 1. GET /health  — MEASURED: NOT REACHABLE

```
curl -s http://127.0.0.1:8001/health            -> (empty)
curl -w 'code=%{http_code}' .../health           -> code=000
curl exit=7  (connect to 127.0.0.1 port 8001 failed: Connection refused)
```

The gateway is NOT listening. `netstat -ano` shows no `:8001` entry at all;
only `:3000` is LISTENING (PID 9620). The empty body with exit 0 in a plain
`curl -s` is a *silent* failure — an operator who only reads stdout sees a
blank line that looks like an empty-but-healthy 200.

## 2. GET /api/ops/integration-health — MEASURED: NOT REACHABLE

```
code=000, curl exit=7 (same connection-refused)
```

`manifest_found` and coverage numbers: **UNOBTAINABLE**. Nothing to report.
Neither item 1 nor item 2 can be verified on this host right now.

## 3. Timeout arithmetic — MEASURED, CORRECT (bug is fixed)

Watchdog (`recovery/watchdog.ps1:50-66`), patience = threshold × $CheckIntervalSeconds(30):

```
:50 $CheckIntervalSeconds     = 30
:65 $GatewayHungThreshold     = 10    # 300 s
:66 $FrontendHungThreshold    = 45    # 1350 s
```

Launcher (`start.ps1`), waits:

```
:873  Wait-ForHealthy -Port $GatewayPort  -Path "/health/ready" -MaxWaitSeconds 240
:917  Wait-ForHealthy -Port $FrontendPort -Path "/"              -MaxWaitSeconds 1200
:935  $maxAttempts = 450      # boot loop: 450 x 2 s = 900 s wall
```

**Is watchdog patience ever SHORTER than the launcher wait? No.**
Gateway: 300 s > 240 s (margin +60 s). Frontend: 1350 s > 1200 s (margin +150 s).
Both strict, both positive margin — the historical 6×30=180 s < 240 s inversion
is gone.

Caveat worth naming: the watchdog's frontend counter also resets while
`frontend.log` is being written (`watchdog.ps1:470`, 120 s window), so real
patience for a compiling frontend is unbounded — a stuck process that keeps
appending to its log is never killed. That is deliberate and documented, but
it means the 45 threshold is not the only protection.

## 4. .github/workflows — 20 files

| File | Jobs | Runs on PR? | Real gate? |
|---|---|---|---|
| backend-blocking-io-tests.yml | backend-blocking-io | yes | gate |
| backend-unit-tests.yml | default-install-collection, backend-unit-tests | yes | gate |
| chart.yaml | validate-chart, verify-versions, publish-chart | yes | gate |
| cold-start-budget.yml | cold-start-imports | yes | gate (no soft-fail, per :23 comment) |
| container.yaml | verify-versions, backend-container, frontend-container, provisioner-container | no (push only) | gate |
| e2e-tests.yml | e2e-tests | yes | **NO — `if: false` (:67)** |
| frontend-unit-tests.yml | frontend-unit-tests | yes | gate |
| generated-drift-gate.yml | generated-drift | yes | gate |
| inert-declaration-report.yml | inert-declaration-report | yes | **NO — both steps `continue-on-error: true` (:59,:65)** |
| label-sync.yml | sync | no (push/dispatch) | n/a |
| lark-cli-images.yaml | build-images | no (push/dispatch) | n/a |
| lint-check.yml | compose-config, agent-guidance, agent-guidance-debt-report, lint-backend, ruff-debt-report, lint-frontend, docs-index, duplicate-expressions | yes | gate |
| nightly.yaml | cold-start-budget, prepare, validate-chart, build-images, publish-chart | no (schedule) | gate |
| replay-e2e.yml | backend-replay-golden, fullstack-replay-render | yes | gate |
| sandbox-image-smoke.yml | sandbox-image-smoke | yes | gate |
| sandbox-network-proxy-image.yaml | validate, publish | yes | gate |
| skill-review-ci.yml | skill-review | yes | gate |
| triage.yml | pr-labels, reviewing, issue-triage | no (pull_request_target) | n/a |
| verify-versions.yml | verify-versions | no (workflow_call only) | reusable |
| windows-installer.yml | build-installer, native-installer | yes | gate (:168 explicitly refuses continue-on-error) |

Two workflows are decorative: `e2e-tests.yml` is disabled by `jobs.e2e-tests.if: false`
because no browser suite exists in the tree (honest — the banner says so), and
`inert-declaration-report.yml` is a report-only job whose two steps both carry
`continue-on-error: true`. The inert gate's blocking enforcement lives in
`tests/test_no_inert_config.py`, run by the unit shards, not here.

## Not verified
- Health/integration endpoint behaviour of any kind (gateway down).
- Whether Dockerfile COPY rules actually ship `contracts/` into the images.
- Runtime performance: blocking-IO gate, N+1, retention bounds.