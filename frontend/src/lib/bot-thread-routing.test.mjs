import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

const chatView = readFileSync(
  fileURLToPath(new URL("../components/ChatView.tsx", import.meta.url)),
  "utf8",
);

test("committing a bot-scoped draft keeps its bot selected during first-turn routing", () => {
  const restore = chatView.slice(
    chatView.indexOf("// Restore this conversation's specialist bot once bots are known."),
    chatView.indexOf("// Auto-scroll follows the answer"),
  );

  assert.match(
    restore,
    /localThreadMetaRef\.current\[activeThreadId\][\s\S]{0,180}\|\|\s*threads\.find\(\(thread\) => thread\.thread_id === activeThreadId\)\?\.botName/,
    "the newly committed draft carries botName in `threads` before its ref metadata is available",
  );
  assert.match(restore, /\}, \[activeThreadId, bots, threads\]\);/);
});
