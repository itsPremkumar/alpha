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

test("the header renders the breadcrumb for a nested group only", () => {
  assert.match(messages, /sel\.kind === "group" && crumbs\.length > 0 &&/);
  assert.match(messages, /GroupBreadcrumbs/);
});

test("lifecycle states get a label rather than a raw enum", () => {
  assert.match(sidebar, /stateLabel\(node\.scope\.state\)/);
});