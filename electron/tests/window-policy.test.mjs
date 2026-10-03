import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ALLOWED_PERMISSIONS,
  DEFAULT_WINDOW,
  buildContentSecurityPolicy,
  clampWindowBounds,
  evaluateNavigation,
  evaluatePermission,
  evaluateWindowOpen,
  isTrustedFrontendUrl,
  pickRestoreDisplay,
} from '../lib/window-policy.js';

const TRUSTED = 'http://127.0.0.1:3000';

test('only the exact local frontend origin is trusted', () => {
  assert.equal(isTrustedFrontendUrl('http://127.0.0.1:3000/workspace', TRUSTED), true);
  assert.equal(isTrustedFrontendUrl('http://127.0.0.1:3000', TRUSTED), true);
  // A different port is a different service: the app falls back to a free port
  // precisely because 3000 can be taken by something else.
  assert.equal(isTrustedFrontendUrl('http://127.0.0.1:3001', TRUSTED), false);
  assert.equal(isTrustedFrontendUrl('http://127.0.0.1:8201', TRUSTED), false);
  // A different host, even on the trusted port.
  assert.equal(isTrustedFrontendUrl('http://evil.example.com:3000', TRUSTED), false);
  assert.equal(isTrustedFrontendUrl('http://localhost:3000', TRUSTED), false);
  // A different scheme (a TLS page is not the loopback app).
  assert.equal(isTrustedFrontendUrl('https://127.0.0.1:3000', TRUSTED), false);
  // Unparseable input.
  assert.equal(isTrustedFrontendUrl('not a url', TRUSTED), false);
  assert.equal(isTrustedFrontendUrl(null, TRUSTED), false);
  assert.equal(isTrustedFrontendUrl('http://127.0.0.1:3000', null), false);
});

test('default ports are compared by value, not by string', () => {
  // http://host and http://host:80 are the same endpoint but compare as '' and
  // '80' if the port is taken literally.
  assert.equal(isTrustedFrontendUrl('http://example.test', 'http://example.test:80'), true);
  assert.equal(isTrustedFrontendUrl('https://example.test', 'https://example.test:443'), true);
  assert.equal(isTrustedFrontendUrl('http://example.test:8080', 'http://example.test:80'), false);
});

test('same-origin navigation is allowed and everything else is not', () => {
  const allowed = evaluateNavigation('http://127.0.0.1:3000/settings', { trustedFrontendUrl: TRUSTED });
  assert.equal(allowed.allowed, true);
  assert.equal(allowed.externalUrl, null);
});

test('an external link is refused in-window and handed to the browser', () => {
  const result = evaluateNavigation('https://example.com/docs', { trustedFrontendUrl: TRUSTED });
  assert.equal(result.allowed, false, 'an external page must never load in the app window');
  // Normalized by URL, so the path survives the round trip to the browser.
  assert.equal(result.externalUrl, 'https://example.com/docs');
  assert.match(result.reason, /external link/);
});

test('dangerous schemes are refused outright, not redirected to a browser', () => {
  // The important distinction: these get `externalUrl: null` because handing
  // them to shell.openExternal would be its own vulnerability.
  for (const url of [
    'file:///C:/Windows/System32/config/SAM',
    'data:text/html,<script>alert(1)</script>',
    'blob:http://127.0.0.1:3000/abc',
    'javascript:alert(document.cookie)',
    'vbscript:msgbox(1)',
    'about:blank',
  ]) {
    const result = evaluateNavigation(url, { trustedFrontendUrl: TRUSTED });
    assert.equal(result.allowed, false, `${url} was allowed`);
    assert.equal(result.externalUrl, null, `${url} was handed to the browser`);
  }
});

test('an empty or unparseable navigation target is refused', () => {
  assert.equal(evaluateNavigation(null, { trustedFrontendUrl: TRUSTED }).allowed, false);
  assert.equal(evaluateNavigation('', { trustedFrontendUrl: TRUSTED }).allowed, false);
  assert.equal(evaluateNavigation('::::', { trustedFrontendUrl: TRUSTED }).allowed, false);
});

