import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const chatView = readFileSync(
  new URL("../components/ChatView.tsx", import.meta.url),
  "utf8",
);

test("new conversations start with bounded subagent fan-out", () => {
  assert.match(
    chatView,
    /const \[subagentConcurrency, setSubagentConcurrency\] = useState\(1\);/,
  );
  assert.match(
    chatView,
    /const \[subagentTotal, setSubagentTotal\] = useState\(3\);/,
  );
});
