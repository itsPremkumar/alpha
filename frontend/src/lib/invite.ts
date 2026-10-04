// Client mirror of the Alpha connection-string grammar.
//
// This is a *mirror*, not a second authority: the Gateway owns parsing, and this
// module exists only so a paste can be validated and previewed before a round
// trip. That boundary is the whole reason the rules are duplicated rather than
// imported — a component cannot import Python, and calling the server to find out
// a string is malformed would make the paste feel broken instead of explained.
//
// Three rules keep the mirror honest, and each has a test:
//
// 1. Every refusal names the offending field, exactly as the server does. A paste
//    box that says "invalid" tells the operator nothing about which of seven
//    fields to fix.
// 2. An *address-only* invite parses and previews successfully, and is refused at
//    connect time with the fix in the message. Parsing is not the same question
//    as "can this be redeemed", and collapsing them would make a legitimate share
//    look broken.
// 3. It never decides that a claim is redeemable. Expiry, replay, and the code
//    check are server verdicts; a client-side guess would let the UI promise a
//    connection the Gateway then refuses.

export const INVITE_VERSION = "1";
export const INVITE_PREFIX = "alpha://connect";
export const CLOCK_SKEW_TOLERANCE_SECONDS = 300;

export interface ParsedInvite {
  agent_id: string;
  url: string;
  websocket_url: string | null;
  name: string | null;
  expires_at: number | null;
  epoch: number | null;
  pairing_code: string | null;
}

export class InviteParseError extends Error {}

const FIELD_LABELS: Record<string, string> = {
  v: "version",
  a: "agent id",
  u: "gateway address",
  w: "websocket address",
  n: "display name",
  e: "expiry",
  ep: "invite number",
  k: "pairing code",
};

const KNOWN_FIELDS = Object.keys(FIELD_LABELS);
const MAX_LENGTH = 4096;
const AGENT_ID = /^[A-Za-z0-9_.:-]{1,128}$/;

function fail(field: string, reason: string): never {
  throw new InviteParseError(`${FIELD_LABELS[field] ?? field}: ${reason}`);
}

/** Strip the quoting and autolink wrappers chat clients add around pasted text. */
export function unwrapPastedText(value: string): string {
  let text = value.trim();
  if (text.startsWith("<") && text.endsWith(">")) text = text.slice(1, -1).trim();
  return text;
}

/** Whether this text even looks like a connection string, for paste routing. */
export function looksLikeInvite(value: string): boolean {
  return unwrapPastedText(value).toLowerCase().startsWith(INVITE_PREFIX);
}

/**
 * Shape-check an endpoint: scheme, embedded credentials, host presence.
 *
 * This is deliberately **not** the SSRF guard. The blocked metadata hosts and
 * addresses live in the server's `validate_endpoint`, which runs when the pairing
 * request is made. Copying that table into the browser would create a second list
 * to keep in step, and it would buy nothing: a client-side pass cannot make an
 * endpoint safe to fetch, because the browser is not the thing that fetches it.
 *
 * So this layer answers only what it can honestly answer — "is this shaped like
 * something this app could have produced?" — and the server remains the authority
 * on what is actually reachable.
 */
function assertEndpoint(value: string, field: string, schemes: string[]): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return fail(field, "is not a valid URL");
  }
  if (!schemes.includes(parsed.protocol.replace(":", ""))) {
    return fail(field, `must start with ${schemes.join(" or ")}`);
  }
  if (parsed.username || parsed.password) {
    return fail(field, "must not contain embedded credentials");
  }
  if (!parsed.hostname) return fail(field, "must contain a host");
  return value;
}

