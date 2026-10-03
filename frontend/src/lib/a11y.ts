"use client";

import { useCallback, useEffect, useRef } from "react";

/**
 * Shared modal accessibility behaviour.
 *
 * ## Why this exists
 *
 * Five overlays in this app declare `role="dialog" aria-modal="true"`:
 * `ui.tsx` `Modal`, `KanbanSection`'s task editor, `FilesSection`'s preview,
 * `chat-shell/NewProjectDialog`, and `bots/BotDetailPanel`. Every one of them
 * shipped the *attributes* without the *behaviour* — there was no focus trap,
 * no Escape handler, and no focus restore. `aria-modal="true"` is a promise to
 * assistive tech that the rest of the page is inert. An overlay that does not
 * keep that promise is worse than one that never claimed it: a screen-reader
 * user trusts the flag, stops navigating the page behind, and then has Tab
 * silently walking them into controls they cannot see.
 *
 * `OmnisearchModal` was the only overlay that did this correctly. Rather than
 * copy its logic a fifth time, it is generalised here into the two hooks below
 * and the modal call sites were reduced to passing a ref.
 *
 * ## What the hooks guarantee
 *
 * - Focus moves **into** the overlay on open (first focusable, else the panel).
 * - Tab and Shift+Tab **cycle** inside the overlay, wrapping at both ends.
 * - Escape closes it.
 * - Focus returns to the element that opened it on close.
 *
 * Background scrolling is locked too, because `Modal` and `BotDetailPanel`
 * are `fixed inset-0` and a scrolling `<body>` behind them produces the
 * "page slides under the drawer" artefact on desktop.
 */

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "details > summary",
  "[tabindex]:not([tabindex='-1'])",
  "[contenteditable='true']",
].join(",");

/**
 * Every element inside `container` that a keyboard user could reach, in DOM
 * order. Elements that are *visually* hidden are filtered out: an offscreen
 * `opacity-0` control would otherwise become a focus stop the user cannot see
 * or hit, which is the same class of bug as hiding a control behind `hover`.
 */
function focusableStops(container: HTMLElement | null): HTMLElement[] {
  if (!container) return [];
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter((el) => {
    if (el.hasAttribute("disabled")) return false;
    if (el.getAttribute("aria-hidden") === "true") return false;
    // `offsetParent` is null for `display:none`, but a fixed-position element
    // reports null too — so fall back to the rect for the `fixed` case.
    const isHidden =
      el.offsetParent === null && getComputedStyle(el).position !== "fixed" && el.getClientRects().length === 0;
    return !isHidden;
  });
}

/**
 * Traps focus inside `ref` while `active`, restores focus on close, and
 * optionally runs `onEscape`.
 *
 * @param ref       the overlay panel (not the backdrop) — the panel is what
 *                  holds the focusable content.
 * @param active    usually `isOpen`; pass `false` to release the trap.
 * @param onEscape  called on Escape. Defaults to nothing so a caller that
 *                  wants Escape to be inert must say so explicitly rather than
 *                  the reverse.
 * @param autoFocus when true, focus moves to the first focusable child on open.
 *                  Default true. Pass false for overlays that manage focus
 *                  themselves (the omnisearch palette focuses its input).
 */
