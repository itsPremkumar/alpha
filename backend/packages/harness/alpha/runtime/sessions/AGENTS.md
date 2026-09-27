### Durable session lifecycle (`runtime/sessions/`)

`runtime/sessions/` is the **vocabulary** Alpha was missing for the durable-runtime
contract, and nothing more. It owns no state, writes no row, and admits no work.

**The gap it closes.** Alpha's run already has a real, durable, DB-enforced
lifecycle (`RunStatus` in `runtime/runs/schemas.py`, with the
`uq_runs_thread_active` partial unique index behind thread admission), and a
thread has a durable `threads_meta` row. What neither had was a lifecycle that
can say *"this task is alive and parked because the machine has no internet."*
`threads_meta.status` is a free-text `String(20)` that only ever says
`idle`/`running`/`error`, so a task waiting for connectivity is
indistinguishable from a task that failed — which is the exact failure the
contract forbids.

**It is a derivation, not a second owner.** `derive_session_state()` folds
signals that existing owners *already* hold — a `RunStatus`, the network
monitor, a pending permission, an in-flight compaction, a recovery disposition
— into one named state. `RunManager` remains the sole lifecycle owner, and
`runtime/runs/verification.py` remains the only source of an acceptance verdict.
`COMPLETED` here still means "the run finished", never "the work was verified".

**The three classes are the operating decision.** A host asks a class, not one
of seventeen names:

| Class | States | Contract |
|---|---|---|
| `ACTIVE` | `created`, `queued`, `planning`, `running`, `compacting`, `recovering`, `retrying` | Execution is happening or imminent. Holds worker capacity and the thread's active-operation reservation. |
| `WAITING` | `waiting_network`, `waiting_provider`, `waiting_permission`, `waiting_resource`, `waiting_user`, `paused` | Alive and durable, parked on an external condition. Releases worker capacity, stays resumable without user action, and is **never** reported as a failure. |
| `TERMINAL` | `blocked`, `completed`, `failed`, `cancelled` | No further automatic progress. |

`is_resumable` is the property the recovery manager asks: true for `ACTIVE` and
`WAITING`, false for `TERMINAL`. `paused` is classified `WAITING`, not
`TERMINAL`, because a user pause is a park and not a death.

**Precedence is the contract.** `derive_session_state()` resolves in a fixed
order, and `reasons[0]` is *the* reason a session is in its state, so a renderer
reads the reason from the report rather than re-deriving it. Two consequences
are load-bearing and are pinned by tests:

- **A terminal run status outranks every live condition.** A network flap
  during teardown must not resurrect finished work as `waiting_network`.
- **A park outranks an in-flight phase.** A session that cannot reach its
  provider is *not* `recovering`; reporting it as recovering is a lie about
  progress.
- **Network outranks provider.** Without a link, provider health is unprovable,
  so `network_unavailable` wins when both are set.

**No `UNKNOWN` state, on purpose.** Every input is a server-owned durable fact,
so "we could not tell" is a programming error rather than a runtime condition.
`SessionStateSignal` is a bag of booleans plus an optional `run_status`; absence
means "no such condition is active", never "unknown".

**Transitions are validated, not clamped.** `SESSION_STATE_TRANSITIONS` lists
only genuine state changes; self-transition is legal for every non-terminal
state (a heartbeat re-reporting `running` must not raise) and is applied by
`allowed_transitions()` rather than spelled out in every row. Terminal states
are final **even against themselves**. `validate_transition()` raises
`IllegalSessionTransition` rather than silently returning the current state,
because a silently-ignored transition is how a recovery pass ends up
re-admitting a cancelled task. A `waiting_*` state can never jump straight to
`completed` — only a run that actually finished may claim that.

**Where things live**:
- `runtime/sessions/states.py` — `SessionState`, `SessionStateClass`,
  `SessionStateSignal`, `SessionStateReport`, the classification maps, the
  transition table, `derive_session_state()`, `validate_transition()`
- Tests: `tests/test_durable_session_state_machine.py` (classification
  partition, the waiting-is-not-failed guarantee, precedence, terminal
  finality, report serialization)
