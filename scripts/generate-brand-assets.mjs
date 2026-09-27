#!/usr/bin/env node

/**
 * Generate every Alpha brand mark from the one real logo.
 *
 * Source of truth: `frontend/src/assets/images/alpha.png` (the lion poster).
 * Nothing here draws a mark — every output is a crop/rescale of that file, so
 * the desktop app, the NSIS installer, the Windows shortcuts, the browser tab,
 * the Apple touch icon and the PWA icons cannot drift apart.
 *
 * Two crops, because one crop cannot serve both jobs:
 *
 *   - the mane circle is the mark (32px and up, plus every large surface), and
 *   - the face is the favicon (16px and 24px).
 *
 * The poster is the lion *plus* the "ALPHA" wordmark *plus* the
 * "AUTONOMOUS - INTELLIGENT - EVOLVING" tagline, so neither crop may include
 * the type. At 16px the whole mane averages out to a dark blob with no
 * readable feature; zoomed onto the face, the same pixels resolve into two
 * glowing eyes over a silver snout, which is the one part of the illustration
 * that still reads at that size. The full poster keeps its own job: it is what
 * `BrandLogo` renders in-app, where there is room to read it.
 *
 * Run:  node scripts/generate-brand-assets.mjs
 * Gate: node scripts/generate-brand-assets.mjs --check   (fails on drift)
 *
 * `sharp` is resolved out of `electron/node_modules` because that is where the
 * desktop build already declares it, so this script adds no dependency to the
 * repository root and the asset pipeline stays part of the Electron build.
 */

import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const sharp = createRequire(path.join(repoRoot, "electron", "package.json"))("sharp");

/** The one real logo. Everything below is derived from this file. */
const SOURCE_LOGO = path.join(repoRoot, "frontend", "src", "assets", "images", "alpha.png");

/**
 * Crops as fractions of the source poster.
 *
 * Measured off the 1254x1254 source. The mane box stops above the wordmark
 * (y >= 950) and inside the loose side circuit traces (x 50..1192) so neither
 * the type nor the antenna lines enter the mark; it is square to within 4px.
 * The face box is a 340px square around the facial mask at x 506..752,
 * y 318..608, keeping a little mane for context. Fractions rather than pixels
 * so a re-export of the poster at another resolution crops the same picture.
 */
const MANE_CROP = { left: 0.13716, top: 0.02233, width: 0.7177, height: 0.7177 };
const FACE_CROP = { left: 0.36603, top: 0.23126, width: 0.27109, height: 0.27109 };

/** At or below this size the face reads and the mane does not. */
const FACE_MAX_SIZE = 24;

/**
 * The poster's own background, sampled from its corners (`rgb(0, 1, 4)`).
 * Used only where a platform refuses transparency: iOS composites a
 * transparent touch icon on black, which would swallow this near-black lion.
 */
const TILE = "#010409";

/**
 * Every generated mark.
 *
 * `ico` entries are written as a real multi-resolution .ico (PNG-compressed
 * entries, which Windows 7+ and every current browser read). `tile` flattens
 * onto TILE first. `inset` letterboxes the mark into the canvas so a circular
 * launcher mask cannot clip the mane.
 */
const TARGETS = [
  // Desktop shell: window/taskbar icon, and the PNG the splash screen shows.
  { file: "electron/assets/alpha-mark.png", size: 512 },
  // Windows executable + Start Menu / Desktop shortcut icon.
  { file: "electron/assets/alpha.ico", ico: [16, 24, 32, 48, 64, 128, 256] },

  // Browser tab, bookmarks, history.
  { file: "frontend/public/favicon.ico", ico: [16, 32, 48] },
  { file: "frontend/public/favicon-16x16.png", size: 16 },
  { file: "frontend/public/favicon-32x32.png", size: 32 },

  // iOS home screen / iPad.
  { file: "frontend/public/apple-touch-icon.png", size: 180, tile: true },

  // PWA / Android / Windows browser install.
  { file: "frontend/public/icon-192.png", size: 192 },
  { file: "frontend/public/icon-512.png", size: 512 },
  // Maskable icons are cropped to a circle by the launcher, so the mark is
  // inset to the safe zone instead of bleeding to the edges.
  { file: "frontend/public/icon-maskable-512.png", size: 512, inset: 0.8 },
];

