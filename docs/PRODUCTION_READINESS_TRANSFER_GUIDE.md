# Alpha Production Readiness and Transfer Guide

## Current work handoff — 2026-10-15

This section is the continuation brief for an agent taking over the active
production-readiness work. Treat repository state and live test output as
authoritative; earlier chat summaries are context, not proof. Work resumed on
branch `main` from baseline commit `3d95eb1` (`fix: harden approval grants and
run evidence`), which was pushed to `origin/main` at that time. Confirm the
checkout and remote before editing.

### Recent verified changes

- Slash-command HTTP approval flags are no longer trusted as authorization.
  Command execution, auto-trigger, and phase-transition paths sanitize
  client-controlled approval context and bind grants to an authenticated admin
  actor; personal access tokens do not receive admin authority.
- Run-scoped artifact readback acceptance evidence now requires a successful,
  correlated write/read in the same complete run, the exact declared path, a
  non-empty artifact, and a bounded digest. Stale, ambiguous, oversized, absent,
  or unsupported evidence stays unverified; empty readback is not met.
- Targeted verification reported at handoff: 80 command safety/honesty tests,
  34 acceptance/evidence tests, 27 Gateway health tests, and 4 documentation
  honesty tests passed. Ruff lint/format checks passed. The frontend unit suite
  previously passed 2,764 tests, `pnpm typecheck` passed, and a replay-backed
  browser E2E passed for file read/write plus goal CRUD/detail/edit/clear. That
  replay E2E is not a live-provider task. Re-run relevant checks against the
  current checkout before relying on these historical results.
- These changes do not complete the general typed evidence collector registry,
  re-verification history, durable long-running conversational goals, or
  production multi-process guarantees. See
  [`PRODUCTION_READINESS_INVENTORY.md`](PRODUCTION_READINESS_INVENTORY.md).
- Current-turn Windows launcher changes: startup recovery checks an expired
  no-listener deadline and a listener that never becomes ready before blocking
  HTTP probes; boot health reports wall-clock `elapsed_seconds`. Steady-state
  monitoring marks port-up but unresponsive services degraded and restarts them
  after two consecutive failed readiness checks. Health fields are scoped to
  their observation phase so a healthy record cannot retain stale boot-wait
  errors.
- Current-turn checks: 41 launcher/watchdog tests passed; Python `compileall`,
  Ruff lint/format, `git diff --check`, and the generated docs-index check
  passed. A separate live UI task used Alpha's `write_file` and `read_file`
  tools against `/mnt/user-data/workspace/alpha-live-verification-20261015.txt`;
  the exact content `alpha-live-task-receipt-20261015` was read back and
  matched. It did not inspect the repository, use the internet, or delegate.
  This verifies one bounded real tool task through the local UI, not every mode
  or general production readiness.

### Runtime incident and current status

The old local stack had listening ports but both HTTP endpoints timed out. Its
health file falsely said `healthy` with stale `phase=boot_wait` fields. Archived
logs show readiness SQLite probe timeouts and slow startup. The repository's
`stop.ps1` and `start.ps1 -NoBrowser -WatchdogMode -Prod` sequence stopped the
stuck process tree and loaded the current launcher. The restarted frontend and
Gateway both returned HTTP 200; `logs/alpha_health.json` reported
`status=healthy`, `phase=running`, and both readiness fields true. Cold-start
latency and the earlier SQLite readiness timeouts remain follow-up items. Keep
checking readiness and monitor logs; one healthy sample does not establish
sustained stability. Do not expose secrets or paste config values into reports.

### Honest acceptance boundaries

- Thread goals currently have bounded in-run continuation (up to eight rounds),
  not a durable scheduler that wakes indefinitely after an ordinary run stops.
  Do not advertise years-long autonomous work. A durable goal dispatcher needs
  owner/generation fencing for edit, clear, completion, restart, and admission,
  and must use the Gateway `RunManager` as the sole run owner.
- The UI replay E2E proves deterministic behavior against replay fixtures only.
  The one live UI task above proves a bounded file write/read round-trip; it does
  not prove bot/subagent modes, coding tasks, natural-language acceptance, or
  sustained long-running autonomy.
- The configured deployment is intended to stay free-provider/auto-router only.
  Do not add paid credentials or silently switch provider mode. A free model that
  emits tool-call JSON as prose cannot substantiate a real tool-execution claim.
- A successful run, a green health check, module presence, a demo, or an agent's
  completion message is not proof of production readiness. Record the exact path,
  fixture/provider mode, command, exit status, and evidence for every acceptance
  claim.
- Existing untracked scratch files at handoff are `.tmp_head_temh.py`,
  `backend/.test_durations`, `backend/.tmp_dbg_mw.py`, and
  `backend/tests/test_zz_debug_tmp.py`. Inspect `git status` before staging and
  do not stage or remove them as part of unrelated work.