export function parseInvite(value: string): ParsedInvite {
  const text = unwrapPastedText(value);
  if (!text) throw new InviteParseError("nothing to paste");
  if (text.length > MAX_LENGTH) throw new InviteParseError(`longer than ${MAX_LENGTH} characters`);
  if (!text.toLowerCase().startsWith(`${INVITE_PREFIX}?`)) {
    if (text.toLowerCase().startsWith(INVITE_PREFIX)) {
      throw new InviteParseError("the connection string is missing its '?' query part");
    }
    throw new InviteParseError(`must start with '${INVITE_PREFIX}?' — this does not look like an Alpha connection string`);
  }

  const query = text.slice(text.indexOf("?") + 1);
  const seen = new Map<string, string>();
  for (const pair of query.split("&")) {
    if (!pair) continue;
    const eq = pair.indexOf("=");
    const key = eq === -1 ? pair : pair.slice(0, eq);
    let raw = eq === -1 ? "" : pair.slice(eq + 1);
    try {
      raw = decodeURIComponent(raw.replace(/\+/g, " "));
    } catch {
      return fail(key, "is not correctly encoded");
    }
    if (seen.has(key)) fail(key, "appears more than once");
    seen.set(key, raw);
  }

  const unknown = [...seen.keys()].filter((key) => !KNOWN_FIELDS.includes(key));
  if (unknown.length > 0) {
    throw new InviteParseError(
      `unrecognised field${unknown.length > 1 ? "s" : ""} ${unknown.join(", ")} — this Alpha understands ${KNOWN_FIELDS.join(", ")}`,
    );
  }

  const version = seen.get("v") ?? "";
  if (version !== INVITE_VERSION) {
    fail("v", `unsupported connection-string version ${version || "(missing)"}; this Alpha speaks version ${INVITE_VERSION}`);
  }

  const agentId = seen.get("a") ?? "";
  if (!agentId) fail("a", "is required");
  if (!AGENT_ID.test(agentId)) fail("a", "must be 1-128 characters using letters, digits, '_', '.', ':', or '-'");

  const url = seen.get("u") ?? "";
  if (!url) fail("u", "is required");
  assertEndpoint(url, "u", ["http", "https"]);

  const websocketUrl = seen.get("w") || null;
  if (websocketUrl) assertEndpoint(websocketUrl, "w", ["ws", "wss"]);

  const name = seen.get("n") || null;

  let expiresAt: number | null = null;
  const rawExpiry = seen.get("e") ?? "";
  if (rawExpiry) {
    if (!/^\d+$/.test(rawExpiry)) fail("e", "must be a whole number of seconds since the epoch");
    expiresAt = Number(rawExpiry);
    if (expiresAt <= 0) fail("e", `must be a positive epoch second, got ${expiresAt}`);
  }

  let epoch: number | null = null;
  const rawEpoch = seen.get("ep") ?? "";
  if (rawEpoch) {
    if (!/^\d+$/.test(rawEpoch)) fail("ep", "must be a whole number");
    epoch = Number(rawEpoch);
    if (epoch < 1) fail("ep", `must be at least 1, got ${epoch}`);
  }

  return {
    agent_id: agentId,
    url,
    websocket_url: websocketUrl,
    name,
    expires_at: expiresAt,
    epoch,
    pairing_code: seen.get("k") || null,
  };
}

/**
 * Whether the *client* can tell it is past the window.
 *
 * Advisory only: the Gateway refuses an expired claim regardless, and it applies
 * its own clock-skew tolerance. This exists so the preview card can say "this
 * looks expired" before the operator presses Connect, not to decide the outcome.
 */
export function looksExpired(invite: ParsedInvite, nowMs: number = Date.now()): boolean {
  if (invite.expires_at === null) return false;
  return nowMs / 1000 > invite.expires_at + CLOCK_SKEW_TOLERANCE_SECONDS;
}

/** The refusal a connect will produce, so the UI can show it before submitting. */
export function redeemabilityProblem(invite: ParsedInvite): string | null {
  if (!invite.pairing_code) {
    return "This is an address-only invite and carries no pairing code. Ask your friend for a full invite, or enter their pairing code manually.";
  }
  if (looksExpired(invite)) return "This connection string looks expired. Ask for a fresh one.";
  return null;
}

/** "in 14 minutes" / "3 minutes ago" — never a bare timestamp. */
export function describeExpiry(expiresAt: number | null, nowMs: number = Date.now()): string {
  if (expiresAt === null) return "no expiry reported";
  const seconds = Math.round(expiresAt - nowMs / 1000);
  const magnitude = Math.abs(seconds);
  const unit = magnitude < 90 ? "seconds" : magnitude < 5400 ? "minutes" : "hours";
  const amount = unit === "seconds" ? magnitude : Math.round(magnitude / (unit === "minutes" ? 60 : 3600));
  if (amount < 1) return seconds >= 0 ? "expires now" : "expired";
  return seconds >= 0 ? `expires in ${amount} ${unit}` : `expired ${amount} ${unit} ago`;
}