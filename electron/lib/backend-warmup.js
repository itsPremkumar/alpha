'use strict';

/**
 * First-launch backend warmup progress.
 *
 * `uv run` provisions CPython and installs ~238 packages inside the same
 * process that later serves the Gateway, so the supervisor's health timeout
 * cannot distinguish "still downloading" from "stuck". The honest fix has two
 * halves: provision explicitly with its own generous timeout (in `main.js`),
 * and narrate that phase on the splash screen so a five-minute first launch
 * reads as progress rather than a hang. This module owns the narration half as
 * pure functions, so the splash cadence is unit-testable.
 */

const MAX_SPLASH_LINE = 160;

/** Map one raw output line to a user-facing stage, or null when it says nothing. */
function summarizeWarmupLine(line) {
  if (typeof line !== 'string') return null;
  const text = line.replace(/\s+/g, ' ').trim();
  if (!text) return null;
  if (/^error[:\s]|failed|traceback/i.test(text)) {
    return { stage: 'failed', message: truncate(text) };
  }
  if (/download/i.test(text)) {
    return { stage: 'downloading-runtime', message: 'Downloading Python runtime…' };
  }
  if (/creating virtual environment/i.test(text)) {
    return { stage: 'creating-environment', message: 'Creating backend environment…' };
  }
  // `uv` prints a final "Installed N packages in …" line. That completion is
  // worth showing verbatim; intermediate package chatter stays generic.
  if (/installed \d+ packages? in /i.test(text)) {
    return { stage: 'installed', message: truncate(text) };
  }
  if (/installed \d+ packages?/i.test(text)) {
    return { stage: 'installing-packages', message: 'Installing backend packages…' };
  }
  return { stage: 'working', message: truncate(text) };
}

function truncate(text) {
  if (text.length <= MAX_SPLASH_LINE) return text;
  return `${text.slice(0, MAX_SPLASH_LINE - 1)}…`;
}

/**
 * Report throttled splash updates for a long warmup.
 *
 * Stage changes always go out immediately (a user should see the phase move
 * from downloading to installing); repeats of the same stage are throttled so
 * `uv`'s per-chunk chatter does not spam the splash or the log.
 */
function createWarmupProgress({ onStatus, now = Date.now, minIntervalMs = 5000 } = {}) {
  if (typeof onStatus !== 'function') {
    throw new TypeError('createWarmupProgress needs an onStatus() function');
  }
  let lastStage = null;
  let lastSentAt = 0;
  const startedAt = now();
  return {
    report(rawText) {
      const lines = String(rawText).split(/\r?\n/);
      for (const rawLine of lines) {
        const summary = summarizeWarmupLine(rawLine);
        if (!summary) continue;
        const elapsedMs = now() - startedAt;
        // eslint-disable-next-line no-continue
        if (summary.stage === lastStage && elapsedMs - lastSentAt < minIntervalMs) continue;
        lastStage = summary.stage;
        lastSentAt = elapsedMs;
        onStatus(summary.message, `Backend provisioning is still running (elapsed ${Math.round(elapsedMs / 1000)}s).`);
      }
    },
    finish(message = 'Backend environment ready') {
      onStatus(message, `Backend provisioning finished after ${Math.round((now() - startedAt) / 1000)}s.`);
    },
  };
}

module.exports = {
  MAX_SPLASH_LINE,
  createWarmupProgress,
  summarizeWarmupLine,
};
