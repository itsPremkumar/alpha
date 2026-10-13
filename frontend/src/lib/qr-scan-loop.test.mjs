import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("./qr-scan-loop.ts", import.meta.url), "utf8");
const output = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const scan = {};
new Function("exports", output)(scan);

test("the first camera frame is scanned immediately", () => {
  assert.equal(scan.isQrScanFrameDue(0, null), true);
});

test("camera decoding is capped at five frames per second", () => {
  assert.equal(scan.isQrScanFrameDue(199, 0), false);
  assert.equal(scan.isQrScanFrameDue(200, 0), true);
  assert.equal(scan.isQrScanFrameDue(400, 200), true);
});

test("invalid timing values never trigger repeated decoding", () => {
  assert.equal(scan.isQrScanFrameDue(Number.NaN, 0), false);
  assert.equal(scan.isQrScanFrameDue(500, Number.POSITIVE_INFINITY), false);
});
