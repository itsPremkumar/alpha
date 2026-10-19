# Alpha Harness Lab

Alpha's implementation of the recursive harness improvement method described in
[`ALPHA_HARNESS2_IMPLEMENTATION_GUIDE.md`](ALPHA_HARNESS2_IMPLEMENTATION_GUIDE.md):
one **improvement run** executes a task under the current harness, lets bounded
proposals suggest candidate improvements, probes each candidate in its own
isolated workspace, measures the difference against the baseline, and composes
the cheapest *evidence-supported* successor. Promotion of a successor to the
active harness is a separate, gated step — a run never promotes anything.

**The subsystem is disabled by default, and even enabled it cannot execute
anything in a stock deployment: no executor and no editor are wired.** A run
attempt refuses with the reason named instead of parking work that could never
progress, and every surface carries a machine-readable capability row saying
so. This page is the user/operations view; the engine contract lives in
[`backend/packages/harness/alpha/harness_improvement/AGENTS.md`](../backend/packages/harness/alpha/harness_improvement/AGENTS.md).

---

## 1. Why it exists

Improving an agent harness (its prompts, guidance, skills, subagent
definitions) is usually done by editing text and hoping. The improvement loop
makes each change carry its own evidence: a falsifiable hypothesis, a probe
that ran the same task under the candidate, a contrast against the baseline,
and a composition step that is allowed — encouraged, even — to answer **"no
change"**.

Three rules make the results trustworthy rather than decorative:

| Rule | Why it is load-bearing |
| --- | --- |
| A check that did not run is **not** a pass | `not_run` / unmeasured gate results can never satisfy a promotion gate: a candidate whose regression suite never ran cannot become active. |
| The engine **never fabricates a result** | With no executor/editor wired, a run parks at `blocked` naming the missing collaborator. It never invents a probe trace, a validator result, or a deliverable. |
| **"No change" is a valid outcome** | When no candidate shows supported benefit, the baseline is retained as the successor and the run says so — a correct result, not a failure. |

## 2. The improvement run

Stages, in order:

```
queued → snapshotting → baseline_running → proposing → candidates_validating
       → probing → contrasting → composing → successor_running → verifying
       → selecting → reflecting → completed
```

Plus `blocked` (parked, resumable once a collaborator exists), and the terminal
`failed` / `cancelled` / `budget_exhausted` — outcomes, never missing rows.

What happens in each stage:

| Stage | What it does |
| --- | --- |
| `snapshotting` | Freezes the editable harness surface into an immutable, content-addressed **`HarnessBundle`**. |
| `baseline_running` | Executes the task under the baseline bundle (its own workspace) and records a `ProbeOutcome`. |
| `proposing` | Asks the editor seam for bounded proposals — one general-scope, one task/domain-scope — each with a **falsifiable hypothesis**; a proposal without a falsification condition is refused. |
| `candidates_validating` | Runs the untrusted proposal through the patch validator: protected policy surface, component-id hygiene, credential/network content screens, size bounds. Every refusal is specific and recorded. |
| `probing` | Executes the task under each accepted candidate, in its **own isolated workspace**, and records probed outcomes plus an environment fingerprint. |
| `contrasting` | Answers the guide's eight narrow questions between baseline and candidate. `confounded` outranks everything; single-run results are flagged as such. |
| `composing` | Produces the successor: only supported edits are adopted; contradictions resolve by edit cost; nothing supported ⇒ the baseline is retained. |
| `successor_running` / `verifying` | Executes the task under the successor (unless the baseline was retained) and re-runs the deterministic deliverable checks. |
| `selecting` | Chooses the deliverable — the successor only when it is actually supported by re-measured checks. |
| `reflecting` | Stores evidence-gated lessons in the experience library; no evidence, no lesson. |

**Durability.** Every stage transition is persisted before the next stage
starts, and per-stage idempotency keys make a resumed run skip already-done
work instead of double-spending. Runs are guarded by a cross-process file lock
so two workers cannot drive the same run. The state files are single-process
JSON/JSONL: atomic against a crash in one process, never cross-process
exactly-once.

## 3. Bundles and the promotion ladder

A `HarnessBundle` is an immutable snapshot of eight editable component kinds
(system prompt, guardrail *guidance*, memory references, tool guidance, skills,
subagent definitions, scripts, middleware config). The non-bypassable safety
core is **not** part of the editable surface — no diff can target it, and the
validator and composer both refuse a proposal that names it.

