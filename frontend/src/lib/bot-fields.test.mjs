import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import ts from "typescript";

/**
 * The bot field schema is transcribed from the server's Pydantic model, which
 * is exactly the kind of copy that drifts. These tests cover three things:
 * the derivation helpers behave, the "operator set" distinction is real, and -
 * the load-bearing one - the transcription still matches the backend source.
 */

const source = readFileSync(new URL("./bot-fields.ts", import.meta.url), "utf8");

let cached = null;
async function load() {
  if (cached) return cached;
  const dir = mkdtempSync(join(tmpdir(), "alpha-bot-fields-"));
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const file = join(dir, "bot-fields.mjs");
  writeFileSync(file, code, "utf8");
  cached = await import(pathToFileURL(file).href);
  return cached;
}

/**
 * A slice of the real BotUpdateRequest, transcribed from
 * `backend/app/gateway/routers/bots.py`. The comparison test reads the actual
 * backend file rather than trusting this fixture; the fixture is here to prove
 * the comparison function reacts to divergence.
 */
const SERVER_SCHEMA = {
  components: {
    schemas: {
      BotUpdateRequest: {
        properties: {
          display_name: { maxLength: 100 },
          role: { maxLength: 200 },
          soul: { maxLength: 20000 },
          model: { maxLength: 200 },
          toolsets: { maxItems: 50 },
          skills: { maxItems: 100 },
          avatar: { maxLength: 16 },
          status: { maxLength: 16 },
          last_active: { maxLength: 32 },
          department: { maxLength: 64 },
          reports_to: { maxLength: 64 },
          responsibilities: {},
          capabilities: {},
          heartbeat: { maxLength: 32 },
          succession_fallback: { maxLength: 64 },
          reputation_score: { minimum: 0, maximum: 1 },
          task_stats: {},
          routines: {},
        },
      },
    },
  },
};

test("the field list matches the server's BotUpdateRequest exactly", async () => {
  const { assertAgainstServerSchema, BOT_FIELDS } = await load();
  const result = assertAgainstServerSchema(SERVER_SCHEMA);
  assert.equal(result.status, "compared");
  assert.deepEqual(
    result.divergences,
    [],
    "the transcribed schema has drifted from the server: " + result.divergences.join("; "),
  );
  assert.equal(result.checked, BOT_FIELDS.length, "every field must have been compared");
});

test("a field the server dropped is reported, not silently offered", async () => {
  const { assertAgainstServerSchema } = await load();
  const drifted = JSON.parse(JSON.stringify(SERVER_SCHEMA));
  delete drifted.components.schemas.BotUpdateRequest.properties.heartbeat;
  const result = assertAgainstServerSchema(drifted);
  assert.equal(result.status, "compared");
  assert.equal(result.divergences.length, 1);
  assert.match(result.divergences[0], /heartbeat: editable here but absent/);
});

test("a field the server added is reported, not silently missing", async () => {
  const { assertAgainstServerSchema } = await load();
  const drifted = JSON.parse(JSON.stringify(SERVER_SCHEMA));
  drifted.components.schemas.BotUpdateRequest.properties.temperature = {};
  const result = assertAgainstServerSchema(drifted);
  assert.match(result.divergences.join(" "), /temperature: accepted by the server but not editable here/);
});

test("a tightened server limit is caught", async () => {
  const { assertAgainstServerSchema } = await load();
  const drifted = JSON.parse(JSON.stringify(SERVER_SCHEMA));
  drifted.components.schemas.BotUpdateRequest.properties.display_name.maxLength = 40;
  const result = assertAgainstServerSchema(drifted);
  assert.match(result.divergences.join(" "), /display_name: this form allows 100, the server allows 40/);
});

test("a missing schema is 'unavailable', never silently 'compared'", async () => {
  const { assertAgainstServerSchema } = await load();
  for (const input of [null, undefined, {}, { components: {} }]) {
    const result = assertAgainstServerSchema(input);
    assert.equal(result.status, "unavailable", "an unreadable schema must not read as a pass");
    assert.equal(result.checked, 0);
  }
});

