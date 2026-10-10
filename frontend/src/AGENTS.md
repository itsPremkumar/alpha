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
- `lib/chat-stream.ts` resumes a dropped SSE connection from its last event id
  with a bounded retry ladder. `onReconnect` is transport state, not run state;
  clear its UI indicator on resumed bytes, Stop, navigation, or stream exit.
  Retry transient network failures, but preserve HTTP/auth refusals and malformed
  response errors. Do not display a reconnect as evidence that a run completed.
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

## Free-model catalog dropdown (header)

`components/FreeCatalogMenu.tsx` is the header's keyless-model control: a status
trigger plus a dropdown of every provider `GET /api/models/free/catalog` knows,
its measured health, and the model IDs that provider actually serves.

**It exists because the sentence was unopenable.** `Free models: 8/10 healthy, 8
eligible.` asserted a count and its `title` repeated the same words — you could
not see *which* 8 were healthy, which 2 were not, what the 10 providers are
called, or what "eligible" excludes. The count was a claim with no evidence
attached; the list is the evidence.

Three ownership rules:

- **`lib/freeCatalogTone.ts` owns the tone, `lib/freeCatalogView.ts` owns the
  per-provider sentences.** Both are pure so `free-catalog-view.test.mjs` can
  drive the exact function that produces each string. Neither may be re-declared
  beside the markup: the trigger's dot is a summary of the rows behind it, and a
  second colour table would let them disagree. They are in `lib/` rather than
  `ChatView.tsx` because the menu is rendered *by* `ChatView`, so importing back
  out of it would make a cycle load-bearing.
- **Opening the list performs no network call.** The trigger used to re-probe
  every gateway on click — slow, and it mutated the very health state it was
  reporting. The probe moved inside the panel, where it disables itself in
  flight. The panel is portalled to `document.body` and placed by
  `lib/workspace-menu-geometry.ts`, because an in-place panel is clipped by the
  `overflow-x-auto` nav wrapper and `<main class="overflow-hidden">` (the failure
  `NavTabs.tsx` documents).
- **The client keeps the provider rows.** `ChatView` retains `freeProviders`
  across a failed read rather than clearing them, so a broken read cannot
  present itself as a catalog with no providers.

The honesty rules this surface is built around, each of which has a tempting
wrong reading:

| Server says | Panel shows |
| --- | --- |
| `healthy: true` | green dot, `healthy` |
| `healthy: false` | red dot, `failing`, plus the failure reason |
| `healthy: null` | muted dot, **`not probed`** — never green |
| `providers: []` | "the server reported no free providers" |
| the read rejected | "the catalog read failed, so no provider was measured", with the reason |
| `latency_ms: null` | `not reported` — never `0 ms`, never a bare dash |
| no `model_count` | `models not reported` — never the length of the list that arrived |
| `models_truncated: true` | `Showing 25 of 61` — the bound is disclosed, not hidden |
| no eligibility data | `eligibility not reported`, distinct from `not eligible` |

**Health is measured per provider, never per model.** The router probes a
gateway, not a model ID, so a listed ID under a green provider has *not* been
individually proven to answer. Model rows therefore render in one neutral chip
class and the panel says so once in its footer — tinting each row by its
parent's verdict is the specific claim the router never makes.

Coverage: `src/lib/free-catalog-view.test.mjs` (the pure sentences, plus the
panel's structural and disclosure pins) and `src/lib/freeModels.test.mjs` (the
envelope mapping, truncation, and the per-provider-not-per-model boundary).

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

## Working status and roster filters (Bots tab)

`lib/bot-working-status.ts` + `components/bots/WorkingStatusView.tsx` answer the
question the Bots tab could not: **is this agent working right now?** The
presence reading above is a roster timestamp; `alpha.bots.health` owns the real
verdict and publishes it on `GET /api/bots/health/overview` (and
`GET /api/bots/kill-switch` for the operator stop), which nothing rendered. The
two disagree exactly where it matters — a bot whose task lease expired is
*stalled* and holds work that will not finish, and a paused bot must never read
as working.

- **Two reads, two independent failures.** `fetchBotHealthOverview` and
  `fetchPauseState` are each their own `ReadResult`, issued separately in
  `BotGallery` and rendered separately: "nobody is paused" and "we could not
  check" lead to opposite actions, so a broken read must not blank a healthy
  verdict (or vice versa). A failed read rejects with the server's reason — it
  never resolves into an empty overview with `total: 0`, which would paint a
  fleet-wide no-heartbeat claim from a network error.
- **Precedence is the payload order.** An operator pause outranks the monitor
  (a stopped bot holding a fresh heartbeat would otherwise read as working),
  then the monitor's `liveness` string verbatim, then — only when no row
  arrived — the presence reading, which labels itself `presence only` in its own
  sentence. A verdict nobody produced is `working: null`, never `false`.
- **A live run outranks the heartbeat engine, and it is the only reading that
  names the work.** `GET /api/bots/working` (Gateway: `routers/bots.py`) joins
  the run store on `RunRow.assistant_id`, which `ChatView` sets to the roster
  bot's `name`, so a bot mid-run reads *"Running on thread "investigate flaky
  timeout" · run af79cfa3 · for 42s · on union-alpha"* instead of "no heartbeat
  recorded". Verified live against a real Gateway: a run for `tester` came back
  attributed with its run id, thread title and elapsed seconds. A pause still
  outranks it, but the card says *both* facts (`Paused · run finishing`) because
  an admitted run really is finishing. The route is a third read, not a field on
  `/health/overview`, because it needs a SQL backend: the Gateway 503s on
  `database.backend: memory`, and folding that in would cost a memory
  deployment its liveness strip.
