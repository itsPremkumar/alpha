'use strict';

function resolveStartUrl(frontendUrl, env = process.env) {
  if (!frontendUrl || !/^https?:\/\//i.test(frontendUrl)) return frontendUrl;
  try {
    return new URL(env.AGENT_WORKSPACE_START_PATH || env.DEERFLOW_START_PATH || '/', frontendUrl).toString();
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

module.exports = { resolveStartUrl, rewriteGatewayDestinations };
