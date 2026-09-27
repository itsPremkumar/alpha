// test_fe_audit_command_registry.test.mjs — the slash-command registry read
// must not turn a failure into "the server has no commands".
//
// `apiFetch` (api-client.ts:106-125) never returns a non-2xx Response: it
// throws `ApiClientError` carrying the gateway's own `detail`. `fetchCommands`
// and `searchCommands` wrapped that call in `catch { return [] }`, so a down
// Gateway, a 500, and a genuine "the registry is empty" were the same value.
// `Composer.tsx` then kept its local DEFAULT_CORE_COMMANDS and said nothing, so
// a broken registry was indistinguishable from a healthy one — which is exactly
// the "an empty list must mean the server said there was nothing, not that the
// call failed" rule in frontend/AGENTS.md and frontend/src/AGENTS.md.
//
// The sibling client for the SAME two routes, `lib/commands.ts:19-27`, already
// rejects with the server's reason; this pins `lib/api.ts` to the same contract.
//
// Pure Node test: transpiles the real api.ts and swaps only its ./api-client
// import for a faithful stub. No server, no browser.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const HERE = fileURLToPath(new URL(".", import.meta.url));
const FRONTEND = join(HERE, "..", "..");

const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;
const transpile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
const read = (name) => readFileSync(join(HERE, name), "utf8");

/* ── Stub: api-client backed by a queue of canned failures/responses ──────── */
const apiClientStub = `
export class ApiClientError extends Error {
  // Mirrors api-client.ts:33-45, message construction included, so an assertion
  // on the text proves what a caller's catch block would really show.
  constructor(kind, status = 0, detail = null) {
    const validStatus = Number.isInteger(status) && status >= 100 && status <= 599 ? status : 0;
    super(kind === "http"
      ? \`Request failed\${validStatus ? \` (HTTP \${validStatus})\` : ""}.\${detail ? \` \${detail}\` : ""}\`
      : kind === "stopped" ? "Request stopped locally."
      : kind === "response" ? "The server returned an unreadable response."
      : kind === "route" ? "Invalid API route."
      : "The request could not be completed. Check your connection.");
    this.name = "ApiClientError";
    this.kind = kind;
    this.status = validStatus;
    this.detail = detail;
  }
}
export const GATEWAY_BASE = "http://gateway.test/api";
let queue = [];
export function setResponses(responses) { queue = [...responses]; }
export async function apiFetch(_path, _init) {
  const r = queue.length > 0 ? queue.shift() : undefined;
  if (!r) throw new Error("no canned response queued for " + _path);
  if (r.error) throw r.error;
  const status = r.status ?? 200;
  const res = new Response(JSON.stringify(r.body ?? {}), {
    status,
    headers: { "Content-Type": "application/json" },
  });
  // Faithful to the real client: a non-2xx never comes back as a Response.
  if (!res.ok) throw new ApiClientError("http", status, r.detail ?? null);
  return res;
}
`;
const apiClientUrl = toDataUrl(apiClientStub);

/* ── REAL api.ts, only its ./api-client import swapped for the stub ───────── */
let apiCode = transpile(read("./api.ts"));
apiCode = apiCode.replace(/from\s+"\.\/api-client"/g, `from "${apiClientUrl}"`);
assert.equal(apiCode.includes('"./api-client"'), false, "api.ts api-client specifier survived");

const { fetchCommands, searchCommands } = await import(toDataUrl(apiCode));
const { setResponses, ApiClientError } = await import(apiClientUrl);

const GATEWAY_DOWN = () => ({ error: new ApiClientError("network") });
const REGISTRY_500 = () => ({ status: 500, detail: "command registry is not loaded" });
const SEARCH_404 = () => ({ status: 404, detail: "Not Found" });

test("a down Gateway makes fetchCommands reject, not resolve to an empty command list", async () => {
  setResponses([GATEWAY_DOWN()]);
  await assert.rejects(
    () => fetchCommands(),
    (err) => {
      assert.ok(err instanceof ApiClientError, `expected the transport's own error, got ${err}`);
      assert.equal(err.kind, "network");
      return true;
    },
    "fetchCommands resolved instead of rejecting: a down backend is being reported as 'no commands'",
  );
});

test("a 500 from the registry reaches the caller with the server's reason attached", async () => {
  setResponses([REGISTRY_500()]);
  await assert.rejects(
    () => fetchCommands(),
    (err) => {
      assert.ok(err instanceof ApiClientError, `expected ApiClientError, got ${err}`);
      assert.equal(err.status, 500);
      assert.equal(err.detail, "command registry is not loaded", "the gateway's own detail must survive");
      assert.match(err.message, /command registry is not loaded/, `reason missing from "${err.message}"`);
      return true;
    },
    "fetchCommands swallowed the 500 and returned an empty list",
  );
});

test("searchCommands rejects on failure for the same reason fetchCommands does", async () => {
  setResponses([SEARCH_404()]);
  await assert.rejects(
    () => searchCommands("goal"),
    (err) => {
      assert.ok(err instanceof ApiClientError, `expected ApiClientError, got ${err}`);
      assert.equal(err.status, 404);
      return true;
    },
    "searchCommands resolved an empty result for a failed search",
  );
});

test("a genuinely empty registry is still an empty list, and a real one is still returned", async () => {
  setResponses([{ body: { commands: [] } }]);
  assert.deepEqual(await fetchCommands(), [], "a server-sent empty registry must stay an empty list");

  setResponses([
    {
      body: {
        commands: [
          { command: "/goal", category: "mission", description: "Define and orchestrate autonomous goals" },
        ],
      },
    },
  ]);
  const commands = await fetchCommands();
  assert.equal(commands.length, 1, "a successful read must still deliver the server's commands");
  assert.equal(commands[0].command, "/goal");
});

test("a rejected registry read cannot escape the Composer effect as an unhandled rejection", async () => {
  // The client now rejects, so the single caller has to handle it. Without this
  // the fix would move the failure from a silent empty list to an unhandled
  // promise rejection in the browser console — still a lie, just a louder one.
  const src = readFileSync(join(FRONTEND, "src", "components", "Composer.tsx"), "utf8");

  const effect = src.match(/useEffect\(\(\) => \{[\s\S]*?fetchCommands\(\)[\s\S]*?\}, \[\]\);/);
  assert.ok(effect, "the Composer effect that loads the registry must still exist");
  assert.match(
    effect[0],
    /catch\s*\(/,
    "the Composer effect must catch the registry failure; today it awaits fetchCommands() bare, so a rejection would be unhandled",
  );
  assert.match(
    effect[0],
    /errMsg\(/,
    "the Composer effect must render the reason through errMsg, not a generic string",
  );

  // ...and the reason has somewhere to go.
  assert.match(
    src,
    /registryError/,
    "Composer must hold the registry failure in state instead of discarding it",
  );
});
