# Multi-worker and process-local state

**Scope.** What Alpha guarantees with one Gateway worker, with N workers, and
with none of them. This is the operator-facing summary; the module guides own
the detail and this document does not restate it.

Alpha is honest about being single-process. That honesty is unevenly
distributed: some modules say it loudly, others quietly rely on process-local
state behind an API that looks durable. This page is the level where those two
are equalised.

Every claim here is pinned by a test. The two companion suites are
`backend/tests/test_process_local_state_inventory.py` (what a second worker
sees) and `backend/tests/test_concurrency_cross_process_guards.py` (the
documented guards, verified).

---

## The one-paragraph version

Alpha guarantees a single Gateway worker. Run one. `GATEWAY_WORKERS=1` is the
default in every compose file and every launcher, and a startup gate refuses
several genuinely unsafe multi-worker configurations. With one worker, run
records, SSE streams, token budgets, approval requests, and rate limits are all
correct and none of them survive a restart in the form you would want. With N
workers the durable half — runs, events, checkpoints, leases, side-effect
ledger, network waits — is cross-process and is genuinely usable, while a
defined set of in-process half-measures silently becomes per-worker rather than
refusing. With no workers at all there is no Gateway, which is a deployment
mistake rather than a mode.

---

## What is guaranteed with one worker

**Durable, and correct:**

- Runs, their statuses, their token usage, their recovery lineage (`runs` table).
- The run-event feed: messages, traces, lifecycle, delivery receipts
  (`run_events`).
- Checkpoints and thread state.
- Run admission: one active run per thread, enforced by a partial unique index
  (`uq_runs_thread_active`).
- The side-effect ledger and parked network waits, when `database.backend` is
  not `memory`.
- Safe run recovery: an interrupted run resumes from a checkpoint, and one with
  an unverified external effect becomes `recovery_confirmation_required` rather
  than being replayed.

**Process-local, and honest about it — nothing here survives a restart:**

| State | Where | Documented? | API says so? |
| --- | --- | --- | --- |
| Live `RunRecord` (task, abort event, finalizing flag) | `runtime/runs/manager.py:270` | yes | yes — hydrated records carry `store_only` |
| SSE stream buffers, 60s late-subscriber window | `runtime/stream_bridge/memory.py:37` | yes | yes — `supports_cross_process = False` |
| `TokenMeter` cumulative ledger | `runtime/token_meter.py:169` | yes, loudly | n/a — no route exposes it |
| Per-run token budget counters | `agents/middlewares/token_budget_middleware.py:86` | yes | n/a — internal |
| In-flight run admission counter | `runtime/lane_scheduler.py:415` | yes — "per process" | n/a — internal |
| `WriterFence` lease table | `runtime/lane_scheduler.py:81` | yes | n/a — internal |
| Keyed lock tables | `runtime/keyed_lock.py:41` | yes | n/a — internal |
| Login lockout table | `app/gateway/routers/auth.py:183` | **yes, explicitly** | n/a |
| Policy approvals | `app/gateway/routers/policy.py:48` | **no** | **no** |
| Compiled accessor-graph cache | `app/gateway/services.py:1091` | yes | n/a |
| Process supervisor restart ledger | `runtime/supervisor/policy.py` | yes — "not yet wired" | n/a |

Two of these deserve naming because the API is genuinely misleading:

1. **Policy approvals.** `POST /api/policy/approvals` returns `201` and an
   approval id. `GET /api/policy/approvals` on a second worker returns an empty
   list. `POST .../decide` on a second worker returns `404`. There is no error,
   no warning, and no documentation. An operator who creates an approval on one
   Pod and approves it on another is told the approval does not exist. This is
   the clearest instance of a durable-looking API over process-local state in
   the tree.
2. **The login lockout** is the same shape but is *documented* in the source: an
   attacker gets `N × max_login_attempts` guesses under N workers. Documented
   and unbounded is a real risk; undocumented and unbounded is a bug.

---

## What is guaranteed with N workers

`GATEWAY_WORKERS > 1` is a supported configuration with a real startup gate at
`app/gateway/deps.py:79`. Passing the gate is what buys the durable half.

### The gate — what a multi-worker deployment must have

The gate runs once at startup, before any persistence engine is initialised, and
calls `SystemExit` rather than starting in a state it cannot defend:

