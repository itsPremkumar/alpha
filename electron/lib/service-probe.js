'use strict';

/**
 * Identity probes: "is the thing on this port actually our service?"
 *
 * ## Why identity and not liveness
 *
 * The desktop app reuses a port when "a healthy Alpha service already serves it"
 * and otherwise takes the next free one. Liveness alone is unsafe: an unrelated
 * process on 8201 answers, the app attaches, and then reports a `gatewayUrl` it
 * never health-checked while the real Gateway is never started. The user sees a
 * window whose every API call fails, with no error anywhere, because the app
 * believes its backend is fine.
 *
 * So the probe checks that the service *identifies itself*:
 *   - Gateway: `/health` returns `service === 'alpha-gateway'`.
 *   - Frontend: the HTML carries the Next.js marker or the app name.
 *
 * `fetchJson` and `fetchText` are injected so these are unit-testable without a
 * server, and both are bounded by a timeout so a port that accepts a connection
 * and then stalls cannot wedge the boot.
 */

const DEFAULT_TIMEOUT_MS = 3000;

/**
 * Fetch JSON with a deadline.
 *
 * @returns {Promise<object|null>} null on any failure, including a non-2xx
 *   status and an unparseable body. "I could not confirm what is there" and
 *   "there is something" are different, and only the second may be reused.
 */
async function fetchJson(url, { fetchImpl = fetch, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  if (timer.unref) timer.unref();
  try {
    const response = await fetchImpl(url, { signal: controller.signal });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** Fetch text with a deadline. Same failure semantics as `fetchJson`. */
async function fetchText(url, { fetchImpl = fetch, timeoutMs = 5000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  if (timer.unref) timer.unref();
  try {
    const response = await fetchImpl(url, { signal: controller.signal });
    if (!response.ok) return null;
    return await response.text();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Is this our Gateway?
 *
 * @param {string} baseUrl e.g. `http://127.0.0.1:8201`
 * @param {object} [deps] injectable `fetch`
 */
async function isAlphaGateway(baseUrl, deps = {}) {
  const data = await fetchJson(`${baseUrl}/health`, deps);
  // Service identity as reported by app/gateway/app.py health_check.
  return Boolean(data && data.service === 'alpha-gateway');
}

/**
 * Is this our frontend?
 *
 * `__next` is the reliable Next.js marker. The display name is a secondary
 * signal only, because after the rename to Alpha it is the same string that
 * appears in unrelated pages, so a page that merely says "Alpha" must not be
 * mistaken for this app.
 */
async function isAlphaFrontend(baseUrl, deps = {}) {
  const text = await fetchText(baseUrl, deps);
  if (typeof text !== 'string') return false;
  return text.includes('__next') || text.includes(deps.displayName || 'Alpha');
}

module.exports = {
  DEFAULT_TIMEOUT_MS,
  fetchJson,
  fetchText,
  isAlphaFrontend,
  isAlphaGateway,
};