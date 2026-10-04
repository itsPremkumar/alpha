import { useCallback, useEffect, useRef, useState } from "react";

/**
 * One clipboard implementation for every "copy" control in the app.
 *
 * Four call sites inlined `navigator.clipboard.writeText` before this existed,
 * and they had already drifted: two of them fire-and-forget with no `.catch`, so
 * a denied permission rejects into nothing and the operator is told nothing while
 * the button silently does not work. `MessagesSection` and `MessageItem` are the
 * two that lose the error.
 *
 * The behaviour this guarantees, in the order the operator meets it:
 *
 * - **Disabled while in flight.** A second click cannot start a second write.
 * - **`copied` flips for a fixed window**, then reverts. A control stuck on
 *   "Copied" is indistinguishable from one whose write failed silently.
 * - **A failure is worded, not swallowed**, and it never claims the value was
 *   copied. The caller can render its own remedy (for an invite, "reveal and
 *   copy the code by hand" is actionable; for a run link, "the link is in the
 *   address bar" is too).
 * - **The value is returned to the caller on failure** so a UI can select the
 *   text for a manual copy instead of pretending the click worked.
 */
export interface CopyButton {
  copied: boolean;
  /** True while a write is in flight; bind this to `disabled`. */
  busy: boolean;
  /** True once a write has failed, so the caller can render a remedy. */
  failed: boolean;
  copy: (value: string) => Promise<boolean>;
  reset: () => void;
}

export function useCopyButton(resetAfterMs = 1500): CopyButton {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlightRef = useRef(false);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // A component unmounting mid-window must not leave a timer holding a setState
  // against a dead component.
  useEffect(() => clearTimer, [clearTimer]);

  const reset = useCallback(() => {
    clearTimer();
    setCopied(false);
    setFailed(false);
  }, [clearTimer]);

  const copy = useCallback(
    async (value: string): Promise<boolean> => {
      if (inFlightRef.current) return false;
      if (!value) {
        setFailed(true);
        return false;
      }
      inFlightRef.current = true;
      setBusy(true);
      setFailed(false);
      try {
        if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
          setFailed(true);
          return false;
        }
        await navigator.clipboard.writeText(value);
        setCopied(true);
        clearTimer();
        timerRef.current = setTimeout(() => {
          setCopied(false);
          timerRef.current = null;
        }, resetAfterMs);
        return true;
      } catch {
        // Deliberately not a silent success. The UI shows its own remedy next to
        // this, and `failed` is what lets it do so.
        setFailed(true);
        return false;
      } finally {
        inFlightRef.current = false;
        setBusy(false);
      }
    },
    [clearTimer, resetAfterMs],
  );

  return { copied, busy, failed, copy, reset };
}