'use strict';

/**
 * Production desktop logger: one detailed event stream in two usable forms.
 *
 * The previous shell wrote terse console-style lines only to `main.log`. That
 * was enough when startup succeeded, but a failed boot needed the facts around
 * the failure: exact arguments, resolved paths, process IDs, port decisions,
 * probe attempts, IPC calls, permission decisions, and shutdown ordering. This
 * module records each as a structured event with process/session metadata.
 *
 * Two files are written for the same event because humans and support tooling
 * read logs differently:
 *   - `main.log` is chronological, human-readable prose.
 *   - `desktop-events.jsonl` is one JSON object per line for filtering.
 *
 * Secrets are redacted recursively before either file is touched. The logger
 * never throws: a full disk or locked log file degrades to one console warning
 * rather than taking the desktop app down with it.
 */

const LOG_LEVELS = Object.freeze({
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
});

const DEFAULT_MAX_BYTES = 5 * 1024 * 1024;
const DEFAULT_BACKUPS = 3;
const REDACTED = '[redacted]';
const MAX_SCALAR_LENGTH = 4000;

const SECRET_KEY_PATTERN = /(^|[^a-z])(api[_-]?key|apikey|token|secret|passwd|password|pwd|auth|authorization|bearer|cookie|credentials?|private[_-]?key|client[_-]?secret|access[_-]?key)$/i;
const SECRET_VALUE_PATTERNS = [
  /sk-[A-Za-z0-9_-]{8,}/,
  /\bghp_[A-Za-z0-9]{8,}/,
  /\bgho_[A-Za-z0-9]{8,}/,
  /\bxox[bap]-[A-Za-z0-9-]{8,}/,
  /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----/,
  /Bearer\s+[A-Za-z0-9\-._~+/=]{8,}/i,
  /Basic\s+[A-Za-z0-9+/=]{8,}/i,
];
const SECRET_QUERY_KEYS = new Set([
  'api_key',
  'apikey',
  'access_token',
  'auth',
  'authorization',
  'client_secret',
  'code',
  'password',
  'secret',
  'token',
]);

function normalizeLevel(level) {
  return Object.prototype.hasOwnProperty.call(LOG_LEVELS, level) ? level : 'info';
}

function validateName(value, label) {
  if (typeof value !== 'string' || !/^[a-z0-9]+(?:[._-][a-z0-9]+)*$/i.test(value)) {
    throw new TypeError(`Desktop log component and event names must use letters, numbers, dots, dashes, or underscores (${label}: ${JSON.stringify(value)})`);
  }
  return value;
}

function truncateScalar(value) {
  if (typeof value !== 'string' || value.length <= MAX_SCALAR_LENGTH) return value;
  return `${value.slice(0, MAX_SCALAR_LENGTH)}…[truncated ${value.length - MAX_SCALAR_LENGTH} characters]`;
}

function redactUrlText(value) {
  let parsed = null;
  try {
    parsed = new URL(value);
  } catch {
    return value;
  }

  let changed = false;
  if (parsed.username || parsed.password) {
    parsed.username = REDACTED;
    parsed.password = '';
    changed = true;
  }
  for (const key of [...parsed.searchParams.keys()]) {
    if (SECRET_QUERY_KEYS.has(key.toLowerCase())) {
      parsed.searchParams.set(key, REDACTED);
      changed = true;
    }
  }
  // Sanitize URL credentials before checking the remaining text for secret
  // patterns. Otherwise a URL containing an API key becomes entirely
  // “[redacted]”, losing the host and path needed to diagnose which endpoint
  // was involved.
  if (!changed) return value;
  // URL serialization percent-encodes the placeholder; restore its readable
  // form without decoding any other part of the address.
  const sanitized = parsed
    .toString()
    .replaceAll('%5Bredacted%5D', '[redacted]')
    .replaceAll('%5bredacted%5d', '[redacted]');
  return containsSecretValue(sanitized) ? REDACTED : sanitized;
}

function containsSecretValue(value) {
  return SECRET_VALUE_PATTERNS.some((pattern) => {
    pattern.lastIndex = 0;
    return pattern.test(value);
  });
}

function redactValue(key, value, seen) {
  if (typeof key === 'string' && SECRET_KEY_PATTERN.test(key)) return REDACTED;
  if (typeof value === 'string') {
    const url = redactUrlText(value);
    if (url !== value) return truncateScalar(url);
    if (containsSecretValue(value)) return REDACTED;
    return truncateScalar(value);
  }
  if (typeof value === 'number' || typeof value === 'boolean' || value === null || value === undefined) {
    return value;
  }
  if (value instanceof Date) return value.toISOString();
  if (value instanceof Error) {
    return {
      name: value.name,
      message: truncateScalar(value.message),
      stack: truncateScalar(value.stack || ''),
    };
  }
  if (Array.isArray(value)) {
    if (seen.has(value)) return '[circular]';
    seen.add(value);
    try {
      return value.map((item) => redactValue('', item, seen));
    } finally {
      seen.delete(value);
    }
  }
  if (typeof value === 'object') {
    if (seen.has(value)) return '[circular]';
    seen.add(value);
    try {
      const result = {};
      for (const [childKey, childValue] of Object.entries(value)) {
        result[childKey] = redactValue(childKey, childValue, seen);
      }
      return result;
    } finally {
      seen.delete(value);
    }
  }
  return truncateScalar(String(value));
}

function redactContext(context) {
  if (context === undefined) return undefined;
  return redactValue('', context || {}, new Set());
}

function formatHumanLine(entry) {
  const context = entry.context === undefined ? '' : ` | ${JSON.stringify(entry.context)}`;
  return `[${entry.timestamp}] [${entry.level.toUpperCase()}] [pid ${entry.pid}] ${entry.component}.${entry.event}: ${entry.message}${context}`;
}

