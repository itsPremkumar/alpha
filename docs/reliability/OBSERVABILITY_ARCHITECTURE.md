# Alpha — Observability Architecture

Alpha's observability is **not** one system. It is four deliberately separate
layers, each owning a question the others cannot answer. This document states
what each owns, why they are separate, and where the seams are.

---

## 1. The four layers

| Layer | Package | Answers | Backing store |
|---|---|---|---|
| **Correlation spine** | `alpha/observability/` | *what is this run, and what did it do in order?* | trace files (JSONL), injected sinks |
| **Stable error codes** | `alpha/errors/` | *what class of failure is this, and what should happen about it?* | in-process registry |
| **Durable run journal** | `alpha/runtime/` | *what is the exact state, and can it be resumed?* | `runs`, `run_events`, checkpoints |
| **Self-inventory** | `alpha/workflow/registry/`, `alpha/intelligence/` | *what can this build do, and is it wired?* | generated `contracts/feature_manifest.json` |

They are separate because they fail separately and are consumed separately. A
trace without a stable error code cannot be aggregated; an error code without a
trace cannot be diagnosed; a journal without either cannot be replayed.

---

## 2. Correlation spine (`alpha/observability/`)

### 2.1 The identity chain

```text
X-Trace-Id / alpha_trace_id
  ↓  ContextVar (alpha/trace_context.py) — the ONLY source
trace_id
  ↓
run_id → thread_id → message_id → tool_call_id → step_id
```

Rules that make it trustworthy:

- Every path that reaches a run binds one **first**; downstream treats it as a
  plain `str` with no `if trace_id:` guards.
- A caller-sent id is **replaced**, so a persisted run can never disagree with
  its header and logs.
- `request_trace_context` (HTTP) deliberately does **not** inherit.
- `logging.enhance.enabled` gates log output only — never the id, header or run
  metadata.

### 2.2 The behaviour-trace envelope

`observability/contract.py::TraceEnvelope` is the single record shape every
instrumentation layer emits. It is a frozen, slotted **stdlib dataclass**, not a
pydantic model — a deliberate cost decision, because the module is on the import
path of anything that traces and the cold-start budget gate has a 2500 ms
tolerance.

Three properties make it worth having:

1. **Fidelity is disclosed, never silent.** A payload over the byte cap is
   clamped, and the envelope then carries `truncated`, `truncated_count`,
   `payload_bytes` and `payload_sha256`. A reader can always tell that something
   was dropped, how much, and verify a payload it independently obtained.
2. **The hash is taken over the redacted payload, not the raw one.** Hashing the
   raw payload would make `payload_sha256` a content oracle.
3. **Both clocks, always.** `ts_monotonic` orders events inside one process (the
   wall clock can step backwards under NTP); `ts_wall` correlates with log files,
   database rows and other machines.

### 2.3 Default-off, and inert when off

`ObservabilityConfig.enabled` defaults `False`. When false:

- `TraceRecorder.record` returns `False` at its first statement and touches no
  sink — the test asserts `sink.calls == 0`, not merely that no record appeared;
- `TraceRecorder.span` yields a span holding no clock read and no id;
- the file sink is never constructed, so a disabled recorder creates no file and
  no parent directory.

There is no partially-on state: a disabled recorder is **inert, not quiet**.

### 2.4 Sampling is per run, not per event

`sample_rate` decides **per run** because a half-sampled run is worse than no
run — you cannot correlate a turn you have but not the tool call that preceded
it. The decision is a hash of the `run_id`, so it is deterministic and
reproducible from the trace file alone, and it is disclosed through the
recorder's `sampled_out_runs` counter.

### 2.5 Loss is disclosed, never silent

Every cap, drop, sink failure and sampled-out run is counted and surfaced in
`TraceRecorder.disclosure`. A cap is something an operator can see rather than
something that silently happens.

### 2.6 No remote sink, by declaration

`remote_sink_enabled` exists so the *absence* of a network export is a declared,
testable decision. There is no remote sink implementation in the package at all:
the shipped default is `false` and the key exists to be asserted. Turning it on
without writing a sink is a configuration error, refused at recorder
construction — not a silent no-op.

---

## 3. Stable error codes (`alpha/errors/`)

