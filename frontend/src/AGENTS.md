# Frontend source guide (`frontend/src/`)

Scope: application source under `frontend/src/`. The parent
[`frontend/AGENTS.md`](../AGENTS.md) owns product-wide UI honesty, client and
branding rules; this guide owns source layout, data flow and surface ownership.
Read the nearest feature test before changing a behavior it pins.

## Layout

- `app/` — Next.js App Router shell, route handlers and metadata.
- `components/ChatView.tsx` — workspace router, chat transcript, composer and
  run-stream integration.
- `components/sections/` — workspace feature views, lazy-loaded by `ChatView`.
- `components/ui.tsx` — shared visual primitives and input styles.
- `lib/` — typed API clients, pure projections and stream/state helpers.
- `content/` — English documentation-site content; `types/` — shared TS types.

## Lion companion boundary

`components/lion-pet/` owns the companion's SVG rig, action registry, settings,
lifecycle, bounded local travel and CSS. `ChatView` passes only bounded
lifecycle state and an optional chat action; never prompt text or thread IDs.
The top bar, chat header, composer, transcript, project inspector, sidebar
footer and dialogs are keep-out regions. The transcript and inspector are
protected because the companion otherwise covers readable answers and project
controls; when no safe slot remains, the component hides until the layout has
room. Placement must escape those regions when a safe position exists, and
resize observation must track every marked region and dialog as the DOM changes
while ignoring companion-owned mutations. The clickable hit area must stay
inside the measured shell at responsive widths; CSS scaling and placement
geometry must agree. The compatibility exports
`components/LionPet.tsx` and `lib/lion-pet.ts` stay facades. Tests:
`lib/lion-pet.test.mjs`.

## Adding a backend surface to the UI

1. Add `lib/<surface>.ts` using shared `get`/`send` transport; preserve the
   Gateway envelope, owner scope, bounds, unknown enum values and nulls.
2. Add `lib/<surface>.test.mjs` pinning exact route/method, mapping and failure
   honesty.
3. Add `components/sections/<Surface>Section.tsx` with shared UI primitives.
4. Add its workspace view id in the `WorkspaceView` union, `WORKSPACE_TABS`,
   `WORKSPACE_VIEW_IDS`, and the `ChatView` lazy import/render switch. Add a
   wiring test so an unmounted section cannot appear implemented.
5. Show loading, unavailable, empty and error states distinctly. Render a write
   as applied only after the server confirms it; after mutations, re-read the
   authoritative view. Keep independent reads independently reportable.
6. Document behavior in the relevant feature guide and update both client and
   component tests. `pnpm test` only globs `src/lib/*.test.mjs`; suites elsewhere
   need an explicit package script and CI step.

### Data Flow

A submitted prompt goes through `ChatView`'s request builder and the shared
stream consumer in `lib/chat-stream.ts`; run status, tool receipts and subagent
progress derive from Gateway frames. Conversation history merges the current
server page with the browser archive in `lib/history-store.ts`. Backend data is
never reconstructed from model prose when a typed response or event exists.

- A first prompt owns admission: selecting New Chat, a bot or a project is only
  a local draft. Do not create a server thread until the user submits a prompt,
  command or attachment.
- Thread, bot and project scope must survive navigation and restore. Run-scoped
  frames may update their own archive but must not overwrite another active
  thread's transcript, usage, suggestions or draft.
- Stream modes must include the event types consumed by the view. Keep
  root/subgraph namespaces distinct. A missing tool result is pending/unknown,
  not a successful operation.
- Client helpers reject non-2xx responses with the Gateway's reason. Components
  keep prior measured data visible on refresh failure and identify which read
  failed.

Coverage: `lib/chat-stream.test.mjs`, `lib/history-store.test.mjs`,
`lib/bot-thread-routing.test.mjs`, `lib/chat-request-error.test.mjs`,
`lib/tool-status-honesty.test.mjs` and `lib/subagent-events.test.mjs`.

## Surface ownership and verification map

The table is an index, not a second copy of each contract. Read the named client,
projection and test before changing the surface; the test is the executable
source for exact route mappings and honest display states.

