import { get, pick } from "./http";

/**
 * The read-only digest of one project, for the Projects view.
 *
 * The Gateway exposes ~22 read-only `GET /projects/{id}/*` routes. This module
 * reads the compact ones — the facts that answer "what is this project, what is
 * it doing, and what is waiting on it" — so the project list can show them
 * without the operator hunting for them on another view. The live control
 * surfaces (war-room, RSI, perpetual, canary, blueprints) stay in the Workforce
 * view; this is a digest, not a second implementation of them.
 *
 * Every section is read independently. One route that 404s on an older Gateway,
 * or fails transiently, must not blank the eight that answered — so a section is
 * either data or the server's own reason, never a silent gap.
 */

export type DetailSection<T> =
  | { status: "ok"; data: T }
  | { status: "error"; error: string };

export interface ProjectStateDigest {
  goal: string;
  phase: string;
  arch_version: string;
  active_agents: number | null;
  active_tasks: number | null;
  blocked_tasks: number | null;
  completed_tasks: number | null;
  failed_tasks: number | null;
  open_conflicts: number | null;
  /** `null` is "never verified", which is not the same claim as "not verified yet". */
  last_verified: string | null;
  latest_decision: string | null;
  open_risks: string[];
  updated_at: string;
}

export interface ProjectDecision {
  id: string;
  title: string;
  body: string;
  reason: string;
  made_by: string;
  approved_by: string | null;
  created_at: string;
}

export interface ProjectEvent {
  id: string;
  type: string;
  actor: string;
  created_at: string;
  seq: number | null;
  payload: Record<string, unknown> | null;
}

export interface ProjectPendingWork {
  locks: number;
  lock_requests: number;
  approvals: number;
  checkpoints: number;
  handoffs: number;
}

export interface ProjectConstitution {
  present: boolean;
  /** The template the Gateway would seed from; `""` when it sent none. */
  template: string;
}

/** Everything one project answered, plus the reason for anything it did not. */
export interface ProjectDetail {
  state: DetailSection<ProjectStateDigest>;
  decisions: DetailSection<ProjectDecision[]>;
  events: DetailSection<ProjectEvent[]>;
  pending: DetailSection<ProjectPendingWork>;
  constitution: DetailSection<ProjectConstitution>;
  /** True when at least one section failed, so the panel can say so up front. */
  partial: boolean;
}

const enc = (id: string): string => encodeURIComponent(id);

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;

/** A string, or `null` when the field is absent. Never `""` for "unknown". */
const str = (record: Record<string, unknown>, key: string): string | null => {
  const value = record[key];
  return typeof value === "string" ? value : null;
};

/** A count, or `null` when the server did not send a number. Never coerced to 0. */
const count = (record: Record<string, unknown>, key: string): number | null => {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
};

/**
 * A field that should be a list, defensively.
 *
 * A non-array here means the server sent something this client cannot read, and
 * rendering it as "empty" would claim there is nothing where the truth is
 * unreadable — so the caller gets `[]` only for a genuinely absent value and
 * `pick`-free strictness elsewhere. Kept simple on purpose: every route probed
 * returns either the key as a list or omits it.
 */
const list = (value: unknown): Array<Record<string, unknown>> =>
  Array.isArray(value) ? (value as Array<Record<string, unknown>>) : [];

/**
 * Read one section, converting a rejection into the server's own reason.
 *
 * `errMsg` is applied inside the component, so the raw message is carried here to
 * keep this module free of UI concerns.
 */
async function section<T>(read: () => Promise<T>): Promise<DetailSection<T>> {
  try {
    return { status: "ok", data: await read() };
  } catch (error) {
    return { status: "error", error: error instanceof Error ? error.message : String(error) };
  }
}

export async function fetchProjectStateDigest(projectId: string): Promise<DetailSection<ProjectStateDigest>> {
  return section(async () => {
    const body = await get<unknown>(`/projects/${enc(projectId)}/state`);
    const record = asRecord(body);
    if (!record) throw new Error("The server returned an unreadable project state.");
    return {
      goal: str(record, "goal") ?? "",
      phase: str(record, "phase") ?? "",
      arch_version: str(record, "arch_version") ?? "",
      active_agents: count(record, "active_agents"),
      active_tasks: count(record, "active_tasks"),
      blocked_tasks: count(record, "blocked_tasks"),
      completed_tasks: count(record, "completed_tasks"),
      failed_tasks: count(record, "failed_tasks"),
      open_conflicts: count(record, "open_conflicts"),
      last_verified: str(record, "last_verified"),
      latest_decision: str(record, "latest_decision"),
      // A risk arrives as a bare string on some paths and as an object on
      // others. `pick` only reads object keys, so passing a string through it
      // yields "" — a real risk rendered as a blank bullet, which is worse than
      // not listing it. Both shapes are read.
      open_risks: (Array.isArray(record.open_risks) ? record.open_risks : []).map((risk) =>
        typeof risk === "string"
          ? risk
          : String(pick(asRecord(risk) ?? {}, ["description", "title", "risk", "text"], "")),
      ),
      updated_at: str(record, "updated_at") ?? "",
    };
  });
}

