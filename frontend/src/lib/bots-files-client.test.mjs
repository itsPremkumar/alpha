import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const compiled = new Map(["api-client", "http", "bots", "files", "workforce"].map((name) => [name,
  ts.transpileModule(readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText,
]));

function load(name, dependencies = {}, errors = []) {
  const exports = {};
  new Function("exports", "require", "process", "console", "fetch", compiled.get(name))(
    exports,
    (dependency) => {
      assert.ok(Object.hasOwn(dependencies, dependency), `Unexpected dependency: ${dependency}`);
      return dependencies[dependency];
    },
    { env: {} },
    { error: (...args) => errors.push(args) },
    () => { throw new Error("Raw fetch must not be used"); },
  );
  return exports;
}

const client = load("api-client");
function fixture(respond, baseUrl = "/api") {
  const calls = [];
  const errors = [];
  const api = { ...client, GATEWAY_BASE: baseUrl, apiFetch: client.createApiClient({
    baseUrl,
    getCookie: () => "other=value; csrf_token=test_token-123",
    fetch: async (url, init) => {
      calls.push({ url, ...init });
      return respond(url, init);
    },
  }) };
  const http = load("http", { "./api-client": api });
  const dependencies = { "./api-client": api, "./http": http };
  return { calls, errors, http, bots: load("bots", dependencies, errors), files: load("files", dependencies), workforce: load("workforce", dependencies) };
}

function assertRequest(call, method, url) {
  assert.equal(call.url, url);
  assert.equal(call.method, method);
  assert.equal(call.credentials, "include");
  assert.equal(call.redirect, "error");
  assert.equal(call.headers.get("X-CSRF-Token"), method === "GET" ? null : "test_token-123");
}

function shaped(kind, status = 0) {
  return (error) => {
    assert.ok(error instanceof client.ApiClientError);
    assert.equal(error.kind, kind);
    assert.equal(error.status, status);
    assert.doesNotMatch(error.message, /secret|private|Bearer/);
    return true;
  };
}

test("bots preserve normalized fleet, detail, templates and departments", async () => {
  const f = fixture((url) => Response.json(url.includes("templates")
    ? { templates: { coder: { role: "Engineer" } } }
    : url.includes("departments") ? { departments: ["engineering"] }
    : url.includes("alice") ? { name: "alice" } : { bots: [{ name: "alice" }] }));
  const bots = await f.bots.fetchBots({ status: "active", department: "R&D" });
  assert.equal(bots[0].display_name, "alice");
  assert.equal(bots[0].status, "active");
  assert.deepEqual(bots[0].task_stats, {});
  assert.deepEqual(await f.bots.fetchBot("alice"), bots[0]);
  assert.deepEqual(await f.bots.fetchBotTemplates(), [{ name: "coder", role: "Engineer" }]);
  assert.deepEqual(await f.bots.fetchDepartments(), ["engineering"]);
  assertRequest(f.calls[0], "GET", "/api/bots?status=active&department=R%26D");
});

test("bot match mutation uses CSRF and credentials with encoded names", async () => {
  const f = fixture(() => new Response(null, { status: 204 }));
  assert.equal(await f.bots.touchBot("alice/bob"), undefined);
  assertRequest(f.calls[0], "POST", "/api/bots/alice%2Fbob/match");
});

for (const kind of ["http", "network"]) {
  test(`bot ${kind} failures keep fallback shapes and sanitized errors`, async () => {
    const f = fixture(() => {
      if (kind === "network") throw new Error("Bearer secret private diagnostics");
      return { ok: false, status: 403, json() { assert.fail("Error body must not be read"); } };
    });
    assert.deepEqual(await f.bots.fetchBots(), []);
    assert.equal(await f.bots.fetchBot("alice"), null);
    assert.deepEqual(await f.bots.fetchBotTemplates(), []);
    assert.deepEqual(await f.bots.fetchDepartments(), []);
    assert.equal(await f.bots.touchBot("alice"), undefined);
    assert.equal(f.errors.length, 4);
    for (const entry of f.errors) shaped(kind, kind === "http" ? 403 : 0)(entry.at(-1));
  });
}

test("file uploads preserve multipart body, CSRF and normalized envelopes", async () => {
  for (const key of ["files", "uploads", "data"]) {
    const f = fixture(() => Response.json({ [key]: [{ filename: "notes.txt", size_bytes: "5", content_type: "text/plain" }] }));
    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    assert.deepEqual(await f.files.uploadFiles("thread/1", [file]), [{ name: "notes.txt", size: 5, type: "text/plain" }]);
    assertRequest(f.calls[0], "POST", "/api/threads/thread%2F1/uploads");
    assert.equal(f.calls[0].headers.has("Content-Type"), false);
    const uploaded = f.calls[0].body.getAll("files");
    assert.equal(uploaded.length, 1);
    assert.equal(uploaded[0].name, "notes.txt");
    assert.equal(await uploaded[0].text(), "hello");
  }
});

test("artifact preview uses a relative route with a configured base and preserves text and URLs", async () => {
  const f = fixture(() => new Response("hello", { headers: { "Content-Type": "text/plain" } }), "/custom/api");
  assert.equal(await f.files.previewArtifact("thread/1", "/folder/a b.txt"), "hello");
  assertRequest(f.calls[0], "GET", "/custom/api/threads/thread%2F1/artifacts/folder/a%20b.txt");
  assert.equal(f.files.artifactUrl("thread/1", "/folder/a b.txt", true), `${f.calls[0].url}?download=true`);
});

for (const method of ["uploadFiles", "previewArtifact"]) {
  test(`${method} shapes HTTP, network and unreadable-response errors`, async () => {
    const args = method === "uploadFiles" ? ["thread", []] : ["thread", "notes.txt"];
    const http = fixture(() => ({ ok: false, status: 500, json() { assert.fail("Error body must not be read"); } }));
    await assert.rejects(http.files[method](...args), shaped("http", 500));
    const network = fixture(() => { throw new Error("Bearer secret private diagnostics"); });
    await assert.rejects(network.files[method](...args), shaped("network"));
    const response = fixture(() => ({ ok: true,
      json: async () => { throw new Error("secret JSON"); },
      blob: async () => { throw new Error("secret body"); },
    }));
    await assert.rejects(response.files[method](...args), shaped("response"));
  });
}

test("artifact preview retains binary and size limits", async () => {
  const binary = fixture(() => new Response("binary", { headers: { "Content-Type": "application/octet-stream" } }));
  await assert.rejects(binary.files.previewArtifact("thread", "image.png"), /Binary file/);
  const large = fixture(() => new Response("x".repeat(200001)));
  await assert.rejects(large.files.previewArtifact("thread", "notes.txt"), /too large/);
});

test("workforce already uses the unified client through http and preserves response shapes", async () => {
  const member = { bot_name: "alice", project_id: "project/1", role_in_project: "worker" };
  const f = fixture((_url, init) => Response.json(init.method === "POST" ? member : { members: [member] }));
  assert.deepEqual(await f.workforce.fetchPresence("project/1"), [member]);
  assert.deepEqual(await f.workforce.joinProject("project/1", "alice"), member);
  assertRequest(f.calls[0], "GET", "/api/projects/project%2F1/presence");
  assertRequest(f.calls[1], "POST", "/api/projects/project%2F1/join");
  assert.deepEqual(JSON.parse(f.calls[1].body), { bot_name: "alice", role_in_project: "worker" });
});
