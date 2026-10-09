# Alpha APEX Work Transfer

This is the working handoff for the next coding agent continuing APEX reliability work. Read the root `AGENTS.md`, `backend/AGENTS.md`, `docs/APEX_AUTOPILOT.md`, `docs/PRODUCTION_READINESS_INVENTORY.md`, and the relevant module guides before editing. The repository instructions and the code are authoritative; this document is a current map, not a substitute for either.

## Objective and claim boundary

Make Alpha's APEX control plane more reliable, observable, recoverable, and useful for bounded real work. APEX is an executive control layer over Alpha's existing execution engine. It is not proof that Alpha can safely perform every possible task, and this project must not claim universal capability or uninterrupted operation for years. Completion means the requested acceptance criteria have measured evidence; a successful RunManager run alone is never verification. Keep refusals, approval gates, the emergency stop, capability ceilings, and truthful partial/blocked outcomes intact.

The user wants sustained implementation, real end-to-end task validation, bug fixes, and safe integration of parallel work. Do not stop after writing plans or tests. Make small, reviewable changes, run the focused checks, update the owned docs, and continue through the queue as time and environment allow.

## Repository and collaboration state

At handoff creation (2026-10-09), the active checkout is `main`, with local `HEAD` and `origin/main` both at `eddbda1350bb81b9c01a74b91efe4082a79666bb`. Verify this before acting because it can change. Recent pushed commits include `37999d2` (serialize local worker persistence), `a87419e` (close SSE replay subscription gap), and `eddbda1` (synchronize mode state across workers).

The tracked working changes now cover the APEX goal store and its tests, one parked-cycle bug fix, the visible SSE reconnect transport/UI and tests, plus targeted user/developer documentation. Preserve these edits and finish/review them before rebasing or merging anything. The checkout also contains roughly 197 pre-existing untracked probes, scans, scratch outputs, and other files. They are intentionally being preserved; do not run `git clean`, `git reset --hard`, broad restore, or broad `git add`. Do not stage those unrelated paths. Some untracked paths may contain useful work, so inspect before making any decision about them.

For other agents, use distinct worktrees branched from the latest `origin/main`, keep each assignment within its named file/test ownership, and return a commit hash plus exact validation results. Do not ask parallel agents to push or merge. Integrate one worktree at a time after reviewing its diff and running its tests. Never overwrite this checkout's current goal-store edits.

## Current work to finish first

`ApexGoalStore` previously persisted child creation and parent linkage in separate writes. A crash between them could leave an orphan. It also cached rows independently in each process, allowing one worker to overwrite another worker's changes. The in-progress edit in `backend/packages/harness/alpha/apex/goals.py` adds a same-host shared file lock, reload-before-write, snapshot rollback on failed persistence, and journal emission only after a successful state save. It wraps nested writes such as `create_child` in one transaction. The focused regression tests are in `TestGoalOperatingSystem` in `backend/tests/test_apex_control.py`.

The focused goal class passed 16 tests; Ruff check and format check passed. Re-run these after reviewing the final diff:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_apex_control.py::TestGoalOperatingSystem -q
backend\.venv\Scripts\ruff.exe check backend/packages/harness/alpha/apex/goals.py backend/tests/test_apex_control.py
backend\.venv\Scripts\ruff.exe format --check backend/packages/harness/alpha/apex/goals.py backend/tests/test_apex_control.py
```

Then run the related APEX control and auth suites. A prior attempt at the combined suite was interrupted and is not a passing result:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_apex_control.py backend/tests/test_apex_authz.py -q
```

Review edge cases before calling this done: missing snapshot versus corrupt snapshot; rollback of all live object references on failed save; nested transaction behavior; cross-process reads and writes; event journal failure after snapshot success; directory fsync and Windows replace semantics; and no false cross-host/exactly-once claims. The state snapshot and event journal are still separate files, so a crash after the state save but before journal append can lose an event. Do not claim atomic state-plus-journal commits.

## Verified baseline and open uncertainty

Earlier confirmed checks include: APEX store durability (26 tests), APEX mode (40), APEX API (58), dispatcher integration (1), SSE stream class (5), and goal operating system class (16). The API suite emitted one existing Starlette/httpx deprecation warning. Ruff and format checks passed for the then-current changes. Re-run relevant checks against the final combined tree; these historical results do not validate later edits.