`errors/registry.py` is the **only** place a stable error code is defined.
Before it, four disjoint taxonomies described the same failures four different
ways; nothing forced them to agree, so a failure could be raised with one code,
logged with another, and rendered to the user as bare prose.

The contract:

- **One code, one meaning.** A code is added here, not invented at a raise site.
- **The user-facing message lives with the code**, not at the call site, so the
  same failure reads the same way in SSE, in a channel reply and in a support
  bundle. Messages never interpolate exception text.
- **Correlation is a property of the code.** Every occurrence reports under the
  same `correlation_id`. Per-occurrence identity rides the existing
  `alpha.trace_context` id and is carried separately in `ReportedError.context`
  — deliberately *not* a code attribute, because a per-occurrence id is not
  stable and stability is the point.
- **Classification is total.** Any exception maps to exactly one code: by
  `CodedError` first, then by exception type, then by provider status, then
  `INTERNAL_ERROR`. A run can never end with an uncoded failure.
- **Adding a code is a contract change.** Codes are append-only: never repurpose
  or renumber an existing code, and never widen a code's severity in place.

Metrics hygiene: labels are drawn only from `ErrorSeverity` and from the closed
code set. A run id, thread id, user id or trace id is never a label — label
cardinality is a hard cap.

---

## 4. Durable run journal (`alpha/runtime/`)

The journal is what makes "resume" a real property rather than a hope.

| Concern | Owner |
|---|---|
| Run lifecycle (sole authority) | `runtime/runs/manager.py` |
| Execution journal | `runtime/journal.py` |
| Checkpoints | `runtime/checkpointer/`, `runtime/checkpoint/` |
| Event store | `runtime/events/` (memory / JSONL / DB+FTS5) |
| Side effects | `runtime/side_effects/` |
| Sessions | `runtime/sessions/` |
| Connectivity | `runtime/network/` |
| Shutdown | `runtime/shutdown.py` |

Invariants that make it trustworthy:

- **A committed checkpoint is sufficient to identify the next safe resumable
  state.** Hydration refuses a stale projection; `/recover` is an explicit route,
  not a relaxation of `/hydrate`.
- **A task cannot be `COMPLETED` unless verification passed.** A terminal
  `success` is never presented as verified without evidence for every required
  kind.
- **A tool cannot be `SUCCESS` merely because it was launched.**
- **A subprocess cannot be `SUCCESS` merely because it was launched.**
- **A delivery receipt is derived from workspace snapshots**, not from a client
  request option. Missing or unverifiable `present_files` coverage **downgrades
  the run to error**.

---

## 5. Self-inventory (`alpha/workflow/registry/`)

Eleven read-only registries behind one `list` / `describe` / `health` protocol:
`identity`, `tools`, `skills`, `mcp`, `models`, `bots`, `commands`,
`capabilities`, `engines`, `wiring`, `memory`.

Three load-bearing invariants:

- **One source of truth per fact, never a re-derivation.** `engines` and `wiring`
  read the generated `contracts/feature_manifest.json` through the single
  `manifest_source` loader; `models` reads the live `AppConfig`. A registry that
  recomputed any of those counts itself would be a second, unreviewed answer to a
  question the build already settles.
- **A broken source must not read as an empty one.** `list()`/`describe()` raise
  `RegistryUnavailable`; the inventory converts that into `status="unavailable"`
  with `count: null` — **never `0`**. "I could not look" and "I looked and found
  nothing" lead to opposite decisions.
- **Availability is what the source declares; health is what we probed.** A
  *declared* model is `available` with `health="unverified"`. Nothing here
  executes what it lists, so `health` is `unverified` on every descriptor.

---

## 6. The seams (where observability is still thin)

Stated plainly:

1. **The error reporter's SSE leg is not bound.** No production
   `configure_error_reporter` caller exists. Run-scoped errors still reach
   clients via `gateway_terminal_error_payload`, but the four-legged fan-out's
   SSE leg never fires.
2. **The crash-loop supervisor is built but not wired** to `start.ps1` /
   `watchdog.ps1`.
3. **The behaviour-trace spine is default-off.** It is opt-in per deployment;
   the absence of a trace file is not currently distinguishable from a disabled
   recorder at the operator surface.
4. **No frontend client telemetry.** The browser has one error boundary and
   console writes; there is no `sendBeacon`/analytics path. Client-side
   failures are visible only where the code already writes to `console`.
