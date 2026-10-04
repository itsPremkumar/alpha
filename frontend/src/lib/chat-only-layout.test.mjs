// chat-only-layout.test.mjs — the chat column must not leak into other views.
//
// The defect, found by screenshotting every tab rather than by asserting: a
// non-chat view drew its own section **and** the entire chat workspace
// underneath it — transcript viewport, empty-state hero, goal bar, subagent
// list, context toolbar and a complete composer.
//
// `<main>` is `flex flex-col h-full overflow-hidden`, so a short section and a
// `flex-1` transcript competed for one screen and the section was clipped.
// Measured on Overview: the atlas was cut through the middle of its first card
// row, with the chat hero and composer drawn below it. The Skills tab showed
// six loaders and then nothing. Chat itself looked correct, which is exactly
// why this read as a per-section styling bug for as long as it did.
//
// The cause was structural, not cosmetic. The chat column sat in the bare
// `else` of the `activeContextTab` chain:
//
//     {activeContextTab === "files" ? … : … : ( <chat column> )}
//
// `activeContextTab` is chat-scoped state, so *every* view whose id was not
// files/tasks/knowledge fell through to that `else`.
//
// node --test src/lib/*.test.mjs — no server, no browser, no DOM shim.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");

/** Comments quote the exact anti-patterns below, so strip them first. */
function code(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/[ \t]+\/\/.*$/gm, "");
}

const chat = code(source);

/** The chat column: from the `view === "chat" ? (` gate to its closing `</div>`. */
const chatColumn = (() => {
  const gate = 'activeContextTab === "knowledge" ? (';
  const from = chat.indexOf(gate);
  assert.ok(from > 0, "expected the activeContextTab chain to still exist");
  const ternary = chat.indexOf(') : view === "chat" ? (', from);
  assert.ok(ternary > from, "the chat column must be gated on view === \"chat\"");
  return chat.slice(ternary);
})();

test("the chat column is gated on the chat view", () => {
  // Asserted against the whole file, not the slice: `chatColumn` deliberately
  // begins *at* the gate, so the preceding `activeContextTab === "knowledge"`
  // that proves it is a branch rather than a bare `else` is not inside it.
  assert.match(
    chat,
    /activeContextTab === "knowledge"[\s\S]{0,400}?\) : view === "chat" \? \(/,
    "the chat column must be the `view === \"chat\"` branch, not the bare else",
  );
  // …and it must not still be the bare else as well.
  assert.doesNotMatch(chat, /activeContextTab === "knowledge" \? \([\s\S]{0,200}?\n\s*\) : \(\s*<div className="flex-1 flex flex-col/);
});

test("the gate has a final alternative so no view falls through to chat", () => {
  // Without `: null` the chain is a syntax error, but the intent is worth
  // pinning: the chat branch is a *branch*, so there must be something after it
  // for every other view id.
  assert.match(chatColumn, /<\/div>\s*\)\s*:\s*null\s*\}/, "the chain must end in `: null}`");
});

test("the transcript viewport and composer are inside that gate", () => {
  // Both markers must appear *after* the gate, not merely exist in the file.
  for (const marker of ['role="log"', "<Composer"]) {
    const at = chat.indexOf(marker);
    assert.ok(at > 0, `expected ${marker} to exist`);
    const gateAt = chat.indexOf(') : view === "chat" ? (');
    assert.ok(at > gateAt, `${marker} renders outside the chat-only gate`);
  }
});

test("the Project Inspector drawer is chat-only", () => {
  // It shares a flex row with the chat column and only ever appeared as a
  // right-hand sidebar because that column filled the space in front of it.
  // Ungated, it became the row's only child once the chat column was correctly
  // removed and rendered as a 320px block against the left edge — measured on
  // Overview, clipping the atlas at the 540px line where the drawer began.
  assert.match(
    chat,
    /\{view === "chat" && inspectorOpen && \(/,
    "the drawer must be gated on the chat view, not left to the flex row",
  );
  assert.doesNotMatch(chat, /\{inspectorOpen && \(/);
});

test("the chat sub-tabs stay reachable from the chat view", () => {
  // files / tasks / knowledge are both workspace views and chat sub-tabs.
  // Gating them on `view === "chat"` would make the sub-tabs unreachable.
  for (const tab of ['"files"', '"tasks"', '"knowledge"']) {
    assert.match(chat, new RegExp(`activeContextTab === ${tab.replace(/"/g, '"')}`));
  }
  assert.doesNotMatch(chat, /activeContextTab === "files" \? view === "chat"/);
});