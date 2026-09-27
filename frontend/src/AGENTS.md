# Frontend source agent guide (`frontend/src/`)

Scope: this file covers `frontend/src/`. It is stricter than `frontend/AGENTS.md`
where the two overlap.

## Layout

- `app/` — App Router routes, layouts, and the page shell.
- `components/` — UI. `ChatView.tsx` is the workspace router: each workspace view
  id maps to a lazy-loaded section component.
- `components/sections/` — one component per surface (Workflows, Supervisor,
  Protocols, Forge, …). A new backend surface gets a section here.
- `components/ui.tsx` — shared primitives (`Section`, `Btn`, `Badge`, `Field`,
  `Notice`, `ErrorBox`, `EmptyState`, `SkeletonList`, `inputCls`).
- `lib/` — API clients, one module per backend plane, plus the shared
  `api-client.ts` / `http.ts` transport.
- `content/` — the documentation site content (en/zh).
- `types/` — shared TypeScript types.

## Lion companion boundary

The local lion companion is isolated under `components/lion-pet/`. Keep the SVG
rig, action registry, settings normalization, lifecycle adapter, bounded local
travel, and feature CSS inside that folder. `ChatView.tsx` may pass only
bounded lifecycle state and an optional chat action; it must not receive prompt
content or thread identifiers.
The compatibility exports at `components/LionPet.tsx` and `lib/lion-pet.ts` are
facades only. Regression coverage lives in `src/lib/lion-pet.test.mjs`.

## Adding a backend surface to the UI (the required path)

1. Add `lib/<surface>.ts`: a typed client over the real routes. Map envelopes
   verbatim, keep `owner_id`-style scoping explicit, clamp/validate query bounds
   to what the server accepts, and reject on error with the server's reason.
2. Add `lib/<surface>.test.mjs` pinning the exact paths/verbs, the envelope
   mapping, and the honesty inversions for that plane.
3. Add `components/sections/<Surface>Section.tsx` using the shared primitives.
4. Wire the view: add the id to the workspace-view union, add a tab entry, and
   add the lazy import plus render case in `ChatView.tsx`. **A section that is
   not reachable from the view registry is dead code** — the id must exist in
   all three places or nowhere.
5. Gate: `tsc --noEmit` = 0 errors and `node --test src/lib/*.test.mjs` green.

### Data Flow

```
browser
  -> Next.js app router (app/) serves the shell
  -> components/ChatView.tsx resolves the active workspace view id
  -> components/sections/<Surface>Section.tsx renders that surface
  -> lib/<surface>.ts calls the gateway through lib/http.ts (get/send)
  -> /api/* on the Gateway (app/gateway/routers/*)
  -> harness subsystems (agents, tools, sandbox, memory, scheduler)
  -> storage (LangGraph checkpoints/store, thread/run metadata, files)
```

The return path matters as much as the outbound one:

- **Mutations** go through the client, then the UI re-reads real state. Never
  paint an optimistic result as if the server had confirmed it.
- **Streaming** (`lib/sse-reducer.ts`, `lib/threads-ext.ts`) consumes the
  gateway's SSE frames: `values`, `messages-tuple` chunks, and `task_*` custom
  events for subagent progress. Reducers must tolerate out-of-order/partial
  frames and must not invent a terminal state for a stream that ended early.
- **Bounded payloads are a contract.** The gateway batches `write_file` /
  `str_replace` argument deltas; the frontend must render the batched frames it
  receives rather than assuming one chunk per token.
- **Authorisation state is server-owned.** The UI reflects what the gateway
  reports (scopes, permissions, disabled capabilities); it never decides access
  locally.

When a frame or field is missing, render the honest unknown state. A partially
received stream is not a successful one.

## Reasoning-effort picker (composer)

`components/ReasoningEffortPicker.tsx` sets how hard the selected model
reasons. `lib/reasoning-effort.ts` is the client mirror of the server's
canonical ladder; `tests/reasoning-effort.test.mjs` owns the contract.

- **The ladder and labels come from the server.** `GET /api/models` returns
  `reasoning_effort_levels` / `reasoning_effort_labels` (via
  `fetchModelCatalog()`) so the picker orders and names rungs from one source.
  `FALLBACK_LADDER` / `FALLBACK_LABELS` are for a degraded or older-Gateway read
  only — never hardcode a second copy as the primary source.
- **No declared ladder, no control.** A model that declares no rungs renders a
  muted "Reasoning fixed" chip carrying the reason. Do **not** render a
  permanently disabled menu: a control that can never succeed implies a pending
  state that does not exist.
- **Only declared rungs are offered**, weakest first, plus a `Default` row that
  means "send nothing" and whose hint discloses the entry's own
  `default_reasoning_effort` when it has one. The `Default` row is a real
  choice, not the absence of one: collapsing it into a rung pins the user to a
  level the provider never chose.
- **Never show a rung the server will clamp.** `reconcileEffortForModel` runs on
  every model switch and on the restored `localStorage` value; a rung the new
  model cannot serve becomes `Default`. When a clamp is unavoidable it is
  disclosed in the open menu, not applied silently.
