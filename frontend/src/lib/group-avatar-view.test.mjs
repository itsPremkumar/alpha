// group-avatar-view.test.mjs — the group picture is actually wired into both
// group surfaces in Messages, and is labelled there rather than only in the
// helper. A helper nobody renders is not a feature.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");

const messages = read("../components/sections/MessagesSection.tsx");

test("the conversation row draws the group's picture", () => {
  assert.match(messages, /groupAvatarEmoji\(c\.title\.slice\(2\)\)/);
});

test("the open group's header draws the same picture", () => {
  assert.match(messages, /groupAvatarEmoji\(sel\.name\)/);
});

test("both group pictures carry the generated label", () => {
  const labels = messages.match(/GROUP_AVATAR_GENERATED_TITLE/g) ?? [];
  // One import + two call sites; the point is that it reaches the DOM, so at
  // least two sites must pass it through.
  assert.ok(
    labels.length >= 3,
    `expected the label imported and passed at both group sites, found ${labels.length} references`,
  );
});

test("the generic group icon survives as the nameless-room fallback", () => {
  // A room with no name to derive from must keep the plain group icon rather
  // than a picture that stands for nothing.
  assert.match(messages, /<Users className="size-4" \/>/);
});

test("an agent's own picture is never replaced by a group picture", () => {
  // The group branch is chosen by `kind`, so a direct thread keeps the agent
  // avatar lookup instead.
  assert.match(
    messages,
    /avatarByName\[activeTitle\]/,
  );
});