function createDesktopLogger(options = {}) {
  const fileSystem = options.fs || require('node:fs');
  const pathModule = options.path || require('node:path');
  const output = options.console || console;
  const now = typeof options.now === 'function' ? options.now : Date.now;
  const pid = Number.isFinite(options.pid) ? options.pid : process.pid;
  const sessionId = typeof options.sessionId === 'string' && options.sessionId ? options.sessionId : `session-${now()}-${pid}`;
  const appName = typeof options.appName === 'string' && options.appName ? options.appName : 'Alpha';
  const appVersion = typeof options.appVersion === 'string' && options.appVersion ? options.appVersion : 'unknown';
  const logDir = options.logDir || pathModule.join(process.cwd(), 'logs');
  const textFile = pathModule.join(logDir, 'main.log');
  const eventsFile = pathModule.join(logDir, 'desktop-events.jsonl');
  const maxBytes = Number.isFinite(options.maxBytes) && options.maxBytes > 0 ? Math.floor(options.maxBytes) : DEFAULT_MAX_BYTES;
  const backups = Number.isFinite(options.backups) && options.backups >= 1 ? Math.floor(options.backups) : DEFAULT_BACKUPS;
  let level = normalizeLevel(options.level);
  let sequence = 0;
  let fileFailureReported = false;

  function reportFileFailure(error) {
    if (fileFailureReported) return;
    fileFailureReported = true;
    try {
      const write = typeof output.error === 'function' ? output.error : output.log;
      write.call(output, `Desktop logging failed; continuing without file logs: ${error && error.message ? error.message : error}`);
    } catch {
      // Console output must not become a second failure mode.
    }
  }

  function rotateIfNeeded(file, incomingBytes) {
    let size = 0;
    try {
      size = fileSystem.statSync(file).size;
    } catch (error) {
      if (error && error.code !== 'ENOENT') throw error;
      return;
    }
    if (size + incomingBytes <= maxBytes) return;
    for (let backup = backups; backup >= 1; backup -= 1) {
      const backupFile = `${file}.${backup}`;
      if (backup === backups) {
        try {
          fileSystem.rmSync(backupFile, { force: true });
        } catch {
          // Continue with rotation; stale history is not fatal.
        }
      } else {
        try {
          fileSystem.renameSync(backupFile, `${file}.${backup + 1}`);
        } catch (error) {
          if (!error || error.code !== 'ENOENT') throw error;
        }
      }
    }
    fileSystem.renameSync(file, `${file}.1`);
  }

  function append(file, text) {
    rotateIfNeeded(file, Buffer.byteLength(text, 'utf8'));
    fileSystem.appendFileSync(file, text, 'utf8');
  }

  function write(levelName, component, event, message, context) {
    validateName(component, 'component name');
    validateName(event, 'event name');
    if (LOG_LEVELS[levelName] < LOG_LEVELS[level]) return null;
    sequence += 1;
    const entry = {
      sequence,
      timestamp: new Date(now()).toISOString(),
      level: levelName,
      pid,
      sessionId,
      app: appName,
      appVersion,
      component,
      event,
      message: String(message),
    };
    const redacted = redactContext(context);
    if (redacted !== undefined) entry.context = redacted;

    try {
      const human = `${formatHumanLine(entry)}\n`;
      const machine = `${JSON.stringify(entry)}\n`;
      append(textFile, human);
      append(eventsFile, machine);
      try {
        const writeConsole = levelName === 'error' && typeof output.error === 'function' ? output.error : output.log;
        writeConsole.call(output, human.trimEnd());
      } catch {
        // Console output must not become a second failure mode.
      }
    } catch (error) {
      reportFileFailure(error);
      return null;
    }
    return entry;
  }

  function time(component, event, context = {}) {
    const startedAt = now();
    return (message = 'completed', extraContext = {}) => write('info', component, event, message, {
      ...context,
      ...extraContext,
      durationMs: now() - startedAt,
    });
  }

  function service(name) {
    validateName(name, 'service name');
    const scoped = (levelName, event, message, context = {}) => write(levelName, name, event, message, {
      service: name,
      ...context,
    });
    return {
      debug: (event, message, context) => scoped('debug', event, message, context),
      info: (event, message, context) => scoped('info', event, message, context),
      warn: (event, message, context) => scoped('warn', event, message, context),
      error: (event, message, context) => scoped('error', event, message, context),
      time: (event, context) => {
        const done = time(name, event, { service: name, ...context });
        return (message, extraContext) => done(message, extraContext);
      },
    };
  }

  return {
    get level() {
      return level;
    },
    get sessionId() {
      return sessionId;
    },
    get paths() {
      return { logDir, textFile, eventsFile };
    },
    setLevel(nextLevel) {
      level = normalizeLevel(nextLevel);
      return level;
    },
    debug: (component, event, message, context) => write('debug', component, event, message, context),
    info: (component, event, message, context) => write('info', component, event, message, context),
    warn: (component, event, message, context) => write('warn', component, event, message, context),
    error: (component, event, message, context) => write('error', component, event, message, context),
    time,
    service,
  };
}

/**
 * Redact an arbitrary support value with the same rules as logged events.
 *
 * Exported so the diagnostics bundle cannot invent a second, weaker redactor.
 */
function redactSupportValue(value) {
  return redactValue('', value, new Set());
}

module.exports = {
  DEFAULT_BACKUPS,
  DEFAULT_MAX_BYTES,
  LOG_LEVELS,
  createDesktopLogger,
  redactSupportValue,
};
