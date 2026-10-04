// QR encoder — zero dependencies.
//
// Why this exists rather than `qrcode.react`: the frontend runtime dependency
// list is deliberately twelve packages and the repo pins that surface. Adding a
// QR library for one dialog is a poor trade, and the *encoder* is the cheap half
// of the format — a Reed-Solomon block over a fixed module layout.
//
// What this deliberately does NOT do:
//
// - **It is not a scanner.** Decoding is a different and much larger problem
//   (`qr-decode.ts`); the encoder only has to produce a matrix.
// - **It does not choose the mode for you.** Numeric/alphanumeric/byte selection
//   is fixed to *byte* below a length threshold because an invite is base64/ASCII
//   and byte mode is always correct for it. A URL would benefit from
//   alphanumeric, and the trade is deliberately declined: byte mode is correct
//   for every input this app encodes, and one mode is one code path to test.
// - **It does not round-trip arbitrary binary.** Byte mode encodes the string as
//   UTF-8, which is what an invite needs.
//
// The output is a boolean matrix (`true` = dark module), not SVG. The caller
// renders it; keeping presentation out of here is what lets the same encoder
// drive a canvas, an SVG path, and a test assertion.

export const QR_MIN_VERSION = 1;
export const QR_MAX_VERSION = 40;

/** Quiet zone in modules. The spec's default is 4 and it is not optional. */
export const QUIET_ZONE = 4;

interface BlockSpec {
  totalCodewords: number;
  eccCodewordsPerBlock: number;
  group1Blocks: number;
  group1DataCodewords: number;
  group2Blocks: number;
  group2DataCodewords: number;
}

// Total codewords and the ECC layout per version, for the versions an invite can
// reach. An invite is ~400 bytes, so version 12 (L) covers it with room; the
// table stops at 20 because anything larger is not something this dialog should
// be asked to render, and a refusal beats a silent fallback to a smaller version.
const VERSIONS: Record<number, BlockSpec> = {
  1: { totalCodewords: 26, eccCodewordsPerBlock: 7, group1Blocks: 1, group1DataCodewords: 19, group2Blocks: 0, group2DataCodewords: 0 },
  2: { totalCodewords: 44, eccCodewordsPerBlock: 10, group1Blocks: 1, group1DataCodewords: 34, group2Blocks: 0, group2DataCodewords: 0 },
  3: { totalCodewords: 70, eccCodewordsPerBlock: 15, group1Blocks: 1, group1DataCodewords: 55, group2Blocks: 0, group2DataCodewords: 0 },
  4: { totalCodewords: 100, eccCodewordsPerBlock: 20, group1Blocks: 1, group1DataCodewords: 80, group2Blocks: 0, group2DataCodewords: 0 },
  5: { totalCodewords: 134, eccCodewordsPerBlock: 26, group1Blocks: 1, group1DataCodewords: 108, group2Blocks: 0, group2DataCodewords: 0 },
  6: { totalCodewords: 172, eccCodewordsPerBlock: 18, group1Blocks: 2, group1DataCodewords: 68, group2Blocks: 0, group2DataCodewords: 0 },
  7: { totalCodewords: 196, eccCodewordsPerBlock: 20, group1Blocks: 2, group1DataCodewords: 78, group2Blocks: 0, group2DataCodewords: 0 },
  8: { totalCodewords: 242, eccCodewordsPerBlock: 24, group1Blocks: 2, group1DataCodewords: 97, group2Blocks: 0, group2DataCodewords: 0 },
  9: { totalCodewords: 292, eccCodewordsPerBlock: 30, group1Blocks: 2, group1DataCodewords: 116, group2Blocks: 0, group2DataCodewords: 0 },
  10: { totalCodewords: 346, eccCodewordsPerBlock: 18, group1Blocks: 2, group1DataCodewords: 68, group2Blocks: 2, group2DataCodewords: 69 },
  11: { totalCodewords: 404, eccCodewordsPerBlock: 20, group1Blocks: 4, group1DataCodewords: 81, group2Blocks: 0, group2DataCodewords: 0 },
  12: { totalCodewords: 466, eccCodewordsPerBlock: 24, group1Blocks: 2, group1DataCodewords: 92, group2Blocks: 2, group2DataCodewords: 93 },
  13: { totalCodewords: 532, eccCodewordsPerBlock: 26, group1Blocks: 4, group1DataCodewords: 107, group2Blocks: 0, group2DataCodewords: 0 },
  14: { totalCodewords: 581, eccCodewordsPerBlock: 30, group1Blocks: 3, group1DataCodewords: 115, group2Blocks: 1, group2DataCodewords: 116 },
  15: { totalCodewords: 655, eccCodewordsPerBlock: 22, group1Blocks: 5, group1DataCodewords: 87, group2Blocks: 1, group2DataCodewords: 88 },
  16: { totalCodewords: 733, eccCodewordsPerBlock: 24, group1Blocks: 5, group1DataCodewords: 98, group2Blocks: 1, group2DataCodewords: 99 },
  17: { totalCodewords: 815, eccCodewordsPerBlock: 28, group1Blocks: 1, group1DataCodewords: 107, group2Blocks: 5, group2DataCodewords: 108 },
  18: { totalCodewords: 901, eccCodewordsPerBlock: 30, group1Blocks: 5, group1DataCodewords: 120, group2Blocks: 1, group2DataCodewords: 121 },
  19: { totalCodewords: 991, eccCodewordsPerBlock: 28, group1Blocks: 3, group1DataCodewords: 113, group2Blocks: 4, group2DataCodewords: 114 },
  20: { totalCodewords: 1085, eccCodewordsPerBlock: 28, group1Blocks: 3, group1DataCodewords: 107, group2Blocks: 5, group2DataCodewords: 108 },
};

