import { get, asList, errMsg } from "./http";

/**
 * Honest, per-subsystem snapshot for the Overview atlas.
 *
 * Each domain is read independently: one 404/403/timeout blanks exactly one
 * card and carries the server's reason. A whole-snapshot failure is therefore
 * never silently an empty workspace. Counts the server did not send are null
 * and render as an em-dash with words beside them — never zero.
 */

export type OverviewDomainId =
  | "bots"
  | "projects"
  | "workflows"
  | "skills"
  | "memory"
  | "scheduled"
  | "channels"
  | "agents"
  | "runs";

export interface OverviewDomain {
  id: OverviewDomainId;
  /** Measured count, or null when the server did not answer that domain. */
  count: number | null;
  /** Server reason when the domain did not answer; null on success. */
  error: string | null;
  /** Short human detail, e.g. "3 active, 1 paused" or "5 enabled". */
  detail: string | null;
  /** Up to 3 preview labels for the card (names/titles), never fabricated. */
  preview: string[];
}

export interface OverviewSnapshot {
  domains: OverviewDomain[];
  /** Names of domains that did not answer, for the header disclosure. */
  failed: string[];
  fetchedAt: string;
}

function countOf(body: unknown, keys: string[]): number | null {
  const list = asList(body, keys);
  // asList returns [] both for "server said empty" and for "unreadable shape".
  // Callers pass the raw body too: an object with none of the keys is treated
  // as unmeasured (null) rather than zero.
  if (Array.isArray(body)) return body.length;
  if (body && typeof body === "object") {
    const rec = body as Record<string, unknown>;
    for (const k of keys) {
      if (Array.isArray(rec[k])) return (rec[k] as unknown[]).length;
    }
    // Some routers answer a map (channels) or a single object (memory).
    if (keys.includes("channels") && rec.channels && typeof rec.channels === "object") {
      return Object.keys(rec.channels as Record<string, unknown>).length;
    }
    if (keys.includes("facts") && Array.isArray(rec.facts)) return (rec.facts as unknown[]).length;
    return null;
  }
  return null;
}

function previewOf(body: unknown, keys: string[], nameKeys: string[]): string[] {
  return asList(body, keys)
    .slice(0, 3)
    .map((row, i) => {
      for (const k of nameKeys) {
        const v = row[k];
        if (typeof v === "string" && v.trim()) return v.slice(0, 60);
      }
      return `item ${i + 1}`;
    });
}

async function readDomain(
  id: OverviewDomainId,
  path: string,
  listKeys: string[],
  nameKeys: string[],
  detailOf?: (body: unknown) => string | null,
): Promise<OverviewDomain> {
  try {
    const body = await get<unknown>(path);
    return {
      id,
      count: countOf(body, listKeys),
      error: null,
      detail: detailOf ? detailOf(body) : null,
      preview: previewOf(body, listKeys, nameKeys),
    };
  } catch (e) {
    return { id, count: null, error: errMsg(e), detail: null, preview: [] };
  }
}

function botDetail(body: unknown): string | null {
  const list = asList(body, ["bots", "data"]);
  if (list.length === 0 && !Array.isArray(body)) return null;
  // An absent `status` is not "active" — it is unknown. Counting it as active
  // would dress a failed read up as a healthy fleet.
  const active = list.filter((b) => b.status === "active").length;
  return `${active}/${list.length} active`;
}

function skillDetail(body: unknown): string | null {
  const list = asList(body, ["skills", "data"]);
  if (list.length === 0 && !Array.isArray(body)) return null;
  // An absent `enabled` is not "enabled" — it is unknown. Counting it as enabled
  // would dress a failed read up as a full catalogue.
  const on = list.filter((s) => s.enabled === true).length;
  return `${on}/${list.length} enabled`;
}

function channelDetail(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const rec = body as Record<string, unknown>;
  const raw = rec.channels;
  const list =
    raw && typeof raw === "object" && !Array.isArray(raw)
      ? Object.entries(raw as Record<string, unknown>).map(([name, v]) => ({
          ...(typeof v === "object" && v !== null ? (v as Record<string, unknown>) : {}),
          name,
        }))
      : asList(raw ?? body, ["channels", "data"]);
  if (list.length === 0) return null;
  const live = list.filter((c) => Boolean(c.connected ?? c.running ?? false)).length;
  return `${live}/${list.length} connected`;
}

function memoryDetail(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const rec = body as Record<string, unknown>;
  if (Array.isArray(rec.facts)) return `${(rec.facts as unknown[]).length} facts`;
  return null;
}

/** Read every domain independently; never rejects. */
export async function fetchOverview(): Promise<OverviewSnapshot> {
  const domains = await Promise.all([
    readDomain("bots", "/bots", ["bots", "data"], ["display_name", "name"], botDetail),
    readDomain("projects", "/projects", ["projects", "data"], ["name"]),
    readDomain("workflows", "/workflows", ["workflows", "data"], ["name"]),
    readDomain("skills", "/skills", ["skills", "data"], ["name"], skillDetail),
    readDomain("memory", "/memory", ["facts", "data"], ["content"], memoryDetail),
    readDomain("scheduled", "/scheduled-tasks", ["tasks", "data"], ["title", "name"]),
    readDomain("channels", "/channels", ["channels", "data"], ["name"], channelDetail),
    readDomain("agents", "/agents", ["agents", "data"], ["display_name", "name"]),
    readDomain("runs", "/console/runs?limit=3", ["runs", "data"], ["thread_title", "title"]),
  ]);
  return {
    domains,
    failed: domains.filter((d) => d.error !== null).map((d) => d.id),
    fetchedAt: new Date().toISOString(),
  };
}

/** Look up one domain from a snapshot (null when absent, never fabricated). */
export function domainOf(snapshot: OverviewSnapshot | null, id: OverviewDomainId): OverviewDomain | null {
  return snapshot?.domains.find((d) => d.id === id) ?? null;
}