### Copy/paste continuation prompt

```text
You are taking over production-readiness work in the Alpha repository at
C:\Users\PREM KUMAR\Videos\alpha. Continue the existing work; do not restart
from scratch or claim completion based on earlier chat. Read the root AGENTS.md,
backend/AGENTS.md or frontend/AGENTS.md for the area you touch, and
docs/PRODUCTION_READINESS_TRANSFER_GUIDE.md plus
docs/PRODUCTION_READINESS_INVENTORY.md. Follow the owning subsystem guide.

First inspect git status, branch, HEAD, remotes, current processes, ports 3000
and 8001, logs/alpha_health.json, and the newest logs/diagnostics archive. At
the previous handoff, `main` was at 1ffdbae, pushed to origin/main. That change
hardens Windows launcher recovery for a missing Gateway listener, a bound but unready Gateway,
and consecutive port-up readiness failures; it also fixes stale health fields.
The current local stack returned HTTP 200 from Gateway readiness and the frontend,
and its health file reported `status=healthy`, `phase=running`. A bounded live UI
file write/read task also completed with exact tool receipts, but it was not a
coding or swarm task. Recheck the live state before acting, and do not restart a
healthy stack without a concrete reason. Prior diagnostics showed SQLite
readiness timeouts and slow startup; measure current cold-start and database
probe behavior before claiming that incident resolved.

Keep Alpha's Gateway RunManager as the only run lifecycle owner. Do not add a
parallel scheduler/executor or claim cross-process exactly-once semantics for
single-process JSON/JSONL stores. Thread goals only have bounded in-run
continuation; durable wake/reschedule is still a gap and requires owner plus
generation fencing across edit/clear/complete/restart and RunManager admission.
The prior baseline commit 3d95eb1 hardened approval grants and added exact-path,
run-correlated artifact readback evidence. Re-inspect current code and tests
rather than assuming summaries are current. General typed
command/HTTP/reviewer evidence collectors and re-verification history remain
incomplete. Never mark arbitrary language criteria verified without a supported
collector or owner evidence.

Keep the selected free-provider/auto-router configuration. Do not add paid
credentials, expose secrets, or quietly change provider selection. Distinguish
replay-backed browser tests from live-provider tests. A model response claiming
that it used a tool is not proof; inspect correlated Gateway tool receipts and
resulting artifacts. If free-provider tool calling is unavailable, report the
live task as blocked/unverified with the concrete provider response.

Work in small, reviewable vertical slices. Add regression tests before fixing a
bug; run targeted checks and the relevant full suite, typecheck/lint, schema
checks, generated-doc checks, and UI/E2E checks according to the repository
guides. Do not add or run extra tests unless needed to validate your changes.
For UI work, use the existing goal UI and reuse its top status strip/detail dialog
instead of adding a duplicate goal surface. Exercise actual visible controls and
capture screenshots when the app is available. Assign a bounded real coding task
through the UI only after the Gateway and provider are healthy; verify it using
run IDs, tool receipts, changed files, and test output, not narrative claims.

Before committing, inspect the complete diff, run git diff --check, stage only
the intended explicit paths, and never include config.yaml,
extensions_config.json, secrets, or pre-existing scratch files. Follow the repo's
commit/push rules and report whether changes were committed or pushed. Update the
production inventory and relevant docs whenever implementation status changes.

Deliver a concise evidence report: current HEAD/branch, root cause, changed files,
checks with exact commands and results, UI/provider mode, run and artifact IDs,
screenshot paths if captured, remaining partial/unverified gaps, and the next
measurable acceptance gate. Do not call Alpha production-ready until the actual
release gates pass.
```

## Current assessment

Alpha already has significant production foundations: durable run records and
leases, cancellation and orphan recovery, a scheduler with durable queued work,
MCP task recovery, per-owner storage, authentication and authorization, server-side
run-context trust boundaries, audit-oriented run events, health/readiness checks,
feature-manifest coverage, and automated integration checks.

The dominant risk is **capability coherence**, not a lack of agent features.
Do not introduce a parallel executor, scheduler, ownership system, or agent runtime.
All new autonomy must use the Gateway `RunManager` lifecycle and its durable stores.

## Release blockers

| Gap | Why it matters | Acceptance gate |
| --- | --- | --- |
| Evidence-backed completion | Models can claim success without independently checkable proof. | Each mission has criteria and evidence; completion shows verification result. |
| Central policy decisions | Tool-level checks drift and allow confused-deputy failures. | A server-side policy decision precedes every consequential external action. |
| Evaluation release gate | Features can degrade real task success or safety silently. | Offline regression corpus blocks release on P0 safety/recovery regressions. |
| Operational runbook | Production incidents require a reproducible response. | On-call can diagnose, restore, and prove recovery from documented commands. |
| Backup/restore drill | Persistence only matters if it can be restored. | A clean-environment restore test passes on a release candidate. |
| Explicit autonomy boundaries | Autonomous schedules can cause repeated external effects. | Budgets, expiry, idempotency, approvals, and cancellation are enforced end to end. |