- **A liveness word from a newer Gateway renders verbatim** in a muted badge
  (`WorkingStatusBadge` carries `data-working-key` for the suite). Snapping an
  unread verdict to `healthy` would lend this build's green badge to a state it
  cannot read. `seconds_since_heartbeat: null` (unparseable heartbeat) is the
  engine's own refusal of a `999999` sentinel, so the card says "heartbeat
  timestamp unreadable" instead of quoting a number.
- **Every absent counter is `null`, never `0`.** `normalizeHealthOverview`
  reads `summary` key by key, so a payload from a Gateway that has not gained a
  state renders the states it did report and `— not reported` for the rest.
- **A failed read is a state, not a number, on both sides.** The strip's
  "bots with a run in flight" cell renders `still reading` while the run-store
  read is in flight and `not reported` (with the server's reason in its
  tooltip) when it failed — never `0`, which would claim the store measured an
  idle fleet. `reported: false` survives the mapper precisely so a caller
  cannot read it as "no runs".
- **`needsAttention` is deliberately narrow** — stalled, or an operator stop. A
  resting fleet reads `dead` on the monitor, so escalating that would make the
  gallery's notice permanent and therefore ignored; the strip still *counts*
  dead, and a dead bot holding a task says so on its own card, which is the
  pair `check_stalled_tasks` keys on.
- **The presence dot and the working badge are allowed to disagree** — the card
  names the second `presentNow` to keep the display threshold uncontaminated.

### The filters live in a pure module

`lib/bot-roster-filters.ts` owns one predicate (`botMatches` / `filterBots`)
plus the sort, because a filter is a claim about what a list contains and
inlined it drifts between the predicate, the "N of M shown" line and each
chip's count with nothing failing.

- **Options are derived from the roster, never hardcoded.** The Gateway
  validates `status` against `active|sleeping|suspended|archived` and its
  registry also writes `disabled`, so the old dropdown offered
  `active/paused/disabled` — two words the fleet report never uses, and no way
  to reach the ones it does. `uniqueStatuses` / `uniqueModels` offer what
  actually arrived, and a bot with `status: null` or no model has its own
  `not reported` option rather than being dropped.
- **Null sorts last, never first and never as zero.** An unmeasured reputation
  is not a low reputation, and a bot with no `last_active` is not one last
  active in 1970 (`new Date("").getTime()` is 0). `server` is the default sort,
  so the roster's own order survives.
- **The working facet buckets are disjoint** (`working` / `stalled` /
  `not-working` / `unknown`), so a stalled bot is not also counted as "not
  working" and two options can never claim the same population.

Coverage: `lib/bot-working-status.test.mjs` (routes, the refusal to invent an
empty fleet, the whole verdict table including the unknown-word and
null-liveness cases, the presence fallback's self-labelling, pause precedence,
disjoint facets), `lib/bot-roster-filters.test.mjs` (search surface,
data-derived options, every facet, the null-sort rule, the disclosure line, and
a source pin that the gallery filters through the module) and
`lib/bot-working-status-view.test.mjs` (renders the real components: the
verbatim badge, the three strip states, and that a card with no verdict makes
no working claim).

## Per-bot model configuration panel

`lib/bot-model-config.ts` + `components/bots/BotModelConfigPanel.tsx`, mounted
on the bot detail page. Four routes only: `GET`/`PUT`/`DELETE
/bots/{name}/model-config` and `POST .../model-config/preview`.

- **A 422's `detail` is an object** (`{message, issues}`), which the shared
  `ApiClientError` deliberately does not parse — it carries string details. The
  client therefore captures the error body through its own `createApiClient`
  instance and rethrows `ModelConfigValidationError` carrying every issue.
  Collapsing that into "Request failed (HTTP 422)" while the server named each
  problem is the failure this exists to stop.
- **`known_models` absent maps to `null`, not `[]`.** "The Gateway did not
  report the list" and "zero models are declared" are different facts; the
  picker discloses the first rather than rendering a mysteriously empty menu.
  It reads *this* list rather than `GET /api/models`, because validation is
  `models[]`-only.
- **Preview never saves.** It posts to the read-only route and shows the plan
  that *would* resolve; Save is gated on `draftsEqual(draft, saved)` so an
  untouched panel cannot write, and the panel re-reads the server's response
  after a save instead of trusting the draft.
- **An empty section is omitted, not sent.** `draftToConfig` drops blank
  `counsel`/`mixture`/`sampling` blocks: the server treats absent as
  inherit-everything, so sending `{enabled:false}` would store a block that
  changes nothing while making the profile look configured.
- **`configToDraft` tolerates junk.** A corrupt stored block must still open in
  the panel — a panel that throws on mount is one the operator can never reach
  to fix the block that broke it. Unknown enum strings (a strategy from a newer
  Gateway) are preserved verbatim and offered back as an option.
- The read-only plan block renders each value beside its `*_source`, then the
  precedence ladder and the server's own limits; the editors are capped by
  those limits so the UI cannot offer what the server refuses.

Coverage: `lib/bot-model-config.test.mjs` (routes, verbs, CSRF, envelope
mapping, the 422 issue list, draft round-trips) and
`lib/bot-model-config-view.test.mjs` (mount, the four states, and the rendered
honesty claims).

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

## The in-chat network bubble (`waiting_network`, not `failed`)

`components/NetworkWaitBubbles.tsx` + `lib/network-wait.ts` →
`lib/network-wait-view.ts`, over `GET /api/threads/{id}/network-waits`. It is
mounted in the chat transcript (`ChatView.tsx`, beside `ActivityStatus`) and it is
the human half of the durable-runtime promise that **an internet outage must not
become a task failure**.

