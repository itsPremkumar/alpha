import { create as createQrCode } from "qrcode";

/** Quiet zone in modules. The QR specification requires at least four. */
export const QUIET_ZONE = 4;
export const QR_MIN_VERSION = 1;
export const QR_MAX_VERSION = 20;

export interface QrMatrix {
  size: number;
  version: number;
  /** `modules[row][col]` — `true` is a dark module. */
  modules: boolean[][];
}

export class QrEncodeError extends Error {}

/**
 * Generate a standards-compliant QR matrix using the maintained node-qrcode
 * implementation. Keep the matrix API stable for the SVG renderer and callers.
 */
export function encodeQr(text: string): QrMatrix {
  if (!text) throw new QrEncodeError("nothing to encode");
  try {
    const qr = createQrCode(text, { errorCorrectionLevel: "L" });
    if (qr.version < QR_MIN_VERSION || qr.version > QR_MAX_VERSION) {
      throw new QrEncodeError(`too much data for a QR code: version ${qr.version} exceeds the supported maximum of ${QR_MAX_VERSION}`);
    }
    const size = qr.modules.size;
    const modules = Array.from({ length: size }, (_, row) =>
      Array.from({ length: size }, (_, col) => qr.modules.get(row, col) === 1),
    );
    return { size, version: qr.version, modules };
  } catch (cause) {
    if (cause instanceof QrEncodeError) throw cause;
    if (cause instanceof Error && /too big|too large|capacity/i.test(cause.message)) {
      throw new QrEncodeError(`too much data for a QR code: payload exceeds version ${QR_MAX_VERSION}`);
    }
    throw new QrEncodeError(cause instanceof Error ? cause.message : "could not encode this QR payload");
  }
}

/** Render dark module runs as one compact SVG path. */
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

/** Total module count including the mandatory quiet zone. */
export function qrViewBoxSize(matrix: QrMatrix): number {
  return matrix.size + QUIET_ZONE * 2;
}
