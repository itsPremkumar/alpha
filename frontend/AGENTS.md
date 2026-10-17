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

The Run Inspector's acceptance panel offers a recheck only after the Gateway
returns an existing verification state for a successful run. A `not_requested`
state means the run declared no acceptance criteria; display the Gateway's
reason and do not show an action the API must reject.

For APEX, the contract preview and profile-to-enable are separate controls.
When they differ, show the mismatch and provide a direct action to preview the
profile that will actually be enabled; never let an operator mistake one
profile's authority or limits for another's.
The APEX objective form narrows each new UI-created session with editable finite
token, tool-call, and runtime ceilings; keep the in-flight token overshoot caveat
visible because token use is measured after model calls. Expanded session history
must show frozen per-session ceilings beside measured usage, not only profile policy.
The workspace header must label the `default` model selection as **Default
(auto-routed)**, matching the composer; an unknown persisted model id must be
shown as unknown rather than as no selection.

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
- Team Ops swarm structure reads detail, events, and measured telemetry as
  independent requests. Keep errors distinct from absent measurements; display
  `parallelism_realised` and `subtask_completion_ratio` as advisory execution
  signals, never as proof that a result is correct. The plan-time admission
  advisory (`swarmAdmissionAdvisory`, from the Gateway's
  `plan.metrics["admission_advisory"]`) renders beside the topology badge only
  on a fulfilled detail read; "not reported" — a plan that predates the
  disclosure — is its own state, never rendered as absent capacity. The swarm
  list polls only while it has nonterminal, non-paused rows; prevent
  overlapping reads and keep the polling independent of unrelated Team Ops
  panels.

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
  never presented as a complete answer. An interrupted stream is archived even
  when it carried only tool calls (no assistant prose), so a run that did tools
  but never spoke is not erased.
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
- When the first prompt commits a bot-scoped draft, the new local thread becomes
  active before Gateway admission finishes. Bot restoration must also resolve
  `botName` from current thread rows, because the local metadata ref is not yet
  populated; otherwise the UI drops specialist scope mid-turn. Coverage:
  `frontend/src/lib/bot-thread-routing.test.mjs`.
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
  the history is partial. The idempotent thread-list read may retry one network
  failure, or a 30s timeout with a 2s bounded retry; do not retry HTTP failures,
  malformed responses, or local cancellation, and keep the final failure visible.
- Subagent chat cards fold the root `task_*` custom-event stream. The Gateway's
  `task_running.message` is a structured LangChain message; project its text or
  tool-call names into the bounded card summary instead of assuming the wire
  field is a string. Keep raw messages intact for durable step receipts.
- Every bot profile exposes project creation and attaches that bot as lead via
  `projects.ts -> createProject(..., agents)`. The Projects view owns multi-bot
  membership through the real presence/attach/detach routes, then re-reads the
  confirmed roster (`confirmProjectAgents`) before any success notice — a 2xx
  attach is not proof the bots joined. Presence rows are validated, project
  conversations are paginated, and moving a conversation re-reads the parent's
  thread list through `onThreadsChanged`.
- The left rail derives its conversation badge from ChatView's loaded thread
  set; its count and empty-state copy must say when they describe that loaded
  view. Crew presence is fetched for expanded visible projects. The explicit
  selected-bot project filter may read all project rosters because membership
  is needed to include staffed projects that have no loaded conversations.
  `ProjectDetailPanel` remains the owner of a project's paginated history.

## Build discipline: `next build` and a running server cannot share `.next`

**The dev server does not use `.next` at all.** `next.config.mjs` is a
`(phase) => config` function and gives the development-server phase its own
`distDir` of `.next/dev`; every other phase (production build, production
server, export, typegen) keeps `.next`. This is not cosmetic. `next dev` deletes
everything under its `distDir` except `cache/` when it starts (in this Next
build, `dist/esm/server/dev/hot-reloader-webpack.js:581` calls `clean()`, which
is `recursiveDelete(join(dir, distDir), /^cache/)`), so a single shared
directory means **starting the dev server destroys the production build**. Two
consequences follow, both measured on this repo:

- `start.ps1:629` and `recovery/watchdog.ps1:436` gate the production path on
  `Test-Path frontend\.next\BUILD_ID`. After any `next dev` run that file is
  gone, so every boot silently falls back to `next dev` and pays its cold
  compile (measured at ~880 s) instead of serving the build.
- The next `next build` then consumes dev-written Pages Router bookkeeping and
  dies in "Collecting page data" with
  `PageNotFoundError: Cannot find module for page: /_document`.

`.next/dev` rather than a sibling such as `.next-dev` deliberately: the shipped
`.gitignore` already ignores `.next/` at any depth, so the split cannot leave an
untracked build tree behind, and `tsconfig.json` includes both `.next/types` and
`.next/dev/types`. **The production `distDir` must stay exactly `.next`** - it is
the contract the launcher and watchdog read. Coverage:
`src/lib/test_fe_audit_next_output_dirs.test.mjs`.

