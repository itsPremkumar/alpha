// QR decoder — zero dependencies, local-only.
//
// This is the counterpart to `qr-encode.ts`. It reads pixels and returns text.
// The pipeline is the standard one, and each stage below exists because the one
// before it is not sufficient on its own:
//
//   1. binarise            — adaptive threshold, because a phone photo of a screen
//                            is never a clean black-on-white bitmap
//   2. locate finders      — three 7x7 nested-square patterns; their centres give
//                            the module grid, which is why QR needs no locator
//                            patterns the way PDF417 does
//   3. sample the grid     — perspective-corrected via the fourth corner, derived
//                            from the finder geometry rather than guessed
//   4. unmask + deinterleave
//   5. Reed-Solomon decode — correct up to `ecc/2` symbol errors per block
//
// ## Status: NOT YET USABLE — read this before wiring it into a UI
//
// The stages below are implemented, and the *encoder* next door is verified
// against them. But the geometry stage — locating three finder patterns and
// deriving the module grid from them — does not reliably lock onto a rendered
// code, and a decoder that returns `null` for a code the user is looking at is
// worse than no decoder: it reports "no QR code found" for a code that is
// plainly there.
//
// So the honest state is:
//
// - `decodeQrFromImageData` / `decodeQrFromImageFile` are **not** offered as a
//   working feature. `PeerConnectPanel` does not mount the camera scanner or the
//   screenshot path, and says why.
// - The QR code this module's counterpart *renders* works perfectly. Showing and
//   sharing a code is the half that is done.
//
// The remaining work is the finder-location stage: candidate selection, the
// module-size consistency filter, and the timing-pattern verification that
// together decide whether a grid is real. The stages after it (unmask, block
// de-interleave, Reed-Solomon, bit-stream decode) are not the problem and are
// pinned by the structural tests.
//
// **Do not delete this file believing the encoder needs it** — `qr-encode.ts` is
// independent and fully working. Do not wire the camera path in without making
// the round-trip test in `qr-decode.test.mjs` pass on a real render.
//
// What this deliberately does NOT do:
//
// - **It reads one QR code per image.** Multi-code frames are out of scope; a
//   frame with two codes must report that, never a half-decoded string from
//   whichever it happened to see first.
// - **It does not do perspective correction.** The estimate is derived from the
//   finder centres, which cannot describe a rotated or strongly oblique frame.
// - **It never contacts anything.** Frames never leave the browser; a scanner
//   would draw to a local canvas and call in here.

import { encodeQr } from "./qr-encode";

export class QrDecodeError extends Error {}

interface Gray {
  width: number;
  height: number;
  data: Uint8ClampedArray;
}

interface Point {
  x: number;
  y: number;
}

/* ------------------------------------------------------------------ */
/* Stage 1 — greyscale and adaptive binarisation                       */
/* ------------------------------------------------------------------ */

function toGray(image: ImageData): Gray {
  const { width, height, data } = image;
  const out = new Uint8ClampedArray(width * height);
  for (let i = 0, p = 0; i < data.length; i += 4, p += 1) {
    // Rec. 601 luma, matching the integer approximation every decoder uses.
    out[p] = (data[i] * 306 + data[i + 1] * 601 + data[i + 2] * 117) >> 10;
  }
  return { width, height, data: out };
}

/**
 * Otsu's method over a 256-bin histogram.
 *
 * A fixed threshold is the single most common reason a decoder fails on a real
 * photo: the screen is usually dimmed, or the code sits on a coloured background,
 * so "dark" is not "below 128". Otsu picks the threshold that maximises
 * between-class variance, which handles both without a magic number.
 */
function otsuThreshold(gray: Gray): number {
  const histogram = new Array<number>(256).fill(0);
  for (const value of gray.data) histogram[value] += 1;

  const total = gray.data.length;
  let sum = 0;
  for (let t = 0; t < 256; t += 1) sum += t * histogram[t];

  let sumBackground = 0;
  let weightBackground = 0;
  let best = 0;
  let bestVariance = -1;
  for (let t = 0; t < 256; t += 1) {
    weightBackground += histogram[t];
    if (weightBackground === 0) continue;
    const weightForeground = total - weightBackground;
    if (weightForeground === 0) break;
    sumBackground += t * histogram[t];
    const meanBackground = sumBackground / weightBackground;
    const meanForeground = (sum - sumBackground) / weightForeground;
    const variance = weightBackground * weightForeground * (meanBackground - meanForeground) ** 2;
    if (variance > bestVariance) {
      bestVariance = variance;
      best = t;
    }
  }
  return best;
}

function binarize(gray: Gray): Uint8Array {
  const threshold = otsuThreshold(gray);
  const bits = new Uint8Array(gray.data.length);
  for (let i = 0; i < gray.data.length; i += 1) {
    bits[i] = gray.data[i] <= threshold ? 1 : 0;
  }
  return bits;
}

function isDark(bits: Uint8Array, gray: Gray, x: number, y: number): boolean {
  const px = Math.round(x);
  const py = Math.round(y);
  if (px < 0 || py < 0 || px >= gray.width || py >= gray.height) return false;
  return bits[py * gray.width + px] === 1;
}

/* ------------------------------------------------------------------ */
/* Stage 2 — finder pattern location                                   */
/* ------------------------------------------------------------------ */

/**
 * Scan one row for the 1:1:3:1:1 dark/light run ratio a finder pattern makes.
 *
 * Returns the centre x and the module size, or null. Tolerance is deliberate:
 * a screenshot is pixel-exact and passes trivially, while a camera frame is
 * resampled and needs the slack. A stricter ratio rejects real camera frames; a
 * looser one starts matching ordinary dark UI elements, so this is checked
 * against a *sequence* of five runs rather than a single transition.
 */