test('external redirection can be turned off for a strict surface', () => {
  const result = evaluateNavigation('https://example.com', {
    trustedFrontendUrl: TRUSTED,
    allowExternal: false,
  });
  assert.equal(result.allowed, false);
  assert.equal(result.externalUrl, null);
});

test('window.open is always denied, even for the trusted origin', () => {
  // A same-origin popup would be a second window with the privileged preload
  // bridge attached that the app does not manage.
  const sameOrigin = evaluateWindowOpen('http://127.0.0.1:3000/popup', { trustedFrontendUrl: TRUSTED });
  assert.equal(sameOrigin.action, 'deny');
  assert.match(sameOrigin.reason, /declares its own windows/);

  const untargeted = evaluateWindowOpen('about:blank', { trustedFrontendUrl: TRUSTED });
  assert.equal(untargeted.action, 'deny');
  assert.match(untargeted.reason, /untargeted/);

  const empty = evaluateWindowOpen('', { trustedFrontendUrl: TRUSTED });
  assert.equal(empty.action, 'deny');
});

test('an external window.open target is denied but still given to the browser', () => {
  const result = evaluateWindowOpen('https://example.com', { trustedFrontendUrl: TRUSTED });
  assert.equal(result.action, 'deny');
  assert.equal(result.externalUrl, 'https://example.com/');
});

test('a dangerous window.open scheme is denied with no browser hand-off', () => {
  const result = evaluateWindowOpen('file:///C:/Windows/System32/drivers/etc/hosts', {
    trustedFrontendUrl: TRUSTED,
  });
  assert.equal(result.action, 'deny');
  assert.equal(result.externalUrl, null);
});

test('saved bounds are restored when they still fit a real display', () => {
  const workArea = { x: 0, y: 0, width: 1920, height: 1040 };
  const bounds = clampWindowBounds({ x: 100, y: 80, width: 1280, height: 800 }, workArea);
  assert.deepEqual(bounds, { x: 100, y: 80, width: 1280, height: 800 });
});

test('a window saved onto a display that is gone is pulled onto a real display', () => {
  // The real failure: a laptop undocks and the saved bounds point at a monitor
  // that no longer exists. Restoring them verbatim yields an invisible but
  // "open" window, so the position is clamped and the size the user chose is
  // kept.
  const workArea = { x: 0, y: 0, width: 1920, height: 1040 };
  const restored = clampWindowBounds({ x: 4000, y: 2000, width: 1280, height: 800 }, workArea);
  assert.notEqual(restored, null);
  assert.equal(restored.width, 1280, 'the user-chosen width was thrown away');
  assert.equal(restored.height, 800, 'the user-chosen height was thrown away');
  assert.ok(restored.x >= 0 && restored.x + restored.width <= 1920, 'still off-screen horizontally');
  assert.ok(restored.y >= 0 && restored.y + restored.height <= 1040, 'still off-screen vertically');
});

test('a partly off-screen window is pulled back so its title bar stays grabbable', () => {
  const workArea = { x: 0, y: 0, width: 1920, height: 1040 };
  const bounds = clampWindowBounds({ x: 1800, y: 1000, width: 1280, height: 800 }, workArea);
  assert.notEqual(bounds, null, 'a mostly-visible window was thrown away entirely');
  assert.ok(bounds.x + bounds.width <= workArea.width, 'still hangs off the right edge');
  assert.ok(bounds.y + bounds.height <= workArea.height, 'still hangs off the bottom');
  assert.ok(bounds.x >= workArea.x);
  assert.ok(bounds.y >= workArea.y);
});

test('restored bounds respect the app minimums and the work-area maximum', () => {
  const workArea = { x: 0, y: 0, width: 1920, height: 1040 };
  // Below the minimum: grown to it.
  const tiny = clampWindowBounds({ x: 0, y: 0, width: 200, height: 100 }, workArea);
  assert.equal(tiny.width, DEFAULT_WINDOW.minWidth);
  assert.equal(tiny.height, DEFAULT_WINDOW.minHeight);
  // Above the work area: shrunk to it.
  const huge = clampWindowBounds({ x: 0, y: 0, width: 5000, height: 4000 }, workArea);
  assert.equal(huge.width, 1920);
  assert.equal(huge.height, 1040);
});