export class QrEncodeError extends Error {}

const ALIGNMENT_CENTERS: Record<number, number[]> = {
  1: [],
  2: [6, 18],
  3: [6, 22],
  4: [6, 26],
  5: [6, 30],
  6: [6, 34],
  7: [6, 22, 38],
  8: [6, 24, 42],
  9: [6, 26, 46],
  10: [6, 28, 50],
  11: [6, 30, 54],
  12: [6, 32, 58],
  13: [6, 34, 62],
  14: [6, 26, 46, 66],
  15: [6, 26, 48, 70],
  16: [6, 26, 50, 74],
  17: [6, 30, 54, 78],
  18: [6, 30, 56, 82],
  19: [6, 30, 58, 86],
  20: [6, 34, 62, 90],
};

/* ------------------------------------------------------------------ */
/* Galois field GF(256), primitive 0x11d                               */
/* ------------------------------------------------------------------ */

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

/** Generator polynomial for `degree` EC codewords. */
function generatorPoly(degree: number): Uint8Array {
  let poly = new Uint8Array([1]);
  for (let i = 0; i < degree; i += 1) {
    const next = new Uint8Array(poly.length + 1);
    for (let j = 0; j < poly.length; j += 1) {
      next[j] ^= poly[j];
      next[j + 1] ^= gfMul(poly[j], EXP[i]);
    }
    poly = next;
  }
  return poly;
}

function ecCodewords(data: Uint8Array, count: number): Uint8Array {
  const gen = generatorPoly(count);
  const remainder = new Uint8Array(data.length + count);
  remainder.set(data, 0);
  for (let i = 0; i < data.length; i += 1) {
    const factor = remainder[i];
    if (factor === 0) continue;
    for (let j = 0; j < gen.length; j += 1) {
      remainder[i + j] ^= gfMul(gen[j], factor);
    }
  }
  return remainder.slice(data.length);
}

/* ------------------------------------------------------------------ */
/* Bit stream                                                          */
/* ------------------------------------------------------------------ */

class BitBuffer {
  private bits: number[] = [];