| Requirement | Why | Gate message |
| --- | --- | --- |
| `database.backend: postgres` | SQLite write-locks cannot support concurrent multi-process access | `deps.py:132` |
| `run_events.backend: db` | memory and JSONL stores are process-local, so the delivery-receipt singleton cannot hold | `deps.py:135` |
| `run_ownership.heartbeat_enabled: true` | without leases every run has a NULL expiry, so a scaling worker reclaims its peer's live runs | `deps.py:142` |
| `scheduler.enabled: false`, or `scheduler.multi_instance: true` | each worker starts its own poller, so occurrences double-fire | `deps.py:126` |
| Browser tools disabled | Chromium/Playwright objects live in one worker's memory and uvicorn dispatch has no thread affinity | `deps.py:129` |

`scheduler.multi_instance` has its own prerequisites checked **even at
`GATEWAY_WORKERS=1`**, because Kubernetes runs one worker per Pod and the worker
count is not the only way to get two Gateways (`deps.py:116`).

`agent_storage.backend: file` with Postgres only warns, because the failure mode
is invisible rather than loud: custom agents created on one node's disk do not
exist on the others (`deps.py:178`).

### What you actually get

- One active run per thread, across processes. The partial unique index is the
  guard, not the in-memory check — two independent sessions with no shared
  Python state are refused by the database.
- Run leases. A peer can durably request cancellation, take over an expired
  lease, and mark a run `error` without the owner cooperating. The owner's lease
  renewal is the fencing signal; a worker that cannot confirm its lease stops
  writing terminal state.
- Cross-process run-event ordering and the delivery-receipt singleton, on
  Postgres. `pg_advisory_xact_lock` keyed by thread id serialises the
  check-then-write in `put_if_absent` and every ordinary `put`.
- Crash-loop-bounded recovery: a crashed worker's runs are reclaimed, and its
  in-flight side effects become enumerable `UNKNOWN` entries rather than silent
  gaps.

### What stays per-worker, silently

These are the honest cost of the gate passing. None of them refuses; each just
becomes per-worker:

- **In-flight run budget.** `max_concurrent_runs` applies *per worker*, so N
  workers allow N × that many concurrent runs against a connection pool sized
  for one. The class docstring says so; no route reports the multiplication.
- **Policy approvals.** As above — the worst of the set.
- **Login lockout.** N × the attempt budget.
- **SSE stream buffers.** Only if `stream_bridge.type: memory`, which the gate
  does **not** check. With Postgres + `run_events.backend: db` + heartbeat, a
  multi-worker deployment that leaves the default memory bridge passes the gate
  and then every cross-worker join returns `409` rather than streaming.
  `stream_bridge.type: redis` is required and nothing enforces it.
- **Token meter, budget counters, writer fence, keyed locks, accessor-graph
  cache, workflow durable-log handles, goal-contract store handles.** All
  per-worker, all internal, none safety-relevant.
- **Delta checkpoint snapshot cadence.** `checkpoint_delta.snapshot_frequency`
  is compiled into each graph's channel table and deliberately **not** stamped
  into checkpoint metadata, so two workers on different cadences will disagree
  and **nothing will detect it**. `freeze_checkpoint_snapshot_frequency` says
  "must match across every process"; the mode itself is detectable via the
  metadata marker, the cadence is not.

### A known defect, recorded rather than fixed

`persistence/side_effects/sql.py::reconcile` guards the **settled** transition
on `AND status = 'unknown'`, exactly as `persistence/side_effects/AGENTS.md`
states. The `UNDETERMINED` **reopen** path a few lines below is a
read-modify-write guarded only on `tool_call_id`. A reconciler that read
`UNKNOWN`, lost the race, and then reported "could not tell" therefore drives an
already-`RECONCILED` entry back to `UNKNOWN` and clears the winning verdict —
silently, with no exception.

The entry keeps needing human attention, so no false conclusion is recorded and
nothing is billed twice. What is lost is a settled reconciliation, quietly
undone, which is the one thing that module says cannot happen. It is pinned as a
strict `xfail` in
`backend/tests/test_concurrency_cross_process_guards.py` with the fix stated in
the marker: add `AND status IN ('unknown', 'reconciled')` to the reopen update
and raise `SideEffectTransitionLost` when `rowcount == 0`. The in-memory
reference has no such gap because it holds one lock across both the read and
the write.

---

## The `store_only` contract

The single most load-bearing invariant in this area, and the one that keeps the
per-worker inventory from being a correctness problem.

`RunManager.get()` returns a live `RunRecord` on the worker that created it and
a `store_only` hydrated record on any other. Every stream/join/wait route
checks `record.store_only and not bridge.supports_cross_process` and answers
`409` instead of subscribing. A request routed to a non-owning worker gets a
durable status, not a hung stream and not a fabricated one.