The latest rerun of `backend/tests/test_apex_control.py backend/tests/test_apex_authz.py -q` passed: 133 tests in 180.86 seconds, with one existing Starlette/httpx deprecation warning. `backend/tests/test_apex_executive.py -q` then passed all 69 tests after its fixture helpers were changed to persist acceptance/usage through `ApexStore.update`; direct mutation of a stale cached object is intentionally not durable. The parked-cycle regression was fixed before the control/auth run. The frontend typecheck passes after fixing missing/incorrect dialog imports/props and disconnected local Kanban editor actions. Focused reconnect/idempotency tests passed 24/24; collaboration surface honesty tests passed 42/42; the load-failure suite reported 67 passing tests in the combined run. A prior collab run exposed a brittle JSX whitespace matcher, now corrected. No full production-scale, multi-month, or broad provider-backed autonomous task has been demonstrated. Current limitations include same-host local-file locking only, no cross-host exactly-once guarantee, no automatic trusted evidence collectors for arbitrary natural-language criteria, opt-in APEX background loop, and parked recovery cases which require owner/operator action in some paths. See the current APEX guide and production inventory for exact behavior.

The latest broader run found a real `run_cycle` bug: a paused session's `blocked=True` no-op decision entered the generic blocked path and created a spurious approval. `backend/packages/harness/alpha/apex/executive.py` now returns a no-op for `REASON_SESSION_PAUSED`; the focused regression plus goal class passed 17 tests together, the full control/auth rerun passed 133 tests, and the executive suite passes 69 tests after its stale-object fixtures were corrected.

## Workstreams and file ownership

These assignments are designed to avoid overlapping edits. Start from updated `origin/main` in separate worktrees after the current main-branch persistence change is committed or otherwise clearly communicated.

| Workstream | Primary ownership | Required outcome | Avoid |
| --- | --- | --- | --- |
| Supervisor lifecycle and restart recovery | `backend/app/gateway/autonomy/supervisor.py` and its dedicated supervisor tests/docs | Prove bounded restart/backoff, shutdown ordering, stale-owner handling, and recovery decision reporting; add deterministic crash/restart tests | Do not edit `apex/goals.py`, APEX session store, dispatcher loop, or UI |
| Dispatcher and RunManager recovery | `backend/app/gateway/autonomy/loops.py`, `backend/app/gateway/services.py`, `backend/tests/test_apex_dispatcher.py` | Verify one dispatcher owns runs; close admission/restart races; test crash after admission and terminal-run projection behavior | Do not edit supervisor module or goal store; coordinate before shared docs |
| Delegation policy and capability ceilings | Existing task middleware and APEX governance owner identified in `backend/AGENTS.md`; only those middleware/governance files and their focused tests | Ensure every ordinary child call revalidates owner, session stamp, approval, tool policy, and persisted parallel/active-agent caps; test malformed/stale stamps and concurrency | Do not weaken approval, shell, authority-ceiling, or emergency-stop checks; avoid dispatcher/store files |
| Acceptance and evidence provenance | `backend/packages/harness/alpha/mission/acceptance.py` plus focused acceptance tests and the existing acceptance route owner | Define trustworthy evidence sources and refusal semantics; ensure unmeasured/incomplete reports stay unverified; no model-summary self-certification | Do not edit goal persistence or dispatcher; do not introduce “verified” status without execution-backed evidence |
| APEX user interface and accessibility | APEX-only components under `frontend/src/` plus their colocated tests | Make status, awaiting-verification, blocked/approval, partial, unlimited ceilings, and recovery action clear; keyboard/screen-reader accessible | Do not change backend API contracts without a separate proposal and coordinate before shared docs |

The user may assign these prompts to other coding agents. In each prompt instruct the agent to read repository guidance, use its own worktree, preserve all existing files, work only in its named ownership, run focused tests, avoid pushing/merging, and return changed paths, commit hash, test output, and known limitations. If a worktree task changes its ownership or needs a shared API change, have it report the proposal before editing shared files.

### New requested feature: visible chat-stream reconnect

