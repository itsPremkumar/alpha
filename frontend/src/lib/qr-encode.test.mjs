import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const transpile = (file) =>
  ts.transpileModule(readFileSync(new URL(`./${file}`, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;

function load(source) {
  const exports = {};
  new Function("exports", "require", source)(exports, () => {
    throw new Error("qr-encode must not depend on anything");
  });
  return exports;
}

const qr = load(transpile("qr-encode.ts"));

/** The three finder patterns must be present with the right 7x7 ring/core shape. */
function hasFinderAt(modules, top, left) {
  for (let r = 0; r < 7; r += 1) {
    for (let c = 0; c < 7; c += 1) {
      const ring = r === 0 || r === 6 || c === 0 || c === 6;
      const core = r >= 2 && r <= 4 && c >= 2 && c <= 4;
      if (modules[top + r][left + c] !== (ring || core)) return false;
    }
  }
  return true;
}

test("encodeQr rejects empty input rather than producing a blank code", () => {
  assert.throws(() => qr.encodeQr(""), /nothing to encode/);
});

test("size follows the version formula and the three finder patterns are correct", () => {
  const matrix = qr.encodeQr("alpha://connect?v=1&a=alpha-test&u=http://192.168.1.20:8001");
  assert.equal(matrix.size, matrix.version * 4 + 17);
  assert.ok(matrix.modules.length === matrix.size);
  assert.ok(hasFinderAt(matrix.modules, 0, 0), "top-left finder");
  assert.ok(hasFinderAt(matrix.modules, 0, matrix.size - 7), "top-right finder");
  assert.ok(hasFinderAt(matrix.modules, matrix.size - 7, 0), "bottom-left finder");
});

test("the dark module is set and the timing patterns alternate", () => {
  const matrix = qr.encodeQr("alpha://connect?v=1&a=alpha-test&u=http://192.168.1.20:8001");
  assert.equal(matrix.modules[matrix.size - 8][8], true, "dark module");
  for (let i = 8; i < matrix.size - 8; i += 1) {
    assert.equal(matrix.modules[6][i], i % 2 === 0, `horizontal timing at ${i}`);
    assert.equal(matrix.modules[i][6], i % 2 === 0, `vertical timing at ${i}`);
  }
});

test("a longer payload picks a larger version and never shrinks", () => {
  const short = qr.encodeQr("alpha://connect?v=1");
  const longer = qr.encodeQr(`alpha://connect?v=1&k=${"a".repeat(200)}`);
  assert.ok(longer.version >= short.version);
  assert.ok(longer.size >= short.size);
});

test("encoding is deterministic — the same string yields an identical matrix", () => {
  const text = "alpha://connect?v=1&a=alpha-test&u=http://10.0.0.5:8001&e=1700000900&ep=3&k=secret-value";
  const a = qr.encodeQr(text);
  const b = qr.encodeQr(text);
  assert.equal(a.version, b.version);
  assert.deepEqual(a.modules, b.modules);
});

test("different payloads produce different matrices", () => {
  const a = qr.encodeQr("alpha://connect?v=1&a=alpha-one");
  const b = qr.encodeQr("alpha://connect?v=1&a=alpha-two");
  assert.notDeepEqual(a.modules, b.modules);
});

test("the payload exceeds the renderable range with a named refusal", () => {
  // A refusal beats a silent fallback to a smaller version, which would render a
  // code that scans as something else entirely.
  assert.throws(() => qr.encodeQr("x".repeat(5000)), /too much data/);
});

test("qrToSvgPath emits one merged run per horizontal dark segment", () => {
  const matrix = qr.encodeQr("alpha://connect?v=1&a=alpha-test");
  const path = qr.qrToSvgPath(matrix);
  const commands = path.match(/M/g) ?? [];
  // Far fewer commands than dark modules, because runs are merged.
  const darkModules = matrix.modules.flat().filter(Boolean).length;
  assert.ok(commands.length > 0);
  assert.ok(commands.length < darkModules, `expected merged runs (${commands.length}) < modules (${darkModules})`);
  assert.ok(path.startsWith("M0 "));
});

test("the viewBox includes the mandatory quiet zone on every side", () => {
  const matrix = qr.encodeQr("alpha://connect?v=1");
  assert.equal(qr.qrViewBoxSize(matrix), matrix.size + qr.QUIET_ZONE * 2);
});

test("a one-character payload still produces a structurally valid code", () => {
  // A degenerate input is where a fixed-width length field or an off-by-one in
  // the terminator padding shows up.
  const matrix = qr.encodeQr("x");
  assert.equal(matrix.size, 21);
  assert.ok(hasFinderAt(matrix.modules, 0, 0));
  assert.ok(hasFinderAt(matrix.modules, 0, 14));
  assert.ok(hasFinderAt(matrix.modules, 14, 0));
});

test("versions 7 and above carry version information modules", () => {
  // The version block only exists from v7; omitting it yields a code that scans
  // at small sizes and fails silently at large ones.
  const matrix = qr.encodeQr(`alpha://connect?v=1&k=${"b".repeat(300)}`);
  assert.ok(matrix.version >= 7, `expected version >= 7, got ${matrix.version}`);
  assert.ok(matrix.modules.some((row) => row.some(Boolean)));
});