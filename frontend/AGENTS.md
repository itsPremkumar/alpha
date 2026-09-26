# Frontend agent guide (`frontend/`)

Scope: this file covers everything under `frontend/`. Deeper, subsystem-specific
rules live in `frontend/src/AGENTS.md` and win where they are stricter.

## What this app is

Alpha's operator UI: a Next.js App Router frontend that talks to the FastAPI
gateway under `/api`. It is a **client of measured gateway state** — it never
invents backend facts.

## Non-negotiables

1. **No fabricated state.** Every number, status, count, and "healthy/ready"
   claim must come from a real API response. If a field is missing, render it as
   unknown/disabled — never as zero, empty, or a green badge.
2. **Failures surface the server's reason.** The shared client
   (`src/lib/api-client.ts`) carries the gateway's `detail` in `ApiClientError`;
   show it. Do not swallow it into a generic "something went wrong".
3. **Defaults that are off must look off.** An autonomy loop that is disabled by
   configuration renders as *disabled* with the reason, not as idle/healthy.
4. **Silence is not success.** A component that is loading, empty, or
   unavailable says which one it is.

## Client rules

- Clients live in `src/lib/*.ts` and wrap the shared `get`/`send` helpers from
  `src/lib/http.ts`. Do not call `fetch` directly from a component.
- A client maps the server payload and preserves honesty: absent optional fields
  become `null` (not `0`, not `""`, not `false`), and unknown enum values are
  rendered verbatim rather than coerced.
- Reject on non-2xx. A failed call must reject with the server's reason rather
  than resolving to an empty list that looks like "nothing was recorded".

## Chat history and project membership contract

- `src/lib/history-store.ts` is the uncapped, browser-local archive. It uses
  IndexedDB (not capped/truncated localStorage), migrates `alpha.chatstore.v1`
  once, and surfaces quota/transaction failures instead of dropping old chats.
  Server pages are additive; the Gateway's current visible thread state still
  wins when it is non-empty, while a genuinely empty/degraded server read may
  fall back to the complete local archive.
- **Never infer emptiness from an absent local message list.** The only
  destructive archive operation is an explicit user delete. A thread is hidden
  from the list only behind a *complete* empty server page **and** a confirmed
  empty upload list; a partial page, a failed read, or an unreadable upload
  list all keep the thread visible. `ChatView` archives every server thread
  into IndexedDB in bounded background batches (`archiveServerHistory`), and a
  partial page is archived with its truncation disclosed — never discarded and
  never presented as a complete answer.
- **IndexedDB write discipline.** Every read-modify-write goes through
  `updateRecord`/`updateRecords`, which issue the `put()` from inside the
  request's own `onsuccess` handler. Never `await` a `get()` and then write:
  a browser auto-commits the `readwrite` transaction once the microtask queue
  drains, so the write would be lost to `TransactionInactiveError`. A merge
  failure aborts the transaction and surfaces its real cause through
  `transactionDoneWith`. `onversionchange`/`onclose` clear the cached
  `databasePromise` so a schema upgrade in another tab reopens instead of
  writing through a dead handle.
- Thread metadata merges field-wise. A server row that omits `bot_name` /
  `project_id` (older Gateway) must not erase a locally known value, in either
  `history-store.ts` or `ChatView.mergeThreads`.
- Opening **New Chat**, changing bot, or choosing a project is an unsent draft.
  Do not call `POST /threads` until a real prompt, slash command, or attachment
  occurs. A first-turn attachment that fails must remove its uncommitted draft
  (or surface a cleanup failure); never leave an empty upload row behind.
  `handleAttach` holds a synchronous lock so two rapid drops cannot each create
  a draft thread and server row.
- **Per-thread vs workspace-wide state.** A run claims a generation
  (`runGenerationRef`); every user navigation bumps it through
  `stopVoiceForNavigation`. After that, a still-streaming run may write to the
  local archive but must not touch messages, usage, suggestions, the retry
  panel, or the composer draft of the thread the user opened next. `isLoading`
  is the exception: it is workspace-wide and always clears in `finally`.