The promotion ladder has six forward rungs:

```
draft → statically_validated → probed → regression_tested → shadow → canary → active
```

- **Gates are tri-state**: satisfied / unsatisfied / *unmeasured*. Unmeasured
  is never a pass. The regression-suite gate is real: the Gateway runs no suite,
  so it stays unmeasured and refuses — an operator cannot force-skip it.
- **Approval is substantive**: `approve` records an operator approval only when
  the other gates already pass. A required approval flag for scripts,
  middleware or permissions is a refusal even under `force`.
- **Activation is an atomic pointer swap that remembers its predecessor**, so
  rollback is a one-step restore. A rollback with no predecessor says exactly
  that instead of clearing the pointer.
- Every promotion event lands in a durable promotion log, in append order.

## 4. Production posture: what is wired

In a stock Gateway: **no executor, no editor, no baseline provider**. The
`harness_improvement` config section exists (disabled), the library and HTTP
routes are mounted and testable offline, and nothing executes model work.

That absence is machine-readable, not only prose. `GET /api/harness/status`
carries a `harness_capability` row — `state: "absent"`, `executes: false`, a
note, and a `missing_wiring` list naming the exact seams a deployment would
have to provide. It answers **even while the feature is disabled**, because a
client checking whether this plane *can* execute is precisely the client that
should be told before it plans around it. `POST /api/harness/runs` refuses with
`409 harness_baseline_absent` when no active bundle and no baseline provider
exist — it never parks a run that could never progress. A deployment that wires
a baseline provider but no executor parks the run at `blocked` naming
`HarnessExecutor` instead, because a run *can* make progress once that seam is
added; the park is the honest state, not a fake baseline.

Binding a real executor is a separate trust decision (isolation,
deny-by-default network, redaction, budgets — guide §12) that this subsystem
deliberately has not made.

## 5. HTTP API

18 routes under `/api/harness`. **Reads are authenticated; every mutation is
admin-gated** (a PAT is never an admin). The subsystem is
**installation-scoped** — one store, one active bundle — so payloads are not
partitioned per user: an improvement run measures this deployment's harness,
not one person's data.

| Method & path | Kind | What it answers / does |
| --- | --- | --- |
| `GET /status` | read | Feature posture: enabled, store root, counts, the capability row. Answers while disabled. |
| `GET /runs` | read | Newest-first run summaries plus `unreadable` disclosure. |
| `POST /runs` | mutation | Start a run from a task contract. Refuses when disabled, unattributed, or executor/baseline absent. |
| `GET /runs/{id}` | read | Full run projection: stage, proposals, validations, probe outcomes, contrasts, composition, deliverable, budget. |
| `GET /runs/{id}/events` | read | The run's JSONL journal (bounded tail) with torn lines disclosed. |
| `POST /runs/{id}/resume` | mutation | Re-enter the stage a `blocked` run parked at. |
| `POST /runs/{id}/cancel` | mutation | Stop a run. Reachable while the feature is disabled (as are reject and rollback): refusing a stop would leave an operator unable to halt a run a previous configuration started. |
| `GET /bundles` | read | Bundle list with promotion states; malformed manifests named in `unreadable`, never dropped. |
| `GET /bundles/{id}` | read | One bundle's manifest and component refs. `503` when the manifest is corrupt. |
| `GET /bundles/{id}/diff` | read | The component-level diff against its parent bundle. |
| `GET /experiences` | read | The experience library, newest first, filterable. |
| `GET /experiences/{id}` | read | One lesson, including its scope and counter-evidence. |
| `GET /promotions` | read | The ladder vocabulary, the promotion log, and the active pointer. |
| `POST /candidates/{id}/approve` | mutation | Record an operator approval — only when the substantive gates already pass. |
| `POST /candidates/{id}/reject` | mutation | Reject a candidate with a recorded reason. |
| `POST /active/{id}/activate` | mutation | Atomic pointer swap to a fully gated bundle. Every unmet gate still refuses with its own words. |
| `POST /active/{id}/rollback` | mutation | One-step restore of the prior active bundle. |
| `GET /budgets` | read | The measured budget ledger plus `unreadable` disclosure. |

Store I/O never runs on the event loop (config resolution, listing scans and
ledger reads go through `asyncio.to_thread`). An unreadable store is a degraded
state, never an empty `200`: a corrupt manifest, run record or ledger answers
`503` carrying the store's own reason, while a *missing* file stays genuinely
empty.

