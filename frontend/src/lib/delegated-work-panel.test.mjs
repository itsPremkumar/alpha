import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const panel = read("../components/sections/DelegatedWorkPanel.tsx");
const section = read("../components/sections/SubagentsSection.tsx");

test("delegated work monitor is mounted for the active conversation", () => {
  assert.match(section, /<DelegatedWorkPanel threadId=\{props\.threadId\}\s*\/>/);
  assert.match(panel, /fetchThreadDelegations\(threadId\)/);
  assert.doesNotMatch(panel, /fetchLiveSubagentsStrict|\/subagents\/control/);
});

test("conversation history separates active, terminal, and unknown statuses", () => {
  assert.match(panel, /Active · \$\{active\.length\}/);
  assert.match(panel, /Done · \$\{done\.length\}/);
  assert.match(panel, /Other reported states · \$\{other\.length\}/);
  assert.match(panel, /isTerminalDelegation\(item\.status\)/);
});

test("expanded delegation detail preserves task and run attribution plus the actual outcome", () => {
  assert.match(panel, /Source run/);
  assert.match(panel, /item\.runId/);
  assert.match(panel, /Reported result/);
  assert.match(panel, /item\.resultBrief/);
  assert.match(panel, /item\.stopReason/);
});

test("absent history and unreadable history are not presented as empty success", () => {
  assert.match(panel, /did not include a delegation ledger/);
  assert.match(panel, /draft has no server conversation yet/);
  assert.match(panel, /Conversation delegation history is unavailable/);
});

test("live thread ledger refreshes only while it reports in-progress work", () => {
  assert.match(panel, /const isActiveDelegation = \(status: string \| null\) => status === "in_progress"/);
  assert.match(panel, /window\.setInterval\(\(\) => void load\(true\), 5000\)/);
});