| UI surface | Owner | Focused verification |
| --- | --- | --- |
| Reasoning picker / model catalog | `ChatView`, `lib/models.ts`, `components/ReasoningEffortPicker.tsx` | `frontend/tests/reasoning-effort.test.mjs`, `lib/freeModels.test.mjs` |
| Free provider health menu | `components/FreeCatalogMenu.tsx`, `lib/freeCatalogView.ts` | `lib/free-catalog-view.test.mjs` |
| Live activity / tool receipts / swarm rows | `components/ActivityStatus.tsx`, `ToolGroup.tsx`, `SubagentList.tsx`, `components/sections/DelegatedWorkPanel.tsx`, `lib/activity.ts` | `lib/activity.test.mjs`, `lib/subagent-events.test.mjs`, `lib/delegated-work-panel.test.mjs`, `lib/tool-status-honesty.test.mjs` |
| Bot roster, liveness and work | `components/bots/`, `lib/bots.ts`, `lib/bot-working-status.ts` | `lib/bots-activity-client.test.mjs`, `lib/bot-working-status.test.mjs`, `lib/bot-working-status-view.test.mjs` |
| Network status and reconnect | workspace strip and chat network bubble | `lib/network*.test.mjs`, `lib/chat-stream.test.mjs` |
| APEX panel and composer control | `components/sections/ApexSection.tsx`, `ApexModePicker.tsx`, `lib/apex.ts` | `lib/apex.test.mjs` |
| Intelligence control plane | `components/sections/IntelligenceSection.tsx`, `lib/intelligence.ts` | `lib/intelligence.test.mjs` |
| Sentinel repair and reports | `components/sections/SentinelSection.tsx`, `lib/sentinel.ts` | `lib/sentinel.test.mjs`, `lib/sentinel-view.test.mjs` |
| Side-effect journal | `components/sections/EffectsSection.tsx`, `lib/side-effects.ts` | `lib/side-effects.test.mjs`, `lib/effects-view.test.mjs` |
| Completion gates | `components/sections/CompletionSection.tsx`, `lib/completion.ts` | `lib/completion.test.mjs`, `lib/completion-view.test.mjs` |
| Mod kernel | `components/sections/ModsSection.tsx`, `lib/mods.ts` | `lib/mods.test.mjs`, `lib/mods-view.test.mjs` |
| Memory overview, extraction health and L1 capture status | `components/sections/MemorySection.tsx`, `lib/memory.ts` | `lib/memory.test.mjs` |
| Specialist catalog and orchestration controls | subagent catalog, composer selectors, Team Ops swarm DAG, per-task acceptance and receipt-citation audit display | `lib/subagent-catalog-view.test.mjs`, `lib/swarm-structure-view.test.mjs`, `lib/swarm-structure-wiring.test.mjs` |
| Delegation scenario catalogue + advisory plan preview | `lib/subagent-scenarios.ts`, `components/DelegationPlanPreview.tsx`, mounted in `Composer` beside the delegation controls | `lib/subagent-scenarios.test.mjs` |
| Swarm mode on/off control | `lib/swarm-preference.ts`, `components/SwarmModeControl.tsx`, mounted in `Composer` beside the delegation controls | `lib/swarm-preference.test.mjs` |
| Chat-side coordinator-swarm board (read-only monitor) | `lib/swarm-run-board.ts`, `components/SwarmRunBoard.tsx`, mounted in `ChatView` beneath the delegation rows; lifecycle controls stay in Team Ops | `lib/swarm-run-board.test.mjs` |
| Projects and group tree | project/group section clients and views | matching `lib/projects*.test.mjs`, `lib/groups*.test.mjs` |
| Alpha peer network | peer client and network panel | `lib/peer-network*.test.mjs`, `lib/qr-decode.test.mjs` |
| Message projections and tags | `lib/messages-view.ts`, tag palette | `lib/messages-view.test.mjs`; tag editing is covered by the composer/UI suite. |
| Autonomous command receipts | `components/MessageItem.tsx` renders the Gateway's command status/output; `types/chat.ts` owns the response contract | `lib/autonomous-command-display.test.mjs` |
| Conversation goal strip and details | `components/ConversationGoalPanel.tsx` renders the saved objective and Gateway-reported status, including the `loading` strip and `budgetPairLabel` projection; `ChatView` owns goal reads and writes | `lib/conversation-goal.test.mjs`, `lib/goal-view.test.mjs`, `lib/threads-ext.test.mjs` |
| Memory status and extraction health | `lib/memory.ts` → `fetchMemoryStatus`; `components/sections/MemorySection.tsx` renders the on/off badge, the extraction-health badge and `MemoryHealthCard` | `lib/memory-status.test.mjs` |

Where a focused test filename changes, update this index in the same edit; do
not replace an absent suite name with a broad `pnpm test` claim.

## View wiring and floating panels

Workspace view identity is defined in four places: the `WorkspaceView` union,
`WORKSPACE_TABS`, `WORKSPACE_VIEW_IDS`, and `ChatView`'s lazy import plus render
case. Keep one stable id across all four and retain the deliberate ordering
pinned by `workspace-nav.test.mjs` and the per-view wiring tests.

Dropdowns and menus near clipped/scrolling navigation must be portalled and
positioned using `lib/workspace-menu-geometry.ts`; test clipping, viewport
edges, outside-click and Escape behavior. Do not introduce a second geometry
implementation beside the shared helper.

## Test and typecheck commands

Run from `frontend/`:

```bash
pnpm typecheck
pnpm test
```

`pnpm test` runs only `src/lib/*.test.mjs`. Use the package's explicit
`test:extra`, `test:branding`, or `verify` scripts when the change reaches those
surfaces, and ensure the corresponding CI job runs any new suite. No test is a
substitute for exercising the affected UI and API path end to end.
`pnpm typecheck` disables TypeScript incremental state after Next route type
generation so stale `.tsbuildinfo` entries cannot turn deleted `.next/dev`
route files into false missing-file errors.

## Bot detail route (`/bots/[name]`)

`lib/bot-detail.ts` and `components/bots/BotDetailView.tsx` provide the bot
operator view. Keep the roster read in `lib/bots.ts` separate from per-bot
reads; preserve independent loading/error states so one unavailable endpoint
does not erase unrelated bot data. Mutations must show server-confirmed results.
The detail contract is covered by `lib/bot-detail.test.mjs` and
`lib/bot-detail-view.test.mjs`; Gateway company/group projections are covered
by `backend/tests/test_bot_companies_route.py` and
`backend/tests/test_bot_groups_route.py`.

Bot-to-project rows link with `/?view=projects&project=<id>` so the project
surface opens the matching inspector after hydration. Keep that one-shot URL
request in `ChatView` and its cross-surface coverage in
`lib/project-deep-links.test.mjs`.