## Core architecture decision

Use the existing run lifecycle (`pending`, `running`, terminal status) as the
single execution owner. Add a **verification overlay**, not a replacement state
machine:

1. A caller supplies optional, typed acceptance criteria with a run.
2. The run records append-only evidence references: test result, artifact digest,
   command exit status, HTTP status, reviewer decision, or explicit exception.
3. A deterministic verifier evaluates evidence against criteria.
4. The run can be technically successful while its outcome is `unverified`; UI and
   APIs must never present that as verified completion.
5. Any action with external side effects requires a policy grant before execution.

This separation preserves crash recovery and compatibility while adding user trust.

## Autonomous prompt-to-completion mode

`RunCreateRequest.autonomous=true` is the opt-in entry point for a one-prompt
run. The Gateway applies plan mode, allowed subagent delegation, and
non-interactive execution *after* it strips client-controlled reserved context
keys. Existing authorization, tool allowlists, sandbox restrictions, budgets,
timeouts, cancellation, and ownership checks remain mandatory. This mode should
be paired with a thread goal and acceptance criteria for multi-turn completion
and evidence-based success.

## 90-day execution sequence

### Days 1–14: trust foundation

- Add acceptance-criteria/evidence contracts and expose them from run details.
- Add policy decision records for tool invocation and approval decisions.
- Establish structured redacted audit events and correlation IDs.
- Add adversarial tests: client-supplied internal context, cross-owner access,
  cancellation race, duplicate idempotency key, and expired lease recovery.

### Days 15–45: measurable reliability

- Publish an offline benchmark corpus for coding, browser, research, documents,
  recovery, and prompt-injection cases.
- Add release scorecard thresholds for task success, P0 security tests, p95 latency,
  error rate, cost envelope, and recovery correctness.
- Add dashboards/alerts plus backup and restore drills.
- Add approval cards that show target, scope, reason, reversibility, and expiry.

### Days 46–90: controlled autonomy

- Introduce mission templates with typed inputs, tool allowlists, policy profile,
  budget, schedule, acceptance criteria, and fixture-based evaluation.
- Add multi-agent handoff contracts: delegated authority, budget, deadline,
  artifacts, and verifier-owned completion criteria.
- Enable self-improvement only via opt-in redacted data, offline evaluation,
  canary rollout, provenance, and rollback.

## Free-first deployment baseline

- Local or OpenAI-compatible model endpoint; model routing remains provider-neutral.
- PostgreSQL for multi-instance durability; SQLite only for clearly documented
  single-node development limits.
- OpenTelemetry + Prometheus/Grafana-compatible self-hosted telemetry.
- OIDC-compatible identity in production; local development authentication only in
  non-production environments.
- Isolated sandbox filesystem/network policy; do not treat a general container as
  the full security boundary.

## Operator handoff checklist

1. Configure production secrets outside Git and validate masked config output.
2. Use HTTPS through a trusted proxy; keep Gateway private behind Nginx.
3. Configure a durable database and test `/health/ready` during deployment.
4. Enable backup, document retention, and execute a restore drill.
5. Configure quotas, model budgets, egress restrictions, and approval policies.
6. Run the offline backend suite, blocking-I/O suite, security regression suite,
   and benchmark scorecard before every release.
7. Deploy through a canary, observe error/latency/cost metrics, then promote or
   roll back.
8. Keep a runbook containing alert owners, rollback steps, database restore steps,
   integration revocation, and incident communication templates.

## Detailed build prompt for the next implementing agent

> Work only in small vertical slices. Read the root and nearest module `AGENTS.md`.
> Preserve the Gateway `RunManager` as the sole run lifecycle owner. Implement an
> acceptance-criteria and evidence overlay. Use server-side validation and owner
> scoping; do not trust user-controlled runtime flags or policy claims. Each change
> needs unit/integration tests, redacted audit telemetry, API documentation, and
> targeted verification. Never claim a run is verified unless deterministic rules
> find sufficient evidence. Add benchmark fixtures before enabling autonomous or
> self-improving behavior by default. Prefer local/open infrastructure, while making
> paid providers optional adapters. Report changed files, tests run, known limits,
> and the next measurable release gate.

## Definition of production-ready

Production-ready means a release can be safely operated, not merely demoed: it is
authorized, observable, recoverable, evaluated, rate-limited, backed up, tested
against abuse, and capable of presenting proof for consequential outcomes.
