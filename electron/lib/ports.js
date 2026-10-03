'use strict';

/**
 * Port search.
 *
 * Extracted from `main.js` so the search is testable and so its one subtlety is
 * documented: a free-port probe is a TOCTOU window. `isPortFree` binds and
 * releases a socket, and the child is spawned afterwards, so another process can
 * take the port in between. The window is narrow, but the honest response is
 * not "this guarantees a free port" — it is a bounded search plus a retry on a
 * bind failure, which `spawnBackend` gets for free because the supervisor
 * restarts a service that fails to come up.
 */

/**
 * Find a free port at or after `preferred`.
 *
 * @param {number} preferred
 * @param {object} deps
 * @param {(port: number) => Promise<boolean>} deps.isPortFree
 * @param {number} [deps.maxOffset] how far past `preferred` to look
 * @returns {Promise<number>}
 * @throws {Error} when the whole range is occupied, naming the range
 *
 * ## Why this is not `async`
 *
 * Argument validation runs SYNCHRONOUSLY, before the returned promise exists, so
 * a typo in `--gateway-port=` throws at the call site instead of surfacing later
 * as an unhandled rejection from inside the boot sequence — where the cause is
 * a stack frame inside a search loop and not the CLI flag the user typed. The
 * search itself is asynchronous, so the function returns a promise either way.
 */
function findFreePort(preferred, { isPortFree, maxOffset = 20 } = {}) {
  if (typeof isPortFree !== 'function') {
    throw new TypeError('findFreePort needs an isPortFree() function');
  }
  const base = Number(preferred);
  if (!Number.isFinite(base) || base <= 0 || base > 65535) {
    throw new RangeError(`Invalid preferred port ${preferred}`);
  }
  return searchFreePort(base, isPortFree, maxOffset);
}

async function searchFreePort(base, isPortFree, maxOffset) {
  for (let offset = 0; offset <= maxOffset; offset += 1) {
    const port = base + offset;
    if (port > 65535) break;
    // Sequential by nature: a probe is async, and probing in parallel would make
    // two searches race each other for the same free port.
    let free = false;
    try {
      // eslint-disable-next-line no-await-in-loop
      free = Boolean(await isPortFree(port));
    } catch {
      // A probe that throws did not establish that the port is free, so it is
      // treated as occupied and the search moves on. Aborting the boot here would
      // be worse: the common causes are EPERM and a transient firewall rule, and
      // the next port is very likely fine.
      free = false;
    }
    if (free) return port;
  }
  throw new Error(`No free port found in range ${base}-${base + maxOffset}`);
}

/** `http://127.0.0.1:<port>` for a port number. */
function loopbackBase(port) {
  return `http://127.0.0.1:${port}`;
}

module.exports = { findFreePort, loopbackBase };