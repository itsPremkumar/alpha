// Regression: the fleet-health strip must not assert a count it has not measured.
//
// The defect
// ----------
// `BotGallery` renders `<FleetHealthBar health={computeFleetHealth(bots)} />`
// unconditionally, ABOVE the `isLoading` branch that already skeletons the card
// grid. `computeFleetHealth([])` returns `total: 0, active: 0, paused: 0`, so
// during the roster fetch the header asserted a *measured empty fleet*.
//
// Measured live in a browser against a real Gateway: the roster takes ~20s to
// arrive, because `fetchBots({activity: true})` costs a per-bot inbox read plus a
// secret scan for every bot. For those 20 seconds the header read
//
//     0 Total bots   0 Active   0 Paused
//
// while `GET /api/bots` returned `{count: 57}` and the same page's roster
// eventually rendered "57 Total bots / 43 Active". Same page, same session, two
// different answers, and the first one is the fabricated one.
//
// `frontend/AGENTS.md` is explicit: "A count the server did not report is `null`
// and renders as `—`, never `0`." The grid already honoured that; the strip did
// not, because it sat outside the branch.
//
// Why this is a source pin rather than a render test
// --------------------------------------------------
// The rule is positional: the strip must be INSIDE a conditional that consults
// the loading flag. Rendering the component and asserting on text would pass
// again the moment someone hoists the strip back out, which is exactly the
// regression. So the structural relationship is what this file pins, and the
// comment above the call site carries the measurement that makes it matter.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import test from "node:test";

const HERE = dirname(fileURLToPath(import.meta.url));
const GALLERY = join(HERE, "..", "components", "bots", "BotGallery.tsx");
const BOTS_LIB = join(HERE, "bots.ts");
const source = readFileSync(GALLERY, "utf8");
const botsLib = readFileSync(BOTS_LIB, "utf8");

test("computeFleetHealth([]) really does produce zeros -- this is why the strip must be gated", () => {
  // `computeFleetHealth` lives in lib/bots.ts; the gallery only calls it. Its
  // arithmetic is the reason the gate exists, so it is asserted at the source.
  const fn = botsLib.slice(botsLib.indexOf("export function computeFleetHealth"));
  assert.ok(fn.length > 0, "computeFleetHealth must exist in lib/bots.ts");
  assert.match(
    fn,
    /const\s+total\s*=\s*bots\.length/,
    "computeFleetHealth derives total from bots.length, so an empty roster is 0, not null",
  );
  assert.match(fn, /b\.status\s*===\s*"active"/);
  assert.match(fn, /b\.status\s*===\s*"paused"/);
});

test("the fleet-health strip is gated on the loading flag", () => {
  const strip = source.indexOf("<FleetHealthBar");
  assert.notStrictEqual(strip, -1, "FleetHealthBar must still be rendered");

  // Find the conditional that owns it. A bare `{isLoading ? ... : (<Fleet.../>)}`
  // is the accepted shape; anything else must be rejected.
  const before = source.slice(Math.max(0, strip - 700), strip);
  assert.match(
    before,
    /isLoading\s*\?/,
    "FleetHealthBar renders unconditionally, so it asserts a count from a roster that has not loaded",
  );
});

test("the loading branch does not paint a number", () => {
  // The skeleton must be the loading branch's content: no digits, and an
  // accessible busy marker so the state is not purely visual.
  const conditional = source.slice(source.indexOf("{isLoading ? ("), source.indexOf("<FleetHealthBar"));
  assert.ok(conditional.length > 0, "expected an isLoading branch before the strip");
  assert.doesNotMatch(
    conditional.replace(/length:\s*5/g, ""),
    />\s*\{\{?\s*(health|bots)\./,
    "the loading branch must not interpolate a fleet count",
  );
  assert.match(conditional, /aria-busy="true"/, "the loading state must be announced, not only animated");
});

test("the honesty reasoning is recorded at the call site", () => {
  // A future edit that hoists the strip out should have to delete a comment
  // that says why, which is a deliberate act rather than an accident.
  const idx = source.indexOf("{/* The fleet strip must not render");
  assert.notStrictEqual(idx, -1, "the strip must carry the reason it is gated");
  assert.match(source.slice(idx, idx + 1200), /never `0`|never 0|forbids/);
});