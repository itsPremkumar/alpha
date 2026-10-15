import test from "node:test";
import assert from "node:assert/strict";
import { moduleUrl } from "./test-modules.mjs";

const reasons = await import(moduleUrl("server-reasons"));

/**
 * Two Gateway answers are *configuration facts*, not errors, and the UI must
 * say so in the operator's own terms.
 *
 * Live pins (Gateway on 127.0.0.1:8001, `agents_api.enabled=false`, no company
 * bootstrapped for the legacy engine):
 *
 *   GET /api/agents              -> 403 {"detail":"Custom-agent management API
 *                                    is disabled. Set agents_api.enabled=true
 *                                    to expose agent and user-profile routes
 *                                    over HTTP."}
 *   GET /api/company/status      -> 404 {"detail":"No active organizations
 *                                    found. Bootstrap a company first."}
 *   GET /api/company/kanban/tasks-> 404 {"detail":"No active organizations
 *                                    found."}
 *   GET /api/company/kpis        -> 404 {"detail":"No active organizations
 *                                    found."}
 *
 * `errMsg` turns a 403/404 into a generic sentence, so the real reason — the
 * config key to set, the bootstrap step to run — never reached the screen.
 */

/** The message shape `ApiClientError` builds and `req()` copies into `ApiError`. */
function httpError(status, detail) {
  const err = new Error(`Request failed (HTTP ${status}). ${detail}`);
  err.status = status;
  return err;
}

const AGENTS_403 = httpError(
  403,
  "Custom-agent management API is disabled. Set agents_api.enabled=true to expose agent and user-profile routes over HTTP.",
);
const NO_COMPANY_404 = httpError(404, "No active organizations found. Bootstrap a company first.");
const NO_COMPANY_404_BARE = httpError(404, "No active organizations found.");

/* ── 1. The server's own words survive ─────────────────────────────────── */

test("a status-carrying failure keeps the Gateway's detail verbatim", () => {
  assert.equal(
    reasons.serverReason(AGENTS_403),
    "Custom-agent management API is disabled. Set agents_api.enabled=true to expose agent and user-profile routes over HTTP.",
  );
  assert.equal(
    reasons.serverReason(NO_COMPANY_404),
    "No active organizations found. Bootstrap a company first.",
  );
});

test("serverReason never reverts to the generic 403/404 sentence when a detail exists", () => {
  assert.notEqual(reasons.serverReason(AGENTS_403), "Not allowed — this action needs admin rights or a feature flag.");
  assert.notEqual(reasons.serverReason(NO_COMPANY_404), "Not found — it may have been deleted.");
});

test("serverReason falls back to the shared helper when there is nothing to keep", () => {
  assert.equal(reasons.serverReason(new Error("Network unreachable")), "Network unreachable");
  assert.equal(reasons.serverReason("boom"), "Something went wrong.");
  assert.equal(reasons.serverReason(null), "Something went wrong.");
});

test("an error carrying a status but no detail still falls back", () => {
  const bare = new Error("");
  bare.status = 403;
  assert.equal(reasons.serverReason(bare), "");
});

/* ── 2. The disabled Agents API is classified, not guessed ─────────────── */

test("the agents 403 is recognised as disabled-by-configuration", () => {
  assert.equal(reasons.isAgentsApiDisabled(AGENTS_403), true);
});

test("a 403 with a different reason is NOT called disabled-by-configuration", () => {
  // A real permission refusal must not be dressed up as a config toggle.
  assert.equal(reasons.isAgentsApiDisabled(httpError(403, "Admin privileges are required.")), false);
});

test("a 404 on /agents is not an agents-API-disabled classification", () => {
  assert.equal(reasons.isAgentsApiDisabled(httpError(404, "Not Found")), false);
});

test("classification tolerates the bare detail without the status prefix", () => {
  const bare = new Error("Custom-agent management API is disabled. Set agents_api.enabled=true to expose agent and user-profile routes over HTTP.");
  bare.status = 403;
  assert.equal(reasons.isAgentsApiDisabled(bare), true);
});

test("a non-Error value is never classified", () => {
  assert.equal(reasons.isAgentsApiDisabled(null), false);
  assert.equal(reasons.isAgentsApiDisabled(undefined), false);
  assert.equal(reasons.isAgentsApiDisabled("Custom-agent management API is disabled."), false);
});

/* ── 3. "No company configured" is classified, not guessed ─────────────── */

test("the company 404 is recognised as no-company-configured, with or without the bootstrap sentence", () => {
  assert.equal(reasons.isNoCompanyConfigured(NO_COMPANY_404), true);
  assert.equal(reasons.isNoCompanyConfigured(NO_COMPANY_404_BARE), true);
});

test("a 404 for a genuinely missing route is NOT no-company-configured", () => {
  // FastAPI's own route miss answers a bare "Not Found"; that is a missing
  // endpoint, not a missing company, and must not be relabelled.
  assert.equal(reasons.isNoCompanyConfigured(httpError(404, "Not Found")), false);
});

test("a 404 naming a specific missing id is NOT no-company-configured", () => {
  assert.equal(
    reasons.isNoCompanyConfigured(httpError(404, "Company 'co-123' was not found.")),
    false,
  );
});

test("a 500 is never no-company-configured", () => {
  assert.equal(
    reasons.isNoCompanyConfigured(httpError(500, "No active organizations found.")),
    false,
  );
});

test("a non-Error value is never classified as no-company", () => {
  assert.equal(reasons.isNoCompanyConfigured(null), false);
  assert.equal(reasons.isNoCompanyConfigured("No active organizations found."), false);
});