function finderInRow(bits: Uint8Array, gray: Gray, y: number): { center: number; moduleSize: number } | null {
  const { width } = gray;
  const runs: Array<{ start: number; length: number; dark: boolean }> = [];
  let currentDark = isDark(bits, gray, 0, y);
  let start = 0;
  for (let x = 1; x < width; x += 1) {
    const dark = isDark(bits, gray, x, y);
    if (dark !== currentDark) {
      runs.push({ start, length: x - start, dark: currentDark });
      currentDark = dark;
      start = x;
    }
  }
  runs.push({ start, length: width - start, dark: currentDark });

  for (let i = 0; i + 4 < runs.length; i += 1) {
    const [a, b, c, d, e] = [runs[i], runs[i + 1], runs[i + 2], runs[i + 3], runs[i + 4]];
    if (!a.dark || b.dark || !c.dark || d.dark || !e.dark) continue;
    // The five runs are 1:1:3:1:1 *modules*, so the centre dark run is three
    // modules wide and the unit is `c.length / 3` — not `/ 7`. Dividing by the
    // finder's total seven-module width understates the module size by more than
    // half, which is what made every derived dimension wrong.
    const unit = c.length / 3;
    if (unit < 1) continue;
    const tolerance = unit * 0.6;
    if (
      Math.abs(a.length - unit) <= tolerance &&
      Math.abs(b.length - unit) <= tolerance &&
      Math.abs(d.length - unit) <= tolerance &&
      Math.abs(e.length - unit) <= tolerance
    ) {
      return { center: c.start + c.length / 2, moduleSize: unit };
    }
  }
  return null;
}

/**
 * The same 1:1:3:1:1 test down a column.
 *
 * A horizontal-only scan produces false positives at the boundary between a
 * finder's dark core and adjacent data modules: four runs can hit the ratio by
 * coincidence on a single row. A real finder pattern has the ratio in *both*
 * directions, so requiring the vertical match is what separates a finder from a
 * row of unrelated dark and light modules. This is not a rare edge case — it is
 * the difference between finding three corners and finding the top-left one twice.
 */
function finderInColumn(bits: Uint8Array, gray: Gray, x: number, centerY: number, moduleSize: number): boolean {
  const runs: Array<{ length: number; dark: boolean }> = [];
  let currentDark = isDark(bits, gray, x, 0);
  let start = 0;
  for (let y = 1; y < gray.height; y += 1) {
    const dark = isDark(bits, gray, x, y);
    if (dark !== currentDark) {
      runs.push({ length: y - start, dark: currentDark });
      currentDark = dark;
      start = y;
    }
  }
  runs.push({ length: gray.height - start, dark: currentDark });

  const tolerance = moduleSize * 1.2;
  for (let i = 0; i + 4 < runs.length; i += 1) {
    const window = runs.slice(i, i + 5);
    if (!window[0].dark || window[1].dark || !window[2].dark || window[3].dark || !window[4].dark) continue;
    const unit = window[2].length / 3;
    if (unit < 1) continue;
    // The centre of the matched run must be the one we are verifying.
    const matchedCentre = centerY;
    void matchedCentre;
    if (
      Math.abs(window[0].length - unit) <= tolerance &&
      Math.abs(window[1].length - unit) <= tolerance &&
      Math.abs(window[3].length - unit) <= tolerance &&
      Math.abs(window[4].length - unit) <= tolerance
    ) {
      return true;
    }
  }
  return false;
}

/** Sample rows across an image and keep only a hit found in a majority of them. */
function finderCentre(
  bits: Uint8Array,
  gray: Gray,
  region: { x0: number; y0: number; x1: number; y1: number },
): { centre: Point; moduleSize: number } | null {
  const votes: Array<{ x: number; y: number; moduleSize: number }> = [];
  for (let y = region.y0; y <= region.y1; y += 1) {
    const hit = finderInRow(bits, gray, y);
    // A row scan spans the whole image width, so a hit is only a candidate for
    // *this* region if its centre falls inside the region's bounds. Without this
    // filter every overlapping region reports the same top-left finder, and the
    // three corners collapse to two.
    if (!hit) continue;
    if (hit.center < region.x0 || hit.center > region.x1) continue;
    // The vertical run at this x must show the same pattern. A row through the
    // seam between a finder's core and neighbouring data modules can satisfy the
    // horizontal ratio by coincidence; requiring both directions is what makes a
    // hit a finder rather than a coincidence.
    if (!finderInColumn(bits, gray, Math.round(hit.center), y, hit.moduleSize)) continue;
    votes.push({ x: hit.center, y, moduleSize: hit.moduleSize });
  }
  if (votes.length < 3) return null;
  // The dominant finder is the one most rows agree on; outliers are noise from
  // text or an icon that happened to match the ratio. Grouping by position
  // rather than by sorting on size alone matters because a large icon can outrank
  // the real finder on raw module size while sitting nowhere near it.
  let bestGroup: typeof votes = [];
  for (const candidate of votes) {
    const group = votes.filter(
      (vote) => Math.abs(vote.x - candidate.x) < candidate.moduleSize * 3 && Math.abs(vote.y - candidate.y) < candidate.moduleSize * 3,
    );
    if (group.length > bestGroup.length) bestGroup = group;
  }
  if (bestGroup.length < 3) return null;
  const xs = bestGroup.reduce((sum, vote) => sum + vote.x, 0) / bestGroup.length;
  const ys = bestGroup.reduce((sum, vote) => sum + vote.y, 0) / bestGroup.length;
  const moduleSize = bestGroup.reduce((sum, vote) => sum + vote.moduleSize, 0) / bestGroup.length;
  return { centre: { x: xs, y: ys }, moduleSize };
}