The user supplied a screenshot of a client visibly reconnecting (`Reconnecting 5/5`, followed by a waiting-for-network message) and asked Alpha to provide the same kind of feature, including its UI. The intended behavior is a truthful, bounded live status during a chat SSE disconnect: current retry number, whether the client is waiting or attempting to join, and a network-offline message when the browser reports offline. Resume from the last received event id so the Gateway's replay path avoids losing already-persisted output. Do not present a transport reconnect as proof that the run is alive or completed.

Implemented in `frontend/src/lib/chat-stream.ts` (resume retry state and retrying transient join-network errors), `frontend/src/lib/idempotency.ts` (visible initial-admission retries), `frontend/src/components/ActivityStatus.tsx` (accessible visual status), and `frontend/src/components/ChatView.tsx` (per-run state wiring and clearing). Focused coverage is in `frontend/src/lib/chat-stream.test.mjs` and `idempotency.test.mjs`. The 24 focused stream/idempotency tests pass, `collaboration-surfaces-honesty.test.mjs` passes 42/42 after its SSR-only a11y stub and whitespace-tolerant source assertion were fixed, and the frontend TypeScript check passes. If it needs a different bounded retry policy, preserve the server's `retry:` instruction, stop cancellation, event replay cursor, and non-retry behavior for HTTP/auth/invalid-response errors. UI state clears when bytes resume, the run ends, the user navigates away, or Stop is pressed. Any future offline wait should be tied to real browser `online`/`offline` events and still disclose retry limits; never fake an infinite reconnect guarantee.

Suggested separate follow-up ownership: a later UI-only agent may refine layout/visual polish in `ActivityStatus.tsx` and its test after this transport contract is merged; it must not edit `chat-stream.ts` or `ChatView.tsx` unless the contract owner agrees.

## Detailed implementation sequence

1. **Finish local goal transaction durability.** Complete review and tests above. Preserve object identity where callers hold a goal object, fail closed on corrupt state, and verify parent/child links commit as one snapshot. Document the same-host limitation and state/journal crash window. Commit only the explicitly reviewed goal-store, focused-test, and relevant documentation files.
2. **Exercise same-host multi-worker behavior.** Run separate processes against one temporary APEX directory. Concurrently create/update goals, sessions, and modes; assert no lost rows, invalid JSON, or duplicate dispatch admission. Repeat on Windows, which is the user's target. Treat advisory locking on network filesystems as unproven.
3. **Audit lifecycle ownership.** Trace a session from creation through policy check, dispatch admission, RunManager start, status/usage projection, acceptance, recovery, and terminal state. Preserve `RunManager` as sole run lifecycle owner and `AutonomySupervisor` as sole background-loop owner. Add tests at the failure windows, not just helper unit tests.
4. **Make recovery bounded and inspectable.** Inject controlled process interruption at each durable boundary. Verify retry ceilings, exponential backoff/jitter bounds, lease expiry, duplicate suppression, shutdown drain, and explicit parked/blocked explanations. Ensure recovery never silently repeats a side effect whose result is unknown; reconcile or request operator input.
5. **Improve planning and decomposition.** Validate a user objective into constraints and measurable criteria, plan dependency-aware child goals, enforce ancestor priority/capability/risk ceilings, and persist plan revisions atomically. Handle empty/ambiguous objectives by asking the user rather than inventing acceptance. Do not give children broader tools or authority than the parent contract.
6. **Harden acceptance evidence.** Add narrowly scoped trusted collectors (for example, actual test exit reports, artifact existence/content hashes, and explicit owner approval) with provenance, timestamps, scope, and failure details. Each criterion needs an explicit mapping to a collector or remains unverified. Test stale, partial, duplicated, forged, and contradictory evidence. A model's narrative is not a measurement.
7. **Audit delegation and tool governance.** Test every tool path used by APEX, including nested ordinary tasks and durable batches. Verify policy is rechecked at the boundary, approvals are scoped and expiring, denied actions are visible, cancellation propagates, and aggregate concurrency is honestly described. Never bypass the emergency stop or tenant ownership for convenience.
8. **Improve operator and user reporting.** Ensure status surfaces distinguish planned, running, waiting, blocked, awaiting verification, verified acceptance, partial, failed, and cancelled. Show resource use and unlimited configured quotas separately. Expose decisions, policy refusals, evidence provenance, recovery actions, and next required user action without leaking secrets or other owners' data.
9. **Run a bounded real task end to end.** Use a temporary local repo/workspace and a safe task with measurable outputs, such as generate a small static page plus a passing local test and an artifact manifest. Observe actual APEX decisions, decomposition, dispatch, tool use, and evidence records. Simulate a controlled worker restart at a known checkpoint, then verify recovery and no duplicate side effect. Avoid paid services, credentials, external publishing, or destructive actions unless the user's configured environment explicitly supplies them and policy allows them. Save sanitized logs and exact acceptance receipts. Report what was and was not exercised.
10. **Run reliability and release gates.** Add repeatable chaos/restart and multi-process tests, schema migration checks, supported-Windows startup/recovery checks, backup/restore drill, load/soak measurements, and alert/runbook coverage. State the duration, workload, and observed failures; a short test cannot support a years-long uptime promise. Update `docs/PRODUCTION_READINESS_INVENTORY.md` conservatively based on measured gates.

