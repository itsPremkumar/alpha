/** Scan at 5 Hz; the camera preview can continue at the display refresh rate. */
export const QR_SCAN_INTERVAL_MS = 200;

/** Whether a camera animation frame is due for QR decoding. */
export function isQrScanFrameDue(nowMs: number, lastScanAtMs: number | null): boolean {
  if (!Number.isFinite(nowMs)) return false;
  if (lastScanAtMs === null) return true;
  if (!Number.isFinite(lastScanAtMs)) return false;
  return nowMs - lastScanAtMs >= QR_SCAN_INTERVAL_MS;
}
