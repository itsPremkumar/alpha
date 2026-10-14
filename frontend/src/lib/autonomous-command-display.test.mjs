import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const messageItem = readFileSync(
  new URL("../components/MessageItem.tsx", import.meta.url),
  "utf8",
);

test("autonomous command receipt displays the measured execution result", () => {
  assert.match(messageItem, /autonomousDetection\?\.execution_result/);
  assert.match(messageItem, /Command result:/);
  assert.match(messageItem, /autonomousExecution\?\.status/);
  assert.match(messageItem, /shownAutonomousOutput/);
  assert.match(
    messageItem,
    /command detection alone does not confirm an action/,
  );
});