test('garbage saved bounds are refused rather than propagated', () => {
  const workArea = { x: 0, y: 0, width: 1920, height: 1040 };
  for (const saved of [
    null,
    undefined,
    {},
    { x: 0, y: 0, width: 'wide', height: 800 },
    { x: 0, y: 0, width: 800, height: Number.NaN },
    { x: 0, y: 0, width: -100, height: 800 },
    'not an object',
  ]) {
    assert.equal(
      clampWindowBounds(saved, workArea),
      null,
      `${JSON.stringify(saved)} was accepted as window bounds`,
    );
  }
  // A missing work area (no display reported) is also a refusal, not a crash.
  assert.equal(clampWindowBounds({ x: 0, y: 0, width: 800, height: 600 }, null), null);
  assert.equal(clampWindowBounds({ x: 0, y: 0, width: 800, height: 600 }, {}), null);
});

test('a negative work-area origin (secondary monitor left of primary) is respected', () => {
  const workArea = { x: -1920, y: 0, width: 1920, height: 1040 };
  const bounds = clampWindowBounds({ x: -1800, y: 50, width: 1280, height: 800 }, workArea);
  assert.deepEqual(bounds, { x: -1800, y: 50, width: 1280, height: 800 });
});

test('the restore display is the one holding the saved window', () => {
  const displays = [
    { bounds: { x: 0, y: 0, width: 1920, height: 1080 }, workArea: { x: 0, y: 0, width: 1920, height: 1040 } },
    { bounds: { x: 1920, y: 0, width: 1920, height: 1080 }, workArea: { x: 1920, y: 0, width: 1920, height: 1040 } },
  ];
  const onSecond = pickRestoreDisplay({ x: 2400, y: 100, width: 1280, height: 800 }, displays, 0);
  assert.equal(onSecond.displayIndex, 1);
  const onFirst = pickRestoreDisplay({ x: 200, y: 100, width: 1280, height: 800 }, displays, 0);
  assert.equal(onFirst.displayIndex, 0);
});

test('a saved window overlapping several displays picks the best fit', () => {
  const displays = [
    { bounds: { x: 0, y: 0, width: 1920, height: 1080 }, workArea: { x: 0, y: 0, width: 1920, height: 1040 } },
    { bounds: { x: 1920, y: 0, width: 1920, height: 1080 }, workArea: { x: 1920, y: 0, width: 1920, height: 1040 } },
  ];
  // Top-left corner on the first display but the body mostly on the second:
  // the corner wins, because that is where the user's title bar was.
  const cornerOnFirst = pickRestoreDisplay({ x: 1700, y: 100, width: 1280, height: 800 }, displays, 0);
  assert.equal(cornerOnFirst.displayIndex, 0);

  // A window straddling the seam, corner in a genuine gap between two
  // non-adjacent displays: no display contains the corner, so best-overlap has
  // to take over. Primary is 0 and the right-hand display is 1, so picking 1
  // proves the overlap rule ran rather than the fallback.
  const gapped = [
    { bounds: { x: 0, y: 0, width: 1920, height: 1080 }, workArea: { x: 0, y: 0, width: 1920, height: 1040 } },
    { bounds: { x: 2560, y: 0, width: 1920, height: 1080 }, workArea: { x: 2560, y: 0, width: 1920, height: 1040 } },
  ];
  const straddling = pickRestoreDisplay({ x: 2400, y: 100, width: 1280, height: 800 }, gapped, 0);
  assert.equal(straddling.displayIndex, 1, 'best-overlap did not run for a corner in a gap');
});

test('an unusable saved position falls back to the primary display', () => {
  const displays = [
    { bounds: { x: 0, y: 0, width: 1920, height: 1080 }, workArea: { x: 0, y: 0, width: 1920, height: 1040 } },
    { bounds: { x: 1920, y: 0, width: 1920, height: 1080 }, workArea: { x: 1920, y: 0, width: 1920, height: 1040 } },
  ];
  assert.equal(pickRestoreDisplay(null, displays, 1).displayIndex, 1);
  assert.equal(pickRestoreDisplay({ x: Number.NaN, y: 0, width: 800, height: 600 }, displays, 1).displayIndex, 1);
  // No displays at all must not throw; the caller then uses default bounds.
  assert.deepEqual(pickRestoreDisplay({ x: 0, y: 0, width: 800, height: 600 }, [], 0), {
    displayIndex: 0,
    workArea: null,
  });
});

