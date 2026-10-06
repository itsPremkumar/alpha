// project-inspector-view.test.mjs — the wiring of the Projects drill-down.
//
// The defect this pins, found by using the product: the Projects tab answered
// "which projects exist, and how many chats and bots does this one hold". Every
// other `GET /projects/{id}/*` route was unreachable from it, and the surfaces
// that did expose those reads (Workforce) keep their own project picker
// defaulting to the FIRST project — so "open my project, find its state" landed
// on someone else's project.
//
// The client contract (routes, honesty inversions) is
// `project-inspector.test.mjs`. These assertions are the WIRING, read from the
// real component sources:
//
//   * the Projects tab is a top-level tab, positioned where it was asked for;
//   * every project is listed, with single-agent vs team-crew distinguished from
//     the server's roster rather than guessed;
//   * "View more" opens one project's full read, not another accordion row;
//   * the drill-down is read-only and hands the live surface THIS project.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");

const nav = read("../components/NavTabs.tsx");
const views = read("../lib/workspace-view.ts");
const chatView = read("../components/ChatView.tsx");
const projectsSection = read("../components/sections/ProjectsSection.tsx");
const inspector = read("../components/sections/ProjectInspectorSection.tsx");

/** The slice of `WORKSPACE_TABS` between two markers, for ordering assertions. */
function tabBlock(startMarker, endMarker) {
  const from = nav.indexOf(startMarker);
  assert.notEqual(from, -1, `expected to find ${startMarker}`);
  const to = nav.indexOf(endMarker, from);
  assert.notEqual(to, -1, `expected to find ${endMarker} after ${startMarker}`);
  return nav.slice(from, to);
}

// ------------------------------------------------------------------- the tab

test("Projects is a top-level tab, not something behind More Views", () => {
  const tab = nav.match(/\{ id: "projects"[^\n]*\}/);
  assert.ok(tab, "projects still declares a tab");
  assert.match(
    tab[0],
    /isPrimary: true/,
    "the Projects tab must be in the primary row",
  );
  // A `projects` tab that was primary AND still listed in the dropdown would
  // render twice; the registry is the single source for both surfaces.
  assert.equal(
    (nav.match(/id: "projects"/g) || []).length,
    1,
    "projects must be declared exactly once — the primary row and the dropdown both read this list",
  );
});

test("Projects sits between the Bots and Board tabs", () => {
  // The placement that was asked for, pinned so a future reorder is a deliberate
  // change rather than an accident: Bots -> Projects -> Board.
  const primary = tabBlock('id: "bots"', 'id: "messages"');
  assert.match(primary, /id: "projects"[\s\S]*id: "kanban"/);
  const order = [
    ...nav.matchAll(
      /id: "(chat|warroom|bots|projects|kanban|messages|dashboard|settings)"/g,
    ),
  ].map((m) => m[1]);
  assert.deepEqual(
    order,
    [
      "chat",
      "warroom",
      "bots",
      "projects",
      "kanban",
      "messages",
      "dashboard",
      "settings",
    ],
    "the primary row order changed",
  );
});

