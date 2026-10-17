// chat-empty-state-leanness.test.mjs — the empty chat names the specialist once.
//
// THE DEFECT (the operator's own screenshot): before the first message the chat
// named the same agent three or four times and spent the whole viewport on it.
// "Chatting as Architect — System Architect & Technical Lead" in the banner, then
// "Talking to Architect (System Architect & Technical Lead). Switch specialists
// anytime from the Bots tab." below it, then an active chip reading "Architect",
// then "You are working with Architect in standalone mode." Two of those also
// carried copy that was wrong or aimed at a developer: the sentence told the user
// to switch "from the Bots tab" while the chips beneath it switched specialists,
// and the landing told an ordinary user to "Set your name in Settings".
//
// These pins exist so the duplication cannot quietly come back.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");

const chatView = read("../components/ChatView.tsx");
const landing = read("../components/chat-shell/ChatShellLanding.tsx");

/**
 * Source with comments stripped.
 *
 * These pins assert that specific copy is GONE, so a comment that *explains*
 * what was removed — and quotes it, as the good ones here do — would otherwise
 * read as the copy still being on screen. The behaviour is the rendered string,
 * not the prose a developer wrote about it.
 */
const code = (source) =>
  source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");

const chat = code(chatView);
const land = code(landing);

test("the empty state does not re-narrate the specialist", () => {
  assert.ok(!chat.includes("Talking to "), "the duplicate specialist sentence is gone");
  assert.ok(
    !chat.includes("Switch specialists anytime from the Bots tab"),
    "the copy that contradicted the chips beside it is gone",
  );
});

test("the empty state offers no specialist chips and no raw bot count", () => {
  // Both were a second and third copy of a switcher that already exists in the
  // sidebar rail (BotWorkspaceRail renders "Agent options for <bot>").
  assert.ok(!chat.includes("View all {bots.length}"), "the raw bot count is gone");
  assert.ok(
    !/bots\.slice\(0, 5\)/.test(chat),
    "the empty-state specialist chip row is gone",
  );
});

test("the chat banner names the specialist once, without the role inline", () => {
  assert.ok(!chat.includes("Chatting as"), "the banner no longer prefixes the name");
  assert.ok(chat.includes("Use default"), "the reset action is still there");
  assert.ok(
    !chat.includes("Reset to {DEFAULT_AGENT_NAME}"),
    '"Reset to Alpha" is ambiguous while chatting as a specialist',
  );
  // The role is still available, just not shouted on every line.
  assert.match(chat, /title=\{activeBot\.role \|\| undefined\}/);
});

test("the landing shows the greeting and nothing else", () => {
  assert.ok(land.includes("landingGreeting"), "the greeting is kept");
  for (const gone of [
    "Set your name in",
    "standalone mode",
    "How can I help you today?",
    "ANONYMOUS_LABEL",
    "contextSentence",
  ]) {
    assert.ok(!land.includes(gone), gone + " must be gone from the landing");
  }
});

test("the starter cards are gone", () => {
  for (const card of [
    "Create a new plan",
    "Write or improve code",
    "Review the project",
    "Ask a question",
  ]) {
    assert.ok(!land.includes(card), 'the "' + card + '" starter card must be gone');
  }
  // The props stay accepted so the caller keeps compiling, but nothing renders.
  assert.match(land, /onPickStarter\?/);
  assert.match(land, /onReviewProject\?/);
});

test("the hero avatar and its tested class survive the trim", () => {
  // `size-18` silently generated nothing once; the arbitrary value is pinned by
  // lib/tailwind-class-guard.test.mjs, so the class must not be "simplified".
  assert.match(land, /size-\[4\.5rem\]/);
  assert.match(land, /DEFAULT_AGENT_AVATAR/);
  assert.match(land, /data-shell="landing"/);
});

test("the empty state carries one identity, not a wordmark plus an avatar", () => {
  // The product wordmark sat directly above the specialist's own avatar, so one
  // empty screen showed two identities — and the wordmark only repeated what the
  // sidebar rail and the browser tab already say.
  assert.ok(!chat.includes("BrandLogo"), "the wordmark is gone from the empty state");
  // The region keeps its accessible name even though the logo left.
  assert.match(chat, /aria-label=\{branding\.name\}/);
  // And the landing still draws the specialist's face, so the screen is not bare.
  assert.match(land, /botAvatar/);
});