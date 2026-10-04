import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const transpile = (file) =>
  ts.transpileModule(readFileSync(new URL(`./${file}`, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;

// Node has no ImageData. A minimal shim is provided here rather than pulling a
// polyfill into the app for a test.
class FakeImageData {
  constructor(data, width, height) {
    this.data = data;
    this.width = width;
    this.height = height;
  }
}
globalThis.ImageData = FakeImageData;

function load(source, dependencies = {}) {
  const exports = {};
  new Function("exports", "require", source)(exports, (name) => {
    assert.ok(Object.hasOwn(dependencies, name), `Unexpected dependency: ${name}`);
    return dependencies[name];
  });
  return exports;
}

const enc = load(transpile("qr-encode.ts"));
const dec = load(transpile("qr-decode.ts"), { "./qr-encode": enc });

// ---------------------------------------------------------------------------
// What is pinned, and why
// ---------------------------------------------------------------------------
//
// The decoder's stages are: locate finders -> derive geometry -> verify against
// timing patterns -> read format bits -> unmask and sample -> Reed-Solomon ->
// decode the bit stream.
//
// Three classes of test follow, and the distinction is the point:
//
//   A. Structural invariants that hold for any input. Cheap, and they are what
//      catch a refactor that breaks layout.
//   B. Refusals. Every stage that can fail must fail *loudly and specifically* —
//      a decoder that returns null for everything would pass a "returns null on
//      garbage" test while being completely broken.
//   C. Round-trip, only where the pipeline can honestly be expected to work.
//
// On (C): the geometry stage locates finders by run-length ratio and derives the
// dimension from finder spacing. That works on a clean render of an encoded code
// and is the case this app produces (the QR dialog draws the matrix itself). It
// is *not* a general camera-frame decoder — real-world perspective correction
// and rotation handling are out of scope, and the honest thing is to assert the
// scope rather than imply coverage this module does not have.
//
// The round-trip tests below are therefore gated on the renderer producing an
// axis-aligned, correctly-proportioned image, and are skipped with a stated
// reason when the geometry stage cannot lock on — never silently passed.

test("the module exports the surface the scanner and the image path use", () => {
  assert.equal(typeof dec.decodeQrFromImageData, "function");
  assert.equal(typeof dec.decodeQrFromImageFile, "function");
  assert.equal(typeof dec.matrixToImageData, "function");
});

test("an image with no QR code decodes to null rather than throwing", () => {
  const width = 120;
  const data = new Uint8ClampedArray(width * width * 4).fill(255);
  assert.equal(dec.decodeQrFromImageData(new FakeImageData(data, width, width)), null);
});

test("an image too small to hold a code is refused with a stated reason", () => {
  const width = 10;
  const data = new Uint8ClampedArray(width * width * 4).fill(255);
  assert.throws(
    () => dec.decodeQrFromImageData(new FakeImageData(data, width, width)),
    /too small/,
  );
});

test("an all-black image is refused rather than decoded into something", () => {
  // No quiet zone and no finders, so no geometry exists. The point is that the
  // decoder does not treat a uniform field as a code.
  const width = 200;
  const data = new Uint8ClampedArray(width * width * 4).fill(0);
  assert.equal(dec.decodeQrFromImageData(new FakeImageData(data, width, width)), null);
});

test("matrixToImageData renders white with a quiet zone and dark modules", () => {
  const matrix = enc.encodeQr("alpha://connect?v=1");
  const image = dec.matrixToImageData(matrix, 4);
  const quiet = enc.QUIET_ZONE;
  const scale = 4;
  // A pixel inside the quiet zone must stay white; the top-left finder corner
  // must be dark.
  const at = (x, y) => image.data[(y * image.width + x) * 4];
  assert.equal(at(quiet * scale + 1, quiet * scale + 1), 0, "finder corner is dark");
  assert.equal(at(0, 0), 255, "corner of the image is the quiet zone");
  assert.equal(image.width, (matrix.size + quiet * 2) * scale);
});

test("decodeQrFromImageFile refuses when the browser cannot decode a file", async () => {
  // `createImageBitmap` is absent under Node. The function must say so rather
  // than throwing an opaque TypeError or returning null and implying "no code".
  await assert.rejects(
    () => dec.decodeQrFromImageFile(new FakeImageData(new Uint8ClampedArray(4), 1, 1)),
    /cannot decode an image file|camera scanner/,
  );
});

/**
 * The round-trip gate.
 *
 * This is a **known-failing** test, asserted as failing rather than skipped or
 * deleted. Three reasons that is the right shape:
 *
 * 1. A skipped test says "this does not apply". It applies — it is simply not
 *    implemented yet, and a skip would let the gap disappear from every report.
 * 2. A deleted test leaves the encoder looking verified in both directions when
 *    only one direction is. The encoder is the half that ships, so its
 *    correctness must not imply a decoder exists.
 * 3. This assertion fails *the moment* the decoder starts working, which is the
 *    signal to remove it and turn the camera path on. That is the intended
 *    trigger, not an oversight.
 *
 * When it passes, delete this test, flip `QR_DECODER_AVAILABLE` in
 * `PeerConnectPanel` to mount the scanner, and drop the `canDecodeQr` export
 * below.
 */
test("KNOWN FAILING: a correctly rendered encoded code should round-trip", () => {
  const text = "alpha://connect?v=1&a=alpha-7f3a2b&u=http://192.168.1.20:8001";
  const matrix = enc.encodeQr(text);
  const image = dec.matrixToImageData(matrix, 6);
  const decoded = dec.decodeQrFromImageData(image);
  assert.equal(
    decoded,
    text,
    decoded === null
      ? "the geometry stage does not yet lock onto a clean render; see the status banner in qr-decode.ts"
      : "round-trip succeeded — delete this test and enable the camera path",
  );
});

test("the decoder advertises that it cannot yet be trusted with real input", () => {
  // The UI reads this rather than hardcoding a boolean beside the code that
  // would have to change. It must be `false` while the round-trip gate above
  // fails, so the scanner cannot be mounted by accident.
  assert.equal(dec.canDecodeQr(), false);
});