/**
 * Source-level honesty pins for the group activity panel.
 *
 * These read the component source rather than calling it, because the failure
 * this guards is *rendering*, not returning: a derivation that maps a missing
 * value to `0` can be unit-tested, but a panel that hides a crashed agent's row
 * or paints `unresponsive` the same red as `crashed` cannot be caught by
 * testing the pure functions it calls.
 *
 * Modelled on `groups-nesting-honesty.test.mjs`.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const panel = readFileSync(
  fileURLToPath(new URL("../components/sections/GroupActivityPanel.tsx", import.meta.url)),
  "utf-8",
);
const section = readFileSync(
  fileURLToPath(new URL("../components/sections/MessagesSection.tsx", import.meta.url)),
  "utf-8",
);

test("crashed and unresponsive are not painted the same colour", () => {
  // The whole point of the feature. Both red would mean an agent inside a long
  // tool call reads as dead, which is the original lie one layer up.
  assert.match(panel, /bad:\s*"bg-red-500"/);
  assert.match(panel, /warn:\s*"bg-amber-500"/);
  // `bad` appears only for a tone the server resolved as a confirmed crash.
  const toneRows = panel.split("\n").filter((l) => /^\s*(busy|ok|warn|bad|off|unknown):/.test(l));
  assert.ok(toneRows.length >= 6, "every declared tone should have one class");
});

test("a crashed agent's row is never conditionally hidden", () => {
  // The user's requirement: the work must not disappear with the agent.
  assert.doesNotMatch(panel, /crashed\s*&&\s*[^?]*return null/);
  assert.doesNotMatch(panel, /activity\s*===\s*"crashed"[^?]*\?\s*null/);
  assert.match(panel, /const crashed = agent\.activity === "crashed"/);
});

test("a failed read names its reason and never reads as an empty room", () => {
  assert.match(panel, /Activity could not be read/);
  assert.match(panel, /unknown, not empty/);
});

test("an unread snapshot renders as reading, not as nobody working", () => {
  assert.match(panel, /const pending = snapshot === null && !error/);
  assert.match(panel, /Reading who is working/);
});

test("an unrecognised state word renders verbatim with a disclosure", () => {
  assert.match(panel, /not a state this build knows/);
  assert.match(panel, /isKnownActivity\(activity\) \? activity/);
});

test("the refresh control is disabled while in flight", () => {
  // A double-click must not become two reads racing to set the state.
  assert.match(panel, /disabled=\{busy\}/);
  assert.match(panel, /busy \? "Refreshing…"/);
});

test("held paths are shown, because the point is to know which file", () => {
  assert.match(panel, /agent\.held_paths/);
  assert.match(panel, /font-mono break-all/);
});

test("orphaned subjects are surfaced as available work", () => {
  assert.match(panel, /availableSubjects/);
  assert.match(panel, /Unclaimed after a stop/);
});

test("activity is read on the existing poll, not a second timer", () => {
  // One cadence, one place, no second interval to leak on unmount.
  assert.match(section, /void loadActivity\(activityRoom\)/);
  assert.match(section, /window\.setInterval\(\(\) => void loadActivity\(activityRoom\), 10000\)/);
  assert.match(section, /window\.clearInterval\(handle\)/);
});

test("activity settles separately from the roster read", () => {
  // An older Gateway has no /activity route; that must read as "this build
  // does not have it", not as "nobody is working".
  assert.match(section, /setActivityError/);
  assert.match(section, /activityByRoom/);
});
