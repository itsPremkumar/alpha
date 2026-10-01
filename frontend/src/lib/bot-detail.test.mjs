import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

/**
 * Regression coverage for the bot detail client.
 *
 * The page this backs is a *reader*, so the whole risk of it is showing a
 * plausible-looking value the Gateway never sent. These tests pin the two
 * directions that matter:
 *
 *   - absent stays absent (null / "not reported"), never 0 / "" / [] / false
 *   - a value the server did send survives verbatim, including enums this
 *     build has never heard of
 *
 * LOADER NOTE. `bot-detail.ts` imports `./api-client` and `./bots` with
 * extensionless specifiers, so the repo's `data:text/javascript` trick cannot
 * resolve them. It is transpiled through `ts.transpileModule` and loaded with an
 * injected `require` that only satisfies those two known dependencies and
 * asserts on anything else — the same pattern as `bots-activity-client.test.mjs`.
 */

const compiled = new Map(
  ["api-client", "bots", "bot-detail"].map((name) => [
    name,
    ts.transpileModule(readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText,
  ]),
);

function load(name, dependencies = {}) {
  const exports = {};
  new Function("exports", "require", "process", "console", compiled.get(name))(
    exports,
    (dependency) => {
      assert.ok(Object.hasOwn(dependencies, dependency), `Unexpected dependency: ${dependency}`);
      return dependencies[dependency];
    },
    { env: {} },
    { error: () => {} },
  );
  return exports;
}

// `bot-detail` needs `apiFetch` and `normalizeBot` only as collaborators; the
// mapping helpers under test are pure and never call them.
const { normalizeInbox, detailValue, enumOrUnknown } = load("bot-detail", {
  "./api-client": { apiFetch: async () => ({}) },
  "./bots": { normalizeBot: (raw) => raw },
});

test("normalizeInbox keeps an unreported unread_count null instead of zero", () => {
  const box = normalizeInbox({ messages: [] });
  assert.equal(box.unread_count, null, "an absent unread_count is 'not reported', not '0 unread'");
  assert.deepEqual(box.messages, []);
});

test("normalizeInbox preserves a real unread_count", () => {
  const box = normalizeInbox({ messages: [{ id: "1" }], unread_count: 4 });
  assert.equal(box.unread_count, 4);
  assert.equal(box.messages.length, 1);
});

test("normalizeInbox coerces a wrongly-typed unread_count to null, not 0", () => {
  // `"0"` is a string. Reading it as a number would be a coercion the client
  // invented; reading it as 0 would be worse - it would claim the server
  // measured zero. Absent is the only honest answer.
  const box = normalizeInbox({ messages: [], unread_count: "0" });
  assert.equal(box.unread_count, null);
});

test("normalizeInbox survives a missing messages key", () => {
  const box = normalizeInbox({ unread_count: 1 });
  assert.deepEqual(box.messages, []);
  assert.equal(box.unread_count, 1);
});

test("detailValue reports absent keys as null", () => {
  assert.equal(detailValue({}, "heartbeat"), null);
  assert.equal(detailValue({ heartbeat: null }, "heartbeat"), null);
  assert.equal(detailValue({ heartbeat: undefined }, "heartbeat"), null);
});

test("detailValue treats an empty string as unreported", () => {
  // The Gateway sends "" for "not set" in some envelope shapes. Rendering that
  // verbatim would put a blank cell on the page that reads as an empty string
  // the server actually sent.
  assert.equal(detailValue({ note: "" }, "note"), null);
});

test("detailValue renders numbers and booleans rather than dropping them", () => {
  assert.equal(detailValue({ total_runs: 0 }, "total_runs"), "0", "a measured zero is a real answer");
  assert.equal(detailValue({ paused: false }, "paused"), "false");
  assert.equal(detailValue({ avg: 12.5 }, "avg"), "12.5");
});

test("detailValue returns null for a value that is not a scalar", () => {
  // A nested object is not a row value; the caller renders its own panel.
  assert.equal(detailValue({ nested: { a: 1 } }, "nested"), null);
});

test("enumOrUnknown preserves an unrecognised status verbatim", () => {
  // A newer Gateway may send a status this build has never seen. Displaying it
  // is honest; snapping it to "active" would invent a claim.
  const res = enumOrUnknown("hibernating", ["active", "paused", "disabled"]);
  assert.equal(res.value, "hibernating");
  assert.equal(res.known, false);
});

test("enumOrUnknown marks a known status as known", () => {
  const res = enumOrUnknown("paused", ["active", "paused", "disabled"]);
  assert.equal(res.value, "paused");
  assert.equal(res.known, true);
});

test("enumOrUnknown reports a non-string status as unreported, never as 'active'", () => {
  const res = enumOrUnknown(null, ["active", "paused", "disabled"]);
  assert.equal(res.value, "not reported");
  assert.equal(res.known, false);
});