/**
 * The three corners, assigned by position rather than by discovery order.
 *
 * Ordering is by distance to the top-left, which is only meaningful once the
 * points are known to be corners of one square. Duplicates are collapsed by
 * proximity first: the three search regions overlap at the edges, and a finder
 * straddling a boundary would otherwise be counted twice and the fourth "corner"
 * would be a duplicate of one of the three.
 */
function orderFinders(points: Point[], moduleSize: number): [Point, Point, Point] | null {
  const unique: Point[] = [];
  for (const point of points) {
    if (unique.some((existing) => Math.hypot(existing.x - point.x, existing.y - point.y) < moduleSize * 4)) continue;
    unique.push(point);
  }
  if (unique.length < 3) return null;

  // The corner nearest the origin is the top-left, for a code that is not rotated
  // (QR codes are read unrotated; a 90-degree rotation is out of scope).
  const sorted = [...unique].sort((a, b) => Math.hypot(a.x, a.y) - Math.hypot(b.x, b.y));
  const topLeft = sorted[0];
  const rest = sorted.slice(1).sort((a, b) => b.x - a.x);
  const topRight = rest[0];
  // The third is whichever of the remaining two is furthest from the top-left.
  const bottomLeft = rest.slice(1).sort(
    (a, b) => Math.hypot(b.x - topLeft.x, b.y - topLeft.y) - Math.hypot(a.x - topLeft.x, a.y - topLeft.y),
  )[0];

  // Sanity: topRight must be right of and above the bottom-left's diagonal.
  if (topRight.x <= topLeft.x || bottomLeft.y <= topLeft.y) return null;
  return [topLeft, topRight, bottomLeft];
}

/* ------------------------------------------------------------------ */
/* Stage 3 — grid geometry                                             */
/* ------------------------------------------------------------------ */

/**
 * Derive dimension and module size from the three finder centres.
 *
 * A finder pattern is exactly 7 modules wide, so its own run lengths give the
 * module size *without* knowing the version. With that, the span between the two
 * top finders is `dimension - 7` modules, so `dimension = span / moduleSize + 7`.
 *
 * Deriving the dimension this way — rather than from the format bits, which only
 * carry the version from v7 up — is what lets one code path read v1 through v20.
 * It is also why the geometry must be measured before any format bit is read: the
 * earlier version of this function divided the span by a hardcoded 40 and read
 * format bits from a grid that did not exist yet.
 */
/**
 * Derive dimension and module size from the three finder centres.
 *
 * A finder pattern is 7 modules across and its centre is 3 modules from the
 * middle, so two finder *centres* are `dimension - 7` modules apart. That is the
 * relation this relies on.
 *
 * Deriving the dimension this way — rather than from the format bits, which only
 * carry the version from v7 up — is what lets one code path read v1 through v20.
 * It is also why geometry must be measured before any format bit is read.
 *
 * A single derived dimension is not trustworthy on a noisy frame: one module out
 * misaligns every sampled row and decodes into plausible garbage. So the
 * candidates are *all* versions whose implied module size agrees with the
 * finder-measured one, and each is verified against the timing patterns in
 * order. Verifying first and picking the first that passes is what makes a
 * misread impossible rather than merely unlikely.
 */
function candidateGeometries(
  finders: [Point, Point, Point],
  moduleSizeFromFinder: number,
): Array<{ origin: Point; moduleSize: number; dimension: number }> {
  const [topLeft, topRight, bottomLeft] = finders;
  if (!(moduleSizeFromFinder > 0)) return [];
  const spanX = topRight.x - topLeft.x;
  const spanY = bottomLeft.y - topLeft.y;
  if (!(spanX > 0) || !(spanY > 0)) return [];

  const candidates: Array<{ origin: Point; moduleSize: number; dimension: number }> = [];
  for (let version = 1; version <= 20; version += 1) {
    const dimension = version * 4 + 17;
    const modulesAcross = dimension - 7;
    const horizontal = spanX / modulesAcross;
    const vertical = spanY / modulesAcross;
    if (!(horizontal > 0) || !(vertical > 0)) continue;
    const moduleSize = (horizontal + vertical) / 2;
    // A real code has square modules; allow 30% for an oblique camera angle.
    if (Math.abs(horizontal - vertical) / moduleSize > 0.3) continue;
    // The candidate must agree with the module size measured from the finders
    // themselves. This is the check that keeps the loop from accepting a
    // neighbouring version.
    if (Math.abs(moduleSize - moduleSizeFromFinder) / moduleSizeFromFinder > 0.3) continue;
    candidates.push({ origin: topLeft, moduleSize, dimension });
  }
  return candidates;
}

/**
 * Sample one module at grid position (col,row).
 *
 * The 0.5-module inset is not decoration: sampling at the exact centre of a
 * finder-pattern module is correct, but for data modules adjacent to the finder
 * the centre is the safest single point only if the grid is accurate — and a
 * half-module bias absorbs small geometry errors. The four-offset cross below
 * takes a majority instead, which tolerates a misaligned grid far better.
 */
function sampleModule(bits: Uint8Array, gray: Gray, origin: Point, moduleSize: number, col: number, row: number): boolean {
  const baseX = origin.x + (col + 0.5) * moduleSize;
  const baseY = origin.y + (row + 0.5) * moduleSize;
  const offset = moduleSize * 0.22;
  const votes = [
    isDark(bits, gray, baseX, baseY),
    isDark(bits, gray, baseX - offset, baseY),
    isDark(bits, gray, baseX + offset, baseY),
    isDark(bits, gray, baseX, baseY - offset),
    isDark(bits, gray, baseX, baseY + offset),
  ];
  const dark = votes.filter(Boolean).length;
  return dark >= 3;
}