On Windows, Next's Webpack watcher must ignore volume-root OS files with an
absolute-path regular expression. Glob ignores alone still caused Watchpack to
`lstat` `C:\pagefile.sys`, `C:\DumpStack.log.tmp`, and
`C:\System Volume Information` during its initial scan. Keep the matcher
limited to those root-owned paths so it does not hide project files; coverage:
`src/lib/next-config-watch-ignores.test.mjs`.

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

- `components/UpdateControl.tsx` is mounted in `WorkspaceTopBar`
  (`components/chat-shell/`), the one header every view renders, so update
  state is visible without opening Settings — and is visible in the chat view
  too. It previously sat beside `WorkspaceVitals` in the per-section header,
  which only renders when `view !== "chat"`: the screen a user lives on was the
  one that never showed it. Settings → General keeps the fuller identity card;
  both read the same client. `evolution-update.test.mjs` pins the mount.
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
  are admin-only and 403 under `ALPHA_AUTH_DISABLED`. Show that
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

## Projects tab: the list and the full per-project read

`projects` is a **primary** tab, ordered `Bots -> Projects -> Board`. It is
declared once in `WORKSPACE_TABS`; the primary row and the More Views dropdown
are both derived from that list, so a second `projects` entry would render twice
and `workspace-nav.test.mjs` fails on it.

- **The list answers "which projects exist", not "what is in one".** Every
  project is listed by default; the bot filter and the crew-type filter both
  default to `all`.
- **Creating is one field.** The create card opens with a name and `Create`;
  the template, instructions and the staffing roster sit behind a settings
  disclosure whose summary line states what is staged, so a hidden crew is never
  an invisible one. Opening a project leads with its staff list, and the project
  name is editable alongside its instructions (a blank name is refused locally,
  not sent as a 422). Inside that staff block the add-agent roster is behind its
  own **Add agent** control: what is already on the project stays visible, what
  is not on it appears only when adding is the task, and a staged selection is
  still announced (and still attachable) while the list is closed.
- **Single-agent vs team crew is the server's claim, not a guess.** The crew
  service provisions a shared group room at the *second* member, so
  `members.length >= 2` is a crew and `=== 1` is solo. A roster read that is in
  flight or has failed is `unknown` — its own badge, its own count, and
  deliberately *not* folded into "single agent", which would dress a broken read
  up as a deliberate choice. `SHAPE_META` in `ProjectsSection.tsx` is the single
  place that mapping lives.
- **"View more" is a real drill-down, not another accordion row.** It swaps the
  whole surface for `ProjectInspectorSection`, which re-reads the project live
  rather than assembling a view from what the list already happened to fetch.
  The card's own expand keeps the management controls.
- **`ProjectInspectorSection` is strictly read-only.** It imports `errMsg` from
  `lib/http` and never `send`, and `src/lib/project-inspector.ts` imports only
  `get`. Every mutating per-project action already belongs to the Workforce view,
  whose project picker defaults to the *first* project — so a copied button here
  would act on a different project than the one on screen. "Live controls"
  therefore calls `onOpenLiveProject(projectId)`, which selects the project; a
  bare view switch is the defect this replaced.
- **Seventeen independent reads, seventeen independent failures.**
  `inspectProject()` never rejects. A route that 404s on an older Gateway, 403s
  on a locked-down deployment, or times out blanks exactly one block and carries
  the server's reason; the header says how many sections did not answer and names
  them. A whole-read failure is therefore never silently an empty project.
- **Honesty rules this surface is built around.** A count the server did not
  send is `null` and renders as `—`, never `0`. `last_verified: null` is "never
  verified". `kill_switch.active` is tri-state: an unreported block is grey and
  disclosed, never a green "not engaged". A member with no recorded brief is
  *absent* from `member_briefs`, not present and empty. Every timestamp goes
  through `parseTime`, because `new Date("")` is the epoch and would claim the
  event happened in 1970.
