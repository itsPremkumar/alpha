/**
 * War Room view: tab provenance, per-tab read isolation, and count badges.
 *
 * These decide which of the six tabs an operator is told is real. A regression
 * here would either paint a preview tab as measured — the specific claim the
 * section's hint exists to deny — or restore the single `Promise.all` that let
 * one failed route blank every tab at once.
 *
 * Network calls are out of scope. The enterprise routes are pinned by
 * backend/tests/test_studio_routes.py.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  TAB_SECTION,
  WAR_ROOM_SECTION_KEYS,
  WAR_ROOM_TABS,
  WAR_ROOM_TAB_IDS,
  provenanceTone,
  sectionEmptyTitle,
  sectionErrorTitle,
  sectionHasData,
  tabBadge,
} from "./war-room-view.ts";

const IDS = ["coordination", "org_chart", "rfcs", "treasury", "missions", "council"];

test("the six tabs are declared once, in a stable order", () => {
  assert.deepEqual([...WAR_ROOM_TAB_IDS], IDS);
  assert.equal(WAR_ROOM_TABS.length, 6);
  // No duplicates: a repeated id would mount one tab body twice.
  assert.equal(new Set(WAR_ROOM_TAB_IDS).size, WAR_ROOM_TABS.length);
});

test("exactly one tab is measured, and it is the one that reads live execution", () => {
  const measured = WAR_ROOM_TABS.filter((t) => t.provenance === "measured");
  assert.deepEqual(
    measured.map((t) => t.id),
    ["coordination"],
  );
});

test("every tab carries a chip label and the sentence behind it", () => {
  for (const tab of WAR_ROOM_TABS) {
    assert.ok(tab.label.length > 0, `${tab.id} has no label`);
    assert.ok(tab.provenanceLabel.length > 0, `${tab.id} has no provenance label`);
    assert.ok(
      tab.provenanceHint.length > 20,
      `${tab.id} provenance hint is too short to explain anything: "${tab.provenanceHint}"`,
    );
  }
});

test("the provenance wording never calls a preview tab measured", () => {
  for (const tab of WAR_ROOM_TABS) {
    if (tab.provenance === "preview") {
      assert.equal(tab.provenanceLabel, "preview", `${tab.id} must not read as measured`);
      assert.doesNotMatch(tab.provenanceHint, /\bmeasured execution\b/i);
    }
  }
});

test("a preview chip is amber and a measured chip is blue, not green", () => {
  assert.equal(provenanceTone("preview"), "amber");
  assert.equal(provenanceTone("measured"), "blue");
  // Green would read as "healthy". Provenance is about where a number came
  // from, which says nothing about whether the system is well.
  assert.notEqual(provenanceTone("measured"), "green");
});

test("every tab names the read it renders from, and only the live one names none", () => {
  const withSection = IDS.filter((id) => TAB_SECTION[id] !== null);
  assert.deepEqual(withSection, ["org_chart", "rfcs", "treasury", "missions", "council"]);
  assert.equal(TAB_SECTION.coordination, null);
});

test("every declared section key is one of the six independent reads", () => {
  const declared = new Set(WAR_ROOM_SECTION_KEYS);
  for (const id of IDS) {
    const section = TAB_SECTION[id];
    if (section !== null) assert.ok(declared.has(section), `${id} maps to unknown section ${section}`);
  }
  // The five preview tabs cover five distinct reads: two tabs sharing one
  // section would mean one failure blanks both, which is what this isolates.
  const sections = IDS.map((id) => TAB_SECTION[id]).filter((s) => s !== null);
  assert.equal(new Set(sections).size, sections.length);
});

test("a tab badge is absent when the server sent no count — never a zero", () => {
  assert.equal(tabBadge("rfcs", null), null);
  assert.equal(tabBadge("rfcs", {}), null);
  assert.equal(tabBadge("rfcs", { active_rfcs_count: null }), null);
  assert.equal(tabBadge("org_chart", { departments_count: null }), null);
  assert.equal(tabBadge("missions", { active_sprints_count: undefined }), null);
});

test("a tab that carries no count reports none rather than borrowing another's", () => {
  // Treasury and council have no declared count. Returning a number here
  // would label them with a figure that describes a different tab.
  assert.equal(tabBadge("treasury", { active_rfcs_count: 9 }), null);
  assert.equal(tabBadge("council", { active_rfcs_count: 9 }), null);
  assert.equal(tabBadge("coordination", { active_rfcs_count: 9 }), null);
});

test("a real count renders as sent, including zero", () => {
  // A measured zero is a fact: the server said there are no RFCs. This is the
  // opposite of the absent case above and must still render.
  assert.equal(tabBadge("rfcs", { active_rfcs_count: 0 }), "0");
  assert.equal(tabBadge("rfcs", { active_rfcs_count: 7 }), "7");
  assert.equal(tabBadge("org_chart", { departments_count: 5 }), "5");
  assert.equal(tabBadge("missions", { active_sprints_count: 1 }), "1");
});

test("a non-finite count is not a count", () => {
  assert.equal(tabBadge("rfcs", { active_rfcs_count: Number.NaN }), null);
  assert.equal(tabBadge("rfcs", { active_rfcs_count: Number.POSITIVE_INFINITY }), null);
  assert.equal(tabBadge("rfcs", { active_rfcs_count: "2" }), null);
});

test("a failed read and an empty read are named differently", () => {
  for (const section of WAR_ROOM_SECTION_KEYS) {
    const failed = sectionErrorTitle(section);
    const empty = sectionEmptyTitle(section);
    assert.notEqual(failed, empty, `${section}: a failure must not read as an empty room`);
    assert.match(failed, /could not be read/i);
    assert.ok(empty.length > 0);
  }
});

test("an unknown section key still gets a usable title", () => {
  // A tab added without a case must not render an empty header, which would
  // leave an error box with no explanation of what failed.
  const title = sectionErrorTitle("something_new");
  assert.ok(title.length > 0);
  assert.match(title, /could not be read/i);
  assert.ok(sectionEmptyTitle("something_new").length > 0);
});

// ── "Is there anything to show?" ────────────────────────────────────────────────
//
// The question the treasury tab used to answer silently: its body was gated on
// `treasury &&`, so a null treasury produced no error, no empty state and no
// skeleton. Three states collapsed into one blank panel.

const FULL_DATA = {
  telemetry: { heartbeat_cycle: 3 },
  hierarchy: { csuite: [{ node_id: "a" }], departments: [{ dept_id: "d1" }] },
  rfcs: [{ rfc_id: "r1" }],
  treasury: { total_allocated_tokens: 1 },
  sprints: [{ sprint_id: "s1" }],
  releases: [{ release_id: "rel1" }],
};

test("a section with data reports it, for every section", () => {
  for (const section of WAR_ROOM_SECTION_KEYS) {
    assert.equal(sectionHasData(section, FULL_DATA), true, `${section} should have data`);
  }
});

test("a section that never arrived reports no data", () => {
  for (const section of WAR_ROOM_SECTION_KEYS) {
    assert.equal(sectionHasData(section, {}), false, `${section} with nothing sent must be false`);
  }
});

test("an absent treasury is no data, not a zero treasury", () => {
  assert.equal(sectionHasData("treasury", { treasury: null }), false);
  // `0` is not a shape this section takes, and an object is.
  assert.equal(sectionHasData("treasury", { treasury: {} }), true);
});

test("empty arrays are nothing to show, for every list-backed section", () => {
  // A 200 that returned zero rows is genuinely empty, and must reach the
  // empty-state wording rather than rendering a body full of empty headers.
  assert.equal(sectionHasData("rfcs", { rfcs: [] }), false);
  assert.equal(sectionHasData("missions", { sprints: [] }), false);
  assert.equal(sectionHasData("council", { releases: [] }), false);
});

test("a hierarchy the server sent with nobody in it is nothing published", () => {
  assert.equal(sectionHasData("hierarchy", { hierarchy: { csuite: [], departments: [] } }), false);
  assert.equal(sectionHasData("hierarchy", { hierarchy: { csuite: [{ node_id: "a" }], departments: [] } }), true);
  assert.equal(sectionHasData("hierarchy", { hierarchy: { csuite: [], departments: [{ dept_id: "d" }] } }), true);
  assert.equal(sectionHasData("hierarchy", { hierarchy: null }), false);
});

test("an unknown section key reports no data rather than throwing", () => {
  assert.equal(sectionHasData("something_new", FULL_DATA), false);
});