## Full-project implementation roadmap

This roadmap covers Alpha beyond APEX. It is ordered by dependencies and risk, not by marketing priority. For every phase, begin with a live inventory: read the owning guides, inspect the current implementation and tests, identify the actual caller/wiring path, and write down what is missing before editing. Existing modules are not proof of working features. Preserve Alpha's additive approach: extend the existing owner rather than creating a competing manager, registry, policy engine, or persistence format.

### Phase A — protect the working tree and establish a truthful baseline

- Start with `git status --short --branch`, `git log -5 --oneline`, remote state, and all existing test/build commands. Save a baseline report. Keep user changes, untracked work, ignored runtime config, and generated artifacts distinct. Never clean/reset/restore blindly.
- Read root, backend, and frontend `AGENTS.md`; then the guides linked for the module being touched. Run feature-manifest, docs-index, generated-brand, and module test checks before registry/docs edits.
- Reconcile the public capability inventory with `contracts/feature_manifest.json`. Fix overclaims and dead wiring before adding capabilities. Do not hand-maintain capability totals.
- Establish a CI baseline on Windows and the supported Linux environment. Identify tests skipped by globs or absent CI jobs; add explicit scripts/CI steps only with meaningful tests.
- Exit gate: reproducible clean baseline, documented existing failures, exact branch ancestry, and no unreviewed user file included in the proposed change.

### Phase B — durable execution, restart and state safety

- Keep `RunManager` the only run-lifecycle owner, `SafeRunRecoveryService` the only safe-continuation authority, and `AutonomySupervisor` the only background-loop owner. Map every startup, shutdown, crash, lease, unknown side effect, cancellation and recovery transition against `docs/architecture/durable-runtime.md`.
- Test persistence and migrations on supported stores; distinguish same-process, same-host, shared-filesystem and distributed guarantees. Add multi-process tests where the contract claims worker safety. Single-process JSON/JSONL plus local locks is not distributed exactly-once.
- Add crash-window tests around admission, checkpoint writes, usage projection, event journaling, safe retries, and shutdown. Unknown side-effect outcomes must reconcile or park for a human rather than replaying blindly.
- Finish backup/restore, schema upgrade/downgrade safety, filesystem-full, corrupt-state, lock-timeout, and orphan cleanup cases. Provide an operator runbook with measured recovery time.
- Exit gate: each recoverable state has one owner, one bounded retry policy, explicit terminal/parked behavior, and restart evidence. Report limitations for unsupported filesystems and hosts.

### Phase C — APEX goals, planning, delegation and evidence

- Complete the APEX sequence above. Keep the executive cycle bounded and deterministic; model output can propose a plan but policy code decides allowed actions. Verify scope/owner authz, policy digest drift, mode, approvals, emergency stop and terminal gates at every route/tool boundary.
- Build structured planning/decomposition with dependency ordering, constraints, resource projections, risk labels, ancestor ceilings, plan revisions and atomic parent/child persistence. Ambiguous goals or missing acceptance criteria must ask instead of inventing requirements.
- Build real measured acceptance collectors with provenance and a criterion-to-collector mapping. Model narrative alone cannot pass. Show verified only after the measured report passes all criteria.
- Keep resource ceilings distinct from actual configured capacity, max agents, approvals, and tool capability. Unlimited token/runtime ceilings must not imply unlimited machine resources or policy authority.
- Exit gate: the bounded end-to-end local task in this document passes through actual planning, dispatch, recovery, and acceptance, while tests prove refusal, failure, and partial-result handling.

