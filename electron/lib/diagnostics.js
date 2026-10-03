'use strict';

/**
 * Preflight checks that run before anything is spawned.
 *
 * ## Why they exist as a pure decision function
 *
 * `runStartupDiagnostics()` used to `throw` from the middle of a function whose
 * only other job was logging — and it ran *before* `fileLoggingReady = true`, so
 * the one error it was most likely to produce (the disk-space failure) had no
 * log file to point the user at. The catch handler said "see main.log for
 * details" about a file that did not exist.
 *
 * The fix has two halves, and this module is the first:
 *
 * 1. Checks return findings; they never throw. The caller decides what is fatal.
 * 2. Every finding is *actionable* — it names the condition, the value, and
 *    what to do. "Preflight failed" is not an action.
 */

const MIN_USER_DATA_FREE_BYTES = 1 * 1024 * 1024 * 1024; // 1 GiB
const MIN_WINDOWS_BUILD = 19041; // Windows 10 2004, the documented floor

/** Severity levels, ordered. */
const SEVERITY = Object.freeze({ fatal: 'fatal', warning: 'warning', info: 'info' });

const CHECK_ORDER = Object.freeze([
  'platform',
  'architecture',
  'os-version',
  'free-space',
  'runtime-tools',
]);

/**
 * @typedef {object} Finding
 * @property {string} id
 * @property {'fatal'|'warning'|'info'} severity
 * @property {string} message   what is wrong
 * @property {string} remedy    what the user can do about it
 * @property {string} [detail]  the measured value
 */

/**
 * Run every preflight check and return the findings.
 *
 * @param {object} env injected environment, so this is testable
 * @param {object} [deps]
 * @param {() => number|null} [deps.freeBytes] free space at the userData root
 * @param {Array<{key: string, label: string, present: boolean, error?: string}>} [deps.tools]
 * @param {string} [deps.appName]
 * @param {string} [deps.userData]
 * @returns {{findings: Finding[], fatal: Finding[], ok: boolean}}
 */
function runPreflight(env = {}, deps = {}) {
  const {
    freeBytes = () => null,
    tools = [],
    appName = 'Alpha',
    userData = null,
  } = deps;
  const findings = [];

  // --- platform ---------------------------------------------------------
  if (env.platform && env.platform !== 'win32') {
    findings.push({
      id: 'platform',
      severity: env.platform === 'darwin' || env.platform === 'linux' ? SEVERITY.warning : SEVERITY.fatal,
      message: `The desktop app is built and tested for Windows, but is running on ${env.platform}.`,
      remedy:
        'The installer and the auto-updater are Windows-only. The services can still be run ' +
        'directly on macOS or Linux via `make dev`; the native window shell is not supported there.',
    });
  }

  // --- architecture -----------------------------------------------------
  // A 32-bit process cannot load the bundled x64 Node and uv, and the failure
  // mode is a confusing spawn error several minutes into first launch rather
  // than a clear message now.
  if (env.arch && env.arch !== 'x64' && env.arch !== 'arm64') {
    findings.push({
      id: 'architecture',
      severity: SEVERITY.fatal,
      message: `The bundled runtime is x64 or arm64, but this process is ${env.arch}.`,
      remedy:
        'Reinstall the 64-bit build of Alpha. A 32-bit process cannot load the bundled ' +
        'Node.js and uv runtimes, so the Gateway would fail to start later with an opaque error.',
    });
  }

  // --- OS version -------------------------------------------------------
  // Read from the injected env, never from `process.env` directly, so the check
  // is testable and so a packaged app and a source run report identically.
  const build = Number.parseInt(env.osBuild ?? '', 10);
  if (Number.isFinite(build) && build > 0 && build < MIN_WINDOWS_BUILD) {
    findings.push({
      id: 'os-version',
      severity: SEVERITY.fatal,
      message: `This is Windows build ${build}; ${appName} needs build ${MIN_WINDOWS_BUILD} or newer.`,
      remedy:
        'Update Windows (Settings → Windows Update), or install an older build of Alpha that ' +
        'supports your version.',
      detail: `build ${build} < ${MIN_WINDOWS_BUILD}`,
    });
  }

  // --- free space -------------------------------------------------------
  // The backend venv, the provisioned CPython, SQLite databases, logs and
  // artifacts all live under the userData root, so this is the one that matters.
  const bytes = freeBytes();
  if (bytes !== null && Number.isFinite(bytes) && bytes < MIN_USER_DATA_FREE_BYTES) {
    findings.push({
      id: 'free-space',
      severity: SEVERITY.fatal,
      message:
        `Not enough free disk space for ${appName} data: ` +
        `${formatBytes(bytes)} free${userData ? ` at ${userData}` : ''}.`,
      remedy:
        'At least 1 GiB is required for the backend environment, Python runtime, databases, ' +
        'logs and artifacts. Free some space and start the app again.',
      detail: formatBytes(bytes),
    });
  }

  // --- runtime tools ----------------------------------------------------
  // A packaged install must not fall back to a system PATH copy: the whole
  // point of bundling is that a machine with no Node and no uv still works.
  for (const tool of tools) {
    if (tool.present) continue;
    findings.push({
      id: `runtime-${tool.key}`,
      severity: env.isPackaged ? SEVERITY.fatal : SEVERITY.warning,
      message: env.isPackaged
        ? `The bundled ${tool.label} runtime is missing from this installation.`
        : `The ${tool.label} executable was not found on PATH.`,
      remedy: env.isPackaged
        ? `Reinstall ${appName}. The packaged app must not depend on a system PATH copy of ${tool.label}.`
        : `Install ${tool.label} and restart, or run the desktop app from a packaged build which bundles it.`,
    });
  }

  const ordered = orderFindings(findings);
  const fatal = ordered.filter((finding) => finding.severity === SEVERITY.fatal);
  return { findings: ordered, fatal, ok: fatal.length === 0 };
}

/** Sort findings by the documented check order, then by severity. */
function orderFindings(findings) {
  const severityRank = { fatal: 0, warning: 1, info: 2 };
  return [...findings].sort((a, b) => {
    const orderDelta =
      CHECK_ORDER.indexOf(a.id.split('-').slice(0, 2).join('-')) - CHECK_ORDER.indexOf(b.id.split('-').slice(0, 2).join('-'));
    if (Number.isFinite(orderDelta) && orderDelta !== 0) return orderDelta;
    return (severityRank[a.severity] ?? 3) - (severityRank[b.severity] ?? 3);
  });
}

/**
 * The text shown to the user for a fatal preflight failure.
 *
 * Includes every finding, not just the first: a machine that is both
 * out of disk and on too old a Windows should be told both, because fixing one
 * and relaunching would otherwise just produce the other.
 */
function formatPreflightFailure(result, appName = 'Alpha') {
  const lines = [`${appName} cannot start on this computer.`];
  for (const finding of result.fatal) {
    lines.push('', `\u2022 ${finding.message}`, `  ${finding.remedy}`);
  }
  return lines.join('\n');
}

function formatBytes(bytes) {
  const mib = Math.round(bytes / (1024 * 1024));
  if (mib >= 1024) return `${(mib / 1024).toFixed(1)} GiB`;
  return `${mib} MiB`;
}

module.exports = {
  CHECK_ORDER,
  MIN_USER_DATA_FREE_BYTES,
  MIN_WINDOWS_BUILD,
  SEVERITY,
  formatBytes,
  formatPreflightFailure,
  runPreflight,
};
