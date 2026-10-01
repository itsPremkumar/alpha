/**
 * Per-bot detail client for the dedicated bot page (`/bots/[name]`).
 *
 * Every function here reads a REAL gateway route and rejects with the server's
 * own reason on failure. Nothing is defaulted, coerced, or invented — this file
 * is the boundary that keeps the page honest.
 *
 * The honesty rules this client exists to enforce:
 *
 *  - An optional field the server omitted becomes `null`, never `0`, `""`, `[]`
 *    or `false`. "The gateway did not report it" and "it is empty" are
 *    different facts and the page must be able to say which is which.
 *  - A failed read rejects. It never resolves to an empty collection, because
 *    an empty list that *looks* like "nothing recorded" is the exact failure
 *    `frontend/AGENTS.md` calls out ("Silence is not success").
 *  - Envelope and enum strings are preserved verbatim. An unknown `status` is
 *    rendered as the server sent it rather than snapped to "active".
 *
 * Why a separate module and not an extension of `lib/bots.ts`: `lib/bots.ts`
 * owns the fleet roster read (`GET /api/bots`) and stays cheap. This one fans
 * out to six per-bot routes, and a page should be allowed to fail per-section
 * without the roster client dragging it down.
 */

import { apiFetch } from "./api-client";
import { normalizeBot } from "./bots";
import type { BotProfile } from "@/types/bots";

export type DetailState<T> =
  | { state: "ok"; value: T }
  /** The read was refused or failed; `reason` is the server's own words. */
  | { state: "error"; reason: string };

/** A per-section read the page can render independently of the others. */
export interface BotDetailBundle {
  profile: DetailState<BotProfile>;
  performance: DetailState<Record<string, unknown>>;
  inbox: DetailState<BotInbox>;
  chat: DetailState<Record<string, unknown>>;
  fleetHealth: DetailState<Record<string, unknown>>;
  organization: DetailState<Record<string, unknown>>;
}

export interface BotInboxMessage {
  id?: string;
  sender?: string;
  body?: string;
  created_at?: number | string;
  read?: boolean;
  [key: string]: unknown;
}

export interface BotInbox {
  messages: BotInboxMessage[];
  /**
   * `null` when the server sent no counter. An absent `unread_count` is not a
   * measured zero, so it must not render as "0 unread".
   */
  unread_count: number | null;
}

const asString = (v: unknown): string | null => (typeof v === "string" ? v : null);
const asNumber = (v: unknown): number | null => (typeof v === "number" ? v : null);

function reasonOf(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return String(error);
}

/**
 * Load one bot's full detail, per-section.
 *
 * Every section is settled independently with `Promise.allSettled`: a failing
 * inbox read must not blank the performance panel. Each rejection is converted
 * into an `error` state carrying the server's reason so the page can show *why*
 * a section is empty instead of implying the bot has nothing there.
 */
export async function fetchBotDetail(name: string): Promise<BotDetailBundle> {
  const key = encodeURIComponent(name);

  // `apiFetch` resolves a `Response`, so each body is parsed here. A non-2xx
  // already rejected inside the client with the server's reason, so `.json()`
  // here only runs on a real success envelope.
  const json = (path: string) => apiFetch(path).then((res) => res.json() as Promise<Record<string, unknown>>);

  const [profile, performance, inbox, chat, fleetHealth, organization] = await Promise.allSettled([
    json(`/bots/${key}`).then((raw) => normalizeBot(raw)),
    json(`/bots/${key}/performance`),
    json(`/bots/${key}/inbox`).then(normalizeInbox),
    json(`/bots/${key}/chat`),
    json("/bots/health/overview"),
    json("/bots/organization-chart"),
  ]);

  const settle = <T,>(result: PromiseSettledResult<T>): DetailState<T> =>
    result.status === "fulfilled"
      ? { state: "ok", value: result.value }
      : { state: "error", reason: reasonOf(result.reason) };

  return {
    profile: settle(profile),
    performance: settle(performance),
    inbox: settle(inbox),
    chat: settle(chat),
    fleetHealth: settle(fleetHealth),
    organization: settle(organization),
  };
}

/** Map `GET /api/bots/{name}/inbox` onto a typed shape (exported for tests). */
export function normalizeInbox(raw: Record<string, unknown>): BotInbox {
  const messages = Array.isArray(raw.messages) ? (raw.messages as BotInboxMessage[]) : [];
  return {
    messages,
    unread_count: asNumber(raw.unread_count),
  };
}

/**
 * Read one labelled value for a detail row.
 *
 * Returns `null` for absent/wrong-typed values so the row renders "not
 * reported" rather than a blank cell that reads as an empty string the server
 * actually sent.
 */
export function detailValue(raw: Record<string, unknown>, key: string): string | null {
  const v = raw[key];
  if (v === null || v === undefined) return null;
  if (typeof v === "string") return v.length ? v : null;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return null;
}

/**
 * Render an unknown enum verbatim.
 *
 * A `status` this client has never heard of is still a real answer from the
 * server, so it is passed through untouched rather than coerced to a known
 * value — `frontend/AGENTS.md` requires unknown enum values to be displayed
 * rather than silently snapped.
 */
export function enumOrUnknown(value: unknown, known: readonly string[]): { value: string; known: boolean } {
  const text = typeof value === "string" ? value : null;
  if (text === null) return { value: "not reported", known: false };
  return { value: text, known: known.includes(text) };
}