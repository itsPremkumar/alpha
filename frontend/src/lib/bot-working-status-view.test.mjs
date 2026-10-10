// bot-working-status-view.test.mjs — the rendered half of "is this bot working?"
//
// The strip and the badge are where the liveness engine's verdict becomes
// something a person reads, so the honesty rules have to hold in the markup:
//
//   * a counter the server omitted renders as a dash with words beside it,
//     never as `0` (which would claim the engine found none);
//   * a failed read shows the server's own REASON, and the cards then say
//     their reading is presence-only rather than wearing a verdict;
//   * the loading state carries no number at all;
//   * a liveness word from a newer Gateway prints verbatim rather than
//     snapping to a badge this build knows;
//   * a card with no verdict passed in renders no working claim at all.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const { pathToFileURL } = require("node:url");
const read = (relative) =>
  readFileSync(new URL(relative, import.meta.url), "utf8");

const transpile = (source, extra = {}) =>
  ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      ...extra,
    },
  }).outputText;

/**
 * The components are written to a temp directory as real `.mjs` files.
 *
 * They are not inlined into `data:` URLs, because the modules import each other
 * by specifier and nesting one data-URL inside another double-decodes the
 * percent escapes: `bot-working-status.ts` imports `./api-client`,
 * `WorkingStatusView` imports `./bot-working-status`, and each layer of nesting
 * re-encodes the previous one — leaving the innermost module looking like
 * `import%20%7B%20apiFetch%20%7D`. Writing sibling files keeps exactly the
 * relative imports the compiler emitted, which is what the build itself does.
 */
const sandbox = mkdtempSync(join(tmpdir(), "bot-working-status-view-"));
const write = (name, source) => {
  const path = join(sandbox, name);
  writeFileSync(path, source, "utf8");
  return path;
};

const reactUrl = pathToFileURL(require.resolve("react")).href;
const jsxRuntimeUrl = pathToFileURL(require.resolve("react/jsx-runtime")).href;
// lucide-react publishes only its package root through `exports`, so the ESM
// bundle is addressed by path rather than by subpath.
const lucideUrl = new URL(
  "../../node_modules/lucide-react/dist/esm/lucide-react.js",
  import.meta.url,
).href;

/** Compile one source file, rewriting alias and third-party specifiers. */
const compile = (source, aliases = {}, jsx = false) => {
  let code = transpile(source, jsx ? { jsx: ts.JsxEmit.ReactJSX } : {})
    .replace(/from\s+"react"/g, `from "${reactUrl}"`)
    .replace(/from\s+"react\/jsx-runtime"/g, `from "${jsxRuntimeUrl}"`)
    .replace(/from\s+"lucide-react"/g, `from "${lucideUrl}"`);
  for (const [specifier, target] of Object.entries(aliases)) {
    code = code.replace(
      new RegExp(
        `from\\s+"${specifier.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`,
      ),
      `from "${target}"`,
    );
  }
  return code;
};

const apiClientFile = write(
  "api-client.mjs",
  `export async function apiFetch(){ throw new Error("no transport in a render test"); }`,
);
const nextLinkFile = write(
  "next-link.mjs",
  `export default function Link() { return null; }`,
);

const timeFile = write("time.mjs", compile(read("../lib/time.ts")));
const botsFile = write(
  "bots.mjs",
  compile(read("../lib/bots.ts"), {
    "./api-client": pathToFileURL(apiClientFile).href,
  }),
);
const uiFile = write(
  "ui.mjs",
  compile(
    read("../components/ui.tsx"),
    {
      "@/lib/a11y": pathToFileURL(
        write("a11y.mjs", compile(read("../lib/a11y.ts"))),
      ).href,
    },
    true,
  ),
);
const workingFile = write(
  "bot-working-status.mjs",
  compile(read("../lib/bot-working-status.ts"), {
    "./api-client": pathToFileURL(apiClientFile).href,
    "./time": pathToFileURL(timeFile).href,
  }),
);
const workingViewFile = write(
  "WorkingStatusView.mjs",
  compile(
    read("../components/bots/WorkingStatusView.tsx"),
    { "@/lib/bot-working-status": pathToFileURL(workingFile).href },
    true,
  ),
);
const livenessStripFile = write(
  "BotLivenessStrip.mjs",
  compile(
    read("../components/bots/BotLivenessStrip.tsx"),
    { "@/components/ui": pathToFileURL(uiFile).href },
    true,
  ),
);
const profileCardFile = write(
  "BotProfileCard.mjs",
  compile(
    read("../components/bots/BotProfileCard.tsx"),
    {
      "@/lib/bots": pathToFileURL(botsFile).href,
      "@/lib/time": pathToFileURL(timeFile).href,
      "@/types/bots": pathToFileURL(
        write("types-bots.mjs", compile(read("../types/bots.ts"))),
      ).href,
      "./WorkingStatusView": pathToFileURL(workingViewFile).href,
      "next/link": pathToFileURL(nextLinkFile).href,
    },
    true,
  ),
);