**It is not the workspace strip, and the two answer different questions.**
`lib/network.ts` answers *"is the machine's link up right now"* — a property of
the host, identical for every thread. This answers *"what happened to this
conversation"*: when the link died, how long the work waited, whether it is
waiting still. A thread that survived an outage an hour ago has a perfectly
healthy link **now** and a lost half-hour of work in its history, so the live
reading alone can never show it.

The honesty rules this surface is built around, each with a plausible wrong
reading:

| Server says | Bubble shows |
|---|---|
| an open wait | `Connectivity lost` · `HH:MM:SS → waiting` · a **client** counter · "I'll pick this up automatically … for as long as it takes" |
| `bounded: false` (the default) | the patience sentence above — it is a fact, not a mood |
| `bounded: true` | "This deployment gives up after a set number of attempts", plus a `bounded by this deployment` chip |
| `bounded: null` | neither promise — `null` is a third state and must not be read as either |
| `state: resumed` | `Back online` · `HH:MM:SS → HH:MM:SS` · **both** stamps · the server-measured duration |
| `state: gave_up` | `Waiting stopped`, red, naming the server's `last_error` |
| `reported: false` | the server's own `detail`, grey — **never** "no outages" |
| the read rejected | "could not be read … unknown rather than fine", grey, with a Retry |
| `waits: []` | nothing at all |
| `terminal_at: null` on a settled row | no duration, and never `0s` |

Rules that must keep:

- **A settled outage stays on screen.** The timeline is deliberately *not* filtered
  to open waits: an outage that ended an hour ago is part of this conversation's
  history, and a view that dropped it the moment it resolved would erase exactly
  the event the user came to read.
- **A settled row's duration is the server's, never this browser's.** It is
  computed from `first_waited_at` → `terminal_at`. The open wait's ticking counter
  is the opposite: explicitly *client-observed*, because the row has not ended so
  no duration exists, and a client-computed "5m 22s" for an interval the browser
  was not running through would be this machine's arithmetic presented as the
  runtime's.
- **`null` never becomes `0s`.** `formatDuration(null)` is `null` and the component
  renders `DURATION_UNMEASURED` beside it. "We could not measure it" and "it took
  no time" are opposite claims.
- **`wait_seconds` is `null` by construction while a wait is open**, so the live
  counter is derived from `first_waited_at` and the browser's own clock. The
  tooltip says so.
- **The mount is not gated on `isLoading`.** A run parked on a dead link is
  terminal from the run's point of view, so a loading-only mount would hide the
  bubble during exactly the wait the user most needs to see.
- **`local-*` threads are never polled.** They are browser-only archive rows with
  no server id, so no park could have been recorded and every tick would be a
  guaranteed 404.
- **Every sentence lives in `lib/network-wait-view.ts`.** The component renders
  `networkWaitTimeline(...)` and must not branch on a wait state of its own; a
  second copy of the wording is how a bubble ends up contradicting itself.
- **A Retry re-reads the timeline and paints nothing from the click**, mirroring
  `WorkspaceVitals`: `POST /ops/network/recheck` measures the *link*, then
  `load()` re-reads the timeline.
- **The poll does not stop when nothing is parked.** The obvious optimisation —
  stop once every park is settled, because "the timeline cannot change without a
  new run" — is wrong: the next run in this thread is exactly the thing that
  parks, so a stopped poll means the bubble only appears after a reload, which is
  not the live wait notice this exists to be. It is one cheap, bounded,
  owner-scoped read per mounted thread.