test("the Projects tab is registered in all three places a view must be", () => {
  // A view id that exists in the tab bar but not the router, or vice versa, is
  // either unreachable or a dead render branch.
  assert.match(views, /"projects"/, "projects is in the workspace-view union");
  // `\s*` between the tokens: a section the formatter split across lines is the
  // same on-demand load. Pinning the single-line shape turns a `prettier` reflow
  // into a red suite, which is a formatting change reported as a regression.
  assert.match(
    chatView,
    /dynamic\(\s*\(\) =>\s*import\("@\/components\/sections\/ProjectsSection"\)/,
  );
  assert.match(chatView, /view === "projects"/);
});

// ---------------------------------------------------------------- the listing

test("the tab badge counts real projects, not the bot roster", () => {
  // The Bots tab already shows a badge from `bots.length`. Projects must show
  // its own count from the project list, so an empty installation reads 0
  // projects rather than inheriting a bot count.
  assert.match(
    chatView,
    /badge=\{\{ bots: bots\.length[^}]*projects: projects\.length/,
  );
});

test("single-agent and team-crew projects are distinguished from the roster", () => {
  // The distinction is the server's, not a guess from the name: the crew service
  // provisions a shared room at the SECOND member, so >=2 is a crew.
  assert.match(
    projectsSection,
    /type ProjectShape = "crew" \| "solo" \| "empty" \| "unknown"/,
  );
  assert.match(projectsSection, /members\.length === 1 \? "solo" : "crew"/);
  // And the unreadable case is its own state: a failed roster read must never be
  // painted as a deliberately single-agent project.
  assert.match(
    projectsSection,
    /if \(teamLoading\[projectId\] \|\| teamErrors\[projectId\]\) return "unknown"/,
  );
  assert.match(
    projectsSection,
    /unknown: \{ label: "crew unknown"/,
    "the unknown shape has its own presentation",
  );
});

test("the shape badge and filter are rendered on the list", () => {
  assert.match(
    projectsSection,
    /SHAPE_META\[shapeOf\(p\.id\)\]/,
    "each card carries its crew type",
  );
  assert.match(
    projectsSection,
    /shapeFilter/,
    "the list can be narrowed to crews or single agents",
  );
  // The filter must not hide a project whose roster failed to read; that is a
  // separate disclosed count, not a silently smaller list.
  assert.match(projectsSection, /shapeCounts\.unknown/);
  assert.match(projectsSection, /crew not reported/);
});

test("every project is listed, not only the ones a bot filter selects", () => {
  // The bot filter defaults to "all", and the shape filter defaults to "all" too,
  // so the default view is every project on the installation.
  assert.match(projectsSection, /useState<"all" \| "crew" \| "solo">\("all"\)/);
  assert.match(projectsSection, /const visibleProjects = projects\.filter/);
  // And the empty state distinguishes "no projects at all" from "your filter
  // matched nothing", which are different situations with different fixes.
  assert.match(projectsSection, /No projects yet/);
});

// ---------------------------------------------------------------- the drill-down

test("View more opens one project's full read, not another accordion row", () => {
  assert.match(projectsSection, /View more/);
  assert.match(
    projectsSection,
    /onClick=\{\(\) => setInspectedProjectId\(p\.id\)\}/,
  );
  // The inline `toggle` expands management controls; the inspector is a separate
  // surface. Reusing `toggle` would have kept the deep state unreachable.
  assert.doesNotMatch(
    projectsSection,
    /View more[^"]*"[\s\S]{0,120}void toggle\(p\)/,
  );
  // The inspector replaces the list while open, and there is a way back.
  assert.match(
    projectsSection,
    /if \(inspectedProjectId\) \{[\s\S]{0,200}ProjectInspectorSection/,
  );
  assert.match(
    projectsSection,
    /onClose=\{\(\) => setInspectedProjectId\(null\)\}/,
  );
});

test("the inspector is mounted, not dead code", () => {
  assert.match(
    projectsSection,
    /import\s*\{[^}]*ProjectInspectorSection[^}]*\}\s*from\s*"@\/components\/sections\/ProjectInspectorSection"/,
  );
});

test("the deep surface is handed the project that was opened", () => {
  // The Workforce view owns the mutating controls and keeps its own picker that
  // defaults to the first project, so "Live controls" must select this project
  // rather than merely switching views.
  assert.match(
    projectsSection,
    /onOpenLiveProject=\{props\.onOpenLiveProject\}/,
  );
  assert.match(chatView, /setFocusedProjectId\(projectId\)/);
  assert.match(
    inspector,
    /props\.onOpenLiveProject\?\.\(props\.projectId\)/,
    "the project id, not a bare view switch",
  );
});

test("the inspector covers the whole project end to end", () => {
  // Every cluster a project has, each on its own tab. A tab that silently omits
  // a subsystem is exactly the "every detail" gap this surface exists to close.
  for (const tab of [
    "overview",
    "crew",
    "conversations",
    "memory",
    "governance",
    "activity",
    "operations",
    "autonomy",
  ]) {
    assert.match(
      inspector,
      new RegExp(`id: "${tab}"`),
      `the ${tab} tab is missing`,
    );
  }
  // And the sections inside them, all fed by the one client.
  for (const piece of [
    "RecordPanel",
    "StatePanel",
    "KillSwitchPanel",
    "CrewPanel",
    "ConversationsPanel",
    "MemoryPanel",
    "ContextPanel",
    "ConstitutionPanel",
    "DecisionsPanel",
    "ContractsPanel",
    "LivingSpecPanel",
    "EventsPanel",
    "StandupPanel",
    "CostPanel",
    "LocksPanel",
    "HandoffsPanel",
    "ApprovalsPanel",
    "CheckpointsPanel",
    "RSIPanel",
    "AVOPanel",
    "EpistemicsPanel",
    "TrajectoriesPanel",
    "LeaderboardPanel",
    "CanaryPanel",
    "SelfConfigPanel",
    "MetaCompilerPanel",
    "PerpetualPanel",
  ]) {
    assert.match(
      inspector,
      new RegExp(`function ${piece}\\b`),
      `${piece} is missing`,
    );
  }
});

test("a conversation in the drill-down opens in the chat view", () => {
  assert.match(projectsSection, /onOpenThread=\{props\.onOpenThread\}/);
  assert.match(
    inspector,
    /onClick=\{\(\) => props\.onOpenThread\(t\.thread_id\)\}/,
  );
});

test("the inspector is read-only and never claims to mutate", () => {
  // It never imports `send` from lib/http, so there is no code path here that
  // could write. Mutations belong to the project card and the Workforce view.
  // `errMsg` is the only thing it takes from lib/http — never `send`, which is
  // the only write helper the transport exposes.
  assert.match(inspector, /import \{ errMsg \} from "@\/lib\/http"/);
  assert.doesNotMatch(
    inspector,
    /import \{[^}]*\bsend\b[^}]*\} from "@\/lib\/http"/,
  );
  assert.doesNotMatch(inspector, /\bsend\s*[(<]/);
  assert.doesNotMatch(inspector, /method:\s*"(POST|PATCH|PUT|DELETE)"/);
});

// ------------------------------------------------------------------ honesty

test("the inspector never paints an unread or unknown value as a healthy one", () => {
  // A count the server did not send.
  assert.match(inspector, /props\.value === null \? "—" : props\.value/);
  // `last_verified: null` is never verified.
  assert.match(inspector, /never verified/);
  // The kill switch is tri-state; an unreported state cannot wear the "off" green.
  assert.match(
    inspector,
    /active === true \? "red" : active === false \? "green" : "gray"/,
  );
  assert.match(inspector, /unknown — not a healthy/);
  // A failed section is named with the server's reason, and a partial read is
  // disclosed up front rather than presenting itself as the whole project.
  assert.match(inspector, /could not be read/);
  assert.match(inspector, /\{props\.section\.error\}/);
  assert.match(inspector, /inspection\?\.partial/);
  assert.match(inspector, /Partly unreadable/);
});

test("an absent timestamp never reaches Date, which would claim the epoch", () => {
  // `new Date("")` is 1970. Every time in the inspector goes through the shared
  // parser, which returns null for an absent value and renders "not reported".
  assert.match(inspector, /const ms = parseTime\(props\.value \?\? null\)/);
  assert.match(inspector, /time not reported/);
  assert.doesNotMatch(inspector, /new Date\(props\.value\)/);
});

test("the three room states stay distinguishable in the drill-down", () => {
  // Absent (solo), parked, and unreadable are opposite claims about the same
  // field. The client rejects on unreadable; the panel shows the other two.
  assert.match(inspector, /room\.parked === true/, "parked is its own state");
  assert.match(
    inspector,
    /No room yet — a shared room opens once this project has 2 agents/,
  );
  assert.match(
    inspector,
    /parked: not reported/,
    "an unreported parked flag is unknown, not false",
  );
});

test("a refresh in flight disables itself", () => {
  // The inspector re-reads seventeen routes; a double-click must not open two
  // reads of every one of them.
  assert.match(
    inspector,
    /const \[refreshing, setRefreshing\] = useState\(false\)/,
  );
  assert.match(
    inspector,
    /onClick=\{\(\) => void load\(\)\} disabled=\{refreshing\}/,
  );
  assert.match(inspector, /finally \{[\s\S]{0,120}setRefreshing\(false\)/);
});

test("an absent member brief or verdict is never fabricated", () => {
  // The two places a plausible-looking default would invent a fact about the
  // project's agents or its evidence.
  assert.match(inspector, /The server reported no verified flag/);
  assert.match(inspector, /regression suite not reported/);
});