export function useFocusTrap(
  ref: React.RefObject<HTMLElement | null>,
  active: boolean,
  onEscape?: () => void,
  autoFocus = true,
) {
  const escapeRef = useRef(onEscape);
  escapeRef.current = onEscape;

  useEffect(() => {
    if (!active) return;
    const panel = ref.current;
    if (!panel) return;

    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    let rafId = 0;
    if (autoFocus) {
      // Defer by a frame: on the first paint after mount the panel may not have
      // been laid out yet, and `.focus()` on a zero-size box is a no-op.
      rafId = requestAnimationFrame(() => {
        const stops = focusableStops(panel);
        (stops[0] ?? panel).focus();
      });
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (escapeRef.current) {
          e.stopPropagation();
          escapeRef.current();
        }
        return;
      }
      if (e.key !== "Tab") return;

      const stops = focusableStops(panel);
      if (stops.length === 0) {
        // Nothing to move to. Keep focus on the panel so it does not fall
        // through to the page behind.
        e.preventDefault();
        panel.focus();
        return;
      }
      const first = stops[0];
      const last = stops[stops.length - 1];
      const active = document.activeElement;
      if (!e.shiftKey && (active === last || !stops.includes(active as HTMLElement))) {
        e.preventDefault();
        first.focus();
      } else if (e.shiftKey && (active === first || !stops.includes(active as HTMLElement))) {
        e.preventDefault();
        last.focus();
      }
    };

    // Listen on `window`, not the panel: Escape should work even when focus has
    // escaped the overlay (which is exactly the bug this hook exists to fix).
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      window.removeEventListener("keydown", handleKeyDown);
      if (previouslyFocused && document.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
  }, [ref, active, autoFocus]);
}

/**
 * Locks `document.body` scroll while `active`. Without this, a `fixed inset-0`
 * overlay sits on top of a page that still scrolls underneath, which reads as a
 * rendering bug and — on iOS — lets the rubber-band drag move the content the
 * user cannot see.
 */
export function useScrollLock(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const { body } = document;
    const previousOverflow = body.style.overflow;
    const previousPaddingRight = body.style.paddingRight;
    // Compensate for the removed scrollbar so the overlay does not shift the
    // layout sideways by ~15px when it appears.
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
    body.style.overflow = "hidden";
    if (scrollbarWidth > 0) body.style.paddingRight = `${scrollbarWidth}px`;
    return () => {
      body.style.overflow = previousOverflow;
      body.style.paddingRight = previousPaddingRight;
    };
  }, [active]);
}

/**
 * Builds a keyboard handler that activates on Enter, Space, and the arrow keys.
 *
 * ## Why this is not a hook
 *
 * The rows that need this are rendered inside `.map()` callbacks, and a hook
 * called from inside a loop violates the Rules of Hooks — the call count
 * changes whenever the list length changes, which React treats as a bug rather
 * than a lint warning. This helper holds no state and registers nothing, so it
 * is a plain function and is safe to call anywhere, including inside a loop.
 *
 * Prefer {@link useActivationKeys} at the top level of a component when the
 * handler is not built inside a loop.
 *
 * @param onActivate invoked for Enter, Space, ArrowDown, or ArrowUp.
 * @param options.passiveArrow when true, arrow keys are ignored (use this for
 *        controls inside a listbox or menu, where arrows already mean
 *        "move selection").
 */
export function activationKeys(
  onActivate: () => void,
  options?: { passiveArrow?: boolean },
): (e: React.KeyboardEvent) => void {
  return (e: React.KeyboardEvent) => {
    const isSpace = e.key === " " || e.key === "Spacebar";
    const isEnter = e.key === "Enter";
    const isArrow = e.key === "ArrowDown" || e.key === "ArrowUp";
    if (!isSpace && !isEnter && !(isArrow && !options?.passiveArrow)) return;
    // Space scrolls the page by default; Enter would submit an enclosing
    // form. Both must be suppressed or the activation happens twice over.
    e.preventDefault();
    onActivate();
  };
}

/**
 * Returns a keyboard handler that activates on both Enter and Space.
 *
 * `role="button"` promises both keys — Space is the native activation key for
 * `<button>`, and omitting it means a user who guesses correctly scrolls the
 * page instead of toggling the control. Arrow keys are also accepted because
 * these rows are disclosure widgets, where the arrow keys are conventional.
 */
export function useActivationKeys(
  onActivate: () => void,
  options?: { passiveArrow?: boolean },
): (e: React.KeyboardEvent) => void {
  const cb = useRef(onActivate);
  cb.current = onActivate;
  return useCallback((e: React.KeyboardEvent) => activationKeys(() => cb.current(), options)(e), [options?.passiveArrow]);
}