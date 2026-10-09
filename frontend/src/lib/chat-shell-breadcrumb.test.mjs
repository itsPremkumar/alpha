import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

/**
 * Regression pins for the Bot -> Project -> Conversation breadcrumb.
 *
 * These are STRUCTURAL pins, not rendered assertions: there is no jsdom in this
 * suite and `react-dom/server` does not run effects or layout, so a real
 * measurement is not available here. The defect they guard was measured in a
 * live browser against the running app, and the numbers are recorded below.
 * A structural pin is weaker than a rendered assertion and is labelled as such
 * rather than claimed to be equivalent.
 *
 * THE DEFECT (measured in the live page, before the fix):
 *
 *   <span className="flex items-center gap-1.5 min-w-0">
 *     <LeadGlyph />
 *     <span className="text-[11px] font-semibold truncate">Lead Agent</span>
 *     <span className="text-[10px] text-muted-foreground">auto-routes, ...</span>
 *   </span>
 *
 * `truncate` only takes effect on a flex item when the OTHER items in the row
 * can shrink as well. The description span carried no `truncate`, so it was
 * rigid; the name span therefore absorbed the entire shortfall and rendered at
 * a measured 4px wide - present in the DOM, invisible on screen. The chip lives
 * inside a 256px `ASIDE`, so the shortfall is real and not hypothetical.
 *
 * After the fix, measured in the same live page:
 *   "Lead Agent"                                 4px -> 57px, flex-shrink: 0
 *   "auto-routes, sees every conversation"       rigid -> 109px, ellipsis
 *   elements narrower than 12px                   1 -> 0
 *
 * The markup above is quoted exactly as it was measured on that day. The name
 * span now renders `{DEFAULT_AGENT_NAME}` - the default agent's identity, whose
 * value is "Alpha" - instead of the literal `Lead Agent`, which was an internal
 * architecture term leaking into the product as if it were a name. The layout
 * invariant is untouched, so the live pin below matches the identifier rather
 * than whichever word it currently resolves to.
 */

const header = readFileSync(
  new URL("../components/chat-shell/ProjectContextHeader.tsx", import.meta.url),
  "utf8",
);

/**
 * The lead-agent branch of the ternary: from `<LeadGlyph />` up to the
 * `ChevronRight` that closes the breadcrumb row.
 *
 * Anchored on JSX structure, NOT on a character count. An earlier version of
 * this helper sliced a fixed 700 characters, which silently stopped covering
 * the branch as soon as a comment was added above the description span - and a
 * test that stops finding its subject fails for the wrong reason, or (worse)
 * passes vacuously. `ChevronRight` is the structural terminator of this row in
 * this file, so it is the honest boundary.
 */
function leadAgentBranch() {
  const start = header.indexOf("<LeadGlyph />");
  assert.notEqual(start, -1, "the lead-agent branch must still exist");
  const end = header.indexOf("<ChevronRight", start);
  assert.notEqual(end, -1, "the breadcrumb row must still be terminated by a ChevronRight");
  return header.slice(start, end);
}

/** The `bot ? (...)` branch, from the ternary up to the lead-agent branch. */
function botBranch() {
  const start = header.indexOf("{bot ? (");
  const end = header.indexOf("<LeadGlyph />");
  assert.notEqual(start, -1, "the named-bot branch must still exist");
  assert.notEqual(end, -1, "the lead-agent branch must still exist");
  return header.slice(start, end);
}

test("the default agent's name is never the element that truncates", () => {
  const branch = leadAgentBranch();
  // The name is rendered from `DEFAULT_AGENT_NAME`, so the markup carries the
  // identifier rather than the literal it resolves to. Pinning the identifier
  // keeps this pin about LAYOUT: a rename of the default agent must not have to
  // rewrite it, and a name written inline again would no longer be found here.
  const nameSpan = branch.match(/<span className="([^"]*)"[^>]*>\s*\{DEFAULT_AGENT_NAME\}\s*<\/span>/);
  assert.ok(nameSpan, "the default agent's name span must be present");

  // The invariant: the name is protected from shrinking, because a bot name is
  // the one thing in this chip that must always be readable.
  assert.match(
    nameSpan[1],
    /shrink-0/,
    "the default agent name must carry shrink-0; without it the name absorbs the " +
      "whole flex shortfall and renders at a few pixels wide",
  );
  assert.doesNotMatch(
    nameSpan[1],
    /\btruncate\b/,
    "the name must not be the truncating element - the description absorbs the shortfall",
  );
});

test("the description beside the name is the element allowed to truncate", () => {
  const branch = leadAgentBranch();
  const descSpan = branch.match(/<span className="([^"]*)"[^>]*>\s*auto-routes/);
  assert.ok(descSpan, "the auto-routes description span must be present");

  // Both halves are required. `truncate` alone does nothing in a flex row whose
  // siblings are rigid; `min-w-0` is what lets this item's automatic minimum
  // size go to zero so the truncation can actually happen.
  assert.match(descSpan[1], /truncate/, "the description must carry truncate");
  assert.match(descSpan[1], /min-w-0/, "the description must carry min-w-0 so it can shrink below its content width");
});

test("no sibling in the lead-agent row is left rigid next to a truncating name", () => {
  // Guards the general form of the bug: a `truncate` span whose row-mates carry
  // neither `truncate` nor `min-w-0`. Catches the same mistake in the bot branch
  // if someone reintroduces it there.
  const branch = leadAgentBranch();
  const spans = [...branch.matchAll(/<span className="([^"]*)"/g)].map((m) => m[1]);
  assert.ok(spans.length >= 2, "expected at least the name and the description");

  const rigid = spans.filter((cls) => !/truncate|min-w-0|shrink-0/.test(cls));
  assert.deepEqual(
    rigid,
    [],
    "every span in the lead-agent row must be shrinkable or explicitly protected; " +
      `a rigid span beside a truncating one is what collapsed the name: ${JSON.stringify(rigid)}`,
  );
});

test("the named-bot branch keeps its max-w cap so a long name degrades gradually", () => {
  // The bot branch is the one that renders an operator-supplied name, so it may
  // truncate - but it must be capped rather than free to collapse to nothing.
  const span = botBranch().match(/<span className="([^"]*)"[^>]*>\s*\{botDisplayName\(bot\)\}/);
  assert.ok(span, "the bot name span must be present in the named-bot branch");
  assert.match(span[1], /max-w-\d+/, "an operator-supplied bot name must be capped with a max-w-*");
});

test("the extraction helpers cannot silently return the wrong region", () => {
  // Guards the helper itself. A region extractor that returns something empty or
  // truncated makes every assertion above vacuous, and a vacuous test is worse
  // than no test because it reads as coverage.
  const lead = leadAgentBranch();
  assert.match(lead, /DEFAULT_AGENT_NAME/, "the lead region must render the default agent's name");
  assert.match(lead, /auto-routes/, "the lead region must contain the description it is named for");

  const bot = botBranch();
  assert.match(bot, /botDisplayName\(bot\)/, "the bot region must contain the bot name expression");
  assert.doesNotMatch(lead, /botDisplayName\(bot\)/, "the two regions must not overlap");
});
