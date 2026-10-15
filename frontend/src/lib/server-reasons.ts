import { errMsg } from "./http";

/**
 * Two Gateway answers are *configuration facts*, not errors.
 *
 * `GET /api/agents` answers 403 with
 * "Custom-agent management API is disabled. Set agents_api.enabled=true to
 * expose agent and user-profile routes over HTTP." when `agents_api.enabled`
 * is false, and every company-owned route answers 404 with
 * "No active organizations found. Bootstrap a company first." when no company
 * has been bootstrapped. Both are the server telling the operator how to turn
 * the feature on.
 *
 * `errMsg` deliberately turns a 401/403/404 into a generic sentence
 * ("Not allowed — this action needs admin rights or a feature flag." /
 * "Not found — it may have been deleted.") — right for an incidental call,
 * wrong here: it replaced the config key and the bootstrap instruction with a
 * diagnosis the server never made. This module keeps those two answers
 * recognisable so a view can render "disabled by configuration" /
 * "no company configured" instead of a generic failure, a spinner, or
 * fabricated empty data.
 *
 * Classification is deliberately conservative: it keys on the status AND the
 * server's own words. A 403 carrying a genuine permission refusal, or a 404
 * for a genuinely missing route or id, is left alone — relabelling those as
 * configuration states would be the same fabrication in a different coat.
 */

/** The detail prefix `ApiClientError` wraps a Gateway `detail` in. */
const HTTP_PREFIX = /^Request failed \(HTTP \d{3}\)\.\s*/;

/**
 * The server's own failure sentence, or the shared fallback.
 *
 * An `Error` carrying a numeric status keeps its message — which already
 * contains the Gateway's `detail` — and everything else falls through to
 * `errMsg`. Same contract as the per-module `failureText` helpers, lifted here
 * for the surfaces that need to branch on *what* the server said.
 */
export function serverReason(err: unknown): string {
  if (err instanceof Error && err.message && typeof (err as { status?: unknown }).status === "number") {
    return err.message.replace(HTTP_PREFIX, "");
  }
  return errMsg(err);
}

/** The Gateway detail carried by a status-carrying error, or `null`. */
function detailOf(err: unknown): string | null {
  if (!(err instanceof Error) || !err.message) return null;
  if (typeof (err as { status?: unknown }).status !== "number") return null;
  return err.message.replace(HTTP_PREFIX, "");
}

/** The HTTP status carried by an error, or `null`. */
function statusOf(err: unknown): number | null {
  const status = (err as { status?: unknown } | null)?.status;
  return typeof status === "number" && Number.isInteger(status) ? status : null;
}

/**
 * True only for the Gateway's own "this API is off" 403.
 *
 * Keyed on the status and the server's own sentence, so an unrelated 403 — a
 * real admin refusal — is never dressed up as a configuration toggle.
 */
export function isAgentsApiDisabled(err: unknown): boolean {
  if (statusOf(err) !== 403) return false;
  return /Custom-agent management API is disabled/i.test(detailOf(err) ?? "");
}

/**
 * True only for the Gateway's own "no company has been bootstrapped" 404.
 *
 * Both spellings count: some company routes append "Bootstrap a company
 * first." and some do not. FastAPI's own route miss answers a bare
 * "Not Found", and a missing-id 404 names the id — neither is this state.
 */
export function isNoCompanyConfigured(err: unknown): boolean {
  if (statusOf(err) !== 404) return false;
  return /^No active organizations found\b/i.test(detailOf(err) ?? "");
}

/** Title and hint a view renders when the Agents API is off by configuration. */
export const AGENTS_API_DISABLED_VIEW = {
  title: "Agents API is disabled by configuration",
  hint: "The Gateway answered that custom-agent management is switched off in config.yaml. Set agents_api.enabled=true and restart the Gateway — this page unlocks then. Nothing here is a failure to read, and there are no agents to show.",
} as const;

/** Title and hint a view renders when no company has been bootstrapped. */
export const NO_COMPANY_CONFIGURED_VIEW = {
  title: "No company configured — this view is unavailable",
  hint: "The Gateway answered that no organization has been bootstrapped, so there is no board, roster or set of figures behind this view. Bootstrap a company first, then reload. Nothing here is an empty result, and no rows are being hidden.",
} as const;