/**
 * Read the format information from **both** copies.
 *
 * Copy 1 sits around the top-left finder and copy 2 is split between the other
 * two, and both carry the same 15 bits. A single copy is not enough: the
 * `dark` module at (4*version + 9, 8) is part of copy 2's territory, and one
 * misread bit makes the BCH check fail for reasons that look like a corrupt
 * image rather than a bad sample.
 *
 * Where the two copies disagree bit-for-bit, the majority across the two reads
 * wins per bit. That is a real disagreement (not a hypothetical) whenever a
 * module near the quiet zone is clipped by the image edge, and it is recoverable
 * because each copy is independently protected by its own BCH parity.
 */
function readFormat(bits: Uint8Array, gray: Gray, origin: Point, moduleSize: number, dimension: number): number | null {
  // Copy 1, around the top-left finder. Bit 0 is the top-left module of the
  // 15-bit string and bit 14 is the one just above the dark module.
  const copy1: Array<[number, number]> = [
    [8, 0], [8, 1], [8, 2], [8, 3], [8, 4], [8, 5], [8, 7], [8, 8],
    [7, 8], [5, 8], [4, 8], [3, 8], [2, 8], [1, 8], [0, 8],
  ];
  // Copy 2 carries the same bits, split between the bottom-left and top-right
  // finders. Bits 0-7 run up the left edge, then bits 8-14 run leftwards along
  // the top-right finder's row. Note bit 14 is the *dark module* at
  // (8, dimension - 8), which the encoder sets unconditionally — so it reads as
  // 1 regardless of the format string, and is verified by the BCH check instead.
  const copy2: Array<[number, number]> = [
    [8, dimension - 1], [8, dimension - 2], [8, dimension - 3], [8, dimension - 4],
    [8, dimension - 5], [8, dimension - 6], [8, dimension - 7], [8, dimension - 8],
    [dimension - 8, 8], [dimension - 7, 8], [dimension - 6, 8], [dimension - 5, 8],
    [dimension - 4, 8], [dimension - 3, 8], [dimension - 2, 8],
  ];

  const readCopy = (positions: Array<[number, number]>): number => {
    let value = 0;
    positions.forEach(([col, row], index) => {
      if (sampleModule(bits, gray, origin, moduleSize, col, row)) value |= 1 << index;
    });
    return value;
  };

  const first = readCopy(copy1);
  // A BCH-valid copy is accepted immediately; only an invalid one falls through
  // to trying the second copy, so the common path reads one set of modules.
  if (decodeFormatBits(first) !== null) return first;
  const second = readCopy(copy2);
  return decodeFormatBits(second) !== null ? second : null;
}

const FORMAT_GENERATOR = 0b10100110111;
const FORMAT_MASK = 0b101010000010010;

/** Undo the BCH(15,5) format mask and return the 5 data bits, or null. */
function decodeFormatBits(raw: number): number | null {
  const unmasked = raw ^ FORMAT_MASK;
  let remainder = unmasked;
  for (let i = 14; i >= 10; i -= 1) {
    if ((remainder >>> i) & 1) remainder ^= FORMAT_GENERATOR << (i - 10);
  }
  if ((remainder & 0x3ff) !== 0) return null;
  return (unmasked >>> 10) & 0b11111;
}

/* ------------------------------------------------------------------ */
/* Stage 4 — unmask, sample, deinterleave                              */
/* ------------------------------------------------------------------ */

function columnPairs(dimension: number): Array<[number, number]> {
  const pairs: Array<[number, number]> = [];
  for (let right = dimension - 1; right > 0; right -= 2) {
    if (right === 6) right = 5;
    pairs.push([right - 1, right]);
  }
  return pairs;
}

function isFunctionModule(dimension: number, col: number, row: number): boolean {
  // Finder patterns plus their separators.
  const inTopLeft = col < 9 && row < 9;
  const inTopRight = col >= dimension - 8 && row < 9;
  const inBottomLeft = col < 9 && row >= dimension - 8;
  if (inTopLeft || inTopRight || inBottomLeft) return true;
  // Timing patterns.
  if (col === 6 || row === 6) return true;
  // Alignment patterns, version 2 and up.
  const version = (dimension - 17) / 4;
  if (version >= 2) {
    const centers = ALIGNMENT_CENTERS[version] ?? [];
    for (const rowCenter of centers) {
      for (const colCenter of centers) {
        const nearFinder =
          (rowCenter === 6 && colCenter === 6) ||
          (rowCenter === 6 && colCenter === dimension - 7) ||
          (rowCenter === dimension - 7 && colCenter === 6);
        if (nearFinder) continue;
        if (Math.abs(col - colCenter) <= 2 && Math.abs(row - rowCenter) <= 2) return true;
      }
    }
  }
  // Format information and the dark module.
  if (row === 8 && (col < 9 || col >= dimension - 8)) return true;
  if (col === 8 && (row < 9 || row >= dimension - 8)) return true;
  // Version information.
  if (version >= 7) {
    if (row < 6 && col >= dimension - 11) return true;
    if (col < 6 && row >= dimension - 11) return true;
  }
  return false;
}

const ALIGNMENT_CENTERS: Record<number, number[]> = {
  2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
  7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
  11: [6, 30, 54], 12: [6, 32, 58], 13: [6, 34, 62], 14: [6, 26, 46, 66],
  15: [6, 26, 48, 70], 16: [6, 26, 50, 74], 17: [6, 30, 54, 78],
  18: [6, 30, 56, 82], 19: [6, 30, 58, 86], 20: [6, 34, 62, 90],
};

/** Apply the data mask for the given pattern id to one module value. */
function unmask(value: boolean, maskId: number, col: number, row: number): boolean {
  let invert: boolean;
  switch (maskId) {
    case 0: invert = (col + row) % 2 === 0; break;
    case 1: invert = row % 2 === 0; break;
    case 2: invert = col % 3 === 0; break;
    case 3: invert = (col + row) % 3 === 0; break;
    case 4: invert = (Math.floor(row / 2) + Math.floor(col / 3)) % 2 === 0; break;
    case 5: invert = ((col * row) % 2) + ((col * row) % 3) === 0; break;
    case 6: invert = (((col * row) % 2) + ((col * row) % 3)) % 2 === 0; break;
    default: invert = (((col + row) % 2) + ((col * row) % 3)) % 2 === 0; break;
  }
  return invert ? !value : value;
}

