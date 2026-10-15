import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("./subagent-capacity.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
  },
}).outputText;
const moduleUrl = `data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`;
const { concurrencyOptionsForTotal, concurrencyForTotal } = await import(moduleUrl);

test("concurrency options never exceed the selected total delegation cap", () => {
  for (const total of [3, 6, 10, 15, 20, 30, 50]) {
    assert.ok(concurrencyOptionsForTotal(total).every((count) => count <= total));
  }
  assert.deepEqual(concurrencyOptionsForTotal(3), [1, 2, 3]);
  assert.deepEqual(concurrencyOptionsForTotal(6), [1, 2, 3, 4, 6]);
});

test("lowering total clamps the selected concurrency to an offered value", () => {
  assert.equal(concurrencyForTotal(8, 3), 3);
  assert.equal(concurrencyForTotal(6, 6), 6);
  assert.equal(concurrencyForTotal(2, 3), 2);
});
