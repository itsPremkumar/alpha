# Alpha Production Readiness and Transfer Guide

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
