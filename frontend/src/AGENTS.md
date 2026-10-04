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
- `content/` — the documentation site content (English only).
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

## Roster activity projection

`lib/bots.ts` opts into `GET /api/bots?activity=true`, which adds per-bot
`unread_count`, `last_message_preview`, `last_message_at` (epoch **seconds**,
normalized by `lib/time.ts`), `last_message_sender` and `last_message_withheld`.

- **The opt-in is the caller's job.** `fetchBots()` sends no `activity` param,
  so the default roster read stays a cheap registry read. `ChatView` passes
  `{ activity: true }` on mount *and* on `refreshBots` — dropping it on refresh
  would make a read look like it had cleared the badges.
- **Absent is not zero.** A row fetched without the projection has
  `unread_count === null`, and `BotProfileCard` renders no message line at all.
  Rendering "0 unread" would claim the server measured zero rather than that
  nobody asked, so `activityRequested` gates the whole block.
- **A withheld body is announced, never paraphrased.**
  `last_message_withheld: true` pairs with a `null` preview because the gateway
  found credential-shaped content and chose not to project it; the card says
  so in words. Do not fill in a placeholder body or hide the flag.
- **"Working now" is a display reading, not liveness.** `isRecent(bot.last_active,
  PRESENCE_WINDOW_SECONDS)` is the only presence signal in the UI;
  `alpha.bots.health` owns healthy/stale/stalled/dead. The window lives in
  `lib/time.ts` so the per-card dot and the gallery strip cannot drift apart,
  and the gallery strip hides itself entirely when the window is empty.

Coverage: `lib/bots-activity-client.test.mjs`.

## Internet connectivity in the workspace strip

`WorkspaceVitals` renders a fourth measurement in its "Backend connection"
cluster, from `src/lib/network.ts` → `GET /api/ops/network`. It is the runtime's
own link reading, **not** the `internet` block on `GET /api/system/vitals`: that
one is a single TCP connect sampled by the host monitor, while this one is the
four-state durable-runtime reading with hysteresis, per-endpoint round-trips, and
the automatic re-probe schedule. Rendering either in place of the other would
show a healthy link while the runtime had parked work.

Four states are four renderings, and three *non*-states are three more:

| Server says | Strip shows | Retry offered |
|---|---|---|
| `online` | the measured round-trip, e.g. `18.4 ms internet` | only if the reading is stale |
| `degraded` | `internet partial`, amber, unreachable endpoints named | yes |
| `offline` | `offline internet`, red, the backend's own retry sentence | yes |
| `unknown` | `unknown internet`, amber — **never** `offline` | yes |
| `reported: false` | `internet not measured, not reported`, grey, the server's `reason` | **no** |
| `state: null` while `reported: true` | `internet state not reported`, grey | yes |
| read failed | `internet not reported`, grey | yes |

Rules that must keep:

- **`latency_ms: null` renders a dash with words beside it, never `0`.** Zero is
  the fastest possible link; `null` means nothing answered. `toConnectivity`
  keeps `null` as `null`, and `connectivityView` pairs every dash with a label
  saying which absence it is (`latency not reported`).
- **A reading older than three poll intervals is disclosed as stale**, even when
  the state is green. This is reachable precisely because the backend backs off
  to a five-minute ceiling while the link is down while the strip refreshes every
  ten seconds, and a stale `online` presented as current is the failure this
  disclosure exists to stop. A stale reading is the one *healthy* case that earns
  a Retry.
- **`Retry` appears only when a recheck could change the answer.** A link that is
  *unmeasured because the process has no probe at all* renders as off with the
  reason in its tooltip — a button that could only come back with the same
  refusal implies a pending state that does not exist.
- **The control is disabled while in flight** and says `Retrying…`, because a
  second click would be a second probe of the same link.
- **A retry never paints its own answer.** It calls
  `POST /api/ops/network/recheck` and then re-reads; the strip always renders
  what the server last confirmed.
- **A recheck the backend declined to publish is not a failure.** The server
  answers `performed: false` with a reason, and a `changed: false` result carries
  how many confirming observations are still outstanding.
- **An unfamiliar state string renders verbatim** rather than snapping to one of
  the four this build knows.
- **A process that is measuring but named no state is a third claim**, distinct
  from both "unrecognised word" and "not measuring": `state` is nullable, so a
  null reaches the final branch. It renders as a grey dash with words beside it,
  never as a state literally called `null`, and it does not borrow the amber of
  an unrecognised *word* — there is no word to recognise.

Coverage: `src/lib/network.test.mjs` (routes, verbs, envelope mapping, every
honesty inversion), and the new entries are also subject to
`src/lib/ui-legibility.test.mjs`'s dash-with-disclosure rule.

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
- **Read independent subsystems independently.** When one surface aggregates
  several routes, each is its own section that can fail alone — never a single
  promise that all-or-nothing blanks the ones that answered. Name the failures
  up front (`project-inspector.ts` → `inspectionSections` is the reference) so a
  partial read cannot present itself as a complete one.

## Projects tab: list plus full per-project read

`projects` is a **primary** tab between Bots and Board, declared once in
`WORKSPACE_TABS` (the primary row and the More Views dropdown are both derived
from it, so a second entry renders twice and `workspace-nav.test.mjs` fails).
Its badge counts `projects.length` — each tab shows its own measured count, and
inheriting the bot roster would label an installation with 2 projects "42".

- **The list shows every project by default.** Both filters (bot, crew type)
  default to `all`.
- **Single-agent vs team crew comes from the roster the server reported.** The
  crew service provisions a shared group room at the *second* member, so
  `members.length >= 2` is a crew. A roster read in flight or failed is
  `unknown` — its own badge and its own count, never folded into "single agent",
  which would dress a broken read as a deliberate choice. `SHAPE_META` in
  `ProjectsSection.tsx` is the single place that mapping lives.
- **"View more" is a drill-down, not another accordion row.** It replaces the
  surface with `ProjectInspectorSection`, which re-reads the project live
  (`src/lib/project-inspector.ts` → `inspectProject`, 17 independent routes)
  rather than assembling a view from what the list happened to have fetched.
- **The inspector is strictly read-only**: it imports `errMsg` from `lib/http`
  and never `send`, and the client imports only `get`. Mutating per-project
  actions already belong to the Workforce view, whose picker defaults to the
  *first* project — so "Live controls" must pass the project id
  (`onOpenLiveProject`), not merely switch views.
- **A conversation in the drill-down opens in the chat view**, so the inspection
  is a step in a task rather than a dead end.
- Coverage: `src/lib/project-inspector.test.mjs` (routes, read-only guarantee,
  partial reads, honesty inversions) and `src/lib/project-inspector-view.test.mjs`
  (tab placement/ordering, crew classification, wiring, rendered claims).

## Alpha Network client boundary

The `peers` workspace view is a separate session from local bot/group messages.
`PeerNetworkSection` must render the Gateway's real provider status, pairing
state, topology, and per-recipient receipts; it must not turn a failed read into
an empty network or mark a discovered peer as paired. The client contract is
`src/lib/peer-network.ts`, with exact-route/honesty tests in
`src/lib/peer-network.test.mjs`. Pairing is an explicit state-changing action,
so the UI keeps the code reveal/copy and rotation controls deliberate.