### Phase D — tools, models, skills, MCP and extensions

- Audit the full execution chain from user prompt to registered capability to tool middleware to sandbox/provider call and result. For each capability check input validation, owner scope, permission, cancellation, timeout, bounded output, secret handling, retry safety and error reporting.
- Models: use the single `config.yaml` authority; validate names and provider slugs against live provider catalogs, refresh rotating catalogs, distinguish unavailable from empty, and avoid silent fallback. Exercise provider timeouts, malformed responses, rate limits, and keyless/free routes without leaking credentials.
- Skills: use `docs/SKILLS.md` and the skill quality gates. Validate UTF-8, metadata, safety boundaries, provenance, quarantine/review, and the runtime loading path. No newly discovered skill should be trusted merely because it parses.
- MCP and extensions: exercise stdio/HTTP/SSE lifecycle, reconnect, auth, server shutdown, tool schema validation, response limits, and dependency/source trust. Extensions execute with Gateway privileges; installation/upgrade/rollback must be transactional and operator-controlled.
- Exit gate: generated feature manifest proves wiring; adversarial tests cover malformed/untrusted tool and MCP content; production inventory reflects real behavior and remaining trust assumptions.

### Phase E — security, identity, tenancy and side-effect governance

- Read `SECURITY.md`, `docs/SECURITY.md`, per-route auth guides, peer network contract, and the tool governance owners. Draw principal/owner/session boundaries for browser, PAT, admin, model, background loop, child task, and peer callers.
- Test route authz systematically: anonymous, ordinary user, owner, other owner, admin, expired/revoked token, auth-disabled mode, malformed identity, and storage-degraded behavior. Keep public exact-path exceptions narrowly scoped and separately token checked.
- Audit prompt injection, tool output, uploads, URLs, local file boundaries, archive extraction, shell command policy, extension trust, secrets in logs, and cross-tenant state/artifact paths. Add a versioned adversarial fixture suite and ensure failures are real CI gates.
- Require explicit scoped/expiring approval for irreversible external actions. Record idempotency keys and unknown outcome reconciliation. Never turn user-supplied approval flags into authority.
- Exit gate: route/tool threat matrix is covered by regression tests, every accepted side effect has an owner and policy decision, and unresolved risks are written in the security docs.

### Phase F — frontend, desktop and accessibility

- Follow `frontend/AGENTS.md` and `frontend/src/AGENTS.md`: API clients through shared `http.ts`, server-owned state only, explicit unavailable/unknown/partial states, and real route/view wiring. Preserve the uncapped local chat archive and its IndexedDB write discipline.
- Finish the screenshot-inspired reconnect feature in this change: visible bounded attempts, waiting/connecting/offline distinctions, resume from last event id, initial admission retry status, cancellation, and clean state on navigation/exit. It is transport state, never a run-completion claim.
- Audit each major surface (chat, APEX, projects/groups, workflows, tools/MCP, integrations, files, settings, desktop shell) for loading/empty/error/permission/degraded modes, keyboard flow, screen-reader announcements, narrow-window layout, and confirmation for consequential actions.
- Desktop and Windows recovery: honor the one generated brand asset source; validate packaged files, installer/shortcuts, tray icon, watchdog/autostart, recovery paths and update rollback on a clean Windows profile. Avoid concurrent Next dev/build per frontend instructions.
- Exit gate: UI displays facts from live APIs; workflows are reachable; typecheck has no new errors; focused and broad frontend suites pass; Windows packaging/recovery is exercised.

### Phase G — observability, operations and long-running behavior

