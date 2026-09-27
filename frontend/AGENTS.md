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

## Branding

`src/assets/images/alpha.png` is the one real logo, and `src/lib/branding.ts` is
the only place a display name or an icon path is written. `components/BrandLogo.tsx`
and `BrandMark` render the logo for the in-app surfaces, and `app/layout.tsx` plus
`app/manifest.ts` read `branding.icons` for the tab, the install and the Apple
touch icon.

The files in `public/` are **generated, not hand-made** — run
`node scripts/generate-brand-assets.mjs` (repo root) after changing the logo and
commit the result. Never edit one, and never add an icon file without adding it
to `branding.icons`; `src/lib/branding.test.mjs` checks that the generator and
`branding.icons` agree in both directions. The full generator contract, the
mane-vs-face crop rule, and the desktop/installer surfaces are in the
[brand assets section of the root guide](../AGENTS.md#brand-assets).

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

## Build discipline: `next build` and a running server cannot share `.next`

A Next.js server (dev **or** `start`) and `next build` both read and write
`.next/`. When they overlap, the build emits a bundle whose runtime references
vendor chunks that were never written, and the failure surfaces as a misleading
error that changes between runs — all of these were observed from the same
cause:

- `PageNotFoundError: Cannot find module for page: /_document`
- `Type error: File '.next/types/app/layout.ts' not found. Root file specified`
- `Could not find files for /_error in .next/build-manifest.json`
- `Cannot find module './vendor-chunks/lucide-react@…js'` at runtime (500)
- `SyntaxError: Unexpected non-whitespace character after JSON at position 4`

None of these are source bugs. **Stop the server before building, and start it
after.** `next dev` supervises and *respawns* its server child, so killing the
child is not enough — kill the `next dev` parent or the port comes straight back
and the race resumes:

```powershell
# Stop every Next process for this app, parent supervisors included.
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -match 'Videos\\alpha\\frontend' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

cd frontend
Remove-Item -Recurse -Force .next          # a failed build leaves a mixed .next
Remove-Item -Force tsconfig.tsbuildinfo -ErrorAction SilentlyContinue
node node_modules/next/dist/bin/next build # no `next typegen` first
node node_modules/next/dist/bin/next start -p 3000
```

Two traps worth stating:

1. **Do not run `next typegen` before `next build`.** `typegen` writes a
   *partial* `.next/types` tree and `build` then skips generating the rest, so
   the type check is handed a root file (`.next/types/app/layout.ts`) that was
   never created. `next build` runs its own typegen.
2. **A build that "succeeds" on a reused `.next` can still be broken.** The
   telling sign is a *partial* `.next/server/vendor-chunks/`: a healthy build of
   this app has **no** `vendor-chunks` directory at all, and no `webpack-runtime`
   that requires a chunk from one. If `vendor-chunks` exists but lists fewer
   chunks than the runtime requires, the server bundle is inconsistent — the
   process will boot, print `Ready`, and then 500 on the first request. Wipe and
   rebuild. (`.next/server/pages/` *does* legitimately exist — Next 15 emits the
   Pages Router fallback files `404.html`/`500.html`/`_app`/`_document`/`_error`
   even for an App-Router-only app. Its presence is not a defect; a missing
   vendor chunk it requires is.)

`frontend/AGENTS.md` gates: `node node_modules/typescript\bin\tsc --noEmit` = 0
errors, `node --test src/lib/*.test.mjs` green. Both are safe to run *while* a
server is up, because only `next build` writes `.next`.

## Update control contract

- `components/UpdateControl.tsx` is mounted in the `ChatView` header beside
  `WorkspaceVitals`, so update state is always visible without opening
  Settings. Settings → General keeps the fuller identity card; both read the
  same client.
- **Mount reads `getEvolutionUpdateState()` only** — a local file read. The
  GitHub-reaching `checkForEvolutionUpdate()` runs solely from the click
  handler. Never add an automatic check on load, on an interval, or on view
  change.
- **The control performs no version comparison of its own.** "An update exists"
  comes from the server's `state === "UPDATE_AVAILABLE"` string, and applying
  additionally requires `canApplyUpdate(state)` — the single predicate exported
  from `lib/evolution.ts`. Gating on the state string alone is a defect.
- `canApply` is read with `=== true`, never truthiness, and a missing or corrupt
  state file is a deny plus a disclosure.
- The three mutating routes (`update-apply`, `update-skip`, `update-recover`)
  are admin-only and 403 under `AGENT_WORKSPACE_AUTH_DISABLED`. Show that
  refusal; do not retry, downgrade it to a notice, or present a queued-looking
  success.
- The client may never send a URL, ref, commit, or archive — the server applies
  only its own verified candidate. `skipEvolutionUpdate("")` refuses locally
  rather than sending an empty version the route would 422.
- Tests: `src/lib/evolution-update.test.mjs`.

## Project crew, grouping, and quick-start contract

- **The crew client** (`src/lib/projects.ts` -> `getCrew` / `updateCollaboration` /
  `getProjectMemory`) reads the real `GET|PATCH /projects/{id}/{crew,collaboration}`
  and `GET /projects/{id}/memory` routes. `GET /crew` is `ensure_crew`, a
  *reconciling* read: it syncs membership against the group room as a side
  effect, so polling it is the supported way to stay honest.
- **Three room states that are not interchangeable.** `room === null` is a real
  answer (a crew under 2 members has no room yet); `room.parked` means the room
  kept its history and is inactive; an *unreadable* room is reported as an
  error. `toCrewView` rejects rather than downgrading an unreadable `room` to
  `null`, because those are opposite claims. Same rule for
  `collaboration.orchestration_mode`, which is never defaulted.
- **Unknown enum values are preserved verbatim** and offered back as an option
  by the settings form, so a mode from a newer Gateway is displayed rather than
  silently snapped to `moderated` on save.
- **A settings save renders the server's reconciled response**, never the draft
  the user typed, and sends only the keys that actually changed (an empty patch
  is a 422). `saving` disables the control so a double-click cannot double-apply.
  When a quick-start template's coordination policy fails to apply after the
  project itself was created, the UI says exactly that instead of reporting a
  clean creation.
- **Quick-start templates declare agent *roles*, never bot names**
  (`PROJECT_TEMPLATES`). Bots are runtime roster data, not configuration. The
  picker matches roles to whatever bots the Gateway reports, names any role it
  could not staff, and stays editable before creation. A template with no
  `collaboration` keys must not fire an empty PATCH.
- **The sidebar groups conversations by the server's `projectId`.** A thread with
  no project is a real state and gets its own `No project` group; a project whose
  name is not in the loaded list renders under its raw id rather than being
  dropped. Group collapse state is keyed by project id and defaults to expanded.
  Search results are not grouped.
- `components/sections/ProjectCrewPanel.tsx` is rendered by `ProjectsSection`, so
  it is reachable without a workspace-view registry entry. If it ever becomes its
  own top-level view it must be registered in all three places the layout guide
  names, or it is dead code.
- Regression coverage lives in `src/lib/projects-crew.test.mjs` (routes/verbs,
  the three room states, unknown-enum preservation, template shape, and the
  sidebar-grouping and panel-wiring source pins) alongside
  `src/lib/projects.test.mjs`.

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