test('a wildly out-of-range primary index is clamped, not trusted', () => {
  const displays = [
    { bounds: { x: 0, y: 0, width: 1920, height: 1080 }, workArea: { x: 0, y: 0, width: 1920, height: 1040 } },
  ];
  assert.equal(pickRestoreDisplay(null, displays, 99).displayIndex, 0);
  assert.equal(pickRestoreDisplay(null, displays, -5).displayIndex, 0);
});

test('only allowlisted non-media permissions are granted', () => {
  for (const permission of ALLOWED_PERMISSIONS) {
    const result = evaluatePermission(permission, {
      trustedFrontendUrl: TRUSTED,
      requestingOrigin: TRUSTED,
    });
    assert.equal(result.granted, true, `${permission} is allowlisted but was denied`);
  }
  // The old handler granted every unrecognized permission. Each of these is
  // something a compromised or injected page in the app window would want.
  for (const permission of [
    'geolocation',
    'midi',
    'midiSysex',
    'openExternal',
    'background-sync',
    'display-capture',
    'hid',
    'serial',
    'usb',
    'idle-detection',
    'mediaKeySystem',
    'storage-access',
  ]) {
    const result = evaluatePermission(permission, {
      trustedFrontendUrl: TRUSTED,
      requestingOrigin: TRUSTED,
    });
    assert.equal(result.granted, false, `${permission} was granted; it is not allowlisted`);
  }
});

test('an allowlisted permission is still denied for a foreign origin', () => {
  // notifications for phishing and clipboard-read for exfiltration are the two
  // allowlisted permissions most worth stealing, so origin is enforced too.
  const result = evaluatePermission('clipboard-read', {
    trustedFrontendUrl: TRUSTED,
    requestingOrigin: 'https://evil.example.com',
  });
  assert.equal(result.granted, false);
  assert.match(result.reason, /non-Alpha origin/);
});

test('the allowlist is a real set, not an open-ended list', () => {
  // Guards against someone adding '*' or a prefix match later.
  assert.equal(ALLOWED_PERMISSIONS.has('*'), false);
  assert.equal(ALLOWED_PERMISSIONS.has(''), false);
  assert.ok(ALLOWED_PERMISSIONS.size < 20, 'the allowlist has grown suspiciously large');
});

test('the CSP locks the page down to its own origin', () => {
  const csp = buildContentSecurityPolicy({ gatewayOrigin: 'http://127.0.0.1:8201' });
  assert.match(csp, /default-src 'self'/);
  assert.match(csp, /object-src 'none'/);
  assert.match(csp, /frame-ancestors 'none'/);
  assert.match(csp, /base-uri 'self'/);
  assert.match(csp, /form-action 'self'/);
  // The Gateway must be reachable or every API call breaks.
  assert.match(csp, /connect-src [^;]*http:\/\/127\.0\.0\.1:8201/);
  // The microphone stream is a blob: URL.
  assert.match(csp, /media-src 'self' blob:/);
  // No wildcard anywhere.
  assert.equal(/\*/.test(csp), false, 'the CSP contains a wildcard source');
});

test('the dev CSP allows the HMR socket and eval, and only then', () => {
  const dev = buildContentSecurityPolicy({ gatewayOrigin: 'http://127.0.0.1:8201', devServer: true });
  assert.match(dev, /connect-src [^;]*ws:/);
  assert.match(dev, /script-src [^;]*'unsafe-eval'/);
  // Everything else stays locked.
  assert.match(dev, /object-src 'none'/);
  assert.match(dev, /frame-ancestors 'none'/);
  const prod = buildContentSecurityPolicy({ gatewayOrigin: 'http://127.0.0.1:8201' });
  assert.equal(/unsafe-eval/.test(prod), false, 'production must not allow eval');
});
