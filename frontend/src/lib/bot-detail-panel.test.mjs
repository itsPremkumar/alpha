import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

/**
 * The panel is the only place a bot's own record is edited, so these pin the
 * three ways it could mislead: sending a rejected empty body, showing the
 * operator's draft instead of the server's answer, and letting an edited
 * measurement keep being described as a measurement.
 */

const bots = readFileSync(new URL("./bots.ts", import.meta.url), "utf8");
const panel = readFileSync(
  new URL("../components/chat-shell/BotDetailPanel.tsx", import.meta.url),
  "utf8",
);

test("the update client refuses an empty body, because the server rejects it", () => {
  assert.match(
    bots,
    /if \(Object\.keys\(patch\)\.length === 0\) \{[\s\S]{0,200}?throw new Error\(/,
    "an empty PATCH is a 422, so the client must refuse it rather than rely on callers",
  );
});

test("the client PATCHes the bot by name and re-reads the server's answer", () => {
  assert.match(bots, /apiFetch\(`\/bots\/\$\{encodeURIComponent\(name\)\}`/);
  assert.match(bots, /method: "PATCH"/);
  // A 2xx is a request accepted, not proof the registry now reads the way the
  // form claims, so the response must be mapped back rather than echoed.
  assert.match(bots, /await res\.json\(\)/, "the response must be parsed and normalized");
  assert.match(bots, /normalizeBot\(candidate\)/, "and mapped through the same normalizer as a read");
});

test("the panel derives its fields from the schema, never from a local list", () => {
  assert.match(panel, /visibleBotFields\(\)/, "the field list must come from BOT_FIELDS");
  assert.match(panel, /collapsedBotFields\(\)/);
  assert.doesNotMatch(
    panel,
    /key: "(display_name|role|soul|model|capabilities|reputation_score)"/,
    "the panel must not restate field names; the schema owns them",
  );
});

test("the panel sends only changed keys and disables Save when there are none", () => {
  assert.match(panel, /changedBotFields\(original, draft\)/);
  assert.match(panel, /hasChanges\(patch\)/);
  assert.match(
    panel,
    /disabled=\{!dirty \|\| saving\}/,
    "Save must be disabled with nothing to send, and while a save is in flight",
  );
});

test("an absent value is rendered as absent, not as a blank cell", () => {
  // `model` is empty on the live roster. A blank input is indistinguishable
  // from a read that failed, so the placeholder says which one this is.
  assert.match(panel, /no model assigned/, "an empty model must be named as a real state");
  assert.match(panel, /not reported/, "other absent fields say the same");
  assert.match(
    panel,
    /const value = field\.key in draft \? draft\[field\.key\] : undefined;/,
    "an absent key must read as undefined, not as a leftover value",
  );
});

test("an operator-edited measurement is labelled, not still called a measurement", () => {
  assert.match(panel, /isOperatorSet\(field, original, draft\)/, "the distinction must be used");
  assert.match(panel, /set by operator/, "and shown in the UI");
  assert.match(
    panel,
    /title="An operator changed this\. It no longer reflects what the runtime measured\."/,
  );
});

test("a successful save shows the server's profile, not the draft", () => {
  assert.match(panel, /onSaved\?\.\(updated\)/, "the parent must be told to re-read");
  assert.match(
    panel,
    /setDraft\(\{ \.\.\.\(updated as unknown as Record<string, unknown>\) \}\)/,
    "and the draft must be reset from the server's answer",
  );
  assert.match(panel, /Showing the Gateway's answer\./);
});

test("a failed save surfaces the server's reason rather than a generic failure", () => {
  assert.match(panel, /err instanceof Error \? err\.message : String\(err\)/);
  assert.match(panel, /<ErrorBox message=\{error\}/, "the reason must be rendered");
});
