'use strict';

function originOf(value) {
  if (!value || typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    return url.origin;
  } catch {
    return null;
  }
}

/**
 * Alpha's desktop renderer is a local trusted origin, but media permissions
 * must still be explicit. Only microphone-only requests are granted; camera
 * and mixed audio/video requests fail closed rather than silently receiving a
 * broader grant than the UI requested.
 */
function shouldGrantDesktopMediaPermission({
  permission,
  requestingOrigin,
  trustedFrontendUrl,
  details,
} = {}) {
  const trustedOrigin = originOf(trustedFrontendUrl);
  const requestOrigin = originOf(requestingOrigin);
  if (!trustedOrigin || requestOrigin !== trustedOrigin) return false;
  if (permission === 'audioCapture' || permission === 'microphone') return true;
  if (permission === 'camera' || permission === 'videoCapture') return false;
  if (permission !== 'media') return false;

  // Permission checks may report a singular mediaType while requests use the
  // mediaTypes array. Unknown/empty media types fail closed so an old or
  // malformed request cannot accidentally receive camera access.
  const mediaTypes = Array.isArray(details?.mediaTypes)
    ? details.mediaTypes
    : typeof details?.mediaType === 'string'
      ? [details.mediaType]
      : null;
  if (!mediaTypes || mediaTypes.length === 0) return false;
  return mediaTypes.includes('audio') && !mediaTypes.includes('video');
}

function resolveStartUrl(frontendUrl, env = process.env) {
  if (!frontendUrl || !/^https?:\/\//i.test(frontendUrl)) return frontendUrl;
  try {
    return new URL(env.AGENT_WORKSPACE_START_PATH || '/', frontendUrl).toString();
  } catch {
    return frontendUrl;
  }
}

function rewriteGatewayDestinations(manifest, gatewayBaseUrl) {
  const groups = manifest && manifest.rewrites;
  const lists = Array.isArray(groups) ? [groups] : groups ? [groups.beforeFiles, groups.afterFiles, groups.fallback] : [];
  const loopback = /^https?:\/\/(?:127\.0\.0\.1|localhost):\d+(?=\/|$)/;
  let matched = 0;
  let patched = 0;
  for (const list of lists) {
    if (!Array.isArray(list)) continue;
    for (const rule of list) {
      if (!rule || typeof rule.source !== 'string' || !/^\/api(?:\/|$)/.test(rule.source)) continue;
      if (typeof rule.destination !== 'string' || !loopback.test(rule.destination)) continue;
      matched += 1;
      const updated = rule.destination.replace(loopback, () => gatewayBaseUrl.replace(/\/+$/, ''));
      if (updated !== rule.destination) {
        rule.destination = updated;
        patched += 1;
      }
    }
  }
  return { manifest, patched, allMatch: matched > 0 && patched === 0 };
}

module.exports = {
  resolveStartUrl,
  rewriteGatewayDestinations,
  shouldGrantDesktopMediaPermission,
};