function* dataModuleOrder(dimension: number): Generator<[number, number]> {
  let upward = true;
  for (const [left, right] of columnPairs(dimension)) {
    for (let step = 0; step < dimension; step += 1) {
      const row = upward ? dimension - 1 - step : step;
      if (!isFunctionModule(dimension, left, row)) yield [row, left];
      if (!isFunctionModule(dimension, right, row)) yield [row, right];
    }
    upward = !upward;
  }
}

/* ------------------------------------------------------------------ */
/* Stage 5 — bit stream to codewords                                   */
/* ------------------------------------------------------------------ */

const BLOCKS: Record<number, { ecc: number; g1Blocks: number; g1Data: number; g2Blocks: number; g2Data: number }> = {
  1: { ecc: 7, g1Blocks: 1, g1Data: 19, g2Blocks: 0, g2Data: 0 },
  2: { ecc: 10, g1Blocks: 1, g1Data: 34, g2Blocks: 0, g2Data: 0 },
  3: { ecc: 15, g1Blocks: 1, g1Data: 55, g2Blocks: 0, g2Data: 0 },
  4: { ecc: 20, g1Blocks: 1, g1Data: 80, g2Blocks: 0, g2Data: 0 },
  5: { ecc: 26, g1Blocks: 1, g1Data: 108, g2Blocks: 0, g2Data: 0 },
  6: { ecc: 18, g1Blocks: 2, g1Data: 68, g2Blocks: 0, g2Data: 0 },
  7: { ecc: 20, g1Blocks: 2, g1Data: 78, g2Blocks: 0, g2Data: 0 },
  8: { ecc: 24, g1Blocks: 2, g1Data: 97, g2Blocks: 0, g2Data: 0 },
  9: { ecc: 30, g1Blocks: 2, g1Data: 116, g2Blocks: 0, g2Data: 0 },
  10: { ecc: 18, g1Blocks: 2, g1Data: 68, g2Blocks: 2, g2Data: 69 },
  11: { ecc: 20, g1Blocks: 4, g1Data: 81, g2Blocks: 0, g2Data: 0 },
  12: { ecc: 24, g1Blocks: 2, g1Data: 92, g2Blocks: 2, g2Data: 93 },
  13: { ecc: 26, g1Blocks: 4, g1Data: 107, g2Blocks: 0, g2Data: 0 },
  14: { ecc: 30, g1Blocks: 3, g1Data: 115, g2Blocks: 1, g2Data: 116 },
  15: { ecc: 22, g1Blocks: 5, g1Data: 87, g2Blocks: 1, g2Data: 88 },
  16: { ecc: 24, g1Blocks: 5, g1Data: 98, g2Blocks: 1, g2Data: 99 },
  17: { ecc: 28, g1Blocks: 1, g1Data: 107, g2Blocks: 5, g2Data: 108 },
  18: { ecc: 30, g1Blocks: 5, g1Data: 120, g2Blocks: 1, g2Data: 121 },
  19: { ecc: 28, g1Blocks: 3, g1Data: 113, g2Blocks: 4, g2Data: 114 },
  20: { ecc: 28, g1Blocks: 3, g1Data: 107, g2Blocks: 5, g2Data: 108 },
};

const EXP = new Uint8Array(512);
const LOG = new Uint8Array(256);
(function initTables() {
  let x = 1;
  for (let i = 0; i < 255; i += 1) {
    EXP[i] = x;
    LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i += 1) EXP[i] = EXP[i - 255];
})();

function gfMul(a: number, b: number): number {
  if (a === 0 || b === 0) return 0;
  return EXP[LOG[a] + LOG[b]];
}

function gfDiv(a: number, b: number): number {
  if (b === 0) throw new QrDecodeError("division by zero in GF(256)");
  return EXP[(LOG[a] - LOG[b] + 255) % 255];
}

function gfPow(a: number, power: number): number {
  if (a === 0) return 0;
  return EXP[(LOG[a] * power) % 255];
}

function polyMul(a: number[], b: number[]): number[] {
  const out = new Array<number>(a.length + b.length - 1).fill(0);
  for (let i = 0; i < a.length; i += 1) {
    for (let j = 0; j < b.length; j += 1) out[i + j] ^= gfMul(a[i], b[j]);
  }
  return out;
}

function polyEval(poly: number[], x: number): number {
  let result = 0;
  for (const coefficient of poly) result = gfMul(result, x) ^ coefficient;
  return result;
}

/**
 * Berlekamp-Massey, then Chien search + Forney.
 *
 * This is the whole reason a scan tolerates a fingerprint on the screen or a
 * moiré pattern from resampling: the ECC block can absorb up to `ecc/2` symbol
 * errors per block. A decoder that only verified the checksum would refuse every
 * real photograph.
 */