**This is the only reason `MemoryStreamBridge.subscribe` is safe to expose.**
A peer subscribing to a run it has never seen does not get an error or a
`StreamGap`: `_get_or_create_stream` *creates* an empty log, and the subscriber
then heartbeats forever, because no producer on this process will ever publish
an end. `StreamGap` is a retention signal, not an unknown-run signal. The
`store_only` check is the only barrier, and it is a barrier in the router, not in
the bridge.

Do not remove the flag, do not make a bridge default `supports_cross_process`,
and do not add a route that subscribes without the check.

---

## What would have to change for honest N-worker support

Smallest first. Nothing here is a redesign.

1. **Require `stream_bridge.type: redis` under `GATEWAY_WORKERS > 1`.** One
   condition in the existing gate, next to the `run_events` check. Today the
   most-supported multi-worker configuration silently degrades streaming to
   `409`. This is the highest value-per-line item on the list.
2. **Report the per-worker multiplication on the in-flight budget.** One field
   on the ops endpoint, or one line in the `Retry-After` detail. The budget is
   documented as per-process; an operator reading a 429 cannot tell.
3. **Give policy approvals a durable store, or make the routes refuse.** An
   explicit `501` is strictly better than a `201` that a second worker cannot
   honour. This is the one place the API actively lies.
4. **Stamp the delta snapshot cadence into checkpoint metadata**, or refuse a
   mismatch the way the mode is refused. Today it is undetectable by
   construction, and the docstring's "must match across every process" is a
   convention with no enforcement.
5. **Share the login lockout** if any deployment runs N workers on a network
   where brute force is in scope. The weakness is documented; this is a
   judgement call, not a defect.
6. **Everything else in the "stays per-worker" list is fine as it is.** The
   token meter, budget counters, writer fence, keyed locks, and graph caches
   are performance or convenience state with no cross-process correctness
   requirement. Making them shared would add contention and failure modes to
   buy nothing.

---

## What this document deliberately does not claim

- That there is cross-process exactly-once. There is not, anywhere.
- That installation-scoped SQLite is a cross-process guarantee. It is not; the
  startup gate refuses SQLite under `GATEWAY_WORKERS > 1` precisely because of
  this, and SQLite's `FOR UPDATE` is emitted and dropped by the dialect.
- That a completed run is verified. `COMPLETED` means the run finished.
- That the `db` run-event store is cross-process on SQLite. The advisory lock
  is PostgreSQL-only; the per-thread `asyncio.Lock` is not shared between
  processes, and SQLite drops `FOR UPDATE` — the gate refuses SQLite under
  `GATEWAY_WORKERS > 1` for exactly that reason.
- That this page replaces the module guides. Where they disagree,
  `runtime/AGENTS.md` and `app/gateway/AGENTS.md` own the detail; see the
  known-drift note below.

## Known documentation drift found while writing this

Two honesty statements in the tree are wrong, and both err toward
*under*-claiming rather than over-claiming, so nothing here is at risk of being
read as a stronger guarantee than the code makes.

1. **`runtime/AGENTS.md:58`** — "the side-effect ledger has no SQL repository
   yet". It has one: `persistence/side_effects/sql.py`, with migration `0027`,
   conditional transitions, and its own `AGENTS.md` claiming the semantics hold
   across processes. The same runtime guide says so correctly fifteen lines
   earlier (line 41). `docs/architecture/durable-runtime.md` is also current.
   The line is stale.
2. **`runtime/AGENTS.md:59-60`** — "a session's parked-across-restart state is
   re-derived rather than kept in a dedicated durable registry".
   `persistence/network_waits/` is that dedicated durable registry, with
   migration `0026`, and the same guide's line 30 calls `wait_registry.py` "the
   durable record of parked sessions". Also stale.

Both are left in place: they are documentation defects, not code defects, and
`runtime/AGENTS.md` is owned by another agent.

Separately, **`docs/MULTI_WORKER.md` needs a `FILE_OVERRIDES` entry in
`scripts/generate_docs_index.py` and a regenerated `docs/INDEX.md`.** The
generator fails closed on any unclassified Markdown under `docs/`, and it
currently reports two unclassified files: this one, and the pre-existing
`docs/FORMAT_DEBT.md` — which means the index generator is *already* failing on
`main`, independently of this work. Both the generator and `docs/INDEX.md` are
outside this change's ownership, so neither was edited.
