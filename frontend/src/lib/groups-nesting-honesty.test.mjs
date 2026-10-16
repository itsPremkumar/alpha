// Source-level honesty pins for the nested-group surfaces.
//
// `groups-tree.test.mjs` covers the pure derivation. This file covers the two
// React surfaces, where the failure mode is different: a control that renders a
// claim the server never made.
//
// The four defects these pin:
//   * a busy dot over a roster nobody asked about;
//   * a membership count shown without its counterpart;
//   * a subgroup form that silently inherits the parent's roster;
//   * a delete that removes a subtree without saying so.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function read(path) {
  return readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
}
const sidebar = read("components/sections/GroupTreeSidebar.tsx");
const messages = read("components/sections/MessagesSection.tsx");

test("the sidebar renders a busy dot only for a measured count", () => {
  // `busyCount` is null until presence was read for that room.
  assert.match(sidebar, /entry\.busyCount !== null && entry\.busyCount > 0/,
    "the dot must be gated on a measured count, not merely a present field");
  assert.match(sidebar, /bg-emerald-500/,
    "the measured dot stays the working colour");
});

test("the sidebar never treats a null busy count as zero working", () => {
  assert.doesNotMatch(sidebar, /busyCount > 0\) \? .* : "0 working"/,
    "an unread roster is not a measured zero");
});

test("the sidebar shows both membership counts or neither", () => {
  assert.match(sidebar, /node\.direct_count !== null && node\.effective_count !== null/,
    "one count standing in for the other fabricates membership");
  assert.match(sidebar, /\$\{node\.direct_count\}\/\$\{node\.effective_count\}/,
    "when they differ, both are rendered");
  assert.match(sidebar, /added by hand, .* visible in total/,
    "the tooltip says where the difference comes from");
});

test("a subgroup read failure is disclosed on the node", () => {
  assert.match(sidebar, /Subgroups could not be read/,
    "silently rendering no subgroups claims the group has none");
  assert.match(sidebar, /props\.error/);
});

test("a subgroup says how many groups are inside it", () => {
  assert.match(sidebar, /group\{subtree === 1 \? "" : "s"\} inside/);
});

test("creating a subgroup discloses that it inherits the parent roster", () => {
  // A silent copy of the parent's members is exactly the kind of behaviour an
  // operator has to see before it happens.
  assert.match(sidebar, /inherit/);
  assert.match(sidebar, /Start with .* members/);
  assert.match(sidebar, /Later additions to the\s*\n?\s*parent are inherited/,
    "the difference between copying now and inheriting later must be stated");
});

test("the breadcrumb renders the whole ancestor chain", () => {
  assert.match(sidebar, /aria-label="Group location"/);
  assert.match(sidebar, /props\.chain\.map/);
});

test("the details pane lists the groups nested under the open room", () => {
  assert.match(messages, /Groups inside this one/);
  assert.match(messages, /No groups inside this one yet/);
  assert.match(messages, /subgroup\{subtree === 1 \? "" : "s"\}|Add a group inside/,
    "the empty state has to say how to add one");
});

test("the details pane reports both counts in one headline", () => {
  assert.match(messages, /membershipHeadline\(activeRoster, activeRosterError\)/);
});

