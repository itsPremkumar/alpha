// Contract tests for the shared clock vocabulary.
//
// Two things are cheap to regress and expensive to notice:
//   1. every helper returns `null` for a value it cannot read rather than
//      substituting "now" — the fabrication `lib/api.ts` used to perform at
//      the mapping layer, which painted the page-load time onto history rows
//      the Gateway never stamped; and
//   2. the three wire shapes (ISO string, epoch seconds, epoch milliseconds)
//      all resolve to one instant, so a bot DM and a thread message from the
//      same second never render an hour apart.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./time.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const clock = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);
const { parseTime, hasTime, relTime, clockTime, dayLabel, absoluteStamp, isRecent } = clock;

const ISO = "2026-09-27T14:32:05.000Z";
const EPOCH_MS = Date.parse(ISO);
const EPOCH_S = EPOCH_MS / 1000;

test("the three wire shapes resolve to the same instant", () => {
  assert.equal(parseTime(ISO), EPOCH_MS);
  assert.equal(parseTime(EPOCH_MS), EPOCH_MS);
  assert.equal(parseTime(EPOCH_S), EPOCH_MS);
  // inbox.py writes time.time(), a float with sub-second precision.
  assert.equal(parseTime(EPOCH_MS + 0.487), EPOCH_MS);
  assert.equal(parseTime(String(EPOCH_S)), EPOCH_MS);
  const fractional = parseTime(EPOCH_S + 0.487);
  assert.ok(Math.abs(fractional - EPOCH_MS) < 1000, "fractional epoch seconds stay in the same second");
});

test("an unreadable value is null, never the current time", () => {
  for (const bad of [null, undefined, "", "   ", "not-a-date", "yesterday", -1, 0, NaN, Infinity]) {
    assert.equal(parseTime(bad), null, `parseTime(${JSON.stringify(bad)}) must be null`);
    assert.equal(hasTime(bad), false);
    assert.equal(relTime(bad, EPOCH_MS), null);
    assert.equal(clockTime(bad), null);
    assert.equal(dayLabel(bad, EPOCH_MS), null);
    assert.equal(absoluteStamp(bad), null);
    assert.equal(isRecent(bad, 90, EPOCH_MS), false);
  }
});

test("magnitudes that are not record times are rejected", () => {
  assert.equal(parseTime(1), null, "a bare counter is not an epoch");
  assert.equal(parseTime(3600), null, "a duration is not an epoch");
  assert.equal(parseTime(978_307_199), null, "2000-12-31 in seconds predates every record");
  assert.equal(parseTime(1_750_000_000_000_000), null, "microseconds are out of scope");
  assert.equal(parseTime(`0.${EPOCH_S}`), null, "a bare counter with a fraction is still not an epoch");
});

test("relative buckets are computed from the injected clock", () => {
  const now = EPOCH_MS;
  assert.equal(relTime(now, now), "just now");
  assert.equal(relTime(now - 59_000, now), "just now");
  assert.equal(relTime(now - 60_000, now), "1m ago");
  assert.equal(relTime(now - 5 * 60_000, now), "5m ago");
  assert.equal(relTime(now - 59 * 60_000, now), "59m ago");
  assert.equal(relTime(now - 60 * 60_000, now), "1h ago");
  assert.equal(relTime(now - 23 * 3_600_000, now), "23h ago");
  assert.equal(relTime(now - 24 * 3_600_000, now), "1d ago");
  assert.equal(relTime(now - 6 * 86_400_000, now), "6d ago");
  assert.equal(relTime(now - 30 * 86_400_000, now), new Date(now - 30 * 86_400_000).toLocaleDateString());
});

test("a future timestamp clamps to just now instead of counting down", () => {
  // Clock skew between the browser and the Gateway is routine; printing
  // "in 3 hours" would claim something about the future nobody measured.
  assert.equal(relTime(EPOCH_MS + 3 * 3_600_000, EPOCH_MS), "just now");
  assert.equal(relTime(EPOCH_MS + 1, EPOCH_MS), "just now");
});

test("isRecent only ever marks a real recent reading", () => {
  const now = EPOCH_MS;
  assert.equal(isRecent(now - 10_000, 90, now), true, "active within the window");
  assert.equal(isRecent(now - 89_000, 90, now), true);
  assert.equal(isRecent(now - 91_000, 90, now), false, "older than the window");
  assert.equal(isRecent(null, 90, now), false, "no reading is not presence");
  assert.equal(isRecent(now + 60_000, 90, now), true, "skewed-future reads as present");
});

test("day labels are Today / Yesterday / the date", () => {
  const now = Date.parse("2026-09-27T09:00:00");
  assert.equal(dayLabel("2026-09-27T23:59:00", now), "Today", "same calendar day, later hour");
  assert.equal(dayLabel("2026-09-27T00:00:30", now), "Today");
  assert.equal(dayLabel("2026-09-26T23:59:59", now), "Yesterday");
  assert.equal(dayLabel("2026-09-25T12:00:00", now), new Date("2026-09-25T12:00:00").toLocaleDateString());
});

test("clockTime and absoluteStamp agree with the locale they claim", () => {
  assert.equal(clockTime(ISO), new Date(EPOCH_MS).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  assert.equal(absoluteStamp(ISO), new Date(EPOCH_MS).toLocaleString());
  assert.equal(clockTime("0"), null, "a zero stamp is not a time");
});