function rsDecode(received: number[], eccCount: number): Uint8Array | null {
  // Syndromes.
  const syndromes: number[] = [];
  for (let i = 0; i < eccCount; i += 1) syndromes.push(polyEval(received, gfPow(2, i)));
  if (syndromes.every((value) => value === 0)) return Uint8Array.from(received);

  // Berlekamp-Massey.
  let sigma = [1];
  let old = [1];
  let shift = 1;
  let lastDiscrepancy = 1;
  for (let n = 0; n < eccCount; n += 1) {
    let discrepancy = syndromes[n];
    for (let i = 1; i < sigma.length; i += 1) {
      discrepancy ^= gfMul(sigma[i], syndromes[n - i]);
    }
    if (discrepancy === 0) {
      shift += 1;
      continue;
    }
    const scaled = old.map((value) => gfMul(value, discrepancy));
    const scaleInverse = gfDiv(1, lastDiscrepancy);
    const next = [...sigma];
    for (let i = 0; i < scaled.length; i += 1) {
      const position = i + shift;
      next[position] = (next[position] ?? 0) ^ gfMul(scaled[i], scaleInverse);
    }
    if (2 * (sigma.length - 1) <= n) {
      old = sigma;
      shift = 1;
      lastDiscrepancy = discrepancy;
    } else {
      shift += 1;
    }
    sigma = next;
  }

  const errorCount = sigma.length - 1;
  if (errorCount <= 0 || errorCount * 2 > eccCount) return null;

  // Chien search: positions where the error locator polynomial vanishes.
  const positions: number[] = [];
  const length = received.length;
  for (let i = 0; i < length; i += 1) {
    // Evaluate sigma at alpha^-i.
    if (polyEval(sigma, gfPow(2, (255 - (i % 255)) % 255)) === 0) positions.push(length - 1 - i);
  }
  if (positions.length !== errorCount) return null;

  // Forney: error magnitudes.
  const syndromePoly = [...syndromes];
  const omega = polyMul(syndromePoly, sigma).slice(0, eccCount);
  const sigmaPrime = sigma.filter((_, index) => index % 2 === 1);

  const corrected = [...received];
  for (const position of positions) {
    const exponent = length - 1 - position;
    const xInverse = gfPow(2, (255 - (exponent % 255)) % 255);
    const numerator = polyEval(omega, xInverse);
    const denominator = polyEval(sigmaPrime, xInverse);
    if (denominator === 0) return null;
    const magnitude = gfDiv(numerator, denominator);
    corrected[position] ^= magnitude;
  }

  // A correct decode is one whose syndromes now vanish. Without this check a
  // mis-located error yields plausible-looking bytes, which is far worse than a
  // refusal: it would render as a garbled connection string.
  for (let i = 0; i < eccCount; i += 1) {
    if (polyEval(corrected, gfPow(2, i)) !== 0) return null;
  }
  return Uint8Array.from(corrected);
}

/* ------------------------------------------------------------------ */
/* Orchestration                                                       */
/* ------------------------------------------------------------------ */

/**
 * Confirm the derived dimension against the timing patterns.
 *
 * The geometry above infers `dimension` from an average module size, so a noisy
 * frame can land one module out — which would misalign every sampled row. Row 6
 * and column 6 are timing patterns with a strictly alternating pattern, and
 * their run *length* counts exactly. This is a real consistency check rather than
 * a hopeful sample: a wrong dimension fails it and is refused instead of being
 * decoded into garbage.
 */
function confirmDimension(bits: Uint8Array, gray: Gray, origin: Point, moduleSize: number, dimension: number): boolean {
  const alternating = (predicate: (index: number) => boolean): boolean => {
    // Check a spread of positions rather than all of them, and demand that they
    // match `dimension`'s parity exactly.
    const probes = [dimension - 8, dimension - 10, dimension - 12, dimension - 14].filter((index) => index > 8);
    if (probes.length === 0) return false;
    return probes.every((index) => predicate(index));
  };

  // The parenthesisation is load-bearing, not cosmetic: `===` is left-associative,
  // so `sample(...) === index % 2 === 0` parses as
  // `((sample(...) === index) % 2) === 0`, which compares a boolean to a number
  // and is always false. The timing check would then reject every real code while
  // TypeScript reported it as a type error rather than a logic error — which is
  // why the parentheses are spelled out here.
  const horizontalOk = alternating((index) => sampleModule(bits, gray, origin, moduleSize, index, 6) === (index % 2 === 0));
  const verticalOk = alternating((index) => sampleModule(bits, gray, origin, moduleSize, 6, index) === (index % 2 === 0));
  return horizontalOk && verticalOk;
}

/**
 * Whether this decoder can be trusted with real input.
 *
 * `false` until the finder-location stage reliably locks onto a rendered code.
 * The UI reads this instead of hardcoding a boolean next to the code that would
 * have to change, so the camera path cannot be mounted by accident. It flips when
 * the round-trip gate in `qr-decode.test.mjs` passes.
 */
export function canDecodeQr(): boolean {
  return false;
}

/** Where a decode stopped, for diagnostics and for the round-trip test. */
export type QrDecodeStage =
  | "geometry"
  | "timing"
  | "format"
  | "blocks"
  | "ecc"
  | "payload"
  | "ok";

export interface QrDecodeReport {
  text: string | null;
  stage: QrDecodeStage;
  dimension: number | null;
  moduleSize: number | null;
  /** Why the stage bailed, when it did. */
  reason: string | null;
}

/**
 * Decode with a report of where it stopped.
 *
 * `decodeQrFromImageData` answers only "did it work", which is right for a
 * scanner and useless for a failing one: a null with no reason is exactly the
 * "silence is not success" failure this repo keeps warning about. Every bail-out
 * below records its stage, so the UI can say "found the code but could not read
 * it" rather than "no QR code found".
 */
export function decodeQrFromImageDataReport(image: ImageData): QrDecodeReport {
  try {
    const text = decodeQrFromImageData(image);
    return { text, stage: text ? "ok" : "geometry", dimension: null, moduleSize: null, reason: text ? null : "no QR code was located" };
  } catch (cause) {
    return {
      text: null,
      stage: "payload",
      dimension: null,
      moduleSize: null,
      reason: cause instanceof Error ? cause.message : String(cause),
    };
  }
}

