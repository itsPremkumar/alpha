#!/usr/bin/env node

/**
 * Generate the Alpha desktop icon (512x512 PNG) from pure SVG shapes.
 *
 * The repo's deer.svg illustration does not rasterize usefully outside a
 * browser, so the desktop icon is a geometric "flow orbit" mark on the app's
 * dark tile color. No fonts required — shapes only.
 *
 * Run:  node scripts/make-icon.mjs   (needs `npm install --no-save sharp`)
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const electronDir = fileURLToPath(new URL("..", import.meta.url));
const outFile = path.join(electronDir, "build", "icon-512.png");

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
  <rect x="8" y="8" width="496" height="496" rx="112" fill="#0b0f14"/>
  <rect x="8" y="8" width="496" height="496" rx="112" fill="none" stroke="#30363d" stroke-width="10"/>
  <circle cx="256" cy="256" r="118" fill="none" stroke="#58a6ff" stroke-width="44"/>
  <circle cx="348" cy="164" r="52" fill="#58a6ff"/>
  <circle cx="348" cy="164" r="22" fill="#0b0f14"/>
</svg>`;

fs.mkdirSync(path.dirname(outFile), { recursive: true });
await sharp(Buffer.from(svg)).png().toFile(outFile);
const meta = await sharp(outFile).metadata();
console.log(`icon written: ${outFile} (${meta.width}x${meta.height})`);