const { createElement } = await import(reactUrl);
const { renderToStaticMarkup } = await import(
  pathToFileURL(require.resolve("react-dom/server")).href
);

const workingView = await import(pathToFileURL(workingViewFile).href);
const livenessStrip = await import(pathToFileURL(livenessStripFile).href);
const profileCard = await import(pathToFileURL(profileCardFile).href);

const render = (Component, props) =>
  renderToStaticMarkup(createElement(Component, props));

const verdict = (over = {}) => ({
  key: "idle",
  label: "Idle",
  detail: "responsive (heartbeat 12s ago) · no task reported.",
  tone: "muted",
  working: false,
  evidence: "health",
  ...over,
});

/* ── 1. The badge and the detail line ─────────────────────────────────────── */

test("a stalled verdict is printed with the number and the lease it lost", () => {
  const status = verdict({
    key: "stalled",
    label: "Stalled",
    detail:
      "Stalled on task run_9 · its lease has expired · heartbeat 12s ago.",
    tone: "bad",
    working: false,
  });
  const markup = render(workingView.WorkingStatusBadge, { status });
  assert.match(markup, /data-working-key="stalled"/);
  assert.match(markup, />Stalled</);
  assert.match(
    markup,
    /run_9 · its lease has expired/,
    "the evidence rides in the tooltip",
  );
  const detail = render(workingView.WorkingStatusDetail, { status });
  assert.match(detail, /data-working-detail="stalled"/);
  assert.match(detail, /Stalled on task run_9/);
});

test("a liveness word from a newer Gateway is printed verbatim", () => {
  const markup = render(workingView.WorkingStatusBadge, {
    status: verdict({
      key: "unclassified",
      label: "quarantined",
      working: null,
    }),
  });
  assert.match(
    markup,
    />quarantined</,
    "the badge carries the server's own word",
  );
  assert.doesNotMatch(
    markup,
    /Idle|Working|Stalled/,
    "it must not be snapped to a word this build knows",
  );
});

test("a live run is printed as the working sentence, with its run id", () => {
  const status = verdict({
    key: "working",
    label: "Working",
    detail: 'Running on thread "investigate flaky timeout" · run af79cfa3 · working · for 42s · on union-alpha.',
    tone: "good",
    working: true,
  });
  const badge = render(workingView.WorkingStatusBadge, { status });
  assert.match(badge, /data-working-key="working"/);
  assert.match(badge, />Working</);
  const detail = render(workingView.WorkingStatusDetail, { status });
  assert.match(detail, /run af79cfa3/);
  assert.match(detail, /investigate flaky timeout/);
});

test("a paused bot finishing a run says both, not one", () => {
  const status = verdict({
    key: "paused",
    label: "Paused · run finishing",
    detail: "Running on thread \"x\" · run abc12345 · working · for 3m. Bot paused: suspicious spend — no new work is accepted.",
    tone: "warn",
    working: true,
  });
  const markup = render(workingView.WorkingStatusBadge, { status });
  assert.match(markup, /Paused · run finishing/, "the pause is not hidden by the running work");
  const detail = render(workingView.WorkingStatusDetail, { status });
  assert.match(detail, /run abc12345/);
  assert.match(detail, /suspicious spend/, "the operator's own reason survives beside the run");
});

test("no verdict means no claim, in both directions", () => {
  for (const status of [null, undefined]) {
    assert.equal(render(workingView.WorkingStatusBadge, { status }), "");
    assert.equal(render(workingView.WorkingStatusDetail, { status }), "");
  }
});

/* ── 2. The card renders the shared verdict ───────────────────────────────── */

const cardProps = (working) => ({
  bot: {
    name: "coder",
    display_name: "Coder",
    role: "Backend Engineer",
    department: "engineering",
    status: "active",
    model: "union-alpha",
    reputation_score: null,
    task_stats: { total_runs: 12, completed: 9 },
    toolsets: [],
    skills: [],
    capabilities: ["python"],
    responsibilities: [],
    routines: [],
    avatar: "",
    version: 1,
    last_active: null,
    unread_count: null,
    last_message_preview: null,
    last_message_at: null,
    last_message_sender: null,
    last_message_withheld: false,
  },
  isActive: false,
  onSelect: () => {},
  onChat: () => {},
});

