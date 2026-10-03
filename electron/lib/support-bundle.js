'use strict';

/**
 * End-to-end diagnostics bundle.
 *
 * A support dialog that names a symptom ("startup failed") is not a diagnosis.
 * This module turns the facts the desktop already records — service states,
 * ports, install contents, and redacted log tails — into one JSON bundle plus
 * a short human summary with named problems. The collector in `main.js` and
 * `scripts/collect-diagnostics.mjs` both build through here, so the installed
 * app and a support script cannot disagree about what "healthy" means.
 *
 * Everything is injected as plain data: this module never touches the disk,
 * the network, or Electron, so the failure analysis itself is unit-testable.
 */

const { redactSupportValue } = require('./app-logger');

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

/** Keep only the last lines of a log; support needs the ending, not the archive. */
function tailLines(lines, maxLines = 200) {
  const list = asArray(lines).filter((line) => typeof line === 'string');
  return list.slice(Math.max(0, list.length - maxLines));
}

function normalizeService(service) {
  const record = asObject(service);
  return {
    key: typeof record.key === 'string' ? record.key : 'unknown',
    label: typeof record.label === 'string' ? record.label : 'Unknown',
    state: typeof record.state === 'string' ? record.state : 'unknown',
    port: Number.isFinite(record.port) ? record.port : null,
    pid: Number.isFinite(record.pid) ? record.pid : null,
    error: typeof record.error === 'string' && record.error ? record.error : null,
    restarts: Number.isFinite(record.restarts) ? record.restarts : 0,
  };
}

/**
 * Build the redacted bundle. All free text is redacted on the way in, because
 * a bundle that leaves the machine must not be the path secrets leak through.
 */
function buildSupportBundle(input = {}) {
  const logs = asObject(input.logs);
  const bundle = {
    generatedAt: typeof input.generatedAt === 'string' ? input.generatedAt : new Date().toISOString(),
    app: {
      name: asObject(input.app).name || 'Alpha',
      version: asObject(input.app).version || null,
      packaged: Boolean(asObject(input.app).packaged),
    },
    runtime: asObject(input.runtime),
    install: asObject(input.install),
    health: redactSupportValue(asObject(input.health)),
    services: asArray(input.services).map(normalizeService),
    logs: {
      main: tailLines(redactSupportValue(asArray(logs.main))),
      events: tailLines(redactSupportValue(asArray(logs.events))),
      gateway: tailLines(redactSupportValue(asArray(logs.gateway))),
      frontend: tailLines(redactSupportValue(asArray(logs.frontend))),
    },
    notes: asArray(input.notes).filter((note) => typeof note === 'string').slice(-50),
  };
  return Object.freeze(bundle);
}

function serviceLine(service) {
  const where = service.port ? `port ${service.port}` : 'no port';
  const restarts = service.restarts > 0 ? `, ${service.restarts} restart(s)` : '';
  const error = service.error ? ` — ${service.error}` : '';
  return `${service.label} ${service.state} (${where}${restarts})${error}`;
}

/**
 * Summarize the bundle for a human and name every actionable problem.
 *
 * The rule is the same one the UI follows: an unknown state is reported as
 * unknown, never as healthy and never as broken.
 */
function summarizeSupportBundle(bundleInput = {}) {
  const bundle = buildSupportBundle(bundleInput);
  const lines = [
    `${bundle.app.name} ${bundle.app.version || 'unknown version'} diagnostics (${bundle.generatedAt})`,
  ];
  const problems = [];

  for (const service of bundle.services) {
    lines.push(serviceLine(service));
    if (service.state === 'failed' || service.error) {
      problems.push(`${service.label} failed: ${service.error || 'no reason recorded'}.`);
    } else if (service.state !== 'healthy') {
      lines.push(`${service.label} is ${service.state}; not yet confirmed healthy.`);
    }
  }

  const runtimes = asObject(bundle.install.runtimes);
  if (runtimes.uv === false) {
    problems.push('The bundled uv runtime is missing from the install; reinstall Alpha.');
  }
  if (runtimes.node === false) {
    problems.push('The bundled Node.js runtime is missing from the install; reinstall Alpha.');
  }

  const haystacks = [
    ['main.log', bundle.logs.main],
    ['gateway.log', bundle.logs.gateway],
    ['frontend.log', bundle.logs.frontend],
  ];
  for (const [file, tail] of haystacks) {
    const failure = tail.find((line) => /startup failed|did not become healthy|traceback|unhandled|uncaught|error/i.test(line));
    if (failure) {
      problems.push(`${file} records: ${failure.slice(0, 220)}`);
    }
  }
  if (bundle.services.length === 0) {
    problems.push('No service states were captured; the app may not have finished starting.');
  }

  for (const note of bundle.notes) lines.push(`note: ${note}`);
  return { lines, problems: [...new Set(problems)] };
}

module.exports = {
  buildSupportBundle,
  summarizeSupportBundle,
};
