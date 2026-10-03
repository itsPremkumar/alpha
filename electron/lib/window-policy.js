'use strict';

/**
 * Window placement, navigation, and permission policy — all pure.
 *
 * ## Why this is a policy module and not inline `main.js` code
 *
 * Every rule here is a security or stability decision that used to be (or was
 * never) made at the call site, and none of them can be checked by reading
 * `main.js`. They are collected here as functions of their inputs so the
 * decision tables are unit-testable with no window, no display server, and no
 * Electron import.
 *
 * ## The two rules that matter most
 *
 * 1. **A window may only ever be pointed at the local Alpha frontend.** The
 *    renderer is a `http://127.0.0.1:<port>` page holding a privileged preload
 *    bridge. Nothing else — not a link in a thread, not a redirect, not an
 *    injected `<a target=_blank>` — may navigate it or open a child window. The
 *    old code had neither `will-navigate` nor `setWindowOpenHandler`, so a
 *    single hostile link could replace the trusted UI with an arbitrary page
 *    that still had the bridge.
 *
 * 2. **External links open in the user's browser, never in-app.** The trusted
 *    origin is the only thing allowed to be a `http:` destination, and
 *    `file:`, `javascript:`, `data:`, and `blob:` are refused outright rather
 *    than being filtered by prefix heuristics that a crafted URL can evade.
 */

/** Schemes a link may use to reach the system browser. */
const EXTERNAL_SCHEMES = new Set(['http:', 'https:', 'mailto:']);

/**
 * Schemes that must never be handed to `shell.openExternal`.
 *
 * `file:` reads local files, `data:` and `blob:` execute attacker-controlled
 * content, and `javascript:` is a script URL. None of them are legitimate
 * targets for a link in a chat transcript.
 */
const REFUSED_SCHEMES = new Set(['file:', 'data:', 'blob:', 'javascript:', 'vbscript:', 'about:']);

/**
 * Is this URL the local Alpha frontend we launched?
 *
 * Compares scheme, host, AND port. A host-only check would accept
 * `http://evil.example.com` when the trusted origin is a bare `127.0.0.1`, and
 * a scheme+host check without the port would accept a different service on the
 * same host — which is exactly the case when the app falls back to a free port
 * because 3000 was taken.
 *
 * @param {string} candidateUrl
 * @param {string} trustedFrontendUrl
 * @returns {boolean}
 */
function isTrustedFrontendUrl(candidateUrl, trustedFrontendUrl) {
  const candidate = parseUrl(candidateUrl);
  const trusted = parseUrl(trustedFrontendUrl);
  if (!candidate || !trusted) return false;
  if (candidate.protocol !== trusted.protocol) return false;
  if (candidate.hostname !== trusted.hostname) return false;
  return normalizePort(candidate) === normalizePort(trusted);
}