- Make health/readiness differentiate process alive, dependencies ready, loops enabled/disabled, queue saturation, degraded storage, network waits, and recovery backlog. Never let one green health response stand in for every subsystem.
- Add structured correlation IDs and bounded/redacted logs, metrics for queue age, run/loop retries, unknown side effects, event gaps, resource usage and recovery time. Define retention and operator dashboards/alerts without transmitting telemetry by default.
- Specify deployment sizing, concurrency, storage durability, backup cadence, upgrade order, rollback, incident response, and support diagnostics. Keep nginx loopback binding as default and require explicit exposure decisions for every published port.
- Add staged load, soak, restart, network-loss, disk-pressure and backup/restore drills. Record host, versions, workload, duration, error rate, memory/CPU, and recovery results. No finite soak test proves multi-year uninterrupted service.
- Exit gate: reproducible runbooks and alert thresholds exist; tested recovery objectives are met in measured drills; unsupported topologies are clearly named.

### Phase H — product quality, benchmarks and release

- Create a versioned, representative benchmark set spanning coding, docs, research, file operations, tool use, MCP, multi-agent coordination, refusal/security, recovery and evidence quality. Use offline deterministic tests by default and provider-backed canaries only when configured. Score correctness, safety, provenance, completion time and resource use separately.
- Fix correctness, task cancellation, memory/history quality, accessibility, performance and onboarding gaps surfaced by measured use. Keep self-improvement proposal-only until offline evaluation, review, canary, provenance and rollback are wired.
- Validate all component version sources with `scripts/verify_versions.sh`; update using `scripts/bump_version.sh`. Run feature-manifest/docs-index/branding gates, backend tests+Ruff, frontend typecheck/tests, packaging, migration checks, and release workflows.
- Update changelog, install/troubleshooting docs, production inventory, README and all llms files. Publish only after a human review of the release artifact and its measured evidence.
- Exit gate: release claims correspond to the actual benchmark and deployment envelope, all critical CI gates pass, no secret/config/runtime state is included, and rollback steps are verified.

## Copy/paste work orders for parallel agents

Use one prompt per agent in a separate worktree from the latest `origin/main`. Do not assign overlapping ownership. Each agent must preserve all files, make no push/merge, run focused checks, and return changed paths, commit hash, exact results and limitations.

### 1. Supervisor restart and recovery

```text
Improve Alpha's APEX supervisor lifecycle/restart reliability. Read root AGENTS.md, backend/AGENTS.md, docs/architecture/durable-runtime.md, docs/APEX_AUTOPILOT.md, and the supervisor module guide. Work in your own worktree based on updated origin/main. Preserve every existing file; do not clean, reset, or stage unrelated paths. Own backend/app/gateway/autonomy/supervisor.py and its dedicated tests/docs only. Audit bounded restart/backoff, shutdown/drain ordering, stale ownership, lease expiry, crash-loop limits, and parked recovery reporting. Add deterministic tests for failure windows. Keep RunManager as the only run lifecycle owner and SafeRunRecoveryService as the only safe-continuation authority. Do not edit APEX goals/store, dispatcher loops/services, delegation middleware, frontend, or shared docs without first proposing a narrowly scoped change. Run focused tests and Ruff check/format. Do not push or merge. Return paths, commit hash, exact commands/results, unresolved risks and any needed API proposal.
```

### 2. Dispatcher and RunManager recovery

```text
Harden Alpha APEX's Gateway dispatch/admission/restart boundary. Read root AGENTS.md, backend/AGENTS.md, docs/APEX_AUTOPILOT.md, docs/RUN_RECOVERY.md, and RunManager ownership guides. Use a separate worktree from updated origin/main; preserve all existing code and scratch files. Own backend/app/gateway/autonomy/loops.py, backend/app/gateway/services.py, and focused backend/tests/test_apex_dispatcher.py tests only. Trace session admission through RunManager start, disconnect, restart, usage projection, acceptance and terminal projection. Close duplicate-admission or stale-run-link races using the existing durable owner; do not add a second dispatcher/lifecycle store. Test interruptions around admission and projection, with acceptance remaining a separate evidence gate. Do not edit supervisor.py, apex/goals.py, or UI. Run focused tests and Ruff check/format. Do not push or merge. Return paths, commit hash, exact test output, invariants proven and remaining limits.
```

### 3. Acceptance evidence and provenance

