import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const sidebar = readFileSync(new URL("../components/ThreadSidebar.tsx", import.meta.url), "utf8");
const chatView = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");

test("New Chat click handlers do not pass the React event as a project id", () => {
  assert.equal(
    (sidebar.match(/onClick=\{\(\) => onNewChat\(\)\}/g) || []).length,
    2,
    "the collapsed and expanded sidebar actions must both discard the click event",
  );
  assert.doesNotMatch(sidebar, /onClick=\{onNewChat\}/);
  assert.match(chatView, /onNewChat=\{\(\) => handleNewChat\(\)\}/);
});