  push(value: number, length: number): void {
    for (let i = length - 1; i >= 0; i -= 1) {
      this.bits.push((value >>> i) & 1);
    }
  }

  get length(): number {
    return this.bits.length;
  }

  toBytes(): Uint8Array {
    const out = new Uint8Array(Math.ceil(this.bits.length / 8));
    this.bits.forEach((bit, index) => {
      if (bit) out[index >>> 3] |= 0x80 >>> index % 8;
    });
    return out;
  }
}

/* ------------------------------------------------------------------ */
/* Matrix layout                                                       */
/* ------------------------------------------------------------------ */

export interface QrMatrix {
  size: number;
  version: number;
  /** `modules[row][col]` — `true` is a dark module. */
  modules: boolean[][];
}

function placeFinder(matrix: boolean[][], reserved: boolean[][], row: number, col: number): void {
  for (let r = -1; r <= 7; r += 1) {
    for (let c = -1; c <= 7; c += 1) {
      const rr = row + r;
      const cc = col + c;
      if (rr < 0 || cc < 0 || rr >= matrix.length || cc >= matrix.length) continue;
      const inRing = (r >= 0 && r <= 6 && (c === 0 || c === 6)) || (c >= 0 && c <= 6 && (r === 0 || r === 6));
      const inCore = r >= 2 && r <= 4 && c >= 2 && c <= 4;
      matrix[rr][cc] = inRing || inCore;
      reserved[rr][cc] = true;
    }
  }
}

function placeAlignment(matrix: boolean[][], reserved: boolean[][], row: number, col: number): void {
  for (let r = -2; r <= 2; r += 1) {
    for (let c = -2; c <= 2; c += 1) {
      const rr = row + r;
      const cc = col + c;
      if (rr < 0 || cc < 0 || rr >= matrix.length || cc >= matrix.length) continue;
      const isDark = Math.max(Math.abs(r), Math.abs(c)) !== 1;
      matrix[rr][cc] = isDark;
      reserved[rr][cc] = true;
    }
  }
}

/** Zig-zag placement order: right column pairs upward, then left, bottom-up. */
function dataModuleOrder(size: number, reserved: boolean[][]): Array<[number, number]> {
  const order: Array<[number, number]> = [];
  let upward = true;
  for (let right = size - 1; right > 0; right -= 2) {
    if (right === 6) right = 5; // the vertical timing pattern is not a data column
    for (let step = 0; step < size; step += 1) {
      const row = upward ? size - 1 - step : step;
      for (let colOffset = 0; colOffset < 2; colOffset += 1) {
        const col = right - colOffset;
        if (!reserved[row][col]) order.push([row, col]);
      }
    }
    upward = !upward;
  }
  return order;
}

