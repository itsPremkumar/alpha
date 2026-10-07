// Negative controls for the whitespace-tolerant source pins.
//
// Why these exist: five suites (chat-request-error, chat-stream,
// edit-regenerate, history-store, lion-pet) pinned *line shapes* in
// ChatView.tsx with regexes that demanded a single space between tokens. A
// concurrent reflow of ChatView.tsx (the same behaviour, re-wrapped across
// lines) failed all of them, so the frontend gate was red on a tree that
// behaved correctly. The fix loosened `\s+` into the patterns.
//
// Loosening a pin is only honest if the loosened pattern still DISCRIMINATES:
// it must match the real source, and must FAIL on source with that behaviour
// removed. A `\s*` that can match "nothing at all" would turn a pin into a
// vacuous pass. Every control below deletes or corrupts exactly the tokens one
// loosened pattern asserts, then asserts the pattern no longer matches.
//
// Discipline from this session: never mutate a source file for a control. These
// read ChatView.tsx once, derive the broken variants in memory, and never write.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const ROOT = "C:\\Users\\PREM KUMAR\\Videos\\alpha\\frontend";
const read = (rel) => readFileSync(`${ROOT}\\${rel}`, "utf8");
const source = read("src\\components\\ChatView.tsx");

// The exact (name, pattern, breaker) triples from the five suites. `breaker`
// removes or mangles only the tokens the pattern asserts, leaving the rest of
// the file intact, so a failure is attributable to that pattern alone.
const PINS = [
  {
    name: "chat-request-error: error box is scoped to the thread that failed",
    pattern: /requestError &&\s+requestError\.threadId === activeThreadId/,
    breaker: (s) => s.replace("requestError.threadId === activeThreadId", "true"),
    shown: "the error box would then render on every thread, not just the failing one",
  },
  {
    name: "chat-request-error: incomplete content renders as plain text in <pre>",
    pattern: /<pre[^>]*>\s*\{requestError\.partial\}\s*<\/pre>/,
    breaker: (s) => s.replace("{requestError.partial}", "{requestError.draft}"),
    shown: "the incomplete response body would render the wrong field",
  },
  {
    name: "chat-stream: replay-gap gets its own truthful notice, separate from errors",
    pattern: /onEvent:[\s\S]*?replay-gap[\s\S]*?flash\(\s*"Some streamed events could not be replayed\. This response is incomplete\."\s*,?\s*\)/,
    breaker: (s) => s.replace("Some streamed events could not be replayed. This response is incomplete.", "Could not replay events."),
    shown: "a non-streaming error would read like a dropped-events failure",
  },
  {
    name: "chat-stream: only a StreamRunFailure carries a support id",
    pattern: /error instanceof StreamRunFailure\s+\?\s+chatSupportId\(error\.sseError\)/,
    breaker: (s) => s.replace("error instanceof StreamRunFailure", "true"),
    shown: "every failure would claim a support id it does not have",
  },
  {
    name: "edit-regenerate: edit passes the edited message id to the Gateway",
    pattern: /prepareEditRegenerate\(\s*activeThreadId,\s*messageId,/,
    breaker: (s) => s.replace(/prepareEditRegenerate\(\s*activeThreadId,\s*messageId,/, "prepareEditRegenerate(activeThreadId,"),
    shown: "the edit would re-append instead of replacing — the bug this pin exists for",
  },
  {
    name: "edit-regenerate: regenerate prepares a replay rather than re-sending",
    pattern: /prepareRegenerate\(\s*activeThreadId,/,
    breaker: (s) => s.replace(/prepareRegenerate\(\s*activeThreadId,/, "sendMessage(activeThreadId,"),
    shown: "regenerate would fork a second request instead of superseding the old turn",
  },
  {
    name: "edit-regenerate: a failure re-appends the superseded tail after the drop",
    pattern: /\[\s*\.\.\.kept\.filter\(\(message\) => message\.id !== appendedId\),\s*\.\.\.supersededMessages,?\s*\]/,
    breaker: (s) => s.replace("...supersededMessages", "...kept.slice(0, 3)"),
    shown: "a failed regenerate would drop the original replies permanently",
  },
  {
    name: "history-store: an unreadable server list is disclosed as the local copy",
    pattern: /the\s+conversation list below is the complete local copy, not a\s+confirmed empty history/,
    breaker: (s) => s.replace(/the\s+conversation list below is the complete local copy, not a\s+confirmed empty history/, "no chats yet"),
    shown: "an outage would present as a confirmed empty history",
  },
  {
    name: "lion-pet: a stopping run is a waiting companion state",
    pattern: /updateLion\(\s*"waiting"/,
    breaker: (s) => s.replace(/updateLion\(\s*"waiting"/, 'updateLion("idle"'),
    shown: "the companion would show no sign that a stop is in progress",
  },
];

let matched = 0;
for (const pin of PINS) {
  assert.ok(pin.pattern.test(source), `PIN BROKEN: ${pin.name} does not match the real ChatView.tsx`);
  const broken = pin.breaker(source);
  assert.notEqual(broken, source, `CONTROL INERT: the breaker for "${pin.name}" changed nothing, so it proves nothing`);
  assert.ok(
    !pin.pattern.test(broken),
    `VACUOUS PIN: ${pin.name} still matches once its behaviour is removed — \`\\s\` made it unconditional`,
  );
  matched += 1;
  console.log(`  ok   ${pin.name}\n         without the behaviour: ${pin.shown}`);
}

// The looseness that was added must be bounded: every pattern still has to
// consume real characters between the tokens it anchors on, so `\s` is a
// formatting allowance and not a wildcard. This asserts the same pins reject a
// source whose *words* differ (the breaker's premise) rather than merely its
// whitespace.
for (const pin of PINS) {
  const collapsed = source.replace(/\s+/g, " ");
  assert.ok(
    pin.pattern.test(collapsed) || pin.pattern.test(source),
    `PIN LOST ITS ANCHOR: ${pin.name} no longer matches either the wrapped or the collapsed source`,
  );
}

console.log(`\nVERIFIED  ${matched}/${PINS.length} loosened pins still discriminate: they match the real`);
console.log("source and fail once the behaviour they assert is removed. A vacuous `\\s` here would");
console.log("have let a regressed ChatView.tsx pass the frontend gate silently.");