- **`default` is omitted from the request, never sent as a string.** The run
  boundary rejects a value that names no rung, so `ChatView` spreads
  `reasoning_effort` into `config.configurable` only when it is a real rung.
- The stored key is `alpha_reasoning_effort`, and a value that no longer applies
  is removed rather than left to be re-read on every load.

`sendMessage` closes over `reasoningEffort` and `DEFAULT_EFFORT`, so
`src/lib/chat-request-error.test.mjs` injects both. A missing binding there
throws inside the request and masks every status-code assertion behind a generic
"request could not be completed" — that is the failure mode to watch for when
adding a run option.

## Live activity layer (what shows between prompt and answer)

Four pieces, all inline in the transcript and quiet by default:

| Piece | File | Role |
| --- | --- | --- |
| Phase + elapsed + tool counters + stream silence | `lib/activity.ts` | Pure derivation from what the stream reported |
| Pinned status line | `components/ActivityStatus.tsx` | Spinner, phase, tool count, elapsed clock, silence notice |
| Collapsed tool receipt | `components/ToolGroup.tsx` | `Used 4 tools · shell, exa` — auto-opens while a call is in flight |
| Subagent rows | `components/SubagentList.tsx` | One row per delegated `task_*` |

The rules this layer must keep:

- **A phase describes the newest reported state, never a prediction.**
  `deriveActivity()` reads only `content` / `thinking` / `toolCalls`. A call
  whose result has not arrived is *running* — not failed, not completed, and
  never counted as success.
- **`stream_mode` must include `custom`.** Subagent progress rides
  root-namespace `task_*` custom events
  (`alpha/tools/builtins/task_tool.py`); requesting only `messages-tuple` and
  `values` delivers no delegation signal at all.
- **The first terminal `task_*` wins.** `withTaskEvent()` refuses to reopen or
  downgrade a settled task, ignores an unrecognized `task_*` type instead of
  guessing an outcome nobody reported, and never rewinds step numbers on an
  out-of-order frame. It keeps `state.tasks` referentially stable so frames
  carrying no subagent news do not re-render.
- **No number is invented.** Durations are client-observed (`ToolTiming`), so a
  call or task restored from history renders no duration rather than a `0s`; an
  absent usage block stays absent rather than reading as zero tokens; a step
  counter with no reported total renders `step 3`, never `0/0`.
- **A streaming turn never looks finished.** `MessageItem` keeps a trailing
  indicator up while `streaming`, because text already on screen followed by
  silence reads as a hang.
- **Silence is measured on bytes, not frames.** `consumeChatStream`'s
  `onActivity` fires for every byte the reader gets, heartbeat comment lines
  included; `onUpdate` fires only on parseable frames, which stop during
  exactly the quiet period the notice exists to catch. `silenceNotice()` says
  only `No update received for 32s` — never "stalled", "stuck" or
  "disconnected", because a reconfigured
  `stream_bridge.heartbeat_interval_seconds` (default 15s, two missed beats
  trigger it), a wedged proxy, and a genuinely hung run are indistinguishable
  from one client-side reading. `lastByteAtRef` is re-seeded at the start of
  every run so a clock left dead by the last turn cannot announce a stall in
  this one.
- **Receipts are turn- and navigation-scoped.** `subagentTasks` clears in
  `stopVoiceForNavigation()` and at the start of each run — deliberately *not*
  when one ends, so the answer lands beside the receipt of the work behind it.

`ToolPill`'s `inFlight` prop is what makes its defined-but-unreachable
`running` state reachable: the stream sets no status until a result exists, so
without it an in-flight call renders "no result reported", which wrongly
implies the run finished without reporting one. Historical messages pass no
`inFlight` and keep `not-reported` exactly as before.

Coverage: `lib/activity.test.mjs`, `lib/subagent-events.test.mjs`, and the
pre-existing `lib/tool-status-honesty.test.mjs`.

## Honesty patterns to copy

- A control that is off by default renders as off, with the reason it is off.
- A run/report panel shows the server's real counters and timestamps; a
  measurement that could not be taken is shown as unknown, never as zero.
- Destructive or state-changing actions require an explicit opt-in (a checkbox
  that defaults to unchecked) and state the safety model in the label.
- In-process or single-worker state is labelled as such in the UI.
- Never render an optimistic success for an action the server has not confirmed.

## Client honesty rules

- Map absent optional values to `null`; do not coerce `undefined` to `0`/`""`.
- Preserve server enum strings verbatim; do not invent a value the server did
  not send.
- Do not catch-and-empty a failed request. Surface the reason; an empty list must
  mean "the server said there is nothing", not "the call failed".
- When a request is retried or triggered repeatedly, disable the control while
  in flight so a double-click cannot create two records.

## Alpha Network client boundary

The `peers` workspace view is a separate session from local bot/group messages.
`PeerNetworkSection` must render the Gateway's real provider status, pairing
state, topology, and per-recipient receipts; it must not turn a failed read into
an empty network or mark a discovered peer as paired. The client contract is
`src/lib/peer-network.ts`, with exact-route/honesty tests in
`src/lib/peer-network.test.mjs`. Pairing is an explicit state-changing action,
so the UI keeps the code reveal/copy and rotation controls deliberate.
