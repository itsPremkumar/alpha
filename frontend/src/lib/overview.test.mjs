// overview.test.mjs — honesty pins for the Overview atlas client.
//
// Every domain must fail alone: one 404/403/timeout blanks exactly one card,
// never the whole snapshot. A count the server did not send renders as null
// (the UI shows an em-dash with words), never zero.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const SRC = readFileSync(new URL("./overview.ts", import.meta.url), "utf8");

test("overview reads every domain independently", () => {
  for (const path of ["/bots", "/projects", "/workflows", "/skills", "/memory", "/scheduled-tasks", "/channels", "/agents", "/console/runs?limit=3"]) {
    assert.ok(SRC.includes(`"${path}"`), `overview reads ${path}`);
  }
  assert.match(SRC, /Promise\.all\(\[/, "domains are read concurrently");
});

test("a failed domain keeps its server reason and a null count", () => {
  assert.match(SRC, /error: errMsg\(e\)/, "failure carries the server reason");
  assert.match(SRC, /count: null, error/, "failure count is null, not zero");
});

test("fetchOverview never rejects — failures are data", () => {
  assert.match(SRC, /export async function fetchOverview\(\): Promise<OverviewSnapshot>/);
  assert.doesNotMatch(SRC, /throw new Error/, "aggregation must not throw");
  assert.match(SRC, /failed: domains\.filter/, "header names the domains that did not answer");
});

test("unmeasured counts are null, not zero", () => {
  assert.match(SRC, /return null;/, "unreadable shapes map to null");
  assert.match(SRC, /count: number \| null/, "count is nullable");
});

test("the client uses only get (read-only atlas)", () => {
  assert.match(SRC, /import \{ get, asList, errMsg \} from "\.\/http"/);
  assert.doesNotMatch(SRC, /\bsend\s*\(/, "atlas performs no mutations");
});

test("the atlas is deep-linkable: ChatView boots its view from the URL", () => {
  // `lib/workspace-view.ts` shipped `workspaceViewFromSearch` with no callers,
  // so `?view=overview` (and the `?demo=1` sample flag the atlas reads) loaded
  // chat anyway and the atlas had no URL at all.
  const chatView = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");
  assert.match(chatView, /workspaceViewFromSearch/, "ChatView reads the view from the URL");
  assert.match(chatView, /window\.location\.search/, "the read happens client-side with an SSR guard");
  assert.match(chatView, /typeof window === "undefined"/, "server rendering keeps the old chat default");
});

test("the atlas is one click away from chat: the shared top bar links it", () => {
  // NavTabs only renders for non-chat views, so from the chat screen — where a
  // user lives — no tab could reach the atlas. The profile menu in the one
  // header every view shares now carries it beside System Monitor.
  const topBar = readFileSync(new URL("../components/chat-shell/WorkspaceTopBar.tsx", import.meta.url), "utf8");
  assert.match(topBar, /onOpenView\("overview"\)/, "the top bar opens the overview");
  assert.match(topBar, /Overview atlas/, "the entry is labelled in words, not an icon alone");
});