/**
 * Build a multi-resolution .ico from already-encoded PNG buffers.
 *
 * The container is a 6-byte ICONDIR, one 16-byte ICONDIRENTRY per size, then
 * the PNG payloads. PNG-compressed entries are the Vista-and-later form and
 * are what every current Windows build, browser and NSIS expects; `width` and
 * `height` are stored as 0 to mean 256.
 */
function buildIco(images) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); // reserved
  header.writeUInt16LE(1, 2); // type: 1 = icon
  header.writeUInt16LE(images.length, 4);

  const directory = Buffer.alloc(16 * images.length);
  let offset = header.length + directory.length;

  images.forEach(({ size, data }, index) => {
    const at = index * 16;
    directory.writeUInt8(size >= 256 ? 0 : size, at + 0); // width  (0 = 256)
    directory.writeUInt8(size >= 256 ? 0 : size, at + 1); // height (0 = 256)
    directory.writeUInt8(0, at + 2); // palette size
    directory.writeUInt8(0, at + 3); // reserved
    directory.writeUInt16LE(1, at + 4); // color planes
    directory.writeUInt16LE(32, at + 6); // bits per pixel
    directory.writeUInt32LE(data.length, at + 8);
    directory.writeUInt32LE(offset, at + 12);
    offset += data.length;
  });

  return Buffer.concat([header, directory, ...images.map((image) => image.data)]);
}

/**
 * Read an .ico back into the same `[{ size, data }]` shape `buildIco` takes.
 *
 * sharp cannot decode .ico (libvips ships no ICO loader), so the container is
 * parsed here by hand and each frame is handed back as the raw PNG payload it
 * already is. That is enough for `sameImage` below, which only needs pixels.
 */
function readIco(buffer) {
  if (buffer.readUInt16LE(0) !== 0 || buffer.readUInt16LE(2) !== 1) {
    throw new Error("not an ICONDIR");
  }
  const count = buffer.readUInt16LE(4);
  const images = [];
  for (let index = 0; index < count; index += 1) {
    const at = 6 + index * 16;
    const bytes = buffer.readUInt32LE(at + 8);
    const offset = buffer.readUInt32LE(at + 12);
    // 0 is the on-disk encoding of 256.
    images.push({
      size: buffer.readUInt8(at) || 256,
      data: buffer.subarray(offset, offset + bytes),
    });
  }
  return images;
}

/**
 * Compare two images by decoded pixels, never by encoded bytes.
 *
 * The marks are committed, so `--check` has to tell "stale" from "identical".
 * Comparing PNG bytes would report drift whenever sharp or its bundled libvips
 * changes encoder defaults, or when the same pixels were produced on Windows
 * and Linux — a false failure that trains people to ignore the gate. Decoding
 * to raw RGBA and comparing those is stable across encoder versions and still
 * fails loudly for the two things that actually matter: a hand-edited mark, and
 * a poster that changed without the marks being regenerated.
 */
async function sameImage(expected, onDisk) {
  const decode = (input) => sharp(input).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  const [a, b] = await Promise.all([decode(expected), decode(onDisk)]);
  return (
    a.info.width === b.info.width &&
    a.info.height === b.info.height &&
    a.info.channels === b.info.channels &&
    a.data.equals(b.data)
  );
}