- An unavailable server conversation list renders as an explicit
  `serverHistoryError` banner stating that the visible history is the local
  copy — never as a confirmed empty history. Sidebar search drops stale
  responses via a generation ref so a slow earlier query cannot overwrite a
  newer one.
- Thread search and message history must follow every server page. Never restore
  the old 100-thread/300-message/20,000-character limits. Page requests carry a
  30s deadline; a malformed 200 envelope is a failure, not an empty list; a
  non-decreasing cursor stops the walk and is reported. A truncated walk returns
  the rows that did arrive with `incomplete` + `resumeCursor` so the UI can say
  the history is partial.
- Every bot profile exposes project creation and attaches that bot as lead via
  `projects.ts -> createProject(..., agents)`. The Projects view owns multi-bot
  membership through the real presence/attach/detach routes, then re-reads the
  confirmed roster (`confirmProjectAgents`) before any success notice — a 2xx
  attach is not proof the bots joined. Presence rows are validated, project
  conversations are paginated, and moving a conversation re-reads the parent's
  thread list through `onThreadsChanged`.

## Testing (required for client changes)

```
cd frontend
node node_modules/typescript\bin\tsc --noEmit     # must be 0 errors
node --test src/lib/*.test.mjs                    # pure Node tests, no server
```

Client tests are plain `node --test` files (`*.test.mjs`) that transpile the TS
module and run it against a stubbed HTTP layer. They pin the real paths/verbs
and the honesty inversions (a degraded read must not look like an empty one).

## Style

- Tailwind utility classes; match the density of neighbouring components
  (`text-[11px]`, `rounded-xl`, `border-border/60`) rather than inventing a new
  scale.
- Icons come from `lucide-react`; reuse the icon vocabulary already imported in
  the file you are editing.
- Keep the existing section-component pattern: a lazy-loaded component in
  `src/components/sections/`, wired through the workspace view registry.

## Lion companion contract

The lion companion is presentation-only and feature-isolated under
`frontend/src/components/lion-pet/`: `LionPet.tsx` owns rendering and
interactions, `lion-pet-model.ts` owns looks/actions/settings and bounded
message sanitization, `useLionPetActivity.ts` is the lifecycle adapter,
walk/run travel is bounded to the visible viewport, and `lion-pet.css` owns
the isolated styles. `ChatView.tsx` consumes only the
adapter; compatibility files at `frontend/src/components/LionPet.tsx` and
`frontend/src/lib/lion-pet.ts` are facades and must not become implementation
locations.

The optional Electron companion in `electron/pet.html` is a transparent
always-on-top window, not a second agent runtime. Native window lifecycle and
state sanitization live in `electron/lib/lion-pet-window.js`; `electron/main.js`
only wires that controller to application lifecycle and IPC. The main process
accepts companion state only from trusted local windows, clamps it to the known
state/action/skin sets and a short message, and never forwards prompts,
responses, thread IDs, tool output, or credentials. The controller also owns
bounded work-area motion for native walk/run actions. The in-app pet does not put
thread IDs in its DOM or pass them through the native bridge. Closing the main
desktop window closes the companion and follows normal service shutdown.

Regression coverage lives in `frontend/src/lib/lion-pet.test.mjs` and
`electron/tests/lion-pet.test.mjs`; the user-facing contract is documented in
`docs/LION_COMPANION.md`.

## Alpha Network view

The `peers` workspace view is the dedicated cross-installation communication
session. It uses `src/lib/peer-network.ts` and must preserve server-reported
provider/trust/delivery state, including honest `available: false` and
`queued` states. It must not reuse local roster/group-chat semantics or expose
pairing credentials/endpoints in model-visible output. Contract tests live in
`src/lib/peer-network.test.mjs`.
