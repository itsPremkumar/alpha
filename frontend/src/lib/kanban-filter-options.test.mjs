import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./kanban-filter-options.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const { uniqueFilterLabels } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

test("unique project names stay concise while duplicate names gain stable identity", () => {
  const labels = uniqueFilterLabels([
    { id: "abc123-project-a", name: "WarRoom" },
    { id: "def456-project-b", name: "WarRoom" },
    { id: "solo-project", name: "One-off" },
  ]);

  assert.equal(labels.get("abc123-project-a"), "WarRoom (abc123)");
  assert.equal(labels.get("def456-project-b"), "WarRoom (def456)");
  assert.equal(labels.get("solo-project"), "One-off");
});

test("id prefixes extend only as far as needed to distinguish collisions", () => {
  const labels = uniqueFilterLabels([
    { id: "abcdef-one", name: "Same" },
    { id: "abcdef-two", name: "Same" },
  ]);

  assert.equal(labels.get("abcdef-one"), "Same (abcdef-o)");
  assert.equal(labels.get("abcdef-two"), "Same (abcdef-t)");
});