- **A project named anywhere can be opened from there.** One
  `openProjectDetails(projectId)` handler in `ChatView` records a one-shot
  `{ projectId, nonce }` request and switches to `projects`, where
  `ProjectsSection` opens the drill-down and clears the request. It is offered
  at every surface that names a project (conversation footer, chat header menu,
  sidebar group headers, rail menu, inspector header, company rows, Workforce
  pickers, the board's single-project filter) and withheld wherever there is no
  project to open.
- Regression coverage: `src/lib/project-inspector.test.mjs` (routes, verbs, the
  read-only guarantee, partial reads, and the honesty inversions),
  `src/lib/project-inspector-view.test.mjs` (tab placement and ordering, the
  crew-type classification, the drill-down wiring, and the rendered claims) and
  `src/lib/project-deep-links.test.mjs` (the cross-surface redirect).

## The project hub: a project is a folder, not a row

Opening a project has to reach *everything the project owns* — its message
rooms, its board, its shared memory, the skills of its crew, its conversations
and the company it sits under — and each of those has to lead somewhere real.
`src/lib/project-hub.ts` is the join that makes that possible, and the inspector
is where it renders.

**The Gateway has no "project hub" endpoint, and the module deliberately does
not invent one.** Every panel is a join over a read that already exists, so
there is one answer per fact rather than a second projection that drifts from
the surface it claims to summarise. Four joins, four rules:

| Join | Source read | The rule it keeps |
| --- | --- | --- |
| rooms | `GET /groups/by-project/{id}` | the room name is the server's to give; composing `project-{id}` breaks on a rename |
| company | `listCompanies()` + `getCompany()` per company | the link lives on the *company*, so attribution is a reverse lookup, and an unreadable company is **named**, never dropped |
| skills | `fetchBot()` per crew member | a skill belongs to an agent, so the union keeps *which* agent carries each one |
| board | `loadCards()` + `GET /kanban/tasks`, then narrowed | a card naming no project is never claimed by one |

Three shapes hold across all four: a failed read is never an empty answer
(every loader returns the reason, or names the rows it could not read, beside
the data it did get); counts travel with their basis (`count` is `null` when it
was not reported, never `0`); and a join claims only what it matched.

- **Three one-shot deep links, same contract as `projectDetailRequest`.**
  `ChatView` owns `projectBoardRequest` / `roomOpenRequest` /
  `companyOpenRequest`, each `{ …, nonce }`, each with a single handler
  (`openProjectBoard`, `openRoomDetail`, `openCompany`) that records it and
  switches view. The receivers (`KanbanSection`, `MessagesSection`,
  `CompanySection`) consume and clear through `onOpen…RequestHandled`, because
  a request left standing re-fires on every later mount. A company a request
  names that the registry does not carry is **reported**, not silently ignored:
  keeping the previous selection would present it as the one asked for.
- **The inspector gained a Board tab plus three panels** — rooms (with subgroups,
  each with its own Open), company, and project skills — and its crew rows now
  link to `/bots/{name}`, so an agent on a project is one click from that
  agent's full record. The board tab only *reads*: moving a card belongs to the
  Kanban view, which owns the write.
- **The project card states its company** and offers Room / Board / hub
  shortcuts. The company badge renders only when the reverse read answered — a
  failed attribution read must not paint "no company" over every card, and a
  failed scan says so once (`company partly unread`).
- **The hub row's shortcuts land, and the loop closes.** The card's **Memory**
  and **Conversations** buttons open the hub *on that tab*
  (`openInspector(projectId, tab)` → the inspector's `initialTab`); the
  **Company** button needs both a handler and an answered reverse read. The
  bot's own page reads `GET /api/bots/{name}/projects` into its per-section
  bundle and links each project back with
  `/?view=projects&project=<id>`, which `ChatView` folds into the same
  one-shot `projectDetailRequest` — a bot page is a different route, so the
  URL is the only channel there is, and one handler still decides which
  project is open. An unread project is named with its reason, and an absent
  project list is `null`, not "no projects".
- **Project status is a lifecycle label, not a health verdict.** `toProject`
  maps an absent status to `""` (it used to invent `"active"`), so
  `projectStatusText` renders "status not reported" and the one tone helper,
  `projectStatusTone`, awards green to `active` and nothing else. The in-chat
  panel's fabricated `Apr 26, 2025` and its always-emerald dot are gone; both
  are pinned in the suite.
- **A board filter that names a project the list does not carry is shown by
  its id.** `All projects` displaying while the filter held one id was a control
  and its rows disagreeing about what was on screen. A card's own project label
  opens that project too, and the room's group profile can open the project it is
  bound to.
- **Navigation is never a mutation.** The sidebar's project row bound
  `onPickProject` — `moveThread` on the active conversation — before opening a
  project's Tasks / Files / Overview / Settings / Knowledge, so browsing a
  project relocated whatever the operator was working on. It is now bound only
  to the explicit *Switch Project* action; Tasks opens the board already
  filtered to that project, Overview/Settings open the hub, Knowledge opens the
  hub's **memory** tab (which is why `projectDetailRequest` carries an optional
  `tab` — the workspace Memory view is conversation-scoped), and the row's
  *Files* link is withdrawn because the Gateway has no project-scoped files
  read to serve it.
- Regression coverage: `src/lib/project-hub.test.mjs` (routes, delegation to
  the existing clients, every honesty inversion above, the pure helpers driven
  directly, and source pins on the wiring and the removed placeholders) and
  `src/lib/project-deep-links.test.mjs` (every entry point, the URL seed, and
  the "navigation never moves the chat" pins).

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
