// The composer's pre-send mention preview is a contract with the Gateway's ONE
// mention grammar, and both of its failure modes are silent:
//
//   * the room rides in the body (`room_name`), not in the path: addressing a
//     per-room path 404s, and a 404 that never surfaces leaves the "Tags
//     address" strip spinning on "checking with the room…" — the pre-send
//     warning is then missing exactly when the operator needs it;
//   * a failed read must reject with the server's reason. Resolving to an
//     empty resolution would claim "this tag addresses nobody", which is a
//     different (and wrong) statement.
//
// No server, no DOM: the real client runs against a stubbed ./http.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const compiled = new Map(
  ["groups-profile-model", "groups-profile"].map((name) => [
    name,
    ts.transpileModule(readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText,
  ]),
);

const model = {};
new Function("exports", "require", compiled.get("groups-profile-model"))(model, () => {
  throw new Error("groups-profile-model must have no imports");
});

function loadClient(handler) {
  const exports = {};
  new Function("exports", "require", compiled.get("groups-profile"))(exports, (dependency) => {
    if (dependency === "@/lib/http") {
      return {
        get: async () => {
          throw new Error("this suite only drives the mention preview");
        },
        send: async (path, method, payload) => handler(path, method, payload),
      };
    }
    if (dependency === "@/lib/api-client") return { apiUrl: (path) => `/api${path}` };
    if (dependency === "./groups-profile-model") return model;
    assert.fail(`Unexpected dependency: ${dependency}`);
  });
  return exports;
}

const SERVER_RESPONSE = {
  text: "@bot:backend-dev the flag flips Thursday",
  spans: [],
  targets: [],
  unresolved: [],
  resolved_handles: ["backend-dev"],
  ok: true,
  human_line: "@backend-dev — 1 agent",
};

test("the room preview posts to /groups/mentions/preview with the room in the body", async () => {
  const calls = [];
  const client = loadClient(async (path, method, payload) => {
    calls.push({ path, method, payload });
    return SERVER_RESPONSE;
  });
  const res = await client.previewMentions("release-train", "@bot:backend-dev the flag flips Thursday", "operator");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/groups/mentions/preview");
  assert.equal(calls[0].method, "POST");
  assert.deepEqual(calls[0].payload, {
    room_name: "release-train",
    content: "@bot:backend-dev the flag flips Thursday",
    sender: "operator",
  });
  assert.deepEqual(res.resolvedHandles, ["backend-dev"], "the strip reads the camel-case alias");
});

test("a failed preview rejects with the server's reason, never an empty resolution", async () => {
  const client = loadClient(async () => {
    throw new Error("Room 'ghost' not found");
  });
  await assert.rejects(() => client.previewMentions("ghost", "@everyone hello"), /Room 'ghost' not found/);
});
