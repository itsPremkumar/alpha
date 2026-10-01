// workspace-menu-geometry.test.mjs — the portalled "More Views" panel must stay
// reachable.
//
// The defect this pins: the panel was an `absolute` child of its trigger, so it
// was clipped by every ancestor. Measured in the running app, `ChatView` wraps
// `NavTabs` in `overflow-x-auto` (whose `overflow-y` computes to `auto`) inside a
// `<main class="overflow-hidden">`, and the menu measured 256x1060 against a
// 30px-tall clip box — 4 visible pixels. It also carried no `max-height` and no
// internal scroll, so its last group sat below the fold of every viewport.
//
// `placeFloatingPanel` is the pure geometry the portal is positioned with, so
// the numbers below are the defect's own numbers: assert on them directly.
import assert from "node:assert/strict";
import test from "node:test";

import {
  FLIP_BELOW_THRESHOLD,
  MIN_PANEL_HEIGHT,
  PANEL_WIDTH,
  TRIGGER_GAP,
  VIEWPORT_MARGIN,
  placeFloatingPanel,
} from "./workspace-menu-geometry.ts";

/**
 * The menu's measured natural height, reproduced from the live DOM.
 *
 * A measured value, not a derived one: it was 1060px with 20 entries. It drifts
 * as tabs are added or promoted to primary, and it must stay comfortably taller
 * than a short viewport — that inequality is what the first test pins, so an
 * exact count is not the contract here.
 */
const MENU_NATURAL_HEIGHT = 1060;

/** A header trigger on a wide, short window — the reported failure case. */
const HEADER_TRIGGER = { top: 64, bottom: 94, left: 761 };
const VIEWPORT = { width: 912, height: 670 };

test("the 1060px menu in a 670px viewport is capped and stays on screen", () => {
  const box = placeFloatingPanel({
    trigger: HEADER_TRIGGER,
    viewport: VIEWPORT,
    naturalHeight: MENU_NATURAL_HEIGHT,
  });

  // The cap is real: the panel is `overflow-y: auto`, so the entries below the
  // fold scroll instead of being unreachable.
  assert.ok(
    box.maxHeight < MENU_NATURAL_HEIGHT,
    `panel must be height-capped, got maxHeight=${box.maxHeight} for ${MENU_NATURAL_HEIGHT}px of content`,
  );
  assert.ok(
    box.maxHeight >= MIN_PANEL_HEIGHT,
    "a capped menu must still be usable",
  );

  // Every edge is inside the viewport, so no clipping ancestor can hide a row.
  assert.ok(box.top >= VIEWPORT_MARGIN, `top ${box.top} above the viewport`);
  assert.ok(
    box.top + box.maxHeight <= VIEWPORT.height - VIEWPORT_MARGIN,
    `bottom ${box.top + box.maxHeight} past a ${VIEWPORT.height}px viewport`,
  );
  assert.ok(box.left >= VIEWPORT_MARGIN, `left ${box.left} off-screen`);
  assert.ok(
    box.left + PANEL_WIDTH <= VIEWPORT.width - VIEWPORT_MARGIN,
    `right ${box.left + PANEL_WIDTH} off a ${VIEWPORT.width}px viewport`,
  );
});

test("the menu never claims more height than the side it opens on", () => {
  const roomBelow = VIEWPORT.height - HEADER_TRIGGER.bottom - TRIGGER_GAP - VIEWPORT_MARGIN;
  const box = placeFloatingPanel({
    trigger: HEADER_TRIGGER,
    viewport: VIEWPORT,
    naturalHeight: MENU_NATURAL_HEIGHT,
  });

  const room = box.opensUp
    ? HEADER_TRIGGER.top - TRIGGER_GAP - VIEWPORT_MARGIN
    : roomBelow;
  assert.ok(
    room >= MIN_PANEL_HEIGHT && box.maxHeight <= room,
    `maxHeight ${box.maxHeight} exceeds the ${room}px available on the chosen side`,
  );
});

test("a short menu is not capped below its own height", () => {
  const box = placeFloatingPanel({
    trigger: HEADER_TRIGGER,
    viewport: VIEWPORT,
    naturalHeight: 180,
  });
  assert.equal(box.maxHeight, 180, "a menu that fits must render at full height");
  assert.equal(box.opensUp, false, "a header trigger has room below");
  assert.equal(box.top, HEADER_TRIGGER.bottom + TRIGGER_GAP, "it hangs off the trigger");
});

test("a trigger near the bottom flips the menu upward", () => {
  const trigger = { top: 600, bottom: 630, left: 40 };
  const box = placeFloatingPanel({ trigger, viewport: VIEWPORT, naturalHeight: 400 });
  assert.equal(box.opensUp, true, "no room below, so the menu opens up");
  assert.ok(
    box.top + box.maxHeight <= trigger.top - TRIGGER_GAP,
    `an upward menu must not cover the trigger it belongs to (bottom ${box.top + box.maxHeight})`,
  );
});

test("a trigger with room on neither side still returns an on-screen box", () => {
  const box = placeFloatingPanel({
    trigger: { top: 330, bottom: 360, left: 10 },
    viewport: { width: 300, height: 400 },
    naturalHeight: MENU_NATURAL_HEIGHT,
  });
  assert.ok(box.top >= 0, "the box must not start off-screen");
  assert.ok(box.left >= 0, "the box must not start off-screen horizontally");
  assert.ok(box.maxHeight > 0, "the menu must still be rendered");
});

test("the flip threshold only applies when there is a better side", () => {
  // Just under the threshold below, but genuinely more room above: flip.
  const flip = placeFloatingPanel({
    trigger: { top: 520, bottom: 550, left: 40 },
    viewport: { width: 900, height: 700 },
    naturalHeight: 300,
  });
  assert.equal(flip.opensUp, true);
  assert.ok(
    700 - 550 - TRIGGER_GAP - VIEWPORT_MARGIN < FLIP_BELOW_THRESHOLD,
    "precondition: the space below is below the flip threshold",
  );

  // Plenty of room below: never flip, however the arithmetic falls.
  const stay = placeFloatingPanel({
    trigger: { top: 100, bottom: 130, left: 40 },
    viewport: { width: 900, height: 900 },
    naturalHeight: 300,
  });
  assert.equal(stay.opensUp, false);
});

test("a right-edge trigger is pulled back on screen instead of overflowing", () => {
  const box = placeFloatingPanel({
    trigger: { top: 60, bottom: 90, left: 880 },
    viewport: { width: 912, height: 800 },
    naturalHeight: 200,
  });
  assert.equal(box.left, VIEWPORT.width - VIEWPORT_MARGIN - PANEL_WIDTH);
});

test("outputs are integers, because they become inline pixel styles", () => {
  const box = placeFloatingPanel({
    trigger: { top: 63.5, bottom: 93.25, left: 761.75 },
    viewport: { width: 912, height: 670 },
    naturalHeight: 1060.5,
  });
  for (const key of ["top", "left", "maxHeight"]) {
    assert.equal(Number.isInteger(box[key]), true, `${key} must be an integer, got ${box[key]}`);
  }
});