// workspace-view-resolution.test.mjs — the view must resolve the SAME way on
// the server and on the client.
//
// The defect this pins, found by rendering pages rather than by asserting:
// `ChatView` seeded its view state with
//
//     workspaceViewFromSearch(typeof window === "undefined" ? "" : window.location.search)
//
// which makes the two renders disagree by construction. The server saw an empty
// query and rendered `chat`; the client saw `?view=<id>` and rendered that view.
// React then threw a hydration mismatch (error #418) and **discarded the whole
// server-rendered tree** on every deep link to any non-chat view.
//
// It looked like a per-section problem because `chat` — the one view the server
// agreed on — was the only clean view. Measured proof: the SSR responses for
// `?view=chat`, `?view=overview` and `?view=reliability` were byte-identical, so
// the server was ignoring the parameter entirely.
//
// The fix moves the resolution to `app/page.tsx`, where `searchParams` is
// available on both sides, and passes the result down as `initialView`. These
// tests pin the rule rather than the diff, because the rule is what a future
// edit could break again.
//
// node --test src/lib/*.test.mjs — no server, no browser, no DOM shim.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

/**
 * Strip comments before asserting anything about code.
 *
 * Both files *document* the defect they were fixed for, so their comments quote
 * the exact anti-patterns these tests look for. A source pin that cannot tell
 * prose from code therefore fails on the explanation and not on the behaviour —
 * and would quietly stop testing anything if it were relaxed to "search the
 * comments too".
 */
function code(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "").replace(/[ \t]+\/\/.*$/gm, "");
}

const pageSource = read("../app/page.tsx");
const chatViewSource = read("../components/ChatView.tsx");
const chatView = code(chatViewSource);
const pageCode = code(pageSource);
const viewModule = read("./workspace-view.ts");

const dataUrl = (code) => `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`;
const transpile = (src) =>
  ts.transpileModule(src, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;

// workspace-view.ts has no imports, so it transpiles and loads directly.
const { isWorkspaceView, workspaceViewFromSearch } = await import(dataUrl(transpile(viewModule)));

/* ── The resolution rule, which both sides must share ───────────────────── */

test("an absent or unknown view resolves to chat", () => {
  assert.equal(workspaceViewFromSearch(""), "chat");
  assert.equal(workspaceViewFromSearch("?view="), "chat");
  assert.equal(workspaceViewFromSearch("?view=not_a_real_view"), "chat");
  assert.equal(workspaceViewFromSearch("?other=1"), "chat");
});

test("every registered view resolves from the query string", () => {
  for (const view of ["overview", "chat", "system", "bots", "reliability", "workflows", "settings"]) {
    assert.equal(isWorkspaceView(view), true, `${view} should be routable`);
    assert.equal(workspaceViewFromSearch(`?view=${view}`), view);
  }
});

test("a repeated view parameter takes the first value", () => {
  // The server receives a list for `?view=a&view=b`; taking the first keeps the
  // server and `URLSearchParams.get` in agreement instead of silently
  // resolving to whichever one the runtime happened to pick.
  assert.equal(workspaceViewFromSearch("?view=system&view=overview"), "system");
});

/* ── The server must do the resolving ───────────────────────────────────── */

test("the page resolves the view from searchParams instead of the browser", () => {
  assert.match(pageCode, /searchParams/, "the page must read the request's searchParams");
  assert.match(pageCode, /isWorkspaceView\(candidate\)/, "and validate the requested id");
  assert.match(pageCode, /initialView=\{initialView\}/, "and pass the resolved view down");
});

test("the page never reads window.location", () => {
  // A server component that reaches for `window` either throws during SSR or
  // diverges from the client — the exact failure being pinned here.
  assert.doesNotMatch(pageCode, /window\.location/);
  assert.doesNotMatch(pageCode, /typeof window/);
});

/* ── The client must not re-derive it during render ────────────────────── */

test("the view state is seeded from the prop, not from the URL", () => {
  assert.match(
    chatView,
    /useState<WorkspaceView>\(\(\) => initialView \?\? "chat"\)/,
    "the initial view must come from the prop the server already resolved",
  );
});

test("no state initialiser reads window.location for the view", () => {
  // Pinned precisely rather than by scanning every initialiser: a broad scan
  // that captures lazily will happily span a statement boundary and match the
  // *effect* below, which is required to read the URL, and then fail on correct
  // code. The rule is about the initialiser only.
  assert.doesNotMatch(chatView, /useState<WorkspaceView>\(\(\) =>\s*workspaceViewFromSearch/);
  // The exact anti-pattern, in code (comments are stripped above).
  assert.doesNotMatch(chatView, /typeof window === "undefined"\s*\?\s*""\s*:\s*window\.location\.search/);
});

test("the browser fallback runs in an effect, never during render", () => {
  // A mount that did not receive `initialView` still has to honour the URL, but
  // only after hydration — an effect is what makes that safe.
  assert.match(
    chatView,
    /useEffect\(\(\) => \{[\s\S]{0,160}?if \(initialView\) return;[\s\S]{0,160}?setView\(workspaceViewFromSearch\(window\.location\.search\)\)/,
    "the fallback must read the URL inside an effect gated on the prop",
  );
});

test("ChatView accepts the prop and defaults it rather than requiring it", () => {
  assert.match(
    chatView,
    /function ChatView\(\{ initialView \}: \{ initialView\?: WorkspaceView \} = \{\}\)/,
    "the prop is optional so the bot profile route can mount ChatView unchanged",
  );
});