export function encodeQr(text: string): QrMatrix {
  const utf8 = new TextEncoder().encode(text);
  if (utf8.length === 0) throw new QrEncodeError("nothing to encode");

  // Smallest version that fits at error-correction level L, which is what the
  // invite dialog wants: a screenshot of a code on a screen is read at a fixed
  // distance, and M would buy a lower density no reader here needs.
  let version = 0;
  let spec: BlockSpec | null = null;
  for (let candidate = QR_MIN_VERSION; candidate <= 20; candidate += 1) {
    const candidateSpec = VERSIONS[candidate];
    const capacityBits = candidateSpec.group1Blocks * candidateSpec.group1DataCodewords * 8 +
      candidateSpec.group2Blocks * candidateSpec.group2DataCodewords * 8;
    const needed = 4 + (utf8.length <= 255 ? 8 : 16) + utf8.length * 8;
    if (needed <= capacityBits) {
      version = candidate;
      spec = candidateSpec;
      break;
    }
  }
  if (!spec || version === 0) {
    throw new QrEncodeError(`too much data for a QR code: ${utf8.length} bytes exceeds what this encoder renders`);
  }

  const dataCapacityBytes = spec.group1Blocks * spec.group1DataCodewords + spec.group2Blocks * spec.group2DataCodewords;

  // Byte mode only. The character-count field is 8 bits below version 10 and 16
  // bits at/above it — a real constraint, and getting it wrong yields a code that
  // scans as garbage rather than failing visibly.
  const buffer = new BitBuffer();
  buffer.push(0b0100, 4);
  buffer.push(utf8.length, version < 10 ? 8 : 16);
  for (const byte of utf8) buffer.push(byte, 8);

  const capacityBits = dataCapacityBytes * 8;
  // Terminator: up to four zero bits, but never past capacity.
  buffer.push(0, Math.min(4, capacityBits - buffer.length));
  // Pad to a byte boundary, then alternate the two spec pad codewords.
  while (buffer.length % 8 !== 0) buffer.push(0, 1);
  const padBytes = [0xec, 0x11];
  let padIndex = 0;
  while (buffer.length < capacityBits) {
    buffer.push(padBytes[padIndex % 2], 8);
    padIndex += 1;
  }

  const dataBytes = buffer.toBytes();

  // Split into blocks, compute EC per block, then interleave both.
  const dataBlocks: Uint8Array[] = [];
  const ecBlocks: Uint8Array[] = [];
  let offset = 0;
  const eccPerBlock = spec.eccCodewordsPerBlock;
  for (let i = 0; i < spec.group1Blocks; i += 1) {
    const block = dataBytes.slice(offset, offset + spec.group1DataCodewords);
    offset += spec.group1DataCodewords;
    dataBlocks.push(block);
    ecBlocks.push(ecCodewords(block, eccPerBlock));
  }
  for (let i = 0; i < spec.group2Blocks; i += 1) {
    const block = dataBytes.slice(offset, offset + spec.group2DataCodewords);
    offset += spec.group2DataCodewords;
    dataBlocks.push(block);
    ecBlocks.push(ecCodewords(block, eccPerBlock));
  }

  const interleaved: number[] = [];
  const maxData = Math.max(spec.group1DataCodewords, spec.group2DataCodewords || 0);
  for (let i = 0; i < maxData; i += 1) {
    for (const block of dataBlocks) if (i < block.length) interleaved.push(block[i]);
  }
  for (let i = 0; i < eccPerBlock; i += 1) {
    for (const block of ecBlocks) interleaved.push(block[i]);
  }

  const size = version * 4 + 17;
  const matrix: boolean[][] = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const reserved: boolean[][] = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));

  placeFinder(matrix, reserved, 0, 0);
  placeFinder(matrix, reserved, 0, size - 7);
  placeFinder(matrix, reserved, size - 7, 0);

  // Timing patterns.
  for (let i = 8; i < size - 8; i += 1) {
    if (!reserved[6][i]) {
      matrix[6][i] = i % 2 === 0;
      reserved[6][i] = true;
    }
    if (!reserved[i][6]) {
      matrix[i][6] = i % 2 === 0;
      reserved[i][6] = true;
    }
  }

  // Alignment patterns, skipping the three finder corners.
  const centers = ALIGNMENT_CENTERS[version] ?? [];
  for (const row of centers) {
    for (const col of centers) {
      const nearFinder =
        (row === 6 && col === 6) ||
        (row === 6 && col === size - 7) ||
        (row === size - 7 && col === 6);
      if (!nearFinder) placeAlignment(matrix, reserved, row, col);
    }
  }

  // Dark module + reserved format areas, filled after data placement.
  matrix[size - 8][8] = true;
  reserved[size - 8][8] = true;
  for (let i = 0; i < 9; i += 1) {
    if (!reserved[8][i]) reserved[8][i] = true;
    if (!reserved[i][8]) reserved[i][8] = true;
  }
  for (let i = 0; i < 8; i += 1) {
    reserved[8][size - 1 - i] = true;
    reserved[size - 1 - i][8] = true;
  }

  // Place the interleaved codewords.
  const order = dataModuleOrder(size, reserved);
  const totalDataBits = interleaved.length * 8;
  if (order.length < totalDataBits) {
    throw new QrEncodeError(`layout produced ${order.length} modules for ${totalDataBits} data bits`);
  }
  for (let i = 0; i < totalDataBits; i += 1) {
    const bit = (interleaved[i >>> 3] >>> (7 - i % 8)) & 1;
    const [row, col] = order[i];
    matrix[row][col] = bit === 1;
  }
  // Remainder bits stay light, which the spec requires; `matrix` starts light,
  // so there is nothing to do and stating it here would be a comment about a
  // no-op. The one that matters is below.

  // Format information: EC level L (0b01) masked with mask 0. The mask is fixed
  // rather than chosen by penalty scoring because this encoder targets a fixed
  // payload shape where the score does not change scannability, and one mask is
  // one less thing to get subtly wrong.
  const formatBits = formatInformation(0b01, 0);
  for (let i = 0; i < 15; i += 1) {
    const bit = ((formatBits >>> i) & 1) === 1;
    // Copy 1, around the top-left finder.
    if (i < 6) matrix[8][i] = bit;
    else if (i === 6) matrix[8][i + 1] = bit;
    else if (i === 7) matrix[8][size - 8 + (i - 7)] = bit;
    else if (i === 8) matrix[7][8] = bit;
    else matrix[14 - i][8] = bit;
    // Copy 2, split between the other two finders.
    if (i < 8) matrix[size - 1 - i][8] = bit;
    else matrix[8][size - 15 + i] = bit;
  }
  // Version information for version >= 7.
  if (version >= 7) {
    const versionBits = versionInformation(version);
    for (let i = 0; i < 18; i += 1) {
      const bit = ((versionBits >>> i) & 1) === 1;
      const row = Math.floor(i / 3);
      const col = (i % 3) + size - 11;
      matrix[row][col] = bit;
      matrix[col][row] = bit;
    }
  }

  return { size, version, modules: matrix };
}

