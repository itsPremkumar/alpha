# Frontend UI/UX — full detail plan

Owner: operator-driven. This is the reviewable plan for bringing every Alpha
surface to one understandable structure with the backend's real detail visible.

**Baseline measured on 2026-10-09.** Counts are from the repository, not
estimates: 36 workspace views (`WORKSPACE_TABS` in `frontend/src/components/
NavTabs.tsx`), 51 section components (125 – 2,690 lines), 69 Gateway routers
under `backend/app/gateway/routers/`, and 116 typed clients in
`frontend/src/lib/`.

## 1. The finding, and why it changes the order of work

The obvious hypothesis was "planes are unwired" — that the UI is thin because
the clients do not exist. That is **not** what the code shows. Twenty-six
routers have a directly-named client, and the rest are covered under different
names (`war_rooms` → `lib/war-room.ts`, `swarms` → `lib/teamops.ts`,
`checkpoints` → `lib/workflows.ts`, `plan_mode` → `lib/plan.ts`).

What is actually true, and what this plan fixes three things:

1. **No shared page skeleton.** Fifty-one sections written independently, each
   with its own header, its own empty state and its own way of showing a
   number. A user relearns the interface on every tab, and there is no place
   where "where am I / what can I do here" has a stable answer.
2. **Density and hierarchy vary by an order of magnitude.**
   `OverviewSection` is 321 lines; `MessagesSection` is 2,690. Thin surfaces
   under-report what the backend returns; dense ones bury a field four levels
   deep with no stable path back to it.
3. **Rich payloads are rendered as summaries.** The War Room analytics block
   already reports `by_status`, `by_strategy`, `tainted_runs`,
   `stages_with_dissent`, `total_agreeing_members` and `gap_free`; almost none
   of it is visible without reading the API. The company plane exposes KPIs,
   `triggered_corrective_tasks`, heartbeats, `should_sleep` and
   `discovered_bots_count` the same way.

So the value is in **one structure**, **the fields that already exist**, and
**a navigable 36-view map** — not in a pixel-level redesign.

## 2. Decisions already taken (do not revisit without the operator)

- **Start with the chat answer experience**, then Messages, then Bots.
- **Structure first, restyle in step.** Every surface receives the new
  skeleton and hierarchy before it is restyled; nothing is left half-done.
- The plan lives here, under `docs/`, and is updated as worklands.

## 3. Workstream 0 — the design contract

Must land before any surface is touched, or 51 files get 51 treatments.

- **`<ViewPage>` primitive** in `components/ui.tsx`. Every view renders:
  header (title, one-line purpose, measured count, actions) → section blocks →
  explicit loading / empty / unavailable states.
