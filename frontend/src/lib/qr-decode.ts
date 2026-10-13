import jsQR from "jsqr";
import { encodeQr } from "./qr-encode";

export class QrDecodeError extends Error {}

export type QrDecodeStage = "geometry" | "payload" | "ok";

export interface QrDecodeReport {
  text: string | null;
  stage: QrDecodeStage;
  dimension: number | null;
  moduleSize: number | null;
  reason: string | null;
}

const MAX_IMAGE_PIXELS = 16_000_000;

/** The bundled local decoder is available for both image uploads and camera frames. */
export function canDecodeQr(): boolean {
  return true;
}

/** Decode one QR code from RGBA pixels. Frames stay entirely in the browser. */
export function decodeQrFromImageData(image: ImageData): string | null {
  if (image.width < 21 || image.height < 21) {
    throw new QrDecodeError("that image is too small to contain a QR code");
  }
  if (image.width * image.height > MAX_IMAGE_PIXELS) {
    throw new QrDecodeError("that image is too large to scan safely");
  }
  if (image.data.length !== image.width * image.height * 4) {
    throw new QrDecodeError("the image pixel data is incomplete");
  }
  return jsQR(image.data, image.width, image.height, { inversionAttempts: "attemptBoth" })?.data ?? null;
}

/** Return a useful distinction between a missing code and a rejected image. */
export function decodeQrFromImageDataReport(image: ImageData): QrDecodeReport {
  try {
    const text = decodeQrFromImageData(image);
    return {
      text,
      stage: text === null ? "geometry" : "ok",
      dimension: null,
      moduleSize: null,
      reason: text === null ? "no readable QR code was found" : null,
    };
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

/** Decode an uploaded image using the same pixel path as the live camera. */
export async function decodeQrFromImageFile(file: File): Promise<string | null> {
  if (file.size > 20 * 1024 * 1024) {
    throw new QrDecodeError("that image file is too large to scan safely");
  }
  if (typeof createImageBitmap !== "function") {
    throw new QrDecodeError("this browser cannot decode an image file; use the camera scanner instead");
  }
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new QrDecodeError("the selected file is not a readable image");
  }
  try {
    return decodeQrFromImageData(bitmapToImageData(bitmap));
  } finally {
    bitmap.close();
  }
}

function bitmapToImageData(bitmap: ImageBitmap): ImageData {
  if (bitmap.width * bitmap.height > MAX_IMAGE_PIXELS) {
    throw new QrDecodeError("that image is too large to scan safely");
  }
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new QrDecodeError("this browser could not provide a 2D canvas to read the image");
  context.drawImage(bitmap, 0, 0);
  return context.getImageData(0, 0, bitmap.width, bitmap.height);
}

/** Render a matrix as image pixels for round-trip tests and local previews. */
export function matrixToImageData(matrix: { size: number; modules: boolean[][] }, scale = 4, quiet = 4): ImageData {
  if (!Number.isInteger(scale) || scale < 1 || !Number.isInteger(quiet) || quiet < 4) {
    throw new QrDecodeError("QR rendering needs a positive integer scale and a quiet zone of at least four modules");
  }
  const width = (matrix.size + quiet * 2) * scale;
  const data = new Uint8ClampedArray(width * width * 4).fill(255);
  for (let row = 0; row < matrix.size; row += 1) {
    for (let col = 0; col < matrix.size; col += 1) {
      if (!matrix.modules[row][col]) continue;
      for (let dy = 0; dy < scale; dy += 1) {
        for (let dx = 0; dx < scale; dx += 1) {
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