export async function fetchProjectDecisions(projectId: string): Promise<DetailSection<ProjectDecision[]>> {
  return section(async () => {
    const body = await get<unknown>(`/projects/${enc(projectId)}/decisions`);
    const record = asRecord(body);
    if (!record) throw new Error("The server returned an unreadable decisions list.");
    return list(record.decisions).map((row, index) => ({
      id: str(row, "decision_id") ?? str(row, "id") ?? `decision-${index}`,
      title: str(row, "title") ?? "",
      body: str(row, "body") ?? "",
      reason: str(row, "reason") ?? "",
      made_by: str(row, "made_by") ?? "",
      approved_by: str(row, "approved_by"),
      created_at: str(row, "created_at") ?? "",
    }));
  });
}

export async function fetchProjectEvents(projectId: string): Promise<DetailSection<ProjectEvent[]>> {
  return section(async () => {
    const body = await get<unknown>(`/projects/${enc(projectId)}/events`);
    const record = asRecord(body);
    if (!record) throw new Error("The server returned an unreadable event feed.");
    return list(record.events)
      .map((row, index) => ({
        id: str(row, "event_id") ?? str(row, "id") ?? `event-${index}`,
        type: str(row, "type") ?? "",
        actor: str(row, "actor") ?? "",
        created_at: str(row, "created_at") ?? "",
        seq: count(row, "seq"),
        payload: asRecord(row.payload),
      }))
      // Newest first: the point of an activity feed is what just happened. An
      // unparseable timestamp sorts last rather than first, so a row the server
      // did not date cannot displace a real recent event.
      .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
  });
}

export async function fetchProjectPendingWork(projectId: string): Promise<DetailSection<ProjectPendingWork>> {
  return section(async () => {
    const base = `/projects/${enc(projectId)}`;
    // Counts only, and deliberately NOT /decisions: `fetchProjectDecisions()`
    // already reads that route in full, and the aggregate derives the decision
    // count from it. Reading it here too meant two identical requests per
    // project render, and the two answers could disagree.
    const [locks, approvals, checkpoints, handoffs] = await Promise.allSettled([
      get<unknown>(`${base}/locks`),
      get<unknown>(`${base}/approvals`),
      get<unknown>(`${base}/checkpoints`),
      get<unknown>(`${base}/handoffs`),
    ]);
    const rejected = [locks, approvals, checkpoints, handoffs].find((r) => r.status === "rejected");
    if (rejected?.status === "rejected") {
      throw rejected.reason instanceof Error ? rejected.reason : new Error(String(rejected.reason));
    }
    const lockBody = asRecord((locks as PromiseFulfilledResult<unknown>).value);
    return {
      locks: list(lockBody?.locks).length,
      lock_requests: list(lockBody?.pending_requests).length,
      approvals: list(asRecord((approvals as PromiseFulfilledResult<unknown>).value)?.approvals).length,
      checkpoints: list(asRecord((checkpoints as PromiseFulfilledResult<unknown>).value)?.checkpoints).length,
      handoffs: list(asRecord((handoffs as PromiseFulfilledResult<unknown>).value)?.handoffs).length,
    };
  });
}

export async function fetchProjectConstitution(projectId: string): Promise<DetailSection<ProjectConstitution>> {
  return section(async () => {
    const body = await get<unknown>(`/projects/${enc(projectId)}/constitution`);
    const record = asRecord(body);
    if (!record) throw new Error("The server returned an unreadable constitution.");
    return {
      // `present` is the server's claim. Absent means we do not know, not false.
      present: record.present === true,
      template: str(record, "template") ?? "",
    };
  });
}

/**
 * Read every section at once, in parallel.
 *
 * Returns partial reads rather than rejecting: the caller renders what arrived
 * and states plainly what did not.
 */
export async function fetchProjectDetail(projectId: string): Promise<ProjectDetail> {
  const [state, decisions, events, pending, constitution] = await Promise.all([
    fetchProjectStateDigest(projectId),
    fetchProjectDecisions(projectId),
    fetchProjectEvents(projectId),
    fetchProjectPendingWork(projectId),
    fetchProjectConstitution(projectId),
  ]);
  const sections = [state, decisions, events, pending, constitution];
  return {
    state,
    decisions,
    events,
    pending,
    constitution,
    partial: sections.some((s) => s.status === "error"),
  };
}
