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

const qrcode = await import("qrcode");
const jsQR = (await import("jsqr")).default;
const enc = load(transpile("qr-encode.ts"), { qrcode });
const dec = load(transpile("qr-decode.ts"), { "./qr-encode": enc, jsqr: { default: jsQR } });

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

test("image upload decodes through the same path and releases the bitmap", async () => {
  const text = "alpha://connect?v=1&a=upload-test";
  const image = dec.matrixToImageData(enc.encodeQr(text), 6);
  let closed = false;
  const previousBitmap = globalThis.createImageBitmap;
  const previousDocument = globalThis.document;
  globalThis.createImageBitmap = async () => ({
    width: image.width,
    height: image.height,
    close() { closed = true; },
  });
  globalThis.document = {
    createElement: () => ({
      width: 0,
      height: 0,
      getContext: () => ({
        drawImage() {},
        getImageData: () => image,
      }),
    }),
  };
  try {
    assert.equal(await dec.decodeQrFromImageFile({ size: 100 }), text);
    assert.equal(closed, true, "decoded image memory is released after scanning");
  } finally {
    if (previousBitmap === undefined) delete globalThis.createImageBitmap;
    else globalThis.createImageBitmap = previousBitmap;
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
  }
});

test("a standards-compliant rendered code round-trips through the production decoder", () => {
  const text = "alpha://connect?v=1&a=alpha-7f3a2b&u=http://192.168.1.20:8001";
  const matrix = enc.encodeQr(text);
  const image = dec.matrixToImageData(matrix, 6);
  const decoded = dec.decodeQrFromImageData(image);
  assert.equal(
    decoded,
    text,
    "encoder and decoder must agree on the complete invite payload",
  );
});

test("the bundled decoder advertises the scanner capability", () => {
  assert.equal(dec.canDecodeQr(), true);
});
