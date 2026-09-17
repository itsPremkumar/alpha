import { get, send, asList } from "./http";

/** Watchdog fleet state (workers, leases, liveness). Null when unavailable. */
export async function supervisionFleet(): Promise<Record<string, unknown> | null> {
  try {
    return await get<Record<string, unknown>>("/supervision/fleet");
  } catch {
    return null;
  }
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