/** Pixel-compare every frame of two .ico files. */
async function sameIco(expectedBuffer, onDiskFile) {
  const [expected, onDisk] = [readIco(expectedBuffer), readIco(fs.readFileSync(onDiskFile))];
  if (expected.length !== onDisk.length) return false;
  for (let index = 0; index < expected.length; index += 1) {
    if (expected[index].size !== onDisk[index].size) return false;
    if (!(await sameImage(expected[index].data, onDisk[index].data))) return false;
  }
  return true;
}

/** Read one fractional crop out of the poster as a sharp pipeline input. */
async function crop(poster, meta, box) {
  return sharp(poster)
    .extract({
      left: Math.round(meta.width * box.left),
      top: Math.round(meta.height * box.top),
      width: Math.round(meta.width * box.width),
      height: Math.round(meta.height * box.height),
    })
    .toBuffer();
}

/** Encode one crop at a square size, optionally inset and/or flattened. */
async function renderMark(cropBuffer, size, { tile = false, inset = 1 } = {}) {
  const side = Math.round(size * inset);
  const mark = await sharp(cropBuffer)
    .resize(side, side, { fit: "cover" })
    .png()
    .toBuffer();

  if (!tile && inset === 1) return mark;

  return sharp({
    create: {
      width: size,
      height: size,
      channels: 4,
      background: tile ? TILE : { r: 0, g: 0, b: 0, alpha: 0 },
    },
  })
    .composite([{ input: mark, gravity: "centre" }])
    .png()
    .toBuffer();
}

/** Render one target to the exact bytes that should be on disk. */
async function buildTarget(target, sourceFor) {
  if (!target.ico) return renderMark(sourceFor(target.size), target.size, target);

  const images = [];
  for (const size of target.ico) {
    images.push({ size, data: await renderMark(sourceFor(size), size) });
  }
  return buildIco(images);
}

async function main() {
  const check = process.argv.includes("--check");

  if (!fs.existsSync(SOURCE_LOGO)) {
    console.error(`ERROR: source logo missing: ${SOURCE_LOGO}`);
    process.exit(1);
  }

  const poster = fs.readFileSync(SOURCE_LOGO);
  const meta = await sharp(poster).metadata();
  const mane = await crop(poster, meta, MANE_CROP);
  const face = await crop(poster, meta, FACE_CROP);

  /** The face is the small-size mark; the mane is everything else. */
  const sourceFor = (size) => (size <= FACE_MAX_SIZE ? face : mane);

  const drifted = [];

  for (const target of TARGETS) {
    const outFile = path.join(repoRoot, target.file);
    const exists = fs.existsSync(outFile);

    if (check) {
      // Compare against what *would* be written, without writing it, so a
      // failing gate leaves the tree exactly as it found it.
      const label = target.ico ? `ico ${target.ico.join("/")}` : `${target.size}px`;
      if (!exists) {
        drifted.push(target.file);
        continue;
      }
      const same = target.ico
        ? await sameIco(await buildTarget(target, sourceFor), outFile)
        : await sameImage(await buildTarget(target, sourceFor), fs.readFileSync(outFile));
      if (!same) drifted.push(`${target.file} (${label})`);
      continue;
    }

    const bytes = await buildTarget(target, sourceFor);
    const label = target.ico ? `ico ${target.ico.join("/")}` : `${target.size}px`;
    fs.mkdirSync(path.dirname(outFile), { recursive: true });
    fs.writeFileSync(outFile, bytes);
    console.log(`  ${label.padEnd(18)} ${target.file}  (${(bytes.length / 1024).toFixed(1)} KB)`);
  }

  if (check) {
    if (drifted.length > 0) {
      console.error("Brand marks are stale — run: node scripts/generate-brand-assets.mjs");
      for (const file of drifted) console.error(`  stale: ${file}`);
      process.exit(1);
    }
    console.log("Brand marks are up to date.");
    return;
  }

  console.log(`\nDerived ${TARGETS.length} brand marks from ${path.relative(repoRoot, SOURCE_LOGO)}.`);
}

await main();