```text
Harden Alpha APEX acceptance evidence only. Read root AGENTS.md, backend/AGENTS.md, docs/APEX_AUTOPILOT.md, docs/PRODUCTION_READINESS_INVENTORY.md, and the alpha.mission acceptance guide. Use your own worktree from updated origin/main; preserve every existing file. Own backend/packages/harness/alpha/mission/acceptance.py and narrow acceptance tests (prefer dedicated test_apex_acceptance* tests; do not take over goal store or dispatcher). Define evidence provenance, criterion coverage, timestamps, and refusal behavior. Explore measured collectors only for actual test exit reports and bounded artifact facts. Model summaries or self-asserted booleans must never become trusted evidence. Test incomplete, duplicate, stale, forged, contradictory, and failed evidence; preserve honest awaiting-verification/partial outcomes. Do not make arbitrary natural-language criteria look automatically verified. Run focused tests and Ruff check/format. Do not push or merge. Return paths, commit hash, exact results and what remains unverified by design.
```

### 4. Delegation and capability governance

```text
Audit and improve APEX delegated-agent governance. Read root AGENTS.md, backend/AGENTS.md, relevant task middleware guides, docs/APEX_AUTOPILOT.md, and owners for alpha.bots.authority_ceiling and alpha.tools.governance. Use a separate worktree from updated origin/main; preserve all existing files. Own only existing ordinary-task middleware/governance modules and their focused tests; do not edit RunManager, supervisor, goal persistence, acceptance, or UI. Verify owner/session-stamp validation, invalid/stale contract behavior, approval scope/expiry, active-agent and parallel caps, nested tool rechecks, cancellation and separate durable batch_task limits. Never widen authority or bypass emergency stop. Add failure-path tests and document remaining aggregate limits. Run focused tests and Ruff check/format. Do not push or merge. Return paths, commit hash, exact results and policy boundaries exercised.
```

### 5. APEX UI and operator workflow

```text
Improve Alpha's APEX UI as an honest operator surface. Read root AGENTS.md, frontend/AGENTS.md, frontend/src/AGENTS.md, docs/APEX_AUTOPILOT.md, and production inventory. Use a separate worktree from updated origin/main; preserve all files. Own only APEX section components, APEX client/view helpers, and focused tests. Show real backend states distinctly: disabled, running, waiting, blocked/approval, awaiting verification, completed with measured acceptance, partial, failed, and cancelled. Make recovery and pending operator action clear; show unlimited configured spend separately from operational concurrency/approval caps. Preserve unknown states verbatim; never fabricate health, counts, or completion. Do not edit backend APIs, ChatView/activity/reconnect transport files, or shared docs without proposing changes first. Add accessible UI tests, run TypeScript and relevant frontend tests, and report pre-existing errors separately. Do not push or merge. Return paths, commit hash, exact results and any backend contract needed.
```

## Documentation and generated-file obligations

When behavior or a public claim changes, update `README.md`, `CHANGELOG.md`, `docs/APEX_AUTOPILOT.md`, the relevant module `AGENTS.md`, and all three agent indexes (`llms.txt`, `llms-full.txt`, `docs/llms.txt`) as applicable. Keep absolute GitHub URLs in the llms files. Do not hand-edit `docs/INDEX.md`; new pages under `docs/` require a `FILE_OVERRIDES` entry in `scripts/generate_docs_index.py` and index regeneration. Capability counts are generated from `contracts/feature_manifest.json`; regenerate the manifest and update all required count references together if a registry/wiring change affects them. Never edit `CLAUDE.md`.

## Integration and completion checklist

- Check `git status`, current branch, `HEAD`, and `origin/main` before any integration. Preserve all unrelated tracked and untracked files.
- Review the full diff and confirm no user data, secrets, generated noise, or scratch scans are included.
- Run focused unit tests, relevant API/integration tests, Ruff check and format, and required module gates. Record warnings and interrupted runs accurately.
- Run documentation checks and generated manifest checks if applicable.
- Keep each commit focused. Push only after inspecting the exact staged file list and verifying branch ancestry. Attach/open a PR only if the user asks or repository workflow requires it.
- Update this handoff with completed work, exact commits and commands, and remaining blockers so the next agent can resume without guessing.

The intended result is a more reliable, evidence-driven APEX system with clear operator controls and honest limits. “Production ready for every possible task” is not a testable acceptance criterion; replace it with explicit workloads, safety boundaries, recovery objectives, and measured evidence.