test("the roster panel keeps direct, inherited and rule-matched separate", () => {
  // Flattening them makes an inherited member look hand-added, which is how a
  // nested roster stops making sense.
  assert.match(messages, /INHERITED \(/);
  assert.match(messages, /RULE MATCHED \(/);
  assert.match(messages, /EXCLUDED \(/);
  assert.match(messages, /EXPIRED \(/);
});

test("an inherited member is labelled with the group it came from", () => {
  assert.match(messages, /via \$\{b\.via\}/,
    "'inherited' without a source is not actionable");
});

test("a roster read failure is not rendered as an empty membership", () => {
  assert.match(messages, /who is here is unknown, not empty/);
});

test("the forest read failure is surfaced rather than leaving every group a leaf", () => {
  assert.match(messages, /Group structure unavailable/);
  assert.match(messages, /could not be read, not empty/);
});

test("group rooms are listed once — one lens at a time, not a second listing", () => {
  // The sidebar used to render every room twice: a "Group structure" card in
  // the header above a flat Groups list. The section is now a tabbed surface
  // (list / tree / working) where exactly one lens renders, so a second
  // <GroupTreeSidebar> would be a second listing again.
  assert.equal(
    messages.split("<GroupTreeSidebar").length - 1,
    1,
    "exactly one group listing in source",
  );
  assert.match(
    messages,
    /section\.key === "groups" \? \(/,
    "the lens lives inside the conversation list's Groups section",
  );
  assert.match(
    messages,
    /groupsTab === "tree" \? \(/,
    "the tree renders only behind its tab",
  );
});

test("the GROUPS section is a tabbed surface, and the list is its default lens", () => {
  assert.match(
    messages,
    /useState<GroupsTab>\("all"\)/,
    "the flat list with live bots is the default view",
  );
  assert.match(messages, /role="tablist"/);
  assert.match(messages, /aria-label="Group views"/);
  assert.match(messages, /aria-selected=\{groupsTab === t\.key\}/);
  assert.match(messages, /groupsTab === "working" \? \(/);
});

test("every group row's roster is read, not just the open room's", () => {
  // The list states "who is in this group, and is any of them working" for
  // every row, and a claim needs a read: the light presence path runs for
  // every room on each poll (`load()` produces a fresh `rooms` array).
  const slice = messages.slice(
    messages.indexOf("const loadPresence"),
    messages.indexOf("const loadMembers"),
  );
  assert.match(slice, /listRoomMembers\(name\)/);
  assert.match(
    messages,
    /for \(const r of rooms\) void loadPresence\(r\.name\);/,
  );
});

test("the working lens counts only measured presence, and says what it missed", () => {
  assert.match(
    messages,
    /const working = rows\.filter\(\(c\) => groupBusyCount\(c\.id\) > 0\)/,
  );
  assert.match(messages, /No bots are working right now/);
  assert.match(messages, /Presence was not read for/);
  assert.match(
    messages,
    /nothing unmeasured is shown as working/,
    "an unread roster is never dressed up as a quiet one",
  );
});

test("a group row carries its members' measured presence, honestly", () => {
  assert.match(
    messages,
    /presenceDotClass\(entry\?\.state \?\? null\)/,
    "the dot comes from the shared vocabulary",
  );
  assert.match(
    messages,
    /presence not read/,
    "hover says why a dot is neutral",
  );
  assert.match(messages, /state not reported/);
});

test("the list rows and the roster panel share one presence vocabulary", () => {
  assert.match(messages, /function presenceDotClass/);
  assert.match(messages, /const dot = presenceDotClass;/);
});

test("the embedded tree drops its second title", () => {
  // Under the sticky GROUPS header, the component's own "Group structure"
  // caption would be a second name for one list.
  assert.match(sidebar, /!props\.embedded &&/);
  assert.match(messages, /embedded\s+nodes=/);
});

test("an unread room carries the shared label on its tree row", () => {
  assert.match(sidebar, /unreadByRoom/);
  assert.match(
    sidebar,
    /props\.unread > 0 &&/,
    "a room whose history was never read shows no badge — not a measured zero",
  );
  assert.match(sidebar, /\{unreadLabel\(props\.unread\)\}/);
});

test("a partial merge reports the counts the server returned", () => {
  // "Merged 3 of 4" — never a bare success for a merge that skipped one.
  assert.match(messages, /Merged \$\{merged\} of \$\{total\} subgroups/);
  assert.match(messages, /has no subgroups to merge/);
});

test("the add-a-group-inside control is disabled with a reason when no group is open", () => {
  assert.match(messages, /Add group inside/);
  assert.match(messages, /Open a group first/,
    "a disabled control must say what would enable it");
});

test("the subgroup form only opens for an open group", () => {
  assert.match(messages, /subgroupFor && \(/);
  assert.match(messages, /NewSubgroupForm/);
});

test("group creation renders inside the Groups section, never the header", () => {
  // The header used to carry the creation controls above the list; both act on
  // the groups listing, so they render from the Groups section now. The only
  // other call site is the "No chats yet" empty state, which is unreachable
  // while a Groups section is on screen.
  const sectionAt = messages.indexOf('section.key === "groups" ? (');
  assert.ok(sectionAt > -1, "the Groups section marker moved");
  assert.equal(
    messages.split("renderGroupControls()").length - 1,
    2,
    "one call site in the Groups section, one in the empty state",
  );
  assert.ok(
    messages.indexOf("renderGroupControls()", sectionAt) > sectionAt,
    "the Groups section renders the controls",
  );
  assert.equal(
    messages.split("Add group inside").length - 1,
    1,
    "one control, rendered once",
  );
  assert.equal(messages.split("<NewSubgroupForm").length - 1, 1);
});

test("the creation controls are rows of the list, with the group icon", () => {
  const slice = messages.slice(
    messages.indexOf("const renderGroupControls"),
    messages.indexOf("const groupTabs"),
  );
  assert.match(
    slice,
    /<Avatar\s+name="__new_group__"\s+group\s+size="lg">/,
    "the first row wears the same group glyph every group row wears",
  );
  assert.match(
    slice,
    /w-full flex items-center gap-3 px-3 py-2\.5 text-left border-b/,
    "creation rows share the conversation-row shape",
  );
});

test("the no-chats empty state can still create the first group", () => {
  // The Groups section is omitted while it is empty, so the creation controls
  // must not depend on it existing — otherwise a fresh workspace could never
  // create its first group.
  const emptyAt = messages.indexOf("Create a group to get started");
  assert.ok(emptyAt > -1, "the empty-state hint moved");
  assert.ok(
    messages.indexOf("renderGroupControls()", emptyAt) > emptyAt,
    "the empty state offers the controls",
  );
});

test("the header renders the breadcrumb for a nested group only", () => {
  assert.match(messages, /sel\.kind === "group" && crumbs\.length > 0 &&/);
  assert.match(messages, /GroupBreadcrumbs/);
});

test("lifecycle states get a label rather than a raw enum", () => {
  assert.match(sidebar, /stateLabel\(node\.scope\.state\)/);
});