const FORMAT_GENERATOR = 0b10100110111;

function formatInformation(eccBits: number, mask: number): number {
  let value = (eccBits << 3) | mask;
  let remainder = value << 10;
  for (let i = 14; i >= 10; i -= 1) {
    if ((remainder >>> i) & 1) remainder ^= FORMAT_GENERATOR << (i - 10);
  }
  return ((value << 10) | remainder) ^ 0b101010000010010;
}

const VERSION_GENERATOR = 0b1111100100101;

function versionInformation(version: number): number {
  let remainder = version << 12;
  for (let i = 17; i >= 12; i -= 1) {
    if ((remainder >>> i) & 1) remainder ^= VERSION_GENERATOR << (i - 12);
  }
  return (version << 12) | remainder;
}

/**
 * Render a matrix as an SVG path string.
 *
 * One `<path>` rather than one `<rect>` per module: a version-10 code is 57x57 =
 * 3,249 modules, and the per-module form produces a DOM large enough to be
 * noticeable on a phone camera scan. Runs of adjacent dark modules are merged
 * horizontally, which cuts the segment count by roughly an order of magnitude.
 */
export function qrToSvgPath(matrix: QrMatrix): string {
  const parts: string[] = [];
  for (let row = 0; row < matrix.size; row += 1) {
    let runStart = -1;
    for (let col = 0; col <= matrix.size; col += 1) {
      const dark = col < matrix.size && matrix.modules[row][col];
      if (dark && runStart === -1) runStart = col;
      if (!dark && runStart !== -1) {
        parts.push(`M${runStart} ${row}h${col - runStart}v1h-${col - runStart}z`);
        runStart = -1;
      }
    }
  }
  return parts.join("");
}

/** Total module count including the quiet zone — what a viewBox must cover. */
export function qrViewBoxSize(matrix: QrMatrix): number {
  return matrix.size + QUIET_ZONE * 2;
}