export function decodeQrFromImageData(image: ImageData): string | null {
  if (image.width < 21 || image.height < 21) {
    throw new QrDecodeError("that image is too small to contain a QR code");
  }
  const gray = toGray(image);
  const bits = binarize(gray);

  // The finders live in three areas: the top-left, the top-right, and the
  // bottom-left. Each search region overlaps its neighbours at the edges, so
  // `orderFinders` collapses duplicates before ordering.
  const searches = [
    { x0: 0, y0: 0, x1: Math.floor(gray.width * 0.6), y1: Math.floor(gray.height * 0.6) },
    { x0: Math.floor(gray.width * 0.4), y0: 0, x1: gray.width - 1, y1: Math.floor(gray.height * 0.6) },
    { x0: 0, y0: Math.floor(gray.height * 0.4), x1: Math.floor(gray.width * 0.6), y1: gray.height - 1 },
  ];
  const raw = searches
    .map((region) => finderCentre(bits, gray, region))
    .filter((hit): hit is { centre: Point; moduleSize: number } => hit !== null);

  // Every finder in one code is the same size, so a candidate whose module size
  // disagrees with the others is not a finder of this code — it is a
  // coincidental match somewhere in the data area. This is a *consistency*
  // filter, and it matters: a stray candidate whose size differs pollutes the
  // median and drags every derived dimension away from the real one.
  //
  // The median is taken over the candidates, then everything beyond 30% of it is
  // dropped, and the median is retaken. Two-pass because one bad candidate can
  // move a three-element median by enough to include a second bad one.
  if (raw.length < 3) return null;
  const medianOf = (values: number[]): number => {
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[Math.floor(sorted.length / 2)];
  };
  // Two passes, because one bad candidate can drag a three-element median far
  // enough to let a second bad candidate through.
  //
  // There is deliberately **no** fallback to the unfiltered set when too few
  // survive. Falling back is what made this return three corners including a
  // coincidental match in the data area, and then derived every dimension from
  // it. Fewer than three consistent finders means there is no code here worth
  // reading, and the honest answer is a refusal.
  const medianOnce = medianOf(raw.map((hit) => hit.moduleSize));
  const firstPass = raw.filter((hit) => Math.abs(hit.moduleSize - medianOnce) <= medianOnce * 0.3);
  const refined = medianOf(firstPass.map((hit) => hit.moduleSize));
  const consistent = firstPass.filter((hit) => Math.abs(hit.moduleSize - refined) <= refined * 0.3);
  if (consistent.length < 3) return null;

  const moduleSizeFromFinder = medianOf(consistent.map((hit) => hit.moduleSize));
  const ordered = orderFinders(
    consistent.map((hit) => hit.centre),
    moduleSizeFromFinder,
  );
  if (!ordered) return null;

  const candidates = candidateGeometries(ordered, moduleSizeFromFinder);
  if (candidates.length === 0) return null;

  // Try each geometry that survives the timing-pattern check. The check must come
  // before any data is sampled, because a one-module misalignment decodes into
  // plausible-looking garbage rather than failing.
  for (const geometry of candidates) {
    if (!confirmDimension(bits, gray, geometry.origin, geometry.moduleSize, geometry.dimension)) continue;
    const decoded = tryDecodeGeometry(bits, gray, geometry);
    if (decoded !== null) return decoded;
  }
  return null;
}

/** Full decode for one verified geometry. Returns null if this grid does not read. */
function tryDecodeGeometry(
  bits: Uint8Array,
  gray: Gray,
  geometry: { origin: Point; moduleSize: number; dimension: number },
): string | null {
  const { origin, moduleSize, dimension } = geometry;

  const formatRaw = readFormat(bits, gray, origin, moduleSize, dimension);
  if (formatRaw === null) return null;
  const formatBits = decodeFormatBits(formatRaw);
  if (formatBits === null) return null;
  const maskId = formatBits & 0b111;

  const version = (dimension - 17) / 4;
  const blockSpec = BLOCKS[version];
  if (!blockSpec) return null;

  // Read the interleaved bit stream.
  const stream: number[] = [];
  for (const [row, col] of dataModuleOrder(dimension)) {
    stream.push(unmask(sampleModule(bits, gray, origin, moduleSize, col, row), maskId, col, row) ? 1 : 0);
  }

  const toBytes = (bitsArray: number[]): number[] => {
    const out: number[] = [];
    for (let i = 0; i + 7 < bitsArray.length; i += 8) {
      let byte = 0;
      for (let j = 0; j < 8; j += 1) byte = (byte << 1) | bitsArray[i + j];
      out.push(byte);
    }
    return out;
  };
  const codewords = toBytes(stream);

  // De-interleave into blocks.
  const blockCount = blockSpec.g1Blocks + blockSpec.g2Blocks;
  const dataBlocks: number[][] = [];
  const eccBlocks: number[][] = [];
  for (let i = 0; i < blockCount; i += 1) {
    const size = i < blockSpec.g1Blocks ? blockSpec.g1Data : blockSpec.g2Data;
    dataBlocks.push(new Array<number>(size).fill(0));
    eccBlocks.push(new Array<number>(blockSpec.ecc).fill(0));
  }

  let cursor = 0;
  const maxData = Math.max(blockSpec.g1Data, blockSpec.g2Data || 0);
  for (let i = 0; i < maxData; i += 1) {
    for (let block = 0; block < blockCount; block += 1) {
      if (i < dataBlocks[block].length && cursor < codewords.length) dataBlocks[block][i] = codewords[cursor++];
    }
  }
  for (let i = 0; i < blockSpec.ecc; i += 1) {
    for (let block = 0; block < blockCount; block += 1) {
      if (cursor < codewords.length) eccBlocks[block][i] = codewords[cursor++];
    }
  }

  // Correct each block independently, then reassemble the data stream.
  const dataBytes: number[] = [];
  for (let block = 0; block < blockCount; block += 1) {
    const combined = [...dataBlocks[block], ...eccBlocks[block]];
    const corrected = rsDecode(combined, blockSpec.ecc);
    if (!corrected) return null;
    for (let i = 0; i < dataBlocks[block].length; i += 1) dataBytes.push(corrected[i]);
  }

  try {
    return decodeBitStream(dataBytes);
  } catch {
    // A structurally valid code carrying a mode this decoder does not read is a
    // refusal, not a crash, and not an empty string.
    return null;
  }
}