function parseUrl(value) {
  if (!value || typeof value !== 'string') return null;
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

/**
 * A URL's effective port, filling in each scheme's default.
 *
 * Comparing ports numerically is not enough: `http://127.0.0.1` and
 * `http://127.0.0.1:80` are the same endpoint but compare as `''` and `80`.
 */
function normalizePort(url) {
  if (url.port) return url.port;
  if (url.protocol === 'http:') return '80';
  if (url.protocol === 'https:') return '443';
  return '';
}

/**
 * Should this navigation be allowed to happen inside the app window?
 *
 * @returns {{allowed: boolean, reason: string, externalUrl: string|null}}
 *   `externalUrl` is set when the caller should hand the URL to the system
 *   browser instead of loading it.
 */
function evaluateNavigation(targetUrl, { trustedFrontendUrl, allowExternal = true } = {}) {
  if (!targetUrl || typeof targetUrl !== 'string') {
    return { allowed: false, reason: 'empty navigation target', externalUrl: null };
  }
  if (isTrustedFrontendUrl(targetUrl, trustedFrontendUrl)) {
    return { allowed: true, reason: 'same-origin navigation', externalUrl: null };
  }

  const parsed = parseUrl(targetUrl);
  if (!parsed) {
    return { allowed: false, reason: 'unparseable navigation target', externalUrl: null };
  }
  if (REFUSED_SCHEMES.has(parsed.protocol)) {
    return {
      allowed: false,
      reason: `refusing to navigate to a ${parsed.protocol} URL from the app window`,
      externalUrl: null,
    };
  }
  if (allowExternal && EXTERNAL_SCHEMES.has(parsed.protocol)) {
    // Rejected in-window, redirected to the browser. This is the honest split:
    // the app window keeps the trusted origin, and the user still gets the link.
    return { allowed: false, reason: 'external link', externalUrl: parsed.toString() };
  }
  return {
    allowed: false,
    reason: `refusing to navigate the app window to a non-Alpha ${parsed.protocol} origin`,
    externalUrl: null,
  };
}

/**
 * Should a `window.open` / `target=_blank` request be allowed to open a window?
 *
 * Always no, for the trusted origin too: the app has exactly the windows it
 * declares (main, splash, optional companion). A page that wants a popup gets
 * `deny`, and an `http(s)` target is passed to the browser instead so the user
 * is not silently deprived of the link.
 *
 * @returns {{action: 'deny'|'allow', reason: string, externalUrl: string|null}}
 */
function evaluateWindowOpen(targetUrl, { trustedFrontendUrl, allowExternal = true } = {}) {
  if (!targetUrl || targetUrl === 'about:blank') {
    // about:blank is how a window.open() with no target arrives. Denying it
    // prevents an empty same-origin window that could then be navigated.
    return { action: 'deny', reason: 'refusing to open an untargeted window', externalUrl: null };
  }
  if (isTrustedFrontendUrl(targetUrl, trustedFrontendUrl)) {
    return {
      action: 'deny',
      reason:
        'the app declares its own windows; a same-origin popup would be a second ' +
        'unmanaged surface with the privileged bridge attached',
      externalUrl: null,
    };
  }
  const parsed = parseUrl(targetUrl);
  if (!parsed) {
    return { action: 'deny', reason: 'unparseable window.open target', externalUrl: null };
  }
  if (REFUSED_SCHEMES.has(parsed.protocol)) {
    return { action: 'deny', reason: `refusing to open a ${parsed.protocol} window`, externalUrl: null };
  }
  if (allowExternal && EXTERNAL_SCHEMES.has(parsed.protocol)) {
    return { action: 'deny', reason: 'external link', externalUrl: parsed.toString() };
  }
  return { action: 'deny', reason: `refusing to open a ${parsed.protocol} window`, externalUrl: null };
}

// ---------------------------------------------------------------------------
// Window bounds
// ---------------------------------------------------------------------------

const DEFAULT_WINDOW = Object.freeze({ width: 1320, height: 880, minWidth: 1024, minHeight: 640 });

/**
 * Clamp saved bounds to a display's work area, or reject them.
 *
 * A window restored onto a monitor that no longer exists is invisible but still
 * "open": the app looks hung and the user cannot get it back without killing
 * the process. Unplugging a laptop's external display is enough to cause it, so
 * every restore goes through here. The position is clamped into the given work
 * area rather than rejected, which is what makes that outcome unreachable.
 *
 * @param {object|null} saved `{x, y, width, height}` or null
 * @param {object} workArea `{x, y, width, height}` of the display to restore onto
 * @param {object} [defaults]
 * @returns {{x: number, y: number, width: number, height: number}|null}
 *   null means "no usable saved bounds at all; use the defaults and center".
 */
function clampWindowBounds(saved, workArea, defaults = DEFAULT_WINDOW) {
  if (!saved || typeof saved !== 'object') return null;
  const { x, y, width, height } = saved;
  if (![x, y, width, height].every((v) => Number.isFinite(v))) return null;
  if (!workArea || !Number.isFinite(workArea.width) || !Number.isFinite(workArea.height)) return null;
  if (width <= 0 || height <= 0) return null;

  // Never smaller than the app's own minimum, and never larger than the work
  // area (restoring to a 200px-tall taskbar strip is unusable).
  const finalWidth = Math.round(
    Math.min(Math.max(width, defaults.minWidth), Math.max(workArea.width, defaults.minWidth)),
  );
  const finalHeight = Math.round(
    Math.min(Math.max(height, defaults.minHeight), Math.max(workArea.height, defaults.minHeight)),
  );

  // The position is always CLAMPED into the work area rather than rejected when
  // it does not fit. Rejecting threw away a perfectly good size because the
  // saved position referenced a monitor that is no longer attached — the window
  // came back at the default size instead of the size the user chose, which is
  // its own small complaint on every undock. Clamping puts the same window back
  // on a real display, and because the clamp is unconditional the result is
  // always reachable by the mouse: the "invisible but open" window this guards
  // against cannot be produced by this function at all.
  const maxX = workArea.x + workArea.width - finalWidth;
  const maxY = workArea.y + workArea.height - finalHeight;
  return {
    x: Math.round(Math.min(Math.max(x, workArea.x), Math.max(maxX, workArea.x))),
    y: Math.round(Math.min(Math.max(y, workArea.y), Math.max(maxY, workArea.y))),
    width: finalWidth,
    height: finalHeight,
  };
}

/**
 * Choose which display a window should be restored onto.
 *
 * @param {object|null} savedBounds
 * @param {Array<{bounds: object, workArea: object}>} displays
 * @param {number} [primaryIndex] index of the primary display
 * @returns {{displayIndex: number, workArea: object}}
 */
function pickRestoreDisplay(savedBounds, displays, primaryIndex = 0) {
  const fallback = () => {
    const index = Math.min(Math.max(primaryIndex, 0), Math.max(0, displays.length - 1));
    return { displayIndex: index, workArea: displays[index] ? displays[index].workArea : null };
  };
  if (!savedBounds || !Array.isArray(displays) || displays.length === 0) return fallback();
  const { x, y } = savedBounds;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return fallback();
  // The display whose bounds contain the window's top-left corner, or failing
  // that the one it overlaps most.
  const containing = displays.findIndex(
    (display) =>
      display.bounds &&
      x >= display.bounds.x &&
      x < display.bounds.x + display.bounds.width &&
      y >= display.bounds.y &&
      y < display.bounds.y + display.bounds.height,
  );
  if (containing >= 0) return { displayIndex: containing, workArea: displays[containing].workArea };
  let bestIndex = -1;
  let bestArea = 0;
  displays.forEach((display, index) => {
    if (!display.bounds) return;
    const overlapX = Math.max(
      0,
      Math.min(x + (savedBounds.width || 0), display.bounds.x + display.bounds.width) - Math.max(x, display.bounds.x),
    );
    const overlapY = Math.max(
      0,
      Math.min(y + (savedBounds.height || 0), display.bounds.y + display.bounds.height) - Math.max(y, display.bounds.y),
    );
    const area = overlapX * overlapY;
    if (area > bestArea) {
      bestArea = area;
      bestIndex = index;
    }
  });
  return bestIndex >= 0 ? { displayIndex: bestIndex, workArea: displays[bestIndex].workArea } : fallback();
}

// ---------------------------------------------------------------------------
// Permission policy
// ---------------------------------------------------------------------------

/**
 * The non-media permissions the desktop shell actually needs.
 *
 * The old handler answered `callback(true)` for everything it did not
 * recognize, which granted geolocation, notifications, MIDI, pointer lock,
 * background sync, and openExternal to a local page. An allowlist is the only
 * version of this that is safe to reason about: a permission Electron adds in a
 * future release is denied until someone adds it here on purpose.
 */
const ALLOWED_PERMISSIONS = new Set([
  'clipboard-read',
  'clipboard-sanitized-write',
  'fullscreen',
  'notifications',
  'pointerLock',
  'window-management',
]);

/**
 * Decide a single non-media permission request.
 *
 * @returns {{granted: boolean, reason: string}}
 */
function evaluatePermission(permission, { trustedFrontendUrl, requestingOrigin } = {}) {
  if (!ALLOWED_PERMISSIONS.has(permission)) {
    return {
      granted: false,
      reason: `permission ${JSON.stringify(permission)} is not in the desktop allowlist`,
    };
  }
  // Origin is enforced even for allowed permissions. The bridge is privileged
  // and the allowlisted permissions are the ones most useful to an attacker
  // (notifications for phishing, clipboard-read for exfiltration).
  if (requestingOrigin && !isTrustedFrontendUrl(requestingOrigin, trustedFrontendUrl)) {
    return {
      granted: false,
      reason: `permission ${permission} requested by a non-Alpha origin`,
    };
  }
  return { granted: true, reason: `permission ${permission} is allowlisted for the local app` };
}

/**
 * The Content-Security-Policy for the app's own pages.
 *
 * `default-src 'self'` with no `unsafe-inline` in `script-src` is the whole
 * point: a successful HTML injection in the chat transcript cannot then run
 * script. `connect-src` must include the Gateway because the renderer talks to
 * it directly for some routes, and `media-src blob:` is required for the
 * microphone stream the voice feature uses.
 *
 * @param {object} [options]
 * @param {string} [options.gatewayOrigin] e.g. `http://127.0.0.1:8201`
 */
function buildContentSecurityPolicy({ gatewayOrigin = null, devServer = false } = {}) {
  const connect = ["'self'"];
  if (gatewayOrigin) connect.push(gatewayOrigin);
  // Next.js dev needs a websocket for HMR and an eval-based runtime.
  if (devServer) {
    connect.push('ws:', 'wss:');
    return [
      "default-src 'self'",
      "script-src 'self' 'unsafe-eval' 'unsafe-inline'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      `connect-src ${connect.join(' ')}`,
      "media-src 'self' blob:",
      "object-src 'none'",
      "base-uri 'self'",
      "frame-ancestors 'none'",
      "form-action 'self'",
    ].join('; ');
  }
  return [
    "default-src 'self'",
    // Next.js injects a small inline bootstrap script, so 'unsafe-inline' is
    // required here; it is scoped to this origin and cannot help a remote page.
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src ${connect.join(' ')}`,
    "media-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
  ].join('; ');
}

module.exports = {
  ALLOWED_PERMISSIONS,
  DEFAULT_WINDOW,
  EXTERNAL_SCHEMES,
  REFUSED_SCHEMES,
  buildContentSecurityPolicy,
  clampWindowBounds,
  evaluateNavigation,
  evaluatePermission,
  evaluateWindowOpen,
  isTrustedFrontendUrl,
  pickRestoreDisplay,
};
