/**
 * Placement geometry for the portalled "More Views" panel.
 *
 * The defect this exists for: the panel used to be an `absolute` child of its
 * trigger, which made it a hostage to every clipping ancestor in the shell.
 * Measured in the running app, `ChatView` wraps `NavTabs` in `overflow-x-auto`
 * (whose `overflow-y` *computes* to `auto`, so it clips vertically too) inside
 * a `<main class="overflow-hidden">`. The menu measured 256x1060 against a
 * 30px-tall clip box: the click toggled state, the DOM node existed, and
 * 4 pixels of the panel were on screen. It also had no `max-height` and no
 * internal scroll, so its last group (`System & Architecture`) was below the
 * fold of every viewport, not just this one.
 *
 * So the panel is portalled to `document.body` (see `NavTabs.tsx`) and
 * positioned here, where the geometry is a pure function of a trigger rect, a
 * viewport and a measured content height — which means it is unit-testable
 * instead of only observable by clicking around a dev server.
 */

export interface PanelTriggerRect {
  /** Viewport-space top edge of the trigger. */
  top: number;
  /** Viewport-space bottom edge of the trigger. */
  bottom: number;
  /** Viewport-space left edge of the trigger. */
  left: number;
}

export interface PanelGeometryInput {
  trigger: PanelTriggerRect;
  viewport: { width: number; height: number };
  /** `scrollHeight` of the unconstrained panel content. */
  naturalHeight: number;
  panelWidth?: number;
}

export interface PanelGeometry {
  /** Viewport-space top edge for a `position: fixed` panel. */
  top: number;
  /** Viewport-space left edge for a `position: fixed` panel. */
  left: number;
  /** Height cap; the panel scrolls internally when the content exceeds it. */
  maxHeight: number;
  /** True when the menu opened upward because there was no room below. */
  opensUp: boolean;
}

/** `w-64`. The panel is pinned to this width so the cap and the box agree. */
export const PANEL_WIDTH = 256;
/** Gap between the trigger and the panel — the old `mt-1.5`. */
export const TRIGGER_GAP = 6;
/** Keep the panel off the viewport edges on every side. */
export const VIEWPORT_MARGIN = 8;
/**
 * Floor for a usable menu. Only ever reached when the window is tiny enough
 * that no side has real room, in which case the panel scrolls.
 */
export const MIN_PANEL_HEIGHT = 200;
/**
 * Below this much room on the chosen side the menu flips upward. The trigger
 * lives in the workspace header, so "upward" is the only alternative that
 * avoids burying the panel under the section content.
 */
export const FLIP_BELOW_THRESHOLD = 240;

function clamp(value: number, min: number, max: number): number {
  // A viewport narrower than the panel collapses the bounds; `min` wins so the
  // panel stays anchored to the edge instead of hanging off-screen.
  if (max < min) return min;
  return Math.min(Math.max(value, min), max);
}

/**
 * Where a portalled panel must sit for all of its content to stay reachable.
 *
 * Two guarantees, both of which the in-place version broke:
 *
 * 1. The returned box fits inside the viewport (`VIEWPORT_MARGIN` on every
 *    side), so no ancestor — or the viewport itself — can hide an entry.
 * 2. `maxHeight` is the height the panel is actually given, and the panel is
 *    `overflow-y: auto`, so content that does not fit scrolls instead of
 *    disappearing. A cap that the browser then ignores (because it was set on
 *    a clipped ancestor) is the bug, not the cap.
 */
export function placeFloatingPanel(input: PanelGeometryInput): PanelGeometry {
  const { trigger, viewport, naturalHeight } = input;
  const panelWidth = input.panelWidth ?? PANEL_WIDTH;

  const roomBelow = viewport.height - trigger.bottom - TRIGGER_GAP - VIEWPORT_MARGIN;
  const roomAbove = trigger.top - TRIGGER_GAP - VIEWPORT_MARGIN;
  const opensUp = roomBelow < FLIP_BELOW_THRESHOLD && roomAbove > roomBelow;

  const room = Math.max(MIN_PANEL_HEIGHT, opensUp ? roomAbove : roomBelow);
  const maxHeight = Math.max(0, Math.floor(Math.min(naturalHeight, room)));

  const preferredTop = opensUp
    ? trigger.top - TRIGGER_GAP - maxHeight
    : trigger.bottom + TRIGGER_GAP;
  const lowestTop = viewport.height - VIEWPORT_MARGIN - maxHeight;
  const top = Math.round(
    clamp(preferredTop, VIEWPORT_MARGIN, Math.max(VIEWPORT_MARGIN, lowestTop)),
  );

  const widestLeft = viewport.width - VIEWPORT_MARGIN - panelWidth;
  const left = Math.round(
    clamp(trigger.left, VIEWPORT_MARGIN, Math.max(VIEWPORT_MARGIN, widestLeft)),
  );

  return { top, left, maxHeight, opensUp };
}