## 6. Configuration

`config.yaml → harness_improvement` (see `config.example.yaml`). Defaults are
the guide's conservative ones; every bound is **refused when out of range,
never clamped**:

```yaml
harness_improvement:
  enabled: false                 # master switch; off = run/advance writes (start, resume, approve, activate) refuse with the reason named; stop-or-restore writes (cancel, reject, rollback) and reads stay reachable
  mode: "sequential"             # the only implemented mode
  max_steps: 1                   # improvement cycles per run
  max_candidates_per_scope: 1    # proposals per scope
  max_concurrent_probes: 1
  max_wall_time_seconds: 300
  max_tool_calls_per_probe: 60
  max_model_turns_per_probe: 80
  max_diff_files: 8              # refused, never truncated
  max_changed_bytes: 32768
  max_model_calls: 24
  max_cost_usd: null             # null = unstated; unreported usage leaves the dimension unenforceable, and that is reported, never read as "under budget"
  model: null                    # optional models[] name for probes and the successor run
  seed: null
  sandbox:
    isolate_filesystem: true
    isolate_memory_writes: true
    allow_external_side_effects: false
    network_mode: "deny_by_default"
    allow_candidate_middleware_changes: false
    allow_candidate_guardrail_policy_changes: false
  promotion:
    automatic_global_promotion: false
    require_regression_suite: true
    require_human_approval_for_scripts: true
    require_human_approval_for_middleware: true
    require_human_approval_for_permissions: true
    keep_last_known_good: true
```

`model` is validated against `models[]` at load, so a typo is a startup error.
The config section and the engine's settings object are kept in lockstep by
test: a key added on one side without the other fails a test, not a run.

## 7. The Harness Lab view

The frontend workspace includes a **Harness Lab** section that reads the HTTP
API above: feature status and the capability row, improvement runs with their
stage and deliverable, the immutable bundle tree and diffs, the promotion
ladder and activation/rollback controls, the experience library, and the budget
ledger. The rendering rules match the backend contract:

- an unreported value renders as **"not reported"**, never as `0`, `$0` or an
  empty list; an absent list is a distinct sentence from an empty one;
- a store-degraded response is shown as **degraded**, naming the store's own
  reason, never as an empty list;
- resumability and the promotion ladder are the server's answers, never
  re-derived in the view;
- the kill switch is never re-checked client-side: start/resume/approve/
  activate send the request and render whichever answer comes back (a `503`
  naming the setting, a `409` absent-baseline refusal, or a `403`), so the
  refusal is visible instead of hidden behind a disabled control. Controls
  disable only while a request is in flight or when nothing is selected;
- every mutation re-reads the authoritative views (posture, runs, bundles,
  promotion log) rather than painting the receipt it received.

Writes (start/resume/cancel, approve/reject, activate/rollback) are admin-only
at the Gateway; the view adds no gate of its own and shows the server's refusal
verbatim.

## 8. Tests

All offline, scripted actors only, zero model calls:

- `backend/tests/test_harness_improvement_engine.py` — the full cycle, resume
  from each checkpoint, cancellation, budget exhaustion, retained-baseline,
  provenance stamps.
- `backend/tests/test_harness_improvement_patches.py` — every rejection reason,
  protected-surface refusals, path hygiene, bounds.
- `backend/tests/test_harness_improvement_config.py` — defaults, bound
  refusals, engine-settings round-trip.
- `backend/tests/test_harness_improvement_promotion.py` — ladder edges,
  tri-state gates, approvals, activation, one-step rollback, log honesty.
- `backend/tests/test_harness_improvement_router.py` — route inventory, auth
  and admin sweeps, disabled posture, start refusals, a full cycle over HTTP,
  promotion flows, degraded-store behavior.
- `frontend/src/lib/harness-lab.test.mjs` — the client: exact routes and verbs
  for all 18 endpoints, null-preserving mappers, bounds, local refusals, and
  verbatim failure text.
- `frontend/src/lib/harness-lab-view.test.mjs` — the four-place view wiring
  (union, tabs, routable list, ChatView) and the honesty pins over
  `HarnessLabSection.tsx`: absent vs empty states, tri-state reads, the
  kill-switch and ladder contracts, and receipt-then-re-read after mutations.