Coverage: `src/lib/network-wait.test.mjs` (routes, escaping, envelope mapping,
every bubble state, the duration/stamp formatters, and source pins on the mount
and the component's own claims).

## APEX control panel (`apex` workspace view)

`lib/apex.ts` + `components/sections/ApexSection.tsx`, over `GET /api/apex/*`.
The `apex` id must exist in **four** places — the `WorkspaceView` union in
`NavTabs.tsx`, the `WORKSPACE_TABS` row, `WORKSPACE_VIEW_IDS` in
`lib/workspace-view.ts`, and `ChatView.tsx`'s lazy import plus render case.
`workspace-nav.test.mjs` checks the first three against each other in both
directions; it exists because `run-inspector` and `reliability` each shipped
with a tab and a render case while `WORKSPACE_VIEW_IDS` still said the view did
not exist, which made `?view=<id>` fall back to `chat`.

- **Absent is not zero, and the mappers enforce it.** Every count in `apex.ts`
  goes through `optNum`, which returns `null` rather than `0`. The backend
  reports an unreadable session store as `available: false` with a reason and a
  `count: null`; a mapper that reached for `?? 0` would turn "we could not read
  the store" into "there are zero sessions", and the operator would read the
  second as a working system.
- **`live` and `declared` travel together on the invariant card.** Rendering
  "12 invariants" over 9 live sites is a fabricated count, so the badge is
  `live/declared` and a missing enforcement site renders `live: false` with the
  server's reason — never a green tick.
- **`policy_sites_missing` is the panel's most important row.** APEX delegates
  every policy verdict (`alpha.tools.governance`, `alpha.guardrails`,
  `alpha.bots.authority_ceiling`, `alpha.runtime.control`), so a delegated kernel
  that is absent is an *unenforced boundary* and has to be visible rather than
  inferred from the panel rendering at all.
- **The emergency stop is a fact, not a control.** The card renders `always on`
  in words and offers no toggle, because `ApexControls` raises on
  `emergency_stop=False` — there is no setting to send.
- **The enable picker offers only profiles an enable may carry.**
  `ENABLE_PROFILES` (`assist`/`autonomous`/`apex_max`) is the single answer to
  "what may the operator turn it on to", consumed by both the `<option>` list
  and `profileToAdopt`, the helper that decides which server profile to adopt.
  `off` is a state, not a rung — `POST /apex/enable` refuses it by name — and a
  scope nobody has enabled yet reads as exactly `off`, so adopting raw left the
  controlled select displaying `assist` (no option matched) while the state
  behind it held `off` and the first-ever "Turn on" posted `{"profile":"off"}`
  and failed 422. `profileToAdopt` returns `null` for `off` *and* for a profile
  from a newer build: snapping `god_mode` to a known rung would enable a
  different authority than the record names.
- **One switch change re-reads the whole panel, not just the switch.** The badge
  draws from `/mode` and "Active profile" draws from `/status`; mutating only
  the first put "off (no mission control)" directly beneath an ON badge. So
  `ApexToggle` takes an `onChanged` callback fired *after* its confirmed re-read
  — never on the click — and the section passes `refresh`, so the two reads stay
  one fact. A refused write fires nothing, leaving the panel as it was.
- **"Run one cycle" is labelled for what it does.** It records a decision and
  re-reads the status. It does not run a tool, start a run, or complete a
  mission, and the panel says so in its own `Notice` rather than leaving it to a
  doc — that button is the one most likely to be read as "do the work".
- **The session-control card acts, then re-reads — never on click alone.**
  `SessionControlCard` posts `pause`/`resume`/`stop` (and approval verdicts),
  then re-reads the mode — which carries `active_session` — and the approvals
  list before painting anything; a refused action leaves the card exactly where
  it was with the server's reason in the error line. The verbs carry **no
  client-side transition rules** ("resume disabled because paused"): the
  server's rules are the single authority, so a click that changes nothing
  reports `applied: false` with the server's own reason instead of a silently
  dead button, and the approval gate's 409 lands verbatim.
- **The card's two reads fail independently.** Mode and approvals go through
  `Promise.allSettled`: a broken approvals store must not blank a healthy
  session view (or vice versa), because "no verdicts are waiting" and "the
  verdicts could not be read" lead to opposite actions. Each failure renders as
  its own `Notice`, and the session badge says `state unknown` rather than
  inventing an absence.
- **A bounded approval list says how many rows it is missing.** `GET
  /apex/approvals` returns at most 200 rows while `count`/`pending` describe the
  whole backlog, so `returned` and `truncated` travel with the envelope.
  `fetchApexApprovals` maps both, *derives* `truncated` when a Gateway bounds
  the list without declaring it (`returned < count`), and the panel renders a
  `Notice` naming both numbers — otherwise a 200-row list under a `count` of 500
  would look internally inconsistent, or a panel deriving the total from
  `approvals.length` would understate the gate by exactly the rows it hid.
- **The verdicts' asymmetry comes from the response, not the button.** The
  decision's `resumed` flag is the server's claim: `reject` renders "the
  session stays parked", and an `approve` that did not resume says so rather
  than implying a release the server never confirmed.
- **Drift is surfaced, not smoothed.** A session whose `contract_digest`
  differs from the active contract renders a `Policy drift` warning naming both,
  because a mission silently continuing under changed policy is the failure the
  digest exists to catch.

Coverage: `src/lib/apex.test.mjs` (routes, verbs, the null-preserving counters,
the control/approval/goal envelope mappings, the approvals truncation mapping,
and the honesty inversions for each block).

## APEX mode chip (composer)

`components/ApexModePicker.tsx` is the same switch as the panel, sitting beside
the reasoning picker in `Composer` — the one composer `ChatView` mounts, so bots,
groups and DMs all get it. Its derivation lives in `lib/apex.ts`
(`apexChipView`, `liveRung`, `APEX_RUNGS`, `APEX_COMPOSER_DISCLOSURE`) so the
suite can drive the function that produces each claim instead of scraping prose
out of JSX.

- **One scope, one fact.** It reads the default scope with no `scope_key`, so the
  chip and the panel can never show two different "APEX is on" answers. A
  per-thread switch would be a second claim beside the panel's, and the operator
  would have to work out which one gated work.
- **The menu is derived from `APEX_PROFILES`.** `APEX_RUNG_HINTS` is a
  `Record<ApexProfile, string>`, so a profile added without a hint is a type
  error rather than a row with nothing under it; each hint states what
  `alpha.apex.contract` actually grants at that rung (ASSIST withholds the seven
  capability-changing keys, AUTONOMOUS and APEX_MAX hold every authority and
  differ only in budgets).
- **The chip renders `apexChipView`, never a branch of its own.** A rejected read
  and a `load_error` in a 200 payload are both `unknown`, checked before the
  enabled flag — the server answers `enabled: false` because it *could not tell*,
  and rendering that as OFF converts "not known" into "it is off". `enabled` but
  not `contract_enabled` is a third state, `degraded`, not a green ON. A profile
  the record retains while off is **not** printed: retained is not in force.
- **Nothing is painted from the write.** `apply()` sends the mutation, then
  re-reads `/apex/mode`; the POST's response body is discarded. A refused write
  closes nothing and shows the server's reason under the rungs.
- **The disclosure is rendered, not exported.** `APEX_COMPOSER_DISCLOSURE` says
  the control sets the autonomy contract for the scope and does not start a run,
  and the menu prints it — the chat screen is where "pick a profile and
  everything runs itself" is the natural reading, and the false one.
- **The menu is portalled to `document.body` and placed by
  `lib/workspace-menu-geometry.ts`.** An `absolute` panel here was clipped by
  `<main class="overflow-hidden">`: its top sat at 157px inside an ancestor cut
  at 191px, so the heading was off screen while the rungs were not — the same
  failure `NavTabs.tsx` documents, and the same fix (`placeFloatingPanel` caps,
  flips and clamps, and the panel scrolls internally instead of hiding rows).
  Because the panel leaves the trigger's wrapper, the outside-click check must
  test **both** refs, and Escape moves to a `document` listener. The height it
  is given is `ceil(scrollHeight + borders)`: `border-border` is 0.8px here and
  `placeFloatingPanel` floors, so an under-estimate of a fraction of a pixel
  still opens the menu with a one-pixel scrollbar.

Coverage: `src/lib/apex.test.mjs` (menu derivation, the five chip states, the
checkmark rule, and source pins for the re-read, the disclosure and the single
composer mount).

## Intelligence control panel (`intelligence` workspace view)

`lib/intelligence.ts` + `components/sections/IntelligenceSection.tsx`, over
`GET /api/intelligence/control-plane` — one payload carrying seven source
sections (mode, loop health, evidence ledger, journal integrity, capability
fabric, replay reservoir, goals), a `metrics[]` list and a `summary`. The
`intelligence` id follows the four-place wiring rule (union, `WORKSPACE_TABS`
row, `WORKSPACE_VIEW_IDS`, ChatView import + render case); the union entry sits
*before* `reliability` because `reliability-view.test.mjs` pins that `reliability`
stays the terminal member.

The panel exists to answer "is the loop actually working, and what is each
answer based on?" — so the **basis is the only thing that licenses a value**,
and the client enforces the same rule the backend enforces at construction:

- **The basis decides the value cell.** `metricValueText()` switches on the
  basis before it ever looks at the number: a non-measured basis renders words
  (unmeasured → `not measured`, unavailable → `source unavailable`, unowned →
  `no aggregate owner`, unknown basis → `value not reported`, missing basis →
  `basis not reported`). A `0` smuggled onto an `unavailable` metric renders
  those words, not `0` — the backend already forces `value = null` there, and
  this is the client's half of that pin. A measured `0` renders `0`; a measured
  `null` renders `not reported`.
- **Missing summary counts are `null`, never `0`.** The mapper returns `null`
  for an absent count, and the view renders it as `—` with the counts line
  saying what was not reported. An absent `metrics` list maps to `null`
  (distinct from `[]`): "the Gateway sent no metric list" and "the list is
  empty" are different facts and get different empty states.
- **A section key missing from the payload becomes `available: false` with a
  reason** — "the Gateway did not include this section in the payload" — so a
  schema that dropped a section reads as unavailable rather than as a card
  grid with a hole in it. A section the server marks unavailable renders the
  server's own reason on the card, and every `SectionFacts` absence is words,
  never a bare dash.
- **`schema_version` is disclosed, not trusted.** A payload whose
  `schema_version` differs from `CONTROL_PLANE_SCHEMA` (`alpha.control-plane.v1`)
  raises a notice naming both strings instead of silently dropping fields it
  does not recognise.
- **The health badge is never green by default.** `healthStateTone()` maps
  improving/stable → green, saturating → amber, regressing → red, and *every
  other string* → gray; a missing health state renders `health state not
  reported`. An unfamiliar regime from a newer Gateway renders verbatim in
  gray rather than snapping to a state this build knows.
- **The loop-health fallback keeps `scored_attempts` at `null`.** When the
  report is the config-failure shape (`{regime, reason}`, no attempts field),
  the reader maps the absent field to `null` — it must never read as `0`
  scored attempts, which would claim the loop measured zero work rather than
  that it could not measure.
- **The client is read-only.** `fetchControlPlane()` imports `get` only, and
  the test suite pins the single `GET /intelligence/control-plane` call plus a
  source-level refusal of `send` — the intelligence router has no mutation
  route, so a client that could post would be describing a surface the server
  does not have.

The section header carries the health badge and a Refresh button that disables
in flight; a failed refresh keeps the previously read plane on screen with the
error stated, never blanking measured data into an empty panel. Unavailable
sources and the `unowned` metrics each get their own notice explaining *why*
they have no number.

Coverage: `src/lib/intelligence.test.mjs` (route/verb pin, read-only source
pins, `SCHEMA_VERSION`/basis/section contract pins, envelope mapping including
the omitted-section state, and the honesty inversions above).

## Sentinel repair loop (`sentinel` workspace view)

`lib/sentinel.ts` + `components/sections/SentinelSection.tsx`, over
`GET|POST /api/autonomy/sentinel/*`. The `sentinel` id follows the same
four-place wiring rule as the two above (union, `WORKSPACE_TABS` row,
`WORKSPACE_VIEW_IDS`, ChatView lazy import plus render case), and the union
entry sits **before** `reliability` because `reliability-view.test.mjs` pins
`reliability` as the terminal member.

The panel answers "is the engine repairing anything, and which faults keep
coming back?" — so the **basis is the only thing that licenses a value**, and
the client enforces the same rules the backend enforces when it folds:

- **A verdict is derived from counters, never asserted.** `analyticsHealthView`
  returns a `reason` beside its `label`, so a green word is always attached to
  the counters that earned it. `verdictWords`/`verdictTone` own the per-kind
  word, and `kindEvidenceLine` lists the counts in a separate sentence — a kind
  with one repair and nine escalations reads "seen 12x, repaired 1x, escalated
  9x", so neither the word nor the numbers can carry the other's claim.
- **Absent is `null`, never `0`.** `scannedText` returns *not reported by any
  pass* when no pass reported a count and `durationText` returns *not measured*
  for a null. A capped window discloses how many older passes it excluded, so a
  total can never be quoted as the whole journal.
- **An unrecognised verdict renders verbatim in a neutral badge.** A verdict
  from a newer Gateway is never snapped to one of the five this build knows,
  which would turn an unknown word into a green tick.
- **Two counts that are not the same number stay separate.**
  `outcome_count` counts outcome rows; `passes_without_outcomes` counts passes
  that carried none, and the fold's own disclosure names both.
- **Six independent reads, six independent failures.** Every panel owns its own
  `useCallback` load and names itself when it fails (`Sentinel analytics
  unavailable`, `Fault-kind registry unavailable`, `Sentinel handoffs
  unavailable`, `Sentinel report journal unreadable`, `Signal collection
  failed`). There is no `Promise.all` anywhere in the section: one corrupt
  journal must not present itself as an entirely empty plane.
- **A refused decision keeps the server's words.** `failureText` is not
  `errMsg`: the shared helper paraphrates a 403, which is right for an
  incidental read and wrong for the answer to a deliberate write. There is no
  client-side role check — the buttons appear for every caller and a member's
  403 is rendered verbatim.
- **Nothing is painted from a click.** A decision posts, then the list is
  re-read; a refused write fires nothing and leaves the row exactly where it
  was. The control is disabled in flight so a double-click cannot record two
  decisions. The repair pass is opt-in per click, off by default, with its
  safety model printed beside the control.

The three re-exported routes (`reports`, `signals`, `run`) stay in
`lib/supervisor.ts`, which already owns the autonomy plane; `sentinel.ts`
re-exports them rather than growing a second mapper that could disagree about
what the same journal means.

Coverage: `src/lib/sentinel.test.mjs` (routes, verbs, the null-preserving
mappers, the derived `truncated`, `failureText`, the derived headline verdict
and the evidence line) and `src/lib/sentinel-view.test.mjs` (the four-place
wiring and every honesty claim the panel renders).

## Effect journal (`effects` workspace view)
`lib/side-effects.ts` + `components/sections/EffectsSection.tsx`, over
`GET /api/side-effects/*`. The `effects` id follows the four-place wiring rule
(union, `WORKSPACE_TABS` row, `WORKSPACE_VIEW_IDS`, ChatView lazy import plus
render case); the union entry sits **before** `reliability`, because
`reliability-view.test.mjs` pins `reliability` as the terminal member.

The panel answers "which external effects is Alpha unsure about, and what did an
operator decide about them?" — so an absent field must never become a measured
one:

| Server says | Panel shows |
| --- | --- |
| `reported: false` | its reason, and *not reported* for every count |
| `oldest_unknown_age_seconds: null` while reported | *no unknown entry waiting* |
| that same `null` while unreported | *not reported* — never `0s` |
| `entries: null` | "The Gateway sent no entry list" — not "no effects" |
| `entries: []` | "No entries match this filter" |
| either read rejected | its own reason, beside the data the *other* read returned |
| `reconcilable: true` | the verdict form |
| `reconcilable: false` | the status, and no button implying a pending verdict |
| `reconcilable: null` | *reconcilability not reported*, and **no button** |
| `reopened` / `escalated: null` after a submit | *not reported* — never `false` |
| an unknown status or level string | rendered verbatim in a grey badge |
| a 403 or 409 on reconcile | the server's own sentence, verbatim |

Rules that must keep:

- **The two reads fail independently.** Summary and list go through
  `Promise.allSettled`, so a summary that 503s cannot blank a list that answered;
  each failure renders its own `Notice` naming which read it was.
- **A reconcile re-reads the entry before it offers a form.** The table row may
  be seconds stale, and offering a verdict on an entry somebody else already
  settled is how a 409 gets blamed on the operator. `fetchSideEffect(id)` decides
  `reconcilable`, and `null` declines the form rather than guessing.
- **Nothing is painted from the click.** The submit awaits
  `reconcileSideEffect(...)`, renders the *response's* `reopened` / `escalated`,
  then re-reads the entry, the summary and the list. An acknowledgement checkbox
  that starts unchecked gates it, and the button is disabled in flight.
- **Authorisation is never inferred.** There is no client-side role check: the
  button appears for every caller and a member's 403 is rendered through
  `failureText`, because `errMsg` would replace it with a generic sentence and
  the refusal *is* the answer.
- **Bounds are mirrored, not tightened.** `clampLimit` copies the server's
  `ge=1, le=500`; an unknown `status`/`level` filter and an out-of-bound `reason`
  are refused **locally with the bound named** — sending them would only earn a
  422 carrying the same list. Reason length is measured on the raw string, as
  Pydantic measures it.
- **`failureText` is not `errMsg`.** `errMsg` paraphrases 401/403/404 on
  purpose, which is right for an incidental call and wrong for a refusal;
  `failureText` keeps an `Error` carrying a numeric `status` verbatim and falls
  back to `errMsg` for anything else.
- **The panel is otherwise read-only.** It reads, it reconciles, and it says so:
  recording a verdict never cancels, resumes or replays a run, and only digests
  cross the API.

Coverage: `src/lib/side-effects.test.mjs` (routes, verbs, the null-preserving
mappers, the clamp, the local refusals, `failureText`) and
`src/lib/effects-view.test.mjs` (the four-place wiring and every rendering
above).

## Subagent catalog panel (every field of a definition)

`lib/subagents.ts` + `lib/subagent-catalog-view.ts` +
`CatalogPanel`/`SubagentDetail` in
`components/sections/SubagentsSection.tsx`, over `GET /api/subagents`.

**It is a detail pane because the fields it shows did not fit on a card.**
`SubagentResponse` sends fifteen fields. The catalog block was a two-column grid
rendering four of them - name, an on/off badge, a `line-clamp-2` description,
and `model . source`. Measured live against the Gateway: 8 builtin definitions,
23 tool names across them, and prompts of 292 to 2384 characters, all of it
discarded. The `line-clamp-2` was the worst of it: every builtin description
ends with a **"when NOT to use this"** paragraph, which is exactly what decides
whether a delegation is worth its context cost, and the clamp hid it. Selecting a
row opens the full record.

An operator choosing a subagent needs to answer *what may it call* and *what is it
told to do*, and those are exactly the two fields that were being dropped.

### `null` and `[]` are different facts, in four places

| Server sent | Meaning | Must not become |
| --- | --- | --- |
| `tools: null` | no allowlist constraint - unrestricted | "no tools" |
| `tools: []` | an explicit empty allowlist - nothing callable | "not reported" |
| `system_prompt: null` | withheld; the route is **admin-gated** | "no prompt" |
| `max_turns: null` | unreported | `50` (the server default) |
| `config_overrides: {}` | operator wrote nothing; defaults in force | "not configured" |

Both list states are reachable live: seven builtins carry explicit tool lists and
`general-purpose` sends `tools: null`. The `[]` state is not reachable from this
route today - it needs a caller to pass `tools: []` explicitly - so it is covered
by unit tests only, and should not be described as live-verified.

`system_prompt` is gated by `is_admin_user` in `subagents.py`, so `null` is a
**permission outcome**. On a deployment with `ALPHA_AUTH_DISABLED` set the prompt
is visible to everyone and the gate never engages; the panel must not be
documented as though it always withholds.

The empty-overrides case is the subtle one: `_explicit_overrides` reports only
keys present in `model_fields_set`, so `{}` is what a stock install sends for
every definition. "Not configured" is false there - the defaults are very much in
force - so the sentence says that instead.

### The enabled tri-state

`SubagentDef.enabled` is `boolean | null` and the third state is load-bearing. It
was `Boolean(pick(s, ["enabled"], true))`, which made an absent flag **TRUE** and
painted it green; `enabledView()` now owns the three states and both the list row
and the detail header call it. `collaboration-surfaces-honesty.test.mjs` pins that
the panel derives its badge from the helper rather than branching on `=== null`
inline, so the pin follows the behaviour instead of the old literal.

### Ownership rules

- **Every sentence lives in `lib/subagent-catalog-view.ts`.** The panel holds no
  claim of its own. A second copy of a phrase is how a list row and its own
  detail pane end up contradicting each other for the same field, and
  `subagent-catalog-view.test.mjs` fails on any absence phrasing that appears in
  the panel markup instead of in a helper.
- **A filtered list says how many it hid.** "N of M hidden by the filter" keeps a
  short list from reading as the whole catalog, which is the same failure as a
  count with no evidence.
- **Selection resets when the catalog changes.** Two catalogs of the same size
  really can differ - a managed definition replacing a builtin - and a pane left
  on the deleted row would be naming a definition the read did not return.
- **An unknown `source` renders verbatim** with a neutral tone and sorts last.
  Snapping it to `builtin` would tell the operator a runtime-created definition
  ships with Alpha.
- The panel is **read-only**. Create/edit/delete belong to the admin routes the
  registry surface already owns; this one reports.

Coverage: `src/lib/subagent-catalog-view.test.mjs` (40 cases, each naming the
payload that would make a plausible wrong word appear) and the enabled-tri-state
pins in `src/lib/collaboration-surfaces-honesty.test.mjs`.
`backend/scripts/render_subagent_catalog.py` transpiles the shipped view module
with the repo's own TypeScript compiler and renders the live Gateway payload, so
"the panel will show this" is checkable without a browser.

## `@` tag palette (composer)

`lib/agent-mentions.ts` + the `@` branch of `components/Composer.tsx`. Type `@`
anywhere in the composer to list the AI agents you can tag.

**It is a mirror of the Gateway's ONE mention grammar**
(`alpha.channels.mentions`), and the mirror has to be exact. That module's own
docstring states the rule: a handle resolves **exactly** after case folding,
never by prefix, substring or fuzzy match, because `@rev` silently landing on
`reviewer` is how a message reaches a bot nobody named. So:

- **Every token inserted is a token the server can resolve.** Insertion writes
  the canonical `@bot:<handle>` / `@role:<name>` / `@everyone` spelling taken
  from the roster row, **never the characters the operator typed**. That is what
  lets `scoreRow` rank generously (exact > prefix > word-boundary > substring >
  subsequence) without any match being able to write the wrong handle.
- **Punctuation is not stripped.** `rev-1` and `rev_1` are different handles.
- **The fan-out ceiling is 32** (`MAX_TARGETS_PER_MESSAGE`). A selector above it
  renders *refused with the numbers*, never clamped.

Four rules the trigger detection owns, each with a plausible wrong reading:

| Situation | Why it must behave this way |
| --- | --- |
| `user@example.com` | Not a mention. The `@` needs a word boundary — start of input, or after whitespace/an opening delimiter. A picker that opens on every `@` is hijacking text it does not own. |
| Caret moved back into an earlier token | The palette follows the caret, not the end of the value. It is derived from `selectionStart` on every event that can move it, because `/` deriving from the value start cannot work mid-sentence. |
| `Escape` then typing more | Dismissal is **per-token, not sticky**. An `Escape` at the end of a sentence, with no palette open, must not disarm a token not yet finished. |
| `@all` / `@everyone` | The fan-out selector, never a handle. An unknown bare token is still refused, never fanned out. |

**Bot mode is a separate row set, not a side effect of insertion.** Tagging an
agent and re-pointing the conversation at it are different decisions, so
`buildMentionRows` emits a `mention` row *and* a `switch` row per agent. Both
write the identical token, which is deliberate: a switch that fails to apply
still leaves a visible, correctable tag. `onMentionSwitchAgent` is the gate — a
surface without a handler gets **no** switch rows, and the active agent is never
offered a switch back to itself. A control that could only be a no-op is not a
control.

**The status strip is not decoration.** The server resolves an unknown handle to
*nothing*, so a typo means the message goes out and calls nobody, silently. The
strip runs the same resolution client-side and names the dead token with the
server's own reason before send. It renders **only** when the draft holds an
`@token`, so "no tags yet" and "a tag that addresses nobody" never look alike.

### What is mirrored, and what is not

Mirrored: token charset, case folding (only), the `bot:`/`role:`/`everyone`
selectors, the `all`/`everyone` aliases, exact resolution, the ceiling.

**Not mirrored — roles are client-derived.** `buildMentionRows` groups by the
roster's `department` column because that is a real column group rules also match
against, but `GroupChatService.post_message` passes **no** role index to
`parse_mentions` at all, so a `@role:` token addressed through a group room
resolves to nothing server-side. The rows state how many agents they reach
*locally* and never claim a dispatch. A **resolved handle is not a delivered
run**, either — resolution is pure string work; only `switch` changes routing,
because only that moves `assistant_id` / `bot_name`.

`fetchBotsResult` exists for this surface: `fetchBots` returns `[]` on failure,
which a picker would render as "no agents available" — a claim about the fleet
that nothing measured. Four states render separately: loading, `unavailable`
(with the server's reason), empty roster, and no-exact-match. A picker that
quietly shows nothing is indistinguishable from a fleet with no agents.

No lookbehind anywhere in the module — Safari below 16.4 cannot parse it and
this is browser code.

Coverage: `src/lib/agent-mentions.test.mjs` (35 cases, each paired against the
server grammar, including a charset-parity test that runs both regexes over the
same probes — the mirror drifting is the only failure mode this file can
introduce).

## Messages view derivations (`messages-view.ts`)

`lib/messages-view.ts` is the pure layer behind `components/sections/
MessagesSection.tsx` (~1,900 lines of JSX). It owns the *shape* of what the
Messages tab shows — how the conversation column splits, what a filter chip's
number means, and when two transcript rows are one author continuing — because
those decide what a reader sees at a glance and rot quietly inlined in JSX:
nothing fails, the numbers just stop meaning what the label says.

Four exports, four rules:

| Export | Owns | The failure it stops |
| --- | --- | --- |
| `sectionConversations` | Groups → Direct, **empty halves omitted** | a "Direct (0)" header over nothing, which reads as a measured zero |
| `hiddenSummary` | `Showing 3 of 12 — 9 hidden…`, `null` when nothing hid | a filtered list presenting itself as the whole inbox |
| `unreadLabel` | the `99+` pill cap | a second inline cap drifting from the one the tests drive |
| `groupMessageRuns` | author / day / deleted-row run breaks | a sender's name printed six times, reading as six speakers |

- **The component keeps the honest *read* sentences inline, deliberately.**
  `History not read yet — open the room`, the failed-room-read gate and the
  unread roster headline stay in the JSX because they are pinned there by
  `collaboration-surfaces-honesty.test.mjs` and friends — a second copy in a
  helper is how a transcript and its own helper end up contradicting each
  other. `messages-view.ts` must stay import-free: its test transpiles it and
  evaluates it behind a `require` that throws, so an accidental import fails the
  suite instead of becoming a drifting second implementation.
- **One predicate counts the chips *and* renders the list.** `matchesFilter` in
  the component is called with the filter being *asked about*, so each chip
  reports what it would show if pressed. Two copies of that rule is how a chip
  claims `4` over a list of three. The unfiltered `allConvs` is the denominator
  for both the chip counts and `totalUnread` — summing the *filtered* rows
  meant pressing "Groups" made unread DMs vanish from the workspace badge.
- **A decisions/blockers filter names the rooms it could not search.** Only
  history that was read can be searched; excluding the rest silently reads as
  "no decisions in this workspace", so the excluded count is rendered beside the
  filter with the reason.
- **`groupMessageRuns` takes the day vocabulary as an argument** — the
  component passes its own `dayLabel`, the same clock the transcript dividers
  use, rather than importing a date library and disagreeing with them.

Coverage: `src/lib/messages-view.test.mjs` (the pure derivations, plus source
pins on the component for the shared predicate, the pre-filter unread sum, the
two disclosures, the sectioning, and the still-rendered pinned sentences).

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

### Connecting is one string, copied once

`PeerConnectPanel` is the surface the whole feature exists for. The rules it is
built around, each of which has a tempting wrong reading:

| Server says | Panel shows |
| --- | --- |
| `include_secret: true` | `contains your pairing code`, plus an explicit "works only once" warning |
| `include_secret: false` | `safe to share publicly` |
| `epoch: null` | no invite number — the claim has no replay guard |
| `expires_at: null` | `no expiry reported`, never "never expires" |
| an expired claim | refused before the request, and again by the server |
| an address-only invite | parses and previews, then **Connect is disabled** with the reason |
| a malformed field | the refusal names the field — never "invalid invite" |

- **The paste decides what happens.** `onPaste` inspects the clipboard before
  the textarea: a connection string previews, an image is decoded, anything else
  falls through untouched. A handler that ate every paste would break typing.
- **A client-side check never decides redeemability.** Expiry, replay, and the
  code check are server verdicts; `invite.ts` mirrors the *grammar* so a paste can
  be explained, and `redeemabilityProblem` only previews the server's answer.
- **`invite.ts` is a mirror, not an authority, and deliberately duplicates less
  than the server.** It checks shape (scheme, credentials, host). It does **not**
  copy the blocked-metadata-host table — a browser-side pass proves nothing about
  what the Gateway will accept, and a second list would only drift.
- **Copy is labelled by what it contains.** `useCopyButton` is the one clipboard
  implementation; a denied permission must surface a worded remedy, never a
  silent no-op.
- **A failed request is never an empty panel.** Each panel owns its own error.

**Reading a QR code is not enabled.** `canDecodeQr()` in `lib/qr-decode.ts` is
`false` and the camera/screenshot buttons are disabled with that reason. Showing
a code works and is verified. Do not mount the scanner by flipping the boolean
alone — the failing round-trip gate in `qr-decode.test.mjs` is what turns it on.