- **State kit**, five shared components replacing the local copies:
  `LoadingRows`, `UnavailableNotice` (carries the server's reason),
  `MeasuredNumber` (`null` renders "not reported", never `0`),
  `NullDisclosure`, `ActionBar`.
- **One tone table**, generalising `lib/freeCatalogTone.ts`, so a badge
  colour cannot disagree with its meaning.
- **One density scale** (`text-[10px]`, `text-[11px]`, `xs`, `sm`) matched to
  neighbours instead of invented per file.
- **A honesty lint** over `src/components/**` so a new surface cannot
  reintroduce a fabricated zero, a swallowed error or an optimistic success.

Honesty rules that are load-bearing and stay:
absent optional value maps to `null`, never `0` / `""` / `false`; server
enum strings are preserved verbatim; a failed request surfaces its reason and
never becomes an empty list; a mutation never paints its own answer; and
independent subsystems are read independently so one failure cannot blank the
ones that answered.

## 4. Workstreams

**A. Shell and navigation** — `chat-shell/WorkspaceTopBar.tsx` (222),
`WorkspaceVitals.tsx` (591), `chat-shell/Honest.tsx` (212), `NavTabs.tsx`.
Group vitals into cards with the reason beside every reading; make the 36-tab
nav searchable and grouped, exposing the blurbs `NavTabs.tsx` already carries;
command palette over every view.

**B. Chat answer experience** — `ChatView.tsx` (4,033), `MessageItem.tsx`
(526), `ToolGroup.tsx` (118), `ActivityStatus.tsx` (121), `SubagentList.tsx`
(120), `sections/RunUsagePanel.tsx` (170).
The screen the user lives on. Per-turn cost and token block; collapsible tool
receipts grouped by phase; subagent delegation tree; file-change and diff
receipts per turn; copy and export per turn; and a per-turn "what did this
cost, what did it touch" summary drawn from the run payload that already
exists.

**C. War Room** — `sections/WarRoomSection.tsx` (1,033),
`sections/WarRoomRunsSection.tsx` (585).
Render the analytics that already exist as charts, a transcript with its
`gap_free` disclosure, strategy comparison, and dissent highlighting.

**D. Bots and agents** — `components/bots/BotGallery.tsx`,
`BotDetailView.tsx` (382), `BotDetailPanel.tsx`, `BotProfileCard.tsx`,
`FleetHealthBar.tsx` (63), `BotModelConfigPanel.tsx` (709).
One bot profile page carrying the full record, the model-config precedence
ladder visualised, and fleet health as a strip.

**E. Messages** — `sections/MessagesSection.tsx` (2,690).
Group/DM split with stable run grouping, unread pills that agree with their
filter, and a decisions/blockers filter that names the rooms it could not
search. The derivations already exist in `lib/messages-view.ts`.

**F. Projects, Companies and Board** — `sections/ProjectsSection.tsx` (902),
`ProjectInspectorSection.tsx` (1,651), `CompanySection.tsx` (1,027),
`KanbanSection.tsx` (1,030).
Surface KPIs, `triggered_corrective_tasks`, heartbeats, `should_sleep`,
retrospective, kanban sync and budget — all already returned.

**G. Autonomy and system planes** — Supervisor, Sentinel, Effects, APEX,
Intelligence, Integration, Forge, Workflows, Skills, Subagents, Memory,
Scheduled, Files, Runs, Run inspector.
Already the deepest surfaces; work here is reduction and hierarchy, plus one
cross-plane "needs attention" triage.

**H. Lift the thin surfaces** — `SkillsSection` (125), `RunUsagePanel` (170),
`ReliabilitySection` (183), `AgentsSection` (184), `FilesSection` (209),
`ChannelsSection` (219), `ScheduledSection` (236), `DashboardSection` (258),
`BotOpsSection` (282), `OverviewSection` (321).
Bring each to the Workstream-0 skeleton with real detail; these hold the
largest availability gaps.

## 5. Verification

- Per change: `node node_modules/typescript\bin\tsc --noEmit` = 0 errors and
  `node --test src/lib/*.test.mjs` green. Baseline is **2237 pass / 1 fail** —
  the single failure is the known `qr-decode` round-trip, which is unrelated
  and is expected until that plane is enabled.
- One screenshot per view under `docs/audits/screenshots/<date>/`, with a
  caption naming what it proves. The review-pane browser reaches the dev
  server, so this is observed evidence rather than a claim.
- Tests land in the same change set as the behaviour they pin. No pin is
  deleted.

## 6. Order of execution

1. **Phase 1 — contract, then the chat answer experience.** Workstream 0,
   then B, then E, then D.
2. **Phase 2 — depth on the heavy planes.** C, F, G.
3. **Phase 3 — thin surfaces and the global sweep.** H, honesty lint, empty
   states, keyboard paths, reduced motion.

## 7. Scope guards

- No mocks, fakes or hardcoded demo data; an unavailable capability is a
  stated limitation.
- A backend field is rendered only after the router's response has been read.
  Anything unread stays out of the UI rather than being guessed.
- Additive only: no file deletions, explicit-path commits, and `--no-verify`
  for frontend commits.
- `next build` never runs while a server is up; both write `.next/` and the
  failure modes that follow are misleading. The dev server keeps its own
  `.next/dev` via `next.config.mjs`.