test("only genuinely changed keys are sent", async () => {
  const { changedBotFields, hasChanges } = await load();
  const original = { display_name: "Coder", role: "Engineer", capabilities: ["python"] };
  const draft = { display_name: "Coder", role: "Senior Engineer", capabilities: ["python"] };
  const patch = changedBotFields(original, draft);
  assert.deepEqual(Object.keys(patch), ["role"], "an untouched field must not be resent");
  assert.equal(hasChanges(patch), true);
});

test("an untouched form sends nothing, because an empty PATCH is a 422", async () => {
  const { changedBotFields, hasChanges } = await load();
  const same = { display_name: "Coder", skills: ["a", "b"] };
  const patch = changedBotFields(same, { display_name: "Coder", skills: ["a", "b"] });
  assert.equal(hasChanges(patch), false);
});

test("list edits are detected by content, not by identity", async () => {
  const { changedBotFields } = await load();
  const original = { capabilities: ["python", "backend"] };
  assert.deepEqual(changedBotFields(original, { capabilities: ["python", "backend"] }), {});
  const removed = changedBotFields(original, { capabilities: ["python"] });
  assert.deepEqual(removed, { capabilities: ["python"] }, "a removed row is a change");
  const reordered = changedBotFields(original, { capabilities: ["backend", "python"] });
  assert.deepEqual(
    reordered,
    { capabilities: ["backend", "python"] },
    "reordering is a real change and must be sent",
  );
});

test("an emptied list is a change, not an absent key", async () => {
  const { changedBotFields } = await load();
  const patch = changedBotFields({ skills: ["a"] }, { skills: [] });
  assert.deepEqual(patch, { skills: [] }, "clearing a list must be sent so the server clears it too");
});

test("a measured field stops being a measurement once an operator edits it", async () => {
  const { botFieldSpec, isOperatorSet } = await load();
  const rep = botFieldSpec("reputation_score");
  assert.ok(rep, "reputation must be a known field");
  assert.equal(rep.measured, true);

  const original = { reputation_score: null, reputation_basis: "no recorded runs" };
  const untouched = { reputation_score: null };
  assert.equal(
    isOperatorSet(rep, original, untouched),
    false,
    "a field nobody touched is still the server's answer, even when it is null",
  );

  const edited = { reputation_score: 0.95 };
  assert.equal(
    isOperatorSet(rep, original, edited),
    true,
    "an edited score is an operator assertion and must stop being called measured",
  );
});

test("a non-measured field is never flagged as operator-asserted", async () => {
  const { botFieldSpec, isOperatorSet } = await load();
  const name = botFieldSpec("display_name");
  assert.ok(name);
  assert.equal(
    isOperatorSet(name, { display_name: "Coder" }, { display_name: "Builder" }),
    false,
    "renaming a bot is authoring, not forging a measurement",
  );
});

test("the transcription is checked against the backend source, not only a fixture", async () => {
  // The fixture proves the comparator reacts to divergence. This proves the
  // fixture is truthful: every key this module claims to edit must actually be
  // declared in the backend's BotUpdateRequest, read from the real file.
  const { BOT_FIELDS } = await load();
  const routerFile = new URL("../../../backend/app/gateway/routers/bots.py", import.meta.url);
  const text = readFileSync(routerFile, "utf8");
  const start = text.indexOf("class BotUpdateRequest");
  assert.notEqual(start, -1, "BotUpdateRequest must exist in the backend router");
  const end = text.indexOf("class BotCloneRequest", start);
  const block = text.slice(start, end === -1 ? undefined : end);
  for (const field of BOT_FIELDS) {
    assert.match(
      block,
      new RegExp(`\\b${field.key}\\s*:`),
      field.key + " is offered as editable but is not declared in the backend BotUpdateRequest",
    );
  }
});
