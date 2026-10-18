import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

/**
 * The smoke probe's client (`lib/bots.ts` → `runBotSmoke` / `botSmokeBadge`)
 * pinned against the route the Gateway really mounts
 * (`POST /api/bots/{name}/smoke`) and the badge vocabulary the plan fixes:
 *
 *  - the request is a bare POST to `/bots/{name}/smoke` — no body: the route
 *    resolves the bot from the path and runs the probe server-side;
 *  - a terminal FAILED or timed-out probe resolves — it is a recorded result,
 *    and `ok` is what says whether a terminal result was observed at all;
 *    treating a failed probe as a transport error would drop exactly the
 *    evidence the operator needs;
 *  - a non-2xx (403 non-admin, 404 unknown bot, 409 busy thread) rejects with
 *    the server's own text — never painted as a smoke result;
 *  - the badge says `created · unverified` until a smoke is RECORDED, `smoke
 *    passed` (with the run id) only on a passed record, and no state renders
 *    as "alive"; a failed profile read is "verification unknown", which is
 *    NOT the same claim as "created · unverified".
 */

const compiled = new Map(
  ["api-client", "bots"].map((name) => [
    name,
    ts.transpileModule(readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText,
  ]),
);

function load(name, dependencies = {}) {
  const exports = {};
  new Function("exports", "require", "process", "console", "fetch", compiled.get(name))(
    exports,
    (dependency) => {
      assert.ok(Object.hasOwn(dependencies, dependency), `Unexpected dependency: ${dependency}`);
      return dependencies[dependency];
    },
    { env: {} },
    { error: () => {} },
    () => {
      throw new Error("Raw fetch must not be used");
    },
  );
  return exports;
}

/** The bodies a queued stub answers with, in order. Mirrors apiFetch: non-2xx throws. */
function client(responses) {
  const queue = [...responses];
  const calls = [];
  const bots = load("bots", {
    "./api-client": {
      apiFetch: async (path, init = {}) => {
        calls.push({ path, method: init.method || "GET", body: init.body ? JSON.parse(init.body) : undefined });
        const r = queue.shift();
        assert.ok(r, `no canned response queued for ${path}`);
        if (r.status && (r.status < 200 || r.status >= 300)) {
          throw new Error(`Request failed (HTTP ${r.status}).${r.detail ? ` ${r.detail}` : ""}`);
        }
        return { json: async () => r.body };
      },
    },
  });
  return { calls, bots };
}

const response = (body, status = 200) => ({ body, status });

test("runBotSmoke POSTs the bot's smoke route and maps the recorded outcome", async () => {
  const { calls, bots } = client([
    response({
      ok: true,
      name: "quill",
      alive: true,
      smoke: {
        status: "passed",
        run_id: "r-1",
        thread_id: "bot-chat-quill",
        observed_status: "success",
        answered: true,
        detail: null,
        at: "2026-10-18T00:00:00Z",
      },
      run_id: "r-1",
      thread_id: "bot-chat-quill",
      reply: "Quill standing by.",
      message_count: 3,
    }),
  ]);

  const result = await bots.runBotSmoke("quill");

  assert.deepEqual(calls, [{ path: "/bots/quill/smoke", method: "POST", body: undefined }]);
  assert.equal(result.ok, true);
  assert.equal(result.alive, true);
  assert.equal(result.smoke.status, "passed");
  assert.equal(result.smoke.run_id, "r-1");
  assert.equal(result.runId, "r-1");
  assert.equal(result.reply, "Quill standing by.");
  assert.equal(result.messageCount, 3);
});

test("a failed probe is a recorded result, not a transport error", async () => {
  const { bots } = client([
    response({
      ok: true,
      name: "quill",
      alive: false,
      smoke: { status: "failed", run_id: "r-2", observed_status: "error", detail: "model refused", answered: false },
      run_id: "r-2",
      reply: null,
      message_count: 0,
    }),
  ]);

  const result = await bots.runBotSmoke("quill");
  assert.equal(result.ok, true, "a terminal failure was still observed");
  assert.equal(result.alive, false);
  assert.equal(result.smoke.status, "failed");
  assert.equal(result.smoke.detail, "model refused");
});

test("a timed-out probe answers ok: false and still resolves", async () => {
  const { bots } = client([
    response({
      ok: false,
      name: "quill",
      alive: false,
      smoke: {
        status: "timeout",
        run_id: "r-3",
        observed_status: "running",
        detail: "still running — interrupted",
        answered: false,
      },
      run_id: "r-3",
      reply: null,
      message_count: 0,
    }),
  ]);

  const result = await bots.runBotSmoke("quill");
  assert.equal(result.ok, false, "no terminal result was observed");
  assert.equal(result.alive, false);
  assert.equal(result.smoke.status, "timeout");
});

test("a non-2xx refuses with the server's own text", async () => {
  const { bots } = client([
    { status: 403, detail: "Admin privileges are required for this action." },
    { status: 404, detail: "Bot 'ghost' not found" },
  ]);

  await assert.rejects(() => bots.runBotSmoke("quill"), /Admin privileges are required/);
  await assert.rejects(() => bots.runBotSmoke("ghost"), /Bot 'ghost' not found/);
});

test("normalizeBot maps the recorded smoke and the server's alive verdict", () => {
  const { bots } = client([]);

  const never = bots.normalizeBot({ name: "quill" });
  assert.equal(never.smoke, null, "no recorded smoke is null, not a verdict");
  assert.equal(never.alive, false);

  const passed = bots.normalizeBot({
    name: "quill",
    alive: true,
    smoke: { status: "passed", run_id: "r-1", answered: true },
  });
  assert.equal(passed.smoke.status, "passed");
  assert.equal(passed.smoke.run_id, "r-1");
  assert.equal(passed.smoke.observed_status, null, "an absent field is null, never ''");
  assert.equal(passed.alive, true);

  const malformed = bots.normalizeBot({ name: "quill", smoke: { run_id: "r-1" } });
  assert.equal(malformed.smoke, null, "a smoke blob with no status is not a verdict");
});

test("the badge vocabulary: unverified → passed, and never 'alive'", () => {
  const { bots } = client([]);

  const unverified = bots.botSmokeBadge({ smoke: null });
  assert.equal(unverified.state, "unverified");
  assert.equal(unverified.label, "created · unverified");
  assert.equal(unverified.runId, null);

  const passed = bots.botSmokeBadge({ smoke: { status: "passed", run_id: "r-1", detail: null } });
  assert.equal(passed.state, "passed");
  assert.equal(passed.label, "smoke passed");
  assert.equal(passed.runId, "r-1", "the badge shows the run id the verdict came from");

  const failed = bots.botSmokeBadge({ smoke: { status: "failed", run_id: "r-2", detail: "model refused" } });
  assert.equal(failed.label, "smoke failed");
  assert.equal(failed.detail, "model refused");

  const timeout = bots.botSmokeBadge({ smoke: { status: "timeout", run_id: "r-3", detail: "still running" } });
  assert.equal(timeout.label, "smoke timed out");

  // A verdict this build does not know is shown verbatim — never snapped.
  const future = bots.botSmokeBadge({ smoke: { status: "quarantined", run_id: "r-4", detail: null } });
  assert.equal(future.state, "unknown");
  assert.equal(future.label, "smoke quarantined");

  // No state may render as "alive": that word belongs to the forge/route
  // result, never to a badge this client synthesizes.
  for (const view of [unverified, passed, failed, timeout, future]) {
    assert.doesNotMatch(view.label.toLowerCase(), /alive/);
  }

  // A failed profile read is NOT "created · unverified": nothing is known.
  const unknown = bots.botSmokeBadge(null);
  assert.equal(unknown.state, "unknown");
  assert.match(unknown.label, /verification unknown/);
  assert.doesNotMatch(unknown.label, /created/);
});

test("the detail page renders the probe action and the badge from the client", () => {
  const view = readFileSync(new URL("../components/bots/BotDetailView.tsx", import.meta.url), "utf8");
  const primitives = readFileSync(
    new URL("../components/bots/detail-primitives.tsx", import.meta.url),
    "utf8",
  );

  assert.match(view, /runBotSmoke\(name\)/, "the Run smoke button drives the route");
  assert.match(view, /<SmokeBadge badge=\{botSmokeBadge\(bot\)\} \/>/, "the badge renders the client view-model");
  // Presentation lives in the primitives module; the view must not grow a
  // second badge with its own vocabulary.
  assert.match(primitives, /export function SmokeBadge\(/);
  assert.doesNotMatch(view, /function SmokeBadge\(/);
});