test("the card shows the working line when it is handed one", () => {
  const markup = render(profileCard.BotProfileCard, {
    ...cardProps(),
    working: verdict({
      key: "working",
      label: "Working",
      detail: "Working on task run_42 · heartbeat 3s ago",
      tone: "good",
      working: true,
    }),
  });
  assert.match(markup, /Working on task run_42/);
  assert.match(markup, /\bWorking\b/);
});

test("the card without a verdict says no activity was recorded", () => {
  const markup = render(profileCard.BotProfileCard, { ...cardProps() });
  assert.match(markup, /no activity recorded/);
  assert.doesNotMatch(markup, /Working|Idle|Stalled/);
  // The status-not-reported disclosure stays put beside the new badge row.
  assert.match(
    markup,
    /status not reported|Active/,
    "the lifecycle badge is independent of the liveness badge",
  );
});

/* ── 3. The liveness strip: three states, three renderings ────────────────── */

const overview = {
  timestamp: "2026-10-09T10:00:00+00:00",
  fleet_health_score: 0.35,
  summary: {
    total: 57,
    healthy: 12,
    stale: 3,
    stalled: 2,
    dead: null,
    sleeping: 5,
    suspended: 1,
    archived: null,
  },
  stalled_workers: [{ bot_name: "coder", active_task_id: "run_9" }],
};

test("loading paints no number and announces itself", () => {
  const markup = render(livenessStrip.BotLivenessStrip, { state: "loading" });
  assert.match(markup, /aria-busy="true"/);
  assert.doesNotMatch(markup, />\d</, "a skeleton must not carry a count");
});

test("a failed read shows the server's reason and not an empty fleet", () => {
  const markup = render(livenessStrip.BotLivenessStrip, {
    state: "error",
    reason: "health monitor unreachable: 503",
  });
  assert.match(markup, /Liveness report unavailable/);
  assert.match(markup, /503/, "the server's own reason is the disclosure");
  assert.match(
    markup,
    /presence reading only/,
    "the fallback is named, not hidden",
  );
  assert.doesNotMatch(
    markup,
    />0</,
    "an unreachable monitor is not a fleet with no healthy bots",
  );
});

test("reported counters print, and omitted ones print a dash with words", () => {
  const markup = render(livenessStrip.BotLivenessStrip, {
    state: "ok",
    overview,
    workingCount: 1,
  });
  assert.match(markup, />12</, "a measured 12 must render as 12");
  assert.match(markup, />3</);
  assert.match(markup, />2</);
  assert.match(markup, />5</);
  assert.match(
    markup,
    /No heartbeat — not reported/,
    "the omitted state is labelled, not zeroed",
  );
  assert.doesNotMatch(markup, />0</, "dead: null must not render as 0");
  assert.match(markup, /0\.35/, "the score is the server's own number");
  assert.match(
    markup,
    /stalled: coder/,
    "a stalled worker is named, not just counted",
  );
  assert.match(markup, /bots with a run in flight/);
  assert.match(markup, /data-working-count="1"/);
});

test("the working count renders a state word, never a zero, when it cannot be read", () => {
  const still = render(livenessStrip.BotLivenessStrip, {
    state: "ok",
    overview,
    workingCount: "loading",
  });
  assert.match(still, /still reading/);
  assert.doesNotMatch(still, />0</);
  const broken = render(livenessStrip.BotLivenessStrip, {
    state: "ok",
    overview,
    workingCount: "unavailable",
    workingReason: "the run store could not be read: OperationalError",
  });
  assert.match(broken, /not reported/, "a failed read is a word, not an idle fleet");
  assert.match(broken, /OperationalError/, "the server's own reason is carried");
  assert.doesNotMatch(broken, />0</);
  // Omitted entirely: the strip stays a liveness strip and says nothing about
  // work rather than guessing at zero.
  const absent = render(livenessStrip.BotLivenessStrip, {
    state: "ok",
    overview,
  });
  assert.doesNotMatch(absent, /bots with a run in flight/);
});

test("an entirely unreported summary renders dashes, never a zeroed fleet", () => {
  const markup = render(livenessStrip.BotLivenessStrip, {
    state: "ok",
    overview: {
      timestamp: null,
      fleet_health_score: null,
      summary: {
        total: null,
        healthy: null,
        stale: null,
        stalled: null,
        dead: null,
        sleeping: null,
        suspended: null,
        archived: null,
      },
      stalled_workers: [],
    },
  });
  assert.doesNotMatch(markup, />\d</, "no counter prints a number");
  const unreported = markup.match(/not reported/g) || [];
  assert.ok(
    unreported.length >= 6,
    `every counter states why it is blank (found ${unreported.length})`,
  );
});