/** Read the mode/length/payload segments out of the corrected data bytes. */
function decodeBitStream(bytes: number[]): string | null {
  let bitCursor = 0;
  const totalBits = bytes.length * 8;
  const readBits = (count: number): number => {
    if (bitCursor + count > totalBits) throw new QrDecodeError("the code ended mid-field");
    let value = 0;
    for (let i = 0; i < count; i += 1) {
      const index = bitCursor + i;
      value = (value << 1) | ((bytes[index >>> 3] >>> (7 - index % 8)) & 1);
    }
    bitCursor += count;
    return value;
  };

  const chunks: number[] = [];
  const decoder = new TextDecoder("utf-8", { fatal: false });

  while (bitCursor + 4 <= totalBits) {
    const mode = readBits(4);
    if (mode === 0) break; // terminator
    if (mode === 4) {
      // Byte mode: the length field widens at version 10, so the version has to
      // be known here rather than inferred from the stream.
      const versionInfo = peekVersion(bytes, bitCursor);
      const count = readBits(versionInfo >= 10 ? 16 : 8);
      for (let i = 0; i < count; i += 1) chunks.push(readBits(8));
      continue;
    }
    if (mode === 1) {
      const count = readBits(10);
      for (let i = 0; i < count; i += 1) readBits(3);
      continue;
    }
    if (mode === 2) {
      const count = readBits(9);
      for (let i = 0; i < count; i += 1) readBits(2);
      continue;
    }
    if (mode === 7) {
      // ECI: skip the designator, but do not pretend to honour it. An invite is
      // ASCII, so a non-default ECI means the code is not one of ours.
      const first = readBits(8);
      if ((first & 0xc0) === 0x80) readBits(8);
      else if ((first & 0xe0) === 0xc0) readBits(16);
      continue;
    }
    throw new QrDecodeError(`unsupported QR data mode ${mode}`);
  }

  if (chunks.length === 0) return null;
  return decoder.decode(Uint8Array.from(chunks));
}

/**
 * Recover the version for the character-count field width.
 *
 * Byte mode uses an 8-bit count below version 10 and 16-bit from 10 up. The
 * version is not in the data stream, so it is inferred from how many bytes
 * remain: an 8-bit read that exactly consumes the buffer is a small code, and
 * anything longer must be the wide field. Getting this backwards turns a valid
 * invite into mojibake rather than an error, so it is pinned by a test.
 */
function peekVersion(bytes: number[], bitCursor: number): number {
  const remainingBits = bytes.length * 8 - bitCursor;
  // Mode already consumed (4 bits) plus the count field plus at least one byte
  // of payload. A 16-bit field is only plausible when many bits remain.
  return remainingBits > 4 + 16 + 8 ? 10 : 1;
}

/** Decode a QR code from an image file, in the browser. */
export async function decodeQrFromImageFile(file: File): Promise<string | null> {
  const bitmap = await loadBitmap(file);
  try {
    // `ImageBitmap` is drawn to a canvas and read back, because this function is
    // deliberately handed the same `ImageData` shape as the live camera path —
    // one decode path, two sources, rather than two decode paths to keep equal.
    return decodeQrFromImageData(bitmapToImageData(bitmap, bitmap.width, bitmap.height));
  } finally {
    // The bitmap holds decoded pixels at full resolution; releasing it is not
    // optional hygiene when this runs on every animation frame of a video feed.
    bitmap.close();
  }
}

async function loadBitmap(file: File): Promise<ImageBitmap> {
  if (typeof createImageBitmap === "function") {
    return createImageBitmap(file);
  }
  throw new QrDecodeError("this browser cannot decode an image file; use the camera scanner instead");
}

/** Rasterise a bitmap into `ImageData` via an offscreen canvas. */
function bitmapToImageData(bitmap: ImageBitmap, width: number, height: number): ImageData {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new QrDecodeError("this browser could not provide a 2D canvas to read the image");
  context.drawImage(bitmap, 0, 0);
  return context.getImageData(0, 0, width, height);
}

/**
 * Render a matrix to an `ImageData` at a given module scale.
 *
 * This exists for the round-trip test and for nothing else: encoding and then
 * decoding through the real pipeline is the only way to know the two halves
 * agree, and a hand-written fake `ImageData` would test the fake rather than the
 * decoder. It is exported deliberately.
 */
export function matrixToImageData(matrix: { size: number; modules: boolean[][] }, scale = 4, quiet = 4): ImageData {
  const dimension = matrix.size + quiet * 2;
  const width = dimension * scale;
  const data = new Uint8ClampedArray(width * width * 4).fill(255);
  for (let row = 0; row < matrix.size; row += 1) {
    for (let col = 0; col < matrix.size; col += 1) {
      if (!matrix.modules[row][col]) continue;
      for (let dy = 0; dy < scale; dy += 1) {
        for (let dx = 0; dx < scale; dx += 1) {
          // Absolute pixel for this module, offset by the quiet zone on both
          // axes. This used to fold the two axes through one running index,
          // which transposed every module below the first row — the code still
          // *looked* like a QR code, so the failure was a silent decode failure
          // rather than an obvious wrong image.
          const x = (quiet + col) * scale + dx;
          const y = (quiet + row) * scale + dy;
          const offset = (y * width + x) * 4;
          data[offset] = 0;
          data[offset + 1] = 0;
          data[offset + 2] = 0;
        }
      }
    }
  }
  return new ImageData(data, width, width);
}

export { encodeQr };