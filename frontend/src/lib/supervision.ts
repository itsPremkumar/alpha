import { get, send, asList } from "./http";

/**
 * Watchdog fleet state (workers, leases, liveness).
 *
 * This used to `catch { return null }`, which is a lie in the same shape as the
 * `"Company engine idle"` string the workspace header was fixed for: the caller
 * gets a `null`, has no server reason, and has to invent a sentence for it. The
 * read now propagates, so a failed read and a legitimately empty fleet stay two
 * distinct claims and only the server gets to word the failure.
 */
export async function supervisionFleet(): Promise<Record<string, unknown>> {
  return get<Record<string, unknown>>("/supervision/fleet");
}

export async function supervisionAnomalies(): Promise<Array<Record<string, unknown>>> {
  try {
    const d = await get<unknown>("/supervision/anomalies");
    return asList(d, ["anomalies", "data"]);
  } catch {
    return [];
  }
}

export async function recoverWorker(workerId: string): Promise<string> {
  const d = await send<Record<string, unknown>>("/supervision/recover", "POST", { worker_id: workerId });
  return JSON.stringify(d).slice(0, 1000);
}

export async function adoptOrphans(supervisorId: string): Promise<string> {
  const d = await send<Record<string, unknown>>("/supervision/adopt", "POST", { supervisor_id: supervisorId });
  return JSON.stringify(d).slice(0, 1000);
}

/** One worker as reported by `GET /api/supervision/fleet`. Null fields mean "not reported", never a guessed 0. */
export interface FleetWorker {
  worker_id: string;
  status: string;
  last_heartbeat_elapsed_seconds: number | null;
  progress_percent: number | null;
  current_action: string;
  active_task_id: string | null;
  unresolved_anomalies_count: number | null;
}

function toFleetWorker(rec: Record<string, unknown>, key: string): FleetWorker {
  const num = (k: string): number | null =>
    typeof rec[k] === "number" && Number.isFinite(rec[k]) ? (rec[k] as number) : null;
  const str = (k: string): string => (typeof rec[k] === "string" ? (rec[k] as string) : "");
  const id = str("worker_id");
  return {
    worker_id: id || key,
    status: str("status") || "unknown",
    last_heartbeat_elapsed_seconds: num("last_heartbeat_elapsed_seconds"),
    progress_percent: num("progress_percent"),
    current_action: str("current_action"),
    active_task_id: typeof rec.active_task_id === "string" ? rec.active_task_id : null,
    unresolved_anomalies_count: num("unresolved_anomalies_count"),
  };
}

function asRecord(v: unknown, what: string): Record<string, unknown> {
  if (!v || typeof v !== "object" || Array.isArray(v)) {
    throw new Error(`The server returned an unreadable ${what} payload.`);
  }
  return v as Record<string, unknown>;
}

function asRecordList(v: unknown, what: string): Array<Record<string, unknown>> {
  if (!Array.isArray(v)) {
    throw new Error(`The server returned an unreadable ${what} payload.`);
  }
  return v.map((item) => asRecord(item, what));
}

/**
 * Parse `GET /api/supervision/fleet` (a map keyed by worker id, tolerating an
 * array or a `{workers|data: [...]}` envelope). Throws on an unreadable body
 * so a failed request is never shown as an empty fleet.
 */
export function parseFleetWorkers(body: unknown): FleetWorker[] {
  if (Array.isArray(body)) {
    return asRecordList(body, "fleet").map((r, i) => toFleetWorker(r, `worker-${i}`));
  }
  if (body && typeof body === "object") {
    const rec = body as Record<string, unknown>;
    const nested = [rec.workers, rec.data].find((v) => Array.isArray(v));
    if (Array.isArray(nested)) {
      return asRecordList(nested, "fleet").map((r, i) => toFleetWorker(r, `worker-${i}`));
    }
    const entries = Object.entries(rec);
    if (entries.length === 0) return [];
    if (entries.every(([, v]) => v && typeof v === "object" && !Array.isArray(v))) {
      return entries.map(([key, v]) => toFleetWorker(v as Record<string, unknown>, key));
    }
  }
  throw new Error("The server returned an unreadable fleet payload.");
}

/** Strict variant: a failed request throws instead of being reported as an empty fleet. */
export async function fetchFleetWorkers(): Promise<FleetWorker[]> {
  return parseFleetWorkers(await get<unknown>("/supervision/fleet"));
}

/**
 * What the workspace header says about the safety watchdog.
 *
 * The probe used to pass a constant `() => "watching"`, which discarded the
 * value it was summarizing: a fleet of zero workers and a fleet of two hundred
 * both rendered the same reassuring word. The measured deployment answers HTTP
 * 200 with `{}` — no worker has ever posted a heartbeat — so the header was
 * asserting liveness supervision over a subsystem that had never observed
 * anything.
 *
 * Zero workers is a real answer and is now stated as one, with the reason
 * attached. It is emphatically not a pass: nothing is being watched.
 */
export function watchdogDetail(workers: FleetWorker[]): string {
  const n = workers.length;
  if (n === 0) return "no workers reporting — no heartbeat received, nothing is being watched";
  const anomalies = workers.filter((w) => w.unresolved_anomalies_count !== null && w.unresolved_anomalies_count > 0).length;
  const base = `${n} ${n === 1 ? "worker" : "workers"} reporting`;
  return anomalies > 0 ? `${base} · ${anomalies} with unresolved anomalies` : base;
}

/** Strict variant of the anomaly list: throws instead of pretending there are no anomalies. */
export async function fetchAnomaliesStrict(): Promise<Array<Record<string, unknown>>> {
  const d = await get<unknown>("/supervision/anomalies");
  if (Array.isArray(d)) return asRecordList(d, "anomaly");
  if (d && typeof d === "object") {
    const nested = [(d as Record<string, unknown>).anomalies, (d as Record<string, unknown>).data].find((v) =>
      Array.isArray(v),
    );
    if (Array.isArray(nested)) return asRecordList(nested, "anomaly");
  }
  throw new Error("The server returned an unreadable anomaly payload.");
}
